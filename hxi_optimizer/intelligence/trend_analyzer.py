"""Multi-day rolling trend analysis.

Reads CSV logs from hxi_optimizer/logs/drill_*.csv, computes rolling
statistics per channel across hours-to-days timescales, and flags drift
before it becomes a visible fault.

Example findings:
    - "This rig's σ(rpm) has grown from 1.5 → 3.2 over the last 48 h.
       Typical pump-wear signature."
    - "Loop temperature baseline shifted by +6 °C in 72 h. Check cooler."
    - "DNIAE has trended up 0.003/day for the last 5 days; classifier still
       says NORMAL but the system is working harder."

Designed to be called once per hour (or manually from the dashboard).
Does NOT block — runs off a snapshot of files. Safe to call while the
optimizer is writing the CSV.
"""
from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("trend_analyzer")


@dataclass
class TrendFinding:
    channel: str
    finding: str
    slope_per_hour: float
    baseline: float
    current: float
    samples: int
    severity: str = "info"   # info | watch | warn
    window_hours: float = 24.0

    def to_json(self) -> dict:
        return {
            "channel": self.channel,
            "finding": self.finding,
            "slope_per_hour": self.slope_per_hour,
            "baseline": self.baseline,
            "current": self.current,
            "samples": self.samples,
            "severity": self.severity,
            "window_hours": self.window_hours,
        }


class TrendAnalyzer:
    """Reads CSVs, computes long-horizon statistics."""

    def __init__(self, log_dir: Path | str = "hxi_optimizer/logs"):
        self.log_dir = Path(log_dir)

    def _collect_recent(self, hours: float = 48.0,
                         channels: tuple = ("rpm_encoder", "loop_temp",
                                             "delivered_torque")) -> dict:
        """Walk drill_*.csv files and pull the last `hours` of each channel."""
        cutoff = time.time() - hours * 3600.0
        buckets: dict[str, list[tuple[float, float]]] = {c: [] for c in channels}
        files = sorted(self.log_dir.glob("drill_*.csv"),
                       key=lambda p: p.stat().st_mtime)
        for path in files[-12:]:   # last 12 CSVs max (covers many hours)
            # Quick mtime gate — skip old files entirely
            if path.stat().st_mtime < cutoff - 3600:
                continue
            try:
                with open(path, newline="", encoding="utf-8",
                          errors="replace") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        try:
                            ts = float(row.get("timestamp") or 0)
                        except ValueError:
                            continue
                        if ts < cutoff:
                            continue
                        for c in channels:
                            raw = row.get(c)
                            if raw in (None, "", "None"):
                                continue
                            try:
                                buckets[c].append((ts, float(raw)))
                            except ValueError:
                                continue
            except Exception as e:
                logger.debug(f"trend read {path.name}: {e}")
        return buckets

    def analyze(self, hours: float = 48.0) -> list[TrendFinding]:
        """Run the analysis; return findings sorted by severity."""
        buckets = self._collect_recent(hours=hours)
        findings: list[TrendFinding] = []
        now = time.time()

        for channel, samples in buckets.items():
            if len(samples) < 50:
                continue   # too few samples for a reliable trend

            # Split into early / late halves
            samples.sort()
            midpoint = len(samples) // 2
            early = samples[:midpoint]
            late = samples[midpoint:]

            early_mean = sum(v for _, v in early) / len(early)
            late_mean = sum(v for _, v in late) / len(late)
            delta = late_mean - early_mean
            duration_hours = (late[-1][0] - early[0][0]) / 3600.0
            slope = delta / max(duration_hours, 0.01)

            # Compute rough std for reference
            all_vals = [v for _, v in samples]
            mu = sum(all_vals) / len(all_vals)
            var = sum((v - mu) ** 2 for v in all_vals) / len(all_vals)
            sigma = var ** 0.5

            # Channel-specific thresholds
            severity, finding = self._interpret(
                channel, early_mean, late_mean, slope, sigma, duration_hours)

            if severity == "info":
                continue   # don't bother surfacing no-op trends

            findings.append(TrendFinding(
                channel=channel,
                finding=finding,
                slope_per_hour=slope,
                baseline=early_mean,
                current=late_mean,
                samples=len(samples),
                severity=severity,
                window_hours=duration_hours,
            ))

        order = {"warn": 0, "watch": 1, "info": 2}
        findings.sort(key=lambda f: order.get(f.severity, 3))
        return findings

    def _interpret(self, channel: str, baseline: float, current: float,
                   slope_per_hour: float, sigma: float,
                   hours: float) -> tuple[str, str]:
        """Translate a raw trend into a severity + message for Steve."""
        change = current - baseline
        rel = abs(change) / max(abs(baseline), 1e-6)

        if channel == "loop_temp":
            # Temp baselines shouldn't drift > 3 °C in 24 h under normal ops
            if abs(change) > 5.0:
                sev = "warn"
                direction = "risen" if change > 0 else "dropped"
                return (sev, f"Loop temperature {direction} {abs(change):.1f} °C "
                              f"over {hours:.0f} h (baseline {baseline:.1f} → {current:.1f}). "
                              f"Check cooler, ambient, or oil level.")
            if abs(change) > 2.5:
                return ("watch", f"Loop temperature drifting {change:+.1f} °C / "
                                  f"{hours:.0f} h.")

        elif channel == "rpm_encoder":
            # σ growth suggests noise / wear
            if sigma > 4.0:
                return ("warn", f"RPM noise (σ={sigma:.1f}) elevated over "
                                f"{hours:.0f} h. Typical pump-wear or "
                                f"filter-restriction signature.")
            if sigma > 2.5:
                return ("watch", f"RPM noise rising (σ={sigma:.1f}) over {hours:.0f} h.")

        elif channel == "delivered_torque":
            # Big mean shifts warrant attention
            if rel > 0.20:
                direction = "up" if change > 0 else "down"
                return ("warn", f"Delivered torque baseline shifted {direction} "
                                f"{rel*100:.0f}% in {hours:.0f} h "
                                f"({baseline:.0f} → {current:.0f} ft-lbs). "
                                f"Formation change or BHA wear.")
            if rel > 0.10:
                return ("watch", f"Torque trending {change:+.0f} ft-lbs / "
                                  f"{hours:.0f} h.")

        return ("info", f"{channel} stable: {baseline:.2f} → {current:.2f}")
