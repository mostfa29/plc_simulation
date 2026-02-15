#!/usr/bin/env python3
"""
Training Loop for Phase 1 Pretraining
========================================
Trains InceptionTime ensemble (5 networks) or ResNet baseline on synthetic data.

Per Section 6 of the Phase 1 spec:
  - AdamW optimizer with cosine annealing LR
  - Focal loss with per-class alpha and label smoothing
  - Balanced batch sampler (6 samples per class per batch)
  - Mixed precision (FP16) for GPU
  - Mixup augmentation (alpha=0.2, prob=0.5)
  - Early stopping (patience=15, monitor val_macro_f1)
  - Gradient clipping (max_norm=1.0)
  - Reproducible via config.yaml

Usage:
  # Full InceptionTime ensemble training
  python train.py --config config.yaml

  # ResNet baseline (pipeline validation)
  python train.py --config config.yaml --model ResNet --epochs 30

  # Smoke test (20 scenarios, 5 epochs)
  python train.py --config config.yaml --smoke-test
"""
import argparse
import json
import time
import csv
import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import yaml
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR

from tqdm import tqdm

from dataset import prepare_datasets, Phase1Dataset
from models import create_model, InceptionTimeNetwork, count_parameters
from losses import FocalLoss, MixupFocalLoss, mixup_data
from sampler import BalancedBatchSampler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


def evaluate(model: nn.Module, dataloader: DataLoader,
             device: torch.device, num_classes: int = 10) -> Dict[str, float]:
    """Evaluate model on a dataloader. Returns metrics dict."""
    model.eval()
    all_preds = []
    all_labels = []

    with torch.no_grad():
        for X_batch, y_batch in dataloader:
            X_batch = X_batch.to(device)
            logits = model(X_batch)
            preds = logits.argmax(dim=-1).cpu().numpy()
            all_preds.extend(preds)
            all_labels.extend(y_batch.numpy())

    all_preds = np.array(all_preds)
    all_labels = np.array(all_labels)

    # Compute per-class precision, recall, F1
    per_class_f1 = []
    for cls in range(num_classes):
        tp = ((all_preds == cls) & (all_labels == cls)).sum()
        fp = ((all_preds == cls) & (all_labels != cls)).sum()
        fn = ((all_preds != cls) & (all_labels == cls)).sum()

        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-8)
        per_class_f1.append(f1)

    macro_f1 = np.mean(per_class_f1)
    accuracy = (all_preds == all_labels).mean()

    return {
        'accuracy': float(accuracy),
        'macro_f1': float(macro_f1),
        'per_class_f1': [float(f) for f in per_class_f1],
    }


def train_single_model(model: nn.Module,
                        train_dataset: Phase1Dataset,
                        val_dataset: Phase1Dataset,
                        config: dict,
                        device: torch.device,
                        checkpoint_path: str,
                        log_path: str,
                        model_name: str = "model") -> float:
    """Train a single model (one ensemble member or baseline).

    Returns best validation macro F1.
    """
    tc = config['training']
    lc = config['loss']

    # Data loaders — only pin_memory on CUDA
    pin_mem = device.type == 'cuda' and config['data'].get('pin_memory', True)
    num_workers = config['data'].get('num_workers', 0)

    train_sampler = BalancedBatchSampler(
        train_dataset.labels,
        batch_size=tc['batch_size'],
        samples_per_class=config['sampler']['samples_per_class'],
    )
    train_loader = DataLoader(
        train_dataset,
        batch_sampler=train_sampler,
        num_workers=num_workers,
        pin_memory=pin_mem,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=tc['batch_size'] * 2,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=pin_mem,
    )

    # Optimizer + scheduler
    optimizer = AdamW(
        model.parameters(),
        lr=tc['lr'],
        weight_decay=tc['weight_decay'],
    )
    scheduler = CosineAnnealingLR(
        optimizer,
        T_max=tc.get('scheduler_T_max', tc['max_epochs']),
        eta_min=tc.get('scheduler_eta_min', 1e-5),
    )

    # Loss
    criterion = FocalLoss(
        alpha=lc['alpha'],
        gamma=lc['gamma'],
        label_smoothing=lc['label_smoothing'],
    ).to(device)
    mixup_criterion = MixupFocalLoss(
        alpha=lc['alpha'],
        gamma=lc['gamma'],
        label_smoothing=lc['label_smoothing'],
    ).to(device)

    # Mixed precision
    use_amp = tc.get('mixed_precision', False) and device.type == 'cuda'
    scaler = torch.amp.GradScaler('cuda') if use_amp else None

    # Training state
    best_f1 = 0.0
    patience_counter = 0
    patience = tc.get('early_stop_patience', 15)
    max_epochs = tc['max_epochs']
    grad_clip = tc.get('gradient_clip', 1.0)
    mixup_alpha = config['augmentation'].get('mixup_alpha', 0.2)
    mixup_prob = config['augmentation'].get('mixup_prob', 0.5)

    # Training log
    log_rows = []

    logger.info(f"Training {model_name}: {count_parameters(model)} params, "
                f"{len(train_dataset)} train, {len(val_dataset)} val")

    for epoch in range(max_epochs):
        epoch_start = time.monotonic()

        # ── Train ──
        model.train()
        epoch_loss = 0.0
        num_batches = 0

        pbar = tqdm(
            train_loader,
            desc=f"[{model_name}] Epoch {epoch+1:3d}/{max_epochs}",
            leave=False,
            ncols=100,
        )
        for X_batch, y_batch in pbar:
            X_batch = X_batch.to(device)
            y_batch = y_batch.to(device)

            # Optional mixup
            use_mixup = np.random.random() < mixup_prob
            if use_mixup:
                X_batch, y_a, y_b, lam = mixup_data(X_batch, y_batch, mixup_alpha)

            if use_amp:
                with torch.amp.autocast('cuda'):
                    logits = model(X_batch)
                    if use_mixup:
                        loss = mixup_criterion(logits, y_a, y_b, lam)
                    else:
                        loss = criterion(logits, y_batch)

                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
                scaler.step(optimizer)
                scaler.update()
            else:
                logits = model(X_batch)
                if use_mixup:
                    loss = mixup_criterion(logits, y_a, y_b, lam)
                else:
                    loss = criterion(logits, y_batch)

                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
                optimizer.step()

            optimizer.zero_grad()
            epoch_loss += loss.item()
            num_batches += 1
            pbar.set_postfix(loss=f"{epoch_loss / num_batches:.4f}")

        pbar.close()
        scheduler.step()
        avg_loss = epoch_loss / max(num_batches, 1)

        # ── Validate ──
        val_metrics = evaluate(model, val_loader, device)
        val_f1 = val_metrics['macro_f1']

        epoch_time = time.monotonic() - epoch_start

        log_row = {
            'epoch': epoch + 1,
            'train_loss': avg_loss,
            'val_accuracy': val_metrics['accuracy'],
            'val_macro_f1': val_f1,
            'lr': optimizer.param_groups[0]['lr'],
            'time_s': epoch_time,
        }
        log_rows.append(log_row)

        # Early stopping
        if val_f1 > best_f1:
            best_f1 = val_f1
            patience_counter = 0
            torch.save({
                'epoch': epoch + 1,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'val_macro_f1': best_f1,
                'config': config,
            }, checkpoint_path)
        else:
            patience_counter += 1

        logger.info(
            f"  [{model_name}] Epoch {epoch+1:3d}/{max_epochs} | "
            f"loss={avg_loss:.4f} | val_f1={val_f1:.4f} | "
            f"best={best_f1:.4f} | patience={patience_counter}/{patience} | "
            f"{epoch_time:.1f}s"
        )

        if patience_counter >= patience:
            logger.info(f"  [{model_name}] Early stopping at epoch {epoch+1}")
            break

    # Save training log
    if log_rows:
        with open(log_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=log_rows[0].keys())
            writer.writeheader()
            writer.writerows(log_rows)

    logger.info(f"  [{model_name}] Best val F1: {best_f1:.4f}")
    return best_f1


def main():
    parser = argparse.ArgumentParser(description='Phase 1 Training')
    parser.add_argument('--config', type=str, default='config.yaml',
                        help='Path to config YAML')
    parser.add_argument('--model', type=str, default=None,
                        help='Override model architecture (ResNet, InceptionTime)')
    parser.add_argument('--epochs', type=int, default=None,
                        help='Override max epochs')
    parser.add_argument('--smoke-test', action='store_true',
                        help='Quick smoke test (5 epochs, small dataset)')
    parser.add_argument('--output-dir', type=str, default='./results',
                        help='Output directory for checkpoints and logs')
    parser.add_argument('--device', type=str, default=None,
                        help='Device (cuda, cpu, mps)')
    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).parent / config_path
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Overrides
    if args.model:
        config['model']['architecture'] = args.model
    if args.epochs:
        config['training']['max_epochs'] = args.epochs
    if args.smoke_test:
        config['training']['max_epochs'] = 5
        config['training']['early_stop_patience'] = 3

    # Device
    if args.device:
        device = torch.device(args.device)
    elif torch.cuda.is_available():
        device = torch.device('cuda')
    elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
        device = torch.device('mps')
    else:
        device = torch.device('cpu')
    logger.info(f"Device: {device}")

    # Output directory
    output_dir = Path(args.output_dir)
    checkpoints_dir = output_dir / 'checkpoints'
    logs_dir = output_dir / 'logs'
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Seed
    seed = config['experiment']['seed']
    torch.manual_seed(seed)
    np.random.seed(seed)

    # Prepare datasets
    logger.info("Preparing datasets...")
    dataset_dir = config['data']['dataset_dir']
    if not Path(dataset_dir).is_absolute():
        dataset_dir = str(Path(__file__).parent / dataset_dir)

    class_map = config['class_map']
    norm_params_path = str(checkpoints_dir / 'norm_params.json')

    train_dataset, val_dataset, test_dataset, norm_params = prepare_datasets(
        dataset_dir=dataset_dir,
        class_map=class_map,
        window_size=config['data']['window_size'],
        stride=config['data']['window_stride'],
        split_ratio=tuple(config['data']['split_ratio']),
        split_seed=config['data'].get('split_seed', 42),
        norm_params_path=norm_params_path,
    )

    # Save config copy
    with open(output_dir / 'config.yaml', 'w') as f:
        yaml.dump(config, f, default_flow_style=False)

    # ── Train ──
    arch = config['model']['architecture']
    c_in = config['model']['c_in']
    c_out = config['model']['c_out']
    nf = config['model']['nf']

    if arch == 'ResNet':
        # Single ResNet baseline
        logger.info("Training ResNet baseline...")
        model = create_model('ResNet', c_in, c_out).to(device)
        best_f1 = train_single_model(
            model, train_dataset, val_dataset, config, device,
            checkpoint_path=str(checkpoints_dir / 'resnet_baseline.pt'),
            log_path=str(logs_dir / 'resnet_training.csv'),
            model_name='ResNet',
        )
        logger.info(f"ResNet baseline: best val F1 = {best_f1:.4f}")

    else:
        # InceptionTime ensemble
        ensemble_size = config['model'].get('ensemble_size', 5)
        ensemble_seeds = config['model'].get('ensemble_seeds', list(range(ensemble_size)))
        ensemble_f1s = []

        for i, member_seed in enumerate(ensemble_seeds):
            logger.info(f"\n{'='*60}")
            logger.info(f"Training InceptionTime ensemble member {i+1}/{ensemble_size} (seed={member_seed})")

            torch.manual_seed(member_seed)
            model = InceptionTimeNetwork(c_in, c_out, nf).to(device)

            best_f1 = train_single_model(
                model, train_dataset, val_dataset, config, device,
                checkpoint_path=str(checkpoints_dir / f'inception_{i}_best.pt'),
                log_path=str(logs_dir / f'inception_{i}_training.csv'),
                model_name=f'Inception-{i}',
            )
            ensemble_f1s.append(best_f1)

        logger.info(f"\n{'='*60}")
        logger.info(f"Ensemble training complete.")
        logger.info(f"Individual F1s: {[f'{f:.4f}' for f in ensemble_f1s]}")
        logger.info(f"Mean F1: {np.mean(ensemble_f1s):.4f}")
        logger.info(f"Checkpoints saved to: {checkpoints_dir}")

    logger.info("Training complete. Run eval.py for final evaluation on test set.")


if __name__ == '__main__':
    main()
