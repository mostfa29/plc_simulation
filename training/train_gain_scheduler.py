"""Train MLP gain scheduler from Phase B advisory logs.

Input:  Real drill CSV logs + audit.log (for accepted bounds)
Output: TFLite model mapping (rpm, psi, depth, temp) → (lower, upper)

Usage:
    python -m training.train_gain_scheduler \
        --csv-dir hxi_optimizer/logs --audit hxi_optimizer/logs/audit.log \
        --model-out training/models/gain_scheduler_v1 --epochs 200
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("train_gain_scheduler")

INPUT_COLS = ["ss_setpoint_fwd", "delivered_torque", "loop_temp"]
TARGET_COLS = ["active_lower", "active_upper"]


def load_csv_logs(csv_dir: str) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(csv_dir, "drill_*.csv")))
    if not files:
        raise FileNotFoundError(f"No drill_*.csv files in {csv_dir}")
    dfs = [pd.read_csv(f, on_bad_lines="skip") for f in files]
    df = pd.concat(dfs, ignore_index=True)
    logger.info(f"Loaded {len(df):,} rows from {len(files)} CSV files")
    return df


def prepare_training_data(df: pd.DataFrame, depth_ft: float = 3000.0):
    df = df.dropna(subset=INPUT_COLS + TARGET_COLS)
    df = df[df["stale"] == 0] if "stale" in df.columns else df
    # Stability filter: only windows where setpoint is stable
    df["sp_std"] = df["ss_setpoint_fwd"].rolling(40, min_periods=20).std()
    df = df[df["sp_std"] < 2.0].copy()
    X = df[INPUT_COLS].values.astype(np.float32)
    # Add depth as constant column
    X = np.column_stack([X, np.full(len(X), depth_ft, dtype=np.float32)])
    y = df[TARGET_COLS].values.astype(np.float32)
    logger.info(f"Training samples: {X.shape[0]:,}")
    return X, y


def build_model(n_inputs: int = 4):
    import tensorflow as tf
    model = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(n_inputs,)),
        tf.keras.layers.Dense(64, activation="relu"),
        tf.keras.layers.BatchNormalization(),
        tf.keras.layers.Dropout(0.1),
        tf.keras.layers.Dense(32, activation="relu"),
        tf.keras.layers.BatchNormalization(),
        tf.keras.layers.Dropout(0.1),
        tf.keras.layers.Dense(16, activation="relu"),
        tf.keras.layers.Dense(2),  # [lower, upper]
    ])
    return model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-dir", required=True)
    parser.add_argument("--audit", default="")
    parser.add_argument("--model-out", required=True)
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--depth-ft", type=float, default=3000.0)
    args = parser.parse_args()

    import tensorflow as tf

    df = load_csv_logs(args.csv_dir)
    X, y = prepare_training_data(df, args.depth_ft)

    # Normalise inputs
    X_mean = X.mean(axis=0)
    X_std = X.std(axis=0) + 1e-8
    X_norm = (X - X_mean) / X_std

    from sklearn.model_selection import train_test_split
    X_train, X_val, y_train, y_val = train_test_split(
        X_norm, y, test_size=0.2, random_state=42
    )

    model = build_model(X.shape[1])
    model.compile(optimizer=tf.keras.optimizers.Adam(args.lr), loss="mse",
                  metrics=["mae"])
    callbacks = [
        tf.keras.callbacks.EarlyStopping(patience=20, restore_best_weights=True),
        tf.keras.callbacks.ReduceLROnPlateau(factor=0.5, patience=10, min_lr=1e-6),
    ]
    model.fit(X_train, y_train, validation_data=(X_val, y_val),
              epochs=args.epochs, batch_size=args.batch_size, callbacks=callbacks)

    y_pred = model.predict(X_val)
    rmse = np.sqrt(np.mean((y_val - y_pred) ** 2, axis=0))
    logger.info(f"Val RMSE — lower: {rmse[0]:.1f}, upper: {rmse[1]:.1f} counts")

    os.makedirs(args.model_out, exist_ok=True)
    model.save(os.path.join(args.model_out, "saved_model"))
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    tflite = converter.convert()
    tflite_path = os.path.join(args.model_out, "gain_scheduler.tflite")
    with open(tflite_path, "wb") as f:
        f.write(tflite)

    meta = {
        "input_cols": INPUT_COLS + ["depth_ft"],
        "X_mean": X_mean.tolist(),
        "X_std": X_std.tolist(),
        "val_rmse_lower": float(rmse[0]),
        "val_rmse_upper": float(rmse[1]),
    }
    with open(os.path.join(args.model_out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    logger.info(f"Saved to {args.model_out}")


if __name__ == "__main__":
    main()
