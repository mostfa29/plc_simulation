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
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("dashboard")

STATIC_DIR = Path(__file__).parent / "static"


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

    # ─── Static files ───────────────────────────────────────────────────
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ─── Root → dashboard HTML ──────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

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
    @app.post("/api/control/disable")
    async def control_disable():
        gate = shared.get("gate")
        if not gate:
            raise HTTPException(503, "Gate not initialized")
        gate.operator_disable()
        logger.info("DASHBOARD: operator DISABLED adaptive")
        return {"status": "disabled", "state": gate.state.name}

    @app.post("/api/control/enable")
    async def control_enable():
        gate = shared.get("gate")
        if not gate:
            raise HTTPException(503, "Gate not initialized")
        gate.operator_enable()
        logger.info("DASHBOARD: operator ENABLED adaptive")
        return {"status": "enabled", "state": gate.state.name}

    @app.post("/api/control/phase")
    async def control_phase(body: dict):
        config = shared.get("config")
        if not config:
            raise HTTPException(503, "Config not loaded")
        new_phase = body.get("phase", "").upper()
        from hxi_optimizer.hxi_config import Phase
        try:
            config.phase = Phase(new_phase)
        except ValueError:
            raise HTTPException(400, f"Invalid phase: {new_phase}")
        logger.info(f"DASHBOARD: phase changed to {new_phase}")
        return {"phase": config.phase.value}

    @app.post("/api/control/depth")
    async def control_depth(body: dict):
        config = shared.get("config")
        if not config:
            raise HTTPException(503, "Config not loaded")
        depth = body.get("depth_ft")
        if depth is None or not isinstance(depth, (int, float)) or depth <= 0:
            raise HTTPException(400, "depth_ft must be a positive number")
        config.drill_depth_ft = float(depth)
        logger.info(f"DASHBOARD: drill depth updated to {depth} ft")
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

    # ─── Simulation sandbox ────────────────────────────────────────────
    @app.post("/api/simulate")
    async def api_simulate(body: dict):
        """Run a simulation scenario and return the time series + metrics.

        body: {
            "scenario": "normal|bias|oscillation|stickslip|...",
            "duration_s": 300,
            "setpoint": 60.0,
            "params": { ...scenario-specific overrides... }
        }
        """
        from training.scenarios import ALL_GENERATORS
        from training.generate_dataset import samples_to_arrays, FEATURE_COLS
        scenario = body.get("scenario", "normal")
        duration = min(body.get("duration_s", 300), 600)
        setpoint = body.get("setpoint", 60.0)
        params = body.get("params", {})

        gen = ALL_GENERATORS.get(scenario)
        if not gen:
            raise HTTPException(400, f"Unknown scenario: {scenario}. "
                                f"Available: {list(ALL_GENERATORS.keys())}")

        import inspect
        sig = inspect.signature(gen)
        kwargs = {"duration_s": duration, "setpoint": setpoint, "seed": 42}
        for k, v in params.items():
            if k in sig.parameters:
                kwargs[k] = v

        samples, labels = gen(**kwargs)

        ts = [s["ts"] for s in samples]
        rpm = [s["rpm_encoder"] for s in samples]
        swash = [s["swash_output"] for s in samples]
        setpoints = [s["ss_setpoint_fwd"] for s in samples]
        torque = [s["delivered_torque"] for s in samples]
        temp = [s["loop_temp"] for s in samples]
        lower = [s["active_lower"] for s in samples]
        upper = [s["active_upper"] for s in samples]

        # Compute performance metrics on the simulation
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

        label_counts = {}
        for l in labels:
            label_counts[l] = label_counts.get(l, 0) + 1

        return {
            "scenario": scenario,
            "duration_s": duration,
            "sample_count": len(samples),
            "label_distribution": label_counts,
            "timeseries": {
                "ts": ts, "rpm": rpm, "setpoint": setpoints,
                "swash": swash, "torque": torque, "temp": temp,
                "lower": lower, "upper": upper, "labels": labels,
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
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        logger.info("Dashboard WebSocket client connected")
        try:
            last_alarm_ts = 0.0
            while True:
                snapshot = _build_snapshot(shared)
                # History is pushed by the background task, not here
                # Include any new alarms
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
        }

    return snap


async def start_dashboard(app: FastAPI, host: str = "0.0.0.0", port: int = 8420):
    """Start uvicorn inside the running asyncio loop (non-blocking)."""
    import uvicorn
    config = uvicorn.Config(
        app, host=host, port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    logger.info(f"Dashboard starting at http://{host}:{port}")
    await server.serve()
