"""Atomic JSON state persistence — per MASTER_CONTEXT §7.8.

write→fsync→rename. A hard kill mid-write leaves the previous file intact.
The .bak copy provides one-step rollback if the latest file is unreadable.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("persistence")

STATE_FILE = Path("state/state.json")
STATE_BAK = Path("state/state.json.bak")


def save_state(state_dict: dict, path: Path = STATE_FILE) -> bool:
    """Atomic JSON write: tmp → fsync → rename (with .bak rotation)."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump({**state_dict, "_saved_at": time.time()}, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            bak = Path(str(path) + ".bak")
            os.replace(path, bak)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.error(f"State save failed: {e}")
        return False


def load_state(path: Path = STATE_FILE) -> dict:
    """Load state, falling back to .bak. Empty dict on total failure."""
    path = Path(path)
    bak = Path(str(path) + ".bak")
    for p in (path, bak):
        if p.exists():
            try:
                with open(p) as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError(f"Expected dict, got {type(data).__name__}")
                age = time.time() - data.get("_saved_at", 0)
                logger.info(f"Loaded state from {p} (saved {age:.0f}s ago)")
                return data
            except (json.JSONDecodeError, KeyError, OSError, ValueError) as e:
                logger.warning(f"State load failed from {p}: {e}")
    logger.info("No valid state found — starting fresh")
    return {}
