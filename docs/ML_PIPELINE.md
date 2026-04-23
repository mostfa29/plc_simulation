# ML Pipeline

Sim-train a classifier and autoencoder on synthetic physics, fine-tune on real captured episodes per rig, deploy to `hxi_optimizer/models/` with validation gates. The default classifier is **never** overwritten by fine-tunes — per-rig models live under `models/per_rig/<slug>/` and the registry hot-swaps them when the rig changes.

---

## Models in production

| Model | Shape | Classes | Deployed | Test accuracy |
|---|---|---|---|---|
| Classifier (1D-CNN) | 40×7 → 7 logits | NORMAL, BIAS, OSCILLATION, DEADBAND_HUNTING, SLUGGISH, WINDUP, CONDITION_CHANGE | `hxi_optimizer/models/classifier.onnx` | **98.05%** |
| Autoencoder (Conv1D) | 40×7 → 16-latent → 40×7 | (NORMAL only, reconstruction error = anomaly score) | `hxi_optimizer/models/autoencoder.onnx` | 8.42× separation |

Per-rig fine-tunes under `models/per_rig/<slug>/` — registry resolves at startup + on machine change.

Features (order matters — must match everywhere):

```
[rpm_encoder, swash_output, ss_setpoint_fwd,
 active_lower, active_upper, delivered_torque, loop_temp]
```

Same order in sim (`training/simulator.py`), real-time capture (`realtime_dataset.py`), live inference (`performance_metrics.py`), fine-tune (`training/fine_tune.py`). Changing the order anywhere breaks all three — don't.

---

## End-to-end pipeline

```
 training/scenarios.py  ─► training/generate_dataset.py  ─► sim_v2.npz
                                                                │
                                                                ▼
 training/prepare_windows.py ──────────────────────────► windows_v2.npz
                                                                │
                               ┌────────────────────────────────┤
                               ▼                                ▼
           training/train_classifier_torch.py     training/train_autoencoder_torch.py
                               │                                │
                               ▼                                ▼
              classifier.onnx + meta.json       autoencoder.onnx + meta.json
                               │                                │
                               └───────────── cp ───────────────┘
                                             │
                                             ▼
                       hxi_optimizer/models/ (default sim pair)
                                             │
                                             │  real captured episodes
                                             ▼
                       training/fine_tune.py --rig "<name>"
                                             │
                                 validation gate (> sim baseline?)
                                             │
                                 ┌─── pass ──┴── fail ───┐
                                 ▼                        ▼
             hxi_optimizer/models/per_rig/<slug>/    no deploy
                                 │
                                 ▼
                    ModelRegistry.resolve(rig) — hot-swapped via switch_models()
```

---

## Stage 1 — Simulation

`training/simulator.py` produces 7-channel samples at 2 Hz. Key physics:

- **Hydraulic plant**: 1st-order RPM lag (τ≈0.35 s) driven by swash command.
- **Torque**: `static_load + J*dω/dt + b*rpm + proportional sensor noise`. Matches how the real CPE305 reports `delivered_torque` (pump-flow-derived), **not** PID error.
- **Temperature**: 1st-order heat-soak from duty² (τ≈300 s). Rises under load, relaxes to ambient when idle.
- **Noise floor**: speed-dependent Gaussian per §6.1.

`training/scenarios.py` has 11 generators:

| Scenario | Label | Notes |
|---|---|---|
| normal | NORMAL | random setpoint 30–110, random bound width |
| bias | BIAS | random bias_rpm ±3–12 |
| oscillation | OSCILLATION | random amp 5–15, period 3–10 s |
| stickslip | OSCILLATION | rectangular on/off (legacy) |
| multiscale_stickslip | OSCILLATION | mixed-frequency + Poisson impulses |
| formation_change | CONDITION_CHANGE | linear load ramp |
| chaotic_formation_change | CONDITION_CHANGE | piecewise-linear + step jumps |
| sluggish | SLUGGISH | tau=1.5–4×, tight bounds |
| windup | WINDUP | bounds undersized 40–70% of needed |
| deadband_hunting | DEADBAND_HUNTING | low-amplitude short-period osc |
| connection | NORMAL | **excluded from training** (TRAINING_GENERATORS filter) |

Generate:

```bash
python -m training.generate_dataset --per-scenario 100 --equipment-types all \
    --output training/data/sim_v2.npz
python -m training.prepare_windows --input training/data/sim_v2.npz \
    --output training/data/windows_v2.npz --window-size 40 --stride 10
```

Takes ~8 min on a desktop (648K windows for 9 equipment types × 10 scenarios × 100 runs). Class distribution is auto-logged.

---

## Stage 2 — Classifier training

```bash
python -u -m training.train_classifier_torch \
    --data training/data/windows_v2.npz \
    --model-out training/models/classifier_torch_v2 \
    --epochs 50 --batch-size 256 --patience 10
```

Defaults (all tunable via CLI):

| Hyperparam | Value |
|---|---|
| Architecture | Conv1D(32,5) → BN → Conv1D(64,5) → pool → Conv1D(64,3) → pool → GAP → Dense(32) → Dropout(0.2) → Dense(7) |
| Loss | Cross-entropy with balanced class weights |
| Optimizer | Adam(lr=3e-4, weight_decay=1e-5) |
| LR schedule | ReduceLROnPlateau(factor=0.5, patience=5) on val_acc |
| Early stopping | patience=10 on val_acc |
| Split | 75 / 12.5 / 12.5 stratified |
| Normalization | Per-channel z-score (μ, σ stored in meta.json) |

Takes ~30 min on GTX 1650 (4 GB VRAM), ~10 min on T4.

**Output**:

- `model.pt` (PyTorch checkpoint)
- `classifier.onnx` (monolithic, opset 17, `dynamo=False` to avoid `.data` sidecar)
- `meta.json` (classes, X_mean, X_std, test_accuracy, classification_report, confusion_matrix)

Copy to production:

```bash
cp training/models/classifier_torch_v2/classifier.onnx \
   hxi_optimizer/models/classifier.onnx
cp training/models/classifier_torch_v2/meta.json \
   hxi_optimizer/models/classifier_meta.json
```

Service restart required for the new model to load (or call `monitor.switch_models()` programmatically).

### Encoding gotcha

`torch.onnx.export` sometimes prints Unicode characters that break Windows cp1252 stdout redirection. Always run training with `PYTHONIOENCODING=utf-8`:

```bash
PYTHONIOENCODING=utf-8 python -u -m training.train_classifier_torch ...
```

---

## Stage 3 — Autoencoder training

```bash
PYTHONIOENCODING=utf-8 python -u -m training.train_autoencoder_torch \
    --data training/data/windows_v2.npz \
    --model-out training/models/autoencoder_torch_v2 \
    --epochs 40 --batch-size 256
```

Trained on NORMAL windows only. Outputs reconstruction error; windows with high error are flagged anomalous. Threshold = `mean + 3σ` on the training set.

`meta.json` fields: `X_min`, `X_max`, `threshold`, `separation_ratio`, `detection_rate_at_threshold`. The `separation_ratio` (anomaly median / normal mean) is the key quality metric — **higher is better**, current V2 is 8.42×.

Takes ~6 min on GTX 1650.

---

## Stage 4 — Fine-tune on real data

Real episodes live at `hxi_optimizer/logs/dataset/<machine_slug>/<LABEL>/episode_<ts>.npz`. Captured automatically (for `CONNECTION` events) or via dashboard Annotate buttons.

```bash
# Per rig
python -m training.fine_tune --rig "Precision Rig 707 3pd HT"

# Fleet-wide (combines all machines)
python -m training.fine_tune --rig all

# Dry run — train but don't deploy
python -m training.fine_tune --rig "Precision Rig 707 3pd HT" --no-deploy

# Validate only — no training, just report what's captured
python -m training.fine_tune --validate-only
```

### What happens

1. **Load real episodes** — walks `logs/dataset/<slug>/<label>/*.npz`, filters by rig.
2. **Class-imbalance check** — if any class has <10 windows, auto-boosts `mix_sim_ratio` to 0.5 (keep minority classes alive through sim data).
3. **Feature-drift gate** — computes z-scores of real data against sim X_mean/X_std. Flags channels where >15% of samples are >3σ from sim, or mean shift >2σ. Surfaces in `result["feature_drift"]["severe_drift_channels"]`. **Does not block training** — it's a warning, not a gate.
4. **Mix sim data** — default 30% sim, 70% real. Anti-forgetting.
5. **Fine-tune** — LR=1e-4, patience=5, early-stop when val_acc plateaus. Held-out is real-only.
6. **Validation gate** — fine-tune accuracy must **exceed** sim baseline on held-out real data. If not, no deploy.
7. **Deploy** — writes to `hxi_optimizer/models/per_rig/<slug>/` (not the default). Backs up any previous per-rig model at `classifier.onnx.backup_<ts>`.
8. **AE threshold recalibration** — runs the sim-trained AE on real NORMAL windows, computes new `mean+3σ` threshold, writes to `per_rig/<slug>/autoencoder_meta.json`. ONNX file stays shared; threshold is per-rig.

### Refused-deploy example

```
VALIDATION GATE FAILED: fine-tune (0.8654) did not beat sim baseline (0.8750).
Sim model stays in production.
```

This is the intended behavior. An imbalanced dataset with only a few classes represented is not enough to improve. Collect more, retry.

---

## Stage 5 — Per-rig model registry

`hxi_optimizer/intelligence/model_registry.py` discovers what's deployed.

```python
reg = ModelRegistry()
pair = reg.resolve("Precision Rig 707 3pd HT")
# Returns ModelPair(rig_slug='precision_rig_707_3pd_ht',
#                   source='per_rig' | 'default',
#                   classifier_path=..., classifier_meta_path=...,
#                   autoencoder_path=..., autoencoder_meta_path=...)
```

**Resolution order**:

1. `models/per_rig/<slug>/classifier.onnx` exists → use per-rig classifier.
2. Per-rig AE present → use per-rig AE. Else inherit default AE.
3. No per-rig pair → default sim pair.
4. No default either → empty pair (system runs heuristic-only on `_classify()` ACF fallback).

**Hot-swap** happens inside `PerformanceMonitor.switch_models()`:

- Stages new ONNX sessions outside the model lock.
- If both load successfully, takes `_model_lock` (RLock) and atomically swaps references.
- `_classify_ml()` and `_compute_anomaly_score()` snapshot model state under the same lock before calling `session.run()` — concurrent swaps cannot tear mid-inference.

Triggered at:

- Service startup, after machine identification.
- `connection_monitor`'s machine-change branch (eCatcher tunnel switched to a different rig).

---

## Stage 6 — A/B compare before promoting

Dashboard Fleet tab → A/B Compare, or:

```
GET /api/intel/compare-models?rig=<name>&n_max=500
```

Runs both the default sim pair and the per-rig pair against the same captured real episodes. Returns:

- Per-model accuracy (overall + per-class).
- Per-model mean confidence.
- Confusion matrix.
- Improvement delta (pp).
- Recommendation: `promote`, `neutral`, `rollback`, `insufficient_data`, `no_fine_tune_yet`.

Thresholds:

- **promote**: delta > +2pp on ≥50 windows.
- **rollback**: delta < −2pp.
- **neutral**: within ±2pp.
- **insufficient_data**: <50 windows total.

The endpoint runs the CPU-bound inference in the ThreadPoolExecutor with a 30 s timeout so a 500-window compare can't block the 2 Hz read_loop.

---

## Config knobs

`training/fine_tune.py` CLI flags (defaults in parens):

- `--rig` rig name (required) or `"all"` for fleet-wide.
- `--mix-sim-ratio` 0.3 — fraction of sim data to mix in for anti-forgetting.
- `--epochs` 20 — max epochs (early-stop kicks in first).
- `--lr` 1e-4 — lower than sim training (3e-4) for stable fine-tune.
- `--seed` 42
- `--no-deploy` — don't write to `models/per_rig/`, just save versioned in `training/models/finetune/`.
- `--validate-only` — skip training, just print episode stats.
- `--skip-classifier` / `--skip-ae-recalibration` — partial runs.

---

## Rollback

If a fine-tune deployed and is misbehaving:

```bash
# Find the backup
ls hxi_optimizer/models/per_rig/<slug>/classifier.onnx.backup_*

# Restore the previous per-rig model
cd hxi_optimizer/models/per_rig/<slug>/
mv classifier.onnx classifier.onnx.bad_$(date +%Y%m%d)
mv classifier.onnx.backup_<ts> classifier.onnx

# Or roll back to sim default entirely — just delete the per-rig dir
rm -r hxi_optimizer/models/per_rig/<slug>/
```

Service restart (or wait for next machine-change event) — the registry picks up the change automatically.

---

## Testing

| File | What it covers |
|---|---|
| [`test_ml_classifier.py`](../hxi_optimizer/tests/test_ml_classifier.py) | Classifier inference, per-scenario correctness, held-out accuracy |
| [`test_autoencoder.py`](../hxi_optimizer/tests/test_autoencoder.py) | AE inference, threshold, separation |
| [`test_model_registry.py`](../hxi_optimizer/tests/test_model_registry.py) | slugify, resolve fallback, per-rig preference, switch_models thread safety |
| [`test_compare_models.py`](../hxi_optimizer/tests/test_compare_models.py) | Recommend logic, evaluation pipeline, JSON shape |
| [`test_fine_tune.py`](../hxi_optimizer/tests/test_fine_tune.py) | Dataset loading, label remap, insufficient-data path, AE recalibration |
| [`test_simulator_v2.py`](../hxi_optimizer/tests/test_simulator_v2.py) | Torque physics, thermal model, scenario realism, operating-point randomization |

Everything green at time of writing: 2,145 passing across the full suite.
