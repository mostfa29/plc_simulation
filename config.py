"""
Physical constants, pipe specifications, and simulation parameters.

Sources:
  - API RP 7G: Recommended Practice for Drill Stem Design and Operating Limits
  - API 5B / 5CT: Thread geometry and casing/tubing specifications
  - API RP 5C1: Care and Use of Casing and Tubing (Table 1 — exact torque values)
  - API RP 5A3: Thread compound friction factors
  - API 7-2: Threading and Gauging for Rotary Shouldered Connections
  - GE CPE305 PLC documentation (register layout — Section 5.1.1)
  - Farr friction model for threaded connections
  - ASTM D341 (Walther equation for oil viscosity)
  - Industrial hydraulic motor / pump specifications
  - NOV / Canrig / MHWirth top drive technical data
  - NOV ST-80/100/120 iron roughneck specifications
  - Vallourec VAM / TenarisHydril / Hunting premium connection datasheets

Design principle: Every magic number lives here. Domain randomization
perturbs these values within specified ranges to create diverse training data.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
from pathlib import Path
import numpy as np


# ═══════════════════════════════════════════════════════════════════
# Per-Machine Register Map & Profile (for multi-rig support)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RegisterDef:
    """Definition of a single PLC register variable.

    Each of Steve's rigs has a different PLC program with process
    data at different register addresses. This captures where one
    variable lives in the %R space.
    """
    address: int                       # %R address (1-based)
    data_type: str = "FLOAT32"         # "FLOAT32", "INT16", "INT32"
    unit: str = ""                     # Engineering unit label
    scale: float = 1.0                 # Multiply raw value by this
    offset: float = 0.0               # Add this after scaling
    description: str = ""


@dataclass
class MachineProfile:
    """Per-machine configuration loaded from YAML.

    Each of Steve's rigs has a different PLC program with process
    data at different register addresses. This profile captures
    the specific register layout, sensor ranges, and mechanical
    characteristics of one physical machine.

    Load with MachineProfile.from_yaml("profiles/precision_rig_709.yaml")
    """
    name: str = "default_sim"
    plc_ip: str = "127.0.0.1"
    plc_port: int = 502
    unit_id: int = 0
    word_swap: bool = True             # GE CPE305 quirk

    # Fleet identification
    equipment_type: str = "unknown"    # EquipmentType.value string
    customer: str = ""                 # Customer / drilling company name
    ewon_name: str = ""                # Talk2m eWon device name

    # Register map: variable_name -> RegisterDef
    reg_map: Dict[str, RegisterDef] = field(default_factory=dict)

    # Machine-specific physical parameters
    motor_displacement_cc: float = 250.0
    max_pressure_psi: float = 5000.0
    encoder_cpr: int = 1174
    torque_cell_capacity_ftlbs: float = 50_000.0

    # Sensor calibration (from real data)
    pressure_offset: float = 0.0
    torque_offset: float = 0.0
    temp_offset: float = 0.0

    def get_register_address(self, var_name: str) -> Optional[int]:
        """Get register address for a variable, or None if not mapped."""
        reg = self.reg_map.get(var_name)
        return reg.address if reg else None

    @staticmethod
    def from_yaml(path: str) -> 'MachineProfile':
        """Load a MachineProfile from a YAML file."""
        import yaml
        with open(path, 'r') as f:
            data = yaml.safe_load(f)

        reg_map = {}
        for name, rdef in data.pop('reg_map', {}).items():
            if isinstance(rdef, dict):
                reg_map[name] = RegisterDef(
                    address=rdef.get('address', 0),
                    data_type=rdef.get('data_type', 'FLOAT32'),
                    unit=rdef.get('unit', ''),
                    scale=rdef.get('scale', 1.0),
                    offset=rdef.get('offset', 0.0),
                    description=rdef.get('description', ''),
                )

        return MachineProfile(reg_map=reg_map, **data)

    def to_yaml(self, path: str):
        """Save this MachineProfile to a YAML file."""
        import yaml
        data = {
            'name': self.name,
            'plc_ip': self.plc_ip,
            'plc_port': self.plc_port,
            'unit_id': self.unit_id,
            'word_swap': self.word_swap,
            'equipment_type': self.equipment_type,
        }
        if self.customer:
            data['customer'] = self.customer
        if self.ewon_name:
            data['ewon_name'] = self.ewon_name
        data.update({
            'motor_displacement_cc': self.motor_displacement_cc,
            'max_pressure_psi': self.max_pressure_psi,
            'encoder_cpr': self.encoder_cpr,
            'torque_cell_capacity_ftlbs': self.torque_cell_capacity_ftlbs,
            'pressure_offset': self.pressure_offset,
            'torque_offset': self.torque_offset,
            'temp_offset': self.temp_offset,
            'reg_map': {},
        })
        for name, rdef in self.reg_map.items():
            entry = {'address': rdef.address, 'data_type': rdef.data_type}
            if rdef.unit:
                entry['unit'] = rdef.unit
            if rdef.scale != 1.0:
                entry['scale'] = rdef.scale
            if rdef.offset != 0.0:
                entry['offset'] = rdef.offset
            if rdef.description:
                entry['description'] = rdef.description
            data['reg_map'][name] = entry

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    @staticmethod
    def load_all_profiles(profiles_dir: str = "profiles") -> Dict[str, 'MachineProfile']:
        """Load all YAML profiles from a directory, keyed by profile name."""
        profiles = {}
        p = Path(profiles_dir)
        if p.exists():
            for f in p.glob("*.yaml"):
                try:
                    profile = MachineProfile.from_yaml(str(f))
                    profiles[profile.name] = profile
                except Exception:
                    pass
            for f in p.glob("*.yml"):
                try:
                    profile = MachineProfile.from_yaml(str(f))
                    profiles[profile.name] = profile
                except Exception:
                    pass
        return profiles

    @staticmethod
    def find_by_ip(profiles_dir: str, plc_ip: str) -> Optional['MachineProfile']:
        """Find a profile matching a given PLC IP address."""
        all_profiles = MachineProfile.load_all_profiles(profiles_dir)
        for profile in all_profiles.values():
            if profile.plc_ip == plc_ip:
                return profile
        return None


# Default R6000 register map (Steve's shop unit / simulator fallback)
DEFAULT_REG_MAP: Dict[str, RegisterDef] = {
    'torque':           RegisterDef(address=6000, data_type='FLOAT32', unit='ft-lbs'),
    'rpm':              RegisterDef(address=6002, data_type='FLOAT32', unit='RPM'),
    'pressure':         RegisterDef(address=6004, data_type='FLOAT32', unit='PSI'),
    'temperature':      RegisterDef(address=6006, data_type='FLOAT32', unit='degF'),
    'encoder_counts':   RegisterDef(address=6008, data_type='FLOAT32', unit='counts'),
    'pid_setpoint':     RegisterDef(address=6010, data_type='FLOAT32'),
    'pid_error':        RegisterDef(address=6012, data_type='FLOAT32'),
    'pid_output':       RegisterDef(address=6014, data_type='INT16'),
    'operating_mode':   RegisterDef(address=6015, data_type='INT16'),
    'target_torque':    RegisterDef(address=6016, data_type='FLOAT32', unit='ft-lbs'),
    'turns':            RegisterDef(address=6018, data_type='FLOAT32', unit='turns'),
    'fault_code':       RegisterDef(address=6020, data_type='INT16'),
    'connection_state': RegisterDef(address=6021, data_type='INT16'),
    'peak_torque':      RegisterDef(address=6022, data_type='FLOAT32', unit='ft-lbs'),
    'hookload':         RegisterDef(address=6024, data_type='FLOAT32', unit='klbs'),
    'shoulder_torque':  RegisterDef(address=6026, data_type='FLOAT32', unit='ft-lbs'),
    'slope_dT_dN':      RegisterDef(address=6028, data_type='FLOAT32', unit='ft-lbs/turn'),
    'connection_count': RegisterDef(address=6030, data_type='INT16'),
}


# ═══════════════════════════════════════════════════════════════════
# Machine Type Enumeration
# ═══════════════════════════════════════════════════════════════════

class MachineType(Enum):
    """Oilfield machine categories per reference Section 2."""
    TOP_DRIVE = "top_drive"
    IRON_ROUGHNECK = "iron_roughneck"
    POWER_TONG = "power_tong"
    BUCKING_UNIT = "bucking_unit"
    CASING_RUNNING_TOOL = "casing_running_tool"


class EquipmentType(Enum):
    """Specific top drive equipment variants in Steve's fleet.

    All are MachineType.TOP_DRIVE but differ in control system,
    gear ratio, motor HP, and torque-speed characteristics.
    Classified from Talk2m eWon fleet export descriptions.
    """
    HXI = "hxi"                       # Standard HXI (single speed)
    HXI_HT = "hxi_ht"                 # HXI High Torque (2-3 speed gearbox)
    HXI_SMART_SLIDE = "hxi_smart_slide"  # HXI with Smart Slide kit
    EXI = "exi"                       # EXI 800HP
    FDS = "fds"                       # FDS (Allen-Bradley CompactLogix)
    ROSTEL = "rostel"                 # Rostel (GE Rx3i PLC)
    WARRIOR = "warrior"               # Warrior 250T
    SMART_DRIVE = "smart_drive"       # Smart Drive 900HP
    ECI = "eci"                       # ECI
    EMI = "emi"                       # EMI 400
    SHOP_UNIT = "shop_unit"           # Shop/commissioning unit
    UNKNOWN = "unknown"


@dataclass
class EquipmentSpec:
    """Physics-relevant defaults per equipment type.

    These override ACMotorSpec and TopDriveSpec defaults when
    generating synthetic data for a specific equipment variant.
    """
    rated_hp: float
    gear_ratio: float
    num_speeds: int                    # 1 = single speed, 2-3 = multi-speed HT
    max_torque_ftlbs: float            # Continuous output torque
    max_rpm: float                     # Max output RPM (after gearbox)
    plc_platform: str                  # "CPE305", "CompactLogix", "Rx3i"
    word_swap: bool = True             # GE word-swapped FLOAT32
    max_intermittent_torque_ftlbs: float = 0.0  # Burst (10s), 0 = auto 1.5x
    rated_motor_rpm: float = 1800.0    # Motor base speed
    rotor_inertia_kgm2: float = 12.0
    gearbox_inertia_kgm2: float = 25.0

    def __post_init__(self):
        if self.max_intermittent_torque_ftlbs == 0:
            self.max_intermittent_torque_ftlbs = self.max_torque_ftlbs * 1.5


EQUIPMENT_SPECS: Dict[str, 'EquipmentSpec'] = {
    # ── Standard HXI ─────────────────────────────────────────────
    EquipmentType.HXI: EquipmentSpec(
        rated_hp=800, gear_ratio=10.5, num_speeds=1,
        max_torque_ftlbs=37_500, max_rpm=228,
        plc_platform="CPE305", word_swap=True,
    ),
    # ── HXI High Torque (multi-speed gearbox) ────────────────────
    EquipmentType.HXI_HT: EquipmentSpec(
        rated_hp=800, gear_ratio=14.0, num_speeds=3,
        max_torque_ftlbs=50_000, max_rpm=170,
        plc_platform="CPE305", word_swap=True,
        max_intermittent_torque_ftlbs=75_000,
        gearbox_inertia_kgm2=35.0,  # Heavier multi-speed gearbox
    ),
    # ── HXI Smart Slide (same drivetrain as HXI, adds slide) ────
    EquipmentType.HXI_SMART_SLIDE: EquipmentSpec(
        rated_hp=800, gear_ratio=10.5, num_speeds=1,
        max_torque_ftlbs=37_500, max_rpm=228,
        plc_platform="CPE305", word_swap=True,
    ),
    # ── EXI 800HP ────────────────────────────────────────────────
    EquipmentType.EXI: EquipmentSpec(
        rated_hp=800, gear_ratio=11.0, num_speeds=1,
        max_torque_ftlbs=40_000, max_rpm=210,
        plc_platform="CPE305", word_swap=True,
    ),
    # ── FDS (Allen-Bradley CompactLogix) ─────────────────────────
    EquipmentType.FDS: EquipmentSpec(
        rated_hp=800, gear_ratio=10.5, num_speeds=1,
        max_torque_ftlbs=37_500, max_rpm=228,
        plc_platform="CompactLogix", word_swap=False,  # AB = normal byte order
    ),
    # ── Rostel (GE Rx3i) ────────────────────────────────────────
    EquipmentType.ROSTEL: EquipmentSpec(
        rated_hp=750, gear_ratio=10.0, num_speeds=1,
        max_torque_ftlbs=35_000, max_rpm=240,
        plc_platform="Rx3i", word_swap=True,
    ),
    # ── Warrior 250T ─────────────────────────────────────────────
    EquipmentType.WARRIOR: EquipmentSpec(
        rated_hp=600, gear_ratio=9.0, num_speeds=1,
        max_torque_ftlbs=25_000, max_rpm=260,
        plc_platform="CPE305", word_swap=True,
        rotor_inertia_kgm2=8.0,       # Smaller motor
        gearbox_inertia_kgm2=18.0,
    ),
    # ── Smart Drive 900HP ────────────────────────────────────────
    EquipmentType.SMART_DRIVE: EquipmentSpec(
        rated_hp=900, gear_ratio=10.5, num_speeds=1,
        max_torque_ftlbs=42_000, max_rpm=220,
        plc_platform="CPE305", word_swap=True,
        rotor_inertia_kgm2=14.0,
        gearbox_inertia_kgm2=28.0,
    ),
    # ── ECI ──────────────────────────────────────────────────────
    EquipmentType.ECI: EquipmentSpec(
        rated_hp=800, gear_ratio=10.5, num_speeds=1,
        max_torque_ftlbs=37_500, max_rpm=228,
        plc_platform="CPE305", word_swap=True,
    ),
    # ── EMI 400 ──────────────────────────────────────────────────
    EquipmentType.EMI: EquipmentSpec(
        rated_hp=400, gear_ratio=8.0, num_speeds=1,
        max_torque_ftlbs=18_000, max_rpm=300,
        plc_platform="CPE305", word_swap=True,
        rotor_inertia_kgm2=6.0,
        gearbox_inertia_kgm2=12.0,
    ),
    # ── Shop / Commissioning Unit ────────────────────────────────
    EquipmentType.SHOP_UNIT: EquipmentSpec(
        rated_hp=800, gear_ratio=10.5, num_speeds=1,
        max_torque_ftlbs=37_500, max_rpm=228,
        plc_platform="CPE305", word_swap=True,
    ),
    # ── Unknown / fallback ───────────────────────────────────────
    EquipmentType.UNKNOWN: EquipmentSpec(
        rated_hp=800, gear_ratio=10.5, num_speeds=1,
        max_torque_ftlbs=37_500, max_rpm=228,
        plc_platform="CPE305", word_swap=True,
    ),
}


class ConnectionCategory(Enum):
    """Connection type categories per reference Section 3."""
    API_8RD_STC = "api_8rd_stc"       # API 8-round Short Thread Coupling
    API_8RD_LTC = "api_8rd_ltc"       # API 8-round Long Thread Coupling
    API_BUTTRESS = "api_buttress"      # API Buttress Thread (BTC)
    API_EXTREME_LINE = "api_xl"        # API Extreme-Line (integral flush)
    PREMIUM_SHOULDERED = "premium"     # Premium with metal-to-metal seal + shoulder
    PREMIUM_FLUSH = "premium_flush"    # Premium flush/semi-flush (integral)
    DRILL_PIPE = "drill_pipe"         # API 7-2 rotary shouldered connections


class TorqueMeasurementMethod(Enum):
    """How torque is measured — affects noise characteristics (Section 4.4)."""
    MOTOR_CURRENT = "motor_current"      # Top drive: T = I * K_motor, SNR 45-60 dB
    PRESSURE_TRANSDUCER = "pressure"     # Iron roughneck: T = P * K, SNR 50-65 dB
    LOAD_CELL = "load_cell"              # Power tong: strain gauge, SNR 55-70 dB
    CALIBRATED_LOAD_CELL = "cal_cell"    # Bucking unit: precision cell, SNR 60-75 dB


# ═══════════════════════════════════════════════════════════════════
# Pipe Steel Grades (API 5CT — Section 3.3)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PipeGrade:
    """Steel grade properties per API 5CT."""
    name: str
    min_yield_ksi: float
    max_yield_ksi: float
    min_tensile_ksi: float
    sour_service: bool
    friction_factor_scale: float = 1.0  # Relative to J55 baseline


GRADE_CATALOG: Dict[str, PipeGrade] = {
    "H-40": PipeGrade("H-40", 40, 80, 60, False, 0.90),
    "J-55": PipeGrade("J-55", 55, 80, 75, False, 1.00),
    "K-55": PipeGrade("K-55", 55, 80, 95, False, 1.00),
    "N-80": PipeGrade("N-80", 80, 110, 100, False, 1.08),
    "L-80": PipeGrade("L-80", 80, 95, 95, True, 1.05),
    "L-80-9Cr": PipeGrade("L-80-9Cr", 80, 95, 95, True, 1.10),
    "L-80-13Cr": PipeGrade("L-80-13Cr", 80, 95, 95, True, 1.15),
    "C-90": PipeGrade("C-90", 90, 105, 100, True, 1.10),
    "R-95": PipeGrade("R-95", 95, 110, 105, False, 1.12),
    "T-95": PipeGrade("T-95", 95, 110, 105, True, 1.12),
    "C-110": PipeGrade("C-110", 110, 120, 125, True, 1.18),
    "P-110": PipeGrade("P-110", 110, 140, 125, False, 1.20),
    "Q-125": PipeGrade("Q-125", 125, 150, 135, False, 1.25),
}


# ═══════════════════════════════════════════════════════════════════
# Sub-System Specifications
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ACMotorSpec:
    """AC induction motor + VFD specifications for top drives (Section 2.1).

    Modern top drives use AC motors with VFDs, NOT hydraulic motors.
    Torque-speed characteristic:
      - Below base speed: constant torque (T = T_rated)
      - Above base speed: constant power (T = T_rated * RPM_base / RPM)
    """
    rated_hp: float = 800.0               # Motor nameplate HP
    rated_rpm: float = 1800.0             # Motor base speed (60Hz)
    rated_torque_nm: float = 3180.0       # T = HP * 5252 / RPM * 1.3558
    max_torque_multiplier: float = 2.5    # VFD can deliver 250% rated torque transiently
    slip_pct: float = 2.5                 # Induction motor slip at rated load
    efficiency: float = 0.94              # Motor efficiency at rated load
    power_factor: float = 0.87            # Rated power factor
    rotor_inertia_kgm2: float = 12.0     # Motor rotor inertia
    vfd_response_ms: float = 50.0         # VFD torque response time
    vfd_current_limit_pct: float = 150.0  # VFD current limit (% of rated)
    regenerative_braking: bool = True     # VFD supports regen braking


@dataclass
class TopDriveSpec:
    """Top drive mechanical specifications (Section 2.1).

    Reference machines: NOV TDS-11SA (800HP), TDS-8 (1150HP), Canrig Sigma 500T
    """
    motor: ACMotorSpec = field(default_factory=ACMotorSpec)
    gear_ratio: float = 10.5              # Helical gear reduction (8:1 to 12:1)
    max_output_rpm: float = 228.0         # Output shaft max RPM (after gearbox)
    max_continuous_torque_ftlbs: float = 37_500.0  # Continuous rating
    max_intermittent_torque_ftlbs: float = 55_000.0  # 10-second burst
    gearbox_inertia_kgm2: float = 25.0   # Gearbox reflected inertia (output side)
    gearbox_efficiency: float = 0.97      # Gear mesh efficiency
    gearbox_backlash_deg: float = 0.1     # Backlash in gear train (degrees)
    brake_torque_ftlbs: float = 80_000.0  # Hydraulic disc brake holding torque
    quill_encoder_cpr: int = 1174         # Quill shaft encoder counts per revolution
    seal_friction_nm: float = 25.0        # Coulomb friction from shaft seals
    viscous_damping_nms: float = 12.0     # Viscous damping (seals + bearings)
    torque_measurement: TorqueMeasurementMethod = TorqueMeasurementMethod.MOTOR_CURRENT
    hoist_capacity_tons: float = 500.0    # Hook load capacity


@dataclass
class IronRoughneckSpec:
    """Iron roughneck specifications (Section 2.2).

    Reference machines: NOV ST-80C, ST-100, ST-120
    Two subsystems: spinner (low torque, high RPM) + torque wrench (high torque, low angular sweep)
    """
    # Spinner subsystem
    spinner_motor_displacement_cc: float = 80.0   # Hydraulic gear motor
    spinner_max_rpm: float = 75.0                 # Spin RPM
    spinner_max_torque_ftlbs: float = 1_750.0     # Spin torque
    spinner_flow_gpm: float = 35.0                # Required flow
    spinner_pressure_psi: float = 2_500.0         # Operating pressure

    # Torque wrench subsystem
    wrench_cylinder_bore_in: float = 6.0          # Hydraulic cylinder bore
    wrench_stroke_in: float = 8.0                 # Cylinder stroke
    wrench_moment_arm_in: float = 12.0            # Torque arm length
    wrench_max_torque_ftlbs: float = 80_000.0     # Makeup torque capacity
    wrench_breakout_torque_ftlbs: float = 100_000.0  # Breakout capacity
    wrench_angular_sweep_deg: float = 45.0        # Degrees per stroke
    wrench_pressure_psi: float = 3_000.0          # Operating pressure

    # Handoff parameters
    handoff_delay_ms: float = 500.0               # Time for spinner-to-wrench transition
    handoff_rpm_threshold: float = 2.0            # RPM below which wrench engages

    # General
    pipe_od_range: Tuple[float, float] = (4.125, 8.5)
    gripper_clamp_force_lbs: float = 50_000.0
    horizontal_travel_in: float = 72.0
    weight_lbs: float = 7_800.0
    torque_measurement: TorqueMeasurementMethod = TorqueMeasurementMethod.PRESSURE_TRANSDUCER


@dataclass
class PowerTongSpec:
    """Power tong specifications (Section 2.3).

    Continuous rotation, suspended operation, requires backup tong.
    """
    motor_displacement_cc: float = 120.0
    max_rpm_high_gear: float = 12.0
    max_rpm_low_gear: float = 5.0
    max_torque_high_gear_ftlbs: float = 30_000.0
    max_torque_low_gear_ftlbs: float = 100_000.0
    operating_pressure_psi: float = 2_500.0
    flow_gpm: float = 50.0
    two_speed: bool = True
    gear_shift_torque_ftlbs: float = 25_000.0   # Auto-shift threshold
    arm_compliance_deg_per_klb: float = 0.02     # Tong arm flex
    backup_tong_friction_ftlbs: float = 500.0
    torque_measurement: TorqueMeasurementMethod = TorqueMeasurementMethod.LOAD_CELL


@dataclass
class BuckingUnitSpec:
    """Bucking unit specifications (Section 2.4).

    Stationary, horizontal, precision torque control.
    Cleanest torque-turn curves (gold standard for training data).
    """
    motor_displacement_cc: float = 150.0
    max_rpm: float = 15.0
    max_torque_ftlbs: float = 150_000.0
    operating_pressure_psi: float = 3_000.0
    flow_gpm: float = 60.0
    servo_response_ms: float = 50.0       # Faster than field machines
    position_resolution_deg: float = 0.01  # CNC-grade positioning
    torque_measurement: TorqueMeasurementMethod = TorqueMeasurementMethod.CALIBRATED_LOAD_CELL
    vibration_isolation: bool = True        # No rig vibration


@dataclass
class ValveSpec:
    """Proportional directional control valve parameters."""
    spool_time_constant_ms: float = 25.0
    rate_limit_pct_per_s: float = 500.0
    dead_zone_pct: float = 1.5
    hysteresis_pct: float = 2.0
    null_band_pct: float = 0.5
    max_flow_gpm: float = 60.0
    rated_pressure_drop_psi: float = 150.0


@dataclass
class PumpSpec:
    """Hydraulic pump (HPU) specifications."""
    pump_type: str = "axial_piston"
    displacement_cc: float = 100.0
    drive_rpm: float = 1800.0
    num_pistons: int = 9
    max_pressure_psi: float = 5000.0
    vol_efficiency_coeffs: Tuple[float, ...] = (0.97, 0.95, 0.92, 0.87)
    mech_efficiency: float = 0.92
    relief_cracking_psi: float = 4800.0
    relief_full_flow_psi: float = 5200.0
    relief_reseat_psi: float = 4600.0
    trapped_volume_in3: float = 150.0


@dataclass
class OilSpec:
    """Hydraulic fluid properties (ISO VG 46)."""
    iso_grade: int = 46
    kinematic_viscosity_40c_cst: float = 46.0
    kinematic_viscosity_100c_cst: float = 6.8
    density_kg_m3: float = 870.0
    specific_heat_j_kgk: float = 2000.0
    bulk_modulus_psi: float = 200_000.0
    air_content_pct: float = 2.0
    thermal_conductivity_w_mk: float = 0.14


@dataclass
class ThreadCompoundSpec:
    """Thread compound (dope) friction properties per API RP 5A3."""
    name: str = "API_Modified_Zinc"
    base_kf: float = 0.08
    temp_coefficient: float = -0.0003
    shear_rate_exponent: float = -0.15
    reference_temp_f: float = 77.0
    reference_shear_rate: float = 100.0
    max_service_temp_f: float = 300.0
    degradation_rate: float = 0.001


@dataclass
class PipeStringSpec:
    """Pipe string properties for torsional dynamics."""
    length_ft: float = 30.0
    num_joints: int = 1
    shear_modulus_psi: float = 11.5e6
    material_damping_ratio: float = 0.02
    joint_friction_ftlbs: float = 5.0


# ═══════════════════════════════════════════════════════════════════
# Thread Compound Catalog
# ═══════════════════════════════════════════════════════════════════

COMPOUND_CATALOG: Dict[str, ThreadCompoundSpec] = {
    "API_Modified_Zinc": ThreadCompoundSpec(
        name="API_Modified_Zinc", base_kf=0.08,
        temp_coefficient=-0.0003, shear_rate_exponent=-0.15,
    ),
    "API_Modified_Lead": ThreadCompoundSpec(
        name="API_Modified_Lead", base_kf=0.06,
        temp_coefficient=-0.0002, shear_rate_exponent=-0.12,
        max_service_temp_f=250.0,
    ),
    "Copper_Based": ThreadCompoundSpec(
        name="Copper_Based", base_kf=0.10,
        temp_coefficient=-0.0004, shear_rate_exponent=-0.10,
        max_service_temp_f=350.0,
    ),
    "Eco_Green": ThreadCompoundSpec(
        name="Eco_Green", base_kf=0.12,
        temp_coefficient=-0.0005, shear_rate_exponent=-0.18,
        max_service_temp_f=200.0,
    ),
    "Molybdenum": ThreadCompoundSpec(
        name="Molybdenum", base_kf=0.05,
        temp_coefficient=-0.0001, shear_rate_exponent=-0.08,
        max_service_temp_f=400.0,
    ),
    "Dopeless_Coating": ThreadCompoundSpec(
        name="Dopeless_Coating", base_kf=0.04,
        temp_coefficient=-0.00005, shear_rate_exponent=-0.05,
        max_service_temp_f=450.0,
        degradation_rate=0.0002,
    ),
}


# ═══════════════════════════════════════════════════════════════════
# Pipe Specifications (API 5B / 5CT / RP 7G)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PipeSpec:
    """Thread geometry and torque specs for a specific pipe size/grade/connection."""
    name: str
    od_inches: float
    weight_per_foot: float
    grade: str
    connection_type: str                # LTC, BTC, STC, PREMIUM, DRILL_PIPE
    connection_category: ConnectionCategory = ConnectionCategory.API_8RD_LTC
    optimum_torque_ftlbs: float = 0.0
    min_torque_ftlbs: float = 0.0
    max_torque_ftlbs: float = 0.0
    thread_pitch_tpi: float = 8.0
    thread_taper_ipf: float = 0.0625
    turns_to_shoulder: float = 5.0
    delta_turns: float = 0.04
    seal_diameter_inches: float = 0.0

    # Thread geometry (API 5B tables)
    thread_compound_kf: float = 0.08
    thread_length_inches: float = 0.0
    pitch_diameter_inches: float = 0.0
    root_diameter_inches: float = 0.0
    thread_height_inches: float = 0.0
    lead_inches: float = 0.0
    taper_half_angle_deg: float = 0.0
    thread_flank_angle_deg: float = 60.0
    hand_tight_turns: float = 0.0
    yield_strength_psi: float = 0.0

    # Premium connection fields (Section 3.2.3)
    shoulder_torque_min_ftlbs: float = 0.0     # Min shoulder torque
    shoulder_torque_max_ftlbs: float = 0.0     # Max shoulder torque
    yield_torque_ftlbs: float = 0.0            # Damage threshold
    seal_engagement_turn_fraction: float = 0.0  # Fraction of power-tight where seal engages
    power_tight_slope_ftlbs_per_turn: float = 0.0  # Expected slope in power-tight zone

    # Drill pipe fields
    pin_od_inches: float = 0.0
    box_od_inches: float = 0.0
    breakout_torque_ftlbs: float = 0.0

    # Compatibility with machine types
    compatible_machines: Tuple[MachineType, ...] = (
        MachineType.TOP_DRIVE, MachineType.IRON_ROUGHNECK,
        MachineType.POWER_TONG, MachineType.BUCKING_UNIT,
    )

    def __post_init__(self):
        if self.lead_inches == 0 and self.thread_pitch_tpi > 0:
            self.lead_inches = 1.0 / self.thread_pitch_tpi
        if self.taper_half_angle_deg == 0 and self.thread_taper_ipf > 0:
            self.taper_half_angle_deg = np.degrees(
                np.arctan(self.thread_taper_ipf / 24.0)
            )
        if self.thread_height_inches == 0 and self.thread_pitch_tpi > 0:
            if self.connection_category in (
                ConnectionCategory.API_BUTTRESS,
            ):
                self.thread_height_inches = 0.500 / self.thread_pitch_tpi
            else:
                self.thread_height_inches = 0.626 / self.thread_pitch_tpi
        if self.yield_strength_psi == 0:
            grade_obj = GRADE_CATALOG.get(self.grade)
            if grade_obj:
                self.yield_strength_psi = grade_obj.min_yield_ksi * 1000
            else:
                self.yield_strength_psi = 80_000
        if self.seal_diameter_inches == 0:
            self.seal_diameter_inches = self.od_inches * 0.88

    @property
    def total_turns(self) -> float:
        return self.turns_to_shoulder + self.delta_turns

    @property
    def torque_gradient(self) -> float:
        if self.delta_turns > 0:
            return self.optimum_torque_ftlbs / self.delta_turns
        return self.optimum_torque_ftlbs / 0.04

    @property
    def id_inches(self) -> float:
        id_sq = self.od_inches**2 - self.weight_per_foot / 10.68
        return np.sqrt(max(id_sq, 0.1))

    @property
    def wall_thickness_inches(self) -> float:
        return (self.od_inches - self.id_inches) / 2.0

    @property
    def cross_section_area_in2(self) -> float:
        return np.pi / 4.0 * (self.od_inches**2 - self.id_inches**2)

    @property
    def polar_moment_in4(self) -> float:
        return np.pi / 32.0 * (self.od_inches**4 - self.id_inches**4)

    @property
    def is_premium(self) -> bool:
        return self.connection_category in (
            ConnectionCategory.PREMIUM_SHOULDERED,
            ConnectionCategory.PREMIUM_FLUSH,
        )

    @property
    def is_drill_pipe(self) -> bool:
        return self.connection_category == ConnectionCategory.DRILL_PIPE

    @property
    def is_buttress(self) -> bool:
        return self.connection_category == ConnectionCategory.API_BUTTRESS


# ═══════════════════════════════════════════════════════════════════
# Pipe Catalog — API 8-Round Casing (API RP 5C1 Table 1, EXACT values)
# ═══════════════════════════════════════════════════════════════════

def _ltc(name, od, wt, grade, min_t, opt_t, max_t, turns, delta,
         seal_d=0.0, tl=0.0, pd=0.0, ht=0.0):
    return PipeSpec(
        name=name, od_inches=od, weight_per_foot=wt, grade=grade,
        connection_type="LTC", connection_category=ConnectionCategory.API_8RD_LTC,
        optimum_torque_ftlbs=opt_t, min_torque_ftlbs=min_t, max_torque_ftlbs=max_t,
        thread_pitch_tpi=8, thread_taper_ipf=0.0625,
        turns_to_shoulder=turns, delta_turns=delta,
        seal_diameter_inches=seal_d if seal_d else od * 0.88,
        thread_length_inches=tl, pitch_diameter_inches=pd,
        hand_tight_turns=turns * 0.8,
    )

def _stc(name, od, wt, grade, min_t, opt_t, max_t, turns, delta, seal_d=0.0):
    return PipeSpec(
        name=name, od_inches=od, weight_per_foot=wt, grade=grade,
        connection_type="STC", connection_category=ConnectionCategory.API_8RD_STC,
        optimum_torque_ftlbs=opt_t, min_torque_ftlbs=min_t, max_torque_ftlbs=max_t,
        thread_pitch_tpi=8, thread_taper_ipf=0.0625,
        turns_to_shoulder=turns, delta_turns=delta,
        seal_diameter_inches=seal_d if seal_d else od * 0.88,
        hand_tight_turns=turns * 0.8,
    )

def _btc(name, od, wt, grade, min_t, opt_t, max_t, turns, delta, seal_d=0.0):
    return PipeSpec(
        name=name, od_inches=od, weight_per_foot=wt, grade=grade,
        connection_type="BTC", connection_category=ConnectionCategory.API_BUTTRESS,
        optimum_torque_ftlbs=opt_t, min_torque_ftlbs=min_t, max_torque_ftlbs=max_t,
        thread_pitch_tpi=5, thread_taper_ipf=0.0625,
        thread_flank_angle_deg=13.0,  # Buttress: 3° + 10° asymmetric
        turns_to_shoulder=turns, delta_turns=delta,
        seal_diameter_inches=seal_d if seal_d else od * 0.88,
        hand_tight_turns=turns * 0.8,
    )


PIPE_CATALOG: Dict[str, PipeSpec] = {
    # ═══════════════════════════════════════════════════════════════
    # API 8-Round LTC Casing — API RP 5C1 Table 1 (EXACT VALUES)
    # ═══════════════════════════════════════════════════════════════

    # 4-1/2" casing
    "4.5in_11.6lb_J55_LTC": _ltc("4.5in_11.6lb_J55_LTC",
        4.5, 11.60, "J-55", 2_140, 2_680, 3_350, 4.5, 0.25),
    "4.5in_11.6lb_N80_LTC": _ltc("4.5in_11.6lb_N80_LTC",
        4.5, 11.60, "N-80", 2_680, 3_350, 4_190, 4.5, 0.25),
    "4.5in_11.6lb_P110_LTC": _ltc("4.5in_11.6lb_P110_LTC",
        4.5, 11.60, "P-110", 3_620, 4_530, 5_660, 4.5, 0.25),

    # 5-1/2" casing
    "5.5in_17lb_J55_LTC": _ltc("5.5in_17lb_J55_LTC",
        5.5, 17.00, "J-55", 2_870, 3_590, 4_490, 5.5, 0.30),
    "5.5in_17lb_N80_LTC": _ltc("5.5in_17lb_N80_LTC",
        5.5, 17.00, "N-80", 3_480, 4_350, 5_440, 5.5, 0.30),
    "5.5in_17lb_P110_LTC": _ltc("5.5in_17lb_P110_LTC",
        5.5, 17.00, "P-110", 4_580, 5_730, 7_160, 5.5, 0.30),
    "5.5in_23lb_P110_LTC": _ltc("5.5in_23lb_P110_LTC",
        5.5, 23.00, "P-110", 6_620, 8_280, 10_350, 5.5, 0.30),

    # 7" casing
    "7in_23lb_J55_LTC": _ltc("7in_23lb_J55_LTC",
        7.0, 23.00, "J-55", 2_780, 3_470, 4_340, 6.0, 0.35),
    "7in_23lb_N80_LTC": _ltc("7in_23lb_N80_LTC",
        7.0, 23.00, "N-80", 4_010, 5_010, 6_260, 6.0, 0.35),
    "7in_23lb_P110_LTC": _ltc("7in_23lb_P110_LTC",
        7.0, 23.00, "P-110", 5_020, 6_280, 7_850, 6.0, 0.35),
    "7in_29lb_N80_LTC": _ltc("7in_29lb_N80_LTC",
        7.0, 29.00, "N-80", 5_440, 6_800, 8_500, 6.0, 0.35),
    "7in_29lb_P110_LTC": _ltc("7in_29lb_P110_LTC",
        7.0, 29.00, "P-110", 6_960, 8_700, 10_870, 6.0, 0.35),
    "7in_35lb_P110_LTC": _ltc("7in_35lb_P110_LTC",
        7.0, 35.00, "P-110", 9_070, 11_340, 14_170, 6.0, 0.35),

    # 9-5/8" casing
    "9.625in_36lb_J55_LTC": _ltc("9.625in_36lb_J55_LTC",
        9.625, 36.00, "J-55", 5_560, 6_950, 8_690, 8.0, 0.50),
    "9.625in_36lb_N80_LTC": _ltc("9.625in_36lb_N80_LTC",
        9.625, 36.00, "N-80", 6_600, 8_250, 10_310, 8.0, 0.50),
    "9.625in_40lb_N80_LTC": _ltc("9.625in_40lb_N80_LTC",
        9.625, 40.00, "N-80", 7_660, 9_580, 11_970, 8.0, 0.50),
    "9.625in_40lb_P110_LTC": _ltc("9.625in_40lb_P110_LTC",
        9.625, 40.00, "P-110", 9_880, 12_350, 15_440, 8.0, 0.50),
    "9.625in_47lb_P110_LTC": _ltc("9.625in_47lb_P110_LTC",
        9.625, 47.00, "P-110", 12_310, 15_390, 19_240, 8.0, 0.50),
    "9.625in_53.5lb_P110_LTC": _ltc("9.625in_53.5lb_P110_LTC",
        9.625, 53.50, "P-110", 14_760, 18_450, 23_060, 8.0, 0.50),

    # 10-3/4" casing
    "10.75in_40.5lb_J55_LTC": _ltc("10.75in_40.5lb_J55_LTC",
        10.75, 40.50, "J-55", 5_730, 7_160, 8_950, 9.0, 0.55),
    "10.75in_40.5lb_N80_LTC": _ltc("10.75in_40.5lb_N80_LTC",
        10.75, 40.50, "N-80", 6_780, 8_480, 10_600, 9.0, 0.55),
    "10.75in_51lb_P110_LTC": _ltc("10.75in_51lb_P110_LTC",
        10.75, 51.00, "P-110", 12_420, 15_530, 19_410, 9.0, 0.55),

    # 11-3/4" casing
    "11.75in_42lb_J55_LTC": _ltc("11.75in_42lb_J55_LTC",
        11.75, 42.00, "J-55", 5_680, 7_100, 8_880, 9.5, 0.60),
    "11.75in_47lb_N80_LTC": _ltc("11.75in_47lb_N80_LTC",
        11.75, 47.00, "N-80", 8_020, 10_030, 12_540, 9.5, 0.60),

    # 13-3/8" casing
    "13.375in_48lb_J55_LTC": _ltc("13.375in_48lb_J55_LTC",
        13.375, 48.00, "J-55", 6_010, 7_510, 9_390, 10.5, 0.70),
    "13.375in_54.5lb_N80_LTC": _ltc("13.375in_54.5lb_N80_LTC",
        13.375, 54.50, "N-80", 8_650, 10_810, 13_510, 10.5, 0.70),
    "13.375in_61lb_N80_LTC": _ltc("13.375in_61lb_N80_LTC",
        13.375, 61.00, "N-80", 10_030, 12_540, 15_680, 10.5, 0.70),
    "13.375in_68lb_P110_LTC": _ltc("13.375in_68lb_P110_LTC",
        13.375, 68.00, "P-110", 14_910, 18_640, 23_300, 10.5, 0.70),
    "13.375in_72lb_P110_LTC": _ltc("13.375in_72lb_P110_LTC",
        13.375, 72.00, "P-110", 16_480, 20_600, 25_750, 10.5, 0.70),

    # ═══════════════════════════════════════════════════════════════
    # API Buttress Thread (BTC) — position-controlled, steeper rise
    # ═══════════════════════════════════════════════════════════════

    "5.5in_23lb_P110_BTC": _btc("5.5in_23lb_P110_BTC",
        5.5, 23.00, "P-110", 5_400, 7_200, 9_000, 4.5, 0.04),
    "7in_26lb_N80_BTC": _btc("7in_26lb_N80_BTC",
        7.0, 26.00, "N-80", 6_380, 8_500, 10_630, 5.0, 0.045),
    "7.625in_33.7lb_N80_BTC": _btc("7.625in_33.7lb_N80_BTC",
        7.625, 33.70, "N-80", 7_650, 10_200, 12_750, 5.2, 0.05),
    "9.625in_36lb_N80_BTC": _btc("9.625in_36lb_N80_BTC",
        9.625, 36.00, "N-80", 9_150, 12_200, 15_250, 5.5, 0.05),
    "9.625in_47lb_P110_BTC": _btc("9.625in_47lb_P110_BTC",
        9.625, 47.00, "P-110", 12_380, 16_500, 20_630, 5.5, 0.05),
    "10.75in_45.5lb_N80_BTC": _btc("10.75in_45.5lb_N80_BTC",
        10.75, 45.50, "N-80", 10_880, 14_500, 18_130, 5.8, 0.055),
    "13.375in_54.5lb_K55_BTC": _btc("13.375in_54.5lb_K55_BTC",
        13.375, 54.50, "K-55", 12_600, 16_800, 21_000, 6.0, 0.06),
    "13.375in_68lb_N80_BTC": _btc("13.375in_68lb_N80_BTC",
        13.375, 68.00, "N-80", 15_750, 21_000, 26_250, 6.0, 0.06),

    # ═══════════════════════════════════════════════════════════════
    # Surface / Conductor Casing (STC)
    # ═══════════════════════════════════════════════════════════════

    "16in_75lb_K55_STC": _stc("16in_75lb_K55_STC",
        16.0, 75.00, "K-55", 13_500, 18_000, 22_500, 6.5, 0.07),
    "20in_94lb_K55_STC": _stc("20in_94lb_K55_STC",
        20.0, 94.00, "K-55", 16_500, 22_000, 27_500, 7.0, 0.08),
}


# ═══════════════════════════════════════════════════════════════════
# Premium Connection Catalog (Section 3.2)
# ═══════════════════════════════════════════════════════════════════

PREMIUM_CATALOG: Dict[str, PipeSpec] = {
    # VAM 21 — Vallourec (Section 3.2.2)
    "7in_29lb_P110_VAM21": PipeSpec(
        name="7in_29lb_P110_VAM21",
        od_inches=7.0, weight_per_foot=29.0, grade="P-110",
        connection_type="PREMIUM", connection_category=ConnectionCategory.PREMIUM_SHOULDERED,
        optimum_torque_ftlbs=18_500, min_torque_ftlbs=14_800,
        max_torque_ftlbs=22_200, thread_pitch_tpi=5, thread_taper_ipf=0.0625,
        turns_to_shoulder=4.0, delta_turns=0.8,
        seal_diameter_inches=6.1,
        shoulder_torque_min_ftlbs=3_700, shoulder_torque_max_ftlbs=7_400,
        yield_torque_ftlbs=28_000,
        seal_engagement_turn_fraction=0.7,
        power_tight_slope_ftlbs_per_turn=18_500,
    ),
    "9.625in_47lb_P110_VAM21": PipeSpec(
        name="9.625in_47lb_P110_VAM21",
        od_inches=9.625, weight_per_foot=47.0, grade="P-110",
        connection_type="PREMIUM", connection_category=ConnectionCategory.PREMIUM_SHOULDERED,
        optimum_torque_ftlbs=28_000, min_torque_ftlbs=22_400,
        max_torque_ftlbs=33_600, thread_pitch_tpi=5, thread_taper_ipf=0.0625,
        turns_to_shoulder=5.0, delta_turns=1.0,
        seal_diameter_inches=8.7,
        shoulder_torque_min_ftlbs=5_600, shoulder_torque_max_ftlbs=11_200,
        yield_torque_ftlbs=42_000,
        seal_engagement_turn_fraction=0.65,
        power_tight_slope_ftlbs_per_turn=22_400,
    ),
    "13.375in_68lb_P110_VAM21": PipeSpec(
        name="13.375in_68lb_P110_VAM21",
        od_inches=13.375, weight_per_foot=68.0, grade="P-110",
        connection_type="PREMIUM", connection_category=ConnectionCategory.PREMIUM_SHOULDERED,
        optimum_torque_ftlbs=45_000, min_torque_ftlbs=36_000,
        max_torque_ftlbs=54_000, thread_pitch_tpi=5, thread_taper_ipf=0.0625,
        turns_to_shoulder=6.0, delta_turns=1.2,
        seal_diameter_inches=12.2,
        shoulder_torque_min_ftlbs=9_000, shoulder_torque_max_ftlbs=18_000,
        yield_torque_ftlbs=67_500,
        seal_engagement_turn_fraction=0.6,
        power_tight_slope_ftlbs_per_turn=30_000,
    ),

    # TenarisHydril Wedge 563 (Section 3.2.2)
    "7in_29lb_P110_W563": PipeSpec(
        name="7in_29lb_P110_W563",
        od_inches=7.0, weight_per_foot=29.0, grade="P-110",
        connection_type="PREMIUM", connection_category=ConnectionCategory.PREMIUM_SHOULDERED,
        optimum_torque_ftlbs=20_000, min_torque_ftlbs=16_000,
        max_torque_ftlbs=24_000, thread_pitch_tpi=4, thread_taper_ipf=0.0625,
        turns_to_shoulder=3.5, delta_turns=0.6,
        seal_diameter_inches=6.0,
        shoulder_torque_min_ftlbs=4_000, shoulder_torque_max_ftlbs=8_000,
        yield_torque_ftlbs=30_000,
        seal_engagement_turn_fraction=0.75,
        power_tight_slope_ftlbs_per_turn=26_600,
    ),
    "9.625in_47lb_P110_W563": PipeSpec(
        name="9.625in_47lb_P110_W563",
        od_inches=9.625, weight_per_foot=47.0, grade="P-110",
        connection_type="PREMIUM", connection_category=ConnectionCategory.PREMIUM_SHOULDERED,
        optimum_torque_ftlbs=30_000, min_torque_ftlbs=24_000,
        max_torque_ftlbs=36_000, thread_pitch_tpi=4, thread_taper_ipf=0.0625,
        turns_to_shoulder=4.5, delta_turns=0.8,
        seal_diameter_inches=8.6,
        shoulder_torque_min_ftlbs=6_000, shoulder_torque_max_ftlbs=12_000,
        yield_torque_ftlbs=45_000,
        seal_engagement_turn_fraction=0.7,
        power_tight_slope_ftlbs_per_turn=30_000,
    ),

    # Hunting SEAL-LOK Apex
    "5.5in_23lb_P110_SealLok": PipeSpec(
        name="5.5in_23lb_P110_SealLok",
        od_inches=5.5, weight_per_foot=23.0, grade="P-110",
        connection_type="PREMIUM", connection_category=ConnectionCategory.PREMIUM_SHOULDERED,
        optimum_torque_ftlbs=15_000, min_torque_ftlbs=12_000,
        max_torque_ftlbs=18_000, thread_pitch_tpi=5, thread_taper_ipf=0.0625,
        turns_to_shoulder=4.0, delta_turns=0.7,
        seal_diameter_inches=4.6,
        shoulder_torque_min_ftlbs=3_000, shoulder_torque_max_ftlbs=6_000,
        yield_torque_ftlbs=22_500,
        seal_engagement_turn_fraction=0.65,
        power_tight_slope_ftlbs_per_turn=17_100,
    ),
}


# ═══════════════════════════════════════════════════════════════════
# Drill Pipe Connection Catalog (API 7-2 — Section 3.4)
# ═══════════════════════════════════════════════════════════════════

DRILL_PIPE_CATALOG: Dict[str, PipeSpec] = {
    "NC26_2.375DP": PipeSpec(
        name="NC26_2.375DP", od_inches=2.375, weight_per_foot=6.65,
        grade="S-135", connection_type="DRILL_PIPE",
        connection_category=ConnectionCategory.DRILL_PIPE,
        optimum_torque_ftlbs=7_000, min_torque_ftlbs=6_000,
        max_torque_ftlbs=8_000, thread_pitch_tpi=4,
        thread_taper_ipf=0.0833, turns_to_shoulder=3.0, delta_turns=0.5,
        seal_diameter_inches=2.0, pin_od_inches=3.375, box_od_inches=3.875,
        breakout_torque_ftlbs=9_000,
        shoulder_torque_min_ftlbs=2_100, shoulder_torque_max_ftlbs=3_500,
        compatible_machines=(MachineType.IRON_ROUGHNECK, MachineType.TOP_DRIVE),
    ),
    "NC31_2.875DP": PipeSpec(
        name="NC31_2.875DP", od_inches=2.875, weight_per_foot=10.40,
        grade="S-135", connection_type="DRILL_PIPE",
        connection_category=ConnectionCategory.DRILL_PIPE,
        optimum_torque_ftlbs=10_500, min_torque_ftlbs=9_000,
        max_torque_ftlbs=12_000, thread_pitch_tpi=4,
        thread_taper_ipf=0.0833, turns_to_shoulder=3.2, delta_turns=0.5,
        seal_diameter_inches=2.5, pin_od_inches=3.875, box_od_inches=4.625,
        breakout_torque_ftlbs=13_500,
        shoulder_torque_min_ftlbs=3_150, shoulder_torque_max_ftlbs=5_250,
        compatible_machines=(MachineType.IRON_ROUGHNECK, MachineType.TOP_DRIVE),
    ),
    "NC38_3.5DP": PipeSpec(
        name="NC38_3.5DP", od_inches=3.5, weight_per_foot=13.30,
        grade="S-135", connection_type="DRILL_PIPE",
        connection_category=ConnectionCategory.DRILL_PIPE,
        optimum_torque_ftlbs=17_500, min_torque_ftlbs=15_000,
        max_torque_ftlbs=20_000, thread_pitch_tpi=4,
        thread_taper_ipf=0.0833, turns_to_shoulder=3.5, delta_turns=0.6,
        seal_diameter_inches=3.0, pin_od_inches=4.625, box_od_inches=5.250,
        breakout_torque_ftlbs=22_500,
        shoulder_torque_min_ftlbs=5_250, shoulder_torque_max_ftlbs=8_750,
        compatible_machines=(MachineType.IRON_ROUGHNECK, MachineType.TOP_DRIVE),
    ),
    "NC40_4DP": PipeSpec(
        name="NC40_4DP", od_inches=4.0, weight_per_foot=14.00,
        grade="S-135", connection_type="DRILL_PIPE",
        connection_category=ConnectionCategory.DRILL_PIPE,
        optimum_torque_ftlbs=21_000, min_torque_ftlbs=18_000,
        max_torque_ftlbs=24_000, thread_pitch_tpi=4,
        thread_taper_ipf=0.0833, turns_to_shoulder=3.5, delta_turns=0.6,
        seal_diameter_inches=3.4, pin_od_inches=5.000, box_od_inches=5.500,
        breakout_torque_ftlbs=27_000,
        shoulder_torque_min_ftlbs=6_300, shoulder_torque_max_ftlbs=10_500,
        compatible_machines=(MachineType.IRON_ROUGHNECK, MachineType.TOP_DRIVE),
    ),
    "NC46_4DP_heavy": PipeSpec(
        name="NC46_4DP_heavy", od_inches=4.0, weight_per_foot=15.70,
        grade="S-135", connection_type="DRILL_PIPE",
        connection_category=ConnectionCategory.DRILL_PIPE,
        optimum_torque_ftlbs=25_000, min_torque_ftlbs=22_000,
        max_torque_ftlbs=28_000, thread_pitch_tpi=4,
        thread_taper_ipf=0.0833, turns_to_shoulder=3.8, delta_turns=0.6,
        seal_diameter_inches=3.8, pin_od_inches=5.500, box_od_inches=6.250,
        breakout_torque_ftlbs=31_500,
        shoulder_torque_min_ftlbs=7_500, shoulder_torque_max_ftlbs=12_500,
        compatible_machines=(MachineType.IRON_ROUGHNECK, MachineType.TOP_DRIVE),
    ),
    "NC50_4.5DP": PipeSpec(
        name="NC50_4.5DP", od_inches=4.5, weight_per_foot=16.60,
        grade="S-135", connection_type="DRILL_PIPE",
        connection_category=ConnectionCategory.DRILL_PIPE,
        optimum_torque_ftlbs=35_000, min_torque_ftlbs=30_000,
        max_torque_ftlbs=40_000, thread_pitch_tpi=4,
        thread_taper_ipf=0.0833, turns_to_shoulder=4.0, delta_turns=0.7,
        seal_diameter_inches=4.2, pin_od_inches=6.625, box_od_inches=7.000,
        breakout_torque_ftlbs=45_000,
        shoulder_torque_min_ftlbs=10_500, shoulder_torque_max_ftlbs=17_500,
        compatible_machines=(MachineType.IRON_ROUGHNECK, MachineType.TOP_DRIVE),
    ),
    "NC56_5DP": PipeSpec(
        name="NC56_5DP", od_inches=5.0, weight_per_foot=19.50,
        grade="S-135", connection_type="DRILL_PIPE",
        connection_category=ConnectionCategory.DRILL_PIPE,
        optimum_torque_ftlbs=47_500, min_torque_ftlbs=40_000,
        max_torque_ftlbs=55_000, thread_pitch_tpi=4,
        thread_taper_ipf=0.0833, turns_to_shoulder=4.2, delta_turns=0.7,
        seal_diameter_inches=4.8, pin_od_inches=7.250, box_od_inches=7.750,
        breakout_torque_ftlbs=62_500,
        shoulder_torque_min_ftlbs=14_250, shoulder_torque_max_ftlbs=23_750,
        compatible_machines=(MachineType.IRON_ROUGHNECK, MachineType.TOP_DRIVE),
    ),
    "6.625REG_5.5DP": PipeSpec(
        name="6.625REG_5.5DP", od_inches=5.5, weight_per_foot=21.90,
        grade="S-135", connection_type="DRILL_PIPE",
        connection_category=ConnectionCategory.DRILL_PIPE,
        optimum_torque_ftlbs=57_500, min_torque_ftlbs=50_000,
        max_torque_ftlbs=65_000, thread_pitch_tpi=4,
        thread_taper_ipf=0.0833, turns_to_shoulder=4.5, delta_turns=0.8,
        seal_diameter_inches=5.3, pin_od_inches=7.500, box_od_inches=8.500,
        breakout_torque_ftlbs=75_000,
        shoulder_torque_min_ftlbs=17_250, shoulder_torque_max_ftlbs=28_750,
        compatible_machines=(MachineType.IRON_ROUGHNECK, MachineType.TOP_DRIVE),
    ),
    "7.625REG_5.875DP": PipeSpec(
        name="7.625REG_5.875DP", od_inches=5.875, weight_per_foot=23.40,
        grade="S-135", connection_type="DRILL_PIPE",
        connection_category=ConnectionCategory.DRILL_PIPE,
        optimum_torque_ftlbs=70_000, min_torque_ftlbs=60_000,
        max_torque_ftlbs=80_000, thread_pitch_tpi=4,
        thread_taper_ipf=0.0833, turns_to_shoulder=4.8, delta_turns=0.8,
        seal_diameter_inches=5.7, pin_od_inches=8.750, box_od_inches=9.500,
        breakout_torque_ftlbs=90_000,
        shoulder_torque_min_ftlbs=21_000, shoulder_torque_max_ftlbs=35_000,
        compatible_machines=(MachineType.IRON_ROUGHNECK, MachineType.TOP_DRIVE),
    ),
}


# Merge all catalogs into a single lookup
ALL_CONNECTIONS: Dict[str, PipeSpec] = {}
ALL_CONNECTIONS.update(PIPE_CATALOG)
ALL_CONNECTIONS.update(PREMIUM_CATALOG)
ALL_CONNECTIONS.update(DRILL_PIPE_CATALOG)


# ═══════════════════════════════════════════════════════════════════
# Sensor Noise Profiles by Machine Type (Section 4.4)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SensorNoiseProfile:
    """Machine-type-specific noise characteristics."""
    torque_snr_db: float
    torque_noise_floor_pct_fs: float    # % of full scale
    rpm_resolution: float               # RPM
    rpm_snr_db: float
    turns_resolution: float             # turns
    pressure_snr_db: float
    temperature_snr_db: float
    hookload_snr_db: float
    emi_60hz_pct_fs: float              # 60Hz EMI as % full scale
    dominant_noise: str                  # Primary noise source description


MACHINE_NOISE_PROFILES: Dict[MachineType, SensorNoiseProfile] = {
    MachineType.TOP_DRIVE: SensorNoiseProfile(
        torque_snr_db=52.0,             # Motor current calc — worst SNR
        torque_noise_floor_pct_fs=0.5,
        rpm_resolution=0.05, rpm_snr_db=72.0,
        turns_resolution=0.002, pressure_snr_db=60.0,
        temperature_snr_db=50.0, hookload_snr_db=57.0,
        emi_60hz_pct_fs=0.5,
        dominant_noise="VFD harmonics, gear mesh, bearing vibration",
    ),
    MachineType.IRON_ROUGHNECK: SensorNoiseProfile(
        torque_snr_db=57.0,             # Pressure transducer
        torque_noise_floor_pct_fs=0.3,
        rpm_resolution=0.1, rpm_snr_db=68.0,
        turns_resolution=0.005, pressure_snr_db=62.0,
        temperature_snr_db=50.0, hookload_snr_db=0.0,  # No hookload
        emi_60hz_pct_fs=0.3,
        dominant_noise="Pump ripple, valve chatter, hydraulic 1/f",
    ),
    MachineType.POWER_TONG: SensorNoiseProfile(
        torque_snr_db=62.0,             # Load cell on tong arm
        torque_noise_floor_pct_fs=0.2,
        rpm_resolution=0.1, rpm_snr_db=65.0,
        turns_resolution=0.003, pressure_snr_db=60.0,
        temperature_snr_db=48.0, hookload_snr_db=0.0,
        emi_60hz_pct_fs=0.4,
        dominant_noise="Vibration, arm compliance, temp drift",
    ),
    MachineType.BUCKING_UNIT: SensorNoiseProfile(
        torque_snr_db=67.0,             # Calibrated load cell — best SNR
        torque_noise_floor_pct_fs=0.1,
        rpm_resolution=0.01, rpm_snr_db=78.0,
        turns_resolution=0.001, pressure_snr_db=65.0,
        temperature_snr_db=52.0, hookload_snr_db=0.0,
        emi_60hz_pct_fs=0.1,
        dominant_noise="Quantization, minimal vibration",
    ),
}


# ═══════════════════════════════════════════════════════════════════
# Simulation Configuration
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SimConfig:
    """Master configuration for the simulation.

    Accepts an optional MachineProfile for per-rig register layout
    and sensor calibration. If no profile is provided, falls back
    to the R6000 layout for unit testing / simulator mode.
    """

    # --- Machine Type ---
    machine_type: MachineType = MachineType.TOP_DRIVE

    # --- Machine Profile (per-rig config, optional) ---
    machine_profile: Optional[MachineProfile] = None

    # --- Timing (Section 5.3) ---
    physics_dt: float = 0.01                # Physics timestep (100 Hz)
    sensor_sample_dt: float = 0.01          # Sensor sampling rate (100 Hz)
    modbus_update_dt: float = 0.2           # PLC register update rate (5 Hz)
    dataset_output_rate_hz: float = 100.0   # CSV output rate (100 Hz per reference)
    total_time: float = 120.0               # Max simulation duration (seconds)

    # --- Sub-System Specifications ---
    top_drive: TopDriveSpec = field(default_factory=TopDriveSpec)
    iron_roughneck: IronRoughneckSpec = field(default_factory=IronRoughneckSpec)
    power_tong: PowerTongSpec = field(default_factory=PowerTongSpec)
    bucking_unit: BuckingUnitSpec = field(default_factory=BuckingUnitSpec)
    valve: ValveSpec = field(default_factory=ValveSpec)
    pump: PumpSpec = field(default_factory=PumpSpec)
    oil: OilSpec = field(default_factory=OilSpec)
    compound: ThreadCompoundSpec = field(default_factory=ThreadCompoundSpec)
    pipe_string: PipeStringSpec = field(default_factory=PipeStringSpec)

    # --- Hydraulic (for non-top-drive machines) ---
    motor_displacement_cc: float = 250.0
    motor_efficiency_mech: float = 0.90
    motor_efficiency_vol: float = 0.93
    max_pressure_psi: float = 5000.0
    operating_pressure_psi: float = 2500.0
    hydraulic_tau_ms: float = 120.0
    pressure_overshoot: float = 0.10
    oil_bulk_modulus_psi: float = 200_000

    # --- PID Controller ---
    pid_kp: float = 1.2
    pid_ki: float = 0.5
    pid_kd: float = 0.05
    pid_output_min: float = 0.0
    pid_output_max: float = 100.0
    pid_integral_clamp: float = 50.0
    pid_scan_rate_ms: float = 50.0
    pid_derivative_filter_tau: float = 0.1
    pid_deadband_pct: float = 1.0
    pid_valve_rate_limit_pct_s: float = 500.0
    pid_feedforward_gain: float = 0.3

    # --- Encoder ---
    encoder_cpr: int = 1174
    encoder_index_pulse: bool = True

    # --- Thermal Model (Section 4.3) ---
    ambient_temp_f: float = 75.0
    thermal_capacity_btu_f: float = 50.0
    heat_dissipation_btu_hr: float = 5000
    oil_heat_rate_btu_per_hp: float = 2545 / 3600
    temp_warning_f: float = 140.0
    temp_shutdown_f: float = 180.0
    manifold_thermal_capacity: float = 2.0
    motor_case_thermal_capacity: float = 15.0

    # --- Torque Calculation ---
    torque_cell_capacity_ftlbs: float = 50_000
    torque_cell_accuracy_pct: float = 0.25

    # --- Sensor Noise (defaults; overridden by machine-type profiles) ---
    pressure_snr_db: float = 60.0
    pressure_drift_pct_per_c: float = 0.02
    encoder_jitter_counts: int = 1
    temp_snr_db: float = 50.0
    temp_self_heat_f: float = 0.5
    torque_snr_db: float = 55.0
    torque_creep_pct: float = 0.02
    emi_60hz_amplitude: float = 0.005
    vfd_noise_amplitude: float = 0.003
    pink_noise_alpha: float = 1.0
    hookload_snr_db: float = 57.0

    # --- ADC / PLC ---
    adc_bits: int = 16
    adc_vref: float = 10.0

    # ─── GE CPE305 Register Map (Section 5.1.1 — CANONICAL) ─────
    # These addresses match the reference document exactly.
    # "Confirmed" = verified on Steve's PLC; "Estimated" = educated guess.
    reg_torque: int = 6000            # FLOAT32 — Torque (ft-lb)         [Confirmed]
    reg_rpm: int = 6002               # FLOAT32 — RPM                    [Confirmed]
    reg_pressure: int = 6004          # FLOAT32 — System pressure (PSI)  [Confirmed]
    reg_temperature: int = 6006       # FLOAT32 — Oil temperature (°F)   [Confirmed]
    reg_encoder_counts: int = 6008    # FLOAT32 — Encoder counts         [Confirmed]
    reg_pid_setpoint: int = 6010      # FLOAT32 — PID setpoint           [Estimated]
    reg_pid_error: int = 6012         # FLOAT32 — PID error              [Estimated]
    reg_pid_output: int = 6014        # INT16   — PID output (% * 100)   [Estimated]
    reg_mode: int = 6015              # INT16   — Operating mode (enum)  [Confirmed]
    reg_target_torque: int = 6016     # FLOAT32 — Target torque (ft-lb)  [Estimated]
    reg_turns_count: int = 6018       # FLOAT32 — Accumulated turns      [Estimated]
    reg_fault_code: int = 6020        # INT16   — Fault code (bitmask)   [Estimated]
    reg_state: int = 6021             # INT16   — Connection state       [Confirmed]
    reg_peak_torque: int = 6022       # FLOAT32 — Peak torque (ft-lb)    [Estimated]
    reg_hookload: int = 6024          # FLOAT32 — Hookload (klbs)        [Estimated]
    reg_shoulder_torque: int = 6026   # FLOAT32 — Shoulder torque        [Estimated]
    reg_slope: int = 6028             # FLOAT32 — Slope dT/dN            [Estimated]
    reg_connection_count: int = 6030  # INT16   — Connection count       [Estimated]

    # --- Domain Randomization Ranges (Section 6.1) ---
    rand_friction: Tuple[float, float] = (0.80, 1.35)       # Log-normal
    rand_shoulder_position: Tuple[float, float] = (0.95, 1.05)  # Normal
    rand_power_tight_slope: Tuple[float, float] = (0.85, 1.20)  # Normal
    rand_hydraulic_tau: Tuple[float, float] = (0.6, 1.5)    # Uniform (80-200ms)
    rand_motor_efficiency: Tuple[float, float] = (0.82, 0.95)  # Normal
    rand_pid_gains: Tuple[float, float] = (0.85, 1.15)      # Normal
    rand_ambient_temp: Tuple[float, float] = (-20.0, 120.0) # Uniform
    rand_noise_amp: Tuple[float, float] = (0.70, 1.50)      # Log-normal
    rand_emi_amplitude: Tuple[float, float] = (0.1, 2.0)    # Log-normal (% FS)
    rand_encoder_cpr: Tuple[int, int] = (1000, 2000)        # Discrete
    rand_pipe_tolerance: Tuple[float, float] = (0.97, 1.03)
    rand_rpm_setpoint: Tuple[float, float] = (8.0, 25.0)
    rand_valve_dead_zone: Tuple[float, float] = (1.0, 5.0)  # Wider range per ref
    rand_valve_hysteresis: Tuple[float, float] = (0.5, 4.0)
    rand_oil_viscosity: Tuple[float, float] = (0.6, 1.8)    # Wider per ref (compound viscosity)
    rand_string_length: Tuple[float, float] = (30.0, 120.0)
    rand_compound_kf: Tuple[float, float] = (0.80, 1.35)    # Match friction range
    rand_pump_efficiency: Tuple[float, float] = (0.88, 0.97)
    rand_backlash_deg: Tuple[float, float] = (0.05, 0.3)    # Gearbox backlash
    rand_adc_bits: Tuple[int, int] = (12, 16)               # ADC quantization
    rand_pipe_straightness: Tuple[float, float] = (0.0, 0.05)  # deg/ft

    def apply_machine_profile(self):
        """Apply MachineProfile settings to this SimConfig.

        Overrides physical parameters from the per-rig profile.
        Called automatically if machine_profile is set.
        """
        mp = self.machine_profile
        if mp is None:
            return
        self.motor_displacement_cc = mp.motor_displacement_cc
        self.max_pressure_psi = mp.max_pressure_psi
        self.encoder_cpr = mp.encoder_cpr
        self.torque_cell_capacity_ftlbs = mp.torque_cell_capacity_ftlbs
        # Apply register addresses from profile
        reg_map = mp.reg_map
        if 'torque' in reg_map:
            self.reg_torque = reg_map['torque'].address
        if 'rpm' in reg_map:
            self.reg_rpm = reg_map['rpm'].address
        if 'pressure' in reg_map:
            self.reg_pressure = reg_map['pressure'].address
        if 'temperature' in reg_map:
            self.reg_temperature = reg_map['temperature'].address
        if 'encoder_counts' in reg_map:
            self.reg_encoder_counts = reg_map['encoder_counts'].address
        if 'pid_setpoint' in reg_map:
            self.reg_pid_setpoint = reg_map['pid_setpoint'].address
        if 'pid_error' in reg_map:
            self.reg_pid_error = reg_map['pid_error'].address
        if 'pid_output' in reg_map:
            self.reg_pid_output = reg_map['pid_output'].address
        if 'operating_mode' in reg_map:
            self.reg_mode = reg_map['operating_mode'].address
        if 'target_torque' in reg_map:
            self.reg_target_torque = reg_map['target_torque'].address
        if 'turns' in reg_map:
            self.reg_turns_count = reg_map['turns'].address
        if 'fault_code' in reg_map:
            self.reg_fault_code = reg_map['fault_code'].address
        if 'connection_state' in reg_map:
            self.reg_state = reg_map['connection_state'].address
        if 'peak_torque' in reg_map:
            self.reg_peak_torque = reg_map['peak_torque'].address
        if 'hookload' in reg_map:
            self.reg_hookload = reg_map['hookload'].address
        if 'shoulder_torque' in reg_map:
            self.reg_shoulder_torque = reg_map['shoulder_torque'].address
        if 'slope_dT_dN' in reg_map:
            self.reg_slope = reg_map['slope_dT_dN'].address
        if 'connection_count' in reg_map:
            self.reg_connection_count = reg_map['connection_count'].address

    def apply_equipment_spec(self, eq_type: 'EquipmentType'):
        """Apply equipment-specific physics from EQUIPMENT_SPECS.

        Overrides motor and gearbox parameters so synthetic data
        matches the torque-speed characteristics of the actual
        equipment variant (HXI vs HXI_HT vs Warrior, etc.).
        """
        spec = EQUIPMENT_SPECS.get(eq_type)
        if spec is None:
            return
        # Motor
        self.top_drive.motor.rated_hp = spec.rated_hp
        self.top_drive.motor.rated_rpm = spec.rated_motor_rpm
        self.top_drive.motor.rated_torque_nm = (
            spec.rated_hp * 5252 / spec.rated_motor_rpm * 1.3558
        )
        self.top_drive.motor.rotor_inertia_kgm2 = spec.rotor_inertia_kgm2
        # Gearbox
        self.top_drive.gear_ratio = spec.gear_ratio
        self.top_drive.max_output_rpm = spec.max_rpm
        self.top_drive.max_continuous_torque_ftlbs = spec.max_torque_ftlbs
        self.top_drive.max_intermittent_torque_ftlbs = spec.max_intermittent_torque_ftlbs
        self.top_drive.gearbox_inertia_kgm2 = spec.gearbox_inertia_kgm2

    def apply_machine_noise_profile(self):
        """Override sensor noise defaults with machine-type-specific values."""
        profile = MACHINE_NOISE_PROFILES.get(self.machine_type)
        if profile:
            self.torque_snr_db = profile.torque_snr_db
            self.pressure_snr_db = profile.pressure_snr_db
            self.temp_snr_db = profile.temperature_snr_db
            self.emi_60hz_amplitude = profile.emi_60hz_pct_fs / 100.0
            self.hookload_snr_db = profile.hookload_snr_db

    def randomize(self, rng: Optional[np.random.Generator] = None) -> 'SimConfig':
        """Create a domain-randomized copy of this config (Section 6.1)."""
        rng = rng or np.random.default_rng()
        import copy
        cfg = copy.deepcopy(self)

        # --- Friction (log-normal distribution) ---
        friction_scale = np.exp(rng.normal(0, 0.12))  # ~log-normal around 1.0
        friction_scale = np.clip(friction_scale, *self.rand_friction)
        cfg.compound.base_kf *= friction_scale

        # --- Hydraulic ---
        cfg.motor_efficiency_mech = rng.uniform(*self.rand_motor_efficiency)
        cfg.hydraulic_tau_ms *= rng.uniform(*self.rand_hydraulic_tau)
        cfg.top_drive.viscous_damping_nms *= rng.uniform(0.8, 1.2)
        cfg.top_drive.gearbox_backlash_deg = rng.uniform(*self.rand_backlash_deg)

        # --- AC Motor (for top drive) ---
        cfg.top_drive.motor.efficiency = rng.uniform(0.90, 0.96)
        cfg.top_drive.motor.vfd_response_ms *= rng.uniform(0.7, 1.5)

        # --- Valve ---
        cfg.valve.dead_zone_pct = rng.uniform(*self.rand_valve_dead_zone)
        cfg.valve.hysteresis_pct = rng.uniform(*self.rand_valve_hysteresis)
        cfg.valve.spool_time_constant_ms *= rng.uniform(0.7, 1.4)

        # --- Pump ---
        pump_eff_scale = rng.uniform(*self.rand_pump_efficiency)
        cfg.pump.vol_efficiency_coeffs = tuple(
            min(e * pump_eff_scale / 0.92, 0.99)
            for e in self.pump.vol_efficiency_coeffs
        )

        # --- Oil (wider range per reference — compound viscosity 0.6-1.8x) ---
        visc_scale = rng.uniform(*self.rand_oil_viscosity)
        cfg.oil.kinematic_viscosity_40c_cst *= visc_scale
        cfg.oil.kinematic_viscosity_100c_cst *= visc_scale
        cfg.oil.air_content_pct = rng.uniform(0.5, 5.0)

        # --- Pipe string ---
        cfg.pipe_string.length_ft = rng.uniform(*self.rand_string_length)
        cfg.pipe_string.num_joints = int(rng.integers(1, 4))

        # --- PID ---
        cfg.pid_kp *= rng.uniform(*self.rand_pid_gains)
        cfg.pid_ki *= rng.uniform(*self.rand_pid_gains)
        cfg.pid_kd *= rng.uniform(*self.rand_pid_gains)
        cfg.pid_scan_rate_ms = rng.choice([10.0, 20.0, 50.0, 100.0])

        # --- Sensor noise (log-normal distribution) ---
        noise_scale = np.exp(rng.normal(0, 0.15))
        noise_scale = np.clip(noise_scale, *self.rand_noise_amp)
        cfg.pressure_snr_db += rng.uniform(-5, 5)
        cfg.torque_snr_db += rng.uniform(-5, 5)
        cfg.temp_snr_db += rng.uniform(-5, 5)
        cfg.ambient_temp_f = rng.uniform(*self.rand_ambient_temp)
        cfg.emi_60hz_amplitude *= noise_scale
        cfg.vfd_noise_amplitude *= noise_scale

        # --- Encoder (discrete randomization) ---
        cfg.encoder_cpr = int(rng.integers(*self.rand_encoder_cpr))

        # --- ADC (discrete randomization) ---
        cfg.adc_bits = int(rng.integers(*self.rand_adc_bits))

        # --- Apply machine-specific noise profile ---
        cfg.apply_machine_noise_profile()

        # --- Apply machine profile overrides (register map, physical params) ---
        cfg.apply_machine_profile()

        return cfg
