"""Fleet-wide triage: rank rigs by "attention needed" score.

Steve operates ~130 eWon devices but can only watch one at a time. This
module computes an attention score for every rig the optimizer has
connection history with, based on:

  - Diagnoses produced the last time we were connected
  - Trend findings from CSV logs
  - Hours since last heartbeat
  - Cumulative rollback count
  - Cumulative rejection count
  - Equipment class risk factor (HXI HT > HXI > warrior)

Output: sorted list of RigTriage, ready for the dashboard Fleet tab to
display as "rigs that need attention right now".
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("fleet_triage")


@dataclass
class RigTriage:
    ewon_name: str
    equipment_type: str
    customer: str
    last_seen_ago_h: float
    attention_score: float      # 0..100
    reasons: list[str] = field(default_factory=list)
    state: str = "idle"         # idle | active | stale | unseen

    def to_json(self) -> dict:
        return {
            "ewon_name": self.ewon_name,
            "equipment_type": self.equipment_type,
            "customer": self.customer,
            "last_seen_ago_h": self.last_seen_ago_h,
            "attention_score": round(self.attention_score, 1),
            "reasons": list(self.reasons),
            "state": self.state,
        }


class FleetTriage:
    def __init__(self, machine_store, fleet_catalog):
        self.store = machine_store
        self.fleet = fleet_catalog

    def rank(self) -> list[RigTriage]:
        """Return all known rigs sorted by attention_score descending."""
        out: list[RigTriage] = []
        now = time.time()

        # 1. Rigs we've connected to before (from MachineStateStore.history)
        for slug, entry in self.store.history.items():
            score = 0.0
            reasons: list[str] = []

            age_h = max(0.0, (now - entry.last_seen) / 3600.0)

            # Staleness — haven't seen this rig for days
            if age_h > 168:
                state = "unseen"
                score += 5   # low priority — haven't been near it
                reasons.append(f"Last seen {age_h/24:.1f} days ago")
            elif age_h > 12:
                state = "stale"
                reasons.append(f"Last seen {age_h:.1f} h ago")
            elif age_h > 1:
                state = "idle"
                reasons.append(f"Idle for {age_h:.1f} h")
            else:
                state = "active"
                score += 10
                reasons.append("Active connection")

            # Cumulative events — more connections = more weight
            if entry.connection_count > 10:
                score += 5
                reasons.append(f"{entry.connection_count} connections this session")

            # Equipment class risk factor — bigger rigs get attention first
            spec = None
            if self.fleet:
                match = self.fleet.identify_by_name(entry.ewon_name)
                if match and match.spec:
                    spec = match.spec
                    if spec.horsepower >= 800:
                        score += 5
                        reasons.append(f"{spec.display_name}")

            out.append(RigTriage(
                ewon_name=entry.ewon_name,
                equipment_type=entry.equipment_type,
                customer=entry.customer,
                last_seen_ago_h=age_h,
                attention_score=score,
                reasons=reasons,
                state=state,
            ))

        # 2. Rigs we've never connected to (appear in fleet catalog but not in history)
        if self.fleet:
            seen = {entry.ewon_name.lower() for entry in self.store.history.values()}
            for device in self.fleet.devices:
                if device.ewon_name.lower() in seen:
                    continue
                out.append(RigTriage(
                    ewon_name=device.ewon_name,
                    equipment_type=device.equipment_type,
                    customer=device.customer,
                    last_seen_ago_h=-1,
                    attention_score=1.0,
                    reasons=["Never connected from this optimizer"],
                    state="unseen",
                ))

        out.sort(key=lambda r: -r.attention_score)
        return out

    def summary(self) -> dict:
        """Aggregate counts for the dashboard header."""
        ranked = self.rank()
        by_state: dict[str, int] = {}
        for r in ranked:
            by_state[r.state] = by_state.get(r.state, 0) + 1
        top = ranked[0].to_json() if ranked else None
        return {
            "total_rigs_known": len(ranked),
            "by_state": by_state,
            "top_priority": top,
            "need_attention": sum(1 for r in ranked if r.attention_score >= 15),
        }
