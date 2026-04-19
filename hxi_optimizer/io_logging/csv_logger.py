"""Crash-safe CSV logger — per MASTER_CONTEXT §7.6.

Background daemon thread consumes from queue.Queue and writes CSV. The read
loop never blocks on disk I/O. `os.fsync` every 5 seconds bounds data loss
on hard kill to ~10 samples.

CSV schema includes the full raw 28-word register block alongside decoded
values, so historical files can be re-decoded if VERIFIED_WORD_ORDER is
later changed.
"""
from __future__ import annotations

import csv
import logging
import os
import queue
import threading
import time
from pathlib import Path

logger = logging.getLogger("csv_logger")

# Decoded columns (named values) followed by raw words for replay
_DECODED_COLS = [
    "timestamp", "seq", "stale",
    "rpm_encoder", "swash_output", "active_lower", "active_upper",
    "delivered_torque", "pid_state", "bump_fwd_set", "bump_rev_set",
    "bump_angle", "bump_flag_fwd", "bump_flag_rev", "esd_bit",
    "loop_temp", "ss_setpoint_fwd", "ss_setpoint_rev",
]
_RAW_COLS = [f"raw_w{i:02d}" for i in range(28)]
CSV_HEADER = _DECODED_COLS + _RAW_COLS


def build_csv_row(sample: dict) -> list:
    """Convert a sample dict (from register_map.build_sample) to ordered CSV row."""
    raw = sample.get("raw_words") or []
    raw = list(raw) + [""] * (28 - len(raw))
    decoded = [
        f"{sample.get('ts', 0.0):.3f}",
        sample.get("seq", -1),
        1 if sample.get("stale") else 0,
        sample.get("rpm_encoder", ""),
        sample.get("swash_output", ""),
        sample.get("active_lower", ""),
        sample.get("active_upper", ""),
        sample.get("delivered_torque", ""),
        sample.get("pid_state", ""),
        sample.get("bump_fwd_set", ""),
        sample.get("bump_rev_set", ""),
        sample.get("bump_angle", ""),
        sample.get("bump_flag_fwd", ""),
        sample.get("bump_flag_rev", ""),
        sample.get("esd_bit", ""),
        sample.get("loop_temp", ""),
        sample.get("ss_setpoint_fwd", ""),
        sample.get("ss_setpoint_rev", ""),
    ]
    return decoded + raw[:28]


class CrashSafeCSVLogger(threading.Thread):
    """Daemon thread: queue → CSV with periodic fsync."""

    def __init__(self, filepath: Path, log_queue: "queue.Queue",
                 fsync_interval: float = 5.0) -> None:
        super().__init__(daemon=True, name="csv-logger")
        self.filepath = Path(filepath)
        self.queue = log_queue
        self.fsync_interval = fsync_interval
        self._write_count = 0

    def run(self) -> None:
        logger.info(f"CSV logger writing to: {self.filepath}")
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(self.filepath, "a", newline="", buffering=1) as f:
            writer = csv.writer(f)
            if f.tell() == 0:
                writer.writerow(CSV_HEADER)
            last_fsync = time.monotonic()

            while True:
                try:
                    row = self.queue.get(timeout=1.0)
                    if row is None:  # Shutdown sentinel
                        break
                    writer.writerow(row)
                    self._write_count += 1
                    if time.monotonic() - last_fsync >= self.fsync_interval:
                        f.flush()
                        os.fsync(f.fileno())
                        last_fsync = time.monotonic()
                except queue.Empty:
                    if time.monotonic() - last_fsync >= self.fsync_interval:
                        f.flush()
                        os.fsync(f.fileno())
                        last_fsync = time.monotonic()
                    continue

            f.flush()
            os.fsync(f.fileno())

        logger.info(f"CSV logger: wrote {self._write_count} rows, closed {self.filepath}")
