"""
Hierarchical Two-Stage Fault Classifier (Session 3)
=====================================================
Decomposes 9-class problem into easier sub-problems:

Stage 1: Binary classifier (normal vs any-fault)
  - Trained on ALL data
  - Single InceptionTimeNetwork (binary is easy)
  - Target: F1 > 0.95 for normal class

Stage 2: 8-class fault subtype classifier
  - Trained ONLY on fault windows (excludes normal)
  - InceptionTime ensemble (5 members)
  - Focuses all model capacity on the hard problem
  - Target: macro F1 > 0.75 across 8 fault types

Inference: Stage 1 gates Stage 2 — only runs subtype on fault windows.
"""
import logging
import time
import csv
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

from models import InceptionTimeNetwork, count_parameters
from sampler import BalancedBatchSampler

logger = logging.getLogger(__name__)


class BinaryDataset(Dataset):
    """Wraps Phase1Dataset with binary labels: 0=normal, 1=fault."""

    def __init__(self, windows, labels):
        self.windows = windows
        self.labels = (labels != 0).astype(np.int64)  # binary

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        x = torch.from_numpy(self.windows[idx]).T  # [C, T]
        return x, int(self.labels[idx])


class FaultSubtypeDataset(Dataset):
    """Wraps Phase1Dataset with fault-only windows, labels remapped 0-7."""

    def __init__(self, windows, labels):
        fault_mask = labels != 0
        self.windows = windows[fault_mask]
        self.labels = (labels[fault_mask] - 1).astype(np.int64)  # remap 1-8 -> 0-7

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        x = torch.from_numpy(self.windows[idx]).T  # [C, T]
        return x, int(self.labels[idx])


def get_lr(epoch, warmup_epochs, max_epochs, base_lr, eta_min=1e-6):
    """Linear warmup then cosine decay."""
    if epoch < warmup_epochs:
        return base_lr * (epoch + 1) / warmup_epochs
    progress = (epoch - warmup_epochs) / max(max_epochs - warmup_epochs, 1)
    return eta_min + 0.5 * (base_lr - eta_min) * (1 + math.cos(math.pi * progress))


def evaluate_model(model, dataloader, device, num_classes):
    """Evaluate model, return per-class F1 and macro F1."""
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


def train_stage(model, train_dataset, val_dataset, config, device,
                checkpoint_path, log_path, model_name, num_classes):
    """Train a single stage model. Returns best validation macro F1."""
    tc = config['training']

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

    # Plain Adam, no weight decay
    optimizer = torch.optim.Adam(model.parameters(), lr=tc['lr'])

    # Stage 1 (binary): slightly upweight faults
    if num_classes == 2:
        criterion = nn.CrossEntropyLoss(weight=torch.tensor([1.0, 2.0]).to(device))
    else:
        criterion = nn.CrossEntropyLoss()

    # Mixed precision
    use_amp = tc.get('mixed_precision', False) and device.type == 'cuda'
    scaler = torch.amp.GradScaler('cuda') if use_amp else None

    best_f1 = 0.0
    patience_counter = 0
    patience = tc.get('early_stop_patience', 15)
    max_epochs = tc['max_epochs']
    warmup_epochs = tc.get('warmup_epochs', 5)
    grad_clip = tc.get('gradient_clip', 1.0)
    log_rows = []

    logger.info(f"Training {model_name}: {count_parameters(model):,} params, "
                f"{len(train_dataset)} train, {len(val_dataset)} val, "
                f"n_classes={num_classes}")

    for epoch in range(max_epochs):
        epoch_start = time.monotonic()

        lr = get_lr(epoch, warmup_epochs, max_epochs, tc['lr'],
                     tc.get('scheduler_eta_min', 1e-6))
        for pg in optimizer.param_groups:
            pg['lr'] = lr

        model.train()
        epoch_loss = 0.0
        num_batches = 0

        for X_batch, y_batch in train_loader:
            X_batch = X_batch.to(device)
            y_batch = y_batch.to(device)
            optimizer.zero_grad()

            if use_amp:
                with torch.amp.autocast('cuda'):
                    logits = model(X_batch)
                    loss = criterion(logits, y_batch)
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
                scaler.step(optimizer)
                scaler.update()
            else:
                logits = model(X_batch)
                loss = criterion(logits, y_batch)
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
                optimizer.step()

            epoch_loss += loss.item()
            num_batches += 1

        avg_loss = epoch_loss / max(num_batches, 1)
        val_metrics = evaluate_model(model, val_loader, device, num_classes)
        val_f1 = val_metrics['macro_f1']
        epoch_time = time.monotonic() - epoch_start

        log_row = {
            'epoch': epoch + 1, 'train_loss': avg_loss,
            'val_accuracy': val_metrics['accuracy'],
            'val_macro_f1': val_f1, 'lr': lr, 'time_s': epoch_time,
        }
        for ci, f1 in enumerate(val_metrics['per_class_f1']):
            log_row[f'val_f1_class_{ci}'] = f1
        log_rows.append(log_row)

        if val_f1 > best_f1:
            best_f1 = val_f1
            patience_counter = 0
            torch.save({
                'epoch': epoch + 1,
                'model_state_dict': model.state_dict(),
                'val_macro_f1': best_f1,
                'num_classes': num_classes,
            }, checkpoint_path)
        else:
            patience_counter += 1

        if epoch < 3 or (epoch + 1) % 10 == 0 or patience_counter >= patience:
            pcf1_str = " | per_class=[" + ",".join(f"{f:.2f}" for f in val_metrics['per_class_f1']) + "]"
            logger.info(
                f"  [{model_name}] E{epoch+1:3d}/{max_epochs} | "
                f"loss={avg_loss:.4f} | val_f1={val_f1:.4f} | "
                f"best={best_f1:.4f} | pat={patience_counter}/{patience} | "
                f"lr={lr:.2e} | {epoch_time:.1f}s{pcf1_str}")

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


class HierarchicalClassifier:
    """Two-stage hierarchical fault classifier.

    Stage 1: Binary (normal=0 vs fault=1)
    Stage 2: 8-class fault subtype (fault windows only)
    Combined: 9-class end-to-end predictions
    """

    def __init__(self, c_in=19, nf=32, device=None):
        self.c_in = c_in
        self.nf = nf
        self.device = device or torch.device('cpu')
        self.stage1_model = None
        self.stage2_models = []

    def train_stage1(self, train_windows, train_labels,
                     val_windows, val_labels, config,
                     checkpoint_dir, logs_dir):
        """Train binary classifier: normal(0) vs fault(1)."""
        logger.info("\n" + "=" * 60)
        logger.info("STAGE 1: Binary Normal vs Fault")
        logger.info("=" * 60)

        train_ds = BinaryDataset(train_windows, train_labels)
        val_ds = BinaryDataset(val_windows, val_labels)

        logger.info(f"  Binary train: {len(train_ds)} windows "
                     f"(normal={int((train_ds.labels==0).sum())}, "
                     f"fault={int((train_ds.labels==1).sum())})")
        logger.info(f"  Binary val:   {len(val_ds)} windows "
                     f"(normal={int((val_ds.labels==0).sum())}, "
                     f"fault={int((val_ds.labels==1).sum())})")

        model = InceptionTimeNetwork(self.c_in, 2, self.nf, dropout=0.0).to(self.device)

        # Stage 1 trains for fewer epochs (binary is easy)
        s1_config = {**config}
        s1_config['training'] = {**config['training'], 'max_epochs': 50, 'early_stop_patience': 15}

        best_f1 = train_stage(
            model, train_ds, val_ds, s1_config, self.device,
            str(checkpoint_dir / 'stage1_binary_best.pt'),
            str(logs_dir / 'stage1_binary_training.csv'),
            'Stage1-Binary', num_classes=2)

        # Load best checkpoint
        ckpt = torch.load(str(checkpoint_dir / 'stage1_binary_best.pt'),
                          map_location=self.device, weights_only=False)
        model.load_state_dict(ckpt['model_state_dict'])
        model.eval()
        self.stage1_model = model

        return best_f1

    def train_stage2(self, train_windows, train_labels,
                     val_windows, val_labels, config,
                     checkpoint_dir, logs_dir):
        """Train 8-class fault subtype classifier on fault windows only."""
        logger.info("\n" + "=" * 60)
        logger.info("STAGE 2: 8-Class Fault Subtype")
        logger.info("=" * 60)

        train_ds = FaultSubtypeDataset(train_windows, train_labels)
        val_ds = FaultSubtypeDataset(val_windows, val_labels)

        from collections import Counter
        train_dist = Counter(int(l) for l in train_ds.labels)
        val_dist = Counter(int(l) for l in val_ds.labels)
        logger.info(f"  Fault train: {len(train_ds)} windows, dist={dict(sorted(train_dist.items()))}")
        logger.info(f"  Fault val:   {len(val_ds)} windows, dist={dict(sorted(val_dist.items()))}")

        ensemble_size = config['model'].get('ensemble_size', 5)
        ensemble_seeds = config['model'].get('ensemble_seeds', list(range(ensemble_size)))
        f1s = []

        for i, mseed in enumerate(ensemble_seeds):
            logger.info(f"\n  Training Stage2 member {i+1}/{ensemble_size} (seed={mseed})")
            torch.manual_seed(mseed)
            np.random.seed(mseed)

            model = InceptionTimeNetwork(self.c_in, 8, self.nf, dropout=0.0).to(self.device)

            best_f1 = train_stage(
                model, train_ds, val_ds, config, self.device,
                str(checkpoint_dir / f'stage2_inception_{i}_best.pt'),
                str(logs_dir / f'stage2_inception_{i}_training.csv'),
                f'Stage2-Inception-{i}', num_classes=8)
            f1s.append(best_f1)

        logger.info(f"\n  Stage2 ensemble F1s: {[f'{f:.4f}' for f in f1s]}, "
                     f"mean: {np.mean(f1s):.4f}")

        # Load best checkpoints
        self.stage2_models = []
        for i in range(ensemble_size):
            ckpt_path = checkpoint_dir / f'stage2_inception_{i}_best.pt'
            if ckpt_path.exists():
                ckpt = torch.load(str(ckpt_path), map_location=self.device, weights_only=False)
                model = InceptionTimeNetwork(self.c_in, 8, self.nf, dropout=0.0)
                model.load_state_dict(ckpt['model_state_dict'])
                model.to(self.device).eval()
                self.stage2_models.append(model)

        return np.mean(f1s)

    def predict(self, X_tensor):
        """Two-stage inference. X_tensor: [N, C, T] torch tensor."""
        N = X_tensor.shape[0]
        predictions = np.zeros(N, dtype=np.int64)

        # Stage 1: Binary classification
        self.stage1_model.eval()
        with torch.no_grad():
            logits1 = self.stage1_model(X_tensor)
            is_fault = logits1.argmax(dim=-1).cpu().numpy()  # 0=normal, 1=fault

        fault_indices = np.where(is_fault == 1)[0]
        if len(fault_indices) > 0 and len(self.stage2_models) > 0:
            # Stage 2: Fault subtype classification (ensemble average)
            fault_X = X_tensor[fault_indices]
            all_probs = []
            with torch.no_grad():
                for model in self.stage2_models:
                    model.eval()
                    logits2 = model(fault_X)
                    probs = F.softmax(logits2, dim=-1)
                    all_probs.append(probs)
            avg_probs = torch.stack(all_probs).mean(dim=0)
            fault_subtypes = avg_probs.argmax(dim=-1).cpu().numpy()
            # Remap: 0-7 back to 1-8 (original class indices)
            predictions[fault_indices] = fault_subtypes + 1

        return predictions

    def predict_from_numpy(self, X_numpy):
        """Predict from numpy array [N, T, C] (dataset format)."""
        # Convert: [N, T, C] -> [N, C, T] for model
        X_tensor = torch.from_numpy(X_numpy).permute(0, 2, 1).to(self.device)
        return self.predict(X_tensor)


def evaluate_hierarchical(predictions, y_test, class_names):
    """Evaluate the two-stage hierarchical classifier end-to-end."""
    num_classes = len(class_names)

    # Overall 9-class metrics
    per_class_f1 = []
    for cls in range(num_classes):
        tp = ((predictions == cls) & (y_test == cls)).sum()
        fp = ((predictions == cls) & (y_test != cls)).sum()
        fn = ((predictions != cls) & (y_test == cls)).sum()
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-8)
        per_class_f1.append(f1)

    macro_f1 = np.mean(per_class_f1)

    print("\n=== COMBINED 9-CLASS METRICS ===")
    print(f"Macro F1: {macro_f1:.4f}")
    print(f"\n{'Class':<20s} {'Precision':>10s} {'Recall':>10s} {'F1':>10s} {'Support':>10s}")
    print("-" * 60)
    for cls in range(num_classes):
        tp = ((predictions == cls) & (y_test == cls)).sum()
        fp = ((predictions == cls) & (y_test != cls)).sum()
        fn = ((predictions != cls) & (y_test == cls)).sum()
        support = (y_test == cls).sum()
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = per_class_f1[cls]
        print(f"{class_names[cls]:<20s} {precision:>10.4f} {recall:>10.4f} {f1:>10.4f} {support:>10d}")

    # Stage 1 metrics (binary)
    y_binary_true = (y_test != 0).astype(int)
    y_binary_pred = (predictions != 0).astype(int)
    tp = ((y_binary_pred == 1) & (y_binary_true == 1)).sum()
    fp = ((y_binary_pred == 1) & (y_binary_true == 0)).sum()
    fn = ((y_binary_pred == 0) & (y_binary_true == 1)).sum()
    tn = ((y_binary_pred == 0) & (y_binary_true == 0)).sum()
    binary_precision = tp / max(tp + fp, 1)
    binary_recall = tp / max(tp + fn, 1)
    binary_f1 = 2 * binary_precision * binary_recall / max(binary_precision + binary_recall, 1e-8)
    normal_recall = tn / max(tn + fp, 1)

    print(f"\n=== STAGE 1: Normal vs Fault ===")
    print(f"Normal recall: {normal_recall:.4f}")
    print(f"Fault precision: {binary_precision:.4f}, recall: {binary_recall:.4f}, F1: {binary_f1:.4f}")

    # Stage 2 metrics (fault windows only)
    fault_mask = y_test != 0
    if fault_mask.sum() > 0:
        y_fault_true = y_test[fault_mask] - 1
        y_fault_pred = predictions[fault_mask] - 1
        valid = y_fault_pred >= 0
        if valid.sum() > 0:
            fault_f1s = []
            for cls in range(8):
                tp = ((y_fault_pred[valid] == cls) & (y_fault_true[valid] == cls)).sum()
                fp = ((y_fault_pred[valid] == cls) & (y_fault_true[valid] != cls)).sum()
                fn = ((y_fault_pred[valid] != cls) & (y_fault_true[valid] == cls)).sum()
                p = tp / max(tp + fp, 1)
                r = tp / max(tp + fn, 1)
                f = 2 * p * r / max(p + r, 1e-8)
                fault_f1s.append(f)
            print(f"\n=== STAGE 2: Fault Subtype (fault windows only) ===")
            print(f"Fault Macro F1: {np.mean(fault_f1s):.4f}")

    return {
        'macro_f1': macro_f1,
        'per_class_f1': per_class_f1,
        'normal_recall': normal_recall,
        'binary_f1': binary_f1,
    }
