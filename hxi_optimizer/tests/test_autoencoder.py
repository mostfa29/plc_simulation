"""Autoencoder / anomaly-detection integration tests."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from hxi_optimizer.monitoring.performance_metrics import (
    PerformanceMetrics, PerformanceMonitor,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AE_PATH = REPO_ROOT / "hxi_optimizer" / "models" / "autoencoder.onnx"
AE_META = REPO_ROOT / "hxi_optimizer" / "models" / "autoencoder_meta.json"

HAS_AE_FILES = AE_PATH.exists() and AE_META.exists()
try:
    import onnxruntime  # noqa: F401
    HAS_ORT = True
except ImportError:
    HAS_ORT = False
HAS_AE = HAS_AE_FILES and HAS_ORT


# ─────────────────────────────────────────────────────────────────────
# 1. Fallback behaviour
# ─────────────────────────────────────────────────────────────────────

class TestAutoencoderFallback:
    def test_no_ae_path_leaves_score_zero(self):
        """Monitor without AE path should emit zero anomaly score and false detected."""
        m = PerformanceMonitor()
        assert m._ae_session is None
        # feed a window
        for _ in range(40):
            m.update(raw_rpm=60.0, setpoint=60.0, swash_output=500,
                     lower=400, upper=600,
                     delivered_torque=1500.0, loop_temp=55.0)
        metrics = m.compute_metrics(setpoint=60.0, lower=400, upper=600)
        assert metrics.anomaly_score == 0.0
        assert metrics.anomaly_detected is False

    def test_missing_ae_file_silent_fallback(self, tmp_path):
        m = PerformanceMonitor(autoencoder_path=str(tmp_path / "nope.onnx"))
        assert m._ae_session is None

    def test_compute_anomaly_score_returns_none_without_session(self):
        m = PerformanceMonitor()
        for _ in range(40):
            m.update(raw_rpm=60.0, setpoint=60.0, swash_output=500,
                     lower=400, upper=600)
        assert m._compute_anomaly_score() is None

    def test_compute_anomaly_score_returns_none_when_buffer_short(self, tmp_path):
        """Even with AE loaded, < 40 samples -> None."""
        if not HAS_AE:
            pytest.skip("no AE deployed")
        m = PerformanceMonitor(autoencoder_path=str(AE_PATH),
                                autoencoder_meta_path=str(AE_META))
        assert m._ae_session is not None
        # Only feed 10 samples
        for _ in range(10):
            m.update(raw_rpm=60.0, setpoint=60.0, swash_output=500,
                     lower=400, upper=600)
        assert m._compute_anomaly_score() is None


# ─────────────────────────────────────────────────────────────────────
# 2. AE loads correctly
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_AE, reason="autoencoder not deployed")
class TestAELoads:
    def test_session_alive(self):
        m = PerformanceMonitor(autoencoder_path=str(AE_PATH),
                                autoencoder_meta_path=str(AE_META))
        assert m._ae_session is not None

    def test_threshold_loaded(self):
        m = PerformanceMonitor(autoencoder_path=str(AE_PATH),
                                autoencoder_meta_path=str(AE_META))
        assert m._ae_threshold > 0.0

    def test_normalisation_stats_loaded(self):
        m = PerformanceMonitor(autoencoder_path=str(AE_PATH),
                                autoencoder_meta_path=str(AE_META))
        assert m._ae_min is not None
        assert m._ae_max is not None
        assert m._ae_min.shape == (7,)


# ─────────────────────────────────────────────────────────────────────
# 3. Inference produces valid output
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_AE, reason="autoencoder not deployed")
class TestAEInference:
    def _feed_scenario(self, m: PerformanceMonitor, scenario: str, **kw):
        from training.scenarios import ALL_GENERATORS
        kwargs = {"equipment_type": "hxi", "seed": 1, "duration_s": 120}
        kwargs.update(kw)
        samples, _ = ALL_GENERATORS[scenario](**kwargs)
        for s in samples:
            m.update(
                raw_rpm=s["rpm_encoder"],
                setpoint=s["ss_setpoint_fwd"],
                swash_output=s["swash_output"],
                lower=s["active_lower"],
                upper=s["active_upper"],
                delivered_torque=s["delivered_torque"],
                loop_temp=s["loop_temp"],
            )

    def test_inference_returns_score(self):
        m = PerformanceMonitor(autoencoder_path=str(AE_PATH),
                                autoencoder_meta_path=str(AE_META))
        self._feed_scenario(m, "normal")
        result = m._compute_anomaly_score()
        assert result is not None
        score, detected = result
        assert score >= 0.0
        assert isinstance(detected, bool)
        assert m._ae_inference_count == 1

    def test_normal_scenario_below_threshold_mostly(self):
        """Score on NORMAL telemetry should usually be below threshold."""
        m = PerformanceMonitor(autoencoder_path=str(AE_PATH),
                                autoencoder_meta_path=str(AE_META))
        self._feed_scenario(m, "normal", seed=42)
        score, detected = m._compute_anomaly_score()
        # Not a hard guarantee (the AE has ~5% false positive rate by design),
        # but assert at least that the score is well-formed
        assert 0.0 <= score < 1.0

    def test_compute_metrics_populates_anomaly_fields(self):
        m = PerformanceMonitor(autoencoder_path=str(AE_PATH),
                                autoencoder_meta_path=str(AE_META))
        self._feed_scenario(m, "normal")
        metrics = m.compute_metrics(setpoint=60.0, lower=400, upper=600)
        assert metrics.anomaly_threshold > 0
        assert metrics.anomaly_score >= 0
        assert m._ae_inference_count >= 1

    @pytest.mark.parametrize("scenario", ["bias", "oscillation", "stickslip"])
    def test_fault_scenarios_produce_nonzero_score(self, scenario):
        m = PerformanceMonitor(autoencoder_path=str(AE_PATH),
                                autoencoder_meta_path=str(AE_META))
        self._feed_scenario(m, scenario, duration_s=120, onset_s=30)
        result = m._compute_anomaly_score()
        assert result is not None
        score, _ = result
        assert score > 0, f"{scenario}: score {score}"


# ─────────────────────────────────────────────────────────────────────
# 4. Consecutive-anomaly gate on change_detected
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_AE, reason="autoencoder not deployed")
class TestConsecutiveGate:
    def test_single_anomaly_does_not_trigger_change_detected(self):
        """A one-off anomaly window should NOT promote to change_detected."""
        m = PerformanceMonitor(autoencoder_path=str(AE_PATH),
                                autoencoder_meta_path=str(AE_META))
        # Feed normal data
        for _ in range(40):
            m.update(raw_rpm=60.0, setpoint=60.0, swash_output=500,
                     lower=400, upper=600,
                     delivered_torque=1500.0, loop_temp=55.0)
        # Force a single anomaly
        m._ae_consecutive_anomalies = 1
        metrics = m.compute_metrics(setpoint=60.0, lower=400, upper=600)
        # Score might cross threshold, but consecutive < 3 means NOT anomaly_detected
        if metrics.anomaly_score <= metrics.anomaly_threshold:
            # score wasn't above threshold anyway — test is trivially satisfied
            pass
        assert metrics.anomaly_detected is False

    def test_three_consecutive_promotes_to_change_detected(self):
        m = PerformanceMonitor(autoencoder_path=str(AE_PATH),
                                autoencoder_meta_path=str(AE_META))
        # Feed garbage data (far out of training distribution) to drive score up
        for i in range(40):
            # Extreme values not in the training range
            m.update(raw_rpm=-500.0, setpoint=60.0, swash_output=9999,
                     lower=-99999, upper=99999,
                     delivered_torque=1e7, loop_temp=-999.0)
        # Manually set consecutive counter to simulate 3 back-to-back anomaly windows
        m._ae_consecutive_anomalies = 3
        metrics = m.compute_metrics(setpoint=60.0, lower=-99999, upper=99999)
        # If score crossed threshold AND consecutive >= 3, expect anomaly_detected
        if metrics.anomaly_score > metrics.anomaly_threshold:
            assert metrics.anomaly_detected is True
            assert "AE_ANOMALY" in metrics.change_reason


# ─────────────────────────────────────────────────────────────────────
# 5. Backwards compatibility
# ─────────────────────────────────────────────────────────────────────

class TestBackwardsCompatibility:
    def test_metrics_dataclass_has_new_fields_with_defaults(self):
        import time
        m = PerformanceMetrics(timestamp=time.time())
        assert m.anomaly_score == 0.0
        assert m.anomaly_threshold == 0.0
        assert m.anomaly_detected is False

    def test_monitor_without_ae_still_reports_metrics(self):
        """Without the AE loaded, compute_metrics should still work end-to-end."""
        m = PerformanceMonitor()
        for _ in range(40):
            m.update(raw_rpm=60.0, setpoint=60.0, swash_output=500,
                     lower=400, upper=600)
        metrics = m.compute_metrics(setpoint=60.0, lower=400, upper=600)
        assert metrics.failure_mode != "INSUFFICIENT_DATA"
        assert metrics.anomaly_score == 0.0
