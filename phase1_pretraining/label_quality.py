"""
Label Quality Tools (Session 4)
=================================
Fix label corruption in the training manifest.

The auto-manifest builder misclassified scenarios by reading fault_code
bitmasks from sensor CSVs. The truth/ files and ground_truth_label in the
manifest contain the actual fault type assigned at generation time.

Tools:
  1. rebuild_manifest() - Rebuild using ground_truth_label > bitmask heuristic
  2. validate_labels() - Cross-check labels against physics signatures
  3. improved bitmask parsing for scenarios without ground truth
"""
import logging
import os
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Fault class mapping (matches config class_map)
CLASS_NAMES = [
    'normal', 'cross_thread', 'galling', 'stripped_thread',
    'over_torque', 'under_torque', 'wrong_compound',
    'misaligned_stab', 'stall'
]

# Bitmask -> fault class (same as dataset.py but with improved logic)
FAULT_BIT_MAP = {
    0: 'normal',
    1: 'cross_thread',
    2: 'galling',
    3: 'stripped_thread',
    4: 'over_torque',
    5: 'under_torque',
    6: 'wrong_compound',
    7: 'misaligned_stab',
    8: 'stall',
}

LABEL_TO_CLASS = {name: i for i, name in enumerate(CLASS_NAMES)}


def rebuild_manifest(dataset_dir, class_map=None):
    """Rebuild scenario manifest using the best available label source.

    Priority:
    1. ground_truth_label column in existing manifest (reliable)
    2. ground_truth_class column in existing manifest
    3. Improved bitmask heuristic from sensor data (fallback)

    Args:
        dataset_dir: Path to dataset directory containing manifest.csv
        class_map: Optional class_map dict from config

    Returns:
        Updated manifest DataFrame with corrected fault_class column.
    """
    dataset_dir = Path(dataset_dir)
    sensor_dir = dataset_dir / 'sensor'

    # Load existing manifest
    manifest_parquet = dataset_dir / 'manifest.parquet'
    manifest_csv = dataset_dir / 'manifest.csv'

    if manifest_parquet.exists():
        manifest = pd.read_parquet(manifest_parquet)
    elif manifest_csv.exists():
        manifest = pd.read_csv(manifest_csv)
    else:
        raise FileNotFoundError(f"No manifest found in {dataset_dir}")

    logger.info(f"Rebuilding manifest: {len(manifest)} scenarios")

    # Track label sources
    source_counts = Counter()
    corrections = 0

    for idx, row in manifest.iterrows():
        old_class = int(row.get('fault_class', -1)) if pd.notna(row.get('fault_class', None)) else -1

        # Priority 1: ground_truth_label (string label from simulation events)
        gt_label = row.get('ground_truth_label', None)
        if pd.notna(gt_label) and str(gt_label) != 'FAILED':
            gt_label = str(gt_label).strip().lower()
            # Map label string to class index
            new_class = LABEL_TO_CLASS.get(gt_label, None)
            if new_class is None:
                # Try matching via class_map
                if class_map:
                    new_class = class_map.get(gt_label, None)
                    # Also try common aliases
                    if new_class is None:
                        for key, val in class_map.items():
                            if gt_label in key or key in gt_label:
                                new_class = val
                                break
            if new_class is not None:
                manifest.at[idx, 'fault_class'] = int(new_class)
                source_counts['ground_truth_label'] += 1
                if old_class != new_class and old_class >= 0:
                    corrections += 1
                continue

        # Priority 2: ground_truth_class (integer class from simulation)
        gt_class = row.get('ground_truth_class', None)
        if pd.notna(gt_class):
            gt_class = int(gt_class)
            if 0 <= gt_class <= 8:
                manifest.at[idx, 'fault_class'] = gt_class
                source_counts['ground_truth_class'] += 1
                if old_class != gt_class and old_class >= 0:
                    corrections += 1
                continue

        # Priority 3: Improved bitmask heuristic (fallback)
        filename = row.get('filename', '')
        if filename:
            fp = sensor_dir / filename
            if fp.exists():
                new_class = parse_fault_from_bitmask_improved(fp)
                manifest.at[idx, 'fault_class'] = new_class
                source_counts['bitmask_heuristic'] += 1
                if old_class != new_class and old_class >= 0:
                    corrections += 1
                continue

        # No source available — keep existing
        source_counts['kept_existing'] += 1

    # Ensure fault_class is int
    manifest['fault_class'] = manifest['fault_class'].astype(int)

    # Report
    logger.info(f"\nLabel sources:")
    for src, count in source_counts.most_common():
        logger.info(f"  {src}: {count}")
    logger.info(f"  Corrections made: {corrections}")

    logger.info(f"\nClass distribution:")
    for cls in sorted(manifest['fault_class'].unique()):
        count = int((manifest['fault_class'] == cls).sum())
        name = CLASS_NAMES[cls] if cls < len(CLASS_NAMES) else f'class_{cls}'
        logger.info(f"  {cls} ({name}): {count}")

    # Save corrected manifest
    try:
        manifest.to_csv(manifest_csv, index=False)
        logger.info(f"\nSaved corrected manifest to {manifest_csv}")
    except OSError as e:
        logger.warning(f"Could not save manifest: {e}")

    return manifest


def parse_fault_from_bitmask_improved(filepath):
    """Improved bitmask heuristic for scenarios without ground truth labels.

    The OLD heuristic took the lowest set bit of the first non-zero fault_code.
    Problem: secondary faults (e.g., stall) often trigger BEFORE the root cause.

    NEW heuristic:
    1. Read the LAST 20% of the scenario (when root cause is most apparent)
    2. Count occurrences of each fault bit across all timesteps
    3. Take the MOST FREQUENT primary fault bit
    4. Exclude stall (bit 4/0x0010) if any other fault bit is present
       (stall is usually a CONSEQUENCE, not a root cause)

    Returns:
        int: fault class index (0-8)
    """
    try:
        if str(filepath).endswith('.parquet'):
            df = pd.read_parquet(filepath, columns=['fault_code'])
        else:
            df = pd.read_csv(filepath, usecols=['fault_code'])
    except Exception:
        return 0

    if 'fault_code' not in df.columns:
        return 0

    n = len(df)
    # Focus on last 20% of scenario
    df_late = df.iloc[int(n * 0.8):]
    fault_codes = df_late['fault_code'].values

    # Count bit occurrences — use the actual bitmask definitions
    # from generate_dataset.py and dataset.py
    BIT_TO_CLASS = {
        0x0001: 4,   # OVER_TORQUE
        0x0004: 1,   # CROSS_THREAD
        0x0008: 2,   # GALLING
        0x0010: 8,   # STALL
        0x0040: 1,   # STICK_SLIP -> cross_thread
        0x0080: 3,   # STRIPPED_THREAD
        0x0100: 7,   # MISALIGNED_STAB
        0x0200: 6,   # WRONG_COMPOUND
        0x0400: 4,   # WASHOUT -> over_torque
        0x0800: 1,   # CONNECTION_JUMP -> cross_thread
    }

    class_counts = Counter()
    for code in fault_codes:
        code = int(code)
        if code == 0:
            class_counts[0] += 1
            continue
        for bit, cls in BIT_TO_CLASS.items():
            if code & bit:
                class_counts[cls] += 1

    if not class_counts or (len(class_counts) == 1 and 0 in class_counts):
        return 0

    # Remove normal from consideration if any fault exists
    non_normal = {k: v for k, v in class_counts.items() if k != 0}
    if not non_normal:
        return 0

    # If multiple fault classes, exclude stall (class 8) as it's usually a consequence
    if len(non_normal) > 1 and 8 in non_normal:
        non_stall = {k: v for k, v in non_normal.items() if k != 8}
        if non_stall:
            non_normal = non_stall

    # Take most frequent fault class
    return max(non_normal, key=non_normal.get)


def validate_labels(manifest, sensor_dir):
    """Cross-check labels against physical signatures in the data.

    Flags scenarios where the label doesn't match expected physics.

    Returns:
        List of (scenario_id, label, reason) tuples for suspicious labels.
    """
    sensor_dir = Path(sensor_dir)
    suspicious = []

    for _, row in manifest.iterrows():
        filename = row.get('filename', '')
        if not filename:
            continue
        fp = sensor_dir / filename
        if not fp.exists():
            continue

        fault_class = int(row.get('fault_class', 0))
        label = CLASS_NAMES[fault_class] if fault_class < len(CLASS_NAMES) else f'class_{fault_class}'
        scenario_id = row.get('scenario_id', row.name)

        try:
            if str(fp).endswith('.parquet'):
                df = pd.read_parquet(fp, columns=['torque_ftlbs', 'rpm'])
            else:
                df = pd.read_csv(fp, usecols=['torque_ftlbs', 'rpm'])
        except Exception:
            continue

        torque = df['torque_ftlbs'].values
        rpm = df['rpm'].values

        max_torque = float(np.max(torque))
        min_rpm_active = float(np.min(rpm[rpm > 0])) if np.any(rpm > 0) else 0

        # Late torque slope
        late_torque = torque[-200:] if len(torque) >= 200 else torque
        if len(late_torque) >= 2:
            x = np.arange(len(late_torque))
            late_slope = np.polyfit(x, late_torque, 1)[0]
        else:
            late_slope = 0.0

        # Physics-based checks
        if label == 'stall' and min_rpm_active > 10:
            suspicious.append((scenario_id, label, 'stall labeled but RPM never drops near zero'))
        if label == 'over_torque' and max_torque < 500:
            suspicious.append((scenario_id, label, 'over_torque labeled but max torque < 500 ft-lbs'))
        if label == 'under_torque' and max_torque > 5000:
            suspicious.append((scenario_id, label, 'under_torque labeled but max torque > 5000 ft-lbs'))
        if label == 'stripped_thread' and late_slope > 5.0:
            suspicious.append((scenario_id, label, 'stripped_thread but torque rising at end'))

    pct = len(suspicious) / max(len(manifest), 1) * 100
    logger.info(f"\nLabel validation: {len(suspicious)} suspicious / {len(manifest)} total ({pct:.1f}%)")
    for s in suspicious[:20]:
        logger.info(f"  {s[0]}: {s[1]} - {s[2]}")

    return suspicious


if __name__ == '__main__':
    import argparse
    import yaml

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    parser = argparse.ArgumentParser(description='Label Quality Tools')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--action', choices=['rebuild', 'validate', 'both'], default='both')
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).parent / config_path
    with open(config_path) as f:
        config = yaml.safe_load(f)

    dataset_dir = config['data']['dataset_dir']
    if not Path(dataset_dir).is_absolute():
        dataset_dir = str(Path(__file__).parent / dataset_dir)

    if args.action in ('rebuild', 'both'):
        manifest = rebuild_manifest(dataset_dir, config.get('class_map'))
    else:
        dataset_dir = Path(dataset_dir)
        mp = dataset_dir / 'manifest.parquet'
        mc = dataset_dir / 'manifest.csv'
        manifest = pd.read_parquet(mp) if mp.exists() else pd.read_csv(mc)

    if args.action in ('validate', 'both'):
        sensor_dir = Path(dataset_dir) / 'sensor'
        validate_labels(manifest, sensor_dir)
