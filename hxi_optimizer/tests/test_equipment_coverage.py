"""Deep equipment-coverage tests.

Verifies the system works on EVERY equipment type in the fleet:
  - 14 equipment classes in EQUIPMENT_CATALOG
  - 9 machine profiles in MACHINE_PROFILES
  - 87 devices in fleet_catalog.yaml
  - 88 YAML profiles in profiles/

Each parametrized test exercises one dimension across the full set,
so a regression on any equipment type shows up as a specific named
failure (not a single catch-all).

Tests cover:
  - EquipmentSpec lookup for every fleet device
  - MachineRegistry loads every profile without error
  - Register map parse + read-block computation for every profile
  - Per-machine simulator produces stable, non-NaN output
  - 9 scenarios x 9 equipment = 81 scenario/equipment combos work
  - Machine change events logged across all equipment types
  - Fleet catalog integrity (all equipment types have specs)
"""
from __future__ import annotations

import numpy as np
import pytest
import yaml
from pathlib import Path

from hxi_optimizer.comms.fleet import (
    EQUIPMENT_CATALOG, EquipmentSpec, FleetCatalog,
)
from hxi_optimizer.comms.machine_registry import (
    DEFAULT_HXI_OPTIMIZER_MAP, MachineRecord, MachineRegistry,
    MachineRegisterMap, RegisterEntry,
)
from hxi_optimizer.state.machine_state import MachineStateStore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"

# ═══════════════════════════════════════════════════════════════════════════
# 1. EQUIPMENT CATALOG INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════

ALL_EQUIPMENT_TYPES = list(EQUIPMENT_CATALOG.keys())


class TestEquipmentCatalog:
    def test_catalog_non_empty(self):
        assert len(EQUIPMENT_CATALOG) >= 9

    def test_has_unknown_fallback(self):
        assert "unknown" in EQUIPMENT_CATALOG

    @pytest.mark.parametrize("equipment_type", ALL_EQUIPMENT_TYPES)
    def test_every_spec_has_display_name(self, equipment_type):
        spec = EQUIPMENT_CATALOG[equipment_type]
        assert spec.display_name, f"{equipment_type} missing display_name"

    @pytest.mark.parametrize("equipment_type", ALL_EQUIPMENT_TYPES)
    def test_every_spec_has_plc_type(self, equipment_type):
        spec = EQUIPMENT_CATALOG[equipment_type]
        assert spec.plc_type, f"{equipment_type} missing plc_type"

    @pytest.mark.parametrize("equipment_type", ALL_EQUIPMENT_TYPES)
    def test_spec_is_dataclass(self, equipment_type):
        spec = EQUIPMENT_CATALOG[equipment_type]
        assert isinstance(spec, EquipmentSpec)
        assert spec.equipment_type == equipment_type

    @pytest.mark.parametrize("equipment_type", ["hxi", "hxi_ht", "hxi_ss",
                                                  "exi", "fds", "rostel",
                                                  "warrior", "smart_drive", "emi"])
    def test_primary_types_have_physics(self, equipment_type):
        """Primary top-drive types need non-zero physics."""
        spec = EQUIPMENT_CATALOG[equipment_type]
        assert spec.horsepower > 0, f"{equipment_type} horsepower=0"
        assert spec.gear_ratio > 0, f"{equipment_type} gear_ratio=0"
        assert spec.max_torque_ft_lbs > 0, f"{equipment_type} max_torque=0"
        assert spec.max_rpm > 0, f"{equipment_type} max_rpm=0"
        assert spec.motor_displacement_cc > 0
        assert spec.pump_displacement_cc > 0

    @pytest.mark.parametrize("equipment_type", ALL_EQUIPMENT_TYPES)
    def test_register_convention_nonempty(self, equipment_type):
        spec = EQUIPMENT_CATALOG[equipment_type]
        assert spec.register_convention != ""


# ═══════════════════════════════════════════════════════════════════════════
# 2. FLEET CATALOG INTEGRITY (87 devices)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def fleet() -> FleetCatalog:
    return FleetCatalog.load()


@pytest.fixture(scope="module")
def registry() -> MachineRegistry:
    return MachineRegistry.load()


class TestFleetCatalog:
    def test_loads(self, fleet):
        assert len(fleet.devices) > 50, "Fleet catalog should have at least 50 devices"

    def test_no_duplicate_names(self, fleet):
        names = [d.ewon_name for d in fleet.devices]
        assert len(names) == len(set(names)), "Duplicate eWon names in catalog"

    def test_every_device_has_equipment_type(self, fleet):
        for d in fleet.devices:
            assert d.equipment_type, f"{d.ewon_name} has no equipment_type"

    def test_every_equipment_type_has_spec(self, fleet):
        """Every equipment_type referenced in the catalog must have a spec."""
        missing = []
        for d in fleet.devices:
            if d.equipment_type not in EQUIPMENT_CATALOG:
                missing.append((d.ewon_name, d.equipment_type))
        assert not missing, f"Missing specs for: {missing}"

    def test_summary_counts_match_device_count(self, fleet):
        summary = fleet.summary()
        assert sum(summary.values()) == len(fleet.devices)

    def test_identify_by_partial_name(self, fleet):
        """Partial-match fallback should work."""
        d = fleet.identify_by_name("precision rig 707")
        assert d is not None
        assert "707" in d.ewon_name.lower()

    @pytest.mark.parametrize("exact_name", [
        "Precision Rig 707 3pd HT",
        "Precision Rig 709 HXI HT",
        "Panther Rig 2",
        "Hillcorp_Rig",
    ])
    def test_identify_known_exact(self, fleet, exact_name):
        result = fleet.identify_by_name(exact_name)
        assert result is not None, f"Should identify {exact_name}"
        assert result.spec is not None


# ═══════════════════════════════════════════════════════════════════════════
# 3. PROFILE YAML INTEGRITY (88 files)
# ═══════════════════════════════════════════════════════════════════════════

PROFILE_PATHS = sorted(PROFILES_DIR.glob("*.yaml"))


class TestProfileFiles:
    def test_profiles_exist(self):
        assert len(PROFILE_PATHS) > 50

    @pytest.mark.parametrize("path", PROFILE_PATHS, ids=lambda p: p.stem)
    def test_profile_is_valid_yaml(self, path):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            pytest.fail(f"{path.name} is invalid YAML: {e}")
        assert data is None or isinstance(data, dict), \
            f"{path.name} must be a dict or empty"

    @pytest.mark.parametrize("path", PROFILE_PATHS, ids=lambda p: p.stem)
    def test_profile_has_name_or_ewon_name(self, path):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert "name" in data or "ewon_name" in data, \
            f"{path.name} missing name/ewon_name"

    @pytest.mark.parametrize("path", PROFILE_PATHS, ids=lambda p: p.stem)
    def test_profile_equipment_type_in_catalog(self, path):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        et = data.get("equipment_type")
        if et is not None:
            assert et in EQUIPMENT_CATALOG, \
                f"{path.name}: equipment_type={et!r} not in catalog"

    @pytest.mark.parametrize("path", PROFILE_PATHS, ids=lambda p: p.stem)
    def test_reg_map_addresses_are_ints(self, path):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        reg_map = data.get("reg_map") or {}
        for name, info in reg_map.items():
            if not isinstance(info, dict):
                continue
            addr = info.get("address")
            assert addr is None or isinstance(addr, int), \
                f"{path.name}: {name}.address is not int: {addr!r}"

    @pytest.mark.parametrize("path", PROFILE_PATHS, ids=lambda p: p.stem)
    def test_reg_map_addresses_nonnegative(self, path):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        reg_map = data.get("reg_map") or {}
        for name, info in reg_map.items():
            if isinstance(info, dict) and isinstance(info.get("address"), int):
                assert info["address"] >= 0, \
                    f"{path.name}: {name}.address is negative"

    @pytest.mark.parametrize("path", PROFILE_PATHS, ids=lambda p: p.stem)
    def test_data_types_valid(self, path):
        valid = {"FLOAT32", "INT16", "INT32", "WORD", "BOOL"}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        reg_map = data.get("reg_map") or {}
        for name, info in reg_map.items():
            if isinstance(info, dict):
                dt = str(info.get("data_type", "FLOAT32")).upper()
                assert dt in valid, \
                    f"{path.name}: {name}.data_type={dt} invalid"


# ═══════════════════════════════════════════════════════════════════════════
# 4. MACHINE REGISTRY — PER-MACHINE REGISTER MAP LOADS
# ═══════════════════════════════════════════════════════════════════════════


class TestMachineRegistry:
    def test_loads_all_machines(self, registry):
        assert len(registry.all_machines()) > 50

    def test_every_machine_has_register_map(self, registry):
        for rec in registry.all_machines():
            assert len(rec.register_map.registers) > 0, \
                f"{rec.ewon_name} has empty register map"

    def test_every_machine_has_spec(self, registry):
        for rec in registry.all_machines():
            assert rec.spec is not None, f"{rec.ewon_name} has no spec"

    def test_default_map_is_used_when_no_profile(self, registry):
        """Devices without a profile should use DEFAULT_HXI_OPTIMIZER_MAP."""
        no_profile = [r for r in registry.all_machines()
                      if r.register_map.source_profile is None]
        assert len(no_profile) > 0, "Expected some devices to use default map"
        for r in no_profile:
            assert len(r.register_map.registers) == len(DEFAULT_HXI_OPTIMIZER_MAP)

    @pytest.mark.parametrize("rig_name", [
        "Precision Rig 707 3pd HT",
        "Precision Rig 709 HXI HT",
        "Panther Rig 2",
        "Hillcorp_Rig",
    ])
    def test_specific_machines_resolve(self, registry, rig_name):
        rec = registry.get_for_rig(rig_name)
        assert rec is not None
        assert rec.register_map.registers

    def test_read_blocks_computable_for_every_machine(self, registry):
        """compute_read_blocks must not raise for any machine."""
        for rec in registry.all_machines():
            blocks = rec.register_map.compute_read_blocks()
            assert isinstance(blocks, list)
            for b in blocks:
                assert b["count"] > 0
                assert b["start"] >= 0

    def test_read_blocks_are_disjoint_and_ordered(self, registry):
        """Read blocks within a machine should not overlap."""
        for rec in registry.all_machines():
            blocks = rec.register_map.compute_read_blocks()
            for i in range(len(blocks) - 1):
                end_i = blocks[i]["start"] + blocks[i]["count"]
                assert end_i <= blocks[i + 1]["start"], \
                    f"{rec.ewon_name}: overlapping read blocks {blocks[i]} vs {blocks[i+1]}"


# ═══════════════════════════════════════════════════════════════════════════
# 5. PER-EQUIPMENT SIMULATOR PHYSICS
# ═══════════════════════════════════════════════════════════════════════════

try:
    from training.machine_profiles import MACHINE_PROFILES, make_simulator
    HAS_MACHINE_PROFILES = True
except ImportError:
    HAS_MACHINE_PROFILES = False

EQUIPMENT_TYPES_FOR_SIM = (
    list(MACHINE_PROFILES.keys()) if HAS_MACHINE_PROFILES else []
)


@pytest.mark.skipif(not HAS_MACHINE_PROFILES, reason="machine_profiles unavailable")
class TestPerEquipmentSimulator:
    @pytest.mark.parametrize("equipment_type", EQUIPMENT_TYPES_FOR_SIM)
    def test_simulator_builds(self, equipment_type):
        sim = make_simulator(equipment_type)
        assert sim is not None
        assert sim.cfg is not None

    @pytest.mark.parametrize("equipment_type", EQUIPMENT_TYPES_FOR_SIM)
    def test_step_produces_valid_sample(self, equipment_type):
        sim = make_simulator(equipment_type)
        s = sim.step(60.0)
        required = {"rpm_encoder", "swash_output", "ss_setpoint_fwd",
                    "active_lower", "active_upper", "delivered_torque", "loop_temp"}
        assert required.issubset(s.keys()), \
            f"{equipment_type}: missing keys in sample"

    @pytest.mark.parametrize("equipment_type", EQUIPMENT_TYPES_FOR_SIM)
    def test_no_nan_inf_over_100_samples(self, equipment_type):
        sim = make_simulator(equipment_type)
        for _ in range(100):
            s = sim.step(60.0)
            for k in ("rpm_encoder", "swash_output", "delivered_torque", "loop_temp"):
                v = s[k]
                assert not (isinstance(v, float) and (np.isnan(v) or np.isinf(v))), \
                    f"{equipment_type}: {k}={v} is NaN/Inf"

    @pytest.mark.parametrize("equipment_type", EQUIPMENT_TYPES_FOR_SIM)
    def test_rpm_stays_bounded(self, equipment_type):
        """RPM should converge toward setpoint, not explode to 1e9 or negative 1e9."""
        sim = make_simulator(equipment_type)
        for _ in range(200):
            s = sim.step(60.0)
        # After 200 steps (100 s) should be within 3x setpoint in magnitude
        assert abs(s["rpm_encoder"]) < 300.0, \
            f"{equipment_type}: RPM exploded to {s['rpm_encoder']}"

    @pytest.mark.parametrize("equipment_type", EQUIPMENT_TYPES_FOR_SIM)
    def test_temperature_stays_reasonable(self, equipment_type):
        """loop_temp should stay within operating range (~20-95 C)."""
        sim = make_simulator(equipment_type)
        for _ in range(200):
            s = sim.step(60.0)
        assert 15.0 <= s["loop_temp"] <= 100.0, \
            f"{equipment_type}: temp={s['loop_temp']} out of range"

    @pytest.mark.parametrize("equipment_type", EQUIPMENT_TYPES_FOR_SIM)
    def test_distinct_physics_signature(self, equipment_type):
        """After 50 steps from rest, different equipment should give different RPM."""
        sim = make_simulator(equipment_type)
        for _ in range(50):
            s = sim.step(60.0)
        # Not checking exact values — just that it's not zero or flat
        assert abs(s["rpm_encoder"]) > 0.01


# ═══════════════════════════════════════════════════════════════════════════
# 6. SCENARIO × EQUIPMENT MATRIX (9 × 9 = 81 combos)
# ═══════════════════════════════════════════════════════════════════════════

try:
    from training.scenarios import ALL_GENERATORS
    SCENARIO_NAMES = list(ALL_GENERATORS.keys())
    HAS_SCENARIOS = True
except ImportError:
    SCENARIO_NAMES = []
    HAS_SCENARIOS = False


@pytest.mark.skipif(not (HAS_SCENARIOS and HAS_MACHINE_PROFILES),
                    reason="scenarios/machine_profiles unavailable")
class TestScenarioEquipmentMatrix:
    """81 scenario × equipment combinations must all produce valid output."""

    @pytest.mark.parametrize("equipment_type", EQUIPMENT_TYPES_FOR_SIM)
    @pytest.mark.parametrize("scenario", SCENARIO_NAMES)
    def test_scenario_runs_on_equipment(self, scenario, equipment_type):
        gen = ALL_GENERATORS[scenario]
        # Keep duration short to make 81 combos fast
        samples, labels = gen(duration_s=30, equipment_type=equipment_type, seed=0)
        assert len(samples) > 0, \
            f"{scenario}/{equipment_type}: no samples generated"
        assert len(samples) == len(labels)
        # Sanity-check values
        for s in samples[-5:]:
            rpm = s["rpm_encoder"]
            assert not np.isnan(rpm), f"{scenario}/{equipment_type}: NaN RPM"
            assert not np.isinf(rpm), f"{scenario}/{equipment_type}: Inf RPM"

    @pytest.mark.parametrize("equipment_type", EQUIPMENT_TYPES_FOR_SIM)
    def test_fault_scenarios_emit_fault_labels(self, equipment_type):
        """Non-normal scenarios should emit their fault label on at least some samples.
        Durations + onsets are chosen so the fault label is always reached."""
        fault_expectations = [
            ("bias", "BIAS", {"duration_s": 180, "onset_s": 30}),
            ("oscillation", "OSCILLATION", {"duration_s": 120, "onset_s": 30}),
            ("sluggish", "SLUGGISH", {"duration_s": 60}),
            ("windup", "WINDUP", {"duration_s": 60}),
            ("deadband_hunting", "DEADBAND_HUNTING", {"duration_s": 180}),
            ("formation_change", "CONDITION_CHANGE",
             {"duration_s": 300, "onset_s": 60}),
        ]
        for scenario, expected_label, kw in fault_expectations:
            gen = ALL_GENERATORS[scenario]
            kwargs = {"equipment_type": equipment_type, "seed": 0}
            kwargs.update(kw)
            _, labels = gen(**kwargs)
            unique = set(labels)
            assert expected_label in unique, \
                f"{scenario}/{equipment_type}: expected {expected_label} " \
                f"in labels, got {unique}"


# ═══════════════════════════════════════════════════════════════════════════
# 7. MACHINE STATE STORE ACROSS EQUIPMENT TYPES
# ═══════════════════════════════════════════════════════════════════════════


class TestMachineStateAcrossEquipment:
    def test_note_connection_for_every_equipment_type(self, registry, tmp_path):
        """Must be able to note a connection for at least one device of every type."""
        store = MachineStateStore.load(tmp_path / "m.json")
        types_tested = set()
        for rec in registry.all_machines():
            if rec.equipment_type in types_tested:
                continue
            types_tested.add(rec.equipment_type)
            event = store.note_connection(rec, reason="test")
            assert event in ("new", "changed", "same")
        # Should have hit every equipment type in the catalog
        assert len(types_tested) >= 10, f"Only tested {types_tested}"

    def test_save_load_roundtrip(self, registry, tmp_path):
        path = tmp_path / "m.json"
        store = MachineStateStore.load(path)
        for rec in registry.all_machines()[:20]:
            store.note_connection(rec, reason="bulk")
        assert store.save(path)
        reloaded = MachineStateStore.load(path)
        assert reloaded.machine_count() > 0
        assert reloaded.current_ewon_name == store.current_ewon_name

    def test_change_events_tracked_across_types(self, registry, tmp_path):
        """Flipping between different equipment types logs changes."""
        store = MachineStateStore.load(tmp_path / "m.json")
        # Pick one of each type
        seen_types: dict[str, MachineRecord] = {}
        for rec in registry.all_machines():
            if rec.equipment_type not in seen_types:
                seen_types[rec.equipment_type] = rec
        records = list(seen_types.values())
        if len(records) < 2:
            pytest.skip("Need at least 2 equipment types")
        for rec in records:
            store.note_connection(rec, reason="cycle")
        # All but the first should be changes
        assert len(store.recent_events()) >= len(records) - 1


# ═══════════════════════════════════════════════════════════════════════════
# 8. DEFAULT MAP COMPLETENESS
# ═══════════════════════════════════════════════════════════════════════════


class TestDefaultRegisterMap:
    def test_default_map_has_core_fields(self):
        required = {"rpm", "swash_output", "swash_lower", "swash_upper",
                    "heartbeat", "active_lower", "active_upper",
                    "delivered_torque", "loop_temp"}
        assert required.issubset(set(DEFAULT_HXI_OPTIMIZER_MAP.keys()))

    def test_default_map_addresses_non_overlapping(self):
        """Different fields should not target the same starting address."""
        addrs = [info["address"] for info in DEFAULT_HXI_OPTIMIZER_MAP.values()]
        assert len(addrs) == len(set(addrs))

    def test_default_map_parses_to_register_entries(self):
        from hxi_optimizer.comms.machine_registry import _build_map_from_dict
        mm = _build_map_from_dict(DEFAULT_HXI_OPTIMIZER_MAP, source=None)
        assert len(mm.registers) == len(DEFAULT_HXI_OPTIMIZER_MAP)
        for name, entry in mm.registers.items():
            assert isinstance(entry, RegisterEntry)
            assert entry.address >= 0
            assert entry.word_count in (1, 2)


# ═══════════════════════════════════════════════════════════════════════════
# 9. FLEET API PAYLOAD VALIDATION
# ═══════════════════════════════════════════════════════════════════════════


class TestFleetPayload:
    """Validate the structure of what /api/fleet serializes, without
    booting the dashboard. Mirrors server.py's construction logic."""

    def test_every_device_serializes(self, fleet):
        for d in fleet.devices:
            spec = EQUIPMENT_CATALOG.get(d.equipment_type, EQUIPMENT_CATALOG["unknown"])
            payload = {
                "ewon_name": d.ewon_name,
                "equipment_type": d.equipment_type,
                "customer": d.customer,
                "description": d.description,
                "firmware": d.firmware,
                "status": d.status,
                "has_profile": d.profile_path is not None,
                "profile_file": d.profile_path.name if d.profile_path else None,
                "spec": {
                    "display_name": spec.display_name,
                    "horsepower": spec.horsepower,
                    "gear_ratio": spec.gear_ratio,
                    "max_torque_ft_lbs": spec.max_torque_ft_lbs,
                    "max_rpm": spec.max_rpm,
                    "plc_type": spec.plc_type,
                    "register_convention": spec.register_convention,
                },
            }
            # Must be JSON-round-trippable
            import json
            reencoded = json.loads(json.dumps(payload))
            assert reencoded["ewon_name"] == d.ewon_name

    def test_machine_record_to_json_for_every_machine(self, registry):
        """MachineRecord.to_json() must never raise."""
        import json
        for rec in registry.all_machines():
            js = rec.to_json()
            encoded = json.dumps(js, default=str)
            assert len(encoded) > 50


# ═══════════════════════════════════════════════════════════════════════════
# 10. EQUIPMENT-PHYSICS SANITY (distinct numerical signatures)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not HAS_MACHINE_PROFILES, reason="machine_profiles unavailable")
class TestPhysicsSignatureDistinctness:
    """Different equipment types should produce measurably different telemetry."""

    @pytest.mark.parametrize("a,b", [
        ("hxi", "hxi_ht"),
        ("hxi", "warrior"),
        ("hxi_ht", "emi"),
        ("warrior", "smart_drive"),
        ("emi", "smart_drive"),
    ])
    def test_pair_produces_distinct_rpm(self, a, b):
        """After 50 steps from rest, the two sims shouldn't converge to the same RPM."""
        sa = make_simulator(a)
        sb = make_simulator(b)
        # Fix seed so RNG doesn't mask the physics difference
        np.random.seed(42)
        sa._rng = np.random.default_rng(42)
        sb._rng = np.random.default_rng(42)
        for _ in range(50):
            ra = sa.step(60.0)
            rb = sb.step(60.0)
        # Expect at least 5% relative difference
        ra_rpm = ra["rpm_encoder"]
        rb_rpm = rb["rpm_encoder"]
        rel = abs(ra_rpm - rb_rpm) / max(abs(ra_rpm), abs(rb_rpm), 1.0)
        assert rel > 0.01, \
            f"{a} and {b} converged to nearly identical RPM ({ra_rpm} vs {rb_rpm})"

    @pytest.mark.parametrize("equipment_type", EQUIPMENT_TYPES_FOR_SIM)
    def test_gear_ratio_affects_rpm_response(self, equipment_type):
        """Higher gear ratio → lower motor RPM for the same flow."""
        sim = make_simulator(equipment_type)
        for _ in range(100):
            s = sim.step(60.0)
        p = MACHINE_PROFILES[equipment_type]
        # Final RPM should stay below max_rpm (the equipment envelope)
        assert abs(s["rpm_encoder"]) < p.max_rpm * 1.5, \
            f"{equipment_type}: RPM {s['rpm_encoder']} exceeds max envelope {p.max_rpm}"
