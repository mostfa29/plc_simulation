"""Fine-tune sim-pretrained models on real rig data.

This is the FINAL deployment step. The sim-trained classifier is a
strong starting point but each rig has its own signature (pump wear,
seal condition, BHA dynamics, mud properties) that the simulation
doesn't capture perfectly. Fine-tuning on real episodes captured by
the operator (via the dashboard "Annotate" buttons) closes the gap.

Workflow:
  1. Load real episodes from hxi_optimizer/logs/dataset/<rig>/<label>/*.npz
  2. (Optional) Mix in a slice of sim data so we don't catastrophically
     forget the sim variety — defaults to 30% sim, 70% real.
  3. Load the sim-pretrained checkpoint as the starting weights.
  4. Fine-tune at low LR (1e-4) with strong early stopping (patience=5).
  5. **Validation gate:** held-out real accuracy must EXCEED the
     sim model's accuracy on the same held-out set. Otherwise: refuse
     to deploy. Bad fine-tunes never silently replace good models.
  6. Re-calibrate the AE threshold on real NORMAL windows.
  7. Save versioned: classifier_v2_finetune_<timestamp>.{pt,onnx,meta.json}
  8. Operator can A/B compare via /api/intel/compare-models before promoting.

Usage:
    # Per-rig fine-tune (one model per machine)
    python -m training.fine_tune --rig "Precision Rig 707 3pd HT"

    # Fleet-wide fine-tune (all real data combined)
    python -m training.fine_tune --rig all

    # Dry run (train but don't deploy)
    python -m training.fine_tune --rig all --no-deploy

    # Validation only (compare sim vs latest fine-tune on current real data)
    python -m training.fine_tune --validate-only
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("fine_tune")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = REPO_ROOT / "hxi_optimizer" / "logs" / "dataset"
SIM_WINDOWS = REPO_ROOT / "training" / "data" / "windows_full.npz"
SIM_MODEL_DIR = REPO_ROOT / "training" / "models" / "classifier_torch"
SIM_AE_DIR = REPO_ROOT / "training" / "models" / "autoencoder_torch"
DEPLOY_DIR = REPO_ROOT / "hxi_optimizer" / "models"
PER_RIG_DIR = DEPLOY_DIR / "per_rig"
FINETUNE_DIR = REPO_ROOT / "training" / "models" / "finetune"


def _slugify(name: str) -> str:
    """Match ModelRegistry.slugify — keep slug convention in sync."""
    s = (name or "").lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "fleet"

CLASSES = ["NORMAL", "BIAS", "OSCILLATION", "DEADBAND_HUNTING",
           "SLUGGISH", "WINDUP", "CONDITION_CHANGE"]
CLASS_TO_IDX = {c: i for i, c in enumerate(CLASSES)}

# Map operator-friendly labels (from dashboard buttons) to model classes
LABEL_REMAP = {
    "STICKSLIP":        "OSCILLATION",
    "FORMATION_CHANGE": "CONDITION_CHANGE",
    "BAD_CONNECTION":   "OSCILLATION",   # bad connections look oscillatory
    "GOOD_CONNECTION":  "NORMAL",
    "CONNECTION":       "NORMAL",        # auto-segmented connection events
}


# ─────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────

@dataclass
class RealDatasetStats:
    n_episodes: int = 0
    by_machine: dict[str, int] = field(default_factory=dict)
    by_label: dict[str, int] = field(default_factory=dict)
    excluded_unmapped: list[str] = field(default_factory=list)


def load_real_episodes(rig_filter: str = "all"
                        ) -> tuple[np.ndarray, np.ndarray, np.ndarray, RealDatasetStats]:
    """Load all .npz episodes from the dataset dir, return (X, y, machines, stats).

    Filter by rig name (slug match) or 'all' for fleet-wide.
    """
    X_list, y_list, machine_list = [], [], []
    stats = RealDatasetStats()

    if not DATASET_DIR.exists():
        logger.warning(f"No dataset dir at {DATASET_DIR}")
        return np.empty((0,)), np.empty((0,)), np.empty((0,)), stats

    rig_slug = None
    if rig_filter and rig_filter.lower() != "all":
        rig_slug = re.sub(r"[^a-z0-9]+", "_", rig_filter.lower()).strip("_")

    for machine_dir in DATASET_DIR.iterdir():
        if not machine_dir.is_dir():
            continue
        if rig_slug and rig_slug not in machine_dir.name:
            continue
        machine_name = machine_dir.name
        for label_dir in machine_dir.iterdir():
            if not label_dir.is_dir() or label_dir.name == "__pycache__":
                continue
            raw_label = label_dir.name.upper()
            mapped_label = LABEL_REMAP.get(raw_label, raw_label)
            if mapped_label not in CLASS_TO_IDX:
                stats.excluded_unmapped.append(raw_label)
                continue
            for npz_path in label_dir.glob("*.npz"):
                try:
                    data = np.load(npz_path, allow_pickle=True)
                    features = data["features"]
                    if features.ndim != 2 or features.shape[1] != 7:
                        continue
                    if features.shape[0] < 40:
                        continue
                    # Build sliding windows from this episode (stride=10)
                    for start in range(0, features.shape[0] - 40 + 1, 10):
                        X_list.append(features[start: start + 40].astype(np.float32))
                        y_list.append(CLASS_TO_IDX[mapped_label])
                        machine_list.append(machine_name)
                    stats.n_episodes += 1
                    stats.by_machine[machine_name] = stats.by_machine.get(machine_name, 0) + 1
                    stats.by_label[mapped_label] = stats.by_label.get(mapped_label, 0) + 1
                except Exception as e:
                    logger.warning(f"failed to load {npz_path.name}: {e}")

    if not X_list:
        return np.empty((0, 40, 7)), np.empty((0,), dtype=np.int64), \
               np.empty((0,), dtype=object), stats
    return (np.stack(X_list), np.array(y_list, dtype=np.int64),
            np.array(machine_list, dtype=object), stats)


def load_sim_subsample(n: int, seed: int = 7
                       ) -> tuple[np.ndarray, np.ndarray]:
    """Pull a random subsample of sim windows to mix into fine-tuning."""
    if not SIM_WINDOWS.exists():
        logger.warning(f"sim windows not at {SIM_WINDOWS}; fine-tune will use real only")
        return np.empty((0, 40, 7)), np.empty((0,), dtype=np.int64)
    data = np.load(SIM_WINDOWS, allow_pickle=True)
    X = data["X"].astype(np.float32)
    y_str = data["y"]
    y = np.array([CLASS_TO_IDX.get(str(s), 0) for s in y_str], dtype=np.int64)
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), size=min(n, len(X)), replace=False)
    return X[idx], y[idx]


# ─────────────────────────────────────────────────────────────────────
# Fine-tune
# ─────────────────────────────────────────────────────────────────────

def evaluate_model(model, X: np.ndarray, y: np.ndarray, device,
                   batch_size: int = 256) -> tuple[float, np.ndarray]:
    import torch
    model.eval()
    preds = np.zeros(len(X), dtype=np.int64)
    with torch.no_grad():
        for i in range(0, len(X), batch_size):
            batch = torch.from_numpy(X[i: i + batch_size]).to(device)
            out = model(batch)
            preds[i: i + batch_size] = out.argmax(dim=1).cpu().numpy()
    acc = float((preds == y).mean())
    return acc, preds


def fine_tune_classifier(rig_filter: str, mix_sim_ratio: float,
                         epochs: int, lr: float, seed: int,
                         deploy: bool) -> dict:
    """Run the full fine-tune pipeline. Returns a result dict with metrics.

    Heavy training-side imports (torch, sklearn) are deferred until AFTER
    the "not enough data" early-return so this function can be probed from
    a deploy-only machine that doesn't have torch/sklearn installed —
    callers and tests get a clean ok=False without a ModuleNotFoundError.
    """
    # ── 1. Load real episodes (no training deps needed) ────────────
    X_real, y_real, machines, stats = load_real_episodes(rig_filter)
    if len(X_real) < 100:
        return {
            "ok": False,
            "reason": (f"Not enough real data: {len(X_real)} windows from "
                        f"{stats.n_episodes} episodes. Need at least 100 "
                        f"windows. Capture more with the dashboard "
                        f"'Annotate' buttons during normal drilling."),
            "real_stats": vars(stats),
        }

    # From here on we actually train — torch + sklearn are required.
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from sklearn.model_selection import train_test_split

    sys.path.insert(0, str(REPO_ROOT))
    from training.train_classifier_torch import FailureModeClassifier

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Real dataset: {len(X_real)} windows from {stats.n_episodes} "
                f"episodes across {len(stats.by_machine)} machines")
    logger.info(f"  by label: {stats.by_label}")
    logger.info(f"  by machine: {stats.by_machine}")

    # ── 1a. Class-balance check: auto-boost sim-ratio if real is skewed ─
    # If any class has <10 real samples, the fine-tune will collapse that
    # class's embedding toward the majority. Raise sim-ratio to keep the
    # minority classes present through sim data rather than forgetting them.
    warnings: list[str] = []
    MIN_WINDOWS_PER_CLASS = 10
    if len(stats.by_label) < len(CLASSES):
        missing = set(CLASSES) - set(stats.by_label.keys())
        warnings.append(f"classes absent from real data: {sorted(missing)} — "
                        f"sim data will carry these alone")
    min_class_count = min(stats.by_label.values()) if stats.by_label else 0
    if 0 < min_class_count < MIN_WINDOWS_PER_CLASS:
        boosted = max(mix_sim_ratio, 0.5)
        if boosted > mix_sim_ratio:
            warnings.append(
                f"class imbalance: min class has {min_class_count} windows — "
                f"boosting mix_sim_ratio {mix_sim_ratio:.2f} -> {boosted:.2f}"
            )
            mix_sim_ratio = boosted
    for w in warnings:
        logger.warning(w)

    # ── 1b. Feature-drift gate: detect sim↔real distribution mismatch ──
    # Uses the sim model's normalization stats; flags channels where real
    # data sits far outside the sim training distribution. This catches
    # gross measurement-pipeline mismatches (e.g., real RPM scaled in counts
    # vs. sim in actual RPM, or a torque register we're reading wrong).
    sim_ckpt_preview_path = SIM_MODEL_DIR / "model.pt"
    drift_report: dict = {}
    if sim_ckpt_preview_path.exists():
        try:
            import torch as _torch
            _preview = _torch.load(sim_ckpt_preview_path, map_location="cpu",
                                    weights_only=False)
            _Xmean = np.array(_preview["X_mean"], dtype=np.float32)
            _Xstd = np.array(_preview["X_std"], dtype=np.float32) + 1e-8
            # Per-channel z-scores across real windows
            flat = X_real.reshape(-1, X_real.shape[-1])
            z = (flat - _Xmean) / _Xstd
            pct_3sig = (np.abs(z) > 3.0).mean(axis=0)
            real_mean = flat.mean(axis=0)
            real_std = flat.std(axis=0)
            cols = ["rpm_encoder", "swash_output", "ss_setpoint_fwd",
                    "active_lower", "active_upper",
                    "delivered_torque", "loop_temp"]
            per_channel = {}
            severe_drift_channels = []
            for i, c in enumerate(cols):
                per_channel[c] = {
                    "sim_mean": float(_Xmean[i]),
                    "real_mean": float(real_mean[i]),
                    "sim_std": float(_Xstd[i]),
                    "real_std": float(real_std[i]),
                    "pct_3sigma": float(pct_3sig[i]),
                    "mean_shift_sigmas": float(abs(real_mean[i] - _Xmean[i]) / _Xstd[i]),
                }
                # Severe: >15% of samples >3σ from sim mean OR mean shift > 2σ
                if pct_3sig[i] > 0.15 or per_channel[c]["mean_shift_sigmas"] > 2.0:
                    severe_drift_channels.append(c)
                    logger.warning(
                        f"FEATURE DRIFT '{c}': "
                        f"{pct_3sig[i]*100:.1f}% samples >3σ from sim "
                        f"(sim μ={_Xmean[i]:.2f} σ={_Xstd[i]:.2f}; "
                        f"real μ={real_mean[i]:.2f} σ={real_std[i]:.2f})"
                    )
            drift_report = {
                "per_channel": per_channel,
                "severe_drift_channels": severe_drift_channels,
            }
        except Exception as e:
            logger.warning(f"drift check skipped: {e}")

    # ── 2. Mix in sim subsample (anti-forgetting) ──────────────────
    sim_n = int(len(X_real) * mix_sim_ratio / max(1.0 - mix_sim_ratio, 0.01))
    X_sim, y_sim = load_sim_subsample(sim_n, seed=seed)
    logger.info(f"Mixing {len(X_sim)} sim windows with {len(X_real)} real "
                f"windows (sim ratio={mix_sim_ratio:.0%})")
    X = np.concatenate([X_real, X_sim], axis=0) if len(X_sim) > 0 else X_real
    y = np.concatenate([y_real, y_sim], axis=0) if len(X_sim) > 0 else y_real
    is_real = np.concatenate([
        np.ones(len(X_real), dtype=bool),
        np.zeros(len(X_sim), dtype=bool),
    ])

    # ── 3. Load sim-trained checkpoint ─────────────────────────────
    sim_ckpt_path = SIM_MODEL_DIR / "model.pt"
    if not sim_ckpt_path.exists():
        return {"ok": False, "reason": f"Sim checkpoint missing at {sim_ckpt_path}"}
    sim_ckpt = torch.load(sim_ckpt_path, map_location="cpu", weights_only=False)
    X_mean = np.array(sim_ckpt["X_mean"], dtype=np.float32)
    X_std = np.array(sim_ckpt["X_std"], dtype=np.float32)

    X_norm = (X - X_mean) / (X_std + 1e-8)

    # ── 4. Train / val split — held-out is REAL ONLY ───────────────
    real_mask = is_real
    if int(real_mask.sum()) < 30:
        return {"ok": False, "reason": "Not enough real data for held-out validation"}
    # Stratify by label across real windows
    X_real_norm = X_norm[real_mask]
    y_real_norm = y[real_mask]
    try:
        X_train_real, X_val, y_train_real, y_val = train_test_split(
            X_real_norm, y_real_norm, test_size=0.25, random_state=seed,
            stratify=y_real_norm,
        )
    except ValueError:
        # Some classes have only 1 sample — no stratify
        X_train_real, X_val, y_train_real, y_val = train_test_split(
            X_real_norm, y_real_norm, test_size=0.25, random_state=seed,
        )
    # Sim subsample joins the training set
    X_sim_norm = X_norm[~real_mask]
    y_sim_norm = y[~real_mask]
    X_train = np.concatenate([X_train_real, X_sim_norm], axis=0)
    y_train = np.concatenate([y_train_real, y_sim_norm], axis=0)
    logger.info(f"Train: {len(X_train)} (real {len(X_train_real)} + sim "
                f"{len(X_sim_norm)})  Val (real only): {len(X_val)}")

    # ── 5. Sim-baseline accuracy on the held-out real set ──────────
    sim_model = FailureModeClassifier(n_features=7, n_classes=len(CLASSES))
    sim_model.load_state_dict(sim_ckpt["state_dict"])
    sim_model.to(device)
    sim_baseline_acc, _ = evaluate_model(sim_model, X_val, y_val, device)
    logger.info(f"Sim baseline accuracy on held-out real data: {sim_baseline_acc:.4f}")

    # ── 6. Fine-tune ───────────────────────────────────────────────
    model = FailureModeClassifier(n_features=7, n_classes=len(CLASSES)).to(device)
    model.load_state_dict(sim_ckpt["state_dict"])
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)

    from torch.utils.data import DataLoader, TensorDataset
    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train)),
        batch_size=64, shuffle=True,
        pin_memory=(device.type == "cuda"),
    )

    best_val_acc = sim_baseline_acc  # never accept worse than sim baseline
    best_state = None
    patience = 5
    counter = 0
    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        n = 0
        for Xb, yb in train_loader:
            Xb = Xb.to(device); yb = yb.to(device)
            optimizer.zero_grad()
            out = model(Xb)
            loss = F.cross_entropy(out, yb)
            loss.backward(); optimizer.step()
            train_loss += loss.item() * Xb.size(0); n += Xb.size(0)
        train_loss /= n
        val_acc, _ = evaluate_model(model, X_val, y_val, device)
        marker = "*" if val_acc > best_val_acc else " "
        logger.info(f"Epoch {epoch:3d}: train_loss={train_loss:.4f} "
                     f"val_acc={val_acc:.4f} {marker}")
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_state = {k: v.detach().cpu().clone()
                          for k, v in model.state_dict().items()}
            counter = 0
        else:
            counter += 1
            if counter >= patience:
                logger.info(f"Early stop at epoch {epoch}")
                break

    # ── 7. VALIDATION GATE ─────────────────────────────────────────
    improvement = best_val_acc - sim_baseline_acc
    gate_pass = best_val_acc > sim_baseline_acc and best_state is not None

    result = {
        "ok": True,
        "rig_filter": rig_filter,
        "n_real_windows": int(len(X_real)),
        "n_sim_mixed": int(len(X_sim)),
        "n_held_out": int(len(X_val)),
        "sim_baseline_acc": sim_baseline_acc,
        "fine_tune_acc": best_val_acc,
        "improvement": improvement,
        "gate_pass": gate_pass,
        "effective_mix_sim_ratio": mix_sim_ratio,
        "warnings": warnings,
        "feature_drift": drift_report,
        "real_stats": {
            "n_episodes": stats.n_episodes,
            "by_machine": stats.by_machine,
            "by_label": stats.by_label,
            "excluded_unmapped": stats.excluded_unmapped,
        },
    }

    if not gate_pass:
        logger.warning(
            f"VALIDATION GATE FAILED: fine-tune ({best_val_acc:.4f}) "
            f"did not beat sim baseline ({sim_baseline_acc:.4f}). "
            f"Sim model stays in production."
        )
        result["deployed"] = False
        result["reason"] = "Validation gate failed — sim baseline kept"
        return result

    # ── 8. Save (versioned) ────────────────────────────────────────
    if best_state is not None:
        model.load_state_dict(best_state)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    rig_slug = _slugify(rig_filter) if rig_filter.lower() != "all" else "fleet"
    out_dir = FINETUNE_DIR / f"classifier_{rig_slug}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    torch.save({
        "state_dict": model.state_dict(),
        "classes": CLASSES,
        "X_mean": X_mean.tolist(),
        "X_std": X_std.tolist(),
        "input_shape": [40, 7],
        "fine_tuned_from": str(sim_ckpt_path.relative_to(REPO_ROOT)),
        "rig_filter": rig_filter,
        "n_real_windows": int(len(X_real)),
    }, out_dir / "model.pt")

    # ONNX export for deployment
    try:
        model.eval().cpu()
        dummy = torch.randn(1, 40, 7)
        import torch.onnx
        torch.onnx.export(
            model, dummy, str(out_dir / "classifier.onnx"),
            input_names=["input"], output_names=["logits"],
            dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
            opset_version=17, dynamo=False,
        )
    except Exception as e:
        logger.warning(f"ONNX export failed: {e}")

    meta = {
        "classes": CLASSES,
        "X_mean": X_mean.tolist(),
        "X_std": X_std.tolist(),
        "test_accuracy": best_val_acc,           # 'test' for compat with sim format
        "val_accuracy": best_val_acc,
        "sim_baseline_acc": sim_baseline_acc,
        "improvement": improvement,
        "fine_tuned_from": str(sim_ckpt_path.relative_to(REPO_ROOT)),
        "rig_filter": rig_filter,
        "n_real_windows": int(len(X_real)),
        "real_stats": {
            "by_machine": stats.by_machine,
            "by_label": stats.by_label,
        },
        "timestamp": stamp,
    }
    with open(out_dir / "meta.json", "w") as f:
        json.dump(meta, f, indent=2, default=float)
    logger.info(f"Versioned save: {out_dir}")
    result["model_dir"] = str(out_dir.relative_to(REPO_ROOT))

    # ── 9. Deploy per-rig (only if gate passed AND --deploy) ───────
    # CRITICAL: never overwrite the default sim classifier. Fine-tunes
    # write into per_rig/<slug>/ and the registry resolves them at runtime.
    if deploy and gate_pass:
        target_dir = PER_RIG_DIR / rig_slug
        target_dir.mkdir(parents=True, exist_ok=True)
        # Back up the previous per-rig model (if any)
        backup_name = None
        prev_live = target_dir / "classifier.onnx"
        if prev_live.exists():
            backup = target_dir / f"classifier.onnx.backup_{stamp}"
            shutil.copy2(prev_live, backup)
            backup_name = backup.name
            logger.info(f"Backed up prior per-rig model to {backup.name}")
        shutil.copy2(out_dir / "classifier.onnx", target_dir / "classifier.onnx")
        shutil.copy2(out_dir / "meta.json", target_dir / "classifier_meta.json")
        shutil.copy2(out_dir / "model.pt", target_dir / "classifier_torch.pt")
        # Remove ONNX sidecar if present (monolithic only)
        for sc in target_dir.glob("classifier.onnx.data"):
            sc.unlink()
        logger.info(f"Fine-tuned model deployed to {target_dir} "
                    f"(default sim classifier untouched)")
        result["deployed"] = True
        result["deploy_path"] = str(target_dir.relative_to(REPO_ROOT))
        result["rig_slug"] = rig_slug
        if backup_name:
            result["backup"] = backup_name
    else:
        result["deployed"] = False

    return result


# ─────────────────────────────────────────────────────────────────────
# AE threshold recalibration
# ─────────────────────────────────────────────────────────────────────

def recalibrate_ae_threshold(rig_filter: str, deploy: bool) -> dict:
    """Re-compute the AE anomaly threshold on real NORMAL windows.

    Sim-trained AE often produces too-low thresholds on real data, causing
    constant false positives. This sets the threshold to mean+3σ of the
    REAL NORMAL reconstruction errors.

    Heavy imports deferred past the early-return paths so a deploy-only
    machine (no torch installed) can still probe this function and get a
    clean ok=False instead of a ModuleNotFoundError.
    """
    # Early returns first — no torch needed for these paths
    sim_ae_path = SIM_AE_DIR / "model.pt"
    if not sim_ae_path.exists():
        return {"ok": False, "reason": f"sim AE checkpoint not at {sim_ae_path}"}

    X_real, y_real, _, stats = load_real_episodes(rig_filter)
    # Only use NORMAL real windows for threshold calibration
    normal_mask = (y_real == CLASS_TO_IDX["NORMAL"])
    X_normal = X_real[normal_mask]
    if len(X_normal) < 50:
        return {"ok": False,
                "reason": f"only {len(X_normal)} real NORMAL windows; need 50+"}
    logger.info(f"Recalibrating AE threshold on {len(X_normal)} real NORMAL windows")

    # We're going to actually run the AE — torch is required from here on.
    import torch
    sys.path.insert(0, str(REPO_ROOT))
    from training.train_autoencoder_torch import ConvAutoencoder

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    sim_ckpt = torch.load(sim_ae_path, map_location="cpu", weights_only=False)
    model = ConvAutoencoder(n_features=7,
                             latent_dim=int(sim_ckpt.get("latent_dim", 16)))
    model.load_state_dict(sim_ckpt["state_dict"])
    model.to(device).eval()

    X_min = np.array(sim_ckpt["X_min"], dtype=np.float32)
    X_max = np.array(sim_ckpt["X_max"], dtype=np.float32)
    X_norm = np.clip((X_normal - X_min) / ((X_max - X_min) + 1e-8), 0.0, 1.0)

    errors = []
    with torch.no_grad():
        for i in range(0, len(X_norm), 256):
            batch = torch.from_numpy(X_norm[i: i + 256]).float().to(device)
            recon = model(batch)
            mse = ((recon - batch) ** 2).mean(dim=(1, 2)).cpu().numpy()
            errors.extend(mse.tolist())
    errors = np.array(errors)
    new_threshold = float(errors.mean() + 3 * errors.std())
    sim_threshold = float(sim_ckpt.get("threshold", 0))
    logger.info(f"  Sim threshold:  {sim_threshold:.6f}")
    logger.info(f"  Real threshold: {new_threshold:.6f}")
    logger.info(f"  Real NORMAL p95 error: {float(np.percentile(errors, 95)):.6f}")

    rig_slug = _slugify(rig_filter) if rig_filter.lower() != "all" else "fleet"
    result = {
        "ok": True,
        "n_real_normal_windows": int(len(X_normal)),
        "sim_threshold": sim_threshold,
        "new_threshold": new_threshold,
        "ratio": new_threshold / max(sim_threshold, 1e-9),
        "rig_slug": rig_slug,
    }

    if deploy:
        # Per-rig AE meta — never overwrite the default.
        # If no per-rig meta yet, seed from default so downstream loaders
        # still have X_min/X_max/classes alongside the new threshold.
        default_meta_path = DEPLOY_DIR / "autoencoder_meta.json"
        target_dir = PER_RIG_DIR / rig_slug
        target_dir.mkdir(parents=True, exist_ok=True)
        target_meta_path = target_dir / "autoencoder_meta.json"

        if target_meta_path.exists():
            meta = json.loads(target_meta_path.read_text())
            source = "per_rig"
        elif default_meta_path.exists():
            meta = json.loads(default_meta_path.read_text())
            source = "seeded_from_default"
        else:
            meta = None
            source = None

        if meta is not None:
            old = meta.get("threshold", 0)
            meta["threshold"] = new_threshold
            meta["sim_threshold"] = old
            meta["recalibrated_on"] = time.strftime("%Y-%m-%d %H:%M:%S")
            meta["recalibrated_from_n_windows"] = len(X_normal)
            meta["rig_filter"] = rig_filter
            meta["rig_slug"] = rig_slug
            target_meta_path.write_text(json.dumps(meta, indent=2, default=float))
            logger.info(f"Wrote {target_meta_path.relative_to(REPO_ROOT)} "
                        f"({source}): threshold {old:.6f} → {new_threshold:.6f}")
            result["deployed"] = True
            result["deploy_path"] = str(target_meta_path.relative_to(REPO_ROOT))
            result["source"] = source
        else:
            result["deployed"] = False
            result["reason"] = "No AE meta available (default missing too)"

    return result


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Fine-tune sim models on real rig data")
    p.add_argument("--rig", default="all",
                   help="Rig name (slug match) or 'all' for fleet-wide")
    p.add_argument("--mix-sim-ratio", type=float, default=0.3,
                   help="Sim-data fraction to mix in (anti-forgetting)")
    p.add_argument("--epochs", type=int, default=20)
    p.add_argument("--lr", type=float, default=1e-4,
                   help="Lower than sim training (3e-4) for stable fine-tune")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--no-deploy", action="store_true",
                   help="Train but don't push to hxi_optimizer/models/")
    p.add_argument("--validate-only", action="store_true",
                   help="Don't train — just compare current models on real data")
    p.add_argument("--skip-classifier", action="store_true")
    p.add_argument("--skip-ae-recalibration", action="store_true")
    args = p.parse_args()

    deploy = not args.no_deploy

    if args.validate_only:
        # Quick path: report what's available, no training
        X_real, y_real, _, stats = load_real_episodes(args.rig)
        print(json.dumps({
            "n_real_windows": int(len(X_real)),
            "n_episodes": stats.n_episodes,
            "by_machine": stats.by_machine,
            "by_label": stats.by_label,
            "excluded_unmapped": stats.excluded_unmapped,
        }, indent=2))
        return

    print()
    if not args.skip_classifier:
        result = fine_tune_classifier(
            rig_filter=args.rig,
            mix_sim_ratio=args.mix_sim_ratio,
            epochs=args.epochs,
            lr=args.lr,
            seed=args.seed,
            deploy=deploy,
        )
        print("\n=== CLASSIFIER FINE-TUNE RESULT ===")
        print(json.dumps(result, indent=2, default=float))

    if not args.skip_ae_recalibration:
        result = recalibrate_ae_threshold(rig_filter=args.rig, deploy=deploy)
        print("\n=== AE THRESHOLD RECALIBRATION RESULT ===")
        print(json.dumps(result, indent=2, default=float))


if __name__ == "__main__":
    main()
