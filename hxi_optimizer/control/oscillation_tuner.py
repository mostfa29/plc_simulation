"""BumpAngleAdvisor — per MASTER_CONTEXT §4.3.

Parameterised as base_angle + asymmetry to decouple WOB transfer (base)
from toolface management (asymmetry):
    FWD_set = base + asymmetry/2
    REV_set = base - asymmetry/2

Phase A/B: advisory only (oscillation tuner does NOT write directly).
Phase C+ writes are gated by SafetyGate after %R06629 interpretation is
confirmed by Steve.

Resonance exclusion: never command an oscillation rate within ±20% of f₁
or its odd harmonics (3·f₁, 5·f₁).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger("oscillation_tuner")


@dataclass
class OscConfig:
    depth_ft: float
    C_motor: float           # PDM motor constant ft-lb/psi (manufacturer spec)
    eta_motor: float = 0.70

    abs_min_base: float = 15.0
    abs_max_base: float = 180.0
    abs_min_asym: float = 0.0
    abs_max_asym: float = 90.0

    delta_base_deg: float = 10.0
    delta_asym_deg: float = 5.0
    adaptation_interval_cycles: int = 5

    tf_error_threshold_deg: float = 10.0
    wob_ratio_low: float = 0.60
    wob_ratio_high: float = 0.80


class OscillationTuner:
    G_PSI = 11.5e6
    OD_IN = 5.000
    ID_IN = 4.276
    C_T_EFF = 8840.0
    SAFETY_FACTOR = 0.70

    def __init__(self, config: OscConfig) -> None:
        self.cfg = config
        self.base_angle = 45.0 * self.SAFETY_FACTOR
        self.asymmetry = 10.0 * self.SAFETY_FACTOR
        self.cycle_count = 0
        self.settling = True

    @property
    def K(self) -> float:
        """Torsional stiffness (ft-lb/deg) for 5" 19.5 lb/ft drill pipe."""
        J = (math.pi / 32.0) * (self.OD_IN ** 4 - self.ID_IN ** 4)
        L_in = self.cfg.depth_ft * 12.0
        K_in_lb_per_rad = self.G_PSI * J / L_in
        return K_in_lb_per_rad * (math.pi / 180.0) / 12.0

    @property
    def f1_hz(self) -> float:
        return self.C_T_EFF / (4.0 * self.cfg.depth_ft)

    @property
    def f1_cpm(self) -> float:
        return self.f1_hz * 60.0

    def check_resonance_risk(self, osc_rate_cpm: float) -> bool:
        for harmonic in (1, 3, 5):
            f_h = harmonic * self.f1_cpm
            if f_h > 0 and abs(osc_rate_cpm - f_h) / f_h < 0.20:
                return True
        return False

    def estimate_reactive_torque(self, delta_P_psi: float) -> float:
        return self.cfg.C_motor * delta_P_psi * self.cfg.eta_motor

    def asymmetry_base_required(self, delta_P_psi: float) -> float:
        return self.estimate_reactive_torque(delta_P_psi) / self.K

    def record_bump_cycle(self, toolface_error_deg: float,
                          delta_P_ratio: float) -> None:
        self.cycle_count += 1
        if self.settling and self.cycle_count >= 10:
            self.settling = False
        if self.settling or self.cycle_count % self.cfg.adaptation_interval_cycles != 0:
            return
        self._adapt(toolface_error_deg, delta_P_ratio)

    def _adapt(self, tf_error: float, dp_ratio: float) -> None:
        cfg = self.cfg
        if abs(tf_error) > cfg.tf_error_threshold_deg:
            delta = cfg.delta_asym_deg * (1.0 if tf_error > 0 else -1.0)
            self.asymmetry = min(cfg.abs_max_asym,
                                 max(cfg.abs_min_asym, self.asymmetry + delta))
        if dp_ratio < cfg.wob_ratio_low:
            self.base_angle = min(cfg.abs_max_base,
                                  self.base_angle + cfg.delta_base_deg)
        elif dp_ratio >= cfg.wob_ratio_high and abs(tf_error) > cfg.tf_error_threshold_deg * 1.5:
            self.base_angle = max(cfg.abs_min_base,
                                  self.base_angle - cfg.delta_base_deg * 0.5)
        rev = self.base_angle - self.asymmetry / 2.0
        if rev < cfg.abs_min_base:
            excess = cfg.abs_min_base - rev
            self.asymmetry = max(0.0, self.asymmetry - excess * 2.0)

    @property
    def fwd_set(self) -> int:
        return int(round(self.base_angle + self.asymmetry / 2.0))

    @property
    def rev_set(self) -> int:
        return int(round(self.base_angle - self.asymmetry / 2.0))

    def wind_up_degrees(self, torque_ft_lb: float) -> float:
        return torque_ft_lb / self.K

    def get_diagnostics(self) -> dict:
        return {
            "depth_ft": self.cfg.depth_ft,
            "K_ft_lb_per_deg": round(self.K, 3),
            "f1_hz": round(self.f1_hz, 4),
            "f1_cpm": round(self.f1_cpm, 1),
            "base_angle": round(self.base_angle, 1),
            "asymmetry": round(self.asymmetry, 1),
            "fwd_set": self.fwd_set,
            "rev_set": self.rev_set,
            "cycle_count": self.cycle_count,
            "settling": self.settling,
            "wind_up_at_5klb": round(5000.0 / self.K, 1),
        }
