"""Persistent machine-connection state + history.

Tracks:
  - Which machine the optimizer is *currently* connected to
  - Every machine it has EVER connected to (first/last seen, connection count)
  - Machine-change events (detected when a new ewon_name or IP appears)

File: state/machines.json (atomic write, .bak rotation)

Usage:
    from hxi_optimizer.state.machine_state import MachineStateStore

    store = MachineStateStore.load()
    # On new connection:
    event = store.note_connection(record)        # returns "new" | "same" | "changed"
    store.save()
    # To see history:
    for entry in store.history:
        print(entry.ewon_name, entry.last_seen, entry.connection_count)
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("machine_state")

STATE_FILE = Path("state/machines.json")


@dataclass
class MachineHistoryEntry:
    ewon_name: str
    equipment_type: str
    customer: str = ""
    plc_ip: Optional[str] = None
    first_seen: float = 0.0
    last_seen: float = 0.0
    connection_count: int = 0
    total_uptime_s: float = 0.0

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class MachineChangeEvent:
    ts: float
    from_machine: Optional[str]      # ewon_name or None (first connection)
    to_machine: str
    reason: str                      # "startup" | "ip_changed" | "name_changed" | "manual"

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class MachineStateStore:
    """Persistent store of every machine we've connected to."""
    history: dict[str, MachineHistoryEntry] = field(default_factory=dict)
    events: list[MachineChangeEvent] = field(default_factory=list)
    current_ewon_name: Optional[str] = None
    current_plc_ip: Optional[str] = None
    session_start: float = 0.0

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "MachineStateStore":
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"machines.json unreadable: {e} — starting fresh")
            return cls()
        store = cls()
        store.current_ewon_name = data.get("current_ewon_name")
        store.current_plc_ip = data.get("current_plc_ip")
        store.session_start = data.get("session_start", 0.0)
        for k, v in (data.get("history") or {}).items():
            store.history[k] = MachineHistoryEntry(**v)
        for evt in data.get("events") or []:
            store.events.append(MachineChangeEvent(**evt))
        return store

    def save(self, path: Path = STATE_FILE) -> bool:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        bak = Path(str(path) + ".bak")
        payload = {
            "current_ewon_name": self.current_ewon_name,
            "current_plc_ip": self.current_plc_ip,
            "session_start": self.session_start,
            "history": {k: v.to_json() for k, v in self.history.items()},
            "events": [e.to_json() for e in self.events[-200:]],  # cap at 200
            "_saved_at": time.time(),
        }
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if path.exists():
                os.replace(path, bak)
            os.replace(tmp, path)
            return True
        except Exception as e:
            logger.error(f"machines.json save failed: {e}")
            return False

    # ── Connection tracking ─────────────────────────────────────────

    def note_connection(self, record, reason: str = "startup") -> str:
        """Register a new/existing connection. Returns 'new', 'same', or 'changed'."""
        now = time.time()
        ewon = record.ewon_name or "<unnamed>"
        ip = record.plc_ip
        key = ewon.lower()

        # Detect change from previous session
        prev = self.current_ewon_name
        is_change = (prev is not None) and (prev.lower() != key)
        if prev is None:
            status = "new"
        elif is_change:
            status = "changed"
            self.events.append(MachineChangeEvent(
                ts=now, from_machine=prev, to_machine=ewon,
                reason=reason,
            ))
            logger.warning(
                f"Machine change detected: {prev!r} -> {ewon!r} ({reason})"
            )
        else:
            status = "same"

        # Update history
        entry = self.history.get(key)
        if entry is None:
            entry = MachineHistoryEntry(
                ewon_name=ewon,
                equipment_type=record.equipment_type,
                customer=record.customer,
                plc_ip=ip,
                first_seen=now,
                last_seen=now,
                connection_count=0,
            )
            self.history[key] = entry
        # Increment only when this is a fresh connection (not a keep-alive tick)
        if status != "same":
            entry.connection_count += 1
        entry.last_seen = now
        if ip:
            entry.plc_ip = ip
        entry.equipment_type = record.equipment_type
        entry.customer = record.customer

        self.current_ewon_name = ewon
        self.current_plc_ip = ip
        if self.session_start == 0:
            self.session_start = now
        return status

    def tick_uptime(self) -> None:
        """Call periodically to add to the current machine's uptime."""
        if not self.current_ewon_name:
            return
        key = self.current_ewon_name.lower()
        entry = self.history.get(key)
        if entry:
            entry.last_seen = time.time()

    def machine_count(self) -> int:
        return len(self.history)

    def recent_events(self, limit: int = 20) -> list[MachineChangeEvent]:
        return self.events[-limit:]
