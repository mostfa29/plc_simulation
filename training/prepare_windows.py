"""Window and label dataset for model training.

Converts per-sample features+labels into fixed-size windows (40 samples = 20s).

Usage:
    python -m training.prepare_windows --input training/data/sim_dataset.npz \
        --output training/data/windows.npz --window-size 40 --stride 10
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("prepare_windows")


def window_dataset(features: np.ndarray, labels: np.ndarray,
                   window_size: int = 40, stride: int = 10
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Slide a window over (N, C) features, assign majority label per window."""
    n_samples, n_features = features.shape
    windows, window_labels = [], []
    for start in range(0, n_samples - window_size + 1, stride):
        w = features[start: start + window_size]
        lbl_slice = labels[start: start + window_size]
        majority = Counter(lbl_slice).most_common(1)[0][0]
        windows.append(w)
        window_labels.append(majority)
    return np.array(windows, dtype=np.float32), np.array(window_labels)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--window-size", type=int, default=40)
    parser.add_argument("--stride", type=int, default=10)
    args = parser.parse_args()

    data = np.load(args.input, allow_pickle=True)
    features = data["features"]
    labels = data["labels"]
    logger.info(f"Input: {features.shape[0]:,} samples")

    X, y = window_dataset(features, labels, args.window_size, args.stride)
    logger.info(f"Windows: {X.shape[0]:,} × ({X.shape[1]}, {X.shape[2]})")
    logger.info(f"Labels: {dict(zip(*np.unique(y, return_counts=True)))}")

    np.savez_compressed(args.output, X=X, y=y,
                        feature_names=list(data.get("feature_names", [])))
    logger.info(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
