#!/usr/bin/env python3
"""
Local Test Runner for Phase 1 Training Pipeline
=================================================
Single-command pipeline: generate data -> train -> evaluate -> diagnose.

Usage:
  python test_local.py                     # Full run (generate 100 + train 50 epochs)
  python test_local.py --skip-gen          # Skip data generation (reuse existing)
  python test_local.py --gen-count 200     # Generate 200 scenarios
  python test_local.py --train-epochs 30   # Train for 30 epochs
  python test_local.py --resnet-only       # Only ResNet sanity check (fastest)
"""
import argparse
import json
import os
import sys
import time
import logging
from pathlib import Path
from collections import Counter

import numpy as np
import yaml

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).parent.resolve()
PHASE1_DIR = ROOT_DIR / 'phase1_pretraining'
CONFIG_PATH = PHASE1_DIR / 'config_local_test.yaml'


def banner(text, char='=', width=60):
    print(f"\n{char * width}")
    print(f"  {text}")
    print(f"{char * width}")


# ═══════════════════════════════════════════════════════════════════
# Step 1: Generate Data
# ═══════════════════════════════════════════════════════════════════

def generate_data(count, data_dir, seed=42):
    """Generate synthetic scenarios using root-level generate_dataset.py."""
    banner(f"GENERATING {count} SCENARIOS -> {data_dir}")

    # Import from root-level modules
    sys.path.insert(0, str(ROOT_DIR))
    from generate_dataset import generate_batch, REBALANCED_DISTRIBUTION, FAULT_CLASS_MAP
    from scenario import ScenarioGenerator, ScenarioType

    class Args:
        pass

    args = Args()
    args.count = count
    args.output = str(data_dir)
    args.seed = seed
    args.output_format = 'csv'
    args.class_balance = 'rebalanced'
    args.output_rate = 100.0
    args.pipe = None
    args.machine_type = None
    args.connection_type = None

    t0 = time.perf_counter()
    generate_batch(args)
    elapsed = time.perf_counter() - t0

    print(f"\nGeneration complete: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"Average: {elapsed/count:.1f}s per scenario")


# ═══════════════════════════════════════════════════════════════════
# Step 2: Data Diagnostics
# ═══════════════════════════════════════════════════════════════════

def diagnose_data(data_dir, config):
    """Analyze generated dataset and print diagnostics."""
    banner("DATA DIAGNOSTICS")

    import pandas as pd

    manifest_path = data_dir / 'manifest.csv'
    if not manifest_path.exists():
        # Try parquet
        manifest_path = data_dir / 'manifest.parquet'

    if manifest_path.suffix == '.parquet':
        import pyarrow.parquet as pq
        df = pq.read_table(manifest_path).to_pandas()
    else:
        df = pd.read_csv(manifest_path)

    n = len(df)
    successful = (df['num_samples'] > 0).sum()

    print(f"Total scenarios:  {n}")
    print(f"Successful:       {successful} ({successful/n:.0%})")
    print(f"Failed:           {n - successful}")

    # Class distribution
    class_names = config['class_names']
    class_counts = Counter(int(c) for c in df['fault_class'])

    print(f"\nFault Class Distribution:")
    print(f"  {'ID':>3s}  {'Name':20s}  {'Count':>5s}  {'Pct':>6s}  {'Scenarios':>10s}")
    print(f"  {'-'*3}  {'-'*20}  {'-'*5}  {'-'*6}  {'-'*10}")

    for cls_id in range(9):
        cnt = class_counts.get(cls_id, 0)
        name = class_names[cls_id] if cls_id < len(class_names) else f'class_{cls_id}'
        # Count actual scenario types for this class
        class_map = config['class_map']
        stypes = [k for k, v in class_map.items() if v == cls_id]
        stype_counts = df[df['fault_class'] == cls_id]['scenario_type'].value_counts()
        stype_str = ', '.join(f"{k}:{v}" for k, v in stype_counts.items())
        print(f"  {cls_id:3d}  {name:20s}  {cnt:5d}  {cnt/n:5.1%}  {stype_str}")

    # Check for missing classes
    missing = set(range(9)) - set(class_counts.keys())
    if missing:
        print(f"\n  WARNING: Missing classes: {sorted(missing)}")
        for m in sorted(missing):
            print(f"    Class {m} ({class_names[m]}): 0 scenarios!")

    # Physical bounds
    valid = df[df['num_samples'] > 0]
    print(f"\nPhysical Bounds (successful scenarios):")
    print(f"  Peak torque:  [{valid['peak_torque_ftlbs'].min():.0f}, {valid['peak_torque_ftlbs'].max():.0f}] ft-lbs")
    print(f"  Peak RPM:     [{valid['peak_rpm'].min():.0f}, {valid['peak_rpm'].max():.0f}]")
    print(f"  Duration:     [{valid['duration_s'].min():.1f}, {valid['duration_s'].max():.1f}] s")
    print(f"  Samples/scen: [{valid['num_samples'].min()}, {valid['num_samples'].max()}]")

    return df


# ═══════════════════════════════════════════════════════════════════
# Step 3: Train
# ═══════════════════════════════════════════════════════════════════

def train_and_evaluate(config, data_dir, results_dir, train_epochs=50,
                       resnet_only=False):
    """Train model(s) and evaluate on test set."""
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader

    # Add phase1 dir to path for imports
    sys.path.insert(0, str(PHASE1_DIR))
    from dataset import prepare_datasets
    from models import InceptionTimeNetwork, ResNetBaseline, create_model, count_parameters
    from train import train_single_model, evaluate
    from losses import FocalLoss, MixupFocalLoss
    from sampler import BalancedBatchSampler

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    banner(f"TRAINING (device={device})")

    if device.type == 'cuda':
        gpu_name = torch.cuda.get_device_name(0)
        vram = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"  GPU: {gpu_name} ({vram:.1f} GB)")
        torch.backends.cudnn.benchmark = True

    # Override config for local
    config['data']['dataset_dir'] = str(data_dir)
    config['training']['max_epochs'] = train_epochs

    # Seed
    seed = config['experiment']['seed']
    torch.manual_seed(seed)
    np.random.seed(seed)

    # Prepare datasets
    checkpoints_dir = results_dir / 'checkpoints'
    logs_dir = results_dir / 'logs'
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    fault_threshold = config['data'].get('fault_threshold', 0.10)
    print(f"\nPreparing datasets (fault_threshold={fault_threshold})...")

    train_dataset, val_dataset, test_dataset, norm_params = prepare_datasets(
        dataset_dir=str(data_dir),
        class_map=config['class_map'],
        window_size=config['data']['window_size'],
        stride=config['data']['window_stride'],
        split_ratio=tuple(config['data']['split_ratio']),
        split_seed=config['data'].get('split_seed', 42),
        norm_params_path=str(checkpoints_dir / 'norm_params.json'),
        fault_threshold=fault_threshold,
    )

    print(f"  Train: {len(train_dataset)} windows")
    print(f"  Val:   {len(val_dataset)} windows")
    print(f"  Test:  {len(test_dataset)} windows")

    # Check class distribution in train set
    train_labels = train_dataset.labels
    label_counts = Counter(int(l) for l in train_labels)
    print(f"\n  Train class distribution:")
    for cls_id in range(9):
        cnt = label_counts.get(cls_id, 0)
        name = config['class_names'][cls_id]
        print(f"    {cls_id} {name:20s}: {cnt:5d} windows")

    c_in = config['model']['c_in']
    c_out = config['model']['c_out']
    nf = config['model']['nf']
    dropout = config['model'].get('dropout', 0.0)

    # ── ResNet Sanity Check ───────────────────────────────────────
    banner("ResNet Sanity Check (10 epochs)", char='-')

    sanity_config = dict(config)
    sanity_config['training'] = dict(config['training'])
    sanity_config['training']['max_epochs'] = 10
    sanity_config['training']['early_stop_patience'] = 8
    sanity_config['training']['warmup_epochs'] = 2

    resnet = ResNetBaseline(c_in, c_out, dropout=dropout).to(device)
    print(f"  ResNet params: {count_parameters(resnet):,}")

    resnet_f1 = train_single_model(
        resnet, train_dataset, val_dataset, sanity_config, device,
        checkpoint_path=str(checkpoints_dir / 'resnet_sanity.pt'),
        log_path=str(logs_dir / 'resnet_sanity.csv'),
        model_name='ResNet',
    )
    print(f"\n  ResNet sanity F1: {resnet_f1:.4f}")
    if resnet_f1 < 0.20:
        print("  CRITICAL: ResNet < 0.20 — likely data pipeline issue!")
    elif resnet_f1 < 0.35:
        print("  WARNING: ResNet < 0.35 — marginal, but continue to InceptionTime")
    else:
        print("  OK: Pipeline validated")

    if resnet_only:
        return resnet_f1, None

    # ── InceptionTime ─────────────────────────────────────────────
    banner(f"InceptionTime Training ({train_epochs} epochs)", char='-')

    torch.manual_seed(0)
    np.random.seed(0)
    model = InceptionTimeNetwork(c_in, c_out, nf, dropout=dropout).to(device)
    print(f"  InceptionTime params: {count_parameters(model):,}")

    inception_f1 = train_single_model(
        model, train_dataset, val_dataset, config, device,
        checkpoint_path=str(checkpoints_dir / 'inception_0_best.pt'),
        log_path=str(logs_dir / 'inception_0_training.csv'),
        model_name='Inception-0',
    )
    print(f"\n  InceptionTime best val F1: {inception_f1:.4f}")

    # ── Evaluate on Test Set ──────────────────────────────────────
    banner("TEST SET EVALUATION")

    # Load best checkpoint
    ckpt = torch.load(
        str(checkpoints_dir / 'inception_0_best.pt'),
        map_location=device, weights_only=False,
    )
    model.load_state_dict(ckpt['model_state_dict'])
    model.to(device).eval()

    test_loader = DataLoader(test_dataset, batch_size=64, shuffle=False)
    num_classes = config['model']['c_out']
    class_names = config['class_names']

    # Get predictions
    all_preds, all_labels = [], []
    with torch.no_grad():
        for X_batch, y_batch in test_loader:
            X_batch = X_batch.to(device)
            logits = model(X_batch)
            preds = logits.argmax(dim=-1).cpu().numpy()
            all_preds.extend(preds)
            all_labels.extend(y_batch.numpy())

    all_preds = np.array(all_preds)
    all_labels = np.array(all_labels)

    # Per-class metrics
    print(f"\n{'Class':20s} {'Prec':>6s} {'Recall':>6s} {'F1':>6s} {'Support':>7s}")
    print(f"{'-'*20} {'-'*6} {'-'*6} {'-'*6} {'-'*7}")

    per_class_f1 = []
    for cls in range(num_classes):
        tp = ((all_preds == cls) & (all_labels == cls)).sum()
        fp = ((all_preds == cls) & (all_labels != cls)).sum()
        fn = ((all_preds != cls) & (all_labels == cls)).sum()
        support = (all_labels == cls).sum()
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-8)
        per_class_f1.append(f1)
        name = class_names[cls] if cls < len(class_names) else f'class_{cls}'
        print(f"{name:20s} {prec:6.3f} {rec:6.3f} {f1:6.3f} {support:7d}")

    macro_f1 = np.mean(per_class_f1)
    print(f"\n{'Macro F1':20s} {'':6s} {'':6s} {macro_f1:6.3f} {len(all_labels):7d}")

    # Confusion matrix (compact)
    print(f"\nConfusion Matrix:")
    header = ''.join(f'{class_names[i][:8]:>9s}' for i in range(num_classes))
    print(f"{'Actual\\Pred':>16s}{header}")

    for i in range(num_classes):
        mask_i = all_labels == i
        n_i = mask_i.sum()
        if n_i == 0:
            row = ''.join(f'{"---":>9s}' for _ in range(num_classes))
        else:
            row = ''
            for j in range(num_classes):
                cnt = ((all_preds == j) & mask_i).sum()
                pct = cnt / n_i * 100
                if cnt > 0:
                    row += f'{cnt:4d}({pct:3.0f}%)'
                else:
                    row += f'{"0":>9s}'

        name = class_names[i][:16] if i < len(class_names) else f'cls{i}'
        print(f"{name:>16s}{row}")

    # Key confusion pairs
    print(f"\nTop Confusion Pairs:")
    confusions = []
    for i in range(num_classes):
        mask_i = all_labels == i
        n_i = mask_i.sum()
        if n_i == 0:
            continue
        for j in range(num_classes):
            if i == j:
                continue
            cnt = ((all_preds == j) & mask_i).sum()
            if cnt > 0:
                confusions.append((cnt, cnt/n_i, i, j))
    confusions.sort(reverse=True)
    for cnt, pct, i, j in confusions[:10]:
        name_i = class_names[i] if i < len(class_names) else f'cls{i}'
        name_j = class_names[j] if j < len(class_names) else f'cls{j}'
        print(f"  {name_i:20s} -> {name_j:20s}: {cnt:4d} ({pct:.0%})")

    # Exit criteria
    banner("EXIT CRITERIA (adapted for small dataset)")
    normal_recall = per_class_f1[0]  # Approximation
    normal_mask = all_labels == 0
    if normal_mask.sum() > 0:
        normal_recall = ((all_preds == 0) & normal_mask).sum() / normal_mask.sum()
    fault_recalls = []
    for cls in range(1, num_classes):
        mask = all_labels == cls
        if mask.sum() > 0:
            fault_recalls.append(((all_preds == cls) & mask).sum() / mask.sum())

    avg_fault_recall = np.mean(fault_recalls) if fault_recalls else 0
    min_f1 = min(per_class_f1)
    zero_classes = sum(1 for f in per_class_f1 if f < 0.01)

    criteria = [
        ("Macro F1 > 0.85", macro_f1, 0.85),
        ("Min per-class F1 > 0.70", min_f1, 0.70),
        ("Normal recall > 0.95", normal_recall, 0.95),
        ("Avg fault recall > 0.80", avg_fault_recall, 0.80),
    ]

    for name, val, target in criteria:
        status = "PASS" if val >= target else "FAIL"
        gap = val - target
        print(f"  [{status}] {name}: {val:.4f} (gap: {gap:+.4f})")

    if zero_classes > 0:
        print(f"  [FAIL] {zero_classes} classes with F1 = 0")

    print(f"\n  Overall: Macro F1 = {macro_f1:.4f}")
    if macro_f1 >= 0.85:
        print("  TARGET MET!")
    else:
        print(f"  Gap to target: {0.85 - macro_f1:.4f}")

    return resnet_f1, inception_f1


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Local test pipeline')
    parser.add_argument('--gen-count', type=int, default=100,
                        help='Number of scenarios to generate (default: 100)')
    parser.add_argument('--train-epochs', type=int, default=50,
                        help='Training epochs (default: 50)')
    parser.add_argument('--skip-gen', action='store_true',
                        help='Skip data generation (reuse existing)')
    parser.add_argument('--resnet-only', action='store_true',
                        help='Only run ResNet sanity check')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()

    # Load config
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    data_dir = ROOT_DIR / 'data' / 'local_test'
    results_dir = PHASE1_DIR / 'results_local'

    total_start = time.perf_counter()

    # Step 1: Generate data
    if not args.skip_gen:
        # Clean existing data to regenerate fresh
        if data_dir.exists():
            import shutil
            shutil.rmtree(data_dir)
            print(f"Cleaned existing data at {data_dir}")
        generate_data(args.gen_count, data_dir, seed=args.seed)
    else:
        print(f"\nSkipping generation (--skip-gen). Using: {data_dir}")

    if not (data_dir / 'manifest.csv').exists() and not (data_dir / 'manifest.parquet').exists():
        print("ERROR: No manifest found. Run without --skip-gen first.")
        sys.exit(1)

    # Step 2: Data diagnostics
    df = diagnose_data(data_dir, config)

    # Step 3: Train + evaluate
    resnet_f1, inception_f1 = train_and_evaluate(
        config, data_dir, results_dir,
        train_epochs=args.train_epochs,
        resnet_only=args.resnet_only,
    )

    total_time = time.perf_counter() - total_start
    banner("SUMMARY")
    print(f"  Total time:       {total_time:.0f}s ({total_time/60:.1f} min)")
    print(f"  ResNet sanity F1: {resnet_f1:.4f}")
    if inception_f1 is not None:
        print(f"  InceptionTime F1: {inception_f1:.4f}")
    print(f"  Results saved to: {results_dir}")


if __name__ == '__main__':
    main()
