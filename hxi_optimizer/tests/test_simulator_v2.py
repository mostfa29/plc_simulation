"""Tests for the v2 simulator physics.

Verifies that the rewritten torque + temperature models produce realistic
distributions that match the ranges a real HXI PLC would report. Catches
regressions where a config change lands torque in the wrong order of
magnitude or makes loop_temp collapse to a single value.
"""
from __future__ import annotations

import numpy as np
import pytest

from training.simulator import HydraulicTopDriveSimulator, SimConfig
from training.scenarios import (
    ALL_GENERATORS, TRAINING_GENERATORS,
    _randomise_operating_point,
)


class TestTorqueModel:
    def test_torque_rises_with_formation_load(self):
        """Formation load must flow through to delivered_torque. This is the
        signal the ML classifier uses to detect CONDITION_CHANGE — if it
        doesn't propagate, the class becomes unlearnable.
        """
        sim = HydraulicTopDriveSimulator(SimConfig())
        # Let it settle at steady state
        for _ in range(200):
            sim.step(50.0)
        tq_nominal = np.mean([sim.step(50.0)["delivered_torque"]
                               for _ in range(20)])
        # Apply formation load
        tq_loaded = np.mean([sim.step(50.0, torque_load=2000.0)["delivered_torque"]
                              for _ in range(20)])
        assert tq_loaded > tq_nominal + 500, \
            f"Torque didn't rise with load: {tq_nominal:.0f} -> {tq_loaded:.0f}"

    def test_torque_tracks_rpm_via_viscous(self):
        """Higher RPM at the same load should show higher torque (viscous
        damping). Without this, the model can't distinguish drill-through
        RPM regimes.
        """
        # Default bounds [350, 650] cap effective RPM at ~35 — both sims
        # would settle near the same speed and the viscous term gets eaten
        # by sensor noise. Widen bounds so 30 and 100 RPM are both reachable.
        sim1 = HydraulicTopDriveSimulator(SimConfig())
        sim1.set_bounds(100, 900)
        sim2 = HydraulicTopDriveSimulator(SimConfig())
        sim2.set_bounds(100, 900)
        for _ in range(300):
            sim1.step(30.0)
            sim2.step(100.0)
        tq_low = np.mean([sim1.step(30.0)["delivered_torque"]
                           for _ in range(20)])
        tq_high = np.mean([sim2.step(100.0)["delivered_torque"]
                            for _ in range(20)])
        assert tq_high > tq_low, \
            f"Torque not RPM-dependent: {tq_low:.0f} @30rpm vs {tq_high:.0f} @100rpm"

    def test_torque_range_matches_real_rig_scale(self):
        """Steady-state torque must be in the 500–6000 ft-lbs band that real
        HXI 800HP rigs report. Wildly out-of-range = something regressed.
        """
        sim = HydraulicTopDriveSimulator(SimConfig())
        for _ in range(200):
            sim.step(60.0)
        tq = [sim.step(60.0, torque_load=1000.0)["delivered_torque"]
              for _ in range(200)]
        mean_tq = float(np.mean(tq))
        assert 500 < mean_tq < 6000, \
            f"Steady-state torque {mean_tq:.0f} outside realistic band"


class TestThermalModel:
    def test_loop_temp_rises_with_duty(self):
        """Oil temp must heat-soak under hydraulic load. Without this, the
        7th channel is a constant and the classifier loses a feature dim.
        """
        # High-duty simulator runs at full setpoint, wider bounds to let it reach it
        sim = HydraulicTopDriveSimulator(SimConfig())
        sim.set_bounds(100, 900)  # wide enough to reach 100 RPM
        start_temp = sim.loop_temp
        for _ in range(800):             # 400 seconds of continuous work
            sim.step(100.0)
        end_temp = sim.loop_temp
        assert end_temp > start_temp + 5.0, \
            f"Temp didn't rise with duty: {start_temp:.1f} -> {end_temp:.1f}"

    def test_loop_temp_variance_nonzero_within_episode(self):
        """Over a 300s episode, temperature should change enough that the
        classifier can use it as a feature. Variance < 0.05 means the
        channel is frozen and useless.
        """
        sim = HydraulicTopDriveSimulator(SimConfig())
        sim.set_bounds(100, 900)
        temps = [sim.step(80.0)["loop_temp"] for _ in range(600)]
        assert np.std(temps) > 0.5, \
            f"loop_temp variance too low ({np.std(temps):.3f}) — channel frozen"

    def test_loop_temp_stays_in_physical_bounds(self):
        sim = HydraulicTopDriveSimulator(SimConfig())
        temps = [sim.step(60.0)["loop_temp"] for _ in range(600)]
        assert all(5 <= t <= 100 for t in temps), \
            f"loop_temp escaped physical bounds: [{min(temps)},{max(temps)}]"


class TestScenarioOperatingPointRandomization:
    def test_normal_actually_reaches_setpoint(self):
        """The critical fix for sim-to-real transfer: NORMAL must track
        setpoint, not pin at bounds. Run the generator across seeds and
        require the final-settled RPM to be close to setpoint.
        """
        reached_setpoint = 0
        for seed in range(10):
            samples, _ = ALL_GENERATORS["normal"](seed=seed)
            sp = samples[-1]["ss_setpoint_fwd"]
            rpm = np.mean([s["rpm_encoder"] for s in samples[-60:]])
            if abs(rpm - sp) < 5.0:
                reached_setpoint += 1
        assert reached_setpoint >= 8, \
            f"Only {reached_setpoint}/10 NORMAL episodes settled near setpoint"

    def test_setpoint_varies_across_seeds(self):
        setpoints = set()
        for seed in range(20):
            samples, _ = ALL_GENERATORS["normal"](seed=seed)
            setpoints.add(round(samples[0]["ss_setpoint_fwd"], 1))
        assert len(setpoints) >= 15, \
            f"Setpoint not varying enough: only {len(setpoints)} unique out of 20"


class TestNewScenarios:
    def test_multiscale_stickslip_labels_oscillation(self):
        samples, labels = ALL_GENERATORS["multiscale_stickslip"](seed=3)
        unique_labels = set(labels)
        assert "OSCILLATION" in unique_labels
        assert "NORMAL" in unique_labels

    def test_multiscale_stickslip_has_mixed_frequency_content(self):
        """Key property: spectrum shouldn't be a single spike. If it is,
        the scenario isn't more informative than the old rectangular one.
        """
        samples, labels = ALL_GENERATORS["multiscale_stickslip"](seed=7)
        rpm = np.array([s["rpm_encoder"] for s in samples])
        osc_start = next(i for i, l in enumerate(labels) if l == "OSCILLATION")
        rpm_osc = rpm[osc_start:] - np.mean(rpm[osc_start:])
        fft = np.abs(np.fft.rfft(rpm_osc))
        # Ignore DC; look for multiple significant peaks
        spectrum = fft[1:]
        top5 = np.sort(spectrum)[-5:]
        # Top 5 bins should each be at least 10% of the absolute max — i.e.,
        # energy is spread, not concentrated in one bin
        assert np.all(top5 > 0.10 * top5.max()), \
            "Spectrum too peaked — not truly multi-scale"

    def test_chaotic_formation_produces_condition_change(self):
        samples, labels = ALL_GENERATORS["chaotic_formation_change"](seed=3)
        assert "CONDITION_CHANGE" in set(labels)
        # Torque should rise over the episode
        torques = [s["delivered_torque"] for s in samples]
        first_quarter = np.mean(torques[:len(torques)//4])
        last_quarter = np.mean(torques[-len(torques)//4:])
        assert last_quarter > first_quarter, \
            f"Chaotic formation torque didn't rise: {first_quarter:.0f} -> {last_quarter:.0f}"


class TestTrainingSetExcludesConnection:
    def test_connection_not_in_training_generators(self):
        assert "connection" not in TRAINING_GENERATORS
        assert "connection" in ALL_GENERATORS  # still available for sandbox

    def test_new_scenarios_in_training_generators(self):
        assert "multiscale_stickslip" in TRAINING_GENERATORS
        assert "chaotic_formation_change" in TRAINING_GENERATORS
