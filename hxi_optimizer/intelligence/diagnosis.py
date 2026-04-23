"""Diagnosis engine — turns metrics + context into actionable findings.

Inputs  : PerformanceMetrics, MachineRecord, current bounds, drill depth, trend stats
Outputs : list of Diagnosis objects, each with:
            - finding            short factual statement
            - likely_cause       rule/physics-based explanation
            - recommended_action what Steve (or the driller) should do
            - urgency            info | watch | warn | critical
            - confidence         0-1
            - evidence           list of signals that triggered this diagnosis

The engine is a union of small rules, each narrowly focused. Every rule fires
independently and is named, so the dashboard can show *why* a diagnosis was
made — not just the final label.

Rules cover:
    1. Cold-start / connection event
    2. Torsional resonance risk (deep rigs + oscillation in the f1 band)
    3. Windup (persistent saturation + bias)
    4. Temperature amber/red
    5. ML anomaly score elevated but not yet triggered
    6. Machine-specific: recent change event + new machine identified
    7. Dead-band hunting at low amplitude (common on ageing pumps)
    8. Bias + high sat: PID fighting a mechanical obstruction
    9. High RPM near max_rpm (equipment envelope)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


URGENCY_ORDER = {"info": 0, "watch": 1, "warn": 2, "critical": 3}


@dataclass
class Diagnosis:
    code: str                            # machine-readable tag, e.g. "RESONANCE_RISK"
    finding: str                         # one-line fact
    likely_cause: str                    # physics / rule-based explanation
    recommended_action: str              # what to do
    urgency: str = "info"                # info | watch | warn | critical
    confidence: float = 0.5              # 0..1
    evidence: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    def to_json(self) -> dict:
        return {
            "code": self.code,
            "finding": self.finding,
            "likely_cause": self.likely_cause,
            "recommended_action": self.recommended_action,
            "urgency": self.urgency,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "ts": self.ts,
            "urgency_rank": URGENCY_ORDER.get(self.urgency, 0),
        }


class DiagnosisEngine:
    """Fires all rules, returns sorted list by urgency + confidence."""

    # Temperature thresholds from MASTER_CONTEXT §9
    TEMP_AMBER = 60.0
    TEMP_RED = 80.0
    TEMP_SHUTDOWN = 90.0

    def diagnose(self,
                 metrics,                   # PerformanceMetrics
                 machine=None,              # MachineRecord (has .spec)
                 depth_ft: float = 3000.0,
                 oscillation_rate_cpm: Optional[float] = None,
                 recent_change_events: Optional[list] = None,
                 ) -> list[Diagnosis]:
        """Run all rules; return non-empty diagnoses sorted by urgency desc."""
        findings: list[Diagnosis] = []

        # ── Rule 1: cold-start / pipe-connection event ─────────────────
        if metrics.failure_mode == "INSUFFICIENT_DATA":
            findings.append(Diagnosis(
                code="WARMUP",
                finding="Waiting for 20 valid samples before classifying.",
                likely_cause="Just connected or recovering from VPN drop.",
                recommended_action="Let it run for 10 seconds. "
                                    "If this persists, check VPN health.",
                urgency="info", confidence=1.0,
                evidence=[f"n_valid={metrics.n_valid}/40"],
            ))
            return findings  # no point running other rules yet

        # ── Rule 2: torsional resonance risk ───────────────────────────
        if oscillation_rate_cpm and depth_ft > 0:
            # f1 (CPM) = 8840 / (4 * depth_ft) * 60 per MASTER_CONTEXT §4.4
            f1_cpm = 8840.0 / (4.0 * depth_ft) * 60.0
            for harmonic in (1, 3, 5):
                fh = harmonic * f1_cpm
                if abs(oscillation_rate_cpm - fh) / max(fh, 1e-6) < 0.20:
                    findings.append(Diagnosis(
                        code="RESONANCE_RISK",
                        finding=f"Oscillation at {oscillation_rate_cpm:.0f} CPM — "
                                f"within ±20% of harmonic {harmonic}×f₁ = {fh:.0f} CPM.",
                        likely_cause=f"At {depth_ft:.0f} ft the drill string's first "
                                    f"torsional natural frequency is {f1_cpm:.0f} CPM. "
                                    f"Exciting near a harmonic amplifies the motion.",
                        recommended_action="Shift the bump rate away from this band "
                                            "immediately. Slow the slide or reduce bump "
                                            "angle by 15%.",
                        urgency="critical", confidence=0.9,
                        evidence=[f"rate={oscillation_rate_cpm:.0f} CPM",
                                  f"f1={f1_cpm:.0f} CPM",
                                  f"harmonic={harmonic}"],
                    ))
                    break

        # ── Rule 3: windup (saturation + persistent bias) ──────────────
        if metrics.is_windup:
            side = "upper" if metrics.mean_error < 0 else "lower"
            findings.append(Diagnosis(
                code="WINDUP",
                finding=f"PID integral windup against the {side} clamp.",
                likely_cause="Bounds are tight enough that the controller is pegged, "
                            "but the error keeps growing. Typically a mechanical block, "
                            "heavy string, or a too-tight swash clamp.",
                recommended_action="If rig is drilling: widen the bounds by 5%. "
                                    "If rig is stalled: check hydraulic pressure.",
                urgency="warn", confidence=0.85,
                evidence=[f"mean_err={metrics.mean_error:.1f} RPM",
                          f"sat_{side}={getattr(metrics, 'sat_' + side):.2f}"],
            ))

        # ── Rule 4: temperature tiers ──────────────────────────────────
        # We don't get temp from the metrics object directly; it's in the snapshot.
        # Callers should check separately — see digest.py for that path.

        # ── Rule 5: ML anomaly elevated but not triggered ──────────────
        if (metrics.anomaly_threshold > 0
                and metrics.anomaly_score > metrics.anomaly_threshold * 0.7
                and not metrics.anomaly_detected):
            findings.append(Diagnosis(
                code="AE_ELEVATED",
                finding="ML anomaly score is approaching the change-detection threshold.",
                likely_cause="The 7-channel pattern is drifting away from the "
                            "model's notion of 'normal'. Could be formation shift, "
                            "ageing seals, or a new BHA picking up different dynamics.",
                recommended_action="Watch for another 5–10 minutes. If the score "
                                    "crosses the threshold, the system will flag a "
                                    "change event automatically.",
                urgency="watch", confidence=0.6,
                evidence=[f"score={metrics.anomaly_score:.6f}",
                          f"threshold={metrics.anomaly_threshold:.6f}"],
            ))

        # ── Rule 6: ML confirmed change ────────────────────────────────
        if metrics.change_detected:
            findings.append(Diagnosis(
                code="CHANGE_CONFIRMED",
                finding=f"Change-point confirmed ({metrics.change_reason}).",
                likely_cause="Multiple detectors agree something meaningful changed — "
                            "formation, BHA, friction, or operating mode.",
                recommended_action="Check the driller's log for any known events. "
                                    "If no known cause, check hydraulic temperature and "
                                    "standpipe pressure for correlated shifts.",
                urgency="warn", confidence=0.8,
                evidence=[metrics.change_reason],
            ))

        # ── Rule 7: deadband hunting ───────────────────────────────────
        if metrics.failure_mode == "DEADBAND_HUNTING":
            findings.append(Diagnosis(
                code="DEADBAND_HUNTING",
                finding="Small-amplitude oscillation confined to the deadband.",
                likely_cause="Hydraulic deadband is slightly smaller than the "
                            "measurement noise; the PID is chasing its own noise. "
                            "Often appears as pumps age or after a filter change.",
                recommended_action="Increase the deadband by 10-20% in the config. "
                                    "Don't widen the clamp bounds — that won't help.",
                urgency="watch", confidence=metrics.failure_confidence,
                evidence=[f"mode={metrics.failure_mode}",
                          f"rmse={metrics.rmse_rpm:.2f}"],
            ))

        # ── Rule 8: bias + saturation (PID fighting something) ─────────
        if (metrics.failure_mode == "BIAS"
                and metrics.sat_total > 0.3):
            findings.append(Diagnosis(
                code="BIAS_SATURATED",
                finding="Persistent RPM bias while the swash command is saturated.",
                likely_cause="The controller wants to deliver more (or less) flow than "
                            "the current bounds allow. Common after a pipe change or "
                            "when the BHA gets heavier.",
                recommended_action="Widen the affected bound by 3-5%. If this recurs "
                                    "often, recalibrate the gain schedule at this depth.",
                urgency="warn", confidence=metrics.failure_confidence,
                evidence=[f"mean_err={metrics.mean_error:.1f}",
                          f"sat_total={metrics.sat_total:.2f}"],
            ))

        # ── Rule 9: envelope limits — high RPM near max ────────────────
        # (callers pass latest live RPM separately; metrics object has no raw RPM)

        # ── Rule 10: healthy idle ──────────────────────────────────────
        if not findings:
            findings.append(Diagnosis(
                code="HEALTHY",
                finding="Everything looks normal.",
                likely_cause="DNIAE, saturation, and anomaly score all within limits.",
                recommended_action="No action needed. Keep an eye on the dashboard.",
                urgency="info", confidence=0.9,
                evidence=[f"mode={metrics.failure_mode}",
                          f"dniae={metrics.dniae:.3f}"],
            ))

        findings.sort(key=lambda d: (-URGENCY_ORDER[d.urgency], -d.confidence))
        return findings


def diagnose_temperature(loop_temp: float,
                         engine: DiagnosisEngine | None = None) -> Optional[Diagnosis]:
    """Separate function — temp lives on the live snapshot, not PerformanceMetrics."""
    e = engine or DiagnosisEngine()
    if loop_temp >= e.TEMP_SHUTDOWN:
        return Diagnosis(
            code="TEMP_SHUTDOWN",
            finding=f"Hydraulic loop temperature {loop_temp:.1f} °C — shutdown band.",
            likely_cause="Heat exchanger undersized, pump worn, or ambient extreme.",
            recommended_action="Stop drilling. Let the system cool. Check oil "
                                "cooler and aux pump.",
            urgency="critical", confidence=1.0,
            evidence=[f"loop_temp={loop_temp:.1f}"],
        )
    if loop_temp >= e.TEMP_RED:
        return Diagnosis(
            code="TEMP_RED",
            finding=f"Hydraulic loop temperature {loop_temp:.1f} °C — red band.",
            likely_cause="Running hot. Not immediately dangerous but can't sustain.",
            recommended_action="Reduce output demand. If driller won't, prepare to "
                                "freeze adaptation.",
            urgency="warn", confidence=1.0,
            evidence=[f"loop_temp={loop_temp:.1f}"],
        )
    if loop_temp >= e.TEMP_AMBER:
        return Diagnosis(
            code="TEMP_AMBER",
            finding=f"Hydraulic loop temperature {loop_temp:.1f} °C — amber band.",
            likely_cause="Normal for hard drilling. Worth monitoring.",
            recommended_action="Increase monitoring frequency. Check ambient.",
            urgency="watch", confidence=1.0,
            evidence=[f"loop_temp={loop_temp:.1f}"],
        )
    if loop_temp < 20.0:
        return Diagnosis(
            code="TEMP_COLD",
            finding=f"Hydraulic loop temperature {loop_temp:.1f} °C — below warm-up.",
            likely_cause="Cold start. Adaptation should be inhibited until warm.",
            recommended_action="Wait for warm-up (20 °C) before running advisory/writes.",
            urgency="watch", confidence=1.0,
            evidence=[f"loop_temp={loop_temp:.1f}"],
        )
    return None
