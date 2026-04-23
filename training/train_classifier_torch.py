"""PyTorch 1D-CNN classifier for failure-mode detection.

Equivalent architecture to train_classifier.py (TF version), but implemented
in PyTorch so it runs on Python 3.14 + GPU locally.

Outputs:
    training/models/classifier_torch/model.pt
    training/models/classifier_torch/meta.json
    training/models/classifier_torch/classifier.onnx   (for deployment)

Usage:
    python -m training.train_classifier_torch \
        --data training/data/windows_full.npz \
        --model-out training/models/classifier_torch \
        --epochs 100 --batch-size 256
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset, WeightedRandomSampler
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.utils.class_weight import compute_class_weight

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("train_classifier")

CLASSES = ["NORMAL", "BIAS", "OSCILLATION", "DEADBAND_HUNTING",
           "SLUGGISH", "WINDUP", "CONDITION_CHANGE"]


# ──────────────────────────────────────────────────────────────────────
# Model
# ──────────────────────────────────────────────────────────────────────
class FailureModeClassifier(nn.Module):
    """1D-CNN for 40×7 windowed telemetry → 7-class failure mode."""

    def __init__(self, n_features: int = 7, n_classes: int = 7,
                 dropout: float = 0.2):
        super().__init__()
        self.conv1 = nn.Conv1d(n_features, 32, kernel_size=5, padding=2)
        self.bn1 = nn.BatchNorm1d(32)
        self.conv2 = nn.Conv1d(32, 64, kernel_size=5, padding=2)
        self.bn2 = nn.BatchNorm1d(64)
        self.pool1 = nn.MaxPool1d(2)
        self.conv3 = nn.Conv1d(64, 64, kernel_size=3, padding=1)
        self.bn3 = nn.BatchNorm1d(64)
        self.pool2 = nn.MaxPool1d(2)
        self.gap = nn.AdaptiveAvgPool1d(1)
        self.fc1 = nn.Linear(64, 32)
        self.dropout = nn.Dropout(dropout)
        self.fc2 = nn.Linear(32, n_classes)

    def forward(self, x):
        # x: (batch, 40, 7) -> permute to (batch, 7, 40) for Conv1D
        x = x.permute(0, 2, 1)
        x = F.relu(self.bn1(self.conv1(x)))
        x = F.relu(self.bn2(self.conv2(x)))
        x = self.pool1(x)
        x = F.relu(self.bn3(self.conv3(x)))
        x = self.pool2(x)
        x = self.gap(x).squeeze(-1)
        x = F.relu(self.fc1(x))
        x = self.dropout(x)
        return self.fc2(x)


# ──────────────────────────────────────────────────────────────────────
# Training
# ──────────────────────────────────────────────────────────────────────
def train(model, loader, optimizer, class_weights, device, epoch: int):
    model.train()
    total_loss = 0.0
    correct = 0
    total = 0
    for X, y in loader:
        X = X.to(device, non_blocking=True)
        y = y.to(device, non_blocking=True)
        optimizer.zero_grad()
        out = model(X)
        loss = F.cross_entropy(out, y, weight=class_weights)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * X.size(0)
        pred = out.argmax(dim=1)
        correct += (pred == y).sum().item()
        total += X.size(0)
    return total_loss / total, correct / total


@torch.no_grad()
def evaluate(model, loader, device):
    model.eval()
    all_pred, all_true = [], []
    for X, y in loader:
        X = X.to(device, non_blocking=True)
        out = model(X)
        pred = out.argmax(dim=1).cpu().numpy()
        all_pred.extend(pred.tolist())
        all_true.extend(y.numpy().tolist())
    acc = np.mean(np.array(all_pred) == np.array(all_true))
    return acc, np.array(all_pred), np.array(all_true)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--model-out", required=True)
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--dropout", type=float, default=0.2)
    p.add_argument("--patience", type=int, default=10)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Device: {device}")
    if device.type == "cuda":
        logger.info(f"GPU: {torch.cuda.get_device_name(0)}")

    # Load data
    data = np.load(args.data, allow_pickle=True)
    X = data["X"].astype(np.float32)
    y_str = data["y"]
    logger.info(f"Data shape: {X.shape}")

    class_to_idx = {c: i for i, c in enumerate(CLASSES)}
    y = np.array([class_to_idx.get(str(l), 0) for l in y_str], dtype=np.int64)

    # Per-channel normalisation
    mean = X.mean(axis=(0, 1), keepdims=True)
    std = X.std(axis=(0, 1), keepdims=True) + 1e-8
    X = (X - mean) / std

    # Split
    X_train, X_tmp, y_train, y_tmp = train_test_split(
        X, y, test_size=0.25, random_state=args.seed, stratify=y
    )
    X_val, X_test, y_val, y_test = train_test_split(
        X_tmp, y_tmp, test_size=0.5, random_state=args.seed, stratify=y_tmp
    )
    logger.info(f"Split: train={len(X_train)} val={len(X_val)} test={len(X_test)}")

    # Class weights (balanced)
    w = compute_class_weight("balanced",
                              classes=np.unique(y_train),
                              y=y_train)
    class_weights = torch.tensor(w, dtype=torch.float32, device=device)
    logger.info(f"Class weights: {dict(zip(CLASSES, w))}")

    # Loaders
    def mk_loader(Xa, ya, shuffle: bool, batch_size: int):
        ds = TensorDataset(torch.from_numpy(Xa), torch.from_numpy(ya))
        return DataLoader(ds, batch_size=batch_size, shuffle=shuffle,
                          num_workers=0, pin_memory=(device.type == "cuda"))

    train_loader = mk_loader(X_train, y_train, True, args.batch_size)
    val_loader = mk_loader(X_val, y_val, False, args.batch_size * 2)
    test_loader = mk_loader(X_test, y_test, False, args.batch_size * 2)

    # Model
    model = FailureModeClassifier(n_features=X.shape[2],
                                   n_classes=len(CLASSES),
                                   dropout=args.dropout).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    logger.info(f"Model params: {n_params:,}")

    opt = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-5)
    sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, mode="max",
                                                       factor=0.5, patience=5)

    best_val_acc = 0.0
    patience_counter = 0
    best_state = None
    t0 = time.time()

    for epoch in range(1, args.epochs + 1):
        train_loss, train_acc = train(model, train_loader, opt,
                                       class_weights, device, epoch)
        val_acc, _, _ = evaluate(model, val_loader, device)
        sched.step(val_acc)
        logger.info(f"Epoch {epoch:3d}: train_loss={train_loss:.4f} "
                     f"train_acc={train_acc:.4f} val_acc={val_acc:.4f}")
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_state = {k: v.detach().cpu().clone()
                          for k, v in model.state_dict().items()}
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= args.patience:
                logger.info(f"Early stop at epoch {epoch} (patience {args.patience})")
                break

    logger.info(f"Training complete in {time.time()-t0:.1f}s. "
                 f"Best val_acc: {best_val_acc:.4f}")

    # Restore best
    if best_state is not None:
        model.load_state_dict(best_state)

    # Final test-set evaluation
    test_acc, y_pred, y_true = evaluate(model, test_loader, device)
    logger.info(f"\n=== FINAL TEST SET ===")
    logger.info(f"Accuracy: {test_acc:.4f}")

    report = classification_report(y_true, y_pred, target_names=CLASSES,
                                     digits=4)
    logger.info(f"\n{report}")

    cm = confusion_matrix(y_true, y_pred)
    logger.info(f"\nConfusion matrix (rows=true, cols=pred):\n{cm}")

    # Save
    out_dir = Path(args.model_out)
    out_dir.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(),
                "classes": CLASSES,
                "X_mean": mean.squeeze().tolist(),
                "X_std": std.squeeze().tolist(),
                "input_shape": list(X.shape[1:])},
               out_dir / "model.pt")

    # Export to ONNX (for cross-runtime deployment)
    try:
        model.eval()
        dummy = torch.randn(1, X.shape[1], X.shape[2], device=device)
        onnx_path = out_dir / "classifier.onnx"
        torch.onnx.export(model, dummy, str(onnx_path),
                          input_names=["input"], output_names=["logits"],
                          dynamic_axes={"input": {0: "batch"}},
                          opset_version=13)
        logger.info(f"ONNX exported: {onnx_path} ({onnx_path.stat().st_size:,} bytes)")
    except Exception as e:
        logger.warning(f"ONNX export failed: {e}")

    # Meta
    meta = {
        "classes": CLASSES,
        "X_mean": mean.squeeze().tolist(),
        "X_std": std.squeeze().tolist(),
        "val_accuracy": float(best_val_acc),
        "test_accuracy": float(test_acc),
        "classification_report": report,
        "confusion_matrix": cm.tolist(),
        "n_params": int(n_params),
        "epochs_trained": epoch,
        "architecture": "1D-CNN (Conv1D(32,5) -> Conv1D(64,5) -> Conv1D(64,3) -> GAP -> Dense(32) -> Dense(7))",
    }
    with open(out_dir / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    logger.info(f"Saved to {out_dir}")
    status = "REACHED" if test_acc >= 0.90 else "NOT REACHED"
    logger.info(f"TARGET 90%+ {status} (test accuracy: {test_acc*100:.2f}%)")


if __name__ == "__main__":
    main()
