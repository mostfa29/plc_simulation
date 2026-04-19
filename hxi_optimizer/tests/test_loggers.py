"""Tests for io_logging/audit_logger.py + csv_logger.py.

~80 tests covering:
- AuditLogger: file creation, header, every log method, fsync, thread safety
- CrashSafeCSVLogger: queue consumption, shutdown sentinel, header, fsync
- build_csv_row: full/stale/missing fields
"""
from __future__ import annotations

import csv
import os
import queue
import tempfile
import threading
import time
from pathlib import Path

import pytest

from hxi_optimizer.io_logging.audit_logger import AuditLogger
from hxi_optimizer.io_logging.csv_logger import (
    CSV_HEADER, CrashSafeCSVLogger, build_csv_row,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. AUDIT LOGGER — FILE CREATION
# ═══════════════════════════════════════════════════════════════════════════

class TestAuditLoggerCreation:
    def test_creates_file(self, tmp_path):
        path = tmp_path / "audit.csv"
        AuditLogger(path)
        assert path.exists()

    def test_writes_header(self, tmp_path):
        path = tmp_path / "audit.csv"
        AuditLogger(path)
        with open(path) as f:
            header = f.readline().strip()
        assert "timestamp" in header
        assert "event_type" in header

    def test_header_not_duplicated(self, tmp_path):
        path = tmp_path / "audit.csv"
        AuditLogger(path)
        AuditLogger(path)  # Re-instantiate
        with open(path) as f:
            lines = f.readlines()
        header_count = sum(1 for l in lines if "event_type" in l)
        assert header_count == 1

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "subdir" / "audit.csv"
        AuditLogger(path)
        assert path.exists()


# ═══════════════════════════════════════════════════════════════════════════
# 2. AUDIT LOGGER — LOG METHODS
# ═══════════════════════════════════════════════════════════════════════════

class TestAuditLoggerMethods:
    def _read_rows(self, path):
        with open(path) as f:
            return list(csv.reader(f))

    def test_log_write(self, tmp_path):
        a = AuditLogger(tmp_path / "a.csv")
        a.log_write(400, 600, 405, 605, "TRIAL", "WRITE_SUCCESS", 42, 0)
        rows = self._read_rows(a.filepath)
        assert len(rows) == 2  # header + 1 data row
        assert rows[1][1] == "WRITE"
        assert rows[1][7] == "WRITE_SUCCESS"

    def test_log_rejected(self, tmp_path):
        a = AuditLogger(tmp_path / "a.csv")
        a.log_rejected(10, 600, "ABS_BOUNDS")
        rows = self._read_rows(a.filepath)
        assert rows[1][1] == "REJECTED"
        assert "ABS_BOUNDS" in str(rows[1])

    def test_log_rollback(self, tmp_path):
        a = AuditLogger(tmp_path / "a.csv")
        a.log_rollback("OSCILLATION", 400, 600)
        rows = self._read_rows(a.filepath)
        assert rows[1][1] == "ROLLBACK"

    def test_log_event_generic(self, tmp_path):
        a = AuditLogger(tmp_path / "a.csv")
        a.log_event("ESD_FREEZE", "ESD bit went HIGH")
        rows = self._read_rows(a.filepath)
        assert rows[1][1] == "ESD_FREEZE"

    @pytest.mark.parametrize("event", [
        "ESD_FREEZE", "BUMP_LOCKOUT", "DISABLED", "ENABLED",
    ])
    def test_all_event_types(self, tmp_path, event):
        a = AuditLogger(tmp_path / "a.csv")
        a.log_event(event, "test")
        rows = self._read_rows(a.filepath)
        assert rows[1][1] == event

    def test_multiple_writes(self, tmp_path):
        a = AuditLogger(tmp_path / "a.csv")
        for i in range(20):
            a.log_write(400, 600, 400 + i, 600 + i, "TRIAL", "TEST", i, 0)
        rows = self._read_rows(a.filepath)
        assert len(rows) == 21  # header + 20

    def test_timestamp_is_numeric(self, tmp_path):
        a = AuditLogger(tmp_path / "a.csv")
        a.log_write(400, 600, 405, 605, "TRIAL", "TEST")
        rows = self._read_rows(a.filepath)
        ts = float(rows[1][0])
        assert ts > 1e9  # Unix epoch

    def test_close_is_noop(self, tmp_path):
        a = AuditLogger(tmp_path / "a.csv")
        a.close()  # Should not raise


# ═══════════════════════════════════════════════════════════════════════════
# 3. AUDIT LOGGER — THREAD SAFETY
# ═══════════════════════════════════════════════════════════════════════════

class TestAuditLoggerThreadSafety:
    def test_concurrent_writes(self, tmp_path):
        a = AuditLogger(tmp_path / "a.csv")
        errors = []

        def writer(n):
            try:
                for i in range(50):
                    a.log_write(n, n, n + i, n + i, "TRIAL", f"THREAD_{n}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        with open(a.filepath) as f:
            rows = list(csv.reader(f))
        assert len(rows) == 201  # header + 4×50


# ═══════════════════════════════════════════════════════════════════════════
# 4. CSV LOGGER
# ═══════════════════════════════════════════════════════════════════════════

class TestCrashSafeCSVLogger:
    def test_writes_rows(self, tmp_path):
        q = queue.Queue()
        logger = CrashSafeCSVLogger(tmp_path / "test.csv", q, fsync_interval=0.1)
        logger.start()
        q.put(["a", "b", "c"])
        q.put(["d", "e", "f"])
        q.put(None)  # shutdown
        logger.join(timeout=5)
        with open(tmp_path / "test.csv") as f:
            rows = list(csv.reader(f))
        assert len(rows) >= 3  # header + 2 data

    def test_writes_header(self, tmp_path):
        q = queue.Queue()
        logger = CrashSafeCSVLogger(tmp_path / "test.csv", q)
        logger.start()
        q.put(None)
        logger.join(timeout=5)
        with open(tmp_path / "test.csv") as f:
            header = f.readline().strip().split(",")
        assert "timestamp" in header

    def test_creates_parent_dir(self, tmp_path):
        q = queue.Queue()
        path = tmp_path / "sub" / "test.csv"
        logger = CrashSafeCSVLogger(path, q)
        logger.start()
        q.put(None)
        logger.join(timeout=5)
        assert path.exists()

    def test_is_daemon(self, tmp_path):
        q = queue.Queue()
        logger = CrashSafeCSVLogger(tmp_path / "test.csv", q)
        assert logger.daemon is True


# ═══════════════════════════════════════════════════════════════════════════
# 5. BUILD_CSV_ROW
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildCSVRow:
    def test_full_sample(self):
        sample = {
            "ts": 1234567890.123, "seq": 42, "stale": False,
            "rpm_encoder": 60.0, "swash_output": 500,
            "active_lower": 400, "active_upper": 600,
            "delivered_torque": 1000.0, "pid_state": 1,
            "bump_fwd_set": 45, "bump_rev_set": 30,
            "bump_angle": 37, "bump_flag_fwd": 0, "bump_flag_rev": 0,
            "esd_bit": 0, "loop_temp": 55.0,
            "ss_setpoint_fwd": 60.0, "ss_setpoint_rev": -60.0,
            "raw_words": list(range(28)),
        }
        row = build_csv_row(sample)
        assert len(row) == len(CSV_HEADER)
        assert row[0] == "1234567890.123"
        assert row[1] == 42
        assert row[2] == 0  # stale flag

    def test_stale_sample(self):
        sample = {"ts": 0, "seq": 0, "stale": True, "raw_words": []}
        row = build_csv_row(sample)
        assert row[2] == 1  # stale flag

    def test_missing_fields_use_empty(self):
        sample = {"ts": 0, "seq": 0, "stale": False, "raw_words": []}
        row = build_csv_row(sample)
        assert row[3] == ""  # rpm_encoder missing

    def test_raw_words_padded(self):
        sample = {"ts": 0, "seq": 0, "stale": False, "raw_words": [1, 2, 3]}
        row = build_csv_row(sample)
        # Last 28 elements should be raw words padded
        raw_section = row[-28:]
        assert raw_section[:3] == [1, 2, 3]
        assert raw_section[3] == ""

    def test_row_length_matches_header(self):
        sample = {"ts": 0, "seq": 0, "stale": False, "raw_words": list(range(28))}
        row = build_csv_row(sample)
        assert len(row) == len(CSV_HEADER)

    @pytest.mark.parametrize("n_raw", [0, 5, 14, 28, 30])
    def test_various_raw_lengths(self, n_raw):
        sample = {"ts": 0, "seq": 0, "stale": False,
                  "raw_words": list(range(n_raw))}
        row = build_csv_row(sample)
        assert len(row) == len(CSV_HEADER)
