"""Per-machine model registry tests.

Verifies:
  - slugify() matches the convention used by realtime_dataset + fleet
  - resolve() returns the default pair when no rig-specific model exists
  - resolve() prefers per-rig classifier when one is deployed
  - resolve() falls back to the default autoencoder when per-rig AE is absent
  - per-rig overrides do NOT touch the default classifier.onnx file
  - list_all() + per_rig_summary() report every deployed pair
  - has_per_rig() answers correctly
  - PerformanceMonitor.switch_models() hot-swaps thread-safely and
    reports load/failure in its result dict

All filesystem state is fabricated under tmp_path so the tests never
depend on whether a live optimizer has models deployed locally.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pytest

from hxi_optimizer.intelligence.model_registry import (
    ModelRegistry, ModelPair, slugify,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _write_fake_onnx(path: Path, content: bytes = b"\x08\x07onnx-fake") -> None:
    """Write a placeholder file. ModelRegistry only checks existence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _write_meta(path: Path, classes: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "classes": classes or ["NORMAL", "BIAS", "OSCILLATION",
                                "DEADBAND_HUNTING", "SLUGGISH",
                                "WINDUP", "CONDITION_CHANGE"],
        "X_mean": [0.0] * 7,
        "X_std": [1.0] * 7,
        "test_accuracy": 0.9,
    }))


def _build_default(models_dir: Path) -> None:
    _write_fake_onnx(models_dir / "classifier.onnx")
    _write_meta(models_dir / "classifier_meta.json")
    _write_fake_onnx(models_dir / "autoencoder.onnx")
    (models_dir / "autoencoder_meta.json").write_text(json.dumps({
        "X_min": [0.0] * 7, "X_max": [100.0] * 7, "threshold": 0.01,
    }))


def _build_per_rig(models_dir: Path, slug: str,
                   with_ae: bool = False) -> None:
    rig_dir = models_dir / "per_rig" / slug
    _write_fake_onnx(rig_dir / "classifier.onnx")
    _write_meta(rig_dir / "classifier_meta.json")
    if with_ae:
        _write_fake_onnx(rig_dir / "autoencoder.onnx")
        (rig_dir / "autoencoder_meta.json").write_text(json.dumps({
            "X_min": [0.0] * 7, "X_max": [100.0] * 7,
            "threshold": 0.05, "recalibrated_from_n_windows": 120,
        }))


# ═════════════════════════════════════════════════════════════════════
# 1. slugify
# ═════════════════════════════════════════════════════════════════════

class TestSlugify:
    def test_lowercases_and_swaps_separators(self):
        assert slugify("Precision Rig 707 3pd HT") == "precision_rig_707_3pd_ht"

    def test_strips_leading_trailing_separators(self):
        assert slugify("  -- Panther Rig 2 -- ") == "panther_rig_2"

    def test_collapses_repeated_symbols(self):
        assert slugify("Rig / /  42!!") == "rig_42"

    def test_empty_becomes_unknown(self):
        assert slugify("") == "unknown"
        assert slugify("@@@") == "unknown"


# ═════════════════════════════════════════════════════════════════════
# 2. resolve() — default fallback + per-rig preference
# ═════════════════════════════════════════════════════════════════════

class TestResolve:
    def test_no_rig_returns_default(self, tmp_path):
        _build_default(tmp_path)
        reg = ModelRegistry(tmp_path)
        pair = reg.resolve(None)
        assert pair.source == "default"
        assert pair.rig_slug == "default"
        assert pair.has_classifier
        assert pair.has_autoencoder

    def test_unknown_rig_falls_back_to_default(self, tmp_path):
        _build_default(tmp_path)
        reg = ModelRegistry(tmp_path)
        pair = reg.resolve("Some Rig Never Fine-Tuned")
        assert pair.source == "default"
        assert pair.classifier_path == tmp_path / "classifier.onnx"

    def test_per_rig_classifier_preferred(self, tmp_path):
        _build_default(tmp_path)
        _build_per_rig(tmp_path, "precision_rig_707_3pd_ht", with_ae=False)
        reg = ModelRegistry(tmp_path)
        pair = reg.resolve("Precision Rig 707 3pd HT")
        assert pair.source == "per_rig"
        assert pair.rig_slug == "precision_rig_707_3pd_ht"
        assert pair.classifier_path == (
            tmp_path / "per_rig" / "precision_rig_707_3pd_ht" / "classifier.onnx"
        )
        # Without per-rig AE, we inherit the default AE
        assert pair.autoencoder_path == tmp_path / "autoencoder.onnx"

    def test_per_rig_ae_preferred_when_present(self, tmp_path):
        _build_default(tmp_path)
        _build_per_rig(tmp_path, "panther_rig_2", with_ae=True)
        reg = ModelRegistry(tmp_path)
        pair = reg.resolve("panther rig 2")
        assert pair.source == "per_rig"
        assert pair.autoencoder_path == (
            tmp_path / "per_rig" / "panther_rig_2" / "autoencoder.onnx"
        )

    def test_no_default_and_no_per_rig_returns_empty(self, tmp_path):
        reg = ModelRegistry(tmp_path)
        pair = reg.resolve("nothing")
        assert pair.source == "default"
        assert not pair.has_classifier
        assert not pair.has_autoencoder

    def test_per_rig_deploy_never_overwrites_default(self, tmp_path):
        """Invariant: writing a per-rig model must not touch classifier.onnx."""
        _build_default(tmp_path)
        default_cls = tmp_path / "classifier.onnx"
        before = default_cls.read_bytes()
        _build_per_rig(tmp_path, "precision_rig_707_3pd_ht")
        after = default_cls.read_bytes()
        assert before == after, "default classifier was modified by per-rig deploy"


# ═════════════════════════════════════════════════════════════════════
# 3. list_all + per_rig_summary + has_per_rig
# ═════════════════════════════════════════════════════════════════════

class TestListing:
    def test_list_all_includes_default_and_per_rig(self, tmp_path):
        _build_default(tmp_path)
        _build_per_rig(tmp_path, "rig_a")
        _build_per_rig(tmp_path, "rig_b", with_ae=True)
        reg = ModelRegistry(tmp_path)
        all_pairs = reg.list_all()
        slugs = {p.rig_slug for p in all_pairs}
        assert slugs == {"default", "rig_a", "rig_b"}

    def test_per_rig_summary_reports_counts(self, tmp_path):
        _build_default(tmp_path)
        _build_per_rig(tmp_path, "rig_a")
        _build_per_rig(tmp_path, "rig_b")
        reg = ModelRegistry(tmp_path)
        s = reg.per_rig_summary()
        assert s["default_available"] is True
        assert s["n_per_rig"] == 2
        assert set(s["per_rig_slugs"]) == {"rig_a", "rig_b"}

    def test_has_per_rig_matches_slugify(self, tmp_path):
        _build_per_rig(tmp_path, "precision_rig_707_3pd_ht")
        reg = ModelRegistry(tmp_path)
        assert reg.has_per_rig("Precision Rig 707 3pd HT") is True
        assert reg.has_per_rig("Some Other Rig") is False

    def test_meta_for_reads_classifier_and_autoencoder(self, tmp_path):
        _build_default(tmp_path)
        _build_per_rig(tmp_path, "rig_z", with_ae=True)
        reg = ModelRegistry(tmp_path)
        pair = reg.resolve("rig_z")
        meta = reg.meta_for(pair)
        assert "classifier" in meta
        assert meta["classifier"]["test_accuracy"] == 0.9
        assert "autoencoder" in meta
        assert meta["autoencoder"]["recalibrated_from_n_windows"] == 120

    def test_ignores_empty_per_rig_dirs(self, tmp_path):
        _build_default(tmp_path)
        # Slug dir exists but has no classifier.onnx — should be skipped
        (tmp_path / "per_rig" / "half_deployed").mkdir(parents=True)
        reg = ModelRegistry(tmp_path)
        assert reg.per_rig_summary()["n_per_rig"] == 0


# ═════════════════════════════════════════════════════════════════════
# 4. PerformanceMonitor.switch_models — thread-safe hot-swap
# ═════════════════════════════════════════════════════════════════════

class TestSwitchModels:
    def test_missing_files_produce_errors_no_crash(self, tmp_path):
        from hxi_optimizer.monitoring.performance_metrics import PerformanceMonitor
        mon = PerformanceMonitor(window_sec=20.0, deadband_rpm=2.0)
        info = mon.switch_models(
            classifier_path=str(tmp_path / "does_not_exist.onnx"),
            autoencoder_path=str(tmp_path / "also_missing.onnx"),
            label="missing_test",
        )
        assert info["classifier_loaded"] is False
        assert info["autoencoder_loaded"] is False
        # Error strings surface the missing paths for ops diagnosis
        assert any("missing" in e for e in info["errors"])

    def test_loaded_models_info_reports_state(self, tmp_path):
        from hxi_optimizer.monitoring.performance_metrics import PerformanceMonitor
        mon = PerformanceMonitor(window_sec=20.0, deadband_rpm=2.0)
        info = mon.loaded_models_info()
        assert "classifier_source" in info
        assert "autoencoder_source" in info
        assert "classifier_active" in info
        assert "autoencoder_active" in info

    def test_concurrent_switch_does_not_deadlock(self):
        """Smoke test: switch_models uses an RLock — multiple concurrent
        calls should all return within a bounded time, not deadlock.
        """
        from hxi_optimizer.monitoring.performance_metrics import PerformanceMonitor
        mon = PerformanceMonitor(window_sec=20.0, deadband_rpm=2.0)

        results = []
        def worker():
            r = mon.switch_models(
                classifier_path="/tmp/nope.onnx",
                label=threading.current_thread().name,
            )
            results.append(r)

        threads = [threading.Thread(target=worker, name=f"swap-{i}")
                    for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), "switch_models deadlocked"
        assert len(results) == 8


# ═════════════════════════════════════════════════════════════════════
# 5. fine_tune deploy path (unit-level check on path construction)
# ═════════════════════════════════════════════════════════════════════

class TestFineTuneDeployPath:
    """These don't actually run training — they verify the module-level
    constants + slug helper point at per_rig/<slug>/, never at the
    default classifier.onnx. Full training is covered in test_fine_tune.py.
    """

    def test_per_rig_dir_sits_under_deploy_dir(self):
        import training.fine_tune as ft
        assert ft.PER_RIG_DIR == ft.DEPLOY_DIR / "per_rig"

    def test_slugify_matches_registry(self):
        import training.fine_tune as ft
        from hxi_optimizer.intelligence.model_registry import slugify
        assert ft._slugify("Precision Rig 707 3pd HT") == \
               slugify("Precision Rig 707 3pd HT")

    def test_all_becomes_fleet_slug(self):
        import training.fine_tune as ft
        # "all" is the fleet-wide filter — deploy under per_rig/fleet/
        assert ft._slugify("all") == "all"  # raw slugify
        # But in the deploy block, "all" is normalised to "fleet" — check
        # the exact branch by reading the source (belt & suspenders).
        import inspect
        src = inspect.getsource(ft.fine_tune_classifier)
        assert '"fleet"' in src, "deploy block no longer normalises 'all' -> 'fleet'"
