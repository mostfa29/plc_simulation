"""Two-layer adaptive bounds advisor — per MASTER_CONTEXT §4.2.

Layer 1: gain-scheduled nominal bounds (lookup table, populated during
commissioning; conservative defaults until then).
Layer 2: sign-based integral trim ±15% of nominal range, with dwell timer
and asymmetric expand/contract rates.

This class NEVER writes to the PLC. It only proposes bounds; SafetyGate
is the sole write path.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger("pid_advisor")


@dataclass
class BoundsAdvisorConfig:
    abs_min_lower: Optional[int] = None
    abs_max_lower: Optional[int] = None
    abs_min_upper: Optional[int] = None
    abs_max_upper: Optional[int] = None
    min_gap_counts: int = 50

    dead_zone_rms: float = 3.0
    dead_zone_low: float = 1.5
    dwell_time_s: float = 20.0
    delta_expand: float = 0.010
    delta_contract: float = 0.001
    trim_max_frac: float = 0.15
    max_rate_per_update: float = 0.05
    accept_window_s: float = 60.0

    sat_high_thresh: float = 0.40
    sat_low_thresh: float = 0.05

    expand_upper_rate: float = 0.010
    contract_upper_rate: float = 0.001
    expand_lower_rate: float = 0.010
    contract_lower_rate: float = 0.001

    # rows: [(rpm_setpoint, hydraulic_psi, nominal_lower, nominal_upper), ...]
    schedule_table: Optional[list] = None


@dataclass
class AdaptationState:
    trim_upper: float = 0.0
    trim_lower: float = 0.0
    dwell_counter: float = 0.0
    last_update_time: float = field(default_factory=time.time)
    current_lower: int = 0
    current_upper: int = 0
    lkg_lower: int = 0
    lkg_upper: int = 0
    lkg_timestamp: float = 0.0
    lkg_iae: float = float("inf")
    consecutive_rejections: int = 0
    total_adaptations: int = 0
    session_id: str = ""


class PIDAdvisor:
    def __init__(self, config: BoundsAdvisorConfig) -> None:
        self.cfg = config
        self.state = AdaptationState()
        self._validate_config()

    def _validate_config(self) -> None:
        required = ("abs_min_lower", "abs_max_lower",
                    "abs_min_upper", "abs_max_upper")
        for name in required:
            if getattr(self.cfg, name) is None:
                raise ValueError(
                    f"BoundsAdvisorConfig.{name} is None. Must come from "
                    f"commissioning before instantiating PIDAdvisor."
                )

    # ─── Layer 1 ────────────────────────────────────────────────────────────

    def get_scheduled_nominal(self, rpm_setpoint: float,
                              hydraulic_psi: float) -> tuple[int, int]:
        table = self.cfg.schedule_table
        if not table:
            mid = (self.cfg.abs_min_upper + self.cfg.abs_max_lower) // 2
            span = (self.cfg.abs_max_upper - self.cfg.abs_min_lower) // 4
            return mid - span // 2, mid + span // 2

        rpm_vals = np.array([r[0] for r in table])
        psi_vals = np.array([r[1] for r in table])
        lower_vals = np.array([r[2] for r in table])
        upper_vals = np.array([r[3] for r in table])
        try:
            from scipy.interpolate import griddata
            pts = np.column_stack([rpm_vals, psi_vals])
            nl = griddata(pts, lower_vals, [rpm_setpoint, hydraulic_psi], method="linear")
            nu = griddata(pts, upper_vals, [rpm_setpoint, hydraulic_psi], method="linear")
            if np.isnan(nl[0]) or np.isnan(nu[0]):
                nl = griddata(pts, lower_vals, [rpm_setpoint, hydraulic_psi], method="nearest")
                nu = griddata(pts, upper_vals, [rpm_setpoint, hydraulic_psi], method="nearest")
            return int(nl[0]), int(nu[0])
        except ImportError:
            # Fallback: nearest-neighbour without scipy
            d = (rpm_vals - rpm_setpoint) ** 2 + (psi_vals - hydraulic_psi) ** 2
            i = int(np.argmin(d))
            return int(lower_vals[i]), int(upper_vals[i])

    # ─── Layer 2 + projection ───────────────────────────────────────────────

    def advise(self, rpm_setpoint: float, hydraulic_psi: float,
               performance, now: Optional[float] = None
               ) -> Optional[tuple[int, int]]:
        if now is None:
            now = time.time()
        dt = now - self.state.last_update_time
        self.state.last_update_time = now

        nom_lower, nom_upper = self.get_scheduled_nominal(rpm_setpoint, hydraulic_psi)
        nom_range_upper = max(1, self.cfg.abs_max_upper - nom_upper)
        nom_range_lower = max(1, nom_lower - self.cfg.abs_min_lower)

        rms_err = performance.std_variation_pct * rpm_setpoint / 100.0
        sat_frac = performance.sat_total

        if rms_err > self.cfg.dead_zone_rms:
            self.state.dwell_counter = min(
                self.state.dwell_counter + dt, self.cfg.dwell_time_s * 3
            )
        else:
            self.state.dwell_counter = max(0.0, self.state.dwell_counter - dt * 0.5)

        if self.state.dwell_counter >= self.cfg.dwell_time_s:
            if sat_frac > self.cfg.sat_high_thresh and rms_err > self.cfg.dead_zone_rms:
                self.state.trim_upper += self.cfg.delta_expand * nom_range_upper
                self.state.trim_lower -= self.cfg.delta_expand * nom_range_lower
                self.state.dwell_counter = 0.0
                self.state.total_adaptations += 1
                logger.info(f"Expand: sat={sat_frac:.2f} rms={rms_err:.1f}")
            elif sat_frac < self.cfg.sat_low_thresh and rms_err < self.cfg.dead_zone_low:
                self.state.trim_upper -= self.cfg.delta_contract * nom_range_upper
                self.state.trim_lower += self.cfg.delta_contract * nom_range_lower
                self.state.dwell_counter = 0.0
                self.state.total_adaptations += 1
                logger.debug(f"Contract: sat={sat_frac:.2f} rms={rms_err:.1f}")

        max_trim_upper = self.cfg.trim_max_frac * nom_range_upper
        max_trim_lower = self.cfg.trim_max_frac * nom_range_lower
        self.state.trim_upper = float(np.clip(self.state.trim_upper,
                                              -max_trim_upper, max_trim_upper))
        self.state.trim_lower = float(np.clip(self.state.trim_lower,
                                              -max_trim_lower, max_trim_lower))

        target_upper = int(round(nom_upper + self.state.trim_upper))
        target_lower = int(round(nom_lower + self.state.trim_lower))
        target_upper = int(np.clip(target_upper,
                                   self.cfg.abs_min_upper, self.cfg.abs_max_upper))
        target_lower = int(np.clip(target_lower,
                                   self.cfg.abs_min_lower, self.cfg.abs_max_lower))

        if target_upper - target_lower < self.cfg.min_gap_counts:
            mid = (target_upper + target_lower) // 2
            target_lower = mid - self.cfg.min_gap_counts // 2
            target_upper = mid + self.cfg.min_gap_counts // 2
            target_upper = int(np.clip(target_upper,
                                       self.cfg.abs_min_upper, self.cfg.abs_max_upper))
            target_lower = int(np.clip(target_lower,
                                       self.cfg.abs_min_lower, self.cfg.abs_max_lower))

        return int(target_lower), int(target_upper)
