"""Remote GPU machine configuration.

Usage:
    1. Copy training/remote_config.template.yaml → training/remote_config.yaml
    2. Fill in your GPU machine's SSH details
    3. Run:  python -m training.auto_pipeline --config training/remote_config.yaml

The system uses SSH key-based auth only — no passwords stored anywhere.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("remote_config")

CONFIG_FILE = Path("training/remote_config.yaml")
TEMPLATE_FILE = Path("training/remote_config.template.yaml")


@dataclass
class GPUMachineConfig:
    # SSH connection
    host: str = ""
    port: int = 22
    user: str = ""
    ssh_key_path: str = ""           # path to private key (id_rsa / id_ed25519)
    ssh_key_passphrase: str = ""     # if key is encrypted (leave empty if not)

    # Remote paths
    remote_base: str = "~/hxi_training"
    remote_venv: str = "~/hxi_training/venv"

    # GPU settings
    gpu_id: int = 0                  # CUDA_VISIBLE_DEVICES
    mixed_precision: bool = True     # fp16 training (disable for GTX 1650)

    # Local paths (on rig PC)
    local_logs_dir: str = "hxi_optimizer/logs"
    local_models_dir: str = "training/models"
    local_data_dir: str = "training/data"

    # Training defaults
    classifier_epochs: int = 100
    autoencoder_epochs: int = 300
    gain_scheduler_epochs: int = 200
    batch_size: int = 128
    sim_scenarios_per_type: int = 200

    # Auto-retrain schedule
    retrain_after_hours: float = 168.0  # 7 days of new data triggers retrain


def load_remote_config(path: str | Path = CONFIG_FILE) -> GPUMachineConfig:
    path = Path(path)
    if not path.exists():
        logger.error(f"{path} not found. Copy {TEMPLATE_FILE} and fill in SSH details.")
        raise FileNotFoundError(f"{path} — see {TEMPLATE_FILE}")
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    cfg = GPUMachineConfig()
    for k, v in data.items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, v)
    # Expand ~ in paths
    cfg.ssh_key_path = os.path.expanduser(cfg.ssh_key_path)
    cfg.remote_base = cfg.remote_base  # expanded on remote side
    _validate(cfg)
    return cfg


def _validate(cfg: GPUMachineConfig) -> None:
    if not cfg.host:
        raise ValueError("remote_config.yaml: 'host' is required")
    if not cfg.user:
        raise ValueError("remote_config.yaml: 'user' is required")
    if not cfg.ssh_key_path:
        raise ValueError("remote_config.yaml: 'ssh_key_path' is required")
    key = Path(cfg.ssh_key_path)
    if not key.exists():
        raise FileNotFoundError(f"SSH key not found: {key}")
    logger.info(f"Remote config loaded: {cfg.user}@{cfg.host}:{cfg.port}")


def write_template() -> None:
    """Generate the template YAML for the user to fill in."""
    template = """\
# HXI Training — Remote GPU Machine Configuration
# Copy this file to remote_config.yaml and fill in your details.

# ─── SSH Connection ───────────────────────────────────────────────
host: ""                    # e.g. "192.168.1.100" or "gpu.example.com"
port: 22
user: ""                    # SSH username
ssh_key_path: "~/.ssh/id_ed25519"   # path to your private key
ssh_key_passphrase: ""      # leave empty if key has no passphrase

# ─── Remote Machine Paths ────────────────────────────────────────
remote_base: "~/hxi_training"       # training workspace on GPU machine
remote_venv: "~/hxi_training/venv"  # Python venv (auto-created)

# ─── GPU Settings ─────────────────────────────────────────────────
gpu_id: 0                   # CUDA_VISIBLE_DEVICES
mixed_precision: true       # fp16 — set false for GTX 1650 (4GB VRAM)

# ─── Local Paths (on rig PC) ─────────────────────────────────────
local_logs_dir: "hxi_optimizer/logs"
local_models_dir: "training/models"
local_data_dir: "training/data"

# ─── Training Hyperparameters ─────────────────────────────────────
classifier_epochs: 100
autoencoder_epochs: 300
gain_scheduler_epochs: 200
batch_size: 128
sim_scenarios_per_type: 200

# ─── Auto-Retrain ────────────────────────────────────────────────
retrain_after_hours: 168    # 7 days of new data triggers retrain
"""
    TEMPLATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATE_FILE.write_text(template)
    logger.info(f"Template written to {TEMPLATE_FILE}")
