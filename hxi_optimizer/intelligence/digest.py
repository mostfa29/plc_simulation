"""Plain-language summaries for Steve.

Converts raw telemetry + diagnoses + trends into prose he can scan in
30 seconds. Called periodically (hourly default) or on-demand.

Two formats:
  - `summarize_now()` — "right now" snapshot (for the dashboard banner)
  - `daily_digest()`  — 24-hour recap (for email / end-of-shift)

Style rules (from Steve's own reports on the old legacy pipeline):
  - No ML jargon. Say "flagged" instead of "classifier output argmax".
  - Numbers matter. Always include RPM, DNIAE, depth.
  - Lead with what changed, not with what stayed the same.
  - End with what to do.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Digest:
    ts: float = field(default_factory=time.time)
    headline: str = ""
    body: str = ""
    actions: list[str] = field(default_factory=list)
    state: str = "NORMAL"    # NORMAL | WATCH | CAUTION | ATTEND

    def to_json(self) -> dict:
        return {
            "ts": self.ts,
            "headline": self.headline,
            "body": self.body,
            "actions": list(self.actions),
            "state": self.state,
        }


def summarize_now(snapshot: dict, diagnoses: list, trends: list) -> Digest:
    """Build the 'right now' summary card for the dashboard."""
    d = Digest()
    live = snapshot.get("live", {})
    metrics = snapshot.get("metrics", {}) or {}
    bounds = snapshot.get("bounds", {})
    config = snapshot.get("config", {})
    safety = snapshot.get("safety", {})

    # Pick the most urgent diagnosis (already sorted by engine)
    top = diagnoses[0] if diagnoses else None
    if top is None:
        top_code = "HEALTHY"
        top_finding = "Everything looks normal."
        top_urgency = "info"
    else:
        top_code = top.code
        top_finding = top.finding
        top_urgency = top.urgency

    # Headline
    ewon = snapshot.get("machine", {}).get("ewon_name") \
           or config.get("plc_host", "unknown")
    rpm = live.get("rpm", 0) or 0
    sp = live.get("setpoint", 0) or 0
    phase = snapshot.get("phase", "?")
    d.headline = (f"{ewon} — Phase {phase} — "
                   f"RPM {rpm:.1f}/sp {sp:.1f} — {top_code}")

    # Body prose
    lines = []
    lines.append(top_finding)
    lines.append("")
    lines.append(f"Current state: {snapshot.get('state_machine', '?')}. "
                  f"Driller setpoint: {sp:.0f} RPM. "
                  f"Bounds: [{bounds.get('current_lower', 0)}, "
                  f"{bounds.get('current_upper', 0)}].")

    if metrics.get("failure_mode"):
        lines.append(f"Classifier: {metrics['failure_mode']} "
                      f"({int(metrics.get('failure_confidence', 0) * 100)}% conf).")
    if metrics.get("dniae") is not None:
        lines.append(f"Performance: DNIAE = {metrics['dniae']:.3f}. "
                      f"Saturation {metrics.get('sat_total', 0) * 100:.0f}%.")
    if metrics.get("anomaly_threshold", 0) > 0:
        score = metrics.get("anomaly_score", 0)
        thresh = metrics["anomaly_threshold"]
        ratio = score / max(thresh, 1e-9)
        if ratio > 1.0:
            lines.append(f"Anomaly score {score:.5f} is {ratio:.1f}× the normal "
                          f"threshold ({thresh:.5f}).")
        elif ratio > 0.7:
            lines.append(f"Anomaly score elevated: {ratio:.0%} of threshold.")

    if safety.get("consecutive_rejections"):
        lines.append(f"SafetyGate has rejected {safety['consecutive_rejections']} "
                      f"proposed writes in a row — cooldown active for "
                      f"{safety.get('cooldown_remaining', 0):.0f} s.")

    # Trends (only non-info)
    warn_trends = [t for t in trends if t.severity in ("warn", "watch")]
    if warn_trends:
        lines.append("")
        lines.append("Trend observations:")
        for t in warn_trends[:3]:
            lines.append(f"  - {t.finding}")

    d.body = "\n".join(lines)

    # Actions
    seen = set()
    for diag in diagnoses:
        if diag.urgency == "info":
            continue
        if diag.recommended_action in seen:
            continue
        seen.add(diag.recommended_action)
        d.actions.append(diag.recommended_action)

    # State
    if top_urgency == "critical" or any(t.severity == "warn" for t in trends):
        d.state = "ATTEND"
    elif top_urgency == "warn":
        d.state = "CAUTION"
    elif top_urgency == "watch":
        d.state = "WATCH"
    else:
        d.state = "NORMAL"

    return d


def daily_digest(hours_elapsed: float, snapshot_summary: dict,
                  audit_events: list, trend_findings: list) -> Digest:
    """Build the end-of-shift / daily summary from persisted data."""
    d = Digest()
    d.headline = f"Last {hours_elapsed:.0f} h summary"

    lines = []
    total_events = len(audit_events)
    writes = sum(1 for e in audit_events if e.get("event_type") == "WRITE")
    rejects = sum(1 for e in audit_events if e.get("event_type") == "REJECTED")
    rollbacks = sum(1 for e in audit_events if e.get("event_type") == "ROLLBACK")

    lines.append(f"Audit trail: {total_events} events — "
                  f"{writes} writes, {rejects} rejections, "
                  f"{rollbacks} rollbacks.")

    if rollbacks > 5:
        lines.append(f"⚠ Rollback rate is high ({rollbacks} in {hours_elapsed:.0f} h). "
                      f"Review recent audit entries.")

    if trend_findings:
        lines.append("")
        lines.append("Notable trends:")
        for t in trend_findings[:5]:
            lines.append(f"  - {t.finding}")

    if not trend_findings and rollbacks <= 5:
        lines.append("No significant issues in this window.")

    d.body = "\n".join(lines)
    d.state = "ATTEND" if rollbacks > 5 else ("WATCH" if trend_findings else "NORMAL")
    return d
