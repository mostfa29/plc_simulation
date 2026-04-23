# Operation

Day-to-day for the operator (Steve). What to click, when, and what the colors mean.

---

## Dashboard map

`http://localhost:8420` — the top bar is always visible. Tabs underneath switch views.

| Tab | Use |
|---|---|
| **Live** | 2 Hz telemetry, swash bar, DNIAE trend, phase/state, connection dot |
| **Intel** | Diagnosis digest (what to do *right now*), fleet triage, trends from CSV logs |
| **Safety** | Gate state history, audit trail, last 200 events |
| **Controls** | Enable/disable adaptive, change phase, set drill depth, dataset annotate |
| **Fleet** | All 130 rigs, per-rig spec, fine-tuned-model indicator, A/B compare |
| **Registers** | Live scanner (all 205 registers, auto-read with decoded values) |
| **Simulation** | Run a scenario without a PLC — useful for training review |
| **Logs** | Tail `optimizer.log`, `audit.log`, or a drill CSV |
| **Diagnostics** | System info — CPU, memory, disk, transport health |
| **Training** | Model registry status, dataset capture summary |

### Header status

Left-to-right:

- **Green / yellow / red dot + label**: WebSocket connection. Red means the browser lost the server, not that the PLC is down.
- **PHASE**: A / B / C / D. Controls whether writes are allowed.
- **STATE**: `ADAPTING`, `BASELINE`, `TRIAL`, `ACCEPTED`, `ROLLING_BACK`, `ESD`, `DISABLED`.
- **Setpoint / RPM** live numeric.
- **Warning icon** if any alarm in the last 10 s.

---

## The 4 phases — what actually happens

| Phase | Behavior | Typical duration |
|---|---|---|
| **A** — Observer | Reads, logs to CSV, runs ML, logs advisory bounds. **Zero writes.** | 24–72 h on a new rig |
| **B** — Advisory | Same as A + dashboard shows "recommended bounds" but no write. | Until review meeting |
| **C** — Limited authority | Writes proposed bounds through gate. Rate-limited. Rollback on regression. | Until 2 drilling stands complete without rollback |
| **D** — Full authority | Same as C + gain scheduler allowed to push more aggressive bounds. | Steady state |

**Promoting a phase**: Controls tab → phase dropdown → confirm dialog (C and D require a second click). Every promotion is audited. Rolling back is the same procedure in reverse and is equally audited.

Each phase transition is written to `audit.log` as `DASHBOARD_PHASE_CHANGE` with `reason=<old> -> <new>`.

---

## Dataset capture (how to make fine-tunes better over time)

The optimizer passively captures every telemetry window. The operator adds a label when something notable happens.

### Automatic captures (no action needed)

- **Connection events**: every time RPM drops to 0 and comes back up (pipe makeup/breakout), a window is saved automatically as `CONNECTION`. These get re-labeled `NORMAL` during fine-tune via `LABEL_REMAP` — the signal is that pipe connections are operationally normal events.

### Operator-labeled captures (Controls tab → Annotate)

Click the button that matches what's happening **now** (or just ended). The dashboard pulls the last 20 s out of the ring buffer and saves it with that label.

Recommended labels:

| Button | When to use |
|---|---|
| Oscillation | Torque or RPM is bouncing — anything you'd describe as "hunting" |
| Stickslip | Classic grab/release; often heard as a low frequency vibration |
| Bias | RPM sits consistently above or below setpoint with no obvious oscillation |
| Formation change | Feel changes — harder or softer rock, different rate of penetration |
| Bad connection | A pipe connection that didn't go smoothly (operator call) |
| Good connection | Optional — a normal connection you want to keep as a positive example |

You're not grading the optimizer — you're telling the ML pipeline what this looks like on *this* rig. The more labels, the better the per-rig fine-tune.

Each annotation writes:

1. An `.npz` episode file under `hxi_optimizer/logs/dataset/<machine_slug>/<LABEL>/episode_<ts>.npz`.
2. An audit row: `DASHBOARD_ANNOTATE reason=label=<X> n=<samples>`.

### Fine-tune trigger

There's no "fine-tune button" on the dashboard — it's a deliberate command-line step so bad fine-tunes can't land by accident. See [ML_PIPELINE.md](ML_PIPELINE.md) for the workflow.

---

## What the colors mean

| Indicator | Color | Meaning |
|---|---|---|
| Connection dot | green | Modbus + eCatcher healthy |
| Connection dot | yellow | 1–5 consecutive read failures |
| Connection dot | red | ≥6 failures; gate will rollback if not resolved in 30 s |
| Phase pill | grey | A/B (observer, no writes) |
| Phase pill | blue | C (limited writes) |
| Phase pill | purple | D (full authority) |
| State pill | green | ADAPTING / ACCEPTED / BASELINE |
| State pill | amber | TRIAL (proposal in progress) |
| State pill | red | ESD / DISABLED / ROLLING_BACK |
| FT Model column | green check | Fine-tuned model deployed for this rig |
| FT Model column | "sim" | Using the default sim-trained model |

---

## Typical workflows

### Bringing up a new rig

1. Operator installs service, gives PLC IP (`plc_host` in config).
2. Service boots in Phase A. Dashboard shows `Machine identified: <rig-name>` in logs.
3. Leave in Phase A for 24–72 h of drilling. Observe audit.log for any unexpected rejections from the advisor.
4. When ready: promote to Phase B (advisory). Review the recommended bounds against what's currently being used. If they diverge, someone should understand why before moving to Phase C.
5. Promote to Phase C. Watch `state=TRIAL → ACCEPTED` transitions in the audit trail. A few rollbacks are fine; repeated rollbacks on the same bounds mean the advisor is overconfident and the machine needs a per-rig fine-tune first.
6. After 2 drilling stands without rollback: promote to Phase D.

### Capturing operator feedback for a fine-tune

1. When something notable happens (stick-slip, formation transition, unusual torque spike), click the matching Annotate button within ~20 s of the event.
2. Repeat through a drilling shift. Target: ≥10 episodes per failure class.
3. At end of shift, check Controls tab → dataset summary. Should show your machine with N episodes across the labels you captured.

### Running a fine-tune (operator-initiated)

See [ML_PIPELINE.md](ML_PIPELINE.md) for the full workflow. Summary:

```bash
python -m training.fine_tune --rig "Precision Rig 707 3pd HT"
```

The pipeline:

1. Loads your captured episodes from the dataset dir.
2. Checks for class imbalance (auto-boosts sim ratio if any class has <10 windows).
3. Checks for feature drift vs. sim baseline — reports any channel where real data is outside 3σ of sim.
4. Fine-tunes at LR=1e-4 with early-stopping.
5. **Refuses to deploy** if the fine-tune doesn't beat the sim baseline on held-out real data.
6. If it passes, writes to `hxi_optimizer/models/per_rig/<slug>/` — default classifier is never overwritten.
7. On next service restart (or when `connection_monitor` detects the rig via eCatcher), `PerformanceMonitor.switch_models()` hot-swaps to the per-rig pair.

### Comparing a new fine-tune to the default before promoting

Fleet tab → scroll to A/B Compare section → pick rig → Run Compare.

- Green **PROMOTE**: fine-tune is >+2pp better than sim on real data. Keep deployed.
- Amber **NEUTRAL**: within noise. Roll back or keep; no material difference.
- Red **ROLLBACK**: fine-tune is worse. Delete `models/per_rig/<slug>/classifier.onnx` and restart — optimizer falls back to sim default.

---

## If something goes wrong

- **Gate state shows DISABLED** and you didn't click disable: check `audit.log` for the `ROLLBACK` reason — it might be `COMMS_LOSS_30S` or `3x_CONSECUTIVE_REJECTION`.
- **Phase promotion reverts to A on restart**: config file isn't being written; check file permissions on `hxi_config.json`.
- **ML classifier stopped firing**: `_classifier_fail_count` is climbing. `loaded_models_info()` in `/api/models` will show the source path. Probably an ONNX runtime error — logs show the stack trace.
- **Dashboard 401 on every request**: token in `hxi_config.json` changed or was rotated. Clear browser localStorage (`hxi_dashboard_token` key) and re-enter.
- **WebSocket keeps reconnecting**: browser-side, not server. Check `/healthz` from a terminal; if that's 200, the browser is blocked by an extension or the VPN dropped.

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for the full list.
