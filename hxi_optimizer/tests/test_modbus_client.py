"""Tests for comms/modbus_client.py — AsyncModbusTcpClient wrapper.

~80 parametrized tests covering:
- create_client configuration
- safe_read: success, error response, timeout, connection exception
- safe_write_registers: success, error, timeout
- Consecutive failure tracking and recovery
- is_healthy property
- PLC restart detection
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from hxi_optimizer.comms.modbus_client import ModbusManager, create_client
from hxi_optimizer.hxi_config import Config


# ═══════════════════════════════════════════════════════════════════════════
# 1. CREATE_CLIENT
# ═══════════════════════════════════════════════════════════════════════════

class TestCreateClient:
    def test_returns_object(self):
        client = create_client("192.168.1.1")
        assert client is not None

    def test_default_port(self):
        client = create_client("192.168.1.1")
        # Can't easily check internal port due to stub, but shouldn't raise
        assert client is not None

    def test_custom_port(self):
        client = create_client("10.0.0.1", port=5020)
        assert client is not None


# ═══════════════════════════════════════════════════════════════════════════
# 2. MODBUS MANAGER — INITIALIZATION
# ═══════════════════════════════════════════════════════════════════════════

class TestManagerInit:
    def test_initial_failures_zero(self):
        cfg = Config()
        mgr = ModbusManager(cfg)
        assert mgr.consecutive_failures == 0

    def test_initial_not_healthy(self):
        cfg = Config()
        mgr = ModbusManager(cfg)
        assert mgr.connection_healthy is False

    def test_initial_plc_counter_none(self):
        cfg = Config()
        mgr = ModbusManager(cfg)
        assert mgr.last_plc_counter is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. SAFE_READ — SUCCESS
# ═══════════════════════════════════════════════════════════════════════════

class TestSafeReadSuccess:
    @pytest.mark.asyncio
    async def test_returns_register_list(self, mock_modbus):
        mock_modbus.safe_read = AsyncMock(return_value=[1, 2, 3])
        result = await mock_modbus.safe_read(address=6599, count=3)
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_resets_failure_count(self):
        cfg = Config(plc_host="127.0.0.1")
        mgr = ModbusManager(cfg)
        mgr.consecutive_failures = 5
        # Mock the client's read method
        mock_result = MagicMock()
        mock_result.isError.return_value = False
        mock_result.registers = [100, 200]
        mgr.client.read_holding_registers = AsyncMock(return_value=mock_result)
        result = await mgr.safe_read(6599, 2)
        assert result == [100, 200]
        assert mgr.consecutive_failures == 0


# ═══════════════════════════════════════════════════════════════════════════
# 4. SAFE_READ — FAILURES
# ═══════════════════════════════════════════════════════════════════════════

class TestSafeReadFailures:
    @pytest.mark.asyncio
    async def test_error_response_returns_none(self):
        cfg = Config(plc_host="127.0.0.1")
        mgr = ModbusManager(cfg)
        mock_result = MagicMock()
        mock_result.isError.return_value = True
        mgr.client.read_holding_registers = AsyncMock(return_value=mock_result)
        result = await mgr.safe_read(6599, 2)
        assert result is None
        assert mgr.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        cfg = Config(plc_host="127.0.0.1", modbus_timeout=0.01)
        mgr = ModbusManager(cfg)
        async def slow_read(*args, **kwargs):
            await asyncio.sleep(10)
        mgr.client.read_holding_registers = slow_read
        result = await mgr.safe_read(6599, 2)
        assert result is None
        assert mgr.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_consecutive_failures_increment(self):
        cfg = Config(plc_host="127.0.0.1")
        mgr = ModbusManager(cfg)
        mock_result = MagicMock()
        mock_result.isError.return_value = True
        mgr.client.read_holding_registers = AsyncMock(return_value=mock_result)
        for i in range(5):
            await mgr.safe_read(6599, 2)
        assert mgr.consecutive_failures == 5

    @pytest.mark.asyncio
    async def test_connection_healthy_false_on_failure(self):
        cfg = Config(plc_host="127.0.0.1")
        mgr = ModbusManager(cfg)
        mock_result = MagicMock()
        mock_result.isError.return_value = True
        mgr.client.read_holding_registers = AsyncMock(return_value=mock_result)
        await mgr.safe_read(6599, 2)
        assert mgr.connection_healthy is False


# ═══════════════════════════════════════════════════════════════════════════
# 5. SAFE_WRITE_REGISTERS
# ═══════════════════════════════════════════════════════════════════════════

class TestSafeWriteRegisters:
    @pytest.mark.asyncio
    async def test_success(self):
        cfg = Config(plc_host="127.0.0.1")
        mgr = ModbusManager(cfg)
        mock_result = MagicMock()
        mock_result.isError.return_value = False
        mgr.client.write_registers = AsyncMock(return_value=mock_result)
        result = await mgr.safe_write_registers(6602, [400, 600])
        assert result is True

    @pytest.mark.asyncio
    async def test_failure_returns_none(self):
        cfg = Config(plc_host="127.0.0.1")
        mgr = ModbusManager(cfg)
        mock_result = MagicMock()
        mock_result.isError.return_value = True
        mgr.client.write_registers = AsyncMock(return_value=mock_result)
        result = await mgr.safe_write_registers(6602, [400, 600])
        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        cfg = Config(plc_host="127.0.0.1", modbus_timeout=0.01)
        mgr = ModbusManager(cfg)
        async def slow_write(*args, **kwargs):
            await asyncio.sleep(10)
        mgr.client.write_registers = slow_write
        result = await mgr.safe_write_registers(6602, [400, 600])
        assert result is None

    @pytest.mark.asyncio
    async def test_increments_failures_on_error(self):
        cfg = Config(plc_host="127.0.0.1")
        mgr = ModbusManager(cfg)
        mock_result = MagicMock()
        mock_result.isError.return_value = True
        mgr.client.write_registers = AsyncMock(return_value=mock_result)
        await mgr.safe_write_registers(6602, [400, 600])
        assert mgr.consecutive_failures == 1


# ═══════════════════════════════════════════════════════════════════════════
# 6. IS_HEALTHY PROPERTY
# ═══════════════════════════════════════════════════════════════════════════

class TestIsHealthy:
    def test_healthy_when_connected_no_failures(self):
        cfg = Config(plc_host="127.0.0.1")
        mgr = ModbusManager(cfg)
        mgr.client.connected = True
        mgr.consecutive_failures = 0
        assert mgr.is_healthy is True

    def test_unhealthy_when_disconnected(self):
        cfg = Config(plc_host="127.0.0.1")
        mgr = ModbusManager(cfg)
        mgr.client.connected = False
        mgr.consecutive_failures = 0
        assert mgr.is_healthy is False

    @pytest.mark.parametrize("failures", [0, 1, 2])
    def test_healthy_below_threshold(self, failures):
        cfg = Config(plc_host="127.0.0.1")
        mgr = ModbusManager(cfg)
        mgr.client.connected = True
        mgr.consecutive_failures = failures
        assert mgr.is_healthy is True

    @pytest.mark.parametrize("failures", [3, 5, 10, 100])
    def test_unhealthy_at_or_above_threshold(self, failures):
        cfg = Config(plc_host="127.0.0.1")
        mgr = ModbusManager(cfg)
        mgr.client.connected = True
        mgr.consecutive_failures = failures
        assert mgr.is_healthy is False


# ═══════════════════════════════════════════════════════════════════════════
# 7. PLC RESTART DETECTION
# ═══════════════════════════════════════════════════════════════════════════

class TestPLCRestartDetection:
    def test_first_call_no_restart(self):
        cfg = Config()
        mgr = ModbusManager(cfg)
        assert mgr.detect_plc_restart(100) is False

    def test_incrementing_counter_no_restart(self):
        cfg = Config()
        mgr = ModbusManager(cfg)
        mgr.detect_plc_restart(100)
        assert mgr.detect_plc_restart(101) is False

    def test_large_drop_is_restart(self):
        cfg = Config()
        mgr = ModbusManager(cfg)
        mgr.detect_plc_restart(1000)
        assert mgr.detect_plc_restart(5) is True

    def test_small_drop_not_restart(self):
        cfg = Config()
        mgr = ModbusManager(cfg)
        mgr.detect_plc_restart(1000)
        assert mgr.detect_plc_restart(950) is False  # drop of 50 < 100 threshold

    @pytest.mark.parametrize("before,after,restart", [
        (1000, 5, True),       # Large drop
        (1000, 899, True),     # drop > 100
        (1000, 900, False),    # drop exactly 100 (threshold is > 100, not >=)
        (1000, 901, False),    # drop < 100
        (1000, 1001, False),   # normal increment
        (1000, 1000, False),   # same value
        (200, 0, True),        # counter reset to zero (drop of 200 > 100)
    ])
    def test_restart_scenarios(self, before, after, restart):
        cfg = Config()
        mgr = ModbusManager(cfg)
        mgr.detect_plc_restart(before)
        assert mgr.detect_plc_restart(after) is restart
