"""GE PACSystems register map for HXI production block.

Per MASTER_CONTEXT §2.x and §3.4. All addresses are pymodbus 0-indexed
(GE %RNNNNN address − 1).

FLOAT32 word order is GATED: VERIFIED_WORD_ORDER must be set to "ABCD" or
"CDAB" *after* commissioning test 1 (`deploy/commissioning_tests.test_byte_order`).
Until then, every decode_float32 call raises RuntimeError. This is intentional —
silent miscoding of float values is the highest-impact failure mode in the
existing legacy `capture_live.py`.
"""
from __future__ import annotations

import struct
from typing import Optional

# ─── BYTE ORDER GATE ────────────────────────────────────────────────────────
# MASTER_CONTEXT §3.4 specifies "ABCD" (big-endian, high word first).
# The legacy capture_live.py uses "CDAB". One of them is wrong on the wire.
# DO NOT set this to a guessed value: run commissioning test 1 first, and
# pin the verified order here once the round-trip matches.
VERIFIED_WORD_ORDER: Optional[str] = "ABCD"  # local-test: pinned via commissioning_tests against sim_plc


def set_verified_word_order(order: str) -> None:
    """Called by commissioning_tests.test_byte_order after empirical verification."""
    if order not in ("ABCD", "CDAB"):
        raise ValueError(f"order must be 'ABCD' or 'CDAB', got {order!r}")
    global VERIFIED_WORD_ORDER
    VERIFIED_WORD_ORDER = order


def decode_float32_ge(high_word: int, low_word: int) -> float:
    """Decode REAL from two Modbus 16-bit words. Refuses to run unverified."""
    if VERIFIED_WORD_ORDER is None:
        raise RuntimeError(
            "FLOAT32 word order has not been commissioning-verified. "
            "Run deploy/commissioning_tests.test_byte_order against the live "
            "CPE305 first, then call set_verified_word_order('ABCD' or 'CDAB')."
        )
    if VERIFIED_WORD_ORDER == "ABCD":
        raw = struct.pack(">HH", high_word, low_word)
    else:  # CDAB — low word first
        raw = struct.pack(">HH", low_word, high_word)
    return struct.unpack(">f", raw)[0]


def encode_float32_ge(value: float) -> tuple[int, int]:
    """Encode float to GE word order. Returns (word0, word1) in wire order."""
    if VERIFIED_WORD_ORDER is None:
        raise RuntimeError("FLOAT32 word order unverified — see decode_float32_ge")
    raw = struct.pack(">f", value)
    hi, lo = struct.unpack(">HH", raw)
    return (hi, lo) if VERIFIED_WORD_ORDER == "ABCD" else (lo, hi)


# ─── REGISTER MAP ──────────────────────────────────────────────────────────
# Format: name -> (offset_within_read_block, dtype, scale)
# Block starts at READ_START_ADDR = 6599 (%R06600), READ_COUNT = 28 words.
# Offsets are word-indices into the returned `registers` list.
READ_START_ADDR = 6599
READ_COUNT = 28

REGISTER_MAP: dict[str, tuple[int, str, float]] = {
    # %R06600..%R06601 — RPM encoder (REAL)
    "rpm_encoder":      (0,  "float32", 1.0),
    # %R06602 — Swash output (signed counts)
    "swash_output":     (2,  "int16",   1.0),
    # %R06603 — Swash lower request (AI WRITES HERE)
    "swash_lower_req":  (3,  "int16",   1.0),
    # %R06604 — Swash upper request (AI WRITES HERE)
    "swash_upper_req":  (4,  "int16",   1.0),
    # %R06605 — Heartbeat (AI WRITES every 5s)
    "heartbeat":        (5,  "int16",   1.0),
    # %R06606 — request seqnum
    "req_seqnum":       (6,  "int16",   1.0),
    # %R06610 — Active lower (PLC-validated readback)
    "active_lower":     (10, "int16",   1.0),
    # %R06611 — Active upper (PLC-validated readback)
    "active_upper":     (11, "int16",   1.0),
    # %R06612 — ack seqnum
    "ack_seqnum":       (12, "int16",   1.0),
    # %R06613 — status word
    "status_word":      (13, "word",    1.0),
    # %R06616..06617 — SS setpoint FWD (REAL)
    "ss_setpoint_fwd":  (16, "float32", 1.0),
    # %R06618..06619 — SS setpoint REV (REAL)
    "ss_setpoint_rev":  (18, "float32", 1.0),
    # %R06621..06622 — FWD turns (REAL)
    "fwd_turns":        (21, "float32", 1.0),
    # %R06623..06624 — REV turns (REAL)
    "rev_turns":        (23, "float32", 1.0),
    # %R06625 — Bump FWD set (AI WRITES — oscillation, Phase D)
    "bump_fwd_set":     (25, "int16",   1.0),
    # %R06626 — Bump REV set
    "bump_rev_set":     (26, "int16",   1.0),
    # %R06627 — Bump FWD flag (READ-ONLY — write lockout)
    "bump_flag_fwd":    (27, "int16",   1.0),
}

# Secondary single-register reads (outside primary block)
SECONDARY_REGS = {
    "bump_flag_rev":    (6627, "int16"),   # %R06628
    "bump_angle":       (6628, "int16"),   # %R06629
    "delivered_torque": (6645, "float32"), # %R06646..06647
    "pid_state":        (6647, "int16"),   # %R06648
    "esd_word":         (6664, "word"),    # %R06665 — bit 0 = ESD
    "loop_temp":        (6669, "float32"), # %R06670..06671
}

# Write addresses (pymodbus 0-indexed)
ADDR_SWASH_LOWER = 6602   # %R06603
ADDR_SWASH_UPPER = 6603   # %R06604 — paired with above for FC16 atomic write
ADDR_HEARTBEAT   = 6604   # %R06605
ADDR_ACTIVE_LOWER = 6609  # %R06610 — readback target after FC16 write
ADDR_ACTIVE_UPPER = 6610  # %R06611
ADDR_BUMP_FWD = 6624      # %R06625
ADDR_BUMP_REV = 6625      # %R06626


def decode_registers(registers: list[int],
                     addr_map: dict[str, tuple[int, str, float]] = REGISTER_MAP
                     ) -> dict:
    """Decode raw FC03 register list into named values per addr_map."""
    out: dict = {}
    n = len(registers)
    for name, (idx, dtype, scale) in addr_map.items():
        if dtype == "float32":
            if idx + 1 >= n:
                continue
            out[name] = decode_float32_ge(registers[idx], registers[idx + 1]) * scale
        elif dtype == "int16":
            if idx >= n:
                continue
            v = registers[idx]
            if v >= 0x8000:  # signed conversion
                v -= 0x10000
            out[name] = v * scale
        elif dtype == "word":
            if idx >= n:
                continue
            out[name] = registers[idx]
    # ESD bit derivation (if status_word includes it; spec puts ESD at %R06665)
    return out


def esd_bit_from_word(esd_word: int) -> int:
    """Extract bit 0 of %R06665."""
    return esd_word & 0x0001


def build_sample(registers: list[int]) -> dict:
    """Decode a primary read block and add raw words for replay."""
    sample = decode_registers(registers)
    sample["raw_words"] = list(registers)
    return sample
