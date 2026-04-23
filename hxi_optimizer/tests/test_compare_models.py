"""Tests for the A/B model-comparison module.

Focus on the decision logic (_recommend + compare_pairs return shape) —
ONNX inference is covered end-to-end via the existing classifier tests,
so we don't repeat that here. Instead we stub model pairs and feed
synthetic predictions/ground-truth to verify:

  - recommend() promotes when delta > +2pp, rolls back when < -2pp
  - compare_pairs reports 'no_fine_tune_yet' when no per-rig pair exists
  - compare_pairs handles errored evaluations without crashing
  - ModelResult.to_json is dashboard-safe (rounded, no numpy scalars)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from hxi_optimizer.intelligence.compare_models import (
    ModelResult, _recommend, _softmax, _evaluate, compare_pairs,
)
from hxi_optimizer.intelligence.model_registry import ModelRegistry, ModelPair


# ═════════════════════════════════════════════════════════════════════
# 1. _recommend — the promote/rollback/neutral decision
# ═════════════════════════════════════════════════════════════════════

class TestRecommend:
    def test_promote_on_clear_win(self):
        rec, why = _recommend(default_acc=0.70, per_rig_acc=0.85, n_windows=300)
        assert rec == "promote"
        assert "15.0pp" in why or "15" in why

    def test_rollback_when_worse_by_3pp(self):
        rec, why = _recommend(default_acc=0.85, per_rig_acc=0.80, n_windows=300)
        assert rec == "rollback"

    def test_neutral_within_noise(self):
        rec, _ = _recommend(default_acc=0.80, per_rig_acc=0.815, n_windows=300)
        assert rec == "neutral"

    def test_insufficient_data_even_when_accuracy_is_high(self):
        # Small sample: always insufficient, regardless of delta
        rec, why = _recommend(default_acc=0.50, per_rig_acc=0.95, n_windows=30)
        assert rec == "insufficient_data"
        assert "30" in why

    def test_boundary_at_exactly_2pp(self):
        # Exactly 2pp delta is still neutral (needs > 2pp to promote)
        rec, _ = _recommend(default_acc=0.80, per_rig_acc=0.82, n_windows=300)
        assert rec == "neutral"


# ═════════════════════════════════════════════════════════════════════
# 2. _softmax — numerical stability (subtract max trick)
# ═════════════════════════════════════════════════════════════════════

class TestSoftmax:
    def test_sums_to_one(self):
        logits = np.array([[1.0, 2.0, 3.0], [10.0, 10.0, 10.0]])
        probs = _softmax(logits, axis=-1)
        assert np.allclose(probs.sum(axis=-1), [1.0, 1.0])

    def test_handles_large_values_without_overflow(self):
        logits = np.array([[1000.0, 1001.0, 999.0]])
        probs = _softmax(logits, axis=-1)
        assert not np.any(np.isnan(probs))
        assert np.isclose(probs.sum(), 1.0)


# ═════════════════════════════════════════════════════════════════════
# 3. ModelResult.to_json — shape + rounding
# ═════════════════════════════════════════════════════════════════════

class TestModelResultJson:
    def test_rounds_floats_and_json_serializable(self):
        r = ModelResult(
            rig_slug="rig_a",
            source="per_rig",
            classifier_path="/models/a.onnx",
            n_samples=200,
            overall_accuracy=0.87123456789,
            per_class_accuracy={"NORMAL": 0.95123456},
            per_class_support={"NORMAL": 50},
            mean_confidence=0.82567891234,
            confusion={"NORMAL": {"NORMAL": 48, "BIAS": 2}},
        )
        j = r.to_json()
        # Round-trip through json to prove no numpy types leaked
        s = json.dumps(j)
        back = json.loads(s)
        assert back["overall_accuracy"] == 0.8712
        assert back["per_class_accuracy"]["NORMAL"] == 0.9512
        assert back["mean_confidence"] == 0.8257
        assert back["confusion"]["NORMAL"]["NORMAL"] == 48


# ═════════════════════════════════════════════════════════════════════
# 4. _evaluate — missing classifier / missing ONNX file path
# ═════════════════════════════════════════════════════════════════════

class TestEvaluateErrorPaths:
    def test_pair_without_classifier_returns_error(self, tmp_path):
        pair = ModelPair(rig_slug="rig_a", source="default",
                          classifier_path=None)  # no classifier at all
        X = np.zeros((10, 40, 7), dtype=np.float32)
        y = np.zeros(10, dtype=np.int64)
        r = _evaluate(pair, classes=["NORMAL"] * 7, X=X, y=y)
        assert r.error == "no classifier deployed"
        assert r.overall_accuracy == 0.0

    def test_broken_onnx_file_sets_error_does_not_crash(self, tmp_path):
        fake = tmp_path / "classifier.onnx"
        fake.write_bytes(b"not a valid onnx file")
        pair = ModelPair(rig_slug="rig_a", source="per_rig",
                          classifier_path=fake)
        X = np.zeros((5, 40, 7), dtype=np.float32)
        y = np.zeros(5, dtype=np.int64)
        r = _evaluate(pair, classes=["NORMAL"] * 7, X=X, y=y)
        assert r.error is not None
        assert "onnx load failed" in r.error


# ═════════════════════════════════════════════════════════════════════
# 5. compare_pairs — orchestration + recommendation wiring
# ═════════════════════════════════════════════════════════════════════

class TestComparePairsNoFineTuneYet:
    def test_registry_with_only_default_reports_no_fine_tune(self, tmp_path):
        # Minimal default setup — a fake onnx placeholder is fine because
        # the default_pair.has_classifier check only looks at existence.
        # We still need meta so evaluate tries to load. Since the onnx
        # is bytes-garbage, the default result will carry an error
        # field — we don't care, we only verify that per_rig=None and the
        # recommendation surfaces the "no fine-tune" state.
        (tmp_path / "classifier.onnx").write_bytes(b"placeholder")
        (tmp_path / "classifier_meta.json").write_text(json.dumps({
            "classes": ["NORMAL"] * 7, "X_mean": [0.0]*7, "X_std": [1.0]*7,
        }))
        reg = ModelRegistry(tmp_path)
        X = np.zeros((20, 40, 7), dtype=np.float32)
        y = np.zeros(20, dtype=np.int64)
        result = compare_pairs(reg, "some rig with no fine-tune",
                               classes=["NORMAL", "BIAS", "OSCILLATION",
                                         "DEADBAND_HUNTING", "SLUGGISH",
                                         "WINDUP", "CONDITION_CHANGE"],
                               X=X, y=y)
        assert result["has_per_rig"] is False
        assert result["per_rig"] is None
        assert result["recommendation"] == "no_fine_tune_yet"
        assert result["improvement"] == 0.0
        # 'default' must be populated even when onnx is fake (with error)
        assert result["default"] is not None
        assert "error" in result["default"]

    def test_result_is_json_serializable(self, tmp_path):
        reg = ModelRegistry(tmp_path)  # nothing deployed
        X = np.zeros((5, 40, 7), dtype=np.float32)
        y = np.zeros(5, dtype=np.int64)
        result = compare_pairs(reg, "anything",
                               classes=["NORMAL"] * 7,
                               X=X, y=y)
        json.dumps(result)  # must not raise


# ═════════════════════════════════════════════════════════════════════
# 6. End-to-end through the live deployed ONNX (if available)
# ═════════════════════════════════════════════════════════════════════

DEPLOY_CLS = Path(__file__).resolve().parent.parent / "models" / "classifier.onnx"


@pytest.mark.skipif(not DEPLOY_CLS.exists(),
                     reason="live classifier.onnx not deployed")
class TestLiveDefaultClassifier:
    """Smoke test against the actually-deployed sim classifier — proves the
    compare pipeline works with the real ONNX runtime, not just stubs.
    """

    def test_evaluate_on_random_windows_produces_predictions(self):
        reg = ModelRegistry()
        pair = reg.resolve(None)
        rng = np.random.default_rng(42)
        # Match the live classifier's input range (roughly normalised RPM etc.)
        X = rng.normal(loc=60.0, scale=10.0,
                       size=(30, 40, 7)).astype(np.float32)
        y = rng.integers(0, 7, size=30, dtype=np.int64)
        r = _evaluate(pair,
                      classes=["NORMAL", "BIAS", "OSCILLATION",
                                "DEADBAND_HUNTING", "SLUGGISH", "WINDUP",
                                "CONDITION_CHANGE"],
                      X=X, y=y)
        assert r.error is None
        assert 0.0 <= r.overall_accuracy <= 1.0
        assert 0.0 <= r.mean_confidence <= 1.0
        assert r.n_samples == 30
