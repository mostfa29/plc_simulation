"""Immutable audit trail for all PLC writes — per MASTER_CONTEXT §7.7.

`os.fsync` on EVERY write. The audit trail must survive a hard kill.
"""
from __future__ import annotations

import csv
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger("audit")


class AuditLogger:
    HEADER = [
        "timestamp", "event_type",
        "old_lower", "old_upper", "new_lower", "new_upper",
        "state", "reason", "heartbeat_seq", "consecutive_rej",
    ]

    def __init__(self, filepath: Path) -> None:
        self.filepath = Path(filepath)
        self._lock = threading.Lock()
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        # Write header if file is new
        write_header = not self.filepath.exists() or self.filepath.stat().st_size == 0
        if write_header:
            with open(self.filepath, "a", newline="") as f:
                csv.writer(f).writerow(self.HEADER)
                f.flush()
                os.fsync(f.fileno())
        logger.info(f"AuditLogger writing to {self.filepath}")

    def _write(self, row: list) -> None:
        with self._lock:
            with open(self.filepath, "a", newline="") as f:
                csv.writer(f).writerow(row)
                f.flush()
                os.fsync(f.fileno())  # Audit must NOT be buffered

    def log_write(self, old_lower, old_upper, new_lower, new_upper,
                  state: str, reason: str,
                  heartbeat: int = 0, consec_rej: int = 0) -> None:
        self._write([
            f"{time.time():.3f}", "WRITE",
            old_lower, old_upper, new_lower, new_upper,
            state, reason, heartbeat, consec_rej,
        ])

    def log_rejected(self, lower, upper, reason: str) -> None:
        self._write([
            f"{time.time():.3f}", "REJECTED",
            "", "", lower, upper, "", reason, "", "",
        ])

    def log_rollback(self, reason: str, lkg_lower: int, lkg_upper: int) -> None:
        self._write([
            f"{time.time():.3f}", "ROLLBACK",
            "", "", lkg_lower, lkg_upper, "ROLLBACK", reason, "", "",
        ])

    def log_event(self, event_type: str, reason: str) -> None:
        """Generic event row: ESD_FREEZE / BUMP_LOCKOUT / DISABLED / ENABLED."""
        self._write([
            f"{time.time():.3f}", event_type,
            "", "", "", "", "", reason, "", "",
        ])

    def close(self) -> None:
        # File is closed after every write — nothing to do.
        pass
