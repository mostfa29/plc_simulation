# HXI Optimizer

Adaptive supervisory PID-bounds optimizer for **TESCO 250T HXI 800HP** top drives (GE CPE305 PLC, eWon Flexy 205 VPN). Monitors one rig at a time, closes bounds on the PLC's swash-plate clamp registers through a 9-layer safety gate, and learns per-rig behavior from real captured episodes.

- **Not a replacement for the PLC's PID loop.** The inner loop (10–20 ms) is untouched. This is a BPCS supervisory layer at 0.1 Hz on top.
- **Writes are gated.** Every write passes `SafetyGate.validate_and_write()`. No bypass paths exist.
- **Models never write.** ML output feeds `PIDAdvisor.advise()`, which feeds the gate.

---

## Quick start (local, no PLC)

```bash
python -m pip install -r requirements.txt   # or list below
# Terminal 1 — simulated PLC on 127.0.0.1:5020
python -m local_test.sim_plc
# Terminal 2 — optimizer + dashboard
python -m hxi_optimizer.main
```

Open http://localhost:8420 → live telemetry, phase controls, simulation sandbox, A/B model compare, audit trail.

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

| Topic | Doc |
|---|---|
| System components + data flow | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Safety gate, phase plan, invariants | [docs/SAFETY.md](docs/SAFETY.md) |
| NSSM install, config, auth setup | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| Day-to-day operation (Steve) | [docs/OPERATION.md](docs/OPERATION.md) |
| Sim → train → fine-tune → deploy | [docs/ML_PIPELINE.md](docs/ML_PIPELINE.md) |
| Dashboard endpoints, auth, WebSocket | [docs/DASHBOARD.md](docs/DASHBOARD.md) |
| Fleet catalog, register maps, eCatcher, per-rig models | [docs/FLEET.md](docs/FLEET.md) |
| Adding scenarios, diagnosis rules, retraining | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| Common issues + fixes | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) |

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
