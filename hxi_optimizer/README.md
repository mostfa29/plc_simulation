# `hxi_optimizer/` — Production service package

Single-process asyncio optimizer for TESCO 250T HXI 800HP top drives. This is the deployable. `training/` and `local_test/` are not needed at runtime.

See repo root docs for the full picture: [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md), [../docs/SAFETY.md](../docs/SAFETY.md), [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).

---

## Layout

```
hxi_optimizer/
├── main.py                      ← asyncio entry point, 5-task gather
├── hxi_config.py                ← Config + Phase + SafetyLimitsConfig dataclasses
├── hxi_config.template.json     ← copy to hxi_config.json, fill in, restart
│
├── comms/
│   ├── modbus_client.py         AsyncModbusTcpClient wrapper (FC03 + FC16 only)
│   ├── opcua_transport.py       Alternate transport (Python 3.11 only)
│   ├── transport.py             Factory — picks modbus or opcua from config
│   ├── register_map.py          GE register addresses + FLOAT32 word-order gate
│   ├── register_scanner.py      Parse Register_List.xlsx into a RegisterCatalog
│   ├── fleet.py                 Equipment catalog (HXI / HXI HT / Warrior / ...)
│   ├── machine_registry.py      Loads profiles/ + catalog, returns MachineRecord
│   ├── profiles/                Per-rig register maps (YAML)
│   └── ecatcher.py              Talk2m API + log parser + adapter scan
│
├── control/
│   ├── safety_gate.py           9-layer SafetyGate — SOLE write path
│   ├── pid_advisor.py           Bounds advisor + integral trim
│   └── oscillation_tuner.py     Bump-angle advisor (Phase D only)
│
├── monitoring/
│   └── performance_metrics.py   DNIAE, CUSUM, ACF fallback classifier,
│                                 ONNX classifier + AE, thread-safe model swap
│
├── intelligence/
│   ├── diagnosis.py             Rule-based diagnoses from metrics
│   ├── trend_analyzer.py        Multi-day patterns from CSV logs
│   ├── fleet_triage.py          Rank rigs by attention score
│   ├── digest.py                Plain-language "what's happening now"
│   ├── model_registry.py        Per-rig model discovery + resolution
│   └── compare_models.py        A/B compare default vs per-rig on real data
│
├── io_logging/                  NOT `logging/` (stdlib shadow avoidance)
│   ├── csv_logger.py            Crash-safe CSV, 5 s fsync
│   ├── audit_logger.py          Per-write fsync audit
│   └── realtime_dataset.py      Operator-labeled episode capture
│
├── state/
│   ├── persistence.py           Atomic JSON (tmp → fsync → rename)
│   └── machine_state.py         Per-machine uptime + event history
│
├── dashboard/
│   ├── server.py                FastAPI + WebSocket + auth + audit + timeouts
│   └── static/index.html        SPA, no build step, localStorage token
│
├── deploy/
│   ├── commissioning_tests.py   Phase A tests (byte order, FC16, bounds)
│   ├── install_service.bat      NSSM Windows Service installer
│   ├── uninstall_service.bat
│   └── windows_hardening.ps1    Power plan + firewall + service user
│
├── models/                      ← deployed ONNX pair (default + per-rig)
│   ├── classifier.onnx
│   ├── classifier_meta.json
│   ├── autoencoder.onnx
│   ├── autoencoder_meta.json
│   └── per_rig/<slug>/          ← fine-tuned models, never overwrite default
│
├── logs/                        ← writable at runtime
│   ├── optimizer.log
│   ├── audit.log                ← per-write fsync; safety trail
│   ├── drill_<epoch>.csv
│   └── dataset/                 ← captured real episodes for fine-tuning
│
└── tests/                       ← 2,145 tests across 22 files
```

---

## Run

```bash
# Local against simulated PLC (Terminal 1: python -m local_test.sim_plc)
python -m hxi_optimizer.main

# Production (Windows Service)
sc start HXIOptimizer
```

Dashboard: `http://localhost:8420` — token prompt on first load if `dashboard_token` is set in config.

---

## Test

```bash
python -m pytest tests/ -q
```

**2,145 tests, ~60 s.** Highlights:

| Suite | Count | What |
|---|---|---|
| `test_safety_gate*.py` | ~400 | All 9 gate layers + state machine |
| `test_pid_advisor.py` | ~120 | Advisor logic, integral trim |
| `test_performance_*.py` | ~380 | DNIAE, CUSUM, saturation, anomaly detection |
| `test_ml_classifier.py` | ~40 | ONNX inference per scenario |
| `test_autoencoder.py` | ~30 | AE inference, threshold, separation |
| `test_model_registry.py` | 21 | Per-rig resolution, thread-safe hot-swap |
| `test_compare_models.py` | 13 | A/B recommend logic |
| `test_fine_tune.py` | 16 | Dataset loading, validation gate, recalibration |
| `test_simulator_v2.py` | 13 | Sim physics invariants |
| `test_dashboard_prod.py` | 24 | Auth, audit, timeouts, body limits, WS |
| `test_integration*.py` | ~120 | End-to-end with simulated PLC |
| `test_realtime_dataset.py` | ~30 | Episode capture, auto-segmentation |
| `test_ecatcher.py` | ~30 | Talk2m / log / adapter detection |
| `test_equipment_coverage.py` | ~20 | Every catalog type has a spec |
| ... | ... | ... |

`-x` stops at first failure. `-v` for per-test detail.

---

## Configure

See [hxi_config.template.json](hxi_config.template.json). Minimum for first boot:

```json
{
  "plc_host": "<ip-or-hostname>",
  "phase": "A",
  "safety": {
    "abs_min_lower": <from-commissioning>,
    "abs_max_lower": <from-commissioning>,
    "abs_min_upper": <from-commissioning>,
    "abs_max_upper": <from-commissioning>
  }
}
```

Production additions (see [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) for full detail):

```json
{
  "dashboard_token": "<random-32-char>",
  "talk2m_account": "<account>",
  "talk2m_username": "<user>",
  "talk2m_password": "<pass>",
  "talk2m_developer_id": "<dev-id>",
  "ecatcher_poll_interval_s": 30.0
}
```

---

## Invariants (do not break)

1. All PLC writes go through `SafetyGate.validate_and_write()`. No other write path exists.
2. `Phase < C` means no writes. Gate refuses.
3. `VERIFIED_WORD_ORDER` and all `safety.abs_*` default to `None`. Service won't start until commissioning fills them in.
4. ML never writes. Classifier + AE feed `PerformanceMetrics` → `PIDAdvisor.advise()` → gate.
5. Per-rig fine-tunes write to `models/per_rig/<slug>/`. Default is never overwritten.
6. Every operator action audits to `audit.log`. One durable trail for safety + ops.

Full rules: [../docs/SAFETY.md](../docs/SAFETY.md).
