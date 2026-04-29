"""HXI Optimizer configuration.

Per MASTER_CONTEXT §10. Module name is `hxi_config` (not `config`) to avoid
collision with the existing legacy `config.py` at the repo root.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hxi_config")


class Phase(str, Enum):
    A = "A"  # Observer — no writes (commissioning + data collection)
    B = "B"  # Advisory — computes recommendations, logs only
    C = "C"  # Limited authority — gated writes via SafetyGate
    D = "D"  # Full authority


@dataclass
class SafetyLimitsConfig:
    """Hardware safety limits for %R06603 (lower) / %R06604 (upper).

    Defaults are None on purpose: SafetyGate refuses to instantiate until
    Steve / hydraulic engineer signs off the absolute counts.
    """
    abs_min_lower: Optional[int] = None
    abs_max_lower: Optional[int] = None
    abs_min_upper: Optional[int] = None
    abs_max_upper: Optional[int] = None
    min_band_counts: int = 50


@dataclass
class Config:
    # PLC connection
    plc_host: str = "CONFIGURE_ME"
    plc_port: int = 502
    unit_id: int = 1
    modbus_timeout: float = 2.0

    # Register read block (%R06600..%R06627 = 28 words)
    read_start_addr: int = 6599   # 0-indexed → %R06600
    read_count: int = 28

    # Timing
    read_interval: float = 0.500     # 2 Hz target
    analysis_interval: float = 10.0  # 0.1 Hz
    write_interval: float = 10.0     # 0.1 Hz max write cadence

    # Control parameters
    nominal_setpoint: float = 60.0
    deadband_rpm: float = 2.0  # SET FROM COMMISSIONING TEST 4

    # Phase gate (controls whether writes occur)
    phase: Phase = Phase.A

    # Safety limits
    safety: SafetyLimitsConfig = field(default_factory=SafetyLimitsConfig)

    # Oscillation tuner
    osc_enabled: bool = False
    drill_depth_ft: float = 3000.0
    pdm_motor_constant: Optional[float] = None  # ft-lb/psi from PDM spec sheet

    # Refuse-to-start switch — overridden after byte-order commissioning passes
    require_verified_word_order: bool = True

    # Machine identity (populated by fleet catalog on first connect)
    ewon_name: Optional[str] = None           # e.g. "Precision Rig 707 3pd HT"
    equipment_type: Optional[str] = None      # e.g. "hxi_ht"  (auto-filled)
    auto_detect_machine: bool = True          # recheck identity every 30 s

    # eCatcher / Talk2m integration (auto-detect which eWon is connected)
    # Without these, the optimizer falls back to log parsing + adapter probing.
    # Talk2m developer API: https://developer.ewon.biz/content/talk2m-developer-api
    talk2m_account: Optional[str] = None
    talk2m_username: Optional[str] = None
    talk2m_password: Optional[str] = None
    talk2m_developer_id: Optional[str] = None
    ecatcher_log_path: Optional[str] = None   # auto-detected if None
    ecatcher_poll_interval_s: float = 30.0

    # Real-time dataset capture (for future model fine-tuning)
    dataset_capture_enabled: bool = True      # writes labelled episodes to disk
    dataset_dir: str = "hxi_optimizer/logs/dataset"
    auto_segment_connections: bool = True     # split CSV on RPM-to-zero events

    # Dashboard — security + concurrency knobs for production deployment.
    # If `dashboard_token` is unset AND the HXI_DASHBOARD_TOKEN env var is
    # unset, auth is DISABLED (backwards compat for local dev). Once either
    # is set, every non-static / non-healthz request must carry
    # `Authorization: Bearer <token>` (or `?token=...` for WebSocket).
    dashboard_token: Optional[str] = None
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8420
    dashboard_endpoint_timeout_s: float = 30.0      # long endpoints aborted after this
    dashboard_max_body_bytes: int = 1_000_000       # 1 MB POST body cap
    dashboard_max_concurrent: int = 64              # uvicorn limit_concurrency

    # Runtime mode — set by the launcher (run.py) to "sim" or "real".
    # Pure metadata; the dashboard surfaces it so operators can see at a
    # glance whether they're talking to the simulator or a real PLC.
    runtime_mode: str = "real"

    # Transport layer — "modbus" (default) or "opcua"
    transport: str = "modbus"
    # OPC UA specific (ignored when transport=="modbus")
    opcua_port: int = 4840
    opcua_security: str = "None"          # "None" | "Basic256Sha256"
    opcua_username: Optional[str] = None
    opcua_password: Optional[str] = None
    opcua_cert_path: Optional[str] = None
    opcua_node_map_file: str = "hxi_optimizer/comms/opcua_nodes.json"


def load_config(path: str = "hxi_config.json") -> Config:
    cfg = Config()
    if not os.path.exists(path):
        logger.warning(f"{path} not found — using built-in defaults")
    else:
        with open(path) as f:
            data = json.load(f)
        for k, v in data.items():
            if k == "safety":
                for sk, sv in v.items():
                    if hasattr(cfg.safety, sk):
                        setattr(cfg.safety, sk, sv)
            elif k == "phase":
                cfg.phase = Phase(v)
            elif hasattr(cfg, k):
                setattr(cfg, k, v)

    # Env-var overrides applied LAST so the launcher (run.py) can switch
    # sim ↔ real without touching hxi_config.json on disk. Keeps the
    # persistent config rig-specific while letting the launcher steer
    # this particular run.
    env_overrides = {
        "HXI_PLC_HOST": ("plc_host", str),
        "HXI_PLC_PORT": ("plc_port", int),
        "HXI_RUNTIME_MODE": ("runtime_mode", str),
        "HXI_DASHBOARD_PORT": ("dashboard_port", int),
    }
    for env_key, (cfg_key, caster) in env_overrides.items():
        val = os.environ.get(env_key)
        if val:
            try:
                setattr(cfg, cfg_key, caster(val))
                logger.info(f"config override from env {env_key}={val}")
            except (ValueError, TypeError) as e:
                logger.warning(f"ignoring bad {env_key}={val!r}: {e}")
    return cfg


def dump_template(path: str = "hxi_config.template.json") -> None:
    cfg = Config()
    d = asdict(cfg)
    d["phase"] = cfg.phase.value
    Path(path).write_text(json.dumps(d, indent=2))
