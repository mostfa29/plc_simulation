# Development

How to extend the optimizer without breaking safety guarantees or the test suite.

---

## Ground rules

1. **Read [SAFETY.md](SAFETY.md) before touching anything under `control/` or `deploy/`.** The 9-layer gate is a contract; adding a new layer requires reordering the sequence + adding a test.
2. **Every POST endpoint that changes state must audit.** Call `_audit_operator(shared, EVENT, reason)` after the mutation succeeds.
3. **Every CPU-bound dashboard endpoint must use the executor + timeout.** `await _run_cpu_bound(shared, fn, *args)`.
4. **Features are ordered**: `[rpm_encoder, swash_output, ss_setpoint_fwd, active_lower, active_upper, delivered_torque, loop_temp]`. Changing the order anywhere breaks the ONNX model, the fine-tune pipeline, and the dataset capture. Don't.
5. **Per-rig models never overwrite the default.** Fine-tune writes to `models/per_rig/<slug>/`. The registry never copies per-rig → default.
6. **Backwards compatibility for `state.json`.** If you add a field, make it optional with a default.

---

## Running tests

```bash
python -m pytest hxi_optimizer/tests/ -q
```

~60 s for 2,145 tests. Stop at first failure:

```bash
python -m pytest hxi_optimizer/tests/ -x -q
```

Single file:

```bash
python -m pytest hxi_optimizer/tests/test_safety_gate.py -v
```

All tests use in-process fixtures (no network, no real PLC). Integration tests that need Modbus spin up `local_test.sim_plc` via pytest-asyncio fixtures.

---

## Adding a new failure-mode scenario

Edit `training/scenarios.py`. Add a new generator function:

```python
def generate_my_scenario(duration_s: float = 300,
                          setpoint: float | None = None,
                          equipment_type: str | None = None,
                          seed: int = 0
                          ) -> tuple[list[dict], list[str]]:
    sim = _make_sim(equipment_type)
    rng = np.random.default_rng(seed)
    sim._rng = rng
    setpoint = _randomise_operating_point(sim, rng, setpoint)
    # ... your physics ...
    samples, labels = [], []
    for t in np.arange(0, duration_s, 0.5):
        dist = ...
        samples.append(sim.step(setpoint, disturbance=dist))
        labels.append("YOUR_LABEL" if t > onset else "NORMAL")
    return samples, labels
```

Register it:

```python
ALL_GENERATORS = {
    ...
    "my_scenario": generate_my_scenario,
}
# Add to TRAINING_GENERATORS if it should appear in dataset generation
```

Then:

1. Smoke-test:

    ```bash
    python -c "from training.scenarios import ALL_GENERATORS; \
       s, l = ALL_GENERATORS['my_scenario'](seed=42); print(len(s), set(l))"
    ```

2. Add a test in `hxi_optimizer/tests/test_simulator_v2.py` that locks in the scenario's invariants (key distinguishing features).

3. Regenerate the dataset + retrain (see [ML_PIPELINE.md](ML_PIPELINE.md)). The new label must be in `training/train_classifier_torch.py:CLASSES` if it's a new fault class.

4. Update `training/fine_tune.py:LABEL_REMAP` if real data might use a different name for this scenario (e.g., operator calls it "X" but the model class is "Y").

---

## Adding a new diagnosis rule

The intelligence layer produces human-readable diagnoses from `PerformanceMetrics`. Edit `hxi_optimizer/intelligence/diagnosis.py`:

```python
class DiagnosisEngine:
    def diagnose(self, metrics, machine, depth_ft):
        diags = []
        # ... existing rules ...

        # Your new rule
        if metrics.sat_upper > 0.7 and metrics.failure_mode == "WINDUP":
            diags.append(Diagnosis(
                severity="warn",
                title="Upper-bound windup",
                description=(
                    f"Saturating upper bound at {metrics.sat_upper:.0%} while "
                    f"classifier reports WINDUP. Consider raising upper bound "
                    f"or widening the band."
                ),
                recommendation="Increase active_upper by 20 counts.",
                evidence={
                    "sat_upper": metrics.sat_upper,
                    "failure_mode": metrics.failure_mode,
                    "confidence": metrics.failure_confidence,
                },
            ))
        return diags
```

Add a test in `test_intelligence.py` that builds a fake `PerformanceMetrics` and asserts the rule fires (and doesn't fire when conditions aren't met).

---

## Adding a new POST endpoint to the dashboard

See [DASHBOARD.md](DASHBOARD.md) — the "Extending" section has the skeleton. Minimum checklist:

1. Pydantic body model (not `dict`) with strict field constraints.
2. Wrap CPU work in `await _run_cpu_bound(shared, ...)`.
3. Audit state-changing actions via `_audit_operator(...)`.
4. Add tests in `test_dashboard_prod.py`:
   - Happy path
   - 422 on bad input
   - Audit row written on success
   - **No** audit row on validation failure
   - 401 when auth enabled and no token

---

## Adding a new gate layer

1. Read [SAFETY.md](SAFETY.md). Understand the 9 layers + ordering.
2. Pick a position in the sequence. New layer goes **before** FC16+readback (layer 8) and **before** audit (layer 9).
3. Add the check method in `SafetyGate`:

    ```python
    def _check_my_new_gate(self, lower, upper) -> tuple[bool, str]:
        if <bad condition>:
            return False, "MY_REJECTION_REASON"
        return True, ""
    ```

4. Wire it into `validate_and_write`:

    ```python
    ok, reason = self._check_my_new_gate(lower, upper)
    if not ok:
        self.audit.log_rejected(lower, upper, reason)
        self.consecutive_rejections += 1
        return WriteResult(accepted=False, reason=reason)
    ```

5. Add tests covering: pass case, fail case, fail case does NOT increment state machine (rejection is not acceptance).

6. Update [SAFETY.md](SAFETY.md)'s "The 9 gate layers" table to 10.

---

## Retraining with new sim physics

Full workflow in [ML_PIPELINE.md](ML_PIPELINE.md). Short version:

```bash
# 1. Edit training/simulator.py / training/scenarios.py
# 2. Regenerate
python -m training.generate_dataset --per-scenario 100 \
    --equipment-types all --output training/data/sim_v3.npz
python -m training.prepare_windows --input training/data/sim_v3.npz \
    --output training/data/windows_v3.npz

# 3. Retrain (unbuffered + UTF-8 for Windows)
PYTHONIOENCODING=utf-8 python -u -m training.train_classifier_torch \
    --data training/data/windows_v3.npz \
    --model-out training/models/classifier_torch_v3 \
    --epochs 50

PYTHONIOENCODING=utf-8 python -u -m training.train_autoencoder_torch \
    --data training/data/windows_v3.npz \
    --model-out training/models/autoencoder_torch_v3 \
    --epochs 40

# 4. Back up current deployment
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p hxi_optimizer/models/backup_v2_$TS
cp hxi_optimizer/models/classifier.* hxi_optimizer/models/backup_v2_$TS/
cp hxi_optimizer/models/autoencoder.* hxi_optimizer/models/backup_v2_$TS/

# 5. Deploy
cp training/models/classifier_torch_v3/classifier.onnx hxi_optimizer/models/
cp training/models/classifier_torch_v3/meta.json hxi_optimizer/models/classifier_meta.json
cp training/models/autoencoder_torch_v3/autoencoder.onnx hxi_optimizer/models/
cp training/models/autoencoder_torch_v3/meta.json hxi_optimizer/models/autoencoder_meta.json

# 6. Run tests
python -m pytest hxi_optimizer/tests/ -q

# 7. Restart service
sc restart HXIOptimizer
```

---

## Adding a per-rig register profile

A new rig class (non-HXI) has a different Modbus register map. Create a profile:

```yaml
# hxi_optimizer/comms/profiles/warrior_default.yaml
name: "Warrior default"
registers:
  rpm_encoder:    { address: 1234, dtype: REAL }
  swash_output:   { address: 1236, dtype: REAL }
  # ...
overrides:
  deadband_rpm: 3.0
```

Register it:

1. Add the equipment spec to `hxi_optimizer/comms/fleet.py:EQUIPMENT_CATALOG`.
2. Update `fleet_catalog.yaml` entries that reference this equipment type to point at the new profile.
3. Run `test_equipment_coverage.py` — fails if any catalog entry lacks a spec.
4. Run the full test suite.

---

## Debugging a live optimizer

The optimizer runs as a single process. To dump state in a running service:

- Dashboard: `/api/diagnostics` — CPU, memory, disk, transport health.
- Dashboard: `/api/status` — full snapshot.
- Dashboard: `/api/models` — which model pair is loaded, inference counts, failure counts.
- Terminal: `tail -F hxi_optimizer/logs/optimizer.log`.
- Terminal: `tail -F hxi_optimizer/logs/audit.log`.
- If asyncio is stuck, install `aiomonitor` (not bundled) and hook it into `main.py` for an async REPL.

---

## Release checklist

Before tagging a new version:

- [ ] `python -m pytest hxi_optimizer/tests/ -q` → all pass
- [ ] `python -m pytest hxi_optimizer/tests/test_safety_gate*.py -v` → special care
- [ ] Dashboard manually exercised (login, phase change, annotate, A/B compare)
- [ ] `python -m hxi_optimizer.main` starts cleanly, reaches `Dashboard available at ...`
- [ ] `/healthz` returns 200
- [ ] Update `README.md` test count if it changed
- [ ] Update `docs/ML_PIPELINE.md` model metrics if retrained
- [ ] Tag commit, update changelog in commit message
