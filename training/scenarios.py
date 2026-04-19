"""Drilling scenario generators for training data synthesis.

Each generator produces a list of samples + per-sample labels.
All scenarios use HydraulicTopDriveSimulator for physical realism.
"""
from __future__ import annotations

import numpy as np

from training.simulator import HydraulicTopDriveSimulator, SimConfig

LABELS = ["NORMAL", "BIAS", "OSCILLATION", "DEADBAND_HUNTING",
          "SLUGGISH", "WINDUP", "CONDITION_CHANGE"]


def generate_normal(duration_s: float = 300, setpoint: float = 60.0,
                    seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = HydraulicTopDriveSimulator(SimConfig())
    sim._rng = np.random.default_rng(seed)
    samples, labels = [], []
    for _ in np.arange(0, duration_s, 0.5):
        samples.append(sim.step(setpoint))
        labels.append("NORMAL")
    return samples, labels


def generate_bias(duration_s: float = 300, setpoint: float = 60.0,
                  bias_rpm: float = 5.0, onset_s: float = 100.0,
                  seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = HydraulicTopDriveSimulator(SimConfig())
    sim._rng = np.random.default_rng(seed)
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        dist = -bias_rpm if t > onset_s else 0.0
        samples.append(sim.step(setpoint, disturbance=dist))
        labels.append("BIAS" if t > onset_s + 5 else "NORMAL")
    return samples, labels


def generate_oscillation(duration_s: float = 300, setpoint: float = 60.0,
                         amplitude: float = 10.0, period_s: float = 6.0,
                         onset_s: float = 60.0,
                         seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = HydraulicTopDriveSimulator(SimConfig())
    sim._rng = np.random.default_rng(seed)
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        osc = amplitude * np.sin(2 * np.pi * t / period_s) if t > onset_s else 0.0
        samples.append(sim.step(setpoint, disturbance=osc))
        labels.append("OSCILLATION" if t > onset_s + 5 else "NORMAL")
    return samples, labels


def generate_stickslip(duration_s: float = 300, setpoint: float = 120.0,
                       stick_s: float = 7.0, slip_s: float = 3.0,
                       onset_s: float = 60.0,
                       seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = HydraulicTopDriveSimulator(SimConfig())
    sim._rng = np.random.default_rng(seed)
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        if t > onset_s:
            phase = t % (stick_s + slip_s)
            dist = -10.0 if phase < stick_s else 25.0
        else:
            dist = 0.0
        samples.append(sim.step(setpoint, disturbance=dist))
        labels.append("OSCILLATION" if t > onset_s + 5 else "NORMAL")
    return samples, labels


def generate_formation_change(duration_s: float = 600, setpoint: float = 60.0,
                              onset_s: float = 200.0,
                              seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = HydraulicTopDriveSimulator(SimConfig())
    sim._rng = np.random.default_rng(seed)
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        load = max(0, (t - onset_s) * 0.03) if t > onset_s else 0.0
        samples.append(sim.step(setpoint, disturbance=-load,
                                torque_load=load * 200))
        labels.append("CONDITION_CHANGE" if t > onset_s + 10 else "NORMAL")
    return samples, labels


def generate_sluggish(duration_s: float = 300, setpoint: float = 60.0,
                      seed: int = 0) -> tuple[list[dict], list[str]]:
    cfg = SimConfig(tau_hydraulic_s=2.0)
    sim = HydraulicTopDriveSimulator(cfg)
    sim._rng = np.random.default_rng(seed)
    sim.set_bounds(480, 520)  # tight bounds → sluggish
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        samples.append(sim.step(setpoint))
        labels.append("SLUGGISH" if t > 20 else "NORMAL")
    return samples, labels


def generate_windup(duration_s: float = 300, setpoint: float = 60.0,
                    seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = HydraulicTopDriveSimulator(SimConfig())
    sim._rng = np.random.default_rng(seed)
    sim.set_bounds(300, 400)  # bounds too low
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        samples.append(sim.step(setpoint, disturbance=-8.0))
        labels.append("WINDUP" if t > 20 else "NORMAL")
    return samples, labels


def generate_deadband_hunting(duration_s: float = 300, setpoint: float = 60.0,
                              seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = HydraulicTopDriveSimulator(SimConfig())
    sim._rng = np.random.default_rng(seed)
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        osc = 1.5 * np.sin(2 * np.pi * t / 3.0) if t > 60 else 0.0
        samples.append(sim.step(setpoint, disturbance=osc))
        labels.append("DEADBAND_HUNTING" if t > 65 else "NORMAL")
    return samples, labels


def generate_connection(duration_s: float = 300, setpoint: float = 60.0,
                        conn_start: float = 120.0, conn_dur: float = 30.0,
                        seed: int = 0) -> tuple[list[dict], list[str]]:
    """Pipe connection: RPM drops to 0 then resumes."""
    sim = HydraulicTopDriveSimulator(SimConfig())
    sim._rng = np.random.default_rng(seed)
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        sp = 0.0 if conn_start < t < conn_start + conn_dur else setpoint
        samples.append(sim.step(sp))
        labels.append("NORMAL")
    return samples, labels


ALL_GENERATORS = {
    "normal": generate_normal,
    "bias": generate_bias,
    "oscillation": generate_oscillation,
    "stickslip": generate_stickslip,
    "formation_change": generate_formation_change,
    "sluggish": generate_sluggish,
    "windup": generate_windup,
    "deadband_hunting": generate_deadband_hunting,
    "connection": generate_connection,
}
