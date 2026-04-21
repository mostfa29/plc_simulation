"""Modbus TCP transport — adapter around ModbusManager.

Implements the PLCTransport interface by delegating to the existing
ModbusManager. No behaviour change; this preserves the 1,065-test surface.
"""
from __future__ import annotations

import logging
from typing import Optional

from hxi_optimizer.comms.modbus_client import ModbusManager
from hxi_optimizer.comms.transport import PLCTransport

logger = logging.getLogger("modbus_transport")


class ModbusTransport(PLCTransport):
    """Thin adapter — ModbusManager already matches the transport contract."""

    def __init__(self, config) -> None:
        self._mgr = ModbusManager(config)

    async def connect(self) -> None:
        await self._mgr.connect()

    async def close(self) -> None:
        try:
            self._mgr.client.close()
        except Exception:
            pass

    async def safe_read(self, address: int, count: int) -> Optional[list[int]]:
        return await self._mgr.safe_read(address, count)

    async def safe_write_registers(self, address: int,
                                   values: list[int]) -> Optional[bool]:
        return await self._mgr.safe_write_registers(address, values)

    @property
    def is_healthy(self) -> bool:
        return self._mgr.is_healthy

    @property
    def consecutive_failures(self) -> int:
        return self._mgr.consecutive_failures

    @property
    def transport_name(self) -> str:
        return "Modbus TCP"

    @property
    def client(self):
        """Legacy accessor — SafetyGate uses self.modbus.client.connected."""
        return self._mgr.client

    def detect_plc_restart(self, plc_counter: int) -> bool:
        return self._mgr.detect_plc_restart(plc_counter)
