# Local Test Runbook
## Full-stack verification before touching the real PLC

Run every step in order. Each one gates on the previous passing.

---

## 0. Prerequisites

```bash
python -m pip install pymodbus==3.13.* numpy fastapi uvicorn openpyxl paramiko pytest pytest-asyncio psutil pyyaml
```

Python 3.11+ required.

---

## 1. Run the unit test suite

```bash
python -m pytest hxi_optimizer/tests/ -q
```

**Expected:** `1065 passed in ~19s`

Confirms: safety gate layers, register map, CUSUM, DNIAE, state machine,
config loading, audit logger, persistence, modbus wrapper.

---

## 2. Start the local simulator

Terminal 1:
```bash
python -m local_test.sim_plc --port 5020
```

**Expected output:**
```
sim_plc INFO HXI sim PLC listening on 127.0.0.1:5020
sim_plc INFO Pre-loaded RPM=60.0 bounds=[400,600] setpoint=60.0 temp=55C
sim_plc INFO Press Ctrl-C to stop
```

The simulator:
- Listens on loopback TCP:5020 (not 502 — no admin needed)
- Implements FC03 (read holding), FC16 (write multiple), FC06 (write single)
- Uses **ABCD** byte order (matches MASTER_CONTEXT §3.4)
- Mirrors writes to `%R06603/%R06604` into readback `%R06610/%R06611`
- Runs a background plant model: RPM drifts toward swash output + Gaussian noise

Leave this running for all subsequent steps.

---

## 3. Commissioning tests against the simulator

Terminal 2:
```bash
python -m hxi_optimizer.deploy.commissioning_tests --test all \
    --host 127.0.0.1 --port 5020 --trials 50
```

**Expected: all 4 tests pass**
- **Test 1 (byte order):** `PASS: ABCD` — confirms round-trip encode/decode
- **Test 2 (FC16 atomicity):** `0 cross-faults across 50 writes`
- **Test 3 (VPN latency):** `P95 < 1ms` (loopback)
- **Test 4 (noise floor):** measures σ of simulated RPM over 60s

---

## 4. Boot the optimizer in Phase A against the simulator

`hxi_optimizer/hxi_config.json` is already pre-populated for local test:
```json
{
  "plc_host": "127.0.0.1",
  "plc_port": 5020,
  "phase": "A",
  "safety": {"abs_min_lower": 50, "abs_max_lower": 700,
             "abs_min_upper": 300, "abs_max_upper": 950,
             "min_band_counts": 50},
  "require_verified_word_order": false
}
```

Terminal 2:
```bash
python -m hxi_optimizer.main
```

**Expected startup sequence (within ~2s):**
```
main INFO === HXI Smart Slide Adaptive PID Optimizer starting ===
audit INFO AuditLogger writing to .../logs/audit.log
csv_logger INFO CSV logger writing to: .../logs/drill_<timestamp>.csv
main INFO Connecting to PLC at 127.0.0.1:5020
main INFO Connection status: True
main INFO Starting main loops (Phase A)
main INFO Dashboard available at http://localhost:8420
main INFO read_loop: starting (2 Hz, drift-compensating)
main INFO heartbeat_loop: starting (5 s)
main INFO analysis_loop: starting (Phase=A)
main INFO connection_monitor: starting (5 s)
dashboard INFO Dashboard starting at http://0.0.0.0:8420
```

**Every 10s** (analysis_loop):
```
main INFO [ADVISORY phase=A] bounds=[388,612] DNIAE=0.962 mode=BIAS sat=0.00
```

Leave it running. Phase A means NO writes to the PLC (only the 5s heartbeat).

---

## 5. Open the dashboard

Browser: http://localhost:8420

**Visual check:**
- Connection dot is green
- Phase badge says "Phase A", State "BASELINE"
- Live Telemetry card shows RPM ticking, swash bar renders
- Register Scanner: click "Load Map" — 205 registers across 10 sections
- Register Scanner: click "Live Scan" — values populate from the simulator
- Simulation Sandbox: pick "Oscillation", click Run — chart draws, metrics table fills
- Operator Controls: toggle Disable/Enable — state badge changes

---

## 6. API smoke test

Terminal 3:
```bash
# All endpoints return 200 + valid JSON
curl -s http://127.0.0.1:8420/api/status | head
curl -s http://127.0.0.1:8420/api/registers | python -c "import sys,json; d=json.load(sys.stdin); print('total:', d['total'], 'active:', d['active'])"
curl -s http://127.0.0.1:8420/api/registers/scan | python -c "import sys,json; d=json.load(sys.stdin); print('scanned:', d['register_count'])"
curl -s -X POST http://127.0.0.1:8420/api/simulate \
    -H 'Content-Type: application/json' \
    -d '{"scenario":"bias","duration_s":60,"setpoint":60}' \
    | python -c "import sys,json; d=json.load(sys.stdin); print('samples:', d['sample_count'], 'labels:', d['label_distribution'])"
```

**Expected:**
```
{"ts":..., "connection":{"healthy":true}, "phase":"A", ...}
total: 205 active: 136
scanned: 187
samples: 120 labels: {'NORMAL': 97, 'BIAS': 23}
```

---

## 7. Training data pipeline (no GPU needed for data gen)

```bash
# Generate small sim dataset
python -m training.generate_dataset --per-scenario 10 --output local_test/sim_dataset.npz

# Window it
python -m training.prepare_windows --input local_test/sim_dataset.npz \
    --output local_test/windows.npz --window-size 40 --stride 10
```

**Expected:**
- `sim_dataset.npz` ~60,000 samples, 7 failure modes represented
- `windows.npz` ~5,994 windows of shape `(40, 7)`

**Training itself (`train_classifier.py` etc) requires TensorFlow and is
designed to run on the remote GPU machine via `training.auto_pipeline`.**
Don't install TF locally — it defeats the purpose of the SSH orchestrator.

---

## 8. Training SSH dry run

```bash
cp training/remote_config.template.yaml training/remote_config.yaml
# edit: set host, user, ssh_key_path to a real GPU machine

python -m training.auto_pipeline --config training/remote_config.yaml --dry-run
```

**Expected:** prints what the pipeline would do, no network calls.

If you have a GPU machine available, set `--setup-only` to just bootstrap
the remote venv without running training:

```bash
python -m training.auto_pipeline --config training/remote_config.yaml --setup-only
```

---

## 9. Safety gate forced-fail scenarios (manual verification)

With the optimizer running in Phase A (no writes), use the dashboard to:

### 9a. Force ESD
In Terminal 3 (direct Modbus write to simulator):
```bash
python -c "
import asyncio
from pymodbus.client import AsyncModbusTcpClient
async def main():
    c = AsyncModbusTcpClient('127.0.0.1', port=5020)
    await c.connect()
    # Set %R06665 bit 0 = 1
    await c.write_registers(address=6664, values=[1], device_id=1)
    c.close()
asyncio.run(main())
"
```
Dashboard should show **ESD active (red pulsing state badge)** within 2s.

Clear it:
```bash
python -c "
import asyncio
from pymodbus.client import AsyncModbusTcpClient
async def main():
    c = AsyncModbusTcpClient('127.0.0.1', port=5020)
    await c.connect()
    await c.write_registers(address=6664, values=[0], device_id=1)
    c.close()
asyncio.run(main())
"
```

### 9b. Force bump lockout
Set `%R06627` (bump_flag_fwd) = 1 the same way. Dashboard shows "LOCKED".

### 9c. Disable/enable via dashboard
Click the red "Disable Adaptive" button — state flips to DISABLED.
Click green "Enable Adaptive" — back to BASELINE.

### 9d. Change phase
Phase dropdown → "B - Advisory" → Set. Log now shows `[ADVISORY phase=B]`.

**Do NOT promote to Phase C against the simulator** — the simulator's safety
limits are test values, not real hardware bounds. Phase C writes require
Steve's sign-off on real hardware limits.

---

## 10. Graceful shutdown

Ctrl-C the optimizer. Expected:
```
main INFO Received signal ... — initiating shutdown
main INFO Shutdown: persisting final state
main INFO Shutdown: stopping CSV logger
main INFO Shutdown: closing Modbus client
main INFO === HXI Optimizer shutdown complete ===
```

Then Ctrl-C the simulator.

Check:
- `hxi_optimizer/logs/audit.log` has a header + any writes attempted (none in Phase A)
- `hxi_optimizer/logs/drill_<ts>.csv` has 2 Hz samples for the duration the optimizer ran
- `state/state.json` + `state/state.json.bak` rotate on each run

---

## Known limitations of the simulator

- Simple first-order plant (no actual hydraulic dynamics — use `training/simulator.py` for realistic data)
- Single device (unit_id=1), no multi-drop support
- No simulated VPN jitter / dropout (use `--drop-rate` flag if added later)
- Temperature drifts randomly instead of following load
- Does not simulate PLC reboot (counter reset)

For realistic-dynamics scenarios, use the `/api/simulate` dashboard endpoint
or `training.scenarios` directly — they use the calibrated
`HydraulicTopDriveSimulator` from the spec.

---

## Checklist before touching the real PLC

- [ ] Unit tests: 1,065 pass
- [ ] Commissioning tests 1–4: all pass against simulator
- [ ] Optimizer boots, connects, runs analysis_loop for ≥60s
- [ ] Dashboard: all 9 endpoints return 200
- [ ] Register Scanner reads 205 registers, scans return live values
- [ ] Simulation Sandbox runs all 9 scenarios without error
- [ ] ESD / bump / disable / enable / phase change all work
- [ ] Training data pipeline generates and windows without error
- [ ] Remote-training dry-run prints expected plan
- [ ] Shutdown is clean; state files rotate

Once every box is checked, swap `plc_host`, `plc_port`, and safety limits in
`hxi_config.json` for the real PLC, re-run the commissioning tests against
the real CPE305, **commit the confirmed VERIFIED_WORD_ORDER into
`comms/register_map.py`**, and only then bring up the production service
via `deploy/install_service.bat`.
