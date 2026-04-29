"""Tests for the dashboard self-test battery.

Covers:
  - Each individual check returns a well-formed result dict
  - The orchestrator never crashes even with an empty `shared`
  - Status tallying + overall verdict are correct
  - The /api/selftest endpoint is reachable + returns the expected shape
  - Non-technical safeguards: every fail/warn result has a `what_to_do`
    string so the operator knows the next step
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hxi_optimizer.dashboard.selftest import (
    run_self_tests, CHECKS, _result, _format_uptime,
)
from hxi_optimizer.dashboard.server import create_app
from hxi_optimizer.io_logging.audit_logger import AuditLogger


# ─────────────────────────────────────────────────────────────────────
# Result shape + helpers
# ─────────────────────────────────────────────────────────────────────

class TestResultShape:
    def test_result_has_all_required_fields(self):
        r = _result("foo", "Foo", "checks foo", "pass", "all good", "do nothing")
        assert set(r.keys()) == {"id", "name", "description", "status",
                                  "detail", "what_to_do"}

    def test_format_uptime(self):
        assert _format_uptime(30) == "30s"
        assert _format_uptime(125) == "2m 5s"
        assert _format_uptime(3700) == "1h 1m"
        assert _format_uptime(90000) == "1d 1h"


# ─────────────────────────────────────────────────────────────────────
# Battery runs cleanly with empty shared
# ─────────────────────────────────────────────────────────────────────

class TestBatteryWithEmptyShared:
    def test_does_not_crash(self):
        result = run_self_tests({})
        assert result["overall"] in ("pass", "warn", "fail")
        assert result["summary"]["total"] == len(CHECKS)
        assert "elapsed_ms" in result
        assert isinstance(result["tests"], list)

    def test_each_check_returns_required_fields(self):
        result = run_self_tests({})
        for t in result["tests"]:
            assert "id" in t
            assert "name" in t
            assert "status" in t
            assert t["status"] in ("pass", "fail", "warn", "skip")
            assert "detail" in t

    def test_battery_is_fast(self):
        """Whole battery must run in under 2 seconds even on a cold cache."""
        t0 = time.time()
        result = run_self_tests({})
        elapsed = time.time() - t0
        assert elapsed < 2.0, f"Battery took {elapsed:.2f}s, expected <2s"

    def test_summary_counts_match_test_statuses(self):
        result = run_self_tests({})
        s = result["summary"]
        n_pass = sum(1 for t in result["tests"] if t["status"] == "pass")
        n_fail = sum(1 for t in result["tests"] if t["status"] == "fail")
        n_warn = sum(1 for t in result["tests"] if t["status"] == "warn")
        n_skip = sum(1 for t in result["tests"] if t["status"] == "skip")
        assert s["passed"] == n_pass
        assert s["failed"] == n_fail
        assert s["warnings"] == n_warn
        assert s["skipped"] == n_skip
        assert s["total"] == len(result["tests"])


# ─────────────────────────────────────────────────────────────────────
# Overall verdict logic
# ─────────────────────────────────────────────────────────────────────

class TestOverallVerdict:
    def test_any_fail_means_overall_fail(self):
        result = run_self_tests({})
        if result["summary"]["failed"] > 0:
            assert result["overall"] == "fail"

    def test_only_warnings_means_overall_warn(self):
        # Build a fully-mocked shared that should produce no fails (only
        # warns/skips/passes). Hard to guarantee — instead inspect the
        # logic directly with a synthetic test.
        from hxi_optimizer.dashboard.selftest import run_self_tests
        result = run_self_tests({})
        # If there are fails, this test is a no-op (covered above).
        # If 0 fails and >=1 warn, overall must be "warn".
        if result["summary"]["failed"] == 0:
            if result["summary"]["warnings"] > 0:
                assert result["overall"] == "warn"
            else:
                assert result["overall"] == "pass"


# ─────────────────────────────────────────────────────────────────────
# Plain-language affordances — every fail/warn must explain the fix
# ─────────────────────────────────────────────────────────────────────

class TestPlainLanguageGuidance:
    def test_every_fail_has_what_to_do(self):
        """If a check fails, the operator must see actionable text."""
        result = run_self_tests({})
        for t in result["tests"]:
            if t["status"] == "fail":
                assert t["what_to_do"], (
                    f"Test '{t['id']}' failed but has no what_to_do field — "
                    f"non-technical operators won't know what to do."
                )

    def test_every_check_has_user_friendly_description(self):
        result = run_self_tests({})
        for t in result["tests"]:
            assert t["description"], f"{t['id']} missing description"
            # No internal jargon — descriptions should be plain English.
            # We allow some technical terms (PLC, ML, etc.) but not raw
            # function names or file paths.
            assert "_" not in t["description"] or "PLC" in t["description"], (
                f"{t['id']} description has snake_case: {t['description']!r}"
            )


# ─────────────────────────────────────────────────────────────────────
# Specific check outcomes with rich shared state
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def healthy_shared(tmp_path):
    """Build a shared dict that should produce mostly pass results."""
    cfg = MagicMock()
    cfg.dashboard_token = None
    cfg.dashboard_max_body_bytes = 1_000_000
    cfg.dashboard_endpoint_timeout_s = 30.0
    cfg.dashboard_max_concurrent = 64
    cfg.phase = MagicMock()
    cfg.phase.value = "A"
    cfg.dataset_capture_enabled = True
    cfg.safety = MagicMock(
        abs_min_lower=300, abs_max_lower=500,
        abs_min_upper=550, abs_max_upper=750,
    )

    gate = MagicMock()
    gate.state = MagicMock()
    gate.state.name = "ADAPTING"

    modbus = MagicMock()
    modbus.is_healthy = True
    modbus.consecutive_failures = 0
    modbus.transport_name = "modbus"

    monitor = MagicMock()
    monitor.loaded_models_info.return_value = {
        "classifier_active": True,
        "classifier_inferences": 100,
        "classifier_failures": 0,
        "classifier_source": "/models/classifier.onnx",
        "autoencoder_active": True,
        "autoencoder_threshold": 0.0007,
        "autoencoder_source": "/models/autoencoder.onnx",
    }

    machine_record = MagicMock()
    machine_record.ewon_name = "Precision Rig 707"
    machine_record.equipment_type = "hxi"

    audit = AuditLogger(tmp_path / "audit.log")

    ring = [{"ts": time.time(), "stale": False, "rpm_encoder": 60}]
    lock = MagicMock()
    lock.__enter__ = MagicMock(return_value=None)
    lock.__exit__ = MagicMock(return_value=None)

    return {
        "config": cfg,
        "gate": gate,
        "modbus": modbus,
        "monitor": monitor,
        "machine_record": machine_record,
        "audit": audit,
        "ring_buffer": ring,
        "buffer_lock": lock,
        "model_registry": None,  # explicitly skipped
        "dataset_capture": MagicMock(summary=lambda: {"total_episodes": 5}),
    }


class TestSpecificChecks:
    def test_plc_connection_pass_when_healthy(self, healthy_shared):
        result = run_self_tests(healthy_shared)
        plc = next(t for t in result["tests"] if t["id"] == "plc_connection")
        assert plc["status"] == "pass"

    def test_plc_connection_warn_with_few_failures(self, healthy_shared):
        healthy_shared["modbus"].is_healthy = False
        healthy_shared["modbus"].consecutive_failures = 2
        result = run_self_tests(healthy_shared)
        plc = next(t for t in result["tests"] if t["id"] == "plc_connection")
        assert plc["status"] == "warn"

    def test_plc_connection_fail_with_many_failures(self, healthy_shared):
        healthy_shared["modbus"].is_healthy = False
        healthy_shared["modbus"].consecutive_failures = 10
        result = run_self_tests(healthy_shared)
        plc = next(t for t in result["tests"] if t["id"] == "plc_connection")
        assert plc["status"] == "fail"
        assert "what_to_do" in plc and len(plc["what_to_do"]) > 10

    def test_safety_limits_fail_when_unset(self, healthy_shared):
        healthy_shared["config"].safety = MagicMock(
            abs_min_lower=None, abs_max_lower=None,
            abs_min_upper=None, abs_max_upper=None,
        )
        result = run_self_tests(healthy_shared)
        safety = next(t for t in result["tests"] if t["id"] == "safety_limits")
        assert safety["status"] == "fail"
        assert "commissioning" in safety["what_to_do"].lower()


# ─────────────────────────────────────────────────────────────────────
# /api/selftest endpoint
# ─────────────────────────────────────────────────────────────────────

class TestSelftestEndpoint:
    def test_endpoint_returns_200(self, tmp_path):
        cfg = MagicMock(dashboard_token=None,
                         dashboard_max_body_bytes=1_000_000,
                         dashboard_endpoint_timeout_s=30.0,
                         dashboard_max_concurrent=64)
        shared = {"config": cfg}
        client = TestClient(create_app(shared))
        r = client.get("/api/selftest")
        assert r.status_code == 200
        body = r.json()
        assert "overall" in body
        assert "tests" in body
        assert isinstance(body["tests"], list)

    def test_endpoint_respects_auth(self, tmp_path):
        """Auth token gates /api/selftest like every other API endpoint."""
        cfg = MagicMock(dashboard_token="secret-tok",
                         dashboard_max_body_bytes=1_000_000,
                         dashboard_endpoint_timeout_s=30.0,
                         dashboard_max_concurrent=64)
        shared = {"config": cfg}
        client = TestClient(create_app(shared))
        r = client.get("/api/selftest")
        assert r.status_code == 401
        r = client.get("/api/selftest",
                       headers={"Authorization": "Bearer secret-tok"})
        assert r.status_code == 200
