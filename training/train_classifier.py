"""Train 1D-CNN failure mode classifier.

Input:  windowed .npz from prepare_windows.py
Output: SavedModel + TFLite in --model-out directory

Usage:
    python -m training.train_classifier --data training/data/windows.npz \
        --model-out training/models/classifier_v1 --epochs 100
"""
from __future__ import annotations

import argparse
import json
import logging
import os

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("train_classifier")

CLASSES = ["NORMAL", "BIAS", "OSCILLATION", "DEADBAND_HUNTING",
           "SLUGGISH", "WINDUP", "CONDITION_CHANGE"]


def build_model(window_size: int, n_features: int, n_classes: int):
    import tensorflow as tf
    model = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(window_size, n_features)),
        tf.keras.layers.Conv1D(32, 5, padding="same", activation="relu"),
        tf.keras.layers.BatchNormalization(),
        tf.keras.layers.Conv1D(64, 5, padding="same", activation="relu"),
        tf.keras.layers.BatchNormalization(),
        tf.keras.layers.MaxPooling1D(2),
        tf.keras.layers.Conv1D(64, 3, padding="same", activation="relu"),
        tf.keras.layers.BatchNormalization(),
        tf.keras.layers.MaxPooling1D(2),
        tf.keras.layers.GlobalAveragePooling1D(),
        tf.keras.layers.Dense(32, activation="relu"),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.Dense(n_classes, activation="softmax"),
    ])
    return model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--model-out", required=True)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--mixed-precision", action="store_true")
    args = parser.parse_args()

    import tensorflow as tf
    if args.mixed_precision:
        tf.keras.mixed_precision.set_global_policy("mixed_float16")

    data = np.load(args.data, allow_pickle=True)
    X, y_str = data["X"], data["y"]
    logger.info(f"Data: {X.shape}, classes: {np.unique(y_str)}")

    # Encode labels
    class_to_idx = {c: i for i, c in enumerate(CLASSES)}
    y_int = np.array([class_to_idx.get(str(l), 0) for l in y_str])
    y_onehot = tf.keras.utils.to_categorical(y_int, num_classes=len(CLASSES))

    # Normalise features per channel
    X_mean = X.mean(axis=(0, 1), keepdims=True)
    X_std = X.std(axis=(0, 1), keepdims=True) + 1e-8
    X_norm = (X - X_mean) / X_std

    # Class weights (handle imbalance)
    from sklearn.utils.class_weight import compute_class_weight
    weights = compute_class_weight("balanced", classes=np.unique(y_int), y=y_int)
    class_weights = dict(enumerate(weights))
    logger.info(f"Class weights: {class_weights}")

    # Train/val split
    from sklearn.model_selection import train_test_split
    X_train, X_val, y_train, y_val = train_test_split(
        X_norm, y_onehot, test_size=0.2, stratify=y_int, random_state=42
    )

    model = build_model(X.shape[1], X.shape[2], len(CLASSES))
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=args.lr),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )
    model.summary(print_fn=logger.info)

    callbacks = [
        tf.keras.callbacks.EarlyStopping(patience=15, restore_best_weights=True),
        tf.keras.callbacks.ReduceLROnPlateau(factor=0.5, patience=7, min_lr=1e-6),
    ]
    model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        epochs=args.epochs,
        batch_size=args.batch_size,
        class_weight=class_weights,
        callbacks=callbacks,
    )

    # Evaluate
    y_pred = model.predict(X_val).argmax(axis=1)
    y_true = y_val.argmax(axis=1)
    from sklearn.metrics import classification_report
    report = classification_report(y_true, y_pred, target_names=CLASSES)
    logger.info(f"\n{report}")

    # Save
    os.makedirs(args.model_out, exist_ok=True)
    model.save(os.path.join(args.model_out, "saved_model"))

    # Export TFLite
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    tflite_model = converter.convert()
    tflite_path = os.path.join(args.model_out, "classifier.tflite")
    with open(tflite_path, "wb") as f:
        f.write(tflite_model)
    logger.info(f"TFLite model: {tflite_path} ({len(tflite_model):,} bytes)")

    # Save normalisation params + class names for deployment
    meta = {
        "classes": CLASSES,
        "X_mean": X_mean.squeeze().tolist(),
        "X_std": X_std.squeeze().tolist(),
        "val_accuracy": float((y_pred == y_true).mean()),
    }
    with open(os.path.join(args.model_out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    logger.info(f"Saved to {args.model_out}")


if __name__ == "__main__":
    main()
