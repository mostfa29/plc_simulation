"""
Simulation Runner: The Orchestrator
=====================================
Ties together physics, sensors, Modbus, and scenario execution.

Enhanced with:
  - Real-time clock synchronization (drift-compensated)
  - PLC scan rate buffer (physics 100Hz -> Modbus at scan rate)
  - Dual-rate logging (physics rate vs CSV output rate)
  - Connection sequencing (field-realistic patterns)
  - FaultInjector delegation (clean fault injection)
  - Warm-up sequence (cold start oil circulation, hydraulic machines only)
  - Machine-type-aware initialization and output columns
  - Extended register map output (fault_code, peak_torque, hookload,
    shoulder_torque, slope, connection_count)
"""
import time
import csv
import logging
import numpy as np
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any, Generator

from config import SimConfig, PipeSpec, MachineType
from physics_engine import PhysicsEngine, PhysicsState, ConnectionState, OperatingMode
from sensor_models import SensorModel, SensorReading
from modbus_server import ModbusTCPServer
from scenario import ConnectionScenario, ScenarioType, FaultInjector

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Helper Classes
# ═══════════════════════════════════════════════════════════════════

class RealtimeClock:
    """Wall-clock synchronization for Modbus mode.

    Tracks cumulative drift and uses high-resolution timer.
    """

    def __init__(self):
        self._wall_start = time.perf_counter()
        self._sim_time = 0.0

    def sync(self, sim_time: float):
        """Sleep if simulation is ahead of wall clock."""
        self._sim_time = sim_time
        elapsed_wall = time.perf_counter() - self._wall_start
        ahead = sim_time - elapsed_wall
        if ahead > 0.001:
            time.sleep(ahead * 0.95)

    def reset(self):
        self._wall_start = time.perf_counter()
        self._sim_time = 0.0


class PLCScanBuffer:
    """Samples physics-rate readings at PLC scan rate for Modbus.

    Physics runs at 100Hz; real PLC reads sensors at 5-20Hz.
    This buffer ensures Modbus clients see scan-rate timing.
    """

    def __init__(self, scan_rate_hz: float = 10.0):
        self.scan_period = 1.0 / scan_rate_hz
        self._accumulator = 0.0
        self._last_reading = None

    def update(self, reading, dt: float):
        """Feed a physics-rate reading, returns reading if scan period elapsed."""
        self._last_reading = reading
        self._accumulator += dt
        if self._accumulator >= self.scan_period:
            self._accumulator -= self.scan_period
            return reading
        return None

    def reset(self):
        self._accumulator = 0.0
        self._last_reading = None


class DualRateLogger:
    """Decimation: physics at 100Hz internally, CSV at configurable rate."""

    def __init__(self, output_rate_hz: float = 100.0, physics_rate_hz: float = 100.0):
        self.decimation = max(1, int(physics_rate_hz / output_rate_hz))
        self._counter = 0

    def should_log(self) -> bool:
        self._counter += 1
        return self._counter % self.decimation == 0

    def reset(self):
        self._counter = 0


class ConnectionSequencer:
    """Field-realistic connection sequencing patterns."""

    def __init__(self, pattern: str = "single", rng: np.random.Generator = None):
        self.pattern = pattern
        self.rng = rng or np.random.default_rng()

    def next_delay(self) -> float:
        """Time between connections (seconds)."""
        if self.pattern == "single":
            return self.rng.uniform(15.0, 45.0)
        elif self.pattern == "doubles":
            return self.rng.uniform(5.0, 15.0)
        elif self.pattern == "casing":
            return self.rng.uniform(30.0, 90.0)
        return self.rng.uniform(10.0, 30.0)


# ═══════════════════════════════════════════════════════════════════
# Simulation Result
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SimulationResult:
    """Complete output from one simulation run."""
    scenario: ConnectionScenario
    timestamps: list
    ground_truth: list
    sensor_data: list
    events: list
    metadata: dict


# ═══════════════════════════════════════════════════════════════════
# Ground Truth / Sensor Data Row Builders
# ═══════════════════════════════════════════════════════════════════

def _build_ground_truth_row(sim_time: float, state: PhysicsState) -> dict:
    """Build a single ground truth data row from PhysicsState."""
    return {
        'time': sim_time,
        'turns': state.turns,
        'rpm': state.rpm,
        'torque_ftlbs': state.torque_ftlbs,
        'pressure_psi': state.pressure_psi,
        'oil_temp_f': state.oil_temp_f,
        'encoder_counts': state.encoder_counts,
        'valve_command_pct': state.valve_command_pct,
        'connection_state': state.connection_state.value,
        'operating_mode': state.operating_mode.value,
        'pid_setpoint': state.pid_setpoint,
        'pid_error': state.pid_error,
        'pid_output': state.pid_output,
        'target_torque': state.target_torque_ftlbs,
        # Extended physics columns
        'valve_spool_pct': state.valve_spool_pct,
        'oil_viscosity_cst': state.oil_viscosity_cst,
        'motor_mech_efficiency': state.motor_mech_efficiency,
        'string_twist_deg': state.string_twist_deg,
        'connection_rpm': state.connection_rpm,
        'motor_torque_ftlbs': state.motor_torque_ftlbs,
        'thread_damage': state.thread_damage,
        'compound_friction_kf': state.compound_friction_kf,
        'leakage_flow_gpm': state.leakage_flow_gpm,
        'pump_ripple_psi': state.pump_ripple_psi,
        'manifold_temp_f': state.manifold_temp_f,
        'motor_case_temp_f': state.motor_case_temp_f,
        # New fields matching expanded register map (Section 5.1.1)
        'fault_code': state.fault_code,
        'peak_torque_ftlbs': state.peak_torque_ftlbs,
        'hookload_klbs': state.hookload_klbs,
        'shoulder_torque_ftlbs': state.shoulder_torque_ftlbs,
        'slope_dT_dN': state.slope_dT_dN,
        'connection_count': state.connection_count,
        # Machine-specific columns
        'machine_phase': state.machine_phase,
        'vfd_frequency_hz': state.vfd_frequency_hz,
        'vfd_current_pct': state.vfd_current_pct,
        'motor_speed_rpm': state.motor_speed_rpm,
    }


def _build_sensor_data_row(sim_time: float, reading: SensorReading) -> dict:
    """Build a single sensor data row from SensorReading."""
    return {
        'time': sim_time,
        'encoder_counts': reading.encoder_counts,
        'rpm': reading.rpm,
        'torque_ftlbs': reading.torque_ftlbs,
        'pressure_psi': reading.pressure_psi,
        'oil_temp_f': reading.oil_temp_f,
        'pid_setpoint': reading.pid_setpoint,
        'pid_error': reading.pid_error,
        'pid_output': reading.pid_output,
        'operating_mode': reading.operating_mode,
        'connection_state': reading.connection_state,
        'target_torque': reading.target_torque,
        'turns': reading.turns,
        # New fields matching expanded register map (Section 5.1.1)
        'fault_code': reading.fault_code,
        'peak_torque': reading.peak_torque,
        'hookload_klbs': reading.hookload_klbs,
        'shoulder_torque': reading.shoulder_torque,
        'slope_dT_dN': reading.slope_dT_dN,
        'connection_count': reading.connection_count,
    }


# ═══════════════════════════════════════════════════════════════════
# Simulation Runner
# ═══════════════════════════════════════════════════════════════════

class SimulationRunner:
    """Runs simulation scenarios and produces training data.

    Two modes:
      1. Batch mode: Run scenario, collect all data, return result
      2. Real-time mode: Run with Modbus server, synced to wall clock
    """

    def __init__(self, realtime: bool = False,
                 modbus_host: str = '0.0.0.0', modbus_port: int = 5020,
                 enable_modbus: bool = False,
                 csv_output_rate_hz: float = 100.0):
        self.realtime = realtime
        self.enable_modbus = enable_modbus or realtime
        self.modbus_host = modbus_host
        self.modbus_port = modbus_port
        self.csv_output_rate_hz = csv_output_rate_hz
        self._modbus_server: Optional[ModbusTCPServer] = None

    def run(self, scenario: ConnectionScenario) -> SimulationResult:
        """Execute a complete scenario simulation."""
        cfg = scenario.config
        pipe = scenario.pipe
        rng = np.random.default_rng(scenario.seed)

        # Initialize subsystems
        engine = PhysicsEngine(cfg, pipe, rng)
        sensors = SensorModel(cfg, rng)

        # Configure connection
        target = scenario.target_torque or pipe.optimum_torque_ftlbs
        engine.configure_connection(
            rpm=scenario.rpm_setpoint,
            target_torque=target,
            breakout_rpm=scenario.breakout_rpm
        )

        # Start Modbus if enabled
        if self.enable_modbus:
            self._modbus_server = ModbusTCPServer(cfg, self.modbus_host, self.modbus_port)
            self._modbus_server.start()

        try:
            result = self._execute_scenario(engine, sensors, scenario, rng)
        finally:
            if self._modbus_server:
                self._modbus_server.stop()

        return result

    def _execute_scenario(self, engine: PhysicsEngine, sensors: SensorModel,
                          scenario: ConnectionScenario,
                          rng: np.random.Generator) -> SimulationResult:
        """Core simulation loop with enhanced orchestration."""
        cfg = scenario.config
        dt = cfg.physics_dt

        # Data collection
        timestamps = []
        ground_truth = []
        sensor_data_list = []
        events = []

        # Helper objects
        fault_injector = FaultInjector(scenario, rng)
        scan_buffer = PLCScanBuffer(scan_rate_hz=1000.0 / cfg.pid_scan_rate_ms)
        rate_logger = DualRateLogger(
            output_rate_hz=self.csv_output_rate_hz if not self.realtime else 100.0
        )
        rt_clock = RealtimeClock() if self.realtime else None

        # State tracking
        prev_state = ConnectionState.IDLE
        connection_count = 0
        max_connections = scenario.num_connections
        sim_phase = 'idle'
        hold_timer = 0.0
        between_timer = 0.0

        # Optional warm-up for cold start (hydraulic machines only)
        if scenario.scenario_type == ScenarioType.COLD_START:
            if cfg.machine_type != MachineType.TOP_DRIVE:
                self._run_warmup(engine, duration_s=15.0)

        # Start first connection
        if scenario.scenario_type == ScenarioType.NORMAL_BREAKOUT:
            engine.start_breakout()
            sim_phase = 'breakout'
        else:
            engine.start_threading()
            sim_phase = 'threading'
            connection_count = 1

        total_steps = int(cfg.total_time / dt)

        for step_i in range(total_steps):
            sim_time = step_i * dt

            # ─── Fault Injection ──────────────────────────────
            fault_injector.apply(engine, dt)

            # ─── Physics Step ─────────────────────────────────
            state = engine.step(dt)

            # ─── Sensor Corruption ────────────────────────────
            reading = sensors.corrupt(state, dt)

            # ─── State Transition Events ──────────────────────
            if state.connection_state != prev_state:
                events.append({
                    'time': sim_time,
                    'event': 'state_change',
                    'from': prev_state.name,
                    'to': state.connection_state.name,
                    'turns': state.turns,
                    'torque': state.torque_ftlbs,
                    'connection': connection_count,
                })
                prev_state = state.connection_state

            # ─── Fault Events ─────────────────────────────────
            if state.is_over_torque and not any(e.get('event') == 'over_torque' for e in events):
                events.append({
                    'time': sim_time, 'event': 'over_torque',
                    'torque': state.torque_ftlbs, 'max': scenario.pipe.max_torque_ftlbs,
                })
            if state.is_cross_threaded and not any(e.get('event') == 'cross_thread' for e in events):
                events.append({
                    'time': sim_time, 'event': 'cross_thread',
                    'turns': state.turns, 'torque': state.torque_ftlbs,
                })
            if state.is_stick_slip and not any(e.get('event') == 'stick_slip' for e in events):
                events.append({
                    'time': sim_time, 'event': 'stick_slip',
                    'frequency_hz': state.stick_slip_frequency_hz,
                    'rpm': state.rpm,
                })
            if state.is_galled and not any(e.get('event') == 'galling' for e in events):
                events.append({
                    'time': sim_time, 'event': 'galling',
                    'turns': state.turns, 'torque': state.torque_ftlbs,
                })
            if state.is_stripped and not any(e.get('event') == 'stripped_thread' for e in events):
                events.append({
                    'time': sim_time, 'event': 'stripped_thread',
                    'turns': state.turns, 'torque': state.torque_ftlbs,
                })
            if state.is_misaligned and not any(e.get('event') == 'misaligned_stab' for e in events):
                events.append({
                    'time': sim_time, 'event': 'misaligned_stab',
                    'turns': state.turns, 'torque': state.torque_ftlbs,
                })
            if state.is_wrong_compound and not any(e.get('event') == 'wrong_compound' for e in events):
                events.append({
                    'time': sim_time, 'event': 'wrong_compound',
                    'turns': state.turns, 'torque': state.torque_ftlbs,
                    'kf': state.compound_friction_kf,
                })

            # ─── Shoulder Detection Event ─────────────────────
            if (state.shoulder_torque_ftlbs > 0 and
                    not any(e.get('event') == 'shoulder_detected' for e in events)):
                events.append({
                    'time': sim_time, 'event': 'shoulder_detected',
                    'turns': state.turns,
                    'shoulder_torque': state.shoulder_torque_ftlbs,
                })

            # ─── Modbus Update (via scan buffer) ──────────────
            if self._modbus_server:
                scan_reading = scan_buffer.update(reading, dt)
                if scan_reading is not None:
                    self._modbus_server.update_from_reading(scan_reading)

            # ─── Data Collection (at output rate) ─────────────
            if rate_logger.should_log():
                timestamps.append(sim_time)
                ground_truth.append(_build_ground_truth_row(sim_time, state))
                sensor_data_list.append(_build_sensor_data_row(sim_time, reading))

            # ─── Scenario Phase Management ────────────────────
            if state.connection_state == ConnectionState.COMPLETE:
                if sim_phase == 'threading':
                    if scenario.scenario_type == ScenarioType.FULL_CYCLE:
                        sim_phase = 'holding'
                        hold_timer = sim_time
                    elif scenario.scenario_type == ScenarioType.MULTI_CONNECTION:
                        if connection_count < max_connections:
                            sim_phase = 'between'
                            between_timer = sim_time
                        else:
                            break
                    else:
                        break
                elif sim_phase == 'breakout':
                    if scenario.scenario_type == ScenarioType.MULTI_CONNECTION:
                        if connection_count < max_connections:
                            sim_phase = 'between'
                            between_timer = sim_time
                        else:
                            break
                    else:
                        break

            # Hold -> breakout
            if sim_phase == 'holding' and sim_time - hold_timer > rng.uniform(2.0, 5.0):
                engine.reset()
                engine.configure_connection(
                    rpm=scenario.rpm_setpoint,
                    target_torque=scenario.target_torque or scenario.pipe.optimum_torque_ftlbs,
                    breakout_rpm=scenario.breakout_rpm
                )
                engine.state.turns = engine.torque_model.scaled_turns + engine.torque_model.scaled_delta
                engine.start_breakout()
                sim_phase = 'breakout'
                prev_state = ConnectionState.BREAKOUT

            # Between connections -> next makeup
            if sim_phase == 'between' and sim_time - between_timer > rng.uniform(3.0, 8.0):
                engine.reset()
                sensors.reset()
                fault_injector.reset()
                engine.configure_connection(
                    rpm=scenario.rpm_setpoint,
                    target_torque=scenario.target_torque or scenario.pipe.optimum_torque_ftlbs,
                    breakout_rpm=scenario.breakout_rpm
                )
                engine.start_threading()
                connection_count += 1
                sim_phase = 'threading'
                prev_state = ConnectionState.APPROACH

            # ─── Real-time Pacing ─────────────────────────────
            if rt_clock:
                rt_clock.sync(sim_time)

            # ─── Fault termination ────────────────────────────
            if state.is_over_torque or state.is_over_temp:
                events.append({
                    'time': sim_time, 'event': 'emergency_stop',
                    'reason': 'over_torque' if state.is_over_torque else 'over_temp',
                })
                # Wind down for 2 seconds
                for _ in range(int(2.0 / dt)):
                    engine.state.operating_mode = OperatingMode.IDLE
                    engine.state.valve_command_pct = 0
                    state = engine.step(dt)
                    reading = sensors.corrupt(state, dt)
                    if rate_logger.should_log():
                        timestamps.append(state.time)
                        ground_truth.append(_build_ground_truth_row(state.time, state))
                        sensor_data_list.append(_build_sensor_data_row(state.time, reading))
                break

        # ─── Metadata ────────────────────────────────────────
        metadata = {
            'scenario_type': scenario.scenario_type.value,
            'machine_type': scenario.machine_type.value,
            'pipe': scenario.pipe.name,
            'connection_type': scenario.pipe.connection_type,
            'seed': scenario.seed,
            'total_samples': len(timestamps),
            'duration_s': timestamps[-1] if timestamps else 0,
            'peak_torque_ftlbs': max((g['torque_ftlbs'] for g in ground_truth), default=0),
            'peak_pressure_psi': max((g['pressure_psi'] for g in ground_truth), default=0),
            'peak_rpm': max((g['rpm'] for g in ground_truth), default=0),
            'final_temp_f': ground_truth[-1]['oil_temp_f'] if ground_truth else 0,
            'shoulder_torque_ftlbs': max((g['shoulder_torque_ftlbs'] for g in ground_truth), default=0),
            'max_slope_dT_dN': max((g['slope_dT_dN'] for g in ground_truth), default=0),
            'max_hookload_klbs': max((g['hookload_klbs'] for g in ground_truth), default=0),
            'fault_code': ground_truth[-1]['fault_code'] if ground_truth else 0,
            'num_events': len(events),
            'connections_completed': connection_count,
            'has_fault': any(e['event'] in (
                'over_torque', 'cross_thread', 'galling', 'stick_slip',
                'stripped_thread', 'misaligned_stab', 'wrong_compound',
                'emergency_stop',
            ) for e in events),
            'label': scenario.label,
            'compound': scenario.compound_name,
            'string_length_ft': scenario.string_length_ft,
        }

        return SimulationResult(
            scenario=scenario,
            timestamps=timestamps,
            ground_truth=ground_truth,
            sensor_data=sensor_data_list,
            events=events,
            metadata=metadata,
        )

    def _run_warmup(self, engine: PhysicsEngine, duration_s: float = 15.0):
        """Simulate pump warm-up and oil circulation (cold start).

        Only applicable to hydraulic machines (not AC motor top drives).
        Runs hydraulics at low speed with no load to warm oil.
        """
        dt = engine.cfg.physics_dt
        steps = int(duration_s / dt)

        for _ in range(steps):
            # Low-speed circulation: 10% command, no load
            viscosity = engine.oil_model.viscosity_at_temp(engine.state.oil_temp_f)
            if hasattr(engine.drive, 'step'):
                try:
                    engine.drive.step(10.0, 0.0, viscosity, dt)
                except TypeError:
                    # AC motor drive has different signature; skip warmup
                    break
            Q_pump = 0.5  # Approximate pump loss HP
            engine.thermal.step(Q_pump, 0.0, 0.0, dt)
            engine.state.oil_temp_f = engine.thermal.get_oil_temp()

    def save_csv(self, result: SimulationResult, filepath: str,
                 data_type: str = 'sensor'):
        """Save simulation data to CSV."""
        data = result.sensor_data if data_type == 'sensor' else result.ground_truth
        if not data:
            logger.warning("No data to save")
            return

        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = list(data[0].keys())

        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)

        logger.info(f"Saved {len(data)} rows to {filepath} ({data_type})")

    def save_events(self, result: SimulationResult, filepath: str):
        """Save event log to CSV."""
        if not result.events:
            return

        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = set()
        for e in result.events:
            fieldnames.update(e.keys())
        fieldnames = sorted(fieldnames)

        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(result.events)
