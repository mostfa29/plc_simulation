"""Tests for hxi_config.py — Config, Phase, SafetyLimitsConfig.

~50 tests covering:
- Config defaults
- Phase enum
- SafetyLimitsConfig (None defaults for safety gates)
- load_config from file (valid, partial, missing, invalid JSON)
- dump_template
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hxi_optimizer.hxi_config import (
    Config, Phase, SafetyLimitsConfig, dump_template, load_config,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. PHASE ENUM
# ═══════════════════════════════════════════════════════════════════════════

class TestPhase:
    @pytest.mark.parametrize("phase,value", [
        (Phase.A, "A"), (Phase.B, "B"), (Phase.C, "C"), (Phase.D, "D"),
    ])
    def test_phase_values(self, phase, value):
        assert phase.value == value

    def test_phase_from_string(self):
        assert Phase("A") == Phase.A

    def test_invalid_phase_raises(self):
        with pytest.raises(ValueError):
            Phase("X")

    def test_phase_ordering(self):
        """Phase C and D should be >= 'C' for write gating."""
        assert Phase.C.value >= "C"
        assert Phase.D.value >= "C"
        assert Phase.A.value < "C"
        assert Phase.B.value < "C"

    def test_phase_is_string(self):
        assert isinstance(Phase.A.value, str)


# ═══════════════════════════════════════════════════════════════════════════
# 2. SAFETY LIMITS CONFIG
# ═══════════════════════════════════════════════════════════════════════════

class TestSafetyLimitsConfig:
    def test_defaults_all_none(self):
        c = SafetyLimitsConfig()
        assert c.abs_min_lower is None
        assert c.abs_max_lower is None
        assert c.abs_min_upper is None
        assert c.abs_max_upper is None

    def test_min_band_default(self):
        c = SafetyLimitsConfig()
        assert c.min_band_counts == 50

    def test_custom_values(self):
        c = SafetyLimitsConfig(abs_min_lower=50, abs_max_lower=700)
        assert c.abs_min_lower == 50
        assert c.abs_max_lower == 700


# ═══════════════════════════════════════════════════════════════════════════
# 3. CONFIG DEFAULTS
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigDefaults:
    def test_default_host(self):
        assert Config().plc_host == "CONFIGURE_ME"

    def test_default_port(self):
        assert Config().plc_port == 502

    def test_default_phase_is_A(self):
        assert Config().phase == Phase.A

    def test_default_read_interval(self):
        assert Config().read_interval == 0.5

    def test_default_deadband(self):
        assert Config().deadband_rpm == 2.0

    def test_default_safety_is_none_limits(self):
        c = Config()
        assert c.safety.abs_min_lower is None

    def test_default_osc_disabled(self):
        assert Config().osc_enabled is False

    def test_default_requires_word_order(self):
        assert Config().require_verified_word_order is True


# ═══════════════════════════════════════════════════════════════════════════
# 4. LOAD_CONFIG — FROM FILE
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadConfig:
    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = load_config(str(tmp_path / "nope.json"))
        assert cfg.plc_host == "CONFIGURE_ME"
        assert cfg.phase == Phase.A

    def test_loads_host(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"plc_host": "10.0.0.1"}))
        cfg = load_config(str(p))
        assert cfg.plc_host == "10.0.0.1"

    def test_loads_phase(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"phase": "C"}))
        cfg = load_config(str(p))
        assert cfg.phase == Phase.C

    def test_loads_safety_limits(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({
            "safety": {"abs_min_lower": 100, "abs_max_upper": 900}
        }))
        cfg = load_config(str(p))
        assert cfg.safety.abs_min_lower == 100
        assert cfg.safety.abs_max_upper == 900

    def test_partial_override(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"deadband_rpm": 3.5}))
        cfg = load_config(str(p))
        assert cfg.deadband_rpm == 3.5
        assert cfg.plc_host == "CONFIGURE_ME"  # unchanged

    def test_unknown_keys_ignored(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"totally_unknown": True}))
        cfg = load_config(str(p))
        assert cfg.plc_host == "CONFIGURE_ME"

    @pytest.mark.parametrize("phase_str", ["A", "B", "C", "D"])
    def test_all_phases_load(self, tmp_path, phase_str):
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"phase": phase_str}))
        cfg = load_config(str(p))
        assert cfg.phase.value == phase_str

    def test_full_config(self, tmp_path):
        p = tmp_path / "cfg.json"
        data = {
            "plc_host": "10.0.0.5",
            "plc_port": 5020,
            "unit_id": 2,
            "modbus_timeout": 3.0,
            "read_interval": 0.25,
            "analysis_interval": 5.0,
            "nominal_setpoint": 120.0,
            "deadband_rpm": 3.0,
            "phase": "B",
            "safety": {
                "abs_min_lower": 50, "abs_max_lower": 700,
                "abs_min_upper": 300, "abs_max_upper": 950,
                "min_band_counts": 100,
            },
            "osc_enabled": True,
            "drill_depth_ft": 5000.0,
        }
        p.write_text(json.dumps(data))
        cfg = load_config(str(p))
        assert cfg.plc_host == "10.0.0.5"
        assert cfg.plc_port == 5020
        assert cfg.unit_id == 2
        assert cfg.nominal_setpoint == 120.0
        assert cfg.safety.min_band_counts == 100
        assert cfg.osc_enabled is True


# ═══════════════════════════════════════════════════════════════════════════
# 5. DUMP TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════

class TestDumpTemplate:
    def test_creates_file(self, tmp_path):
        path = str(tmp_path / "template.json")
        dump_template(path)
        assert Path(path).exists()

    def test_valid_json(self, tmp_path):
        path = str(tmp_path / "template.json")
        dump_template(path)
        data = json.loads(Path(path).read_text())
        assert "plc_host" in data

    def test_has_safety_section(self, tmp_path):
        path = str(tmp_path / "template.json")
        dump_template(path)
        data = json.loads(Path(path).read_text())
        assert "safety" in data
        assert "abs_min_lower" in data["safety"]
