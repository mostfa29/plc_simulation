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
│  │ Drive Models:      │  │ White noise  │  │ GE CPE305 %R6000+  │  │
│  │  AC Motor + VFD    │  │ Pink (1/f)   │  │ Word-swapped F32   │  │
│  │  Iron Roughneck    │  │ EMI (60Hz)   │  │ FC03/FC06/FC16     │  │
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
│  │ Slope Calculator   │  │  manifest    │                           │
│  └────────────────────┘  └──────────────┘                           │
│                                                                      │
│  ┌────────────────────┐                                              │
│  │  ScenarioGenerator │  22 scenario types                           │
│  │  Domain Randomize  │  Section 6.2 distribution                   │
│  │  FaultInjector     │  11 fault modes                             │
│  │  54 connections    │  5 machine types                            │
│  └────────────────────┘                                              │
└──────────────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

1. **Flight simulator pattern**: Physics engine runs ground truth at 100Hz, sensor models corrupt it with machine-type-specific noise, Modbus server exposes identical register layout to real GE CPE305 PLC. Your data pipeline can't tell the difference.

2. **Multi-machine support**: Top drives (AC motor + VFD), iron roughnecks (dual-phase spinner/wrench with handoff), power tongs (load cell torque), and bucking units (calibrated precision). Each machine type has distinct noise profiles, torque measurement methods, and operational characteristics.

3. **Domain randomization (NVIDIA Sim-to-Real)**: Every scenario randomizes friction (log-normal), hydraulic lag, noise amplitude, PID gains, ambient temperature (-20 to 120F), pipe tolerances, compound viscosity, ADC resolution (12-16 bit), gearbox backlash, and EMI amplitude. The real machine is just one sample from this distribution.

4. **Zero-dependency Modbus**: Pure stdlib TCP server (no pymodbus). Supports FC03 Read Holding, FC06 Write Single, FC16 Write Multiple with GE word-swapped FLOAT32 encoding.

5. **Reference-accurate physics**: Torque-turn curves use Farr friction model with temperature/shear-rate-dependent compound friction per API RP 5A3. Pipe specs from API RP 5C1 Table 1 (exact values). Premium connections model 4-phase shoulder contact with seal engagement.

## Machine Types

| Machine | Drive | Torque Measurement | SNR | Key Feature |
|---------|-------|-------------------|-----|-------------|
| Top Drive | AC Motor + VFD | Motor current calc | 45-60 dB | Constant torque/constant power regions, hookload |
| Iron Roughneck | Hydraulic dual-phase | Pressure transducer | 50-65 dB | Spinner -> handoff -> torque wrench transition |
| Power Tong | Hydraulic motor | Load cell (strain gauge) | 55-70 dB | Two-speed, arm compliance, backup tong |
| Bucking Unit | Hydraulic servo | Calibrated load cell | 60-75 dB | Cleanest curves, CNC-grade positioning |

## Quick Start

```bash
# Dependencies (only numpy)
pip install numpy

# Generate 100 training scenarios (all machine types, all connections)
python generate_dataset.py --count 100 --output ./data/synthetic

# Filter by machine type
python generate_dataset.py --count 50 --machine-type top_drive

# Filter by connection type
python generate_dataset.py --count 50 --connection-type PREMIUM

# Single scenario (debugging)
python generate_dataset.py --single normal_casing_ltc --pipe 7in_23lb_N80_LTC -o ./debug

# Real-time mode with Modbus server
python generate_dataset.py --realtime --modbus-port 5020

# Custom output rate (default 100Hz)
python generate_dataset.py --count 50 --output-rate 10
```

## Output Format

```
data/synthetic/
├── sensor/         # Noisy data (train your AI on this)
├── truth/          # Ground truth (validate against this)
├── events/         # State transitions, faults, shoulder detection
├── manifest.csv    # Index with metadata (machine_type, connection_type, fault_code)
└── stats.txt       # Distribution statistics (scenario, machine, connection, pipe)
```

### Sensor Data Columns (19)
`time, encoder_counts, rpm, torque_ftlbs, pressure_psi, oil_temp_f, pid_setpoint, pid_error, pid_output, operating_mode, connection_state, target_torque, turns, fault_code, peak_torque, hookload_klbs, shoulder_torque, slope_dT_dN, connection_count`

### Ground Truth Columns (36)
All sensor columns plus: `valve_command_pct, valve_spool_pct, oil_viscosity_cst, motor_mech_efficiency, string_twist_deg, connection_rpm, motor_torque_ftlbs, thread_damage, compound_friction_kf, leakage_flow_gpm, pump_ripple_psi, manifold_temp_f, motor_case_temp_f, peak_torque_ftlbs, hookload_klbs, shoulder_torque_ftlbs, slope_dT_dN, connection_count, machine_phase, vfd_frequency_hz, vfd_current_pct, motor_speed_rpm`

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

## PLC Register Map (GE CPE305 — Section 5.1.1)

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

## Fault Code Bitmask (%R6020)

| Bit | Value | Fault |
|-----|-------|-------|
| 0 | 0x0001 | Over-torque |
| 1 | 0x0002 | Under-torque |
| 2 | 0x0004 | Cross-thread |
| 3 | 0x0008 | Galling |
| 4 | 0x0010 | Stall |
| 5 | 0x0020 | Over-temperature |
| 6 | 0x0040 | Stick-slip |
| 7 | 0x0080 | Stripped thread |
| 8 | 0x0100 | Misaligned stabbing |
| 9 | 0x0200 | Wrong compound |
| 10 | 0x0400 | Washout |
| 11 | 0x0800 | Connection jump |

## Live Data Capture & Detection

For connecting to a real PLC via eWon VPN and running inference.

### capture_live.py — Real-Time Modbus Data Recorder

Connects to PLC via eWon VPN, polls registers at configurable rate, logs timestamped sensor data to CSV. Zero external dependencies (raw Modbus TCP over socket).

```bash
# Step 1: Discover active registers on Steve's PLC
python capture_live.py --host 10.0.0.1 --discover

# Step 2: Capture at 10 Hz (default), single continuous file
python capture_live.py --host 10.0.0.1

# Step 3: Capture at 20 Hz, one CSV per detected connection
python capture_live.py --host 10.0.0.1 --hz 20 --segment-mode segmented

# Custom register map (if PLC layout differs from simulator defaults)
python capture_live.py --host 10.0.0.1 --register-map my_registers.json
```

Key features:
- **Discovery mode** (`--discover`): Scans registers to find active addresses
- **Block reads**: Single Modbus request for R6000-R6030 (critical over VPN latency)
- **GE CPE305 FLOAT32**: Word-swapped decoding matching the simulator exactly
- **Connection segmentation**: Detects makeup boundaries from RPM/torque/state transitions
- **Auto-reconnect**: Exponential backoff on VPN drops
- **Live dashboard**: Real-time console display of torque, RPM, pressure, state, faults

### detect_live.py — InceptionTime Fault Detection

Runs trained ensemble inference on captured data or live Modbus stream. Same 12-channel feature pipeline as training.

```bash
# Offline: batch process captured CSVs
python detect_live.py offline \
    --input ./live_captures \
    --model ./results/checkpoints/model.pt \
    --norm ./results/checkpoints/norm_params.json

# Live: real-time Modbus polling + sliding window inference
python detect_live.py live \
    --host 10.0.0.1 \
    --model ./results/checkpoints/model.pt \
    --norm ./results/checkpoints/norm_params.json

# With custom alerting thresholds
python detect_live.py live \
    --host 10.0.0.1 \
    --model ./model.pt --norm ./norm.json \
    --alert-consecutive 3 --alert-confidence 0.7
```

Key features:
- **Offline mode**: Per-window predictions CSV + detection summary with alert counts
- **Live mode**: Sliding window buffer, inference every N samples, consensus alerting
- **Consensus alerts**: Requires N consecutive fault predictions above confidence threshold before triggering
- **Severity levels**: OK / WARN (over/under torque, wrong compound) / CRIT (cross-thread, galling, stall)
- **Prediction logging**: Optional CSV log of every inference result

### Deployment Sequence

1. Steve connects eWon to active rig
2. Run `capture_live.py --discover` to map actual register addresses
3. Run `capture_live.py` to record a few hours of normal operations
4. Retrain model with corrected labels (current blocker)
5. Run `detect_live.py offline` on captured data to validate model
6. Go live with `detect_live.py live` for real-time fault detection

## Module Structure

| File | Purpose | Lines |
|------|---------|-------|
| `config.py` | All constants, pipe catalogs, machine specs, domain randomization | ~1,060 |
| `physics_engine.py` | Drive models, torque-turn, PID, thermal, shoulder detection | ~1,620 |
| `sensor_models.py` | Machine-type-specific noise corruption pipeline | ~305 |
| `scenario.py` | Scenario generation, fault injection, distribution weights | ~580 |
| `runner.py` | Orchestrator: physics + sensors + Modbus + CSV output | ~390 |
| `modbus_server.py` | Zero-dependency Modbus TCP server (FC03/FC06/FC16) | ~255 |
| `generate_dataset.py` | CLI entry point with filtering and statistics | ~280 |
| `capture_live.py` | Live Modbus TCP data recorder via eWon VPN | ~430 |
| `detect_live.py` | Real-time InceptionTime ensemble fault detection | ~520 |

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

## Calibration

When connected to real machine via eWon VPN:
1. Run `capture_live.py --discover` to verify register layout
2. Run `capture_live.py` during threading cycles to record real data
3. Compare real torque-turn curves to simulated
4. Adjust `config.py` friction factor, hydraulic tau, PID gains
5. Re-generate dataset with matching domain randomization
6. Target TSTR ratio > 0.90

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
