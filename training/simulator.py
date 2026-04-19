"""Hydraulic top drive simulator — generates realistic 2 Hz register streams.

Calibrated to the TESCO 250T HXI 800HP per MASTER_CONTEXT:
  - Rineer MV125H motor (3,212 cc/rev)
  - 4× Sauer-Danfoss S90 pumps
  - 2.28:1 gear ratio
  - Noise floor by RPM from §6.1
  - Temperature model from §9
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SimConfig:
    # PID (approximating CPE305 inner loop discretised to 2 Hz observation)
    Kp: float = 2.5
    Ki: float = 0.8
    Kd: float = 0.05
    # Hydraulic plant
    tau_hydraulic_s: float = 0.35        # first-order lag time constant
    J_rotor_kgm2: float = 50.0          # rotational inertia
    motor_cc: float = 3212.0            # Rineer MV125H
    pump_cc: float = 130.0              # per pump
    n_pumps: int = 4
    gear_ratio: float = 2.28
    eta_vol: float = 0.93
    eta_mech: float = 0.92
    # Bounds
    default_lower: int = 350
    default_upper: int = 650
    swash_mid: int = 500
    swash_range: int = 500
    # Register count scale
    counts_per_rpm: float = 2.5
    # Noise (from §6.1)
    noise_floor_30: float = 2.0
    noise_floor_60: float = 1.5
    noise_floor_120: float = 0.75
    noise_floor_180: float = 0.4


class HydraulicTopDriveSimulator:
    """Generates register-schema-compatible samples at 2 Hz."""

    DT = 0.5  # seconds per step (2 Hz)

    def __init__(self, config: SimConfig | None = None):
        self.cfg = config or SimConfig()
        self.rpm = 0.0
        self.swash_command = self.cfg.swash_mid
        self.integral = 0.0
        self.prev_error = 0.0
        self.lower = self.cfg.default_lower
        self.upper = self.cfg.default_upper
        self.loop_temp = 50.0
        self.torque = 0.0
        self.seq = 0
        self.time_s = 0.0
        self.heartbeat = 0
        self._rng = np.random.default_rng()

    def _noise_std(self, setpoint: float) -> float:
        """Speed-dependent noise floor per MASTER_CONTEXT §6.1."""
        c = self.cfg
        if setpoint <= 30:
            return c.noise_floor_30
        if setpoint <= 60:
            return c.noise_floor_60
        if setpoint <= 120:
            return c.noise_floor_120
        return c.noise_floor_180

    def step(self, setpoint: float, disturbance: float = 0.0,
             torque_load: float = 0.0) -> dict:
        """Advance one 0.5s step. Returns a dict matching csv_logger schema."""
        dt = self.DT
        c = self.cfg

        # PID controller
        error = setpoint - self.rpm
        self.integral = np.clip(self.integral + error * dt, -200, 200)
        deriv = (error - self.prev_error) / dt
        self.prev_error = error
        command = c.Kp * error + c.Ki * self.integral + c.Kd * deriv

        # Map PID output to swash counts
        self.swash_command = int(np.clip(
            c.swash_mid + command * c.counts_per_rpm,
            self.lower, self.upper
        ))

        # Plant model: first-order + inertia
        swash_frac = (self.swash_command - c.swash_mid) / c.swash_range
        flow_cc_per_s = (c.n_pumps * c.pump_cc * 1800 / 60 * abs(swash_frac)
                         * c.eta_vol)
        target_rpm = flow_cc_per_s / c.motor_cc * 60 * np.sign(swash_frac) / c.gear_ratio
        alpha = dt / (c.tau_hydraulic_s + dt)
        self.rpm += alpha * (target_rpm - self.rpm)
        self.rpm += disturbance

        # Noise
        noise = self._rng.normal(0, self._noise_std(setpoint))
        measured_rpm = self.rpm + noise

        # Torque model
        self.torque = (abs(error) * 50 + torque_load
                       + self._rng.normal(0, 30))

        # Temperature drift (slow)
        self.loop_temp += self._rng.normal(0, 0.02)
        self.loop_temp = np.clip(self.loop_temp, 20, 95)

        self.heartbeat = (self.heartbeat + 1) % 65536 if self.seq % 10 == 0 else self.heartbeat
        self.time_s += dt
        self.seq += 1

        return {
            "ts": self.time_s,
            "seq": self.seq,
            "stale": False,
            "rpm_encoder": float(measured_rpm),
            "swash_output": self.swash_command,
            "ss_setpoint_fwd": float(setpoint),
            "ss_setpoint_rev": float(-setpoint),
            "active_lower": self.lower,
            "active_upper": self.upper,
            "delivered_torque": float(max(0, self.torque)),
            "pid_state": 1,
            "bump_fwd_set": 0,
            "bump_rev_set": 0,
            "bump_angle": 0,
            "bump_flag_fwd": 0,
            "bump_flag_rev": 0,
            "esd_bit": 0,
            "loop_temp": float(self.loop_temp),
            "heartbeat": self.heartbeat,
            "raw_words": [0] * 28,
        }

    def set_bounds(self, lower: int, upper: int) -> None:
        self.lower = lower
        self.upper = upper

    def reset(self) -> None:
        self.rpm = 0.0
        self.integral = 0.0
        self.prev_error = 0.0
        self.swash_command = self.cfg.swash_mid
        self.seq = 0
        self.time_s = 0.0
