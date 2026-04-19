"""Train convolutional autoencoder for condition-change detection.

Trains ONLY on NORMAL windows. Anomalies detected by high reconstruction error.

Usage:
    python -m training.train_autoencoder --data training/data/windows.npz \
        --model-out training/models/autoencoder_v1 --epochs 300
"""
from __future__ import annotations

import argparse
import json
import logging
import os

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("train_autoencoder")


def build_autoencoder(window_size: int, n_features: int, latent_dim: int = 16):
    import tensorflow as tf
    # Encoder
    inp = tf.keras.layers.Input(shape=(window_size, n_features))
    x = tf.keras.layers.Conv1D(32, 5, strides=2, padding="same", activation="relu")(inp)
    x = tf.keras.layers.BatchNormalization()(x)
    x = tf.keras.layers.Conv1D(64, 3, strides=2, padding="same", activation="relu")(x)
    x = tf.keras.layers.BatchNormalization()(x)
    x = tf.keras.layers.Conv1D(16, 3, strides=2, padding="same", activation="relu")(x)
    x = tf.keras.layers.BatchNormalization()(x)
    shape_before = x.shape[1:]
    x = tf.keras.layers.Flatten()(x)
    latent = tf.keras.layers.Dense(latent_dim, name="latent")(x)

    # Decoder
    x = tf.keras.layers.Dense(int(np.prod(shape_before)))(latent)
    x = tf.keras.layers.Reshape(shape_before)(x)
    x = tf.keras.layers.Conv1DTranspose(64, 3, strides=2, padding="same", activation="relu")(x)
    x = tf.keras.layers.BatchNormalization()(x)
    x = tf.keras.layers.Conv1DTranspose(32, 3, strides=2, padding="same", activation="relu")(x)
    x = tf.keras.layers.BatchNormalization()(x)
    x = tf.keras.layers.Conv1DTranspose(n_features, 5, strides=2, padding="same", activation="sigmoid")(x)
    # Trim to match input size
    out = tf.keras.layers.Lambda(lambda t: t[:, :window_size, :])(x)

    model = tf.keras.Model(inp, out, name="condition_ae")
    return model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--model-out", required=True)
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--latent-dim", type=int, default=16)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--label-filter", default="NORMAL")
    parser.add_argument("--mixed-precision", action="store_true")
    args = parser.parse_args()

    import tensorflow as tf
    if args.mixed_precision:
        tf.keras.mixed_precision.set_global_policy("mixed_float16")

    data = np.load(args.data, allow_pickle=True)
    X_all, y_all = data["X"], data["y"]

    # Filter to NORMAL only for training
    mask = np.array([str(l) == args.label_filter for l in y_all])
    X_normal = X_all[mask]
    logger.info(f"Normal windows: {X_normal.shape[0]:,} / {X_all.shape[0]:,}")

    # Min-max normalise to [0, 1] (sigmoid output)
    X_min = X_normal.min(axis=(0, 1), keepdims=True)
    X_max = X_normal.max(axis=(0, 1), keepdims=True) + 1e-8
    X_norm = (X_normal - X_min) / (X_max - X_min)

    from sklearn.model_selection import train_test_split
    X_train, X_val = train_test_split(X_norm, test_size=0.1, random_state=42)

    model = build_autoencoder(X_norm.shape[1], X_norm.shape[2], args.latent_dim)
    model.compile(optimizer=tf.keras.optimizers.Adam(args.lr), loss="mse")
    model.summary(print_fn=logger.info)

    callbacks = [
        tf.keras.callbacks.ReduceLROnPlateau(factor=0.5, patience=20, min_lr=1e-6),
    ]
    model.fit(
        X_train, X_train,
        validation_data=(X_val, X_val),
        epochs=args.epochs,
        batch_size=args.batch_size,
        callbacks=callbacks,
    )

    # Compute threshold from training set reconstruction error
    recon = model.predict(X_train, batch_size=256)
    mse_per_window = np.mean((X_train - recon) ** 2, axis=(1, 2))
    threshold = float(np.mean(mse_per_window) + 3 * np.std(mse_per_window))
    logger.info(f"Threshold (mean + 3σ): {threshold:.6f}")

    # Test on anomaly windows
    X_anomaly = X_all[~mask]
    if len(X_anomaly) > 0:
        X_anom_norm = np.clip((X_anomaly - X_min) / (X_max - X_min), 0, 1)
        recon_a = model.predict(X_anom_norm, batch_size=256)
        mse_a = np.mean((X_anom_norm - recon_a) ** 2, axis=(1, 2))
        detection_rate = float(np.mean(mse_a > threshold))
        logger.info(f"Anomaly detection rate: {detection_rate:.1%} "
                     f"({np.sum(mse_a > threshold)}/{len(mse_a)})")

    os.makedirs(args.model_out, exist_ok=True)
    model.save(os.path.join(args.model_out, "saved_model"))

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    tflite = converter.convert()
    tflite_path = os.path.join(args.model_out, "autoencoder.tflite")
    with open(tflite_path, "wb") as f:
        f.write(tflite)
    logger.info(f"TFLite: {tflite_path} ({len(tflite):,} bytes)")

    meta = {
        "X_min": X_min.squeeze().tolist(),
        "X_max": X_max.squeeze().tolist(),
        "threshold": threshold,
        "latent_dim": args.latent_dim,
    }
    with open(os.path.join(args.model_out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)


if __name__ == "__main__":
    main()
