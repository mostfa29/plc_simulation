# Dashboard

FastAPI + WebSocket app at `http://localhost:8420`. Single-user operator UI with Bearer token auth, Pydantic-validated POST bodies, per-endpoint timeouts, and an audit trail of every state-changing action.

Source: [`hxi_optimizer/dashboard/server.py`](../hxi_optimizer/dashboard/server.py) + [`static/index.html`](../hxi_optimizer/dashboard/static/index.html).

---

## Authentication

**Token-based**, single value. Set via:

```json
// hxi_optimizer/hxi_config.json
{ "dashboard_token": "<random-32-char-string>" }
```

Or env var `HXI_DASHBOARD_TOKEN` (env wins over config). If neither is set, auth is disabled.

**Sending the token**:

- REST: `Authorization: Bearer <token>`, or `?token=<token>` query param.
- WebSocket: `?token=<token>` only (browsers can't set headers on WS handshakes).

**Browser UX**: `index.html` probes `/api/auth/status` on load. If auth is enabled and no token is in `localStorage['hxi_dashboard_token']`, it prompts once. 401 responses trigger a re-prompt. Stored token survives browser restart.

**Public endpoints** (no auth required):

- `GET /` — HTML page itself (so the login prompt can load)
- `GET /healthz` — liveness probe for NSSM / supervisors
- `GET /api/auth/status` — tells the client whether auth is needed
- `GET /static/*`, `/favicon.ico`

---

## Global behaviors

### Body-size cap

Requests with `Content-Length > dashboard_max_body_bytes` (default 1 MB) are rejected with **413** before the endpoint reads the body.

### Timeout

Heavy endpoints run in the ThreadPoolExecutor with `asyncio.wait_for(..., dashboard_endpoint_timeout_s)` (default 30 s). Exceed it → **504**.

### Exception handling

Unhandled exceptions → **500** with `{"error": "internal server error", "type": <ExceptionClass>, "path": <url>}`. Full stack trace logged server-side only. Validation errors → **422** with field-level detail (from Pydantic).

### Concurrency cap

`uvicorn.limit_concurrency = dashboard_max_concurrent` (default 64). Runaway clients hit the cap before the asyncio loop slows.

---

## REST endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/healthz` | — | `{status, uptime_s, modbus_healthy, auth_enabled}` |
| GET | `/api/auth/status` | — | `{auth_enabled}` |
| GET | `/api/status` | — | Full snapshot (see WebSocket schema) |
| GET | `/api/audit` | — | Last 200 audit rows |
| GET | `/api/history` | — | Rolling 120 s telemetry buffer |
| GET | `/api/logs?file=<name>&lines=<n>` | — | Tail of a log file |
| GET | `/api/diagnostics` | — | Process info (CPU, mem, disk, transport health) |
| GET | `/api/config` | — | Sanitized config view |
| GET | `/api/transport` | — | Transport type, host, port, connected |
| GET | `/api/alarms?since=<ts>` | — | Alarms since timestamp |
| POST | `/api/alarms/dismiss` | — | Clear alarm queue |
| GET | `/api/export/history.csv` | — | History buffer as CSV |

### Operator controls (state-changing — all audited)

| Method | Path | Body (Pydantic) | Audit event |
|---|---|---|---|
| POST | `/api/control/disable` | — | `DASHBOARD_DISABLE` |
| POST | `/api/control/enable` | — | `DASHBOARD_ENABLE` |
| POST | `/api/control/phase` | `PhaseBody {phase: "A"\|"B"\|"C"\|"D"}` | `DASHBOARD_PHASE_CHANGE` |
| POST | `/api/control/depth` | `DepthBody {depth_ft: float}` | `DASHBOARD_DEPTH_UPDATE` |
| POST | `/api/machine/switch` | `MachineSwitchBody {ewon_name: str}` | `DASHBOARD_MACHINE_SWITCH` |
| POST | `/api/dataset/annotate` | `AnnotateBody {label, lookback_s, notes}` | `DASHBOARD_ANNOTATE` |

### Intelligence layer

| Method | Path | Notes |
|---|---|---|
| GET | `/api/intel/diagnose` | Current rule+ML diagnoses |
| GET | `/api/intel/trends?hours=48` | Multi-day trend analysis (CPU-bound; runs in executor w/ timeout) |
| GET | `/api/intel/digest` | Plain-language "right now" summary |
| GET | `/api/intel/triage` | Fleet-wide attention ranking |
| GET | `/api/intel/compare-models?rig=<n>&n_max=500` | A/B sim vs per-rig on real data (CPU-bound; executor + timeout) |

### Fleet / machines / models

| Method | Path | Returns |
|---|---|---|
| GET | `/api/fleet` | All eWon devices + which have fine-tuned models |
| GET | `/api/machine/current` | Currently-connected machine record |
| GET | `/api/machine/history` | Every machine this optimizer has seen |
| GET | `/api/models` | Per-rig model registry summary + live loaded pair |
| GET | `/api/ecatcher` | eCatcher tunnel state |
| POST | `/api/ecatcher/probe` | Force an immediate eCatcher poll |

### Registers

| Method | Path | Returns |
|---|---|---|
| GET | `/api/registers` | Full catalog from `Register_List.xlsx` |
| GET | `/api/registers/scan` | Live FC03 scan with decoded values (CPU-bound on PLC side; uses its own timeouts) |

### Simulation + training

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/simulate` | `SimulateBody {scenario, duration_s, setpoint, equipment_type, params}` | Runs sim + metrics in executor w/ timeout |
| GET | `/api/simulate/scenarios` | — | Available scenarios + their parameters |
| GET | `/api/training/status` | — | Dataset capture summary |
| GET | `/api/dataset/summary` | — | Episode counts by label + machine |

---

## WebSocket

`ws://localhost:8420/ws?token=<token>` — pushes telemetry at **2 Hz**.

Accept: `ws.accept()` (after token check — 4401 close code if auth fails).

### Schema (JSON per message)

```ts
{
  ts: float,                           // unix epoch
  connection: {
    healthy: bool,
    consecutive_failures: int,
    connected: bool
  },
  phase: "A" | "B" | "C" | "D",
  state_machine: "BASELINE" | "TRIAL" | "ACCEPTED" | "ROLLING_BACK" | "ESD" | "DISABLED" | ...,
  bounds: {
    current_lower: int,
    current_upper: int,
    lkg_lower: int,
    lkg_upper: int,
    lkg_iae: float | null
  },
  safety: {
    esd_active: bool,
    bump_lockout: bool,
    heartbeat_counter: int,
    consecutive_rejections: int,
    cooldown_remaining: float
  },
  metrics: {
    dniae: float,
    mean_error: float,
    rmse_rpm: float,
    failure_mode: str,
    failure_confidence: float,
    sat_total: float,
    anomaly_score: float,
    anomaly_threshold: float,
    anomaly_detected: bool,
    ...
  },
  advisor: { trim_upper, trim_lower, dwell_counter, total_adaptations },
  live: { rpm, setpoint, swash_output, active_lower, active_upper,
          delivered_torque, loop_temp, esd_bit, stale },
  config: { plc_host, drill_depth_ft, deadband_rpm, nominal_setpoint },
  new_alarms: [{ts, severity, message, source, id}, ...]
}
```

### Close codes

- **1001** (going away) — server is shutting down gracefully. Client should reconnect.
- **4401** (custom: unauthorized) — token missing or wrong. Client prompts for a new one.
- **1000** (normal) — client-initiated close.

### Reconnection

Client retries 2 s after close unless code is 4401 (retry immediately after re-prompting).

### Graceful shutdown

On service stop, `shutdown_dashboard()` iterates `shared["ws_clients"]` and sends close(1001) to each before flipping `uvicorn.server.should_exit = True`. Uvicorn then drains in-flight requests for up to `timeout_graceful_shutdown=5` seconds.

---

## Audit trail

Every state-changing dashboard action writes to `audit.log` (CSV, fsync per row). Same file as SafetyGate writes — one durable trail for "what happened".

```csv
timestamp,event_type,old_lower,old_upper,new_lower,new_upper,state,reason,heartbeat_seq,consecutive_rej
1745418000.123,DASHBOARD_PHASE_CHANGE,,,,,, "A -> C",,
1745418002.456,WRITE,450,600,440,610,ACCEPTED,advisor_converged,142,0
1745418005.789,DASHBOARD_ANNOTATE,,,,,, "label=OSCILLATION n=40",,
```

Read via `/api/audit` (last 200 rows) or tail the file directly.

---

## Config (production)

```json
{
  "dashboard_host": "0.0.0.0",
  "dashboard_port": 8420,
  "dashboard_token": "<required-if-not-env>",
  "dashboard_endpoint_timeout_s": 30.0,
  "dashboard_max_body_bytes": 1000000,
  "dashboard_max_concurrent": 64
}
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for full production config.

---

## Extending — adding a POST endpoint

1. Define a Pydantic model in the server (like `PhaseBody`). Use strict bounds.
2. Write the handler — accept the Pydantic model as parameter (not `dict`).
3. If the action changes state, call `_audit_operator(shared, "<EVENT_NAME>", "<reason>")` after the mutation succeeds.
4. If the endpoint does CPU work, wrap it in `await _run_cpu_bound(shared, fn, *args)`.
5. Add a test in `test_dashboard_prod.py` — at minimum: happy path, 422 on bad input, audit row on success, no audit row on validation failure.

Example skeleton:

```python
class FooBody(BaseModel):
    thing: float = Field(..., gt=0, le=100)

@app.post("/api/foo")
async def api_foo(body: FooBody):
    result = await _run_cpu_bound(shared, _do_foo, body.thing)
    _audit_operator(shared, "FOO", f"thing={body.thing}")
    return result
```
