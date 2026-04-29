"""Dashboard backend — FastAPI + WebSocket, runs inside the optimizer's asyncio loop.

Serves a single-page operator dashboard at http://localhost:8420.
Pushes live telemetry at 2 Hz via WebSocket at ws://localhost:8420/ws.
Accepts operator commands (enable/disable, phase promote, depth update)
via REST POST endpoints.

Usage from main.py:
    from hxi_optimizer.dashboard.server import create_app, start_dashboard
    app = create_app(shared_state)
    await start_dashboard(app, host="0.0.0.0", port=8420)
"""
from __future__ import annotations

import asyncio
import csv
import functools
import hmac
import json
import logging
import os
import time
import traceback
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Depends,
)
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import (
    HTTP_401_UNAUTHORIZED, HTTP_413_CONTENT_TOO_LARGE,
    HTTP_500_INTERNAL_SERVER_ERROR, HTTP_504_GATEWAY_TIMEOUT,
)

logger = logging.getLogger("dashboard")

STATIC_DIR = Path(__file__).parent / "static"
SERVER_START_TS = time.time()

# Paths served without auth — root HTML (so the login form can load), static
# assets, and /healthz for supervision probes. Everything else is gated.
PUBLIC_PATH_PREFIXES = ("/static", "/favicon.ico")
PUBLIC_PATHS_EXACT = {"/", "/healthz", "/api/auth/status"}


# ─────────────────────────────────────────────────────────────────────
# Pydantic request models (strict validation on state-changing endpoints)
# ─────────────────────────────────────────────────────────────────────

class PhaseBody(BaseModel):
    phase: str = Field(..., min_length=1, max_length=4)


class DepthBody(BaseModel):
    depth_ft: float = Field(..., gt=0, le=50000)


class AnnotateBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=64)
    lookback_s: float = Field(20.0, gt=0, le=600)
    notes: str = Field("", max_length=500)

    @field_validator("label")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class MachineSwitchBody(BaseModel):
    ewon_name: str = Field(..., min_length=1, max_length=128)


class AlarmDismissBody(BaseModel):
    ts: float | None = None
    all: bool = False


class SimulateBody(BaseModel):
    scenario: str = Field("normal", max_length=64)
    duration_s: float = Field(300.0, gt=0, le=600)
    setpoint: float = Field(60.0, ge=0, le=300)
    equipment_type: str | None = Field(None, max_length=64)
    params: dict = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────
# Auth + body-size middleware
# ─────────────────────────────────────────────────────────────────────

def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS_EXACT:
        return True
    for p in PUBLIC_PATH_PREFIXES:
        if path.startswith(p):
            return True
    return False


def _configured_token(shared: dict) -> str | None:
    """Return the configured auth token, or None if auth is disabled."""
    cfg = shared.get("config")
    token = getattr(cfg, "dashboard_token", None) if cfg else None
    if not token:
        token = os.environ.get("HXI_DASHBOARD_TOKEN")
    return token or None


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # Fallback: query param (needed for WebSocket handshakes where custom
    # headers are hard to attach from the browser).
    return request.query_params.get("token")


class AuthAndBodyMiddleware(BaseHTTPMiddleware):
    """Gate non-public routes behind Bearer auth + enforce body-size cap."""

    def __init__(self, app, shared: dict):
        super().__init__(app)
        self.shared = shared

    async def dispatch(self, request: Request, call_next):
        # Body-size cap — reject early before the endpoint reads the body
        cfg = self.shared.get("config")
        max_bytes = getattr(cfg, "dashboard_max_body_bytes", 1_000_000) if cfg else 1_000_000
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > max_bytes:
                    return JSONResponse(
                        {"error": "request body too large",
                         "max_bytes": max_bytes},
                        status_code=HTTP_413_CONTENT_TOO_LARGE,
                    )
            except ValueError:
                pass

        path = request.url.path
        if _is_public_path(path):
            return await call_next(request)

        token = _configured_token(self.shared)
        if token is None:
            # Auth disabled (dev / legacy) — let everything through
            return await call_next(request)

        supplied = _extract_token(request)
        if not supplied or not hmac.compare_digest(supplied, token):
            return JSONResponse(
                {"error": "unauthorized",
                 "hint": "send Authorization: Bearer <token> or ?token=<token>"},
                status_code=HTTP_401_UNAUTHORIZED,
            )
        return await call_next(request)


# ─────────────────────────────────────────────────────────────────────
# Timeout + executor helpers
# ─────────────────────────────────────────────────────────────────────

def _endpoint_timeout(shared: dict) -> float:
    cfg = shared.get("config")
    return float(getattr(cfg, "dashboard_endpoint_timeout_s", 30.0) if cfg else 30.0)


async def _run_cpu_bound(shared: dict, fn: Callable, *args, **kwargs):
    """Run a blocking CPU-bound function in the shared ThreadPoolExecutor
    under a bounded timeout, so a slow endpoint can't starve the 2 Hz
    read_loop or trip SafetyGate's COMMS_LOSS rollback.

    Raises HTTPException(504) on timeout.
    """
    loop = asyncio.get_event_loop()
    fut = loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))
    try:
        return await asyncio.wait_for(fut, timeout=_endpoint_timeout(shared))
    except asyncio.TimeoutError:
        raise HTTPException(
            HTTP_504_GATEWAY_TIMEOUT,
            f"endpoint exceeded {_endpoint_timeout(shared):.0f}s timeout",
        )


async def _with_timeout(shared: dict, coro: Awaitable):
    """Wrap an awaitable in the configured endpoint timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=_endpoint_timeout(shared))
    except asyncio.TimeoutError:
        raise HTTPException(
            HTTP_504_GATEWAY_TIMEOUT,
            f"endpoint exceeded {_endpoint_timeout(shared):.0f}s timeout",
        )


# ─────────────────────────────────────────────────────────────────────
# Audit helpers — dashboard operator actions land in the same audit.log
# as PLC writes so there's one durable source of truth for "what happened".
# ─────────────────────────────────────────────────────────────────────

def _audit_operator(shared: dict, event_type: str, reason: str) -> None:
    audit = shared.get("audit")
    if audit is None:
        logger.warning(f"no audit logger — would have written {event_type}: {reason}")
        return
    try:
        audit.log_event(event_type=f"DASHBOARD_{event_type}", reason=reason)
    except Exception as e:
        logger.error(f"audit write failed for {event_type}: {e}")


def create_app(shared: dict) -> FastAPI:
    """Create the FastAPI app wired to the optimizer's shared state dict.

    `shared` must contain references to:
      - gate: SafetyGate
      - advisor: PIDAdvisor
      - monitor: PerformanceMonitor
      - config: Config
      - modbus: ModbusManager
      - buffer_lock: threading.Lock
      - ring_buffer: deque
      - latest_metrics: PerformanceMetrics | None
      - shutdown_event: asyncio.Event
      - csv_queue: queue.Queue (for log path)
    """
    app = FastAPI(title="HXI Optimizer Dashboard", docs_url=None, redoc_url=None)
    app.state.shared = shared

    # ─── Auth + body-size middleware (runs before every route) ──────────
    app.add_middleware(AuthAndBodyMiddleware, shared=shared)

    # ─── Global exception handler — mask stack traces, log with context ─
    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        # HTTPException is handled by FastAPI's default handler before this
        logger.error(
            f"unhandled error at {request.method} {request.url.path}: "
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
        return JSONResponse(
            {"error": "internal server error",
             "type": type(exc).__name__,
             "path": str(request.url.path)},
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        )

    @app.exception_handler(ValidationError)
    async def _validation_handler(request: Request, exc: ValidationError):
        return JSONResponse({"error": "validation failed",
                             "detail": exc.errors()}, status_code=422)

    # ─── Static files ───────────────────────────────────────────────────
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ─── Root → dashboard HTML ──────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # ─── Liveness probe ────────────────────────────────────────────────
    @app.get("/healthz")
    async def api_healthz():
        """Cheap liveness probe for NSSM/supervisors. No auth required.

        Returns 200 if the dashboard process is up and the shared state
        looks wired. Does NOT check PLC connectivity — that's
        /api/diagnostics (a deeper check that does).
        """
        modbus = shared.get("modbus")
        return {
            "status": "ok",
            "uptime_s": round(time.time() - SERVER_START_TS, 1),
            "modbus_healthy": modbus.is_healthy if modbus else None,
            "auth_enabled": _configured_token(shared) is not None,
        }

    # ─── Auth status (public) — so the login form knows if auth is on ──
    @app.get("/api/auth/status")
    async def api_auth_status():
        return {"auth_enabled": _configured_token(shared) is not None}

    # ─── System self-test battery (for non-technical operators) ────────
    @app.get("/api/selftest")
    async def api_selftest():
        """Run the full system health-check battery.

        Returns pass/fail/warn for each check + plain-language explanation
        of what to do if it fails. Used by the dashboard "System Test" tab
        so non-technical operators can verify the system without running
        pytest from a terminal.

        Read-only; safe to run at any time. ~12 checks, <500 ms.
        """
        from hxi_optimizer.dashboard.selftest import run_self_tests
        return run_self_tests(shared)

    # ─── Snapshot REST endpoint (for initial load / polling fallback) ───
    @app.get("/api/status")
    async def api_status():
        return _build_snapshot(shared)

    # ─── Audit log endpoint ────────────────────────────────────────────
    @app.get("/api/audit")
    async def api_audit():
        audit = shared.get("audit")
        if not audit or not audit.filepath.exists():
            return []
        rows = []
        with open(audit.filepath, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return rows[-200:]  # last 200 entries

    # ─── Operator controls ─────────────────────────────────────────────
    # Every state-changing endpoint writes to audit.log so there's a durable
    # record of what the operator asked for — SafetyGate already audits
    # accepted/rejected PLC writes, but without these we had no trail of
    # *who asked* for a phase promotion or bounds override.
    @app.post("/api/control/disable")
    async def control_disable():
        gate = shared.get("gate")
        if not gate:
            raise HTTPException(503, "Gate not initialized")
        gate.operator_disable()
        _audit_operator(shared, "DISABLE",
                         f"state_after={gate.state.name}")
        logger.info("DASHBOARD: operator DISABLED adaptive")
        return {"status": "disabled", "state": gate.state.name}

    @app.post("/api/control/enable")
    async def control_enable():
        gate = shared.get("gate")
        if not gate:
            raise HTTPException(503, "Gate not initialized")
        gate.operator_enable()
        _audit_operator(shared, "ENABLE",
                         f"state_after={gate.state.name}")
        logger.info("DASHBOARD: operator ENABLED adaptive")
        return {"status": "enabled", "state": gate.state.name}

    @app.post("/api/control/phase")
    async def control_phase(body: PhaseBody):
        config = shared.get("config")
        if not config:
            raise HTTPException(503, "Config not loaded")
        new_phase = body.phase.upper()
        from hxi_optimizer.hxi_config import Phase
        try:
            old = config.phase.value
            config.phase = Phase(new_phase)
        except ValueError:
            raise HTTPException(400, f"Invalid phase: {new_phase}")
        _audit_operator(shared, "PHASE_CHANGE",
                         f"{old} -> {config.phase.value}")
        logger.info(f"DASHBOARD: phase changed {old} -> {new_phase}")
        return {"phase": config.phase.value}

    @app.post("/api/control/depth")
    async def control_depth(body: DepthBody):
        config = shared.get("config")
        if not config:
            raise HTTPException(503, "Config not loaded")
        old = config.drill_depth_ft
        config.drill_depth_ft = float(body.depth_ft)
        _audit_operator(shared, "DEPTH_UPDATE",
                         f"{old:.0f}ft -> {config.drill_depth_ft:.0f}ft")
        logger.info(f"DASHBOARD: drill depth updated to {body.depth_ft} ft")
        return {"drill_depth_ft": config.drill_depth_ft}

    # ─── Register catalog from Excel ──────────────────────────────────
    _catalog = None
    _catalog_json = None

    def _get_catalog():
        nonlocal _catalog, _catalog_json
        if _catalog is None:
            from pathlib import Path
            xlsx = Path("Register_List.xlsx")
            if not xlsx.exists():
                return None, []
            from hxi_optimizer.comms.register_scanner import (
                load_register_catalog, catalog_to_json, catalog_to_read_plan,
            )
            _catalog = load_register_catalog(xlsx)
            _catalog_json = catalog_to_json(_catalog)
        return _catalog, _catalog_json

    @app.get("/api/registers")
    async def api_registers():
        """Full register catalog parsed from Register_List.xlsx."""
        cat, js = _get_catalog()
        if cat is None:
            raise HTTPException(404, "Register_List.xlsx not found")
        return {
            "total": len(js),
            "active": sum(1 for r in js if not r["is_spare"]),
            "read_blocks": cat.read_blocks,
            "sections": list(cat.sections.keys()),
            "registers": js,
        }

    @app.get("/api/registers/scan")
    async def api_register_scan():
        """Live-read ALL non-spare registers from PLC and return decoded values."""
        cat, _ = _get_catalog()
        modbus = shared.get("modbus")
        if cat is None:
            raise HTTPException(404, "Register_List.xlsx not found")
        if not modbus or not modbus.is_healthy:
            raise HTTPException(503, "PLC not connected")
        results = []
        for block in cat.read_blocks:
            raw = await modbus.safe_read(block["start"], block["count"])
            if raw is None:
                for r in cat.registers:
                    if block["start"] <= r.address < block["start"] + block["count"]:
                        results.append({
                            "name": r.name, "ge_address": r.ge_address,
                            "section": r.section, "dtype": r.dtype,
                            "bit": r.bit, "value": None, "raw": None, "error": "read_failed",
                        })
                continue
            for r in cat.registers:
                offset = r.address - block["start"]
                if offset < 0 or offset >= len(raw):
                    continue
                val, raw_val = _decode_register_value(r, raw, offset)
                results.append({
                    "name": r.name, "ge_address": r.ge_address,
                    "section": r.section, "dtype": r.dtype,
                    "bit": r.bit, "is_spare": r.is_spare,
                    "value": val, "raw": raw_val,
                })
        return {"ts": time.time(), "register_count": len(results), "values": results}

    # ─── eCatcher tunnel status ────────────────────────────────────────
    @app.get("/api/ecatcher")
    async def api_ecatcher():
        """Current eCatcher / Talk2m tunnel detection state."""
        state = shared.get("ecatcher_state")
        monitor = shared.get("ecatcher_monitor")
        info = {
            "state": state,
            "log_path": str(monitor.log_path) if monitor and monitor.log_path else None,
            "talk2m_configured": bool(monitor and monitor.t2m_account) if monitor else False,
        }
        return info

    @app.post("/api/ecatcher/probe")
    async def api_ecatcher_probe():
        """Force an immediate eCatcher poll (don't wait for the 30 s cycle)."""
        monitor = shared.get("ecatcher_monitor")
        if not monitor:
            raise HTTPException(503, "eCatcher monitor not initialized")
        state = await monitor.detect()
        shared["ecatcher_state"] = state.to_json()
        return state.to_json()

    # ─── Real-time dataset capture (for model fine-tuning) ─────────────
    @app.get("/api/dataset/summary")
    async def api_dataset_summary():
        cap = shared.get("dataset_capture")
        if not cap:
            return {"enabled": False, "reason": "Dataset capture disabled in config"}
        return {"enabled": True, **cap.summary()}

    @app.post("/api/dataset/annotate")
    async def api_dataset_annotate(body: AnnotateBody):
        """Capture the last N seconds of telemetry with an operator-supplied label."""
        cap = shared.get("dataset_capture")
        if not cap:
            raise HTTPException(503, "Dataset capture disabled")
        meta = cap.capture_now(label=body.label, notes=body.notes,
                                 lookback_s=body.lookback_s)
        if not meta:
            raise HTTPException(400, "Not enough telemetry in buffer yet")
        _audit_operator(shared, "ANNOTATE",
                         f"label={body.label} n={meta.n_samples}")
        logger.info(f"DASHBOARD: operator annotation '{body.label}' "
                     f"({meta.n_samples} samples)")
        return meta.to_json()

    # ─── Current machine + history ─────────────────────────────────────
    @app.get("/api/machine/current")
    async def api_machine_current():
        """Full record of the machine the optimizer is currently connected to."""
        rec = shared.get("machine_record")
        if not rec:
            return {"identified": False, "reason": "No fleet match for current host"}
        return {"identified": True, **rec.to_json()}

    @app.get("/api/machine/history")
    async def api_machine_history():
        """Every machine this optimizer instance has ever connected to."""
        store = shared.get("machine_store")
        if not store:
            raise HTTPException(503, "Machine state store not initialized")
        return {
            "current_ewon_name": store.current_ewon_name,
            "current_plc_ip": store.current_plc_ip,
            "machine_count": store.machine_count(),
            "session_start": store.session_start,
            "history": [h.to_json() for h in store.history.values()],
            "recent_events": [e.to_json() for e in store.recent_events(50)],
        }

    @app.post("/api/machine/switch")
    async def api_machine_switch(body: MachineSwitchBody):
        """Manually force the optimizer to treat the PLC as a specific machine.
        Useful when auto-detect can't identify the rig. Does NOT restart comms."""
        registry = shared.get("machine_registry")
        store = shared.get("machine_store")
        config = shared.get("config")
        if not registry or not store:
            raise HTTPException(503, "Registry not initialized")
        match = registry.get_for_rig(body.ewon_name)
        if not match:
            raise HTTPException(
                404, f"No machine named {body.ewon_name!r} in fleet catalog")
        old = store.current_ewon_name
        if config:
            match.plc_ip = config.plc_host
        store.note_connection(match, reason="manual")
        shared["machine_record"] = match
        store.save()
        _audit_operator(shared, "MACHINE_SWITCH",
                         f"{old or '<none>'} -> {match.ewon_name}")
        logger.info(f"DASHBOARD: operator switched identity -> {match.ewon_name}")
        return {"switched_to": match.ewon_name,
                "equipment_type": match.equipment_type,
                "register_count": len(match.register_map.registers)}

    # ─── Intelligence layer (diagnosis + trends + digest + fleet triage) ─
    @app.get("/api/intel/diagnose")
    async def api_intel_diagnose():
        """Run the rule+ML diagnosis engine on the current metrics."""
        from hxi_optimizer.intelligence.diagnosis import (
            DiagnosisEngine, diagnose_temperature,
        )
        metrics = shared.get("latest_metrics")
        if metrics is None:
            return {"ready": False, "reason": "no metrics yet"}
        config = shared.get("config")
        machine = shared.get("machine_record")
        ring = shared.get("ring_buffer")
        buffer_lock = shared.get("buffer_lock")
        latest_live = {}
        if ring and buffer_lock:
            with buffer_lock:
                if ring:
                    latest_live = dict(ring[-1])
        engine = DiagnosisEngine()
        diags = engine.diagnose(
            metrics=metrics,
            machine=machine,
            depth_ft=(config.drill_depth_ft if config else 3000.0),
        )
        # Temperature rule runs separately
        temp = latest_live.get("loop_temp")
        if temp is not None:
            t_diag = diagnose_temperature(float(temp), engine)
            if t_diag is not None:
                diags.insert(0, t_diag)
        return {
            "ready": True,
            "count": len(diags),
            "diagnoses": [d.to_json() for d in diags],
        }

    @app.get("/api/intel/trends")
    async def api_intel_trends(hours: float = 48.0):
        """Multi-day trend analysis from CSV logs. CPU-heavy — runs in the
        executor under the dashboard timeout so it can't starve read_loop.
        """
        from hxi_optimizer.intelligence.trend_analyzer import TrendAnalyzer

        def _run():
            analyzer = TrendAnalyzer(log_dir="hxi_optimizer/logs")
            return analyzer.analyze(hours=hours)

        findings = await _run_cpu_bound(shared, _run)
        return {
            "hours": hours,
            "count": len(findings),
            "findings": [f.to_json() for f in findings],
        }

    @app.get("/api/intel/digest")
    async def api_intel_digest():
        """Plain-language 'right now' summary for the dashboard banner."""
        from hxi_optimizer.intelligence.diagnosis import (
            DiagnosisEngine, diagnose_temperature,
        )
        from hxi_optimizer.intelligence.digest import summarize_now
        from hxi_optimizer.intelligence.trend_analyzer import TrendAnalyzer

        metrics = shared.get("latest_metrics")
        if metrics is None:
            return {"ready": False}
        snapshot = _build_snapshot(shared)
        config = shared.get("config")
        engine = DiagnosisEngine()
        diags = engine.diagnose(
            metrics=metrics,
            depth_ft=(config.drill_depth_ft if config else 3000.0),
        )
        # Temperature
        temp = snapshot.get("live", {}).get("loop_temp")
        if temp is not None:
            t_diag = diagnose_temperature(float(temp), engine)
            if t_diag is not None:
                diags.insert(0, t_diag)
        trends = TrendAnalyzer(log_dir="hxi_optimizer/logs").analyze()
        digest = summarize_now(snapshot, diags, trends)
        return {"ready": True, **digest.to_json()}

    @app.get("/api/intel/triage")
    async def api_intel_triage():
        """Fleet-wide attention ranking."""
        from hxi_optimizer.intelligence.fleet_triage import FleetTriage
        store = shared.get("machine_store")
        registry = shared.get("machine_registry")
        if store is None:
            return {"ready": False, "reason": "no machine store"}
        fleet = registry.fleet if registry else None
        triage = FleetTriage(store, fleet)
        ranked = triage.rank()
        return {
            "ready": True,
            "summary": triage.summary(),
            "rigs": [r.to_json() for r in ranked[:50]],
        }

    # ─── A/B model comparison ─────────────────────────────────────────
    @app.get("/api/intel/compare-models")
    async def api_intel_compare_models(rig: str | None = None,
                                        n_max: int = 500):
        """Run the default sim classifier and the per-rig fine-tune against
        the same captured real episodes; report per-model accuracy + a
        promote/rollback recommendation.

        Query params:
          rig   — rig name (defaults to the currently-connected machine)
          n_max — cap on the number of windows to score (default 500)

        Returns ok=False if no labeled real data exists yet for the rig.
        """
        from training.fine_tune import load_real_episodes, CLASSES
        from hxi_optimizer.intelligence.compare_models import compare_pairs
        from hxi_optimizer.intelligence.model_registry import ModelRegistry

        # Resolve rig: explicit param > current connected machine
        if not rig:
            record = shared.get("machine_record")
            if record is not None:
                rig = record.ewon_name
        if not rig:
            return {"ok": False,
                    "reason": "No rig specified and no machine connected"}

        registry = shared.get("model_registry") or ModelRegistry()

        # Load captured real episodes for this rig
        try:
            X, y, _machines, stats = load_real_episodes(rig)
        except Exception as e:
            raise HTTPException(500, f"Dataset load failed: {e}")

        if len(X) == 0:
            return {
                "ok": False,
                "rig_name": rig,
                "reason": ("No labeled real episodes for this rig yet. "
                            "Capture some with the dashboard 'Annotate' "
                            "buttons, then retry."),
                "n_episodes": int(stats.n_episodes),
                "has_per_rig": registry.has_per_rig(rig),
            }

        # Cap window count so the endpoint stays responsive
        if len(X) > n_max:
            import numpy as _np
            # Deterministic subsample (seed=7) so repeated calls are stable
            rng = _np.random.default_rng(7)
            idx = rng.choice(len(X), size=n_max, replace=False)
            X = X[idx]
            y = y[idx]

        # ONNX inference for both models runs in the executor with a
        # timeout — two full model sweeps over up to n_max windows would
        # otherwise block the 2 Hz read_loop.
        comparison = await _run_cpu_bound(
            shared, compare_pairs, registry, rig, CLASSES, X, y,
        )
        comparison["ok"] = True
        comparison["n_episodes"] = int(stats.n_episodes)
        comparison["label_distribution"] = stats.by_label
        return comparison

    # ─── Fleet catalog ─────────────────────────────────────────────────
    @app.get("/api/fleet")
    async def api_fleet():
        """Full fleet catalog (all eWon devices Steve operates)."""
        try:
            from hxi_optimizer.comms.fleet import FleetCatalog, EQUIPMENT_CATALOG
        except ImportError:
            raise HTTPException(500, "Fleet module unavailable")
        catalog = FleetCatalog.load()
        model_registry = shared.get("model_registry")
        if model_registry is None:
            try:
                from hxi_optimizer.intelligence.model_registry import ModelRegistry
                model_registry = ModelRegistry()
            except Exception:
                model_registry = None
        devices = []
        for d in catalog.devices:
            spec = EQUIPMENT_CATALOG.get(d.equipment_type, EQUIPMENT_CATALOG["unknown"])
            has_ft = (model_registry.has_per_rig(d.ewon_name)
                      if model_registry else False)
            devices.append({
                "ewon_name": d.ewon_name,
                "equipment_type": d.equipment_type,
                "customer": d.customer,
                "description": d.description,
                "firmware": d.firmware,
                "status": d.status,
                "has_profile": d.profile_path is not None,
                "profile_file": d.profile_path.name if d.profile_path else None,
                "has_per_rig_model": has_ft,
                "spec": {
                    "display_name": spec.display_name,
                    "horsepower": spec.horsepower,
                    "gear_ratio": spec.gear_ratio,
                    "max_torque_ft_lbs": spec.max_torque_ft_lbs,
                    "max_rpm": spec.max_rpm,
                    "plc_type": spec.plc_type,
                    "register_convention": spec.register_convention,
                },
            })
        # Current connected machine (try to identify from config)
        config = shared.get("config")
        current = None
        if config:
            ewon = getattr(config, "ewon_name", None)
            ident = catalog.identify_by_name(ewon) if ewon else None
            if not ident and config.plc_host:
                ident = catalog.identify_by_ip(config.plc_host)
            if ident:
                current = {
                    "ewon_name": ident.ewon_name,
                    "equipment_type": ident.equipment_type,
                    "display_name": ident.spec.display_name,
                    "customer": ident.customer,
                    "source": ident.source,
                    "confidence": ident.confidence,
                }
        return {
            "total": len(devices),
            "summary": catalog.summary(),
            "current_machine": current,
            "devices": devices,
        }

    # ─── Per-rig model registry ────────────────────────────────────────
    @app.get("/api/models")
    async def api_models():
        """List fine-tuned per-rig models alongside the default sim pair.

        The optimizer auto-selects a per-rig model when the connected rig
        has one. Returns provenance for every pair + which one is live.
        """
        registry = shared.get("model_registry")
        if registry is None:
            try:
                from hxi_optimizer.intelligence.model_registry import ModelRegistry
                registry = ModelRegistry()
            except Exception as e:
                raise HTTPException(500, f"Model registry unavailable: {e}")
        summary = registry.per_rig_summary()

        # Enrich with meta.json contents (accuracy, recalibration, etc.)
        for entry in summary.get("per_rig", []):
            pair = next((p for p in registry.list_all()
                         if p.rig_slug == entry["rig_slug"]
                         and p.source == "per_rig"), None)
            if pair:
                entry["meta"] = registry.meta_for(pair)

        # Live info: which pair the running PerformanceMonitor is using
        monitor = shared.get("monitor")
        live = None
        if monitor is not None and hasattr(monitor, "loaded_models_info"):
            try:
                live = monitor.loaded_models_info()
            except Exception as e:
                live = {"error": str(e)}
        summary["live"] = live

        # Current rig for convenience
        record = shared.get("machine_record")
        if record is not None:
            summary["current_rig"] = {
                "ewon_name": record.ewon_name,
                "equipment_type": record.equipment_type,
                "has_per_rig_model": registry.has_per_rig(record.ewon_name),
            }
        return summary

    # ─── Simulation sandbox ────────────────────────────────────────────
    @app.post("/api/simulate")
    async def api_simulate(body: SimulateBody):
        """Run a simulation scenario and return the time series + metrics.

        Heavy work runs in the executor with a bounded timeout so a long
        scenario can't starve the 2 Hz PLC read_loop.
        """
        from training.scenarios import ALL_GENERATORS

        gen = ALL_GENERATORS.get(body.scenario)
        if not gen:
            raise HTTPException(400, f"Unknown scenario: {body.scenario}. "
                                f"Available: {list(ALL_GENERATORS.keys())}")

        import inspect
        sig = inspect.signature(gen)
        duration = min(body.duration_s, 600.0)
        kwargs = {"duration_s": duration, "setpoint": body.setpoint, "seed": 42}
        if body.equipment_type and "equipment_type" in sig.parameters:
            kwargs["equipment_type"] = body.equipment_type
        for k, v in body.params.items():
            if k in sig.parameters:
                kwargs[k] = v

        def _run_sim_and_metrics():
            samples, labels = gen(**kwargs)
            from hxi_optimizer.monitoring.performance_metrics import PerformanceMonitor
            mon = PerformanceMonitor(window_sec=20.0, deadband_rpm=2.0)
            metrics_snapshots = []
            for i, s in enumerate(samples):
                mon.update(s["rpm_encoder"], s["ss_setpoint_fwd"],
                           s["swash_output"], s["active_lower"], s["active_upper"])
                if i > 0 and i % 20 == 0:
                    m = mon.compute_metrics(s["ss_setpoint_fwd"],
                                             s["active_lower"], s["active_upper"])
                    metrics_snapshots.append({
                        "t": s["ts"],
                        "dniae": round(m.dniae, 4),
                        "mode": m.failure_mode,
                        "confidence": round(m.failure_confidence, 2),
                        "sat_total": round(m.sat_total, 3),
                        "rmse": round(m.rmse_rpm, 2),
                        "mean_error": round(m.mean_error, 2),
                        "is_windup": m.is_windup,
                        "change_detected": m.change_detected,
                    })
            return samples, labels, metrics_snapshots

        samples, labels, metrics_snapshots = await _run_cpu_bound(
            shared, _run_sim_and_metrics)

        label_counts = {}
        for l in labels:
            label_counts[l] = label_counts.get(l, 0) + 1

        return {
            "scenario": body.scenario,
            "duration_s": duration,
            "sample_count": len(samples),
            "label_distribution": label_counts,
            "timeseries": {
                "ts": [s["ts"] for s in samples],
                "rpm": [s["rpm_encoder"] for s in samples],
                "setpoint": [s["ss_setpoint_fwd"] for s in samples],
                "swash": [s["swash_output"] for s in samples],
                "torque": [s["delivered_torque"] for s in samples],
                "temp": [s["loop_temp"] for s in samples],
                "lower": [s["active_lower"] for s in samples],
                "upper": [s["active_upper"] for s in samples],
                "labels": labels,
            },
            "metrics": metrics_snapshots,
        }

    @app.get("/api/simulate/scenarios")
    async def api_scenarios():
        """List available simulation scenarios with parameters."""
        import inspect
        from training.scenarios import ALL_GENERATORS
        out = {}
        for name, gen in ALL_GENERATORS.items():
            sig = inspect.signature(gen)
            params = {}
            for pname, p in sig.parameters.items():
                if pname == "seed":
                    continue
                params[pname] = {
                    "default": p.default if p.default is not inspect.Parameter.empty else None,
                    "type": str(p.annotation) if p.annotation is not inspect.Parameter.empty else "any",
                }
            out[name] = {"params": params}
        return out

    # ─── History buffer for charts (60 s rolling at 2 Hz = 120 samples) ─
    shared.setdefault("history", {
        "ts": [], "rpm": [], "setpoint": [], "swash": [],
        "lower": [], "upper": [], "torque": [], "temp": [],
        "dniae": [], "sat_total": [], "heartbeat": [],
        "mode": [], "state": [],
    })
    HISTORY_MAX = 240  # 120 s at 2 Hz

    def _push_history(snapshot: dict) -> None:
        h = shared["history"]
        live = snapshot.get("live", {})
        met = snapshot.get("metrics", {}) or {}
        safety = snapshot.get("safety", {}) or {}
        h["ts"].append(snapshot["ts"])
        h["rpm"].append(_safe_float(live.get("rpm")))
        h["setpoint"].append(_safe_float(live.get("setpoint")))
        h["swash"].append(_safe_float(live.get("swash_output")))
        h["lower"].append(_safe_float(live.get("active_lower")))
        h["upper"].append(_safe_float(live.get("active_upper")))
        h["torque"].append(_safe_float(live.get("delivered_torque")))
        h["temp"].append(_safe_float(live.get("loop_temp")))
        h["dniae"].append(_safe_float(met.get("dniae")))
        h["sat_total"].append(_safe_float(met.get("sat_total")))
        h["heartbeat"].append(safety.get("heartbeat_counter", 0))
        h["mode"].append(met.get("failure_mode", "NORMAL"))
        h["state"].append(snapshot.get("state_machine", ""))
        for k in h:
            if len(h[k]) > HISTORY_MAX:
                h[k] = h[k][-HISTORY_MAX:]

    # Background history pusher — runs independently of WebSocket clients
    async def _history_background():
        while True:
            try:
                _push_history(_build_snapshot(shared))
            except Exception as e:
                logger.debug(f"history pusher: {e}")
            await asyncio.sleep(0.5)

    @app.on_event("startup")
    async def _start_history_task():
        asyncio.create_task(_history_background())

    @app.get("/api/history")
    async def api_history():
        """Rolling 120 s telemetry buffer for charts."""
        return shared.get("history", {})

    # ─── Config view/edit endpoints ────────────────────────────────────
    @app.get("/api/config")
    async def api_config():
        """Current optimizer configuration (sanitised for display)."""
        c = shared.get("config")
        if not c:
            raise HTTPException(503, "Config not loaded")
        return {
            "plc_host": c.plc_host,
            "plc_port": c.plc_port,
            "unit_id": c.unit_id,
            "modbus_timeout": c.modbus_timeout,
            "read_start_addr": c.read_start_addr,
            "read_count": c.read_count,
            "read_interval": c.read_interval,
            "analysis_interval": c.analysis_interval,
            "write_interval": c.write_interval,
            "nominal_setpoint": c.nominal_setpoint,
            "deadband_rpm": c.deadband_rpm,
            "phase": c.phase.value,
            "drill_depth_ft": c.drill_depth_ft,
            "osc_enabled": c.osc_enabled,
            "require_verified_word_order": c.require_verified_word_order,
            "transport": getattr(c, "transport", "modbus"),
            "opcua_port": getattr(c, "opcua_port", 4840),
            "safety": {
                "abs_min_lower": c.safety.abs_min_lower,
                "abs_max_lower": c.safety.abs_max_lower,
                "abs_min_upper": c.safety.abs_min_upper,
                "abs_max_upper": c.safety.abs_max_upper,
                "min_band_counts": c.safety.min_band_counts,
            },
        }

    # ─── Diagnostics ─────────────────────────────────────────────────
    @app.get("/api/diagnostics")
    async def api_diagnostics():
        """Runtime diagnostics: connection metrics, counters, word-order, etc."""
        gate = shared.get("gate")
        modbus = shared.get("modbus")
        advisor = shared.get("advisor")
        monitor = shared.get("monitor")
        config = shared.get("config")
        from hxi_optimizer.comms.register_map import VERIFIED_WORD_ORDER
        return {
            "transport": modbus.transport_name if modbus else "?",
            "transport_healthy": modbus.is_healthy if modbus else False,
            "consecutive_failures": modbus.consecutive_failures if modbus else 0,
            "verified_word_order": VERIFIED_WORD_ORDER,
            "heartbeat_counter": gate.heartbeat_counter if gate else 0,
            "gate_state": gate.state.name if gate else "UNKNOWN",
            "total_adaptations": advisor.state.total_adaptations if advisor else 0,
            "consecutive_rejections": gate.consecutive_rejections if gate else 0,
            "cooldown_remaining": max(0, gate.cooldown_until - time.time()) if gate else 0,
            "advisor_dwell": round(advisor.state.dwell_counter, 2) if advisor else 0,
            "cusum_baseline_collected": monitor._baseline_collected if monitor else False,
            "cusum_mean_alarms": monitor.cusum_mean.alarm_count if monitor else 0,
            "cusum_var_alarms": monitor.cusum_var.alarm_count if monitor else 0,
            "cusum_osc_alarms": monitor.cusum_osc.alarm_count if monitor else 0,
            "current_phase": config.phase.value if config else "?",
            "drill_depth_ft": config.drill_depth_ft if config else 0,
        }

    # ─── Logs tail endpoint ──────────────────────────────────────────
    @app.get("/api/logs")
    async def api_logs(lines: int = 200):
        """Return the last N lines of optimizer.log."""
        log_path = Path("hxi_optimizer/logs/optimizer.log")
        if not log_path.exists():
            return {"lines": [], "source": str(log_path)}
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            return {
                "lines": all_lines[-lines:],
                "total": len(all_lines),
                "source": str(log_path),
            }
        except Exception as e:
            raise HTTPException(500, f"Log read failed: {e}")

    # ─── Training status ─────────────────────────────────────────────
    @app.get("/api/training/status")
    async def api_training_status():
        """Which models are deployed, last training time, versions."""
        models_dir = Path("hxi_optimizer/models")
        training_models = Path("training/models")
        result = {
            "deployed_models": [],
            "training_artefacts": [],
            "reload_flag": False,
        }
        if models_dir.exists():
            for f in models_dir.glob("*.tflite"):
                stat = f.stat()
                result["deployed_models"].append({
                    "name": f.name,
                    "size_bytes": stat.st_size,
                    "modified": stat.st_mtime,
                })
            reload_flag = models_dir / ".reload"
            if reload_flag.exists():
                result["reload_flag"] = True
                result["reload_timestamp"] = reload_flag.stat().st_mtime
        if training_models.exists():
            for d in training_models.iterdir():
                if d.is_dir():
                    tflite = list(d.glob("*.tflite"))
                    result["training_artefacts"].append({
                        "name": d.name,
                        "has_tflite": len(tflite) > 0,
                        "modified": d.stat().st_mtime,
                    })
        return result

    # ─── Alarms / events stream ──────────────────────────────────────
    shared.setdefault("alarms", [])

    def _push_alarm(severity: str, message: str, source: str = "system") -> None:
        """Push a notification into the dashboard's alarm queue."""
        alarm = {
            "ts": time.time(),
            "severity": severity,  # info | warn | critical
            "message": message,
            "source": source,
            "id": int(time.time() * 1000),
        }
        alarms = shared["alarms"]
        alarms.append(alarm)
        if len(alarms) > 100:
            del alarms[0:len(alarms) - 100]

    shared["push_alarm"] = _push_alarm

    @app.get("/api/alarms")
    async def api_alarms(since: float = 0):
        """Alarms newer than `since` (unix timestamp)."""
        alarms = shared.get("alarms", [])
        return [a for a in alarms if a["ts"] > since]

    @app.post("/api/alarms/dismiss")
    async def api_alarms_dismiss():
        """Clear the alarm queue."""
        shared["alarms"] = []
        return {"status": "cleared"}

    # ─── Transport info ───────────────────────────────────────────────
    @app.get("/api/transport")
    async def api_transport():
        modbus = shared.get("modbus")
        config = shared.get("config")
        if not modbus:
            raise HTTPException(503, "Transport not initialized")
        return {
            "name": modbus.transport_name,
            "healthy": modbus.is_healthy,
            "consecutive_failures": modbus.consecutive_failures,
            "connected": modbus.client.connected if hasattr(modbus, "client") else False,
            "host": config.plc_host if config else "",
            "port": getattr(config, "opcua_port" if getattr(config, "transport", "modbus") == "opcua" else "plc_port", None),
        }

    # ─── CSV data export ─────────────────────────────────────────────
    @app.get("/api/export/history.csv")
    async def api_export_history():
        """Download the current history buffer as CSV."""
        from fastapi.responses import PlainTextResponse
        h = shared.get("history", {})
        if not h.get("ts"):
            return PlainTextResponse("timestamp\n", media_type="text/csv")
        cols = ["ts", "rpm", "setpoint", "swash", "lower", "upper",
                "torque", "temp", "dniae", "sat_total", "heartbeat", "mode", "state"]
        lines = [",".join(cols)]
        n = len(h["ts"])
        for i in range(n):
            row = []
            for c in cols:
                v = h.get(c, [None] * n)[i] if i < len(h.get(c, [])) else ""
                row.append("" if v is None else str(v))
            lines.append(",".join(row))
        return PlainTextResponse("\n".join(lines), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=history.csv"})

    # ─── WebSocket — 2 Hz live telemetry push ──────────────────────────
    # Active connections are tracked in `shared["ws_clients"]` so the
    # graceful-shutdown hook can send close frames before killing the
    # asyncio loop. BaseHTTPMiddleware doesn't run for WebSocket, so auth
    # is checked inline here against ?token=... query param.
    shared.setdefault("ws_clients", set())

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        token = _configured_token(shared)
        if token is not None:
            supplied = ws.query_params.get("token", "")
            if not supplied or not hmac.compare_digest(supplied, token):
                await ws.close(code=4401)     # 4401: unauthorized
                return
        await ws.accept()
        shared["ws_clients"].add(ws)
        logger.info("Dashboard WebSocket client connected "
                    f"(total={len(shared['ws_clients'])})")
        shutdown_event: asyncio.Event | None = shared.get("shutdown_event")
        try:
            last_alarm_ts = 0.0
            while True:
                if shutdown_event is not None and shutdown_event.is_set():
                    await ws.close(code=1001)  # 1001: going away
                    return
                snapshot = _build_snapshot(shared)
                alarms = shared.get("alarms", [])
                new_alarms = [a for a in alarms if a["ts"] > last_alarm_ts]
                if new_alarms:
                    last_alarm_ts = new_alarms[-1]["ts"]
                snapshot["new_alarms"] = new_alarms
                await ws.send_json(snapshot)
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            logger.info("Dashboard WebSocket client disconnected")
        except Exception as e:
            logger.warning(f"Dashboard WebSocket error: {e}")
        finally:
            shared["ws_clients"].discard(ws)

    return app


def _decode_register_value(reg, raw_block: list[int], offset: int):
    """Decode a single register value from a raw Modbus block."""
    if reg.dtype == "REAL" and offset + 1 < len(raw_block):
        hi, lo = raw_block[offset], raw_block[offset + 1]
        import struct
        try:
            val = struct.unpack(">f", struct.pack(">HH", hi, lo))[0]
            return round(val, 4), [hi, lo]
        except Exception:
            return None, [hi, lo]
    elif reg.dtype == "WORD" and reg.bit is not None:
        word = raw_block[offset]
        bit_val = (word >> reg.bit) & 1
        return bit_val, word
    elif reg.dtype == "INT":
        val = raw_block[offset]
        if val >= 0x8000:
            val -= 0x10000
        return val, raw_block[offset]
    elif reg.dtype == "WORD":
        return raw_block[offset], raw_block[offset]
    return raw_block[offset], raw_block[offset]


def _safe_float(v) -> float | None:
    """Convert inf/nan to None for JSON serialisation."""
    import math
    try:
        f = float(v)
        if math.isinf(f) or math.isnan(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _build_snapshot(shared: dict) -> dict:
    """Build a JSON-serializable telemetry snapshot from shared state."""
    gate = shared.get("gate")
    advisor = shared.get("advisor")
    monitor = shared.get("monitor")
    config = shared.get("config")
    modbus = shared.get("modbus")
    metrics = shared.get("latest_metrics")
    ring = shared.get("ring_buffer")
    lock = shared.get("buffer_lock")

    # Latest sample from ring buffer
    latest_sample = {}
    if ring and lock:
        import threading
        with lock:
            if ring:
                latest_sample = dict(ring[-1])

    # Remove non-serializable items
    latest_sample.pop("raw_words", None)

    snap: dict[str, Any] = {
        "ts": time.time(),
        "connection": {
            "healthy": modbus.is_healthy if modbus else False,
            "consecutive_failures": modbus.consecutive_failures if modbus else 0,
            "connected": modbus.client.connected if modbus and hasattr(modbus, "client") else False,
        },
        "phase": config.phase.value if config else "?",
        "runtime_mode": getattr(config, "runtime_mode", "real") if config else "real",
        "state_machine": gate.state.name if gate else "UNKNOWN",
        "bounds": {
            "current_lower": gate.current_lower if gate else 0,
            "current_upper": gate.current_upper if gate else 0,
            "lkg_lower": gate.lkg.lower if gate else 0,
            "lkg_upper": gate.lkg.upper if gate else 0,
            "lkg_iae": _safe_float(gate.lkg.iae_at_acceptance) if gate else 0,
        },
        "safety": {
            "esd_active": gate.state.name == "ESD" if gate else False,
            "bump_lockout": bool(latest_sample.get("bump_flag_fwd") or
                                 latest_sample.get("bump_flag_rev")),
            "heartbeat_counter": gate.heartbeat_counter if gate else 0,
            "consecutive_rejections": gate.consecutive_rejections if gate else 0,
            "cooldown_remaining": max(0, gate.cooldown_until - time.time()) if gate else 0,
        },
        "metrics": {},
        "advisor": {
            "trim_upper": round(advisor.state.trim_upper, 2) if advisor else 0,
            "trim_lower": round(advisor.state.trim_lower, 2) if advisor else 0,
            "dwell_counter": round(advisor.state.dwell_counter, 1) if advisor else 0,
            "total_adaptations": advisor.state.total_adaptations if advisor else 0,
        },
        "live": {
            "rpm": latest_sample.get("rpm_encoder", 0),
            "setpoint": latest_sample.get("ss_setpoint_fwd", config.nominal_setpoint if config else 60),
            "swash_output": latest_sample.get("swash_output", 0),
            "active_lower": latest_sample.get("active_lower", 0),
            "active_upper": latest_sample.get("active_upper", 0),
            "delivered_torque": latest_sample.get("delivered_torque", 0),
            "loop_temp": latest_sample.get("loop_temp", 0),
            "esd_bit": latest_sample.get("esd_bit", 0),
            "stale": latest_sample.get("stale", True),
        },
        "config": {
            "plc_host": config.plc_host if config else "",
            "drill_depth_ft": config.drill_depth_ft if config else 0,
            "deadband_rpm": config.deadband_rpm if config else 0,
            "nominal_setpoint": config.nominal_setpoint if config else 0,
        },
    }

    if metrics:
        snap["metrics"] = {
            "dniae": round(metrics.dniae, 4),
            "mean_error": round(metrics.mean_error, 2),
            "rmse_rpm": round(metrics.rmse_rpm, 2),
            "failure_mode": metrics.failure_mode,
            "failure_confidence": round(metrics.failure_confidence, 2),
            "sat_total": round(metrics.sat_total, 3),
            "sat_upper": round(metrics.sat_upper, 3),
            "sat_lower": round(metrics.sat_lower, 3),
            "is_windup": metrics.is_windup,
            "change_detected": metrics.change_detected,
            "change_reason": metrics.change_reason,
            "n_valid": metrics.n_valid,
            "stale_fraction": round(metrics.stale_fraction, 3),
            "anomaly_score": round(metrics.anomaly_score, 6),
            "anomaly_threshold": round(metrics.anomaly_threshold, 6),
            "anomaly_detected": metrics.anomaly_detected,
        }

    return snap


async def start_dashboard(app: FastAPI, host: str = "0.0.0.0", port: int = 8420,
                           shared: dict | None = None):
    """Start uvicorn inside the running asyncio loop.

    Enforces a concurrency cap and a body-size limit pulled from Config so
    a runaway client can't drown out the PLC read_loop. The uvicorn Server
    object is stashed in `shared['dashboard_server']` so main.py's
    shutdown path can flip `server.should_exit = True` and let the ASGI
    app drain in-flight requests + WebSocket close frames before we kill
    the event loop.
    """
    import uvicorn
    cfg = shared.get("config") if shared else None
    limit_concurrency = getattr(cfg, "dashboard_max_concurrent", 64) if cfg else 64
    max_body_bytes = getattr(cfg, "dashboard_max_body_bytes", 1_000_000) if cfg else 1_000_000
    config = uvicorn.Config(
        app, host=host, port=port,
        log_level="warning",
        access_log=False,
        limit_concurrency=limit_concurrency,
        limit_max_requests=None,
        h11_max_incomplete_event_size=max_body_bytes,
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)
    if shared is not None:
        shared["dashboard_server"] = server

    auth_msg = ("auth ENABLED"
                if (shared and _configured_token(shared) is not None)
                else "auth DISABLED (set dashboard_token or HXI_DASHBOARD_TOKEN)")
    logger.info(f"Dashboard starting at http://{host}:{port} ({auth_msg})")
    await server.serve()


async def shutdown_dashboard(shared: dict) -> None:
    """Gracefully close WebSocket clients and tell uvicorn to stop.

    Called from main.py's shutdown path before the csv_logger join so the
    audit trail captures any in-flight operator actions. Idempotent.
    """
    clients = list(shared.get("ws_clients", []))
    for ws in clients:
        try:
            await ws.close(code=1001)  # 1001 = going away
        except Exception:
            pass
    server = shared.get("dashboard_server")
    if server is not None:
        server.should_exit = True
