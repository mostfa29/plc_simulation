# HXI Optimizer

Adaptive supervisory PID-bounds optimizer for the **TESCO 250T HXI 800HP top drive**
(GE CPE305 PLC, eWon Flexy 205 VPN).

The optimizer reads 14 Modbus registers at 2 Hz, monitors PID performance with
DNIAE + CUSUM change detection, and writes recommended swash-plate clamp bounds
(%R06603 / %R06604) back to the PLC through a 9-layer safety gate. Runs 24/7 as
a Windows service, supervised from a built-in operator dashboard.

**Not a replacement for the PLC's PID loop.** The inner loop (10–20 ms) stays
untouched. This is a BPCS supervisory layer operating at 0.1 Hz on top.

---

## What's in this repo

| Directory | Purpose | Status |
|---|---|---|
| [`hxi_optimizer/`](hxi_optimizer/) | Production optimizer service (main deliverable) | **Active** |
| [`training/`](training/) | Automated remote-GPU ML training pipeline | **Active** |
| [`local_test/`](local_test/) | Pure-stdlib PLC simulator + pre-PLC test runbook | **Active** |
| `phase1_pretraining/`, `colab/` | Legacy 9-class fault-classification pipeline | **Retired** |
| `capture_live.py`, `rig_monitor.py`, `fleet_manager.py`, `discover_machine.py` | Legacy capture + fleet tools | **Read-only reference** |

The legacy tree stays in place for reference (register hints, field capture
patterns) but is not part of the current deliverable. See the bottom of this
file for a short retrospective.

---

## Quick start (local, no PLC needed)

Install:
```bash
python -m pip install pymodbus==3.13.* numpy fastapi uvicorn openpyxl paramiko pytest pytest-asyncio psutil pyyaml
```

Run the full stack in two terminals:
```bash
# Terminal 1: simulated PLC on 127.0.0.1:5020
python -m local_test.sim_plc

# Terminal 2: optimizer + dashboard
python -m hxi_optimizer.main
```

Open http://localhost:8420 — live telemetry, register scanner, simulation
sandbox, operator controls, audit trail.

Run the 1,065-test suite:
```bash
python -m pytest hxi_optimizer/tests/ -q
```

Full pre-PLC verification walkthrough: [local_test/RUNBOOK.md](local_test/RUNBOOK.md).

---

## Authoritative documents

| File | What it is |
|---|---|
| [MASTER_CONTEXT_FOR_CLAUDE_CODE.md](MASTER_CONTEXT_FOR_CLAUDE_CODE.md) | 3,113-line spec: register map, physics, safety gate design, deployment |
| [Register_List.xlsx](Register_List.xlsx) | Steve's canonical register catalog (10 sections, 205 registers, %R06600–%R06700) |
| [CODEBASE_DIAGNOSTIC_REPORT.md](CODEBASE_DIAGNOSTIC_REPORT.md) | Pre-build audit — what the codebase was before the rewrite |
| [rebuild_plan.md](rebuild_plan.md) | The plan that produced `hxi_optimizer/` |
| [HXI_ML_TRAINING_PLAN.md](HXI_ML_TRAINING_PLAN.md) | 4-model ML architecture, exact numbers, data pipeline |
| [CLAUDE.md](CLAUDE.md) | Project-level instructions for Claude Code sessions |
| [hxi_optimizer/README.md](hxi_optimizer/README.md) | Package-level README with bring-up + test breakdown |
| [local_test/RUNBOOK.md](local_test/RUNBOOK.md) | 10-step local verification runbook |

**Changing anything safety-relevant requires reading MASTER_CONTEXT first.**

---

## Architecture at a glance

```
┌──────────────────────────────────────────────────────────────────────┐
│                  hxi_optimizer/main.py (asyncio)                     │
│                                                                      │
│  ┌───────────────┐  ┌──────────────┐  ┌─────────────────────────┐    │
│  │  read_loop    │  │  heartbeat   │  │      analysis_loop       │    │
│  │   (2 Hz)      │  │   (0.2 Hz)   │  │      (0.1 Hz)            │    │
│  │ FC03 14 regs  │  │ FC16 %R06605 │  │ PerformanceMonitor       │    │
│  │ decode ABCD   │  │              │  │ PIDAdvisor.advise()      │    │
│  └───────┬───────┘  └──────┬───────┘  └─────────┬───────────────┘    │
│          │                 │                    │                    │
│          ▼                 ▼                    ▼                    │
│  ┌─────────────────────────────────────────────────────┐             │
│  │            SafetyGate (sole write path)             │             │
│  │  1. ESD check   2. Bump flags   3. Abs bounds       │             │
│  │  4. Consistency 5. Rate limit   6. Heartbeat        │             │
│  │  7. State machine  8. FC16 + readback  9. Audit     │             │
│  └─────────────────────────┬───────────────────────────┘             │
│                            │                                         │
│  ┌──────────────┐  ┌───────▼──────┐  ┌─────────────────┐             │
│  │  CSV logger  │  │  ModbusMgr   │  │ Audit logger    │             │
│  │ (bg thread)  │  │ (FC03+FC16)  │  │ (per-write      │             │
│  │  5s fsync    │  │              │  │  fsync)         │             │
│  └──────┬───────┘  └──────┬───────┘  └────────┬────────┘             │
│         │                 │                   │                      │
└─────────┼─────────────────┼───────────────────┼──────────────────────┘
          │                 │                   │
          ▼                 ▼                   ▼
   drill_<ts>.csv     eWon VPN → PLC        audit.log
   raw + decoded
                                         ┌──────────────────┐
     ┌───────────────────┐               │ dashboard/server │
     │ state/state.json  │◄──────────────┤ FastAPI + WS     │
     │ (atomic, .bak)    │               │ http://:8420     │
     └───────────────────┘               └──────────────────┘
```

Every layer has a single, testable responsibility. No shortcuts — writes
**must** go through every `SafetyGate` layer; the gate cannot be bypassed.

---

## Hard rules (never violate)

1. All PLC writes go through `SafetyGate.validate_and_write()`. No exceptions.
2. Paired writes use **FC16 only**. FC06 is not exposed by the Modbus wrapper.
3. `VERIFIED_WORD_ORDER` and `SafetyLimitsConfig` defaults are `None` — the
   service refuses to start otherwise. Commissioning fills them in.
4. `Phase < C` means **no writes**. `analysis_loop` only logs advisory bounds.
5. ML models never write directly. They feed `PIDAdvisor.advise()`, which
   feeds the gate.

See [CLAUDE.md](CLAUDE.md) for full rules and common pitfalls.

---

## Phase plan

| Phase | Writes? | Gate condition |
|---|---|---|
| **A** (Observer) | No | Byte order verified + safety limits populated |
| **B** (Advisory) | No | 24 h+ of Phase A data, drilling engineer review |
| **C** (Limited authority) | **Yes**, gated | All 25 items in MASTER_CONTEXT §13 signed |
| **D** (Full authority) | Yes | 2+ drilling stands in Phase C with zero rollbacks |

The service boots in Phase A. Promotion is a manual edit to
`hxi_optimizer/hxi_config.json` + restart.

---

## Testing

```bash
python -m pytest hxi_optimizer/tests/ -q
```

**1,065 tests across 14 files, ~19 s.** Every SafetyGate layer, every state
transition, every CUSUM edge case, ACF classifier, CSV/audit/persistence
round-trips, full-pipeline integration. Full breakdown in
[hxi_optimizer/README.md](hxi_optimizer/README.md).

Pre-PLC local verification (simulator + commissioning + dashboard +
training-data-gen): follow [local_test/RUNBOOK.md](local_test/RUNBOOK.md).

---

## Deployment

Production runs as an **NSSM Windows Service** on the rig PC.

```cmd
REM Once, as Administrator:
powershell -ExecutionPolicy Bypass -File hxi_optimizer\deploy\windows_hardening.ps1
hxi_optimizer\deploy\install_service.bat
sc query HXIOptimizer
```

Service runs in Session 0 (independent of login), restart-on-failure with
5 s → 30 s → 60 s backoff, logs to `hxi_optimizer/logs/service_stdout.log`.

The legacy `start_hidden.vbs` + Startup-folder approach is **deprecated** —
delete those files after confirming NSSM works.

---

## ML training

Training runs on a **remote GPU** via SSH — never on the rig PC.

```bash
cp training/remote_config.template.yaml training/remote_config.yaml
# edit: host, user, ssh_key_path

# One-command end-to-end: sim data → train → download TFLite → deploy
python -m training.auto_pipeline --config training/remote_config.yaml --real-data --deploy
```

Four models, per [HXI_ML_TRAINING_PLAN.md](HXI_ML_TRAINING_PLAN.md):

| Model | Replaces | Architecture |
|---|---|---|
| Gain scheduler | `PIDAdvisor.get_scheduled_nominal()` lookup table | 3-layer MLP |
| Failure classifier | `PerformanceMonitor._classify()` ACF heuristic | 1D-CNN |
| Condition-change detector | Supplements CUSUM | Convolutional autoencoder |
| Oscillation predictor | `OscillationTuner._adapt()` (Phase D only) | LSTM |

All four run as TFLite on the rig PC (sub-ms inference, no GPU needed at
runtime). Models never bypass `SafetyGate`.

---

## Dashboard

http://localhost:8420 (auto-starts with the optimizer).

- Live telemetry (RPM, swash bar, temperature, DNIAE, saturation)
- Safety gate state machine (BASELINE / TRIAL / ACCEPTED / DISABLED / ESD)
- Register Scanner — auto-parses [Register_List.xlsx](Register_List.xlsx),
  205 registers across 10 sections, live FC03 scan with decoded values
- Simulation Sandbox — pick 1 of 9 drilling scenarios, run it, view
  labelled timeline + per-window performance metrics
- Operator controls — enable/disable adaptive, promote phase (with
  confirmation for C/D), set drill depth
- Audit trail — last 200 safety events, filterable
- Connection health — color-coded dot, WebSocket staleness indicator

---

## What existed before

The repo used to be a legacy **"TopDrive AI"** project — a physics simulator
generating synthetic data for a 9-class fault-classification ML pipeline
(pipe-threading quality: cross_thread, galling, stripped_thread, over_torque,
etc.). That work:

- Never reached production (`F1 = 0.17` best)
- Had zero PLC write capability
- Decoded FLOAT32 with the wrong byte order (never verified against the real CPE305)
- Used a Colab-based training flow that kept expiring mid-run

The [CODEBASE_DIAGNOSTIC_REPORT.md](CODEBASE_DIAGNOSTIC_REPORT.md) audit
(April 2026) documented this in detail. The current `hxi_optimizer/` is a
greenfield rebuild against [MASTER_CONTEXT_FOR_CLAUDE_CODE.md](MASTER_CONTEXT_FOR_CLAUDE_CODE.md).
The legacy files stay for reference (capture patterns, fleet catalog) but
are not part of the deliverable.

---

## License / contact

Private project for Steve (TESCO fleet operator). No external license.

Questions: see [CLAUDE.md](CLAUDE.md) for coding guidance, or open the
dashboard and click through each panel — every feature is self-documenting.
