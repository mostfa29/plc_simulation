# Phase 1: Synthetic Pretraining Pipeline

## Overview

Phase 1 trains a time-series classifier on synthetic simulator data to distinguish 10 connection outcome classes. The trained model serves as a pretrained backbone for Phase 2 (fine-tuning on real field data).

**Target**: Macro F1 > 0.85 on held-out synthetic test set, with TSTR ratio > 90%.

## Architecture

### InceptionTime Ensemble (Primary - Tier 2)

Ensemble of 5 independently-initialized InceptionTime networks. Final prediction = mean of softmax outputs.

Each network:
```
Input [B, 12, 2000]
  -> InceptionBlock 1 (3 InceptionModules + residual skip)
  -> InceptionBlock 2 (3 InceptionModules + residual skip)
  -> Global Average Pooling -> [B, 128]
  -> FC -> [B, 10]
```

Each InceptionModule:
```
Input -> Bottleneck (1x1 conv, 32 filters)
      -> Branch A: Conv1d k=11 (~0.11s at 100Hz)
      -> Branch B: Conv1d k=21 (~0.21s)
      -> Branch C: Conv1d k=41 (~0.41s)
      -> Branch MP: MaxPool(3) -> Conv1d(1)
      -> Concat [4 * 32 = 128 channels] -> BN -> ReLU
```

- ~495K params per network, ~2.5M total ensemble
- Ensemble reduces variance by ~60% vs single network

### ResNet Baseline (Tier 1 - Pipeline Validation)

Simple 3-block ResNet for validating the data pipeline before committing to full ensemble training.

```
Input [B, 12, 2000]
  -> ResBlock(12, 64)   k=7,5
  -> ResBlock(64, 128)  k=7,5
  -> ResBlock(128, 128) k=7,5
  -> GAP -> FC(128, 10)
```

- ~374K params
- Target: >80% macro F1. If this fails, debug data before trying InceptionTime.

## Input Channels (12 total)

| Index | Channel | Source | Description |
|-------|---------|--------|-------------|
| 0 | torque_ftlbs | Raw | Measured torque |
| 1 | rpm | Raw | Rotational speed |
| 2 | pressure_psi | Raw | Hydraulic/system pressure |
| 3 | oil_temp_f | Raw | Oil temperature |
| 4 | turns | Raw | Cumulative turn count |
| 5 | hookload_klbs | Raw | Hook load |
| 6 | d_torque_dt | Derived | Torque rate of change (ft-lbs/sec) |
| 7 | d_torque_dturns | Derived | Torque-turn slope (ft-lbs/turn) - primary diagnostic |
| 8 | torque_norm | Derived | torque / target_torque (0-1+) |
| 9 | turns_norm | Derived | turns / expected_turns (0-1+) |
| 10 | phase | Derived | Connection phase indicator (0-5) |
| 11 | mask | Derived | Validity mask (1=real, 0=padded) |

### Key Derived Feature: d_torque_dturns

The torque-turn slope is the primary diagnostic signal:
- **Galling**: Progressive slope increase (metal-to-metal friction buildup)
- **Stripped thread**: Slope plateau then sudden drop
- **Cross-thread**: Extreme slope at low turn count
- **Normal**: Smooth exponential rise at shoulder

## Output Classes (10)

| Class | Label | Description |
|-------|-------|-------------|
| 0 | normal_makeup | Normal connection (LTC, BTC, premium, drill pipe, tubing) |
| 1 | cross_thread | Threads engaged at wrong angle |
| 2 | galling | Metal-to-metal adhesive wear |
| 3 | stripped_thread | Thread failure under load |
| 4 | over_torque | Exceeded target torque |
| 5 | under_torque | Failed to reach target torque |
| 6 | wrong_compound | Incorrect thread compound applied |
| 7 | misaligned_stab | Poor stabbing alignment |
| 8 | stall | Motor stall during makeup |
| 9 | coupling_makeup | Coupling-specific makeup pattern |

## Data Pipeline

### 1. Generate Dataset

```bash
# Phase 1 production dataset (5000 scenarios, Parquet, rebalanced)
python generate_dataset.py \
  --count 5000 \
  --output ./data/synthetic_v2 \
  --output-format parquet \
  --class-balance rebalanced \
  --seed 42
```

The `--class-balance rebalanced` flag uses a 50/50 normal/fault split instead of the field-realistic 65/35 distribution. This improves minority class learning.

### 2. Preprocessing (dataset.py)

1. **Load scenarios** from Parquet/CSV files listed in manifest.csv
2. **Compute derived channels** (features.py): 6 raw -> 12 total channels
3. **Sliding window**: 2000 samples @ 100Hz = 20s window, stride 1000 (50% overlap)
4. **Zero-pad** short windows (mask channel = 0 for padded regions)
5. **Stratified scenario-level split**: 70% train / 15% val / 15% test
   - All windows from one scenario stay in the same split (no data leakage)
6. **Z-score normalization**: Per-channel mean/std from TRAINING set only
   - Saved to `norm_params.json` for inference
   - Mask channel (index 11) is never normalized

### 3. Training (train.py)

```bash
# Full InceptionTime ensemble (5 networks)
python phase1_pretraining/train.py --config config.yaml

# ResNet baseline first (pipeline validation)
python phase1_pretraining/train.py --config config.yaml --model ResNet --epochs 30

# Smoke test (5 epochs, quick sanity check)
python phase1_pretraining/train.py --config config.yaml --smoke-test
```

Training configuration:
- **Optimizer**: AdamW (lr=1e-3, weight_decay=1e-4)
- **Scheduler**: CosineAnnealingLR (T_max=100, eta_min=1e-5)
- **Loss**: Focal loss (gamma=2.0) with per-class alpha weights and label smoothing (0.1)
- **Augmentation**: Mixup (alpha=0.2, probability=0.5)
- **Batch sampling**: BalancedBatchSampler (6 samples per class = 60 per batch)
- **Early stopping**: Patience=15 epochs on val_macro_f1
- **Gradient clipping**: max_norm=1.0
- **Mixed precision**: FP16 on CUDA

### 4. Evaluation (eval.py)

```bash
python phase1_pretraining/eval.py \
  --config config.yaml \
  --checkpoint-dir results/checkpoints
```

Produces:
- 10x10 confusion matrix (CSV + text)
- Per-class precision / recall / F1 table
- Macro and weighted F1
- Exit criteria pass/fail assessment
- Markdown report (`phase1_report.md`)

## Exit Criteria

All must pass before proceeding to Phase 2:

| Criterion | Target | Rationale |
|-----------|--------|-----------|
| Macro F1 | > 0.85 | Overall classifier quality |
| Per-class F1 | > 0.70 (all classes) | No class left behind |
| Normal recall | > 0.95 | False alarms unacceptable in field |
| Avg fault recall | > 0.80 | Must catch real faults |
| No class at 0% | All classes represented | Dataset completeness |

## Loss Function: Focal Loss

Standard cross-entropy struggles with class imbalance. Focal loss down-weights well-classified examples:

```
FL(p_t) = -alpha_t * (1 - p_t)^gamma * log(p_t)
```

Per-class alpha weights (inversely proportional to expected frequency):

| Class | Alpha | Rationale |
|-------|-------|-----------|
| 0 (normal) | 0.35 | Most common - down-weight |
| 1 (cross_thread) | 1.0 | Moderate frequency |
| 2 (galling) | 1.5 | Less common |
| 3 (stripped_thread) | 1.5 | Less common |
| 4 (over_torque) | 2.0 | Uncommon |
| 5 (under_torque) | 1.0 | Moderate |
| 6 (wrong_compound) | 2.5 | Rare - up-weight |
| 7 (misaligned_stab) | 1.5 | Less common |
| 8 (stall) | 2.0 | Uncommon |
| 9 (coupling) | 3.0 | Rarest - highest weight |

Label smoothing (0.1) prevents overconfidence on synthetic data, which helps Phase 2 fine-tuning.

## File Structure

```
phase1_pretraining/
  __init__.py
  config.yaml              # All hyperparameters and class definitions
  docs.md                  # This file
  requirements_phase1.txt  # Python dependencies

  # Core modules
  features.py              # Derived channel computation
  dataset.py               # Data loading, windowing, normalization
  losses.py                # Focal loss + mixup variant
  sampler.py               # Balanced batch sampler
  models.py                # InceptionTime + ResNet architectures

  # Scripts
  train.py                 # Training loop (ensemble or single model)
  eval.py                  # Evaluation and report generation

  # Generated at runtime
  results/
    checkpoints/
      inception_{0-4}_best.pt   # Best ensemble member checkpoints
      resnet_baseline.pt        # ResNet checkpoint
      norm_params.json          # Z-score normalization parameters
    logs/
      inception_{0-4}_training.csv  # Per-epoch metrics
      resnet_training.csv
    config.yaml                 # Copy of config used
    confusion_matrix.csv
    per_class_metrics.csv
    phase1_report.md            # Evaluation report

# Live capture & detection (project root)
capture_live.py              # Modbus TCP data recorder via eWon VPN
detect_live.py               # Real-time InceptionTime fault detection
live_captures/               # Default output dir for captured data
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r phase1_pretraining/requirements_phase1.txt

# 2. Generate training data (takes ~30-60 min for 5000 scenarios)
python generate_dataset.py \
  --count 5000 \
  --output ./data/synthetic_v2 \
  --output-format parquet \
  --class-balance rebalanced \
  --seed 42

# 3. Validate pipeline with ResNet baseline (~30 min on GPU)
cd phase1_pretraining
python train.py --config config.yaml --model ResNet --epochs 30

# 4. Train full InceptionTime ensemble (~2-4 hours on GPU)
python train.py --config config.yaml

# 5. Evaluate on test set
python eval.py --config config.yaml --checkpoint-dir results/checkpoints

# 6. Check results
cat results/phase1_report.md
```

## Inference & Deployment

After training, the model can be deployed for fault detection on captured or live data using `detect_live.py` in the project root.

### Required Artifacts

Two files from training are needed for inference:
- **Model checkpoint** (`inception_*_best.pt` or ensemble checkpoint): Trained weights
- **Normalization params** (`norm_params.json`): Per-channel mean/std from training data

### Offline Detection (Batch)

Process captured CSV files from `capture_live.py`:

```bash
python detect_live.py offline \
    --input ./live_captures \
    --model ./results/checkpoints/model.pt \
    --norm ./results/checkpoints/norm_params.json \
    --window-size 1000 --stride 250
```

Outputs per-window predictions CSV with class probabilities and a detection summary.

### Live Detection (Real-Time)

Poll Modbus registers via eWon VPN and run sliding window inference:

```bash
python detect_live.py live \
    --host 10.0.0.1 \
    --model ./results/checkpoints/model.pt \
    --norm ./results/checkpoints/norm_params.json \
    --hz 10 --inference-every 10
```

The feature pipeline in `detect_live.py` uses the same `features.py` functions as training — raw channels are decoded from Modbus registers, derived channels are computed identically, and normalization uses the saved training-set mean/std. No feature skew between training and inference.

### Alert Thresholds

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--alert-consecutive` | 3 | Consecutive fault windows required before alert fires |
| `--alert-confidence` | 0.6 | Minimum predicted probability to count as fault |

Severity levels:
- **CRIT**: cross_thread, galling, stripped_thread, misaligned_stab, stall
- **WARN**: over_torque, under_torque, wrong_compound
- **OK**: normal_makeup

## Troubleshooting

**ResNet baseline F1 < 0.60**: Data pipeline issue. Check:
- Are all 10 classes present in training data? (`manifest.csv`)
- Are derived channels computed correctly? (Run features.py unit tests)
- Is normalization applied correctly? (Check `norm_params.json` for NaN/inf)

**InceptionTime not converging**: Check:
- Learning rate too high (try 3e-4)
- Gradient explosion (check grad norms in training log)
- Class imbalance (verify BalancedBatchSampler is active)

**Single class dominates predictions**: Check:
- Focal loss alpha weights may need tuning
- Class distribution in training set
- Label smoothing value (increase if overconfident)

**Out of memory**: Reduce batch_size in config.yaml (default 60). Mixed precision (FP16) is enabled automatically on CUDA.
