# Troubleshooting

Common issues, what they mean, and the exact fix.

---

## Service won't start

### `ValueError: VERIFIED_WORD_ORDER is None — run commissioning`

Byte-order verification hasn't been run against this PLC. Fix:

```bash
python -m hxi_optimizer.deploy.commissioning_tests
```

This writes `VERIFIED_WORD_ORDER = "ABCD"` (or whichever) into `hxi_optimizer/comms/register_map.py`. Do this once per PLC firmware version.

### `SafetyGate refuses to start — abs_min_lower is None`

Safety limits aren't populated. Same fix — the commissioning tests write them into `hxi_config.json`.

### `Port 8420 already in use`

Another optimizer instance is running, or the port is taken.

```bash
netstat -ano | findstr :8420
taskkill /PID <pid> /F
```

Or change `dashboard_port` in config.

### `ModuleNotFoundError` on startup

Deps out of date. Reinstall:

```bash
python -m pip install -r requirements.txt
```

---

## ML classifier not firing

### `loaded_models_info()` shows `classifier_source: null`

ONNX file didn't load. Check the log for `failed to load classifier`. Common causes:

- File missing — `ls hxi_optimizer/models/classifier.onnx` should exist (>90 KB).
- File has an external-data sidecar (`classifier.onnx.data`). V2 training uses `dynamo=False` which avoids the sidecar, but if you rebuilt the ONNX with `dynamo=True` you'll hit this. Re-export with `dynamo=False, opset_version=17`.
- Meta.json is missing or corrupt — check JSON validity.

### `classifier_fail_count` climbing

The session loaded but every inference is throwing. Check `optimizer.log` for the actual stack trace from `session.run()`. Common causes:

- **Feature count mismatch**: the ONNX expects 7 channels but 12 came in (or vice versa). Feature order in `performance_metrics.py` must match `training/generate_dataset.py:FEATURE_COLS`.
- **Input shape wrong**: expected `(batch, 40, 7)`, got something else. Probably ring_buffer isn't fully populated yet — should settle after 20 s of data.
- **Mean/std mismatch**: meta.json has 12 values, model expects 7 (or vice versa).

### Classifier always predicts the same class

Normalization is off. Check `meta.json` X_mean and X_std look right for the current sim data. If you retrained without `PYTHONIOENCODING=utf-8`, the ONNX export may have silently failed — check file size matches expectations (~100 KB for the 7-channel 1D-CNN).

---

## Fine-tune issues

### `Not enough real data: X windows`

Need ≥100 windows. Get more by annotating operator events on the dashboard. Run `python -m training.fine_tune --validate-only --rig "<name>"` to see the current count + label distribution.

### `VALIDATION GATE FAILED: fine-tune ... did not beat sim baseline`

By design — the fine-tune isn't better than the sim model on held-out real data. Do NOT force-deploy. Options:

1. Capture more data, especially in the weak classes.
2. Check `result["warnings"]` in the fine-tune output — if class imbalance was auto-boosted, the fine-tune may be saturated on majority classes.
3. Check `result["feature_drift"]["severe_drift_channels"]` — if flagged, real data doesn't match sim, so the sim baseline isn't representative of what real fine-tune can do. Re-run the sim retrain with better physics (see [ML_PIPELINE.md](ML_PIPELINE.md)).

### `FEATURE DRIFT 'delivered_torque': 72% samples >3σ from sim`

Real PLC is reporting torque in a different scale than sim. V1 sim used `|error|*50` which was wrong; V2 sim uses pump-flow-derived torque which matches real PLC. If you're still seeing drift on V2, check:

- Is the PLC reporting torque in N·m vs ft·lbs? V2 sim is ft·lbs (800–6000 range).
- Is the torque register you're reading actually `delivered_torque` or something else (e.g., torque setpoint)?
- Is the PLC's torque sensor correctly scaled?

---

## Dashboard issues

### 401 on every request

Token expired / rotated. Clear browser localStorage:

- F12 → Application → Local Storage → `http://localhost:8420` → delete `hxi_dashboard_token`
- Reload — dashboard will prompt again.

Or set a new token in `hxi_config.json` and restart the service.

### WebSocket keeps reconnecting

1. `curl http://localhost:8420/healthz` — is the server up at all?
2. Check NSSM service status: `sc query HXIOptimizer`.
3. Firewall blocking port 8420? `netsh advfirewall firewall show rule name=all | findstr 8420`.
4. Browser extension (ad-blocker, VPN) interfering with WS. Test in incognito.

### /api/simulate returns 504

The requested duration was too long for the configured timeout (default 30 s). Reduce `duration_s` in the request or increase `dashboard_endpoint_timeout_s` in config.

### Dashboard is blank / "Network error" on every call

Server didn't start cleanly. Check `optimizer.log` for exceptions. Also check the `/api/auth/status` endpoint — if that works but `/api/status` doesn't, you have an auth token mismatch.

---

## Safety gate issues

### State stuck in `ROLLING_BACK`

Look at the most recent `ROLLBACK` row in `audit.log` to see the reason:

- `COMMS_LOSS_30S`: Modbus dropped for ≥30 s. Gate wrote LKG and parked. To clear: fix the comms, then dashboard → Controls → Enable.
- `3x_CONSECUTIVE_REJECTION`: advisor's last 3 proposals were rejected by the gate. Usually means bounds hit abs limits or rate limit. Check `audit.log` for the rejection reasons.
- `IAE_REGRESSION`: trial bounds made things worse. Gate reverted to LKG. Normal during Phase C tuning.

### State stuck in `ESD`

%R06664 ESD bit is set. Gate refuses to write until the operator clears it at the PLC. Check the live register scanner to confirm the bit went from 1 → 0. Then dashboard → Controls → Enable.

### Gate says "heartbeat stale" and refuses to write

`heartbeat_loop` is blocked. Check:

- Is `read_loop` running? Look for fresh `drill_*.csv` rows.
- Is something blocking the asyncio loop (a runaway endpoint)? Restart the service.

---

## PLC communication issues

### `Modbus: consecutive_failures=10`

Tunnel or PLC is unreachable. Check:

1. Is the eCatcher tunnel up? Dashboard → Fleet → eCatcher status.
2. `ping <plc_host>` from the rig PC.
3. PLC Modbus server running? Some PLCs disable Modbus after a firmware update.
4. Unit ID correct? (`unit_id` in config, default 1)
5. `plc_port` correct? (default 502)

### Reads succeed but values are garbage (torque = 10^38, RPM negative)

Byte-order wrong. Re-run commissioning tests — PLC firmware may have shipped a new byte order.

### `COMMS_LOSS_30S` fires every few hours

eCatcher tunnel is dropping. Known issue with certain eCatcher versions + Windows sleep. Fix:

- Disable Windows sleep + power-saving on the network adapter.
- Upgrade eCatcher to the latest stable release.
- Configure Talk2m API access (`talk2m_*` in config) so the optimizer can detect + re-establish automatically.

---

## Test suite issues

### Tests fail after pulling new code

Always reinstall deps first:

```bash
python -m pip install -r requirements.txt
```

If tests still fail:

```bash
python -m pytest hxi_optimizer/tests/ -x --no-header -q 2>&1 | tail -20
```

The `-x` stops at the first failure. Read the error — usually a schema mismatch or a fixture that needs refreshing.

### `test_ml_classifier` tests fail after retraining

Expected immediately after retraining. The deployed classifier needs to match the sim physics it was trained on. If you retrained with new sim physics but haven't redeployed the model:

```bash
cp training/models/classifier_torch_v2/classifier.onnx hxi_optimizer/models/classifier.onnx
cp training/models/classifier_torch_v2/meta.json hxi_optimizer/models/classifier_meta.json
```

Then re-run tests.

---

## Disk / logs

### `audit.log` is huge

Expected behavior — audit is append-only with fsync. At ~100 ops/day this is ~50 KB/week. If it's hundreds of MB, something is logging excessively — grep for the most common `event_type` to find the loop:

```bash
cut -d, -f2 hxi_optimizer/logs/audit.log | sort | uniq -c | sort -rn
```

### `drill_<ts>.csv` files filling disk

One file per service run at ~30 MB/day. Delete files older than 30 days:

```bash
# PowerShell
Get-ChildItem hxi_optimizer\logs\drill_*.csv | Where-Object LastWriteTime -lt (Get-Date).AddDays(-30) | Remove-Item
```

Schedule this as a weekly task in Task Scheduler.

---

## Still stuck?

1. Full test suite: `python -m pytest hxi_optimizer/tests/ -q` — narrows to code-level vs deployment-level.
2. Check `optimizer.log` for the last INFO/WARNING before things went bad.
3. Check `audit.log` for the last accepted write (baseline you know worked).
4. Reproduce in `local_test` (simulated PLC on 127.0.0.1:5020) — that isolates the optimizer from PLC / eCatcher / VPN issues.
