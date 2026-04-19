"""Tests for control/oscillation_tuner.py — bump angle advisor.

~100 parametrized tests covering:
- Torsional stiffness K at various depths
- Natural frequency f₁ calculations
- Resonance exclusion zone (±20% of f₁ and odd harmonics)
- Reactive torque estimation
- Adaptation rules (toolface error, WOB ratio)
- REV floor enforcement
- Settling period
- fwd_set / rev_set properties
- Diagnostics output
"""
from __future__ import annotations

import math

import pytest

from hxi_optimizer.control.oscillation_tuner import OscConfig, OscillationTuner


# ═══════════════════════════════════════════════════════════════════════════
# 1. TORSIONAL STIFFNESS K
# ═══════════════════════════════════════════════════════════════════════════

class TestTorsionalStiffness:
    @pytest.mark.parametrize("depth_ft,expected_K", [
        (1000, 39760 / 1000),
        (2000, 39760 / 2000),
        (3000, 39760 / 3000),
        (5000, 39760 / 5000),
        (10000, 39760 / 10000),
    ])
    def test_K_approximation(self, depth_ft, expected_K):
        cfg = OscConfig(depth_ft=depth_ft, C_motor=5.0)
        t = OscillationTuner(cfg)
        assert abs(t.K - expected_K) / expected_K < 0.02  # 2% tolerance

    def test_K_inversely_proportional_to_depth(self):
        t1 = OscillationTuner(OscConfig(depth_ft=1000, C_motor=5.0))
        t2 = OscillationTuner(OscConfig(depth_ft=2000, C_motor=5.0))
        assert abs(t1.K / t2.K - 2.0) < 0.01

    def test_K_positive(self, tuner):
        assert tuner.K > 0

    @pytest.mark.parametrize("depth", [100, 500, 1000, 5000, 15000, 30000])
    def test_K_always_positive(self, depth):
        t = OscillationTuner(OscConfig(depth_ft=depth, C_motor=5.0))
        assert t.K > 0


# ═══════════════════════════════════════════════════════════════════════════
# 2. NATURAL FREQUENCY
# ═══════════════════════════════════════════════════════════════════════════

class TestNaturalFrequency:
    @pytest.mark.parametrize("depth_ft,expected_hz", [
        (1000, 8840 / 4000),
        (2000, 8840 / 8000),
        (5000, 8840 / 20000),
        (10000, 8840 / 40000),
    ])
    def test_f1_hz(self, depth_ft, expected_hz):
        t = OscillationTuner(OscConfig(depth_ft=depth_ft, C_motor=5.0))
        assert abs(t.f1_hz - expected_hz) < 0.001

    def test_f1_cpm_is_60x_hz(self, tuner):
        assert abs(tuner.f1_cpm - tuner.f1_hz * 60.0) < 0.001

    def test_f1_inversely_proportional(self):
        t1 = OscillationTuner(OscConfig(depth_ft=1000, C_motor=5.0))
        t2 = OscillationTuner(OscConfig(depth_ft=2000, C_motor=5.0))
        assert abs(t1.f1_hz / t2.f1_hz - 2.0) < 0.001


# ═══════════════════════════════════════════════════════════════════════════
# 3. RESONANCE EXCLUSION
# ═══════════════════════════════════════════════════════════════════════════

class TestResonanceExclusion:
    def test_at_f1_is_risky(self, tuner):
        assert tuner.check_resonance_risk(tuner.f1_cpm) is True

    def test_at_3f1_is_risky(self, tuner):
        assert tuner.check_resonance_risk(3 * tuner.f1_cpm) is True

    def test_at_5f1_is_risky(self, tuner):
        assert tuner.check_resonance_risk(5 * tuner.f1_cpm) is True

    def test_well_below_f1_is_safe(self, tuner):
        safe_rate = tuner.f1_cpm * 0.5
        assert tuner.check_resonance_risk(safe_rate) is False

    def test_well_above_5f1_is_safe(self, tuner):
        safe_rate = tuner.f1_cpm * 7.0
        assert tuner.check_resonance_risk(safe_rate) is False

    @pytest.mark.parametrize("pct_of_f1", [0.80, 0.85, 0.90, 0.95, 1.0,
                                            1.05, 1.10, 1.15, 1.19])
    def test_within_20pct_of_f1_is_risky(self, tuner, pct_of_f1):
        rate = tuner.f1_cpm * pct_of_f1
        assert tuner.check_resonance_risk(rate) is True

    @pytest.mark.parametrize("pct_of_f1", [0.70, 0.75, 0.79, 1.21, 1.25, 1.30])
    def test_outside_20pct_of_f1_is_safe(self, tuner, pct_of_f1):
        rate = tuner.f1_cpm * pct_of_f1
        # Must also not be near 3*f1 or 5*f1
        if not any(abs(rate - h * tuner.f1_cpm) / (h * tuner.f1_cpm) < 0.20
                   for h in [3, 5]):
            assert tuner.check_resonance_risk(rate) is False

    @pytest.mark.parametrize("harmonic", [1, 3, 5])
    def test_exact_harmonic_boundary_plus_19pct(self, tuner, harmonic):
        rate = harmonic * tuner.f1_cpm * 1.19
        assert tuner.check_resonance_risk(rate) is True

    @pytest.mark.parametrize("harmonic", [1, 3, 5])
    def test_exact_harmonic_boundary_plus_21pct(self, tuner, harmonic):
        rate = harmonic * tuner.f1_cpm * 1.21
        # Should be safe for this harmonic (may be near another)
        pct = abs(rate - harmonic * tuner.f1_cpm) / (harmonic * tuner.f1_cpm)
        if pct >= 0.20:
            is_near_other = any(
                abs(rate - h * tuner.f1_cpm) / (h * tuner.f1_cpm) < 0.20
                for h in [1, 3, 5] if h != harmonic
            )
            if not is_near_other:
                assert tuner.check_resonance_risk(rate) is False

    def test_zero_rate(self, tuner):
        assert tuner.check_resonance_risk(0.0) is False


# ═══════════════════════════════════════════════════════════════════════════
# 4. REACTIVE TORQUE
# ═══════════════════════════════════════════════════════════════════════════

class TestReactiveTorque:
    def test_basic_calculation(self, tuner):
        # C_motor=5.0, eta=0.70, dP=1000 → 3500
        result = tuner.estimate_reactive_torque(1000.0)
        assert abs(result - 3500.0) < 0.01

    def test_zero_pressure(self, tuner):
        assert tuner.estimate_reactive_torque(0.0) == 0.0

    @pytest.mark.parametrize("dp", [100, 500, 1000, 2000, 5000])
    def test_linear_in_pressure(self, tuner, dp):
        t1 = tuner.estimate_reactive_torque(dp)
        t2 = tuner.estimate_reactive_torque(dp * 2)
        assert abs(t2 / t1 - 2.0) < 0.001


# ═══════════════════════════════════════════════════════════════════════════
# 5. ASYMMETRY BASE REQUIRED
# ═══════════════════════════════════════════════════════════════════════════

class TestAsymmetryRequired:
    def test_positive(self, tuner):
        assert tuner.asymmetry_base_required(1000.0) > 0

    def test_proportional_to_pressure(self, tuner):
        a1 = tuner.asymmetry_base_required(1000)
        a2 = tuner.asymmetry_base_required(2000)
        assert abs(a2 / a1 - 2.0) < 0.01

    def test_zero_dp_zero_asymmetry(self, tuner):
        assert tuner.asymmetry_base_required(0.0) == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 6. ADAPTATION
# ═══════════════════════════════════════════════════════════════════════════

class TestAdaptation:
    def test_settling_period_no_adapt(self, tuner):
        for i in range(9):
            tuner.record_bump_cycle(20.0, 0.5)
        assert tuner.settling is True
        assert tuner.base_angle == 45.0 * 0.70

    def test_settling_ends_at_10(self, tuner):
        for i in range(10):
            tuner.record_bump_cycle(20.0, 0.5)
        assert tuner.settling is False

    def test_adapt_interval(self, tuner):
        tuner.settling = False
        tuner.cycle_count = 0
        initial_base = tuner.base_angle
        tuner.record_bump_cycle(20.0, 0.5)  # cycle 1: not interval
        assert tuner.base_angle == initial_base
        # Skip to interval
        tuner.cycle_count = tuner.cfg.adaptation_interval_cycles - 1
        tuner.record_bump_cycle(20.0, 0.3)
        # Should have adapted (low dp_ratio → base_angle up)
        assert tuner.base_angle > initial_base

    def test_toolface_error_adjusts_asymmetry(self, tuner):
        tuner.settling = False
        tuner.cycle_count = tuner.cfg.adaptation_interval_cycles - 1
        initial_asym = tuner.asymmetry
        tuner.record_bump_cycle(20.0, 0.70)  # large tf_error, mid WOB
        assert tuner.asymmetry != initial_asym

    def test_low_wob_increases_base(self, tuner):
        tuner.settling = False
        tuner.cycle_count = tuner.cfg.adaptation_interval_cycles - 1
        initial_base = tuner.base_angle
        tuner.record_bump_cycle(5.0, 0.50)  # low dp_ratio
        assert tuner.base_angle > initial_base

    def test_base_angle_capped_at_max(self, tuner):
        tuner.settling = False
        tuner.base_angle = tuner.cfg.abs_max_base - 1
        tuner.cycle_count = tuner.cfg.adaptation_interval_cycles - 1
        tuner.record_bump_cycle(5.0, 0.30)
        assert tuner.base_angle <= tuner.cfg.abs_max_base

    def test_base_angle_floored_at_min(self, tuner):
        tuner.settling = False
        tuner.base_angle = tuner.cfg.abs_min_base + 1
        tuner.cycle_count = tuner.cfg.adaptation_interval_cycles - 1
        tuner.record_bump_cycle(20.0, 0.90)  # high WOB + large error → reduce base
        assert tuner.base_angle >= tuner.cfg.abs_min_base


# ═══════════════════════════════════════════════════════════════════════════
# 7. REV FLOOR ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════

class TestRevFloor:
    def test_rev_never_below_abs_min_base(self, tuner):
        tuner.base_angle = 20.0
        tuner.asymmetry = 50.0  # rev = 20 - 25 = -5 → too low
        tuner.settling = False
        tuner.cycle_count = tuner.cfg.adaptation_interval_cycles - 1
        tuner.record_bump_cycle(20.0, 0.50)
        assert tuner.rev_set >= int(tuner.cfg.abs_min_base)

    @pytest.mark.parametrize("asym", [0, 10, 30, 60, 90])
    def test_fwd_rev_consistency(self, tuner, asym):
        tuner.asymmetry = min(asym, tuner.cfg.abs_max_asym)
        tuner.base_angle = max(tuner.cfg.abs_min_base + asym / 2,
                               tuner.cfg.abs_min_base)
        assert tuner.fwd_set >= tuner.rev_set


# ═══════════════════════════════════════════════════════════════════════════
# 8. FWD/REV PROPERTIES
# ═══════════════════════════════════════════════════════════════════════════

class TestFwdRevProperties:
    def test_fwd_set_is_int(self, tuner):
        assert isinstance(tuner.fwd_set, int)

    def test_rev_set_is_int(self, tuner):
        assert isinstance(tuner.rev_set, int)

    def test_fwd_equals_base_plus_half_asym(self, tuner):
        expected = int(round(tuner.base_angle + tuner.asymmetry / 2.0))
        assert tuner.fwd_set == expected

    def test_rev_equals_base_minus_half_asym(self, tuner):
        expected = int(round(tuner.base_angle - tuner.asymmetry / 2.0))
        assert tuner.rev_set == expected

    def test_symmetric_when_asym_zero(self, tuner):
        tuner.asymmetry = 0.0
        assert tuner.fwd_set == tuner.rev_set


# ═══════════════════════════════════════════════════════════════════════════
# 9. WIND-UP
# ═══════════════════════════════════════════════════════════════════════════

class TestWindUp:
    def test_zero_torque_zero_windup(self, tuner):
        assert tuner.wind_up_degrees(0.0) == 0.0

    def test_positive_torque(self, tuner):
        result = tuner.wind_up_degrees(5000.0)
        assert result > 0

    @pytest.mark.parametrize("torque", [1000, 5000, 10000, 20000])
    def test_linear_in_torque(self, tuner, torque):
        w1 = tuner.wind_up_degrees(torque)
        w2 = tuner.wind_up_degrees(torque * 2)
        assert abs(w2 / w1 - 2.0) < 0.001


# ═══════════════════════════════════════════════════════════════════════════
# 10. DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════════════════

class TestDiagnostics:
    def test_diagnostics_has_required_fields(self, tuner):
        d = tuner.get_diagnostics()
        expected_keys = {
            "depth_ft", "K_ft_lb_per_deg", "f1_hz", "f1_cpm",
            "base_angle", "asymmetry", "fwd_set", "rev_set",
            "cycle_count", "settling", "wind_up_at_5klb",
        }
        assert expected_keys.issubset(set(d.keys()))

    def test_diagnostics_depth_matches_config(self, tuner):
        d = tuner.get_diagnostics()
        assert d["depth_ft"] == tuner.cfg.depth_ft

    def test_diagnostics_all_numeric(self, tuner):
        d = tuner.get_diagnostics()
        for k, v in d.items():
            if k == "settling":
                assert isinstance(v, bool)
            else:
                assert isinstance(v, (int, float)), f"{k} is {type(v)}"
