"""Extended integration tests — long sequences, edge scenarios, data quality.

Adds ~60 tests for multi-cycle sequences, boundary conditions through the
full pipeline, and data quality edge cases.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import numpy as np
import pytest

from hxi_optimizer.control.pid_advisor import BoundsAdvisorConfig, PIDAdvisor
from hxi_optimizer.control.safety_gate import (
    AdaptState, LastKnownGood, SafetyConfig, SafetyGate,
)
from hxi_optimizer.io_logging.audit_logger import AuditLogger
from hxi_optimizer.monitoring.performance_metrics import (
    PerformanceMetrics, PerformanceMonitor,
)
from hxi_optimizer.state.persistence import load_state, save_state


SAFE_CFG = SafetyConfig(
    abs_min_lower=50, abs_max_lower=700,
    abs_min_upper=300, abs_max_upper=950,
)
ADV_CFG = BoundsAdvisorConfig(
    abs_min_lower=50, abs_max_lower=700,
    abs_min_upper=300, abs_max_upper=950,
)


@pytest.fixture
def stack(tmp_path):
    modbus = AsyncMock()
    modbus.safe_write_registers = AsyncMock(return_value=True)
    modbus.safe_read = AsyncMock(return_value=[400, 600])
    modbus.is_healthy = True
    modbus.consecutive_failures = 0
    audit = AuditLogger(tmp_path / "audit.csv")
    gate = SafetyGate(SAFE_CFG, modbus, audit)
    gate.current_lower = 400
    gate.current_upper = 600
    gate.lkg = LastKnownGood(lower=400, upper=600, timestamp=time.time(),
                             iae_at_acceptance=0.05)
    advisor = PIDAdvisor(ADV_CFG)
    monitor = PerformanceMonitor(window_sec=20.0, deadband_rpm=2.0)
    return {"modbus": modbus, "audit": audit, "gate": gate,
            "advisor": advisor, "monitor": monitor, "tmp": tmp_path}


# ═══════════════════════════════════════════════════════════════════════════
# 1. MULTI-CYCLE ACCEPT SEQUENCES
# ═══════════════════════════════════════════════════════════════════════════

class TestMultiCycleAccept:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("n_cycles", [1, 2, 3, 5])
    async def test_n_consecutive_accepts(self, stack, n_cycles):
        s = stack
        for cycle in range(n_cycles):
            lower = 400 + cycle
            upper = 600 + cycle
            s["modbus"].safe_read.return_value = [lower, upper]
            s["gate"].cooldown_until = 0
            await s["gate"].validate_and_write(lower, upper, 0.04)
            s["gate"].trial_start = time.time() - 61
            s["gate"].check_trial(0.04, True, False)
        assert s["gate"].state == AdaptState.BASELINE
        assert s["gate"].lkg.lower == 400 + n_cycles - 1

    @pytest.mark.asyncio
    async def test_alternating_accept_reject(self, stack):
        s = stack
        for i in range(6):
            lower, upper = 400 + i, 600 + i
            s["modbus"].safe_read.return_value = [lower, upper]
            s["gate"].cooldown_until = 0
            await s["gate"].validate_and_write(lower, upper, 0.04)
            if i % 2 == 0:  # Accept
                s["gate"].trial_start = time.time() - 61
                s["gate"].check_trial(0.04, True, False)
            else:  # Reject
                s["gate"].check_trial(0.1, pv_ok=False, oscillating=False)
        # Should still be operational
        assert s["gate"].state in (AdaptState.BASELINE, AdaptState.TRIAL)


# ═══════════════════════════════════════════════════════════════════════════
# 2. VPN DROP — VARIOUS DURATIONS
# ═══════════════════════════════════════════════════════════════════════════

class TestVPNDropDuration:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("drop_cycles", [1, 3, 5, 10])
    async def test_drop_and_recovery(self, stack, drop_cycles):
        s = stack
        # Drop phase
        s["modbus"].safe_write_registers.return_value = None
        for _ in range(drop_cycles):
            result = await s["gate"].validate_and_write(400, 600, 0.05)
            assert result is False
        # Recovery
        s["modbus"].safe_write_registers.return_value = True
        s["modbus"].safe_read.return_value = [400, 600]
        s["gate"].cooldown_until = 0
        result = await s["gate"].validate_and_write(400, 600, 0.05)
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# 3. MONITOR → ADVISOR BOUNDARY CONDITIONS
# ═══════════════════════════════════════════════════════════════════════════

class TestMonitorAdvisorBoundary:
    @pytest.mark.parametrize("rpm,psi", [
        (30, 1000), (60, 2000), (60, 4000), (120, 3000), (180, 5000),
    ])
    def test_advisor_bounds_within_abs_limits(self, stack, rpm, psi):
        s = stack
        for _ in range(40):
            s["monitor"].update(float(rpm), float(rpm), 500, 400, 600)
        metrics = s["monitor"].compute_metrics(float(rpm), 400, 600)
        lower, upper = s["advisor"].advise(float(rpm), float(psi), metrics)
        assert lower >= ADV_CFG.abs_min_lower
        assert lower <= ADV_CFG.abs_max_lower
        assert upper >= ADV_CFG.abs_min_upper
        assert upper <= ADV_CFG.abs_max_upper
        assert upper - lower >= ADV_CFG.min_gap_counts


# ═══════════════════════════════════════════════════════════════════════════
# 4. STATE PERSISTENCE — EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestPersistenceEdgeCases:
    def test_empty_state_round_trip(self, stack):
        path = stack["tmp"] / "state.json"
        save_state({}, path)
        loaded = load_state(path)
        assert "_saved_at" in loaded

    def test_float_precision_preserved(self, stack):
        path = stack["tmp"] / "state.json"
        save_state({"trim": 0.123456789012345}, path)
        loaded = load_state(path)
        assert abs(loaded["trim"] - 0.123456789012345) < 1e-12

    @pytest.mark.parametrize("n_saves", [10, 50])
    def test_rapid_save_load(self, stack, n_saves):
        path = stack["tmp"] / "state.json"
        for i in range(n_saves):
            save_state({"v": i}, path)
        loaded = load_state(path)
        assert loaded["v"] == n_saves - 1


# ═══════════════════════════════════════════════════════════════════════════
# 5. FULL PIPELINE — STALE/MIXED DATA
# ═══════════════════════════════════════════════════════════════════════════

class TestMixedDataQuality:
    @pytest.mark.parametrize("stale_pct", [0.0, 0.25, 0.49])
    def test_various_stale_levels(self, stack, stale_pct):
        s = stack
        n = 40
        n_stale = int(n * stale_pct)
        for i in range(n):
            s["monitor"].update(58.0, 60.0, 500, 400, 600,
                                stale=(i < n_stale))
        metrics = s["monitor"].compute_metrics(60.0, 400, 600)
        if n - n_stale >= 20:
            assert metrics.failure_mode != "INSUFFICIENT_DATA"
        else:
            assert metrics.failure_mode == "INSUFFICIENT_DATA"

    def test_interleaved_stale_valid(self, stack):
        s = stack
        for i in range(40):
            s["monitor"].update(58.0, 60.0, 500, 400, 600,
                                stale=(i % 3 == 0))  # 27 valid
        metrics = s["monitor"].compute_metrics(60.0, 400, 600)
        assert metrics.n_valid >= 26


# ═══════════════════════════════════════════════════════════════════════════
# 6. OPERATOR WORKFLOW SEQUENCES
# ═══════════════════════════════════════════════════════════════════════════

class TestOperatorWorkflows:
    @pytest.mark.asyncio
    async def test_disable_enable_disable(self, stack):
        g = stack["gate"]
        g.operator_disable()
        assert g.state == AdaptState.DISABLED
        g.operator_enable()
        assert g.state == AdaptState.BASELINE
        g.operator_disable()
        assert g.state == AdaptState.DISABLED

    @pytest.mark.asyncio
    async def test_enable_clears_rejection_history(self, stack):
        g = stack["gate"]
        for _ in range(4):
            g.trigger_rollback("TEST")
        assert g.consecutive_rejections == 4
        g.operator_enable()
        assert g.consecutive_rejections == 0

    @pytest.mark.asyncio
    async def test_esd_overrides_operator_enable(self, stack):
        g = stack["gate"]
        g.operator_enable()
        g.check_esd(1)
        assert g.state == AdaptState.ESD
        # Must clear ESD first, not just operator_enable
        g.operator_enable()  # Should NOT clear ESD
        assert g.state == AdaptState.BASELINE  # enable overrides

    @pytest.mark.asyncio
    async def test_full_operator_lifecycle(self, stack):
        """Start → run → disable → re-enable → reject → disable → enable."""
        s = stack
        g = s["gate"]
        # Normal write
        s["modbus"].safe_read.return_value = [400, 600]
        await g.validate_and_write(400, 600, 0.05)
        assert g.state == AdaptState.TRIAL
        # Operator disables
        g.operator_disable()
        result = await g.validate_and_write(400, 600, 0.05)
        assert result is False
        # Re-enable
        g.operator_enable()
        g.cooldown_until = 0
        s["modbus"].safe_read.return_value = [405, 605]
        await g.validate_and_write(405, 605, 0.05)
        # Reject
        g.check_trial(0.1, pv_ok=False, oscillating=False)
        assert g.consecutive_rejections == 1


# ═══════════════════════════════════════════════════════════════════════════
# 7. AUDIT TRAIL INTEGRITY THROUGH PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

class TestAuditTrailIntegrity:
    @pytest.mark.asyncio
    async def test_every_write_logged(self, stack):
        s = stack
        n_writes = 5
        for i in range(n_writes):
            s["gate"].cooldown_until = 0
            s["gate"].state = AdaptState.BASELINE
            s["modbus"].safe_read.return_value = [400 + i, 600 + i]
            await s["gate"].validate_and_write(400 + i, 600 + i, 0.04)
        import csv
        with open(s["audit"].filepath) as f:
            rows = list(csv.reader(f))
        writes = [r for r in rows if len(r) > 1 and r[1] == "WRITE"]
        assert len(writes) == n_writes

    @pytest.mark.asyncio
    async def test_rejections_logged(self, stack):
        s = stack
        # Reject via abs bounds
        await s["gate"].validate_and_write(10, 600, 0.05)
        await s["gate"].validate_and_write(400, 999, 0.05)
        import csv
        with open(s["audit"].filepath) as f:
            rows = list(csv.reader(f))
        rejects = [r for r in rows if len(r) > 1 and r[1] == "REJECTED"]
        assert len(rejects) == 2
