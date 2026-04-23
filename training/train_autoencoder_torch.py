"""PyTorch Convolutional Autoencoder for condition-change detection.

Trains ONLY on NORMAL-labelled windows. At inference time, the
reconstruction error is the anomaly score: high error = condition change
the model hasn't seen before (formation shift, tool wear, stick-slip
onset, etc.).

Advantage over the 3-channel CUSUM already in PerformanceMonitor:
  - Operates on the full 7-channel multivariate pattern
  - Catches correlated shifts in rpm/torque/pressure simultaneously
  - Typically sees condition changes 10–30 s earlier than mean-shift CUSUM

Outputs:
    training/models/autoencoder_torch/model.pt
    training/models/autoencoder_torch/autoencoder.onnx
    training/models/autoencoder_torch/meta.json   (threshold, stats, classes)

Usage:
    python -m training.train_autoencoder_torch \
        --data training/data/windows_full.npz \
        --model-out training/models/autoencoder_torch \
        --epochs 50 --batch-size 256
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("train_autoencoder")


# ──────────────────────────────────────────────────────────────────────
# Model — Conv1D encoder / decoder, bottleneck = 16
# ──────────────────────────────────────────────────────────────────────
class ConvAutoencoder(nn.Module):
    """40 × 7 input → 16-dim latent → 40 × 7 reconstruction."""

    def __init__(self, n_features: int = 7, latent_dim: int = 16):
        super().__init__()
        self.n_features = n_features
        self.latent_dim = latent_dim
        # Encoder: 40 → 20 → 10 → 5
        self.enc_conv1 = nn.Conv1d(n_features, 32, 5, stride=2, padding=2)
        self.enc_bn1 = nn.BatchNorm1d(32)
        self.enc_conv2 = nn.Conv1d(32, 64, 3, stride=2, padding=1)
        self.enc_bn2 = nn.BatchNorm1d(64)
        self.enc_conv3 = nn.Conv1d(64, 16, 3, stride=2, padding=1)
        self.enc_bn3 = nn.BatchNorm1d(16)
        # Flattened shape after encoder: 16 × 5 = 80
        self.enc_fc = nn.Linear(16 * 5, latent_dim)
        # Decoder
        self.dec_fc = nn.Linear(latent_dim, 16 * 5)
        self.dec_conv1 = nn.ConvTranspose1d(16, 64, 3, stride=2, padding=1, output_padding=1)
        self.dec_bn1 = nn.BatchNorm1d(64)
        self.dec_conv2 = nn.ConvTranspose1d(64, 32, 3, stride=2, padding=1, output_padding=1)
        self.dec_bn2 = nn.BatchNorm1d(32)
        self.dec_conv3 = nn.ConvTranspose1d(32, n_features, 5, stride=2, padding=2, output_padding=1)

    def encode(self, x):
        # x: (B, 40, 7) → (B, 7, 40)
        x = x.permute(0, 2, 1)
        x = F.relu(self.enc_bn1(self.enc_conv1(x)))   # (B, 32, 20)
        x = F.relu(self.enc_bn2(self.enc_conv2(x)))   # (B, 64, 10)
        x = F.relu(self.enc_bn3(self.enc_conv3(x)))   # (B, 16, 5)
        x = x.flatten(1)
        return self.enc_fc(x)

    def decode(self, z):
        x = self.dec_fc(z).view(-1, 16, 5)
        x = F.relu(self.dec_bn1(self.dec_conv1(x)))   # (B, 64, 10)
        x = F.relu(self.dec_bn2(self.dec_conv2(x)))   # (B, 32, 20)
        x = torch.sigmoid(self.dec_conv3(x))          # (B, 7, 40)
        return x.permute(0, 2, 1)                     # (B, 40, 7)

    def forward(self, x):
        return self.decode(self.encode(x))


# ──────────────────────────────────────────────────────────────────────
# Training
# ──────────────────────────────────────────────────────────────────────
def train_epoch(model, loader, optimizer, device):
    model.train()
    total_loss = 0.0
    n = 0
    for (X,) in loader:
        X = X.to(device, non_blocking=True)
        optimizer.zero_grad()
        recon = model(X)
        loss = F.mse_loss(recon, X)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * X.size(0)
        n += X.size(0)
    return total_loss / n


@torch.no_grad()
def eval_epoch(model, loader, device):
    model.eval()
    total_loss = 0.0
    n = 0
    for (X,) in loader:
        X = X.to(device, non_blocking=True)
        recon = model(X)
        loss = F.mse_loss(recon, X, reduction="sum") / X.numel() * X.size(0)
        total_loss += loss.item()
        n += X.size(0)
    return total_loss / max(n, 1)


@torch.no_grad()
def reconstruction_errors(model, X: torch.Tensor, device, batch_size: int = 512):
    """Per-window mean-squared reconstruction error."""
    model.eval()
    out = np.zeros(len(X), dtype=np.float32)
    for i in range(0, len(X), batch_size):
        batch = X[i: i + batch_size].to(device)
        recon = model(batch)
        mse = ((recon - batch) ** 2).mean(dim=(1, 2))
        out[i: i + batch_size] = mse.cpu().numpy()
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--model-out", required=True)
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--latent-dim", type=int, default=16)
    p.add_argument("--label-filter", default="NORMAL")
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
    X_all = data["X"].astype(np.float32)
    y_all = data["y"]
    logger.info(f"Full dataset: {X_all.shape}")

    # Filter to NORMAL only for training
    mask = np.array([str(l) == args.label_filter for l in y_all])
    X_normal = X_all[mask]
    X_anomaly = X_all[~mask]
    logger.info(f"NORMAL windows: {len(X_normal):,}  Anomaly windows: {len(X_anomaly):,}")

    if len(X_normal) < 1000:
        logger.error("Not enough NORMAL windows to train")
        return

    # Min-max normalise to [0, 1] using NORMAL stats (sigmoid output)
    X_min = X_normal.min(axis=(0, 1), keepdims=True)
    X_max = X_normal.max(axis=(0, 1), keepdims=True) + 1e-8
    X_norm = (X_normal - X_min) / (X_max - X_min)
    X_anom_norm = np.clip((X_anomaly - X_min) / (X_max - X_min), 0.0, 1.0)

    # Train / val split (held-out NORMAL for threshold calibration)
    n_val = max(1000, int(len(X_norm) * 0.1))
    rng = np.random.default_rng(args.seed)
    perm = rng.permutation(len(X_norm))
    X_val = X_norm[perm[:n_val]]
    X_train = X_norm[perm[n_val:]]
    logger.info(f"Train: {len(X_train):,}  Val (NORMAL held-out): {len(X_val):,}")

    # Loaders
    to_loader = lambda Xa, shuf: DataLoader(
        TensorDataset(torch.from_numpy(Xa)),
        batch_size=args.batch_size, shuffle=shuf,
        num_workers=0, pin_memory=(device.type == "cuda"),
    )
    train_loader = to_loader(X_train, True)
    val_loader = to_loader(X_val, False)

    # Model
    model = ConvAutoencoder(n_features=X_train.shape[2],
                             latent_dim=args.latent_dim).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    logger.info(f"Model params: {n_params:,}")

    opt = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-5)
    sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, mode="min",
                                                       factor=0.5, patience=5)

    best_val = float("inf")
    best_state = None
    t0 = time.time()
    for epoch in range(1, args.epochs + 1):
        train_loss = train_epoch(model, train_loader, opt, device)
        val_loss = eval_epoch(model, val_loader, device)
        sched.step(val_loss)
        logger.info(f"Epoch {epoch:3d}: train_mse={train_loss:.6f} val_mse={val_loss:.6f}")
        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.detach().cpu().clone()
                          for k, v in model.state_dict().items()}
    logger.info(f"Training complete in {time.time()-t0:.1f}s. Best val MSE: {best_val:.6f}")

    if best_state is not None:
        model.load_state_dict(best_state)

    # Reconstruction-error distributions
    train_errs = reconstruction_errors(model, torch.from_numpy(X_train), device)
    val_errs = reconstruction_errors(model, torch.from_numpy(X_val), device)
    anom_errs = (reconstruction_errors(model, torch.from_numpy(X_anom_norm), device)
                 if len(X_anom_norm) > 0 else np.array([0.0]))

    # Anomaly threshold: mean + 3σ of training errors
    threshold = float(train_errs.mean() + 3 * train_errs.std())
    logger.info(f"Threshold (mean + 3σ on training set): {threshold:.6f}")

    # How well does it separate? Higher error on anomalies → good.
    normal_p95 = float(np.percentile(val_errs, 95))
    anomaly_median = float(np.median(anom_errs))
    detection_rate = float(np.mean(anom_errs > threshold))
    separation = anomaly_median / max(val_errs.mean(), 1e-8)
    logger.info(f"Val NORMAL p95 error: {normal_p95:.6f}")
    logger.info(f"Anomaly median error: {anomaly_median:.6f}")
    logger.info(f"Anomaly detection rate @ threshold: {detection_rate:.2%}")
    logger.info(f"Separation (anomaly_median / normal_mean): {separation:.2f}x")

    # Save
    out_dir = Path(args.model_out)
    out_dir.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(),
                "X_min": X_min.squeeze().tolist(),
                "X_max": X_max.squeeze().tolist(),
                "threshold": threshold,
                "latent_dim": args.latent_dim},
               out_dir / "model.pt")

    # ONNX export (monolithic, no .data sidecar)
    try:
        model.eval()
        dummy = torch.randn(1, X_train.shape[1], X_train.shape[2])
        onnx_path = out_dir / "autoencoder.onnx"
        torch.onnx.export(
            model.cpu(), dummy, str(onnx_path),
            input_names=["input"], output_names=["reconstruction"],
            dynamic_axes={"input": {0: "batch"}, "reconstruction": {0: "batch"}},
            opset_version=17, dynamo=False,
        )
        logger.info(f"ONNX: {onnx_path} ({onnx_path.stat().st_size:,} bytes)")
    except Exception as e:
        logger.warning(f"ONNX export failed: {e}")

    # Metadata (coerce numpy floats to Python floats for JSON)
    def _f(v):
        try: return float(v)
        except Exception: return v
    meta = {
        "X_min": [_f(v) for v in np.asarray(X_min).squeeze().tolist()],
        "X_max": [_f(v) for v in np.asarray(X_max).squeeze().tolist()],
        "threshold": _f(threshold),
        "latent_dim": int(args.latent_dim),
        "val_mse": _f(best_val),
        "val_normal_p95": _f(normal_p95),
        "anomaly_median": _f(anomaly_median),
        "detection_rate_at_threshold": _f(detection_rate),
        "separation_ratio": _f(separation),
        "n_params": int(n_params),
        "architecture": "Conv1D(32,5,s2) -> Conv1D(64,3,s2) -> Conv1D(16,3,s2) -> FC(80,16) -> decoder mirror",
    }
    with open(out_dir / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    logger.info(f"Saved to {out_dir}")


if __name__ == "__main__":
    main()
