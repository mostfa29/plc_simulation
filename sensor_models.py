"""
Sensor Models: Reality Layer
==============================
Transforms ground-truth physics into what the PLC actually measures.

Machine-type-specific noise profiles (Section 4.4):
  - Top Drive:       Torque from motor current calc (SNR 45-60 dB), VFD harmonics
  - Iron Roughneck:  Torque from pressure transducer (SNR 50-65 dB), pump ripple
  - Power Tong:      Torque from load cell (SNR 55-70 dB), arm compliance
  - Bucking Unit:    Calibrated load cell (SNR 60-75 dB), minimal noise

Noise sources per channel:
  1. White noise (thermal/electronic) — Gaussian
  2. Pink noise (1/f) — Voss-McCartney algorithm
  3. EMI (60 Hz) — power line pickup
  4. VFD switching noise — broadband spikes (top drive only)
  5. Pump ripple — at pump frequency (hydraulic machines only)
  6. Drift — temperature-dependent offset
  7. Quantization — ADC discretization (12-16 bit)
"""
import numpy as np
from dataclasses import dataclass
from typing import Optional

from config import SimConfig, MachineType, MACHINE_NOISE_PROFILES


class PinkNoiseGenerator:
    """Voss-McCartney algorithm for 1/f pink noise.
    O(1) per sample vs O(N) for FFT methods.
    """

    def __init__(self, num_sources: int = 16, rng: Optional[np.random.Generator] = None):
        self.rng = rng or np.random.default_rng()
        self.num_sources = num_sources
        self.sources = self.rng.standard_normal(num_sources)
        self.counter = 0

    def sample(self) -> float:
        self.counter += 1
        for i in range(self.num_sources):
            if self.counter % (1 << i) == 0:
                self.sources[i] = self.rng.standard_normal()
        return np.sum(self.sources) / np.sqrt(self.num_sources)

    def samples(self, n: int) -> np.ndarray:
        return np.array([self.sample() for _ in range(n)])


@dataclass
class SensorReading:
    """A single instant of all sensor readings after noise corruption.
    These are the values written into PLC registers.
    """
    encoder_counts: int = 0
    rpm: float = 0.0
    torque_ftlbs: float = 0.0
    pressure_psi: float = 0.0
    oil_temp_f: float = 0.0
    pid_setpoint: float = 0.0
    pid_error: float = 0.0
    pid_output: float = 0.0
    operating_mode: int = 0
    connection_state: int = 0
    pid_kp: float = 0.0
    pid_ki: float = 0.0
    target_torque: float = 0.0
    turns: float = 0.0

    # New fields matching expanded register map
    fault_code: int = 0
    peak_torque: float = 0.0
    hookload_klbs: float = 0.0
    shoulder_torque: float = 0.0
    slope_dT_dN: float = 0.0
    connection_count: int = 0


class SensorModel:
    """Applies realistic, machine-type-specific noise to ground-truth physics."""

    def __init__(self, cfg: SimConfig, rng: Optional[np.random.Generator] = None):
        self.cfg = cfg
        self.rng = rng or np.random.default_rng()
        self.machine_type = cfg.machine_type

        # Get machine-specific noise profile
        profile = MACHINE_NOISE_PROFILES.get(cfg.machine_type)
        if profile:
            torque_snr = profile.torque_snr_db
            pressure_snr = profile.pressure_snr_db
            temp_snr = profile.temperature_snr_db
            self._emi_amplitude = profile.emi_60hz_pct_fs / 100.0
        else:
            torque_snr = cfg.torque_snr_db
            pressure_snr = cfg.pressure_snr_db
            temp_snr = cfg.temp_snr_db
            self._emi_amplitude = cfg.emi_60hz_amplitude

        # Noise standard deviations from SNR
        self.pressure_noise_std = cfg.max_pressure_psi * 10 ** (-pressure_snr / 20)
        self.torque_noise_std = cfg.torque_cell_capacity_ftlbs * 10 ** (-torque_snr / 20)
        self.temp_noise_std = 200.0 * 10 ** (-temp_snr / 20)
        self.hookload_noise_std = 500.0 * 10 ** (-cfg.hookload_snr_db / 20) if cfg.hookload_snr_db > 0 else 0.0

        # RPM noise varies by machine type
        if profile:
            self.rpm_noise_std = profile.rpm_resolution * 1.5
        else:
            self.rpm_noise_std = 0.1

        # Pink noise generators (one per channel)
        self.pressure_pink = PinkNoiseGenerator(rng=self.rng)
        self.torque_pink = PinkNoiseGenerator(rng=self.rng)
        self.temp_pink = PinkNoiseGenerator(rng=self.rng)
        self.hookload_pink = PinkNoiseGenerator(rng=self.rng)
        self.rpm_pink = PinkNoiseGenerator(rng=self.rng)

        # EMI phase (shared across channels)
        self._emi_phase = self.rng.uniform(0, 2 * np.pi)

        # Drift accumulators
        self._pressure_drift = 0.0
        self._torque_drift = 0.0
        self._temp_drift = 0.0
        self._hookload_drift = 0.0

        # Pump ripple parameters (hydraulic machines only)
        self._pump_freq_hz = cfg.pump.num_pistons * cfg.pump.drive_rpm / 60.0
        self._pump_ripple_enabled = cfg.machine_type in (
            MachineType.IRON_ROUGHNECK, MachineType.POWER_TONG, MachineType.BUCKING_UNIT
        )

        # VFD noise (top drive only)
        self._vfd_noise_enabled = cfg.machine_type == MachineType.TOP_DRIVE
        self._vfd_noise_amplitude = cfg.vfd_noise_amplitude

        self._t = 0.0

    def corrupt(self, truth, dt: float) -> SensorReading:
        """Apply noise to ground-truth PhysicsState, return SensorReading."""
        self._t += dt
        reading = SensorReading()

        # ─── Encoder (digital, nearly ideal) ──────────────────
        jitter = self.rng.integers(-self.cfg.encoder_jitter_counts,
                                    self.cfg.encoder_jitter_counts + 1)
        reading.encoder_counts = truth.encoder_counts + jitter

        # ─── RPM (derived from encoder + pink noise) ─────────
        rpm_noise = self.rng.normal(0, self.rpm_noise_std)
        rpm_pink = self.rpm_pink.sample() * self.rpm_noise_std * 0.3
        reading.rpm = max(0, truth.rpm + rpm_noise + rpm_pink)

        # ─── Pressure (strain gauge transducer) ──────────────
        pressure_base = truth.pressure_psi
        # Add pump ripple for hydraulic machines
        if self._pump_ripple_enabled and pressure_base > 0:
            pump_ripple = 0.02 * pressure_base * np.sin(
                2 * np.pi * self._pump_freq_hz * self._t)
            pressure_base += pump_ripple
        reading.pressure_psi = self._apply_analog_noise(
            pressure_base,
            white_std=self.pressure_noise_std,
            pink_gen=self.pressure_pink,
            drift_state='pressure',
            full_scale=self.cfg.max_pressure_psi,
            dt=dt
        )
        reading.pressure_psi = max(0, reading.pressure_psi)

        # ─── Torque (method depends on machine type) ─────────
        torque_base = truth.torque_ftlbs
        # Creep under sustained load
        creep = truth.torque_ftlbs * self.cfg.torque_creep_pct * min(self._t / 60.0, 1.0)
        torque_base += creep

        # Top drive: torque from motor current — additional noise from VFD harmonics
        if self._vfd_noise_enabled:
            # VFD harmonics at switching frequency (~4-8 kHz, aliased)
            vfd_harm = self._vfd_noise_amplitude * self.cfg.torque_cell_capacity_ftlbs
            if self.rng.random() < 0.15:  # 15% chance per sample
                torque_base += self.rng.normal(0, vfd_harm)
            # Gear mesh noise (proportional to RPM)
            gear_mesh_freq = truth.rpm * self.cfg.top_drive.gear_ratio / 60.0
            if gear_mesh_freq > 0:
                gear_noise = 0.001 * self.cfg.torque_cell_capacity_ftlbs * np.sin(
                    2 * np.pi * gear_mesh_freq * self._t)
                torque_base += gear_noise

        reading.torque_ftlbs = self._apply_analog_noise(
            torque_base,
            white_std=self.torque_noise_std,
            pink_gen=self.torque_pink,
            drift_state='torque',
            full_scale=self.cfg.torque_cell_capacity_ftlbs,
            dt=dt
        )
        reading.torque_ftlbs = max(0, reading.torque_ftlbs)

        # ─── Temperature (RTD) ───────────────────────────────
        reading.oil_temp_f = self._apply_analog_noise(
            truth.oil_temp_f + self.cfg.temp_self_heat_f,
            white_std=self.temp_noise_std,
            pink_gen=self.temp_pink,
            drift_state='temp',
            full_scale=200.0,
            dt=dt
        )

        # ─── Hookload (top drive only) ───────────────────────
        if self.cfg.machine_type == MachineType.TOP_DRIVE and hasattr(truth, 'hookload_klbs'):
            reading.hookload_klbs = self._apply_analog_noise(
                truth.hookload_klbs,
                white_std=self.hookload_noise_std,
                pink_gen=self.hookload_pink,
                drift_state='hookload',
                full_scale=500.0,
                dt=dt
            )
        else:
            reading.hookload_klbs = 0.0

        # ─── Pass-through digital values (no noise) ──────────
        reading.pid_setpoint = truth.pid_setpoint
        reading.pid_error = truth.pid_error
        reading.pid_output = truth.pid_output
        reading.operating_mode = int(truth.operating_mode)
        reading.connection_state = int(truth.connection_state)
        reading.pid_kp = self.cfg.pid_kp
        reading.pid_ki = self.cfg.pid_ki
        reading.target_torque = truth.target_torque_ftlbs
        reading.turns = truth.turns

        # New digital fields
        reading.fault_code = truth.fault_code
        reading.peak_torque = truth.peak_torque_ftlbs
        reading.shoulder_torque = truth.shoulder_torque_ftlbs
        reading.slope_dT_dN = truth.slope_dT_dN
        reading.connection_count = truth.connection_count

        # ─── ADC Quantization ────────────────────────────────
        reading = self._quantize(reading)

        return reading

    def _apply_analog_noise(self, true_value: float, white_std: float,
                             pink_gen: PinkNoiseGenerator, drift_state: str,
                             full_scale: float, dt: float) -> float:
        value = true_value

        # 1. Drift (bounded random walk)
        drift_attr = f'_{drift_state}_drift'
        drift = getattr(self, drift_attr, 0.0)
        drift_step = self.rng.normal(0, full_scale * 0.0001 * np.sqrt(dt))
        drift = np.clip(drift + drift_step, -full_scale * 0.005, full_scale * 0.005)
        setattr(self, drift_attr, drift)
        value += drift

        # 2. White noise
        value += self.rng.normal(0, white_std)

        # 3. Pink noise (1/f, ~30% of white level)
        value += pink_gen.sample() * white_std * 0.3

        # 4. 60 Hz EMI (coherent across channels)
        emi = self._emi_amplitude * full_scale * np.sin(
            2 * np.pi * 60.0 * self._t + self._emi_phase
        )
        value += emi

        return value

    def _quantize(self, reading: SensorReading) -> SensorReading:
        bits = self.cfg.adc_bits
        levels = 2 ** bits

        if self.cfg.max_pressure_psi > 0:
            lsb_p = self.cfg.max_pressure_psi / levels
            reading.pressure_psi = round(reading.pressure_psi / lsb_p) * lsb_p

        if self.cfg.torque_cell_capacity_ftlbs > 0:
            lsb_t = self.cfg.torque_cell_capacity_ftlbs / levels
            reading.torque_ftlbs = round(reading.torque_ftlbs / lsb_t) * lsb_t

        lsb_temp = 300.0 / levels
        reading.oil_temp_f = round(reading.oil_temp_f / lsb_temp) * lsb_temp

        if reading.hookload_klbs > 0:
            lsb_h = 500.0 / levels
            reading.hookload_klbs = round(reading.hookload_klbs / lsb_h) * lsb_h

        return reading

    def reset(self):
        self._pressure_drift = 0.0
        self._torque_drift = 0.0
        self._temp_drift = 0.0
        self._hookload_drift = 0.0
        self._t = 0.0
        self.pressure_pink = PinkNoiseGenerator(rng=self.rng)
        self.torque_pink = PinkNoiseGenerator(rng=self.rng)
        self.temp_pink = PinkNoiseGenerator(rng=self.rng)
        self.hookload_pink = PinkNoiseGenerator(rng=self.rng)
        self.rpm_pink = PinkNoiseGenerator(rng=self.rng)
