"""Real-time labelled-episode capture for model fine-tuning.

Each "episode" is a bounded window of telemetry tagged with:
  - Machine identity (ewon_name, equipment_type)
  - Depth / operating conditions at capture time
  - Label from auto-segmentation OR operator annotation
  - Confidence metadata

Produces:
  dataset/<ewon_slug>/<label>/episode_<timestamp>.npz
  dataset/<ewon_slug>/index.jsonl       (one line per episode)

The index file makes it easy to bundle a dataset later:
  - Per-machine filtering
  - Label distribution analysis
  - Train/val/test splits

Used by training/auto_pipeline.py --real-data to ship these back to the
GPU box and fine-tune the simulation-trained model on real telemetry.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("realtime_dataset")

EPISODE_WINDOW_S = 20.0
SAMPLES_AT_2HZ = 40


# ──────────────────────────────────────────────────────────────────────
# Episode metadata
# ──────────────────────────────────────────────────────────────────────
@dataclass
class EpisodeMeta:
    ts_start: float
    ts_end: float
    label: str                       # NORMAL / BIAS / OSCILLATION / CONNECTION / ...
    source: str                      # auto_segment | operator | classifier
    ewon_name: str
    equipment_type: str
    n_samples: int
    file_path: str
    setpoint_mean: float = 0.0
    rpm_mean: float = 0.0
    dniae: Optional[float] = None
    notes: str = ""

    def to_json(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────
# Capture engine
# ──────────────────────────────────────────────────────────────────────
class RealtimeDatasetCapture:
    """Writes labelled-episode .npz files + index.jsonl for fine-tuning.

    Call feed() from the read_loop at 2 Hz. Periodically checks whether a
    connection event happened (RPM dropped to ~0 then rose), auto-segments
    the prior window, and writes it as a CONNECTION episode. Operator can
    also manually trigger an episode capture with `capture_now(label)`.
    """

    def __init__(self, dataset_dir: Path, ewon_name: str | None,
                 equipment_type: str | None,
                 auto_segment_connections: bool = True) -> None:
        self.dataset_dir = Path(dataset_dir)
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        self.ewon_name = ewon_name or "unknown_machine"
        self.equipment_type = equipment_type or "unknown"
        self.auto_segment = auto_segment_connections

        # Rolling buffer — 40 samples at 2 Hz
        self._buf: deque[dict] = deque(maxlen=SAMPLES_AT_2HZ * 3)  # 60 s
        self._conn_state = "running"   # running | connecting
        self._dip_start_ts = 0.0      # when RPM first dropped below threshold
        self._dip_last_ts = 0.0       # last timestamp seen while dipped
        self._episodes_written = 0

    def update_machine(self, ewon_name: str, equipment_type: str) -> None:
        """Call when the connected machine changes — future episodes are tagged to it."""
        self.ewon_name = ewon_name or self.ewon_name
        self.equipment_type = equipment_type or self.equipment_type

    # ── Feeding telemetry ───────────────────────────────────────────

    def feed(self, sample: dict) -> None:
        """Ingest one 2 Hz telemetry sample."""
        if sample.get("stale"):
            return
        self._buf.append(sample)

        if not self.auto_segment:
            return

        # Connection-event detection: RPM dropped below 2 for at least 5 s,
        # then rose back above 10 — then it counts as a connection episode.
        rpm = float(sample.get("rpm_encoder", 0.0) or 0.0)
        now = sample.get("ts", time.time())
        if self._conn_state == "running" and rpm < 2.0:
            # Dip started
            self._conn_state = "connecting"
            self._dip_start_ts = now
            self._dip_last_ts = now
        elif self._conn_state == "connecting":
            if rpm < 2.0:
                # Still dipped — extend the dip window
                self._dip_last_ts = now
            else:
                # RPM rose. Did the dip last long enough to count?
                dip_duration = self._dip_last_ts - self._dip_start_ts
                if rpm > 10.0 and dip_duration >= 5.0:
                    # Real connection event — write episode
                    self._write_connection_episode(self._dip_start_ts, now)
                self._conn_state = "running"
            if now - self._dip_start_ts > 120.0:
                # Abandon very long "dips" (likely not a connection)
                self._conn_state = "running"

    # ── Explicit operator annotation ─────────────────────────────────

    def capture_now(self, label: str, notes: str = "",
                    lookback_s: float = 20.0) -> Optional[EpisodeMeta]:
        """Write the last `lookback_s` of telemetry as a labelled episode."""
        if not self._buf:
            return None
        end_ts = self._buf[-1].get("ts", time.time())
        start_ts = end_ts - lookback_s
        samples = [s for s in self._buf if s.get("ts", 0.0) >= start_ts]
        if len(samples) < 10:
            return None
        return self._write_episode(samples, label, source="operator",
                                    notes=notes)

    # ── Internal ─────────────────────────────────────────────────────

    def _write_connection_episode(self, start_ts: float, end_ts: float) -> None:
        samples = [s for s in self._buf
                   if start_ts - 10.0 <= s.get("ts", 0.0) <= end_ts + 5.0]
        if len(samples) < 10:
            return
        self._write_episode(samples, "CONNECTION", source="auto_segment")

    def _write_episode(self, samples: list[dict], label: str,
                       source: str, notes: str = "") -> Optional[EpisodeMeta]:
        try:
            feature_cols = ["rpm_encoder", "swash_output", "ss_setpoint_fwd",
                            "active_lower", "active_upper",
                            "delivered_torque", "loop_temp"]
            arr = np.zeros((len(samples), len(feature_cols)), dtype=np.float32)
            for i, s in enumerate(samples):
                for j, col in enumerate(feature_cols):
                    v = s.get(col, 0.0)
                    arr[i, j] = float(v) if v is not None else 0.0

            ts_start = samples[0].get("ts", time.time())
            ts_end = samples[-1].get("ts", time.time())
            slug = _slug(self.ewon_name)
            label_dir = self.dataset_dir / slug / label
            label_dir.mkdir(parents=True, exist_ok=True)
            file_path = label_dir / f"episode_{int(ts_start)}.npz"

            np.savez_compressed(
                file_path,
                features=arr,
                feature_names=np.array(feature_cols),
                ts_start=np.float64(ts_start),
                ts_end=np.float64(ts_end),
                label=np.array([label]),
                ewon_name=np.array([self.ewon_name]),
                equipment_type=np.array([self.equipment_type]),
            )

            rpm_col = feature_cols.index("rpm_encoder")
            sp_col = feature_cols.index("ss_setpoint_fwd")
            meta = EpisodeMeta(
                ts_start=ts_start,
                ts_end=ts_end,
                label=label,
                source=source,
                ewon_name=self.ewon_name,
                equipment_type=self.equipment_type,
                n_samples=len(samples),
                file_path=str(file_path.relative_to(self.dataset_dir)),
                setpoint_mean=float(np.mean(arr[:, sp_col])),
                rpm_mean=float(np.mean(arr[:, rpm_col])),
                notes=notes,
            )

            # Append to per-machine index
            idx_path = self.dataset_dir / slug / "index.jsonl"
            idx_path.parent.mkdir(parents=True, exist_ok=True)
            with open(idx_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(meta.to_json()) + "\n")

            self._episodes_written += 1
            logger.info(
                f"Episode captured: {label} ({source}) "
                f"{len(samples)} samples -> {file_path.name}"
            )
            return meta

        except Exception as e:
            logger.error(f"Episode write failed: {e}")
            return None

    # ── Dataset inventory ────────────────────────────────────────────

    def summary(self) -> dict:
        """Return a summary of all captured episodes on disk."""
        out = {
            "total_episodes": 0,
            "total_size_bytes": 0,
            "by_machine": {},
            "by_label": {},
        }
        if not self.dataset_dir.exists():
            return out
        for slug_dir in self.dataset_dir.iterdir():
            if not slug_dir.is_dir():
                continue
            machine = slug_dir.name
            idx = slug_dir / "index.jsonl"
            if not idx.exists():
                continue
            with open(idx) as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    out["total_episodes"] += 1
                    out["by_machine"][machine] = out["by_machine"].get(machine, 0) + 1
                    lbl = e.get("label", "?")
                    out["by_label"][lbl] = out["by_label"].get(lbl, 0) + 1
        for root, _, files in os.walk(self.dataset_dir):
            for f in files:
                try:
                    out["total_size_bytes"] += (Path(root) / f).stat().st_size
                except Exception:
                    pass
        return out


def _slug(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "unknown"
