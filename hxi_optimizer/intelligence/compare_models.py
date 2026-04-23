"""A/B compare two classifier pairs on the same labeled episodes.

Used by the dashboard before promoting a fine-tuned model to production.
Runs the sim-trained default AND the per-rig fine-tune against the same
captured real episodes, then reports per-model + per-class accuracy plus
a recommendation (promote / neutral / rollback / no_fine_tune_yet) so
the operator doesn't have to eyeball confusion matrices.

Key design points:
  - Each model normalises with its OWN meta.json X_mean / X_std — never
    mix a stats vector across models (would silently corrupt inference).
  - Class order is aligned to a canonical list; if a model was saved
    with a different permutation the predictions are remapped before
    scoring.
  - Pure numpy + onnxruntime, no torch dependency. The dashboard already
    loads onnxruntime for live inference so we pay zero extra import cost.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("compare_models")


@dataclass
class ModelResult:
    rig_slug: str
    source: str                                # "default" | "per_rig"
    classifier_path: Optional[str]
    n_samples: int
    overall_accuracy: float
    per_class_accuracy: dict[str, float] = field(default_factory=dict)
    per_class_support: dict[str, int] = field(default_factory=dict)
    mean_confidence: float = 0.0
    confusion: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "rig_slug": self.rig_slug,
            "source": self.source,
            "classifier_path": self.classifier_path,
            "n_samples": self.n_samples,
            "overall_accuracy": round(self.overall_accuracy, 4),
            "per_class_accuracy": {k: round(v, 4)
                                    for k, v in self.per_class_accuracy.items()},
            "per_class_support": self.per_class_support,
            "mean_confidence": round(self.mean_confidence, 4),
            "confusion": self.confusion,
            "error": self.error,
        }


def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    x = x - x.max(axis=axis, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=axis, keepdims=True)


def _load_meta(path: Optional[Path]) -> dict:
    if path is None or not Path(path).exists():
        return {}
    try:
        return json.loads(Path(path).read_text())
    except Exception as e:
        logger.warning(f"meta parse failed {path}: {e}")
        return {}


def _evaluate(pair, classes: list[str], X: np.ndarray,
               y: np.ndarray) -> ModelResult:
    """Run one classifier pair over the windows and score predictions."""
    result = ModelResult(
        rig_slug=pair.rig_slug,
        source=pair.source,
        classifier_path=str(pair.classifier_path) if pair.classifier_path else None,
        n_samples=int(len(X)),
        overall_accuracy=0.0,
    )
    if not pair.has_classifier:
        result.error = "no classifier deployed"
        return result

    meta = _load_meta(pair.classifier_meta_path)
    n_features = X.shape[-1]
    X_mean = np.array(meta.get("X_mean", [0.0] * n_features), dtype=np.float32)
    X_std = np.array(meta.get("X_std", [1.0] * n_features), dtype=np.float32)
    cls_names = meta.get("classes", classes)

    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(pair.classifier_path),
                                     providers=["CPUExecutionProvider"])
        input_name = sess.get_inputs()[0].name
    except Exception as e:
        result.error = f"onnx load failed: {e}"
        return result

    X_norm = ((X - X_mean) / (X_std + 1e-8)).astype(np.float32)

    preds = np.zeros(len(X_norm), dtype=np.int64)
    confs = np.zeros(len(X_norm), dtype=np.float32)
    try:
        for i in range(0, len(X_norm), 64):
            batch = X_norm[i: i + 64]
            logits = sess.run(None, {input_name: batch})[0]
            probs = _softmax(logits, axis=-1)
            preds[i: i + 64] = probs.argmax(axis=-1)
            confs[i: i + 64] = probs.max(axis=-1)
    except Exception as e:
        result.error = f"inference failed: {e}"
        return result

    # Align model's class-index space to the canonical one. If the per-rig
    # fine-tune was saved with a different class order, remap.
    if list(cls_names) != list(classes):
        remap = np.array([
            classes.index(n) if n in classes else -1
            for n in cls_names
        ], dtype=np.int64)
        # Drop predictions into unknown classes (shouldn't happen with our
        # pipeline but guard against it)
        preds_aligned = np.where(remap[preds] >= 0, remap[preds], preds)
    else:
        preds_aligned = preds

    result.overall_accuracy = float((preds_aligned == y).mean())
    result.mean_confidence = float(confs.mean())

    for i, name in enumerate(classes):
        mask = (y == i)
        support = int(mask.sum())
        if support == 0:
            continue
        result.per_class_accuracy[name] = float((preds_aligned[mask] == i).mean())
        result.per_class_support[name] = support

    # Compact confusion dict: {true_label: {pred_label: count}}
    confusion: dict[str, dict[str, int]] = {}
    for t, p in zip(y, preds_aligned):
        tn = classes[t] if 0 <= t < len(classes) else str(t)
        pn = classes[p] if 0 <= p < len(classes) else str(p)
        row = confusion.setdefault(tn, {})
        row[pn] = row.get(pn, 0) + 1
    result.confusion = confusion
    return result


def _recommend(default_acc: float, per_rig_acc: float,
                n_windows: int) -> tuple[str, str]:
    """Turn the accuracy delta into a one-word recommendation + rationale."""
    delta = per_rig_acc - default_acc
    if n_windows < 50:
        return ("insufficient_data",
                f"Only {n_windows} windows — capture more before promoting")
    if delta > 0.02:
        return ("promote",
                f"Fine-tune beats sim by {delta*100:.1f}pp — safe to keep deployed")
    if delta > -0.02:
        return ("neutral",
                f"Fine-tune differs from sim by {delta*100:+.1f}pp (within noise)")
    return ("rollback",
            f"Fine-tune is {-delta*100:.1f}pp WORSE than sim — rollback recommended")


def compare_pairs(registry, rig_name: str, classes: list[str],
                   X: np.ndarray, y: np.ndarray) -> dict:
    """Evaluate both default and per-rig pair on the same (X, y). Returns a
    JSON-ready dict with the comparison + a promote/rollback recommendation.
    """
    default_pair = registry.resolve(None)
    has_per_rig = registry.has_per_rig(rig_name) if rig_name else False
    per_rig_pair = registry.resolve(rig_name) if rig_name else None

    default_result = _evaluate(default_pair, classes, X, y)

    per_rig_result = None
    if has_per_rig and per_rig_pair and per_rig_pair.source == "per_rig":
        per_rig_result = _evaluate(per_rig_pair, classes, X, y)

    # Agreement rate (how often both models predict the same class)
    agreement: Optional[float] = None

    if per_rig_result and per_rig_result.error is None \
            and default_result.error is None:
        recommendation, rationale = _recommend(
            default_result.overall_accuracy,
            per_rig_result.overall_accuracy,
            len(X),
        )
        improvement = per_rig_result.overall_accuracy \
                      - default_result.overall_accuracy
    else:
        recommendation = "no_fine_tune_yet" if not has_per_rig else "error"
        rationale = ("No per-rig fine-tune deployed — using sim default"
                     if not has_per_rig else
                     "Could not evaluate fine-tune (see error fields)")
        improvement = 0.0

    return {
        "rig_name": rig_name,
        "classes": classes,
        "n_windows": int(len(X)),
        "has_per_rig": has_per_rig,
        "default": default_result.to_json(),
        "per_rig": per_rig_result.to_json() if per_rig_result else None,
        "improvement": round(improvement, 4),
        "improvement_pp": round(improvement * 100, 2),
        "recommendation": recommendation,
        "rationale": rationale,
    }
