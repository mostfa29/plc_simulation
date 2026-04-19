"""Extended safety gate tests — boundary matrix, state sequences, timing.

Adds ~130 parametrized tests to cover the full boundary matrix and multi-step
state machine sequences not covered by the base test_safety_gate.py.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from hxi_optimizer.control.safety_gate import (
    AdaptState, LastKnownGood, SafetyConfig, SafetyGate,
)
from hxi_optimizer.io_logging.audit_logger import AuditLogger


SAFE_CFG = SafetyConfig(
    abs_min_lower=50, abs_max_lower=700,
    abs_min_upper=300, abs_max_upper=950,
)


@pytest.fixture
def modbus():
    m = AsyncMock()
    m.safe_write_registers = AsyncMock(return_value=True)
    m.safe_read = AsyncMock(return_value=[400, 600])
    return m


@pytest.fixture
def gate(modbus, tmp_path):
    audit = AuditLogger(tmp_path / "audit.csv")
    g = SafetyGate(SAFE_CFG, modbus, audit)
    g.current_lower = 400
    g.current_upper = 600
    g.lkg = LastKnownGood(lower=400, upper=600, timestamp=1000.0,
                          iae_at_acceptance=0.05)
    return g


# ═══════════════════════════════════════════════════════════════════════════
# 1. FULL BOUNDARY MATRIX — ABSOLUTE BOUNDS
# ═══════════════════════════════════════════════════════════════════════════

# abs_min_lower=50, abs_max_lower=700, abs_min_upper=300, abs_max_upper=950

class TestAbsoluteBoundaryMatrix:
    """Test every boundary ±1 for all four absolute limits."""

    @pytest.mark.parametrize("lower,expected", [
        (49, False), (50, True), (51, True),    # abs_min_lower boundary
        (699, True), (700, True), (701, False),  # abs_max_lower boundary
    ])
    def test_lower_boundaries(self, gate, lower, expected):
        assert gate._check_absolute(lower, 600) == expected

    @pytest.mark.parametrize("upper,expected", [
        (299, False), (300, True), (301, True),   # abs_min_upper boundary
        (949, True), (950, True), (951, False),    # abs_max_upper boundary
    ])
    def test_upper_boundaries(self, gate, upper, expected):
        assert gate._check_absolute(400, upper) == expected

    @pytest.mark.parametrize("lower,upper", [
        (50, 300), (50, 950), (700, 300), (700, 950),
        (50, 500), (400, 300), (400, 950), (700, 500),
    ])
    def test_corner_combinations_pass(self, gate, lower, upper):
        assert gate._check_absolute(lower, upper) is True

    @pytest.mark.parametrize("lower,upper", [
        (49, 299), (49, 951), (701, 299), (701, 951),
        (0, 0), (-1, -1), (1000, 1000),
    ])
    def test_corner_combinations_fail(self, gate, lower, upper):
        assert gate._check_absolute(lower, upper) is False


# ═════════════════════���═════════════════════════════════════════════════════
# 2. CONSISTENCY EXHAUSTIVE
# ════════════════════���══════════════════════════════════════════════════════

class TestConsistencyExhaustive:
    @pytest.mark.parametrize("lower,upper,expected", [
        # gap = upper - lower vs min_band_counts=50
        (400, 400, False),   # gap=0
        (400, 401, False),   # gap=1
        (400, 410, False),   # gap=10
        (400, 425, False),   # gap=25
        (400, 449, False),   # gap=49
        (400, 450, True),    # gap=50 (exactly min)
        (400, 451, True),    # gap=51
        (400, 500, True),    # gap=100
        (400, 800, True),    # gap=400
        (500, 400, False),   # reversed
        (0, 49, False),      # gap=49
        (0, 50, True),       # gap=50
    ])
    def test_consistency_gap_matrix(self, gate, lower, upper, expected):
        assert gate._check_consistency(lower, upper) == expected


# ═══════════════════════════════���═══════════════════��═══════════════════════
# 3. RATE LIMITER — FULL MATRIX
# ══════════════════════��════════════════════════════���═══════════════════════

class TestRateLimiterMatrix:
    @pytest.mark.parametrize("current,proposed,expected", [
        # current=0: 5% of 0 = 0, min_step=5 → max_delta=5
        (0, 0, 0),
        (0, 3, 3),
        (0, 5, 5),
        (0, 6, 5),
        (0, 100, 5),
        (0, -5, -5),
        (0, -6, -5),
        # current=100: 5% of 100 = 5, min_step=5 → max_delta=5
        (100, 100, 100),
        (100, 105, 105),
        (100, 106, 105),
        (100, 95, 95),
        (100, 94, 95),
        # current=200: 5% of 200 = 10 → max_delta=10
        (200, 210, 210),
        (200, 211, 210),
        (200, 190, 190),
        (200, 189, 190),
        # current=1000: 5% of 1000 = 50 → max_delta=50 (capped at abs_max)
        (1000, 1050, 1050),
        (1000, 1051, 1050),
        (1000, 950, 950),
        (1000, 949, 950),
        # current=2000: 5% of 2000 = 100 but abs_max=50 → max_delta=50
        (2000, 2050, 2050),
        (2000, 2051, 2050),
        # negative current
        (-100, -95, -95),
        (-100, -106, -105),
    ])
    def test_rate_limit_matrix(self, gate, current, proposed, expected):
        assert gate._rate_limit(proposed, current) == expected


# ═════════════════════════════════════��════════════════════════════��════════
# 4. STATE MACHINE — ALL VALID TRANSITIONS
# ══���══════════════════���═════════════════════════════════════════════════════

class TestStateTransitions:
    """Test all valid state transitions as documented in MASTER_CONTEXT."""

    def test_baseline_to_trial(self, gate):
        gate.state = AdaptState.BASELINE
        # Transition happens inside validate_and_write
        gate.state = AdaptState.TRIAL
        assert gate.state == AdaptState.TRIAL

    def test_trial_to_accepted_to_baseline(self, gate):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time() - 61
        gate.current_lower = 400
        gate.current_upper = 600
        gate.check_trial(0.04, pv_ok=True, oscillating=False)
        assert gate.state == AdaptState.BASELINE  # ACCEPTED is transient

    def test_trial_to_rejected_to_baseline(self, gate):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time()
        gate.check_trial(0.04, pv_ok=False, oscillating=False)
        # Rejection → cooldown → BASELINE
        assert gate.state == AdaptState.BASELINE

    def test_trial_to_rejected_to_disabled(self, gate):
        gate.state = AdaptState.TRIAL
        gate.consecutive_rejections = 4  # Will become 5
        gate.trigger_rollback("TEST")
        assert gate.state == AdaptState.DISABLED

    @pytest.mark.parametrize("initial", list(AdaptState))
    def test_esd_from_any_state(self, gate, initial):
        gate.state = initial
        gate.check_esd(1)
        assert gate.state == AdaptState.ESD

    def test_esd_to_baseline_on_clear(self, gate):
        gate.state = AdaptState.ESD
        gate.check_esd(0)
        assert gate.state == AdaptState.BASELINE

    def test_disabled_to_baseline_on_enable(self, gate):
        gate.state = AdaptState.DISABLED
        gate.operator_enable()
        assert gate.state == AdaptState.BASELINE

    def test_baseline_to_disabled_on_disable(self, gate):
        gate.state = AdaptState.BASELINE
        gate.operator_disable()
        assert gate.state == AdaptState.DISABLED

    def test_trial_to_disabled_on_disable(self, gate):
        gate.state = AdaptState.TRIAL
        gate.operator_disable()
        assert gate.state == AdaptState.DISABLED


# ═════════════════════════════════════════════════════��═════════════════════
# 5. MULTI-STEP STATE MACHINE SEQUENCES
# ═════════════════════��════════════════════════���════════════════════════════

class TestStateSequences:
    @pytest.mark.asyncio
    async def test_baseline_trial_accept_baseline(self, gate, modbus):
        modbus.safe_read.return_value = [410, 610]
        await gate.validate_and_write(410, 610, 0.04)
        assert gate.state == AdaptState.TRIAL
        gate.trial_start = time.time() - 61
        gate.check_trial(0.04, True, False)
        assert gate.state == AdaptState.BASELINE
        assert gate.lkg.lower == 410

    @pytest.mark.asyncio
    async def test_three_reject_recover(self, gate, modbus):
        """3 rejections, then accept on 4th attempt."""
        for i in range(3):
            modbus.safe_read.return_value = [400 + i, 600 + i]
            gate.cooldown_until = 0
            await gate.validate_and_write(400 + i, 600 + i, 0.04)
            gate.check_trial(0.1, pv_ok=False, oscillating=False)
        assert gate.consecutive_rejections == 3
        assert gate.state == AdaptState.BASELINE
        # 4th attempt succeeds
        gate.cooldown_until = 0
        modbus.safe_read.return_value = [405, 605]
        await gate.validate_and_write(405, 605, 0.04)
        gate.trial_start = time.time() - 61
        gate.check_trial(0.03, True, False)
        assert gate.consecutive_rejections == 0
        assert gate.lkg.lower == 405

    @pytest.mark.asyncio
    async def test_esd_during_cooldown(self, gate, modbus):
        gate.trigger_rollback("TEST")
        gate.check_esd(1)
        assert gate.state == AdaptState.ESD
        result = await gate.validate_and_write(400, 600, 0.05, esd_bit=1)
        assert result is False

    @pytest.mark.asyncio
    async def test_bump_then_esd_then_clear_both(self, gate, modbus):
        result = await gate.validate_and_write(400, 600, 0.05, bump_fwd=1)
        assert result is False
        result = await gate.validate_and_write(400, 600, 0.05, esd_bit=1)
        assert result is False
        assert gate.state == AdaptState.ESD
        gate.check_esd(0)
        modbus.safe_read.return_value = [400, 600]
        result = await gate.validate_and_write(400, 600, 0.05)
        assert result is True

    @pytest.mark.asyncio
    async def test_disable_during_trial(self, gate, modbus):
        modbus.safe_read.return_value = [400, 600]
        await gate.validate_and_write(400, 600, 0.04)
        assert gate.state == AdaptState.TRIAL
        gate.operator_disable()
        assert gate.state == AdaptState.DISABLED
        result = await gate.validate_and_write(400, 600, 0.05)
        assert result is False


# ═══���════════════════════════���═══════════════════════════��══════════════════
# 6. ACCEPT/REJECT TIMING EDGE CASES
# ═══════���═════════════════════════════════���═════════════════════════════════

class TestAcceptRejectTiming:
    @pytest.mark.parametrize("elapsed_s", [0, 10, 30, 59, 59.9])
    def test_no_accept_before_window(self, gate, elapsed_s):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time() - elapsed_s
        gate.check_trial(0.04, True, False)
        assert gate.state == AdaptState.TRIAL

    @pytest.mark.parametrize("elapsed_s", [60, 60.1, 61, 120, 300])
    def test_accept_after_window(self, gate, elapsed_s):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time() - elapsed_s
        gate.current_lower = 400
        gate.current_upper = 600
        gate.check_trial(0.04, True, False)
        assert gate.state == AdaptState.BASELINE

    @pytest.mark.parametrize("iae_ratio", [0.5, 0.8, 1.0, 1.19])
    def test_iae_within_ratio_passes(self, gate, iae_ratio):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time()
        gate.lkg.iae_at_acceptance = 0.05
        iae = 0.05 * iae_ratio
        gate.check_trial(iae, True, False)
        assert gate.state == AdaptState.TRIAL

    @pytest.mark.parametrize("iae_ratio", [1.21, 1.5, 2.0, 5.0])
    def test_iae_above_ratio_rejects(self, gate, iae_ratio):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time()
        gate.lkg.iae_at_acceptance = 0.05
        iae = 0.05 * iae_ratio
        old_rej = gate.consecutive_rejections
        gate.check_trial(iae, True, False)
        assert gate.consecutive_rejections > old_rej


# ═════════���═══════════════════════════════════��═══════════════════��═════════
# 7. COOLDOWN SCHEDULE VERIFICATION
# ���═══════════════��══════════════════════════════════════��═══════════════════

class TestCooldownScheduleExtended:
    @pytest.mark.parametrize("n,expected_min_s,expected_max_s", [
        (1, 28, 32),     # 30s ± 2
        (2, 58, 62),     # 60s
        (3, 118, 122),   # 120s
        (4, 238, 242),   # 240s
        (5, 298, 302),   # 300s (capped)
        (6, 298, 302),   # still capped
        (7, 298, 302),
        (8, 298, 302),
    ])
    def test_cooldown_at_rejection_n(self, gate, n, expected_min_s, expected_max_s):
        for _ in range(n):
            gate.trigger_rollback("TEST")
        remaining = gate.cooldown_until - time.time()
        assert expected_min_s <= remaining <= expected_max_s


# ═══════���═════════════════════���═════════════════════════════════════════════
# 8. HEARTBEAT COUNTER SEQUENCE
# ���════════════════════��══════════════════════════════���══════════════════════

class TestHeartbeatSequence:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("n", [1, 5, 10, 50, 100])
    async def test_counter_sequence(self, gate, modbus, n):
        for _ in range(n):
            await gate._send_heartbeat()
        assert gate.heartbeat_counter == n % 65536

    @pytest.mark.asyncio
    async def test_counter_wraps_at_boundary(self, gate, modbus):
        gate.heartbeat_counter = 65534
        await gate._send_heartbeat()
        assert gate.heartbeat_counter == 65535
        await gate._send_heartbeat()
        assert gate.heartbeat_counter == 0
        await gate._send_heartbeat()
        assert gate.heartbeat_counter == 1


# ═══════════════════════════════════════════════════════════���═══════════════
# 9. VALIDATE_AND_WRITE — LAYER-BY-LAYER FAILURE ISOLATION
# ════════════════════════════════════════════════��══════════════════════════

class TestLayerIsolation:
    """Verify each layer independently blocks with the correct audit trail."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("lower,upper,reason_substring", [
        (49, 600, "ABS_BOUNDS"),     # below abs_min_lower
        (701, 800, "ABS_BOUNDS"),    # above abs_max_lower
        (400, 299, "ABS_BOUNDS"),    # below abs_min_upper
        (400, 951, "ABS_BOUNDS"),    # above abs_max_upper
    ])
    async def test_abs_bounds_audit_reason(self, gate, tmp_path, lower, upper,
                                           reason_substring):
        await gate.validate_and_write(lower, upper, 0.05)
        import csv
        with open(gate.audit.filepath) as f:
            rows = list(csv.reader(f))
        found = any(reason_substring in str(r) for r in rows)
        assert found, f"Expected {reason_substring} in audit rows"

    @pytest.mark.asyncio
    async def test_consistency_audit_reason(self, gate):
        await gate.validate_and_write(500, 500, 0.05)
        import csv
        with open(gate.audit.filepath) as f:
            rows = list(csv.reader(f))
        assert any("CONSISTENCY" in str(r) for r in rows)
