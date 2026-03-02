"""
Dataset & Preprocessing Pipeline for Phase 1
===============================================
Loads Parquet/CSV scenarios, computes derived features, applies sliding windows,
normalizes, and yields (X, y) for PyTorch training.

KEY FIX: Per-window label assignment using fault_code activity.
Windows from fault scenarios are only labeled as faults if fault_code
is active in >= threshold of the window's timesteps. Pre-fault windows
(which look normal) get labeled as class 0.
"""
import json
import logging
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

import torch
from torch.utils.data import Dataset

from features import (
    compute_derived_channels,
    NUM_RAW_CHANNELS, NUM_TOTAL_CHANNELS,
)

# Channel modes: 'all' = 12 channels (6 raw + 6 derived), 'raw_only' = 6 raw channels
CHANNEL_MODES = {
    'all': NUM_TOTAL_CHANNELS,       # 12
    'raw_only': NUM_RAW_CHANNELS,    # 6
}

logger = logging.getLogger(__name__)

RAW_COLUMNS = [
    'torque_ftlbs', 'rpm', 'pressure_psi',
    'oil_temp_f', 'turns', 'hookload_klbs',
]
STATE_COLUMN = 'connection_state'

# FaultCode bitmask -> fault class (priority order, first match wins)
FAULT_PRIORITY = [
    (0x0800, 1),   # CONNECTION_JUMP -> cross_thread
    (0x0400, 4),   # WASHOUT        -> over_torque
    (0x0200, 6),   # WRONG_COMPOUND -> wrong_compound
    (0x0100, 7),   # MISALIGNED_STAB-> misaligned
    (0x0080, 3),   # STRIPPED_THREAD-> stripped
    (0x0008, 2),   # GALLING        -> galling
    (0x0004, 1),   # CROSS_THREAD   -> cross_thread
    (0x0040, 1),   # STICK_SLIP     -> cross_thread
    (0x0010, 8),   # STALL          -> stall
    (0x0001, 4),   # OVER_TORQUE    -> over_torque
]

CLASS_TO_SCENARIO_TYPE = {
    0: 'normal_casing_ltc', 1: 'cross_thread', 2: 'galling',
    3: 'stripped_thread', 4: 'over_torque', 5: 'under_torque',
    6: 'wrong_compound', 7: 'misaligned_stabbing', 8: 'stall',
}


@dataclass
class NormParams:
    mean: np.ndarray
    std: np.ndarray

    def save(self, path: str):
        with open(path, 'w') as f:
            json.dump({'mean': self.mean.tolist(), 'std': self.std.tolist()}, f, indent=2)

    @classmethod
    def load(cls, path: str) -> 'NormParams':
        with open(path) as f:
            data = json.load(f)
        return cls(mean=np.array(data['mean'], dtype=np.float32),
                   std=np.array(data['std'], dtype=np.float32))


def _classify_fault_code(fault_codes, torque_values=None, target_torque=None):
    combined = int(np.bitwise_or.reduce(fault_codes.astype(np.int64)))
    if combined == 0:
        return 0
    fault_class = 0
    for bit, cls in FAULT_PRIORITY:
        if combined & bit:
            fault_class = cls
            break
    if (fault_class == 8 and torque_values is not None
            and target_torque and target_torque > 0):
        if float(np.max(np.abs(torque_values))) < 0.65 * target_torque:
            return 5
    return fault_class


def _estimate_expected_turns(target_torque):
    if target_torque < 2000: return 8.0
    elif target_torque < 4000: return 5.5
    elif target_torque < 7000: return 4.0
    elif target_torque < 15000: return 3.5
    else: return 6.0


def auto_build_manifest(sensor_dir, class_map):
    class_to_type = {}
    for stype, cls in class_map.items():
        if cls not in class_to_type:
            class_to_type[cls] = stype
    for cls, stype in CLASS_TO_SCENARIO_TYPE.items():
        if cls not in class_to_type:
            class_to_type[cls] = stype

    files = sorted(f for f in sensor_dir.iterdir() if f.suffix in ('.parquet', '.csv'))
    if not files:
        raise FileNotFoundError(f"No sensor files in {sensor_dir}")

    logger.info(f"Auto-building manifest from {len(files)} sensor files...")
    records = []
    for i, fp in enumerate(files):
        if (i + 1) % 500 == 0 or i == 0:
            logger.info(f"  Scanning {i+1}/{len(files)}...")
        try:
            df = pd.read_parquet(fp) if fp.suffix == '.parquet' else pd.read_csv(fp)
        except Exception as e:
            logger.warning(f"  Skipping {fp.name}: {e}")
            continue

        target_torque = 5000.0
        if 'target_torque' in df.columns:
            tt = df['target_torque'].dropna()
            if len(tt) > 0:
                modes = tt.mode()
                target_torque = float(modes.iloc[0]) if len(modes) > 0 else float(tt.median())

        torque_values = df['torque_ftlbs'].values if 'torque_ftlbs' in df.columns else None
        if 'fault_code' in df.columns:
            fault_class = _classify_fault_code(df['fault_code'].values, torque_values, target_torque)
        else:
            fault_class = 0

        records.append({
            'scenario_id': i, 'filename': fp.name,
            'scenario_type': class_to_type.get(fault_class, 'normal_casing_ltc'),
            'fault_class': fault_class,
            'target_torque_ftlbs': target_torque,
            'expected_turns': _estimate_expected_turns(target_torque),
            'num_samples': len(df),
        })

    manifest = pd.DataFrame(records)
    logger.info(f"Auto-manifest: {len(manifest)} scenarios")
    for cls in sorted(manifest['fault_class'].unique()):
        count = int((manifest['fault_class'] == cls).sum())
        logger.info(f"  Class {cls} ({class_to_type.get(cls, '?')}): {count}")

    try:
        manifest.to_csv(sensor_dir.parent / 'manifest.csv', index=False)
    except OSError:
        pass
    return manifest


# ═══════════════════════════════════════════════════════════════════
# Core pipeline
# ═══════════════════════════════════════════════════════════════════

def scenario_to_windows(filepath, target_torque, expected_turns,
                         window_size=2000, stride=1000, dt=0.01,
                         channels_mode='all'):
    """Load scenario and create sliding windows.

    Args:
        channels_mode: 'all' = 12 channels (raw + derived),
                       'raw_only' = 6 raw channels only.

    Returns (features, fault_codes):
      features: [W, window_size, num_channels] float32
      fault_codes: [W, window_size] int64 or None
    """
    num_channels = CHANNEL_MODES.get(channels_mode, NUM_TOTAL_CHANNELS)

    try:
        df = pd.read_parquet(filepath) if filepath.suffix == '.parquet' else pd.read_csv(filepath)
    except Exception as e:
        logger.warning(f"Error loading {filepath}: {e}")
        return np.empty((0, window_size, num_channels), dtype=np.float32), None

    for col in RAW_COLUMNS:
        if col not in df.columns:
            logger.warning(f"Missing column {col} in {filepath}")
            return np.empty((0, window_size, num_channels), dtype=np.float32), None

    raw = df[RAW_COLUMNS].values.astype(np.float32)
    has_fc = 'fault_code' in df.columns
    fc_raw = df['fault_code'].values.astype(np.int64) if has_fc else None

    if channels_mode == 'raw_only':
        features = raw
    else:
        state_col = STATE_COLUMN if STATE_COLUMN in df.columns else None
        if state_col:
            state = df[state_col].values.astype(np.int32)
        else:
            state = np.zeros(len(raw), dtype=np.int32)
        features = compute_derived_channels(raw, state, target_torque, expected_turns, dt=dt)

    n = features.shape[0]

    feat_wins, fc_wins = [], []
    start = 0
    while start < n:
        end = start + window_size
        if end <= n:
            feat_wins.append(features[start:end])
            if fc_raw is not None:
                fc_wins.append(fc_raw[start:end])
        else:
            win = np.zeros((window_size, num_channels), dtype=np.float32)
            actual = n - start
            win[:actual] = features[start:n]
            feat_wins.append(win)
            if fc_raw is not None:
                fc_w = np.zeros(window_size, dtype=np.int64)
                fc_w[:actual] = fc_raw[start:n]
                fc_wins.append(fc_w)
        start += stride

    if not feat_wins:
        win = np.zeros((window_size, num_channels), dtype=np.float32)
        actual = min(n, window_size)
        win[:actual] = features[:actual]
        feat_wins.append(win)
        if fc_raw is not None:
            fc_w = np.zeros(window_size, dtype=np.int64)
            fc_w[:actual] = fc_raw[:actual]
            fc_wins.append(fc_w)

    return np.stack(feat_wins), (np.stack(fc_wins) if fc_wins else None)


def build_dataset_index(manifest, scenario_ids, sensor_dir, class_map,
                         window_size=2000, stride=1000,
                         fault_threshold=0.10, channels_mode='all'):
    """Build windowed dataset with PER-WINDOW label assignment.

    For fault scenarios, each window is labeled based on whether fault_code
    is active in >= fault_threshold fraction of timesteps. Pre-fault windows
    are relabeled as normal (class 0).
    """
    all_windows, all_labels, all_sids = [], [], []
    relabeled, total_fault = 0, 0

    for _, row in manifest[manifest['scenario_id'].isin(scenario_ids)].iterrows():
        fp = sensor_dir / row['filename']
        if not fp.exists():
            continue

        tt = float(row.get('target_torque_ftlbs', 5000))
        et = float(row.get('expected_turns', 5.0))

        if 'fault_class' in row.index and pd.notna(row['fault_class']):
            sc = int(row['fault_class'])
        else:
            sc = class_map.get(row['scenario_type'], 0)

        windows, fc_windows = scenario_to_windows(
            fp, tt, et, window_size, stride, channels_mode=channels_mode)
        if windows.shape[0] == 0:
            continue

        nw = windows.shape[0]

        if sc == 0 or fc_windows is None:
            labels = [sc] * nw
        else:
            labels = []
            for wi in range(nw):
                total_fault += 1
                frac = np.count_nonzero(fc_windows[wi]) / len(fc_windows[wi])
                if frac >= fault_threshold:
                    labels.append(sc)
                else:
                    labels.append(0)
                    relabeled += 1

        all_windows.append(windows)
        all_labels.extend(labels)
        all_sids.extend([int(row['scenario_id'])] * nw)

    if total_fault > 0:
        logger.info(f"  Per-window labeling: {relabeled}/{total_fault} fault windows "
                     f"relabeled as normal ({100*relabeled/total_fault:.1f}% were pre-fault)")

    return all_windows, np.array(all_labels), np.array(all_sids)


def compute_norm_params(windows_list):
    num_ch = windows_list[0].shape[-1] if windows_list else NUM_TOTAL_CHANNELS
    all_data = np.concatenate([w.reshape(-1, num_ch) for w in windows_list], axis=0)
    mean = np.mean(all_data, axis=0).astype(np.float32)
    std = np.std(all_data, axis=0).astype(np.float32)
    std = np.where(std < 1e-8, 1.0, std)
    return NormParams(mean=mean, std=std)


def apply_normalization(windows, norm):
    normalized = (windows - norm.mean) / norm.std
    return normalized.astype(np.float32)


class Phase1Dataset(Dataset):
    def __init__(self, windows, labels):
        self.windows = windows
        self.labels = labels
    def __len__(self):
        return len(self.labels)
    def __getitem__(self, idx):
        x = torch.from_numpy(self.windows[idx]).T
        return x, int(self.labels[idx])


def create_splits(manifest, class_map, split_ratio=(0.70, 0.15, 0.15), seed=42):
    rng = np.random.RandomState(seed)
    manifest = manifest.copy()
    manifest['fault_class'] = manifest['scenario_type'].map(class_map).fillna(0).astype(int)
    class_groups = manifest.groupby('fault_class')['scenario_id'].apply(list).to_dict()

    train_ids, val_ids, test_ids = [], [], []
    train_frac, val_frac, _ = split_ratio
    for cls, ids in class_groups.items():
        ids = np.array(ids)
        rng.shuffle(ids)
        n = len(ids)
        n_train = max(1, int(n * train_frac))
        n_val = max(1, int(n * val_frac))
        train_ids.extend(ids[:n_train].tolist())
        val_ids.extend(ids[n_train:n_train + n_val].tolist())
        test_ids.extend(ids[n_train + n_val:].tolist())
    return np.array(train_ids), np.array(val_ids), np.array(test_ids)


def prepare_datasets(dataset_dir, class_map, window_size=2000, stride=1000,
                      split_ratio=(0.70, 0.15, 0.15), split_seed=42,
                      norm_params_path=None, fault_threshold=0.10,
                      channels_mode='all'):
    """Full pipeline: manifest -> split -> window -> normalize -> Dataset.

    Args:
        channels_mode: 'all' = 12 channels (raw + derived),
                       'raw_only' = 6 raw channels only.
    """
    num_channels = CHANNEL_MODES.get(channels_mode, NUM_TOTAL_CHANNELS)
    dataset_dir = Path(dataset_dir)
    sensor_dir = dataset_dir / 'sensor'

    manifest_parquet = dataset_dir / 'manifest.parquet'
    manifest_csv = dataset_dir / 'manifest.csv'

    if manifest_parquet.exists():
        manifest = pd.read_parquet(manifest_parquet)
    elif manifest_csv.exists():
        manifest = pd.read_csv(manifest_csv)
    else:
        manifest = auto_build_manifest(sensor_dir, class_map)

    if 'scenario_id' not in manifest.columns:
        manifest['scenario_id'] = range(len(manifest))

    print(f"Channel mode: {channels_mode} ({num_channels} channels)")

    train_ids, val_ids, test_ids = create_splits(manifest, class_map, split_ratio, split_seed)

    print(f"Building training windows ({len(train_ids)} scenarios)...")
    train_windows, train_labels, _ = build_dataset_index(
        manifest, train_ids, sensor_dir, class_map, window_size, stride,
        fault_threshold, channels_mode=channels_mode)

    print(f"Building validation windows ({len(val_ids)} scenarios)...")
    val_windows, val_labels, _ = build_dataset_index(
        manifest, val_ids, sensor_dir, class_map, window_size, stride,
        fault_threshold, channels_mode=channels_mode)

    print(f"Building test windows ({len(test_ids)} scenarios)...")
    test_windows, test_labels, _ = build_dataset_index(
        manifest, test_ids, sensor_dir, class_map, window_size, stride,
        fault_threshold, channels_mode=channels_mode)

    train_all = np.concatenate(train_windows) if train_windows else np.empty((0, window_size, num_channels))
    val_all = np.concatenate(val_windows) if val_windows else np.empty((0, window_size, num_channels))
    test_all = np.concatenate(test_windows) if test_windows else np.empty((0, window_size, num_channels))

    print("Computing normalization from training data...")
    norm = compute_norm_params(train_windows if train_windows else [train_all])
    train_all = apply_normalization(train_all, norm)
    val_all = apply_normalization(val_all, norm)
    test_all = apply_normalization(test_all, norm)

    if norm_params_path:
        norm.save(norm_params_path)

    # Check for NaN/Inf
    for name, arr in [('train', train_all), ('val', val_all), ('test', test_all)]:
        if np.any(np.isnan(arr)):
            print(f"  WARNING: {name} has NaN values!")
        if np.any(np.isinf(arr)):
            print(f"  WARNING: {name} has Inf values!")

    print(f"\nDataset sizes: train={len(train_labels)}, val={len(val_labels)}, test={len(test_labels)}")
    from collections import Counter
    for sn, lb in [('train', train_labels), ('val', val_labels), ('test', test_labels)]:
        dist = Counter(int(l) for l in lb)
        print(f"  {sn}: {dict(sorted(dist.items()))}")

    return (Phase1Dataset(train_all, train_labels),
            Phase1Dataset(val_all, val_labels),
            Phase1Dataset(test_all, test_labels), norm)