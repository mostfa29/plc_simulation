#!/usr/bin/env python3
"""
Physics-Based PLC Register Auto-Mapper
========================================
Uses equipment-specific physical constraints (max torque, max RPM, gear ratio)
and cross-register validation (power equation, torque ordering) to identify
what each PLC register represents with high confidence.

Each machine type has different physical specs — this mapper adapts bounds
automatically based on the EquipmentSpec for the given equipment type.

Usage:
    # Re-analyze existing scan data (no PLC needed)
    python physics_mapper.py --scan-file t.md --equipment-type hxi_ht

    # Live scan
    python physics_mapper.py --ip 129.168.1.25 --equipment-type hxi_ht

    # With profile YAML (auto-detects equipment_type)
    python physics_mapper.py --ip 129.168.1.25 --profile profiles/rig707.yaml

    # Generate output YAML + report
    python physics_mapper.py --scan-file t.md --equipment-type hxi_ht \\
        --output profiles/new_rig.yaml --report rig_physics.md
"""
import argparse
import math
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import (EquipmentSpec, EquipmentType, EQUIPMENT_SPECS,
                    MachineProfile, RegisterDef)

try:
    from discover_machine import ModbusTCPClient, decode_ge_float32, is_sane_float32
except ImportError:
    ModbusTCPClient = None  # scan-file mode doesn't need live connection


# ═══════════════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class RegisterObservation:
    """Raw observation data for a single register address."""
    address: int
    raw_values: List[int] = field(default_factory=list)   # uint16 per snapshot
    timestamps: List[float] = field(default_factory=list)

    # Derived (computed by analyze())
    is_changing: bool = False
    raw_min: int = 0
    raw_max: int = 0
    raw_range: int = 0
    int16_value: int = 0          # latest, signed

    # FLOAT32 decoded (set if adjacent register exists)
    float32_ge: Optional[float] = None
    float32_std: Optional[float] = None
    float32_ge_values: List[float] = field(default_factory=list)
    float32_std_values: List[float] = field(default_factory=list)

    # Temporal analysis
    temporal_pattern: str = "unknown"  # static/oscillating/monotonic_up/drift/step/noisy
    change_rate: float = 0.0
    variance: float = 0.0


@dataclass
class CandidateAssignment:
    """A scored proposal: register at `address` represents `quantity`."""
    address: int
    quantity: str
    data_type: str                 # FLOAT32, INT16, INT32
    byte_order: str                # ge_swap or standard
    confidence: float              # 0.0 to 1.0
    decoded_value: float           # current engineering value
    reasoning: List[str] = field(default_factory=list)

    # Sub-scores
    range_score: float = 0.0
    temporal_score: float = 0.0
    dtype_score: float = 0.0


@dataclass
class RegisterBlock:
    """Contiguous group of related registers."""
    start_addr: int
    end_addr: int
    addresses: List[int] = field(default_factory=list)
    num_float32_pairs: int = 0
    all_changing: bool = False
    description: str = ""


@dataclass
class AssignmentResult:
    """Complete register map assignment with confidence scores."""
    equipment_type: str
    equipment_spec: EquipmentSpec
    assignments: Dict[str, CandidateAssignment] = field(default_factory=dict)
    unassigned_quantities: List[str] = field(default_factory=list)
    unassigned_registers: List[RegisterObservation] = field(default_factory=list)
    blocks: List[RegisterBlock] = field(default_factory=list)
    cross_validation_score: float = 0.0
    cross_validation_details: List[str] = field(default_factory=list)
    word_swap_detected: str = "ge_swap"
    template_matches: Dict[str, float] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
# Float Decoding Helpers
# ═══════════════════════════════════════════════════════════════════════

def _decode_float32_ge(low_word: int, high_word: int) -> float:
    """GE word-swapped FLOAT32: low word at N, high word at N+1."""
    raw = struct.pack('>HH', high_word, low_word)
    return struct.unpack('>f', raw)[0]


def _decode_float32_std(word_n: int, word_n1: int) -> float:
    """Standard IEEE FLOAT32: high word at N, low word at N+1."""
    raw = struct.pack('>HH', word_n, word_n1)
    return struct.unpack('>f', raw)[0]


def _is_sane(f: float) -> bool:
    """Check if float is in a physically reasonable range."""
    if math.isnan(f) or math.isinf(f):
        return False
    return -1e6 < f < 1e6


def _int16_signed(val: int) -> int:
    """Convert uint16 to signed int16."""
    return val - 65536 if val >= 32768 else val


# ═══════════════════════════════════════════════════════════════════════
# Temporal Pattern Classifier
# ═══════════════════════════════════════════════════════════════════════

def classify_temporal(values: List[float], timestamps: List[float] = None) -> str:
    """Classify temporal behavior from a time-series of values.

    Returns one of: static, oscillating, monotonic_up, monotonic_down,
                    drift, step, noisy
    """
    if not values or len(values) < 2:
        return "static"

    n = len(values)
    mean = sum(values) / n
    if mean == 0:
        mean = 1e-10  # avoid div by zero

    variance = sum((v - mean) ** 2 for v in values) / n
    std = math.sqrt(variance)
    cv = abs(std / mean) if mean != 0 else 0  # coefficient of variation

    # Static: negligible variation
    if std < 0.5 and cv < 0.001:
        return "static"

    # Count derivative sign changes
    diffs = [values[i + 1] - values[i] for i in range(n - 1)]
    sign_changes = 0
    for i in range(len(diffs) - 1):
        if diffs[i] * diffs[i + 1] < 0:
            sign_changes += 1

    # Monotonic up: all diffs >= 0 (with small tolerance)
    tol = abs(mean) * 0.001 + 0.5
    if all(d >= -tol for d in diffs) and any(d > tol for d in diffs):
        return "monotonic_up"

    # Monotonic down: all diffs <= 0
    if all(d <= tol for d in diffs) and any(d < -tol for d in diffs):
        return "monotonic_down"

    # Step: 1-2 large jumps, otherwise flat
    large_jumps = sum(1 for d in diffs if abs(d) > std * 2) if std > 0 else 0
    if large_jumps <= 2 and cv < 0.1:
        return "step"

    # Drift: slow change, low relative variance
    if cv < 0.05 and sign_changes <= 2:
        return "drift"

    # Oscillating: multiple sign changes in derivative
    if sign_changes >= 2 and cv > 0.01:
        return "oscillating"

    return "noisy"


# ═══════════════════════════════════════════════════════════════════════
# Word Swap Detector
# ═══════════════════════════════════════════════════════════════════════

def detect_word_swap(observations: Dict[int, RegisterObservation],
                     spec: EquipmentSpec) -> str:
    """Detect whether PLC uses GE word-swap or standard IEEE FLOAT32.

    Tests all adjacent register pairs against equipment-specific physical
    bounds. The byte order producing more valid values wins.
    """
    ge_sane = 0
    std_sane = 0
    addrs = sorted(observations.keys())

    # Physical bounds for this equipment
    max_val = max(spec.max_intermittent_torque_ftlbs, 500_000)  # hookload can be large

    for addr in addrs:
        next_addr = addr + 1
        if next_addr not in observations:
            continue
        if addr % 2 != 0 and (addr - 1) in observations:
            continue  # skip odd addresses if even exists (avoid double-counting)

        low = observations[addr].raw_values[-1]
        high = observations[next_addr].raw_values[-1]

        # GE decode
        try:
            ge_val = _decode_float32_ge(low, high)
            if _is_sane(ge_val) and -100 < ge_val < max_val:
                ge_sane += 1
        except (struct.error, OverflowError):
            pass

        # Standard decode
        try:
            std_val = _decode_float32_std(low, high)
            if _is_sane(std_val) and -100 < std_val < max_val:
                std_sane += 1
        except (struct.error, OverflowError):
            pass

    # Use spec.word_swap as prior (tie-breaker)
    if ge_sane > std_sane:
        return "ge_swap"
    elif std_sane > ge_sane:
        return "standard"
    else:
        return "ge_swap" if spec.word_swap else "standard"


# ═══════════════════════════════════════════════════════════════════════
# Block Pattern Detection
# ═══════════════════════════════════════════════════════════════════════

def detect_blocks(observations: Dict[int, RegisterObservation]) -> List[RegisterBlock]:
    """Detect contiguous groups of related registers."""
    addrs = sorted(observations.keys())
    if not addrs:
        return []

    # Cluster into runs (gap > 3 = new block)
    clusters: List[List[int]] = [[addrs[0]]]
    for a in addrs[1:]:
        if a - clusters[-1][-1] <= 3:
            clusters[-1].append(a)
        else:
            clusters.append([a])

    blocks = []
    for cluster in clusters:
        if len(cluster) < 3:
            continue  # skip tiny clusters

        # Count FLOAT32 pairs in this cluster
        n_float_pairs = 0
        n_changing = 0
        for i in range(0, len(cluster) - 1, 2):
            addr = cluster[i]
            if addr + 1 in observations:
                obs = observations[addr]
                if obs.float32_ge is not None and _is_sane(obs.float32_ge):
                    n_float_pairs += 1
            if addr in observations and observations[addr].is_changing:
                n_changing += 1

        blocks.append(RegisterBlock(
            start_addr=cluster[0],
            end_addr=cluster[-1],
            addresses=cluster,
            num_float32_pairs=n_float_pairs,
            all_changing=n_changing >= len(cluster) * 0.5,
            description=f"R{cluster[0]}-R{cluster[-1]} ({len(cluster)} regs, "
                        f"{n_float_pairs} float pairs, "
                        f"{'LIVE' if n_changing >= len(cluster) * 0.5 else 'mixed'})"
        ))

    return blocks


# ═══════════════════════════════════════════════════════════════════════
# Quantity Classifier Functions
# ═══════════════════════════════════════════════════════════════════════

def _score_torque(obs: RegisterObservation, spec: EquipmentSpec,
                  word_swap: str) -> Optional[CandidateAssignment]:
    """Score a register as torque (ft-lbs)."""
    f_val = obs.float32_ge if word_swap == "ge_swap" else obs.float32_std
    if f_val is None or not _is_sane(f_val):
        return None
    v = abs(f_val)

    # Equipment-specific bounds
    max_t = spec.max_intermittent_torque_ftlbs * 1.1
    if v > max_t or v < 0:
        return None

    reasons = []

    # Range score: prefer values in realistic operating range
    typical = spec.max_torque_ftlbs * 0.5
    sigma = spec.max_torque_ftlbs * 0.4
    range_score = math.exp(-0.5 * ((v - typical) / sigma) ** 2) if sigma > 0 else 0
    if v > 500:
        range_score = min(range_score * 1.3, 1.0)
        reasons.append(f"value {v:.0f} in torque range for {spec.max_torque_ftlbs:.0f} max")
    elif v > 100:
        range_score *= 0.7
        reasons.append(f"value {v:.0f} low for torque (max={spec.max_torque_ftlbs:.0f})")
    else:
        return None  # too low to be torque

    # Temporal: torque should be oscillating or varying during active operation
    temporal_score = {
        "oscillating": 0.95, "noisy": 0.7, "drift": 0.5,
        "monotonic_up": 0.4, "step": 0.3, "static": 0.15,
    }.get(obs.temporal_pattern, 0.1)
    if obs.is_changing:
        reasons.append(f"changing ({obs.temporal_pattern})")
    else:
        reasons.append("static — could be setpoint instead")

    # Data type score
    dtype_score = 1.0  # FLOAT32 is expected
    reasons.append("FLOAT32")

    conf = range_score * 0.5 + temporal_score * 0.3 + dtype_score * 0.2
    return CandidateAssignment(
        address=obs.address, quantity="torque", data_type="FLOAT32",
        byte_order=word_swap, confidence=min(conf, 0.99),
        decoded_value=f_val, reasoning=reasons,
        range_score=range_score, temporal_score=temporal_score, dtype_score=dtype_score,
    )


def _score_rpm(obs: RegisterObservation, spec: EquipmentSpec,
               word_swap: str) -> Optional[CandidateAssignment]:
    """Score a register as RPM."""
    f_val = obs.float32_ge if word_swap == "ge_swap" else obs.float32_std
    if f_val is None or not _is_sane(f_val):
        return None
    v = abs(f_val)

    # Equipment-specific bounds — this is the key differentiator
    max_r = spec.max_rpm * 1.2
    if v > max_r:
        return None

    reasons = []

    # Range score
    if v > 0.5:
        typical = spec.max_rpm * 0.4
        sigma = spec.max_rpm * 0.35
        range_score = math.exp(-0.5 * ((v - typical) / sigma) ** 2) if sigma > 0 else 0
        range_score = min(range_score * 1.2, 1.0)
        reasons.append(f"value {v:.1f} in RPM range (max={spec.max_rpm:.0f})")
    else:
        range_score = 0.2
        reasons.append(f"value {v:.1f} near zero — may be idle")

    # Gear ratio cross-check
    motor_rpm = v * spec.gear_ratio
    if motor_rpm <= 2700:
        range_score = min(range_score * 1.1, 1.0)
        reasons.append(f"motor RPM={motor_rpm:.0f} valid (gear={spec.gear_ratio})")
    else:
        range_score *= 0.3
        reasons.append(f"motor RPM={motor_rpm:.0f} too high!")

    # Temporal
    temporal_score = {
        "oscillating": 0.95, "noisy": 0.7, "drift": 0.5,
        "static": 0.2, "step": 0.3, "monotonic_up": 0.3,
    }.get(obs.temporal_pattern, 0.1)
    if obs.is_changing:
        reasons.append(f"changing ({obs.temporal_pattern})")

    dtype_score = 1.0
    conf = range_score * 0.5 + temporal_score * 0.3 + dtype_score * 0.2
    return CandidateAssignment(
        address=obs.address, quantity="rpm", data_type="FLOAT32",
        byte_order=word_swap, confidence=min(conf, 0.99),
        decoded_value=f_val, reasoning=reasons,
        range_score=range_score, temporal_score=temporal_score, dtype_score=dtype_score,
    )


def _score_temperature(obs: RegisterObservation, spec: EquipmentSpec,
                       word_swap: str) -> Optional[CandidateAssignment]:
    """Score a register as temperature (degF)."""
    f_val = obs.float32_ge if word_swap == "ge_swap" else obs.float32_std
    if f_val is None or not _is_sane(f_val):
        return None
    v = f_val

    # Temperature bounds: -40 to 400°F
    if v < -50 or v > 450:
        return None

    reasons = []

    # Range score: centered on typical operating temp
    if 70 < v < 250:
        range_score = 0.9
        reasons.append(f"value {v:.1f}°F in operating range")
    elif 32 < v <= 70:
        range_score = 0.6
        reasons.append(f"value {v:.1f}°F — cold startup?")
    elif -40 <= v <= 32:
        range_score = 0.3
        reasons.append(f"value {v:.1f}°F — very cold or offset")
    elif 250 < v <= 400:
        range_score = 0.5
        reasons.append(f"value {v:.1f}°F — high but possible")
    else:
        range_score = 0.1

    # Key differentiator: temperature drifts SLOWLY
    temporal_score = {
        "drift": 0.95, "static": 0.7, "noisy": 0.4,
        "oscillating": 0.2,  # temp doesn't oscillate fast
        "step": 0.1, "monotonic_up": 0.6, "monotonic_down": 0.6,
    }.get(obs.temporal_pattern, 0.1)
    if obs.temporal_pattern == "drift":
        reasons.append("slow drift — characteristic of temperature")
    elif obs.temporal_pattern in ("static", "monotonic_up"):
        reasons.append(f"{obs.temporal_pattern} — plausible for temp")

    dtype_score = 1.0
    conf = range_score * 0.5 + temporal_score * 0.3 + dtype_score * 0.2
    return CandidateAssignment(
        address=obs.address, quantity="temperature", data_type="FLOAT32",
        byte_order=word_swap, confidence=min(conf, 0.99),
        decoded_value=f_val, reasoning=reasons,
        range_score=range_score, temporal_score=temporal_score, dtype_score=dtype_score,
    )


def _score_pressure(obs: RegisterObservation, spec: EquipmentSpec,
                    word_swap: str, profile: Optional[MachineProfile]
                    ) -> Optional[CandidateAssignment]:
    """Score a register as pressure (PSI)."""
    f_val = obs.float32_ge if word_swap == "ge_swap" else obs.float32_std
    if f_val is None or not _is_sane(f_val):
        return None
    v = abs(f_val)

    max_p = spec.max_system_pressure_psi if spec.max_system_pressure_psi > 0 else 5000.0
    if profile and profile.max_pressure_psi > 0:
        max_p = profile.max_pressure_psi
    if v > max_p * 1.2 or v < 10:
        return None

    reasons = []

    # Range score
    if 100 < v < max_p:
        range_score = 0.7
        reasons.append(f"value {v:.0f} PSI in range (max={max_p:.0f})")
    else:
        range_score = 0.3
        reasons.append(f"value {v:.0f} PSI — borderline")

    # Temporal
    temporal_score = {
        "oscillating": 0.9, "noisy": 0.7, "drift": 0.5,
        "static": 0.2, "monotonic_up": 0.4,
    }.get(obs.temporal_pattern, 0.1)
    if obs.is_changing:
        reasons.append(f"changing ({obs.temporal_pattern})")

    dtype_score = 1.0
    conf = range_score * 0.5 + temporal_score * 0.3 + dtype_score * 0.2
    return CandidateAssignment(
        address=obs.address, quantity="pressure", data_type="FLOAT32",
        byte_order=word_swap, confidence=min(conf, 0.99),
        decoded_value=f_val, reasoning=reasons,
        range_score=range_score, temporal_score=temporal_score, dtype_score=dtype_score,
    )


def _score_turns(obs: RegisterObservation, spec: EquipmentSpec,
                 word_swap: str) -> Optional[CandidateAssignment]:
    """Score a register as turns count."""
    f_val = obs.float32_ge if word_swap == "ge_swap" else obs.float32_std
    if f_val is None or not _is_sane(f_val):
        return None
    v = f_val

    # Turns: 0-30 typical
    if v < -0.1 or v > 50:
        return None

    reasons = []

    # Range score
    if 0 <= v <= 30:
        range_score = 0.8
        reasons.append(f"value {v:.3f} turns in typical range")
    else:
        range_score = 0.3

    # Key: turns should be monotonic up during makeup
    temporal_score = {
        "monotonic_up": 0.95, "drift": 0.6, "static": 0.3,
        "oscillating": 0.1,  # turns don't oscillate
        "noisy": 0.2, "step": 0.2,
    }.get(obs.temporal_pattern, 0.1)
    if obs.temporal_pattern == "monotonic_up":
        reasons.append("monotonic increasing — characteristic of turns")
    elif obs.is_changing:
        reasons.append(f"changing ({obs.temporal_pattern})")

    dtype_score = 1.0
    conf = range_score * 0.5 + temporal_score * 0.3 + dtype_score * 0.2
    return CandidateAssignment(
        address=obs.address, quantity="turns", data_type="FLOAT32",
        byte_order=word_swap, confidence=min(conf, 0.99),
        decoded_value=f_val, reasoning=reasons,
        range_score=range_score, temporal_score=temporal_score, dtype_score=dtype_score,
    )


def _score_hookload(obs: RegisterObservation, spec: EquipmentSpec,
                    word_swap: str) -> Optional[CandidateAssignment]:
    """Score a register as hookload (lbs)."""
    f_val = obs.float32_ge if word_swap == "ge_swap" else obs.float32_std
    if f_val is None or not _is_sane(f_val):
        return None
    v = abs(f_val)

    # Hookload: 0 to capacity (in lbs, 1 ton = 2000 lbs)
    max_lbs = spec.hookload_capacity_tons * 2000 * 1.2
    if v > max_lbs or v < 100:
        return None

    reasons = []

    # Range score
    if 1_000 < v < 300_000:
        range_score = 0.7
        reasons.append(f"value {v:.0f} lbs ({v / 1000:.1f} klbs) — reasonable hookload")
    elif v >= 300_000:
        range_score = 0.4
        reasons.append(f"value {v:.0f} lbs — heavy string")
    else:
        range_score = 0.3

    # Temporal
    temporal_score = {
        "oscillating": 0.8, "noisy": 0.6, "drift": 0.7,
        "static": 0.4, "step": 0.3,
    }.get(obs.temporal_pattern, 0.2)
    if obs.is_changing:
        reasons.append(f"changing ({obs.temporal_pattern})")

    dtype_score = 1.0
    conf = range_score * 0.5 + temporal_score * 0.3 + dtype_score * 0.2
    return CandidateAssignment(
        address=obs.address, quantity="hookload", data_type="FLOAT32",
        byte_order=word_swap, confidence=min(conf, 0.99),
        decoded_value=f_val, reasoning=reasons,
        range_score=range_score, temporal_score=temporal_score, dtype_score=dtype_score,
    )


def _score_encoder(obs: RegisterObservation, spec: EquipmentSpec
                   ) -> Optional[CandidateAssignment]:
    """Score a register pair as encoder counts (INT32)."""
    raw = obs.raw_values[-1] if obs.raw_values else 0

    reasons = []

    if not obs.is_changing:
        return None

    # Encoder: compute temporal from RAW values (not float32 which is garbage for INT32)
    if len(obs.raw_values) > 1:
        raw_temporal = classify_temporal([float(v) for v in obs.raw_values])
    else:
        raw_temporal = obs.temporal_pattern

    # Encoder should be monotonically incrementing
    temporal_score = {
        "monotonic_up": 0.95, "noisy": 0.4, "oscillating": 0.3,
        "drift": 0.5, "static": 0.0, "step": 0.2,
    }.get(raw_temporal, 0.1)
    if raw_temporal in ("static",):
        return None
    reasons.append(f"raw pattern: {raw_temporal}")

    # Range score: encoder LSW can be any uint16
    range_score = 0.5
    if obs.raw_range > 5:
        range_score = 0.7
        reasons.append(f"LSW changing (range={obs.raw_range})")
    reasons.append(f"raw uint16={raw}")

    dtype_score = 0.8
    reasons.append("INT32 (register pair)")

    conf = range_score * 0.5 + temporal_score * 0.3 + dtype_score * 0.2
    return CandidateAssignment(
        address=obs.address, quantity="encoder_counts", data_type="INT32",
        byte_order="n/a", confidence=min(conf, 0.99),
        decoded_value=float(raw), reasoning=reasons,
        range_score=range_score, temporal_score=temporal_score, dtype_score=dtype_score,
    )


def _score_connection_state(obs: RegisterObservation, spec: EquipmentSpec
                            ) -> Optional[CandidateAssignment]:
    """Score a register as connection state (INT16 enum 0-13)."""
    i16 = _int16_signed(obs.raw_values[-1]) if obs.raw_values else -1
    if i16 < 0 or i16 > 13:
        return None

    reasons = []

    # Range score: must be 0-13
    range_score = 0.8
    state_names = {0: "IDLE", 1: "STAB", 2: "SPIN_IN", 3: "SHOULDER",
                   4: "POWER_TIGHT", 5: "HOLD", 9: "COMPLETE"}
    name = state_names.get(i16, f"state_{i16}")
    reasons.append(f"value {i16} ({name}) — valid state enum")

    # Temporal: state changes in steps, usually static within snapshot window
    temporal_score = {
        "static": 0.9, "step": 0.8, "drift": 0.1,
        "oscillating": 0.05, "noisy": 0.05,
    }.get(obs.temporal_pattern, 0.1)
    if obs.temporal_pattern in ("static", "step"):
        reasons.append(f"{obs.temporal_pattern} — expected for state enum")

    dtype_score = 1.0  # INT16 is correct
    reasons.append("INT16")

    conf = range_score * 0.5 + temporal_score * 0.3 + dtype_score * 0.2
    return CandidateAssignment(
        address=obs.address, quantity="connection_state", data_type="INT16",
        byte_order="n/a", confidence=min(conf, 0.99),
        decoded_value=float(i16), reasoning=reasons,
        range_score=range_score, temporal_score=temporal_score, dtype_score=dtype_score,
    )


def _score_target_torque(obs: RegisterObservation, spec: EquipmentSpec
                         ) -> Optional[CandidateAssignment]:
    """Score a register as target torque (INT16, ft-lbs)."""
    i16 = _int16_signed(obs.raw_values[-1]) if obs.raw_values else 0
    if i16 < 1000 or i16 > spec.max_torque_ftlbs:
        return None
    if obs.is_changing:
        return None  # target torque should be static during a connection

    reasons = []

    # Range score
    typical = spec.max_torque_ftlbs * 0.6
    sigma = spec.max_torque_ftlbs * 0.3
    range_score = math.exp(-0.5 * ((i16 - typical) / sigma) ** 2) if sigma > 0 else 0
    range_score = min(range_score * 1.2, 1.0)
    reasons.append(f"value {i16} ft-lbs (max={spec.max_torque_ftlbs:.0f})")

    # Must be static
    temporal_score = 0.9 if obs.temporal_pattern == "static" else 0.2
    reasons.append("static — expected for setpoint")

    dtype_score = 1.0
    reasons.append("INT16")

    conf = range_score * 0.5 + temporal_score * 0.3 + dtype_score * 0.2
    return CandidateAssignment(
        address=obs.address, quantity="target_torque", data_type="INT16",
        byte_order="n/a", confidence=min(conf, 0.99),
        decoded_value=float(i16), reasoning=reasons,
        range_score=range_score, temporal_score=temporal_score, dtype_score=dtype_score,
    )


def _score_shoulder_torque(obs: RegisterObservation, spec: EquipmentSpec
                           ) -> Optional[CandidateAssignment]:
    """Score a register as shoulder torque (INT16, ft-lbs)."""
    i16 = _int16_signed(obs.raw_values[-1]) if obs.raw_values else 0
    if i16 < 500 or i16 > spec.max_torque_ftlbs:
        return None
    if obs.is_changing:
        return None

    reasons = []

    typical = spec.max_torque_ftlbs * 0.45
    sigma = spec.max_torque_ftlbs * 0.3
    range_score = math.exp(-0.5 * ((i16 - typical) / sigma) ** 2) if sigma > 0 else 0
    range_score = min(range_score * 1.1, 1.0)
    reasons.append(f"value {i16} ft-lbs (< target expected)")

    temporal_score = 0.9 if obs.temporal_pattern == "static" else 0.2
    reasons.append("static — expected for threshold")

    dtype_score = 1.0
    reasons.append("INT16")

    conf = range_score * 0.5 + temporal_score * 0.3 + dtype_score * 0.2
    return CandidateAssignment(
        address=obs.address, quantity="shoulder_torque", data_type="INT16",
        byte_order="n/a", confidence=min(conf, 0.99),
        decoded_value=float(i16), reasoning=reasons,
        range_score=range_score, temporal_score=temporal_score, dtype_score=dtype_score,
    )


def _score_connection_count(obs: RegisterObservation, spec: EquipmentSpec
                            ) -> Optional[CandidateAssignment]:
    """Score a register as connection count (INT16)."""
    i16 = _int16_signed(obs.raw_values[-1]) if obs.raw_values else -1
    if i16 < 0 or i16 > 10000:
        return None

    reasons = []

    # Range: small positive integer, typically 0-500
    if 0 < i16 < 500:
        range_score = 0.7
        reasons.append(f"value {i16} — reasonable connection count")
    elif i16 == 0:
        range_score = 0.3
        reasons.append("value 0 — no connections yet?")
    else:
        range_score = 0.4
        reasons.append(f"value {i16} — high count")

    # Should be static within a snapshot window
    temporal_score = {
        "static": 0.9, "step": 0.5, "monotonic_up": 0.5,
    }.get(obs.temporal_pattern, 0.1)
    reasons.append(f"{obs.temporal_pattern}")

    dtype_score = 1.0
    reasons.append("INT16")

    conf = range_score * 0.5 + temporal_score * 0.3 + dtype_score * 0.2
    return CandidateAssignment(
        address=obs.address, quantity="connection_count", data_type="INT16",
        byte_order="n/a", confidence=min(conf, 0.99),
        decoded_value=float(i16), reasoning=reasons,
        range_score=range_score, temporal_score=temporal_score, dtype_score=dtype_score,
    )


# ═══════════════════════════════════════════════════════════════════════
# Score All Quantities
# ═══════════════════════════════════════════════════════════════════════

QUANTITY_SCORERS = {
    "torque": _score_torque,
    "rpm": _score_rpm,
    "temperature": _score_temperature,
    "turns": _score_turns,
    "hookload": _score_hookload,
}

INT_QUANTITY_SCORERS = {
    "connection_state": _score_connection_state,
    "target_torque": _score_target_torque,
    "shoulder_torque": _score_shoulder_torque,
    "connection_count": _score_connection_count,
}


def _get_template_prior(equipment_type: str) -> Dict[str, int]:
    """Get known register addresses for this equipment family as prior.

    Returns {quantity: expected_address} for the equipment's template.
    """
    hxi_family = {"hxi", "hxi_ht", "hxi_smart_slide", "hxi_ss",
                  "hxi_standard", "hxi_relay"}
    shop_types = {"shop_unit"}

    if equipment_type in hxi_family:
        return {
            "torque": 163, "turns": 165, "temperature": 167, "rpm": 169,
            "connection_state": 175, "target_torque": 178,
            "shoulder_torque": 180, "connection_count": 185,
            "hookload": 189, "encoder_counts": 2076,
        }
    elif equipment_type in shop_types:
        return {
            "torque": 6000, "rpm": 6002, "pressure": 6004, "temperature": 6006,
            "encoder_counts": 6008, "target_torque": 6016, "turns": 6018,
            "connection_state": 6021, "hookload": 6024, "shoulder_torque": 6026,
            "connection_count": 6030,
        }
    return {}


TEMPLATE_BONUS = 0.25  # confidence bonus for matching a known template address
PROCESS_BLOCK_BONUS = 0.10  # bonus for being in a contiguous process data block


def score_all(observations: Dict[int, RegisterObservation],
              spec: EquipmentSpec, word_swap: str,
              equipment_type: str = "unknown",
              profile: Optional[MachineProfile] = None
              ) -> Dict[str, List[CandidateAssignment]]:
    """Score every register against every quantity. Returns candidates per quantity."""
    candidates: Dict[str, List[CandidateAssignment]] = {q: [] for q in
        list(QUANTITY_SCORERS) + list(INT_QUANTITY_SCORERS) + ["encoder_counts", "pressure"]}

    # Get template prior for this equipment family
    template_prior = _get_template_prior(equipment_type)

    # Detect process data blocks for context bonus
    blocks = detect_blocks(observations)
    process_block_addrs = set()
    for b in blocks:
        if b.all_changing and b.num_float32_pairs >= 2:
            process_block_addrs.update(b.addresses)

    for addr, obs in observations.items():
        # FLOAT32 quantities (need adjacent register)
        if obs.float32_ge is not None or obs.float32_std is not None:
            for qty, scorer in QUANTITY_SCORERS.items():
                result = scorer(obs, spec, word_swap)
                if result and result.confidence > 0.05:
                    # Template prior bonus
                    if template_prior.get(qty) == addr:
                        result.confidence = min(result.confidence + TEMPLATE_BONUS, 0.99)
                        result.reasoning.insert(0, f"TEMPLATE MATCH (R{addr})")
                    # Process block bonus
                    if addr in process_block_addrs:
                        result.confidence = min(result.confidence + PROCESS_BLOCK_BONUS, 0.99)
                        result.reasoning.append("in process data block")
                    candidates[qty].append(result)

            # Pressure (needs profile for max_pressure)
            result = _score_pressure(obs, spec, word_swap, profile)
            if result and result.confidence > 0.05:
                if template_prior.get("pressure") == addr:
                    result.confidence = min(result.confidence + TEMPLATE_BONUS, 0.99)
                    result.reasoning.insert(0, f"TEMPLATE MATCH (R{addr})")
                if addr in process_block_addrs:
                    result.confidence = min(result.confidence + PROCESS_BLOCK_BONUS, 0.99)
                candidates["pressure"].append(result)

        # INT16 quantities
        for qty, scorer in INT_QUANTITY_SCORERS.items():
            result = scorer(obs, spec)
            if result and result.confidence > 0.05:
                if template_prior.get(qty) == addr:
                    result.confidence = min(result.confidence + TEMPLATE_BONUS, 0.99)
                    result.reasoning.insert(0, f"TEMPLATE MATCH (R{addr})")
                candidates[qty].append(result)

        # Encoder (INT32 — special case)
        result = _score_encoder(obs, spec)
        if result and result.confidence > 0.05:
            if template_prior.get("encoder_counts") == addr:
                result.confidence = min(result.confidence + TEMPLATE_BONUS, 0.99)
                result.reasoning.insert(0, f"TEMPLATE MATCH (R{addr})")
            candidates["encoder_counts"].append(result)

    # Sort each quantity's candidates by confidence descending
    for qty in candidates:
        candidates[qty].sort(key=lambda c: c.confidence, reverse=True)

    return candidates


# ═══════════════════════════════════════════════════════════════════════
# Assignment Solver (Greedy + Swap-Improve)
# ═══════════════════════════════════════════════════════════════════════

def solve_assignment(candidates: Dict[str, List[CandidateAssignment]],
                     min_confidence: float = 0.15
                     ) -> Dict[str, CandidateAssignment]:
    """Find optimal unique assignment of registers to quantities.

    Greedy: assign highest-confidence candidates first.
    Swap-improve: check if swapping any two assignments improves total.
    """
    assigned: Dict[str, CandidateAssignment] = {}
    used_addrs: set = set()

    # Build flat list of (confidence, quantity, candidate)
    all_cands = []
    for qty, cand_list in candidates.items():
        for c in cand_list:
            if c.confidence >= min_confidence:
                all_cands.append((c.confidence, qty, c))
    all_cands.sort(key=lambda x: x[0], reverse=True)

    # Greedy assignment
    for conf, qty, cand in all_cands:
        if qty in assigned:
            continue
        # Check if register address is already used
        consumed = {cand.address}
        if cand.data_type in ("FLOAT32", "INT32"):
            consumed.add(cand.address + 1)
        if consumed & used_addrs:
            continue

        assigned[qty] = cand
        used_addrs |= consumed

    # Swap-improve (max 3 passes)
    for _ in range(3):
        improved = False
        qtys = list(assigned.keys())
        for i in range(len(qtys)):
            for j in range(i + 1, len(qtys)):
                qi, qj = qtys[i], qtys[j]
                ci, cj = assigned[qi], assigned[qj]

                # Find best candidate for qi at qj's address and vice versa
                alt_i = None
                alt_j = None
                for c in candidates.get(qi, []):
                    if c.address == cj.address and c.confidence >= min_confidence:
                        alt_i = c
                        break
                for c in candidates.get(qj, []):
                    if c.address == ci.address and c.confidence >= min_confidence:
                        alt_j = c
                        break

                if alt_i and alt_j:
                    old_score = ci.confidence + cj.confidence
                    new_score = alt_i.confidence + alt_j.confidence
                    if new_score > old_score + 0.01:
                        assigned[qi] = alt_i
                        assigned[qj] = alt_j
                        improved = True
        if not improved:
            break

    return assigned


# ═══════════════════════════════════════════════════════════════════════
# Cross-Validation Engine
# ═══════════════════════════════════════════════════════════════════════

def cross_validate(assignments: Dict[str, CandidateAssignment],
                   spec: EquipmentSpec,
                   profile: Optional[MachineProfile] = None
                   ) -> Tuple[float, List[str]]:
    """Apply physics constraints to validate and adjust confidence.

    Returns (overall_score, details_list).
    """
    details = []
    checks_passed = 0
    checks_total = 0

    # 1. Power equation: T × RPM / 5252 ≤ rated_hp × 1.5
    if "torque" in assignments and "rpm" in assignments:
        checks_total += 1
        T = assignments["torque"].decoded_value
        N = assignments["rpm"].decoded_value
        P = abs(T * N / 5252.0) if T * N != 0 else 0
        P_limit = spec.rated_hp * 1.5
        if P <= P_limit:
            checks_passed += 1
            details.append(f"[PASS] Power: {T:.0f} x {N:.1f} / 5252 = {P:.0f} HP "
                           f"<= {P_limit:.0f} HP limit")
            assignments["torque"].confidence = min(assignments["torque"].confidence + 0.10, 0.99)
            assignments["rpm"].confidence = min(assignments["rpm"].confidence + 0.10, 0.99)
        else:
            details.append(f"[FAIL] Power: {T:.0f} x {N:.1f} / 5252 = {P:.0f} HP "
                           f"> {P_limit:.0f} HP limit!")
            assignments["torque"].confidence = max(assignments["torque"].confidence - 0.15, 0.01)
            assignments["rpm"].confidence = max(assignments["rpm"].confidence - 0.15, 0.01)

    # 2. Gear ratio: RPM × gear_ratio ≤ 2700 (motor RPM limit)
    if "rpm" in assignments:
        checks_total += 1
        N = assignments["rpm"].decoded_value
        motor_rpm = abs(N * spec.gear_ratio)
        if motor_rpm <= 2700:
            checks_passed += 1
            details.append(f"[PASS] Gear: {N:.1f} x {spec.gear_ratio} = "
                           f"{motor_rpm:.0f} motor RPM <= 2700")
            assignments["rpm"].confidence = min(assignments["rpm"].confidence + 0.05, 0.99)
        else:
            details.append(f"[FAIL] Gear: {N:.1f} x {spec.gear_ratio} = "
                           f"{motor_rpm:.0f} motor RPM > 2700!")
            assignments["rpm"].confidence = max(assignments["rpm"].confidence - 0.20, 0.01)

    # 3. Pressure-torque relationship (if both found)
    if "torque" in assignments and "pressure" in assignments and profile:
        checks_total += 1
        T = assignments["torque"].decoded_value
        P = assignments["pressure"].decoded_value
        D_in3 = profile.motor_displacement_cc * 0.0610237
        if D_in3 > 0 and P > 0:
            T_predicted = P * D_in3 / (2 * math.pi) * 0.92 * spec.gear_ratio
            ratio = T / T_predicted if T_predicted > 0 else float('inf')
            if 0.3 < ratio < 3.0:
                checks_passed += 1
                details.append(f"[PASS] Pressure-Torque: T={T:.0f}, predicted={T_predicted:.0f} "
                               f"(ratio={ratio:.2f})")
                assignments["torque"].confidence = min(assignments["torque"].confidence + 0.10, 0.99)
                assignments["pressure"].confidence = min(assignments["pressure"].confidence + 0.10, 0.99)
            else:
                details.append(f"[WARN] Pressure-Torque: T={T:.0f}, predicted={T_predicted:.0f} "
                               f"(ratio={ratio:.2f}) — may be AC drive (no P-T correlation)")

    # 4. Torque ordering: shoulder < target < max_torque
    if "shoulder_torque" in assignments and "target_torque" in assignments:
        checks_total += 1
        shoulder = assignments["shoulder_torque"].decoded_value
        target = assignments["target_torque"].decoded_value
        if shoulder < target:
            checks_passed += 1
            details.append(f"[PASS] Order: shoulder({shoulder:.0f}) < "
                           f"target({target:.0f}) < max({spec.max_torque_ftlbs:.0f})")
            assignments["shoulder_torque"].confidence = min(
                assignments["shoulder_torque"].confidence + 0.10, 0.99)
            assignments["target_torque"].confidence = min(
                assignments["target_torque"].confidence + 0.10, 0.99)
        else:
            details.append(f"[WARN] Order: shoulder({shoulder:.0f}) >= "
                           f"target({target:.0f}) — swapping assignment")
            # Swap them
            assignments["shoulder_torque"], assignments["target_torque"] = \
                assignments["target_torque"], assignments["shoulder_torque"]
            assignments["shoulder_torque"].quantity = "shoulder_torque"
            assignments["target_torque"].quantity = "target_torque"

    # 5. Temperature reasonableness
    if "temperature" in assignments:
        checks_total += 1
        temp = assignments["temperature"].decoded_value
        if 70 < temp < 250:
            checks_passed += 1
            details.append(f"[PASS] Temp: {temp:.1f}°F in operating range [70-250]")
            assignments["temperature"].confidence = min(
                assignments["temperature"].confidence + 0.05, 0.99)
        elif 32 < temp <= 70 or 250 <= temp < 350:
            checks_passed += 1
            details.append(f"[INFO] Temp: {temp:.1f}°F — outside normal but plausible")
        else:
            details.append(f"[WARN] Temp: {temp:.1f}°F — unusual")

    # 6. Connection state validity
    if "connection_state" in assignments:
        checks_total += 1
        state = int(assignments["connection_state"].decoded_value)
        if 0 <= state <= 13:
            checks_passed += 1
            state_names = {0: "IDLE", 1: "STAB", 2: "SPIN_IN", 3: "SHOULDER",
                           4: "POWER_TIGHT", 5: "HOLD", 9: "COMPLETE"}
            name = state_names.get(state, f"state_{state}")
            details.append(f"[PASS] State: {state} is valid ConnectionState ({name})")
            assignments["connection_state"].confidence = min(
                assignments["connection_state"].confidence + 0.10, 0.99)
        else:
            details.append(f"[FAIL] State: {state} — invalid state enum!")
            assignments["connection_state"].confidence = max(
                assignments["connection_state"].confidence - 0.30, 0.01)

    # 7. Encoder-turns consistency
    if "encoder_counts" in assignments:
        checks_total += 1
        enc = assignments["encoder_counts"].decoded_value
        cpr = profile.encoder_cpr if profile else 1174
        revs = enc / cpr if cpr > 0 else 0
        checks_passed += 1
        details.append(f"[INFO] Encoder: {enc:.0f} / {cpr} CPR = {revs:.1f} revolutions")

    score = checks_passed / checks_total if checks_total > 0 else 0.0
    details.append(f"Overall: {checks_passed}/{checks_total} passed, "
                   f"cross-validation score = {score:.2f}")
    return score, details


# ═══════════════════════════════════════════════════════════════════════
# Template Comparison
# ═══════════════════════════════════════════════════════════════════════

# Known register templates
TEMPLATES = {
    "HXI (R163+)": {
        "torque": 163, "turns": 165, "temperature": 167, "rpm": 169,
        "connection_state": 175, "target_torque": 178, "shoulder_torque": 180,
        "connection_count": 185, "hookload": 189, "encoder_counts": 2076,
    },
    "Shop Unit (R6000+)": {
        "torque": 6000, "rpm": 6002, "pressure": 6004, "temperature": 6006,
        "encoder_counts": 6008, "target_torque": 6016, "turns": 6018,
        "connection_state": 6021, "hookload": 6024, "shoulder_torque": 6026,
        "connection_count": 6030,
    },
}


def compare_templates(assignments: Dict[str, CandidateAssignment]
                      ) -> Dict[str, float]:
    """Compare proposed assignments against known templates."""
    matches = {}
    for tname, tmap in TEMPLATES.items():
        matched = 0
        total = len(tmap)
        for qty, expected_addr in tmap.items():
            if qty in assignments and assignments[qty].address == expected_addr:
                matched += 1
        matches[tname] = matched / total if total > 0 else 0.0
    return matches


# ═══════════════════════════════════════════════════════════════════════
# Scan File Parser (parse analyze_registers.py output)
# ═══════════════════════════════════════════════════════════════════════

def parse_scan_file(path: str) -> Dict[int, RegisterObservation]:
    """Parse output from analyze_registers.py to extract register data.

    Handles two sections:
    1. Register table:  R{addr}  {raw}  {int16}  0x{hex}  [C]/[S]  {range}  {ge_f32}  {std_f32}
    2. Raw snapshot table at the bottom for multi-pass values.
    """
    observations: Dict[int, RegisterObservation] = {}
    text = Path(path).read_text(encoding='utf-8', errors='replace')
    lines = text.split('\n')

    # Phase 1: Parse the register analysis table
    # Pattern:  R{addr}  {raw}  {int16}  0x{hex}  [C]/[S]  {range}  {ge}  {std}
    reg_pattern = re.compile(
        r'R(\d+)\s+'
        r'(\d+)\s+'           # raw uint16
        r'(-?\d+)\s+'         # signed int16
        r'0x[0-9A-Fa-f]+\s+'  # hex
        r'\[([CS])\]\s+'       # changing/static
        r'(\d+)\s+'            # range
        r'([-\d.]+|---)\s+'    # GE float32
        r'([-\d.]+|---)'       # STD float32
    )

    for line in lines:
        m = reg_pattern.search(line)
        if not m:
            continue
        addr = int(m.group(1))
        raw = int(m.group(2))
        i16 = int(m.group(3))
        is_changing = m.group(4) == 'C'
        change_range = int(m.group(5))

        ge_str = m.group(6)
        std_str = m.group(7)
        ge_f32 = float(ge_str) if ge_str != '---' else None
        std_f32 = float(std_str) if std_str != '---' else None

        obs = RegisterObservation(
            address=addr,
            raw_values=[raw],
            is_changing=is_changing,
            raw_min=raw - change_range // 2,
            raw_max=raw + change_range // 2,
            raw_range=change_range,
            int16_value=i16,
            float32_ge=ge_f32,
            float32_std=std_f32,
        )
        observations[addr] = obs

    # Phase 2: Parse raw snapshot section to get multi-pass values
    # Format:  R{addr}  {v1}  {v2}  {v3}  {v4}  {v5}  {delta}  [<-- CHANGING]
    snap_pattern = re.compile(r'R(\d+)\s+([\d\s]+?)(?:\s+<--|$)')
    in_snapshot_section = False

    for line in lines:
        if 'RAW SNAPSHOT' in line.upper() or 'SNAPSHOT DUMP' in line.upper():
            in_snapshot_section = True
            continue
        if not in_snapshot_section:
            continue
        # Skip separator lines
        if line.strip().startswith('─') or line.strip().startswith('='):
            continue
        if 'Addr' in line and 'Pass' in line:
            continue

        m = re.match(r'\s*R(\d+)\s+(.*)', line)
        if not m:
            continue

        addr = int(m.group(1))
        rest = m.group(2)
        # Extract numbers, stop at "<-- CHANGING" marker
        parts = re.split(r'\s+', rest.split('<--')[0].strip())
        raw_vals = []
        for p in parts:
            try:
                raw_vals.append(int(p))
            except ValueError:
                continue

        # Last value is the delta column — separate it
        if len(raw_vals) >= 2:
            pass_vals = raw_vals[:-1]  # all except last (delta)
            if addr in observations and pass_vals:
                observations[addr].raw_values = pass_vals

    # Phase 3: Compute temporal patterns and FLOAT32 values from multi-pass data
    addrs = sorted(observations.keys())
    for addr in addrs:
        obs = observations[addr]

        # Generate fake timestamps if we only have 1 value
        if len(obs.raw_values) > 1:
            obs.timestamps = [i * 2.0 for i in range(len(obs.raw_values))]
        else:
            obs.timestamps = [0.0]

        # Compute FLOAT32 values from all snapshots if adjacent register exists
        next_addr = addr + 1
        if next_addr in observations and len(obs.raw_values) > 0:
            next_obs = observations[next_addr]
            n = min(len(obs.raw_values), len(next_obs.raw_values))
            ge_vals = []
            std_vals = []
            for i in range(n):
                try:
                    ge = _decode_float32_ge(obs.raw_values[i], next_obs.raw_values[i])
                    if _is_sane(ge):
                        ge_vals.append(ge)
                except (struct.error, OverflowError):
                    pass
                try:
                    std = _decode_float32_std(obs.raw_values[i], next_obs.raw_values[i])
                    if _is_sane(std):
                        std_vals.append(std)
                except (struct.error, OverflowError):
                    pass
            obs.float32_ge_values = ge_vals
            obs.float32_std_values = std_vals
            if ge_vals:
                obs.float32_ge = ge_vals[-1]
            if std_vals:
                obs.float32_std = std_vals[-1]

        # Classify temporal pattern
        # Use float32 values if they're sane (not denormalized), else raw
        use_float = False
        if obs.float32_ge_values and len(obs.float32_ge_values) > 1:
            # Check if values are meaningful (not all denormalized/zero)
            max_abs = max(abs(v) for v in obs.float32_ge_values)
            if max_abs > 1e-10:
                use_float = True

        if use_float:
            obs.temporal_pattern = classify_temporal(obs.float32_ge_values, obs.timestamps)
        elif len(obs.raw_values) > 1:
            float_vals = [float(v) for v in obs.raw_values]
            obs.temporal_pattern = classify_temporal(float_vals, obs.timestamps)
        else:
            obs.temporal_pattern = "static" if not obs.is_changing else "noisy"

        # Variance
        if obs.raw_values:
            mean = sum(obs.raw_values) / len(obs.raw_values)
            obs.variance = sum((v - mean) ** 2 for v in obs.raw_values) / len(obs.raw_values)

    return observations


# ═══════════════════════════════════════════════════════════════════════
# Live Scanner
# ═══════════════════════════════════════════════════════════════════════

def scan_live(host: str, port: int = 502, unit_id: int = 0,
              passes: int = 8, delay: float = 2.0
              ) -> Dict[int, RegisterObservation]:
    """Scan a live PLC and collect register observations.

    Raises ConnectionError if PLC is unreachable.
    Raises RuntimeError if discover_machine.py is not available.
    """
    if ModbusTCPClient is None:
        raise RuntimeError("discover_machine.py not found — cannot do live scan. "
                           "Use --scan-file to analyze existing scan data instead.")

    client = ModbusTCPClient(host, port, timeout=5.0, unit_id=unit_id)
    if not client.connect():
        raise ConnectionError(f"Cannot connect to {host}:{port}")

    print(f"  Connected to {host}:{port} (unit_id={unit_id})")

    # Scan ranges (same as analyze_registers.py)
    scan_ranges = [
        (0, 500, "Main PLC registers"),
        (500, 1000, "Extended registers"),
        (1000, 1500, "Extended I/O"),
        (2000, 2200, "Encoder / counter area"),
        (3400, 3600, "RTC / diagnostics"),
        (5000, 5600, "eWON tag registers"),
        (6000, 6100, "Shop unit layout"),
    ]

    # Phase 1: Find all non-zero registers
    print("\n  Phase 1: Full register scan...")
    all_nonzero: Dict[int, int] = {}
    for start, end, desc in scan_ranges:
        print(f"  Scanning R{start}-R{end} ({desc})...", end="", flush=True)
        addr = start
        count_found = 0
        while addr < end:
            chunk = min(50, end - addr)
            regs = client.read_holding_registers(addr, chunk)
            if regs:
                for i, val in enumerate(regs):
                    if val != 0:
                        all_nonzero[addr + i] = val
                        count_found += 1
            addr += chunk
            time.sleep(0.05)
        print(f" {count_found} non-zero")

    print(f"  Total: {len(all_nonzero)} non-zero registers")

    # Phase 2: Take snapshots of non-zero registers
    watch_addrs = set()
    for a in all_nonzero:
        watch_addrs.update(range(max(0, a - 1), a + 2))
    sorted_addrs = sorted(watch_addrs)

    # Cluster into blocks
    clusters: List[List[int]] = [[sorted_addrs[0]]]
    for a in sorted_addrs[1:]:
        if a - clusters[-1][-1] > 10:
            clusters.append([a])
        else:
            clusters[-1].append(a)
    blocks = [(min(c), max(c) - min(c) + 1) for c in clusters]

    snapshots: List[Dict[int, int]] = []
    print(f"\n  Phase 2: Taking {passes} snapshots ({delay}s apart)...")
    for p in range(passes):
        print(f"  Snapshot {p + 1}/{passes}...", end="", flush=True)
        snap: Dict[int, int] = {}
        for bstart, bcount in blocks:
            offset = 0
            while offset < bcount:
                chunk = min(125, bcount - offset)
                regs = client.read_holding_registers(bstart + offset, chunk)
                if regs:
                    for i, val in enumerate(regs):
                        snap[bstart + offset + i] = val
                offset += chunk
            time.sleep(0.02)
        snapshots.append(snap)
        t_stamp = time.monotonic()
        print(f" ({len(snap)} regs)")
        if p < passes - 1:
            time.sleep(delay)

    client.close()

    # Build observations
    observations: Dict[int, RegisterObservation] = {}
    for addr in sorted(all_nonzero.keys()):
        values = [s.get(addr, 0) for s in snapshots]
        unique = set(values)
        obs = RegisterObservation(
            address=addr,
            raw_values=values,
            timestamps=[i * delay for i in range(len(values))],
            is_changing=len(unique) > 1,
            raw_min=min(values),
            raw_max=max(values),
            raw_range=max(values) - min(values),
            int16_value=_int16_signed(values[-1]),
        )
        observations[addr] = obs

    # Compute FLOAT32 and temporal patterns
    addrs = sorted(observations.keys())
    for addr in addrs:
        obs = observations[addr]
        next_addr = addr + 1
        if next_addr in observations:
            next_obs = observations[next_addr]
            n = min(len(obs.raw_values), len(next_obs.raw_values))
            ge_vals = []
            std_vals = []
            for i in range(n):
                try:
                    ge = _decode_float32_ge(obs.raw_values[i], next_obs.raw_values[i])
                    if _is_sane(ge):
                        ge_vals.append(ge)
                except (struct.error, OverflowError):
                    pass
                try:
                    std = _decode_float32_std(obs.raw_values[i], next_obs.raw_values[i])
                    if _is_sane(std):
                        std_vals.append(std)
                except (struct.error, OverflowError):
                    pass
            obs.float32_ge_values = ge_vals
            obs.float32_std_values = std_vals
            if ge_vals:
                obs.float32_ge = ge_vals[-1]
            if std_vals:
                obs.float32_std = std_vals[-1]

        # Temporal — use float32 if sane, else raw
        use_float = False
        if obs.float32_ge_values and len(obs.float32_ge_values) > 1:
            max_abs = max(abs(v) for v in obs.float32_ge_values)
            if max_abs > 1e-10:
                use_float = True
        if use_float:
            obs.temporal_pattern = classify_temporal(obs.float32_ge_values, obs.timestamps)
        elif len(obs.raw_values) > 1:
            obs.temporal_pattern = classify_temporal(
                [float(v) for v in obs.raw_values], obs.timestamps)
        else:
            obs.temporal_pattern = "static" if not obs.is_changing else "noisy"

        if obs.raw_values:
            mean = sum(obs.raw_values) / len(obs.raw_values)
            obs.variance = sum((v - mean) ** 2 for v in obs.raw_values) / len(obs.raw_values)

    return observations


# ═══════════════════════════════════════════════════════════════════════
# Main Orchestrator
# ═══════════════════════════════════════════════════════════════════════

def analyze(observations: Dict[int, RegisterObservation],
            spec: EquipmentSpec, equipment_type: str,
            profile: Optional[MachineProfile] = None,
            min_confidence: float = 0.15) -> AssignmentResult:
    """Full physics-based analysis pipeline."""
    # Phase 3: Detect word swap
    word_swap = detect_word_swap(observations, spec)

    # Phase 4: Score all registers against all quantities
    candidates = score_all(observations, spec, word_swap, equipment_type, profile)

    # Phase 5: Solve assignment
    assigned = solve_assignment(candidates, min_confidence)

    # Phase 6: Cross-validate
    xv_score, xv_details = cross_validate(assigned, spec, profile)

    # Detect blocks
    blocks = detect_blocks(observations)

    # Template comparison
    tmpl_matches = compare_templates(assigned)

    # Identify unassigned but interesting registers
    assigned_addrs = set()
    for c in assigned.values():
        assigned_addrs.add(c.address)
        if c.data_type in ("FLOAT32", "INT32"):
            assigned_addrs.add(c.address + 1)

    unassigned_interesting = []
    for addr, obs in sorted(observations.items()):
        if addr in assigned_addrs:
            continue
        if obs.is_changing and obs.float32_ge is not None and _is_sane(obs.float32_ge):
            unassigned_interesting.append(obs)

    all_quantities = list(QUANTITY_SCORERS) + ["pressure"] + list(INT_QUANTITY_SCORERS) + ["encoder_counts"]
    unassigned_qty = [q for q in all_quantities if q not in assigned]

    return AssignmentResult(
        equipment_type=equipment_type,
        equipment_spec=spec,
        assignments=assigned,
        unassigned_quantities=unassigned_qty,
        unassigned_registers=unassigned_interesting,
        blocks=blocks,
        cross_validation_score=xv_score,
        cross_validation_details=xv_details,
        word_swap_detected=word_swap,
        template_matches=tmpl_matches,
    )


# ═══════════════════════════════════════════════════════════════════════
# Output: Console Report
# ═══════════════════════════════════════════════════════════════════════

def print_report(result: AssignmentResult, host: str = ""):
    """Print human-readable console report."""
    spec = result.equipment_spec
    print()
    print("=" * 78)
    print(f"  PHYSICS-BASED REGISTER MAPPING")
    if host:
        print(f"  PLC: {host}")
    print(f"  Equipment: {result.equipment_type.upper()} "
          f"({spec.rated_hp:.0f}HP, {spec.gear_ratio}:1 gear, "
          f"{spec.max_torque_ftlbs:,.0f} ft-lbs max, {spec.max_rpm:.0f} RPM max)")
    print(f"  Word swap: {result.word_swap_detected} "
          f"({'matches' if (result.word_swap_detected == 'ge_swap') == spec.word_swap else 'DIFFERS from'} spec)")
    print("=" * 78)

    # Assignments table
    print()
    print("  PROPOSED REGISTER MAP")
    print("  " + "-" * 74)
    print(f"  {'Quantity':<20s} {'Addr':>6s}   {'Type':<8s} {'Value':<18s} "
          f"{'Conf':>5s}  Notes")
    print("  " + "-" * 74)

    for qty in ["torque", "rpm", "temperature", "pressure", "turns", "hookload",
                "connection_state", "target_torque", "shoulder_torque",
                "connection_count", "encoder_counts"]:
        if qty not in result.assignments:
            continue
        c = result.assignments[qty]
        # Format value with units
        units = {"torque": "ft-lbs", "rpm": "RPM", "temperature": "degF",
                 "pressure": "PSI", "turns": "turns", "hookload": "lbs",
                 "encoder_counts": "counts", "connection_state": "",
                 "target_torque": "ft-lbs", "shoulder_torque": "ft-lbs",
                 "connection_count": ""}
        u = units.get(qty, "")
        if qty == "connection_state":
            state_names = {0: "IDLE", 1: "STAB", 2: "SPIN_IN", 3: "SHOULDER",
                           4: "POWER_TIGHT", 5: "HOLD", 9: "COMPLETE"}
            val_str = f"{int(c.decoded_value)} ({state_names.get(int(c.decoded_value), '?')})"
        elif abs(c.decoded_value) >= 1000:
            val_str = f"{c.decoded_value:,.0f} {u}"
        elif abs(c.decoded_value) < 1:
            val_str = f"{c.decoded_value:.3f} {u}"
        else:
            val_str = f"{c.decoded_value:.1f} {u}"
        notes = "; ".join(c.reasoning[:2])
        print(f"  {qty:<20s} R{c.address:<5d} {c.data_type:<8s} {val_str:<18s} "
              f"{c.confidence:5.2f}  {notes}")

    if result.unassigned_quantities:
        print()
        print(f"  NOT FOUND: {', '.join(result.unassigned_quantities)}")

    # Cross-validation
    print()
    print("  CROSS-VALIDATION")
    print("  " + "-" * 74)
    for detail in result.cross_validation_details:
        print(f"  {detail}")

    # Template comparison
    print()
    print("  TEMPLATE COMPARISON")
    print("  " + "-" * 74)
    for tname, score in sorted(result.template_matches.items(),
                                key=lambda x: x[1], reverse=True):
        pct = score * 100
        marker = " <-- USE THIS" if pct >= 70 else ""
        print(f"  {tname:<25s} {pct:5.1f}% match{marker}")

    # Unassigned interesting registers
    if result.unassigned_registers:
        print()
        print("  UNASSIGNED INTERESTING REGISTERS (changing, valid FLOAT32)")
        print("  " + "-" * 74)
        for obs in result.unassigned_registers[:15]:
            v = obs.float32_ge if obs.float32_ge is not None else 0
            print(f"  R{obs.address:<5d} GE_f32={v:>12.1f}  {obs.temporal_pattern:<14s}  "
                  f"range={obs.raw_range}")

    # Register blocks
    if result.blocks:
        print()
        print("  REGISTER BLOCKS (contiguous groups)")
        print("  " + "-" * 74)
        for b in result.blocks:
            if b.num_float32_pairs >= 2 and b.all_changing:
                print(f"  {b.description}  ** PROCESS DATA BLOCK **")
            elif b.num_float32_pairs >= 2:
                print(f"  {b.description}")

    print()
    print("=" * 78)


# ═══════════════════════════════════════════════════════════════════════
# Output: YAML Profile
# ═══════════════════════════════════════════════════════════════════════

def generate_yaml(result: AssignmentResult, output_path: str,
                  host: str = "", port: int = 502):
    """Generate a MachineProfile-compatible YAML file."""
    lines = []
    lines.append(f"# Auto-generated by physics_mapper.py")
    lines.append(f"# Equipment: {result.equipment_type.upper()} | "
                 f"Cross-validation: {result.cross_validation_score:.2f}")
    lines.append(f"# PROPOSED — verify before production use")
    name = Path(output_path).stem
    lines.append(f"name: {name}")
    lines.append(f'plc_ip: "{host}"')
    lines.append(f"plc_port: {port}")
    lines.append(f"unit_id: 0")
    lines.append(f"word_swap: {'true' if result.word_swap_detected == 'ge_swap' else 'false'}")
    lines.append(f"equipment_type: {result.equipment_type}")
    lines.append("")
    lines.append("# Equipment specs")
    lines.append(f"motor_displacement_cc: 250.0")
    lines.append(f"max_pressure_psi: 5000.0")
    lines.append(f"encoder_cpr: 1174")
    lines.append(f"torque_cell_capacity_ftlbs: {result.equipment_spec.max_torque_ftlbs:.1f}")
    lines.append("")
    lines.append("# Register map — physics-mapped")
    lines.append("reg_map:")

    for qty in ["torque", "rpm", "temperature", "pressure", "turns", "hookload",
                "connection_state", "target_torque", "shoulder_torque",
                "connection_count", "encoder_counts"]:
        if qty not in result.assignments:
            continue
        c = result.assignments[qty]
        units = {"torque": "ft-lbs", "rpm": "RPM", "temperature": "degF",
                 "pressure": "PSI", "turns": "turns", "hookload": "lbs",
                 "encoder_counts": "counts", "connection_state": "enum",
                 "target_torque": "ft-lbs", "shoulder_torque": "ft-lbs",
                 "connection_count": "count"}
        u = units.get(qty, "")
        lines.append(f"  {qty}:")
        lines.append(f"    address: {c.address}")
        lines.append(f"    data_type: {c.data_type}")
        lines.append(f"    unit: {u}")
        if qty == "hookload":
            lines.append(f"    scale: 0.001")
        desc = f"AUTO-MAPPED (conf={c.confidence:.2f})"
        lines.append(f'    description: "{desc}"')

    Path(output_path).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f"\n  YAML profile written to: {output_path}")


# ═══════════════════════════════════════════════════════════════════════
# Output: Markdown Report
# ═══════════════════════════════════════════════════════════════════════

def generate_report(result: AssignmentResult, report_path: str, host: str = ""):
    """Generate detailed markdown report."""
    spec = result.equipment_spec
    lines = []
    lines.append(f"# Physics-Based Register Mapping Report")
    lines.append(f"**Equipment:** {result.equipment_type.upper()} "
                 f"({spec.rated_hp:.0f}HP, {spec.gear_ratio}:1, "
                 f"{spec.max_torque_ftlbs:,.0f} ft-lbs, {spec.max_rpm:.0f} RPM)")
    if host:
        lines.append(f"**PLC:** {host}")
    lines.append(f"**Word swap:** {result.word_swap_detected}")
    lines.append(f"**Cross-validation score:** {result.cross_validation_score:.2f}")
    lines.append("")

    # Assignments
    lines.append("## Proposed Register Map")
    lines.append("")
    lines.append("| Quantity | Addr | Type | Value | Confidence | Reasoning |")
    lines.append("|----------|------|------|-------|------------|-----------|")
    for qty in ["torque", "rpm", "temperature", "pressure", "turns", "hookload",
                "connection_state", "target_torque", "shoulder_torque",
                "connection_count", "encoder_counts"]:
        if qty not in result.assignments:
            continue
        c = result.assignments[qty]
        reasons = "; ".join(c.reasoning)
        lines.append(f"| {qty} | R{c.address} | {c.data_type} | "
                     f"{c.decoded_value:,.2f} | {c.confidence:.2f} | {reasons} |")

    if result.unassigned_quantities:
        lines.append("")
        lines.append(f"**Not found:** {', '.join(result.unassigned_quantities)}")

    # Cross-validation
    lines.append("")
    lines.append("## Cross-Validation")
    lines.append("")
    for d in result.cross_validation_details:
        lines.append(f"- {d}")

    # Template comparison
    lines.append("")
    lines.append("## Template Comparison")
    lines.append("")
    for tname, score in sorted(result.template_matches.items(),
                                key=lambda x: x[1], reverse=True):
        lines.append(f"- **{tname}**: {score * 100:.1f}% match")

    # Blocks
    if result.blocks:
        lines.append("")
        lines.append("## Register Blocks")
        lines.append("")
        for b in result.blocks:
            marker = " **PROCESS DATA**" if b.all_changing and b.num_float32_pairs >= 2 else ""
            lines.append(f"- {b.description}{marker}")

    # Unassigned
    if result.unassigned_registers:
        lines.append("")
        lines.append("## Unassigned Interesting Registers")
        lines.append("")
        lines.append("| Addr | GE Float32 | Pattern | Range |")
        lines.append("|------|-----------|---------|-------|")
        for obs in result.unassigned_registers[:20]:
            v = obs.float32_ge if obs.float32_ge is not None else 0
            lines.append(f"| R{obs.address} | {v:.1f} | {obs.temporal_pattern} | "
                         f"{obs.raw_range} |")

    Path(report_path).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f"  Report written to: {report_path}")


# ═══════════════════════════════════════════════════════════════════════
# Public API — called from capture_live.py and other modules
# ═══════════════════════════════════════════════════════════════════════

def auto_map_registers(ip: str, port: int = 502, unit_id: int = 0,
                       equipment_type: str = "unknown",
                       profile: Optional[MachineProfile] = None,
                       passes: int = 8, delay: float = 2.0,
                       min_confidence: float = 0.15,
                       save_profile: str = None,
                       save_report: str = None,
                       quiet: bool = False
                       ) -> Optional[Dict[str, 'RegisterDef']]:
    """Auto-map PLC registers using physics-based analysis.

    This is the main entry point for integration with capture_live.py.
    Connects to the PLC, scans registers, and returns a reg_map dict
    compatible with MachineProfile.

    Args:
        ip: PLC IP address
        port: Modbus TCP port (default 502)
        unit_id: Modbus unit ID
        equipment_type: Equipment type string (hxi_ht, warrior, etc.)
        profile: Existing MachineProfile (overrides equipment_type if set)
        passes: Number of scan snapshots (default 8)
        delay: Seconds between snapshots (default 2.0)
        min_confidence: Minimum confidence to accept an assignment
        save_profile: If set, write YAML profile to this path
        save_report: If set, write markdown report to this path
        quiet: Suppress console output

    Returns:
        Dict[str, RegisterDef] mapping quantity names to register definitions,
        or None if scan failed. Only includes assignments above min_confidence.
    """
    if not quiet:
        print(f"\n  Physics mapper: scanning {ip}:{port} "
              f"(type={equipment_type}, {passes} passes, {delay}s apart)")

    # Resolve equipment spec
    eq_type = equipment_type
    if profile and profile.equipment_type:
        eq_type = profile.equipment_type

    spec, eq_type, _ = resolve_equipment(eq_type)

    if not quiet:
        print(f"  Equipment: {eq_type.upper()} "
              f"({spec.rated_hp:.0f}HP, {spec.gear_ratio}:1, "
              f"{spec.max_torque_ftlbs:,.0f} ft-lbs, {spec.max_rpm:.0f} RPM)")

    # Phase 1-2: Scan
    try:
        observations = scan_live(ip, port, unit_id, passes, delay)
    except Exception as e:
        if not quiet:
            print(f"  Physics mapper: scan failed — {e}")
        return None

    if not observations:
        if not quiet:
            print(f"  Physics mapper: no register data found")
        return None

    # Phase 3-6: Analyze
    result = analyze(observations, spec, eq_type, profile, min_confidence)

    if not result.assignments:
        if not quiet:
            print(f"  Physics mapper: no confident assignments found")
        return None

    if not quiet:
        n = len(result.assignments)
        xv = result.cross_validation_score
        print(f"  Physics mapper: mapped {n} registers "
              f"(cross-validation={xv:.2f})")
        for qty, c in sorted(result.assignments.items(),
                              key=lambda x: x[1].confidence, reverse=True):
            print(f"    R{c.address:<5d} = {qty:<20s} "
                  f"({c.decoded_value:>12,.2f})  conf={c.confidence:.2f}")

    # Convert to RegisterDef dict
    reg_map = _result_to_reg_map(result)

    # Optional outputs
    if save_profile:
        generate_yaml(result, save_profile, ip, port)
    if save_report:
        generate_report(result, save_report, ip)

    return reg_map


def auto_map_from_scan_file(scan_file: str,
                            equipment_type: str = "unknown",
                            profile: Optional[MachineProfile] = None,
                            min_confidence: float = 0.15,
                            ) -> Optional[Dict[str, 'RegisterDef']]:
    """Auto-map registers from an existing scan file (offline mode).

    Same as auto_map_registers() but reads from a scan file instead of
    connecting to a live PLC. Useful for re-analysis and testing.
    """
    eq_type = equipment_type
    if profile and profile.equipment_type:
        eq_type = profile.equipment_type

    spec, eq_type, _ = resolve_equipment(eq_type)
    observations = parse_scan_file(scan_file)
    if not observations:
        return None

    result = analyze(observations, spec, eq_type, profile, min_confidence)
    if not result.assignments:
        return None

    return _result_to_reg_map(result)


def _result_to_reg_map(result: AssignmentResult) -> Dict[str, 'RegisterDef']:
    """Convert AssignmentResult to MachineProfile-compatible reg_map dict."""
    units = {
        "torque": "ft-lbs", "rpm": "RPM", "temperature": "degF",
        "pressure": "PSI", "turns": "turns", "hookload": "lbs",
        "encoder_counts": "counts", "connection_state": "",
        "target_torque": "ft-lbs", "shoulder_torque": "ft-lbs",
        "connection_count": "",
    }

    reg_map = {}
    for qty, c in result.assignments.items():
        reg_map[qty] = RegisterDef(
            address=c.address,
            data_type=c.data_type,
            unit=units.get(qty, ""),
            scale=0.001 if qty == "hookload" else 1.0,
            offset=0.0,
            description=f"AUTO-MAPPED (conf={c.confidence:.2f})",
        )
    return reg_map


def discover_fleet(profile_dir: str = "profiles",
                   port: int = 502,
                   passes: int = 8,
                   delay: float = 2.0,
                   min_confidence: float = 0.15,
                   update_profiles: bool = True,
                   ) -> Dict[str, Dict[str, 'RegisterDef']]:
    """Scan all profiles with empty reg_maps and auto-map their registers.

    Iterates through all YAML profiles in profile_dir, finds those with
    empty or minimal reg_maps, connects to each PLC, and runs physics-based
    register mapping.

    Args:
        profile_dir: Directory containing YAML profiles
        port: Modbus TCP port
        passes: Number of scan snapshots per PLC
        delay: Seconds between snapshots
        min_confidence: Minimum confidence threshold
        update_profiles: If True, update YAML files with discovered reg_maps

    Returns:
        Dict mapping profile name to discovered reg_map (only successful ones).
    """
    from pathlib import Path

    profile_files = sorted(Path(profile_dir).glob("*.yaml"))
    if not profile_files:
        print(f"  No YAML profiles found in {profile_dir}/")
        return {}

    # Find profiles needing mapping
    needs_mapping = []
    for pf in profile_files:
        try:
            p = MachineProfile.from_yaml(str(pf))
        except Exception:
            continue
        # Check if reg_map is empty or has only TBD entries
        has_real_regs = any(
            r.address > 0 and "TBD" not in r.description
            for r in p.reg_map.values()
        ) if p.reg_map else False
        if not has_real_regs and p.plc_ip:
            needs_mapping.append((pf, p))

    if not needs_mapping:
        print(f"  All {len(profile_files)} profiles already have register maps.")
        return {}

    print(f"\n  Fleet Discovery: {len(needs_mapping)} profiles need register mapping "
          f"(of {len(profile_files)} total)")
    print(f"  " + "=" * 60)

    results = {}
    for pf, profile in needs_mapping:
        ip = profile.plc_ip
        eq_type = profile.equipment_type or "unknown"
        print(f"\n  [{profile.name}] {ip} — {eq_type.upper()}")

        reg_map = auto_map_registers(
            ip=ip,
            port=profile.plc_port or port,
            unit_id=profile.unit_id,
            equipment_type=eq_type,
            profile=profile,
            passes=passes,
            delay=delay,
            min_confidence=min_confidence,
            quiet=True,
        )

        if reg_map:
            n = len(reg_map)
            print(f"    Mapped {n} registers")
            results[profile.name] = reg_map

            if update_profiles:
                # Update the profile with discovered reg_map
                profile.reg_map = reg_map
                profile.to_yaml(str(pf))
                print(f"    Updated: {pf.name}")
        else:
            print(f"    FAILED — PLC unreachable or no confident assignments")

    print(f"\n  Fleet Discovery complete: "
          f"{len(results)}/{len(needs_mapping)} profiles updated")
    return results


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def resolve_equipment(equipment_type: str = None,
                      profile_path: str = None
                      ) -> Tuple[EquipmentSpec, str, Optional[MachineProfile]]:
    """Resolve equipment spec from type string or profile YAML."""
    profile = None
    if profile_path:
        profile = MachineProfile.from_yaml(profile_path)
        if not equipment_type:
            equipment_type = profile.equipment_type

    if not equipment_type:
        equipment_type = "unknown"

    # Map string to EquipmentType enum
    eq_type = None
    for et in EquipmentType:
        if et.value == equipment_type:
            eq_type = et
            break

    # Also handle fleet CSV types that don't match enum exactly
    alias_map = {
        "hxi_ss": EquipmentType.HXI_SMART_SLIDE,
        "hxi_standard": EquipmentType.HXI,
        "hxi_relay": EquipmentType.HXI,
        "rx3i": EquipmentType.ROSTEL,
        "wireless": EquipmentType.UNKNOWN,
        "hmi": EquipmentType.UNKNOWN,
        "hci": EquipmentType.UNKNOWN,
    }
    if eq_type is None:
        eq_type = alias_map.get(equipment_type, EquipmentType.UNKNOWN)

    spec = EQUIPMENT_SPECS.get(eq_type, EQUIPMENT_SPECS[EquipmentType.UNKNOWN])
    return spec, equipment_type, profile


def main():
    parser = argparse.ArgumentParser(
        description="Physics-Based PLC Register Auto-Mapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze existing scan data
  python physics_mapper.py --scan-file t.md --equipment-type hxi_ht

  # Live scan single PLC
  python physics_mapper.py --ip 129.168.1.25 --equipment-type hxi_ht

  # With profile YAML
  python physics_mapper.py --ip 129.168.1.25 --profile profiles/rig707.yaml

  # Fleet discovery — scan all profiles with empty reg_maps
  python physics_mapper.py --discover --profile-dir profiles/

  # Fleet dry-run (scan but don't update YAMLs)
  python physics_mapper.py --discover --profile-dir profiles/ --dry-run
        """)

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ip", help="PLC IP for live scan")
    source.add_argument("--scan-file", help="Path to analyze_registers.py output file")
    source.add_argument("--discover", action="store_true",
                        help="Fleet discovery mode: scan all profiles with empty reg_maps")

    parser.add_argument("--equipment-type", help="Equipment type (hxi, hxi_ht, warrior, etc.)")
    parser.add_argument("--profile", help="Existing YAML profile (auto-detects equipment type)")
    parser.add_argument("--profile-dir", default="profiles",
                        help="Profile directory for --discover mode (default: profiles/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="In --discover mode, scan but don't update YAML files")

    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit-id", type=int, default=0)
    parser.add_argument("--passes", type=int, default=8)
    parser.add_argument("--delay", type=float, default=2.0)

    parser.add_argument("--output", "-o", help="Output YAML profile path")
    parser.add_argument("--report", help="Output markdown report path")
    parser.add_argument("--min-confidence", type=float, default=0.15)

    args = parser.parse_args()

    # Fleet discovery mode
    if args.discover:
        discover_fleet(
            profile_dir=args.profile_dir,
            port=args.port,
            passes=args.passes,
            delay=args.delay,
            min_confidence=args.min_confidence,
            update_profiles=not args.dry_run,
        )
        return

    # Single PLC mode
    spec, eq_type, profile = resolve_equipment(args.equipment_type, args.profile)
    print(f"\n  Equipment: {eq_type.upper()} "
          f"({spec.rated_hp:.0f}HP, {spec.gear_ratio}:1, "
          f"{spec.max_torque_ftlbs:,.0f} ft-lbs max, {spec.max_rpm:.0f} RPM max)")

    # Collect observations
    if args.scan_file:
        print(f"  Parsing scan file: {args.scan_file}")
        observations = parse_scan_file(args.scan_file)
        print(f"  Parsed {len(observations)} registers")
    else:
        try:
            observations = scan_live(args.ip, args.port, args.unit_id,
                                     args.passes, args.delay)
        except (ConnectionError, RuntimeError) as e:
            print(f"  ERROR: {e}")
            sys.exit(1)

    if not observations:
        print("  ERROR: No register data found!")
        sys.exit(1)

    # Run analysis
    result = analyze(observations, spec, eq_type, profile, args.min_confidence)

    # Output
    host = args.ip or ""
    print_report(result, host)

    if args.output:
        generate_yaml(result, args.output, host, args.port)

    if args.report:
        generate_report(result, args.report, host)


if __name__ == '__main__':
    main()
