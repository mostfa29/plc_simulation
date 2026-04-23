"""Generate simulation dataset for training. Runs locally or on remote GPU.

Usage:
    python -m training.generate_dataset --per-scenario 200 --output training/data/sim_dataset.npz
"""
from __future__ import annotations

import argparse
import logging
import time

import numpy as np

from training.scenarios import ALL_GENERATORS, TRAINING_GENERATORS

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
    parser.add_argument("--per-scenario", type=int, default=200,
                        help="Scenarios per (equipment_type × scenario) cell")
    parser.add_argument("--output", default="training/data/sim_dataset.npz")
    parser.add_argument("--scenarios",
                        default=",".join(TRAINING_GENERATORS.keys()),
                        help="Comma-separated scenario list. Defaults to "
                             "TRAINING_GENERATORS (excludes 'connection' — "
                             "pass it explicitly to include).")
    parser.add_argument("--equipment-types", default="all",
                        help="Comma-separated list, or 'all' (default), or 'single' for generic")
    parser.add_argument("--fleet-weighted", action="store_true",
                        help="Weight per-equipment runs by real fleet counts")
    args = parser.parse_args()

    scenario_names = [s.strip() for s in args.scenarios.split(",")]

    # Determine equipment types to sweep
    if args.equipment_types == "single":
        equipment_list = [(None, args.per_scenario)]  # legacy: single generic sim
    elif args.equipment_types == "all":
        try:
            from training.machine_profiles import MACHINE_PROFILES, fleet_weighted_types
            if args.fleet_weighted:
                # Scale per_scenario by fleet share
                weighted = fleet_weighted_types()
                total = sum(w for _, w in weighted)
                equipment_list = [
                    (eq, max(5, int(args.per_scenario * w / total)))
                    for eq, w in weighted
                ]
            else:
                equipment_list = [(eq, args.per_scenario) for eq in MACHINE_PROFILES]
        except ImportError:
            logger.warning("machine_profiles unavailable — falling back to single")
            equipment_list = [(None, args.per_scenario)]
    else:
        equipment_list = [(e.strip(), args.per_scenario)
                          for e in args.equipment_types.split(",")]

    all_features, all_labels, all_equipment = [], [], []
    t0 = time.time()
    total_rows = 0

    for equipment_type, count in equipment_list:
        eq_label = equipment_type or "generic"
        logger.info(f"=== {eq_label} ({count}x per scenario) ===")
        for name in scenario_names:
            gen = ALL_GENERATORS.get(name)
            if not gen:
                logger.warning(f"Unknown scenario: {name}")
                continue
            for i in range(count):
                kwargs = {"seed": i * 1000 + hash((name, eq_label)) % 10000}
                if equipment_type:
                    kwargs["equipment_type"] = equipment_type
                samples, labels = gen(**kwargs)
                arr = samples_to_arrays(samples)
                all_features.append(arr)
                all_labels.extend(labels)
                all_equipment.extend([eq_label] * len(samples))
                total_rows += len(samples)

    features = np.concatenate(all_features, axis=0)
    labels = np.array(all_labels)
    equipment = np.array(all_equipment)
    logger.info(f"Dataset: {features.shape[0]:,} samples, {features.shape[1]} features")
    logger.info(f"Labels: {dict(zip(*np.unique(labels, return_counts=True)))}")
    logger.info(f"Equipment: {dict(zip(*np.unique(equipment, return_counts=True)))}")
    np.savez_compressed(args.output, features=features, labels=labels,
                        equipment=equipment, feature_names=FEATURE_COLS)
    logger.info(f"Saved to {args.output} ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
