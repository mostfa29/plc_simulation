# Safety

This document is the contract. If your change breaks anything here, **stop and reread** before merging.

---

## Hard rules

1. **All PLC writes go through `SafetyGate.validate_and_write()`.** There is no other write path. `ModbusManager` exposes FC16 only; FC06 is not wrapped.
2. **Phase < C = no writes.** `analysis_loop` computes advisory bounds in every phase but only calls `validate_and_write()` in Phase C and D.
3. **ML never writes.** Classifier and autoencoder output feeds `PerformanceMetrics` → `PIDAdvisor.advise()` → `SafetyGate`. They cannot short-circuit the gate.
4. **`VERIFIED_WORD_ORDER` and `SafetyLimitsConfig` defaults are `None`.** Service refuses to start until both are populated from the commissioning tests.
5. **Operator actions are audited.** Every dashboard POST that changes state writes a row to `audit.log` with old/new values. The text logger is not enough — only `audit.log` is fsynced per write.

---

## Phase plan

| Phase | Writes? | Gate condition for promotion |
|---|---|---|
| **A** (Observer) | No | Byte order verified + safety limits populated |
| **B** (Advisory) | No | ≥24 h of Phase A data, drilling-engineer review |
| **C** (Limited authority) | Yes (gated) | Every item in MASTER_CONTEXT §13 signed off |
| **D** (Full authority) | Yes (gated) | ≥2 drilling stands in Phase C with zero rollbacks |

Service boots in Phase A. Promotion is a **manual edit** to `hxi_optimizer/hxi_config.json` + service restart. The dashboard's "promote phase" button is deliberately gated with a confirmation dialog for C/D, and **every promotion is audited** (`DASHBOARD_PHASE_CHANGE` event in `audit.log`).

---

## The 9 gate layers

Every write passes every layer in order. First failure short-circuits with a rejection audit row.

| # | Layer | What it checks | Where |
|---|---|---|---|
| 1 | ESD | %R06664 bit ESD flag = 0 | `SafetyGate._check_esd` |
| 2 | Bump flags | FWD + REV bump flags clear | `_check_bump` |
| 3 | Abs bounds | lower ≥ abs_min_lower, upper ≤ abs_max_upper | `_check_abs_bounds` |
| 4 | Consistency | upper − lower ≥ min_band_counts | `_check_consistency` |
| 5 | Rate limit | |Δlower|, |Δupper| ≤ rate_limit_per_cycle | `_check_rate` |
| 6 | Heartbeat | %R06605 advanced since last write | `_check_heartbeat` |
| 7 | State machine | gate in ACCEPTING state (not ROLLING_BACK/ESD/DISABLED) | `_check_state` |
| 8 | FC16 + readback | write + re-read + byte-for-byte equality | `_do_write_and_verify` |
| 9 | Audit | row written + fsynced before returning | `audit.log_write` |

Abs bounds (#3) are populated from commissioning test #7 (safe-count range). The gate refuses to start if they're `None`. Defaults in `hxi_config.py`:

```python
abs_min_lower: None    # must be set from commissioning
abs_max_lower: None
abs_min_upper: None
abs_max_upper: None
min_band_counts: 50    # 20 RPM at 2.5 counts/RPM
```

---

## State machine

```
  BASELINE ──(advisor proposes)──► TRIAL ──(iae improves over window)──► ACCEPTED
     ▲                                │                                       │
     │                                │                                       │
     │                                └──(iae regresses)──► ROLLING_BACK ────►┘
     │                                                         │
     │                                                         ▼
     │                                             LKG (last-known-good)
     │                                                         │
     └─────────────────────────────────────────────────────────┘

  ANY STATE ──ESD bit set──────────► ESD (writes blocked until cleared + ack)
  ANY STATE ──COMMS_LOSS_30S───────► ROLLING_BACK → LKG
  ANY STATE ──3× consecutive rej───► ROLLING_BACK → LKG
  ANY STATE ──operator disable─────► DISABLED (writes blocked until enable)
```

Transitions, preserved on restart via `state.json`:

| Transition | Trigger | Audit event |
|---|---|---|
| BASELINE → TRIAL | advisor proposes a new bound | `WRITE` (accepted) |
| TRIAL → ACCEPTED | trial IAE < previous IAE for ≥N samples | `WRITE` with `state=ACCEPTED` |
| TRIAL → ROLLING_BACK | trial IAE > previous IAE for ≥N samples | `ROLLBACK` with reason `IAE_REGRESSION` |
| ACCEPTED → TRIAL | advisor proposes a further move | `WRITE` |
| any → ESD | %R06664 bit set | `ROLLBACK` with reason `ESD` |
| any → DISABLED | operator hits disable | `DASHBOARD_DISABLE` |

The gate writes `state.json` after every accepted write. On boot, `load_state()` restores `current_lower`, `current_upper`, `lkg`, and `state`.

---

## LKG (last-known-good)

If the trial regresses or comms drop for 30 s, the gate writes LKG back to the PLC and re-enters BASELINE. LKG is the most recent `(lower, upper, iae)` triple where `iae` was the minimum seen.

`state.json` holds `lkg_lower`, `lkg_upper`, `lkg_iae_at_acceptance`. The JSON is written with an atomic `.tmp → rename` so a power loss during the write doesn't corrupt it.

---

## Heartbeat + COMMS_LOSS

`heartbeat_loop` writes a monotonic counter to `%R06605` every 5 s via FC16. The PLC is expected to mirror the counter (either by relaying it or by incrementing its own side). If `modbus.consecutive_failures >= 6` (30 s), `connection_monitor` calls `gate.trigger_rollback("COMMS_LOSS_30S")` and the gate writes LKG via FC16 before giving up.

If writing LKG itself fails (comms are dead), the gate logs a `CRITICAL` event and leaves the PLC with whatever bounds are resident. The PLC's own watchdog is the last line of defense — the optimizer does not guarantee a safe state if its own comms are dead.

---

## Testing

Every gate layer is covered:

| File | Coverage |
|---|---|
| [`test_safety_gate.py`](../hxi_optimizer/tests/test_safety_gate.py) | All 9 layers, individually + in sequence |
| [`test_safety_gate_extended.py`](../hxi_optimizer/tests/test_safety_gate_extended.py) | State transitions, COMMS_LOSS, ESD, rollback, LKG |
| [`test_integration.py`](../hxi_optimizer/tests/test_integration.py) | End-to-end with simulated PLC |
| [`test_persistence.py`](../hxi_optimizer/tests/test_persistence.py) | `state.json` atomic write, restart continuity |

`python -m pytest hxi_optimizer/tests/test_safety_gate*.py -q` → all green.

---

## Commissioning tests (pre-Phase-A)

Before the service runs against a real PLC, run:

```bash
python -m hxi_optimizer.deploy.commissioning_tests
```

Covers (MASTER_CONTEXT §12):

1. Modbus handshake (FC03 read of %R06600, non-zero, correct byte order)
2. Float byte-order verification (known register with known value)
3. Heartbeat write round-trip
4. Safe bounds discovery (moves bounds through a range, watches for RPM instability)
5. ESD bit reachable (manually trigger + verify read)
6. Bump flags reachable
7. Rate-limit tuning
8. FC16 + readback consistency (write N, read back, bytes must equal)

Tests populate `hxi_config.json`:

- `VERIFIED_WORD_ORDER` (ABCD/BADC/CDAB/DCBA)
- `safety.abs_min_lower`, `abs_max_lower`, `abs_min_upper`, `abs_max_upper`
- Service will now start in Phase A.

---

## Invariants to preserve

If you touch these, run the full test suite **and** re-read MASTER_CONTEXT §7.5 before merging:

- **Write path uniqueness**: every FC16 in the codebase must go through `SafetyGate.validate_and_write()` or `_do_write_and_verify()` (internal helper). Grep for `func=16` or `write_registers` before merging.
- **Audit-before-return**: `validate_and_write` writes the audit row before returning on success. If you add a new accepted path, audit in the same function.
- **Phase check at the top**: `analysis_loop` checks `config.phase in (Phase.C, Phase.D)` before calling the gate. If you refactor phase handling, keep this check in the outer loop, not inside the gate.
- **`_model_lock`**: `PerformanceMonitor._classify_ml` and `_compute_anomaly_score` snapshot model state under the lock *before* calling `session.run()`. If you add a third inference method, do the same.
- **Audit for dashboard actions**: every state-changing dashboard endpoint calls `_audit_operator(shared, EVENT, reason)`. If you add one, audit it.

See also [DEVELOPMENT.md](DEVELOPMENT.md) for how to add a new gate layer without breaking the ordering guarantees.
