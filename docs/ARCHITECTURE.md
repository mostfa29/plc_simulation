# Architecture

Single-process asyncio service with one write path, one audit trail, and one dashboard. Every component has a single responsibility; `SafetyGate` is the only thing that can write to the PLC.

---

## Process model

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    hxi_optimizer/main.py                                 │
│                 one asyncio event loop, 5 tasks                          │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  read_loop (2 Hz) ────── ring_buffer(40) ──── analysis_loop (0.1 Hz)     │
│  • FC03 %R06600-6627                           • PerformanceMonitor       │
│  • ABCD float decode                           • PIDAdvisor.advise()      │
│  • sample → csv_queue                          • SafetyGate write (C/D)   │
│                                                                          │
│  heartbeat_loop (5 s) ─── SafetyGate._send_heartbeat → FC16 %R06605      │
│                                                                          │
│  connection_monitor (5 s) ─── checks modbus.is_healthy                   │
│                           ─── every 30s: tick uptime, re-check identity  │
│                           ─── every 30s: eCatcher probe → hot-swap model │
│                                                                          │
│  start_dashboard ─── uvicorn (FastAPI + WebSocket @ :8420)               │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

            ┌─────────── ThreadPoolExecutor (1 worker) ──────────┐
            │ analysis_loop runs PerformanceMonitor.compute_*    │
            │ + ONNX inference here, not on the asyncio loop     │
            │ (so the 2 Hz read cadence can't be starved)        │
            └────────────────────────────────────────────────────┘
```

The ThreadPoolExecutor exists so CPU-bound work (ONNX classifier + autoencoder inference, DNIAE computation, CUSUM) doesn't block the 500 ms read cadence. `read_loop` must keep ticking or `SafetyGate` will eventually trip `COMMS_LOSS_30S` rollback.

---

## Data flow (single sample)

```
 PLC %R06600+ ──FC03──► pymodbus ──► build_sample() ──► ring_buffer.append()
                                                      │
                                                      ▼
                                          csv_queue (bounded, drop-on-full)
                                                      │
                                                      ▼
                                          CrashSafeCSVLogger (bg thread)
                                                      │
                                                      ▼
                                          drill_<timestamp>.csv
                                                      │
 ring_buffer ──20s window──► analysis_loop (in executor)
                                 │
                                 ▼
                       PerformanceMonitor.update_*   (thread-safe model lock)
                                 │
                                 ├──► _classify_ml()  → ONNX classifier
                                 ├──► _compute_anomaly_score() → ONNX AE
                                 └──► compute_metrics() → PerformanceMetrics
                                             │
                                             ▼
                                    PIDAdvisor.advise()
                                             │
                                             ▼
                                    (lower, upper) proposal
                                             │
                                  ┌──────────┴──────────┐
                           Phase A/B                Phase C/D
                          log only           SafetyGate.validate_and_write()
                                                      │
                                                      ├─ ESD check
                                                      ├─ Bump flag check
                                                      ├─ Abs bounds
                                                      ├─ Consistency
                                                      ├─ Rate limit
                                                      ├─ Heartbeat
                                                      ├─ State machine
                                                      ├─ FC16 + readback
                                                      └─ audit.log_write()
                                                             │
                                                             ▼
                                                  PLC %R06603/6604
```

See [SAFETY.md](SAFETY.md) for the gate layers and [ML_PIPELINE.md](ML_PIPELINE.md) for classifier/AE details.

---

## Module map

| Package | What it owns |
|---|---|
| [`hxi_optimizer/comms/`](../hxi_optimizer/comms/) | Modbus/OPC-UA transports, register map, eCatcher monitor, fleet catalog, machine registry |
| [`hxi_optimizer/control/`](../hxi_optimizer/control/) | `SafetyGate`, `PIDAdvisor`, oscillation tuner |
| [`hxi_optimizer/monitoring/`](../hxi_optimizer/monitoring/) | `PerformanceMonitor` — DNIAE, CUSUM, ACF classifier fallback, ONNX classifier + AE, thread-safe model hot-swap |
| [`hxi_optimizer/intelligence/`](../hxi_optimizer/intelligence/) | Diagnosis engine, trend analyzer, fleet triage, digest, model registry, A/B compare |
| [`hxi_optimizer/io_logging/`](../hxi_optimizer/io_logging/) | `CrashSafeCSVLogger`, `AuditLogger` (per-write fsync), real-time dataset capture |
| [`hxi_optimizer/state/`](../hxi_optimizer/state/) | Atomic state persistence, machine state store |
| [`hxi_optimizer/dashboard/`](../hxi_optimizer/dashboard/) | FastAPI app, WebSocket push, Pydantic bodies, auth middleware, operator audit |
| [`hxi_optimizer/deploy/`](../hxi_optimizer/deploy/) | NSSM service install, Windows hardening, commissioning tests |
| [`training/`](../training/) | Sim physics (`simulator.py`), scenarios, dataset generation, classifier/AE trainers, fine-tune |

Each module has tests under [`hxi_optimizer/tests/`](../hxi_optimizer/tests/) — 2,145 passing.

---

## Concurrency model

- **One asyncio loop** owns all I/O (Modbus reads, HTTP serving, WebSocket pushes).
- **One ThreadPoolExecutor worker** runs `analysis_loop` computation + long dashboard endpoints. This worker is bounded to 1 thread so ONNX sessions can be shared without lock contention.
- **`PerformanceMonitor._model_lock`** (`threading.RLock`) guards ONNX model swap vs. inference. `switch_models()` stages new sessions, then atomically swaps under the lock. Inference paths snapshot model references under the same lock before calling `session.run()` — concurrent swaps can't tear a mid-inference call.
- **CSV logger + audit logger** run on their own threads, each with its own lock. Audit does `fsync` after every write.

State read across threads:

- `ring_buffer` — guarded by `buffer_lock` (stdlib `threading.Lock`).
- `latest_metrics` / `latest_bounds` — guarded by `result_lock`.
- `machine_state_store.history` — read without lock from many places; safe only for reads because writes go through `note_connection()` which serializes on the asyncio loop.

---

## Shutdown ordering

`main.py` `finally:` block runs in this exact order (do not change without reading [SAFETY.md](SAFETY.md)):

1. `shutdown_dashboard()` — sends WS close frames (code 1001), flips `uvicorn.server.should_exit = True`. Lets in-flight operator actions write to `audit.log`.
2. `_persist_state()` — LKG bounds, advisor trim, metrics mode → `state.json` (atomic write via `.tmp` → rename).
3. CSV logger sentinel → `join(timeout=5)`.
4. `modbus_mgr.close()`.
5. `executor.shutdown(wait=False)`.
6. `audit.close()`.

If the dashboard blocked (e.g. a stuck WebSocket client), the `timeout_graceful_shutdown=5` on uvicorn forces progress after 5 seconds.
