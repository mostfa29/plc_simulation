"""One-command automated training pipeline.

Connects to the remote GPU machine via SSH, uploads data, runs all training
jobs, downloads models, and optionally hot-deploys them into the running
optimizer.

Usage:
    # Full pipeline (sim data → train → download models)
    python -m training.auto_pipeline --config training/remote_config.yaml

    # With real data from Phase A/B logs
    python -m training.auto_pipeline --config training/remote_config.yaml --real-data

    # Train only classifier
    python -m training.auto_pipeline --config training/remote_config.yaml --only classifier

    # Deploy downloaded models into running optimizer
    python -m training.auto_pipeline --config training/remote_config.yaml --deploy
"""
from __future__ import annotations

import argparse
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-18s %(levelname)-8s: %(message)s",
)
logger = logging.getLogger("auto_pipeline")


def main():
    parser = argparse.ArgumentParser(description="HXI Automated ML Training Pipeline")
    parser.add_argument("--config", default="training/remote_config.yaml",
                        help="Path to remote_config.yaml")
    parser.add_argument("--only", default="all",
                        choices=["all", "classifier", "autoencoder", "gain_scheduler", "sim_only"],
                        help="Which model to train (default: all)")
    parser.add_argument("--real-data", action="store_true",
                        help="Upload and include real CSV logs from rig PC")
    parser.add_argument("--deploy", action="store_true",
                        help="Hot-deploy downloaded models to running optimizer")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without executing")
    parser.add_argument("--setup-only", action="store_true",
                        help="Only set up remote environment, don't train")
    args = parser.parse_args()

    from training.remote_config import load_remote_config
    cfg = load_remote_config(args.config)

    if args.dry_run:
        logger.info("=== DRY RUN — no remote commands will execute ===")
        logger.info(f"Would connect to {cfg.user}@{cfg.host}:{cfg.port}")
        logger.info(f"SSH key: {cfg.ssh_key_path}")
        logger.info(f"Remote base: {cfg.remote_base}")
        logger.info(f"GPU: {cfg.gpu_id}, mixed precision: {cfg.mixed_precision}")
        logger.info(f"Models to train: {args.only}")
        logger.info(f"Real data: {args.real_data}")
        logger.info(f"Deploy: {args.deploy}")
        return

    from training.remote_runner import RemoteRunner

    with RemoteRunner(cfg) as runner:
        t0 = time.time()

        # ─── Step 0: Health check ───────────────────────────────────────
        logger.info("=" * 60)
        logger.info("STEP 0: Remote health check")
        logger.info("=" * 60)
        gpu = runner.check_gpu()
        if gpu.get("available"):
            logger.info(f"GPU: {gpu['gpu_name']} — "
                        f"{gpu['memory_free_mb']}MB free / "
                        f"{gpu['memory_total_mb']}MB total")
        else:
            logger.warning("No GPU detected — training will use CPU (slow)")
        disk = runner.check_disk_space()
        if disk:
            logger.info(f"Disk: {disk.get('avail_gb', '?')}GB available")

        # ─── Step 1: Environment setup ──────────────────────────────────
        logger.info("=" * 60)
        logger.info("STEP 1: Setting up remote environment")
        logger.info("=" * 60)
        runner.setup_remote_env()

        if args.setup_only:
            logger.info("Setup complete. Exiting (--setup-only).")
            return

        # ─── Step 2: Upload training scripts ────────────────────────────
        logger.info("=" * 60)
        logger.info("STEP 2: Uploading training scripts")
        logger.info("=" * 60)
        runner.upload_training_scripts()

        # ─── Step 3: Generate simulation data (on remote GPU) ───────────
        logger.info("=" * 60)
        logger.info("STEP 3: Generating simulation dataset")
        logger.info("=" * 60)
        mp_flag = "--mixed-precision" if cfg.mixed_precision else ""
        runner.run_training_job(
            "generate_dataset.py",
            f"--per-scenario {cfg.sim_scenarios_per_type} "
            f"--output {cfg.remote_base}/data/sim_dataset.npz",
            timeout=600,
        )

        # ─── Step 4: Window the data ───────────────────────────────────
        logger.info("=" * 60)
        logger.info("STEP 4: Preparing windowed dataset")
        logger.info("=" * 60)
        runner.run_training_job(
            "prepare_windows.py",
            f"--input {cfg.remote_base}/data/sim_dataset.npz "
            f"--output {cfg.remote_base}/data/windows.npz "
            f"--window-size 40 --stride 10",
            timeout=300,
        )

        # ─── Step 5: Upload real CSV logs (optional) ───────────────────
        if args.real_data:
            logger.info("=" * 60)
            logger.info("STEP 5: Uploading real CSV logs")
            logger.info("=" * 60)
            n = runner.upload_csv_logs()
            logger.info(f"Uploaded {n} CSV files")
        else:
            logger.info("STEP 5: Skipped (no --real-data flag)")

        # ─── Step 6: Train models ──────────────────────────────────────
        if args.only in ("all", "classifier"):
            logger.info("=" * 60)
            logger.info("STEP 6a: Training failure mode classifier")
            logger.info("=" * 60)
            runner.run_training_job(
                "train_classifier.py",
                f"--data {cfg.remote_base}/data/windows.npz "
                f"--model-out {cfg.remote_base}/models/classifier "
                f"--epochs {cfg.classifier_epochs} "
                f"--batch-size {cfg.batch_size} "
                f"{mp_flag}",
                timeout=3600,
            )

        if args.only in ("all", "autoencoder"):
            logger.info("=" * 60)
            logger.info("STEP 6b: Training condition-change autoencoder")
            logger.info("=" * 60)
            runner.run_training_job(
                "train_autoencoder.py",
                f"--data {cfg.remote_base}/data/windows.npz "
                f"--model-out {cfg.remote_base}/models/autoencoder "
                f"--epochs {cfg.autoencoder_epochs} "
                f"--batch-size {cfg.batch_size} "
                f"{mp_flag}",
                timeout=7200,
            )

        if args.only in ("all", "gain_scheduler") and args.real_data:
            logger.info("=" * 60)
            logger.info("STEP 6c: Training gain scheduler (from real data)")
            logger.info("=" * 60)
            runner.run_training_job(
                "train_gain_scheduler.py",
                f"--csv-dir {cfg.remote_base}/data/csv_logs "
                f"--model-out {cfg.remote_base}/models/gain_scheduler "
                f"--epochs {cfg.gain_scheduler_epochs} "
                f"--batch-size {cfg.batch_size}",
                timeout=3600,
            )
        elif args.only in ("all", "gain_scheduler"):
            logger.info("STEP 6c: Skipped gain scheduler (needs --real-data)")

        # ─── Step 7: Download models ──────────────────────────────────
        logger.info("=" * 60)
        logger.info("STEP 7: Downloading trained models")
        logger.info("=" * 60)
        n = runner.download_models()
        logger.info(f"Downloaded {n} model files")
        runner.download_training_logs()

        elapsed = time.time() - t0
        logger.info("=" * 60)
        logger.info(f"PIPELINE COMPLETE in {elapsed/60:.1f} minutes")
        logger.info(f"Models in: {cfg.local_models_dir}/")
        logger.info("=" * 60)

    # ─── Step 8: Deploy (optional) ──────────────────────────────────────
    if args.deploy:
        logger.info("=" * 60)
        logger.info("STEP 8: Deploying models to running optimizer")
        logger.info("=" * 60)
        from training.deploy import deploy_models
        deploy_models(cfg.local_models_dir)


if __name__ == "__main__":
    main()
