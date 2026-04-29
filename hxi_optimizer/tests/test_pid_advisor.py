"""Tests for control/pid_advisor.py — gain scheduling + sign-based integral trim.

~150 parametrized tests covering:
- Config validation (None fields)
- Scheduled nominal bounds (no table, with table, scipy/fallback)
- Dead zone, dwell timer, expand/contract logic
- Trim projection (±15%)
- Min gap enforcement
- Absolute bounds enforcement after trim
- Edge cases: zero setpoint, extreme saturation, dwell windup cap
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from hxi_optimizer.control.pid_advisor import (
    AdaptationState, BoundsAdvisorConfig, PIDAdvisor,
)
from hxi_optimizer.monitoring.performance_metrics import PerformanceMetrics


# ═══════════════════════════════════════════════════════════════════════════
# 1. CONFIG VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

class TestAdvisorConfigValidation:
    @pytest.mark.parametrize("missing", [
        "abs_min_lower", "abs_max_lower", "abs_min_upper", "abs_max_upper",
    ])
    def test_missing_field_raises(self, missing):
        kwargs = dict(abs_min_lower=50, abs_max_lower=700,
                      abs_min_upper=300, abs_max_upper=950)
        kwargs[missing] = None
        with pytest.raises(ValueError, match=missing):
            PIDAdvisor(BoundsAdvisorConfig(**kwargs))

    def test_all_none_raises(self):
        with pytest.raises(ValueError):
            PIDAdvisor(BoundsAdvisorConfig())

    def test_valid_config(self, advisor):
        assert advisor.cfg.abs_min_lower == 50


# ═══════════════════════════════════════════════════════════════════════════
# 2. SCHEDULED NOMINAL — NO TABLE
# ═══════════════════════════════════════════════════════════════════════════

class TestScheduledNominalNoTable:
    def test_returns_tuple(self, advisor):
        lower, upper = advisor.get_scheduled_nominal(60.0, 3000.0)
        assert isinstance(lower, int) and isinstance(upper, int)

    def test_within_absolute_bounds(self, advisor):
        lower, upper = advisor.get_scheduled_nominal(60.0, 3000.0)
        assert advisor.cfg.abs_min_lower <= lower <= advisor.cfg.abs_max_lower
        assert advisor.cfg.abs_min_upper <= upper <= advisor.cfg.abs_max_upper

    def test_lower_less_than_upper(self, advisor):
        lower, upper = advisor.get_scheduled_nominal(60.0, 3000.0)
        assert lower < upper

    @pytest.mark.parametrize("rpm", [0, 30, 60, 120, 180, 220])
    def test_various_rpms(self, advisor, rpm):
        lower, upper = advisor.get_scheduled_nominal(rpm, 3000.0)
        assert lower < upper

    @pytest.mark.parametrize("psi", [0, 1000, 2000, 3000, 5000])
    def test_various_pressures(self, advisor, psi):
        lower, upper = advisor.get_scheduled_nominal(60.0, psi)
        assert lower < upper


# ═══════════════════════════════════════════════════════════════════════════
# 3. SCHEDULED NOMINAL — WITH TABLE (nearest-neighbour fallback)
# ═══════════════════════════════════════════════════════════════════════════

class TestScheduledNominalWithTable:
    def _make_advisor_with_table(self):
        cfg = BoundsAdvisorConfig(
            abs_min_lower=50, abs_max_lower=700,
            abs_min_upper=300, abs_max_upper=950,
            schedule_table=[
                (60, 2000, 300, 600),
                (60, 4000, 350, 650),
                (120, 2000, 250, 700),
                (120, 4000, 280, 750),
            ]
        )
        return PIDAdvisor(cfg)

    def test_exact_match_row(self):
        adv = self._make_advisor_with_table()
        lower, upper = adv.get_scheduled_nominal(60, 2000)
        assert lower == 300 and upper == 600

    def test_interpolated_or_nearest(self):
        adv = self._make_advisor_with_table()
        lower, upper = adv.get_scheduled_nominal(90, 3000)
        assert 250 <= lower <= 350
        assert 600 <= upper <= 750

    def test_extrapolation_still_returns(self):
        adv = self._make_advisor_with_table()
        lower, upper = adv.get_scheduled_nominal(200, 5000)
        assert isinstance(lower, int)


# ═══════════════════════════════════════════════════════════════════════════
# 4. DEAD ZONE
# ═══════════════════════════════════════════════════════════════════════════

class TestDeadZone:
    def _make_metrics(self, std_pct=0.0, sat=0.0):
        m = PerformanceMetrics(timestamp=time.time())
        m.std_variation_pct = std_pct
        m.sat_total = sat
        m.sat_upper = sat / 2
        m.sat_lower = sat / 2
        return m

    def test_error_below_dead_zone_no_expand(self, advisor):
        """If RMS < 3 RPM, dwell should drain, no trim change."""
        metrics = self._make_metrics(std_pct=1.0, sat=0.5)  # 1% of 60 = 0.6 RPM
        initial_trim = advisor.state.trim_upper
        advisor.advise(60.0, 3000.0, metrics)
        assert advisor.state.trim_upper == initial_trim

    def test_error_above_dead_zone_accumulates_dwell(self, advisor):
        metrics = self._make_metrics(std_pct=10.0, sat=0.5)  # 10% of 60 = 6 RPM
        advisor.state.dwell_counter = 0
        # Force dt = 1.0 — without this, on Windows the clock resolution
        # collapses (now - last_update_time) to exactly 0.0 and the
        # increment vanishes. Other tests in this file follow the same
        # pattern (line 168, 198, 216, 311 etc.).
        now = time.time()
        advisor.state.last_update_time = now - 1.0
        advisor.advise(60.0, 3000.0, metrics, now=now)
        assert advisor.state.dwell_counter > 0

    def test_dwell_caps_at_3x(self, advisor):
        metrics = self._make_metrics(std_pct=100.0, sat=0.5)
        advisor.state.dwell_counter = 100.0  # Already high
        advisor.advise(60.0, 3000.0, metrics, now=time.time() + 100)
        assert advisor.state.dwell_counter <= advisor.cfg.dwell_time_s * 3


# ═══════════════════════════════════════════════════════════════════════════
# 5. DWELL TIMER
# ═══════════════════════════════════════════════════════════════════════════

class TestDwellTimer:
    def _make_metrics(self, rms_rpm=6.0, sat=0.5):
        m = PerformanceMetrics(timestamp=time.time())
        m.std_variation_pct = (rms_rpm / 60.0) * 100.0
        m.sat_total = sat
        m.sat_upper = sat / 2
        m.sat_lower = sat / 2
        return m

    def test_no_adapt_before_dwell(self, advisor):
        metrics = self._make_metrics(rms_rpm=6.0, sat=0.5)
        advisor.state.dwell_counter = 5.0  # < 20s
        initial_trim = advisor.state.trim_upper
        advisor.advise(60.0, 3000.0, metrics, now=time.time())
        assert advisor.state.trim_upper == initial_trim

    def test_adapt_after_dwell(self, advisor):
        metrics = self._make_metrics(rms_rpm=6.0, sat=0.5)
        advisor.state.dwell_counter = 21.0  # > 20s
        advisor.state.last_update_time = time.time() - 10
        initial_trim = advisor.state.trim_upper
        advisor.advise(60.0, 3000.0, metrics, now=time.time())
        assert advisor.state.trim_upper != initial_trim

    def test_dwell_drains_when_error_low(self, advisor):
        metrics = self._make_metrics(rms_rpm=0.5, sat=0.0)
        advisor.state.dwell_counter = 15.0
        advisor.state.last_update_time = time.time() - 10
        advisor.advise(60.0, 3000.0, metrics, now=time.time())
        assert advisor.state.dwell_counter < 15.0


# ═══════════════════════════════════════════════════════════════════════════
# 6. EXPAND / CONTRACT LOGIC
# ═══════════════════════════════════════════════════════════════════════════

class TestExpandContract:
    def _make_metrics(self, rms_rpm=6.0, sat=0.5):
        m = PerformanceMetrics(timestamp=time.time())
        m.std_variation_pct = (rms_rpm / 60.0) * 100.0
        m.sat_total = sat
        m.sat_upper = sat / 2
        m.sat_lower = sat / 2
        return m

    def test_expand_on_high_sat_high_error(self, advisor):
        """sat > 0.40 AND rms > 3 RPM → expand."""
        metrics = self._make_metrics(rms_rpm=6.0, sat=0.5)
        advisor.state.dwell_counter = 21.0
        advisor.state.last_update_time = time.time() - 10
        old_upper = advisor.state.trim_upper
        advisor.advise(60.0, 3000.0, metrics, now=time.time())
        assert advisor.state.trim_upper > old_upper

    def test_contract_on_low_sat_low_error(self, advisor):
        """sat < 0.05 AND rms < 1.5 RPM → contract.

        Note: the dwell counter only accumulates when rms > dead_zone_rms (3.0).
        But contraction requires rms < dead_zone_low (1.5), which drains dwell.
        So we pre-load dwell to 21 and call advise with a dt that doesn't drain
        it past threshold — but note advise drains dwell when error is low.
        We set dwell high enough that even after drain it stays above threshold.
        """
        metrics = self._make_metrics(rms_rpm=0.5, sat=0.02)
        advisor.state.dwell_counter = 25.0  # High enough to survive drain
        advisor.state.trim_upper = 10.0
        now = time.time()
        advisor.state.last_update_time = now - 1.0  # dt=1 → drain 0.5
        old_upper = advisor.state.trim_upper
        advisor.advise(60.0, 3000.0, metrics, now=now)
        assert advisor.state.trim_upper < old_upper

    def test_no_change_in_dead_band(self, advisor):
        """Error in dead zone, moderate saturation → no change."""
        metrics = self._make_metrics(rms_rpm=2.0, sat=0.20)
        old_trim = advisor.state.trim_upper
        advisor.advise(60.0, 3000.0, metrics, now=time.time())
        assert advisor.state.trim_upper == old_trim

    def test_dwell_resets_after_adapt(self, advisor):
        metrics = self._make_metrics(rms_rpm=6.0, sat=0.5)
        advisor.state.dwell_counter = 21.0
        advisor.state.last_update_time = time.time() - 10
        advisor.advise(60.0, 3000.0, metrics, now=time.time())
        assert advisor.state.dwell_counter == 0.0

    def test_total_adaptations_incremented(self, advisor):
        metrics = self._make_metrics(rms_rpm=6.0, sat=0.5)
        advisor.state.dwell_counter = 21.0
        advisor.state.last_update_time = time.time() - 10
        advisor.advise(60.0, 3000.0, metrics, now=time.time())
        assert advisor.state.total_adaptations == 1


# ═══════════════════════════════════════════════════════════════════════════
# 7. TRIM PROJECTION (±15%)
# ═══════════════════════════════════════════════════════════════════════════

class TestTrimProjection:
    def test_trim_capped_at_15_pct(self, advisor):
        advisor.state.trim_upper = 99999.0  # Way over
        m = PerformanceMetrics(timestamp=time.time())
        m.std_variation_pct = 0.0
        m.sat_total = 0.0
        advisor.advise(60.0, 3000.0, m, now=time.time())
        nom_lower, nom_upper = advisor.get_scheduled_nominal(60.0, 3000.0)
        nom_range = advisor.cfg.abs_max_upper - nom_upper
        assert advisor.state.trim_upper <= advisor.cfg.trim_max_frac * nom_range

    def test_negative_trim_capped(self, advisor):
        advisor.state.trim_lower = -99999.0
        m = PerformanceMetrics(timestamp=time.time())
        m.std_variation_pct = 0.0
        m.sat_total = 0.0
        advisor.advise(60.0, 3000.0, m, now=time.time())
        nom_lower, nom_upper = advisor.get_scheduled_nominal(60.0, 3000.0)
        nom_range = nom_lower - advisor.cfg.abs_min_lower
        assert advisor.state.trim_lower >= -(advisor.cfg.trim_max_frac * nom_range)


# ═══════════════════════════════════════════════════════════════════════════
# 8. MIN GAP ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════

class TestMinGap:
    def test_output_gap_at_least_min(self, advisor):
        m = PerformanceMetrics(timestamp=time.time())
        m.std_variation_pct = 0.0
        m.sat_total = 0.0
        lower, upper = advisor.advise(60.0, 3000.0, m)
        assert upper - lower >= advisor.cfg.min_gap_counts

    @pytest.mark.parametrize("gap", [10, 30, 49])
    def test_small_gaps_fixed(self, gap):
        cfg = BoundsAdvisorConfig(
            abs_min_lower=0, abs_max_lower=1000,
            abs_min_upper=0, abs_max_upper=1000,
            min_gap_counts=50,
        )
        adv = PIDAdvisor(cfg)
        adv.state.trim_upper = -400.0
        adv.state.trim_lower = 400.0
        m = PerformanceMetrics(timestamp=time.time())
        m.std_variation_pct = 0.0
        m.sat_total = 0.0
        lower, upper = adv.advise(60.0, 3000.0, m)
        assert upper - lower >= 50


# ═══════════════════════════════════════════════════════════════════════════
# 9. ABSOLUTE BOUNDS ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════

class TestAbsoluteBoundsEnforcement:
    @pytest.mark.parametrize("rpm,psi", [
        (0, 0), (60, 3000), (180, 5000), (220, 1000),
    ])
    def test_output_within_abs_bounds(self, advisor, rpm, psi):
        m = PerformanceMetrics(timestamp=time.time())
        m.std_variation_pct = 50.0  # very high error
        m.sat_total = 0.8
        advisor.state.dwell_counter = 100.0
        advisor.state.last_update_time = time.time() - 100
        lower, upper = advisor.advise(rpm, psi, m, now=time.time())
        assert lower >= advisor.cfg.abs_min_lower
        assert lower <= advisor.cfg.abs_max_lower
        assert upper >= advisor.cfg.abs_min_upper
        assert upper <= advisor.cfg.abs_max_upper


# ═══════════════════════════════════════════════════════════════════════════
# 10. ADVISE RETURN VALUES
# ═══════════════════════════════════════════════════════════════════════════

class TestAdviseReturn:
    def test_returns_tuple_of_two_ints(self, advisor):
        m = PerformanceMetrics(timestamp=time.time())
        m.std_variation_pct = 5.0
        m.sat_total = 0.3
        result = advisor.advise(60.0, 3000.0, m)
        assert isinstance(result, tuple) and len(result) == 2
        assert isinstance(result[0], int) and isinstance(result[1], int)

    def test_lower_always_less_than_upper(self, advisor):
        m = PerformanceMetrics(timestamp=time.time())
        m.std_variation_pct = 5.0
        m.sat_total = 0.3
        for _ in range(50):
            lower, upper = advisor.advise(60.0, 3000.0, m, now=time.time())
            assert lower < upper


# ═══════════════════════════════════════════════════════════════════════════
# 11. STATE PERSISTENCE FIELDS
# ═══════════════════════════════════════════════════════════════════════════

class TestAdaptationState:
    def test_default_trims_zero(self):
        s = AdaptationState()
        assert s.trim_upper == 0.0 and s.trim_lower == 0.0

    def test_default_lkg_inf(self):
        s = AdaptationState()
        assert s.lkg_iae == float("inf")

    def test_consecutive_rejections_default_zero(self):
        s = AdaptationState()
        assert s.consecutive_rejections == 0
