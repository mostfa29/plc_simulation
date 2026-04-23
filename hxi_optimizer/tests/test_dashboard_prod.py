"""Production-readiness tests for the dashboard.

Covers the Tier 1 + Tier 2 hardening:
  - Bearer token auth (enabled + disabled modes)
  - Operator actions audited to audit.log
  - POST body validation via Pydantic
  - Global exception handler masks stack traces
  - /healthz liveness probe
  - Endpoint timeouts / 504 path
  - Body-size cap / 413 path
  - WebSocket rejects without token

Uses FastAPI's TestClient (in-process, no actual network).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hxi_optimizer.dashboard.server import create_app
from hxi_optimizer.io_logging.audit_logger import AuditLogger


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def audit_tmp(tmp_path):
    return AuditLogger(tmp_path / "audit.log")


@pytest.fixture
def shared(audit_tmp):
    """Minimal shared state — enough for the endpoints we test."""
    cfg = MagicMock()
    cfg.dashboard_token = None                    # auth disabled by default
    cfg.dashboard_max_body_bytes = 1_000_000
    cfg.dashboard_endpoint_timeout_s = 0.5        # short for timeout tests
    cfg.dashboard_max_concurrent = 64
    cfg.phase = MagicMock(value="A")
    cfg.drill_depth_ft = 3000.0
    cfg.plc_host = "127.0.0.1"
    cfg.nominal_setpoint = 60.0
    cfg.deadband_rpm = 2.0

    gate = MagicMock()
    gate.state = MagicMock(name="ADAPTING"); gate.state.name = "ADAPTING"
    gate.current_lower = 450; gate.current_upper = 600
    gate.lkg = MagicMock(lower=450, upper=600, iae_at_acceptance=0.05)
    gate.heartbeat_counter = 0
    gate.consecutive_rejections = 0
    gate.cooldown_until = 0.0
    gate.operator_disable = MagicMock()
    gate.operator_enable = MagicMock()

    modbus = MagicMock()
    modbus.is_healthy = True
    modbus.consecutive_failures = 0
    modbus.client = MagicMock(connected=True)
    modbus.transport_name = "modbus"

    advisor = MagicMock()
    advisor.state = MagicMock(trim_upper=0.0, trim_lower=0.0,
                                dwell_counter=0, total_adaptations=0)

    return {
        "config": cfg,
        "gate": gate,
        "modbus": modbus,
        "advisor": advisor,
        "monitor": MagicMock(),
        "audit": audit_tmp,
        "ring_buffer": [],
        "buffer_lock": MagicMock(__enter__=lambda s: None, __exit__=lambda *a: None),
        "latest_metrics": None,
        "alarms": [],
    }


@pytest.fixture
def client(shared):
    app = create_app(shared)
    return TestClient(app)


@pytest.fixture
def client_auth(shared):
    """Client with auth enabled via dashboard_token."""
    shared["config"].dashboard_token = "secret123"
    app = create_app(shared)
    return TestClient(app)


# ═════════════════════════════════════════════════════════════════════
# 1. /healthz — public, cheap, no auth
# ═════════════════════════════════════════════════════════════════════

class TestHealthz:
    def test_healthz_returns_200_without_auth(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["uptime_s"] >= 0

    def test_healthz_public_even_with_auth_enabled(self, client_auth):
        r = client_auth.get("/healthz")
        assert r.status_code == 200
        assert r.json()["auth_enabled"] is True

    def test_healthz_reports_modbus_health(self, client):
        r = client.get("/healthz")
        assert r.json()["modbus_healthy"] is True


# ═════════════════════════════════════════════════════════════════════
# 2. Bearer auth
# ═════════════════════════════════════════════════════════════════════

class TestAuth:
    def test_auth_disabled_allows_open_access(self, client):
        r = client.get("/api/status")
        assert r.status_code == 200

    def test_auth_enabled_rejects_no_token(self, client_auth):
        r = client_auth.get("/api/status")
        assert r.status_code == 401
        assert "unauthorized" in r.json()["error"]

    def test_auth_enabled_rejects_wrong_token(self, client_auth):
        r = client_auth.get("/api/status",
                            headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_auth_enabled_accepts_bearer(self, client_auth):
        r = client_auth.get("/api/status",
                            headers={"Authorization": "Bearer secret123"})
        assert r.status_code == 200

    def test_auth_accepts_query_token(self, client_auth):
        r = client_auth.get("/api/status?token=secret123")
        assert r.status_code == 200

    def test_root_html_public(self, client_auth):
        """The login form itself must load without a token."""
        r = client_auth.get("/")
        assert r.status_code == 200

    def test_auth_status_public(self, client_auth):
        r = client_auth.get("/api/auth/status")
        assert r.status_code == 200
        assert r.json()["auth_enabled"] is True

    def test_env_var_fallback(self, client, shared, monkeypatch):
        """HXI_DASHBOARD_TOKEN env var enables auth when config token is unset."""
        monkeypatch.setenv("HXI_DASHBOARD_TOKEN", "envtok")
        r = client.get("/api/status")
        assert r.status_code == 401
        r = client.get("/api/status",
                       headers={"Authorization": "Bearer envtok"})
        assert r.status_code == 200


# ═════════════════════════════════════════════════════════════════════
# 3. Audit logging of operator actions
# ═════════════════════════════════════════════════════════════════════

def _read_audit_rows(audit: AuditLogger):
    import csv
    with open(audit.filepath, newline="") as f:
        return list(csv.DictReader(f))


class TestOperatorAudit:
    def test_phase_change_writes_audit_row(self, client, shared, audit_tmp):
        r = client.post("/api/control/phase", json={"phase": "B"})
        assert r.status_code == 200
        rows = _read_audit_rows(audit_tmp)
        phase_rows = [r for r in rows if r["event_type"] == "DASHBOARD_PHASE_CHANGE"]
        assert len(phase_rows) == 1
        assert "->" in phase_rows[0]["reason"]

    def test_disable_writes_audit_row(self, client, audit_tmp):
        r = client.post("/api/control/disable")
        assert r.status_code == 200
        rows = _read_audit_rows(audit_tmp)
        dis = [r for r in rows if r["event_type"] == "DASHBOARD_DISABLE"]
        assert len(dis) == 1

    def test_depth_update_writes_audit_row(self, client, audit_tmp):
        r = client.post("/api/control/depth", json={"depth_ft": 8500})
        assert r.status_code == 200
        rows = _read_audit_rows(audit_tmp)
        dep = [r for r in rows if r["event_type"] == "DASHBOARD_DEPTH_UPDATE"]
        assert len(dep) == 1
        assert "8500" in dep[0]["reason"]

    def test_no_audit_row_on_validation_failure(self, client, audit_tmp):
        """A 422 validation error must NOT create an audit trail entry —
        the action never happened."""
        r = client.post("/api/control/depth", json={"depth_ft": -5})
        assert r.status_code in (400, 422)
        rows = _read_audit_rows(audit_tmp)
        dep = [r for r in rows if r["event_type"] == "DASHBOARD_DEPTH_UPDATE"]
        assert len(dep) == 0


# ═════════════════════════════════════════════════════════════════════
# 4. Pydantic body validation
# ═════════════════════════════════════════════════════════════════════

class TestBodyValidation:
    def test_invalid_phase_returns_422_or_400(self, client):
        r = client.post("/api/control/phase", json={})  # missing field
        assert r.status_code in (400, 422)

    def test_negative_depth_rejected(self, client):
        r = client.post("/api/control/depth", json={"depth_ft": -100})
        assert r.status_code in (400, 422)

    def test_absurdly_large_depth_rejected(self, client):
        r = client.post("/api/control/depth", json={"depth_ft": 1e9})
        assert r.status_code in (400, 422)

    def test_empty_annotate_label_rejected(self, client):
        r = client.post("/api/dataset/annotate", json={"label": ""})
        assert r.status_code in (400, 422)


# ═════════════════════════════════════════════════════════════════════
# 5. Global exception handler
# ═════════════════════════════════════════════════════════════════════

class TestExceptionHandler:
    def test_unexpected_exception_returns_500_not_traceback(self, shared):
        """If an endpoint blows up internally, the response body must be
        a short JSON error, not a multi-line Python traceback.
        """
        from hxi_optimizer.dashboard.server import create_app
        app = create_app(shared)

        @app.get("/api/boom")
        async def _boom():
            raise RuntimeError("internal detail that should NOT leak")

        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/boom")
        assert r.status_code == 500
        body = r.json()
        assert body["error"] == "internal server error"
        assert body["type"] == "RuntimeError"
        # Payload must NOT contain the private message
        assert "internal detail" not in json.dumps(body)


# ═════════════════════════════════════════════════════════════════════
# 6. Body-size cap
# ═════════════════════════════════════════════════════════════════════

class TestBodySizeLimit:
    def test_oversized_body_returns_413(self, shared):
        shared["config"].dashboard_max_body_bytes = 1_000  # tiny cap
        app = create_app(shared)
        client = TestClient(app)
        big = {"depth_ft": 3000, "padding": "x" * 2000}
        r = client.post("/api/control/depth", json=big)
        assert r.status_code == 413
        assert r.json()["max_bytes"] == 1_000


# ═════════════════════════════════════════════════════════════════════
# 7. Endpoint timeout (504)
# ═════════════════════════════════════════════════════════════════════

class TestEndpointTimeout:
    def test_cpu_bound_timeout_returns_504(self, shared):
        from hxi_optimizer.dashboard.server import _run_cpu_bound
        shared["config"].dashboard_endpoint_timeout_s = 0.1
        app = create_app(shared)

        def _sleep_forever():
            time.sleep(5.0)

        @app.get("/api/slow")
        async def _slow():
            return await _run_cpu_bound(shared, _sleep_forever)

        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/slow")
        assert r.status_code == 504
        assert "timeout" in r.json()["detail"].lower()


# ═════════════════════════════════════════════════════════════════════
# 8. WebSocket auth
# ═════════════════════════════════════════════════════════════════════

class TestWebSocketAuth:
    def test_ws_without_token_closes(self, client_auth):
        from starlette.testclient import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client_auth.websocket_connect("/ws") as ws:
                ws.receive_json()
        # 4401 = unauthorized (custom code, browsers see the close)
        assert exc_info.value.code == 4401

    def test_ws_with_token_accepts(self, client_auth, shared):
        with client_auth.websocket_connect("/ws?token=secret123") as ws:
            snap = ws.receive_json()
            assert "ts" in snap
            assert "connection" in snap
