"""Generate simulation dataset for training. Runs locally or on remote GPU.

Usage:
    python -m training.generate_dataset --per-scenario 200 --output training/data/sim_dataset.npz
"""
from __future__ import annotations

import argparse
import logging
import time

import numpy as np

from training.scenarios import ALL_GENERATORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("generate_dataset")

FEATURE_COLS = [
    "rpm_encoder", "swash_output", "ss_setpoint_fwd",
    "active_lower", "active_upper", "delivered_torque", "loop_temp",
]


def samples_to_arrays(samples: list[dict]) -> np.ndarray:
    """Convert sample dicts to (N, 7) float32 array."""
    rows = []
    for s in samples:
        rows.append([s.get(c, 0.0) for c in FEATURE_COLS])
    return np.array(rows, dtype=np.float32)


def main():
    parser = argparse.ArgumentParser(description="Generate sim training data")
    parser.add_argument("--per-scenario", type=int, default=200)
    parser.add_argument("--output", default="training/data/sim_dataset.npz")
    parser.add_argument("--scenarios", default=",".join(ALL_GENERATORS.keys()))
    args = parser.parse_args()

    scenario_names = [s.strip() for s in args.scenarios.split(",")]
    all_features, all_labels = [], []
    t0 = time.time()

    for name in scenario_names:
        gen = ALL_GENERATORS.get(name)
        if not gen:
            logger.warning(f"Unknown scenario: {name}")
            continue
        logger.info(f"Generating {args.per_scenario}x '{name}'...")
        for i in range(args.per_scenario):
            samples, labels = gen(seed=i * 1000 + hash(name) % 10000)
            arr = samples_to_arrays(samples)
            all_features.append(arr)
            all_labels.extend(labels)

    features = np.concatenate(all_features, axis=0)
    labels = np.array(all_labels)
    logger.info(f"Dataset: {features.shape[0]:,} samples, {features.shape[1]} features")
    logger.info(f"Labels: {dict(zip(*np.unique(labels, return_counts=True)))}")
    np.savez_compressed(args.output, features=features, labels=labels,
                        feature_names=FEATURE_COLS)
    logger.info(f"Saved to {args.output} ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
