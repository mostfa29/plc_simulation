"""
MiniRocket Diagnostic Baseline
================================
Runs MiniRocket (random convolutional kernels + Ridge classifier) on the
same dataset as InceptionTime. Takes minutes, not hours.

Purpose: If MiniRocket also gets F1 ~ 0.17, the problem is DATA/FEATURES.
         If MiniRocket gets F1 > 0.50, the problem was TRAINING STRATEGY.

Usage:
  cd phase1_pretraining
  pip install aeon scikit-learn
  python diagnostics/minirocket_baseline.py --config config_triage.yaml
  python diagnostics/minirocket_baseline.py --config config.yaml  # compare with old
"""
import argparse
import sys
import time
import logging
from pathlib import Path

import numpy as np
import yaml

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

# Add parent to path so we can import dataset
sys.path.insert(0, str(Path(__file__).parent.parent))
from dataset import prepare_datasets


def run_minirocket(config_path: str):
    """Run MiniRocket baseline on the configured dataset."""
    try:
        from aeon.transformations.collection.convolution_based import MiniRocketMultivariate
        from sklearn.linear_model import RidgeClassifierCV
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import make_pipeline
        from sklearn.metrics import classification_report, f1_score
    except ImportError:
        print("\nMissing dependencies. Install with:")
        print("  pip install aeon scikit-learn")
        sys.exit(1)

    # Load config
    config_path = Path(config_path)
    if not config_path.is_absolute():
        config_path = Path(__file__).parent.parent / config_path
    with open(config_path) as f:
        config = yaml.safe_load(f)

    dataset_dir = config['data']['dataset_dir']
    if not Path(dataset_dir).is_absolute():
        dataset_dir = str(Path(__file__).parent.parent / dataset_dir)

    channels_mode = config['data'].get('channels_mode', 'all')

    print("=" * 60)
    print("  MiniRocket Diagnostic Baseline")
    print("=" * 60)
    print(f"  Config: {config_path.name}")
    print(f"  Channels: {channels_mode}")
    print(f"  Dataset: {dataset_dir}")
    print()

    # Load datasets
    train_ds, val_ds, test_ds, _ = prepare_datasets(
        dataset_dir=dataset_dir,
        class_map=config['class_map'],
        window_size=config['data']['window_size'],
        stride=config['data']['window_stride'],
        split_ratio=tuple(config['data']['split_ratio']),
        split_seed=config['data'].get('split_seed', 42),
        channels_mode=channels_mode,
    )

    # Convert to numpy: [N, channels, time]
    X_train = train_ds.windows.transpose(0, 2, 1)  # [N, T, C] -> [N, C, T] for aeon
    y_train = train_ds.labels
    X_test = test_ds.windows.transpose(0, 2, 1)
    y_test = test_ds.labels

    print(f"\nTrain: {X_train.shape}, Test: {X_test.shape}")
    print(f"Train labels: {dict(zip(*np.unique(y_train, return_counts=True)))}")
    print(f"Test labels:  {dict(zip(*np.unique(y_test, return_counts=True)))}")

    # MiniRocket transform + Ridge classifier
    print("\nFitting MiniRocket (10,000 random kernels)...")
    t0 = time.time()

    minirocket = MiniRocketMultivariate(n_kernels=10000, random_state=42)
    X_train_feat = minirocket.fit_transform(X_train)
    X_test_feat = minirocket.transform(X_test)

    print(f"  Transform done in {time.time()-t0:.1f}s")
    print(f"  Feature shape: {X_train_feat.shape}")

    # Ridge classifier with cross-validation for alpha
    print("\nFitting RidgeClassifierCV...")
    t0 = time.time()
    clf = make_pipeline(
        StandardScaler(),
        RidgeClassifierCV(alphas=np.logspace(-3, 3, 10)),
    )
    clf.fit(X_train_feat, y_train)
    print(f"  Fit done in {time.time()-t0:.1f}s")

    # Evaluate
    y_pred_train = clf.predict(X_train_feat)
    y_pred_test = clf.predict(X_test_feat)

    train_f1 = f1_score(y_train, y_pred_train, average='macro')
    test_f1 = f1_score(y_test, y_pred_test, average='macro')

    class_names = config.get('class_names', [f'class_{i}' for i in range(config['model']['c_out'])])

    print("\n" + "=" * 60)
    print(f"  MiniRocket Results ({channels_mode} channels)")
    print("=" * 60)
    print(f"  Train Macro F1: {train_f1:.4f}")
    print(f"  Test  Macro F1: {test_f1:.4f}")
    print()

    if train_f1 > 0.80 and test_f1 < 0.30:
        print("  DIAGNOSIS: Overfitting. Data is separable but model doesn't generalize.")
        print("  ACTION: Add regularization back ONE at a time.")
    elif train_f1 < 0.30:
        print("  DIAGNOSIS: Data representation problem. Classes overlap in feature space.")
        print("  ACTION: Fix features/derived channels. Run Fisher Discriminant analysis.")
    elif test_f1 > 0.50:
        print("  DIAGNOSIS: Data IS separable! InceptionTime training was the bottleneck.")
        print("  ACTION: Stripped training config should fix InceptionTime too.")
    else:
        print(f"  DIAGNOSIS: Partial separability. Feature engineering needed for hard classes.")

    print(f"\n  Detailed Test Report:")
    print(classification_report(y_test, y_pred_test, target_names=class_names, digits=4))

    # Per-class comparison
    print("  Per-class F1 comparison:")
    print(f"  {'Class':<20s} {'MiniRocket':>10s}  {'InceptionTime (old)':>18s}")
    print(f"  {'-'*50}")
    # Old baseline F1s from the 12-channel pipeline with FocalLoss
    old_f1s = [0.6045, 0.3539, 0.0646, 0.1692, 0.1581, 0.0332, 0.0618, 0.0052, 0.0519]
    for i, name in enumerate(class_names):
        per_cls = f1_score(y_test == i, y_pred_test == i)
        old = old_f1s[i] if i < len(old_f1s) else 0.0
        delta = per_cls - old
        print(f"  {name:<20s} {per_cls:>10.4f}  {old:>10.4f}  ({delta:+.4f})")

    print("=" * 60)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MiniRocket Diagnostic Baseline')
    parser.add_argument('--config', default='config_triage.yaml',
                        help='Config YAML (default: config_triage.yaml)')
    args = parser.parse_args()
    run_minirocket(args.config)
