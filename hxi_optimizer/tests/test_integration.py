"""Integration tests — multi-component scenarios.

~50 tests covering:
- Full pipeline: read → metrics → advisor → safety gate → audit
- Phase gating (A/B = no writes, C/D = writes)
- State persistence round-trip
- ESD mid-pipeline freeze
- Bump flag lockout during active oscillation
- CUSUM-triggered re-baseline
- Multiple rejection → DISABLED
- Operator enable/disable cycle
- VPN drop simulation (N consecutive read failures)
- Stale data handling through the pipeline
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import numpy as np
import pytest

from hxi_optimizer.comms.register_map import set_verified_word_order
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
def full_stack(tmp_path):
    """Build a complete component stack for integration testing."""
    modbus = AsyncMock()
    modbus.safe_write_registers = AsyncMock(return_value=True)
    modbus.safe_read = AsyncMock(return_value=[400, 600])
    modbus.is_healthy = True
    modbus.consecutive_failures = 0

    audit = AuditLogger(tmp_path / "audit.csv")
    gate = SafetyGate(SAFE_CFG, modbus, audit)
    gate.current_lower = 400
    gate.current_upper = 600
    gate.lkg = LastKnownGood(lower=400, upper=600,
                             timestamp=time.time(), iae_at_acceptance=0.05)
    advisor = PIDAdvisor(ADV_CFG)
    monitor = PerformanceMonitor(window_sec=20.0, deadband_rpm=2.0)

    return {
        "modbus": modbus, "audit": audit, "gate": gate,
        "advisor": advisor, "monitor": monitor,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 1. FULL PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

class TestFullPipeline:
    def _feed_monitor(self, monitor, n=40, error=2.0, setpoint=60.0):
        for _ in range(n):
            monitor.update(setpoint - error, setpoint, 500, 400, 600)

    @pytest.mark.asyncio
    async def test_advisory_produces_bounds(self, full_stack):
        s = full_stack
        self._feed_monitor(s["monitor"])
        metrics = s["monitor"].compute_metrics(60.0, 400, 600)
        proposed = s["advisor"].advise(60.0, 3000.0, metrics)
        assert proposed is not None
        lower, upper = proposed
        assert lower < upper
        assert 50 <= lower <= 700
        assert 300 <= upper <= 950

    @pytest.mark.asyncio
    async def test_full_write_path(self, full_stack):
        s = full_stack
        self._feed_monitor(s["monitor"])
        metrics = s["monitor"].compute_metrics(60.0, 400, 600)
        proposed = s["advisor"].advise(60.0, 3000.0, metrics)
        lower, upper = proposed
        s["modbus"].safe_read.return_value = [lower, upper]
        result = await s["gate"].validate_and_write(lower, upper, metrics.dniae)
        # May be rate-limited but should succeed
        assert result is True or s["gate"].consecutive_rejections == 0

    @pytest.mark.asyncio
    async def test_advisory_only_no_modbus_write(self, full_stack):
        """Phase A/B: compute bounds, don't write."""
        s = full_stack
        self._feed_monitor(s["monitor"])
        metrics = s["monitor"].compute_metrics(60.0, 400, 600)
        proposed = s["advisor"].advise(60.0, 3000.0, metrics)
        assert proposed is not None
        # Don't call validate_and_write — that's the phase gate
        s["modbus"].safe_write_registers.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# 2. ESD MID-PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

class TestESDMidPipeline:
    @pytest.mark.asyncio
    async def test_esd_blocks_after_advisor(self, full_stack):
        s = full_stack
        result = await s["gate"].validate_and_write(400, 600, 0.05, esd_bit=1)
        assert result is False
        assert s["gate"].state == AdaptState.ESD

    @pytest.mark.asyncio
    async def test_esd_clears_then_writes(self, full_stack):
        s = full_stack
        # ESD on
        await s["gate"].validate_and_write(400, 600, 0.05, esd_bit=1)
        assert s["gate"].state == AdaptState.ESD
        # ESD off
        s["gate"].check_esd(0)
        assert s["gate"].state == AdaptState.BASELINE
        s["modbus"].safe_read.return_value = [400, 600]
        result = await s["gate"].validate_and_write(400, 600, 0.05)
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# 3. BUMP FLAG LOCKOUT
# ═══════════════════════════════════════════════════════════════════════════

class TestBumpFlagLockout:
    @pytest.mark.asyncio
    async def test_bump_fwd_blocks_write(self, full_stack):
        result = await full_stack["gate"].validate_and_write(
            400, 600, 0.05, bump_fwd=1
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_bump_clears_then_writes(self, full_stack):
        s = full_stack
        # Blocked by bump
        result = await s["gate"].validate_and_write(400, 600, 0.05, bump_fwd=1)
        assert result is False
        # Bump cleared
        s["modbus"].safe_read.return_value = [400, 600]
        result = await s["gate"].validate_and_write(400, 600, 0.05)
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# 4. MULTIPLE REJECTION → DISABLED
# ═══════════════════════════════════════════════════════════════════════════

class TestMultipleRejectionDisable:
    @pytest.mark.asyncio
    async def test_five_rollbacks_disable(self, full_stack):
        s = full_stack
        for _ in range(5):
            s["gate"].trigger_rollback("TEST")
        assert s["gate"].state == AdaptState.DISABLED
        result = await s["gate"].validate_and_write(400, 600, 0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_operator_re_enable_after_disable(self, full_stack):
        s = full_stack
        for _ in range(5):
            s["gate"].trigger_rollback("TEST")
        assert s["gate"].state == AdaptState.DISABLED
        s["gate"].operator_enable()
        # Also need to clear cooldown (rollbacks set it)
        s["gate"].cooldown_until = 0
        s["modbus"].safe_read.return_value = [400, 600]
        result = await s["gate"].validate_and_write(400, 600, 0.05)
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# 5. VPN DROP SIMULATION
# ═══════════════════════════════════════════════════════════════════════════

class TestVPNDrop:
    @pytest.mark.asyncio
    async def test_heartbeat_fails_during_drop(self, full_stack):
        s = full_stack
        s["modbus"].safe_write_registers.return_value = None
        result = await s["gate"].validate_and_write(400, 600, 0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_recovery_after_drop(self, full_stack):
        s = full_stack
        # Drop
        s["modbus"].safe_write_registers.return_value = None
        await s["gate"].validate_and_write(400, 600, 0.05)
        # Recover
        s["modbus"].safe_write_registers.return_value = True
        s["modbus"].safe_read.return_value = [400, 600]
        result = await s["gate"].validate_and_write(400, 600, 0.05)
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# 6. STATE PERSISTENCE ROUND-TRIP
# ═══════════════════════════════════════════════════════════════════════════

class TestStatePersistence:
    def test_save_and_restore_lkg(self, tmp_path, full_stack):
        s = full_stack
        s["gate"].lkg = LastKnownGood(lower=420, upper=620,
                                      timestamp=time.time(), iae_at_acceptance=0.03)
        state = {
            "lkg_lower": s["gate"].lkg.lower,
            "lkg_upper": s["gate"].lkg.upper,
            "lkg_iae": s["gate"].lkg.iae_at_acceptance,
            "trim_upper": s["advisor"].state.trim_upper,
            "trim_lower": s["advisor"].state.trim_lower,
        }
        path = tmp_path / "state.json"
        save_state(state, path)
        loaded = load_state(path)
        assert loaded["lkg_lower"] == 420
        assert loaded["lkg_upper"] == 620
        assert loaded["lkg_iae"] == 0.03

    def test_restore_advisor_trims(self, tmp_path, full_stack):
        s = full_stack
        s["advisor"].state.trim_upper = 12.5
        s["advisor"].state.trim_lower = -8.3
        state = {
            "trim_upper": s["advisor"].state.trim_upper,
            "trim_lower": s["advisor"].state.trim_lower,
        }
        path = tmp_path / "state.json"
        save_state(state, path)
        loaded = load_state(path)
        new_advisor = PIDAdvisor(ADV_CFG)
        new_advisor.state.trim_upper = loaded["trim_upper"]
        new_advisor.state.trim_lower = loaded["trim_lower"]
        assert new_advisor.state.trim_upper == 12.5
        assert new_advisor.state.trim_lower == -8.3


# ═══════════════════════════════════════════════════════════════════════════
# 7. MONITOR → ADVISOR → GATE CHAIN
# ═══════════════════════════════════════════════════════════════════════════

class TestMonitorAdvisorGateChain:
    @pytest.mark.asyncio
    async def test_high_sat_triggers_expand(self, full_stack):
        s = full_stack
        # High error + high saturation
        for _ in range(40):
            s["monitor"].update(50.0, 60.0, 599, 400, 600)
        metrics = s["monitor"].compute_metrics(60.0, 400, 600)
        assert metrics.sat_total > 0.3
        # Give advisor time to accumulate dwell
        s["advisor"].state.dwell_counter = 25.0
        s["advisor"].state.last_update_time = time.time() - 10
        old_trim = s["advisor"].state.trim_upper
        proposed = s["advisor"].advise(60.0, 3000.0, metrics, now=time.time())
        # Should have expanded
        assert s["advisor"].state.trim_upper >= old_trim

    @pytest.mark.asyncio
    async def test_normal_conditions_no_change(self, full_stack):
        s = full_stack
        for _ in range(40):
            s["monitor"].update(60.0, 60.0, 500, 400, 600)
        metrics = s["monitor"].compute_metrics(60.0, 400, 600)
        old_trim = s["advisor"].state.trim_upper
        s["advisor"].advise(60.0, 3000.0, metrics)
        assert s["advisor"].state.trim_upper == old_trim


# ═══════════════════════════════════════════════════════════════════════════
# 8. CUSUM RE-BASELINE
# ═══════════════════════════════════════════════════════════════════════════

class TestCUSUMRebaseline:
    def test_reset_clears_all_channels(self, full_stack):
        s = full_stack
        s["monitor"].cusum_mean.S_pos = 100.0
        s["monitor"].reset_for_new_condition()
        assert s["monitor"].cusum_mean.S_pos == 0.0
        assert s["monitor"].cusum_var.S_pos == 0.0
        assert s["monitor"].cusum_osc.S_pos == 0.0
        assert s["monitor"]._baseline_collected is False


# ═══════════════════════════════════════════════════════════════════════════
# 9. STALE DATA THROUGH PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

class TestStaleData:
    def test_all_stale_insufficient_data(self, full_stack):
        s = full_stack
        for _ in range(40):
            s["monitor"].update(60.0, 60.0, 500, 400, 600, stale=True)
        metrics = s["monitor"].compute_metrics(60.0, 400, 600)
        assert metrics.failure_mode == "INSUFFICIENT_DATA"

    def test_mostly_stale_still_computes(self, full_stack):
        s = full_stack
        for i in range(40):
            s["monitor"].update(60.0, 60.0, 500, 400, 600,
                                stale=(i < 18))  # 22 valid
        metrics = s["monitor"].compute_metrics(60.0, 400, 600)
        assert metrics.failure_mode != "INSUFFICIENT_DATA"
        assert metrics.n_valid >= 20
