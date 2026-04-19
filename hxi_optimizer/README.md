# HXI Smart Slide Adaptive PID Optimizer

Production supervisory service for the TESCO 250T HXI 800HP top drive
(GE CPE305 PLC, eWon Flexy 205 VPN). Implements the architecture in
[MASTER_CONTEXT_FOR_CLAUDE_CODE.md](../MASTER_CONTEXT_FOR_CLAUDE_CODE.md).

Built-from-scratch replacement for the legacy capture/ML pipeline that existed
in the repo. The old code did not have a safety gate, wrote nothing to the PLC,
and decoded FLOAT32 with the wrong byte order.

---

## Layout

```
hxi_optimizer/
├── main.py                     asyncio entry point — 5-task gather:
│                                 read / heartbeat / analysis / conn / dashboard
├── hxi_config.py               Config / Phase / SafetyLimitsConfig dataclasses
├── hxi_config.template.json    copy to hxi_config.json and fill in
│
├── comms/
│   ├── modbus_client.py        AsyncModbusTcpClient wrapper (FC03 + FC16 only)
│   ├── register_map.py         GE register addresses + FLOAT32 word-order gate
│   └── register_scanner.py     Parse Register_List.xlsx into a RegisterCatalog
│
├── control/
│   ├── safety_gate.py          9-layer SafetyGate — SOLE write path
│   ├── pid_advisor.py          Gain-scheduled bounds + sign-based integral trim
│   └── oscillation_tuner.py    Bump-angle advisor (Phase D only)
│
├── monitoring/
│   └── performance_metrics.py  DNIAE, CUSUM, ACF classifier, saturation analysis
│
├── io_logging/                 NOT `logging/` (stdlib shadow avoidance)
│   ├── csv_logger.py           Background-thread CSV, 5 s fsync
│   └── audit_logger.py         Per-write fsync audit trail
│
├── state/
│   └── persistence.py          Atomic JSON writes: tmp → fsync → rename
│
├── dashboard/                  Operator UI (FastAPI + WebSocket)
│   ├── server.py               Runs as 5th asyncio task
│   └── static/index.html       Single-page dashboard, no build step
│
├── deploy/
│   ├── commissioning_tests.py  Phase A tests 1–4 (byte order, FC16, VPN, noise)
│   ├── install_service.bat     NSSM Windows Service installer
│   ├── uninstall_service.bat
│   └── windows_hardening.ps1   Power plan + Defender + Windows Update
│
├── logs/                       runtime CSVs, audit.log, optimizer.log
└── tests/                      1,065 parametrized pytest tests (~18 s)
```

---

## Bring-up sequence

Follow this order. Steps gate on each other — the system refuses to start if
prerequisites are missing.

### 1. Install dependencies (Python 3.11+)
```bash
pip install pymodbus==3.13.* numpy psutil fastapi uvicorn openpyxl paramiko pytest pytest-asyncio
```

### 2. Configure
```bash
cp hxi_optimizer/hxi_config.template.json hxi_optimizer/hxi_config.json
# Edit:
#   - plc_host: eWon VPN IP of the PLC
#   - safety.abs_min/max_lower/upper: FROM STEVE'S SIGN-OFF
#   - drill_depth_ft: current well depth
```

### 3. Commission the byte order (blocks startup)
```bash
python -m hxi_optimizer.deploy.commissioning_tests --test byte_order --host <PLC_IP>
```
Test 1 writes 1234.5 to spare %R06630 and determines whether the PLC uses
ABCD (big-endian, §3.4 of spec) or CDAB (low-word-first, legacy assumption).

**Commit the result** into [comms/register_map.py](comms/register_map.py):
```python
VERIFIED_WORD_ORDER = "ABCD"   # or "CDAB" — from test result
```
Until this constant is set, every float decode raises `RuntimeError`.

### 4. Run remaining commissioning tests
```bash
python -m hxi_optimizer.deploy.commissioning_tests --test all --host <PLC_IP>
```
- **Test 2** (FC16 atomicity): 1,000 paired writes to spare registers, checks
  for cross-faults. Must be zero.
- **Test 3** (VPN latency): 100 reads, records mean / P95 / P99.
- **Test 4** (noise floor): 60 s at steady RPM, measures σ. Used to set
  `deadband_rpm` in config.

### 5. Phase A — observer mode (24 h+)
```bash
python -m hxi_optimizer.main
```
Dashboard auto-starts at http://localhost:8420. System reads at 2 Hz, logs
to CSV, never writes to the PLC. Collect 24 h+ of data at multiple operating
points (RPM × pressure × depth combinations).

### 6. Phase B — advisory mode
Edit `hxi_config.json`: `"phase": "B"`. Optimizer now computes recommended
bounds every 10 s and logs them as `[ADVISORY] bounds=[L,U] DNIAE=X mode=Y`.
Still no writes. Drilling engineer reviews advisory log for correctness.

### 7. Phase C — limited authority (requires sign-off)
All 25 items in MASTER_CONTEXT §13 must be signed before Phase C. SafetyGate
then permits writes via FC16 with full rollback protection.

### 8. Phase D — full authority
After 2+ drilling stands with zero rollbacks, promote to Phase D. Gain schedule
populated from Phase C data. Oscillation tuner writes enabled once %R06629
interpretation is confirmed by Steve.

---

## Service install (Windows)

```cmd
REM 1. (Once, as Admin) harden the host
powershell -ExecutionPolicy Bypass -File deploy\windows_hardening.ps1

REM 2. Install NSSM service (edit ROOT inside the .bat first)
deploy\install_service.bat

REM 3. Verify
sc query HXIOptimizer
```

The service runs in Session 0, independent of user login. Restart policy:
5 s → 30 s → 60 s exponential backoff over a 24-hour reset window. Logs go to
`hxi_optimizer/logs/service_stdout.log` and `service_stderr.log`.

---

## Operator dashboard

http://localhost:8420 (or LAN access on the rig PC's IP).

**Cards:**
- **Live Telemetry** — RPM, setpoint, error, swash position bar, temperature
- **Performance** — DNIAE, failure mode, saturation bars, windup/change flags
- **Safety Gate** — state machine, rejection count, heartbeat, cooldown, LKG
- **Adaptation** — trim upper/lower, dwell timer, total adaptations
- **Operator Controls** — enable/disable, phase promote, depth setter
- **Connection** — PLC host, deadband, WebSocket health
- **Register Scanner** — auto-loads `Register_List.xlsx`, live FC03 scan,
  filter by section, highlights writable registers
- **Simulation Sandbox** — pick a scenario (normal / bias / oscillation /
  stick-slip / formation change / sluggish / windup / deadband hunting /
  connection), run it, view labelled RPM timeline + per-window metrics
- **Audit Trail** — last 200 safety events (WRITE/REJECTED/ROLLBACK/ESD/ACCEPTED)

---

## Testing

```bash
python -m pytest hxi_optimizer/tests/ -v
```

1,065 tests, ~18 s. Coverage:

| File | Tests | Focus |
|---|---|---|
| test_register_map.py | 181 | FLOAT32 gate, ABCD/CDAB, encode round-trips, signed int16, ESD bits |
| test_safety_gate.py | 169 | All 9 layers, state machine, accept/reject, rollback, LKG |
| test_edge_cases.py | 138 | All 28 register positions, bit patterns, extremes, audit stress |
| test_safety_gate_extended.py | 119 | Boundary ±1 matrix, rate-limiter matrix, state transitions |
| test_oscillation_tuner.py | 83 | K, f₁, resonance ±20%, reactive torque, adaptation |
| test_performance_metrics.py | 81 | CUSUM, filter, DNIAE, classification, saturation, windup |
| test_performance_extended.py | 75 | CUSUM sensitivity, filter numerics, saturation eps boundaries |
| test_pid_advisor.py | 49 | Dead zone, dwell, expand/contract, trim projection |
| test_modbus_client.py | 36 | safe_read/write, is_healthy, PLC restart detection |
| test_config.py | 33 | Phase enum, config loading matrix, template dump |
| test_loggers.py | 30 | Audit thread safety, CSV daemon, build_csv_row |
| test_integration_extended.py | 28 | Multi-cycle accept, VPN drop, operator workflows |
| test_persistence.py | 25 | Atomic write, .bak rotation, corrupt fallback |
| test_integration.py | 18 | Full pipeline, ESD/bump, 5-rejection→DISABLED |

---

## Hard rules (NEVER violate)

1. **All PLC writes go through `SafetyGate.validate_and_write()`.**
2. **Paired writes use FC16 only.** FC06 is not exposed.
3. **`VERIFIED_WORD_ORDER` and `SafetyLimitsConfig` defaults are `None`** — the
   system refuses to start otherwise.
4. **Phase < C means no writes.** `analysis_loop` only logs advisory bounds.
5. **Do not bypass the gate for "just testing".** Use the simulator instead.
6. **ML models never write directly.** They feed `PIDAdvisor.advise()`, which
   feeds the gate.
