# Operator Quick Reference

**For the crew on shift.** One page. Pin near the rig PC.

This is what you need to know to use the dashboard during a shift. Everything else (deployment, training, debugging) is in the other docs — you don't need to touch them.

---

## Daily start

1. Open browser to **http://localhost:8420**.
2. Header should show:
   - Green dot top-left (connection OK)
   - **PHASE: A / B / C / D** — should match what was set last shift
   - **STATE: ADAPTING / BASELINE / TRIAL / ACCEPTED** — green is good
3. If the dot is **red** → see "Red dot" below.
4. If the page asks for a token → enter the value Steve provided. Browser remembers it.

---

## 2-minute health check (do this once per shift)

Click the **System Test** tab. The battery runs automatically and shows green / amber / red for each part of the system. Takes about 2 seconds.

- **All green** → system is healthy, get on with the shift.
- **Anything red** → click the row to read what to do. If the fix isn't obvious, call Steve.
- **Amber rows** → the system is working but something is degraded. Read the row, fix if you can, otherwise note it and continue.

You can re-run this at any time during the shift if something looks off (slow dashboard, weird numbers, suspicious noise). It's safe to run repeatedly — no PLC writes happen.

---

## What the dashboard tells you

| Color / state | What it means | Action |
|---|---|---|
| Green dot | All comms healthy | None |
| Yellow dot | A few PLC reads have failed | Wait 30 s; if still yellow, see "Comms degraded" |
| Red dot | PLC unreachable | See "Red dot" below |
| **STATE: ESD** | ESD bit triggered at the PLC | Investigate at PLC, then click **Enable** |
| **STATE: ROLLING_BACK** | Optimizer detected a regression and is reverting bounds | Watch for 60 s — usually self-resolves |
| **STATE: DISABLED** | Someone clicked Disable | If safe, click **Enable** to resume |
| **PHASE: A / B** | Advisory only, no writes | Normal during commissioning |
| **PHASE: C / D** | Writes enabled, gated | Production mode |

---

## Annotate workflow (the main thing the crew does)

When something **interesting** happens during drilling — anything you'd describe to your supervisor — click the matching button on the **Controls** tab within ~20 seconds of the event.

| Click this | When |
|---|---|
| **Oscillation** | Torque or RPM is bouncing visibly |
| **Stickslip** | Classic grab/release; low-frequency vibration |
| **Bias** | RPM sits consistently above or below setpoint |
| **Formation change** | Rate of penetration noticeably changed |
| **Bad connection** | A pipe connection that didn't go smoothly |
| **Good connection** | Optional — a textbook connection you want kept as a positive example |

Each click pulls the last 20 seconds of telemetry off the wire and saves it as a labeled example. The model uses these to fine-tune itself for **this** rig. You're not grading the optimizer — you're teaching it.

**Aim for ~10 events per fault type per shift.** More is better. There's no penalty for over-labeling.

---

## When to call Steve

- Red dot for more than 5 minutes (you've checked the basics — see below).
- ESD state that doesn't clear after the operator resets the PLC.
- Continuous ROLLING_BACK every few minutes (the model thinks every move is bad).
- Dashboard won't load at all.
- Audit log warnings about anything you didn't do.

For everything else, the system is designed to recover itself. Wait 60 seconds before escalating.

---

## Red dot (PLC unreachable)

1. Check **Fleet** tab → eCatcher status. Is the tunnel up?
   - If down: open eCatcher, reconnect to the rig.
2. Once tunnel is up, the dot should turn green within 30 s.
3. If still red after 1 minute: open the **Diagnostics** tab → look at "transport health". A `CRITICAL` line tells you what to escalate.

---

## Comms degraded (yellow dot)

- Often clears itself within 30 s.
- If it stays yellow for >2 min: VPN is glitching. Check eCatcher log. Re-establish if needed.
- The system **does not write to the PLC** while comms are unhealthy — there is no risk to the rig from a degraded indicator.

---

## Phase change

**Do not change the phase without Steve's go-ahead.** The dropdown on the Controls tab is gated with a confirmation dialog for C and D, but a wrong promotion has consequences.

Every phase change is recorded in `audit.log` with the operator's action and the old/new phase.

---

## Enable / Disable

The **Disable** button (Controls tab) freezes the optimizer's writes. Use it if:
- You want to manually adjust the rig and don't want the optimizer fighting you.
- Something looks weird and you want to stop the world while you check.

The **Enable** button reverses that. Both actions are audited.

---

## What the optimizer is doing right now

The **Intel** tab → **Digest** card gives you a plain-English summary, e.g.:
> "Torque rising steadily over the last 5 minutes — possible formation change at ~3,400 ft. Recommended: widen upper bound by 10 counts."

If the digest says nothing actionable, the system is happy and you don't need to do anything.

---

## A/B compare (only after a fine-tune)

When Steve tells you a per-rig model is ready to evaluate:

1. Fleet tab → A/B Compare card.
2. Pick this rig from the list.
3. Click **Run Compare**. Wait ~10 seconds.
4. Read the recommendation:
   - **PROMOTE** — keep the fine-tune deployed.
   - **NEUTRAL** — no real difference; either is fine.
   - **ROLLBACK** — fine-tune is worse; ask Steve to revert.

Don't act on the recommendation yourself — relay it to Steve.

---

## End of shift

Nothing required. The system runs as a service, not a program you start/stop. Just close the browser when you're done. The optimizer keeps running.

---

## Emergency stop

If something is genuinely wrong and you need the optimizer **out** of the loop **right now**:

1. Click **Disable** on the Controls tab. (One click. Does not require a phase change.)
2. The optimizer stops writing within the next read cycle (~0.5 s).
3. The PLC continues running on its own PID loop unaffected.
4. The current bounds stay where they are — they don't reset.

To completely stop the service (for example, to replace the rig PC):

```cmd
sc stop HXIOptimizer
```

---

For deeper info: [OPERATION.md](OPERATION.md) covers everything in detail.
For when something is broken: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
