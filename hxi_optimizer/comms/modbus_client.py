"""pymodbus 3.13.x AsyncModbusTcpClient wrapper.

Per MASTER_CONTEXT §3.3, §3.5. Only FC03 reads and FC16 writes are exposed.
FC06 (write_register) is intentionally NOT wrapped — paired-register safety
requires atomic FC16 writes, and exposing FC06 would create a back door.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pymodbus.client import AsyncModbusTcpClient
from pymodbus import FramerType
from pymodbus.exceptions import ConnectionException, ModbusIOException

logger = logging.getLogger("modbus_client")


def create_client(host: str, port: int = 502) -> AsyncModbusTcpClient:
    """Create a configured AsyncModbusTcpClient per spec §3.3."""
    return AsyncModbusTcpClient(
        host=host,
        port=port,
        framer=FramerType.SOCKET,
        timeout=2.0,
        retries=3,
        reconnect_delay=1.0,
        reconnect_delay_max=30.0,
    )


class ModbusManager:
    """Connection lifecycle + safe read/write with VPN-aware reconnection."""

    def __init__(self, config) -> None:
        self.config = config
        self.client = create_client(config.plc_host, config.plc_port)
        self.consecutive_failures = 0
        self.last_plc_counter: Optional[int] = None
        self.connection_healthy = False

    async def connect(self) -> None:
        await self.client.connect()
        self.connection_healthy = self.client.connected

    async def safe_read(self, address: int, count: int) -> Optional[list[int]]:
        """Read holding registers; return None on any error (never raises)."""
        try:
            result = await asyncio.wait_for(
                self.client.read_holding_registers(
                    address=address, count=count, device_id=self.config.unit_id
                ),
                timeout=self.config.modbus_timeout,
            )
            if result.isError():
                self.consecutive_failures += 1
                self.connection_healthy = False
                return None
            self.consecutive_failures = 0
            self.connection_healthy = True
            return list(result.registers)
        except (asyncio.TimeoutError, ConnectionException, ModbusIOException) as e:
            self.consecutive_failures += 1
            self.connection_healthy = False
            if self.consecutive_failures % 3 == 1:
                logger.warning(
                    f"Modbus read error (attempt {self.consecutive_failures}): {e}"
                )
            return None

    async def safe_write_registers(self, address: int,
                                   values: list[int]) -> Optional[bool]:
        """FC16 paired write. Returns True on success, None on failure.

        Atomic by protocol: a single FC16 PDU carries multiple registers; the
        PLC commits them in one scan. Never use FC06 for the swash bounds.
        """
        try:
            result = await asyncio.wait_for(
                self.client.write_registers(
                    address=address, values=values, device_id=self.config.unit_id
                ),
                timeout=self.config.modbus_timeout,
            )
            if result.isError():
                self.consecutive_failures += 1
                self.connection_healthy = False
                logger.error(f"FC16 write returned error at addr {address}: {result}")
                return None
            self.consecutive_failures = 0
            self.connection_healthy = True
            return True
        except (asyncio.TimeoutError, ConnectionException, ModbusIOException) as e:
            self.consecutive_failures += 1
            self.connection_healthy = False
            logger.error(f"FC16 write exception at addr {address}: {e}")
            return None

    def detect_plc_restart(self, plc_counter: int) -> bool:
        if self.last_plc_counter is None:
            self.last_plc_counter = plc_counter
            return False
        restarted = plc_counter < self.last_plc_counter - 100
        self.last_plc_counter = plc_counter
        return restarted

    @property
    def is_healthy(self) -> bool:
        return self.client.connected and self.consecutive_failures < 3
