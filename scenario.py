"""
Scenario Generator: Domain Randomization & Failure Modes
=========================================================
Generates diverse pipe threading scenarios for training data.

Scenario distribution targets from reference Section 6.2:
  Normal casing makeup (STC/LTC)   25%    Top drive, power tong
  Normal casing makeup (BTC)       15%    Top drive, power tong
  Normal casing makeup (premium)   10%    Top drive, power tong
  Normal drill pipe makeup         15%    Iron roughneck, top drive
  Normal tubing makeup              5%    Power tong
  Full cycle (make + break)         8%    All
  Cross-thread fault                5%    All
  Galling fault                     4%    All (more common w/ premium)
  Over-torque fault                 3%    All
  Under-torque fault                3%    All
  Stall (motor limit)               2%    Top drive, roughneck
  Wrong compound                    2%    All
  Misaligned stabbing               2%    All
  Multi-connection batch            1%    All

Failure mode signatures from Section 4.1.3:
  Cross-thread:      Spike torque at low turns (<2 turns), erratic
  Galling:           Progressive rise above normal curve, rough/jerky
  Stripped thread:   Torque plateau or drop before target
  Over-torque:       Exceeds max envelope, continues rising
  Under-torque:      Target not reached, RPM at limit
  Wrong compound:    Abnormal shoulder position or slope
  Misaligned stab:   High torque at spin-in, oscillating first 2 turns
"""
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from enum import Enum

from config import (
    SimConfig, PipeSpec, MachineType, ConnectionCategory,
    PIPE_CATALOG, PREMIUM_CATALOG, DRILL_PIPE_CATALOG,
    ALL_CONNECTIONS, COMPOUND_CATALOG,
)


class ScenarioType(Enum):
    NORMAL_CASING_LTC = "normal_casing_ltc"
    NORMAL_CASING_BTC = "normal_casing_btc"
    NORMAL_CASING_PREMIUM = "normal_casing_premium"
    NORMAL_DRILL_PIPE = "normal_drill_pipe"
    NORMAL_TUBING = "normal_tubing"
    NORMAL_BREAKOUT = "normal_breakout"
    FULL_CYCLE = "full_cycle"
    CROSS_THREAD = "cross_thread"
    GALLING = "galling"
    OVER_TORQUE = "over_torque"
    UNDER_TORQUE = "under_torque"
    STALL = "stall"
    WRONG_COMPOUND = "wrong_compound"
    MISALIGNED_STABBING = "misaligned_stabbing"
    STRIPPED_THREAD = "stripped_thread"
    MULTI_CONNECTION = "multi_connection"
    STICK_SLIP = "stick_slip"
    CONNECTION_JUMP = "connection_jump"
    WASHOUT = "washout"
    COLD_START = "cold_start"
    HOT_ENVIRONMENT = "hot_environment"
    STAGED_FAULT = "staged_fault"


@dataclass
class ConnectionScenario:
    """A fully-specified scenario ready for simulation."""
    scenario_type: ScenarioType
    pipe: PipeSpec
    config: SimConfig
    machine_type: MachineType = MachineType.TOP_DRIVE
    rpm_setpoint: float = 15.0
    target_torque: float = 0.0
    breakout_rpm: float = 10.0
    num_connections: int = 1

    # Fault injection parameters
    cross_thread_turns: float = 0.0
    galling_onset_turns: float = 0.0
    galling_rate: float = 0.0
    over_torque_factor: float = 1.0
    stall_pressure_limit: float = 0.0

    # Advanced fault parameters
    stick_slip_enabled: bool = False
    stick_slip_critical_rpm: float = 5.0
    connection_jump_turn: float = 0.0
    connection_jump_severity: float = 0.0
    washout_enabled: bool = False
    washout_leak_rate: float = 0.0
    ambient_temp_override: float = 0.0
    oil_start_temp_override: float = 0.0
    staged_faults: list = field(default_factory=list)
    compound_name: str = "API_Modified_Zinc"
    string_length_ft: float = 30.0
    string_num_joints: int = 1

    # New fault parameters (Section 4.1.3)
    misaligned_severity: float = 0.0        # 0-1 severity of misalignment
    wrong_compound_kf_shift: float = 0.0    # Friction factor shift
    stripped_thread_turn: float = 0.0       # Turn at which stripping occurs
    stripped_thread_severity: float = 0.0   # How much torque drops

    # Metadata
    label: str = ""
    seed: int = 0


class FaultInjector:
    """Encapsulates all fault injection logic per Section 4.1.3."""

    def __init__(self, scenario: ConnectionScenario, rng: np.random.Generator):
        self.scenario = scenario
        self.rng = rng
        self._fault_state = {}

    def apply(self, engine, dt: float):
        state = engine.state

        if self.scenario.cross_thread_turns > 0:
            self._cross_thread(engine, state)

        if self.scenario.galling_onset_turns > 0:
            self._galling(engine, state, dt)

        if self.scenario.connection_jump_turn > 0:
            self._connection_jump(engine, state)

        if self.scenario.washout_enabled:
            self._washout(engine, state, dt)

        if self.scenario.misaligned_severity > 0:
            self._misaligned_stabbing(engine, state)

        if self.scenario.wrong_compound_kf_shift != 0:
            self._wrong_compound(engine, state)

        if self.scenario.stripped_thread_turn > 0:
            self._stripped_thread(engine, state)

        for stage_turn, fault_type, params in self.scenario.staged_faults:
            if state.turns >= stage_turn:
                self._apply_staged_fault(engine, state, fault_type, params, dt)

        if self.scenario.stall_pressure_limit > 0:
            engine.cfg.max_pressure_psi = min(
                engine.cfg.max_pressure_psi,
                self.scenario.stall_pressure_limit
            )

    def _cross_thread(self, engine, state):
        """Section 4.1.3: Spike torque at low turns (<2 turns), erratic.
        Detection: High torque before shoulder.
        """
        if state.turns < self.scenario.cross_thread_turns:
            return
        if not state.is_cross_threaded:
            state.is_cross_threaded = True

        excess = state.turns - self.scenario.cross_thread_turns
        baseline = 1.0 + 3.0 * (1.0 - np.exp(-excess * 2.0))
        ratchet = 0.3 * np.sin(excess * 2.0 * np.pi * 8.0)
        engine.torque_model.set_fault_multiplier(baseline + ratchet)

    def _galling(self, engine, state, dt):
        """Section 4.1.3: Progressive rise above normal curve, rough/jerky.
        Detection: Slope deviation > 2 sigma.
        """
        if state.turns < self.scenario.galling_onset_turns:
            return
        if not state.is_galled:
            state.is_galled = True
        if 'galling_severity' not in self._fault_state:
            self._fault_state['galling_severity'] = 0.0

        contact_pressure = state.torque_ftlbs / max(engine.pipe.seal_diameter_inches, 1.0)
        sliding_velocity = abs(state.rpm) / 60.0 * engine.pipe.seal_diameter_inches * np.pi
        damage_rate = contact_pressure * sliding_velocity * 1e-8
        self._fault_state['galling_severity'] += damage_rate * dt
        severity = self._fault_state['galling_severity']

        # Rough/jerky signature — random torque fluctuations
        jitter = self.rng.normal(0, 0.05 * severity)
        multiplier = 1.0 + severity * self.scenario.galling_rate + jitter

        if severity > 1.0:
            multiplier *= 3.0
            state.is_stalled = True

        engine.torque_model.set_fault_multiplier(multiplier)

    def _misaligned_stabbing(self, engine, state):
        """Section 4.1.3: High torque at spin-in, oscillating first 2 turns.
        Detection: Erratic first 2 turns.
        """
        if state.turns > 2.0:
            # Misalignment effects diminish after threads catch
            severity_decay = max(0, 1.0 - (state.turns - 2.0) * 2.0)
            if severity_decay <= 0:
                engine.torque_model.reset_fault_multiplier()
                return

        if not state.is_misaligned:
            state.is_misaligned = True

        severity = self.scenario.misaligned_severity
        # Oscillating torque (pipe wobbling in the box)
        oscillation = severity * 2.0 * np.sin(state.turns * 2.0 * np.pi * 4.0)
        # Elevated baseline (friction from misaligned threads)
        baseline = 1.0 + severity * 3.0 * max(0, 1.0 - state.turns / 2.0)
        engine.torque_model.set_fault_multiplier(baseline + oscillation)

    def _wrong_compound(self, engine, state):
        """Section 4.1.3: Abnormal shoulder position or slope.
        Detection: Shoulder shift > 0.5 turns.
        """
        if not state.is_wrong_compound:
            state.is_wrong_compound = True
            # Shift the shoulder position by modifying the torque model
            shift = self.scenario.wrong_compound_kf_shift
            engine.torque_model._kf_scale *= (1.0 + shift)
            # Re-compute to reflect shifted friction
            engine.torque_model._compute_farr_constants()

    def _stripped_thread(self, engine, state):
        """Section 4.1.3: Torque plateau or drop before target.
        Detection: Torque stall < 80% target.
        """
        if state.turns < self.scenario.stripped_thread_turn:
            return

        if not state.is_stripped:
            state.is_stripped = True

        excess = state.turns - self.scenario.stripped_thread_turn
        severity = self.scenario.stripped_thread_severity

        # Torque drops as threads strip — exponential decay
        decay = 1.0 - severity * (1.0 - np.exp(-excess * 5.0))
        engine.torque_model.set_fault_multiplier(max(decay, 0.2))

    def _connection_jump(self, engine, state):
        if state.turns < self.scenario.connection_jump_turn:
            return
        if 'jump_applied' not in self._fault_state:
            self._fault_state['jump_applied'] = False
        if not self._fault_state['jump_applied']:
            turn_distance = state.turns - self.scenario.connection_jump_turn
            if turn_distance < 0.1:
                spike = 1.0 + 5.0 * self.scenario.connection_jump_severity * np.exp(-turn_distance * 50.0)
                engine.torque_model.set_fault_multiplier(spike)
            else:
                self._fault_state['jump_applied'] = True
                engine.torque_model.set_fault_multiplier(0.8)

    def _washout(self, engine, state, dt):
        from physics_engine import ConnectionState
        if state.connection_state not in (ConnectionState.HOLD, ConnectionState.COMPLETE):
            return
        if 'washout_start_torque' not in self._fault_state:
            self._fault_state['washout_start_torque'] = state.torque_ftlbs
            self._fault_state['washout_time'] = 0.0
        self._fault_state['washout_time'] += dt
        decay = np.exp(-self.scenario.washout_leak_rate * self._fault_state['washout_time'])
        engine.torque_model.set_fault_multiplier(decay)

    def _apply_staged_fault(self, engine, state, fault_type, params, dt):
        if fault_type == 'mild_galling':
            rate = params.get('rate', 1.0)
            key = f'staged_galling_{fault_type}'
            if key not in self._fault_state:
                self._fault_state[key] = 0.0
            self._fault_state[key] += rate * dt * 0.1
            engine.torque_model.set_fault_multiplier(1.0 + self._fault_state[key])
        elif fault_type == 'severe_galling':
            rate = params.get('rate', 3.0)
            key = f'staged_galling_{fault_type}'
            if key not in self._fault_state:
                self._fault_state[key] = self._fault_state.get('staged_galling_mild_galling', 0.0)
            self._fault_state[key] += rate * dt * 0.1
            engine.torque_model.set_fault_multiplier(1.0 + self._fault_state[key])
        elif fault_type == 'seizure':
            multiplier = params.get('multiplier', 4.0)
            engine.torque_model.set_fault_multiplier(multiplier)
            state.is_stalled = True

    def reset(self):
        self._fault_state = {}


# ═══════════════════════════════════════════════════════════════════
# Pipe Selection Helpers
# ═══════════════════════════════════════════════════════════════════

def _get_pipes_by_category(category: ConnectionCategory) -> List[str]:
    """Get pipe names matching a connection category."""
    return [name for name, pipe in ALL_CONNECTIONS.items()
            if pipe.connection_category == category]

def _get_ltc_stc_pipes() -> List[str]:
    return [name for name, pipe in PIPE_CATALOG.items()
            if pipe.connection_category in (ConnectionCategory.API_8RD_LTC, ConnectionCategory.API_8RD_STC)]

def _get_btc_pipes() -> List[str]:
    return [name for name, pipe in PIPE_CATALOG.items()
            if pipe.connection_category == ConnectionCategory.API_BUTTRESS]

def _get_premium_pipes() -> List[str]:
    return list(PREMIUM_CATALOG.keys())

def _get_drill_pipe_pipes() -> List[str]:
    return list(DRILL_PIPE_CATALOG.keys())

def _get_small_casing_pipes() -> List[str]:
    """Tubing-sized pipes (OD < 5.5")."""
    return [name for name, pipe in PIPE_CATALOG.items() if pipe.od_inches <= 5.5]


class ScenarioGenerator:
    """Generates randomized scenarios matching reference Section 6.2 distribution."""

    def __init__(self, seed: int = 42,
                 base_config: Optional[SimConfig] = None):
        self.rng = np.random.default_rng(seed)
        self.base_config = base_config or SimConfig()

        # Section 6.2 target distribution
        self.weights = {
            ScenarioType.NORMAL_CASING_LTC: 0.25,
            ScenarioType.NORMAL_CASING_BTC: 0.15,
            ScenarioType.NORMAL_CASING_PREMIUM: 0.10,
            ScenarioType.NORMAL_DRILL_PIPE: 0.15,
            ScenarioType.NORMAL_TUBING: 0.02,
            ScenarioType.FULL_CYCLE: 0.08,
            ScenarioType.CROSS_THREAD: 0.05,
            ScenarioType.GALLING: 0.04,
            ScenarioType.OVER_TORQUE: 0.03,
            ScenarioType.UNDER_TORQUE: 0.03,
            ScenarioType.STALL: 0.02,
            ScenarioType.WRONG_COMPOUND: 0.02,
            ScenarioType.MISALIGNED_STABBING: 0.02,
            ScenarioType.STRIPPED_THREAD: 0.01,
            ScenarioType.MULTI_CONNECTION: 0.01,
            ScenarioType.STICK_SLIP: 0.01,
            ScenarioType.WASHOUT: 0.005,
            ScenarioType.CONNECTION_JUMP: 0.005,
        }

    def generate_batch(self, n: int,
                       pipe_names: Optional[List[str]] = None,
                       machine_types: Optional[List[MachineType]] = None
                       ) -> List[ConnectionScenario]:
        types = list(self.weights.keys())
        probs = np.array([self.weights[t] for t in types])
        probs /= probs.sum()

        scenarios = []
        for i in range(n):
            stype = types[self.rng.choice(len(types), p=probs)]
            seed = int(self.rng.integers(0, 2**31))

            # Select pipe and machine based on scenario type
            pipe_name, machine = self._select_pipe_and_machine(stype, pipe_names, machine_types)
            pipe = ALL_CONNECTIONS[pipe_name]

            cfg = self.base_config.randomize(np.random.default_rng(seed))
            cfg.machine_type = machine
            cfg.apply_machine_noise_profile()

            scenario = self._build_scenario(stype, pipe, cfg, seed, machine)
            scenarios.append(scenario)

        return scenarios

    def generate_one(self, scenario_type: ScenarioType,
                     pipe_name: str = "7in_23lb_N80_LTC",
                     machine_type: Optional[MachineType] = None,
                     seed: Optional[int] = None) -> ConnectionScenario:
        pipe = ALL_CONNECTIONS[pipe_name]
        seed = seed or int(self.rng.integers(0, 2**31))
        machine = machine_type or MachineType.TOP_DRIVE
        cfg = self.base_config.randomize(np.random.default_rng(seed))
        cfg.machine_type = machine
        cfg.apply_machine_noise_profile()
        return self._build_scenario(scenario_type, pipe, cfg, seed, machine)

    def _select_pipe_and_machine(self, stype: ScenarioType,
                                  pipe_filter: Optional[List[str]],
                                  machine_filter: Optional[List[MachineType]]
                                  ) -> Tuple[str, MachineType]:
        """Select appropriate pipe and machine based on scenario type."""

        if stype == ScenarioType.NORMAL_CASING_LTC:
            pipes = _get_ltc_stc_pipes()
            machines = [MachineType.TOP_DRIVE, MachineType.POWER_TONG]
        elif stype == ScenarioType.NORMAL_CASING_BTC:
            pipes = _get_btc_pipes()
            machines = [MachineType.TOP_DRIVE, MachineType.POWER_TONG]
        elif stype == ScenarioType.NORMAL_CASING_PREMIUM:
            pipes = _get_premium_pipes()
            machines = [MachineType.TOP_DRIVE, MachineType.POWER_TONG]
        elif stype == ScenarioType.NORMAL_DRILL_PIPE:
            pipes = _get_drill_pipe_pipes()
            machines = [MachineType.IRON_ROUGHNECK, MachineType.TOP_DRIVE]
        elif stype == ScenarioType.NORMAL_TUBING:
            pipes = _get_small_casing_pipes()
            machines = [MachineType.POWER_TONG]
        else:
            # Fault/special scenarios: use any pipe and primary machine types
            all_pipes = list(ALL_CONNECTIONS.keys())
            pipes = all_pipes
            machines = [MachineType.TOP_DRIVE, MachineType.IRON_ROUGHNECK,
                       MachineType.POWER_TONG, MachineType.BUCKING_UNIT]

        # Apply filters
        if pipe_filter:
            pipes = [p for p in pipe_filter if p in ALL_CONNECTIONS]
        if machine_filter:
            machines = [m for m in machine_filter if m in machines] or machine_filter

        if not pipes:
            pipes = list(PIPE_CATALOG.keys())
        if not machines:
            machines = [MachineType.TOP_DRIVE]

        pipe_name = self.rng.choice(pipes)
        machine = self.rng.choice(machines)
        return pipe_name, machine

    def _build_scenario(self, stype: ScenarioType, pipe: PipeSpec,
                        cfg: SimConfig, seed: int,
                        machine: MachineType) -> ConnectionScenario:
        rng = np.random.default_rng(seed)

        rpm = rng.uniform(*cfg.rand_rpm_setpoint)
        breakout_rpm = rng.uniform(8.0, 15.0)

        compound_name = rng.choice(list(COMPOUND_CATALOG.keys()))
        cfg.compound = COMPOUND_CATALOG[compound_name]

        string_length = rng.uniform(*cfg.rand_string_length)
        string_joints = int(rng.integers(1, 4))
        cfg.pipe_string.length_ft = string_length
        cfg.pipe_string.num_joints = string_joints

        scenario = ConnectionScenario(
            scenario_type=stype, pipe=pipe, config=cfg,
            machine_type=machine, rpm_setpoint=rpm,
            breakout_rpm=breakout_rpm, seed=seed,
            compound_name=compound_name,
            string_length_ft=string_length,
            string_num_joints=string_joints,
        )

        # ─── Normal Scenarios ────────────────────────────────

        if stype in (ScenarioType.NORMAL_CASING_LTC, ScenarioType.NORMAL_CASING_BTC,
                     ScenarioType.NORMAL_CASING_PREMIUM, ScenarioType.NORMAL_DRILL_PIPE,
                     ScenarioType.NORMAL_TUBING):
            scenario.target_torque = pipe.optimum_torque_ftlbs * rng.uniform(0.95, 1.05)
            scenario.label = f"Normal {stype.value}: {pipe.name} [{machine.value}] @ {rpm:.0f} RPM"

        elif stype == ScenarioType.NORMAL_BREAKOUT:
            scenario.label = f"Normal breakout: {pipe.name} [{machine.value}]"

        elif stype == ScenarioType.FULL_CYCLE:
            scenario.target_torque = pipe.optimum_torque_ftlbs * rng.uniform(0.95, 1.05)
            scenario.label = f"Full cycle: {pipe.name} [{machine.value}] @ {rpm:.0f} RPM"

        # ─── Fault Scenarios (Section 4.1.3) ──────────────────

        elif stype == ScenarioType.CROSS_THREAD:
            scenario.cross_thread_turns = rng.uniform(0.5, 2.0)
            scenario.target_torque = pipe.optimum_torque_ftlbs
            scenario.label = f"Cross-thread @ {scenario.cross_thread_turns:.1f} turns: {pipe.name}"

        elif stype == ScenarioType.GALLING:
            scenario.galling_onset_turns = pipe.turns_to_shoulder * rng.uniform(0.5, 0.9)
            scenario.galling_rate = rng.uniform(1.5, 3.0)
            scenario.target_torque = pipe.optimum_torque_ftlbs
            scenario.label = f"Galling @ {scenario.galling_onset_turns:.1f} turns: {pipe.name}"

        elif stype == ScenarioType.OVER_TORQUE:
            scenario.over_torque_factor = rng.uniform(1.15, 1.40)
            scenario.target_torque = pipe.optimum_torque_ftlbs * scenario.over_torque_factor
            scenario.label = f"Over-torque ({scenario.over_torque_factor:.0%}): {pipe.name}"

        elif stype == ScenarioType.UNDER_TORQUE:
            scenario.stall_pressure_limit = cfg.operating_pressure_psi * rng.uniform(0.3, 0.6)
            scenario.target_torque = pipe.optimum_torque_ftlbs
            scenario.label = f"Under-torque: {pipe.name}"

        elif stype == ScenarioType.STALL:
            scenario.stall_pressure_limit = cfg.operating_pressure_psi * rng.uniform(0.1, 0.3)
            scenario.target_torque = pipe.optimum_torque_ftlbs
            scenario.rpm_setpoint = rng.uniform(3.0, 8.0)
            scenario.label = f"Stall: {pipe.name}"

        elif stype == ScenarioType.WRONG_COMPOUND:
            # Shift friction factor significantly — shoulder appears at wrong position
            scenario.wrong_compound_kf_shift = rng.choice([-0.4, -0.3, 0.3, 0.5, 0.6])
            scenario.target_torque = pipe.optimum_torque_ftlbs
            direction = "low" if scenario.wrong_compound_kf_shift < 0 else "high"
            scenario.label = f"Wrong compound ({direction} friction): {pipe.name}"

        elif stype == ScenarioType.MISALIGNED_STABBING:
            scenario.misaligned_severity = rng.uniform(0.3, 1.0)
            scenario.target_torque = pipe.optimum_torque_ftlbs
            scenario.label = f"Misaligned stab (sev={scenario.misaligned_severity:.1f}): {pipe.name}"

        elif stype == ScenarioType.STRIPPED_THREAD:
            # Stripping occurs during power-tight zone
            scenario.stripped_thread_turn = pipe.turns_to_shoulder + pipe.delta_turns * rng.uniform(0.2, 0.7)
            scenario.stripped_thread_severity = rng.uniform(0.4, 0.9)
            scenario.target_torque = pipe.optimum_torque_ftlbs
            scenario.label = f"Stripped thread @ {scenario.stripped_thread_turn:.1f} turns: {pipe.name}"

        elif stype == ScenarioType.MULTI_CONNECTION:
            scenario.num_connections = int(rng.integers(3, 8))
            scenario.target_torque = pipe.optimum_torque_ftlbs * rng.uniform(0.95, 1.05)
            scenario.config.total_time = scenario.num_connections * 45.0
            scenario.label = f"Multi-connection ({scenario.num_connections}x): {pipe.name}"

        elif stype == ScenarioType.STICK_SLIP:
            scenario.stick_slip_enabled = True
            scenario.stick_slip_critical_rpm = rng.uniform(3.0, 8.0)
            scenario.rpm_setpoint = rng.uniform(3.0, 6.0)
            scenario.target_torque = pipe.optimum_torque_ftlbs
            cfg.pipe_string.length_ft = rng.uniform(60.0, 120.0)
            cfg.pipe_string.num_joints = int(rng.integers(2, 5))
            scenario.label = f"Stick-slip @ {scenario.rpm_setpoint:.0f} RPM: {pipe.name}"

        elif stype == ScenarioType.CONNECTION_JUMP:
            scenario.connection_jump_turn = pipe.turns_to_shoulder * rng.uniform(0.3, 0.7)
            scenario.connection_jump_severity = rng.uniform(0.3, 1.0)
            scenario.target_torque = pipe.optimum_torque_ftlbs
            scenario.label = f"Conn jump @ {scenario.connection_jump_turn:.1f} turns: {pipe.name}"

        elif stype == ScenarioType.WASHOUT:
            scenario.washout_enabled = True
            scenario.washout_leak_rate = rng.uniform(0.1, 0.5)
            scenario.target_torque = pipe.optimum_torque_ftlbs
            scenario.label = f"Washout: {pipe.name}"

        elif stype == ScenarioType.STAGED_FAULT:
            galling_start = pipe.turns_to_shoulder * rng.uniform(0.6, 0.8)
            severe_turn = pipe.turns_to_shoulder * rng.uniform(0.9, 1.0)
            seizure_turn = pipe.turns_to_shoulder + pipe.delta_turns * rng.uniform(0.3, 0.7)
            scenario.staged_faults = [
                (galling_start, 'mild_galling', {'rate': rng.uniform(0.5, 1.0)}),
                (severe_turn, 'severe_galling', {'rate': rng.uniform(2.0, 4.0)}),
                (seizure_turn, 'seizure', {'multiplier': rng.uniform(3.0, 5.0)}),
            ]
            scenario.target_torque = pipe.optimum_torque_ftlbs
            scenario.label = f"Staged fault: {pipe.name}"

        return scenario

    def get_distribution_summary(self) -> str:
        lines = ["Scenario Distribution (Section 6.2 targets):"]
        for stype, weight in sorted(self.weights.items(), key=lambda x: -x[1]):
            lines.append(f"  {stype.value:30s}  {weight:5.1%}")
        lines.append(f"\nConnection catalogs:")
        lines.append(f"  API 8-round (LTC/STC): {len(_get_ltc_stc_pipes())} types")
        lines.append(f"  API Buttress (BTC):    {len(_get_btc_pipes())} types")
        lines.append(f"  Premium shouldered:    {len(_get_premium_pipes())} types")
        lines.append(f"  Drill pipe (NC/IF):    {len(_get_drill_pipe_pipes())} types")
        lines.append(f"  Total connections:     {len(ALL_CONNECTIONS)} types")
        lines.append(f"\nCompound catalog: {len(COMPOUND_CATALOG)} types")
        lines.append(f"  " + ", ".join(COMPOUND_CATALOG.keys()))
        lines.append(f"\nMachine types: {', '.join(m.value for m in MachineType)}")
        return "\n".join(lines)
