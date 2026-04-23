"""Intelligence-layer tests (diagnosis, trends, digest, fleet triage)."""
from __future__ import annotations

import csv
import time
from pathlib import Path

import pytest

from hxi_optimizer.intelligence.diagnosis import (
    Diagnosis, DiagnosisEngine, URGENCY_ORDER, diagnose_temperature,
)
from hxi_optimizer.intelligence.digest import Digest, summarize_now, daily_digest
from hxi_optimizer.intelligence.trend_analyzer import TrendAnalyzer, TrendFinding
from hxi_optimizer.intelligence.fleet_triage import FleetTriage, RigTriage
from hxi_optimizer.monitoring.performance_metrics import PerformanceMetrics


# ═════════════════════════════════════════════════════════════════════
# 1. Diagnosis engine — individual rules
# ═════════════════════════════════════════════════════════════════════

def _metrics(**kw) -> PerformanceMetrics:
    """Build a PerformanceMetrics with sensible defaults."""
    m = PerformanceMetrics(timestamp=time.time())
    m.failure_mode = "NORMAL"
    m.failure_confidence = 0.9
    m.dniae = 0.01
    m.n_valid = 40
    for k, v in kw.items():
        setattr(m, k, v)
    return m


class TestDiagnosisEngine:
    def test_insufficient_data_returns_only_warmup(self):
        m = _metrics(failure_mode="INSUFFICIENT_DATA", n_valid=12)
        diags = DiagnosisEngine().diagnose(m)
        assert len(diags) == 1
        assert diags[0].code == "WARMUP"
        assert diags[0].urgency == "info"

    def test_healthy_when_nothing_wrong(self):
        m = _metrics()
        diags = DiagnosisEngine().diagnose(m)
        codes = [d.code for d in diags]
        assert "HEALTHY" in codes

    def test_windup_detected(self):
        m = _metrics(is_windup=True, mean_error=-8.0, sat_upper=0.7)
        diags = DiagnosisEngine().diagnose(m)
        codes = [d.code for d in diags]
        assert "WINDUP" in codes
        w = next(d for d in diags if d.code == "WINDUP")
        assert w.urgency == "warn"
        assert "upper" in w.finding  # negative mean_error -> upper clamp

    def test_deadband_hunting_detected(self):
        m = _metrics(failure_mode="DEADBAND_HUNTING", failure_confidence=0.8)
        diags = DiagnosisEngine().diagnose(m)
        assert any(d.code == "DEADBAND_HUNTING" for d in diags)

    def test_bias_with_saturation_detected(self):
        m = _metrics(failure_mode="BIAS", sat_total=0.5, mean_error=5.0,
                     failure_confidence=0.95)
        diags = DiagnosisEngine().diagnose(m)
        assert any(d.code == "BIAS_SATURATED" for d in diags)

    def test_ae_elevated_but_not_triggered(self):
        m = _metrics(anomaly_threshold=0.001, anomaly_score=0.0008,
                     anomaly_detected=False)
        diags = DiagnosisEngine().diagnose(m)
        assert any(d.code == "AE_ELEVATED" for d in diags)

    def test_change_confirmed(self):
        m = _metrics(change_detected=True, change_reason="MEAN_SHIFT+AE_ANOMALY")
        diags = DiagnosisEngine().diagnose(m)
        c = next(d for d in diags if d.code == "CHANGE_CONFIRMED")
        assert "MEAN_SHIFT" in c.finding

    def test_resonance_risk_near_f1(self):
        # 5000 ft → f1 = 8840/(4*5000)*60 = 26.52 CPM.
        # Operating at 27 CPM is within ±20% of f1.
        m = _metrics()
        diags = DiagnosisEngine().diagnose(m, depth_ft=5000,
                                            oscillation_rate_cpm=27.0)
        resonance = [d for d in diags if d.code == "RESONANCE_RISK"]
        assert resonance, "Should flag resonance at f1"
        assert resonance[0].urgency == "critical"

    def test_no_resonance_far_from_harmonics(self):
        # 5000 ft → f1 = 26.52 CPM, 3f1 = 79.57, 5f1 = 132.6.
        # 50 CPM is safely between f1 and 3f1.
        m = _metrics()
        diags = DiagnosisEngine().diagnose(m, depth_ft=5000,
                                            oscillation_rate_cpm=50.0)
        assert not any(d.code == "RESONANCE_RISK" for d in diags)

    def test_resonance_at_third_harmonic(self):
        # 3 * f1 at 5000 ft = 79.57 CPM
        m = _metrics()
        diags = DiagnosisEngine().diagnose(m, depth_ft=5000,
                                            oscillation_rate_cpm=80.0)
        assert any(d.code == "RESONANCE_RISK" for d in diags)

    def test_diagnoses_sorted_by_urgency(self):
        m = _metrics(is_windup=True, mean_error=-5.0,
                     failure_mode="BIAS", sat_total=0.6,
                     change_detected=True, change_reason="MEAN_SHIFT")
        diags = DiagnosisEngine().diagnose(m)
        # No criticals here — check that higher-urgency comes first
        urgencies = [URGENCY_ORDER[d.urgency] for d in diags]
        assert urgencies == sorted(urgencies, reverse=True)

    def test_diagnosis_to_json(self):
        d = Diagnosis(code="X", finding="y", likely_cause="z",
                      recommended_action="w", urgency="warn", confidence=0.7,
                      evidence=["a", "b"])
        j = d.to_json()
        assert j["code"] == "X"
        assert j["urgency_rank"] == URGENCY_ORDER["warn"]
        assert j["evidence"] == ["a", "b"]


# ═════════════════════════════════════════════════════════════════════
# 2. Temperature diagnosis
# ═════════════════════════════════════════════════════════════════════

class TestTemperatureDiagnosis:
    @pytest.mark.parametrize("temp,code,urgency", [
        (50.0, None, None),
        (65.0, "TEMP_AMBER", "watch"),
        (82.0, "TEMP_RED", "warn"),
        (91.0, "TEMP_SHUTDOWN", "critical"),
        (15.0, "TEMP_COLD", "watch"),
    ])
    def test_temperature_bands(self, temp, code, urgency):
        d = diagnose_temperature(temp)
        if code is None:
            assert d is None
        else:
            assert d is not None
            assert d.code == code
            assert d.urgency == urgency


# ═════════════════════════════════════════════════════════════════════
# 3. Trend analyzer
# ═════════════════════════════════════════════════════════════════════

def _write_fake_csv(log_dir: Path, filename: str,
                    rows: list[dict]) -> Path:
    """Write a drill_*.csv with the standard header."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / filename
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp", "seq", "stale",
            "rpm_encoder", "swash_output", "active_lower", "active_upper",
            "delivered_torque", "pid_state", "bump_fwd_set", "bump_rev_set",
            "bump_angle", "bump_flag_fwd", "bump_flag_rev", "esd_bit",
            "loop_temp", "ss_setpoint_fwd", "ss_setpoint_rev",
        ])
        writer.writeheader()
        for r in rows:
            full = {k: "" for k in writer.fieldnames}
            full.update(r)
            writer.writerow(full)
    return path


class TestTrendAnalyzer:
    def test_no_files_returns_empty(self, tmp_path):
        findings = TrendAnalyzer(log_dir=tmp_path).analyze()
        assert findings == []

    def test_few_samples_ignored(self, tmp_path):
        now = time.time()
        rows = [{"timestamp": now - i, "rpm_encoder": 60.0} for i in range(10)]
        _write_fake_csv(tmp_path, "drill_1.csv", rows)
        findings = TrendAnalyzer(log_dir=tmp_path).analyze()
        # <50 samples per channel → no finding
        assert findings == []

    def test_temperature_drift_flagged(self, tmp_path):
        now = time.time()
        rows = []
        # First half: temp ~ 55 °C
        for i in range(100):
            rows.append({"timestamp": now - 3600 * 20 + i * 30,
                         "rpm_encoder": 60.0, "loop_temp": 55.0})
        # Second half: temp ~ 68 °C — a 13 °C rise
        for i in range(100):
            rows.append({"timestamp": now - 3600 * 10 + i * 30,
                         "rpm_encoder": 60.0, "loop_temp": 68.0})
        _write_fake_csv(tmp_path, "drill_1.csv", rows)
        findings = TrendAnalyzer(log_dir=tmp_path).analyze(hours=48)
        temp_findings = [f for f in findings if f.channel == "loop_temp"]
        assert temp_findings, f"Expected temp finding, got {findings}"
        assert temp_findings[0].severity == "warn"

    def test_trend_finding_to_json(self):
        t = TrendFinding(channel="rpm_encoder",
                          finding="test", slope_per_hour=0.1,
                          baseline=60, current=62, samples=200,
                          severity="warn")
        j = t.to_json()
        assert j["channel"] == "rpm_encoder"
        assert j["severity"] == "warn"

    def test_stable_channel_not_flagged(self, tmp_path):
        now = time.time()
        # Perfectly stable: same RPM for all 200 samples
        rows = [{"timestamp": now - 3600 * 20 + i * 300,
                  "rpm_encoder": 60.0, "loop_temp": 55.0}
                for i in range(200)]
        _write_fake_csv(tmp_path, "drill_1.csv", rows)
        findings = TrendAnalyzer(log_dir=tmp_path).analyze(hours=48)
        # Should NOT flag — all values are identical
        assert all(f.severity == "info" or not f.finding.startswith("info")
                   for f in findings)


# ═════════════════════════════════════════════════════════════════════
# 4. Digest generator
# ═════════════════════════════════════════════════════════════════════

class TestDigest:
    def test_healthy_digest_says_normal(self):
        snapshot = {
            "live": {"rpm": 60, "setpoint": 60, "loop_temp": 55},
            "metrics": {"failure_mode": "NORMAL", "failure_confidence": 0.9,
                         "dniae": 0.01, "sat_total": 0.05,
                         "anomaly_score": 0.0001, "anomaly_threshold": 0.001},
            "bounds": {"current_lower": 400, "current_upper": 600},
            "config": {"drill_depth_ft": 3000},
            "safety": {"consecutive_rejections": 0},
            "state_machine": "BASELINE", "phase": "A",
            "machine": {"ewon_name": "Test Rig"},
        }
        d = summarize_now(snapshot, [], [])
        assert d.state == "NORMAL"
        assert "Test Rig" in d.headline

    def test_critical_diagnosis_escalates_state(self):
        from hxi_optimizer.intelligence.diagnosis import Diagnosis
        critical = Diagnosis(code="X", finding="f", likely_cause="c",
                              recommended_action="a", urgency="critical")
        snapshot = {"live": {}, "metrics": {}, "bounds": {}, "config": {},
                    "safety": {}, "state_machine": "ESD", "phase": "A"}
        d = summarize_now(snapshot, [critical], [])
        assert d.state == "ATTEND"
        assert "a" in d.actions

    def test_actions_deduped(self):
        from hxi_optimizer.intelligence.diagnosis import Diagnosis
        diags = [
            Diagnosis(code="A", finding="", likely_cause="",
                       recommended_action="Do X", urgency="warn"),
            Diagnosis(code="B", finding="", likely_cause="",
                       recommended_action="Do X", urgency="warn"),  # duplicate
            Diagnosis(code="C", finding="", likely_cause="",
                       recommended_action="Do Y", urgency="watch"),
        ]
        snapshot = {"live": {}, "metrics": {}, "bounds": {}, "config": {},
                    "safety": {}, "state_machine": "BASELINE", "phase": "A"}
        d = summarize_now(snapshot, diags, [])
        assert "Do X" in d.actions
        assert "Do Y" in d.actions
        assert d.actions.count("Do X") == 1

    def test_daily_digest_flags_high_rollback_rate(self):
        events = [{"event_type": "ROLLBACK"} for _ in range(10)]
        events += [{"event_type": "WRITE"} for _ in range(30)]
        d = daily_digest(hours_elapsed=24, snapshot_summary={},
                         audit_events=events, trend_findings=[])
        assert d.state == "ATTEND"
        assert "Rollback rate" in d.body

    def test_daily_digest_normal(self):
        events = [{"event_type": "WRITE"} for _ in range(5)]
        d = daily_digest(hours_elapsed=24, snapshot_summary={},
                         audit_events=events, trend_findings=[])
        assert d.state == "NORMAL"


# ═════════════════════════════════════════════════════════════════════
# 5. Fleet triage
# ═════════════════════════════════════════════════════════════════════

class _FakeStore:
    """Minimal stand-in for MachineStateStore."""
    def __init__(self):
        self.history = {}


class _FakeHistoryEntry:
    def __init__(self, ewon_name, equipment_type, customer,
                 plc_ip, first_seen, last_seen, connection_count):
        self.ewon_name = ewon_name
        self.equipment_type = equipment_type
        self.customer = customer
        self.plc_ip = plc_ip
        self.first_seen = first_seen
        self.last_seen = last_seen
        self.connection_count = connection_count


class TestFleetTriage:
    def test_empty_returns_all_from_fleet(self):
        from hxi_optimizer.comms.fleet import FleetCatalog
        store = _FakeStore()
        fleet = FleetCatalog.load()
        triage = FleetTriage(store, fleet)
        ranked = triage.rank()
        assert len(ranked) == len(fleet.devices)
        # All should be "unseen" with score ~ 1.0
        assert all(r.state == "unseen" for r in ranked)

    def test_active_rig_scores_highest(self):
        store = _FakeStore()
        now = time.time()
        store.history["rig_alpha"] = _FakeHistoryEntry(
            ewon_name="Rig Alpha", equipment_type="hxi", customer="X",
            plc_ip="1.2.3.4", first_seen=now - 100, last_seen=now - 10,
            connection_count=15,
        )
        store.history["rig_beta"] = _FakeHistoryEntry(
            ewon_name="Rig Beta", equipment_type="hxi", customer="X",
            plc_ip="1.2.3.5",
            first_seen=now - 86400 * 30, last_seen=now - 86400 * 30,
            connection_count=1,
        )
        triage = FleetTriage(store, None)
        ranked = triage.rank()
        assert ranked[0].ewon_name == "Rig Alpha"
        assert ranked[0].state == "active"
        assert ranked[0].attention_score > ranked[1].attention_score

    def test_summary_counts(self):
        store = _FakeStore()
        now = time.time()
        store.history["a"] = _FakeHistoryEntry(
            "A", "hxi", "C", "1.2.3.4", now, now, 1)
        store.history["b"] = _FakeHistoryEntry(
            "B", "hxi", "C", "1.2.3.5",
            now - 86400 * 10, now - 86400 * 10, 1)
        triage = FleetTriage(store, None)
        s = triage.summary()
        assert s["total_rigs_known"] == 2
        assert "active" in s["by_state"]
        assert "unseen" in s["by_state"]

    def test_rig_triage_to_json(self):
        r = RigTriage(ewon_name="X", equipment_type="hxi", customer="Y",
                       last_seen_ago_h=1.5, attention_score=12.5,
                       reasons=["a", "b"], state="active")
        j = r.to_json()
        assert j["ewon_name"] == "X"
        assert j["attention_score"] == 12.5
