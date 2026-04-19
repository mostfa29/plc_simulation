"""Real-time PID performance monitoring at 2 Hz — per MASTER_CONTEXT §6.2.

Primary metric: DNIAE (Deadband-adjusted Normalized IAE).
Failure-mode classification: BIAS / OSCILLATION / DEADBAND_HUNTING / SLUGGISH.
Change-point detection: 3 parallel CUSUM detectors (mean, variance, oscillation),
require 2 consecutive alarms before flagging.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from itertools import groupby

import numpy as np

logger = logging.getLogger("performance")


@dataclass
class PerformanceMetrics:
    timestamp: float
    dniae: float = 0.0
    mean_error: float = 0.0
    std_variation_pct: float = 0.0
    rmse_rpm: float = 0.0
    failure_mode: str = "NORMAL"
    failure_confidence: float = 0.0
    sat_upper: float = 0.0
    sat_lower: float = 0.0
    sat_total: float = 0.0
    sat_asymmetry: float = 0.0
    is_windup: bool = False
    change_detected: bool = False
    change_reason: str = ""
    n_valid: int = 0
    stale_fraction: float = 0.0


class CUSUMDetector:
    """Online CUSUM. h=5.0 at 2 Hz → ARL ~232s false alarm."""

    def __init__(self, k: float = 0.5, h: float = 5.0) -> None:
        self.k = k
        self.h = h
        self.S_pos = 0.0
        self.S_neg = 0.0
        self.baseline_mu = 0.0
        self.baseline_sigma = 1.0
        self.alarm_count = 0
        self.consecutive_alarms = 0

    def set_baseline(self, mu: float, sigma: float) -> None:
        self.baseline_mu = mu
        self.baseline_sigma = max(sigma, 1e-6)
        self.reset()

    def update(self, x: float) -> bool:
        z = (x - self.baseline_mu) / self.baseline_sigma
        self.S_pos = max(0.0, self.S_pos + z - self.k)
        self.S_neg = max(0.0, self.S_neg - z - self.k)
        alarming = self.S_pos > self.h or self.S_neg > self.h
        if alarming:
            self.consecutive_alarms += 1
            self.alarm_count += 1
        else:
            self.consecutive_alarms = 0
        return alarming

    def confirmed_change(self, required_consecutive: int = 2) -> bool:
        return self.consecutive_alarms >= required_consecutive

    def reset(self) -> None:
        self.S_pos = 0.0
        self.S_neg = 0.0
        self.consecutive_alarms = 0


class PerformanceMonitor:
    DEADBAND_DEFAULT_RPM = 2.0
    IIR_ALPHA = 0.200  # τ ≈ 2.24s at 2 Hz

    def __init__(self, window_sec: float = 20.0, sample_rate: float = 2.0,
                 deadband_rpm: float = DEADBAND_DEFAULT_RPM) -> None:
        self.window_size = int(window_sec * sample_rate)  # 40 samples
        self.deadband = deadband_rpm
        self.error_buffer = deque(maxlen=self.window_size)
        self.swash_buffer = deque(maxlen=self.window_size)
        self.rpm_buffer = deque(maxlen=self.window_size)
        self.stale_buffer = deque(maxlen=self.window_size)
        self._median3 = deque(maxlen=3)
        self._iir_state: float | None = None
        self.cusum_mean = CUSUMDetector(k=0.5, h=5.0)
        self.cusum_var = CUSUMDetector(k=0.5, h=5.0)
        self.cusum_osc = CUSUMDetector(k=0.5, h=6.0)
        self._baseline_collected = False

    def filter_rpm(self, raw_rpm: float) -> float:
        self._median3.append(raw_rpm)
        median_val = sorted(self._median3)[len(self._median3) // 2]
        if self._iir_state is None:
            self._iir_state = median_val
        else:
            self._iir_state = (self.IIR_ALPHA * median_val
                               + (1.0 - self.IIR_ALPHA) * self._iir_state)
        return self._iir_state

    def update(self, raw_rpm: float, setpoint: float, swash_output: int,
               lower: int, upper: int, stale: bool = False) -> None:
        filtered = self.filter_rpm(raw_rpm)
        error = setpoint - filtered if not stale else 0.0
        self.rpm_buffer.append(filtered)
        self.error_buffer.append(error)
        self.swash_buffer.append(swash_output)
        self.stale_buffer.append(stale)

    def compute_metrics(self, setpoint: float, lower: int, upper: int) -> PerformanceMetrics:
        errors = np.array(self.error_buffer, dtype=float)
        swash = np.array(self.swash_buffer, dtype=float)
        stale = np.array(self.stale_buffer, dtype=bool)
        n = len(errors)
        n_valid = int(np.sum(~stale))

        m = PerformanceMetrics(timestamp=time.time())
        m.n_valid = n_valid
        m.stale_fraction = 1.0 - n_valid / max(n, 1)

        # Spec requires ≥20 valid samples before producing metrics
        if n_valid < 20:
            m.failure_mode = "INSUFFICIENT_DATA"
            return m

        valid_errors = errors[~stale]

        abs_excess = np.maximum(0.0, np.abs(valid_errors) - self.deadband)
        m.dniae = float(np.mean(abs_excess) / max(setpoint, 1.0))
        m.mean_error = float(np.mean(valid_errors))
        m.rmse_rpm = float(np.sqrt(np.mean(valid_errors ** 2)))
        m.std_variation_pct = float(np.std(valid_errors) / max(setpoint, 1.0) * 100.0)

        # Saturation (counts ε for boundary tolerance)
        eps = 2
        if upper > lower:
            sat_up = int(np.sum(swash >= upper - eps))
            sat_lo = int(np.sum(swash <= lower + eps))
            m.sat_upper = sat_up / n
            m.sat_lower = sat_lo / n
            m.sat_total = m.sat_upper + m.sat_lower
            m.sat_asymmetry = (m.sat_upper - m.sat_lower) / max(m.sat_total, 1e-6)

        if m.sat_total > 0.30 and abs(m.mean_error) > self.deadband * 2:
            sign_consistent = (
                (m.mean_error > 0 and m.sat_upper > 0.25)
                or (m.mean_error < 0 and m.sat_lower > 0.25)
            )
            m.is_windup = sign_consistent

        m.failure_mode, m.failure_confidence = self._classify(valid_errors, setpoint)

        self._update_cusum(m)
        m.change_detected = (
            self.cusum_mean.confirmed_change(2)
            or self.cusum_var.confirmed_change(2)
            or self.cusum_osc.confirmed_change(2)
        )
        if m.change_detected:
            reasons = []
            if self.cusum_mean.confirmed_change(2): reasons.append("MEAN_SHIFT")
            if self.cusum_var.confirmed_change(2): reasons.append("VAR_SHIFT")
            if self.cusum_osc.confirmed_change(2): reasons.append("OSC_SHIFT")
            m.change_reason = "+".join(reasons)
        return m

    def _classify(self, errors: np.ndarray, setpoint: float) -> tuple[str, float]:
        if len(errors) < 8:
            return "INSUFFICIENT_DATA", 0.0
        mean_e = float(np.mean(errors))
        std_e = float(np.std(errors)) + 1e-6
        zero_crossings = int(np.sum(np.diff(np.sign(errors - mean_e)) != 0))
        bias_ratio = abs(mean_e) / std_e
        if bias_ratio > 2.0 and zero_crossings < 2:
            return "BIAS", min(1.0, bias_ratio / 4.0)

        acf = self._compute_acf(errors, max_lag=min(20, len(errors) // 2))
        zc_lags = self._acf_zero_crossings(acf)
        if len(zc_lags) >= 4:
            regularity = float(np.mean(zc_lags) / (np.std(zc_lags) + 1e-6))
            if regularity > 1.0:
                p2p = float(np.max(errors) - np.min(errors))
                if p2p < 1.5 * self.deadband:
                    return "DEADBAND_HUNTING", min(1.0, regularity / 2.0)
                return "OSCILLATION", min(1.0, regularity / 2.0)

        runs = sum(1 for _ in groupby(np.sign(errors)))
        if runs < 2:
            return "BIAS", 0.5
        if abs(mean_e) > self.deadband and zero_crossings < 3:
            return "SLUGGISH", 0.4
        return "NORMAL", 1.0

    @staticmethod
    def _compute_acf(x: np.ndarray, max_lag: int) -> np.ndarray:
        n = len(x)
        x_norm = x - np.mean(x)
        var = np.var(x_norm) + 1e-12
        return np.array([
            np.sum(x_norm[: n - k] * x_norm[k:]) / ((n - k) * var)
            for k in range(max_lag + 1)
        ])

    @staticmethod
    def _acf_zero_crossings(acf: np.ndarray) -> list:
        signs = np.sign(acf[1:])
        zc_indices = np.where(np.diff(signs) != 0)[0] + 1
        return list(np.diff(zc_indices)) if len(zc_indices) > 1 else []

    def _update_cusum(self, m: PerformanceMetrics) -> None:
        if not self._baseline_collected and m.n_valid >= self.window_size // 2:
            self.cusum_mean.set_baseline(m.mean_error, max(m.rmse_rpm, 0.5))
            self.cusum_var.set_baseline(np.log1p(m.rmse_rpm ** 2), 0.3)
            self.cusum_osc.set_baseline(0.0, 1.0)
            self._baseline_collected = True
        if self._baseline_collected:
            self.cusum_mean.update(m.mean_error)
            self.cusum_var.update(np.log1p(m.rmse_rpm ** 2))
            self.cusum_osc.update(1.0 if m.failure_mode == "OSCILLATION" else 0.0)

    def reset_for_new_condition(self) -> None:
        self._baseline_collected = False
        self.cusum_mean.reset()
        self.cusum_var.reset()
        self.cusum_osc.reset()
        logger.info("PerformanceMonitor: re-baselining after change-point")
