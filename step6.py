"""
Step 6 — Load Config, Prepare Datasets, Build DataLoaders
===========================================================
Handles manifests from generate_dataset.py (ground_truth_class/ground_truth_label)
AND older manifests (fault_class/scenario_type).
Works on RunPod, Colab, or local GPU.
"""
import os
import numpy as np
import torch
import yaml
from pathlib import Path

# ── Load config ──
print(f"Using config: {CONFIG_FILE}")
with open(CONFIG_FILE) as f:
    config = yaml.safe_load(f)

# Override data dir
config['data']['dataset_dir'] = DRIVE_DATA_DIR

# Override from notebook config
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
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(seed)

# ── Fix manifest column names ──
# generate_dataset.py uses: ground_truth_class, ground_truth_label
# dataset.py expects:       fault_class, scenario_type
import pandas as pd
dataset_dir = Path(DRIVE_DATA_DIR)
manifest_csv = dataset_dir / 'manifest.csv'
manifest_parquet = dataset_dir / 'manifest.parquet'

manifest_path = None
if manifest_parquet.exists():
    manifest_path = manifest_parquet
    manifest_df = pd.read_parquet(manifest_parquet)
elif manifest_csv.exists():
    manifest_path = manifest_csv
    manifest_df = pd.read_csv(manifest_csv)
else:
    manifest_df = None

if manifest_df is not None:
    print(f"Manifest loaded: {len(manifest_df)} scenarios")
    print(f"  Columns: {list(manifest_df.columns)}")
    changed = False

    # Map generate_dataset.py columns -> dataset.py columns
    if 'fault_class' not in manifest_df.columns and 'ground_truth_class' in manifest_df.columns:
        manifest_df['fault_class'] = manifest_df['ground_truth_class']
        print("  Mapped 'ground_truth_class' -> 'fault_class'")
        changed = True

    if 'scenario_type' not in manifest_df.columns and 'ground_truth_label' in manifest_df.columns:
        manifest_df['scenario_type'] = manifest_df['ground_truth_label']
        print("  Mapped 'ground_truth_label' -> 'scenario_type'")
        changed = True

    # If still missing scenario_type, reconstruct from fault_class
    if 'scenario_type' not in manifest_df.columns:
        print("  NOTICE: reconstructing 'scenario_type' from 'fault_class'")
        reverse_map = {
            0: 'normal_casing_ltc', 1: 'cross_thread', 2: 'galling',
            3: 'stripped_thread', 4: 'over_torque', 5: 'under_torque',
            6: 'wrong_compound', 7: 'misaligned_stabbing', 8: 'stall',
        }
        if 'fault_class' in manifest_df.columns:
            manifest_df['scenario_type'] = manifest_df['fault_class'].map(reverse_map).fillna('normal_casing_ltc')
        else:
            manifest_df['scenario_type'] = 'normal_casing_ltc'
        changed = True

    # If still missing fault_class, map from scenario_type
    if 'fault_class' not in manifest_df.columns:
        class_map = config.get('class_map', {})
        manifest_df['fault_class'] = manifest_df['scenario_type'].map(class_map).fillna(0).astype(int)
        changed = True

    # Save fixed manifest so dataset.py picks it up
    if changed and manifest_path:
        if manifest_path.suffix == '.csv':
            manifest_df.to_csv(manifest_path, index=False)
        elif manifest_path.suffix == '.parquet':
            manifest_df.to_parquet(manifest_path, index=False)
        print(f"  Saved fixed manifest to {manifest_path}")

    # Print class distribution
    if 'fault_class' in manifest_df.columns:
        print(f"\n  Class distribution:")
        class_names = config.get('class_names', [])
        for cls in sorted(manifest_df['fault_class'].unique()):
            count = int((manifest_df['fault_class'] == cls).sum())
            name = class_names[cls] if cls < len(class_names) else f'class_{cls}'
            print(f"    {cls} ({name}): {count}")

        # SANITY CHECK: warn if all one class
        unique_classes = manifest_df['fault_class'].nunique()
        if unique_classes <= 1:
            print(f"\n  *** WARNING: Only {unique_classes} class found! ***")
            print(f"  *** Check that the manifest has proper labels ***")
    print()

# ── Prepare datasets ──
from dataset import prepare_datasets

checkpoints_dir = os.path.join(LOCAL_RESULTS_DIR, 'checkpoints')
os.makedirs(checkpoints_dir, exist_ok=True)

channels_mode = config['data'].get('channels_mode', 'all')
print(f"Loading and windowing dataset (channels: {channels_mode})...")
train_dataset, val_dataset, test_dataset, norm_params = prepare_datasets(
    dataset_dir=DRIVE_DATA_DIR,
    class_map=config['class_map'],
    window_size=config['data']['window_size'],
    stride=config['data']['window_stride'],
    split_ratio=tuple(config['data']['split_ratio']),
    split_seed=config['data'].get('split_seed', 42),
    norm_params_path=os.path.join(checkpoints_dir, 'norm_params.json'),
    channels_mode=channels_mode,
)

print(f"\nDataset sizes:")
print(f"  Train : {len(train_dataset):,} windows")
print(f"  Val   : {len(val_dataset):,} windows")
print(f"  Test  : {len(test_dataset):,} windows")

# ── Verify shapes ──
x_sample, y_sample = train_dataset[0]
print(f"\nSample shape: {x_sample.shape}  (window_size x channels)")
print(f"Sample label: {y_sample}")

num_classes = config['model']['c_out']
print(f"Num classes:  {num_classes}")

# Check label distribution
from collections import Counter
train_labels = [int(train_dataset[i][1]) for i in range(len(train_dataset))]
label_dist = Counter(train_labels)
class_names = config.get('class_names', [])
print(f"\nTraining label distribution:")
for cls in sorted(label_dist.keys()):
    name = class_names[cls] if cls < len(class_names) else f'class_{cls}'
    print(f"  {cls} ({name}): {label_dist[cls]:,}")

# FINAL SANITY CHECK
if len(label_dist) <= 1:
    print(f"\n*** CRITICAL: Only {len(label_dist)} class in training data! ***")
    print(f"*** Model will NOT learn fault detection. Check manifest labels. ***")
elif min(label_dist.values()) < 10:
    print(f"\n*** WARNING: Some classes have very few samples (<10). ***")
