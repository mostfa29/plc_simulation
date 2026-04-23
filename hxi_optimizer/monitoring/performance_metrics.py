"""Real-time PID performance monitoring at 2 Hz — per MASTER_CONTEXT §6.2.

Primary metric: DNIAE (Deadband-adjusted Normalized IAE).
Failure-mode classification: BIAS / OSCILLATION / DEADBAND_HUNTING / SLUGGISH.
Change-point detection: 3 parallel CUSUM detectors (mean, variance, oscillation),
require 2 consecutive alarms before flagging.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from itertools import groupby

import numpy as np

logger = logging.getLogger("performance")


@dataclass
class PerformanceMetrics:
    timestamp: float
    dniae: float = 0.0
    mean_error: float = 0.0
    std_variation_pct: float = 0.0
    rmse_rpm: float = 0.0
    failure_mode: str = "NORMAL"
    failure_confidence: float = 0.0
    sat_upper: float = 0.0
    sat_lower: float = 0.0
    sat_total: float = 0.0
    sat_asymmetry: float = 0.0
    is_windup: bool = False
    change_detected: bool = False
    change_reason: str = ""
    n_valid: int = 0
    stale_fraction: float = 0.0
    # ML autoencoder anomaly score (reconstruction error)
    anomaly_score: float = 0.0        # raw MSE between input and reconstruction
    anomaly_threshold: float = 0.0    # threshold learned at training time
    anomaly_detected: bool = False    # True if score > threshold


class CUSUMDetector:
    """Online CUSUM. h=5.0 at 2 Hz → ARL ~232s false alarm."""

    def __init__(self, k: float = 0.5, h: float = 5.0) -> None:
        self.k = k
        self.h = h
        self.S_pos = 0.0
        self.S_neg = 0.0
        self.baseline_mu = 0.0
        self.baseline_sigma = 1.0
        self.alarm_count = 0
        self.consecutive_alarms = 0

    def set_baseline(self, mu: float, sigma: float) -> None:
        self.baseline_mu = mu
        self.baseline_sigma = max(sigma, 1e-6)
        self.reset()

    def update(self, x: float) -> bool:
        z = (x - self.baseline_mu) / self.baseline_sigma
        self.S_pos = max(0.0, self.S_pos + z - self.k)
        self.S_neg = max(0.0, self.S_neg - z - self.k)
        alarming = self.S_pos > self.h or self.S_neg > self.h
        if alarming:
            self.consecutive_alarms += 1
            self.alarm_count += 1
        else:
            self.consecutive_alarms = 0
        return alarming

    def confirmed_change(self, required_consecutive: int = 2) -> bool:
        return self.consecutive_alarms >= required_consecutive

    def reset(self) -> None:
        self.S_pos = 0.0
        self.S_neg = 0.0
        self.consecutive_alarms = 0


class PerformanceMonitor:
    DEADBAND_DEFAULT_RPM = 2.0
    IIR_ALPHA = 0.200  # τ ≈ 2.24s at 2 Hz

    # Feature order expected by the trained ONNX classifier
    ML_FEATURES = [
        "rpm_encoder", "swash_output", "ss_setpoint_fwd",
        "active_lower", "active_upper", "delivered_torque", "loop_temp",
    ]

    def __init__(self, window_sec: float = 20.0, sample_rate: float = 2.0,
                 deadband_rpm: float = DEADBAND_DEFAULT_RPM,
                 classifier_path: str | None = None,
                 classifier_meta_path: str | None = None,
                 autoencoder_path: str | None = None,
                 autoencoder_meta_path: str | None = None) -> None:
        self.window_size = int(window_sec * sample_rate)  # 40 samples
        self.deadband = deadband_rpm
        # Core buffers used by the ACF / DNIAE / CUSUM path
        self.error_buffer = deque(maxlen=self.window_size)
        self.swash_buffer = deque(maxlen=self.window_size)
        self.rpm_buffer = deque(maxlen=self.window_size)
        self.stale_buffer = deque(maxlen=self.window_size)
        # Extra buffers the ML classifier needs — separate so legacy
        # callers of update() that don't pass torque/temp still work
        self.setpoint_buffer = deque(maxlen=self.window_size)
        self.lower_buffer = deque(maxlen=self.window_size)
        self.upper_buffer = deque(maxlen=self.window_size)
        self.torque_buffer = deque(maxlen=self.window_size)
        self.temp_buffer = deque(maxlen=self.window_size)
        self._median3 = deque(maxlen=3)
        self._iir_state: float | None = None
        self.cusum_mean = CUSUMDetector(k=0.5, h=5.0)
        self.cusum_var = CUSUMDetector(k=0.5, h=5.0)
        self.cusum_osc = CUSUMDetector(k=0.5, h=6.0)
        self._baseline_collected = False

        # Lock for hot-swapping models from the connection_monitor task
        # while update() may be running in the analysis executor
        import threading
        self._model_lock = threading.RLock()

        # Optional ONNX classifier (loaded lazily, falls back to ACF on any failure)
        self._classifier_session = None
        self._classifier_classes: list[str] | None = None
        self._classifier_mean: np.ndarray | None = None
        self._classifier_std: np.ndarray | None = None
        self._classifier_input_name = "input"
        self._classifier_inference_count = 0
        self._classifier_fail_count = 0
        self._classifier_source_path: str | None = None    # for diagnostics
        if classifier_path:
            self._try_load_classifier(classifier_path, classifier_meta_path)

        # Optional ONNX autoencoder for anomaly detection
        self._ae_session = None
        self._ae_input_name = "input"
        self._ae_min: np.ndarray | None = None
        self._ae_max: np.ndarray | None = None
        self._ae_threshold: float = 0.0
        self._ae_inference_count = 0
        self._ae_fail_count = 0
        self._ae_last_score = 0.0
        self._ae_consecutive_anomalies = 0
        self._ae_source_path: str | None = None             # for diagnostics
        if autoencoder_path:
            self._try_load_autoencoder(autoencoder_path, autoencoder_meta_path)

    def _try_load_classifier(self, model_path: str,
                              meta_path: str | None) -> None:
        """Load the ONNX classifier. Silent fallback to ACF on any failure."""
        from pathlib import Path
        if not Path(model_path).exists():
            logger.warning(f"Classifier not found at {model_path} — using ACF heuristic")
            return
        try:
            import onnxruntime as ort
        except ImportError:
            logger.warning("onnxruntime not installed — using ACF heuristic")
            return
        try:
            sess = ort.InferenceSession(
                model_path,
                providers=["CPUExecutionProvider"],
            )
            self._classifier_session = sess
            self._classifier_input_name = sess.get_inputs()[0].name
            self._classifier_source_path = str(model_path)
            if meta_path and Path(meta_path).exists():
                import json
                meta = json.loads(Path(meta_path).read_text())
                self._classifier_classes = meta.get("classes")
                if "X_mean" in meta and "X_std" in meta:
                    self._classifier_mean = np.array(meta["X_mean"], dtype=np.float32)
                    self._classifier_std = np.array(meta["X_std"], dtype=np.float32)
            logger.info(
                f"ML classifier loaded: {model_path} "
                f"({len(self._classifier_classes or [])} classes)"
            )
        except Exception as e:
            logger.warning(f"Classifier load failed: {e} — using ACF heuristic")
            self._classifier_session = None

    def _try_load_autoencoder(self, model_path: str,
                              meta_path: str | None) -> None:
        """Load the ONNX autoencoder for anomaly detection. Silent fallback on failure."""
        from pathlib import Path
        if not Path(model_path).exists():
            logger.info(f"Autoencoder not deployed at {model_path} — skipping anomaly detection")
            return
        try:
            import onnxruntime as ort
        except ImportError:
            logger.warning("onnxruntime not installed — autoencoder unavailable")
            return
        try:
            sess = ort.InferenceSession(
                model_path, providers=["CPUExecutionProvider"],
            )
            self._ae_session = sess
            self._ae_input_name = sess.get_inputs()[0].name
            self._ae_source_path = str(model_path)
            if meta_path and Path(meta_path).exists():
                import json
                meta = json.loads(Path(meta_path).read_text())
                if "X_min" in meta and "X_max" in meta:
                    self._ae_min = np.array(meta["X_min"], dtype=np.float32)
                    self._ae_max = np.array(meta["X_max"], dtype=np.float32)
                self._ae_threshold = float(meta.get("threshold", 0.0))
            logger.info(
                f"Autoencoder loaded: {model_path} "
                f"(threshold={self._ae_threshold:.6f})"
            )
        except Exception as e:
            logger.warning(f"Autoencoder load failed: {e}")
            self._ae_session = None

    def _compute_anomaly_score(self) -> tuple[float, bool] | None:
        """Run the autoencoder on the current window. Returns (score, detected) or None."""
        # Snapshot the session under the lock so a concurrent switch_models()
        # can't tear our inference. Read-side: cheap, contention-free in steady state.
        with self._model_lock:
            sess = self._ae_session
            input_name = self._ae_input_name
            ae_min = self._ae_min
            ae_max = self._ae_max
            threshold = self._ae_threshold
        if sess is None:
            return None
        try:
            n = self.window_size
            if len(self.rpm_buffer) < n:
                return None
            window = np.stack([
                np.array(self.rpm_buffer, dtype=np.float32)[-n:],
                np.array(self.swash_buffer, dtype=np.float32)[-n:],
                np.array(self.setpoint_buffer, dtype=np.float32)[-n:],
                np.array(self.lower_buffer, dtype=np.float32)[-n:],
                np.array(self.upper_buffer, dtype=np.float32)[-n:],
                np.array(self.torque_buffer, dtype=np.float32)[-n:],
                np.array(self.temp_buffer, dtype=np.float32)[-n:],
            ], axis=1)
            if ae_min is not None and ae_max is not None:
                denom = (ae_max - ae_min) + 1e-8
                window = np.clip((window - ae_min) / denom, 0.0, 1.0)
            inp = window[None, ...].astype(np.float32)
            recon = sess.run(None, {input_name: inp})[0]
            mse = float(np.mean((recon[0] - inp[0]) ** 2))
            self._ae_inference_count += 1
            self._ae_last_score = mse
            detected = mse > threshold
            if detected:
                self._ae_consecutive_anomalies += 1
            else:
                self._ae_consecutive_anomalies = 0
            return mse, detected
        except Exception as e:
            self._ae_fail_count += 1
            if self._ae_fail_count % 10 == 1:
                logger.warning(f"AE inference failed ({self._ae_fail_count}x): {e}")
            return None

    def switch_models(self,
                       classifier_path: str | None = None,
                       classifier_meta_path: str | None = None,
                       autoencoder_path: str | None = None,
                       autoencoder_meta_path: str | None = None,
                       label: str = "") -> dict:
        """Hot-swap the loaded ONNX models. Thread-safe.

        Designed to be called when the connected machine changes (or via the
        dashboard). The new sessions are loaded into temporary attributes
        first; only after both succeed (and any newly-required normalisation
        stats parse) do we swap them in under the lock. This guarantees that
        an in-flight `_classify_ml()` / `_compute_anomaly_score()` either
        sees the old pair completely or the new pair completely — never a
        torn mix.

        Returns a dict describing what changed (for diagnostics + the dashboard).
        """
        from pathlib import Path
        result = {
            "label": label,
            "classifier_loaded": False,
            "autoencoder_loaded": False,
            "classifier_source": self._classifier_source_path,
            "autoencoder_source": self._ae_source_path,
            "errors": [],
        }

        # ── Stage classifier ────────────────────────────────────────
        new_cls = None
        new_cls_input = "input"
        new_cls_classes = self._classifier_classes
        new_cls_mean = self._classifier_mean
        new_cls_std = self._classifier_std
        if classifier_path:
            try:
                import onnxruntime as ort
                if not Path(classifier_path).exists():
                    result["errors"].append(f"classifier missing: {classifier_path}")
                else:
                    new_cls = ort.InferenceSession(
                        classifier_path, providers=["CPUExecutionProvider"])
                    new_cls_input = new_cls.get_inputs()[0].name
                    if classifier_meta_path and Path(classifier_meta_path).exists():
                        import json
                        meta = json.loads(Path(classifier_meta_path).read_text())
                        new_cls_classes = meta.get("classes") or new_cls_classes
                        if "X_mean" in meta and "X_std" in meta:
                            new_cls_mean = np.array(meta["X_mean"], dtype=np.float32)
                            new_cls_std = np.array(meta["X_std"], dtype=np.float32)
            except Exception as e:
                result["errors"].append(f"classifier load: {e}")
                new_cls = None

        # ── Stage autoencoder ───────────────────────────────────────
        new_ae = None
        new_ae_input = "input"
        new_ae_min = self._ae_min
        new_ae_max = self._ae_max
        new_ae_threshold = self._ae_threshold
        if autoencoder_path:
            try:
                import onnxruntime as ort
                if not Path(autoencoder_path).exists():
                    result["errors"].append(f"AE missing: {autoencoder_path}")
                else:
                    new_ae = ort.InferenceSession(
                        autoencoder_path, providers=["CPUExecutionProvider"])
                    new_ae_input = new_ae.get_inputs()[0].name
                    if autoencoder_meta_path and Path(autoencoder_meta_path).exists():
                        import json
                        meta = json.loads(Path(autoencoder_meta_path).read_text())
                        if "X_min" in meta and "X_max" in meta:
                            new_ae_min = np.array(meta["X_min"], dtype=np.float32)
                            new_ae_max = np.array(meta["X_max"], dtype=np.float32)
                        new_ae_threshold = float(meta.get("threshold", new_ae_threshold))
            except Exception as e:
                result["errors"].append(f"AE load: {e}")
                new_ae = None

        # ── Atomic swap under the lock ──────────────────────────────
        with self._model_lock:
            if classifier_path and new_cls is not None:
                self._classifier_session = new_cls
                self._classifier_input_name = new_cls_input
                self._classifier_classes = new_cls_classes
                self._classifier_mean = new_cls_mean
                self._classifier_std = new_cls_std
                self._classifier_source_path = classifier_path
                self._classifier_inference_count = 0
                self._classifier_fail_count = 0
                result["classifier_loaded"] = True
                result["classifier_source"] = classifier_path
                logger.info(f"switch_models: classifier <- {classifier_path}"
                             + (f" ({label})" if label else ""))
            if autoencoder_path and new_ae is not None:
                self._ae_session = new_ae
                self._ae_input_name = new_ae_input
                self._ae_min = new_ae_min
                self._ae_max = new_ae_max
                self._ae_threshold = new_ae_threshold
                self._ae_source_path = autoencoder_path
                self._ae_inference_count = 0
                self._ae_fail_count = 0
                self._ae_consecutive_anomalies = 0
                result["autoencoder_loaded"] = True
                result["autoencoder_source"] = autoencoder_path
                logger.info(f"switch_models: autoencoder <- {autoencoder_path}"
                             + (f" ({label})" if label else ""))

        return result

    def loaded_models_info(self) -> dict:
        """Diagnostics for the dashboard — which models are currently loaded."""
        return {
            "classifier_source": self._classifier_source_path,
            "classifier_active": self._classifier_session is not None,
            "classifier_inferences": self._classifier_inference_count,
            "classifier_failures": self._classifier_fail_count,
            "autoencoder_source": self._ae_source_path,
            "autoencoder_active": self._ae_session is not None,
            "autoencoder_threshold": self._ae_threshold,
            "autoencoder_inferences": self._ae_inference_count,
            "autoencoder_failures": self._ae_fail_count,
        }

    def filter_rpm(self, raw_rpm: float) -> float:
        self._median3.append(raw_rpm)
        median_val = sorted(self._median3)[len(self._median3) // 2]
        if self._iir_state is None:
            self._iir_state = median_val
        else:
            self._iir_state = (self.IIR_ALPHA * median_val
                               + (1.0 - self.IIR_ALPHA) * self._iir_state)
        return self._iir_state

    def update(self, raw_rpm: float, setpoint: float, swash_output: int,
               lower: int, upper: int, stale: bool = False,
               delivered_torque: float = 0.0, loop_temp: float = 55.0) -> None:
        filtered = self.filter_rpm(raw_rpm)
        error = setpoint - filtered if not stale else 0.0
        self.rpm_buffer.append(filtered)
        self.error_buffer.append(error)
        self.swash_buffer.append(swash_output)
        self.stale_buffer.append(stale)
        # Full-feature buffers for the ML classifier (backward-compatible —
        # callers that don't pass torque/temp get defaults)
        self.setpoint_buffer.append(float(setpoint))
        self.lower_buffer.append(float(lower))
        self.upper_buffer.append(float(upper))
        self.torque_buffer.append(float(delivered_torque))
        self.temp_buffer.append(float(loop_temp))

    def compute_metrics(self, setpoint: float, lower: int, upper: int) -> PerformanceMetrics:
        errors = np.array(self.error_buffer, dtype=float)
        swash = np.array(self.swash_buffer, dtype=float)
        stale = np.array(self.stale_buffer, dtype=bool)
        n = len(errors)
        n_valid = int(np.sum(~stale))

        m = PerformanceMetrics(timestamp=time.time())
        m.n_valid = n_valid
        m.stale_fraction = 1.0 - n_valid / max(n, 1)

        # Spec requires ≥20 valid samples before producing metrics
        if n_valid < 20:
            m.failure_mode = "INSUFFICIENT_DATA"
            return m

        valid_errors = errors[~stale]

        abs_excess = np.maximum(0.0, np.abs(valid_errors) - self.deadband)
        m.dniae = float(np.mean(abs_excess) / max(setpoint, 1.0))
        m.mean_error = float(np.mean(valid_errors))
        m.rmse_rpm = float(np.sqrt(np.mean(valid_errors ** 2)))
        m.std_variation_pct = float(np.std(valid_errors) / max(setpoint, 1.0) * 100.0)

        # Saturation (counts ε for boundary tolerance)
        eps = 2
        if upper > lower:
            sat_up = int(np.sum(swash >= upper - eps))
            sat_lo = int(np.sum(swash <= lower + eps))
            m.sat_upper = sat_up / n
            m.sat_lower = sat_lo / n
            m.sat_total = m.sat_upper + m.sat_lower
            m.sat_asymmetry = (m.sat_upper - m.sat_lower) / max(m.sat_total, 1e-6)

        if m.sat_total > 0.30 and abs(m.mean_error) > self.deadband * 2:
            sign_consistent = (
                (m.mean_error > 0 and m.sat_upper > 0.25)
                or (m.mean_error < 0 and m.sat_lower > 0.25)
            )
            m.is_windup = sign_consistent

        m.failure_mode, m.failure_confidence = self._classify(valid_errors, setpoint)

        self._update_cusum(m)
        m.change_detected = (
            self.cusum_mean.confirmed_change(2)
            or self.cusum_var.confirmed_change(2)
            or self.cusum_osc.confirmed_change(2)
        )
        if m.change_detected:
            reasons = []
            if self.cusum_mean.confirmed_change(2): reasons.append("MEAN_SHIFT")
            if self.cusum_var.confirmed_change(2): reasons.append("VAR_SHIFT")
            if self.cusum_osc.confirmed_change(2): reasons.append("OSC_SHIFT")
            m.change_reason = "+".join(reasons)

        # ML autoencoder anomaly score (supplements CUSUM)
        ae = self._compute_anomaly_score()
        if ae is not None:
            score, detected = ae
            m.anomaly_score = score
            m.anomaly_threshold = self._ae_threshold
            # Require 3 consecutive anomaly windows (30 s at 0.1 Hz analysis)
            # before promoting to change_detected to match CUSUM's 2-consecutive gate
            if detected and self._ae_consecutive_anomalies >= 3:
                m.anomaly_detected = True
                if not m.change_detected:
                    m.change_detected = True
                    m.change_reason = (m.change_reason + "+AE_ANOMALY").strip("+") \
                        if m.change_reason else "AE_ANOMALY"
        return m

    def _classify(self, errors: np.ndarray, setpoint: float) -> tuple[str, float]:
        # Try the ONNX 1D-CNN first (98.97% test accuracy). On any failure
        # fall through to the ACF heuristic — the gate + tests depend on
        # _classify always returning a valid (label, confidence) tuple.
        if self._classifier_session is not None and len(errors) >= self.window_size:
            ml = self._classify_ml()
            if ml is not None:
                return ml
        return self._classify_acf(errors, setpoint)

    def _classify_ml(self) -> tuple[str, float] | None:
        """Run the trained ONNX classifier on the current window. None on failure."""
        # Snapshot under the lock so a concurrent switch_models() can't tear us
        with self._model_lock:
            sess = self._classifier_session
            input_name = self._classifier_input_name
            mean = self._classifier_mean
            std = self._classifier_std
            classes = self._classifier_classes
        if sess is None:
            return None
        try:
            n = self.window_size
            window = np.stack([
                np.array(self.rpm_buffer, dtype=np.float32)[-n:],
                np.array(self.swash_buffer, dtype=np.float32)[-n:],
                np.array(self.setpoint_buffer, dtype=np.float32)[-n:],
                np.array(self.lower_buffer, dtype=np.float32)[-n:],
                np.array(self.upper_buffer, dtype=np.float32)[-n:],
                np.array(self.torque_buffer, dtype=np.float32)[-n:],
                np.array(self.temp_buffer, dtype=np.float32)[-n:],
            ], axis=1)
            if window.shape != (n, 7):
                return None
            if mean is not None and std is not None:
                window = (window - mean) / (std + 1e-8)
            logits = sess.run(None, {input_name: window[None, ...].astype(np.float32)})[0]
            x = logits[0] - logits[0].max()
            probs = np.exp(x) / np.exp(x).sum()
            idx = int(np.argmax(probs))
            classes = classes or [
                "NORMAL", "BIAS", "OSCILLATION", "DEADBAND_HUNTING",
                "SLUGGISH", "WINDUP", "CONDITION_CHANGE",
            ]
            self._classifier_inference_count += 1
            return classes[idx], float(probs[idx])
        except Exception as e:
            self._classifier_fail_count += 1
            if self._classifier_fail_count % 10 == 1:
                logger.warning(f"ML classifier inference failed ({self._classifier_fail_count}x): {e}")
            return None

    def _classify_acf(self, errors: np.ndarray, setpoint: float) -> tuple[str, float]:
        """Original ACF zero-crossing heuristic (kept as fallback / baseline)."""
        if len(errors) < 8:
            return "INSUFFICIENT_DATA", 0.0
        mean_e = float(np.mean(errors))
        std_e = float(np.std(errors)) + 1e-6
        zero_crossings = int(np.sum(np.diff(np.sign(errors - mean_e)) != 0))
        bias_ratio = abs(mean_e) / std_e
        if bias_ratio > 2.0 and zero_crossings < 2:
            return "BIAS", min(1.0, bias_ratio / 4.0)

        acf = self._compute_acf(errors, max_lag=min(20, len(errors) // 2))
        zc_lags = self._acf_zero_crossings(acf)
        if len(zc_lags) >= 4:
            regularity = float(np.mean(zc_lags) / (np.std(zc_lags) + 1e-6))
            if regularity > 1.0:
                p2p = float(np.max(errors) - np.min(errors))
                if p2p < 1.5 * self.deadband:
                    return "DEADBAND_HUNTING", min(1.0, regularity / 2.0)
                return "OSCILLATION", min(1.0, regularity / 2.0)

        runs = sum(1 for _ in groupby(np.sign(errors)))
        if runs < 2:
            return "BIAS", 0.5
        if abs(mean_e) > self.deadband and zero_crossings < 3:
            return "SLUGGISH", 0.4
        return "NORMAL", 1.0

    @staticmethod
    def _compute_acf(x: np.ndarray, max_lag: int) -> np.ndarray:
        n = len(x)
        x_norm = x - np.mean(x)
        var = np.var(x_norm) + 1e-12
        return np.array([
            np.sum(x_norm[: n - k] * x_norm[k:]) / ((n - k) * var)
            for k in range(max_lag + 1)
        ])

    @staticmethod
    def _acf_zero_crossings(acf: np.ndarray) -> list:
        signs = np.sign(acf[1:])
        zc_indices = np.where(np.diff(signs) != 0)[0] + 1
        return list(np.diff(zc_indices)) if len(zc_indices) > 1 else []

    def _update_cusum(self, m: PerformanceMetrics) -> None:
        if not self._baseline_collected and m.n_valid >= self.window_size // 2:
            self.cusum_mean.set_baseline(m.mean_error, max(m.rmse_rpm, 0.5))
            self.cusum_var.set_baseline(np.log1p(m.rmse_rpm ** 2), 0.3)
            self.cusum_osc.set_baseline(0.0, 1.0)
            self._baseline_collected = True
        if self._baseline_collected:
            self.cusum_mean.update(m.mean_error)
            self.cusum_var.update(np.log1p(m.rmse_rpm ** 2))
            self.cusum_osc.update(1.0 if m.failure_mode == "OSCILLATION" else 0.0)

    def reset_for_new_condition(self) -> None:
        self._baseline_collected = False
        self.cusum_mean.reset()
        self.cusum_var.reset()
        self.cusum_osc.reset()
        logger.info("PerformanceMonitor: re-baselining after change-point")
