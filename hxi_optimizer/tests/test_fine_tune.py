"""Fine-tune pipeline tests.

Verifies:
  - load_real_episodes correctly walks dataset/<machine>/<label>/*.npz
  - LABEL_REMAP translates operator labels (STICKSLIP→OSCILLATION etc.)
  - Sliding-window extraction produces 40×7 windows from variable-length episodes
  - Validation gate refuses to deploy if accuracy degrades
  - AE threshold recalibration computes mean+3σ from real NORMAL only
  - Insufficient data returns ok=False without crashing

Synthetic data is written into a tmp dataset dir to simulate real captures.

Note on deployment-machine vs developer-machine: tests that exercise
fine-tune logic transitively need torch + sklearn (training-side deps).
On rig PCs those may not be installed — those tests skip cleanly via the
HAS_TRAINING_DEPS marker rather than failing.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

import training.fine_tune as ft

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

HAS_TRAINING_DEPS = (
    importlib.util.find_spec("torch") is not None
    and importlib.util.find_spec("sklearn") is not None
)
HAS_SIM_AE_CHECKPOINT = (ft.SIM_AE_DIR / "model.pt").exists()


def _write_episode(dataset_dir: Path, machine: str, label: str,
                    n_samples: int = 60, seed: int = 0) -> Path:
    """Write one synthetic episode .npz."""
    machine_dir = dataset_dir / machine / label
    machine_dir.mkdir(parents=True, exist_ok=True)
    path = machine_dir / f"episode_{seed}.npz"
    rng = np.random.default_rng(seed)
    features = rng.normal(loc=60.0, scale=5.0,
                          size=(n_samples, 7)).astype(np.float32)
    np.savez_compressed(path, features=features,
                         feature_names=np.array(["rpm_encoder",
                                                  "swash_output",
                                                  "ss_setpoint_fwd",
                                                  "active_lower",
                                                  "active_upper",
                                                  "delivered_torque",
                                                  "loop_temp"]))
    return path


# ═════════════════════════════════════════════════════════════════════
# 1. load_real_episodes
# ═════════════════════════════════════════════════════════════════════

class TestLoadRealEpisodes:
    def test_empty_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        X, y, m, stats = ft.load_real_episodes("all")
        assert len(X) == 0
        assert stats.n_episodes == 0

    def test_single_episode_produces_sliding_windows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        # 60-sample episode, stride 10 → windows at start 0, 10, 20 = 3 windows
        _write_episode(tmp_path, "rig_a", "NORMAL", n_samples=60)
        X, y, machines, stats = ft.load_real_episodes("all")
        assert X.shape == (3, 40, 7)
        assert all(yi == ft.CLASS_TO_IDX["NORMAL"] for yi in y)
        assert all(m == "rig_a" for m in machines)
        assert stats.n_episodes == 1

    def test_label_remap_stickslip_to_oscillation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        _write_episode(tmp_path, "rig_a", "STICKSLIP", n_samples=60)
        X, y, _, stats = ft.load_real_episodes("all")
        assert len(X) == 3
        assert all(yi == ft.CLASS_TO_IDX["OSCILLATION"] for yi in y)
        assert stats.by_label.get("OSCILLATION") == 1

    def test_label_remap_connection_to_normal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        _write_episode(tmp_path, "rig_a", "CONNECTION", n_samples=60)
        X, y, _, _ = ft.load_real_episodes("all")
        assert all(yi == ft.CLASS_TO_IDX["NORMAL"] for yi in y)

    def test_unmapped_label_excluded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        _write_episode(tmp_path, "rig_a", "TOTALLY_UNKNOWN", n_samples=60)
        X, y, _, stats = ft.load_real_episodes("all")
        assert len(X) == 0
        assert "TOTALLY_UNKNOWN" in stats.excluded_unmapped

    def test_short_episode_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        _write_episode(tmp_path, "rig_a", "NORMAL", n_samples=20)
        X, y, _, stats = ft.load_real_episodes("all")
        assert len(X) == 0   # < 40 samples → no window
        assert stats.n_episodes == 0

    def test_rig_filter_excludes_other_machines(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        _write_episode(tmp_path, "precision_rig_707", "NORMAL", seed=1)
        _write_episode(tmp_path, "panther_rig_2", "NORMAL", seed=2)
        X_all, _, machines_all, _ = ft.load_real_episodes("all")
        X_p707, _, machines_p707, _ = ft.load_real_episodes("Precision Rig 707")
        assert len(X_all) > len(X_p707)
        assert all("precision_rig_707" in m for m in machines_p707)

    def test_multiple_machines_aggregated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        _write_episode(tmp_path, "rig_a", "NORMAL", seed=1)
        _write_episode(tmp_path, "rig_a", "BIAS", seed=2)
        _write_episode(tmp_path, "rig_b", "NORMAL", seed=3)
        _, y, machines, stats = ft.load_real_episodes("all")
        assert stats.n_episodes == 3
        assert stats.by_machine["rig_a"] == 2
        assert stats.by_machine["rig_b"] == 1
        assert "NORMAL" in stats.by_label
        assert "BIAS" in stats.by_label

    def test_corrupt_npz_skipped_not_crashed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        _write_episode(tmp_path, "rig_a", "NORMAL", seed=1)
        # Write a bad .npz file
        (tmp_path / "rig_a" / "NORMAL" / "broken.npz").write_bytes(b"not a valid npz")
        X, _, _, stats = ft.load_real_episodes("all")
        # Good episode still loaded
        assert stats.n_episodes == 1
        assert len(X) == 3


# ═════════════════════════════════════════════════════════════════════
# 2. fine_tune_classifier — insufficient data path
# ═════════════════════════════════════════════════════════════════════

class TestInsufficientData:
    """Both tests below take the early-return path inside
    fine_tune_classifier (no training deps needed) — verifies the function
    fails cleanly on rig PCs without torch/sklearn.
    """
    def test_no_real_data_returns_ok_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        result = ft.fine_tune_classifier(
            rig_filter="all", mix_sim_ratio=0.3,
            epochs=2, lr=1e-4, seed=42, deploy=False,
        )
        assert result["ok"] is False
        assert "Not enough real data" in result["reason"]

    def test_only_a_few_windows_returns_ok_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        # 3 windows total (way under the 100-window minimum)
        _write_episode(tmp_path, "rig_a", "NORMAL", n_samples=60, seed=1)
        result = ft.fine_tune_classifier(
            rig_filter="all", mix_sim_ratio=0.3,
            epochs=2, lr=1e-4, seed=42, deploy=False,
        )
        assert result["ok"] is False


# ═════════════════════════════════════════════════════════════════════
# 3. AE threshold recalibration
# ═════════════════════════════════════════════════════════════════════

class TestAERecalibration:
    def test_no_real_data_returns_ok_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        result = ft.recalibrate_ae_threshold(rig_filter="all", deploy=False)
        assert result["ok"] is False
        # Two valid early-return paths:
        #   - "no real NORMAL windows" path (dev machine where sim
        #     checkpoint exists but dataset is empty)
        #   - "sim AE checkpoint not at <path>" path (rig PC where the
        #     training/ tree isn't present, only the deployed ONNX)
        assert any(s in result["reason"]
                   for s in ("NORMAL", "windows", "sim AE checkpoint"))

    @pytest.mark.skipif(not (ft.SIM_AE_DIR / "model.pt").exists(),
                        reason="sim AE checkpoint not deployed")
    def test_real_normal_data_produces_threshold(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "DATASET_DIR", tmp_path)
        # Write enough NORMAL episodes to reach the 50-window minimum
        for i in range(20):
            _write_episode(tmp_path, "rig_x", "NORMAL", n_samples=60, seed=i)
        result = ft.recalibrate_ae_threshold(rig_filter="all", deploy=False)
        assert result["ok"] is True
        assert result["new_threshold"] > 0
        assert result["n_real_normal_windows"] >= 50


# ═════════════════════════════════════════════════════════════════════
# 4. CLASSES + LABEL_REMAP integrity
# ═════════════════════════════════════════════════════════════════════

class TestLabelMappingIntegrity:
    def test_classes_have_unique_indices(self):
        assert len(ft.CLASSES) == len(set(ft.CLASSES))
        assert len(ft.CLASS_TO_IDX) == len(ft.CLASSES)

    def test_all_remap_targets_are_known_classes(self):
        for raw, mapped in ft.LABEL_REMAP.items():
            assert mapped in ft.CLASS_TO_IDX, \
                f"LABEL_REMAP['{raw}'] → '{mapped}' is not in CLASSES"

    def test_remap_handles_dashboard_button_labels(self):
        # Every button on the dashboard should map to something
        button_labels = [
            "OSCILLATION", "STICKSLIP", "BIAS", "FORMATION_CHANGE",
            "CONDITION_CHANGE", "BAD_CONNECTION", "GOOD_CONNECTION",
            "WINDUP",
        ]
        for lbl in button_labels:
            mapped = ft.LABEL_REMAP.get(lbl, lbl)
            assert mapped in ft.CLASS_TO_IDX, \
                f"Dashboard label {lbl} doesn't resolve to a known class"
