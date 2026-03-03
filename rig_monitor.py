#!/usr/bin/env python3
"""
TopDrive AI — Rig Monitor
===========================
Background service + dashboard for automatic PLC data capture.

Watches for eWon VPN tunnel changes, auto-identifies connected rigs,
captures live Modbus data to CSV, and provides a Rich TUI dashboard.

Usage:
    python rig_monitor.py --service       # Headless background service
    python rig_monitor.py --dashboard     # Rich TUI dashboard
    python rig_monitor.py --status        # One-shot status print
    python rig_monitor.py --install       # Create Windows Task Scheduler entry
    python rig_monitor.py --uninstall     # Remove Task Scheduler entry
    python rig_monitor.py --stop          # Stop running service
"""
import argparse
import json
import logging
import os
import signal
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional, Tuple

# Project root = directory containing this script
PROJECT_DIR = Path(__file__).resolve().parent

# Add project to sys.path so imports work
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from config import MachineProfile, RegisterDef

logger = logging.getLogger("rig_monitor")

# ═══════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════

STATUS_FILE = PROJECT_DIR / "monitor_status.json"
STOP_SIGNAL_FILE = PROJECT_DIR / "monitor_stop.signal"
LOG_DIR = PROJECT_DIR / "logs"
DEFAULT_PROFILE_DIR = PROJECT_DIR / "profiles"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "live_captures"
VPN_POLL_INTERVAL = 5.0     # seconds between VPN checks
STATUS_WRITE_INTERVAL = 0.5  # seconds between status.json updates
DEFAULT_HZ = 10.0
DEFAULT_TIMEOUT = 5.0

CONNECTION_STATE_NAMES = {
    0: "IDLE", 1: "STAB", 2: "SPIN_IN", 3: "SHOULDER",
    4: "POWER_TIGHT", 5: "HOLD", 6: "BREAKOUT", 7: "SPIN_OUT",
    8: "COMPLETE", 9: "COMPLETE",
}


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def atomic_write_json(path: Path, data: dict):
    """Atomically write JSON using tmp file + os.replace (safe on NTFS)."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=".status_")
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def format_uptime(seconds: float) -> str:
    """Format seconds as human-readable uptime string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.0f}m"
    elif seconds < 86400:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"
    else:
        d = int(seconds // 86400)
        h = int((seconds % 86400) // 3600)
        return f"{d}d {h}h"


def check_pid_alive(pid: int) -> bool:
    """Check if a process with given PID is still running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


# ═══════════════════════════════════════════════════════════════════
# RigMonitorService — Background Capture Service
# ═══════════════════════════════════════════════════════════════════

class RigMonitorService:
    """Headless background service: watches VPN, captures data, writes status."""

    def __init__(self, profile_dir: str = None, output_dir: str = None,
                 hz: float = DEFAULT_HZ, timeout: float = DEFAULT_TIMEOUT):
        self.profile_dir = Path(profile_dir or DEFAULT_PROFILE_DIR)
        self.output_dir = Path(output_dir or DEFAULT_OUTPUT_DIR)
        self.hz = hz
        self.timeout = timeout

        self._running = True
        self._capture_thread: Optional[threading.Thread] = None
        self._capture_stop = threading.Event()
        self._current_profile: Optional[MachineProfile] = None
        self._current_ewon: str = ""
        self._current_profile_path: str = ""

        # Shared state (read by status writer, written by capture thread)
        self._state = "starting"
        self._rig_state: Dict = {}
        self._vpn_info: Dict = {}
        self._history = {
            "session_connections": 0,
            "today_connections": 0,
            "vpn_switches_today": 0,
            "last_switch_time": None,
        }
        self._errors: list = []
        self._start_time = time.time()

    # ───────────────────────────────────────────────────────────────
    # Lifecycle
    # ───────────────────────────────────────────────────────────────

    def run(self):
        """Main service entry point. Runs until stopped."""
        self._setup_logging()
        self._setup_signal_handlers()
        self._start_time = time.time()
        logger.info("=" * 60)
        logger.info("TopDrive AI Rig Monitor — service starting")
        logger.info(f"  Profiles: {self.profile_dir}")
        logger.info(f"  Output:   {self.output_dir}")
        logger.info(f"  PID:      {os.getpid()}")
        logger.info("=" * 60)

        # Remove stale stop signal
        if STOP_SIGNAL_FILE.exists():
            STOP_SIGNAL_FILE.unlink()

        # Start status writer thread
        status_thread = threading.Thread(
            target=self._status_writer_loop, daemon=True)
        status_thread.start()

        # Import EWonDetector (late import to avoid breaking if fleet_manager missing)
        try:
            from fleet_manager import EWonDetector
            detector = EWonDetector()
        except ImportError:
            logger.error("fleet_manager.py not found — cannot detect VPN tunnels")
            self._state = "error"
            self._errors.append("fleet_manager.py not found")
            # Fall back: just wait
            while self._running:
                time.sleep(1)
            return

        # VPN watch loop (modified from EWonDetector.watch())
        self._state = "waiting_for_vpn"
        previous_tunnel = None
        logger.info("Watching for eWon VPN tunnels...")

        while self._running:
            # Check for stop signal file
            if STOP_SIGNAL_FILE.exists():
                logger.info("Stop signal received")
                STOP_SIGNAL_FILE.unlink(missing_ok=True)
                break

            try:
                current = detector.detect_active_tunnel()
                current_gw = current["gateway_ip"] if current else None
                prev_gw = (previous_tunnel["gateway_ip"]
                           if previous_tunnel else None)

                if current_gw != prev_gw:
                    self._on_tunnel_change(current)
                    previous_tunnel = current

            except Exception as e:
                logger.exception(f"Tunnel detection error: {e}")

            # Interruptible sleep
            for _ in range(int(VPN_POLL_INTERVAL)):
                if not self._running:
                    break
                time.sleep(1.0)

        # Shutdown
        self._stop_capture()
        self._state = "stopped"
        self._write_status()
        logger.info("Service stopped")

    # ───────────────────────────────────────────────────────────────
    # VPN Tunnel Handling
    # ───────────────────────────────────────────────────────────────

    def _on_tunnel_change(self, tunnel_info):
        """Handle VPN tunnel up/down/switch."""
        if tunnel_info is None:
            logger.info("VPN tunnel DOWN")
            self._stop_capture()
            self._current_profile = None
            self._current_ewon = ""
            self._vpn_info = {"connected": False}
            self._state = "waiting_for_vpn"
            return

        gw = tunnel_info.get("gateway_ip", "?")
        device = tunnel_info.get("device_name", "")
        method = tunnel_info.get("detection_method", "?")
        latency = tunnel_info.get("latency_ms", 0)

        logger.info(f"VPN tunnel UP: gw={gw} device=\"{device}\" "
                    f"method={method} latency={latency:.0f}ms")

        self._vpn_info = {
            "connected": True,
            "gateway_ip": gw,
            "device_name": device,
            "detection_method": method,
            "latency_ms": latency,
        }

        # Same rig reconnecting? (VPN glitch)
        if (self._current_ewon and device and
                device == self._current_ewon and
                self._capture_thread and self._capture_thread.is_alive()):
            logger.info(f"Same rig reconnected: {device} — keeping capture")
            return

        # Different rig (or first connection)
        self._stop_capture()
        self._history["vpn_switches_today"] += 1
        self._history["last_switch_time"] = datetime.now(
            timezone.utc).isoformat()

        self._state = "discovering"
        result = self._identify_rig(tunnel_info)
        if result:
            path, profile = result
            self._current_profile = profile
            self._current_profile_path = path
            self._current_ewon = device or profile.ewon_name
            self._start_capture(path, profile)
            self._state = "capturing"
        else:
            logger.warning("Could not identify rig — waiting for next change")
            self._state = "error"
            self._errors.append(f"Could not identify rig: {device or gw}")

    # ───────────────────────────────────────────────────────────────
    # Rig Identification (reuses capture_live.py functions)
    # ───────────────────────────────────────────────────────────────

    def _identify_rig(self, tunnel_info) -> Optional[Tuple[str, MachineProfile]]:
        """Identify which rig is connected and get/create its profile."""
        try:
            from capture_live import (
                _scrape_ewon_name, _find_profile_by_ewon,
                _find_profile_by_ip, _match_fleet_entry,
                _load_fleet_csv, _create_rig_profile,
                _detect_equipment_type,
            )
        except ImportError as e:
            logger.error(f"Cannot import capture_live functions: {e}")
            return None

        gw = tunnel_info.get("gateway_ip", "")
        device_name = tunnel_info.get("device_name", "")
        profile_dir = str(self.profile_dir)

        # Step 1: Get device name (scrape if not from eCatcher)
        if not device_name:
            logger.info("No device name from eCatcher — scraping eWon web UI")
            subnet = gw.rsplit(".", 1)[0]
            for try_ip in [f"{subnet}.19", gw]:
                device_name = _scrape_ewon_name(try_ip, timeout=6.0)
                if device_name:
                    logger.info(f"Got device name: \"{device_name}\"")
                    self._vpn_info["device_name"] = device_name
                    break
            if not device_name:
                logger.warning("Could not determine device name")

        # Step 2: Match existing profile by ewon_name
        if device_name:
            match = _find_profile_by_ewon(profile_dir, device_name)
            if match:
                path, profile = match
                logger.info(f"Matched profile: {profile.name} ({path})")

                # Check if reg_map needs physics mapping
                if not profile.reg_map:
                    self._try_physics_map(profile, path)

                return (path, profile)

        # Step 3: Try fleet CSV lookup
        if device_name:
            try:
                fleet = _load_fleet_csv()
                fleet_entry = _match_fleet_entry(device_name, fleet)
            except Exception:
                fleet_entry = None

            if fleet_entry:
                # Detect PLC IP (scan subnet for Modbus)
                plc_ip = self._find_plc_ip(gw)
                if plc_ip:
                    result = _create_rig_profile(
                        ip=plc_ip, port=502,
                        device_name=device_name,
                        fleet_entry=fleet_entry,
                        profile_dir=profile_dir,
                    )
                    if result:
                        path, profile = result
                        logger.info(f"Created new profile: {profile.name}")
                        return result

        # Step 4: Fallback — find by IP
        plc_ip = self._find_plc_ip(gw)
        if plc_ip:
            match = _find_profile_by_ip(profile_dir, plc_ip)
            if match:
                path, profile = match
                logger.info(f"Matched by IP: {profile.name}")
                return (path, profile)

            # Last resort: create generic profile
            try:
                result = _create_rig_profile(
                    ip=plc_ip, port=502,
                    device_name=device_name or f"unknown_{gw}",
                    fleet_entry=None,
                    profile_dir=profile_dir,
                )
                if result:
                    return result
            except Exception as e:
                logger.error(f"Failed to create profile: {e}")

        return None

    def _find_plc_ip(self, gateway_ip: str) -> Optional[str]:
        """Find PLC IP on the eWon subnet via quick Modbus probe."""
        try:
            from capture_live import ModbusTCPClient
        except ImportError:
            return None

        subnet = gateway_ip.rsplit(".", 1)[0]
        # Common PLC IPs behind eWon: .25, .1, .2, .10
        candidates = [f"{subnet}.{h}" for h in (25, 1, 2, 10, 100, 50)]

        for ip in candidates:
            try:
                client = ModbusTCPClient(ip, 502, timeout=3.0)
                if client.connect():
                    # Quick read to verify it's a real PLC
                    regs = client.read_holding_registers(0, 1)
                    client.close()
                    if regs is not None:
                        logger.info(f"Found PLC at {ip}")
                        return ip
                    client.close()
            except Exception:
                pass
        logger.warning(f"No PLC found on subnet {subnet}.x")
        return None

    def _try_physics_map(self, profile: MachineProfile, path: str):
        """Try to auto-map registers for a profile with empty reg_map."""
        try:
            from physics_mapper import auto_map_registers
            logger.info(f"Running physics auto-mapper for {profile.name}...")
            reg_map = auto_map_registers(
                ip=profile.plc_ip,
                port=profile.plc_port or 502,
                unit_id=profile.unit_id,
                equipment_type=profile.equipment_type or "unknown",
                profile=profile,
                passes=6, delay=2.0,
                min_confidence=0.20,
                quiet=True,
            )
            if reg_map:
                profile.reg_map = reg_map
                profile.to_yaml(path)
                logger.info(f"Physics mapper: {len(reg_map)} registers mapped")
        except Exception as e:
            logger.warning(f"Physics mapper failed: {e}")

    # ───────────────────────────────────────────────────────────────
    # Capture Thread
    # ───────────────────────────────────────────────────────────────

    def _start_capture(self, profile_path: str, profile: MachineProfile):
        """Start capture in a background thread."""
        self._capture_stop.clear()
        self._rig_state = {
            "connected": False,
            "values": {},
            "elapsed": 0,
            "hz": 0,
            "conn_num": 0,
            "rows": 0,
            "engine": None,
            "mapped_fields": set(),
            "error": None,
            "scan_regs": 0,
        }
        self._capture_thread = threading.Thread(
            target=self._capture_worker,
            args=(profile_path, profile),
            daemon=True,
            name=f"capture_{profile.name}",
        )
        self._capture_thread.start()
        logger.info(f"Capture started: {profile.name} at {profile.plc_ip}")

    def _stop_capture(self):
        """Stop current capture thread gracefully."""
        if self._capture_thread and self._capture_thread.is_alive():
            logger.info("Stopping capture thread...")
            self._capture_stop.set()
            self._capture_thread.join(timeout=10)
            if self._capture_thread.is_alive():
                logger.warning("Capture thread did not stop in time")
        self._capture_thread = None

    def _capture_worker(self, profile_path: str, profile: MachineProfile):
        """Capture worker thread — modeled on capture_live._rig_worker()."""
        try:
            from capture_live import (
                ModbusTCPClient, reg_map_from_profile, compute_block_reads,
                StateInferenceEngine, ConnectionSegmenter, LiveCSVWriter,
                decode_registers_from_dict, decode_int16_signed,
                CSV_COLUMNS, _build_profile_blocks, _quick_register_scan,
                _build_adaptive_blocks, log_connection_event,
            )
        except ImportError as e:
            logger.error(f"Cannot import capture functions: {e}")
            self._rig_state["error"] = f"Import error: {e}"
            return

        name = profile.name
        reg_map, word_swap = reg_map_from_profile(profile)
        unit_id = profile.unit_id
        mapped_fields = set(reg_map.keys())
        state = self._rig_state
        state["mapped_fields"] = mapped_fields

        if not reg_map:
            logger.error(f"Empty register map for {name}")
            state["error"] = "Empty register map"
            return

        # Block reads
        block_reads = compute_block_reads(reg_map)
        scan_done = False

        # Client, engine, writer
        client = ModbusTCPClient(
            profile.plc_ip, profile.plc_port or 502,
            timeout=self.timeout, unit_id=unit_id)
        engine = StateInferenceEngine()
        segmenter = engine
        rig_output = str(self.output_dir / name)
        writer = LiveCSVWriter(rig_output, mode="segmented", prefix="capture")
        writer.start()

        poll_interval = 1.0 / self.hz
        poll_count = 0
        poll_errors = 0
        total_rows = 0
        t_start = time.time()
        reconnect_delay = 1.0

        logger.info(f"[{name}] Worker started: {len(reg_map)} regs, "
                    f"{len(block_reads)} blocks, {self.hz} Hz target")

        while not self._capture_stop.is_set():
            # Connect / reconnect
            if not client.connected:
                if client.connect():
                    state["connected"] = True
                    state["error"] = None
                    reconnect_delay = 1.0
                    logger.info(f"[{name}] Connected to {profile.plc_ip}")

                    # Adaptive scan for smart mode
                    if not scan_done:
                        if len(reg_map) >= 3:
                            try:
                                block_reads = _build_profile_blocks(reg_map)
                            except Exception:
                                pass
                            state["scan_regs"] = sum(
                                bc for _, bc in block_reads)
                        else:
                            state["error"] = "Scanning..."
                            try:
                                nonzero = _quick_register_scan(client)
                                n_found = len(nonzero) if nonzero else 0
                                state["scan_regs"] = n_found
                                if n_found < 15:
                                    state["error"] = (
                                        f"Only {n_found} regs — likely gateway")
                                    state["connected"] = False
                                    client.close()
                                    scan_done = True
                                    return
                                block_reads = _build_adaptive_blocks(
                                    nonzero, reg_map)
                            except Exception:
                                pass
                        scan_done = True
                else:
                    state["connected"] = False
                    state["error"] = f"Retry {reconnect_delay:.0f}s"
                    wait_until = time.time() + reconnect_delay
                    while (not self._capture_stop.is_set() and
                           time.time() < wait_until):
                        time.sleep(0.5)
                    reconnect_delay = min(reconnect_delay * 2, 30.0)
                    continue

            t_poll_start = time.time()

            # Read all blocks
            reg_dict = {}
            read_ok = True
            for bstart, bcount in block_reads:
                regs = client.read_holding_registers(bstart, bcount)
                if regs is None:
                    read_ok = False
                    break
                for i, val in enumerate(regs):
                    reg_dict[bstart + i] = val

            if not read_ok:
                poll_errors += 1
                if poll_errors > 3:
                    client.close()
                    state["connected"] = False
                    poll_errors = 0
                    logger.warning(f"[{name}] Too many read errors, reconnecting")
                continue

            poll_errors = 0
            poll_count += 1
            elapsed = time.time() - t_start
            now_str = datetime.now(timezone.utc).isoformat(
                timespec='milliseconds')
            values = decode_registers_from_dict(reg_dict, reg_map, word_swap)

            # Capture raw unmapped registers
            decoded_addrs = set()
            for info in reg_map.values():
                a = info["addr"]
                decoded_addrs.add(a)
                if info["type"] in ("FLOAT32", "INT32", "UINT32"):
                    decoded_addrs.add(a + 1)
            for addr, val in sorted(reg_dict.items()):
                if addr not in decoded_addrs and val != 0:
                    values[f"raw_R{addr}"] = float(decode_int16_signed(val))

            # State inference
            event, inferred = engine.update(values, elapsed, mapped_fields)
            values.update(inferred)

            if event:
                if "START" in event:
                    writer.new_connection(segmenter.connection_num)
                    self._history["session_connections"] += 1
                    self._history["today_connections"] += 1
                    logger.info(f"[{name}] Connection #{segmenter.connection_num} started")
                elif "END" in event:
                    meta = engine.get_connection_metadata(elapsed)
                    sp = writer._current_path
                    if sp:
                        try:
                            with open(sp.with_suffix('.json'), 'w') as jf:
                                json.dump(meta, jf, indent=2)
                        except Exception:
                            pass
                    writer.end_connection()
                    logger.info(f"[{name}] Connection ended")
                    # Log to connection history
                    try:
                        log_connection_event(
                            self._current_ewon, profile.plc_ip,
                            profile.name, profile.equipment_type,
                            event_type="connection_end",
                            profile_dir=str(self.profile_dir),
                        )
                    except Exception:
                        pass

            # Idle decimation
            skip_write = False
            if engine.state == 0 and not engine.in_connection:
                if total_rows % 10 != 0:
                    skip_write = True

            if not skip_write:
                writer.write_row(values, now_str, elapsed)
            total_rows += 1

            # Update shared state for dashboard
            state["values"] = values
            state["elapsed"] = elapsed
            state["hz"] = poll_count / max(elapsed, 0.001)
            state["conn_num"] = segmenter.connection_num
            state["rows"] = total_rows
            state["engine"] = engine
            state["mapped_fields"] = mapped_fields

            # Sleep to maintain target rate
            sleep_time = poll_interval - (time.time() - t_poll_start)
            if sleep_time > 0:
                time.sleep(sleep_time)

        # Cleanup
        writer.close()
        client.close()
        logger.info(f"[{name}] Worker stopped ({total_rows} rows captured)")

    # ───────────────────────────────────────────────────────────────
    # Status JSON Writer
    # ───────────────────────────────────────────────────────────────

    def _status_writer_loop(self):
        """Periodically write status to JSON file (daemon thread)."""
        while self._running:
            try:
                self._write_status()
            except Exception as e:
                logger.error(f"Status write failed: {e}")
            time.sleep(STATUS_WRITE_INTERVAL)

    def _write_status(self):
        """Write current status to monitor_status.json."""
        engine = self._rig_state.get("engine")
        values = self._rig_state.get("values", {})

        # Extract engine state safely
        engine_info = {}
        if engine:
            try:
                state_val = engine.state
                engine_info = {
                    "state": state_val,
                    "state_name": CONNECTION_STATE_NAMES.get(
                        state_val, f"UNKNOWN({state_val})"),
                    "state_path": [s for s in getattr(
                        engine, '_state_history', [])],
                    "shoulder_detected": getattr(
                        engine, '_shoulder_detected', False),
                    "slope_dT_dN": getattr(engine, 'slope_dT_dN', 0),
                    "quality_score": getattr(
                        engine, 'quality_score', 0),
                }
            except Exception:
                pass

        # Build rig info
        rig_info = {}
        if self._current_profile:
            p = self._current_profile
            rig_info = {
                "name": p.name,
                "equipment_type": p.equipment_type,
                "customer": p.customer,
                "plc_ip": p.plc_ip,
                "profile_path": self._current_profile_path,
            }

        # Clean values for JSON (remove non-serializable items)
        clean_values = {}
        for k, v in values.items():
            if isinstance(v, (int, float)):
                clean_values[k] = v

        status = {
            "service_pid": os.getpid(),
            "service_start_time": datetime.fromtimestamp(
                self._start_time, tz=timezone.utc).isoformat(),
            "uptime_seconds": time.time() - self._start_time,
            "state": self._state,
            "vpn": self._vpn_info,
            "rig": rig_info,
            "capture": {
                "active": (self._capture_thread is not None and
                           self._capture_thread.is_alive()),
                "connected": self._rig_state.get("connected", False),
                "elapsed_s": self._rig_state.get("elapsed", 0),
                "hz": self._rig_state.get("hz", 0),
                "total_rows": self._rig_state.get("rows", 0),
                "connection_num": self._rig_state.get("conn_num", 0),
                "output_dir": str(self.output_dir),
                "error": self._rig_state.get("error"),
            },
            "live_values": clean_values,
            "engine": engine_info,
            "history": self._history.copy(),
            "errors": self._errors[-10:],  # last 10 errors
            "last_updated": datetime.now(timezone.utc).isoformat(
                timespec='milliseconds'),
        }

        atomic_write_json(STATUS_FILE, status)

    # ───────────────────────────────────────────────────────────────
    # Setup
    # ───────────────────────────────────────────────────────────────

    def _setup_logging(self):
        """Configure rotating file logger (skip if already set up)."""
        root = logging.getLogger()
        if root.handlers:
            return  # Already configured by _setup_service_logging or previous run
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_DIR / "rig_monitor.log",
            maxBytes=5_000_000,
            backupCount=5,
            encoding='utf-8',
        )
        handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        ))
        root.setLevel(logging.INFO)
        root.addHandler(handler)

        # Also log to stderr when running interactively
        if sys.stderr and hasattr(sys.stderr, 'isatty') and sys.stderr.isatty():
            root.addHandler(logging.StreamHandler(sys.stderr))

    def _setup_signal_handlers(self):
        """Handle SIGTERM/SIGINT for clean shutdown."""
        def _handler(signum, frame):
            logger.info(f"Signal {signum} received — shutting down")
            self._running = False

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)


# ═══════════════════════════════════════════════════════════════════
# DashboardViewer — Rich TUI Dashboard
# ═══════════════════════════════════════════════════════════════════

class DashboardViewer:
    """Read-only dashboard that displays service status from status.json."""

    def __init__(self, status_path: Path = STATUS_FILE):
        self.status_path = status_path

    def run(self):
        """Main dashboard loop."""
        try:
            from rich.live import Live
            from rich.table import Table
            from rich.panel import Panel
            from rich.text import Text
            from rich.console import Console
            from rich.layout import Layout
            self._run_rich()
        except ImportError:
            print("  'rich' not installed — using plain text mode")
            print("  Install with: pip install rich")
            self._run_plain()

    def _read_status(self) -> Optional[dict]:
        """Read status.json, return None if stale or missing."""
        try:
            data = json.loads(self.status_path.read_text(encoding='utf-8'))
            return data
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _run_rich(self):
        """Rich TUI dashboard with auto-refresh."""
        from rich.live import Live
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
        from rich.console import Console

        console = Console()
        console.clear()

        def build_display():
            status = self._read_status()
            if not status:
                return Panel(
                    "[bold red]SERVICE NOT RUNNING[/]\n\n"
                    "Start with: python rig_monitor.py --service",
                    title="TopDrive AI — Rig Monitor",
                    border_style="red",
                )

            # Check freshness
            try:
                last = datetime.fromisoformat(
                    status.get("last_updated", ""))
                age = (datetime.now(timezone.utc) - last).total_seconds()
                stale = age > 30
            except Exception:
                stale = True
                age = 999

            state = status.get("state", "unknown")
            uptime = format_uptime(status.get("uptime_seconds", 0))
            vpn = status.get("vpn", {})
            rig = status.get("rig", {})
            cap = status.get("capture", {})
            vals = status.get("live_values", {})
            eng = status.get("engine", {})
            hist = status.get("history", {})

            # Build output lines
            lines = []

            # Header
            if stale:
                lines.append(
                    "[bold red]SERVICE NOT RESPONDING[/] "
                    f"(last update {age:.0f}s ago)")
            else:
                state_color = {
                    "capturing": "green",
                    "waiting_for_vpn": "yellow",
                    "discovering": "cyan",
                    "error": "red",
                    "starting": "yellow",
                    "stopped": "red",
                }.get(state, "white")
                lines.append(
                    f"  Status: [bold {state_color}]{state.upper()}"
                    f"[/]    Uptime: {uptime}    "
                    f"PID: {status.get('service_pid', '?')}")

            lines.append("")

            # VPN section
            if vpn.get("connected"):
                dev = vpn.get("device_name", "?")
                gw = vpn.get("gateway_ip", "?")
                lat = vpn.get("latency_ms", 0)
                lines.append(
                    f"  VPN: [bold green]● CONNECTED[/]  "
                    f'"{dev}"  {gw}  '
                    f"({lat:.0f}ms)")
            else:
                lines.append(
                    "  VPN: [bold red]● DISCONNECTED[/]  "
                    "Waiting for eWon tunnel...")

            # Rig section
            if rig.get("name"):
                lines.append(
                    f"  Rig: [bold]{rig['name']}[/]    "
                    f"Type: {rig.get('equipment_type', '?').upper()}    "
                    f"IP: {rig.get('plc_ip', '?')}")
            lines.append("")

            # Capture section
            if cap.get("active"):
                hz = cap.get("hz", 0)
                rows = cap.get("total_rows", 0)
                conn = cap.get("connection_num", 0)
                err = cap.get("error", "")
                conn_str = (f"[bold green]● ACTIVE[/]  "
                            f"{hz:.1f} Hz  {rows:,} rows  Conn #{conn}")
                if err:
                    conn_str += f"  [yellow]({err})[/]"
                lines.append(f"  Capture: {conn_str}")
            elif cap.get("error"):
                lines.append(
                    f"  Capture: [red]● ERROR[/]  {cap['error']}")
            else:
                lines.append("  Capture: [dim]● IDLE[/]")

            lines.append("")

            # Live values
            if vals:
                def v(key, fmt="{:.1f}", unit=""):
                    val = vals.get(key)
                    if val is None:
                        return "[dim]--[/]"
                    return f"{fmt.format(val)}{unit}"

                # State display
                state_val = eng.get("state", 0)
                state_name = eng.get("state_name", "?")
                quality = eng.get("quality_score", 0)
                shoulder = eng.get("shoulder_detected", False)
                path_hist = eng.get("state_path", [])
                path_names = {
                    0: "IDL", 1: "STB", 2: "SPN", 3: "SHO",
                    4: "PWR", 5: "HLD", 6: "BRK", 7: "SPO",
                    8: "CMP", 9: "CMP",
                }
                path_str = ">".join(
                    path_names.get(s, "?") for s in path_hist[-6:])

                lines.append(
                    f"  Torque  {v('torque_ftlbs', '{:>10,.0f}', ' ft-lb')}"
                    f"  │  RPM   {v('rpm', '{:>7.1f}')}"
                    f"  │  Temp  {v('oil_temp_f', '{:>6.1f}', ' F')}")
                lines.append(
                    f"  Turns   {v('turns', '{:>10.3f}')}"
                    f"  │  Target {v('target_torque', '{:>6,.0f}')}"
                    f"  │  Peak  {v('peak_torque', '{:>6,.0f}')}")
                lines.append(
                    f"  Hookload {v('hookload_klbs', '{:>9.1f}', ' klbs')}"
                    f"  │  Shoulder {v('shoulder_torque', '{:>5,.0f}')}"
                    f"  │  Press {v('pressure_psi', '{:>5,.0f}', ' PSI')}")
                lines.append(
                    f"  State: [bold]{state_name}[/]"
                    f"  │  Fault: {v('fault_code', '{:.0f}')}"
                    f"  │  Quality: {quality:.2f}"
                    f"  │  Shoulder: "
                    f"{'[green]YES[/]' if shoulder else '[dim]no[/]'}")
                if path_str:
                    lines.append(f"  Path: [cyan]{path_str}[/]")

            lines.append("")

            # History
            sess_conn = hist.get("session_connections", 0)
            today_conn = hist.get("today_connections", 0)
            switches = hist.get("vpn_switches_today", 0)
            lines.append(
                f"  Session: {sess_conn} connections  │  "
                f"Today: {today_conn} total  │  "
                f"VPN switches: {switches}")

            # Errors
            errs = status.get("errors", [])
            if errs:
                lines.append("")
                lines.append(
                    f"  [yellow]Last error: {errs[-1]}[/]")

            border = "green" if state == "capturing" else (
                "yellow" if state == "waiting_for_vpn" else "red")

            return Panel(
                "\n".join(lines),
                title="[bold]TopDrive AI — Rig Monitor[/]",
                border_style=border,
                padding=(1, 2),
            )

        try:
            with Live(build_display(), refresh_per_second=2,
                      console=console) as live:
                while True:
                    time.sleep(0.5)
                    live.update(build_display())
        except KeyboardInterrupt:
            console.print("\n  Dashboard closed. Service continues running.")

    def _run_plain(self):
        """Plain text fallback (no rich)."""
        try:
            while True:
                status = self._read_status()
                # Clear screen (ANSI)
                sys.stdout.write("\033[2J\033[H")

                if not status:
                    print("  SERVICE NOT RUNNING")
                    print("  Start with: python rig_monitor.py --service")
                else:
                    state = status.get("state", "?")
                    uptime = format_uptime(
                        status.get("uptime_seconds", 0))
                    vpn = status.get("vpn", {})
                    rig = status.get("rig", {})
                    cap = status.get("capture", {})
                    vals = status.get("live_values", {})
                    eng = status.get("engine", {})

                    print("=" * 60)
                    print(f"  TopDrive AI — Rig Monitor")
                    print(f"  Status: {state.upper()}  Uptime: {uptime}")
                    print("=" * 60)

                    if vpn.get("connected"):
                        print(f"  VPN: CONNECTED  "
                              f"\"{vpn.get('device_name', '?')}\"  "
                              f"{vpn.get('gateway_ip', '?')}")
                    else:
                        print("  VPN: DISCONNECTED")

                    if rig.get("name"):
                        print(f"  Rig: {rig['name']}  "
                              f"Type: {rig.get('equipment_type', '?')}")

                    if cap.get("active"):
                        print(f"  Capture: ACTIVE  {cap.get('hz', 0):.1f} Hz"
                              f"  {cap.get('total_rows', 0):,} rows"
                              f"  Conn #{cap.get('connection_num', 0)}")

                    print("-" * 60)

                    def pv(key, label, fmt="{:.1f}", unit=""):
                        val = vals.get(key)
                        if val is not None:
                            return f"{label}: {fmt.format(val)}{unit}"
                        return f"{label}: --"

                    print(f"  {pv('torque_ftlbs', 'Torque', '{:,.0f}', ' ft-lb')}"
                          f"  |  {pv('rpm', 'RPM', '{:.1f}')}"
                          f"  |  {pv('oil_temp_f', 'Temp', '{:.1f}', ' F')}")
                    print(f"  {pv('turns', 'Turns', '{:.3f}')}"
                          f"  |  {pv('target_torque', 'Target', '{:,.0f}')}"
                          f"  |  {pv('hookload_klbs', 'Hookload', '{:.1f}', ' klbs')}")
                    print(f"  State: {eng.get('state_name', '?')}"
                          f"  |  Quality: {eng.get('quality_score', 0):.2f}")
                    print("=" * 60)

                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n  Dashboard closed.")


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def _setup_service_logging():
    """Set up logging early so crashes before RigMonitorService.run() are captured."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "rig_monitor.log",
        maxBytes=5_000_000, backupCount=5, encoding='utf-8',
    )
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        root.addHandler(handler)


def cmd_status():
    """Print one-shot status and exit."""
    if not STATUS_FILE.exists():
        print("  Service not running (no status file)")
        return

    try:
        status = json.loads(STATUS_FILE.read_text(encoding='utf-8'))
    except Exception as e:
        print(f"  Error reading status: {e}")
        return

    state = status.get("state", "?")
    uptime = format_uptime(status.get("uptime_seconds", 0))
    pid = status.get("service_pid", "?")
    vpn = status.get("vpn", {})
    rig = status.get("rig", {})
    cap = status.get("capture", {})

    # Check if service is actually alive
    alive = check_pid_alive(pid) if isinstance(pid, int) else False

    print(f"  State:    {state.upper()}"
          f"{'  (process dead!)' if not alive else ''}")
    print(f"  PID:      {pid}  Uptime: {uptime}")

    if vpn.get("connected"):
        print(f"  VPN:      CONNECTED — \"{vpn.get('device_name', '?')}\""
              f"  ({vpn.get('gateway_ip', '?')})")
    else:
        print(f"  VPN:      DISCONNECTED")

    if rig.get("name"):
        print(f"  Rig:      {rig['name']} ({rig.get('equipment_type', '?')})")

    if cap.get("active"):
        print(f"  Capture:  {cap.get('hz', 0):.1f} Hz, "
              f"{cap.get('total_rows', 0):,} rows, "
              f"Conn #{cap.get('connection_num', 0)}")
    else:
        print(f"  Capture:  Inactive")


def cmd_stop():
    """Send stop signal to running service."""
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text(encoding='utf-8'))
            pid = status.get("service_pid", 0)
            if isinstance(pid, int) and check_pid_alive(pid):
                # Create stop signal file
                STOP_SIGNAL_FILE.write_text("stop", encoding='utf-8')
                print(f"  Stop signal sent to PID {pid}")
                print(f"  Waiting for shutdown...")
                for _ in range(10):
                    time.sleep(1)
                    if not check_pid_alive(pid):
                        print(f"  Service stopped.")
                        return
                print(f"  Service still running (PID {pid}). "
                      f"Try: taskkill /PID {pid} /F")
                return
        except Exception:
            pass
    print("  No running service found.")


def cmd_install():
    """Install auto-start via Windows Startup folder shortcut."""
    import subprocess

    python_exe = sys.executable
    script_dir = Path(__file__).resolve().parent
    vbs_path = script_dir / "start_hidden.vbs"

    if not vbs_path.exists():
        print(f"  ERROR: start_hidden.vbs not found at {vbs_path}")
        return

    # Remove old Task Scheduler entry if present
    subprocess.run(
        ["schtasks", "/DELETE", "/TN", "TopDriveAI_RigMonitor", "/F"],
        capture_output=True, text=True,
    )

    # Install rich
    print("  Installing 'rich' package...")
    subprocess.run([python_exe, "-m", "pip", "install", "rich", "--quiet"],
                   check=False)

    # Create shortcut in Startup folder
    startup_dir = Path(os.environ.get("APPDATA", "")) / \
        "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    shortcut = startup_dir / "TopDriveAI_RigMonitor.lnk"

    # Use PowerShell to create .lnk
    ps_cmd = (
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'$s = $ws.CreateShortcut("{shortcut}"); '
        f'$s.TargetPath = "{vbs_path}"; '
        f'$s.WorkingDirectory = "{script_dir}"; '
        f'$s.Description = "TopDrive AI Rig Monitor"; '
        f'$s.Save()'
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True, text=True,
    )

    if shortcut.exists():
        print(f"\n  Service installed (Startup folder method)")
        print(f"  Shortcut: {shortcut}")
        print(f"  VBS:      {vbs_path}")
        print(f"\n  To start NOW:      double-click start_hidden.vbs")
        print(f"  To open dashboard: double-click launch_dashboard.bat")
        print(f"  To stop:           python rig_monitor.py --stop")
        print(f"  To uninstall:      python rig_monitor.py --uninstall")
    else:
        print(f"\n  FAILED to create startup shortcut.")
        print(f"  Try manually copying start_hidden.vbs to:")
        print(f"    {startup_dir}")


def cmd_uninstall():
    """Remove all auto-start methods (Task Scheduler + Startup folder)."""
    import subprocess

    removed = []

    # Remove Task Scheduler entry (old method)
    task_name = "TopDriveAI_RigMonitor"
    result = subprocess.run(
        ["schtasks", "/DELETE", "/TN", task_name, "/F"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        removed.append(f"Task Scheduler: {task_name}")

    # Remove Startup folder shortcut (new method)
    startup_dir = Path(os.environ.get("APPDATA", "")) / \
        "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    shortcut = startup_dir / "TopDriveAI_RigMonitor.lnk"
    if shortcut.exists():
        shortcut.unlink()
        removed.append(f"Startup shortcut: {shortcut}")

    # Stop running service
    cmd_stop()

    if removed:
        print(f"  Uninstalled:")
        for r in removed:
            print(f"    - {r}")
    else:
        print(f"  Nothing to uninstall (no task or shortcut found)")


def main():
    parser = argparse.ArgumentParser(
        description="TopDrive AI — Rig Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  --service     Run headless background service (for Task Scheduler)
  --dashboard   Launch Rich TUI dashboard
  --status      Print current status and exit
  --install     Create Windows auto-start task
  --uninstall   Remove Windows auto-start task
  --stop        Stop running service

Examples:
  python rig_monitor.py --service
  python rig_monitor.py --dashboard
  python rig_monitor.py --install
        """)

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--service", action="store_true",
                      help="Run as headless background service")
    mode.add_argument("--dashboard", action="store_true",
                      help="Launch Rich TUI dashboard")
    mode.add_argument("--status", action="store_true",
                      help="Print current status and exit")
    mode.add_argument("--install", action="store_true",
                      help="Create Windows Task Scheduler entry")
    mode.add_argument("--uninstall", action="store_true",
                      help="Remove Windows Task Scheduler entry")
    mode.add_argument("--stop", action="store_true",
                      help="Stop running service")

    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--hz", type=float, default=DEFAULT_HZ)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    args = parser.parse_args()

    if args.service:
        # Redirect stdout/stderr for pythonw.exe (no console)
        if not sys.stdout or not hasattr(sys.stdout, 'write'):
            sys.stdout = open(os.devnull, 'w')
        if not sys.stderr or not hasattr(sys.stderr, 'write'):
            sys.stderr = open(os.devnull, 'w')

        # Check for existing instance
        if STATUS_FILE.exists():
            try:
                existing = json.loads(
                    STATUS_FILE.read_text(encoding='utf-8'))
                pid = existing.get("service_pid", 0)
                if isinstance(pid, int) and pid != os.getpid() and check_pid_alive(pid):
                    # Already running — exit silently (no crash, no restart)
                    sys.exit(0)
            except Exception:
                pass

        # ── Crash-proof service loop ──
        # If the service crashes, wait with backoff then retry.
        # This prevents the CMD-window-flashing loop on Task Scheduler.
        _setup_service_logging()
        crash_count = 0
        max_backoff = 300  # 5 minutes max wait between retries

        while True:
            try:
                service = RigMonitorService(
                    profile_dir=args.profile_dir,
                    output_dir=args.output_dir,
                    hz=args.hz,
                    timeout=args.timeout,
                )
                service.run()
                break  # Clean exit (stop signal received)
            except SystemExit:
                break  # Explicit exit
            except Exception:
                crash_count += 1
                backoff = min(10 * (2 ** (crash_count - 1)), max_backoff)
                logger.exception(
                    f"Service crashed (attempt #{crash_count}), "
                    f"retrying in {backoff}s")
                # Write crash status so dashboard can show it
                try:
                    atomic_write_json(STATUS_FILE, {
                        "service_pid": os.getpid(),
                        "state": "crashed",
                        "errors": [
                            f"Crash #{crash_count}, retrying in {backoff}s"],
                        "last_updated": datetime.now(
                            timezone.utc).isoformat(timespec='milliseconds'),
                    })
                except Exception:
                    pass
                time.sleep(backoff)
                if crash_count >= 20:
                    logger.error("Too many crashes — giving up")
                    break

    elif args.dashboard:
        viewer = DashboardViewer()
        viewer.run()

    elif args.status:
        cmd_status()

    elif args.install:
        cmd_install()

    elif args.uninstall:
        cmd_uninstall()

    elif args.stop:
        cmd_stop()


if __name__ == "__main__":
    main()
