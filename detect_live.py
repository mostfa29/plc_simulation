#!/usr/bin/env python3
"""
Real-Time Fault Detection — InceptionTime Ensemble
=====================================================
Runs trained model inference on captured CSV data or live Modbus stream.

Two modes:
  1. Offline: Batch-process captured CSVs → predictions + alerts
  2. Live:   Poll Modbus → sliding window → ensemble inference → real-time alerts

Uses the same 12-channel feature engineering pipeline as training:
  Raw (6):     torque_ftlbs, rpm, pressure_psi, oil_temp_f, turns, hookload_klbs
  Derived (6): d_torque_dt, d_torque_dturns, torque_norm, turns_norm, phase, mask

Usage:
  # Offline: process all CSVs in a directory
  python detect_live.py offline --input ./live_captures --model ./model.pt --norm ./norm.json

  # Live: real-time Modbus polling + inference
  python detect_live.py live --host 10.0.0.1 --model ./model.pt --norm ./norm.json

  # Live with alerting thresholds
  python detect_live.py live --host 10.0.0.1 --model ./model.pt --norm ./norm.json \\
      --alert-consecutive 3 --alert-confidence 0.7
"""
import argparse
import csv
import json
import os
import signal
import struct
import socket
import sys
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F

# ── Import project modules (add parent to path) ──────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "phase1_pretraining"))

from phase1_pretraining.models import InceptionTimeEnsemble, InceptionTimeNetwork
from phase1_pretraining.features import (
    compute_derived_channels,
    NUM_RAW_CHANNELS, NUM_TOTAL_CHANNELS,
    CH_MASK,
)
from phase1_pretraining.dataset import NormParams


# ═══════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════

CLASS_NAMES = [
    "normal_makeup", "cross_thread", "galling", "stripped_thread",
    "over_torque", "under_torque", "wrong_compound", "misaligned_stab", "stall",
]

# Fault severity for alerting (0=info, 1=warning, 2=critical)
FAULT_SEVERITY = {
    0: 0,  # normal
    1: 2,  # cross_thread — critical
    2: 2,  # galling — critical
    3: 2,  # stripped_thread — critical
    4: 1,  # over_torque — warning
    5: 1,  # under_torque — warning
    6: 1,  # wrong_compound — warning
    7: 2,  # misaligned_stab — critical
    8: 2,  # stall — critical
}

SEVERITY_LABELS = {0: "OK", 1: "WARN", 2: "CRIT"}

# Raw columns expected in captured CSV files
RAW_COLUMNS = [
    'torque_ftlbs', 'rpm', 'pressure_psi',
    'oil_temp_f', 'turns', 'hookload_klbs',
]
STATE_COLUMN = 'connection_state'


# ═══════════════════════════════════════════════════════════════════
# Model Loader
# ═══════════════════════════════════════════════════════════════════

def load_model(model_path: str, device: str = "cpu",
               c_in: int = 12, c_out: int = 9, nf: int = 32,
               ensemble_size: int = 5) -> torch.nn.Module:
    """Load trained InceptionTime model (single or ensemble).

    Handles both:
      - Full ensemble checkpoint (InceptionTimeEnsemble)
      - Single member checkpoint (InceptionTimeNetwork)
    """
    checkpoint = torch.load(model_path, map_location=device, weights_only=False)

    # Detect if it's an ensemble or single model
    state_dict = checkpoint if isinstance(checkpoint, dict) and 'networks.0.block1.module1.bottleneck.weight' in checkpoint else None

    if state_dict is None and isinstance(checkpoint, dict) and 'model_state_dict' in checkpoint:
        state_dict = checkpoint['model_state_dict']

    if state_dict is None and isinstance(checkpoint, dict):
        # Try the checkpoint directly as state_dict
        state_dict = checkpoint

    # Check if ensemble keys exist
    is_ensemble = any(k.startswith('networks.') for k in state_dict.keys())

    if is_ensemble:
        model = InceptionTimeEnsemble(c_in=c_in, c_out=c_out, nf=nf,
                                       ensemble_size=ensemble_size)
        model.load_state_dict(state_dict, strict=False)
        print(f"  Loaded InceptionTime ENSEMBLE ({ensemble_size} members)")
    else:
        model = InceptionTimeNetwork(c_in=c_in, c_out=c_out, nf=nf)
        model.load_state_dict(state_dict, strict=False)
        print(f"  Loaded InceptionTime single network")

    model.to(device)
    model.eval()
    return model


def load_norm_params(norm_path: str) -> NormParams:
    """Load normalization parameters (mean/std per channel)."""
    norm = NormParams.load(norm_path)
    print(f"  Loaded normalization params: {norm_path}")
    print(f"    Mean shape: {norm.mean.shape}, Std shape: {norm.std.shape}")
    return norm


# ═══════════════════════════════════════════════════════════════════
# Feature Engineering (matches training pipeline exactly)
# ═══════════════════════════════════════════════════════════════════

def prepare_window(raw_data: np.ndarray, connection_state: np.ndarray,
                   target_torque: float, expected_turns: float,
                   norm: NormParams, dt: float = 0.01) -> np.ndarray:
    """Convert raw sensor data into normalized 12-channel window.

    Args:
        raw_data: [N, 6] array of raw channels
        connection_state: [N] int array
        target_torque: Target torque for normalization
        expected_turns: Expected turns for normalization
        norm: Normalization parameters from training

    Returns:
        [1, 12, N] tensor ready for model input
    """
    # Compute derived channels → [N, 12]
    features = compute_derived_channels(
        raw_data, connection_state, target_torque, expected_turns, dt=dt
    )

    # Normalize (same as training)
    normalized = (features - norm.mean) / norm.std
    # Preserve mask channel
    normalized[:, CH_MASK] = features[:, CH_MASK]

    return normalized.astype(np.float32)


# ═══════════════════════════════════════════════════════════════════
# Inference Engine
# ═══════════════════════════════════════════════════════════════════

class FaultDetector:
    """Runs InceptionTime inference and manages alert state."""

    def __init__(self, model: torch.nn.Module, norm: NormParams,
                 device: str = "cpu", window_size: int = 1000,
                 target_torque: float = 5000.0, expected_turns: float = 5.0,
                 alert_consecutive: int = 3, alert_confidence: float = 0.6):
        self.model = model
        self.norm = norm
        self.device = device
        self.window_size = window_size
        self.target_torque = target_torque
        self.expected_turns = expected_turns
        self.alert_consecutive = alert_consecutive
        self.alert_confidence = alert_confidence

        # Alert tracking
        self._fault_streak = 0
        self._last_fault_class = 0
        self._alert_active = False
        self.alerts: List[Dict] = []

    @torch.no_grad()
    def predict_window(self, features_12ch: np.ndarray
                       ) -> Tuple[int, float, np.ndarray]:
        """Run inference on a single [N, 12] feature window.

        Returns: (predicted_class, confidence, probabilities[9])
        """
        # Shape: [1, 12, window_size] (model expects channels-first)
        x = torch.from_numpy(features_12ch).T.unsqueeze(0).to(self.device)

        if hasattr(self.model, 'networks'):
            # Ensemble: average softmax across members
            probs = self.model(x)  # Already averaged softmax
        else:
            logits = self.model(x)
            probs = F.softmax(logits, dim=-1)

        probs_np = probs.cpu().numpy()[0]
        pred_class = int(np.argmax(probs_np))
        confidence = float(probs_np[pred_class])

        return pred_class, confidence, probs_np

    def process_window(self, raw_data: np.ndarray,
                       connection_state: np.ndarray,
                       timestamp: str = "") -> Dict:
        """Full pipeline: raw data → features → inference → alert check.

        Args:
            raw_data: [N, 6] raw sensor channels
            connection_state: [N] int array
            timestamp: ISO timestamp string

        Returns:
            Result dict with prediction, confidence, alert info
        """
        # Feature engineering
        features = prepare_window(
            raw_data, connection_state,
            self.target_torque, self.expected_turns, self.norm
        )

        # Inference
        pred_class, confidence, probs = self.predict_window(features)

        # Alert logic: N consecutive fault predictions above threshold
        is_fault = pred_class != 0 and confidence >= self.alert_confidence

        if is_fault:
            if pred_class == self._last_fault_class:
                self._fault_streak += 1
            else:
                self._fault_streak = 1
                self._last_fault_class = pred_class
        else:
            self._fault_streak = 0
            self._last_fault_class = 0

        triggered = (self._fault_streak >= self.alert_consecutive and
                     not self._alert_active)

        if triggered:
            self._alert_active = True
            alert = {
                "timestamp": timestamp,
                "fault_class": pred_class,
                "fault_name": CLASS_NAMES[pred_class],
                "confidence": confidence,
                "severity": FAULT_SEVERITY[pred_class],
                "severity_label": SEVERITY_LABELS[FAULT_SEVERITY[pred_class]],
                "consecutive": self._fault_streak,
            }
            self.alerts.append(alert)

        if not is_fault and self._alert_active:
            self._alert_active = False

        return {
            "timestamp": timestamp,
            "pred_class": pred_class,
            "pred_name": CLASS_NAMES[pred_class],
            "confidence": confidence,
            "probabilities": probs,
            "is_fault": is_fault,
            "streak": self._fault_streak,
            "alert_triggered": triggered,
            "severity": FAULT_SEVERITY[pred_class],
        }


# ═══════════════════════════════════════════════════════════════════
# Offline Mode — Batch Process Captured CSVs
# ═══════════════════════════════════════════════════════════════════

def run_offline(args):
    """Process captured CSV files and output predictions."""
    device = "cuda" if torch.cuda.is_available() and not args.cpu else "cpu"
    print(f"\n  Device: {device}")

    model = load_model(args.model, device, c_in=args.c_in, c_out=args.c_out,
                       nf=args.nf, ensemble_size=args.ensemble_size)
    norm = load_norm_params(args.norm)

    detector = FaultDetector(
        model, norm, device,
        window_size=args.window_size,
        target_torque=args.target_torque,
        expected_turns=args.expected_turns,
        alert_consecutive=args.alert_consecutive,
        alert_confidence=args.alert_confidence,
    )

    input_dir = Path(args.input)
    csv_files = sorted(input_dir.glob("*.csv"))
    if not csv_files:
        print(f"  ERROR: No CSV files found in {input_dir}")
        return

    print(f"\n  Processing {len(csv_files)} CSV files...\n")

    output_dir = Path(args.output) if args.output else input_dir / "predictions"
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []

    for fi, csv_path in enumerate(csv_files):
        print(f"  [{fi+1}/{len(csv_files)}] {csv_path.name}")

        try:
            df = pd.read_csv(csv_path)
        except Exception as e:
            print(f"    ERROR reading: {e}")
            continue

        # Check required columns
        missing = [c for c in RAW_COLUMNS + [STATE_COLUMN] if c not in df.columns]
        if missing:
            print(f"    Missing columns: {missing}, skipping")
            continue

        raw = df[RAW_COLUMNS].values.astype(np.float32)
        state = df[STATE_COLUMN].values.astype(np.int32)
        n = len(df)

        if n < 10:
            print(f"    Too few rows ({n}), skipping")
            continue

        # Get target_torque from CSV if available
        target_torque = args.target_torque
        if 'target_torque' in df.columns:
            tt = df['target_torque'].dropna()
            if len(tt) > 0:
                target_torque = float(tt.mode().iloc[0])

        detector.target_torque = target_torque

        # Sliding window inference
        window_size = min(args.window_size, n)
        stride = args.stride
        predictions = []

        start = 0
        while start + window_size <= n:
            window_raw = raw[start:start + window_size]
            window_state = state[start:start + window_size]

            ts = df['timestamp'].iloc[start] if 'timestamp' in df.columns else ""
            result = detector.process_window(window_raw, window_state, ts)
            result["window_start"] = start
            result["window_end"] = start + window_size
            predictions.append(result)

            if result["alert_triggered"]:
                sev = result["severity"]
                sev_label = SEVERITY_LABELS[sev]
                print(f"    ** {sev_label} ALERT: {result['pred_name']} "
                      f"({result['confidence']:.2f}) at row {start}")

            start += stride

        # Handle remainder (pad if needed)
        if start < n and n - start >= window_size // 2:
            # Pad the last partial window
            remainder = n - start
            padded_raw = np.zeros((window_size, NUM_RAW_CHANNELS), dtype=np.float32)
            padded_state = np.zeros(window_size, dtype=np.int32)
            padded_raw[:remainder] = raw[start:n]
            padded_state[:remainder] = state[start:n]

            ts = df['timestamp'].iloc[start] if 'timestamp' in df.columns else ""
            result = detector.process_window(padded_raw, padded_state, ts)
            result["window_start"] = start
            result["window_end"] = n
            predictions.append(result)

        # Write predictions CSV
        pred_path = output_dir / f"{csv_path.stem}_predictions.csv"
        with open(pred_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=[
                "window_start", "window_end", "timestamp",
                "pred_class", "pred_name", "confidence",
                "is_fault", "streak", "alert_triggered", "severity",
            ] + [f"prob_{CLASS_NAMES[i]}" for i in range(len(CLASS_NAMES))])
            writer.writeheader()
            for pred in predictions:
                row = {k: pred[k] for k in [
                    "window_start", "window_end", "timestamp",
                    "pred_class", "pred_name", "confidence",
                    "is_fault", "streak", "alert_triggered", "severity",
                ]}
                for i, name in enumerate(CLASS_NAMES):
                    row[f"prob_{name}"] = f"{pred['probabilities'][i]:.4f}"
                writer.writerow(row)

        # File summary
        fault_windows = sum(1 for p in predictions if p["is_fault"])
        dominant = max(set(p["pred_class"] for p in predictions),
                       key=lambda c: sum(1 for p in predictions if p["pred_class"] == c))
        summary_rows.append({
            "file": csv_path.name,
            "rows": n,
            "windows": len(predictions),
            "fault_windows": fault_windows,
            "dominant_class": CLASS_NAMES[dominant],
            "alerts": sum(1 for p in predictions if p["alert_triggered"]),
        })

        print(f"    {len(predictions)} windows, {fault_windows} fault, "
              f"dominant={CLASS_NAMES[dominant]}")

    # Summary
    print(f"\n{'='*60}")
    print(f"  OFFLINE DETECTION SUMMARY")
    print(f"{'='*60}")
    print(f"  Files processed:  {len(summary_rows)}")
    print(f"  Total alerts:     {len(detector.alerts)}")

    if detector.alerts:
        print(f"\n  Alert Details:")
        for a in detector.alerts:
            print(f"    [{a['severity_label']}] {a['fault_name']} "
                  f"({a['confidence']:.2f}) at {a['timestamp']}")

    # Write summary
    summary_path = output_dir / "detection_summary.csv"
    if summary_rows:
        pd.DataFrame(summary_rows).to_csv(summary_path, index=False)
        print(f"\n  Summary written to: {summary_path}")
    print(f"  Predictions dir:   {output_dir}")


# ═══════════════════════════════════════════════════════════════════
# Live Mode — Real-Time Modbus Polling + Inference
# ═══════════════════════════════════════════════════════════════════

# Import the Modbus client from capture_live
from capture_live import (
    ModbusTCPClient, DEFAULT_REGISTER_MAP, decode_registers,
    CONNECTION_STATE_NAMES, format_fault_code,
)


class SlidingWindowBuffer:
    """Maintains a rolling buffer of raw sensor readings for windowed inference."""

    def __init__(self, window_size: int, num_raw_channels: int = 6):
        self.window_size = window_size
        self.num_raw_channels = num_raw_channels
        self.raw_buffer = np.zeros((window_size, num_raw_channels), dtype=np.float32)
        self.state_buffer = np.zeros(window_size, dtype=np.int32)
        self.count = 0

    def push(self, raw_values: np.ndarray, connection_state: int):
        """Add a single sample to the buffer (FIFO, rolls when full)."""
        if self.count < self.window_size:
            self.raw_buffer[self.count] = raw_values
            self.state_buffer[self.count] = connection_state
            self.count += 1
        else:
            # Roll buffer left by 1, append new at end
            self.raw_buffer[:-1] = self.raw_buffer[1:]
            self.state_buffer[:-1] = self.state_buffer[1:]
            self.raw_buffer[-1] = raw_values
            self.state_buffer[-1] = connection_state

    def ready(self) -> bool:
        """Buffer has enough data for inference."""
        return self.count >= self.window_size

    def get_window(self) -> Tuple[np.ndarray, np.ndarray]:
        """Return current window data."""
        return self.raw_buffer.copy(), self.state_buffer.copy()

    def reset(self):
        self.raw_buffer[:] = 0
        self.state_buffer[:] = 0
        self.count = 0


def run_live(args):
    """Real-time Modbus polling with sliding window inference."""
    device = "cuda" if torch.cuda.is_available() and not args.cpu else "cpu"
    print(f"\n  Device: {device}")

    model = load_model(args.model, device, c_in=args.c_in, c_out=args.c_out,
                       nf=args.nf, ensemble_size=args.ensemble_size)
    norm = load_norm_params(args.norm)

    detector = FaultDetector(
        model, norm, device,
        window_size=args.window_size,
        target_torque=args.target_torque,
        expected_turns=args.expected_turns,
        alert_consecutive=args.alert_consecutive,
        alert_confidence=args.alert_confidence,
    )

    # Load register map
    if args.register_map:
        with open(args.register_map) as f:
            reg_map = json.load(f)
    else:
        reg_map = DEFAULT_REGISTER_MAP

    # Compute block read range
    max_addr = max(
        info["addr"] + (2 if info["type"] == "FLOAT32" else 1)
        for info in reg_map.values()
    )
    reg_start = min(info["addr"] for info in reg_map.values())
    reg_count = max_addr - reg_start

    # Setup
    client = ModbusTCPClient(args.host, args.port, timeout=args.timeout)
    buffer = SlidingWindowBuffer(args.window_size, NUM_RAW_CHANNELS)

    poll_interval = 1.0 / args.hz
    inference_interval = args.inference_every  # Run inference every N samples
    running = True
    poll_count = 0
    inference_count = 0
    t_start = time.time()

    # Optional CSV logging
    log_writer = None
    log_file = None
    if args.log_output:
        log_dir = Path(args.log_output)
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"detect_live_{ts}.csv"
        log_file = open(log_path, 'w', newline='', buffering=1)
        log_writer = csv.writer(log_file)
        log_writer.writerow([
            "timestamp", "elapsed_s", "pred_class", "pred_name",
            "confidence", "is_fault", "streak", "alert_triggered",
        ] + [f"prob_{name}" for name in CLASS_NAMES])
        print(f"  Logging predictions to: {log_path}")

    def signal_handler(sig, frame):
        nonlocal running
        print(f"\n\n  Stopping live detection (signal {sig})...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print(f"\n  Poll rate: {args.hz} Hz")
    print(f"  Inference every: {inference_interval} samples "
          f"({inference_interval/args.hz:.1f}s)")
    print(f"  Window size: {args.window_size} samples "
          f"({args.window_size/args.hz:.1f}s)")
    print(f"  Alert threshold: {args.alert_consecutive} consecutive @ "
          f"{args.alert_confidence:.0%} confidence")
    print(f"\n  Connecting to {args.host}:{args.port}...")
    print(f"  Filling buffer ({args.window_size} samples)...\n")

    reconnect_delay = 1.0
    last_result = None

    while running:
        # Connect / reconnect
        if not client.connected:
            if client.connect():
                print(f"  [CONN] Connected to {args.host}:{args.port}")
                reconnect_delay = 1.0
            else:
                wait_until = time.time() + reconnect_delay
                while running and time.time() < wait_until:
                    time.sleep(0.5)
                reconnect_delay = min(reconnect_delay * 2, 30.0)
                continue

        # Poll
        t_poll = time.time()
        raw_regs = client.read_holding_registers(reg_start, reg_count)

        if raw_regs is None:
            client.close()
            continue

        poll_count += 1
        elapsed = time.time() - t_start
        now_str = datetime.now(timezone.utc).isoformat(timespec='milliseconds')

        # Decode registers
        values = decode_registers(raw_regs, reg_start, reg_map)

        # Extract raw channels for buffer (order must match training pipeline)
        raw_sample = np.array([
            values.get("torque_ftlbs", 0),
            values.get("rpm", 0),
            values.get("pressure_psi", 0),
            values.get("oil_temp_f", 0),
            values.get("turns", 0),
            values.get("hookload_klbs", 0),
        ], dtype=np.float32)

        conn_state = int(values.get("connection_state", 0))
        buffer.push(raw_sample, conn_state)

        # Run inference at specified interval
        if buffer.ready() and poll_count % inference_interval == 0:
            window_raw, window_state = buffer.get_window()
            result = detector.process_window(window_raw, window_state, now_str)
            inference_count += 1
            last_result = result

            # Alert handling
            if result["alert_triggered"]:
                sev = result["severity"]
                sev_label = SEVERITY_LABELS[sev]
                fault_name = result["pred_name"]
                conf = result["confidence"]
                print(f"\n  {'!'*50}")
                print(f"  !! {sev_label} ALERT: {fault_name} "
                      f"({conf:.1%} confidence, "
                      f"{result['streak']} consecutive)")
                print(f"  !! Time: {now_str}")
                print(f"  {'!'*50}\n")

            # Log to CSV
            if log_writer:
                row = [
                    now_str, f"{elapsed:.2f}",
                    result["pred_class"], result["pred_name"],
                    f"{result['confidence']:.4f}",
                    result["is_fault"], result["streak"],
                    result["alert_triggered"],
                ] + [f"{p:.4f}" for p in result["probabilities"]]
                log_writer.writerow(row)

        # Live display (throttled to ~2 Hz)
        if poll_count % max(1, int(args.hz / 2)) == 0:
            _display_live_detection(
                values, elapsed, poll_count / max(elapsed, 0.001),
                buffer.count, args.window_size,
                last_result, inference_count,
            )

        # Sleep to maintain poll rate
        sleep_time = poll_interval - (time.time() - t_poll)
        if sleep_time > 0:
            time.sleep(sleep_time)

    # Cleanup
    client.close()
    if log_file:
        log_file.close()

    elapsed = time.time() - t_start
    print(f"\n  Live detection complete:")
    print(f"    Duration:    {elapsed:.1f}s")
    print(f"    Polls:       {poll_count}")
    print(f"    Inferences:  {inference_count}")
    print(f"    Alerts:      {len(detector.alerts)}")


def _display_live_detection(values: Dict, elapsed: float, hz: float,
                             buf_count: int, buf_size: int,
                             last_result: Optional[Dict],
                             inference_count: int):
    """Print live detection dashboard."""
    state = int(values.get("connection_state", 0))
    state_name = CONNECTION_STATE_NAMES.get(state, f"UNK({state})")
    fc = int(values.get("fault_code", 0))

    buf_pct = (buf_count / buf_size * 100) if buf_size > 0 else 0

    # Prediction info
    if last_result:
        pred_str = (f"{last_result['pred_name']:15s} "
                    f"({last_result['confidence']:.1%})")
        streak = last_result['streak']
        sev = last_result['severity']
        sev_label = SEVERITY_LABELS[sev]
    else:
        pred_str = "waiting..."
        streak = 0
        sev_label = "--"

    lines = [
        f"  [{elapsed:8.1f}s] {hz:5.1f} Hz | Buf: {buf_pct:5.1f}% | "
        f"Inferences: {inference_count} | State: {state_name}",
        f"    Torque: {values.get('torque_ftlbs', 0):8.1f} | "
        f"RPM: {values.get('rpm', 0):6.1f} | "
        f"Pressure: {values.get('pressure_psi', 0):7.1f} | "
        f"Fault: {format_fault_code(fc)}",
        f"    Prediction: {pred_str} | "
        f"Streak: {streak} | Severity: {sev_label}",
    ]

    sys.stdout.write(f"\033[3A" if elapsed > 1.0 else "")
    for line in lines:
        sys.stdout.write(f"\033[K{line}\n")
    sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Real-Time Fault Detection — InceptionTime Ensemble",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    # ── Shared arguments ──────────────────────────────────────────
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--model", required=True,
                        help="Path to trained model checkpoint (.pt)")
    shared.add_argument("--norm", required=True,
                        help="Path to normalization params (.json)")
    shared.add_argument("--window-size", type=int, default=1000,
                        help="Inference window size (default: 1000)")
    shared.add_argument("--target-torque", type=float, default=5000.0,
                        help="Target torque for normalization (default: 5000)")
    shared.add_argument("--expected-turns", type=float, default=5.0,
                        help="Expected turns for normalization (default: 5.0)")
    shared.add_argument("--alert-consecutive", type=int, default=3,
                        help="Consecutive fault windows before alert (default: 3)")
    shared.add_argument("--alert-confidence", type=float, default=0.6,
                        help="Minimum confidence for fault (default: 0.6)")
    shared.add_argument("--c-in", type=int, default=12,
                        help="Model input channels (default: 12)")
    shared.add_argument("--c-out", type=int, default=9,
                        help="Model output classes (default: 9)")
    shared.add_argument("--nf", type=int, default=32,
                        help="InceptionTime filter count (default: 32)")
    shared.add_argument("--ensemble-size", type=int, default=5,
                        help="Ensemble member count (default: 5)")
    shared.add_argument("--cpu", action="store_true",
                        help="Force CPU inference")

    # ── Offline subcommand ────────────────────────────────────────
    p_offline = subparsers.add_parser("offline", parents=[shared],
                                       help="Batch process captured CSV files")
    p_offline.add_argument("--input", required=True,
                           help="Directory containing captured CSV files")
    p_offline.add_argument("--output", default=None,
                           help="Output directory for predictions")
    p_offline.add_argument("--stride", type=int, default=250,
                           help="Window stride for sliding inference (default: 250)")

    # ── Live subcommand ───────────────────────────────────────────
    p_live = subparsers.add_parser("live", parents=[shared],
                                    help="Real-time Modbus polling + inference")
    p_live.add_argument("--host", required=True,
                        help="PLC/eWon IP address")
    p_live.add_argument("--port", type=int, default=502,
                        help="Modbus TCP port (default: 502)")
    p_live.add_argument("--hz", type=float, default=10.0,
                        help="Poll rate in Hz (default: 10)")
    p_live.add_argument("--timeout", type=float, default=5.0,
                        help="Socket timeout (default: 5s)")
    p_live.add_argument("--inference-every", type=int, default=10,
                        help="Run inference every N samples (default: 10)")
    p_live.add_argument("--register-map", default=None,
                        help="Custom register map JSON")
    p_live.add_argument("--log-output", default=None,
                        help="Directory to log predictions CSV")

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  Fault Detection — InceptionTime Ensemble")
    print("=" * 60)
    print(f"  Mode: {args.mode.upper()}")

    if args.mode == "offline":
        run_offline(args)
    elif args.mode == "live":
        run_live(args)


if __name__ == "__main__":
    main()
