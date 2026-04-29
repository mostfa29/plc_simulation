"""HXI Optimizer — one-command launcher.

Single entry point that handles both local testing (with the bundled
simulated PLC) and production (against a real CPE305).

Usage examples:

    # Auto mode — chooses sim if no real plc_host is configured, else real
    python run.py

    # Force sim mode (spawns the simulator subprocess + launches optimizer)
    python run.py --sim

    # Force real-PLC mode (uses plc_host/plc_port from hxi_config.json)
    python run.py --real

    # Don't auto-open the browser
    python run.py --no-browser

    # Different sim port
    python run.py --sim --sim-port 5021

The launcher:
    1. Resolves which mode to run in.
    2. Writes a small overlay config to point the optimizer at the right
       PLC host/port (without modifying hxi_config.json on disk).
    3. (Sim only) spawns local_test.sim_plc as a child process and waits
       for it to bind.
    4. Spawns hxi_optimizer.main as a child process.
    5. Polls /healthz until the dashboard is up.
    6. Opens http://localhost:<port>/ in the default browser.
    7. Streams both subprocesses' output to stdout. On Ctrl+C, terminates
       both gracefully (sim first so the optimizer logs a clean shutdown).

Exit codes:
    0  clean shutdown via Ctrl+C
    1  launcher error
    2  optimizer crashed
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = REPO_ROOT / "hxi_optimizer" / "hxi_config.json"
TEMPLATE_PATH = REPO_ROOT / "hxi_optimizer" / "hxi_config.template.json"
DEFAULT_SIM_PORT = 5020


# ─────────────────────────────────────────────────────────────────────
# Mode resolution
# ─────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load hxi_config.json, falling back to template."""
    for p in (CONFIG_PATH, TEMPLATE_PATH):
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception as e:
                print(f"[launcher] WARNING could not parse {p.name}: {e}",
                      file=sys.stderr)
    return {}


def _resolve_mode(args, config: dict) -> str:
    """Return 'sim' or 'real'."""
    if args.sim:
        return "sim"
    if args.real:
        return "real"
    # Auto: real if config has a real-looking plc_host, else sim.
    host = config.get("plc_host", "")
    if host and host not in ("CONFIGURE_ME", "127.0.0.1", "localhost"):
        return "real"
    return "sim"


# ─────────────────────────────────────────────────────────────────────
# Subprocess control
# ─────────────────────────────────────────────────────────────────────

def _wait_for_port(host: str, port: int, timeout_s: float = 10.0) -> bool:
    """Poll until something binds host:port, or timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _wait_for_dashboard(port: int, timeout_s: float = 30.0) -> bool:
    """Poll /healthz until 200, or timeout."""
    deadline = time.time() + timeout_s
    url = f"http://127.0.0.1:{port}/healthz"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, socket.timeout, ConnectionResetError,
                ConnectionRefusedError):
            time.sleep(0.5)
    return False


def _spawn_sim_plc(port: int) -> subprocess.Popen:
    """Spawn `python -m local_test.sim_plc --port <port>`."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen(
        [sys.executable, "-u", "-m", "local_test.sim_plc", "--port", str(port)],
        cwd=str(REPO_ROOT),
        env=env,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def _spawn_optimizer(env_overrides: dict) -> subprocess.Popen:
    """Spawn `python -m hxi_optimizer.main` with HXI_* env overrides."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    env.update(env_overrides)
    return subprocess.Popen(
        [sys.executable, "-u", "-m", "hxi_optimizer.main"],
        cwd=str(REPO_ROOT),
        env=env,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def _terminate(proc: Optional[subprocess.Popen], name: str,
                timeout_s: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    print(f"[launcher] stopping {name}...")
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGINT)
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        print(f"[launcher] {name} didn't shut down gracefully; killing.",
              file=sys.stderr)
        proc.kill()
        proc.wait()


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="HXI Optimizer single-command launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--sim", action="store_true",
                        help="Force sim mode (spawn local PLC simulator)")
    parser.add_argument("--real", action="store_true",
                        help="Force real-PLC mode (use config's plc_host)")
    parser.add_argument("--sim-port", type=int, default=DEFAULT_SIM_PORT,
                        help=f"Port for the sim PLC (default {DEFAULT_SIM_PORT})")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't auto-open the dashboard browser tab")
    parser.add_argument("--dashboard-port", type=int, default=None,
                        help="Override dashboard port (default from config)")
    args = parser.parse_args()

    if args.sim and args.real:
        print("[launcher] --sim and --real are mutually exclusive",
              file=sys.stderr)
        return 1

    config = _load_config()
    mode = _resolve_mode(args, config)
    dashboard_port = (args.dashboard_port
                      or int(config.get("dashboard_port", 8420)))

    print(f"[launcher] === HXI Optimizer launcher ===")
    print(f"[launcher] mode:           {mode.upper()}")
    print(f"[launcher] dashboard:      http://localhost:{dashboard_port}")

    # Build env overrides for the optimizer subprocess. We use HXI_* env
    # vars so we don't have to touch hxi_config.json on disk — clean
    # separation between "this launcher run" and persistent config.
    env_overrides: dict = {"HXI_RUNTIME_MODE": mode}

    sim_proc: Optional[subprocess.Popen] = None
    optimizer_proc: Optional[subprocess.Popen] = None

    try:
        # ─── Sim PLC (if needed) ─────────────────────────────────────
        if mode == "sim":
            print(f"[launcher] starting sim PLC on 127.0.0.1:{args.sim_port}...")
            sim_proc = _spawn_sim_plc(args.sim_port)
            if not _wait_for_port("127.0.0.1", args.sim_port, timeout_s=8.0):
                print("[launcher] sim PLC didn't bind in 8s — aborting",
                      file=sys.stderr)
                return 1
            print(f"[launcher] sim PLC ready on 127.0.0.1:{args.sim_port}")
            # Tell the optimizer to point at the sim PLC.
            env_overrides["HXI_PLC_HOST"] = "127.0.0.1"
            env_overrides["HXI_PLC_PORT"] = str(args.sim_port)
        else:
            host = config.get("plc_host", "?")
            port = config.get("plc_port", 502)
            print(f"[launcher] real-PLC mode: connecting to {host}:{port}")

        # ─── Optimizer ─────────────────────────────────────────────
        print("[launcher] starting optimizer...")
        optimizer_proc = _spawn_optimizer(env_overrides)

        # ─── Wait for dashboard, then open browser ─────────────────
        if _wait_for_dashboard(dashboard_port, timeout_s=30.0):
            print(f"[launcher] dashboard live at http://localhost:{dashboard_port}")
            if not args.no_browser:
                try:
                    webbrowser.open(f"http://localhost:{dashboard_port}/")
                    print("[launcher] opened browser tab")
                except Exception as e:
                    print(f"[launcher] (could not open browser: {e})")
        else:
            print("[launcher] WARNING dashboard didn't become reachable in 30s",
                  file=sys.stderr)
            print("[launcher] check the optimizer output below for errors.",
                  file=sys.stderr)

        print("[launcher] running — Ctrl+C to stop")
        rc = optimizer_proc.wait()
        if rc != 0:
            print(f"[launcher] optimizer exited with code {rc}", file=sys.stderr)
            return 2
        return 0

    except KeyboardInterrupt:
        print("\n[launcher] Ctrl+C — shutting down")
        return 0
    finally:
        _terminate(optimizer_proc, "optimizer", timeout_s=8.0)
        _terminate(sim_proc, "sim PLC", timeout_s=3.0)
        print("[launcher] shutdown complete")


if __name__ == "__main__":
    sys.exit(main())
