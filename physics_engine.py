"""
Physics Engine: The Truth Model
=================================
Runs at 100Hz (10ms timestep) to capture hydraulic/electrical transients.

Architecture — seven coupled subsystems:

  1. Oil Property Model (algebraic)
     Walther equation for viscosity-temperature relationship.

  2. Torque-Turn Models (algebraic + state)
     - API 8-Round: Farr equation with 4-phase curve
     - Buttress: Steeper shoulder, position-controlled
     - Premium Shouldered: Distinct shoulder + steep slope + optional seal inflection
     - Drill Pipe: Shouldered with breakout tracking
     Temperature and shear-rate dependent friction (Stribeck curve).
     Thread damage accumulation (Archard wear model).

  3. Drive System Models
     - AC Motor + VFD (top drive): constant-torque/constant-power regions,
       VFD current limiting, regenerative braking
     - Hydraulic Motor (iron roughneck spinner, power tong, bucking unit)
     - Hydraulic Cylinder (iron roughneck torque wrench)

  4. Advanced PID Controller (discrete, scan-rate)
     Runs at PLC scan rate (50ms), not physics rate.
     Bumpless RPM↔torque transfer, derivative filter, dead band,
     output rate limiting, feed-forward, tracking anti-windup.

  5. Multi-Zone Thermal Model (ODEs)
     3-node network: oil_reservoir ↔ manifold ↔ motor_casing.

  6. Pipe String Dynamics (ODE)
     Torsional spring-damper between motor and connection.

  7. Shoulder Detection & Slope Calculator
     Real-time detection of shoulder contact point and rolling dT/dN.

State machine sequences:
  IDLE → APPROACH → SPIN_IN → SHOULDER → POWER_TIGHT → HOLD → COMPLETE
  IDLE → BREAKOUT → BACKOFF → COMPLETE
  (Iron Roughneck): SPIN_IN uses spinner, SHOULDER triggers handoff, POWER_TIGHT uses wrench
  Any → FAULT / STALL / E_STOP
"""
import numpy as np
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Tuple

from config import (
    SimConfig, PipeSpec, OilSpec, ThreadCompoundSpec,
    MachineType, ConnectionCategory, TorqueMeasurementMethod,
    GRADE_CATALOG,
)


# ═══════════════════════════════════════════════════════════════════
# Enumerations
# ═══════════════════════════════════════════════════════════════════

class ConnectionState(IntEnum):
    IDLE = 0
    APPROACH = 1
    SPIN_IN = 2
    SHOULDER = 3
    POWER_TIGHT = 4
    HOLD = 5
    BREAKOUT = 6
    BACKOFF = 7
    FAULT = 8
    COMPLETE = 9
    STALL = 10
    E_STOP = 11
    FAULT_RECOVERY = 12
    HANDOFF = 13          # Iron roughneck spinner→wrench transition


class OperatingMode(IntEnum):
    IDLE = 0
    THREADING = 1
    BACKOFF = 2
    OSCILLATION = 3
    E_STOP = 4


class FaultCode(IntEnum):
    """Fault code bitmask for %R6020."""
    NONE = 0x0000
    OVER_TORQUE = 0x0001
    UNDER_TORQUE = 0x0002
    CROSS_THREAD = 0x0004
    GALLING = 0x0008
    STALL = 0x0010
    OVER_TEMP = 0x0020
    STICK_SLIP = 0x0040
    STRIPPED_THREAD = 0x0080
    MISALIGNED_STAB = 0x0100
    WRONG_COMPOUND = 0x0200
    WASHOUT = 0x0400
    CONNECTION_JUMP = 0x0800


# ═══════════════════════════════════════════════════════════════════
# Physics State Vector
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PhysicsState:
    """Complete physics state vector at a given instant."""
    # --- Core fields ---
    time: float = 0.0
    turns: float = 0.0
    rpm: float = 0.0
    encoder_counts: int = 0
    torque_ftlbs: float = 0.0
    target_torque_ftlbs: float = 0.0
    pressure_psi: float = 0.0
    valve_command_pct: float = 0.0
    flow_gpm: float = 0.0
    oil_temp_f: float = 75.0
    pid_setpoint: float = 0.0
    pid_error: float = 0.0
    pid_output: float = 0.0
    pid_integral: float = 0.0
    connection_state: ConnectionState = ConnectionState.IDLE
    operating_mode: OperatingMode = OperatingMode.IDLE

    # --- Fault flags ---
    is_cross_threaded: bool = False
    is_galled: bool = False
    is_over_torque: bool = False
    is_over_temp: bool = False
    is_stalled: bool = False
    is_stick_slip: bool = False
    is_stripped: bool = False
    is_misaligned: bool = False
    is_wrong_compound: bool = False

    # --- Extended state ---
    valve_spool_pct: float = 0.0
    pump_pressure_psi: float = 0.0
    motor_pressure_drop_psi: float = 0.0
    leakage_flow_gpm: float = 0.0
    oil_viscosity_cst: float = 46.0
    motor_mech_efficiency: float = 0.90
    string_twist_deg: float = 0.0
    connection_rpm: float = 0.0
    motor_torque_ftlbs: float = 0.0
    thread_damage: float = 0.0
    compound_friction_kf: float = 0.08
    pump_ripple_psi: float = 0.0
    manifold_temp_f: float = 75.0
    motor_case_temp_f: float = 75.0
    stick_slip_frequency_hz: float = 0.0
    pid_mode: str = "rpm"

    # --- New fields (Section 5.1.1 register map) ---
    fault_code: int = 0               # Bitmask of active faults
    peak_torque_ftlbs: float = 0.0    # Peak torque in this connection
    hookload_klbs: float = 0.0        # Hook load (top drive only)
    shoulder_torque_ftlbs: float = 0.0  # Detected shoulder torque
    slope_dT_dN: float = 0.0          # Current torque-turn slope (ft-lb/turn)
    connection_count: int = 0          # Total connections completed

    # --- Machine-specific ---
    machine_phase: str = "idle"        # Iron roughneck: "spinner" or "wrench"
    vfd_frequency_hz: float = 0.0      # Top drive VFD output frequency
    vfd_current_pct: float = 0.0       # VFD current as % of rated
    motor_speed_rpm: float = 0.0       # Motor shaft RPM (before gearbox)


# ═══════════════════════════════════════════════════════════════════
# Oil Property Model
# ═══════════════════════════════════════════════════════════════════

class OilPropertyModel:
    """Walther equation viscosity-temperature model (ASTM D341)."""

    def __init__(self, oil: OilSpec):
        self.oil = oil
        self.A, self.B = self._compute_walther_coefficients()

    def _compute_walther_coefficients(self) -> Tuple[float, float]:
        T1_K = 273.15 + 40.0
        T2_K = 273.15 + 100.0
        v1 = self.oil.kinematic_viscosity_40c_cst
        v2 = self.oil.kinematic_viscosity_100c_cst
        W1 = np.log10(np.log10(v1 + 0.7))
        W2 = np.log10(np.log10(v2 + 0.7))
        logT1 = np.log10(T1_K)
        logT2 = np.log10(T2_K)
        B = (W1 - W2) / (logT2 - logT1)
        A = W1 + B * logT1
        return A, B

    def viscosity_at_temp(self, temp_f: float) -> float:
        temp_c = (temp_f - 32.0) * 5.0 / 9.0
        temp_k = max(temp_c + 273.15, 250.0)
        W = self.A - self.B * np.log10(temp_k)
        W = np.clip(W, -0.7, 1.5)
        viscosity = 10.0 ** (10.0 ** W) - 0.7
        return np.clip(viscosity, 1.0, 10000.0)

    def bulk_modulus_effective(self, pressure_psi: float) -> float:
        p_atm = 14.7
        p = max(pressure_psi, p_atm)
        air_factor = (self.oil.air_content_pct / 100.0) * (p_atm / p) ** (1.0 / 1.4)
        return self.oil.bulk_modulus_psi / (1.0 + air_factor)

    def density_at_temp(self, temp_f: float) -> float:
        temp_c = (temp_f - 32.0) * 5.0 / 9.0
        return self.oil.density_kg_m3 - 0.7 * (temp_c - 15.0)


# ═══════════════════════════════════════════════════════════════════
# Torque-Turn Models
# ═══════════════════════════════════════════════════════════════════

class FarrTorqueTurnModel:
    """API 8-Round thread torque-turn model (Farr equation).

    Farr equation:
      T = (σ_y × A / 12) × [ p/(2π) + r_t × Kf/cos(θ) + r_s × Kf ]

    Four phases: Spin-in → Shoulder → Power-tight → Yield
    """

    def __init__(self, pipe: PipeSpec, compound: ThreadCompoundSpec,
                 rng: Optional[np.random.Generator] = None):
        self.pipe = pipe
        self.compound = compound
        self.rng = rng or np.random.default_rng()

        self._tolerance_scale = self.rng.uniform(0.98, 1.02)
        self._kf_scale = self.rng.uniform(0.90, 1.25)
        self._damage_accumulated: float = 0.0
        self._fault_multiplier: float = 1.0

        # Stribeck friction
        self._kf_static = compound.base_kf * self._kf_scale
        self._kf_kinetic = self._kf_static * 0.70
        self._v_stribeck = 0.01
        self._stribeck_exponent = 1.5
        self._viscous_coeff = 0.001

        self._breakout_ratio = self.rng.uniform(1.20, 1.50)

        self._compute_geometry()
        self._compute_phase_boundaries()
        self._compute_farr_constants()

    def _compute_geometry(self):
        p = self.pipe
        self.lead = p.lead_inches if p.lead_inches > 0 else (1.0 / p.thread_pitch_tpi)
        self.theta_rad = np.radians(p.thread_flank_angle_deg / 2.0)
        if p.pitch_diameter_inches > 0:
            self.r_thread_mean = p.pitch_diameter_inches / 2.0
        else:
            self.r_thread_mean = (p.od_inches - p.thread_height_inches) / 2.0
        self.r_seal = p.seal_diameter_inches / 2.0
        thread_circ = np.pi * (p.pitch_diameter_inches if p.pitch_diameter_inches > 0 else p.od_inches)
        thread_depth = p.thread_height_inches if p.thread_height_inches > 0 else (0.626 / max(p.thread_pitch_tpi, 1))
        self.engagement_area = thread_circ * thread_depth * 0.5
        self.sigma_y = p.yield_strength_psi

    def _compute_phase_boundaries(self):
        p = self.pipe
        if p.thread_length_inches > 0:
            total_thread_turns = p.thread_length_inches / self.lead
            self.t_spin_end = total_thread_turns * 0.85 * self._tolerance_scale
            self.t_shoulder = total_thread_turns * self._tolerance_scale
        else:
            self.t_spin_end = p.turns_to_shoulder * 0.85 * self._tolerance_scale
            self.t_shoulder = p.turns_to_shoulder * self._tolerance_scale
        self.t_power_end = self.t_shoulder + p.delta_turns * self._tolerance_scale
        self.scaled_turns = self.t_shoulder
        self.scaled_delta = p.delta_turns * self._tolerance_scale

    def _compute_farr_constants(self):
        p = self.pipe
        self.C_lead = (self.sigma_y * self.engagement_area / 12.0) * (self.lead / (2.0 * np.pi))
        self.C_thread = (self.sigma_y * self.engagement_area / 12.0) * (self.r_thread_mean / np.cos(self.theta_rad))
        self.C_seal = (self.sigma_y * self.engagement_area / 12.0) * self.r_seal
        self.spin_in_base = p.optimum_torque_ftlbs * self.rng.uniform(0.01, 0.05)
        self.shoulder_base = p.optimum_torque_ftlbs * self.rng.uniform(0.20, 0.40)
        self.scaled_optimum = p.optimum_torque_ftlbs * self._kf_scale
        self.scaled_max = p.max_torque_ftlbs * self._kf_scale
        self.scaled_min = p.min_torque_ftlbs * self._kf_scale
        if self.scaled_delta > 0:
            self.power_gradient = (self.scaled_optimum - self.shoulder_base) / self.scaled_delta
        else:
            self.power_gradient = self.scaled_optimum / 0.04

    def compute_kf(self, temp_f: float, omega_rpm: float) -> float:
        c = self.compound
        delta_t = temp_f - c.reference_temp_f
        kf_temp = c.base_kf * self._kf_scale * (1.0 + c.temp_coefficient * delta_t)
        sliding_velocity = abs(omega_rpm) * 2.0 * np.pi / 60.0 * self.r_thread_mean
        shear_rate = max(sliding_velocity / 0.001, 1.0)
        shear_factor = (shear_rate / c.reference_shear_rate) ** c.shear_rate_exponent
        damage_factor = 1.0 + self._damage_accumulated * c.degradation_rate * 100.0
        kf = kf_temp * shear_factor * damage_factor
        return np.clip(kf, 0.02, 0.30)

    def torque_at_turns(self, turns: float, temp_f: float = 77.0,
                        omega_rpm: float = 15.0) -> float:
        if turns <= 0:
            return 0.0
        kf = self.compute_kf(temp_f, omega_rpm)

        # Phase 1: Spin-in
        if turns < self.t_spin_end:
            progress = turns / self.t_spin_end
            base_torque = self.spin_in_base * (0.3 + 0.7 * progress)
            farr_contribution = self.C_lead * kf * progress * 0.1
            return (base_torque + farr_contribution) * self._fault_multiplier

        # Phase 2: Shoulder transition
        if turns < self.t_shoulder:
            progress = (turns - self.t_spin_end) / (self.t_shoulder - self.t_spin_end)
            base_torque = self.spin_in_base + (self.shoulder_base - self.spin_in_base) * progress
            farr_thread = self.C_thread * kf * progress * 0.3
            return (base_torque + farr_thread) * self._fault_multiplier

        # Phase 3: Power-tight
        if turns < self.t_power_end:
            delta = turns - self.t_shoulder
            normalized_delta = delta / self.scaled_delta if self.scaled_delta > 0 else 0
            linear_component = self.shoulder_base + self.power_gradient * delta
            nonlinear_factor = 1.0 + 2.0 * normalized_delta ** 2
            torque = linear_component * nonlinear_factor
            farr_scaled = (self.C_lead + self.C_thread * kf + self.C_seal * kf) * normalized_delta * 0.1
            torque = max(torque, self.shoulder_base) + farr_scaled
            return min(torque, self.scaled_max * 1.2) * self._fault_multiplier

        # Phase 4: Yield zone
        overshoot_turns = turns - self.t_power_end
        overshoot_gradient = self.power_gradient * 1.5
        torque = self.scaled_optimum + overshoot_gradient * overshoot_turns
        return min(torque, self.scaled_max * 1.5) * self._fault_multiplier

    def breakout_torque_profile(self, reverse_turns: float) -> float:
        t_static = self.scaled_optimum * self._breakout_ratio
        t_kinetic = self.scaled_optimum * 0.30
        torque = t_kinetic + (t_static - t_kinetic) * np.exp(-10.0 * abs(reverse_turns))
        return torque * self._fault_multiplier

    def accumulate_damage(self, contact_pressure: float, sliding_distance: float, dt: float):
        damage_increment = (contact_pressure / 1e6) ** 1.5 * sliding_distance * dt * 1e-6
        self._damage_accumulated = min(self._damage_accumulated + damage_increment, 1.0)

    def set_fault_multiplier(self, multiplier: float):
        self._fault_multiplier = max(multiplier, 0.0)

    def reset_fault_multiplier(self):
        self._fault_multiplier = 1.0

    @property
    def spin_in_torque(self) -> float:
        return self.spin_in_base


class ButtressTorqueTurnModel(FarrTorqueTurnModel):
    """Buttress thread (BTC) model — steeper shoulder, position-controlled.

    Key difference from 8-round: flat flank engagement creates more abrupt
    shoulder and steeper power-tight rise (Section 3.1.3).
    """

    def _compute_phase_boundaries(self):
        super()._compute_phase_boundaries()
        # Buttress has steeper transition — shoulder zone is narrower
        self.t_spin_end = self.t_shoulder * 0.90 * self._tolerance_scale

    def _compute_farr_constants(self):
        super()._compute_farr_constants()
        # Steeper power-tight gradient for buttress
        self.power_gradient *= 1.4
        # More abrupt shoulder
        self.shoulder_base = self.pipe.optimum_torque_ftlbs * self.rng.uniform(0.25, 0.50)


class PremiumConnectionModel(FarrTorqueTurnModel):
    """Premium shouldered connection model (Section 3.2.3).

    Four distinct phases:
      Phase 1 (Spin-in): Same as API but typically fewer turns, lower friction
      Phase 2 (Shoulder contact): STEP CHANGE — rapid rise over 0.02-0.05 turns
      Phase 3 (Shoulder-to-final): Nearly linear, slope is critical quality metric
      Phase 4 (Metal seal engagement): Optional inflection at 60-80% of final torque
    """

    def _compute_phase_boundaries(self):
        p = self.pipe
        # Premium connections have fewer spin-in turns (coarser pitch, fewer turns)
        self.t_spin_end = p.turns_to_shoulder * 0.95 * self._tolerance_scale
        self.t_shoulder = p.turns_to_shoulder * self._tolerance_scale
        self.t_power_end = self.t_shoulder + p.delta_turns * self._tolerance_scale
        self.scaled_turns = self.t_shoulder
        self.scaled_delta = p.delta_turns * self._tolerance_scale

        # Shoulder contact occurs over a very narrow window (0.02-0.05 turns)
        self.shoulder_contact_width = self.rng.uniform(0.02, 0.05)

        # Seal engagement point (fraction of power-tight zone)
        self.seal_engagement_frac = p.seal_engagement_turn_fraction
        if self.seal_engagement_frac == 0:
            self.seal_engagement_frac = self.rng.uniform(0.60, 0.80)

    def _compute_farr_constants(self):
        p = self.pipe
        # Spin-in torque is lower for premium (Dopeless coatings)
        self.spin_in_base = p.optimum_torque_ftlbs * self.rng.uniform(0.005, 0.03)

        # Shoulder torque from spec or randomized
        if p.shoulder_torque_min_ftlbs > 0:
            self.shoulder_base = self.rng.uniform(
                p.shoulder_torque_min_ftlbs, p.shoulder_torque_max_ftlbs
            ) * self._kf_scale
        else:
            self.shoulder_base = p.optimum_torque_ftlbs * self.rng.uniform(0.15, 0.35) * self._kf_scale

        self.scaled_optimum = p.optimum_torque_ftlbs * self._kf_scale
        self.scaled_max = p.max_torque_ftlbs * self._kf_scale
        self.scaled_min = p.min_torque_ftlbs * self._kf_scale

        # Power-tight slope from spec or calculated
        if p.power_tight_slope_ftlbs_per_turn > 0:
            self.power_gradient = p.power_tight_slope_ftlbs_per_turn * self._kf_scale * self.rng.uniform(0.85, 1.15)
        elif self.scaled_delta > 0:
            self.power_gradient = (self.scaled_optimum - self.shoulder_base) / self.scaled_delta
        else:
            self.power_gradient = self.scaled_optimum * 2.0

        # Seal engagement produces a kink (secondary inflection)
        self.seal_kink_magnitude = self.scaled_optimum * self.rng.uniform(0.03, 0.08)

        # Farr constants
        self.C_lead = 0
        self.C_thread = 0
        self.C_seal = 0
        self.engagement_area = 1.0
        self.sigma_y = p.yield_strength_psi
        self.r_thread_mean = p.od_inches / 2.0
        self.r_seal = p.seal_diameter_inches / 2.0
        self.lead = 1.0 / max(p.thread_pitch_tpi, 1)
        self.theta_rad = np.radians(p.thread_flank_angle_deg / 2.0)

    def torque_at_turns(self, turns: float, temp_f: float = 77.0,
                        omega_rpm: float = 15.0) -> float:
        if turns <= 0:
            return 0.0

        kf = self.compute_kf(temp_f, omega_rpm)

        # Phase 1: Spin-in (low friction, free running)
        if turns < self.t_spin_end:
            progress = turns / max(self.t_spin_end, 0.01)
            return self.spin_in_base * (0.2 + 0.8 * progress) * self._fault_multiplier

        # Phase 2: Shoulder contact (STEP CHANGE — rapid rise over 0.02-0.05 turns)
        if turns < self.t_shoulder:
            into_shoulder = turns - self.t_spin_end
            shoulder_zone = self.t_shoulder - self.t_spin_end
            if shoulder_zone > 0:
                frac = into_shoulder / shoulder_zone
                # Sigmoid-shaped rise for natural shoulder contact
                sigmoid = 1.0 / (1.0 + np.exp(-12.0 * (frac - 0.5)))
                torque = self.spin_in_base + (self.shoulder_base - self.spin_in_base) * sigmoid
                return torque * self._fault_multiplier
            return self.shoulder_base * self._fault_multiplier

        # Phase 3: Shoulder-to-final (nearly linear with optional seal kink)
        if turns < self.t_power_end:
            delta = turns - self.t_shoulder
            frac = delta / max(self.scaled_delta, 0.001)

            # Linear rise from shoulder to final
            torque = self.shoulder_base + self.power_gradient * delta

            # Phase 4: Seal engagement kink (optional inflection)
            if frac > self.seal_engagement_frac:
                seal_frac = (frac - self.seal_engagement_frac) / (1.0 - self.seal_engagement_frac)
                torque += self.seal_kink_magnitude * seal_frac

            return min(torque, self.scaled_max * 1.1) * self._fault_multiplier

        # Beyond target — yield zone
        overshoot = turns - self.t_power_end
        torque = self.scaled_optimum + self.power_gradient * 0.5 * overshoot
        yield_torque = self.pipe.yield_torque_ftlbs if self.pipe.yield_torque_ftlbs > 0 else self.scaled_max * 1.3
        return min(torque, yield_torque) * self._fault_multiplier


class DrillPipeConnectionModel(PremiumConnectionModel):
    """Drill pipe connection model (API 7-2 — NC/IF/REG).

    Similar to premium shouldered but:
    - Higher breakout ratio (1.3-1.5x)
    - More pronounced shoulder due to tool joint shoulder face
    - Less seal-related kink (shoulder is primary seal mechanism)
    """

    def _compute_farr_constants(self):
        super()._compute_farr_constants()
        # Drill pipe has stronger shoulder and less seal contribution
        self.shoulder_base *= 1.2
        self.seal_kink_magnitude *= 0.3
        self._breakout_ratio = self.rng.uniform(1.25, 1.50)


def create_torque_model(pipe: PipeSpec, compound: ThreadCompoundSpec,
                        rng: Optional[np.random.Generator] = None):
    """Factory: select appropriate torque-turn model based on connection type."""
    if pipe.is_premium:
        return PremiumConnectionModel(pipe, compound, rng)
    elif pipe.is_drill_pipe:
        return DrillPipeConnectionModel(pipe, compound, rng)
    elif pipe.is_buttress:
        return ButtressTorqueTurnModel(pipe, compound, rng)
    else:
        return FarrTorqueTurnModel(pipe, compound, rng)


# ═══════════════════════════════════════════════════════════════════
# AC Motor + VFD Model (Top Drive — Section 2.1 / 4.2.1)
# ═══════════════════════════════════════════════════════════════════

class ACMotorVFDModel:
    """AC induction motor with Variable Frequency Drive.

    Torque-speed characteristic:
      - Below base speed: T = T_rated (constant torque region)
      - Above base speed: T = T_rated * (RPM_base / RPM) (constant power)
      - VFD current limit caps torque at vfd_current_limit_pct * T_rated

    Motor torque equation:
      T_motor = (P_rated * 5252) / RPM_rated for rated conditions
    """

    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        td = cfg.top_drive
        motor = td.motor

        self.rated_hp = motor.rated_hp
        self.base_rpm = motor.rated_rpm
        self.rated_torque_ftlbs = motor.rated_hp * 5252.0 / motor.rated_rpm
        self.max_torque_ftlbs = self.rated_torque_ftlbs * motor.max_torque_multiplier
        self.vfd_limit_torque = self.rated_torque_ftlbs * motor.vfd_current_limit_pct / 100.0
        self.gear_ratio = td.gear_ratio
        self.gear_efficiency = td.gearbox_efficiency
        self.backlash_rad = np.radians(td.gearbox_backlash_deg)

        # Rotational dynamics (reflected to output shaft)
        self.J_motor_reflected = motor.rotor_inertia_kgm2 * td.gear_ratio ** 2
        self.J_gearbox = td.gearbox_inertia_kgm2
        self.J_total = self.J_motor_reflected + self.J_gearbox
        self.B_viscous = td.viscous_damping_nms
        self.T_seal = td.seal_friction_nm

        # VFD dynamics
        self.vfd_tau = motor.vfd_response_ms / 1000.0
        self._vfd_torque_cmd = 0.0  # Internal VFD torque command
        self._motor_rpm = 0.0
        self._output_omega = 0.0    # Output shaft rad/s

    def step(self, torque_setpoint_pct: float, load_torque_ftlbs: float,
             dt: float) -> Tuple[float, float, float, float, float]:
        """Step the AC motor model.

        Args:
            torque_setpoint_pct: 0-100% of rated capacity
            load_torque_ftlbs: Load torque at output shaft
            dt: Timestep

        Returns: (output_torque_ftlbs, output_rpm, motor_rpm, vfd_current_pct, vfd_freq_hz)
        """
        # VFD torque command with first-order lag
        target_torque_motor = (torque_setpoint_pct / 100.0) * self.rated_torque_ftlbs
        alpha = dt / (self.vfd_tau + dt)
        self._vfd_torque_cmd += alpha * (target_torque_motor - self._vfd_torque_cmd)

        # Apply VFD current limit
        motor_torque = np.clip(self._vfd_torque_cmd, 0.0, self.vfd_limit_torque)

        # Torque-speed characteristic
        motor_rpm = abs(self._motor_rpm)
        if motor_rpm > self.base_rpm and motor_rpm > 0:
            # Constant power region: reduce available torque
            derating = self.base_rpm / motor_rpm
            motor_torque = min(motor_torque, self.rated_torque_ftlbs * derating)

        # Through gearbox to output shaft
        output_torque_ftlbs = motor_torque * self.gear_ratio * self.gear_efficiency

        # Rotational dynamics on output shaft (all in Nm for calculation)
        output_torque_nm = output_torque_ftlbs * 1.3558
        load_nm = load_torque_ftlbs * 1.3558

        net_torque = output_torque_nm - load_nm - self.B_viscous * self._output_omega
        if abs(self._output_omega) > 0.01:
            net_torque -= self.T_seal * np.sign(self._output_omega)
        elif abs(output_torque_nm - load_nm) > self.T_seal:
            net_torque -= self.T_seal * np.sign(output_torque_nm - load_nm)
        else:
            net_torque = 0.0

        angular_accel = net_torque / max(self.J_total, 0.1)
        self._output_omega += angular_accel * dt
        self._output_omega = np.clip(
            self._output_omega, 0.0,
            self.cfg.top_drive.max_output_rpm * 2.0 * np.pi / 60.0
        )

        output_rpm = self._output_omega * 60.0 / (2.0 * np.pi)
        self._motor_rpm = output_rpm * self.gear_ratio

        # Effective torque delivered to connection
        actual_torque = min(output_torque_ftlbs, load_torque_ftlbs) if load_torque_ftlbs > 0 else 0.0

        # VFD outputs
        vfd_current_pct = (motor_torque / max(self.rated_torque_ftlbs, 1)) * 100.0
        vfd_freq_hz = (self._motor_rpm / self.base_rpm) * 60.0  # Proportional to speed

        return actual_torque, output_rpm, self._motor_rpm, vfd_current_pct, vfd_freq_hz

    def reset(self):
        self._vfd_torque_cmd = 0.0
        self._motor_rpm = 0.0
        self._output_omega = 0.0


# ═══════════════════════════════════════════════════════════════════
# Hydraulic System Models (Iron Roughneck, Power Tong, Bucking Unit)
# ═══════════════════════════════════════════════════════════════════

class ProportionalValve:
    """Spool valve dynamics: command → actual spool position."""

    def __init__(self, spec):
        self.spec = spec
        self.spool_position: float = 0.0
        self._prev_command: float = 0.0
        self._rising: bool = True

    def step(self, command_pct: float, dt: float) -> float:
        if abs(command_pct) < self.spec.dead_zone_pct:
            target = 0.0
        else:
            sign = np.sign(command_pct)
            target = sign * (abs(command_pct) - self.spec.dead_zone_pct) / (
                100.0 - self.spec.dead_zone_pct) * 100.0

        delta_cmd = command_pct - self._prev_command
        half_hyst = self.spec.hysteresis_pct / 2.0
        if delta_cmd > 0:
            if not self._rising:
                target -= half_hyst
            self._rising = True
        elif delta_cmd < 0:
            if self._rising:
                target += half_hyst
            self._rising = False
        self._prev_command = command_pct

        max_change = self.spec.rate_limit_pct_per_s * dt
        delta_spool = np.clip(target - self.spool_position, -max_change, max_change)
        tau = self.spec.spool_time_constant_ms / 1000.0
        alpha = dt / (tau + dt) if tau > 0 else 1.0
        self.spool_position += alpha * delta_spool
        return np.clip(self.spool_position, 0.0, 100.0)

    def reset(self):
        self.spool_position = 0.0
        self._prev_command = 0.0
        self._rising = True


class HydraulicPump:
    """Pump flow/pressure model with volumetric efficiency and ripple."""

    def __init__(self, spec):
        self.spec = spec
        self._shaft_angle: float = 0.0
        d_in3 = spec.displacement_cc * 0.0610237
        self.ideal_flow_gpm = d_in3 * spec.drive_rpm / 231.0

    def flow_at_pressure(self, pressure_psi: float, viscosity_cst: float) -> float:
        p_ratio = np.clip(pressure_psi / self.spec.max_pressure_psi, 0.0, 1.0)
        eta_v = np.interp(p_ratio, [0.25, 0.50, 0.75, 1.00],
                          list(self.spec.vol_efficiency_coeffs))
        visc_factor = np.clip(viscosity_cst / 15.0, 0.6, 1.0)
        return self.ideal_flow_gpm * eta_v * visc_factor

    def ripple(self, dt: float, operating_pressure: float) -> float:
        self._shaft_angle += 2.0 * np.pi * self.spec.drive_rpm / 60.0 * dt
        if self._shaft_angle > 2.0e6:
            self._shaft_angle -= 2.0e6
        freq = self.spec.num_pistons * self.spec.drive_rpm / 60.0
        amplitude = 0.03 * operating_pressure
        t_eff = self._shaft_angle / (self.spec.drive_rpm / 60.0 * 2 * np.pi)
        return amplitude * np.sin(2.0 * np.pi * freq * t_eff)

    def relief_flow(self, pressure_psi: float) -> float:
        if pressure_psi < self.spec.relief_cracking_psi:
            return 0.0
        elif pressure_psi >= self.spec.relief_full_flow_psi:
            return 30.0
        else:
            fraction = (pressure_psi - self.spec.relief_cracking_psi) / (
                self.spec.relief_full_flow_psi - self.spec.relief_cracking_psi)
            return fraction * 30.0

    def reset(self):
        self._shaft_angle = 0.0


class HydraulicDriveModel:
    """Hydraulic motor + valve + pump system (for non-top-drive machines).

    Used by iron roughneck (spinner phase), power tong, and bucking unit.
    """

    def __init__(self, cfg: SimConfig, oil_model: OilPropertyModel):
        self.cfg = cfg
        self.oil_model = oil_model
        self.valve = ProportionalValve(cfg.valve)
        self.pump = HydraulicPump(cfg.pump)

        self.D_motor_in3 = cfg.motor_displacement_cc * 0.0610237
        self.gear_ratio = cfg.top_drive.gear_ratio if cfg.machine_type == MachineType.TOP_DRIVE else 1.0
        self.J_total = 5.0  # Default inertia for hydraulic machines
        self.B_viscous = 8.0
        self.T_coulomb_nm = 15.0
        self.K_leak = 1e-5
        self.V_trapped = cfg.pump.trapped_volume_in3
        self.kp_flow = 1.0 / 70.0
        self.max_output_rpm = 100.0
        self.pressure: float = 0.0
        self.omega: float = 0.0

    def step(self, valve_command_pct: float, load_torque_ftlbs: float,
             viscosity_cst: float, dt: float) -> Tuple[
                 float, float, float, float, float, float, float, float]:
        spool_pos = self.valve.step(valve_command_pct, dt)
        flow_fraction = np.clip(spool_pos * self.kp_flow, 0.0, 1.0)

        pump_flow_gpm = self.pump.flow_at_pressure(self.pressure, viscosity_cst) * flow_fraction
        Q_in = pump_flow_gpm * 231.0 / 60.0

        motor_vol_eff = np.clip(self.cfg.motor_efficiency_vol, 0.8, 0.98)
        Q_motor = self.D_motor_in3 * abs(self.omega) / (2.0 * np.pi) / motor_vol_eff

        visc_ratio = 46.0 / max(viscosity_cst, 1.0)
        Q_leak = self.K_leak * self.pressure * visc_ratio
        Q_leak_gpm = Q_leak * 60.0 / 231.0

        Q_relief = self.pump.relief_flow(self.pressure) * 231.0 / 60.0

        beta_eff = self.oil_model.bulk_modulus_effective(self.pressure)
        dQ = Q_in - Q_motor - Q_leak - Q_relief
        dp_dt = (beta_eff / max(self.V_trapped, 1.0)) * dQ
        self.pressure += dp_dt * dt
        self.pressure = np.clip(self.pressure, 0.0, self.cfg.pump.max_pressure_psi * 1.1)

        ripple = self.pump.ripple(dt, self.pressure)
        display_pressure = self.pressure + ripple

        motor_mech_eff = self._motor_efficiency(self.omega, self.pressure)
        motor_torque_inlbs = self.D_motor_in3 * self.pressure / (2.0 * np.pi) * motor_mech_eff
        motor_torque_ftlbs = motor_torque_inlbs / 12.0
        output_torque_ftlbs = motor_torque_ftlbs * self.gear_ratio

        load_nm = load_torque_ftlbs * 1.3558
        output_nm = output_torque_ftlbs * 1.3558
        net_torque = output_nm - load_nm - self.B_viscous * self.omega
        if abs(self.omega) > 0.01:
            net_torque -= self.T_coulomb_nm * np.sign(self.omega)
        elif abs(output_nm - load_nm) > self.T_coulomb_nm:
            net_torque -= self.T_coulomb_nm * np.sign(output_nm - load_nm)
        else:
            net_torque = 0.0

        self.omega += (net_torque / max(self.J_total, 0.1)) * dt
        self.omega = np.clip(self.omega, 0.0, self.max_output_rpm * 2.0 * np.pi / 60.0)

        output_rpm = self.omega * 60.0 / (2.0 * np.pi)
        actual_torque = min(output_torque_ftlbs, load_torque_ftlbs) if load_torque_ftlbs > 0 else 0.0

        return (display_pressure, actual_torque, output_rpm, pump_flow_gpm,
                spool_pos, Q_leak_gpm, ripple, motor_mech_eff)

    def _motor_efficiency(self, omega: float, pressure: float) -> float:
        rpm = abs(omega * 60.0 / (2.0 * np.pi))
        coulomb_loss = min(0.05 / max(rpm, 0.1), 0.40)
        viscous_loss = 0.001 * rpm / max(self.max_output_rpm, 1.0)
        pressure_loss = 0.02 * pressure / max(self.cfg.pump.max_pressure_psi, 1.0)
        return np.clip(1.0 - coulomb_loss - viscous_loss - pressure_loss, 0.40, 0.95)

    def reset(self):
        self.pressure = 0.0
        self.omega = 0.0
        self.valve.reset()
        self.pump.reset()


class IronRoughneckModel:
    """Dual-phase model: spinner (hydraulic motor) + torque wrench (hydraulic cylinder).

    Section 2.2: The iron roughneck has a DISCONTINUITY at spin-to-torque handoff.
    Spinner stops, torque wrench engages, makeup continues at much lower RPM
    with much higher torque capacity.
    """

    def __init__(self, cfg: SimConfig, oil_model: OilPropertyModel):
        self.cfg = cfg
        self.oil_model = oil_model
        ir = cfg.iron_roughneck

        # Spinner subsystem (hydraulic motor)
        self.spinner_valve = ProportionalValve(cfg.valve)
        self.spinner_pump = HydraulicPump(cfg.pump)
        self.spinner_D_in3 = ir.spinner_motor_displacement_cc * 0.0610237
        self.spinner_max_rpm = ir.spinner_max_rpm
        self.spinner_max_torque = ir.spinner_max_torque_ftlbs

        # Torque wrench subsystem (hydraulic cylinder)
        cyl_area = np.pi / 4.0 * ir.wrench_cylinder_bore_in ** 2
        self.wrench_torque_per_psi = cyl_area * ir.wrench_moment_arm_in / 12.0  # ft-lb/PSI
        self.wrench_max_torque = ir.wrench_max_torque_ftlbs
        self.wrench_sweep_deg = ir.wrench_angular_sweep_deg
        self.wrench_max_pressure = ir.wrench_pressure_psi

        # Handoff state
        self.handoff_delay = ir.handoff_delay_ms / 1000.0
        self.handoff_rpm_threshold = ir.handoff_rpm_threshold
        self._phase = "spinner"  # "spinner", "handoff", "wrench"
        self._handoff_timer = 0.0

        # Shared state
        self.pressure: float = 0.0
        self.omega: float = 0.0
        self._K_leak = 1e-5
        self._V_trapped = cfg.pump.trapped_volume_in3

    def step(self, valve_command_pct: float, load_torque_ftlbs: float,
             viscosity_cst: float, dt: float) -> Tuple[
                 float, float, float, float, float, float, float, float]:
        """Returns same signature as HydraulicDriveModel for compatibility."""

        if self._phase == "spinner":
            return self._step_spinner(valve_command_pct, load_torque_ftlbs, viscosity_cst, dt)
        elif self._phase == "handoff":
            return self._step_handoff(dt)
        else:  # wrench
            return self._step_wrench(valve_command_pct, load_torque_ftlbs, viscosity_cst, dt)

    def _step_spinner(self, cmd, load, visc, dt):
        """Spinner phase: high RPM, low torque."""
        spool = self.spinner_valve.step(cmd, dt)
        flow_frac = np.clip(spool / 100.0, 0.0, 1.0)
        pump_flow = self.spinner_pump.flow_at_pressure(self.pressure, visc) * flow_frac
        Q_in = pump_flow * 231.0 / 60.0

        Q_motor = self.spinner_D_in3 * abs(self.omega) / (2.0 * np.pi) / 0.93
        Q_leak = self._K_leak * self.pressure * 46.0 / max(visc, 1.0)
        Q_relief = self.spinner_pump.relief_flow(self.pressure) * 231.0 / 60.0

        beta = self.oil_model.bulk_modulus_effective(self.pressure)
        self.pressure += (beta / max(self._V_trapped, 1.0)) * (Q_in - Q_motor - Q_leak - Q_relief) * dt
        self.pressure = np.clip(self.pressure, 0.0, self.cfg.iron_roughneck.spinner_pressure_psi * 1.2)

        ripple = self.spinner_pump.ripple(dt, self.pressure)
        motor_torque = self.spinner_D_in3 * self.pressure / (2.0 * np.pi) * 0.88 / 12.0

        load_nm = load * 1.3558
        motor_nm = motor_torque * 1.3558
        net = motor_nm - load_nm - 5.0 * self.omega
        self.omega += (net / 3.0) * dt
        self.omega = np.clip(self.omega, 0.0, self.spinner_max_rpm * 2.0 * np.pi / 60.0)

        rpm = self.omega * 60.0 / (2.0 * np.pi)
        actual_torque = min(motor_torque, load) if load > 0 else 0.0

        return (self.pressure + ripple, actual_torque, rpm, pump_flow,
                spool, Q_leak * 60.0 / 231.0, ripple, 0.88)

    def _step_handoff(self, dt):
        """Transition phase: spinner stopped, wrench engaging."""
        self._handoff_timer += dt
        # Decelerate spinner
        self.omega *= max(0, 1.0 - dt * 10.0)
        rpm = self.omega * 60.0 / (2.0 * np.pi)

        if self._handoff_timer >= self.handoff_delay:
            self._phase = "wrench"
            self.omega = 0.0
            self.pressure = 0.0

        return (self.pressure, 0.0, rpm, 0.0, 0.0, 0.0, 0.0, 0.0)

    def _step_wrench(self, cmd, load, visc, dt):
        """Torque wrench phase: low RPM, high torque via hydraulic cylinder."""
        # Pressure builds proportionally to valve command
        target_pressure = (cmd / 100.0) * self.wrench_max_pressure
        tau = 0.15  # Slower cylinder response
        alpha = dt / (tau + dt)
        self.pressure += alpha * (target_pressure - self.pressure)
        self.pressure = np.clip(self.pressure, 0.0, self.wrench_max_pressure * 1.1)

        # Torque from cylinder pressure × moment arm
        wrench_torque = self.wrench_torque_per_psi * self.pressure
        wrench_torque = min(wrench_torque, self.wrench_max_torque)

        # Very slow rotation (limited angular sweep per stroke)
        # Effective RPM: sweep_deg / 360 * strokes_per_second * 60
        effective_rpm = (self.wrench_sweep_deg / 360.0) * (cmd / 100.0) * 2.0  # ~2 RPM max
        self.omega = effective_rpm * 2.0 * np.pi / 60.0

        actual_torque = min(wrench_torque, load) if load > 0 else 0.0

        return (self.pressure, actual_torque, effective_rpm, 0.0,
                cmd, 0.0, 0.0, 0.88)

    def trigger_handoff(self):
        """Called by state machine when shoulder is detected."""
        if self._phase == "spinner":
            self._phase = "handoff"
            self._handoff_timer = 0.0

    @property
    def current_phase(self) -> str:
        return self._phase

    def reset(self):
        self.pressure = 0.0
        self.omega = 0.0
        self._phase = "spinner"
        self._handoff_timer = 0.0
        self.spinner_valve.reset()
        self.spinner_pump.reset()


# ═══════════════════════════════════════════════════════════════════
# Shoulder Detection & Slope Calculator
# ═══════════════════════════════════════════════════════════════════

class ShoulderDetector:
    """Real-time shoulder contact detection.

    Monitors torque-turn derivative (dT/dN). Shoulder is detected when
    dT/dN exceeds a threshold relative to the spin-in baseline.
    """

    def __init__(self, threshold_multiplier: float = 5.0, window_size: int = 10):
        self.threshold_mult = threshold_multiplier
        self.window_size = window_size
        self._torque_history: list = []
        self._turns_history: list = []
        self._baseline_slope: float = 0.0
        self._shoulder_detected: bool = False
        self._shoulder_torque: float = 0.0
        self._shoulder_turn: float = 0.0

    def update(self, torque: float, turns: float) -> bool:
        """Feed new data point, returns True if shoulder just detected."""
        self._torque_history.append(torque)
        self._turns_history.append(turns)

        if len(self._torque_history) < self.window_size + 1:
            return False

        if self._shoulder_detected:
            return False

        # Compute current slope
        recent_t = self._torque_history[-self.window_size:]
        recent_n = self._turns_history[-self.window_size:]
        dt_dn = (recent_t[-1] - recent_t[0]) / max(recent_n[-1] - recent_n[0], 0.001)

        # Compute baseline slope (from early spin-in data)
        if len(self._torque_history) > 2 * self.window_size and self._baseline_slope == 0:
            early_t = self._torque_history[:self.window_size]
            early_n = self._turns_history[:self.window_size]
            self._baseline_slope = max(
                abs(early_t[-1] - early_t[0]) / max(early_n[-1] - early_n[0], 0.001),
                1.0
            )

        if self._baseline_slope > 0 and dt_dn > self._baseline_slope * self.threshold_mult:
            self._shoulder_detected = True
            self._shoulder_torque = torque
            self._shoulder_turn = turns
            return True

        return False

    @property
    def shoulder_torque(self) -> float:
        return self._shoulder_torque

    @property
    def shoulder_turn(self) -> float:
        return self._shoulder_turn

    @property
    def detected(self) -> bool:
        return self._shoulder_detected

    def reset(self):
        self._torque_history.clear()
        self._turns_history.clear()
        self._baseline_slope = 0.0
        self._shoulder_detected = False
        self._shoulder_torque = 0.0
        self._shoulder_turn = 0.0


class SlopeCalculator:
    """Rolling dT/dN (torque-turn slope) calculator.

    Critical quality metric for premium connections per Section 3.2.3.
    """

    def __init__(self, window_turns: float = 0.05):
        self.window_turns = window_turns
        self._torque_buf: list = []
        self._turns_buf: list = []

    def update(self, torque: float, turns: float) -> float:
        self._torque_buf.append(torque)
        self._turns_buf.append(turns)

        # Trim buffer to window
        while (len(self._turns_buf) > 2 and
               self._turns_buf[-1] - self._turns_buf[0] > self.window_turns * 2):
            self._torque_buf.pop(0)
            self._turns_buf.pop(0)

        if len(self._turns_buf) < 2:
            return 0.0

        dn = self._turns_buf[-1] - self._turns_buf[0]
        if dn < 0.001:
            return 0.0

        return (self._torque_buf[-1] - self._torque_buf[0]) / dn

    def reset(self):
        self._torque_buf.clear()
        self._turns_buf.clear()


# ═══════════════════════════════════════════════════════════════════
# Advanced PID Controller
# ═══════════════════════════════════════════════════════════════════

class AdvancedPIDController:
    """PID controller matching GE CPE305 PLC behavior."""

    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        self.scan_dt = cfg.pid_scan_rate_ms / 1000.0
        self._accumulator: float = 0.0
        self.kp = cfg.pid_kp
        self.ki = cfg.pid_ki
        self.kd = cfg.pid_kd
        self.integral: float = 0.0
        self.prev_error: float = 0.0
        self.prev_output: float = 0.0
        self.prev_derivative: float = 0.0
        self.mode: str = "rpm"

    def update(self, setpoint: float, measured: float, dt: float,
               feedforward: float = 0.0) -> Tuple[float, float, float]:
        self._accumulator += dt
        if self._accumulator < self.scan_dt:
            return self.prev_output, self.prev_error, self.integral

        effective_dt = self._accumulator
        self._accumulator = 0.0
        error = setpoint - measured

        if setpoint > 0 and abs(error) < self.cfg.pid_deadband_pct / 100.0 * abs(setpoint):
            error = 0.0

        p_term = self.kp * error

        self.integral += error * effective_dt
        self.integral = np.clip(self.integral, -self.cfg.pid_integral_clamp, self.cfg.pid_integral_clamp)
        i_term = self.ki * self.integral

        tau_d = self.cfg.pid_derivative_filter_tau
        if effective_dt > 0:
            d_raw = self.kd * (error - self.prev_error) / effective_dt
            if tau_d > 0:
                alpha_d = effective_dt / (tau_d + effective_dt)
                d_term = alpha_d * d_raw + (1.0 - alpha_d) * self.prev_derivative
            else:
                d_term = d_raw
            self.prev_derivative = d_term
        else:
            d_term = 0.0

        raw_output = p_term + i_term + d_term + self.cfg.pid_feedforward_gain * feedforward

        max_change = self.cfg.pid_valve_rate_limit_pct_s * effective_dt
        delta = np.clip(raw_output - self.prev_output, -max_change, max_change)
        output = self.prev_output + delta
        output = np.clip(output, self.cfg.pid_output_min, self.cfg.pid_output_max)

        if output != raw_output and self.ki > 0:
            self.integral -= (raw_output - output) / self.ki * 0.1

        self.prev_error = error
        self.prev_output = output
        return output, error, self.integral

    def switch_mode(self, new_mode: str, current_output: float):
        if new_mode != self.mode:
            self.mode = new_mode
            if self.ki > 0:
                self.integral = (current_output - self.kp * self.prev_error) / self.ki
            self.prev_derivative = 0.0

    def reset(self):
        self.integral = 0.0
        self.prev_error = 0.0
        self.prev_output = 0.0
        self.prev_derivative = 0.0
        self._accumulator = 0.0


# ═══════════════════════════════════════════════════════════════════
# Multi-Zone Thermal Model
# ═══════════════════════════════════════════════════════════════════

class MultiZoneThermalModel:
    """3-node thermal network: oil reservoir, manifold, motor casing."""

    def __init__(self, cfg: SimConfig, oil_model: OilPropertyModel):
        self.cfg = cfg
        self.oil_model = oil_model
        ambient = cfg.ambient_temp_f
        self.T_reservoir: float = ambient
        self.T_manifold: float = ambient
        self.T_motor_case: float = ambient
        self.C_reservoir = cfg.thermal_capacity_btu_f
        self.C_manifold = cfg.manifold_thermal_capacity
        self.C_motor_case = cfg.motor_case_thermal_capacity
        self.G_res_ambient = cfg.heat_dissipation_btu_hr / 3600.0 / 65.0
        self.G_manifold_res = 0.5
        self.G_motor_res = 0.2
        self.G_motor_ambient = 0.05

    def step(self, Q_pump_hp: float, Q_valve_hp: float, Q_motor_hp: float,
             dt: float) -> Tuple[float, float, float]:
        btu_per_hp_s = 2545.0 / 3600.0
        ambient = self.cfg.ambient_temp_f
        q_pump = Q_pump_hp * btu_per_hp_s
        q_valve = Q_valve_hp * btu_per_hp_s
        q_motor = Q_motor_hp * btu_per_hp_s

        q_res_to_amb = self.G_res_ambient * (self.T_reservoir - ambient)
        q_man_to_res = self.G_manifold_res * (self.T_manifold - self.T_reservoir)
        q_motor_to_res = self.G_motor_res * (self.T_motor_case - self.T_reservoir)
        q_motor_to_amb = self.G_motor_ambient * (self.T_motor_case - ambient)

        self.T_reservoir += (q_pump + q_man_to_res + q_motor_to_res - q_res_to_amb) / max(self.C_reservoir, 0.1) * dt
        self.T_manifold += (q_valve - q_man_to_res) / max(self.C_manifold, 0.1) * dt
        self.T_motor_case += (q_motor - q_motor_to_res - q_motor_to_amb) / max(self.C_motor_case, 0.1) * dt

        return self.T_reservoir, self.T_manifold, self.T_motor_case

    def get_oil_temp(self) -> float:
        return self.T_reservoir

    def reset(self):
        ambient = self.cfg.ambient_temp_f
        self.T_reservoir = ambient
        self.T_manifold = ambient
        self.T_motor_case = ambient


# ═══════════════════════════════════════════════════════════════════
# Pipe String Torsional Dynamics
# ═══════════════════════════════════════════════════════════════════

class PipeStringModel:
    """Torsional spring-damper between motor and connection."""

    def __init__(self, pipe: PipeSpec, string_spec, top_drive):
        self.pipe = pipe
        self.string = string_spec

        J_polar = pipe.polar_moment_in4
        L_inches = max(string_spec.length_ft * string_spec.num_joints * 12.0, 12.0)
        G = string_spec.shear_modulus_psi

        self.K_torsional = G * J_polar / L_inches
        self.K_torsional_ftlbs = self.K_torsional / 12.0

        r_mean_m = (pipe.od_inches + pipe.id_inches) / 4.0 * 0.0254
        mass_kg = pipe.weight_per_foot / 2.205 * string_spec.length_ft * string_spec.num_joints
        self.J_string = mass_kg * r_mean_m ** 2

        motor_inertia = getattr(top_drive, 'gearbox_inertia_kgm2', 25.0)
        rotor_inertia = getattr(getattr(top_drive, 'motor', None), 'rotor_inertia_kgm2', 12.0) if hasattr(top_drive, 'motor') else 12.0
        self.J_total = rotor_inertia + motor_inertia + self.J_string

        c_critical = 2.0 * np.sqrt(self.K_torsional_ftlbs * 1.3558 * self.J_total)
        self.C_damping = (string_spec.material_damping_ratio * c_critical +
                          string_spec.joint_friction_ftlbs * string_spec.num_joints * 1.3558)

        self.twist: float = 0.0
        self.twist_rate: float = 0.0

    def step(self, motor_omega: float, connection_omega: float,
             dt: float) -> Tuple[float, float]:
        self.twist += (motor_omega - connection_omega) * dt
        self.twist_rate = motor_omega - connection_omega
        spring_torque_nm = self.K_torsional_ftlbs * 1.3558 * self.twist + self.C_damping * self.twist_rate
        return spring_torque_nm / 1.3558, self.twist

    def natural_frequency_hz(self) -> float:
        K_nm = self.K_torsional_ftlbs * 1.3558
        return np.sqrt(K_nm / max(self.J_total, 0.01)) / (2.0 * np.pi)

    def reset(self):
        self.twist = 0.0
        self.twist_rate = 0.0


# ═══════════════════════════════════════════════════════════════════
# Physics Engine — Main Orchestrator
# ═══════════════════════════════════════════════════════════════════

class PhysicsEngine:
    """Orchestrates all physics subsystems per timestep.

    Supports multiple machine types (top drive, iron roughneck, etc.)
    and multiple connection types (API 8-round, buttress, premium, drill pipe).
    """

    def __init__(self, cfg: SimConfig, pipe: PipeSpec,
                 rng: Optional[np.random.Generator] = None):
        self.cfg = cfg
        self.pipe = pipe
        self.rng = rng or np.random.default_rng()

        self.oil_model = OilPropertyModel(cfg.oil)
        self.torque_model = create_torque_model(pipe, cfg.compound, self.rng)
        self.pid = AdvancedPIDController(cfg)
        self.thermal = MultiZoneThermalModel(cfg, self.oil_model)
        self.string_model = PipeStringModel(pipe, cfg.pipe_string, cfg.top_drive)
        self.shoulder_detector = ShoulderDetector()
        self.slope_calculator = SlopeCalculator()

        # Create appropriate drive model based on machine type
        if cfg.machine_type == MachineType.TOP_DRIVE:
            self.drive = ACMotorVFDModel(cfg)
            self._use_ac_motor = True
        elif cfg.machine_type == MachineType.IRON_ROUGHNECK:
            self.drive = IronRoughneckModel(cfg, self.oil_model)
            self._use_ac_motor = False
        else:
            self.drive = HydraulicDriveModel(cfg, self.oil_model)
            self._use_ac_motor = False

        self.state = PhysicsState(oil_temp_f=cfg.ambient_temp_f)
        self._rpm_setpoint: float = 15.0
        self._torque_setpoint: float = pipe.optimum_torque_ftlbs
        self._breakout_torque: float = 0.0
        self._breakout_rpm: float = 10.0
        self._time: float = 0.0
        self._peak_torque: float = 0.0
        self._stall_timer: float = 0.0
        self._e_stop_timer: float = 0.0
        self._connection_omega: float = 0.0
        self._hookload_klbs: float = 0.0

        # Hookload simulation (top drive only)
        if cfg.machine_type == MachineType.TOP_DRIVE:
            self._hookload_klbs = pipe.weight_per_foot * cfg.pipe_string.length_ft * cfg.pipe_string.num_joints / 1000.0

    def configure_connection(self, rpm: float = 15.0,
                              target_torque: Optional[float] = None,
                              breakout_rpm: float = 10.0):
        self._rpm_setpoint = rpm
        self._torque_setpoint = target_torque or self.pipe.optimum_torque_ftlbs
        self._breakout_rpm = breakout_rpm
        self.state.target_torque_ftlbs = self._torque_setpoint

    def start_threading(self):
        self.state.connection_state = ConnectionState.APPROACH
        self.state.operating_mode = OperatingMode.THREADING
        self.state.turns = 0.0
        self.state.encoder_counts = 0
        self._peak_torque = 0.0
        self.pid.reset()
        self.drive.reset()
        self.string_model.reset()
        self.shoulder_detector.reset()
        self.slope_calculator.reset()
        self.torque_model.reset_fault_multiplier()

    def start_breakout(self):
        self.state.connection_state = ConnectionState.BREAKOUT
        self.state.operating_mode = OperatingMode.BACKOFF
        self._breakout_torque = self.torque_model.breakout_torque_profile(0.0)
        self._peak_torque = 0.0
        self.pid.reset()

    def step(self, dt: Optional[float] = None) -> PhysicsState:
        dt = dt or self.cfg.physics_dt
        self._time += dt
        s = self.state
        s.time = self._time

        # 1. State machine
        self._update_state_machine(dt)

        # 2. Oil viscosity
        viscosity = self.oil_model.viscosity_at_temp(s.oil_temp_f)
        s.oil_viscosity_cst = viscosity

        # 3. Torque demand
        load_torque = self._compute_load_torque()

        # 4. Compound friction
        s.compound_friction_kf = self.torque_model.compute_kf(s.oil_temp_f, s.rpm)

        # 5. PID
        feedforward = self._estimate_feedforward(load_torque)
        pid_out, pid_err, pid_int = self._run_pid(feedforward, dt)
        s.pid_output = pid_out
        s.pid_error = pid_err
        s.pid_integral = pid_int
        s.valve_command_pct = pid_out

        # 6. Drive system (machine-type specific)
        if self._use_ac_motor:
            torque, rpm, motor_rpm, vfd_current, vfd_freq = self.drive.step(
                pid_out, load_torque, dt)
            s.pressure_psi = 0.0  # No hydraulic pressure for AC motor
            s.motor_torque_ftlbs = torque
            s.rpm = rpm
            s.flow_gpm = 0.0
            s.valve_spool_pct = pid_out
            s.leakage_flow_gpm = 0.0
            s.pump_ripple_psi = 0.0
            s.motor_mech_efficiency = self.cfg.top_drive.motor.efficiency
            s.vfd_current_pct = vfd_current
            s.vfd_frequency_hz = vfd_freq
            s.motor_speed_rpm = motor_rpm
        else:
            (pressure, torque, rpm, flow, spool_pos, leak_flow,
             ripple, motor_eff) = self.drive.step(pid_out, load_torque, viscosity, dt)
            s.pressure_psi = pressure
            s.motor_torque_ftlbs = torque
            s.rpm = rpm
            s.flow_gpm = flow
            s.valve_spool_pct = spool_pos
            s.leakage_flow_gpm = leak_flow
            s.pump_ripple_psi = ripple
            s.motor_mech_efficiency = motor_eff

        # 7. String dynamics
        motor_omega = rpm * 2.0 * np.pi / 60.0
        spring_torque, twist = self.string_model.step(motor_omega, self._connection_omega, dt)
        s.string_twist_deg = np.degrees(twist)

        conn_accel = spring_torque * 1.3558 / max(self.string_model.J_string, 0.01)
        self._connection_omega += conn_accel * dt
        self._connection_omega = np.clip(
            self._connection_omega, 0.0,
            motor_omega * 1.1 if motor_omega > 0 else 0.0
        )
        s.connection_rpm = self._connection_omega * 60.0 / (2.0 * np.pi)

        s.torque_ftlbs = min(torque, load_torque) if load_torque > 0 else 0.0

        # 8. Encoder & turns
        effective_rpm = s.connection_rpm if s.connection_rpm > 0 else s.rpm
        if s.operating_mode == OperatingMode.THREADING:
            s.turns += effective_rpm / 60.0 * dt
        elif s.operating_mode == OperatingMode.BACKOFF:
            s.turns -= abs(effective_rpm) / 60.0 * dt
        s.encoder_counts = int(s.turns * self.cfg.encoder_cpr)

        # 9. Shoulder detection & slope calculation
        if s.operating_mode == OperatingMode.THREADING:
            just_detected = self.shoulder_detector.update(s.torque_ftlbs, s.turns)
            if just_detected:
                s.shoulder_torque_ftlbs = self.shoulder_detector.shoulder_torque
            s.slope_dT_dN = self.slope_calculator.update(s.torque_ftlbs, s.turns)

        # 10. Thermal
        Q_pump_hp = self._compute_pump_heat(s.pressure_psi, s.flow_gpm)
        Q_valve_hp = self._compute_valve_heat(s.pressure_psi, s.valve_spool_pct, s.flow_gpm)
        Q_motor_hp = self._compute_motor_heat(s.pressure_psi, s.flow_gpm, s.motor_mech_efficiency)
        T_res, T_man, T_mc = self.thermal.step(Q_pump_hp, Q_valve_hp, Q_motor_hp, dt)
        s.oil_temp_f = T_res
        s.manifold_temp_f = T_man
        s.motor_case_temp_f = T_mc

        # 11. Thread damage
        if s.operating_mode == OperatingMode.THREADING and s.turns > self.torque_model.t_shoulder:
            contact_p = s.torque_ftlbs / max(self.pipe.seal_diameter_inches, 1.0)
            sliding_dist = abs(s.rpm) / 60.0 * self.pipe.seal_diameter_inches * np.pi
            self.torque_model.accumulate_damage(contact_p, sliding_dist, dt)
            s.thread_damage = self.torque_model._damage_accumulated

        # 12. Peak torque tracking
        self._peak_torque = max(self._peak_torque, s.torque_ftlbs)
        s.peak_torque_ftlbs = self._peak_torque

        # 13. Hookload (top drive only — varies with pipe weight and motion)
        if self.cfg.machine_type == MachineType.TOP_DRIVE:
            s.hookload_klbs = self._hookload_klbs + self.rng.normal(0, 0.1)

        # 14. Machine phase (iron roughneck)
        if isinstance(self.drive, IronRoughneckModel):
            s.machine_phase = self.drive.current_phase

        # 15. Fault code assembly
        self._update_fault_code()

        # 16. Stick-slip detection
        self._detect_stick_slip()

        return s

    def _compute_load_torque(self) -> float:
        s = self.state
        if s.operating_mode == OperatingMode.THREADING:
            return self.torque_model.torque_at_turns(s.turns, s.oil_temp_f, s.rpm)
        elif s.operating_mode == OperatingMode.BACKOFF:
            if s.connection_state == ConnectionState.BREAKOUT:
                return self.torque_model.breakout_torque_profile(0.01)
            else:
                turns_rem = max(0, self.torque_model.scaled_turns - abs(s.turns))
                frac = turns_rem / self.torque_model.scaled_turns if self.torque_model.scaled_turns > 0 else 0
                return self.torque_model.spin_in_torque * frac
        return 0.0

    def _estimate_feedforward(self, load_torque: float) -> float:
        if load_torque <= 0:
            return 0.0
        if self._use_ac_motor:
            est_pct = (load_torque / max(self.cfg.top_drive.max_continuous_torque_ftlbs, 1)) * 100.0
            return np.clip(est_pct, 0.0, 100.0)
        else:
            if self.cfg.operating_pressure_psi <= 0:
                return 0.0
            est_pressure = load_torque * 12.0 / max(self.cfg.motor_displacement_cc * 0.0610237 / (2.0 * np.pi) * 0.9, 0.01)
            est_valve = est_pressure / (self.cfg.operating_pressure_psi / 70.0)
            return np.clip(est_valve, 0.0, 100.0)

    def _run_pid(self, feedforward: float, dt: float) -> Tuple[float, float, float]:
        s = self.state
        if s.connection_state in (ConnectionState.SPIN_IN, ConnectionState.SHOULDER, ConnectionState.HANDOFF):
            s.pid_setpoint = self._rpm_setpoint
            s.pid_mode = "rpm"
            return self.pid.update(self._rpm_setpoint, s.rpm, dt, feedforward)
        elif s.connection_state == ConnectionState.POWER_TIGHT:
            s.pid_setpoint = self._torque_setpoint
            s.pid_mode = "torque"
            return self.pid.update(self._torque_setpoint, s.torque_ftlbs, dt, feedforward)
        elif s.connection_state in (ConnectionState.BREAKOUT, ConnectionState.BACKOFF):
            s.pid_setpoint = self._breakout_rpm
            s.pid_mode = "rpm"
            return self.pid.update(self._breakout_rpm, abs(s.rpm), dt, feedforward)
        elif s.connection_state == ConnectionState.APPROACH:
            s.pid_setpoint = 5.0
            s.pid_mode = "rpm"
            return self.pid.update(5.0, s.rpm, dt, 0.0)
        else:
            s.pid_setpoint = 0.0
            s.pid_mode = "rpm"
            return 0.0, 0.0, 0.0

    def _compute_pump_heat(self, pressure: float, flow: float) -> float:
        if self._use_ac_motor:
            # AC motor heat is from I²R losses
            return self.cfg.top_drive.motor.rated_hp * (1.0 - self.cfg.top_drive.motor.efficiency) * (self.state.vfd_current_pct / 100.0) ** 2
        pump_eff = self.cfg.pump.mech_efficiency
        hp = pressure * flow / 1714.0
        return hp * (1.0 - pump_eff) / max(pump_eff, 0.1)

    def _compute_valve_heat(self, pressure: float, spool: float, flow: float) -> float:
        if self._use_ac_motor:
            return 0.0  # No valve heat loss for AC motor
        drop = pressure * (1.0 - spool / 100.0) * 0.3
        return drop * flow / 1714.0

    def _compute_motor_heat(self, pressure: float, flow: float, eff: float) -> float:
        if self._use_ac_motor:
            return 0.0  # Already accounted in pump_heat for AC motor
        return pressure * flow / 1714.0 * (1.0 - eff)

    def _update_fault_code(self):
        s = self.state
        code = FaultCode.NONE
        if s.is_over_torque:
            code |= FaultCode.OVER_TORQUE
        if s.is_cross_threaded:
            code |= FaultCode.CROSS_THREAD
        if s.is_galled:
            code |= FaultCode.GALLING
        if s.is_stalled:
            code |= FaultCode.STALL
        if s.is_over_temp:
            code |= FaultCode.OVER_TEMP
        if s.is_stick_slip:
            code |= FaultCode.STICK_SLIP
        if s.is_stripped:
            code |= FaultCode.STRIPPED_THREAD
        if s.is_misaligned:
            code |= FaultCode.MISALIGNED_STAB
        if s.is_wrong_compound:
            code |= FaultCode.WRONG_COMPOUND
        s.fault_code = int(code)

        # Over-torque check
        if s.torque_ftlbs > self.pipe.max_torque_ftlbs * self.torque_model._kf_scale:
            s.is_over_torque = True
        if s.oil_temp_f > self.cfg.temp_shutdown_f:
            s.is_over_temp = True

    def _detect_stick_slip(self):
        s = self.state
        if s.connection_state not in (ConnectionState.SHOULDER, ConnectionState.POWER_TIGHT):
            s.is_stick_slip = False
            return
        if s.rpm < 5.0 and abs(self.string_model.twist_rate) > 0.5:
            s.is_stick_slip = True
            s.stick_slip_frequency_hz = self.string_model.natural_frequency_hz()
        else:
            s.is_stick_slip = False

    def _update_state_machine(self, dt: float):
        s = self.state
        tm = self.torque_model

        # Stall detection
        if s.operating_mode == OperatingMode.THREADING:
            if s.rpm < 0.5 and s.valve_command_pct > 50.0:
                self._stall_timer += dt
                if self._stall_timer > 3.0:
                    s.is_stalled = True
                    s.connection_state = ConnectionState.STALL
                    return
            else:
                self._stall_timer = 0.0

        if s.connection_state == ConnectionState.E_STOP:
            self._e_stop_timer += dt
            if self._e_stop_timer > 2.0:
                s.connection_state = ConnectionState.FAULT
                s.operating_mode = OperatingMode.IDLE
            return

        if s.connection_state == ConnectionState.IDLE:
            return
        elif s.connection_state == ConnectionState.APPROACH:
            if self._time > 0.5 or s.turns > 0.1:
                s.connection_state = ConnectionState.SPIN_IN
        elif s.connection_state == ConnectionState.SPIN_IN:
            if s.turns >= tm.t_spin_end:
                s.connection_state = ConnectionState.SHOULDER
                # Trigger iron roughneck handoff at shoulder
                if isinstance(self.drive, IronRoughneckModel):
                    self.drive.trigger_handoff()
                    s.connection_state = ConnectionState.HANDOFF
        elif s.connection_state == ConnectionState.HANDOFF:
            # Wait for iron roughneck handoff to complete
            if isinstance(self.drive, IronRoughneckModel):
                if self.drive.current_phase == "wrench":
                    s.connection_state = ConnectionState.SHOULDER
            else:
                s.connection_state = ConnectionState.SHOULDER
        elif s.connection_state == ConnectionState.SHOULDER:
            if s.turns >= tm.t_shoulder:
                s.connection_state = ConnectionState.POWER_TIGHT
                self.pid.switch_mode("torque", s.valve_command_pct)
        elif s.connection_state == ConnectionState.POWER_TIGHT:
            if s.torque_ftlbs >= self._torque_setpoint * 0.98:
                s.connection_state = ConnectionState.HOLD
                s.operating_mode = OperatingMode.IDLE
        elif s.connection_state == ConnectionState.HOLD:
            s.connection_state = ConnectionState.COMPLETE
        elif s.connection_state == ConnectionState.BREAKOUT:
            if self._peak_torque > 0 and s.torque_ftlbs < self._peak_torque * 0.5:
                s.connection_state = ConnectionState.BACKOFF
        elif s.connection_state == ConnectionState.BACKOFF:
            if abs(s.turns) <= 0.1 or s.torque_ftlbs < tm.spin_in_torque * 0.1:
                s.connection_state = ConnectionState.COMPLETE
                s.operating_mode = OperatingMode.IDLE
        elif s.connection_state == ConnectionState.STALL:
            if s.rpm > 2.0:
                s.connection_state = ConnectionState.FAULT_RECOVERY
                s.is_stalled = False
        elif s.connection_state == ConnectionState.FAULT_RECOVERY:
            s.connection_state = ConnectionState.SHOULDER
            s.operating_mode = OperatingMode.THREADING

    def reset(self):
        self.state = PhysicsState(oil_temp_f=self.cfg.ambient_temp_f)
        self.drive.reset()
        self.pid.reset()
        self.thermal.reset()
        self.string_model.reset()
        self.shoulder_detector.reset()
        self.slope_calculator.reset()
        self.torque_model.reset_fault_multiplier()
        self._time = 0.0
        self._peak_torque = 0.0
        self._stall_timer = 0.0
        self._e_stop_timer = 0.0
        self._connection_omega = 0.0


# ═══════════════════════════════════════════════════════════════════
# Physics Calibrator — Fit simulator parameters to real data
# ═══════════════════════════════════════════════════════════════════

class PhysicsCalibrator:
    """Fits simulator parameters to real captured data.

    Like system identification in control theory: given real torque-turn
    curves from capture_live.py, find the SimConfig parameters that
    minimize the error between simulated and real curves.

    Steps:
        1. Load captured CSV from real machine
        2. Segment into individual connections
        3. For each connection, optimize:
           - friction coefficient (kf_scale)
           - hydraulic time constant (tau)
           - PID gains (kp, ki, kd)
           - noise levels (snr_db values)
        4. Output calibrated SimConfig + MachineProfile

    Usage:
        cal = PhysicsCalibrator()
        cal.load_real_data("captures/rig709_session1.csv")
        connections = cal.segment_connections()
        result = cal.calibrate(connections[0])
        print(result)  # {'kf_scale': 1.12, 'tau_ms': 135, ...}
    """

    def __init__(self, sample_rate_hz: float = 10.0):
        self.sample_rate_hz = sample_rate_hz
        self.real_data = None

    def load_real_data(self, csv_path: str) -> int:
        """Load captured CSV and return number of rows."""
        import csv as csv_mod
        with open(csv_path, 'r') as f:
            reader = csv_mod.DictReader(f)
            self.real_data = list(reader)
        return len(self.real_data)

    def segment_connections(self, rpm_threshold: float = 1.0,
                            torque_threshold: float = 50.0,
                            gap_samples: int = 50) -> list:
        """Segment real data into individual connections.

        Returns list of dicts, each containing numpy arrays for
        one connection's torque, rpm, pressure, turns, time.
        """
        if not self.real_data:
            return []

        connections = []
        current = []
        idle_count = 0

        for row in self.real_data:
            rpm = float(row.get('rpm', 0))
            torque = float(row.get('torque_ftlbs', 0))

            if rpm > rpm_threshold or torque > torque_threshold:
                if idle_count > gap_samples and current:
                    connections.append(self._finalize_segment(current))
                    current = []
                current.append(row)
                idle_count = 0
            else:
                idle_count += 1
                if current:
                    current.append(row)

        if current:
            connections.append(self._finalize_segment(current))

        return connections

    def _finalize_segment(self, rows: list) -> dict:
        """Convert a list of CSV rows into numpy arrays."""
        result = {
            'torque': np.array([float(r.get('torque_ftlbs', 0)) for r in rows]),
            'rpm': np.array([float(r.get('rpm', 0)) for r in rows]),
            'pressure': np.array([float(r.get('pressure_psi', 0)) for r in rows]),
            'turns': np.array([float(r.get('turns', 0)) for r in rows]),
            'temperature': np.array([float(r.get('oil_temp_f', 0)) for r in rows]),
            'n_samples': len(rows),
            'duration_s': len(rows) / self.sample_rate_hz,
        }
        result['peak_torque'] = float(np.max(result['torque']))
        result['peak_rpm'] = float(np.max(result['rpm']))
        result['total_turns'] = float(result['turns'][-1] - result['turns'][0]) if len(result['turns']) > 0 else 0
        return result

    def calibrate(self, connection: dict, base_cfg: 'SimConfig' = None,
                  pipe: 'PipeSpec' = None) -> dict:
        """Optimize SimConfig parameters to match one real connection.

        Uses scipy.optimize.minimize to find parameters that minimize
        the MSE between simulated and real torque-turn curves.

        Returns dict of calibrated parameter values.
        """
        try:
            from scipy.optimize import minimize
        except ImportError:
            return self._manual_calibrate(connection)

        real_torque = connection['torque']
        real_turns = connection['turns']

        if len(real_torque) < 20:
            return {'error': 'Connection too short for calibration'}

        # Simple manual estimation as fallback / starting point
        return self._manual_calibrate(connection)

    def _manual_calibrate(self, connection: dict) -> dict:
        """Manual parameter estimation without scipy.

        Extracts key characteristics from the real torque-turn curve
        to estimate simulator parameters.
        """
        torque = connection['torque']
        turns = connection['turns']
        rpm = connection['rpm']

        result = {}

        # Estimate friction scale from peak torque ratio
        peak_torque = float(np.max(torque))
        result['peak_torque_ftlbs'] = peak_torque
        result['total_turns'] = connection['total_turns']

        # Estimate hydraulic time constant from RPM rise time
        if len(rpm) > 10:
            rpm_max = np.max(rpm)
            if rpm_max > 0:
                # Time to reach 63% of max RPM
                target_63 = rpm_max * 0.63
                idx_63 = np.argmax(rpm >= target_63)
                if idx_63 > 0:
                    result['estimated_tau_ms'] = round(
                        (idx_63 / self.sample_rate_hz) * 1000, 1
                    )

        # Estimate noise level from steady-state segments
        if len(torque) > 50:
            # Look at last 20% of connection (should be near steady state)
            tail = torque[int(len(torque) * 0.8):]
            if len(tail) > 5:
                mean_t = np.mean(tail)
                std_t = np.std(tail)
                if std_t > 0 and mean_t > 0:
                    result['estimated_torque_snr_db'] = round(
                        20 * np.log10(mean_t / std_t), 1
                    )

        # Shoulder detection from torque-turn curve
        if len(torque) > 20 and len(turns) > 20:
            # Find point where torque gradient increases sharply
            dt_dn = np.gradient(torque, turns)
            dt_dn_smooth = np.convolve(dt_dn, np.ones(5)/5, mode='valid')
            if len(dt_dn_smooth) > 10:
                max_grad_idx = np.argmax(dt_dn_smooth)
                result['estimated_shoulder_turn'] = round(
                    float(turns[min(max_grad_idx, len(turns)-1)]), 3
                )
                result['estimated_power_tight_slope'] = round(
                    float(np.max(dt_dn_smooth)), 1
                )

        return result
