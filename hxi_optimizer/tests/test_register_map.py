"""Tests for comms/register_map.py — FLOAT32 gating, decode/encode, register map.

~150 parametrized tests covering:
- Word-order gate refuses decode when unverified
- ABCD and CDAB decode paths with known floats
- Encode round-trips
- Edge values: 0, -0, inf, -inf, NaN, denormalized, max/min float
- Signed int16 conversion
- ESD bit extraction from all 16 bit positions
- decode_registers with full / partial / empty blocks
- build_sample raw-word preservation
"""
from __future__ import annotations

import math
import struct

import pytest

from hxi_optimizer.comms import register_map
from hxi_optimizer.comms.register_map import (
    ADDR_ACTIVE_LOWER, ADDR_ACTIVE_UPPER, ADDR_HEARTBEAT,
    ADDR_SWASH_LOWER, ADDR_SWASH_UPPER,
    READ_COUNT, READ_START_ADDR, REGISTER_MAP,
    build_sample, decode_float32_ge, decode_registers,
    encode_float32_ge, esd_bit_from_word, set_verified_word_order,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. WORD-ORDER GATE
# ═══════════════════════════════════════════════════════════════════════════

class TestWordOrderGate:
    def test_decode_raises_when_unverified(self):
        register_map.VERIFIED_WORD_ORDER = None
        with pytest.raises(RuntimeError, match="word order has not been commissioning"):
            decode_float32_ge(0x449A, 0x522B)

    def test_encode_raises_when_unverified(self):
        register_map.VERIFIED_WORD_ORDER = None
        with pytest.raises(RuntimeError, match="word order unverified"):
            encode_float32_ge(1234.5)

    def test_set_verified_abcd(self):
        set_verified_word_order("ABCD")
        assert register_map.VERIFIED_WORD_ORDER == "ABCD"

    def test_set_verified_cdab(self):
        set_verified_word_order("CDAB")
        assert register_map.VERIFIED_WORD_ORDER == "CDAB"

    @pytest.mark.parametrize("bad_order", ["abcd", "DCBA", "", "AB", None, 42])
    def test_set_verified_rejects_invalid(self, bad_order):
        with pytest.raises((ValueError, TypeError)):
            set_verified_word_order(bad_order)

    def test_decode_works_after_setting_abcd(self):
        set_verified_word_order("ABCD")
        val = decode_float32_ge(0x449A, 0x5225)
        assert abs(val - 1234.56) < 0.1

    def test_decode_works_after_setting_cdab(self):
        set_verified_word_order("CDAB")
        val = decode_float32_ge(0x5225, 0x449A)
        assert abs(val - 1234.56) < 0.1


# ═══════════════════════════════════════════════════════════════════════════
# 2. FLOAT32 DECODE — ABCD
# ═══════════════════════════════════════════════════════════════════════════

# Helper: generate word pairs for a given float in ABCD order
def _abcd_words(f: float) -> tuple[int, int]:
    raw = struct.pack(">f", f)
    return struct.unpack(">HH", raw)


# Known float values and their expected ABCD words
FLOAT_TEST_VALUES = [
    0.0, 1.0, -1.0, 0.5, -0.5,
    1234.5, -1234.5, 3.14159, -3.14159,
    100.0, 200.0, 500.0, 999.99,
    0.001, 0.0001, 1e10, -1e10, 1e-10, -1e-10,
    32767.0, -32768.0,
    float("inf"), float("-inf"),
]


class TestDecodeFloat32ABCD:
    @pytest.fixture(autouse=True)
    def _set_abcd(self):
        set_verified_word_order("ABCD")

    @pytest.mark.parametrize("value", FLOAT_TEST_VALUES)
    def test_decode_known_value(self, value):
        hi, lo = _abcd_words(value)
        result = decode_float32_ge(hi, lo)
        if math.isinf(value):
            assert math.isinf(result) and (result > 0) == (value > 0)
        else:
            assert abs(result - value) < abs(value) * 1e-6 + 1e-12

    def test_decode_nan(self):
        hi, lo = _abcd_words(float("nan"))
        assert math.isnan(decode_float32_ge(hi, lo))

    def test_decode_negative_zero(self):
        hi, lo = struct.unpack(">HH", struct.pack(">f", -0.0))
        result = decode_float32_ge(hi, lo)
        assert result == 0.0
        assert math.copysign(1.0, result) == -1.0

    @pytest.mark.parametrize("word", [0x0000, 0x7FFF, 0x8000, 0xFFFF])
    def test_extreme_word_values(self, word):
        # Should not crash — just decode whatever float it is
        result = decode_float32_ge(word, word)
        assert isinstance(result, float)

    def test_denormalized_float(self):
        raw = struct.pack(">I", 0x00000001)  # smallest denorm
        hi, lo = struct.unpack(">HH", raw)
        result = decode_float32_ge(hi, lo)
        assert result > 0 and result < 1e-38

    def test_max_float(self):
        raw = struct.pack(">I", 0x7F7FFFFF)  # float max
        hi, lo = struct.unpack(">HH", raw)
        result = decode_float32_ge(hi, lo)
        assert result > 3.4e38

    def test_min_normal_float(self):
        raw = struct.pack(">I", 0x00800000)
        hi, lo = struct.unpack(">HH", raw)
        result = decode_float32_ge(hi, lo)
        assert result > 0 and result < 2e-38


# ═══════════════════════════════════════════════════════════════════════════
# 3. FLOAT32 DECODE — CDAB
# ═══════════════════════════════════════════════════════════════════════════

class TestDecodeFloat32CDAB:
    @pytest.fixture(autouse=True)
    def _set_cdab(self):
        set_verified_word_order("CDAB")

    @pytest.mark.parametrize("value", FLOAT_TEST_VALUES)
    def test_decode_known_value_cdab(self, value):
        hi, lo = _abcd_words(value)
        # CDAB: low word first
        result = decode_float32_ge(lo, hi)
        if math.isinf(value):
            assert math.isinf(result) and (result > 0) == (value > 0)
        else:
            assert abs(result - value) < abs(value) * 1e-6 + 1e-12


# ═══════════════════════════════════════════════════════════════════════════
# 4. ENCODE ROUND-TRIP
# ═══════════════════════════════════════════════════════════════════════════

class TestEncodeFloat32:
    @pytest.mark.parametrize("order", ["ABCD", "CDAB"])
    @pytest.mark.parametrize("value", FLOAT_TEST_VALUES)
    def test_encode_decode_roundtrip(self, order, value):
        set_verified_word_order(order)
        w0, w1 = encode_float32_ge(value)
        result = decode_float32_ge(w0, w1)
        if math.isinf(value):
            assert math.isinf(result)
        elif math.isnan(value):
            assert math.isnan(result)
        else:
            assert abs(result - value) < abs(value) * 1e-6 + 1e-12

    @pytest.mark.parametrize("order", ["ABCD", "CDAB"])
    def test_encode_returns_two_ints(self, order):
        set_verified_word_order(order)
        w0, w1 = encode_float32_ge(42.0)
        assert isinstance(w0, int) and isinstance(w1, int)
        assert 0 <= w0 <= 0xFFFF and 0 <= w1 <= 0xFFFF


# ═══════════════════════════════════════════════════════════════════════════
# 5. REGISTER MAP CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

class TestRegisterMapConstants:
    def test_read_start_addr(self):
        assert READ_START_ADDR == 6599  # %R06600 - 1

    def test_read_count(self):
        assert READ_COUNT == 28

    def test_swash_lower_addr(self):
        assert ADDR_SWASH_LOWER == 6602  # %R06603

    def test_swash_upper_addr(self):
        assert ADDR_SWASH_UPPER == 6603

    def test_heartbeat_addr(self):
        assert ADDR_HEARTBEAT == 6604   # %R06605

    def test_active_lower_readback(self):
        assert ADDR_ACTIVE_LOWER == 6609  # %R06610

    def test_active_upper_readback(self):
        assert ADDR_ACTIVE_UPPER == 6610

    def test_register_map_has_rpm_encoder(self):
        assert "rpm_encoder" in REGISTER_MAP
        assert REGISTER_MAP["rpm_encoder"][1] == "float32"

    def test_register_map_has_swash_output(self):
        assert "swash_output" in REGISTER_MAP
        assert REGISTER_MAP["swash_output"][1] == "int16"

    def test_register_map_has_all_primary_fields(self):
        expected = {
            "rpm_encoder", "swash_output", "swash_lower_req", "swash_upper_req",
            "heartbeat", "req_seqnum", "active_lower", "active_upper",
            "ack_seqnum", "status_word", "ss_setpoint_fwd", "ss_setpoint_rev",
            "fwd_turns", "rev_turns", "bump_fwd_set", "bump_rev_set",
            "bump_flag_fwd",
        }
        assert expected.issubset(set(REGISTER_MAP.keys()))

    @pytest.mark.parametrize("name,expected_offset", [
        ("rpm_encoder", 0),
        ("swash_output", 2),
        ("swash_lower_req", 3),
        ("swash_upper_req", 4),
        ("heartbeat", 5),
        ("active_lower", 10),
        ("active_upper", 11),
        ("ss_setpoint_fwd", 16),
        ("bump_flag_fwd", 27),
    ])
    def test_register_offsets(self, name, expected_offset):
        assert REGISTER_MAP[name][0] == expected_offset


# ═══════════════════════════════════════════════════════════════════════════
# 6. decode_registers
# ═══════════════════════════════════════════════════════════════════════════

class TestDecodeRegisters:
    @pytest.fixture(autouse=True)
    def _set_abcd(self):
        set_verified_word_order("ABCD")

    def _make_block(self, **overrides) -> list[int]:
        """Build a 28-word register block with sensible defaults."""
        block = [0] * 28
        # RPM = 60.0 at offset 0..1
        hi, lo = _abcd_words(60.0)
        block[0], block[1] = hi, lo
        # swash_output at offset 2
        block[2] = 500
        # active_lower at 10, active_upper at 11
        block[10] = 400
        block[11] = 600
        for k, v in overrides.items():
            block[int(k)] = v
        return block

    def test_decode_rpm(self):
        block = self._make_block()
        result = decode_registers(block)
        assert abs(result["rpm_encoder"] - 60.0) < 0.1

    def test_decode_swash_output(self):
        block = self._make_block()
        result = decode_registers(block)
        assert result["swash_output"] == 500

    def test_decode_active_lower_upper(self):
        block = self._make_block()
        result = decode_registers(block)
        assert result["active_lower"] == 400
        assert result["active_upper"] == 600

    def test_signed_int16_positive(self):
        block = self._make_block()
        block[2] = 32767
        result = decode_registers(block)
        assert result["swash_output"] == 32767

    def test_signed_int16_negative(self):
        block = self._make_block()
        block[2] = 0xFFFF  # -1 in unsigned
        result = decode_registers(block)
        assert result["swash_output"] == -1

    def test_signed_int16_min(self):
        block = self._make_block()
        block[2] = 0x8000  # -32768
        result = decode_registers(block)
        assert result["swash_output"] == -32768

    def test_word_dtype_no_sign_conversion(self):
        block = self._make_block()
        block[13] = 0xABCD
        result = decode_registers(block)
        assert result["status_word"] == 0xABCD

    def test_short_block_skips_missing_offsets(self):
        block = [0] * 5  # Too short for most fields
        result = decode_registers(block)
        assert "swash_output" in result
        assert "active_lower" not in result

    def test_empty_block(self):
        result = decode_registers([])
        assert isinstance(result, dict)

    @pytest.mark.parametrize("rpm", [0.0, 30.0, 60.0, 120.0, 180.0, 220.0])
    def test_various_rpms(self, rpm):
        hi, lo = _abcd_words(rpm)
        block = [0] * 28
        block[0], block[1] = hi, lo
        result = decode_registers(block)
        assert abs(result["rpm_encoder"] - rpm) < 0.01


# ═══════════════════════════════════════════════════════════════════════════
# 7. ESD BIT EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════

class TestEsdBit:
    def test_bit0_high(self):
        assert esd_bit_from_word(0x0001) == 1

    def test_bit0_low(self):
        assert esd_bit_from_word(0x0000) == 0

    def test_other_bits_dont_trigger(self):
        assert esd_bit_from_word(0xFFFE) == 0

    def test_all_bits_high(self):
        assert esd_bit_from_word(0xFFFF) == 1

    @pytest.mark.parametrize("bit", range(16))
    def test_each_bit_position(self, bit):
        word = 1 << bit
        expected = 1 if bit == 0 else 0
        assert esd_bit_from_word(word) == expected

    @pytest.mark.parametrize("word", [0, 1, 2, 3, 0x8000, 0x8001, 0xFFFE, 0xFFFF])
    def test_known_words(self, word):
        assert esd_bit_from_word(word) == (word & 1)


# ═══════════════════════════════════════════════════════════════════════════
# 8. build_sample
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildSample:
    @pytest.fixture(autouse=True)
    def _set_abcd(self):
        set_verified_word_order("ABCD")

    def test_includes_raw_words(self):
        block = [0] * 28
        sample = build_sample(block)
        assert "raw_words" in sample
        assert len(sample["raw_words"]) == 28

    def test_raw_words_match_input(self):
        block = list(range(28))
        sample = build_sample(block)
        assert sample["raw_words"] == block

    def test_decoded_fields_present(self):
        hi, lo = _abcd_words(60.0)
        block = [0] * 28
        block[0], block[1] = hi, lo
        sample = build_sample(block)
        assert "rpm_encoder" in sample

    def test_raw_words_preserves_for_replay(self):
        block = [0xDEAD] * 28
        sample = build_sample(block)
        assert all(w == 0xDEAD for w in sample["raw_words"])
