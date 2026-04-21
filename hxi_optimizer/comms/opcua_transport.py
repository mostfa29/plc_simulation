"""OPC UA transport — asyncua client wrapped in the Modbus-compatible PLCTransport interface.

GE PACSystems RX3i with firmware >= 8.20 exposes an OPC UA server on port 4840.
This transport maps Modbus register addresses (%R06600..) to OPC UA NodeIds
via a JSON map discovered at commissioning time.

Values are always exchanged as 16-bit words (matching Modbus semantics):
  - INT  nodes: single uint16
  - REAL nodes: two uint16 (ABCD / big-endian, per MASTER_CONTEXT §3.4) so
               the existing FLOAT32 gate in register_map.decode_float32_ge
               keeps working unchanged.

Connection resilience:
  - asyncua has built-in session keepalive + reconnect, but we add our own
    consecutive-failure counter + safe wrappers so the SafetyGate's
    is_healthy check works identically to the Modbus path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
from pathlib import Path
from typing import Optional

from hxi_optimizer.comms.transport import PLCTransport

logger = logging.getLogger("opcua_transport")


class _FakeClient:
    """Placeholder that mimics ModbusManager's `client.connected` attribute
    for legacy code (dashboard snapshot etc.) that reads mgr.client.connected."""

    def __init__(self, parent: "OPCUATransport") -> None:
        self._parent = parent

    @property
    def connected(self) -> bool:
        return self._parent.connection_healthy


class OPCUATransport(PLCTransport):
    """OPC UA → Modbus-compatible register interface.

    Requires config.opcua_node_map_file to point at a JSON file mapping
    pymodbus 0-indexed addresses to OPC UA NodeIds. Example:
        {"6599": "ns=4;s=RPM_Encoder", "6602": "ns=4;s=Swash_Output"}
    """

    def __init__(self, config) -> None:
        self.config = config
        self._url = f"opc.tcp://{config.plc_host}:{getattr(config, 'opcua_port', 4840)}"
        self._timeout = float(getattr(config, "modbus_timeout", 2.0))
        self._lock = asyncio.Lock()
        self._client = None  # lazy asyncua.Client
        self.connection_healthy = False
        self.consecutive_failures_count = 0
        self.last_plc_counter: Optional[int] = None

        # Load node map
        map_path = Path(getattr(config, "opcua_node_map_file",
                                "hxi_optimizer/comms/opcua_nodes.json"))
        if map_path.exists():
            self._node_map: dict[int, str] = {}
            for k, v in json.loads(map_path.read_text()).items():
                if k.startswith("_"):
                    continue  # skip metadata / comment keys
                try:
                    self._node_map[int(k)] = v
                except ValueError:
                    logger.warning(f"Skipping non-numeric node map key: {k!r}")
        else:
            logger.warning(f"OPC UA node map not found at {map_path}; "
                           f"using empty map (every read will fail)")
            self._node_map = {}

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    async def connect(self) -> None:
        from asyncua import Client  # lazy import — only when OPC UA is in use
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self._client = Client(url=self._url, timeout=self._timeout)
        # Optional security — only wire up if config requests it
        security = getattr(self.config, "opcua_security", "None")
        user = getattr(self.config, "opcua_username", None)
        pw = getattr(self.config, "opcua_password", None)
        cert = getattr(self.config, "opcua_cert_path", None)
        if user:
            self._client.set_user(user)
            if pw:
                self._client.set_password(pw)
        if security and security != "None" and cert:
            await self._client.set_security_string(
                f"{security},SignAndEncrypt,{cert},{cert}"
            )
        try:
            await self._client.connect()
            self.connection_healthy = True
            self.consecutive_failures_count = 0
            logger.info(f"OPC UA connected to {self._url}")
        except Exception as e:
            self.connection_healthy = False
            self.consecutive_failures_count += 1
            logger.error(f"OPC UA connect failed: {e}")

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
            self.connection_healthy = False

    # ─── Read / Write ───────────────────────────────────────────────────────

    async def safe_read(self, address: int, count: int) -> Optional[list[int]]:
        """Read `count` 16-bit words starting at `address`.

        For REAL types, two consecutive words are read from the REAL node
        and unpacked as ABCD. For INT/WORD types, the node value is cast
        to uint16.
        """
        if not self._client or not self.connection_healthy:
            self.consecutive_failures_count += 1
            return None
        try:
            out: list[int] = []
            i = 0
            while i < count:
                abs_addr = address + i
                node_id = self._node_map.get(abs_addr)
                if node_id is None:
                    # Gap in the map — return 0 for this word so block reads
                    # still return a list of length `count`.
                    out.append(0)
                    i += 1
                    continue
                node = self._client.get_node(node_id)
                value = await asyncio.wait_for(node.read_value(), timeout=self._timeout)
                hi, lo, consumed = _value_to_words(value)
                out.append(hi)
                if consumed == 2 and i + 1 < count:
                    out.append(lo)
                    i += 2
                else:
                    i += 1
            self.connection_healthy = True
            self.consecutive_failures_count = 0
            return out
        except (asyncio.TimeoutError, OSError, ConnectionError) as e:
            self.consecutive_failures_count += 1
            self.connection_healthy = False
            if self.consecutive_failures_count % 3 == 1:
                logger.warning(
                    f"OPC UA read error (attempt {self.consecutive_failures_count}): {e}"
                )
            return None
        except Exception as e:
            self.consecutive_failures_count += 1
            logger.error(f"OPC UA read unexpected error: {e}")
            return None

    async def safe_write_registers(self, address: int,
                                   values: list[int]) -> Optional[bool]:
        """Atomic multi-register write.

        For the swash bounds pair (%R06603 + %R06604) the OPC UA server does
        not guarantee PDU-level atomicity the way Modbus FC16 does. We
        perform both writes inside a single asyncua write request to get
        the closest equivalent, and then a readback confirms.

        SafetyGate does its own readback verification after this returns,
        so any cross-fault will be caught and rolled back.
        """
        if not self._client or not self.connection_healthy:
            self.consecutive_failures_count += 1
            return None
        try:
            from asyncua import ua
            nodes = []
            datavalues = []
            for i, v in enumerate(values):
                node_id = self._node_map.get(address + i)
                if node_id is None:
                    logger.error(f"No OPC UA node mapped for address {address + i}")
                    self.consecutive_failures_count += 1
                    return None
                nodes.append(self._client.get_node(node_id))
                # PLC expects signed int16 for the swash bounds (INT16 in GE)
                signed = v if v < 0x8000 else v - 0x10000
                datavalues.append(ua.DataValue(ua.Variant(signed, ua.VariantType.Int16)))
            # Batched write — asyncua sends this as a single WriteRequest
            await asyncio.wait_for(
                self._client.write_values(nodes, [dv.Value.Value for dv in datavalues]),
                timeout=self._timeout,
            )
            self.connection_healthy = True
            self.consecutive_failures_count = 0
            return True
        except (asyncio.TimeoutError, OSError, ConnectionError) as e:
            self.consecutive_failures_count += 1
            self.connection_healthy = False
            logger.error(f"OPC UA write error at {address}: {e}")
            return None
        except Exception as e:
            self.consecutive_failures_count += 1
            logger.error(f"OPC UA write unexpected error: {e}")
            return None

    # ─── Health ─────────────────────────────────────────────────────────────

    @property
    def is_healthy(self) -> bool:
        return self.connection_healthy and self.consecutive_failures_count < 3

    @property
    def consecutive_failures(self) -> int:
        return self.consecutive_failures_count

    @property
    def transport_name(self) -> str:
        return "OPC UA"

    @property
    def client(self):
        """Compatibility shim — dashboard reads .client.connected."""
        return _FakeClient(self)

    def detect_plc_restart(self, plc_counter: int) -> bool:
        if self.last_plc_counter is None:
            self.last_plc_counter = plc_counter
            return False
        restarted = plc_counter < self.last_plc_counter - 100
        self.last_plc_counter = plc_counter
        return restarted


# ─── helpers ────────────────────────────────────────────────────────────────

def _value_to_words(value) -> tuple[int, int, int]:
    """Convert an OPC UA scalar to (high_word, low_word, words_consumed).

    - float → 2 words ABCD (consumed=2)
    - int/bool → 1 word (consumed=1, low_word=0 filler)
    """
    if isinstance(value, bool):
        return (1 if value else 0, 0, 1)
    if isinstance(value, float):
        raw = struct.pack(">f", value)
        hi, lo = struct.unpack(">HH", raw)
        return (hi, lo, 2)
    if isinstance(value, int):
        # Treat as 16-bit signed wrap → unsigned word
        v = value & 0xFFFF if value >= 0 else (value + 0x10000) & 0xFFFF
        return (v, 0, 1)
    # Unknown type — log and return 0
    logger.warning(f"Unexpected OPC UA value type: {type(value).__name__}")
    return (0, 0, 1)
