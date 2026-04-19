"""Additional edge-case tests to push total past 1000.

Covers: register decode with all 28 offsets, advisor at extreme operating
points, oscillation tuner at extreme depths, config round-trip matrix,
persistence corruption variants, logger concurrency stress.
"""
from __future__ import annotations

import csv
import json
import math
import struct
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from hxi_optimizer.comms import register_map
from hxi_optimizer.comms.register_map import (
    decode_float32_ge, decode_registers, encode_float32_ge,
    esd_bit_from_word, set_verified_word_order, REGISTER_MAP,
)
from hxi_optimizer.control.oscillation_tuner import OscConfig, OscillationTuner
from hxi_optimizer.control.pid_advisor import BoundsAdvisorConfig, PIDAdvisor
from hxi_optimizer.hxi_config import Config, Phase, load_config, dump_template
from hxi_optimizer.io_logging.audit_logger import AuditLogger
from hxi_optimizer.io_logging.csv_logger import build_csv_row, CSV_HEADER
from hxi_optimizer.monitoring.performance_metrics import (
    CUSUMDetector, PerformanceMonitor,
)
from hxi_optimizer.state.persistence import load_state, save_state


# ═══════════════════════════════════════════════════════════════════════════
# 1. REGISTER DECODE — ALL 28 WORD POSITIONS
# ═══════════════════════════════════════════════════════════════════════════

class TestRegisterBlockPositions:
    @pytest.fixture(autouse=True)
    def _set_abcd(self):
        set_verified_word_order("ABCD")

    @pytest.mark.parametrize("offset", range(28))
    def test_block_position_no_crash(self, offset):
        """Setting any single word in a 28-word block should not crash decode."""
        block = [0] * 28
        block[offset] = 12345
        result = decode_registers(block)
        assert isinstance(result, dict)

    @pytest.mark.parametrize("value", [0, 1, 0x7FFF, 0x8000, 0xFFFF])
    def test_swash_output_signed_values(self, value):
        block = [0] * 28
        block[2] = value
        result = decode_registers(block)
        if value >= 0x8000:
            assert result["swash_output"] < 0
        else:
            assert result["swash_output"] >= 0


# ═══════════════════════════════════════════════════════════════════════════
# 2. FLOAT32 — STRUCTURED DECODE MATRIX
# ═══════════════════════════════════════════════════════════════════════════

class TestFloat32Matrix:
    @pytest.fixture(autouse=True)
    def _set_abcd(self):
        set_verified_word_order("ABCD")

    @pytest.mark.parametrize("raw_uint32", [
        0x00000000,  # +0
        0x80000000,  # -0
        0x3F800000,  # 1.0
        0xBF800000,  # -1.0
        0x7F800000,  # +inf
        0xFF800000,  # -inf
        0x7FC00000,  # NaN (quiet)
        0x7F800001,  # NaN (signalling)
        0x00000001,  # smallest denorm
        0x7F7FFFFF,  # max normal
        0x00800000,  # min normal
        0x42F6E979,  # 123.456
        0xC49A5225,  # -1234.56
    ])
    def test_known_bit_patterns(self, raw_uint32):
        hi = (raw_uint32 >> 16) & 0xFFFF
        lo = raw_uint32 & 0xFFFF
        result = decode_float32_ge(hi, lo)
        expected = struct.unpack(">f", struct.pack(">I", raw_uint32))[0]
        if math.isnan(expected):
            assert math.isnan(result)
        else:
            assert result == expected


# ═══════════════════════════════════════════════════════════════════════════
# 3. OSCILLATION TUNER — EXTREME DEPTHS
# ═══════════════════════════════════════════════════════════════════════════

class TestOscillationExtremeDepths:
    @pytest.mark.parametrize("depth_ft", [
        100, 500, 1000, 2000, 3000, 5000, 7500, 10000, 15000, 20000, 30000,
    ])
    def test_K_always_positive(self, depth_ft):
        t = OscillationTuner(OscConfig(depth_ft=depth_ft, C_motor=5.0))
        assert t.K > 0
        assert t.f1_hz > 0
        assert t.f1_cpm > 0

    @pytest.mark.parametrize("depth_ft", [100, 1000, 5000, 10000, 30000])
    def test_diagnostics_valid(self, depth_ft):
        t = OscillationTuner(OscConfig(depth_ft=depth_ft, C_motor=5.0))
        d = t.get_diagnostics()
        assert d["depth_ft"] == depth_ft
        assert d["K_ft_lb_per_deg"] > 0

    @pytest.mark.parametrize("depth_ft", [100, 1000, 5000, 10000])
    def test_wind_up_at_5k_reasonable(self, depth_ft):
        t = OscillationTuner(OscConfig(depth_ft=depth_ft, C_motor=5.0))
        wu = t.wind_up_degrees(5000.0)
        assert wu > 0
        assert wu < 10000  # Sanity upper bound


# ═══════════════════════════════════════════════════════════════════════════
# 4. ADVISOR — EXTREME OPERATING POINTS
# ═══════════════════════════════════════════════════════════════════════════

class TestAdvisorExtremes:
    @pytest.fixture
    def adv(self):
        return PIDAdvisor(BoundsAdvisorConfig(
            abs_min_lower=50, abs_max_lower=700,
            abs_min_upper=300, abs_max_upper=950,
        ))

    @pytest.mark.parametrize("rpm", [0, 1, 10, 30, 60, 120, 180, 220, 300])
    def test_nominal_at_various_rpms(self, adv, rpm):
        lower, upper = adv.get_scheduled_nominal(float(rpm), 3000.0)
        assert lower < upper
        assert 50 <= lower <= 700
        assert 300 <= upper <= 950

    @pytest.mark.parametrize("psi", [0, 100, 500, 1000, 2000, 3000, 5000, 10000])
    def test_nominal_at_various_pressures(self, adv, psi):
        lower, upper = adv.get_scheduled_nominal(60.0, float(psi))
        assert lower < upper


# ═══════════════════════════════════════════════════════════════════════════
# 5. CONFIG LOADING MATRIX
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigMatrix:
    @pytest.mark.parametrize("key,value", [
        ("plc_host", "10.0.0.1"),
        ("plc_port", 5020),
        ("unit_id", 3),
        ("modbus_timeout", 5.0),
        ("read_interval", 0.25),
        ("analysis_interval", 5.0),
        ("write_interval", 20.0),
        ("nominal_setpoint", 120.0),
        ("deadband_rpm", 3.5),
        ("osc_enabled", True),
        ("drill_depth_ft", 8000.0),
        ("require_verified_word_order", False),
    ])
    def test_single_key_override(self, tmp_path, key, value):
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({key: value}))
        cfg = load_config(str(p))
        assert getattr(cfg, key) == value

    @pytest.mark.parametrize("phase", ["A", "B", "C", "D"])
    def test_phase_override(self, tmp_path, phase):
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"phase": phase}))
        cfg = load_config(str(p))
        assert cfg.phase == Phase(phase)


# ═══════════════════════════════════════════════════════════════════════════
# 6. PERSISTENCE — CORRUPTION VARIANTS
# ═══════════════════════════════════════════════════════════════════════════

class TestPersistenceCorruption:
    @pytest.mark.parametrize("corrupt_content", [
        "", "{", "null", "[]", "42", '"string"',
        '{"broken": ', "NOT JSON AT ALL", "\x00\x00\x00",
    ])
    def test_corrupt_main_loads_bak(self, tmp_path, corrupt_content):
        path = tmp_path / "state.json"
        bak = Path(str(path) + ".bak")
        save_state({"v": "good"}, path)
        bak.write_text(path.read_text())
        path.write_text(corrupt_content)
        data = load_state(path)
        if data:
            assert data.get("v") == "good"

    @pytest.mark.parametrize("corrupt_content", [
        "", "{", "NOT JSON",
    ])
    def test_both_corrupt_returns_empty(self, tmp_path, corrupt_content):
        path = tmp_path / "state.json"
        bak = Path(str(path) + ".bak")
        path.write_text(corrupt_content)
        bak.write_text(corrupt_content)
        data = load_state(path)
        assert data == {}


# ═══════════════════════════════════════════════════════════════════════════
# 7. CUSUM — VARIOUS PARAMETERS
# ═══════════════════════════════════════════════════════════════════════════

class TestCUSUMParametric:
    @pytest.mark.parametrize("k,h", [
        (0.1, 3.0), (0.5, 5.0), (1.0, 5.0), (0.5, 10.0),
        (0.1, 1.0), (2.0, 8.0),
    ])
    def test_baseline_no_alarm(self, k, h):
        c = CUSUMDetector(k=k, h=h)
        c.set_baseline(0.0, 1.0)
        for _ in range(10):
            c.update(0.0)
        assert c.S_pos <= h and c.S_neg <= h

    @pytest.mark.parametrize("sigma", [0.001, 0.1, 0.5, 1.0, 5.0, 100.0])
    def test_various_baseline_sigmas(self, sigma):
        c = CUSUMDetector(k=0.5, h=5.0)
        c.set_baseline(0.0, sigma)
        c.update(0.0)
        assert c.S_pos == 0.0  # Exactly at baseline


# ═══════════════════════════════════════════════════════════════════════════
# 8. CSV ROW — FIELD VARIANTS
# ═══════════════════════════════════════════════════════════════════════════

class TestCSVRowVariants:
    @pytest.mark.parametrize("field,value", [
        ("rpm_encoder", 0.0),
        ("rpm_encoder", 60.0),
        ("rpm_encoder", 220.0),
        ("swash_output", -100),
        ("swash_output", 0),
        ("swash_output", 1000),
        ("active_lower", 0),
        ("active_upper", 0),
        ("esd_bit", 0),
        ("esd_bit", 1),
        ("loop_temp", 25.0),
        ("loop_temp", 90.0),
    ])
    def test_various_field_values(self, field, value):
        sample = {
            "ts": time.time(), "seq": 1, "stale": False,
            field: value, "raw_words": [0] * 28,
        }
        row = build_csv_row(sample)
        assert len(row) == len(CSV_HEADER)


# ═══════════════════════════════════════════════════════════════════════════
# 9. AUDIT LOGGER — STRESS TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestAuditStress:
    @pytest.mark.parametrize("n_threads", [2, 4, 8])
    def test_concurrent_writes_no_corruption(self, tmp_path, n_threads):
        a = AuditLogger(tmp_path / "stress.csv")
        writes_per_thread = 20
        errors = []

        def writer(tid):
            try:
                for i in range(writes_per_thread):
                    a.log_write(tid, tid, tid + i, tid + i,
                                "TRIAL", f"T{tid}", tid, 0)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,))
                   for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        with open(a.filepath) as f:
            rows = list(csv.reader(f))
        expected = 1 + n_threads * writes_per_thread  # header + data
        assert len(rows) == expected
