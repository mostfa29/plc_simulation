# System Testing Guide

Step-by-step verification procedure for Steve's team. Walks through every behavior the system should exhibit, what success looks like, and what to do when something fails.

**Audience**: anyone on the crew or IT — does not assume Python knowledge. The hard parts run themselves; this guide tells you what to look for.

---

## Quick check (2 minutes) — Dashboard "System Test" tab

Before running the full procedure below, do a one-click sanity check:

1. Open the dashboard at **http://localhost:8420**.
2. Click the **System Test** tab.
3. The battery runs automatically (~2 seconds). Each row shows green / amber / red.
4. Click any row to expand and see *what* the check verified and, if failed, *what to do about it*.

**If everything is green** — the system is healthy at this moment. You can either stop here (for a quick health check) or continue with the full procedure below for a deeper verification.

**If anything is red** — fix that first using the per-row "If this needs attention" instructions, then re-run. Don't continue with the full test procedure until the System Test tab is green or only amber.

**What System Test covers** (14 checks, runs server-side):

- Optimizer process up + dashboard responsive
- PLC connection healthy
- Telemetry samples flowing at 2 Hz
- Classifier + autoencoder loaded and inferring
- Model files on disk + per-rig registry resolves
- Float byte order pinned
- Safety limits populated from commissioning
- Phase + gate state are sensible
- Machine identified (eCatcher / Talk2m / config hint)
- Audit log writable
- Real-data capture configured
- Disk space available

This panel is also useful for ongoing monitoring — re-run any time something looks off.

---

## Full test procedure

**Format**: each test has an **ID**, **prerequisites**, **steps** (numbered), **expected result** (what you should see), and **pass/fail criteria** (what makes it green). Print this guide and check off rows as you go. A blank results table is at the bottom for sign-off.

**How long**: ~3–4 hours end-to-end if everything passes. If a test fails, stop and consult [TROUBLESHOOTING.md](TROUBLESHOOTING.md) before continuing.

---

## Test plan structure

| Part | What it proves | Time | PLC required |
|---|---|---|---|
| [Part 1 — Install & smoke](#part-1--install--smoke) | The code runs locally | 30 min | No |
| [Part 2 — Dashboard prod behaviors](#part-2--dashboard-production-behaviors) | Auth, audit, validation, timeouts | 30 min | No |
| [Part 3 — ML pipeline](#part-3--ml-pipeline) | Classifier + autoencoder + per-rig models work | 30 min | No |
| [Part 4 — Safety gate](#part-4--safety-gate) | The gate blocks unsafe writes; rollback works | 30 min | No |
| [Part 5 — Pre-PLC commissioning](#part-5--pre-plc-commissioning-against-real-plc) | Float byte order + safe-bounds + ESD reachable | 60 min | **Yes** |
| [Part 6 — Live Phase A](#part-6--live-phase-a-on-real-plc) | Observer mode collects clean data, no writes | 60+ min | **Yes** |
| [Part 7 — Sign-off](#part-7--sign-off) | Results table | — | — |

---

## Part 1 — Install & smoke

### T1.1 — Install dependencies

**Prereq**: Python 3.14 installed. Verify: `python --version` returns 3.14.x.

**Steps**:
1. Open a terminal at the repo root (the folder containing `README.md`).
2. Run: `python -m pip install -r requirements.txt` (or the deps list in [DEPLOYMENT.md](DEPLOYMENT.md)).

**Expected**: All packages install without errors. Last line shows `Successfully installed ...`.

**Pass**: No red error text.
**Fail**: If `pymodbus`, `onnxruntime`, `fastapi`, or `pydantic` fail to install, check Python version is 3.14 and re-run.

---

### T1.2 — Smoke import the package

**Steps**:
1. Run: `python -c "import hxi_optimizer.main; print('OK')"`

**Expected**: Single line `OK`. No tracebacks.

**Pass**: `OK` printed.
**Fail**: Any traceback means a dep is missing or broken — re-run T1.1.

---

### T1.3 — Run the test suite

**Steps**:
1. Run: `python -m pytest hxi_optimizer/tests/ -q`
2. Wait ~60 seconds.

**Expected**:
```
2145 passed in ~60s
```

**Pass**: All 2,145 tests pass.
**Fail**: If even one test fails — do **not** continue with the rest of this guide. Send the last 30 lines of output to the developer. The deployed code does not match the deployed tests.

---

### T1.4 — Start the simulated PLC + optimizer

**Steps**:
1. Open **Terminal A**: `python -m local_test.sim_plc`
   - You should see `Sim PLC listening on 127.0.0.1:5020`.
2. Open **Terminal B**: `python -m hxi_optimizer.main`
   - Watch for log lines. Within 5 seconds you should see:
     ```
     === HXI Smart Slide Adaptive PID Optimizer starting ===
     Transport: modbus (127.0.0.1:5020)
     Connecting to PLC at 127.0.0.1:5020
     Connection status: True
     Dashboard starting at http://0.0.0.0:8420
     Starting main loops (Phase A)
     ```

**Pass**: Both terminals show their startup banners with no red errors.
**Fail**: If "Port 8420 already in use" — another optimizer is running; stop it. If "Connection refused" on terminal B — terminal A didn't start; check for Python errors.

---

### T1.5 — Open the dashboard

**Steps**:
1. Open a web browser to `http://localhost:8420`.
2. The page should load within 3 seconds.

**Expected**: Dashboard appears with header showing **PHASE: A** and a connection dot.

**Pass**: Page loads and the dot is green within 30 seconds.
**Fail**: If page is blank → check browser console (F12) for errors. If 401 → see Part 2 (auth) for token setup.

---

### T1.6 — Walk every tab

**Steps**:
1. Click each tab in the top bar: Live, Intel, Safety, Controls, Fleet, Registers, Simulation, Logs, Diagnostics, Training.
2. For each, wait 5 seconds and verify content loads.

**Expected**: Every tab populates. No "Network error" toasts.

| Tab | What you should see |
|---|---|
| Live | Telemetry numbers updating, swash bar moving |
| Intel | "Awaiting metrics" or a digest message |
| Safety | "BASELINE" state, empty audit table |
| Controls | Phase dropdown, Disable/Enable buttons |
| Fleet | Empty (no real rigs) |
| Registers | "Register_List.xlsx not found" or a register list |
| Simulation | Scenario dropdown with 11 options |
| Logs | Tail of `optimizer.log` |
| Diagnostics | CPU / RAM / disk numbers |
| Training | Dataset capture summary |

**Pass**: 9 of 10 tabs work (Fleet may be empty in local mode — that's normal).
**Fail**: Reload page once. If still failing on a specific tab, note which tab and the browser console error.

---

### T1.7 — Run a simulation scenario

**Steps**:
1. Go to **Simulation** tab.
2. Pick **stickslip** from the dropdown.
3. Click **Run**.
4. Wait ~3 seconds.

**Expected**: A chart appears showing RPM bouncing periodically. Below the chart, a "Per-window metrics" table populates with `failure_mode` rows showing OSCILLATION.

**Pass**: Chart renders, classifier picks up OSCILLATION at least once in the metrics table.
**Fail**: If 504 timeout → endpoint timeout misconfigured (default 30s). If chart blank → check Logs tab for the simulator error.

---

## Part 2 — Dashboard production behaviors

### T2.1 — Health probe (no auth)

**Steps**:
1. In a terminal: `curl http://localhost:8420/healthz`

**Expected**:
```json
{"status": "ok", "uptime_s": 123.4, "modbus_healthy": true, "auth_enabled": false}
```

**Pass**: 200 response, JSON body looks like above.
**Fail**: If 401 → middleware misconfigured. If timeout → uvicorn not running.

---

### T2.2 — Enable token auth

**Steps**:
1. Stop the optimizer (Ctrl+C in Terminal B).
2. Edit `hxi_optimizer/hxi_config.json` (or create it from `hxi_config.template.json`):
   ```json
   { "dashboard_token": "test-token-12345" }
   ```
3. Restart: `python -m hxi_optimizer.main`
4. In a browser, refresh `http://localhost:8420`.
5. A prompt should appear asking for a token.
6. Enter `test-token-12345`. The dashboard should load normally.

**Expected**:
- Without the token, every API call returns 401.
- With the token, every API call works.
- Token persists across browser refresh (stored in localStorage).

**Pass**: Login prompt appears, accepting the token unlocks the dashboard, refresh doesn't re-prompt.
**Fail**: If no prompt appears, check `optimizer.log` for `auth ENABLED` on the "Dashboard starting" line.

---

### T2.3 — Token auth from the API directly

**Steps**:
1. With auth still enabled, run:
   ```bash
   curl http://localhost:8420/api/status
   curl -H "Authorization: Bearer test-token-12345" http://localhost:8420/api/status
   curl http://localhost:8420/api/status?token=test-token-12345
   ```

**Expected**:
- First call: `{"error": "unauthorized", ...}` with status 401.
- Second call: full status JSON, status 200.
- Third call (query param): full status JSON, status 200.

**Pass**: All three behave as described.
**Fail**: If first call returns 200 → token isn't being checked. Restart with `dashboard_token` set in config.

---

### T2.4 — Bad input rejected with 422

**Steps**:
1. With auth enabled, run:
   ```bash
   curl -X POST -H "Content-Type: application/json" \
        -H "Authorization: Bearer test-token-12345" \
        -d '{"depth_ft": -100}' \
        http://localhost:8420/api/control/depth
   ```

**Expected**: Status 422, body contains `"error": "validation failed"` and field-level detail.

**Pass**: 422 returned. The negative depth was rejected before reaching any business logic.
**Fail**: If 200 → Pydantic model not enforcing `gt=0`. If 500 → exception leaked instead of being caught.

---

### T2.5 — Audit trail captures operator actions

**Steps**:
1. Browser: Controls tab → Phase dropdown → set to **B** → confirm dialog.
2. Browser: Controls tab → enter `5000` in drill depth → click Update.
3. In a terminal:
   ```bash
   tail -5 hxi_optimizer/logs/audit.log
   ```

**Expected**: At least two recent rows showing:
```csv
<ts>,DASHBOARD_PHASE_CHANGE,,,,,, "A -> B",,
<ts>,DASHBOARD_DEPTH_UPDATE,,,,,, "3000ft -> 5000ft",,
```

**Pass**: Both rows present with old → new values.
**Fail**: If rows missing → audit logger not wired into dashboard. If text says "DASHBOARD" without details → check `_audit_operator()` in `dashboard/server.py`.

---

### T2.6 — Endpoint timeout (504 path)

**Steps**:
1. Edit `hxi_config.json`: set `"dashboard_endpoint_timeout_s": 1.0` (very short, just for this test).
2. Restart the service.
3. Browser: Simulation tab → pick **chaotic_formation_change** → set duration_s to 600 → Run.

**Expected**: After ~1 second, an error toast: `Error: endpoint exceeded 1s timeout`.

**Pass**: 504 returned within 2 seconds.
**Fail**: If the request hangs forever → executor not wrapped in `asyncio.wait_for`. If 200 returns slowly → timeout not being applied.

**Cleanup**: Reset `dashboard_endpoint_timeout_s` to `30.0` and restart.

---

### T2.7 — Graceful shutdown

**Steps**:
1. With the dashboard open in a browser, watch the connection dot (top-left).
2. In Terminal B, press Ctrl+C.
3. Watch the optimizer log lines.

**Expected log sequence**:
```
Received signal SIGINT — initiating shutdown
Shutdown: closing dashboard (WebSocket + uvicorn)
Shutdown: persisting final state
Shutdown: stopping CSV logger
Shutdown: closing modbus client
=== HXI Optimizer shutdown complete ===
```

The browser dot turns red and shows "Reconnecting...". After Terminal B fully exits, the dot stays red.

**Pass**: All shutdown lines printed in order, no traceback.
**Fail**: If "Shutdown" lines missing → signal handler not wired. If the process hangs → uvicorn not respecting `should_exit`. Force-kill with Ctrl+C twice.

---

### T2.8 — State persists across restart

**Steps**:
1. Restart the optimizer (still in Phase B from T2.5).
2. Watch the startup log and the dashboard.

**Expected**:
- Log line: `Restored LKG: [<lower>, <upper>]` if any writes happened.
- Dashboard header still shows **PHASE: B** (the change from T2.5 persisted).
- Audit log file still has all old rows + new startup events.

**Pass**: Phase, LKG bounds, and audit history all preserved.
**Fail**: If phase reverted to A → `hxi_config.json` not being written by the dashboard. (Manual edit + restart required for phase change in current build — that's intentional, not a bug.)

---

## Part 3 — ML pipeline

### T3.1 — Classifier loaded and inferring

**Steps**:
1. With the simulator running and optimizer up, browser to **Live** tab.
2. Wait ~30 seconds.
3. In the metrics panel, look at `failure_mode`.

**Expected**: `failure_mode` shows a class name (NORMAL, BIAS, OSCILLATION, etc.), not "—" or "UNKNOWN". `failure_confidence` is between 0 and 1.

**Pass**: A class is reported with confidence > 0.
**Fail**: If "—" → ONNX classifier failed to load. Check `optimizer.log` for `classifier load:` errors and Logs tab → search for "classifier".

---

### T3.2 — Anomaly score active

**Steps**:
1. Same Live tab.
2. Check `anomaly_score` and `anomaly_threshold`.

**Expected**: Both are small numbers (~0.0001 range). `anomaly_detected` is `false` for normal sim data.

**Pass**: Numbers shown, both > 0.
**Fail**: If anomaly_score = 0.0 forever → AE not loaded. If anomaly_detected = true with sim NORMAL → threshold misconfigured for the deployed AE.

---

### T3.3 — Per-rig model registry status

**Steps**:
1. Browser: open `/api/models` directly (or use Fleet tab, scroll to Models section).

**Expected JSON**:
```json
{
  "default_available": true,
  "default_has_classifier": true,
  "default_has_autoencoder": true,
  "n_per_rig": 0,
  "per_rig_slugs": [],
  "live": {
    "classifier_source": "<path>/models/classifier.onnx",
    "classifier_active": true,
    "autoencoder_active": true
  }
}
```

**Pass**: Default models reported, no per-rig models yet (none deployed).
**Fail**: If `default_available: false` → ONNX missing from `hxi_optimizer/models/`. Re-deploy from `training/models/classifier_torch_v2/`.

---

### T3.4 — A/B compare with no data

**Steps**:
1. Browser: Fleet tab → A/B Compare card → enter any rig name → Run Compare.

**Expected**: Result card shows:
```
Cannot compare: No labeled real episodes for this rig yet.
Capture some with the dashboard 'Annotate' buttons, then retry.
```

**Pass**: Friendly error, not a crash.
**Fail**: If 500 → exception not caught. If hangs → no timeout on the endpoint.

---

### T3.5 — Capture an annotated episode

**Steps**:
1. With sim running, let it accumulate ~30 seconds of data.
2. Controls tab → click **Annotate → OSCILLATION** (or use any label).
3. Check terminal for log: `DASHBOARD: operator annotation 'OSCILLATION' (40 samples)`.
4. Check disk: `ls hxi_optimizer/logs/dataset/<machine_slug>/OSCILLATION/`.

**Expected**: A new `episode_<timestamp>.npz` file appears.

**Pass**: File created, log line present, audit row written (`DASHBOARD_ANNOTATE`).
**Fail**: If "Not enough telemetry in buffer yet" → wait longer (need 10+ samples in the ring buffer). If no file → check disk permissions on the dataset dir.

---

### T3.6 — Fine-tune refuses on insufficient data

**Steps**:
1. With only the few episodes you just captured (definitely < 100 windows total):
   ```bash
   python -m training.fine_tune --rig "all" --no-deploy
   ```

**Expected**: Output ends with:
```
=== CLASSIFIER FINE-TUNE RESULT ===
{
  "ok": false,
  "reason": "Not enough real data: <N> windows from <X> episodes. Need at least 100 windows..."
}
```

**Pass**: Refuses gracefully, no traceback. Tells you exactly what's missing.
**Fail**: If it tries to train anyway → safety threshold disabled.

---

### T3.7 — (Optional) Full fine-tune cycle

Only attempt if you have ≥100 captured windows across multiple labels.

**Steps**:
1. `python -m training.fine_tune --rig "all" --no-deploy`
2. Watch for these log lines:
   - `Real dataset: N windows from M episodes...`
   - `Sim baseline accuracy on held-out real data: 0.XX`
   - `Epoch 1: ...`
   - `VALIDATION GATE FAILED` OR `Versioned save: ...`
3. If gate passed and you used `--deploy` (without `--no-deploy`), check:
   - `hxi_optimizer/models/per_rig/fleet/classifier.onnx` exists
   - `models/classifier.onnx` (the default) is **unchanged** (compare timestamps)

**Pass**: Either gate-fail (with friendly message) or gate-pass with new files in `per_rig/<slug>/`. **Default classifier file timestamp must not change.**
**Fail**: If default classifier got overwritten → fine-tune is writing to the wrong path. Stop and contact developer.

---

## Part 4 — Safety gate

### T4.1 — Phase A: no writes happen

**Steps**:
1. Confirm `hxi_config.json` shows `"phase": "A"`.
2. Restart optimizer.
3. Let it run for 5 minutes.
4. Check audit log: `cat hxi_optimizer/logs/audit.log | grep ",WRITE,"`

**Expected**: Zero `WRITE` rows. Only `DASHBOARD_*` rows from earlier tests, possibly a header.

**Pass**: No `WRITE` rows in Phase A.
**Fail**: If any `WRITE` row appears → phase check missing in `analysis_loop`. Critical issue — stop.

---

### T4.2 — Operator disable blocks writes (Phase B)

**Steps**:
1. Promote to Phase B in config + restart.
2. Wait 1 minute (still no writes — Phase B is advisory only).
3. Verify still no `WRITE` rows.

**Expected**: Same as T4.1 — Phase B is also no-write.

**Pass**: No `WRITE` rows in Phase B.
**Fail**: Same as T4.1. Phase A and B must both be write-free.

---

### T4.3 — Phase change requires confirmation

**Steps**:
1. Browser: Controls tab → set Phase to **C** → expect a confirm dialog.
2. Click Cancel. Phase should not change.
3. Repeat, click OK. Phase changes to C.
4. Tail `audit.log`. Should see `DASHBOARD_PHASE_CHANGE A -> C` (or whatever the previous phase was).

**Expected**: Confirm dialog blocks accidental clicks. After confirmation, the change is audited.

**Pass**: Cannot change to C/D without confirming. Audit row written.
**Fail**: If no confirm dialog → frontend missing the `confirm()` call. If audit row missing → `_audit_operator()` not invoked.

---

### T4.4 — Comms loss triggers rollback to LKG

**Setup**: optimizer in Phase C with at least one `WRITE` row in audit (so LKG is populated).

**Steps**:
1. Stop the simulated PLC (Terminal A: Ctrl+C).
2. Watch optimizer logs for the next 60 seconds.

**Expected log sequence** (within ~30 s of comms loss):
```
WARN: Connection unhealthy: 6 consecutive failures
SafetyGate: COMMS_LOSS_30S — rolling back to LKG
```
And in `audit.log`:
```csv
<ts>,ROLLBACK,,,<lkg_lower>,<lkg_upper>,ROLLBACK,COMMS_LOSS_30S,,
```

**Pass**: Rollback triggered within 30–35s of comms loss. LKG bounds present in the audit row.
**Fail**: If no rollback by 60s → connection_monitor's threshold check is broken.

**Cleanup**: Restart the simulated PLC. Optimizer should reconnect within 10s and log "Connection status: True".

---

### T4.5 — State persists across crash

**Steps**:
1. Note the current phase, gate state, and LKG (visible on Live tab).
2. Hard-kill the optimizer: in Terminal B, press Ctrl+C. (Or kill -9 the process for a true crash test.)
3. Restart. Watch the startup log for:
   - `Restored LKG: [<lower>, <upper>]`
4. Verify dashboard shows the same phase, state, and LKG values.

**Expected**: All three values restored exactly.

**Pass**: No "starting fresh" — service picks up where it left off.
**Fail**: If LKG shows defaults → `state.json` not being written or loaded. Check file exists at `hxi_optimizer/state/state.json`.

---

## Part 5 — Pre-PLC commissioning (against real PLC)

**This part requires a real CPE305 PLC reachable from the rig PC.**

Stop the simulated PLC if running. Update `hxi_config.json`:

```json
{
  "plc_host": "<actual-PLC-IP>",
  "plc_port": 502,
  "phase": "A"
}
```

### T5.1 — Run commissioning tests

**Steps**:
1. Run: `python -m hxi_optimizer.deploy.commissioning_tests`
2. Watch the output. Each test reports PASS or FAIL.

**Expected output**:
```
Test 1: Modbus handshake .................... PASS
Test 2: FLOAT32 byte order verification ..... PASS (ABCD)
Test 3: Heartbeat write round-trip .......... PASS
Test 4: Safe bounds discovery ............... PASS (lower 280-450, upper 580-780)
Test 5: ESD bit reachable ................... PASS
Test 6: Bump flags reachable ................ PASS
Test 7: Rate-limit tuning ................... PASS
Test 8: FC16 + readback consistency ......... PASS
All 8 commissioning tests PASS.
hxi_config.json updated:
  VERIFIED_WORD_ORDER = "ABCD"
  safety.abs_min_lower = 280
  safety.abs_max_lower = 450
  safety.abs_min_upper = 580
  safety.abs_max_upper = 780
```

**Pass**: All 8 tests PASS, config file updated.
**Fail**: Each test prints what failed. **Do NOT promote the rig past Phase A** until every test passes. If Test 2 fails → byte order mismatch (try the other 3 orderings: BADC / CDAB / DCBA, the script will try them automatically). If Test 5 fails → ESD bit isn't where the spec says — this is critical, contact developer.

---

### T5.2 — Verify byte order with manual register read

**Steps**:
1. Open the dashboard.
2. Registers tab → click **Live Scan**.
3. Look at `rpm_encoder` row. The PLC should be at idle or a known RPM.

**Expected**: The decoded value matches the actual physical RPM (within a few units of noise). E.g., if the rig is at 60 RPM, the dashboard should show ~60.

**Pass**: Reading matches reality.
**Fail**: If reading is `1e+38` or wildly negative → byte order wrong. Re-run T5.1.

---

### T5.3 — Verify writes are blocked in Phase A

**Steps**:
1. Start the optimizer against the real PLC.
2. Let it run 5 minutes.
3. Check the PLC's swash bound registers (%R06603, %R06604) directly via the Registers tab → Live Scan, before and after.

**Expected**: %R06603 and %R06604 do not change during the 5-minute window (Phase A = no writes).

**Pass**: Bounds unchanged.
**Fail**: If they change → either someone else is writing, or phase check is broken. Stop the optimizer immediately and check audit log.

---

## Part 6 — Live Phase A on real PLC

### T6.1 — Service starts cleanly

**Steps**:
1. Install as a Windows Service: `hxi_optimizer\deploy\install_service.bat` (as Administrator).
2. `sc start HXIOptimizer`
3. `sc query HXIOptimizer`

**Expected**: Service state shows `RUNNING`. Check `hxi_optimizer/logs/service_stdout.log` — same boot sequence as Terminal B before.

**Pass**: Service running, dashboard reachable at `http://localhost:8420`.
**Fail**: If service starts then stops → check `service_stderr.log` for the actual error. Common cause: missing config file or unverified word order.

---

### T6.2 — Machine identification

**Steps**:
1. Watch the optimizer log for the first minute after start.

**Expected**: Within 30s, a log line:
```
Machine identified: <eWon Name> (TESCO 250T HXI 800HP) [first connection]
```
Or: `Reconnected to: <eWon Name>` if seen before.

If the optimizer can't identify the rig:
```
WARN: No fleet match for <plc_host> (hint=None). Using default HXI register map.
```

**Pass**: Either correct identification, or the warning (in which case set `ewon_name` in config and restart).
**Fail**: If neither line appears → eCatcher integration broken. See [FLEET.md](FLEET.md).

---

### T6.3 — Telemetry collection — 1-hour soak

**Steps**:
1. Leave the service running for 1 hour during normal drilling.
2. After the hour, check:
   ```bash
   ls -la hxi_optimizer/logs/drill_*.csv
   wc -l hxi_optimizer/logs/drill_*.csv
   ```

**Expected**: A single CSV file ~200 KB / minute (so ~12 MB after an hour). Line count ~7,200 (2 Hz × 3600 s).

**Pass**: CSV growing at expected rate, no `WRITE` rows in audit log.
**Fail**: If CSV stops growing → check optimizer log for read errors. If `WRITE` rows appear → critical, see T4.1.

---

### T6.4 — Annotate during the soak

**Steps**:
1. During the 1-hour soak, when something notable happens (operator judgment), open the dashboard and click an Annotate button.
2. Repeat 3–5 times across different events.
3. After the hour:
   ```bash
   ls hxi_optimizer/logs/dataset/<machine_slug>/*/
   ```

**Expected**: Each click produces an `episode_*.npz` file under the matching label folder.

**Pass**: ≥3 episode files saved across ≥1 label.
**Fail**: If files missing → check audit log for `DASHBOARD_ANNOTATE` rows; if those exist but files don't, check disk permissions.

---

### T6.5 — eCatcher tunnel switch (optional, if a 2nd rig is reachable)

**Steps**:
1. With the optimizer running connected to Rig A.
2. Use eCatcher to switch the tunnel to Rig B.
3. Within 30–60s, watch the optimizer log.

**Expected log**:
```
eCatcher tunnel: <RigB-Name> (source=talk2m)
AUTO-DETECTED MACHINE CHANGE via eCatcher: -> <RigB-Name>
Model hot-swap (ecatcher) -> <RigB-Name> [default/<slug>]
```

If a per-rig model exists for Rig B, you'll also see:
```
Per-rig model loaded for <RigB-Name> (slug=<slug>): {...}
```

**Pass**: Auto-detect fires within 60s. Register map switched. (Verify Registers tab shows updated values consistent with Rig B.)
**Fail**: If no detection → check eCatcher log path or Talk2m credentials in config. Manual override: Controls tab → Machine Switch.

---

## Part 7 — Sign-off

### Results table

Print this table and check it off as you go.

| Test ID | Description | Pass / Fail | Notes |
|---|---|---|---|
| T1.1 | Install dependencies | ☐ | |
| T1.2 | Smoke import | ☐ | |
| T1.3 | Test suite (2,145 pass) | ☐ | |
| T1.4 | Sim PLC + optimizer start | ☐ | |
| T1.5 | Dashboard loads | ☐ | |
| T1.6 | Every tab populates | ☐ | |
| T1.7 | Simulation scenario runs | ☐ | |
| T2.1 | /healthz returns 200 | ☐ | |
| T2.2 | Token auth — login prompt | ☐ | |
| T2.3 | Token auth — API enforcement | ☐ | |
| T2.4 | Bad input → 422 | ☐ | |
| T2.5 | Audit trail captures actions | ☐ | |
| T2.6 | Endpoint timeout → 504 | ☐ | |
| T2.7 | Graceful shutdown | ☐ | |
| T2.8 | State persists across restart | ☐ | |
| T3.1 | Classifier inferring | ☐ | |
| T3.2 | Anomaly score active | ☐ | |
| T3.3 | Model registry healthy | ☐ | |
| T3.4 | A/B compare graceful failure | ☐ | |
| T3.5 | Annotated episode captured | ☐ | |
| T3.6 | Fine-tune refuses insufficient data | ☐ | |
| T3.7 | Full fine-tune cycle (optional) | ☐ N/A | |
| T4.1 | Phase A: no writes | ☐ | |
| T4.2 | Phase B: no writes | ☐ | |
| T4.3 | Phase change requires confirm | ☐ | |
| T4.4 | Comms loss → rollback | ☐ | |
| T4.5 | State persists across crash | ☐ | |
| T5.1 | Commissioning tests pass | ☐ | |
| T5.2 | Byte order verified | ☐ | |
| T5.3 | Phase A blocks PLC writes | ☐ | |
| T6.1 | Windows Service runs | ☐ | |
| T6.2 | Machine identified | ☐ | |
| T6.3 | 1-hour CSV collection | ☐ | |
| T6.4 | Annotate produces files | ☐ | |
| T6.5 | eCatcher hot-swap (optional) | ☐ N/A | |

### Issue log

If any test failed, record here before contacting the developer:

| Test ID | What you saw | Last log line before failure | What you tried |
|---|---|---|---|
| | | | |
| | | | |
| | | | |

### Sign-off

By signing below, the tester confirms that all required tests above were executed and the results recorded.

```
Tester name:    ______________________
Date:           ______________________
Rig (if T5+):   ______________________
PLC firmware:   ______________________
Result:         ☐ APPROVED for next phase
                ☐ APPROVED for production
                ☐ FAILED — see issues log

Signature:      ______________________
```

---

## Promotion checklist

Before promoting a rig to a higher phase, **all** of these must be true:

**Phase A → B** (advisory enabled):
- [ ] Part 5 commissioning tests all PASS for this rig
- [ ] Part 6 ran for ≥24 hours with zero `WRITE` rows
- [ ] At least 3 annotated episodes captured

**Phase B → C** (writes enabled, gated):
- [ ] Drilling engineer reviewed advisory bounds vs. current production bounds
- [ ] At least one operator on shift trained on the dashboard
- [ ] Audit log archived (the Phase A/B run is the baseline)

**Phase C → D** (full authority):
- [ ] ≥2 drilling stands completed in Phase C
- [ ] Zero unscheduled rollbacks during those stands
- [ ] If a per-rig fine-tune is deployed, A/B compare shows PROMOTE recommendation

---

## Escalation

If a test fails and the fix isn't in [TROUBLESHOOTING.md](TROUBLESHOOTING.md):

1. Stop testing.
2. Don't restart or modify config to "make it work" — that hides the bug.
3. Save the last 100 lines of `optimizer.log` and the relevant `audit.log` rows.
4. Contact the developer with: test ID, expected vs actual, log excerpt, your config (sanitize the auth token first).
