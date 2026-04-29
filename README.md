# HXI Optimizer

Adaptive supervisory PID-bounds optimizer for **TESCO 250T HXI 800HP** top drives (GE CPE305 PLC, eWon Flexy 205 VPN). Monitors one rig at a time, closes bounds on the PLC's swash-plate clamp registers through a 9-layer safety gate, and learns per-rig behavior from real captured episodes.

- **Not a replacement for the PLC's PID loop.** The inner loop (10–20 ms) is untouched. This is a BPCS supervisory layer at 0.1 Hz on top.
- **Writes are gated.** Every write passes `SafetyGate.validate_and_write()`. No bypass paths exist.
- **Models never write.** ML output feeds `PIDAdvisor.advise()`, which feeds the gate.

---

## Quick start — one command

```bash
python -m pip install -r requirements.txt   # or list below
python run.py                               # auto: sim if no real PLC configured
```

That's it. The launcher handles everything: starts the simulated PLC if needed, starts the optimizer + dashboard, waits for `/healthz` to be green, opens your browser to `http://localhost:8420`. Ctrl+C in the terminal stops everything cleanly.

The dashboard header shows a **Mode** badge: amber **SIM** when running against the bundled simulator, green **REAL** when connected to a physical PLC. Nobody mistakes test data for production data.

| Command | What it does |
|---|---|
| `python run.py` | Auto-detect: SIM if no real `plc_host`, REAL if config has one |
| `python run.py --sim` | Force simulator mode |
| `python run.py --real` | Force real-PLC mode (uses `plc_host` from `hxi_config.json`) |
| `python run.py --no-browser` | Don't auto-open the browser |
| `python run.py --sim-port 5021` | Different sim PLC port |

Or use the existing `hxi.bat` interactive menu (Windows) for a guided experience: bring-up, commissioning, configure, status, logs, backup.

Run the test suite: `python -m pytest hxi_optimizer/tests/ -q` — **2,145 tests** in ~60s.

Full local pre-PLC bring-up: [local_test/RUNBOOK.md](local_test/RUNBOOK.md).

---

## Repo layout

| Path | Purpose |
|---|---|
| [`hxi_optimizer/`](hxi_optimizer/) | Production service — main + dashboard + safety gate + ML inference |
| [`training/`](training/) | Sim-data generation, classifier + autoencoder training, fine-tune pipeline |
| [`local_test/`](local_test/) | Pure-stdlib PLC simulator for pre-PLC verification |
| [`docs/`](docs/) | Detailed documentation (architecture, safety, deployment, operation, …) |
| `phase1_pretraining/` | **Retired** — legacy 9-class pipe-threading classifier (unrelated) |

Authoritative specs (keep for reference, don't edit without reason):

| File | What it is |
|---|---|
| [Register_List.xlsx](Register_List.xlsx) | Canonical register catalog (205 registers, %R06600–%R06700) |
| [docs/SAFETY.md](docs/SAFETY.md) | Safety contract — gate layers, phase plan, invariants |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture — components, data flow, concurrency |

---

## Documentation

Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the overview. Every subsystem has its own page:

| Topic | Doc | Audience |
|---|---|---|
| System components + data flow | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Engineers |
| Safety gate, phase plan, invariants | [docs/SAFETY.md](docs/SAFETY.md) | Engineers |
| NSSM install, config, auth setup | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | IT / installer |
| Day-to-day operation (Steve) | [docs/OPERATION.md](docs/OPERATION.md) | Steve |
| **Test procedure for Steve's team (step-by-step)** | **[docs/TESTING.md](docs/TESTING.md)** | **Crew + IT** |
| **Pin-near-the-PC quick reference** | **[docs/OPERATOR_QUICK_REFERENCE.md](docs/OPERATOR_QUICK_REFERENCE.md)** | **Shift operators** |
| Sim → train → fine-tune → deploy | [docs/ML_PIPELINE.md](docs/ML_PIPELINE.md) | ML engineers |
| Dashboard endpoints, auth, WebSocket | [docs/DASHBOARD.md](docs/DASHBOARD.md) | Engineers |
| Fleet catalog, register maps, eCatcher, per-rig models | [docs/FLEET.md](docs/FLEET.md) | Steve / IT |
| Adding scenarios, diagnosis rules, retraining | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Engineers |
| Common issues + fixes | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | All |

---

## What it does (one screen)

```
┌─────────────────────────────────────────────────────────────────────┐
│              hxi_optimizer/main.py (single asyncio loop)            │
│                                                                     │
│   read_loop(2Hz) ─┐                                                 │
│   heartbeat(0.2Hz)├─► PerformanceMonitor ─► PIDAdvisor ─► SafetyGate│
│   analysis(0.1Hz)─┘     │                       │            │      │
│   conn_monitor(5s)      │                       │            │      │
│                         ▼                       │            ▼      │
│                    ML classifier               advise()   FC16 write│
│                    + autoencoder                            audit   │
│                    (per-rig if deployed)                            │
│                                                                     │
│   Dashboard (FastAPI+WS @ :8420)  ◄── token auth, 2145 tests        │
│   eCatcher monitor (auto-detect which eWon is tunneled)             │
└─────────────────────────────────────────────────────────────────────┘
```

Key guarantees:

1. **No bypass of SafetyGate** — writes go through 9 layers (ESD, bump, abs bounds, consistency, rate, heartbeat, state, FC16+readback, audit) or they don't happen.
2. **Phase gate** — `Phase < C` means zero PLC writes. `Phase = A` at first boot. Promotion is a manual config edit + restart.
3. **Float byte order pinned at commissioning** — `VERIFIED_WORD_ORDER = None` until the byte-order commissioning test passes. Service refuses to start otherwise.
4. **Every operator action is audited** — phase changes, bounds overrides, dataset annotations, machine switches, all land in `audit.log` with old/new values.
5. **Per-rig models never overwrite the default** — `fine_tune.py` writes to `hxi_optimizer/models/per_rig/<slug>/`. Bad fine-tunes never silently replace good ones.

---

## Production status

- **V2 classifier**: 98.05% test accuracy (7 classes, 9 equipment types, 648K windows). See [docs/ML_PIPELINE.md](docs/ML_PIPELINE.md).
- **V2 autoencoder**: 8.42× anomaly/normal separation, 50% detection rate at mean+3σ.
- **Dashboard**: token auth + operator audit + Pydantic validation + 30s endpoint timeouts + graceful shutdown.
- **Per-rig model registry**: hot-swaps classifier when eCatcher switches tunnels. Falls back to sim default if no per-rig model.
- **Fine-tune validation gate**: refuses to deploy if real-data accuracy doesn't beat sim baseline.
- **Tests**: 2,145 passing, including prod-readiness (auth, audit, timeouts, validation) and end-to-end (sim→train→deploy).

---

## License / contact

Private project for Steve (TESCO fleet operator). Questions → see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) first, then [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for coding guidance.
