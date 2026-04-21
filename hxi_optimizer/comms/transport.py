"""Protocol-neutral PLC transport interface.

SafetyGate calls methods on a PLCTransport. Concrete implementations:
  - ModbusTransport    (current Modbus TCP via pymodbus)
  - OPCUATransport     (OPC UA via asyncua, port 4840)

Both expose the same API surface so the gate, advisor, and analysis loop
do not need to know which protocol is in use.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class PLCTransport(ABC):
    """Minimum contract every PLC transport must satisfy."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection. Idempotent: safe to call multiple times."""

    @abstractmethod
    async def close(self) -> None:
        """Close connection gracefully."""

    @abstractmethod
    async def safe_read(self, address: int, count: int) -> Optional[list[int]]:
        """Read `count` holding registers starting at `address`.

        Returns a list of unsigned 16-bit integers, or None on any error.
        Never raises — errors are swallowed and counted.
        """

    @abstractmethod
    async def safe_write_registers(self, address: int,
                                   values: list[int]) -> Optional[bool]:
        """Atomic paired write (FC16 semantics).

        Returns True on success, None on failure. Never raises.
        """

    @property
    @abstractmethod
    def is_healthy(self) -> bool:
        """True if connected and recent operations succeeded."""

    @property
    @abstractmethod
    def consecutive_failures(self) -> int:
        """Running count of consecutive failed operations (resets on success)."""

    @property
    @abstractmethod
    def transport_name(self) -> str:
        """Human-readable protocol name, e.g. 'Modbus TCP', 'OPC UA'."""

    def detect_plc_restart(self, plc_counter: int) -> bool:
        """Optional: detect PLC reboot from a counter register. Default no-op."""
        return False


def build_transport(config) -> PLCTransport:
    """Factory — returns the transport specified by config.transport.

    Keeps the switch in one place so main.py, commissioning_tests, and the
    dashboard all resolve the transport the same way.
    """
    name = getattr(config, "transport", "modbus").lower()
    if name == "modbus":
        from hxi_optimizer.comms.modbus_transport import ModbusTransport
        return ModbusTransport(config)
    elif name == "opcua":
        from hxi_optimizer.comms.opcua_transport import OPCUATransport
        return OPCUATransport(config)
    raise ValueError(
        f"Unknown transport {name!r}. Must be 'modbus' or 'opcua'."
    )
