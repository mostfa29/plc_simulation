"""Tests for opcua_transport.py.

Covers:
  - _value_to_words conversion (int, bool, float, signed negatives)
  - Node map loading from JSON
  - safe_read / safe_write when disconnected return failure
  - Failure counter increments and is_healthy responds correctly
  - detect_plc_restart tracks the counter
"""
from __future__ import annotations

import asyncio
import json
import struct

import pytest

from hxi_optimizer.comms.opcua_transport import OPCUATransport, _value_to_words
from hxi_optimizer.hxi_config import Config


# ═══════════════════════════════════════════════════════════════════════════
# 1. _value_to_words conversion
# ═══════════════════════════════════════════════════════════════════════════

class TestValueToWords:
    def test_bool_true(self):
        hi, lo, n = _value_to_words(True)
        assert (hi, lo, n) == (1, 0, 1)

    def test_bool_false(self):
        hi, lo, n = _value_to_words(False)
        assert (hi, lo, n) == (0, 0, 1)

    def test_int_zero(self):
        hi, lo, n = _value_to_words(0)
        assert (hi, lo, n) == (0, 0, 1)

    @pytest.mark.parametrize("val", [1, 100, 1000, 32767])
    def test_int_positive(self, val):
        hi, lo, n = _value_to_words(val)
        assert hi == val
        assert n == 1

    @pytest.mark.parametrize("val", [-1, -100, -32768])
    def test_int_negative(self, val):
        hi, lo, n = _value_to_words(val)
        assert hi == (val & 0xFFFF)
        assert n == 1

    @pytest.mark.parametrize("val", [0.0, 1.0, -1.0, 60.0, 1234.5, 3.14159, -1234.5])
    def test_float_roundtrip(self, val):
        hi, lo, n = _value_to_words(val)
        assert n == 2
        back = struct.unpack(">f", struct.pack(">HH", hi, lo))[0]
        assert abs(back - val) < 1e-5

    def test_unknown_type_returns_zero(self):
        hi, lo, n = _value_to_words("not a number")
        assert (hi, lo, n) == (0, 0, 1)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Node map loading
# ═══════════════════════════════════════════════════════════════════════════

class TestNodeMapLoading:
    def test_loads_existing_map(self, tmp_path):
        map_file = tmp_path / "nodes.json"
        map_file.write_text(json.dumps({"6599": "ns=4;s=RPM", "6602": "ns=4;s=Swash"}))
        cfg = Config(plc_host="127.0.0.1", transport="opcua",
                     opcua_node_map_file=str(map_file))
        t = OPCUATransport(cfg)
        assert t._node_map[6599] == "ns=4;s=RPM"
        assert t._node_map[6602] == "ns=4;s=Swash"

    def test_missing_map_is_empty(self, tmp_path):
        cfg = Config(plc_host="127.0.0.1", transport="opcua",
                     opcua_node_map_file=str(tmp_path / "nonexistent.json"))
        t = OPCUATransport(cfg)
        assert t._node_map == {}

    def test_int_keys_preserved(self, tmp_path):
        map_file = tmp_path / "nodes.json"
        map_file.write_text(json.dumps({"6599": "node_a"}))
        cfg = Config(plc_host="127.0.0.1", transport="opcua",
                     opcua_node_map_file=str(map_file))
        t = OPCUATransport(cfg)
        assert 6599 in t._node_map  # int key, not string


# ═══════════════════════════════════════════════════════════════════════════
# 3. Disconnected behaviour
# ═══════════════════════════════════════════════════════════════════════════

class TestDisconnectedBehaviour:
    @pytest.mark.asyncio
    async def test_read_returns_none_when_disconnected(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        assert await t.safe_read(6599, 2) is None

    @pytest.mark.asyncio
    async def test_write_returns_none_when_disconnected(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        assert await t.safe_write_registers(6602, [400, 600]) is None

    @pytest.mark.asyncio
    async def test_failure_count_increments_on_read_while_down(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        for _ in range(5):
            await t.safe_read(6599, 2)
        assert t.consecutive_failures == 5

    @pytest.mark.asyncio
    async def test_failure_count_increments_on_write_while_down(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        for _ in range(5):
            await t.safe_write_registers(6602, [400])
        assert t.consecutive_failures == 5


# ═══════════════════════════════════════════════════════════════════════════
# 4. Health property
# ═══════════════════════════════════════════════════════════════════════════

class TestHealthProperty:
    def test_initial_unhealthy(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        assert t.is_healthy is False

    def test_healthy_when_connection_ok_and_no_failures(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        t.connection_healthy = True
        assert t.is_healthy is True

    def test_unhealthy_above_failure_threshold(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        t.connection_healthy = True
        t.consecutive_failures_count = 3
        assert t.is_healthy is False

    @pytest.mark.parametrize("failures", [0, 1, 2])
    def test_healthy_below_threshold(self, failures):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        t.connection_healthy = True
        t.consecutive_failures_count = failures
        assert t.is_healthy is True


# ═══════════════════════════════════════════════════════════════════════════
# 5. Client compatibility shim
# ═══════════════════════════════════════════════════════════════════════════

class TestClientShim:
    def test_client_property_exists(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        assert hasattr(t, "client")
        assert hasattr(t.client, "connected")

    def test_client_connected_mirrors_health(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        assert t.client.connected is False
        t.connection_healthy = True
        assert t.client.connected is True


# ═══════════════════════════════════════════════════════════════════════════
# 6. Restart detection
# ═══════════════════════════════════════════════════════════════════════════

class TestRestartDetection:
    def test_first_call_no_restart(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        assert t.detect_plc_restart(100) is False

    def test_increment_no_restart(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        t.detect_plc_restart(100)
        assert t.detect_plc_restart(101) is False

    def test_large_drop_is_restart(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        t.detect_plc_restart(1000)
        assert t.detect_plc_restart(5) is True

    @pytest.mark.parametrize("before,after,restart", [
        (1000, 5, True),
        (1000, 899, True),
        (1000, 901, False),
        (1000, 1001, False),
    ])
    def test_scenarios(self, before, after, restart):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = OPCUATransport(cfg)
        t.detect_plc_restart(before)
        assert t.detect_plc_restart(after) is restart
