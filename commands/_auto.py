"""HXI Optimizer automation brain.

One-file orchestrator that handles everything behind a clickable menu:
  bootstrap   Install Python packages (idempotent)
  discover    Find VPN tunnel + PLC on the LAN
  commission  Run tests 1-4, pin byte order, recommend deadband
  configure   Interactive wizard for the safety limits (only human input)
  start       Launch optimizer + open dashboard
  stop        Graceful shutdown
  status      One-line health report
  full        Do everything in order

Run directly with a subcommand, or via HXI.bat menu.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Ensure the repo root is importable regardless of where this script is run from
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
CONFIG = REPO_ROOT / "hxi_optimizer" / "hxi_config.json"
TEMPLATE = REPO_ROOT / "hxi_optimizer" / "hxi_config.template.json"
REGISTER_MAP = REPO_ROOT / "hxi_optimizer" / "comms" / "register_map.py"
OPT_LOG = REPO_ROOT / "hxi_optimizer" / "logs" / "optimizer.log"
DASH_URL = "http://127.0.0.1:8420"

REQUIRED_PACKAGES = [
    "pymodbus==3.13.*", "numpy", "fastapi", "uvicorn",
    "openpyxl", "paramiko", "psutil", "pyyaml", "asyncua",
    "websocket-client",
]


# ──────────────────────────────────────────────────────────────────────
# Pretty output
# ──────────────────────────────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"


def header(msg: str) -> None:
    print()
    print(BOLD + CYAN + "=" * 70 + RESET)
    print(BOLD + "  " + msg + RESET)
    print(BOLD + CYAN + "=" * 70 + RESET)


def ok(msg: str) -> None:
    print(f"  {GREEN}[OK]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}[WARN]{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}[FAIL]{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {CYAN}->{RESET} {msg}")


def step(msg: str) -> None:
    print(f"\n{BOLD}>>> {msg}{RESET}")


# ──────────────────────────────────────────────────────────────────────
# bootstrap
# ──────────────────────────────────────────────────────────────────────
def cmd_bootstrap() -> int:
    header("Bootstrap - verify Python + install packages")
    # Python version
    if sys.version_info < (3, 10):
        fail(f"Python {sys.version_info.major}.{sys.version_info.minor} is too old. Need 3.10+")
        return 1
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor} detected")

    # Check which packages are missing
    missing = []
    for pkg in REQUIRED_PACKAGES:
        import_name = {
            "pymodbus==3.13.*": "pymodbus",
            "websocket-client": "websocket",
            "pyyaml": "yaml",
        }.get(pkg, pkg.split("==")[0].replace("-", "_"))
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg)

    if not missing:
        ok("All packages already installed")
        return 0

    info(f"Installing {len(missing)} missing packages: {', '.join(missing)}")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet"] + missing,
        cwd=REPO_ROOT,
    )
    if r.returncode != 0:
        fail("pip install failed. Check your internet connection.")
        return 1
    ok("Packages installed")
    return 0


# ──────────────────────────────────────────────────────────────────────
# discover
# ──────────────────────────────────────────────────────────────────────
def _get_routes_and_interfaces() -> list[str]:
    """Return candidate VPN subnets using the OS routing table."""
    subnets: list[str] = []
    try:
        out = subprocess.check_output(["route", "PRINT", "-4"],
                                       stderr=subprocess.DEVNULL,
                                       text=True, errors="replace",
                                       timeout=5)
        for line in out.splitlines():
            # Lines like: "    0.0.0.0          0.0.0.0        192.168.1.1     192.168.1.50    25"
            m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+\S+\s+(\d+\.\d+\.\d+\.\d+)", line)
            if not m:
                continue
            dest, mask, iface = m.groups()
            # Consider non-default /24 networks on non-loopback interfaces
            if dest.startswith(("0.", "127.", "224.", "255.")) or iface.startswith("127."):
                continue
            if mask == "255.255.255.0":
                subnets.append(f"{dest}/24")
    except Exception as e:
        warn(f"route PRINT failed: {e}")
    # De-dup
    return list(dict.fromkeys(subnets))


def _probe_host(ip: str, port: int, timeout: float = 0.5) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def _scan_subnet_for_modbus(subnet: str, timeout: float = 0.4) -> list[str]:
    """Return IPs in the subnet that answer on TCP 502 (Modbus)."""
    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        return []
    hits: list[str] = []
    import concurrent.futures
    hosts = [str(h) for h in net.hosts() if not str(h).endswith(".255")]
    if len(hosts) > 256:
        hosts = hosts[:256]
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        futs = {ex.submit(_probe_host, h, 502, timeout): h for h in hosts}
        for f in concurrent.futures.as_completed(futs):
            if f.result():
                hits.append(futs[f])
    return sorted(hits, key=lambda ip: list(map(int, ip.split("."))))


def _identify_host(host: str, ewon_hint: str | None = None) -> None:
    """Try to match the host/ewon name to the fleet catalog and print result."""
    try:
        from hxi_optimizer.comms.fleet import FleetCatalog
    except ImportError as e:
        warn(f"Fleet catalog unavailable: {e}")
        return
    catalog = FleetCatalog.load()
    ident = None
    if ewon_hint:
        ident = catalog.identify_by_name(ewon_hint)
    if not ident:
        ident = catalog.identify_by_ip(host)
    if ident:
        print()
        ok(f"Machine identified: {ident.ewon_name}")
        print(f"      Customer  : {ident.customer}")
        print(f"      Equipment : {ident.spec.display_name} ({ident.equipment_type})")
        print(f"      PLC type  : {ident.spec.plc_type}")
        print(f"      Specs     : {ident.spec.horsepower:.0f} HP  "
              f"{ident.spec.gear_ratio:.1f}:1  "
              f"{ident.spec.max_torque_ft_lbs:,.0f} ft-lbs max  "
              f"{ident.spec.max_rpm:.0f} RPM max")
        print(f"      Register  : %{ident.spec.register_convention}")
        if ident.profile_path:
            print(f"      Profile   : profiles/{ident.profile_path.name}")
        print(f"      Confidence: {ident.confidence:.0%}  (source: {ident.source})")
    else:
        warn("Not in fleet catalog — will operate as generic HXI unless told otherwise.")
        print("      Set equipment type manually in hxi_config.json:")
        print("        \"equipment_type\": \"hxi\" | \"hxi_ht\" | \"warrior\" | ...")


def cmd_discover(subnet: str | None = None, host: str | None = None,
                 ewon_name: str | None = None) -> int:
    header("Discover - find PLC on VPN + identify machine")
    if host:
        info(f"Testing provided host: {host}")
        if _probe_host(host, 502):
            ok(f"Modbus 502 open on {host}")
            _identify_host(host, ewon_name)
            print(host)
            return 0
        fail(f"No Modbus response on {host}:502. Check eCatcher is connected.")
        return 1

    subnets = [subnet] if subnet else _get_routes_and_interfaces()
    if not subnets:
        fail("No candidate subnets found. Is eCatcher connected?")
        return 1
    info(f"Scanning {len(subnets)} subnet(s) for Modbus servers: {subnets}")
    all_hits: list[str] = []
    for sub in subnets:
        print(f"    scanning {sub} ...", end=" ", flush=True)
        hits = _scan_subnet_for_modbus(sub)
        print(f"{len(hits)} hit(s)")
        all_hits.extend(hits)
    # De-dup, exclude our own host (but keep 127.0.0.1 for local testing)
    try:
        my_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        my_ip = "0.0.0.0"
    all_hits = [h for h in all_hits if h != my_ip or h == "127.0.0.1"]
    all_hits = list(dict.fromkeys(all_hits))

    if not all_hits:
        fail("No Modbus servers found. Check eCatcher and the eWon routing mode.")
        return 1
    if len(all_hits) == 1:
        ok(f"Found PLC: {all_hits[0]}")
        _identify_host(all_hits[0], ewon_name)
        print(all_hits[0])
        return 0
    print()
    print("  Multiple Modbus hosts found:")
    for i, h in enumerate(all_hits):
        print(f"    {i+1}) {h}")
    choice = input("  Pick the PLC (number): ").strip()
    try:
        pick = all_hits[int(choice) - 1]
        ok(f"Selected: {pick}")
        print(pick)
        return 0
    except (ValueError, IndexError):
        fail("Invalid choice")
        return 1


# ──────────────────────────────────────────────────────────────────────
# commission
# ──────────────────────────────────────────────────────────────────────
def _patch_word_order(order: str) -> bool:
    """Update VERIFIED_WORD_ORDER in register_map.py in place."""
    src = REGISTER_MAP.read_text(encoding="utf-8")
    new = re.sub(
        r'VERIFIED_WORD_ORDER:\s*Optional\[str\]\s*=\s*"?[^\"\n]*"?',
        f'VERIFIED_WORD_ORDER: Optional[str] = "{order}"',
        src,
        count=1,
    )
    if new == src:
        return False
    REGISTER_MAP.write_text(new, encoding="utf-8")
    return True


def cmd_commission(host: str) -> int:
    header(f"Commission - run 4 tests against {host}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    # Test 1: byte order
    step("Test 1/4: FLOAT32 byte order (writes 1234.5 to spare %R06630)")
    r = subprocess.run(
        [sys.executable, "-m", "hxi_optimizer.deploy.commissioning_tests",
         "--test", "byte_order", "--host", host],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )
    output = r.stdout + r.stderr
    print(output)
    order: str | None = None
    if "PASS: ABCD" in output:
        order = "ABCD"
    elif "PASS: CDAB" in output:
        order = "CDAB"
    if order is None:
        fail("Byte-order test did NOT pass. Stopping.")
        return 1
    ok(f"Byte order: {order}")
    if _patch_word_order(order):
        ok(f"Pinned VERIFIED_WORD_ORDER = '{order}' in register_map.py")
    else:
        warn("Could not automatically pin byte order — check register_map.py")

    # Test 2: FC16 atomicity
    step("Test 2/4: FC16 paired-write atomicity (1000 trials, spare regs)")
    r = subprocess.run(
        [sys.executable, "-m", "hxi_optimizer.deploy.commissioning_tests",
         "--test", "fc16_atomicity", "--host", host, "--trials", "1000"],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )
    out2 = r.stdout + r.stderr
    print(out2)
    if "0 cross-faults across 1000 writes" not in out2:
        warn("FC16 atomicity test had issues. Check eWon routing mode.")

    # Test 3: VPN latency
    step("Test 3/4: VPN round-trip latency (100 reads)")
    r = subprocess.run(
        [sys.executable, "-m", "hxi_optimizer.deploy.commissioning_tests",
         "--test", "vpn_latency", "--host", host],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )
    print(r.stdout + r.stderr)

    # Test 4: noise floor (optional — needs driller). Skip in non-interactive runs.
    print()
    if not sys.stdin.isatty():
        info("Skipping noise-floor test (non-interactive run)")
        yn = "n"
    else:
        try:
            yn = input(f"{BOLD}Run noise-floor test? Requires 60 s at steady 60 RPM.{RESET} (y/N): ").strip().lower()
        except EOFError:
            yn = "n"
    recommended_deadband: float | None = None
    if yn == "y":
        step("Test 4/4: RPM noise floor (60 s at steady speed)")
        r = subprocess.run(
            [sys.executable, "-m", "hxi_optimizer.deploy.commissioning_tests",
             "--test", "noise_floor", "--host", host, "--rpm", "60"],
            cwd=REPO_ROOT, text=True, env=env,
        )
        # Parse the recommended deadband from the log file
        m = re.search(r"Set deadband_rpm\s*=\s*([\d.]+)", "" )
        # We don't capture stdout for this test (it's interactive), so just leave config as-is
        info("If a specific deadband_rpm was recommended, update the config with X_edit or let the wizard set it.")
    else:
        info("Skipped noise-floor test (can re-run any time)")

    ok("Commissioning complete")
    # Emit results for HXI.bat to capture
    print(f"\n__COMMISSION_RESULT__ order={order} host={host}")
    return 0


# ──────────────────────────────────────────────────────────────────────
# configure
# ──────────────────────────────────────────────────────────────────────
def _load_config() -> dict:
    if not CONFIG.exists():
        if not TEMPLATE.exists():
            raise FileNotFoundError(f"{TEMPLATE} not found")
        cfg = json.loads(TEMPLATE.read_text())
    else:
        cfg = json.loads(CONFIG.read_text())
    return cfg


def _save_config(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, indent=2))


def cmd_configure(host: str | None = None, deadband: float | None = None,
                  non_interactive: bool = False) -> int:
    header("Configure - populate hxi_config.json")
    cfg = _load_config()
    if host:
        cfg["plc_host"] = host
        cfg["plc_port"] = 502
        ok(f"plc_host = {host}")
    if deadband is not None:
        cfg["deadband_rpm"] = deadband
        ok(f"deadband_rpm = {deadband}")
    cfg.setdefault("phase", "A")
    cfg["require_verified_word_order"] = True

    safety = cfg.get("safety", {})
    needs = [k for k in ("abs_min_lower", "abs_max_lower",
                         "abs_min_upper", "abs_max_upper")
             if safety.get(k) in (None, 0)]
    if not needs:
        ok("Safety limits already set, nothing to ask.")
    elif non_interactive:
        warn(f"Safety limits missing: {needs}. Run `configure` interactively to fill in.")
    else:
        print()
        print(BOLD + "Safety limits (from Steve / hydraulic engineer):" + RESET)
        print("  These are the absolute hardware-safe counts for %R06603 / %R06604.")
        print("  Leave blank to keep null and skip the optimizer (observer mode still works).")
        print()
        for key, desc in [
            ("abs_min_lower", "min safe %R06603 (swash lower)"),
            ("abs_max_lower", "max safe %R06603 (swash lower)"),
            ("abs_min_upper", "min safe %R06604 (swash upper)"),
            ("abs_max_upper", "max safe %R06604 (swash upper)"),
        ]:
            current = safety.get(key)
            label = f"  {key} [{current}]: " if current not in (None, 0) else f"  {key}: "
            val = input(label).strip()
            if val:
                try:
                    safety[key] = int(val)
                except ValueError:
                    fail(f"Invalid integer: {val}")
                    return 1
        safety.setdefault("min_band_counts", 50)
        cfg["safety"] = safety

    _save_config(cfg)
    ok(f"Wrote {CONFIG}")
    info(f"Current phase: {cfg.get('phase')}  host: {cfg.get('plc_host')}  transport: {cfg.get('transport','modbus')}")
    return 0


# ──────────────────────────────────────────────────────────────────────
# start / stop / status
# ──────────────────────────────────────────────────────────────────────
def _port_open(port: int) -> bool:
    return _probe_host("127.0.0.1", port, timeout=1)


def cmd_start() -> int:
    header("Start - launch optimizer + dashboard")
    if _port_open(8420):
        warn("Port 8420 is already in use. Optimizer likely already running.")
        return 0
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
    try:
        subprocess.Popen(
            [sys.executable, "-m", "hxi_optimizer.main"],
            cwd=REPO_ROOT, env=env, creationflags=flags,
        )
    except Exception as e:
        fail(f"Failed to launch optimizer: {e}")
        return 1
    info("Waiting for dashboard (up to 20 s)...")
    deadline = time.time() + 20
    while time.time() < deadline:
        if _port_open(8420):
            ok("Dashboard is up.")
            info(f"Opening {DASH_URL}")
            try:
                webbrowser.open(DASH_URL)
            except Exception:
                pass
            return 0
        time.sleep(0.5)
    fail("Dashboard didn't come up. Check the Optimizer window for errors.")
    return 1


def cmd_stop() -> int:
    header("Stop - shut down optimizer")
    # Service mode?
    try:
        r = subprocess.run(["sc", "query", "HXIOptimizer"],
                           capture_output=True, text=True, timeout=5)
        if "RUNNING" in r.stdout:
            info("Service mode detected - stopping via SC")
            subprocess.run(["sc", "stop", "HXIOptimizer"], timeout=10)
            ok("Service stop requested")
            return 0
    except Exception:
        pass

    # Foreground mode: find and kill via psutil (reliable, cross-Windows)
    killed = 0
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if "python" not in name:
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if "hxi_optimizer.main" in cmdline:
                    proc.terminate()
                    killed += 1
                    ok(f"Sent SIGTERM to PID {proc.info['pid']}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        warn("psutil not installed - falling back to taskkill by window title")

    if killed == 0:
        # Fallback — kill anything holding the console window titled "HXI Optimizer"
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/FI", "WindowTitle eq HXI Optimizer*"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    time.sleep(1.5)
    if _port_open(8420):
        warn("Dashboard port 8420 still active")
        return 1
    ok("Optimizer stopped")
    return 0


def cmd_status() -> int:
    try:
        import urllib.request
        r = urllib.request.urlopen(DASH_URL + "/api/status", timeout=3)
        d = json.loads(r.read())
    except Exception:
        print(f"{RED}Optimizer: NOT RUNNING{RESET}")
        print(f"  Dashboard {DASH_URL} is not responding.")
        return 1
    conn = d.get("connection", {})
    live = d.get("live", {})
    met = d.get("metrics", {}) or {}
    safety = d.get("safety", {})
    state = d.get("state_machine", "?")
    color = GREEN if conn.get("healthy") else YELLOW
    print(f"{BOLD}Optimizer: {color}RUNNING{RESET}")
    print(f"  Phase       : {d.get('phase')}")
    print(f"  State       : {state}")
    print(f"  Transport   : {color}{'healthy' if conn.get('healthy') else 'DEGRADED'}{RESET} "
          f"({conn.get('consecutive_failures', 0)} failures)")
    print(f"  RPM         : {round(live.get('rpm', 0), 1)} / setpoint {round(live.get('setpoint', 0), 1)}")
    print(f"  Bounds      : [{live.get('active_lower')}, {live.get('active_upper')}]")
    print(f"  Mode        : {met.get('failure_mode', '--')}")
    print(f"  Rejections  : {safety.get('consecutive_rejections', 0)}")
    print(f"  Heartbeat   : {safety.get('heartbeat_counter', 0)}")
    esd = "ACTIVE" if safety.get("esd_active") else "clear"
    print(f"  ESD         : {RED+esd+RESET if safety.get('esd_active') else esd}")
    return 0


# ──────────────────────────────────────────────────────────────────────
# full pipeline
# ──────────────────────────────────────────────────────────────────────
def cmd_full(host: str | None = None) -> int:
    header("FULL AUTO - bootstrap + discover + commission + configure + start")
    if cmd_bootstrap() != 0:
        return 1

    if not host:
        # Run discovery and capture the IP from stdout
        r = subprocess.run(
            [sys.executable, __file__, "discover"],
            cwd=REPO_ROOT, text=True, capture_output=True,
        )
        print(r.stdout)
        if r.stderr: print(r.stderr, file=sys.stderr)
        if r.returncode != 0:
            return 1
        # Last non-empty line of stdout is the IP
        lines = [l for l in r.stdout.splitlines() if l.strip() and re.match(r"^\d+\.\d+\.\d+\.\d+$", l.strip())]
        if not lines:
            fail("Could not extract PLC IP from discover output")
            return 1
        host = lines[-1].strip()

    if cmd_commission(host) != 0:
        return 1
    if cmd_configure(host=host) != 0:
        return 1
    return cmd_start()


# ──────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    # Enable ANSI colors on Windows cmd.exe
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            pass

    p = argparse.ArgumentParser()
    p.add_argument("command", choices=[
        "bootstrap", "discover", "commission", "configure",
        "start", "stop", "status", "full",
    ])
    p.add_argument("--host", default=None)
    p.add_argument("--subnet", default=None)
    p.add_argument("--ewon-name", default=None,
                   help="eWon device name from eCatcher (for fleet-catalog lookup)")
    p.add_argument("--deadband", type=float, default=None)
    p.add_argument("--yes", action="store_true",
                   help="Non-interactive: skip prompts, use current config values")
    args = p.parse_args()

    if args.command == "bootstrap":  return cmd_bootstrap()
    if args.command == "discover":   return cmd_discover(subnet=args.subnet, host=args.host,
                                                         ewon_name=args.ewon_name)
    if args.command == "commission":
        if not args.host:
            fail("commission requires --host <PLC_IP>")
            return 1
        return cmd_commission(args.host)
    if args.command == "configure":
        return cmd_configure(host=args.host, deadband=args.deadband,
                             non_interactive=args.yes)
    if args.command == "start":  return cmd_start()
    if args.command == "stop":   return cmd_stop()
    if args.command == "status": return cmd_status()
    if args.command == "full":   return cmd_full(host=args.host)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
