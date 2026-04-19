"""
Modbus TCP Server: The Interface Layer
========================================
SIMULATOR ONLY — DO NOT IMPORT INTO hxi_optimizer/ PRODUCTION CODE.
Default bind address is 127.0.0.1 (loopback) to prevent accidental LAN exposure.

Zero-dependency implementation using only Python stdlib.

Register map matches Section 5.1.1 of the reference document exactly:
  %R6000-6001  FLOAT32  Torque (ft-lb)           [Confirmed]
  %R6002-6003  FLOAT32  RPM                      [Confirmed]
  %R6004-6005  FLOAT32  Pressure (PSI)           [Confirmed]
  %R6006-6007  FLOAT32  Oil Temperature (°F)     [Confirmed]
  %R6008-6009  FLOAT32  Encoder Counts           [Confirmed]
  %R6010-6011  FLOAT32  PID Setpoint             [Estimated]
  %R6012-6013  FLOAT32  PID Error                [Estimated]
  %R6014       INT16    PID Output (% × 100)     [Estimated]
  %R6015       INT16    Operating Mode            [Confirmed]
  %R6016-6017  FLOAT32  Target Torque (ft-lb)    [Estimated]
  %R6018-6019  FLOAT32  Accumulated Turns         [Estimated]
  %R6020       INT16    Fault Code (bitmask)      [Estimated]
  %R6021       INT16    Connection State           [Confirmed]
  %R6022-6023  FLOAT32  Peak Torque (ft-lb)       [Estimated]
  %R6024-6025  FLOAT32  Hookload (klbs)           [Estimated]
  %R6026-6027  FLOAT32  Shoulder Torque (ft-lb)   [Estimated]
  %R6028-6029  FLOAT32  Slope dT/dN (ft-lb/turn) [Estimated]
  %R6030       INT16    Connection Count           [Estimated]

GE CPE305 FLOAT32 encoding: word-swapped (low word at N, high word at N+1).
"""
import struct
import threading
import socketserver
import logging
from typing import Optional

from config import SimConfig, MachineProfile

logger = logging.getLogger(__name__)


class RegisterBank:
    """Thread-safe register storage matching GE CPE305 %R space.

    GE FLOAT32 encoding (word-swapped):
      IEEE 754:  [High Word] [Low Word]
      GE CPE305: [Low Word]  [High Word]
    """

    def __init__(self, size: int = 10000):
        self._registers = [0] * size
        self._lock = threading.Lock()
        self.size = size

    def write_int16(self, address: int, value: int):
        with self._lock:
            if 0 <= address < self.size:
                self._registers[address] = value & 0xFFFF

    def write_int32(self, address: int, value: int):
        with self._lock:
            if 0 <= address < self.size - 1:
                self._registers[address] = (value >> 16) & 0xFFFF
                self._registers[address + 1] = value & 0xFFFF

    def write_float32(self, address: int, value: float):
        packed = struct.pack('>f', value)
        high_word = struct.unpack('>H', packed[0:2])[0]
        low_word = struct.unpack('>H', packed[2:4])[0]
        with self._lock:
            if 0 <= address < self.size - 1:
                self._registers[address] = low_word
                self._registers[address + 1] = high_word

    def read_registers(self, start: int, count: int) -> bytes:
        with self._lock:
            result = bytearray()
            for i in range(count):
                addr = start + i
                if 0 <= addr < self.size:
                    result.extend(struct.pack('>H', self._registers[addr]))
                else:
                    result.extend(b'\x00\x00')
            return bytes(result)


class ModbusRequestHandler(socketserver.BaseRequestHandler):

    def handle(self):
        client_addr = self.client_address
        logger.info(f"Modbus client connected: {client_addr}")
        try:
            while True:
                header = self._recv_exact(7)
                if not header:
                    break
                trans_id, protocol, length, unit_id = struct.unpack('>HHHB', header)
                if protocol != 0:
                    logger.warning(f"Invalid protocol ID: {protocol}")
                    continue
                pdu = self._recv_exact(length - 1)
                if not pdu:
                    break
                fc = pdu[0]
                if fc in (0x03, 0x04):
                    response = self._handle_read_registers(pdu)
                elif fc in (0x06,):
                    response = self._handle_write_single(pdu)
                elif fc in (0x10,):
                    response = self._handle_write_multiple(pdu)
                else:
                    response = bytes([fc | 0x80, 0x01])
                    logger.warning(f"Unsupported FC: 0x{fc:02X}")
                resp_length = len(response) + 1
                resp_header = struct.pack('>HHHB', trans_id, 0, resp_length, unit_id)
                self.request.sendall(resp_header + response)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            logger.info(f"Modbus client disconnected: {client_addr}")

    def _handle_read_registers(self, pdu: bytes) -> bytes:
        fc = pdu[0]
        start_reg, count = struct.unpack('>HH', pdu[1:5])
        if count < 1 or count > 125:
            return bytes([fc | 0x80, 0x03])
        bank: RegisterBank = self.server.register_bank
        data = bank.read_registers(start_reg, count)
        byte_count = count * 2
        return bytes([fc, byte_count]) + data

    def _handle_write_single(self, pdu: bytes) -> bytes:
        """FC06: Write Single Register."""
        fc = pdu[0]
        reg_addr, value = struct.unpack('>HH', pdu[1:5])
        bank: RegisterBank = self.server.register_bank
        bank.write_int16(reg_addr, value)
        return pdu[:5]  # Echo back

    def _handle_write_multiple(self, pdu: bytes) -> bytes:
        """FC16: Write Multiple Registers."""
        fc = pdu[0]
        start_reg, count = struct.unpack('>HH', pdu[1:5])
        byte_count = pdu[5]
        bank: RegisterBank = self.server.register_bank
        for i in range(count):
            offset = 6 + i * 2
            if offset + 2 <= len(pdu):
                value = struct.unpack('>H', pdu[offset:offset+2])[0]
                bank.write_int16(start_reg + i, value)
        return bytes([fc]) + struct.pack('>HH', start_reg, count)

    def _recv_exact(self, n: int) -> Optional[bytes]:
        data = bytearray()
        while len(data) < n:
            try:
                chunk = self.request.recv(n - len(data))
                if not chunk:
                    return None
                data.extend(chunk)
            except (ConnectionResetError, OSError):
                return None
        return bytes(data)


class ThreadedModbusServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class ModbusTCPServer:
    """Public API for the Modbus TCP simulator server.

    Supports per-machine register layouts via MachineProfile.
    Falls back to SimConfig's hardcoded R6000 layout if no profile.

    Usage:
        server = ModbusTCPServer(cfg)
        server.start()
        server.update_from_reading(sensor_reading)
        server.stop()

        # Or with a machine profile:
        server = ModbusTCPServer(cfg, profile=my_profile)
    """

    def __init__(self, cfg: SimConfig, host: str = '127.0.0.1', port: int = 502,
                 profile: Optional[MachineProfile] = None):
        self.cfg = cfg
        self.host = host
        self.port = port
        self.profile = profile or cfg.machine_profile
        self.register_bank = RegisterBank()
        self._server: Optional[ThreadedModbusServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._server = ThreadedModbusServer((self.host, self.port), ModbusRequestHandler)
        self._server.register_bank = self.register_bank
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"Modbus TCP server listening on {self.host}:{self.port}")

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            logger.info("Modbus TCP server stopped")

    def update_from_reading(self, reading) -> None:
        """Write SensorReading into register bank.

        Uses MachineProfile register map if available, otherwise
        falls back to SimConfig's hardcoded R6000 layout.
        """
        cfg = self.cfg
        bank = self.register_bank

        # Mapping from register variable name to (reading attribute, write method)
        # This enables dynamic per-machine register layouts.
        _FLOAT32_MAP = {
            'torque':           ('torque_ftlbs',    cfg.reg_torque),
            'rpm':              ('rpm',             cfg.reg_rpm),
            'pressure':         ('pressure_psi',    cfg.reg_pressure),
            'temperature':      ('oil_temp_f',      cfg.reg_temperature),
            'encoder_counts':   ('encoder_counts',  cfg.reg_encoder_counts),
            'pid_setpoint':     ('pid_setpoint',    cfg.reg_pid_setpoint),
            'pid_error':        ('pid_error',       cfg.reg_pid_error),
            'target_torque':    ('target_torque',   cfg.reg_target_torque),
            'turns':            ('turns',           cfg.reg_turns_count),
            'peak_torque':      ('peak_torque',     cfg.reg_peak_torque),
            'hookload':         ('hookload_klbs',   cfg.reg_hookload),
            'shoulder_torque':  ('shoulder_torque',  cfg.reg_shoulder_torque),
            'slope_dT_dN':      ('slope_dT_dN',     cfg.reg_slope),
        }
        _INT16_MAP = {
            'pid_output':       ('pid_output',       cfg.reg_pid_output, 100),  # scale by 100
            'operating_mode':   ('operating_mode',   cfg.reg_mode, 1),
            'fault_code':       ('fault_code',       cfg.reg_fault_code, 1),
            'connection_state': ('connection_state',  cfg.reg_state, 1),
            'connection_count': ('connection_count',  cfg.reg_connection_count, 1),
        }

        # Write FLOAT32 values
        for var_name, (attr, default_addr) in _FLOAT32_MAP.items():
            addr = default_addr
            if self.profile and var_name in self.profile.reg_map:
                addr = self.profile.reg_map[var_name].address
            value = getattr(reading, attr, 0.0)
            if isinstance(value, int):
                value = float(value)
            bank.write_float32(addr, value)

        # Write INT16 values
        for var_name, (attr, default_addr, scale) in _INT16_MAP.items():
            addr = default_addr
            if self.profile and var_name in self.profile.reg_map:
                addr = self.profile.reg_map[var_name].address
            value = getattr(reading, attr, 0)
            bank.write_int16(addr, int(value * scale))
