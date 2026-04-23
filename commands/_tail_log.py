"""Cross-platform tail -f used by 8_logs.bat."""
import sys
import time
from pathlib import Path

log_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("hxi_optimizer/logs/optimizer.log")
if not log_path.exists():
    print(f"[INFO] Log file not yet created: {log_path}")
    sys.exit(0)

# Print last 20 lines, then follow
with open(log_path, "r", encoding="utf-8", errors="replace") as f:
    lines = f.readlines()
    for line in lines[-20:]:
        sys.stdout.write(line)
    sys.stdout.flush()

    # Follow
    while True:
        line = f.readline()
        if line:
            sys.stdout.write(line)
            sys.stdout.flush()
        else:
            time.sleep(0.25)
