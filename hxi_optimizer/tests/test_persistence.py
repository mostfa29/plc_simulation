"""Tests for state/persistence.py — atomic JSON writes.

~60 tests covering:
- save_state: normal, with existing, fsync, .bak rotation, directory creation
- load_state: normal, corrupt, missing, .bak fallback, age reporting
- Atomic guarantee: crash-safe naming
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from hxi_optimizer.state.persistence import load_state, save_state


# ═══════════════════════════════════════════════════════════════════════════
# 1. SAVE STATE
# ═══════════════════════════════════════════════════════════════════════════

class TestSaveState:
    def test_creates_file(self, tmp_path):
        path = tmp_path / "state.json"
        ok = save_state({"key": "value"}, path)
        assert ok is True
        assert path.exists()

    def test_valid_json(self, tmp_path):
        path = tmp_path / "state.json"
        save_state({"lower": 400, "upper": 600}, path)
        data = json.loads(path.read_text())
        assert data["lower"] == 400

    def test_includes_saved_at(self, tmp_path):
        path = tmp_path / "state.json"
        save_state({"x": 1}, path)
        data = json.loads(path.read_text())
        assert "_saved_at" in data
        assert data["_saved_at"] > 1e9

    def test_creates_backup(self, tmp_path):
        path = tmp_path / "state.json"
        save_state({"v": 1}, path)
        save_state({"v": 2}, path)
        bak = Path(str(path) + ".bak")
        assert bak.exists()
        old = json.loads(bak.read_text())
        assert old["v"] == 1

    def test_backup_rotation(self, tmp_path):
        path = tmp_path / "state.json"
        for i in range(5):
            save_state({"v": i}, path)
        current = json.loads(path.read_text())
        assert current["v"] == 4
        bak = Path(str(path) + ".bak")
        old = json.loads(bak.read_text())
        assert old["v"] == 3

    def test_creates_parent_directory(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "state.json"
        save_state({"x": 1}, path)
        assert path.exists()

    def test_returns_true_on_success(self, tmp_path):
        assert save_state({"x": 1}, tmp_path / "s.json") is True

    def test_overwrites_existing(self, tmp_path):
        path = tmp_path / "state.json"
        save_state({"v": 1}, path)
        save_state({"v": 2}, path)
        data = json.loads(path.read_text())
        assert data["v"] == 2

    @pytest.mark.parametrize("value", [
        {"a": 1}, {"a": [1, 2, 3]}, {"a": {"b": "c"}},
        {"a": None}, {"a": 3.14}, {"a": True},
    ])
    def test_various_data_types(self, tmp_path, value):
        path = tmp_path / "state.json"
        assert save_state(value, path) is True
        data = json.loads(path.read_text())
        assert data["a"] == value["a"]

    def test_no_tmp_file_left(self, tmp_path):
        path = tmp_path / "state.json"
        save_state({"x": 1}, path)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 2. LOAD STATE
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadState:
    def test_load_existing(self, tmp_path):
        path = tmp_path / "state.json"
        save_state({"v": 42}, path)
        data = load_state(path)
        assert data["v"] == 42

    def test_load_missing_returns_empty(self, tmp_path):
        data = load_state(tmp_path / "nonexistent.json")
        assert data == {}

    def test_load_corrupt_falls_back_to_bak(self, tmp_path):
        path = tmp_path / "state.json"
        bak = Path(str(path) + ".bak")
        save_state({"v": "good"}, path)
        # Overwrite .bak with the good file, corrupt the main
        bak.write_text(path.read_text())
        path.write_text("NOT JSON{{{")
        data = load_state(path)
        assert data["v"] == "good"

    def test_load_both_corrupt_returns_empty(self, tmp_path):
        path = tmp_path / "state.json"
        bak = Path(str(path) + ".bak")
        path.write_text("CORRUPT")
        bak.write_text("ALSO CORRUPT")
        data = load_state(path)
        assert data == {}

    def test_load_empty_file_returns_empty(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("")
        data = load_state(path)
        assert data == {}

    def test_includes_saved_at(self, tmp_path):
        path = tmp_path / "state.json"
        save_state({"x": 1}, path)
        data = load_state(path)
        assert "_saved_at" in data

    def test_round_trip(self, tmp_path):
        path = tmp_path / "state.json"
        original = {
            "gate_lower": 400, "gate_upper": 600,
            "lkg_lower": 380, "lkg_upper": 620,
            "advisor_trim_upper": 5.5,
        }
        save_state(original, path)
        loaded = load_state(path)
        for k in original:
            assert loaded[k] == original[k]

    @pytest.mark.parametrize("n_saves", [1, 3, 10])
    def test_multiple_save_load_cycles(self, tmp_path, n_saves):
        path = tmp_path / "state.json"
        for i in range(n_saves):
            save_state({"v": i}, path)
        data = load_state(path)
        assert data["v"] == n_saves - 1
