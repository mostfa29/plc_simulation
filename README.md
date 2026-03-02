# TopDrive AI — Physics-Based Pipe Threading Simulator

Multi-machine, multi-connection simulator for oilfield pipe threading operations. Generates synthetic training data for ML-based anomaly detection and connection quality classification targeting >90% TSTR (Train on Synthetic, Test on Real) ratio.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        SimulationRunner                              │
│                                                                      │
│  ┌────────────────────┐  ┌──────────────┐  ┌─────────────────────┐  │
│  │   PhysicsEngine    │→ │ SensorModel  │→ │  ModbusTCPServer    │  │
│  │     (100 Hz)       │  │ (noise+ADC)  │  │  (port 502/5020)    │  │
│  │                    │  │              │  │                     │  │
│  │ Drive Models:      │  │ White noise  │  │ Per-machine profile │  │
│  │  AC Motor + VFD    │  │ Pink (1/f)   │  │ Dynamic reg map     │  │
│  │  Iron Roughneck    │  │ EMI (60Hz)   │  │ FC03/FC06/FC16      │  │
│  │  Hydraulic Motor   │  │ VFD harmonics│  │                     │  │
│  │                    │  │ Pump ripple  │  │ Your pipeline       │  │
│  │ Torque-Turn Models:│  │ Gear mesh    │  │ connects identical  │  │
│  │  Farr (API 8-Rd)   │  │ ADC quant.  │  │ to real PLC         │  │
│  │  Buttress (BTC)    │  │ Drift       │  │                     │  │
│  │  Premium (shoulder)│  └──────────────┘  └─────────────────────┘  │
│  │  Drill Pipe (API7) │                                             │
│  │                    │  ┌──────────────┐                           │
│  │ PID Controller     │  │  CSV Output  │                           │
│  │ Thermal Model      │  │  sensor/     │  36 ground truth cols     │
│  │ String Dynamics    │  │  truth/      │  19 sensor data cols      │
│  │ Shoulder Detector  │  │  events/     │  100 Hz default           │
│  │ Slope Calculator   │  │  manifest    │  Ground truth labels      │
│  └────────────────────┘  └──────────────┘                           │
│                                                                      │
│  ┌────────────────────┐  ┌──────────────────────┐                   │
│  │  ScenarioGenerator │  │  MachineProfile       │                   │
│  │  Domain Randomize  │  │  Per-rig register map │                   │
│  │  FaultInjector     │  │  Sensor calibration   │                   │
│  │  54 connections    │  │  YAML profiles/       │                   │
│  └────────────────────┘  └──────────────────────┘                   │
└──────────────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

1. **Flight simulator pattern**: Physics engine runs ground truth at 100Hz, sensor models corrupt it with machine-type-specific noise, Modbus server exposes identical register layout to real GE CPE305 PLC. Your data pipeline can't tell the difference.

2. **Per-machine profiles**: Each rig has its own `MachineProfile` YAML in `profiles/` with register map, sensor calibration, and mechanical parameters. The system auto-discovers register layouts via differential scanning.

3. **Ground truth labeling**: Labels are determined from simulation **events** (what actually happened), not from scenario intent. A "cross_thread" scenario where the trigger wasn't reached is correctly labeled as "normal".

4. **Domain randomization (NVIDIA Sim-to-Real)**: Every scenario randomizes friction (log-normal), hydraulic lag, noise amplitude, PID gains, ambient temperature (-20 to 120F), pipe tolerances, compound viscosity, ADC resolution (12-16 bit), gearbox backlash, and EMI amplitude.

5. **Zero-dependency Modbus**: Pure stdlib TCP server (no pymodbus). Supports FC03 Read Holding, FC06 Write Single, FC16 Write Multiple with GE word-swapped FLOAT32 encoding.

6. **VPN-aware design**: 400ms latency is the constraint. Designed for 1-2 Hz, block reads, auto-reconnect with exponential backoff.

## Machine Types

| Machine | Drive | Torque Measurement | SNR | Key Feature |
|---------|-------|-------------------|-----|-------------|
| Top Drive | AC Motor + VFD | Motor current calc | 45-60 dB | Constant torque/constant power regions, hookload |
| Iron Roughneck | Hydraulic dual-phase | Pressure transducer | 50-65 dB | Spinner -> handoff -> torque wrench transition |
| Power Tong | Hydraulic motor | Load cell (strain gauge) | 55-70 dB | Two-speed, arm compliance, backup tong |
| Bucking Unit | Hydraulic servo | Calibrated load cell | 60-75 dB | Cleanest curves, CNC-grade positioning |

## Quick Start

```bash
# Dependencies (only numpy required for simulation)
pip install numpy

# Optional: pyyaml for machine profiles
pip install pyyaml

# Generate 100 training scenarios (all machine types, all connections)
python generate_dataset.py --count 100 --output ./data/synthetic

# Generate with rebalanced class distribution (50/50 normal/fault)
python generate_dataset.py --count 5000 --output ./data/synthetic_v2 \
    --class-balance rebalanced --seed 42

# Verify ground truth labels before training
python generate_dataset.py --verify-labels --count 1000

# Filter by machine type
python generate_dataset.py --count 50 --machine-type top_drive

# Filter by connection type
python generate_dataset.py --count 50 --connection-type PREMIUM

# Single scenario (debugging)
python generate_dataset.py --single normal_casing_ltc --pipe 7in_23lb_N80_LTC -o ./debug

# Real-time mode with Modbus server
python generate_dataset.py --realtime --modbus-port 5020
```

## Output Format

```
data/synthetic/
├── sensor/         # Noisy data (train your AI on this)
├── truth/          # Ground truth (validate against this)
├── events/         # State transitions, faults, shoulder detection
├── manifest.csv    # Index with ground truth labels and metadata
├── stats.txt       # Distribution statistics with label match rate
└── config.json     # Generation config for reproducibility
```

### Manifest Format (Updated)

The manifest now uses **ground truth labels** based on what actually happened in the simulation:

| Column | Description |
|--------|-------------|
| `ground_truth_label` | Label from simulation events (USE THIS FOR TRAINING) |
| `ground_truth_class` | Numeric class (0-8) |
| `intended_label` | Original scenario type (for debugging) |
| `intended_class` | Intended numeric class |
| `label_match` | True if ground_truth == intended |
| `fault_onset_time` | When fault first detected (seconds) |
| `fault_onset_turns` | Turns at fault onset |
| `final_torque_ftlbs` | Torque at end of connection |
| `total_turns` | Total turns accumulated |
| `torque_at_shoulder` | Torque when shoulder detected |
| `torque_gradient_ftlbs_per_turn` | Slope in power-tight zone |

### 9-Class Fault Classification

| Class | Label | Severity |
|-------|-------|----------|
| 0 | normal | OK |
| 1 | cross_thread | CRIT |
| 2 | galling | CRIT |
| 3 | stripped_thread | CRIT |
| 4 | over_torque | WARN |
| 5 | under_torque | WARN |
| 6 | wrong_compound | WARN |
| 7 | misaligned_stab | CRIT |
| 8 | stall | CRIT |

## Fleet Catalog & Equipment Types

The system tracks all ~130 eWon devices across the fleet in `fleet_catalog.yaml`, classified by equipment type with equipment-specific physics parameters.

### Equipment Types

| Type | HP | Gear Ratio | Max Torque | Max RPM | PLC |
|------|-----|-----------|------------|---------|-----|
| HXI | 800 | 10.5:1 | 37,500 ft-lbs | 228 | CPE305 |
| HXI HT | 800 | 14.0:1 | 50,000 ft-lbs | 170 | CPE305 |
| HXI Smart Slide | 800 | 10.5:1 | 37,500 ft-lbs | 228 | CPE305 |
| EXI | 800 | 11.0:1 | 40,000 ft-lbs | 210 | CPE305 |
| FDS | 800 | 10.5:1 | 37,500 ft-lbs | 228 | CompactLogix |
| Rostel | 750 | 10.0:1 | 35,000 ft-lbs | 240 | Rx3i |
| Warrior | 600 | 9.0:1 | 25,000 ft-lbs | 260 | CPE305 |
| Smart Drive | 900 | 10.5:1 | 42,000 ft-lbs | 220 | CPE305 |
| EMI | 400 | 8.0:1 | 18,000 ft-lbs | 300 | CPE305 |

### Fleet Distribution (Training Data Weighting)

Synthetic data is weighted proportionally to real fleet composition:

```
HXI              19 (22%)    HXI Smart Slide  16 (18%)
Rostel           15 (17%)    HXI HT            9 (10%)
Warrior           8 ( 9%)    EXI                7 ( 8%)
FDS               6 ( 7%)    Smart Drive        3 ( 3%)
ECI               2 ( 2%)    EMI                2 ( 2%)
```

```bash
# View fleet catalog summary and training weights
python fleet_manager.py --fleet-catalog

# Apply equipment-specific physics to simulation
from config import SimConfig, EquipmentType
cfg = SimConfig()
cfg.apply_equipment_spec(EquipmentType.HXI_HT)  # 14:1 gear, 50k torque
```

## Per-Machine Profiles

Each of Steve's rigs has a different PLC program with process data at different register addresses. The `MachineProfile` system handles this:

```
profiles/
├── shop_unit.yaml           # Baseline (confirmed R6000 layout)
└── precision_rig_709.yaml   # First field machine (confirmed R160+ layout)
```

### Creating a Profile

```bash
# Auto-discover registers on a live machine
python discover_machine.py --host 129.168.1.25 --output profiles/rig709.yaml

# Network scan to find PLCs on eWon VPN
python discover_machine.py --scan-network --subnet 129.168.1
```

### Using Profiles

```python
from config import MachineProfile, SimConfig

# Load a profile
profile = MachineProfile.from_yaml("profiles/precision_rig_709.yaml")

# Use with SimConfig
cfg = SimConfig(machine_profile=profile)
cfg.apply_machine_profile()  # Updates register addresses and physical params
```

## Machine Discovery

`discover_machine.py` uses differential scanning to identify live PLC registers:

1. **Network Discovery**: Scans eWon VPN subnet for Modbus TCP hosts
2. **Differential Scan**: Multiple passes with wait periods to find changing registers
3. **Register Classification**: Heuristic identification (torque, RPM, pressure, etc.)
4. **Profile Generation**: Outputs MachineProfile YAML for review

```bash
# Full discovery (3 passes, 10s wait between each)
python discover_machine.py --host 129.168.1.25

# Quick scan (2 passes, 5s wait)
python discover_machine.py --host 129.168.1.25 --passes 2 --wait 5

# Single snapshot (no differential, just find non-zero registers)
python discover_machine.py --host 129.168.1.25 --snapshot-only
```

## Physics Calibration

`PhysicsCalibrator` fits simulator parameters to real captured data:

```python
from physics_engine import PhysicsCalibrator

cal = PhysicsCalibrator(sample_rate_hz=10.0)
cal.load_real_data("captures/rig709_session1.csv")
connections = cal.segment_connections()
result = cal.calibrate(connections[0])
# result: {'peak_torque_ftlbs': 8500, 'estimated_tau_ms': 135, ...}
```

## Noise Profiling

`NoiseProfiler` analyzes real captured data to characterize sensor noise:

```python
from sensor_models import NoiseProfiler

profiler = NoiseProfiler()
profiler.load_csv("captures/rig709_idle.csv", sample_rate_hz=10.0)
profile = profiler.analyze()
# profile: {'torque_snr_db': 54.2, 'pressure_drift_per_s': 0.003, ...}
```

## Live Data Capture & Detection

### capture_live.py — Real-Time Modbus Data Recorder

Connects to PLC via eWon VPN with support for per-machine profiles:

```bash
# Smart multi-rig capture (RECOMMENDED): auto-detects VPN, finds PLCs, captures
python capture_live.py --multi --smart

# Smart capture with rig name override (when eWon name can't be auto-detected)
python capture_live.py --host 129.168.1.25 --smart --rig "Panther Rig 2"

# Discovery mode: scan registers 0-200, auto-detect unit ID
python capture_live.py --host 129.168.1.25 --discover

# Auto-detect profile from IP address (checks profiles/ directory)
python capture_live.py --host 129.168.1.25

# Build training dataset from captured connections
python capture_live.py --build-dataset --input ./live_captures --rig-name "Panther Rig 2"
```

Key features:
- **Smart mode**: Infers connection state, auto-segments each connection, writes JSON metadata, idle decimation
- **Multi-rig**: Auto-detects eWon VPN tunnel, scans LAN for PLCs, identifies rigs from fleet catalog
- **Unit ID auto-scan**: Tries unit IDs 0-5 to find the working one (eWon gateways vary)
- **MachineProfile support**: Auto-detects by IP or loads from YAML
- **Non-contiguous register maps**: Multiple block reads for scattered registers
- **Fleet catalog integration**: Matches eWon name to fleet catalog for equipment type identification
- **Auto-reconnect**: Exponential backoff on VPN drops

### detect_live.py — InceptionTime Fault Detection

```bash
# Offline: batch process captured CSVs
python detect_live.py offline \
    --input ./live_captures \
    --model ./model.pt --norm ./norm.json

# Live: real-time Modbus polling + inference
python detect_live.py live \
    --host 129.168.1.25 \
    --model ./model.pt --norm ./norm.json

# TSTR validation: test sim-trained model on real data
python detect_live.py tstr \
    --input ./real_captures \
    --model ./model.pt --norm ./norm.json \
    --labels ./real_labels.csv

# Verify manifest labels before training
python detect_live.py verify-labels --manifest ./data/synthetic/manifest.csv
```

### Connection Manager

For managing multiple machines via eWon VPN:

```python
from connection_manager import ConnectionManager

mgr = ConnectionManager("profiles/")
mgr.scan()
active = mgr.get_active()
if active:
    print(f"Connected to {active.name} at {active.plc_ip}")
```

### Fleet Management

`fleet_manager.py` is the top-level orchestrator that automates the entire workflow: detect VPN tunnel → discover PLCs → identify machine → run diagnostics → ready for capture.

```bash
# Daemon mode — watch for VPN tunnels and auto-handle
python fleet_manager.py --daemon

# One-shot scan — detect tunnel, discover, test, report
python fleet_manager.py --scan-now

# Test a specific known machine
python fleet_manager.py --test shop_unit

# Onboard a new machine (auto-fingerprint, differential scan, profile generation)
python fleet_manager.py --onboard --host 129.168.1.25 --name rig_709

# Fleet status report
python fleet_manager.py --fleet-report

# Detect VPN tunnel only
python fleet_manager.py --detect-tunnel
```

**Components:**

| Class | Purpose |
|-------|---------|
| `EWonDetector` | Detects active eWon VPN tunnels via route scan, eCatcher process detection, or heartbeat |
| `LANDiscovery` | Scans eWon LAN subnet for Modbus hosts, fingerprints PLCs |
| `MachineIdentifier` | Matches discovered PLCs to known profiles (by IP or register fingerprint) |
| `SmartTester` | 9-point diagnostic suite: connectivity, latency, register access, data sanity, sensor ranges, dynamics, noise floor, word swap, sample rate |
| `FleetManager` | State machine orchestrator with daemon mode, scan, onboard, and reporting |

**SmartTester diagnostic suite:**

1. **Connectivity** — TCP connect + Modbus read within 3 attempts
2. **Latency Profile** — 20 reads, min/avg/max/p95/p99, recommended Hz
3. **Register Readability** — All mapped registers return data (100% required)
4. **Data Sanity** — Decoded values within physical range per variable type
5. **Sensor Ranges** — No stuck/dead/railed sensors (20 samples over 10s)
6. **Dynamics** — Motion detection (is machine threading or idle?)
7. **Noise Floor** — Per-channel SNR characterization for domain randomization
8. **Word Swap** — Confirm GE FLOAT32 byte order matches profile
9. **Sample Rate** — Achievable poll rate with optimal block grouping

### Deployment Sequence

1. Steve connects eWon to active rig
2. Run `capture_live.py --multi --smart` to auto-detect VPN, find PLCs, capture
3. If new machine: `fleet_manager.py --onboard --host <ip>` (Steve confirms registers)
4. Run `capture_live.py --build-dataset` to build training data from captures
5. Generate synthetic data with `generate_dataset.py --class-balance rebalanced`
6. Train InceptionTime ensemble (Colab/RunPod)
7. Run `detect_live.py tstr` to validate sim-to-real transfer
8. Go live with `detect_live.py live`

## Phase 1 Training

### Architecture

InceptionTime ensemble (5 members), 9-class fault classification from 100Hz PLC time-series data.

### Triage Config (Current — Fixing F1=0.17)

Analysis identified three compounding failures in the original training:

1. **Regularization catastrophe**: FocalLoss + label smoothing + mixup + BalancedBatchSampler created contradictory training signals (FocalLoss demands confidence; label smoothing punishes it)
2. **Derived channel noise**: `d_torque_dt` via finite differences amplified noise 100x at 100Hz; `torque_norm` destroyed magnitude info separating over_torque from normal
3. **Insufficient inter-class signal**: Physically similar faults (galling/stripped/over_torque) overlap in raw feature space

**Triage config** (`config_triage.yaml`) strips everything to bare metal:
- Plain `CrossEntropyLoss` (no focal, no label smoothing)
- `Adam` optimizer (no weight decay)
- No mixup augmentation
- Raw 6 channels only (no derived)
- Savitzky-Golay differentiation (replaces noisy finite differences for later use)

```bash
cd phase1_pretraining

# Run diagnostics FIRST (5 minutes total)
pip install aeon scikit-learn
python diagnostics/minirocket_baseline.py --config config_triage.yaml
python diagnostics/fisher_discriminant.py --config config_triage.yaml

# Train with triage config
python train.py --config config_triage.yaml --output-dir ./results_triage
```

### Diagnostic Tools

| Tool | Time | Purpose |
|------|------|---------|
| `diagnostics/minirocket_baseline.py` | 5 min | If MiniRocket F1 ~ 0.17: data problem. If > 0.50: training was the bottleneck. |
| `diagnostics/fisher_discriminant.py` | 2 min | Shows which class pairs are inseparable and which channels discriminate best |

### Training Configs

| Config | Loss | Channels | Mixup | Smoothing | Purpose |
|--------|------|----------|-------|-----------|---------|
| `config_triage.yaml` | CE | 6 (raw) | No | No | **USE THIS** — stripped baseline |
| `config.yaml` | Focal | 12 (all) | 0.3 | 0.10 | Original (F1=0.17, broken) |
| `config_local_test.yaml` | Focal | 12 (all) | 0.3 | 0.10 | Local GTX 1650 test variant |

## Module Structure

| File | Purpose |
|------|---------|
| `config.py` | Constants, pipe catalogs, machine specs, `EquipmentType`, `EquipmentSpec`, `MachineProfile` |
| `physics_engine.py` | Drive models, torque-turn, PID, thermal, shoulder detection, `PhysicsCalibrator` |
| `sensor_models.py` | Machine-type-specific noise corruption pipeline, `NoiseProfiler` |
| `scenario.py` | Scenario generation, fault injection, distribution weights |
| `runner.py` | Orchestrator: physics + sensors + Modbus + CSV output |
| `modbus_server.py` | Zero-dependency Modbus TCP server (FC03/FC06/FC16) |
| `generate_dataset.py` | CLI with ground truth labeling and label verification |
| `capture_live.py` | Live Modbus capture: smart mode, multi-rig, auto unit ID scan |
| `detect_live.py` | Real-time InceptionTime ensemble fault detection, TSTR validation |
| `discover_machine.py` | Smart register scanner & profile generator |
| `connection_manager.py` | Multi-machine connection orchestration |
| `fleet_manager.py` | Fleet orchestrator: VPN detection, discovery, `FleetCatalog`, smart testing |
| `fleet_catalog.yaml` | All ~130 eWon devices classified by equipment type |

### Phase 1 Training (`phase1_pretraining/`)

| File | Purpose |
|------|---------|
| `train.py` | Training loop: warmup + cosine LR, Adam/AdamW, early stopping |
| `dataset.py` | Data pipeline: manifest, splits, windowing, normalization, `channels_mode` |
| `features.py` | Derived channels: Savitzky-Golay d_torque_dt, torque-turn slope, phase |
| `models.py` | InceptionTime ensemble, ResNet baseline |
| `losses.py` | FocalLoss, MixupFocalLoss, mixup augmentation |
| `sampler.py` | BalancedBatchSampler (7 per class per batch) |
| `config_triage.yaml` | **Stripped baseline** — CE loss, raw 6ch, no regularization stack |
| `config.yaml` | Original config (broken — regularization catastrophe) |
| `diagnostics/minirocket_baseline.py` | MiniRocket diagnostic: data vs training problem |
| `diagnostics/fisher_discriminant.py` | Fisher Discriminant Ratio: inter-class separability |

## Scenario Distribution (Section 6.2)

| Type | Weight | Description |
|------|--------|-------------|
| `normal_casing_ltc` | 25% | API 8-Round LTC/STC makeup |
| `normal_casing_btc` | 15% | API Buttress makeup (position-controlled) |
| `normal_casing_premium` | 10% | Premium shouldered (VAM 21, Wedge 563, SEAL-LOK) |
| `normal_drill_pipe` | 15% | Drill pipe (NC26-7-5/8 REG) |
| `normal_tubing` | 5% | Small-diameter tubing |
| `full_cycle` | 8% | Makeup + hold + breakout |
| `cross_thread` | 5% | Spike torque at low turns, erratic |
| `galling` | 4% | Progressive rise, rough/jerky |
| `over_torque` | 3% | Exceeds max envelope |
| `under_torque` | 3% | Target not reached |
| `stall` | 2% | Motor limit, RPM drops to zero |
| `wrong_compound` | 2% | Abnormal shoulder position or slope |
| `misaligned_stabbing` | 2% | High torque at spin-in, oscillating |
| `stripped_thread` | 1% | Torque plateau/drop before target |
| `multi_connection` | 1% | Sequential field operations |
| `stick_slip` | 1% | Torsional oscillation |

## Connection Catalog (54 total)

### API 8-Round LTC (29 entries) — API RP 5C1 Table 1 exact values
| Size | Grades | Torque Range |
|------|--------|-------------|
| 4-1/2" | J-55, N-80, P-110 | 2,680 - 5,660 ft-lbs |
| 5-1/2" | J-55, N-80, P-110 | 3,590 - 10,350 ft-lbs |
| 7" | J-55, N-80, P-110 | 3,470 - 14,170 ft-lbs |
| 9-5/8" | J-55, N-80, P-110 | 6,950 - 23,060 ft-lbs |
| 10-3/4" | J-55, N-80, P-110 | 7,160 - 19,410 ft-lbs |
| 11-3/4" | J-55, N-80 | 7,100 - 12,540 ft-lbs |
| 13-3/8" | J-55, N-80, P-110 | 7,510 - 25,750 ft-lbs |

### API Buttress BTC (8 entries)
5-1/2" through 13-3/8", N-80/P-110/K-55 grades. Steeper shoulder, position-controlled.

### Premium Shouldered (6 entries)
VAM 21, Wedge 563, SEAL-LOK in 5-1/2" to 13-3/8". 4-phase torque-turn with shoulder contact + seal engagement.

### Drill Pipe (9 entries)
NC26 through 7-5/8 REG, S-135 grade. 7,000 - 80,000 ft-lbs.

### Surface/Conductor STC (2 entries)
16" and 20" K-55. 18,000 - 27,500 ft-lbs.

## PLC Register Map (GE CPE305)

Default register layout (R6000 zone, confirmed on shop unit):

| Register | Type | Description | Status |
|----------|------|-------------|--------|
| %R6000-6001 | FLOAT32 | Torque (ft-lb) | Confirmed |
| %R6002-6003 | FLOAT32 | RPM | Confirmed |
| %R6004-6005 | FLOAT32 | System Pressure (PSI) | Confirmed |
| %R6006-6007 | FLOAT32 | Oil Temperature (F) | Confirmed |
| %R6008-6009 | FLOAT32 | Encoder Counts | Confirmed |
| %R6010-6011 | FLOAT32 | PID Setpoint | Estimated |
| %R6012-6013 | FLOAT32 | PID Error | Estimated |
| %R6014 | INT16 | PID Output (% x 100) | Estimated |
| %R6015 | INT16 | Operating Mode | Confirmed |
| %R6016-6017 | FLOAT32 | Target Torque (ft-lb) | Estimated |
| %R6018-6019 | FLOAT32 | Accumulated Turns | Estimated |
| %R6020 | INT16 | Fault Code (bitmask) | Estimated |
| %R6021 | INT16 | Connection State | Confirmed |
| %R6022-6023 | FLOAT32 | Peak Torque (ft-lb) | Estimated |
| %R6024-6025 | FLOAT32 | Hookload (klbs) | Estimated |
| %R6026-6027 | FLOAT32 | Shoulder Torque (ft-lb) | Estimated |
| %R6028-6029 | FLOAT32 | Slope dT/dN (ft-lb/turn) | Estimated |
| %R6030 | INT16 | Connection Count | Estimated |

**Note**: Each rig has different register addresses. Use `discover_machine.py` to find the actual layout, then save as a MachineProfile YAML in `profiles/`.

## GE CPE305 FLOAT32 Encoding

GE stores FLOAT32 as word-swapped: `[Low Word at N] [High Word at N+1]`

```python
import struct
low_word = registers[n]      # e.g., 0x3B5F
high_word = registers[n+1]   # e.g., 0x440E
value = struct.unpack('>f', struct.pack('>HH', high_word, low_word))[0]
```

## Domain Randomization Ranges

| Parameter | Range | Distribution |
|-----------|-------|-------------|
| Friction factor (Kf) | 0.80x - 1.35x | Log-normal |
| Hydraulic time constant | 0.6x - 1.5x | Uniform |
| Motor efficiency | 0.82 - 0.95 | Normal |
| PID gains | 0.85x - 1.15x | Normal |
| Ambient temperature | -20F to 120F | Uniform |
| Noise amplitude | 0.70x - 1.50x | Log-normal |
| Oil viscosity | 0.6x - 1.8x | Uniform |
| Encoder CPR | 1000 - 2000 | Discrete |
| ADC resolution | 12 - 16 bits | Discrete |
| Gearbox backlash | 0.05 - 0.3 deg | Uniform |
| EMI amplitude | 0.1x - 2.0x | Log-normal |
| String length | 30 - 120 ft | Uniform |

## References

- API RP 5C1 — Care and Use of Casing and Tubing (Table 1 torque values)
- API 5B / 5CT — Thread geometry and casing specifications
- API RP 5A3 — Thread compound friction factors
- API 7-2 — Rotary shouldered connections (drill pipe)
- GE CPE305 PLC documentation (register layout)
- Farr friction model for threaded connections
- ASTM D341 — Walther equation (oil viscosity-temperature)
- NOV TDS-11SA / Canrig Sigma 500T — Top drive specifications
- NOV ST-80/100/120 — Iron roughneck specifications
- Vallourec VAM 21 / TenarisHydril Wedge 563 / Hunting SEAL-LOK — Premium connections
