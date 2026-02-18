#!/usr/bin/env python3
"""
Generate Google Colab notebooks for TopDrive AI.

Creates two notebooks:
  01_generate_synthetic_data.ipynb  — generates & saves dataset to Drive
  02_train_phase1.ipynb             — loads dataset from Drive, trains model
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent   # plc_simulation/
PHASE1 = ROOT / "phase1_pretraining"
OUT = Path(__file__).parent           # colab/

NOTEBOOK_META = {
    "kernelspec": {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3"
    },
    "language_info": {
        "name": "python",
        "version": "3.10.12"
    },
    "accelerator": "GPU",
    "colab": {
        "provenance": [],
        "gpuType": "T4"
    }
}


def code_cell(source: str):
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source
    }


def md_cell(source: str):
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source
    }


def writefile_cell(filename: str, path: Path):
    content = path.read_text(encoding="utf-8")
    return code_cell(f"%%writefile {filename}\n" + content)


def notebook(cells: list) -> dict:
    return {
        "cells": cells,
        "metadata": NOTEBOOK_META,
        "nbformat": 4,
        "nbformat_minor": 4
    }


# ══════════════════════════════════════════════════════════════════════════
# NOTEBOOK 1: Synthetic Data Generation
# ══════════════════════════════════════════════════════════════════════════

gen_cells = []

gen_cells.append(md_cell("""\
# TopDrive AI — Synthetic Dataset Generation

Generates physics-based pipe threading scenarios for ML training.
**All code is embedded below — no file uploads required.**

Data is saved to Google Drive so it persists between sessions and can be
loaded by the training notebook.

## Workflow
1. Run **Setup** cells (install deps, mount Drive, write modules)
2. Configure generation parameters in the **Configuration** cell
3. Run **Generate Dataset** — takes ~1 hr for 5000 scenarios on Colab CPU
4. Data is auto-copied to Drive when done
"""))

# ── Step 1: Dependencies ────────────────────────────────────────────────
gen_cells.append(md_cell("## Step 1 — Install Dependencies"))

gen_cells.append(code_cell("""\
# Only pyarrow + pandas needed (numpy is pre-installed on Colab)
!pip install pyarrow pandas -q
print("Dependencies ready.")
"""))

# ── Step 2: Mount Drive ─────────────────────────────────────────────────
gen_cells.append(md_cell("## Step 2 — Mount Google Drive"))

gen_cells.append(code_cell("""\
from google.colab import drive
drive.mount('/content/drive')
print("Drive mounted at /content/drive")
"""))

# ── Step 3: Configuration ───────────────────────────────────────────────
gen_cells.append(md_cell("""\
## Step 3 — Configuration

Edit these variables before running the generation cells.
"""))

gen_cells.append(code_cell("""\
import os

# ── Generation parameters ─────────────────────────────────────────────────
NUM_SCENARIOS   = 5000       # Total scenarios (5000 = Phase 1 full dataset)
SEED            = 42         # Reproducibility seed
CLASS_BALANCE   = 'rebalanced'   # 'rebalanced' (50/50) or 'default' (field dist)
OUTPUT_FORMAT   = 'parquet'       # 'parquet' (faster/smaller) or 'csv'
OUTPUT_RATE_HZ  = 100.0          # Sensor data output rate (Hz)

# ── Paths ─────────────────────────────────────────────────────────────────
LOCAL_OUTPUT_DIR = '/content/synthetic_v2'
DRIVE_OUTPUT_DIR = '/content/drive/MyDrive/topdrive_ai/synthetic_v2'

os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)
os.makedirs(DRIVE_OUTPUT_DIR, exist_ok=True)
print(f"Local output : {LOCAL_OUTPUT_DIR}")
print(f"Drive output : {DRIVE_OUTPUT_DIR}")
print(f"Scenarios    : {NUM_SCENARIOS}")
print(f"Class balance: {CLASS_BALANCE}")
print(f"Format       : {OUTPUT_FORMAT}")
"""))

# ── Step 4: Write modules ───────────────────────────────────────────────
gen_cells.append(md_cell("""\
## Step 4 — Write Simulation Modules

Each cell below writes one Python module to the Colab VM disk.
Run them all once per session (they reset when the runtime restarts).
"""))

sim_modules = [
    ("config.py",         ROOT / "config.py"),
    ("physics_engine.py", ROOT / "physics_engine.py"),
    ("sensor_models.py",  ROOT / "sensor_models.py"),
    ("scenario.py",       ROOT / "scenario.py"),
    ("modbus_server.py",  ROOT / "modbus_server.py"),
    ("runner.py",         ROOT / "runner.py"),
    ("generate_dataset.py", ROOT / "generate_dataset.py"),
]

for fname, fpath in sim_modules:
    gen_cells.append(md_cell(f"### `{fname}`"))
    gen_cells.append(writefile_cell(fname, fpath))

# Verify all written
gen_cells.append(code_cell("""\
import os
modules = ['config.py','physics_engine.py','sensor_models.py',
           'scenario.py','modbus_server.py','runner.py','generate_dataset.py']
for m in modules:
    size = os.path.getsize(m)
    print(f"  {m:<30s}  {size:>8,} bytes  OK")
print("\\nAll modules written successfully.")
"""))

# ── Step 5: Generate ────────────────────────────────────────────────────
gen_cells.append(md_cell("""\
## Step 5 — Generate Dataset

This cell generates all scenarios and saves them to the local VM disk.
Progress is logged for each scenario.

**Tip:** Colab sessions time out after ~12 hours.
For large datasets, consider splitting into multiple runs with `--seed` offsets.
"""))

gen_cells.append(code_cell("""\
import sys, types, logging

# Set up logging so progress is visible in the notebook
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    force=True,
)

# Import the generation function directly (avoids argparse)
from generate_dataset import (
    generate_batch, _save_parquet, _compute_stats,
    REBALANCED_DISTRIBUTION, FAULT_CLASS_MAP,
)
from scenario import ScenarioGenerator, ScenarioType
from runner import SimulationRunner
from config import MachineType

import time, csv, json
from pathlib import Path
from collections import Counter

# Build a simple args-like namespace
class Args:
    count          = NUM_SCENARIOS
    output         = LOCAL_OUTPUT_DIR
    seed           = SEED
    output_format  = OUTPUT_FORMAT
    class_balance  = CLASS_BALANCE
    output_rate    = OUTPUT_RATE_HZ
    pipe           = None
    machine_type   = None
    connection_type = None

print(f"Starting generation: {NUM_SCENARIOS} scenarios")
print(f"Output: {LOCAL_OUTPUT_DIR}")
print("-" * 60)

generate_batch(Args())

print("\\nGeneration complete!")
"""))

# ── Step 6: Copy to Drive ───────────────────────────────────────────────
gen_cells.append(md_cell("""\
## Step 6 — Copy to Google Drive

Copies the generated dataset to Drive for persistent storage.
This step is safe to re-run if interrupted.
"""))

gen_cells.append(code_cell("""\
import shutil, os

print(f"Syncing {LOCAL_OUTPUT_DIR} -> {DRIVE_OUTPUT_DIR}")
shutil.copytree(LOCAL_OUTPUT_DIR, DRIVE_OUTPUT_DIR, dirs_exist_ok=True)
print("Sync complete.\\n")

# Summary
for subdir in ['sensor', 'truth', 'events']:
    path = os.path.join(DRIVE_OUTPUT_DIR, subdir)
    if os.path.exists(path):
        n = len(os.listdir(path))
        print(f"  {subdir}/  {n} files")

stats_path = os.path.join(DRIVE_OUTPUT_DIR, 'stats.txt')
if os.path.exists(stats_path):
    print()
    with open(stats_path) as f:
        print(f.read())
"""))

# Write notebook 1
nb1 = notebook(gen_cells)
out1 = OUT / "01_generate_synthetic_data.ipynb"
with open(out1, "w", encoding="utf-8") as f:
    json.dump(nb1, f, indent=1, ensure_ascii=False)
print(f"Written: {out1.name}  ({out1.stat().st_size:,} bytes)")


# ══════════════════════════════════════════════════════════════════════════
# NOTEBOOK 2: Phase 1 Training
# ══════════════════════════════════════════════════════════════════════════

train_cells = []

train_cells.append(md_cell("""\
# TopDrive AI — Phase 1 Training (InceptionTime Ensemble)

Trains the InceptionTime ensemble classifier on the synthetic dataset
generated by `01_generate_synthetic_data.ipynb`.

**All ML code is embedded below — no uploads needed.**
Data is loaded directly from Google Drive.

## Workflow
1. Enable GPU: Runtime → Change runtime type → T4 GPU
2. Run **Setup** cells (install deps, mount Drive, write modules)
3. Set **DRIVE_DATA_DIR** to match where you saved the data
4. Run **Train** — checkpoints auto-saved to Drive
5. Run **Evaluate** to see final metrics

## Exit Criteria (Phase 1)
| Metric | Target |
|--------|--------|
| Macro F1 | > 0.85 |
| All per-class F1 | > 0.70 |
| Normal class recall | > 0.95 |
| Avg fault recall | > 0.80 |
"""))

# ── GPU check ───────────────────────────────────────────────────────────
train_cells.append(md_cell("## Step 1 — Check GPU"))

train_cells.append(code_cell("""\
import torch

if torch.cuda.is_available():
    gpu = torch.cuda.get_device_name(0)
    mem = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f"GPU: {gpu}  ({mem:.1f} GB VRAM)")
else:
    print("WARNING: No GPU detected. Training will be slow on CPU.")
    print("Go to Runtime -> Change runtime type -> T4 GPU")
"""))

# ── Install deps ─────────────────────────────────────────────────────────
train_cells.append(md_cell("## Step 2 — Install Dependencies"))

train_cells.append(code_cell("""\
!pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118 -q
!pip install pandas pyarrow pyyaml tqdm matplotlib seaborn -q
print("All dependencies installed.")
"""))

# ── Mount Drive ──────────────────────────────────────────────────────────
train_cells.append(md_cell("## Step 3 — Mount Google Drive"))

train_cells.append(code_cell("""\
from google.colab import drive
drive.mount('/content/drive')
print("Drive mounted.")
"""))

# ── Configuration ────────────────────────────────────────────────────────
train_cells.append(md_cell("""\
## Step 4 — Configuration

Set `DRIVE_DATA_DIR` to where the dataset was saved by the generation notebook.
"""))

train_cells.append(code_cell("""\
import os, torch

# ── Paths ──────────────────────────────────────────────────────────────────
DRIVE_DATA_DIR    = '/content/drive/MyDrive/topdrive_ai/synthetic_v2'
DRIVE_RESULTS_DIR = '/content/drive/MyDrive/topdrive_ai/results'
LOCAL_RESULTS_DIR = '/content/results'

# ── Training settings ──────────────────────────────────────────────────────
ARCHITECTURE  = 'InceptionTime'   # 'InceptionTime' or 'ResNet'
MAX_EPOCHS    = 100
BATCH_SIZE    = 64
LEARNING_RATE = 0.001
ENSEMBLE_SIZE = 5                  # Number of InceptionTime members
SMOKE_TEST    = False              # True = 5 epochs only (fast debug run)

# ── Device ─────────────────────────────────────────────────────────────────
if torch.cuda.is_available():
    DEVICE = 'cuda'
elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
    DEVICE = 'mps'
else:
    DEVICE = 'cpu'

os.makedirs(LOCAL_RESULTS_DIR, exist_ok=True)
os.makedirs(DRIVE_RESULTS_DIR, exist_ok=True)

print(f"Data dir     : {DRIVE_DATA_DIR}")
print(f"Results dir  : {LOCAL_RESULTS_DIR}")
print(f"Architecture : {ARCHITECTURE}")
print(f"Device       : {DEVICE}")
print(f"Smoke test   : {SMOKE_TEST}")
"""))

# ── Write ML modules ────────────────────────────────────────────────────
train_cells.append(md_cell("""\
## Step 5 — Write ML Modules

Each cell writes one Python module to the Colab VM disk.
"""))

ml_modules = [
    ("features.py", PHASE1 / "features.py"),
    ("dataset.py",  PHASE1 / "dataset.py"),
    ("models.py",   PHASE1 / "models.py"),
    ("losses.py",   PHASE1 / "losses.py"),
    ("sampler.py",  PHASE1 / "sampler.py"),
    ("eval.py",     PHASE1 / "eval.py"),
    ("train.py",    PHASE1 / "train.py"),
]

for fname, fpath in ml_modules:
    train_cells.append(md_cell(f"### `{fname}`"))
    train_cells.append(writefile_cell(fname, fpath))

# Write config.yaml
config_yaml_content = (PHASE1 / "config.yaml").read_text(encoding="utf-8")
train_cells.append(md_cell("### `config.yaml`"))
train_cells.append(code_cell(f"%%writefile config.yaml\n" + config_yaml_content))

# Verify
train_cells.append(code_cell("""\
import os
modules = ['features.py','dataset.py','models.py','losses.py',
           'sampler.py','eval.py','train.py','config.yaml']
for m in modules:
    size = os.path.getsize(m)
    print(f"  {m:<20s}  {size:>7,} bytes  OK")
print("\\nAll modules written.")
"""))

# ── Prepare datasets ─────────────────────────────────────────────────────
train_cells.append(md_cell("""\
## Step 6 — Prepare Datasets

Loads the manifest, splits scenarios into train/val/test, applies sliding windows
and Z-score normalization. Normalization parameters are computed from the training
split only (no data leakage).

This step can take a few minutes depending on dataset size.
"""))

train_cells.append(code_cell("""\
import numpy as np
import torch
import yaml
from pathlib import Path

# Load config
with open('config.yaml') as f:
    config = yaml.safe_load(f)

# Override data dir to point at Drive
config['data']['dataset_dir'] = DRIVE_DATA_DIR

# Overrides from notebook config
config['model']['architecture'] = ARCHITECTURE
config['training']['max_epochs'] = MAX_EPOCHS
config['training']['batch_size'] = BATCH_SIZE
config['training']['lr'] = LEARNING_RATE
config['model']['ensemble_size'] = ENSEMBLE_SIZE
if SMOKE_TEST:
    config['training']['max_epochs'] = 5
    config['training']['early_stop_patience'] = 3

# Seed
seed = config['experiment']['seed']
torch.manual_seed(seed)
np.random.seed(seed)

# Prepare datasets
from dataset import prepare_datasets

checkpoints_dir = os.path.join(LOCAL_RESULTS_DIR, 'checkpoints')
os.makedirs(checkpoints_dir, exist_ok=True)

print("Loading and windowing dataset...")
train_dataset, val_dataset, test_dataset, norm_params = prepare_datasets(
    dataset_dir=DRIVE_DATA_DIR,
    class_map=config['class_map'],
    window_size=config['data']['window_size'],
    stride=config['data']['window_stride'],
    split_ratio=tuple(config['data']['split_ratio']),
    split_seed=config['data'].get('split_seed', 42),
    norm_params_path=os.path.join(checkpoints_dir, 'norm_params.json'),
)

print(f"\\nDataset sizes:")
print(f"  Train : {len(train_dataset):,} windows")
print(f"  Val   : {len(val_dataset):,} windows")
print(f"  Test  : {len(test_dataset):,} windows")
"""))

# ── Train ────────────────────────────────────────────────────────────────
train_cells.append(md_cell("""\
## Step 7 — Train

Trains the selected architecture.

- **ResNet**: ~30 min on T4 GPU. Good baseline (target >80% macro F1).
- **InceptionTime ensemble**: ~3–6 hrs on T4 GPU (5 members). Target >85% macro F1.

Checkpoints are saved after each epoch improvement.
Training auto-stops if validation F1 does not improve for 15 epochs.
"""))

train_cells.append(code_cell("""\
import torch
import torch.nn as nn
from pathlib import Path

from models import create_model, InceptionTimeNetwork, count_parameters
from train import train_single_model
from losses import FocalLoss, MixupFocalLoss

device = torch.device(DEVICE)
print(f"Training on: {device}")

arch = config['model']['architecture']
c_in = config['model']['c_in']
c_out = config['model']['c_out']
nf   = config['model']['nf']

logs_dir = os.path.join(LOCAL_RESULTS_DIR, 'logs')
os.makedirs(logs_dir, exist_ok=True)

if arch == 'ResNet':
    print("Training ResNet baseline...")
    model = create_model('ResNet', c_in, c_out).to(device)
    print(f"Parameters: {count_parameters(model):,}")
    best_f1 = train_single_model(
        model, train_dataset, val_dataset, config, device,
        checkpoint_path=os.path.join(checkpoints_dir, 'resnet_baseline.pt'),
        log_path=os.path.join(logs_dir, 'resnet_training.csv'),
        model_name='ResNet',
    )
    print(f"\\nResNet best val F1: {best_f1:.4f}")

else:
    ensemble_size = config['model'].get('ensemble_size', 5)
    ensemble_seeds = config['model'].get('ensemble_seeds', list(range(ensemble_size)))
    f1_scores = []

    for i, member_seed in enumerate(ensemble_seeds):
        print(f"\\n{'='*60}")
        print(f"Training InceptionTime member {i+1}/{ensemble_size}  (seed={member_seed})")

        torch.manual_seed(member_seed)
        model = InceptionTimeNetwork(c_in, c_out, nf).to(device)
        print(f"Parameters: {count_parameters(model):,}")

        best_f1 = train_single_model(
            model, train_dataset, val_dataset, config, device,
            checkpoint_path=os.path.join(checkpoints_dir, f'inception_{i}_best.pt'),
            log_path=os.path.join(logs_dir, f'inception_{i}_training.csv'),
            model_name=f'Inception-{i}',
        )
        f1_scores.append(best_f1)
        print(f"  Member {i} best F1: {best_f1:.4f}")

    print(f"\\n{'='*60}")
    print(f"Ensemble training complete.")
    print(f"Individual F1s : {[f'{f:.4f}' for f in f1_scores]}")
    print(f"Mean F1        : {np.mean(f1_scores):.4f}")
"""))

# ── Evaluate ─────────────────────────────────────────────────────────────
train_cells.append(md_cell("""\
## Step 8 — Evaluate on Test Set

Loads the best checkpoints and runs evaluation on the held-out test set.
"""))

train_cells.append(code_cell("""\
import torch
import torch.nn.functional as F
import numpy as np
from torch.utils.data import DataLoader

from models import InceptionTimeNetwork, ResNetBaseline
from eval import (
    load_ensemble, ensemble_predict, compute_confusion_matrix,
    compute_per_class_metrics, assess_exit_criteria,
    save_confusion_matrix_csv, save_confusion_matrix_text, generate_report,
)

device = torch.device(DEVICE)
arch = config['model']['architecture']
c_in = config['model']['c_in']
c_out = config['model']['c_out']
nf   = config['model']['nf']
class_names = config['class_names']
num_classes = len(class_names)

test_loader = DataLoader(test_dataset, batch_size=128, shuffle=False)

if arch == 'ResNet':
    from pathlib import Path
    import torch
    ckpt_path = os.path.join(checkpoints_dir, 'resnet_baseline.pt')
    checkpoint = torch.load(ckpt_path, map_location=device, weights_only=False)
    model = ResNetBaseline(c_in, c_out)
    model.load_state_dict(checkpoint['model_state_dict'])
    model.to(device).eval()
    models = [model]
    model_label = "ResNet baseline"
else:
    ensemble_size = config['model'].get('ensemble_size', 5)
    models = load_ensemble(Path(checkpoints_dir), device, c_in, c_out, nf, ensemble_size)
    model_label = f"InceptionTime ensemble ({len(models)} members)"

print(f"Loaded {len(models)} model(s): {model_label}")

# Inference
preds, labels, probs = ensemble_predict(models, test_loader, device)

# Metrics
cm = compute_confusion_matrix(preds, labels, num_classes)
per_class = compute_per_class_metrics(preds, labels, num_classes)
macro_f1 = np.mean([m['f1'] for m in per_class])
criteria = assess_exit_criteria(per_class, class_names)

# Print
print(f"\\n{'='*60}")
print(f"Macro F1: {macro_f1:.4f}")
print(f"\\nPer-class metrics:")
print(f"{'Class':>20s} {'P':>8s} {'R':>8s} {'F1':>8s} {'N':>8s}")
for m, name in zip(per_class, class_names):
    print(f"{name:>20s} {m['precision']:>8.4f} {m['recall']:>8.4f} "
          f"{m['f1']:>8.4f} {m['support']:>8d}")

print(f"\\nConfusion Matrix:")
print(save_confusion_matrix_text(cm, class_names))

print(f"\\nExit Criteria:")
for c in criteria[:5]:
    marker = '[PASS]' if c['status'] == 'PASS' else '[FAIL]'
    print(f"  {marker} {c['criterion']}: {c['value']}  (target: {c['target']})")

# Save report
from pathlib import Path
generate_report(
    Path(LOCAL_RESULTS_DIR), class_names, macro_f1, per_class, criteria,
    cm, len(models), model_label=model_label
)
save_confusion_matrix_csv(cm, class_names,
    os.path.join(LOCAL_RESULTS_DIR, 'confusion_matrix.csv'))
print(f"\\nReport saved to {LOCAL_RESULTS_DIR}/phase1_report.md")
"""))

# ── Copy results to Drive ─────────────────────────────────────────────────
train_cells.append(md_cell("""\
## Step 9 — Save Results to Google Drive

Copies checkpoints, logs, and evaluation reports to Drive.
"""))

train_cells.append(code_cell("""\
import shutil, os

print(f"Copying results to Drive...")
shutil.copytree(LOCAL_RESULTS_DIR, DRIVE_RESULTS_DIR, dirs_exist_ok=True)
print(f"Done! Results saved to: {DRIVE_RESULTS_DIR}")

# List checkpoints
ckpt_drive = os.path.join(DRIVE_RESULTS_DIR, 'checkpoints')
if os.path.exists(ckpt_drive):
    print("\\nCheckpoints:")
    for f in sorted(os.listdir(ckpt_drive)):
        path = os.path.join(ckpt_drive, f)
        size_mb = os.path.getsize(path) / 1e6
        print(f"  {f}  ({size_mb:.1f} MB)")
"""))

# ── Plot training curves ──────────────────────────────────────────────────
train_cells.append(md_cell("## Step 10 — Plot Training Curves (Optional)"))

train_cells.append(code_cell("""\
import pandas as pd
import matplotlib.pyplot as plt
import os

logs_dir = os.path.join(LOCAL_RESULTS_DIR, 'logs')
log_files = sorted([f for f in os.listdir(logs_dir) if f.endswith('.csv')])

if not log_files:
    print("No training logs found yet.")
else:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    colors = plt.cm.tab10.colors

    for i, logfile in enumerate(log_files):
        df = pd.read_csv(os.path.join(logs_dir, logfile))
        label = logfile.replace('_training.csv', '')
        color = colors[i % len(colors)]
        axes[0].plot(df['epoch'], df['train_loss'],
                     label=f'{label} loss', color=color, alpha=0.7)
        axes[1].plot(df['epoch'], df['val_macro_f1'],
                     label=f'{label} F1', color=color)

    axes[0].set_title('Training Loss')
    axes[0].set_xlabel('Epoch')
    axes[0].set_ylabel('Focal Loss')
    axes[0].legend(fontsize=8)
    axes[0].grid(True, alpha=0.3)

    axes[1].set_title('Validation Macro F1')
    axes[1].set_xlabel('Epoch')
    axes[1].set_ylabel('Macro F1')
    axes[1].axhline(y=0.85, color='green', linestyle='--', label='Target 0.85')
    axes[1].legend(fontsize=8)
    axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(LOCAL_RESULTS_DIR, 'training_curves.png'), dpi=150)
    plt.show()
    print("Training curves saved.")
"""))

# Write notebook 2
nb2 = notebook(train_cells)
out2 = OUT / "02_train_phase1.ipynb"
with open(out2, "w", encoding="utf-8") as f:
    json.dump(nb2, f, indent=1, ensure_ascii=False)
print(f"Written: {out2.name}  ({out2.stat().st_size:,} bytes)")

print("\nDone. Upload the colab/ folder to Google Drive or open directly in Colab.")
