"""End-to-end local training pipeline — no SSH, no remote GPU.

Does in one command what `auto_pipeline.py --real-data --deploy` does remotely:
    1. Generate the simulation dataset (all equipment types, optional weighting)
    2. Window it
    3. Train the 1D-CNN classifier
    4. Train the Conv-AE anomaly detector
    5. Export both to ONNX
    6. Deploy both into hxi_optimizer/models/
    7. Validate by importing onnxruntime and running one inference

Usage:
    # Default run (balanced across equipment types)
    python -m training.local_pipeline

    # Bigger dataset
    python -m training.local_pipeline --per-scenario 60

    # Weighted by real fleet composition
    python -m training.local_pipeline --fleet-weighted --per-scenario 40

    # Skip AE if only the classifier changed
    python -m training.local_pipeline --skip-autoencoder
"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("local_pipeline")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "training" / "data"
MODELS_DIR = REPO_ROOT / "training" / "models"
DEPLOY_DIR = REPO_ROOT / "hxi_optimizer" / "models"


def _run(cmd: list[str], label: str) -> None:
    """Run a Python subcommand, stream output."""
    t0 = time.time()
    logger.info(f"===== {label} =====")
    logger.info("  " + " ".join(cmd))
    # PYTHONIOENCODING=utf-8 so Unicode in logs doesn't crash on cp1252 consoles
    import os
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(cmd, cwd=REPO_ROOT, env=env)
    elapsed = time.time() - t0
    if r.returncode != 0:
        logger.error(f"[FAIL] {label} (exit {r.returncode}) after {elapsed:.1f}s")
        sys.exit(r.returncode)
    logger.info(f"[OK] {label} done in {elapsed:.1f}s")


def _deploy_classifier() -> None:
    src_dir = MODELS_DIR / "classifier_torch"
    dst = DEPLOY_DIR
    dst.mkdir(parents=True, exist_ok=True)

    # Prefer monolithic ONNX — re-export to be safe
    import torch
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT))
    from training.train_classifier_torch import FailureModeClassifier, CLASSES
    ckpt = torch.load(src_dir / "model.pt", map_location="cpu", weights_only=False)
    model = FailureModeClassifier(n_features=7, n_classes=len(CLASSES))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    dummy = torch.randn(1, 40, 7)
    torch.onnx.export(
        model, dummy, str(dst / "classifier.onnx"),
        input_names=["input"], output_names=["logits"],
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=17, dynamo=False,
    )
    # Remove external-data sidecar if present
    sidecar = dst / "classifier.onnx.data"
    if sidecar.exists():
        sidecar.unlink()
    shutil.copy2(src_dir / "meta.json", dst / "classifier_meta.json")
    shutil.copy2(src_dir / "model.pt", dst / "classifier_torch.pt")
    logger.info(f"[OK] Classifier deployed to {dst}")


def _deploy_autoencoder() -> None:
    src_dir = MODELS_DIR / "autoencoder_torch"
    dst = DEPLOY_DIR
    # Re-export the AE monolithically too
    import torch
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT))
    from training.train_autoencoder_torch import ConvAutoencoder
    ckpt = torch.load(src_dir / "model.pt", map_location="cpu", weights_only=False)
    model = ConvAutoencoder(n_features=7, latent_dim=int(ckpt.get("latent_dim", 16)))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    dummy = torch.randn(1, 40, 7)
    torch.onnx.export(
        model, dummy, str(dst / "autoencoder.onnx"),
        input_names=["input"], output_names=["reconstruction"],
        dynamic_axes={"input": {0: "batch"}, "reconstruction": {0: "batch"}},
        opset_version=17, dynamo=False,
    )
    sidecar = dst / "autoencoder.onnx.data"
    if sidecar.exists():
        sidecar.unlink()
    shutil.copy2(src_dir / "meta.json", dst / "autoencoder_meta.json")
    logger.info(f"[OK] Autoencoder deployed to {dst}")


def _verify_deployment() -> None:
    logger.info("===== VERIFY =====")
    import onnxruntime as ort
    import json
    import numpy as np

    cls_path = DEPLOY_DIR / "classifier.onnx"
    ae_path = DEPLOY_DIR / "autoencoder.onnx"

    if cls_path.exists():
        sess = ort.InferenceSession(str(cls_path), providers=["CPUExecutionProvider"])
        x = np.random.randn(1, 40, 7).astype(np.float32)
        out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
        meta = json.loads((DEPLOY_DIR / "classifier_meta.json").read_text())
        logger.info(f"  classifier:  size={cls_path.stat().st_size:,}B "
                     f"out_shape={out.shape} "
                     f"test_acc={meta.get('test_accuracy', 0):.4f}")

    if ae_path.exists():
        sess = ort.InferenceSession(str(ae_path), providers=["CPUExecutionProvider"])
        x = np.random.randn(1, 40, 7).astype(np.float32)
        out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
        meta = json.loads((DEPLOY_DIR / "autoencoder_meta.json").read_text())
        logger.info(f"  autoencoder: size={ae_path.stat().st_size:,}B "
                     f"out_shape={out.shape} "
                     f"threshold={meta.get('threshold', 0):.6f} "
                     f"separation={meta.get('separation_ratio', 0):.2f}x")

    logger.info("[OK] Both models inference-verified with onnxruntime")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--per-scenario", type=int, default=40,
                   help="Runs per (scenario × equipment) combo")
    p.add_argument("--fleet-weighted", action="store_true",
                   help="Weight runs by real fleet counts")
    p.add_argument("--classifier-epochs", type=int, default=50)
    p.add_argument("--ae-epochs", type=int, default=40)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--skip-dataset", action="store_true",
                   help="Reuse existing sim_full.npz + windows_full.npz")
    p.add_argument("--skip-classifier", action="store_true")
    p.add_argument("--skip-autoencoder", action="store_true")
    p.add_argument("--no-deploy", action="store_true",
                   help="Train but don't copy to hxi_optimizer/models/")
    args = p.parse_args()

    t_total = time.time()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_npz = DATA_DIR / "sim_full.npz"
    win_npz = DATA_DIR / "windows_full.npz"

    py = sys.executable

    # ── 1) Generate dataset ─────────────────────────────────────────
    if not args.skip_dataset or not raw_npz.exists():
        gen_cmd = [py, "-m", "training.generate_dataset",
                   "--per-scenario", str(args.per_scenario),
                   "--equipment-types", "all",
                   "--output", str(raw_npz)]
        if args.fleet_weighted:
            gen_cmd.append("--fleet-weighted")
        _run(gen_cmd, "GENERATE dataset")
    else:
        logger.info(f"[SKIP] dataset (reusing {raw_npz.name})")

    # ── 2) Window the dataset ───────────────────────────────────────
    if not args.skip_dataset or not win_npz.exists():
        _run([py, "-m", "training.prepare_windows",
              "--input", str(raw_npz), "--output", str(win_npz),
              "--window-size", "40", "--stride", "10"],
             "WINDOW dataset")
    else:
        logger.info(f"[SKIP] windowing (reusing {win_npz.name})")

    # ── 3) Train classifier ─────────────────────────────────────────
    if not args.skip_classifier:
        _run([py, "-m", "training.train_classifier_torch",
              "--data", str(win_npz),
              "--model-out", str(MODELS_DIR / "classifier_torch"),
              "--epochs", str(args.classifier_epochs),
              "--batch-size", str(args.batch_size)],
             "TRAIN classifier")
    else:
        logger.info("[SKIP] classifier")

    # ── 4) Train autoencoder ────────────────────────────────────────
    if not args.skip_autoencoder:
        _run([py, "-m", "training.train_autoencoder_torch",
              "--data", str(win_npz),
              "--model-out", str(MODELS_DIR / "autoencoder_torch"),
              "--epochs", str(args.ae_epochs),
              "--batch-size", str(args.batch_size)],
             "TRAIN autoencoder")
    else:
        logger.info("[SKIP] autoencoder")

    # ── 5) Deploy ────────────────────────────────────────────────────
    if not args.no_deploy:
        logger.info("===== DEPLOY =====")
        if not args.skip_classifier:
            _deploy_classifier()
        if not args.skip_autoencoder:
            _deploy_autoencoder()
        _verify_deployment()
    else:
        logger.info("[SKIP] deploy (use --deploy later with training.deploy)")

    elapsed = time.time() - t_total
    logger.info("")
    logger.info(f"===== PIPELINE COMPLETE in {elapsed/60:.1f} min =====")
    logger.info(f"  Dataset:     {raw_npz}")
    logger.info(f"  Windows:     {win_npz}")
    logger.info(f"  Classifier:  {MODELS_DIR / 'classifier_torch'}")
    logger.info(f"  Autoencoder: {MODELS_DIR / 'autoencoder_torch'}")
    if not args.no_deploy:
        logger.info(f"  Deployed to: {DEPLOY_DIR}")
    logger.info("")
    logger.info("Restart the optimizer to pick up the new models.")


if __name__ == "__main__":
    main()
