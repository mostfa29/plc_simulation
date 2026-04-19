"""HXI-compatible Modbus simulator on 127.0.0.1:5020 — pure stdlib.

Implements the GE CPE305 register map at the addresses the optimizer reads
(%R06600..%R06700, pymodbus 0-indexed = 6599..6699). Just enough behaviour
for end-to-end local testing:

  - FC03 (read holding registers): returns in-memory register bank
  - FC16 (write multiple registers): echoes the value into the bank AND
    mirrors %R06603/%R06604 (request) into %R06610/%R06611 (active readback)
    so SafetyGate's readback verification step passes
  - Background thread "drives" RPM toward swash output so the optimizer
    sees realistic plant dynamics

Pure stdlib — no pymodbus dependency, so it won't break across pymodbus versions.

Usage:
    python -m local_test.sim_plc                    # default: 127.0.0.1:5020
    python -m local_test.sim_plc --port 5021
"""
from __future__ import annotations

import argparse
import logging
import random
import socketserver
import struct
import threading
import time

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)-14s %(levelname)-7s %(message)s")
logger = logging.getLogger("sim_plc")

# Register layout (pymodbus 0-indexed)
ADDR_RPM_ENCODER     = 6599   # %R06600..6601 REAL
ADDR_SWASH_OUTPUT    = 6601   # %R06602
ADDR_SWASH_LOWER_REQ = 6602   # %R06603 — written by optimizer
ADDR_SWASH_UPPER_REQ = 6603   # %R06604 — written by optimizer
ADDR_HEARTBEAT       = 6604   # %R06605 — written by optimizer
ADDR_ACTIVE_LOWER    = 6609   # %R06610 — readback target
ADDR_ACTIVE_UPPER    = 6610   # %R06611 — readback target
ADDR_STATUS_WORD     = 6612   # %R06613
ADDR_SS_SETPOINT_FWD = 6615   # %R06616..6617 REAL
ADDR_DELIVERED_TQ    = 6645   # %R06646..6647 REAL
ADDR_LOOP_TEMP       = 6669   # %R06670..6671 REAL
ADDR_ESD_WORD        = 6664   # %R06665 (bit 0 = ESD per spec)
ADDR_BUMP_FLAG_FWD   = 6626   # %R06627
ADDR_BUMP_FLAG_REV   = 6627   # %R06628


class RegisterBank:
    """Thread-safe holding-register bank (10,000 registers)."""

    def __init__(self, size: int = 10000) -> None:
        self._regs = [0] * size
        self._lock = threading.Lock()

    def read(self, start: int, count: int) -> list[int]:
        with self._lock:
            return list(self._regs[start: start + count])

    def write(self, start: int, values: list[int]) -> None:
        with self._lock:
            for i, v in enumerate(values):
                self._regs[start + i] = v & 0xFFFF

    def write_word(self, addr: int, value: int) -> None:
        self.write(addr, [value])

    def write_real_abcd(self, addr: int, value: float) -> None:
        """Encode float as ABCD (big-endian, high word first) per MASTER_CONTEXT §3.4."""
        raw = struct.pack(">f", value)
        hi, lo = struct.unpack(">HH", raw)
        self.write(addr, [hi, lo])

    def read_real_abcd(self, addr: int) -> float:
        regs = self.read(addr, 2)
        try:
            return struct.unpack(">f", struct.pack(">HH", regs[0], regs[1]))[0]
        except Exception:
            return 0.0


def seed_bank(bank: RegisterBank) -> None:
    """Pre-populate registers with sensible operating values."""
    bank.write_real_abcd(ADDR_RPM_ENCODER, 60.0)
    bank.write_word(ADDR_SWASH_OUTPUT, 500)
    bank.write_word(ADDR_SWASH_LOWER_REQ, 400)
    bank.write_word(ADDR_SWASH_UPPER_REQ, 600)
    bank.write_word(ADDR_HEARTBEAT, 0)
    bank.write_word(ADDR_ACTIVE_LOWER, 400)
    bank.write_word(ADDR_ACTIVE_UPPER, 600)
    bank.write_word(ADDR_STATUS_WORD, 0x0001)
    bank.write_real_abcd(ADDR_SS_SETPOINT_FWD, 60.0)
    bank.write_real_abcd(ADDR_DELIVERED_TQ, 1500.0)
    bank.write_real_abcd(ADDR_LOOP_TEMP, 55.0)
    bank.write_word(ADDR_ESD_WORD, 0)


def dynamics_loop(bank: RegisterBank, period: float = 0.5) -> None:
    """Background loop — advances the simulated plant every 500 ms."""
    while True:
        time.sleep(period)
        try:
            sw = bank.read(ADDR_SWASH_OUTPUT, 1)[0]
            current_rpm = bank.read_real_abcd(ADDR_RPM_ENCODER)
            target = (sw - 500) / 500.0 * 200.0
            new_rpm = current_rpm + 0.15 * (target - current_rpm) + random.gauss(0, 0.5)
            bank.write_real_abcd(ADDR_RPM_ENCODER, new_rpm)
            temp = bank.read_real_abcd(ADDR_LOOP_TEMP)
            bank.write_real_abcd(ADDR_LOOP_TEMP, temp + random.gauss(0, 0.02))
        except Exception as e:
            logger.debug(f"Dynamics tick error: {e}")


# ─── Modbus TCP wire protocol ────────────────────────────────────────────────


def _ex_response(transaction_id: int, unit_id: int, fc: int, ex_code: int) -> bytes:
    payload = bytes([unit_id, fc | 0x80, ex_code])
    return struct.pack(">HHH", transaction_id, 0, len(payload)) + payload


def handle_pdu(bank: RegisterBank, transaction_id: int, unit_id: int,
               pdu: bytes) -> bytes:
    """Process one Modbus PDU and return the response bytes (with MBAP header)."""
    fc = pdu[0]
    if fc == 0x03:  # Read Holding Registers
        if len(pdu) != 5:
            return _ex_response(transaction_id, unit_id, fc, 0x03)
        addr, count = struct.unpack(">HH", pdu[1:5])
        if count < 1 or count > 125 or addr + count > 10000:
            return _ex_response(transaction_id, unit_id, fc, 0x02)
        regs = bank.read(addr, count)
        data = b"".join(struct.pack(">H", r) for r in regs)
        payload = bytes([unit_id, fc, len(data)]) + data
        return struct.pack(">HHH", transaction_id, 0, len(payload)) + payload

    elif fc == 0x10:  # Write Multiple Registers (FC16)
        if len(pdu) < 6:
            return _ex_response(transaction_id, unit_id, fc, 0x03)
        addr, count, byte_count = struct.unpack(">HHB", pdu[1:6])
        if len(pdu) != 6 + byte_count or byte_count != count * 2:
            return _ex_response(transaction_id, unit_id, fc, 0x03)
        values = list(struct.unpack(f">{count}H", pdu[6:]))
        bank.write(addr, values)
        # Mirror swash bound writes to active readback registers
        if addr == ADDR_SWASH_LOWER_REQ:
            if len(values) >= 1:
                bank.write_word(ADDR_ACTIVE_LOWER, values[0])
            if len(values) >= 2:
                bank.write_word(ADDR_ACTIVE_UPPER, values[1])
        elif addr == ADDR_SWASH_UPPER_REQ and len(values) >= 1:
            bank.write_word(ADDR_ACTIVE_UPPER, values[0])
        payload = bytes([unit_id, fc]) + struct.pack(">HH", addr, count)
        return struct.pack(">HHH", transaction_id, 0, len(payload)) + payload

    elif fc == 0x06:  # Write Single Register
        if len(pdu) != 5:
            return _ex_response(transaction_id, unit_id, fc, 0x03)
        addr, value = struct.unpack(">HH", pdu[1:5])
        bank.write(addr, [value])
        payload = bytes([unit_id, fc]) + struct.pack(">HH", addr, value)
        return struct.pack(">HHH", transaction_id, 0, len(payload)) + payload

    return _ex_response(transaction_id, unit_id, fc, 0x01)  # illegal function


class ModbusHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        bank: RegisterBank = self.server.bank  # type: ignore[attr-defined]
        client = self.client_address
        logger.info(f"Client connected from {client}")
        try:
            while True:
                header = self._recv_n(7)
                if not header:
                    break
                transaction_id, _proto, length = struct.unpack(">HHH", header[:6])
                unit_id = header[6]
                pdu = self._recv_n(length - 1)
                if not pdu:
                    break
                response = handle_pdu(bank, transaction_id, unit_id, pdu)
                self.request.sendall(response)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        logger.info(f"Client {client} disconnected")

    def _recv_n(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.request.recv(n - len(buf))
            if not chunk:
                return b""
            buf += chunk
        return buf


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    p = argparse.ArgumentParser(description="HXI Modbus simulator (local test only)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5020)
    args = p.parse_args()

    bank = RegisterBank()
    seed_bank(bank)

    server = ThreadedTCPServer((args.host, args.port), ModbusHandler)
    server.bank = bank  # type: ignore[attr-defined]

    threading.Thread(target=dynamics_loop, args=(bank,), daemon=True).start()

    logger.info(f"HXI sim PLC listening on {args.host}:{args.port}")
    logger.info("Pre-loaded RPM=60.0 bounds=[400,600] setpoint=60.0 temp=55C")
    logger.info("Press Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        server.shutdown()


if __name__ == "__main__":
    main()
