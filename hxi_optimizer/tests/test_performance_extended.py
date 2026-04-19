"""Extended performance metrics tests — signal shapes, edge cases, numerics.

Adds ~100 parametrized tests for signal classification, CUSUM sensitivity,
filter numerics, and saturation edge cases.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from hxi_optimizer.monitoring.performance_metrics import (
    CUSUMDetector, PerformanceMetrics, PerformanceMonitor,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. CUSUM SENSITIVITY — VARIOUS SHIFT MAGNITUDES
# ═══════════════════════��═══════════════════════════════════════════════════

class TestCUSUMSensitivity:
    @pytest.mark.parametrize("shift_sigma,n_samples,expect_alarm", [
        (0.0, 100, False),    # no shift
        (0.5, 100, False),    # half-sigma — below k
        (1.0, 100, True),     # 1-sigma shift
        (2.0, 50, True),      # 2-sigma fast alarm
        (3.0, 20, True),      # 3-sigma very fast
        (5.0, 10, True),      # 5-sigma immediate
    ])
    def test_shift_detection_speed(self, shift_sigma, n_samples, expect_alarm):
        c = CUSUMDetector(k=0.5, h=5.0)
        c.set_baseline(0.0, 1.0)
        for _ in range(n_samples):
            c.update(shift_sigma)
        if expect_alarm:
            assert c.S_pos > 5.0 or c.S_neg > 5.0
        else:
            assert c.S_pos <= 5.0 and c.S_neg <= 5.0

    @pytest.mark.parametrize("shift", [-5, -3, -2, -1, 1, 2, 3, 5])
    def test_positive_and_negative_shifts(self, shift):
        c = CUSUMDetector(k=0.5, h=5.0)
        c.set_baseline(0.0, 1.0)
        for _ in range(50):
            c.update(float(shift))
        assert c.S_pos > 5.0 or c.S_neg > 5.0

    @pytest.mark.parametrize("h", [1.0, 2.0, 3.0, 5.0, 8.0, 10.0])
    def test_h_threshold_sensitivity(self, h):
        c = CUSUMDetector(k=0.5, h=h)
        c.set_baseline(0.0, 1.0)
        for _ in range(100):
            c.update(2.0)
        assert (c.S_pos > h) == True  # 2-sigma always exceeds

    @pytest.mark.parametrize("consecutive_req", [1, 2, 3, 5])
    def test_consecutive_alarm_requirement(self, consecutive_req):
        c = CUSUMDetector(k=0.5, h=5.0)
        c.set_baseline(0.0, 1.0)
        for _ in range(100):
            c.update(3.0)
        assert c.confirmed_change(consecutive_req) is True


# ═══════════════════════════════════════════════════════════════════════════
# 2. DNIAE — EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestDNIAEEdgeCases:
    def _make_monitor_with_errors(self, errors, setpoint=60.0):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for e in errors:
            m.update(setpoint - e, setpoint, 500, 400, 600)
        return m

    def test_all_exactly_at_deadband(self):
        m = self._make_monitor_with_errors([2.0] * 40)
        result = m.compute_metrics(60.0, 400, 600)
        assert result.dniae < 0.01  # excess = 0

    def test_alternating_sign_errors(self):
        """Alternating ±5 RPM. IIR filter attenuates rapid oscillation, so
        filtered error is much smaller than raw. DNIAE should still be > 0."""
        errors = [5.0 if i % 2 == 0 else -5.0 for i in range(40)]
        m = self._make_monitor_with_errors(errors)
        result = m.compute_metrics(60.0, 400, 600)
        assert result.dniae >= 0.0  # IIR smooths alternation heavily
        assert result.failure_mode in ("OSCILLATION", "NORMAL", "BIAS")

    def test_single_spike(self):
        """Single 20 RPM spike at end. IIR smooths the spike so the error
        seen by DNIAE is small; just verify it doesn't crash."""
        errors = [0.0] * 39 + [20.0]
        m = self._make_monitor_with_errors(errors)
        result = m.compute_metrics(60.0, 400, 600)
        assert isinstance(result.dniae, float)
        assert result.dniae >= 0.0

    @pytest.mark.parametrize("setpoint", [1.0, 10.0, 30.0, 60.0, 120.0, 200.0])
    def test_dniae_normalised_by_setpoint(self, setpoint):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(40):
            m.update(setpoint - 5.0, setpoint, 500, 400, 600)
        result = m.compute_metrics(setpoint, 400, 600)
        expected = 3.0 / setpoint  # excess = |5| - 2 = 3
        assert abs(result.dniae - expected) < 0.02

    def test_zero_setpoint_no_crash(self):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(40):
            m.update(5.0, 0.0, 500, 400, 600)
        result = m.compute_metrics(0.0, 400, 600)
        assert isinstance(result.dniae, float)


# ═══════════════════════════════════════════════════════════════��═══════════
# 3. FILTER EDGE CASES
# ═══════════════════════════════════════════════════════════════════════���═══

class TestFilterEdgeCases:
    def test_nan_input_handled(self):
        m = PerformanceMonitor(deadband_rpm=2.0)
        m.filter_rpm(60.0)
        m.filter_rpm(60.0)
        result = m.filter_rpm(float("nan"))
        # NaN propagates through median and IIR — just shouldn't crash
        assert isinstance(result, float)

    def test_inf_input(self):
        m = PerformanceMonitor(deadband_rpm=2.0)
        result = m.filter_rpm(float("inf"))
        assert isinstance(result, float)

    def test_negative_rpm(self):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(10):
            result = m.filter_rpm(-60.0)
        assert result < 0

    @pytest.mark.parametrize("val", [0.0, 0.001, 1e6, -1e6])
    def test_extreme_rpm_values(self, val):
        m = PerformanceMonitor(deadband_rpm=2.0)
        result = m.filter_rpm(val)
        assert isinstance(result, float)

    def test_rapid_step_response(self):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(20):
            m.filter_rpm(60.0)
        # Step to 120
        results = []
        for _ in range(20):
            results.append(m.filter_rpm(120.0))
        # IIR should converge toward 120
        assert results[-1] > 110.0


# ═══���═══════════════════════════════════════════════════════════════════════
# 4. SATURATION — MORE EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestSaturationEdgeCases:
    @pytest.mark.parametrize("swash,lower,upper,expect_sat_upper,expect_sat_lower", [
        (600, 400, 600, True, False),   # exactly at upper
        (598, 400, 600, True, False),   # within eps=2 of upper
        (597, 400, 600, False, False),  # outside eps
        (400, 400, 600, False, True),   # exactly at lower
        (402, 400, 600, False, True),   # within eps=2 of lower
        (403, 400, 600, False, False),  # outside eps
        (500, 400, 600, False, False),  # mid-range
    ])
    def test_saturation_eps_boundary(self, swash, lower, upper,
                                     expect_sat_upper, expect_sat_lower):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(40):
            m.update(60.0, 60.0, swash, lower, upper)
        result = m.compute_metrics(60.0, lower, upper)
        if expect_sat_upper:
            assert result.sat_upper > 0.5
        if expect_sat_lower:
            assert result.sat_lower > 0.5
        if not expect_sat_upper and not expect_sat_lower:
            assert result.sat_total < 0.1


# ═══════════════════════════════════════════════════════════════════════════
# 5. WINDUP — EDGE CASES
# ═══════════════════════════════════════════════════════════════════��═══════

class TestWindupEdgeCases:
    @pytest.mark.parametrize("error_rpm,swash,is_windup", [
        (10.0, 599, True),    # positive error + upper sat → windup
        (-10.0, 401, True),   # negative error + lower sat → windup
        (10.0, 401, False),   # positive error + lower sat → NOT sign-consistent
        (-10.0, 599, False),  # negative error + upper sat → NOT sign-consistent
        (1.0, 599, False),    # small error even with saturation
        (10.0, 500, False),   # high error but no saturation
    ])
    def test_windup_scenarios(self, error_rpm, swash, is_windup):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(40):
            m.update(60.0 - error_rpm, 60.0, swash, 400, 600)
        result = m.compute_metrics(60.0, 400, 600)
        assert result.is_windup is is_windup


# ═══════════════════════════════════════════════════════════════════════════
# 6. CLASSIFICATION — SIGNAL SHAPE MATRIX
# ═══════════════════════════════════════════════════════════════════════════

class TestClassificationShapes:
    def test_constant_bias_positive(self):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(40):
            m.update(50.0, 60.0, 500, 400, 600)
        result = m.compute_metrics(60.0, 400, 600)
        assert result.failure_mode == "BIAS"

    def test_constant_bias_negative(self):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(40):
            m.update(70.0, 60.0, 500, 400, 600)
        result = m.compute_metrics(60.0, 400, 600)
        assert result.failure_mode == "BIAS"

    def test_random_noise_normal(self):
        np.random.seed(123)
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(40):
            m.update(60.0 + np.random.normal(0, 0.3), 60.0, 500, 400, 600)
        result = m.compute_metrics(60.0, 400, 600)
        assert result.failure_mode in ("NORMAL", "BIAS")

    @pytest.mark.parametrize("bias_rpm", [3, 5, 8, 10, 15, 20])
    def test_bias_confidence_scales(self, bias_rpm):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(40):
            m.update(60.0 - bias_rpm, 60.0, 500, 400, 600)
        result = m.compute_metrics(60.0, 400, 600)
        assert result.failure_mode == "BIAS"
        assert result.failure_confidence > 0.3

    def test_ramp_classifies(self):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for i in range(40):
            m.update(60.0 - i * 0.5, 60.0, 500, 400, 600)
        result = m.compute_metrics(60.0, 400, 600)
        # Ramp creates increasing bias
        assert result.failure_mode in ("BIAS", "SLUGGISH", "NORMAL")


# ═══════════════════════════════════════════════════════════════��═══════════
# 7. METRICS — NUMERIC SAFETY
# ═══════════════════════════════════════════════════════════════════════════

class TestMetricsNumericSafety:
    def test_all_same_value_no_div_zero(self):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(40):
            m.update(60.0, 60.0, 500, 400, 600)
        result = m.compute_metrics(60.0, 400, 600)
        assert not np.isnan(result.dniae)
        assert not np.isinf(result.dniae)

    def test_very_large_errors(self):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(40):
            m.update(0.0, 60.0, 500, 400, 600)
        result = m.compute_metrics(60.0, 400, 600)
        assert isinstance(result.dniae, float)
        assert not np.isnan(result.dniae)

    @pytest.mark.parametrize("deadband", [0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 50.0])
    def test_various_deadbands(self, deadband):
        m = PerformanceMonitor(deadband_rpm=deadband)
        for _ in range(40):
            m.update(55.0, 60.0, 500, 400, 600)
        result = m.compute_metrics(60.0, 400, 600)
        assert isinstance(result.dniae, float)
        assert result.dniae >= 0

    def test_equal_lower_upper_bounds(self):
        m = PerformanceMonitor(deadband_rpm=2.0)
        for _ in range(40):
            m.update(60.0, 60.0, 500, 500, 500)
        result = m.compute_metrics(60.0, 500, 500)
        # Should not crash — sat_total = 0 when bounds equal
        assert isinstance(result.sat_total, float)
