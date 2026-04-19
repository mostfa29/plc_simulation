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


def load_config(path: str = "hxi_config.json") -> Config:
    cfg = Config()
    if not os.path.exists(path):
        logger.warning(f"{path} not found — using built-in defaults")
        return cfg
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
    return cfg


def dump_template(path: str = "hxi_config.template.json") -> None:
    cfg = Config()
    d = asdict(cfg)
    d["phase"] = cfg.phase.value
    Path(path).write_text(json.dumps(d, indent=2))
