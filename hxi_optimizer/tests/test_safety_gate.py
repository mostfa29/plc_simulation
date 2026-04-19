"""Tests for control/safety_gate.py — the most safety-critical module.

~300 parametrized tests covering every layer, state transition, rollback path,
cooldown schedule, and edge case.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hxi_optimizer.control.safety_gate import (
    AdaptState, LastKnownGood, SafetyConfig, SafetyGate,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. CONFIG VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

class TestSafetyConfigValidation:
    """SafetyGate must refuse to instantiate if any limit is None."""

    @pytest.mark.parametrize("missing_field", [
        "abs_min_lower", "abs_max_lower", "abs_min_upper", "abs_max_upper",
    ])
    def test_missing_single_field_raises(self, missing_field, mock_modbus, mock_audit):
        kwargs = dict(abs_min_lower=50, abs_max_lower=700,
                      abs_min_upper=300, abs_max_upper=950)
        kwargs[missing_field] = None
        cfg = SafetyConfig(**kwargs)
        with pytest.raises(ValueError, match=missing_field):
            SafetyGate(cfg, mock_modbus, mock_audit)

    def test_all_none_raises(self, mock_modbus, mock_audit):
        cfg = SafetyConfig()
        with pytest.raises(ValueError):
            SafetyGate(cfg, mock_modbus, mock_audit)

    def test_all_set_succeeds(self, safety_cfg, mock_modbus, mock_audit):
        gate = SafetyGate(safety_cfg, mock_modbus, mock_audit)
        assert gate.state == AdaptState.BASELINE

    @pytest.mark.parametrize("val", [0, -100, 1, 999, 32767])
    def test_accepts_any_integer_value(self, val, mock_modbus, mock_audit):
        cfg = SafetyConfig(
            abs_min_lower=val, abs_max_lower=val + 10,
            abs_min_upper=val + 20, abs_max_upper=val + 30,
        )
        gate = SafetyGate(cfg, mock_modbus, mock_audit)
        assert gate is not None


# ═══════════════════════════════════════════════════════════════════════════
# 2. LAYER 1 — ESD CHECK
# ═══════════════════════════════════════════════════════════════════════════

class TestESDCheck:
    def test_esd_bit_high_freezes(self, gate):
        assert gate.check_esd(1) is False
        assert gate.state == AdaptState.ESD

    def test_esd_bit_low_passes(self, gate):
        assert gate.check_esd(0) is True
        assert gate.state == AdaptState.BASELINE

    def test_esd_clear_resets_to_baseline(self, gate):
        gate.state = AdaptState.ESD
        gate.check_esd(0)
        assert gate.state == AdaptState.BASELINE

    def test_esd_persists_while_high(self, gate):
        gate.check_esd(1)
        gate.check_esd(1)
        assert gate.state == AdaptState.ESD

    @pytest.mark.parametrize("initial_state", list(AdaptState))
    def test_esd_overrides_any_state(self, gate, initial_state):
        gate.state = initial_state
        gate.check_esd(1)
        assert gate.state == AdaptState.ESD

    @pytest.mark.parametrize("esd_val", [1, 2, 255, 0xFFFF, True])
    def test_any_truthy_value_triggers_esd(self, gate, esd_val):
        assert gate.check_esd(esd_val) is False

    def test_zero_does_not_trigger(self, gate):
        assert gate.check_esd(0) is True

    @pytest.mark.safety_critical
    @pytest.mark.asyncio
    async def test_validate_and_write_blocked_by_esd(self, gate):
        result = await gate.validate_and_write(400, 600, 0.05, esd_bit=1)
        assert result is False
        assert gate.state == AdaptState.ESD


# ═══════════════════════════════════════════════════════════════════════════
# 3. LAYER 2 — BUMP FLAG CHECK
# ═══════════════════════════════════════════════════════════════════════════

class TestBumpFlagCheck:
    @pytest.mark.parametrize("fwd,rev,expected", [
        (0, 0, True),
        (1, 0, False),
        (0, 1, False),
        (1, 1, False),
        (100, 0, False),
        (0, 100, False),
    ])
    def test_bump_flag_combinations(self, gate, fwd, rev, expected):
        assert gate.check_bump_flags(fwd, rev) == expected

    @pytest.mark.safety_critical
    @pytest.mark.asyncio
    async def test_validate_and_write_blocked_by_bump_fwd(self, gate):
        result = await gate.validate_and_write(400, 600, 0.05, bump_fwd=1)
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_and_write_blocked_by_bump_rev(self, gate):
        result = await gate.validate_and_write(400, 600, 0.05, bump_rev=1)
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════
# 4. LAYER 3 — ABSOLUTE BOUNDS
# ═══════════════════════════════════════════════════════════════════════════

class TestAbsoluteBounds:
    """safety_cfg: min_lower=50, max_lower=700, min_upper=300, max_upper=950."""

    @pytest.mark.parametrize("lower,upper,ok", [
        # Both in range
        (50, 300, True),     # at minimums
        (700, 950, True),    # at maximums
        (400, 600, True),    # mid-range
        # Lower out of range
        (49, 600, False),    # below abs_min_lower
        (701, 950, False),   # above abs_max_lower
        # Upper out of range
        (400, 299, False),   # below abs_min_upper
        (400, 951, False),   # above abs_max_upper
        # Both at boundaries
        (50, 950, True),
        (700, 300, True),    # lower > upper but abs ok (consistency catches it)
    ])
    def test_absolute_bounds_check(self, gate, lower, upper, ok):
        assert gate._check_absolute(lower, upper) == ok

    @pytest.mark.parametrize("lower", [49, 0, -100, -32768])
    def test_lower_below_abs_min(self, gate, lower):
        assert gate._check_absolute(lower, 600) is False

    @pytest.mark.parametrize("lower", [701, 800, 32767])
    def test_lower_above_abs_max(self, gate, lower):
        assert gate._check_absolute(lower, 600) is False

    @pytest.mark.parametrize("upper", [299, 200, 0, -1])
    def test_upper_below_abs_min(self, gate, upper):
        assert gate._check_absolute(400, upper) is False

    @pytest.mark.parametrize("upper", [951, 1000, 32767])
    def test_upper_above_abs_max(self, gate, upper):
        assert gate._check_absolute(400, upper) is False

    # Boundary-exact tests
    def test_lower_exactly_at_min(self, gate):
        assert gate._check_absolute(50, 600) is True

    def test_lower_exactly_at_max(self, gate):
        assert gate._check_absolute(700, 800) is True

    def test_upper_exactly_at_min(self, gate):
        assert gate._check_absolute(100, 300) is True

    def test_upper_exactly_at_max(self, gate):
        assert gate._check_absolute(400, 950) is True

    def test_lower_one_below_min(self, gate):
        assert gate._check_absolute(49, 600) is False

    def test_upper_one_above_max(self, gate):
        assert gate._check_absolute(400, 951) is False


# ═══════════════════════════════════════════════════════════════════════════
# 5. LAYER 4 — LOGICAL CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════

class TestConsistency:
    def test_lower_equals_upper_fails(self, gate):
        assert gate._check_consistency(500, 500) is False

    def test_lower_greater_than_upper_fails(self, gate):
        assert gate._check_consistency(600, 400) is False

    def test_band_below_minimum_fails(self, gate):
        assert gate._check_consistency(400, 449) is False  # 49 < 50

    def test_band_exactly_minimum_passes(self, gate):
        assert gate._check_consistency(400, 450) is True  # 50 == 50

    def test_band_above_minimum_passes(self, gate):
        assert gate._check_consistency(400, 500) is True

    @pytest.mark.parametrize("lower,upper", [
        (0, 0), (100, 100), (500, 500),
        (600, 400), (950, 50),
    ])
    def test_cross_fault_rejected(self, gate, lower, upper):
        assert gate._check_consistency(lower, upper) is False

    @pytest.mark.parametrize("gap", [1, 10, 25, 49])
    def test_gap_below_min_band(self, gate, gap):
        assert gate._check_consistency(400, 400 + gap) is False

    @pytest.mark.parametrize("gap", [50, 51, 100, 200, 500])
    def test_gap_at_or_above_min_band(self, gate, gap):
        assert gate._check_consistency(400, 400 + gap) is True


# ═══════════════════════════════════════════════════════════════════════════
# 6. LAYER 5 — RATE LIMITER
# ═══════════════════════════════════════════════════════════════════════════

class TestRateLimiter:
    def test_small_change_passes_through(self, gate):
        assert gate._rate_limit(401, 400) == 401

    def test_large_positive_change_clamped(self, gate):
        # 5% of 400 = 20, abs_min=5, abs_max=50 → max_delta = 20
        result = gate._rate_limit(500, 400)
        assert result == 420

    def test_large_negative_change_clamped(self, gate):
        result = gate._rate_limit(300, 400)
        assert result == 380

    def test_zero_current_uses_abs_min_step(self, gate):
        # 5% of 0 = 0, abs_min_step = 5
        result = gate._rate_limit(100, 0)
        assert result == 5

    def test_no_change(self, gate):
        assert gate._rate_limit(400, 400) == 400

    @pytest.mark.parametrize("current", [50, 100, 200, 500, 900])
    def test_max_step_scales_with_current(self, gate, current):
        proposed = current + 1000  # Way too much
        result = gate._rate_limit(proposed, current)
        max_delta = max(5, min(50, int(abs(current) * 0.05)))
        assert result == current + max_delta

    @pytest.mark.parametrize("delta", [-5, -4, -1, 0, 1, 4, 5])
    def test_within_min_step_passes(self, gate, delta):
        # At current=100, 5% = 5 which equals abs_min_step
        result = gate._rate_limit(100 + delta, 100)
        assert result == 100 + delta

    def test_negative_current(self, gate):
        result = gate._rate_limit(-50, -100)
        # 5% of |-100| = 5, min_step = 5, so max_delta = 5
        assert result == -95


# ═══════════════════════════════════════════════════════════════════════════
# 7. LAYER 6 — HEARTBEAT
# ═══════════════════════════════════════════════════════════════════════════

class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_success(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        result = await gate._send_heartbeat()
        assert result is True
        assert gate.heartbeat_counter == 1

    @pytest.mark.asyncio
    async def test_heartbeat_failure(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = None
        result = await gate._send_heartbeat()
        assert result is False

    @pytest.mark.asyncio
    async def test_heartbeat_counter_wraps(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        gate.heartbeat_counter = 65535
        await gate._send_heartbeat()
        assert gate.heartbeat_counter == 0

    @pytest.mark.asyncio
    async def test_heartbeat_counter_increments(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        for i in range(10):
            await gate._send_heartbeat()
        assert gate.heartbeat_counter == 10

    @pytest.mark.asyncio
    async def test_heartbeat_writes_to_correct_register(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        await gate._send_heartbeat()
        mock_modbus.safe_write_registers.assert_called_with(
            address=6604, values=[1]
        )


# ═══════════════════════════════════════════════════════════════════════════
# 8. LAYERS 7–8 — WRITE + VERIFY
# ═══════════════════════════════════════════════════════════════════════════

class TestWriteAndVerify:
    @pytest.mark.asyncio
    async def test_successful_write_and_readback(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [400, 600]
        result = await gate._write_and_verify(400, 600)
        assert result is True

    @pytest.mark.asyncio
    async def test_write_failure(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = None
        result = await gate._write_and_verify(400, 600)
        assert result is False

    @pytest.mark.asyncio
    async def test_readback_returns_none(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = None
        result = await gate._write_and_verify(400, 600)
        assert result is False

    @pytest.mark.asyncio
    async def test_readback_mismatch_lower(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [399, 600]  # lower mismatch
        result = await gate._write_and_verify(400, 600)
        assert result is False

    @pytest.mark.asyncio
    async def test_readback_mismatch_upper(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [400, 601]
        result = await gate._write_and_verify(400, 600)
        assert result is False

    @pytest.mark.asyncio
    async def test_readback_mismatch_both(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [0, 0]
        result = await gate._write_and_verify(400, 600)
        assert result is False

    @pytest.mark.asyncio
    async def test_writes_to_correct_address(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [400, 600]
        await gate._write_and_verify(400, 600)
        mock_modbus.safe_write_registers.assert_called_with(
            address=6602, values=[400, 600]
        )


# ═══════════════════════════════════════════════════════════════════════════
# 9. STATE MACHINE
# ═══════════════════════════════════════════════════════════════════════════

class TestStateMachine:
    def test_initial_state_is_baseline(self, safety_cfg, mock_modbus, mock_audit):
        gate = SafetyGate(safety_cfg, mock_modbus, mock_audit)
        assert gate.state == AdaptState.BASELINE

    @pytest.mark.asyncio
    async def test_baseline_to_trial_on_write(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [400, 600]
        await gate.validate_and_write(400, 600, 0.05)
        assert gate.state == AdaptState.TRIAL

    @pytest.mark.asyncio
    async def test_disabled_blocks_write(self, gate):
        gate.state = AdaptState.DISABLED
        result = await gate.validate_and_write(400, 600, 0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_esd_blocks_write(self, gate):
        result = await gate.validate_and_write(400, 600, 0.05, esd_bit=1)
        assert result is False
        assert gate.state == AdaptState.ESD

    def test_operator_disable(self, gate):
        gate.operator_disable()
        assert gate.state == AdaptState.DISABLED

    def test_operator_enable_from_disabled(self, gate):
        gate.state = AdaptState.DISABLED
        gate.operator_enable()
        assert gate.state == AdaptState.BASELINE
        assert gate.consecutive_rejections == 0

    def test_operator_enable_resets_rejection_count(self, gate):
        gate.consecutive_rejections = 4
        gate.operator_enable()
        assert gate.consecutive_rejections == 0


# ═══════════════════════════════════════════════════════════════════════════
# 10. ACCEPT/REJECT CYCLE
# ═══════════════════════════════════════════════════════════════════════════

class TestAcceptRejectCycle:
    def test_check_trial_ignores_non_trial_state(self, gate):
        gate.state = AdaptState.BASELINE
        gate.check_trial(0.05, True, False)
        assert gate.state == AdaptState.BASELINE

    @pytest.mark.parametrize("state", [
        AdaptState.BASELINE, AdaptState.DISABLED, AdaptState.ESD,
        AdaptState.ACCEPTED, AdaptState.REJECTED,
    ])
    def test_check_trial_noop_outside_trial(self, gate, state):
        gate.state = state
        gate.check_trial(0.05, True, False)
        assert gate.state == state

    def test_pv_alarm_triggers_rollback(self, gate):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time()
        gate.check_trial(0.05, pv_ok=False, oscillating=False)
        # Should have triggered rollback
        assert gate.consecutive_rejections >= 1

    def test_oscillation_triggers_rollback(self, gate):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time()
        gate.check_trial(0.05, pv_ok=True, oscillating=True)
        assert gate.consecutive_rejections >= 1

    def test_high_iae_triggers_rollback(self, gate):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time()
        gate.lkg.iae_at_acceptance = 0.05
        # IAE > 1.20 * 0.05 = 0.06
        gate.check_trial(0.07, pv_ok=True, oscillating=False)
        assert gate.consecutive_rejections >= 1

    def test_iae_within_ratio_does_not_reject(self, gate):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time()
        gate.lkg.iae_at_acceptance = 0.05
        gate.check_trial(0.059, pv_ok=True, oscillating=False)
        assert gate.state == AdaptState.TRIAL

    def test_accept_after_window(self, gate):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time() - 61  # past 60s window
        gate.current_lower = 400
        gate.current_upper = 600
        gate.check_trial(0.04, pv_ok=True, oscillating=False)
        assert gate.state == AdaptState.BASELINE
        assert gate.lkg.lower == 400
        assert gate.lkg.upper == 600
        assert gate.consecutive_rejections == 0

    def test_accept_updates_lkg_iae(self, gate):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time() - 61
        gate.current_lower = 410
        gate.current_upper = 610
        gate.check_trial(0.03, pv_ok=True, oscillating=False)
        assert gate.lkg.iae_at_acceptance == 0.03

    def test_accept_not_before_window(self, gate):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time() - 30  # only 30s elapsed
        gate.check_trial(0.04, pv_ok=True, oscillating=False)
        assert gate.state == AdaptState.TRIAL


# ═══════════════════════════════════════════════════════════════════════════
# 11. ROLLBACK AND COOLDOWN
# ═══════════════════════════════════════════════════════════════════════════

class TestRollbackAndCooldown:
    def test_rollback_increments_rejections(self, gate):
        gate.trigger_rollback("TEST_REASON")
        assert gate.consecutive_rejections == 1

    def test_rollback_sets_cooldown(self, gate):
        gate.trigger_rollback("TEST")
        assert gate.cooldown_until > time.time()

    @pytest.mark.parametrize("n_rejections,expected_cooldown_s", [
        (1, 30),    # base * 2^0
        (2, 60),    # base * 2^1
        (3, 120),   # base * 2^2
        (4, 240),   # base * 2^3
        (5, 300),   # capped at max_cooldown_s
        (6, 300),   # still capped
    ])
    def test_exponential_cooldown_schedule(self, gate, n_rejections, expected_cooldown_s):
        for _ in range(n_rejections):
            gate.trigger_rollback("TEST")
        # The last cooldown should be approximately expected_cooldown_s
        remaining = gate.cooldown_until - time.time()
        assert abs(remaining - expected_cooldown_s) < 2.0

    def test_max_rejections_disables(self, gate):
        for i in range(5):
            gate.trigger_rollback(f"TEST_{i}")
        assert gate.state == AdaptState.DISABLED

    def test_below_max_rejections_stays_baseline(self, gate):
        for i in range(4):
            gate.trigger_rollback(f"TEST_{i}")
        assert gate.state == AdaptState.BASELINE

    @pytest.mark.asyncio
    async def test_cooldown_blocks_writes(self, gate):
        gate.cooldown_until = time.time() + 1000
        result = await gate.validate_and_write(400, 600, 0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_cooldown_allows_writes(self, gate, mock_modbus):
        gate.cooldown_until = time.time() - 1
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [400, 600]
        result = await gate.validate_and_write(400, 600, 0.05)
        assert result is True

    def test_rollback_logs_to_audit(self, gate, mock_audit):
        gate.trigger_rollback("SOME_REASON")
        # Check the audit file has content
        import csv
        with open(mock_audit.filepath) as f:
            rows = list(csv.reader(f))
        assert len(rows) >= 2  # header + rollback row
        assert any("ROLLBACK" in str(r) for r in rows)


# ═══════════════════════════════════════════════════════════════════════════
# 12. VALIDATE_AND_WRITE — FULL INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

class TestValidateAndWriteIntegration:
    @pytest.mark.asyncio
    async def test_successful_full_path(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [405, 600]
        result = await gate.validate_and_write(405, 600, 0.05)
        assert result is True
        assert gate.current_lower == 405
        assert gate.current_upper == 600

    @pytest.mark.asyncio
    async def test_out_of_bounds_rejected(self, gate):
        result = await gate.validate_and_write(10, 600, 0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_cross_fault_rejected(self, gate):
        result = await gate.validate_and_write(600, 400, 0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_rate_limited_value_written(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        # gate.current_lower = 400. Propose 500. 5% of 400 = 20. Clamped to 420.
        mock_modbus.safe_read.return_value = [420, 600]
        result = await gate.validate_and_write(500, 600, 0.05)
        assert result is True
        assert gate.current_lower == 420  # rate-limited

    @pytest.mark.asyncio
    async def test_heartbeat_failure_blocks(self, gate, mock_modbus):
        # First call is heartbeat write → fail. Second would be bounds write.
        mock_modbus.safe_write_registers.return_value = None
        result = await gate.validate_and_write(400, 600, 0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_write_verify_failure_triggers_rollback(self, gate, mock_modbus):
        call_count = 0
        async def side_effect(address, values):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # heartbeat
                return True
            return None  # bounds write fails
        mock_modbus.safe_write_registers.side_effect = side_effect
        result = await gate.validate_and_write(400, 600, 0.05)
        assert result is False
        assert gate.consecutive_rejections >= 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("esd,bump_fwd,bump_rev", [
        (1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1),
    ])
    async def test_preflight_rejections(self, gate, esd, bump_fwd, bump_rev):
        result = await gate.validate_and_write(
            400, 600, 0.05, esd_bit=esd, bump_fwd=bump_fwd, bump_rev=bump_rev
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_write_updates_current_values(self, gate, mock_modbus):
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [405, 605]
        await gate.validate_and_write(405, 605, 0.05)
        assert gate.current_lower == 405
        assert gate.current_upper == 605

    @pytest.mark.asyncio
    async def test_audit_logged_on_success(self, gate, mock_modbus, mock_audit):
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [400, 600]
        await gate.validate_and_write(400, 600, 0.05)
        import csv
        with open(mock_audit.filepath) as f:
            rows = list(csv.reader(f))
        assert any("WRITE_SUCCESS" in str(r) for r in rows)

    @pytest.mark.asyncio
    async def test_audit_logged_on_abs_bounds_reject(self, gate, mock_audit):
        await gate.validate_and_write(10, 600, 0.05)
        import csv
        with open(mock_audit.filepath) as f:
            rows = list(csv.reader(f))
        assert any("REJECTED" in str(r) for r in rows)


# ═══════════════════════════════════════════════════════════════════════════
# 13. SCENARIO TESTS — MULTI-STEP SEQUENCES
# ═══════════════════════════════════════════════════════════════════════════

class TestScenarios:
    @pytest.mark.asyncio
    async def test_full_accept_cycle(self, gate, mock_modbus):
        """Write → trial → 60s passes → accept → LKG updated."""
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [405, 605]
        await gate.validate_and_write(405, 605, 0.04)
        assert gate.state == AdaptState.TRIAL
        gate.trial_start = time.time() - 61
        gate.check_trial(0.04, pv_ok=True, oscillating=False)
        assert gate.state == AdaptState.BASELINE
        assert gate.lkg.lower == 405

    @pytest.mark.asyncio
    async def test_write_reject_cooldown_retry(self, gate, mock_modbus):
        """Write → reject (oscillation) → cooldown → retry after cooldown."""
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [405, 605]
        await gate.validate_and_write(405, 605, 0.04)
        gate.check_trial(0.04, pv_ok=True, oscillating=True)
        assert gate.consecutive_rejections == 1
        # During cooldown
        result = await gate.validate_and_write(400, 600, 0.05)
        assert result is False
        # After cooldown
        gate.cooldown_until = time.time() - 1
        mock_modbus.safe_read.return_value = [400, 600]
        result = await gate.validate_and_write(400, 600, 0.05)
        assert result is True

    @pytest.mark.asyncio
    async def test_five_rejections_disables(self, gate, mock_modbus):
        """5 consecutive rollbacks → DISABLED state."""
        mock_modbus.safe_write_registers.return_value = True
        for i in range(5):
            mock_modbus.safe_read.return_value = [400 + i, 600 + i]
            gate.cooldown_until = 0
            await gate.validate_and_write(400 + i, 600 + i, 0.04)
            gate.check_trial(0.1, pv_ok=False, oscillating=False)
        assert gate.state == AdaptState.DISABLED

    @pytest.mark.asyncio
    async def test_esd_during_trial_freezes(self, gate, mock_modbus):
        """Trial in progress → ESD fires → immediate freeze."""
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [400, 600]
        await gate.validate_and_write(400, 600, 0.04)
        assert gate.state == AdaptState.TRIAL
        gate.check_esd(1)
        assert gate.state == AdaptState.ESD
        # Now write attempt with ESD bit high → blocked
        result = await gate.validate_and_write(400, 600, 0.04, esd_bit=1)
        assert result is False

    @pytest.mark.asyncio
    async def test_operator_disable_enable_cycle(self, gate, mock_modbus):
        gate.operator_disable()
        result = await gate.validate_and_write(400, 600, 0.05)
        assert result is False
        gate.operator_enable()
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [400, 600]
        result = await gate.validate_and_write(400, 600, 0.05)
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# 14. LKG (LAST KNOWN GOOD)
# ═══════════════════════════════════════════════════════════════════════════

class TestLastKnownGood:
    def test_initial_lkg_defaults(self, safety_cfg, mock_modbus, mock_audit):
        gate = SafetyGate(safety_cfg, mock_modbus, mock_audit)
        assert gate.lkg.lower == 0
        assert gate.lkg.upper == 0
        assert gate.lkg.iae_at_acceptance == float("inf")

    def test_lkg_updated_on_accept(self, gate):
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time() - 61
        gate.current_lower = 420
        gate.current_upper = 620
        gate.check_trial(0.03, pv_ok=True, oscillating=False)
        assert gate.lkg.lower == 420
        assert gate.lkg.upper == 620
        assert gate.lkg.iae_at_acceptance == 0.03

    def test_lkg_not_updated_on_reject(self, gate):
        original_lkg = gate.lkg.lower, gate.lkg.upper
        gate.state = AdaptState.TRIAL
        gate.trial_start = time.time()
        gate.check_trial(0.1, pv_ok=False, oscillating=False)
        assert (gate.lkg.lower, gate.lkg.upper) == original_lkg

    @pytest.mark.asyncio
    async def test_rollback_writes_lkg_values(self, gate, mock_modbus):
        gate.lkg.lower = 350
        gate.lkg.upper = 650
        mock_modbus.safe_write_registers.return_value = True
        mock_modbus.safe_read.return_value = [350, 650]
        gate.trigger_rollback("TEST")
        await asyncio.sleep(0.1)  # Allow _async_rollback to run
        assert gate.current_lower == 350
        assert gate.current_upper == 650
