"""Drilling scenario generators for training data synthesis.

Each generator produces a list of samples + per-sample labels. All scenarios
use HydraulicTopDriveSimulator for physical realism. An optional
`equipment_type` selects machine-class-specific physics (gear ratio, inertia,
noise floor, etc.) via training.machine_profiles.
"""
from __future__ import annotations

import numpy as np

from training.simulator import HydraulicTopDriveSimulator, SimConfig

LABELS = ["NORMAL", "BIAS", "OSCILLATION", "DEADBAND_HUNTING",
          "SLUGGISH", "WINDUP", "CONDITION_CHANGE"]


def _make_sim(equipment_type: str | None = None,
              config_override: SimConfig | None = None
              ) -> HydraulicTopDriveSimulator:
    """Build a simulator for the given equipment class (falls back to generic)."""
    if config_override is not None:
        return HydraulicTopDriveSimulator(config_override)
    if equipment_type:
        try:
            from training.machine_profiles import make_simulator
            return make_simulator(equipment_type)
        except ImportError:
            pass
    return HydraulicTopDriveSimulator(SimConfig())


def _randomise_operating_point(sim: HydraulicTopDriveSimulator,
                                 rng: np.random.Generator,
                                 setpoint: float | None = None,
                                 ) -> float:
    """Set random per-episode bounds wide enough to actually reach setpoint.

    The default bounds [350, 650] only let the plant reach ~35 RPM, so every
    NORMAL episode was actually pinned at its bounds (the classifier learned
    'NORMAL = capped-at-bounds' rather than 'NORMAL = tracking setpoint').
    This widens bounds per episode so we get the full operating envelope
    and match what real rigs do at different drilling phases.
    """
    if setpoint is None:
        setpoint = float(rng.uniform(30.0, 110.0))
    # Widen bounds so the plant can reach setpoint + 30% headroom.
    # We keep them symmetric around swash_mid. The viscous+inertia model
    # means required swash_frac ~ setpoint/max_rpm, but empirically a
    # 400-unit band is enough for 100 RPM operation.
    half_band = int(np.clip(setpoint * 5, 150, 450))
    lower = max(100, sim.cfg.swash_mid - half_band)
    upper = min(900, sim.cfg.swash_mid + half_band)
    sim.set_bounds(lower, upper)
    return setpoint


def generate_normal(duration_s: float = 300, setpoint: float | None = None,
                    equipment_type: str | None = None,
                    seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = _make_sim(equipment_type)
    rng = np.random.default_rng(seed)
    sim._rng = rng
    setpoint = _randomise_operating_point(sim, rng, setpoint)
    samples, labels = [], []
    for _ in np.arange(0, duration_s, 0.5):
        samples.append(sim.step(setpoint))
        labels.append("NORMAL")
    return samples, labels


def generate_bias(duration_s: float = 300, setpoint: float | None = None,
                  bias_rpm: float | None = None, onset_s: float = 100.0,
                  equipment_type: str | None = None,
                  seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = _make_sim(equipment_type)
    rng = np.random.default_rng(seed)
    sim._rng = rng
    setpoint = _randomise_operating_point(sim, rng, setpoint)
    if bias_rpm is None:
        bias_rpm = float(rng.uniform(3.0, 12.0)) * rng.choice([-1, 1])
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        dist = bias_rpm if t > onset_s else 0.0
        samples.append(sim.step(setpoint, disturbance=dist))
        labels.append("BIAS" if t > onset_s + 5 else "NORMAL")
    return samples, labels


def generate_oscillation(duration_s: float = 300, setpoint: float | None = None,
                         amplitude: float | None = None,
                         period_s: float | None = None,
                         onset_s: float = 60.0,
                         equipment_type: str | None = None,
                         seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = _make_sim(equipment_type)
    rng = np.random.default_rng(seed)
    sim._rng = rng
    setpoint = _randomise_operating_point(sim, rng, setpoint)
    if amplitude is None:
        amplitude = float(rng.uniform(5.0, 15.0))
    if period_s is None:
        period_s = float(rng.uniform(3.0, 10.0))
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        osc = amplitude * np.sin(2 * np.pi * t / period_s) if t > onset_s else 0.0
        samples.append(sim.step(setpoint, disturbance=osc))
        labels.append("OSCILLATION" if t > onset_s + 5 else "NORMAL")
    return samples, labels


def generate_stickslip(duration_s: float = 300,
                       setpoint: float | None = None,
                       stick_s: float | None = None, slip_s: float | None = None,
                       onset_s: float = 60.0,
                       equipment_type: str | None = None,
                       seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = _make_sim(equipment_type)
    rng = np.random.default_rng(seed)
    sim._rng = rng
    setpoint = _randomise_operating_point(sim, rng, setpoint)
    if stick_s is None:
        stick_s = float(rng.uniform(4.0, 10.0))
    if slip_s is None:
        slip_s = float(rng.uniform(1.5, 5.0))
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


def generate_multiscale_stickslip(duration_s: float = 300,
                                    setpoint: float | None = None,
                                    onset_s: float = 60.0,
                                    equipment_type: str | None = None,
                                    seed: int = 0
                                    ) -> tuple[list[dict], list[str]]:
    """Stick-slip with mixed-frequency content — closer to the chaotic
    behaviour real drilling exhibits.

    Components:
      - Slow torsional mode (~0.1 Hz): amplitude-modulated sinusoid
      - Fast stick-release transients (0.5–2 Hz): Poisson-distributed impulses
      - Amplitude envelope drifts over minutes (BHA warming + bit dull)

    Labeled OSCILLATION — it's a vibration mode, not a bias or formation
    change. Distinct spectral signature from the classic rectangular
    stick_slip generator above.
    """
    sim = _make_sim(equipment_type)
    rng = np.random.default_rng(seed)
    sim._rng = rng
    setpoint = _randomise_operating_point(sim, rng, setpoint)
    # Randomised per-episode so repeat runs cover parameter space
    slow_freq = rng.uniform(0.05, 0.20)          # Hz
    slow_amp = rng.uniform(6.0, 18.0)
    fast_freq = rng.uniform(0.5, 2.0)            # Hz
    fast_amp = rng.uniform(4.0, 12.0)
    impulse_rate_s = rng.uniform(0.3, 1.5)       # mean impulses per second
    impulse_mag = rng.uniform(10.0, 30.0)
    env_period = rng.uniform(40.0, 120.0)        # slow amplitude envelope

    samples, labels = [], []
    last_impulse_t = onset_s
    for t in np.arange(0, duration_s, 0.5):
        if t > onset_s:
            envelope = 0.6 + 0.4 * np.sin(2 * np.pi * t / env_period)
            slow = slow_amp * envelope * np.sin(2 * np.pi * slow_freq * t)
            fast = fast_amp * envelope * np.sin(2 * np.pi * fast_freq * t
                                                 + rng.uniform(-0.5, 0.5))
            # Poisson-ish impulses: each step, prob ~ rate * dt
            dist = slow + fast
            if (t - last_impulse_t) > rng.exponential(1.0 / impulse_rate_s):
                dist += impulse_mag * rng.choice([-1, 1]) * envelope
                last_impulse_t = t
        else:
            dist = 0.0
        samples.append(sim.step(setpoint, disturbance=dist))
        labels.append("OSCILLATION" if t > onset_s + 5 else "NORMAL")
    return samples, labels


def generate_formation_change(duration_s: float = 600,
                              setpoint: float | None = None,
                              onset_s: float = 200.0,
                              equipment_type: str | None = None,
                              seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = _make_sim(equipment_type)
    rng = np.random.default_rng(seed)
    sim._rng = rng
    setpoint = _randomise_operating_point(sim, rng, setpoint)
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        load = max(0, (t - onset_s) * 0.03) if t > onset_s else 0.0
        samples.append(sim.step(setpoint, disturbance=-load,
                                torque_load=load * 200))
        labels.append("CONDITION_CHANGE" if t > onset_s + 10 else "NORMAL")
    return samples, labels


def generate_chaotic_formation_change(duration_s: float = 600,
                                        setpoint: float | None = None,
                                        onset_s: float = 120.0,
                                        equipment_type: str | None = None,
                                        seed: int = 0
                                        ) -> tuple[list[dict], list[str]]:
    """Formation-change with multi-timescale dynamics — matches what real
    bit-to-formation transitions look like (plateaus, step jumps, gradient
    changes) rather than a single linear ramp.

    Random seed controls the shape: 2–4 hardness regions with piecewise
    load slopes + occasional step changes when breaking into harder rock.
    """
    sim = _make_sim(equipment_type)
    rng = np.random.default_rng(seed)
    sim._rng = rng
    setpoint = _randomise_operating_point(sim, rng, setpoint)

    # Clamp onset so it always leaves room for at least one region boundary
    effective_onset = min(onset_s, max(5.0, duration_s * 0.3))

    # Build a random piecewise-linear + jump load profile
    n_regions = int(rng.integers(2, 5))               # 2–4 formation layers
    region_boundaries = sorted(
        rng.uniform(effective_onset, duration_s, n_regions - 1).tolist()
    ) if duration_s > effective_onset else []
    # Each region has its own load slope (ft-lbs per second reaching the bit)
    slopes = rng.uniform(0.0, 8.0, n_regions)
    jumps = rng.uniform(0.0, 400.0, n_regions)        # step-on-entry (ft-lbs)

    def _load_at(t: float) -> float:
        if t < effective_onset:
            return 0.0
        load = 0.0
        prev = effective_onset
        region_idx = 0
        for b in region_boundaries:
            if t < b:
                load += slopes[region_idx] * (t - prev)
                return load
            load += slopes[region_idx] * (b - prev) + jumps[region_idx + 1]
            prev = b
            region_idx += 1
        load += slopes[-1] * (t - prev)
        return load

    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        load = _load_at(t)
        load *= 1.0 + rng.normal(0, 0.10)
        disturbance = -load / 200.0 if load > 0 else 0.0
        samples.append(sim.step(setpoint, disturbance=disturbance,
                                 torque_load=load))
        labels.append("CONDITION_CHANGE"
                      if t > effective_onset + 10 else "NORMAL")
    return samples, labels


def generate_sluggish(duration_s: float = 300,
                      setpoint: float | None = None,
                      equipment_type: str | None = None,
                      seed: int = 0) -> tuple[list[dict], list[str]]:
    # Override tau_hydraulic for sluggish behaviour regardless of machine class
    base = _make_sim(equipment_type)
    rng = np.random.default_rng(seed)
    base._rng = rng
    # Randomise the degree of sluggishness: 1.5x..4x baseline tau
    base.cfg.tau_hydraulic_s = float(rng.uniform(1.5, 4.0))
    if setpoint is None:
        setpoint = float(rng.uniform(30.0, 90.0))
    # Tight bounds are part of the sluggish failure mode — keep them tight
    # but centred around whatever swash the setpoint requires.
    center = base.cfg.swash_mid
    base.set_bounds(center - 20, center + 20)
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        samples.append(base.step(setpoint))
        labels.append("SLUGGISH" if t > 20 else "NORMAL")
    return samples, labels


def generate_windup(duration_s: float = 300,
                    setpoint: float | None = None,
                    equipment_type: str | None = None,
                    seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = _make_sim(equipment_type)
    rng = np.random.default_rng(seed)
    sim._rng = rng
    if setpoint is None:
        setpoint = float(rng.uniform(60.0, 120.0))
    # Undersized bounds are the DEFINITION of windup — bounds below what
    # setpoint requires. Randomise the degree of undersizing.
    undersizing = rng.uniform(0.4, 0.7)               # 40–70% of needed
    center = sim.cfg.swash_mid
    half_band = int(setpoint * 5 * undersizing)
    sim.set_bounds(center - half_band, center + half_band)
    disturbance_mag = float(rng.uniform(5.0, 12.0))
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        samples.append(sim.step(setpoint, disturbance=-disturbance_mag))
        labels.append("WINDUP" if t > 20 else "NORMAL")
    return samples, labels


def generate_deadband_hunting(duration_s: float = 300,
                              setpoint: float | None = None,
                              equipment_type: str | None = None,
                              seed: int = 0) -> tuple[list[dict], list[str]]:
    sim = _make_sim(equipment_type)
    rng = np.random.default_rng(seed)
    sim._rng = rng
    setpoint = _randomise_operating_point(sim, rng, setpoint)
    # Low-amplitude, short-period oscillation — distinguishing feature
    # of deadband hunting (vs. full OSCILLATION which has larger amp)
    amp = float(rng.uniform(0.8, 2.5))
    period = float(rng.uniform(2.0, 4.5))
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        osc = amp * np.sin(2 * np.pi * t / period) if t > 60 else 0.0
        samples.append(sim.step(setpoint, disturbance=osc))
        labels.append("DEADBAND_HUNTING" if t > 65 else "NORMAL")
    return samples, labels


def generate_connection(duration_s: float = 300,
                        setpoint: float | None = None,
                        conn_start: float = 120.0, conn_dur: float = 30.0,
                        equipment_type: str | None = None,
                        seed: int = 0) -> tuple[list[dict], list[str]]:
    """Pipe connection: RPM drops to 0 then resumes.

    NOTE: excluded from the default training set (see TRAINING_GENERATORS).
    Labeled NORMAL so it doesn't leak a 'CONNECTION' class that would
    collide with real CONNECTION episodes routed to NORMAL by LABEL_REMAP.
    """
    sim = _make_sim(equipment_type)
    rng = np.random.default_rng(seed)
    sim._rng = rng
    setpoint = _randomise_operating_point(sim, rng, setpoint)
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
    "multiscale_stickslip": generate_multiscale_stickslip,
    "formation_change": generate_formation_change,
    "chaotic_formation_change": generate_chaotic_formation_change,
    "sluggish": generate_sluggish,
    "windup": generate_windup,
    "deadband_hunting": generate_deadband_hunting,
    "connection": generate_connection,
}

# Training set excludes `connection` — it's a NORMAL operational event
# (pipe makeup/breakout, RPM drops to 0 then resumes) that collides with
# the NORMAL class and dilutes the majority distribution. Real captured
# CONNECTION episodes are still routed to NORMAL via fine_tune.LABEL_REMAP.
# The `connection` generator is kept in ALL_GENERATORS for the /api/simulate
# sandbox and for manual inspection.
TRAINING_GENERATORS = {
    k: v for k, v in ALL_GENERATORS.items() if k != "connection"
}
