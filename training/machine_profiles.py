"""Per-equipment simulator profiles for training data generation.

Each equipment class has distinct physical characteristics (gear ratio,
inertia, hydraulic time constant, noise floor) that produce meaningfully
different telemetry signatures. Training a single model across all of
these teaches it to recognise faults regardless of equipment type —
this is critical for deployment across Steve's fleet of ~130 machines.

Usage:
    from training.machine_profiles import MACHINE_PROFILES, make_simulator
    sim = make_simulator("hxi_ht")
    samples = [sim.step(60.0) for _ in range(200)]

    # Weighted by fleet composition (see fleet_catalog.yaml summary)
    for eq_type, weight in MACHINE_PROFILES.items():
        ...
"""
from __future__ import annotations

from dataclasses import dataclass

from training.simulator import HydraulicTopDriveSimulator, SimConfig


@dataclass
class MachineProfile:
    """Physical parameters + training weight for one equipment class."""
    equipment_type: str
    display_name: str
    fleet_count: int               # For training-data weighting

    # PID (approximating per-PLC inner loop)
    Kp: float
    Ki: float
    Kd: float

    # Hydraulic plant
    tau_hydraulic_s: float
    J_rotor_kgm2: float
    gear_ratio: float
    motor_cc: float
    pump_cc: float
    n_pumps: int = 4
    eta_vol: float = 0.93
    eta_mech: float = 0.92

    # Bounds (default operating envelope — override per rig)
    default_lower: int = 350
    default_upper: int = 650

    # Register count scale (per equipment: counts-per-RPM relationship)
    counts_per_rpm: float = 2.5

    # Noise floor by RPM (MASTER_CONTEXT §6.1 values tuned per equipment)
    noise_floor_30_rpm: float = 2.0
    noise_floor_60_rpm: float = 1.5
    noise_floor_120_rpm: float = 0.75
    noise_floor_180_rpm: float = 0.4

    # Max operating envelope (from OEM specs)
    max_rpm: float = 220.0
    max_torque_ft_lbs: float = 37500.0


# ──────────────────────────────────────────────────────────────────────
# Profile catalog — fleet_count from fleet_catalog.yaml summary
# ──────────────────────────────────────────────────────────────────────
MACHINE_PROFILES: dict[str, MachineProfile] = {
    "hxi": MachineProfile(
        equipment_type="hxi",
        display_name="TESCO HXI 800HP",
        fleet_count=12,
        Kp=2.5, Ki=0.8, Kd=0.05,
        tau_hydraulic_s=0.35, J_rotor_kgm2=50.0,
        gear_ratio=10.5, motor_cc=3212, pump_cc=130,
        max_rpm=228, max_torque_ft_lbs=37500,
    ),
    "hxi_ht": MachineProfile(
        equipment_type="hxi_ht",
        display_name="TESCO HXI HT 800HP",
        fleet_count=5,
        Kp=2.2, Ki=0.7, Kd=0.06,        # softer gains — heavier rotor
        tau_hydraulic_s=0.42,            # slightly slower hydraulics
        J_rotor_kgm2=68.0,               # higher inertia (14:1 gear)
        gear_ratio=14.0, motor_cc=3212, pump_cc=130,
        max_rpm=170, max_torque_ft_lbs=50000,
        counts_per_rpm=3.4,              # higher count/rpm at lower max RPM
        noise_floor_60_rpm=1.2,          # smoother due to inertia
    ),
    "hxi_ss": MachineProfile(
        equipment_type="hxi_ss",
        display_name="TESCO HXI Smart Slide 800HP",
        fleet_count=10,
        Kp=2.5, Ki=0.8, Kd=0.05,
        tau_hydraulic_s=0.35, J_rotor_kgm2=50.0,
        gear_ratio=10.5, motor_cc=3212, pump_cc=130,
        max_rpm=228, max_torque_ft_lbs=37500,
    ),
    "exi": MachineProfile(
        equipment_type="exi",
        display_name="TESCO EXI 800HP",
        fleet_count=6,
        Kp=2.6, Ki=0.85, Kd=0.05,
        tau_hydraulic_s=0.33, J_rotor_kgm2=48.0,
        gear_ratio=11.0, motor_cc=3212, pump_cc=130,
        max_rpm=210, max_torque_ft_lbs=40000,
    ),
    "fds": MachineProfile(
        equipment_type="fds",
        display_name="TESCO FDS 800HP (CompactLogix)",
        fleet_count=5,
        Kp=2.4, Ki=0.75, Kd=0.05,
        tau_hydraulic_s=0.38, J_rotor_kgm2=50.0,
        gear_ratio=10.5, motor_cc=3212, pump_cc=130,
        max_rpm=228, max_torque_ft_lbs=37500,
    ),
    "rostel": MachineProfile(
        equipment_type="rostel",
        display_name="Rostel TDS11 750HP",
        fleet_count=11,
        Kp=2.3, Ki=0.7, Kd=0.05,
        tau_hydraulic_s=0.40, J_rotor_kgm2=46.0,
        gear_ratio=10.0, motor_cc=2800, pump_cc=125,
        max_rpm=240, max_torque_ft_lbs=35000,
    ),
    "warrior": MachineProfile(
        equipment_type="warrior",
        display_name="Warrior 600HP",
        fleet_count=5,
        Kp=2.8, Ki=0.9, Kd=0.04,
        tau_hydraulic_s=0.28, J_rotor_kgm2=35.0,   # smaller rotor
        gear_ratio=9.0, motor_cc=2500, pump_cc=110,
        max_rpm=260, max_torque_ft_lbs=25000,
        noise_floor_60_rpm=1.8,           # noisier (less inertia smoothing)
    ),
    "smart_drive": MachineProfile(
        equipment_type="smart_drive",
        display_name="Smart Drive 900HP",
        fleet_count=3,
        Kp=2.5, Ki=0.8, Kd=0.05,
        tau_hydraulic_s=0.37, J_rotor_kgm2=58.0,
        gear_ratio=10.5, motor_cc=3500, pump_cc=140,
        max_rpm=220, max_torque_ft_lbs=42000,
    ),
    "emi": MachineProfile(
        equipment_type="emi",
        display_name="EMI 400HP",
        fleet_count=2,
        Kp=3.0, Ki=1.0, Kd=0.03,          # faster loop (smaller system)
        tau_hydraulic_s=0.22, J_rotor_kgm2=22.0,
        gear_ratio=8.0, motor_cc=1800, pump_cc=90,
        max_rpm=300, max_torque_ft_lbs=18000,
        noise_floor_60_rpm=2.2,
    ),
}


# ──────────────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────────────
def make_simulator(equipment_type: str,
                   config_overrides: dict | None = None
                   ) -> HydraulicTopDriveSimulator:
    """Build a HydraulicTopDriveSimulator calibrated for a specific equipment class.

    Args:
        equipment_type: one of MACHINE_PROFILES keys
        config_overrides: optional dict merged into the generated SimConfig

    Returns:
        HydraulicTopDriveSimulator with per-equipment physics
    """
    p = MACHINE_PROFILES.get(equipment_type)
    if p is None:
        # Fallback to generic HXI
        p = MACHINE_PROFILES["hxi"]

    cfg = SimConfig(
        Kp=p.Kp, Ki=p.Ki, Kd=p.Kd,
        tau_hydraulic_s=p.tau_hydraulic_s,
        J_rotor_kgm2=p.J_rotor_kgm2,
        motor_cc=p.motor_cc,
        pump_cc=p.pump_cc,
        n_pumps=p.n_pumps,
        gear_ratio=p.gear_ratio,
        eta_vol=p.eta_vol,
        eta_mech=p.eta_mech,
        default_lower=p.default_lower,
        default_upper=p.default_upper,
        counts_per_rpm=p.counts_per_rpm,
        noise_floor_30=p.noise_floor_30_rpm,
        noise_floor_60=p.noise_floor_60_rpm,
        noise_floor_120=p.noise_floor_120_rpm,
        noise_floor_180=p.noise_floor_180_rpm,
    )

    if config_overrides:
        for k, v in config_overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)

    return HydraulicTopDriveSimulator(cfg)


def fleet_weighted_types() -> list[tuple[str, int]]:
    """Return (equipment_type, weight) pairs sorted by fleet_count descending.

    Use for training-data generation: sample equipment types proportionally
    to the real fleet composition so the model sees what actually exists.
    """
    return sorted(
        [(eq, p.fleet_count) for eq, p in MACHINE_PROFILES.items()],
        key=lambda x: -x[1],
    )


def total_fleet_count() -> int:
    return sum(p.fleet_count for p in MACHINE_PROFILES.values())
