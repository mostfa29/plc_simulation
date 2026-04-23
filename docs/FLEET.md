# Fleet

Steve operates ~130 eWon Flexy 205 devices across a mix of rig types (HXI, HXI HT, HXI SS, EXI, FDS, Rostel, Warrior, Smart Drive, EMI). Each has its own Modbus register map, its own PID tuning history, and potentially its own fine-tuned ML model. The fleet subsystem keeps all of this straight so the optimizer can auto-detect which rig is connected and apply the right config.

---

## Components

```
 fleet_catalog.yaml           FleetCatalog — 130+ device records
       │                          │
       │                          │
       ▼                          ▼
 EQUIPMENT_CATALOG        MachineRegistry ─── profiles/<rig>.yaml
 (HP, gear, PLC type,           │               (per-rig register map)
  register convention)           ▼
                           MachineRecord
                                │
                                ▼
                           machine_state_store
                           (connection history,
                            change events,
                            uptime tracking)
                                │
                                ▼
                          main.py — swap register map
                          + call ModelRegistry.resolve()
                          → monitor.switch_models()
```

---

## Catalogs

### 1. Equipment catalog (`hxi_optimizer/comms/fleet.py`)

Hard-coded specs per equipment class. Used to rank "which rigs need attention first" (fleet triage).

```python
EQUIPMENT_CATALOG = {
    "hxi": EquipmentSpec(display_name="TESCO 250T HXI 800HP", horsepower=800,
                         gear_ratio=2.28, max_torque_ft_lbs=55000, max_rpm=235,
                         plc_type="CPE305", register_convention="ABCD"),
    "hxi_ht":   ...  # 800HP HT variant
    "hxi_ss":   ...  # slow-speed
    "exi":      ...
    "fds":      ...
    "rostel":   ...
    "warrior":  ...
    "smart_drive": ...
    "emi":      ...
    "shop_unit": ...
    "unknown":  ...
}
```

### 2. Fleet catalog (`fleet_catalog.yaml`)

Per-eWon records: eWon name, customer, equipment type, firmware, status, PLC IP, optional profile reference.

```yaml
- ewon_name: "Precision Rig 707 3pd HT"
  customer: "Precision Drilling"
  equipment_type: "hxi_ht"
  firmware: "14.7s0"
  status: "online"
  plc_ip: "192.168.120.10"
  profile: "profiles/hxi_ht_default.yaml"
```

### 3. Per-rig profiles (`hxi_optimizer/comms/profiles/*.yaml`)

Each profile has a register map and optional overrides. Profiles are loaded by `MachineRegistry` and attached to the `MachineRecord` that matches an eWon name.

```yaml
# profiles/hxi_ht_default.yaml
name: "HXI HT default"
registers:
  rpm_encoder:      { address: 6610, dtype: REAL }
  swash_output:     { address: 6612, dtype: REAL }
  active_lower:     { address: 6603, dtype: INT  }
  active_upper:     { address: 6604, dtype: INT  }
  # ... etc
overrides:
  deadband_rpm: 2.5
```

---

## Machine identification (which rig am I connected to?)

At startup and every 30 s, `main.py` + `connection_monitor` try to identify the rig. Four sources, in order of preference:

| Source | Accuracy | When it works |
|---|---|---|
| **Talk2m REST API** | Highest | `talk2m_account` + credentials configured |
| **eCatcher log tail** | High | eCatcher installed, log accessible |
| **Virtual adapter scan** | Medium | eCatcher has opened a VPN adapter |
| **config.ewon_name manual** | Low | Falls back when all three above fail |

Once an eWon name is resolved, `MachineRegistry.get_for_rig(<ewon_name>)` returns a `MachineRecord` with the right profile attached. `main.py` updates `config.plc_host` to the VPN IP of that rig, calls `machine_state_store.note_connection()`, and fires `ModelRegistry.resolve()` to hot-swap the per-rig classifier if one exists.

### eCatcher auto-detect flow

```
 connection_monitor (every 30 s)
        │
        ▼
 EcatcherMonitor.detect()   ─── returns EcatcherState
        │
        ├── tunnel_up: bool
        ├── connected_ewon: str | None
        └── source: "talk2m" | "log" | "adapter"
        │
        ▼
 if connected_ewon != current.ewon_name:
     MachineRegistry.get_for_rig(connected_ewon)
     store.note_connection(...)
     shared["machine_record"] = match
     _swap_models_for_rig(match.ewon_name, reason="ecatcher")
```

The hot-swap is **thread-safe** — `PerformanceMonitor.switch_models()` stages new ONNX sessions outside the model lock, then atomically swaps under `_model_lock` (RLock).

---

## Machine state store (`hxi_optimizer/state/machine_state.py`)

Persistent record of every machine this optimizer instance has connected to. Survives restarts via `machine_state.json` (atomic write).

Tracks per eWon:

- First seen, last seen
- Connection count
- Cumulative uptime
- Recent events (connection, change, error)

Used by:

- **Fleet triage** — ranks rigs by "attention score" (age-of-last-seen + connection count + equipment class risk factor).
- **Dashboard** — Fleet tab's "Machines seen" table.
- **Machine change events** — audit trail of rig switches.

---

## Per-rig models

Each rig can have its own fine-tuned classifier + autoencoder at:

```
hxi_optimizer/models/per_rig/<slug>/
├── classifier.onnx
├── classifier_meta.json
├── autoencoder.onnx       (optional — falls back to default)
└── autoencoder_meta.json  (holds per-rig threshold)
```

Slug convention (`hxi_optimizer/intelligence/model_registry.py:slugify`):

```python
slugify("Precision Rig 707 3pd HT") → "precision_rig_707_3pd_ht"
```

Same slug is used by:

- `realtime_dataset.py` for `logs/dataset/<slug>/` episode files
- `fine_tune.py` as the `--rig` flag translates to
- `ModelRegistry.resolve()` to look up `models/per_rig/<slug>/`

### Registry lookup

```python
reg = ModelRegistry()
pair = reg.resolve("Precision Rig 707 3pd HT")
# pair.source in {"per_rig", "default"}
# pair.classifier_path, pair.classifier_meta_path
# pair.autoencoder_path, pair.autoencoder_meta_path
# pair.has_classifier, pair.has_autoencoder
```

`reg.per_rig_summary()` returns which rigs have fine-tunes for the dashboard's Fleet tab. Each device row has `has_per_rig_model: bool` so the UI shows a green check or "sim" badge.

---

## Limitations & known issues

### eCatcher bottleneck

**eCatcher only connects to one rig at a time.** This is a client-side limitation, not a bug. Implication: the optimizer monitors one rig at a time, and switching rigs takes 5–30 s (tunnel teardown + re-establish + machine identification).

Discussion of alternatives (customer VPN, fleet relay, OpenVPN passthrough) in the surveyor's audit — still in planning, not implemented.

### Profile coverage

`MachineRegistry` has profiles for HXI / HXI HT / HXI SS / generic. Non-HXI rigs (Warrior, Rostel, EMI, …) fall back to the default HXI map, which may not match their register layout. If the optimizer boots with `No fleet match for <IP>`, manually specify `ewon_name` in config to force a profile.

### Fleet catalog freshness

`fleet_catalog.yaml` is updated manually. When a new rig comes online, add a row + a profile reference. `test_equipment_coverage.py` verifies every `equipment_type` in the catalog has a matching `EQUIPMENT_CATALOG` entry so the test suite fails loudly on new types.

---

## Tests

| File | What |
|---|---|
| [`test_equipment_coverage.py`](../hxi_optimizer/tests/test_equipment_coverage.py) | Every catalog equipment_type has a spec |
| [`test_ecatcher.py`](../hxi_optimizer/tests/test_ecatcher.py) | Talk2m / log / adapter detection paths |
| [`test_model_registry.py`](../hxi_optimizer/tests/test_model_registry.py) | Registry resolution, per-rig preference, fallback |
| [`test_realtime_dataset.py`](../hxi_optimizer/tests/test_realtime_dataset.py) | Dataset capture + slug convention |
