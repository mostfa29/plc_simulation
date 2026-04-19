"""Shared fixtures for the HXI optimizer test suite."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── Stub pymodbus before any hxi_optimizer import ──────────────────────────
import sys
import types

if "pymodbus" not in sys.modules:
    _pm = types.ModuleType("pymodbus")
    _pm.FramerType = types.SimpleNamespace(SOCKET="socket")
    sys.modules["pymodbus"] = _pm

    _pmc = types.ModuleType("pymodbus.client")

    class _FakeAsyncClient:
        def __init__(self, **kw):
            self.connected = False

        async def connect(self):
            self.connected = True

        def close(self):
            self.connected = False

    _pmc.AsyncModbusTcpClient = _FakeAsyncClient
    sys.modules["pymodbus.client"] = _pmc

    _pme = types.ModuleType("pymodbus.exceptions")
    _pme.ConnectionException = type("ConnectionException", (Exception,), {})
    _pme.ModbusIOException = type("ModbusIOException", (Exception,), {})
    sys.modules["pymodbus.exceptions"] = _pme


from hxi_optimizer.comms import register_map
from hxi_optimizer.control.safety_gate import (
    AdaptState, LastKnownGood, SafetyConfig, SafetyGate,
)
from hxi_optimizer.control.pid_advisor import (
    AdaptationState, BoundsAdvisorConfig, PIDAdvisor,
)
from hxi_optimizer.control.oscillation_tuner import OscConfig, OscillationTuner
from hxi_optimizer.monitoring.performance_metrics import (
    CUSUMDetector, PerformanceMetrics, PerformanceMonitor,
)
from hxi_optimizer.io_logging.audit_logger import AuditLogger
from hxi_optimizer.io_logging.csv_logger import CrashSafeCSVLogger, build_csv_row
from hxi_optimizer.state.persistence import load_state, save_state
from hxi_optimizer.hxi_config import Config, Phase, SafetyLimitsConfig, load_config


# ─── Common safety-limits fixture (mid-range, plausible) ────────────────────

SAFE_LIMITS = SafetyConfig(
    abs_min_lower=50,
    abs_max_lower=700,
    abs_min_upper=300,
    abs_max_upper=950,
    min_band_counts=50,
)


@pytest.fixture
def safety_cfg():
    return SafetyConfig(
        abs_min_lower=50,
        abs_max_lower=700,
        abs_min_upper=300,
        abs_max_upper=950,
        min_band_counts=50,
    )


@pytest.fixture
def mock_modbus():
    m = AsyncMock()
    m.safe_read = AsyncMock(return_value=[400, 600])
    m.safe_write_registers = AsyncMock(return_value=True)
    m.is_healthy = True
    m.consecutive_failures = 0
    m.client = MagicMock()
    m.client.connected = True
    return m


@pytest.fixture
def mock_audit(tmp_path):
    return AuditLogger(tmp_path / "audit.csv")


@pytest.fixture
def gate(safety_cfg, mock_modbus, mock_audit):
    g = SafetyGate(safety_cfg, mock_modbus, mock_audit)
    g.current_lower = 400
    g.current_upper = 600
    g.lkg = LastKnownGood(lower=400, upper=600, timestamp=1000.0,
                          iae_at_acceptance=0.05)
    return g


@pytest.fixture
def advisor_cfg():
    return BoundsAdvisorConfig(
        abs_min_lower=50,
        abs_max_lower=700,
        abs_min_upper=300,
        abs_max_upper=950,
    )


@pytest.fixture
def advisor(advisor_cfg):
    return PIDAdvisor(advisor_cfg)


@pytest.fixture
def osc_cfg():
    return OscConfig(depth_ft=5000.0, C_motor=5.0)


@pytest.fixture
def tuner(osc_cfg):
    return OscillationTuner(osc_cfg)


@pytest.fixture
def monitor():
    return PerformanceMonitor(window_sec=20.0, deadband_rpm=2.0)


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_word_order():
    """Reset VERIFIED_WORD_ORDER before each test."""
    original = register_map.VERIFIED_WORD_ORDER
    yield
    register_map.VERIFIED_WORD_ORDER = original
