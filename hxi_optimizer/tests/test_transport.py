"""Tests for transport.py — factory + interface contract.

Covers:
  - build_transport routes to Modbus/OPC UA based on config
  - Invalid transport name raises ValueError
  - Default (no explicit transport) returns Modbus
  - Both transports satisfy the abstract interface
"""
from __future__ import annotations

import pytest

from hxi_optimizer.comms.transport import PLCTransport, build_transport
from hxi_optimizer.comms.modbus_transport import ModbusTransport
from hxi_optimizer.comms.opcua_transport import OPCUATransport
from hxi_optimizer.hxi_config import Config


class TestTransportFactory:
    def test_default_is_modbus(self):
        cfg = Config(plc_host="127.0.0.1")
        t = build_transport(cfg)
        assert isinstance(t, ModbusTransport)
        assert t.transport_name == "Modbus TCP"

    def test_explicit_modbus(self):
        cfg = Config(plc_host="127.0.0.1", transport="modbus")
        t = build_transport(cfg)
        assert isinstance(t, ModbusTransport)

    def test_explicit_opcua(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = build_transport(cfg)
        assert isinstance(t, OPCUATransport)
        assert t.transport_name == "OPC UA"

    def test_case_insensitive(self):
        cfg = Config(plc_host="127.0.0.1", transport="OPCUA")
        t = build_transport(cfg)
        assert isinstance(t, OPCUATransport)

    @pytest.mark.parametrize("bad", ["tcp", "serial", "", "TCP/IP", "grpc"])
    def test_invalid_transport_raises(self, bad):
        cfg = Config(plc_host="127.0.0.1", transport=bad)
        with pytest.raises(ValueError, match="Unknown transport"):
            build_transport(cfg)


class TestTransportContract:
    """Every transport must expose the PLCTransport contract."""

    @pytest.mark.parametrize("transport_name", ["modbus", "opcua"])
    def test_satisfies_interface(self, transport_name):
        cfg = Config(plc_host="127.0.0.1", transport=transport_name)
        t = build_transport(cfg)
        assert isinstance(t, PLCTransport)
        # Required methods
        assert callable(t.connect)
        assert callable(t.close)
        assert callable(t.safe_read)
        assert callable(t.safe_write_registers)
        # Properties
        assert isinstance(t.is_healthy, bool)
        assert isinstance(t.consecutive_failures, int)
        assert isinstance(t.transport_name, str)

    @pytest.mark.parametrize("transport_name", ["modbus", "opcua"])
    def test_initial_state(self, transport_name):
        cfg = Config(plc_host="127.0.0.1", transport=transport_name)
        t = build_transport(cfg)
        assert t.consecutive_failures == 0
        assert t.is_healthy is False  # not connected yet

    @pytest.mark.parametrize("transport_name", ["modbus", "opcua"])
    def test_detect_plc_restart_first_call(self, transport_name):
        cfg = Config(plc_host="127.0.0.1", transport=transport_name)
        t = build_transport(cfg)
        assert t.detect_plc_restart(100) is False  # no baseline

    def test_modbus_detect_restart(self):
        cfg = Config(plc_host="127.0.0.1", transport="modbus")
        t = build_transport(cfg)
        t.detect_plc_restart(1000)
        assert t.detect_plc_restart(5) is True

    def test_opcua_detect_restart(self):
        cfg = Config(plc_host="127.0.0.1", transport="opcua")
        t = build_transport(cfg)
        t.detect_plc_restart(1000)
        assert t.detect_plc_restart(5) is True
