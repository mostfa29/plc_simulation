"""Real-time dataset capture tests — operator annotation + auto-segment.

Covers:
  - RealtimeDatasetCapture writes .npz with correct shape
  - Episode metadata in index.jsonl is valid
  - Auto-segmentation triggers on RPM-to-zero-to-running
  - Summary reports correct counts
  - Per-machine segregation works
  - update_machine() re-tags future episodes
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest

from hxi_optimizer.io_logging.realtime_dataset import (
    RealtimeDatasetCapture, EpisodeMeta, EPISODE_WINDOW_S, SAMPLES_AT_2HZ,
)


def _sample(ts: float, rpm: float = 60.0, **kwargs) -> dict:
    base = {
        "ts": ts, "seq": int(ts),
        "stale": False,
        "rpm_encoder": rpm,
        "swash_output": 500,
        "ss_setpoint_fwd": 60.0,
        "active_lower": 400,
        "active_upper": 600,
        "delivered_torque": 1500.0,
        "loop_temp": 55.0,
    }
    base.update(kwargs)
    return base


class TestBasicAnnotation:
    def test_capture_now_writes_npz(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, "Test Rig 1", "hxi")
        # Feed 40 samples at 0.5 s intervals
        for i in range(40):
            cap.feed(_sample(ts=i * 0.5))
        meta = cap.capture_now("OSCILLATION", lookback_s=10.0)
        assert meta is not None
        assert meta.label == "OSCILLATION"
        assert meta.source == "operator"
        assert meta.ewon_name == "Test Rig 1"
        assert meta.equipment_type == "hxi"
        assert meta.n_samples >= 10
        # .npz exists and loadable
        npz_path = tmp_path / meta.file_path
        assert npz_path.exists()
        data = np.load(npz_path, allow_pickle=True)
        assert data["features"].shape[1] == 7  # 7 feature columns
        assert str(data["label"][0]) == "OSCILLATION"

    def test_index_jsonl_is_appended(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, "Test Rig 1", "hxi")
        for i in range(40):
            cap.feed(_sample(ts=i * 0.5))
        cap.capture_now("A")
        cap.capture_now("B")
        cap.capture_now("C")
        idx = list((tmp_path / "test_rig_1").glob("index.jsonl"))[0]
        lines = [json.loads(l) for l in idx.read_text().splitlines() if l.strip()]
        assert len(lines) == 3
        assert [l["label"] for l in lines] == ["A", "B", "C"]

    def test_capture_with_insufficient_buffer_returns_none(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, "Rig", "hxi")
        # Only feed 5 samples
        for i in range(5):
            cap.feed(_sample(ts=i * 0.5))
        assert cap.capture_now("X") is None

    def test_empty_buffer_returns_none(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, "Rig", "hxi")
        assert cap.capture_now("X") is None

    def test_stale_samples_skipped(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, "Rig", "hxi")
        for i in range(40):
            s = _sample(ts=i * 0.5)
            s["stale"] = True
            cap.feed(s)
        # All stale -> buffer empty -> annotation fails
        assert cap.capture_now("X") is None


class TestAutoSegmentation:
    def test_rpm_drop_then_recovery_triggers_connection_episode(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, "Rig", "hxi")
        t = 0.0
        # Running at 60 RPM for 10 s
        for _ in range(20):
            cap.feed(_sample(ts=t, rpm=60.0))
            t += 0.5
        # Drop to 0 for 15 s (simulating pipe connection)
        for _ in range(30):
            cap.feed(_sample(ts=t, rpm=0.5))
            t += 0.5
        # Recovery to 60 RPM
        for _ in range(10):
            cap.feed(_sample(ts=t, rpm=60.0))
            t += 0.5
        summary = cap.summary()
        assert summary["by_label"].get("CONNECTION", 0) >= 1

    def test_brief_rpm_dip_does_not_trigger(self, tmp_path):
        """A 2-second dip is too short — should not create a CONNECTION episode."""
        cap = RealtimeDatasetCapture(tmp_path, "Rig", "hxi")
        t = 0.0
        for _ in range(20):
            cap.feed(_sample(ts=t, rpm=60.0)); t += 0.5
        for _ in range(4):  # 2 s dip
            cap.feed(_sample(ts=t, rpm=0.5)); t += 0.5
        for _ in range(20):
            cap.feed(_sample(ts=t, rpm=60.0)); t += 0.5
        # Connection episodes require > 5 s dip
        summary = cap.summary()
        assert summary["by_label"].get("CONNECTION", 0) == 0

    def test_auto_segment_disabled(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, "Rig", "hxi",
                                      auto_segment_connections=False)
        t = 0.0
        for _ in range(20): cap.feed(_sample(ts=t, rpm=60.0)); t += 0.5
        for _ in range(30): cap.feed(_sample(ts=t, rpm=0.5)); t += 0.5
        for _ in range(10): cap.feed(_sample(ts=t, rpm=60.0)); t += 0.5
        assert cap.summary()["total_episodes"] == 0


class TestSummary:
    def test_summary_empty_on_fresh_capture(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, "Rig", "hxi")
        s = cap.summary()
        assert s["total_episodes"] == 0
        assert s["by_machine"] == {}
        assert s["by_label"] == {}

    def test_summary_counts_all_labels(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, "Rig", "hxi")
        for i in range(40):
            cap.feed(_sample(ts=i * 0.5))
        cap.capture_now("A")
        cap.capture_now("A")
        cap.capture_now("B")
        s = cap.summary()
        assert s["total_episodes"] == 3
        assert s["by_label"] == {"A": 2, "B": 1}

    def test_summary_counts_all_machines(self, tmp_path):
        # Two separate captures in the same dataset dir
        for name in ("Rig Alpha", "Rig Beta"):
            cap = RealtimeDatasetCapture(tmp_path, name, "hxi")
            for i in range(40):
                cap.feed(_sample(ts=i * 0.5))
            cap.capture_now("NORMAL")

        cap = RealtimeDatasetCapture(tmp_path, "Rig Alpha", "hxi")
        s = cap.summary()
        assert s["total_episodes"] == 2
        assert "rig_alpha" in s["by_machine"]
        assert "rig_beta" in s["by_machine"]

    def test_total_size_bytes_nonzero(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, "Rig", "hxi")
        for i in range(40):
            cap.feed(_sample(ts=i * 0.5))
        cap.capture_now("X")
        s = cap.summary()
        assert s["total_size_bytes"] > 0


class TestMachineUpdate:
    def test_update_machine_changes_tag_for_future_episodes(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, "Rig A", "hxi")
        for i in range(40):
            cap.feed(_sample(ts=i * 0.5))
        m1 = cap.capture_now("X")
        assert m1.ewon_name == "Rig A"

        # Switch rigs mid-session
        cap.update_machine("Rig B", "hxi_ht")
        m2 = cap.capture_now("X")
        assert m2.ewon_name == "Rig B"
        assert m2.equipment_type == "hxi_ht"


class TestEpisodeMetaSerialization:
    def test_meta_roundtrips_json(self):
        meta = EpisodeMeta(
            ts_start=1000.0, ts_end=1020.0,
            label="OSCILLATION", source="operator",
            ewon_name="Test", equipment_type="hxi",
            n_samples=40, file_path="test/x.npz",
            setpoint_mean=60.0, rpm_mean=59.5,
            notes="",
        )
        j = meta.to_json()
        reparsed = EpisodeMeta(**j)
        assert reparsed.label == meta.label

    @pytest.mark.parametrize("label", [
        "NORMAL", "BIAS", "OSCILLATION", "DEADBAND_HUNTING",
        "SLUGGISH", "WINDUP", "CONDITION_CHANGE",
        "BAD_CONNECTION", "GOOD_CONNECTION", "CUSTOM_LABEL",
    ])
    def test_various_labels_accepted(self, tmp_path, label):
        cap = RealtimeDatasetCapture(tmp_path, "Rig", "hxi")
        for i in range(40):
            cap.feed(_sample(ts=i * 0.5))
        meta = cap.capture_now(label)
        assert meta is not None
        assert meta.label == label


class TestPerMachineDirectories:
    def test_slug_handles_special_chars(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, "Rig #1 / HT @2026", "hxi")
        for i in range(40):
            cap.feed(_sample(ts=i * 0.5))
        cap.capture_now("X")
        # Slug should be filesystem-safe
        machine_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert len(machine_dirs) == 1
        assert all(c.isalnum() or c == "_" for c in machine_dirs[0].name)

    def test_none_machine_name_uses_unknown(self, tmp_path):
        cap = RealtimeDatasetCapture(tmp_path, None, None)
        for i in range(40):
            cap.feed(_sample(ts=i * 0.5))
        meta = cap.capture_now("X")
        assert meta is not None
        assert meta.ewon_name == "unknown_machine"
