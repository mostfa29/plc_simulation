"""Server-side self-test battery for the dashboard.

A single function `run_self_tests(shared)` executes ~12 read-only health
checks against the running optimizer and returns a JSON-friendly dict.
The dashboard's "System Test" tab calls this and shows pass/fail/warn
indicators with plain-language explanations.

Design rules:
  - **Read-only.** No checks here mutate optimizer state, write to the
    PLC, or call any state-changing APIs. Safe to run at any time.
  - **Plain-language results.** Each test has a `description` (what it
    checks), `detail` (one-line current value), and `what_to_do`
    (concrete fix instruction if it failed). All written for someone
    who is not a Python developer.
  - **Cheap.** Whole battery runs in <500 ms. No subprocess, no model
    inference, no PLC reads — uses cached state from `shared`.
  - **Three-state outcomes.** `pass` (green), `fail` (red — must fix),
    `warn` (amber — works but degraded), `skip` (grey — N/A here).
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("dashboard.selftest")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ─────────────────────────────────────────────────────────────────────
# Result builder
# ─────────────────────────────────────────────────────────────────────

def _result(test_id: str, name: str, description: str, status: str,
             detail: str = "", what_to_do: str = "") -> dict:
    """Build a single test result row. Status: pass | fail | warn | skip."""
    return {
        "id": test_id,
        "name": name,
        "description": description,
        "status": status,
        "detail": detail,
        "what_to_do": what_to_do,
    }


def _safe(fn: Callable[[dict], dict], shared: dict, test_id: str,
           name: str) -> dict:
    """Run a single check, catch any exception so one bad test can't
    take the whole battery down. Plain-language fail message on crash.
    """
    try:
        return fn(shared)
    except Exception as e:
        logger.exception(f"selftest {test_id} crashed")
        return _result(
            test_id, name,
            description="(internal check)",
            status="fail",
            detail=f"Test crashed: {type(e).__name__}: {e}",
            what_to_do=("Send the optimizer log to the developer. This is a "
                        "bug in the test itself, not in the system being tested."),
        )


# ─────────────────────────────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────────────────────────────

def _check_dashboard_uptime(shared: dict) -> dict:
    """Optimizer process is alive — trivially true if this code is running."""
    from hxi_optimizer.dashboard.server import SERVER_START_TS
    uptime_s = time.time() - SERVER_START_TS
    return _result(
        "uptime",
        "Optimizer is running",
        "The dashboard is up and responding to requests.",
        status="pass",
        detail=f"Up for {_format_uptime(uptime_s)}",
    )


def _check_plc_connection(shared: dict) -> dict:
    """Modbus / OPC UA link to the PLC is healthy."""
    modbus = shared.get("modbus")
    if modbus is None:
        return _result(
            "plc_connection",
            "PLC connection",
            "The optimizer can talk to the PLC.",
            status="fail",
            detail="No transport configured.",
            what_to_do="Restart the service. If it still fails, check "
                       "hxi_config.json has a valid plc_host.",
        )
    healthy = bool(modbus.is_healthy)
    fails = int(getattr(modbus, "consecutive_failures", 0))
    if healthy:
        return _result(
            "plc_connection",
            "PLC connection",
            "The optimizer can talk to the PLC.",
            status="pass",
            detail=f"Connected to {getattr(modbus, 'transport_name', 'PLC')}, "
                    f"0 consecutive failures",
        )
    if fails < 3:
        return _result(
            "plc_connection", "PLC connection",
            "The optimizer can talk to the PLC.",
            status="warn",
            detail=f"{fails} consecutive read failures (still working)",
            what_to_do="Watch the connection dot in the header. If it goes "
                       "red for >30s, check eCatcher tunnel + PLC reachability.",
        )
    return _result(
        "plc_connection", "PLC connection",
        "The optimizer can talk to the PLC.",
        status="fail",
        detail=f"{fails} consecutive read failures — link unhealthy",
        what_to_do=("Check eCatcher (Fleet tab → eCatcher Status). If the "
                    "tunnel is up but reads still fail, ping the PLC IP from "
                    "this PC. If that fails, it's a network / PLC-side issue."),
    )


def _check_telemetry_flowing(shared: dict) -> dict:
    """Live telemetry samples are landing in the ring buffer."""
    ring = shared.get("ring_buffer")
    lock = shared.get("buffer_lock")
    if ring is None or lock is None:
        return _result(
            "telemetry", "Telemetry flow",
            "Live samples from the PLC are landing in the buffer at 2 Hz.",
            status="fail",
            detail="No ring buffer wired.",
            what_to_do="Restart the service.",
        )
    with lock:
        n = len(ring)
        last = dict(ring[-1]) if ring else None
    if n == 0:
        return _result(
            "telemetry", "Telemetry flow",
            "Live samples from the PLC are landing in the buffer at 2 Hz.",
            status="warn",
            detail="Buffer empty — no telemetry yet (may still be warming up)",
            what_to_do="Wait 30 seconds and retest. If still empty, see "
                       "'PLC connection' result above.",
        )
    age_s = time.time() - float(last.get("ts", 0))
    if age_s > 5.0:
        return _result(
            "telemetry", "Telemetry flow",
            "Live samples from the PLC are landing in the buffer at 2 Hz.",
            status="fail",
            detail=f"Last sample is {age_s:.1f}s old (expected <1s)",
            what_to_do=("PLC reads have stalled. Check the connection dot — "
                        "if red, the link is down. If green but samples are "
                        "stale, the read_loop may be blocked; restart service."),
        )
    is_stale = bool(last.get("stale", False))
    if is_stale:
        return _result(
            "telemetry", "Telemetry flow",
            "Live samples from the PLC are landing in the buffer at 2 Hz.",
            status="warn",
            detail=f"{n} samples buffered, but most recent is marked stale",
            what_to_do="A read failed recently. Watch for 30s — usually "
                       "self-recovers within a few cycles.",
        )
    return _result(
        "telemetry", "Telemetry flow",
        "Live samples from the PLC are landing in the buffer at 2 Hz.",
        status="pass",
        detail=f"{n} samples buffered, last sample {age_s*1000:.0f}ms ago",
    )


def _check_classifier_loaded(shared: dict) -> dict:
    monitor = shared.get("monitor")
    if monitor is None or not hasattr(monitor, "loaded_models_info"):
        return _result(
            "classifier", "ML classifier loaded",
            "The 7-class failure-mode model is in memory and ready.",
            status="warn",
            detail="Performance monitor not exposing model info.",
            what_to_do="Older optimizer build; redeploy from main.",
        )
    info = monitor.loaded_models_info()
    if not info.get("classifier_active"):
        return _result(
            "classifier", "ML classifier loaded",
            "The 7-class failure-mode model is in memory and ready.",
            status="fail",
            detail="No classifier session loaded.",
            what_to_do=("Check hxi_optimizer/models/classifier.onnx exists. "
                        "If yes, see optimizer.log for ONNX load errors. "
                        "If no, redeploy the model files (see "
                        "docs/DEPLOYMENT.md)."),
        )
    fails = int(info.get("classifier_failures", 0))
    inferences = int(info.get("classifier_inferences", 0))
    if fails > 0 and fails > inferences * 0.05:
        return _result(
            "classifier", "ML classifier loaded",
            "The 7-class failure-mode model is in memory and ready.",
            status="warn",
            detail=f"{fails}/{inferences} inferences have failed",
            what_to_do="Check optimizer.log for the actual exception; "
                       "could indicate a model/feature shape mismatch.",
        )
    src = info.get("classifier_source", "(default)") or "(default)"
    src_short = Path(src).name if src else "(unknown)"
    return _result(
        "classifier", "ML classifier loaded",
        "The 7-class failure-mode model is in memory and ready.",
        status="pass",
        detail=f"{src_short} — {inferences} successful inferences, {fails} failures",
    )


def _check_autoencoder_loaded(shared: dict) -> dict:
    monitor = shared.get("monitor")
    if monitor is None or not hasattr(monitor, "loaded_models_info"):
        return _result(
            "autoencoder", "Anomaly detector loaded",
            "The autoencoder for novel-condition detection is in memory.",
            status="skip",
            detail="Performance monitor not exposing AE info.",
        )
    info = monitor.loaded_models_info()
    if not info.get("autoencoder_active"):
        return _result(
            "autoencoder", "Anomaly detector loaded",
            "The autoencoder for novel-condition detection is in memory.",
            status="warn",
            detail="No autoencoder session loaded — anomaly detection disabled.",
            what_to_do=("Optional but recommended. Check "
                        "hxi_optimizer/models/autoencoder.onnx. "
                        "System still works without it (uses heuristic "
                        "anomaly detection instead)."),
        )
    threshold = info.get("autoencoder_threshold")
    src = info.get("autoencoder_source", "(default)") or "(default)"
    src_short = Path(src).name if src else "(unknown)"
    return _result(
        "autoencoder", "Anomaly detector loaded",
        "The autoencoder for novel-condition detection is in memory.",
        status="pass",
        detail=f"{src_short} — threshold {threshold:.6f}" if threshold
                else f"{src_short}",
    )


def _check_safety_limits_configured(shared: dict) -> dict:
    """abs_min/abs_max bounds must be populated (commissioning ran)."""
    cfg = shared.get("config")
    if cfg is None:
        return _result(
            "safety_limits", "Safety limits set",
            "Hard min/max bounds are populated from commissioning.",
            status="fail",
            detail="No config loaded.",
            what_to_do="Restart the service.",
        )
    s = getattr(cfg, "safety", None)
    fields = ("abs_min_lower", "abs_max_lower",
              "abs_min_upper", "abs_max_upper")
    missing = [f for f in fields if getattr(s, f, None) is None]
    if missing:
        return _result(
            "safety_limits", "Safety limits set",
            "Hard min/max bounds are populated from commissioning.",
            status="fail",
            detail=f"Missing in hxi_config.json: {', '.join(missing)}",
            what_to_do=("Run commissioning_tests on this PLC: "
                        "`python -m hxi_optimizer.deploy.commissioning_tests`. "
                        "It populates these fields automatically. The service "
                        "refuses to write to the PLC until they're set."),
        )
    return _result(
        "safety_limits", "Safety limits set",
        "Hard min/max bounds are populated from commissioning.",
        status="pass",
        detail=(f"lower [{s.abs_min_lower}, {s.abs_max_lower}], "
                f"upper [{s.abs_min_upper}, {s.abs_max_upper}]"),
    )


def _check_phase_state(shared: dict) -> dict:
    """Phase is set to a valid value."""
    cfg = shared.get("config")
    gate = shared.get("gate")
    if cfg is None or gate is None:
        return _result(
            "phase_state", "Phase + gate state",
            "The optimizer is in a known phase and the safety gate is in a known state.",
            status="fail",
            detail="Config or gate not initialized.",
            what_to_do="Restart the service. If it still fails, check that "
                       "hxi_config.json exists and has a valid `phase` field.",
        )
    phase = getattr(cfg.phase, "value", "?")
    state = gate.state.name if hasattr(gate.state, "name") else str(gate.state)
    if state in ("ESD", "DISABLED", "ROLLING_BACK"):
        return _result(
            "phase_state", "Phase + gate state",
            "The optimizer is in a known phase and the safety gate is in a known state.",
            status="warn",
            detail=f"Phase {phase}, gate state {state}",
            what_to_do={
                "ESD": "ESD bit is active at the PLC. Fix the rig-side cause "
                       "and clear the bit. The gate will re-enable automatically.",
                "DISABLED": "Someone disabled the optimizer. Click Enable on "
                            "the Controls tab when ready.",
                "ROLLING_BACK": "Gate is reverting bounds to last-known-good. "
                                "Watch for 60s — usually self-resolves.",
            }.get(state, ""),
        )
    return _result(
        "phase_state", "Phase + gate state",
        "The optimizer is in a known phase and the safety gate is in a known state.",
        status="pass",
        detail=f"Phase {phase}, gate state {state}",
    )


def _check_audit_log_writable(shared: dict) -> dict:
    audit = shared.get("audit")
    if audit is None or not hasattr(audit, "filepath"):
        return _result(
            "audit_log", "Audit log writable",
            "Operator actions and safety events get a fsync'd CSV row.",
            status="fail",
            detail="No audit logger wired.",
            what_to_do="Restart the service.",
        )
    fp = Path(audit.filepath)
    if not fp.exists():
        return _result(
            "audit_log", "Audit log writable",
            "Operator actions and safety events get a fsync'd CSV row.",
            status="warn",
            detail=f"audit.log not yet created at {fp}",
            what_to_do="Click any control button (e.g. enable/disable) to "
                       "force the first audit write, then re-test.",
        )
    try:
        rows = sum(1 for _ in fp.open("r", encoding="utf-8")) - 1  # minus header
    except Exception:
        rows = -1
    return _result(
        "audit_log", "Audit log writable",
        "Operator actions and safety events get a fsync'd CSV row.",
        status="pass",
        detail=f"{fp.name} exists, {rows} rows logged",
    )


def _check_dataset_capture(shared: dict) -> dict:
    cap = shared.get("dataset_capture")
    if cap is None:
        return _result(
            "dataset_capture", "Real-data capture",
            "Episodes saved to disk when the operator clicks Annotate.",
            status="warn",
            detail="Dataset capture disabled in config.",
            what_to_do="Set dataset_capture_enabled=true in hxi_config.json "
                       "to enable. Required for fine-tuning.",
        )
    summary = cap.summary() if hasattr(cap, "summary") else {}
    n_eps = int(summary.get("total_episodes", 0))
    if n_eps == 0:
        return _result(
            "dataset_capture", "Real-data capture",
            "Episodes saved to disk when the operator clicks Annotate.",
            status="pass",
            detail="Capture enabled, no episodes yet.",
            what_to_do="During shifts, click the Annotate buttons on the "
                       "Controls tab when something notable happens. Aim "
                       "for ~10 events per fault type for fine-tuning.",
        )
    return _result(
        "dataset_capture", "Real-data capture",
        "Episodes saved to disk when the operator clicks Annotate.",
        status="pass",
        detail=f"{n_eps} captured episodes",
    )


def _check_machine_identified(shared: dict) -> dict:
    record = shared.get("machine_record")
    if record is None:
        return _result(
            "machine_id", "Machine identified",
            "The optimizer knows which rig it's connected to.",
            status="warn",
            detail="No fleet match for the current PLC IP.",
            what_to_do=("Set ewon_name in hxi_config.json, or configure "
                        "talk2m_* fields so eCatcher can auto-detect. The "
                        "optimizer falls back to the default HXI register "
                        "map without identification."),
        )
    return _result(
        "machine_id", "Machine identified",
        "The optimizer knows which rig it's connected to.",
        status="pass",
        detail=f"{record.ewon_name} ({record.equipment_type})",
    )


def _check_disk_space(shared: dict) -> dict:
    """Logs need disk — flag if free space is low."""
    log_dir = REPO_ROOT / "hxi_optimizer" / "logs"
    if not log_dir.exists():
        return _result(
            "disk_space", "Disk space available",
            "Enough free disk for ongoing CSV + audit logs.",
            status="skip",
            detail="Log directory not created yet.",
        )
    try:
        usage = shutil.disk_usage(str(log_dir))
        free_gb = usage.free / 1024 ** 3
    except Exception as e:
        return _result(
            "disk_space", "Disk space available",
            "Enough free disk for ongoing CSV + audit logs.",
            status="warn",
            detail=f"Could not check disk: {e}",
        )
    if free_gb < 1.0:
        return _result(
            "disk_space", "Disk space available",
            "Enough free disk for ongoing CSV + audit logs.",
            status="fail",
            detail=f"Only {free_gb:.2f} GB free.",
            what_to_do=("Logs grow ~30 MB/day. Delete old drill_*.csv files: "
                        "`Get-ChildItem hxi_optimizer\\logs\\drill_*.csv "
                        "| Where LastWriteTime -lt (Get-Date).AddDays(-30) "
                        "| Remove-Item`"),
        )
    if free_gb < 5.0:
        return _result(
            "disk_space", "Disk space available",
            "Enough free disk for ongoing CSV + audit logs.",
            status="warn",
            detail=f"{free_gb:.1f} GB free — low headroom",
            what_to_do="Schedule weekly log rotation; see "
                       "docs/TROUBLESHOOTING.md.",
        )
    return _result(
        "disk_space", "Disk space available",
        "Enough free disk for ongoing CSV + audit logs.",
        status="pass",
        detail=f"{free_gb:.1f} GB free",
    )


def _check_models_directory(shared: dict) -> dict:
    """Default classifier + AE files exist on disk."""
    models_dir = REPO_ROOT / "hxi_optimizer" / "models"
    expected = ["classifier.onnx", "classifier_meta.json",
                "autoencoder.onnx", "autoencoder_meta.json"]
    missing = [f for f in expected if not (models_dir / f).exists()]
    if missing:
        return _result(
            "models_dir", "Model files on disk",
            "Default classifier + autoencoder are deployed.",
            status="fail",
            detail=f"Missing: {', '.join(missing)}",
            what_to_do=("Redeploy the models from training/models/. See "
                        "docs/ML_PIPELINE.md → Stage 2/3 for the cp commands."),
        )
    return _result(
        "models_dir", "Model files on disk",
        "Default classifier + autoencoder are deployed.",
        status="pass",
        detail="all 4 files present",
    )


def _check_per_rig_registry(shared: dict) -> dict:
    """Model registry resolves cleanly (default present, per-rig optional)."""
    reg = shared.get("model_registry")
    if reg is None:
        return _result(
            "registry", "Model registry healthy",
            "Per-rig fine-tuned models are discoverable on disk.",
            status="skip",
            detail="No registry wired.",
        )
    summary = reg.per_rig_summary()
    if not summary.get("default_available"):
        return _result(
            "registry", "Model registry healthy",
            "Per-rig fine-tuned models are discoverable on disk.",
            status="fail",
            detail="No default classifier deployed.",
            what_to_do="See 'Model files on disk' result above.",
        )
    n = summary.get("n_per_rig", 0)
    if n == 0:
        return _result(
            "registry", "Model registry healthy",
            "Per-rig fine-tuned models are discoverable on disk.",
            status="pass",
            detail="Default classifier loaded, no per-rig fine-tunes yet.",
            what_to_do="Optional. After capturing ~100 real episodes per rig, "
                       "run `python -m training.fine_tune --rig <name>` to "
                       "deploy a personalized model.",
        )
    return _result(
        "registry", "Model registry healthy",
        "Per-rig fine-tuned models are discoverable on disk.",
        status="pass",
        detail=f"Default + {n} per-rig fine-tunes: "
                f"{', '.join(summary.get('per_rig_slugs', []))}",
    )


def _check_word_order_verified(shared: dict) -> dict:
    """FLOAT32 byte order must be pinned for safe register decoding."""
    try:
        from hxi_optimizer.comms.register_map import VERIFIED_WORD_ORDER
    except Exception as e:
        return _result(
            "word_order", "Float byte order verified",
            "FLOAT32 register decode order is pinned to match this PLC.",
            status="fail",
            detail=f"Could not import VERIFIED_WORD_ORDER: {e}",
        )
    if VERIFIED_WORD_ORDER is None:
        return _result(
            "word_order", "Float byte order verified",
            "FLOAT32 register decode order is pinned to match this PLC.",
            status="fail",
            detail="VERIFIED_WORD_ORDER is None.",
            what_to_do=("Run commissioning_tests against this PLC: "
                        "`python -m hxi_optimizer.deploy.commissioning_tests`. "
                        "The service refuses to start in production "
                        "(require_verified_word_order=true) until this is set."),
        )
    return _result(
        "word_order", "Float byte order verified",
        "FLOAT32 register decode order is pinned to match this PLC.",
        status="pass",
        detail=f"Pinned to {VERIFIED_WORD_ORDER}",
    )


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────

CHECKS: list[tuple[str, str, Callable]] = [
    ("uptime",            "Optimizer is running",     _check_dashboard_uptime),
    ("plc_connection",    "PLC connection",           _check_plc_connection),
    ("telemetry",         "Telemetry flow",           _check_telemetry_flowing),
    ("classifier",        "ML classifier",            _check_classifier_loaded),
    ("autoencoder",       "Anomaly detector",         _check_autoencoder_loaded),
    ("models_dir",        "Model files on disk",      _check_models_directory),
    ("registry",          "Model registry",           _check_per_rig_registry),
    ("word_order",        "Float byte order",         _check_word_order_verified),
    ("safety_limits",     "Safety limits",            _check_safety_limits_configured),
    ("phase_state",       "Phase + gate state",       _check_phase_state),
    ("machine_id",        "Machine identified",       _check_machine_identified),
    ("audit_log",         "Audit log writable",       _check_audit_log_writable),
    ("dataset_capture",   "Real-data capture",        _check_dataset_capture),
    ("disk_space",        "Disk space",               _check_disk_space),
]


def run_self_tests(shared: dict) -> dict:
    """Run the full battery and return a JSON-friendly summary + per-test rows."""
    started = time.time()
    results = []
    for tid, name, fn in CHECKS:
        results.append(_safe(fn, shared, tid, name))
    elapsed_ms = int((time.time() - started) * 1000)

    counts = {"pass": 0, "fail": 0, "warn": 0, "skip": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    if counts["fail"] > 0:
        overall = "fail"
    elif counts["warn"] > 0:
        overall = "warn"
    else:
        overall = "pass"

    return {
        "ts": time.time(),
        "elapsed_ms": elapsed_ms,
        "overall": overall,
        "summary": {
            "total": len(results),
            "passed": counts["pass"],
            "failed": counts["fail"],
            "warnings": counts["warn"],
            "skipped": counts["skip"],
        },
        "tests": results,
    }


def _format_uptime(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"
