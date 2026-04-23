"""Small helpers called by the .bat wrappers to avoid complex one-liners."""
import json
import os
import socket
import sys
import time
import zipfile
from pathlib import Path


def setup_local_config():
    """Create hxi_config.json pre-populated for local simulator testing."""
    template = Path("hxi_optimizer/hxi_config.template.json")
    target = Path("hxi_optimizer/hxi_config.json")
    if not target.exists():
        cfg = json.load(open(template))
    else:
        cfg = json.load(open(target))
    cfg["plc_host"] = "127.0.0.1"
    cfg["plc_port"] = 5020
    cfg["safety"] = {
        "abs_min_lower": 50, "abs_max_lower": 700,
        "abs_min_upper": 300, "abs_max_upper": 950,
        "min_band_counts": 50,
    }
    cfg["require_verified_word_order"] = False
    cfg["phase"] = "A"
    with open(target, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"Local test config ready at {target}")


def wait_port(host: str, port: int, timeout_s: int = 15) -> bool:
    """Wait until a TCP port accepts connections."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        s = socket.socket()
        s.settimeout(1)
        ok = s.connect_ex((host, port)) == 0
        s.close()
        if ok:
            return True
        time.sleep(0.5)
    return False


def backup_to_zip(out_path: Path) -> int:
    """Zip up logs + state + config. Returns count of files archived."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        src_dir = Path("hxi_optimizer/logs")
        if src_dir.exists():
            for root, _, files in os.walk(src_dir):
                for f in files:
                    p = Path(root) / f
                    z.write(p, p.relative_to("hxi_optimizer"))
                    count += 1
        for extra in [
            "hxi_optimizer/hxi_config.json",
            "state/state.json",
            "state/state.json.bak",
        ]:
            p = Path(extra)
            if p.exists():
                z.write(p, p)
                count += 1
    return count


def set_phase(new_phase: str) -> str:
    """Change 'phase' in hxi_config.json. Returns the applied value."""
    p = Path("hxi_optimizer/hxi_config.json")
    cfg = json.load(open(p))
    cfg["phase"] = new_phase.upper()
    with open(p, "w") as f:
        json.dump(cfg, f, indent=2)
    return cfg["phase"]


def show_status_from_api():
    """Fetch /api/status and pretty-print the important fields."""
    import urllib.request
    try:
        r = urllib.request.urlopen("http://127.0.0.1:8420/api/status", timeout=3)
        d = json.loads(r.read())
    except Exception as e:
        print("Optimizer: NOT RUNNING")
        print(f"  Dashboard port 8420 is not responding ({e})")
        return 1
    print("Optimizer: RUNNING")
    print(f"  Phase       : {d['phase']}")
    print(f"  State       : {d['state_machine']}")
    print(f"  Connection  : {'healthy' if d['connection']['healthy'] else 'DEGRADED'}")
    print(f"  RPM         : {round(d['live']['rpm'], 1)}")
    print(f"  Setpoint    : {round(d['live']['setpoint'], 1)}")
    print(f"  Bounds      : [{d['live']['active_lower']}, {d['live']['active_upper']}]")
    print(f"  Mode        : {d.get('metrics', {}).get('failure_mode', '--')}")
    print(f"  Rejections  : {d['safety']['consecutive_rejections']}")
    print(f"  Heartbeat   : {d['safety']['heartbeat_counter']}")
    print(f"  ESD         : {'ACTIVE' if d['safety']['esd_active'] else 'clear'}")
    print(f"  Temperature : {round(d['live']['loop_temp'], 1)} C")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: _helpers.py <command> [args...]")
        sys.exit(2)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == "setup_local_config":
        setup_local_config()
    elif cmd == "wait_port":
        host = args[0]
        port = int(args[1])
        timeout = int(args[2]) if len(args) > 2 else 15
        sys.exit(0 if wait_port(host, port, timeout) else 1)
    elif cmd == "backup":
        out = Path(args[0])
        n = backup_to_zip(out)
        print(f"Archived {n} files to {out}")
    elif cmd == "set_phase":
        p = set_phase(args[0])
        print(f"Phase changed to {p}")
    elif cmd == "show_status":
        sys.exit(show_status_from_api())
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(2)
