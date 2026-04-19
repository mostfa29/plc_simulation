"""Hot-deploy trained TFLite models into the running HXI optimizer.

Copies .tflite + meta.json into hxi_optimizer/models/ and signals the
optimizer to reload (via a reload flag file that main.py watches).

Usage:
    python -m training.deploy --models-dir training/models
    python -m training.deploy --models-dir training/models --validate-only
"""
from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("deploy")

DEPLOY_DIR = Path("hxi_optimizer/models")
RELOAD_FLAG = Path("hxi_optimizer/models/.reload")

MODEL_FILES = {
    "classifier": ("classifier.tflite", "classifier/meta.json"),
    "autoencoder": ("autoencoder.tflite", "autoencoder/meta.json"),
    "gain_scheduler": ("gain_scheduler.tflite", "gain_scheduler/meta.json"),
}


def validate_tflite(path: Path) -> dict:
    """Basic validation: loads the TFLite model and checks input/output shapes."""
    try:
        import tensorflow as tf
        interp = tf.lite.Interpreter(model_path=str(path))
        interp.allocate_tensors()
        inp = interp.get_input_details()
        out = interp.get_output_details()
        return {
            "valid": True,
            "input_shape": inp[0]["shape"].tolist(),
            "output_shape": out[0]["shape"].tolist(),
            "size_bytes": path.stat().st_size,
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


def deploy_models(models_dir: str, validate_only: bool = False) -> dict:
    """Copy trained models to the optimizer's model directory."""
    models_dir = Path(models_dir)
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    results = {}

    for name, (tflite_name, meta_rel) in MODEL_FILES.items():
        # Look for the tflite in models_dir or models_dir/name/
        candidates = [
            models_dir / tflite_name,
            models_dir / name / tflite_name,
        ]
        tflite_src = next((p for p in candidates if p.exists()), None)
        if not tflite_src:
            logger.info(f"  {name}: not found — skipping")
            results[name] = {"deployed": False, "reason": "file not found"}
            continue

        # Validate
        info = validate_tflite(tflite_src)
        logger.info(f"  {name}: {info}")
        if not info["valid"]:
            results[name] = {"deployed": False, "reason": info["error"]}
            continue

        if validate_only:
            results[name] = {"validated": True, **info}
            continue

        # Copy tflite
        dst = DEPLOY_DIR / tflite_name
        shutil.copy2(tflite_src, dst)
        logger.info(f"  {name}: deployed {dst} ({info['size_bytes']:,} bytes)")

        # Copy meta.json if it exists
        meta_src = models_dir / meta_rel
        if not meta_src.exists():
            meta_src = models_dir / name / "meta.json"
        if meta_src.exists():
            meta_dst = DEPLOY_DIR / f"{name}_meta.json"
            shutil.copy2(meta_src, meta_dst)

        results[name] = {"deployed": True, **info}

    if not validate_only:
        # Write reload flag — optimizer watches this
        RELOAD_FLAG.write_text(f"reload_at={__import__('time').time()}")
        logger.info(f"Reload flag set: {RELOAD_FLAG}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Deploy TFLite models to optimizer")
    parser.add_argument("--models-dir", default="training/models")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    results = deploy_models(args.models_dir, args.validate_only)
    for name, info in results.items():
        status = "OK" if info.get("deployed") or info.get("validated") else "SKIP"
        logger.info(f"  {name}: {status}")


if __name__ == "__main__":
    main()
