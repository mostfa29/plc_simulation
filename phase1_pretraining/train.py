"""
Training Loop for Phase 1
============================
Key improvements over original:
  - num_classes computed from config (was hardcoded to 10)
  - LR warmup (linear over first N epochs) before cosine decay
  - CrossEntropyLoss by default (simpler, more stable gradients)
  - Mixup disabled by default (reduce over-regularization)
  - Per-class F1 logged each epoch for monitoring
"""
import argparse
import json
import time
import csv
import logging
import math
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import yaml
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torch.optim import AdamW

from tqdm import tqdm

from dataset import prepare_datasets, Phase1Dataset
from models import create_model, InceptionTimeNetwork, count_parameters
from losses import FocalLoss, MixupFocalLoss, mixup_data
from sampler import BalancedBatchSampler

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)


def get_lr(epoch, warmup_epochs, max_epochs, base_lr, eta_min=1e-6):
    """Linear warmup then cosine decay."""
    if epoch < warmup_epochs:
        return base_lr * (epoch + 1) / warmup_epochs
    progress = (epoch - warmup_epochs) / max(max_epochs - warmup_epochs, 1)
    return eta_min + 0.5 * (base_lr - eta_min) * (1 + math.cos(math.pi * progress))


def evaluate(model, dataloader, device, num_classes):
    """Evaluate model. num_classes is required (no default)."""
    model.eval()
    all_preds, all_labels = [], []

    with torch.no_grad():
        for X_batch, y_batch in dataloader:
            X_batch = X_batch.to(device)
            logits = model(X_batch)
            preds = logits.argmax(dim=-1).cpu().numpy()
            all_preds.extend(preds)
            all_labels.extend(y_batch.numpy())

    all_preds = np.array(all_preds)
    all_labels = np.array(all_labels)

    per_class_f1 = []
    for cls in range(num_classes):
        tp = ((all_preds == cls) & (all_labels == cls)).sum()
        fp = ((all_preds == cls) & (all_labels != cls)).sum()
        fn = ((all_preds != cls) & (all_labels == cls)).sum()
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-8)
        per_class_f1.append(f1)

    return {
        'accuracy': float((all_preds == all_labels).mean()),
        'macro_f1': float(np.mean(per_class_f1)),
        'per_class_f1': [float(f) for f in per_class_f1],
    }


def train_single_model(model, train_dataset, val_dataset, config, device,
                        checkpoint_path, log_path, model_name="model"):
    """Train a single model (one ensemble member or baseline).

    Returns best validation macro F1.
    """
    tc = config['training']
    lc = config['loss']
    num_classes = config['model']['c_out']

    pin_mem = device.type == 'cuda'
    num_workers = config['data'].get('num_workers', 0)

    # Balanced batch sampler
    train_sampler = BalancedBatchSampler(
        train_dataset.labels,
        batch_size=tc['batch_size'],
        samples_per_class=config['sampler']['samples_per_class'],
    )
    train_loader = DataLoader(
        train_dataset, batch_sampler=train_sampler,
        num_workers=num_workers, pin_memory=pin_mem,
    )
    val_loader = DataLoader(
        val_dataset, batch_size=tc['batch_size'] * 2,
        shuffle=False, num_workers=num_workers, pin_memory=pin_mem,
    )

    # Optimizer
    optimizer = AdamW(model.parameters(), lr=tc['lr'], weight_decay=tc['weight_decay'])

    # Loss: CE with class weights (default) or Focal
    loss_type = lc.get('type', 'CrossEntropyLoss')
    if loss_type == 'FocalLoss':
        criterion = FocalLoss(
            alpha=lc['alpha'], gamma=lc['gamma'],
            label_smoothing=lc.get('label_smoothing', 0.0),
        ).to(device)
        mixup_criterion = MixupFocalLoss(
            alpha=lc['alpha'], gamma=lc['gamma'],
            label_smoothing=lc.get('label_smoothing', 0.0),
        ).to(device)
    else:
        # Standard CrossEntropyLoss with optional class weights
        alpha = lc.get('alpha', None)
        weight = torch.tensor(alpha, dtype=torch.float32).to(device) if alpha else None
        criterion = nn.CrossEntropyLoss(
            weight=weight,
            label_smoothing=lc.get('label_smoothing', 0.0),
        )
        mixup_criterion = None  # Mixup with CE not supported here

    # Mixed precision
    use_amp = tc.get('mixed_precision', False) and device.type == 'cuda'
    scaler = torch.amp.GradScaler('cuda') if use_amp else None

    # Training state
    best_f1 = 0.0
    patience_counter = 0
    patience = tc.get('early_stop_patience', 15)
    max_epochs = tc['max_epochs']
    warmup_epochs = tc.get('warmup_epochs', 5)
    grad_clip = tc.get('gradient_clip', 1.0)
    mixup_alpha = config['augmentation'].get('mixup_alpha', 0.2)
    mixup_prob = config['augmentation'].get('mixup_prob', 0.0)

    log_rows = []

    logger.info(f"Training {model_name}: {count_parameters(model):,} params, "
                f"{len(train_dataset)} train, {len(val_dataset)} val, "
                f"loss={loss_type}, warmup={warmup_epochs} epochs")

    for epoch in range(max_epochs):
        epoch_start = time.monotonic()

        # LR schedule: warmup + cosine
        lr = get_lr(epoch, warmup_epochs, max_epochs, tc['lr'],
                     tc.get('scheduler_eta_min', 1e-6))
        for pg in optimizer.param_groups:
            pg['lr'] = lr

        # ── Train ──
        model.train()
        epoch_loss = 0.0
        num_batches = 0

        pbar = tqdm(train_loader,
                     desc=f"[{model_name}] Epoch {epoch+1:3d}/{max_epochs}",
                     leave=False, ncols=100)
        for X_batch, y_batch in pbar:
            X_batch = X_batch.to(device)
            y_batch = y_batch.to(device)

            optimizer.zero_grad()

            # Optional mixup (only with Focal loss)
            use_mixup = (mixup_prob > 0 and mixup_criterion is not None
                         and np.random.random() < mixup_prob)
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

            epoch_loss += loss.item()
            num_batches += 1
            pbar.set_postfix(loss=f"{epoch_loss / num_batches:.4f}", lr=f"{lr:.2e}")

        pbar.close()
        avg_loss = epoch_loss / max(num_batches, 1)

        # ── Validate ──
        val_metrics = evaluate(model, val_loader, device, num_classes=num_classes)
        val_f1 = val_metrics['macro_f1']

        epoch_time = time.monotonic() - epoch_start

        log_row = {
            'epoch': epoch + 1, 'train_loss': avg_loss,
            'val_accuracy': val_metrics['accuracy'],
            'val_macro_f1': val_f1, 'lr': lr, 'time_s': epoch_time,
        }
        # Also log per-class F1
        for ci, f1 in enumerate(val_metrics['per_class_f1']):
            log_row[f'val_f1_class_{ci}'] = f1
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

        # Log per-class F1 for first few epochs and then every 10
        pcf1_str = ""
        if epoch < 3 or (epoch + 1) % 10 == 0 or patience_counter >= patience:
            pcf1_str = " | per_class=[" + ",".join(f"{f:.2f}" for f in val_metrics['per_class_f1']) + "]"

        logger.info(
            f"  [{model_name}] E{epoch+1:3d}/{max_epochs} | "
            f"loss={avg_loss:.4f} | val_f1={val_f1:.4f} | "
            f"best={best_f1:.4f} | pat={patience_counter}/{patience} | "
            f"lr={lr:.2e} | {epoch_time:.1f}s{pcf1_str}"
        )

        if patience_counter >= patience:
            logger.info(f"  [{model_name}] Early stopping at epoch {epoch+1}")
            break

    if log_rows:
        with open(log_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=log_rows[0].keys())
            writer.writeheader()
            writer.writerows(log_rows)

    logger.info(f"  [{model_name}] Best val F1: {best_f1:.4f}")
    return best_f1


def main():
    parser = argparse.ArgumentParser(description='Phase 1 Training')
    parser.add_argument('--config', type=str, default='config.yaml')
    parser.add_argument('--model', type=str, default=None)
    parser.add_argument('--epochs', type=int, default=None)
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--output-dir', type=str, default='./results')
    parser.add_argument('--device', type=str, default=None)
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).parent / config_path
    with open(config_path) as f:
        config = yaml.safe_load(f)

    if args.model:
        config['model']['architecture'] = args.model
    if args.epochs:
        config['training']['max_epochs'] = args.epochs
    if args.smoke_test:
        config['training']['max_epochs'] = 5
        config['training']['early_stop_patience'] = 3

    if args.device:
        device = torch.device(args.device)
    elif torch.cuda.is_available():
        device = torch.device('cuda')
    else:
        device = torch.device('cpu')
    logger.info(f"Device: {device}")

    output_dir = Path(args.output_dir)
    checkpoints_dir = output_dir / 'checkpoints'
    logs_dir = output_dir / 'logs'
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    seed = config['experiment']['seed']
    torch.manual_seed(seed)
    np.random.seed(seed)

    dataset_dir = config['data']['dataset_dir']
    if not Path(dataset_dir).is_absolute():
        dataset_dir = str(Path(__file__).parent / dataset_dir)

    train_dataset, val_dataset, test_dataset, norm_params = prepare_datasets(
        dataset_dir=dataset_dir, class_map=config['class_map'],
        window_size=config['data']['window_size'],
        stride=config['data']['window_stride'],
        split_ratio=tuple(config['data']['split_ratio']),
        split_seed=config['data'].get('split_seed', 42),
        norm_params_path=str(checkpoints_dir / 'norm_params.json'))

    with open(output_dir / 'config.yaml', 'w') as f:
        yaml.dump(config, f, default_flow_style=False)

    arch = config['model']['architecture']
    c_in, c_out, nf = config['model']['c_in'], config['model']['c_out'], config['model']['nf']

    if arch == 'ResNet':
        model = create_model('ResNet', c_in, c_out).to(device)
        best_f1 = train_single_model(
            model, train_dataset, val_dataset, config, device,
            str(checkpoints_dir / 'resnet_baseline.pt'),
            str(logs_dir / 'resnet_training.csv'), 'ResNet')
        logger.info(f"ResNet: best val F1 = {best_f1:.4f}")
    else:
        ensemble_size = config['model'].get('ensemble_size', 5)
        ensemble_seeds = config['model'].get('ensemble_seeds', list(range(ensemble_size)))
        f1s = []
        for i, mseed in enumerate(ensemble_seeds):
            logger.info(f"\n{'='*60}")
            logger.info(f"Training member {i+1}/{ensemble_size} (seed={mseed})")
            torch.manual_seed(mseed)
            np.random.seed(mseed)
            model = InceptionTimeNetwork(c_in, c_out, nf).to(device)
            best_f1 = train_single_model(
                model, train_dataset, val_dataset, config, device,
                str(checkpoints_dir / f'inception_{i}_best.pt'),
                str(logs_dir / f'inception_{i}_training.csv'),
                f'Inception-{i}')
            f1s.append(best_f1)
        logger.info(f"\nEnsemble complete. F1s: {[f'{f:.4f}' for f in f1s]}, mean: {np.mean(f1s):.4f}")


if __name__ == '__main__':
    main()