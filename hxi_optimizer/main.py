"""HXI Smart Slide Adaptive PID Optimizer — entry point.

Per MASTER_CONTEXT §7.5. Four asyncio tasks under a single asyncio.gather:
  read_loop          — 2 Hz, drift-compensating absolute deadlines, FC03
  heartbeat_loop     — 5 s, writes %R06605 via SafetyGate._send_heartbeat
  analysis_loop      — 0.1 Hz, advisor + SafetyGate (only writes if Phase >= C)
  connection_monitor — 5 s, watches comms health, triggers rollback on loss

Phase A: observer only — no writes (commissioning + data collection).
Phase B: advisory — bounds computed and logged but never written.
Phase C: limited authority — writes pass through SafetyGate.
Phase D: full authority — same path, gain schedule populated.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import queue
import signal
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ─── Startup optimisations (must happen before heavy imports) ──────────────
gc.collect(2)
gc.freeze()
gc.set_threshold(50000, 20, 20)

from hxi_optimizer.hxi_config import Config, Phase, load_config
from hxi_optimizer.comms.transport import build_transport
from hxi_optimizer.comms.register_map import (
    build_sample, esd_bit_from_word, VERIFIED_WORD_ORDER,
)
from hxi_optimizer.control.safety_gate import SafetyGate, SafetyConfig
from hxi_optimizer.control.pid_advisor import PIDAdvisor, BoundsAdvisorConfig
from hxi_optimizer.monitoring.performance_metrics import (
    PerformanceMonitor, PerformanceMetrics,
)
from hxi_optimizer.io_logging.csv_logger import CrashSafeCSVLogger, build_csv_row
from hxi_optimizer.io_logging.audit_logger import AuditLogger
from hxi_optimizer.state.persistence import save_state, load_state
from hxi_optimizer.dashboard.server import create_app, start_dashboard

# ─── Paths / logging ────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
LOG_DIR = HERE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(name)-20s %(levelname)-8s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "optimizer.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logging.getLogger("pymodbus").setLevel(logging.WARNING)
logger = logging.getLogger("main")

# ─── Shared state ───────────────────────────────────────────────────────────
buffer_lock = threading.Lock()
ring_buffer: deque = deque(maxlen=40)   # 20 s at 2 Hz
result_lock = threading.Lock()
latest_metrics: PerformanceMetrics | None = None
latest_bounds: tuple[int, int] | None = None
csv_queue: "queue.Queue" = queue.Queue(maxsize=500)
shutdown_event = asyncio.Event()
# Dashboard reads from this dict; analysis_loop updates "latest_metrics" in it
dashboard_shared: dict = {}


# ─── Read loop ──────────────────────────────────────────────────────────────

async def read_loop(mgr, config: Config) -> None:
    logger.info("read_loop: starting (2 Hz, drift-compensating)")
    next_time = asyncio.get_event_loop().time()
    seq = 0
    while not shutdown_event.is_set():
        next_time += config.read_interval
        registers = await mgr.safe_read(
            address=config.read_start_addr, count=config.read_count
        )
        if registers is None:
            sample = {"seq": seq, "stale": True, "ts": time.time(),
                      "raw_words": []}
        else:
            sample = build_sample(registers)
            sample["seq"] = seq
            sample["stale"] = False
            sample["ts"] = time.time()
            # Secondary read: ESD word + bump_flag_rev (small, infrequent)
            if seq % 4 == 0:  # 0.5 Hz
                esd_block = await mgr.safe_read(address=6627, count=2)
                if esd_block is not None:
                    sample["bump_flag_rev"] = esd_block[0]
                esd_word = await mgr.safe_read(address=6664, count=1)
                if esd_word is not None:
                    sample["esd_bit"] = esd_bit_from_word(esd_word[0])

        with buffer_lock:
            ring_buffer.append(sample)
        try:
            csv_queue.put_nowait(build_csv_row(sample))
        except queue.Full:
            pass  # Drop sample rather than block read loop

        seq += 1
        delay = max(0.0, next_time - asyncio.get_event_loop().time())
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=delay)
            return
        except asyncio.TimeoutError:
            continue


async def heartbeat_loop(gate: SafetyGate, config: Config) -> None:
    logger.info("heartbeat_loop: starting (5 s)")
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(),
                                   timeout=config.write_interval / 2)
            return
        except asyncio.TimeoutError:
            try:
                await gate._send_heartbeat()
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")


async def analysis_loop(executor: ThreadPoolExecutor,
                        monitor: PerformanceMonitor,
                        advisor: PIDAdvisor,
                        gate: SafetyGate,
                        config: Config) -> None:
    global latest_metrics, latest_bounds
    logger.info(f"analysis_loop: starting (Phase={config.phase.value})")
    loop = asyncio.get_event_loop()

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(),
                                   timeout=config.analysis_interval)
            return
        except asyncio.TimeoutError:
            pass

        with buffer_lock:
            snapshot = list(ring_buffer)
        if len(snapshot) < 4:
            continue

        try:
            metrics = await loop.run_in_executor(
                executor, _run_analysis, snapshot, monitor, config
            )
        except Exception as e:
            logger.error(f"Analysis error: {e}", exc_info=True)
            continue

        with result_lock:
            latest_metrics = metrics
        dashboard_shared["latest_metrics"] = metrics

        latest_sample = snapshot[-1]
        if latest_sample.get("stale"):
            continue

        esd = int(latest_sample.get("esd_bit", 0) or 0)
        bump_fwd = int(latest_sample.get("bump_flag_fwd", 0) or 0)
        bump_rev = int(latest_sample.get("bump_flag_rev", 0) or 0)
        setpoint = float(latest_sample.get("ss_setpoint_fwd",
                                           config.nominal_setpoint) or config.nominal_setpoint)
        psi = float(latest_sample.get("hydraulic_psi", 3000.0) or 3000.0)

        proposed = advisor.advise(
            rpm_setpoint=setpoint, hydraulic_psi=psi, performance=metrics,
        )
        if proposed is None:
            continue
        lower, upper = proposed

        pv_ok = metrics.dniae < 0.10
        oscillating = (metrics.failure_mode == "OSCILLATION"
                       and metrics.failure_confidence > 0.7)
        gate.check_trial(metrics.dniae, pv_ok, oscillating)

        if config.phase in (Phase.C, Phase.D):
            await gate.validate_and_write(
                lower, upper, metrics.dniae,
                esd_bit=esd, bump_fwd=bump_fwd, bump_rev=bump_rev,
            )
            with result_lock:
                latest_bounds = (lower, upper)
        else:
            logger.info(
                f"[ADVISORY phase={config.phase.value}] "
                f"bounds=[{lower},{upper}] "
                f"DNIAE={metrics.dniae:.3f} mode={metrics.failure_mode} "
                f"sat={metrics.sat_total:.2f}"
            )

        _persist_state(gate, advisor, metrics)


async def connection_monitor(mgr, gate: SafetyGate,
                             config: Config) -> None:
    logger.info("connection_monitor: starting (5 s)")
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)
            return
        except asyncio.TimeoutError:
            pass
        if not mgr.is_healthy:
            logger.warning(
                f"Connection unhealthy: "
                f"{mgr.consecutive_failures} consecutive failures"
            )
            if mgr.consecutive_failures >= 6:
                gate.trigger_rollback("COMMS_LOSS_30S")


# ─── Helpers ────────────────────────────────────────────────────────────────

def _run_analysis(snapshot: list, monitor: PerformanceMonitor,
                  config: Config) -> PerformanceMetrics:
    valid = [s for s in snapshot if not s.get("stale", True)]
    for s in valid:
        monitor.update(
            raw_rpm=float(s.get("rpm_encoder", 0.0) or 0.0),
            setpoint=float(s.get("ss_setpoint_fwd", config.nominal_setpoint)
                           or config.nominal_setpoint),
            swash_output=int(s.get("swash_output", 0) or 0),
            lower=int(s.get("active_lower", 0) or 0),
            upper=int(s.get("active_upper", 1000) or 1000),
            stale=False,
        )
    last = valid[-1] if valid else {}
    return monitor.compute_metrics(
        setpoint=float(last.get("ss_setpoint_fwd", config.nominal_setpoint)
                       or config.nominal_setpoint),
        lower=int(last.get("active_lower", 0) or 0),
        upper=int(last.get("active_upper", 1000) or 1000),
    )


def _persist_state(gate: SafetyGate, advisor: PIDAdvisor,
                   metrics: PerformanceMetrics | None) -> None:
    state = {
        "gate_lower": gate.current_lower,
        "gate_upper": gate.current_upper,
        "gate_state": gate.state.name,
        "lkg_lower": gate.lkg.lower,
        "lkg_upper": gate.lkg.upper,
        "lkg_iae": gate.lkg.iae_at_acceptance,
        "consecutive_rejections": gate.consecutive_rejections,
        "advisor_trim_upper": advisor.state.trim_upper,
        "advisor_trim_lower": advisor.state.trim_lower,
        "advisor_total_adaptations": advisor.state.total_adaptations,
        "metrics_dniae": metrics.dniae if metrics else None,
        "metrics_mode": metrics.failure_mode if metrics else None,
    }
    save_state(state)


# ─── Shutdown ───────────────────────────────────────────────────────────────

def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    def _handler(sig):  # noqa: ANN001
        logger.info(f"Received signal {sig} — initiating shutdown")
        loop.call_soon_threadsafe(shutdown_event.set)
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handler, sig)
    else:
        # Windows: signal.signal works for SIGINT only inside services run
        # under NSSM (NSSM sends SIGBREAK then forcibly terminates).
        signal.signal(signal.SIGINT, lambda s, f: _handler(s))
        try:
            signal.signal(signal.SIGBREAK, lambda s, f: _handler(s))  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


# ─── Entry point ────────────────────────────────────────────────────────────

async def main() -> None:
    logger.info("=== HXI Smart Slide Adaptive PID Optimizer starting ===")
    config = load_config(str(HERE / "hxi_config.json"))

    # Refuse to start if FLOAT32 word order is unverified
    if config.require_verified_word_order and VERIFIED_WORD_ORDER is None:
        logger.critical(
            "FLOAT32 word order is UNVERIFIED. Run "
            "deploy/commissioning_tests.py against the live PLC and pin "
            "VERIFIED_WORD_ORDER in comms/register_map.py before starting."
        )
        sys.exit(2)

    # Process priority (best-effort)
    try:
        import psutil
        psutil.Process().nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
        logger.info("Process priority: ABOVE_NORMAL")
    except Exception as e:
        logger.warning(f"Could not raise process priority: {e}")

    # Build components — transport is pluggable (Modbus or OPC UA) via config
    modbus_mgr = build_transport(config)
    logger.info(f"Transport: {modbus_mgr.transport_name} "
                f"({config.plc_host}:{getattr(config, 'opcua_port' if config.transport=='opcua' else 'plc_port', '?')})")
    audit = AuditLogger(LOG_DIR / "audit.log")

    safety_cfg = SafetyConfig(
        abs_min_lower=config.safety.abs_min_lower,
        abs_max_lower=config.safety.abs_max_lower,
        abs_min_upper=config.safety.abs_min_upper,
        abs_max_upper=config.safety.abs_max_upper,
        min_band_counts=config.safety.min_band_counts,
    )
    gate = SafetyGate(safety_cfg, modbus_mgr, audit)

    saved = load_state()
    if saved.get("lkg_lower") and saved.get("lkg_upper"):
        gate.lkg.lower = saved["lkg_lower"]
        gate.lkg.upper = saved["lkg_upper"]
        gate.lkg.iae_at_acceptance = saved.get("lkg_iae", float("inf"))
        gate.current_lower = saved.get("gate_lower", gate.lkg.lower)
        gate.current_upper = saved.get("gate_upper", gate.lkg.upper)
        logger.info(f"Restored LKG: [{gate.lkg.lower}, {gate.lkg.upper}]")

    advisor_cfg = BoundsAdvisorConfig(
        abs_min_lower=config.safety.abs_min_lower,
        abs_max_lower=config.safety.abs_max_lower,
        abs_min_upper=config.safety.abs_min_upper,
        abs_max_upper=config.safety.abs_max_upper,
        min_gap_counts=config.safety.min_band_counts,
    )
    advisor = PIDAdvisor(advisor_cfg)
    if saved.get("advisor_trim_upper") is not None:
        advisor.state.trim_upper = saved["advisor_trim_upper"]
        advisor.state.trim_lower = saved.get("advisor_trim_lower", 0.0)

    monitor = PerformanceMonitor(window_sec=20.0,
                                  deadband_rpm=config.deadband_rpm)

    csv_path = LOG_DIR / f"drill_{int(time.time())}.csv"
    csv_logger = CrashSafeCSVLogger(csv_path, csv_queue)
    csv_logger.start()

    loop = asyncio.get_event_loop()
    _install_signal_handlers(loop)

    logger.info(f"Connecting to PLC at {config.plc_host}:{config.plc_port}")
    await modbus_mgr.connect()
    logger.info(f"Connection status: {modbus_mgr.client.connected}")

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")

    # Dashboard — shares live references with the optimizer's asyncio loop
    dashboard_shared.update({
        "gate": gate,
        "advisor": advisor,
        "monitor": monitor,
        "config": config,
        "modbus": modbus_mgr,
        "audit": audit,
        "buffer_lock": buffer_lock,
        "ring_buffer": ring_buffer,
        "latest_metrics": latest_metrics,
        "shutdown_event": shutdown_event,
        "csv_queue": csv_queue,
    })
    dashboard_app = create_app(dashboard_shared)

    try:
        logger.info(f"Starting main loops (Phase {config.phase.value})")
        logger.info("Dashboard available at http://localhost:8420")
        await asyncio.gather(
            read_loop(modbus_mgr, config),
            heartbeat_loop(gate, config),
            analysis_loop(executor, monitor, advisor, gate, config),
            connection_monitor(modbus_mgr, gate, config),
            start_dashboard(dashboard_app, host="0.0.0.0", port=8420),
        )
    except asyncio.CancelledError:
        logger.info("Tasks cancelled")
    except Exception as e:
        logger.critical(f"Unhandled exception: {e}", exc_info=True)
    finally:
        logger.info("Shutdown: persisting final state")
        _persist_state(gate, advisor, latest_metrics)
        logger.info("Shutdown: stopping CSV logger")
        try:
            csv_queue.put_nowait(None)
        except queue.Full:
            pass
        csv_logger.join(timeout=5)
        logger.info(f"Shutdown: closing {modbus_mgr.transport_name} client")
        try:
            await modbus_mgr.close()
        except Exception:
            pass
        executor.shutdown(wait=False)
        audit.close()
        logger.info("=== HXI Optimizer shutdown complete ===")


if __name__ == "__main__":
    asyncio.run(main())
