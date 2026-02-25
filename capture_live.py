#!/usr/bin/env python3
"""
Live Modbus TCP Data Capture
==============================
Connects to PLC via eWon VPN, reads registers at configurable rate,
logs timestamped sensor data to CSV in real-time.

Register map matches GE CPE305 layout (Section 5.1.1):
  %R6000-6030  — All sensor channels (block read)

GE FLOAT32 encoding: word-swapped (low word at N, high word at N+1).

Usage:
  # Discover active registers (scan 0-200)
  python capture_live.py --host 10.0.0.1 --discover

  # Capture at 10 Hz (default), auto-segment connections
  python capture_live.py --host 10.0.0.1

  # Capture at 20 Hz, custom output dir
  python capture_live.py --host 10.0.0.1 --hz 20 --output ./captures

  # Capture with custom register range
  python capture_live.py --host 10.0.0.1 --reg-start 6000 --reg-count 31
"""
import argparse
import csv
import json
import os
import signal
import socket
import struct
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import MachineProfile, DEFAULT_REG_MAP


# ═══════════════════════════════════════════════════════════════════
# Register Map — GE CPE305 / Section 5.1.1
# ═══════════════════════════════════════════════════════════════════

# Default register layout (configurable via --register-map JSON or --profile YAML)
DEFAULT_REGISTER_MAP = {
    "torque_ftlbs":     {"addr": 6000, "type": "FLOAT32"},
    "rpm":              {"addr": 6002, "type": "FLOAT32"},
    "pressure_psi":     {"addr": 6004, "type": "FLOAT32"},
    "oil_temp_f":       {"addr": 6006, "type": "FLOAT32"},
    "encoder_counts":   {"addr": 6008, "type": "FLOAT32"},
    "pid_setpoint":     {"addr": 6010, "type": "FLOAT32"},
    "pid_error":        {"addr": 6012, "type": "FLOAT32"},
    "pid_output":       {"addr": 6014, "type": "INT16"},
    "operating_mode":   {"addr": 6015, "type": "INT16"},
    "target_torque":    {"addr": 6016, "type": "FLOAT32"},
    "turns":            {"addr": 6018, "type": "FLOAT32"},
    "fault_code":       {"addr": 6020, "type": "INT16"},
    "connection_state": {"addr": 6021, "type": "INT16"},
    "peak_torque":      {"addr": 6022, "type": "FLOAT32"},
    "hookload_klbs":    {"addr": 6024, "type": "FLOAT32"},
    "shoulder_torque":  {"addr": 6026, "type": "FLOAT32"},
    "slope_dT_dN":      {"addr": 6028, "type": "FLOAT32"},
    "connection_count": {"addr": 6030, "type": "INT16"},
}


def reg_map_from_profile(profile: MachineProfile) -> Tuple[Dict, bool]:
    """Convert a MachineProfile's reg_map into capture_live's format.

    Maps profile register names to the CSV column names used by the
    capture pipeline. Returns (reg_map, word_swap) tuple.
    """
    # Map profile var names -> capture CSV column names
    name_map = {
        'torque': 'torque_ftlbs',
        'rpm': 'rpm',
        'pressure': 'pressure_psi',
        'temperature': 'oil_temp_f',
        'encoder_counts': 'encoder_counts',
        'pid_setpoint': 'pid_setpoint',
        'pid_error': 'pid_error',
        'pid_output': 'pid_output',
        'operating_mode': 'operating_mode',
        'target_torque': 'target_torque',
        'turns': 'turns',
        'fault_code': 'fault_code',
        'connection_state': 'connection_state',
        'peak_torque': 'peak_torque',
        'hookload': 'hookload_klbs',
        'shoulder_torque': 'shoulder_torque',
        'slope_dT_dN': 'slope_dT_dN',
        'connection_count': 'connection_count',
    }
    result = {}
    for prof_name, rdef in profile.reg_map.items():
        # Skip placeholder/TBD registers (address=0 with unconfirmed description)
        if rdef.address == 0 and ('TBD' in rdef.description or 'tbd' in rdef.description
                                   or not rdef.description):
            continue
        csv_name = name_map.get(prof_name, prof_name)
        entry = {
            'addr': rdef.address,
            'type': rdef.data_type,
        }
        if rdef.scale != 1.0:
            entry['scale'] = rdef.scale
        if rdef.offset != 0.0:
            entry['offset'] = rdef.offset
        result[csv_name] = entry
    return result, profile.word_swap


def compute_block_reads(reg_map: Dict, max_gap: int = 20) -> List[Tuple[int, int]]:
    """Compute optimal Modbus block reads for non-contiguous register maps.

    Groups registers into contiguous blocks where gaps <= max_gap registers.
    Returns list of (start_addr, count) tuples for block reads.
    """
    all_addrs = []
    for info in reg_map.values():
        addr = info['addr']
        size = 2 if info['type'] in ('FLOAT32', 'INT32', 'UINT32') else 1
        all_addrs.append(addr)
        if size == 2:
            all_addrs.append(addr + 1)

    if not all_addrs:
        return []

    all_addrs = sorted(set(all_addrs))
    blocks = []
    block_start = all_addrs[0]
    block_end = all_addrs[0]

    for addr in all_addrs[1:]:
        if addr - block_end <= max_gap:
            block_end = addr
        else:
            blocks.append((block_start, block_end - block_start + 1))
            block_start = addr
            block_end = addr

    blocks.append((block_start, block_end - block_start + 1))

    # Ensure no block exceeds 125 registers (Modbus limit)
    final_blocks = []
    for start, count in blocks:
        while count > 125:
            final_blocks.append((start, 125))
            start += 125
            count -= 125
        if count > 0:
            final_blocks.append((start, count))

    return final_blocks

# Columns we write to CSV (order matters for downstream pipeline)
CSV_COLUMNS = [
    "timestamp", "elapsed_s",
    "torque_ftlbs", "rpm", "pressure_psi", "oil_temp_f",
    "encoder_counts", "turns", "hookload_klbs",
    "target_torque", "fault_code", "connection_state",
    "operating_mode", "peak_torque", "shoulder_torque",
    "slope_dT_dN", "connection_count",
    "pid_setpoint", "pid_error", "pid_output",
]


# ═══════════════════════════════════════════════════════════════════
# Raw Modbus TCP Client (no pymodbus dependency)
# ═══════════════════════════════════════════════════════════════════

class ModbusTCPClient:
    """Minimal Modbus TCP client — FC03 (Read Holding Registers) only.

    Matches the protocol implementation in modbus_server.py exactly.
    No external dependencies — just socket + struct.
    """

    def __init__(self, host: str, port: int = 502, timeout: float = 5.0,
                 unit_id: int = 1):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.unit_id = unit_id
        self._sock: Optional[socket.socket] = None
        self._trans_id = 0
        self._lock = threading.Lock()

    def connect(self) -> bool:
        """Connect to Modbus TCP server. Returns True on success."""
        try:
            self.close()
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self.timeout)
            self._sock.connect((self.host, self.port))
            return True
        except (socket.error, OSError) as e:
            print(f"  [CONN] Failed to connect to {self.host}:{self.port}: {e}")
            self._sock = None
            return False

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def read_holding_registers(self, start: int, count: int
                                ) -> Optional[List[int]]:
        """FC03: Read Holding Registers. Returns list of 16-bit register values."""
        if not self._sock:
            return None

        with self._lock:
            self._trans_id = (self._trans_id + 1) & 0xFFFF
            tid = self._trans_id

            # MBAP Header (7 bytes) + PDU (5 bytes)
            # Transaction ID (2) | Protocol (2) | Length (2) | Unit (1)
            # FC (1) | Start (2) | Count (2)
            pdu = struct.pack('>BHH', 0x03, start, count)
            mbap = struct.pack('>HHHB', tid, 0, len(pdu) + 1, self.unit_id)

            try:
                self._sock.sendall(mbap + pdu)
                # Read response MBAP header
                header = self._recv_exact(7)
                if not header:
                    return None
                resp_tid, proto, length, unit = struct.unpack('>HHHB', header)
                # Read response PDU
                resp_pdu = self._recv_exact(length - 1)
                if not resp_pdu:
                    return None

                fc = resp_pdu[0]
                if fc & 0x80:  # Error response
                    exc_code = resp_pdu[1] if len(resp_pdu) > 1 else 0
                    print(f"  [MODBUS] Exception FC=0x{fc:02X} code={exc_code}")
                    return None

                byte_count = resp_pdu[1]
                data = resp_pdu[2:2 + byte_count]

                # Parse 16-bit register values
                regs = []
                for i in range(0, len(data), 2):
                    regs.append(struct.unpack('>H', data[i:i+2])[0])
                return regs

            except (socket.timeout, socket.error, OSError, struct.error) as e:
                print(f"  [MODBUS] Read error: {e}")
                self._sock = None
                return None

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Receive exactly n bytes."""
        data = bytearray()
        while len(data) < n:
            try:
                chunk = self._sock.recv(n - len(data))
                if not chunk:
                    return None
                data.extend(chunk)
            except (socket.timeout, socket.error, OSError):
                return None
        return bytes(data)


# ═══════════════════════════════════════════════════════════════════
# Register Value Decoding (FLOAT32 / INT32 / INT16)
# ═══════════════════════════════════════════════════════════════════

def decode_float32(word_a: int, word_b: int, word_swap: bool = True) -> float:
    """Decode 32-bit float from two consecutive 16-bit registers.

    Args:
        word_a: Register at address N.
        word_b: Register at address N+1.
        word_swap: True = GE convention (low word at N, high word at N+1).
                   False = standard IEEE (high word at N, low word at N+1).
    """
    if word_swap:
        packed = struct.pack('>HH', word_b, word_a)   # GE: swap back
    else:
        packed = struct.pack('>HH', word_a, word_b)    # IEEE: as-is
    return struct.unpack('>f', packed)[0]


# Keep old name as alias for backward compatibility
decode_ge_float32 = decode_float32


def decode_int32(word_a: int, word_b: int, word_swap: bool = False,
                 signed: bool = True) -> int:
    """Decode 32-bit integer from two consecutive 16-bit registers.

    NOTE: word_swap defaults to False because GE PLCs only word-swap
    FLOAT32 values. INT32 always uses standard order (MSW at N, LSW at N+1)
    even on GE hardware. Pass word_swap=True only for non-standard PLCs
    that swap integer word order.
    """
    if word_swap:
        packed = struct.pack('>HH', word_b, word_a)
    else:
        packed = struct.pack('>HH', word_a, word_b)
    fmt = '>i' if signed else '>I'
    return struct.unpack(fmt, packed)[0]


def decode_int16_signed(value: int) -> int:
    """Decode unsigned 16-bit to signed."""
    if value >= 0x8000:
        return value - 0x10000
    return value


# ═══════════════════════════════════════════════════════════════════
# Register Decoder
# ═══════════════════════════════════════════════════════════════════

def decode_registers(raw_regs: List[int], reg_start: int,
                     reg_map: Dict, word_swap: bool = True) -> Dict[str, float]:
    """Decode raw register array into named values using register map."""
    values = {}
    for name, info in reg_map.items():
        addr = info["addr"]
        rtype = info["type"]
        offset = addr - reg_start

        if offset < 0 or offset >= len(raw_regs):
            values[name] = 0.0
            continue

        if rtype == "FLOAT32":
            if offset + 1 >= len(raw_regs):
                values[name] = 0.0
                continue
            val = decode_float32(raw_regs[offset], raw_regs[offset + 1],
                                 word_swap)
        elif rtype in ("INT32", "UINT32"):
            if offset + 1 >= len(raw_regs):
                values[name] = 0.0
                continue
            # INT32 uses standard word order (MSW@N) even on GE PLCs
            val = float(decode_int32(raw_regs[offset], raw_regs[offset + 1],
                                     word_swap=False,
                                     signed=(rtype == "INT32")))
        elif rtype == "INT16":
            val = float(raw_regs[offset])
        else:
            val = float(raw_regs[offset])

        # Apply scale and offset from profile (e.g. hookload lbs -> klbs)
        val = val * info.get("scale", 1.0) + info.get("offset", 0.0)
        values[name] = val

    return values


def decode_registers_from_dict(reg_dict: Dict[int, int],
                                reg_map: Dict,
                                word_swap: bool = True) -> Dict[str, float]:
    """Decode named values from a register address->value dict.

    Used for multi-block reads where registers are non-contiguous.
    Each block read populates reg_dict with {address: raw_value}.
    """
    values = {}
    for name, info in reg_map.items():
        addr = info["addr"]
        rtype = info["type"]

        if rtype == "FLOAT32":
            word_a = reg_dict.get(addr, 0)
            word_b = reg_dict.get(addr + 1, 0)
            val = decode_float32(word_a, word_b, word_swap)
        elif rtype in ("INT32", "UINT32"):
            word_a = reg_dict.get(addr, 0)
            word_b = reg_dict.get(addr + 1, 0)
            # INT32 uses standard word order (MSW@N) even on GE PLCs
            val = float(decode_int32(word_a, word_b, word_swap=False,
                                     signed=(rtype == "INT32")))
        elif rtype == "INT16":
            val = float(reg_dict.get(addr, 0))
        else:
            val = float(reg_dict.get(addr, 0))

        # Apply scale and offset from profile
        val = val * info.get("scale", 1.0) + info.get("offset", 0.0)
        values[name] = val

    return values


# ═══════════════════════════════════════════════════════════════════
# Discovery Mode
# ═══════════════════════════════════════════════════════════════════

def discover_registers(client: ModbusTCPClient, scan_start: int = 0,
                       scan_end: int = 200, block_size: int = 10):
    """Scan register range to find active (non-zero) registers."""
    print(f"\n{'='*60}")
    print(f"  REGISTER DISCOVERY: scanning {scan_start}-{scan_end}")
    print(f"{'='*60}\n")

    active = []
    addr = scan_start
    while addr < scan_end:
        count = min(block_size, scan_end - addr)
        regs = client.read_holding_registers(addr, count)
        if regs is None:
            print(f"  [{addr:5d}-{addr+count-1:5d}] READ FAILED")
            addr += count
            continue

        for i, val in enumerate(regs):
            if val != 0:
                reg_addr = addr + i
                active.append((reg_addr, val))
                # Try to decode as FLOAT32 pair (both word orders)
                if i + 1 < len(regs) and regs[i + 1] != 0:
                    f32_ge = decode_float32(val, regs[i + 1], word_swap=True)
                    f32_std = decode_float32(val, regs[i + 1], word_swap=False)
                    i32 = decode_int32(val, regs[i + 1], word_swap=True)
                    print(f"  R{reg_addr:5d} = 0x{val:04X} "
                          f"(raw={val}, GE_f32={f32_ge:.4f}, "
                          f"STD_f32={f32_std:.4f}, i32={i32})")
                else:
                    print(f"  R{reg_addr:5d} = 0x{val:04X} "
                          f"(raw={val}, int16s={decode_int16_signed(val)})")
        addr += count
        time.sleep(0.05)  # Rate limit over VPN

    print(f"\n  Found {len(active)} active registers")
    return active


# ═══════════════════════════════════════════════════════════════════
# Connection Segmenter
# ═══════════════════════════════════════════════════════════════════

class ConnectionSegmenter:
    """Detects makeup connection boundaries from sensor data.

    A connection starts when:
      - RPM goes above threshold OR torque goes above threshold
      - connection_state transitions from IDLE (0) to active

    A connection ends when:
      - connection_state returns to IDLE/COMPLETE
      - RPM drops to 0 and torque drops for sustained period
    """

    # Connection states
    IDLE = 0
    COMPLETE = 9
    ACTIVE_STATES = {1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13}

    def __init__(self, rpm_threshold: float = 1.0, torque_threshold: float = 50.0,
                 idle_timeout_s: float = 5.0):
        self.rpm_threshold = rpm_threshold
        self.torque_threshold = torque_threshold
        self.idle_timeout_s = idle_timeout_s
        self.in_connection = False
        self.connection_num = 0
        self._idle_since: Optional[float] = None

    def update(self, values: Dict[str, float], timestamp: float) -> Optional[str]:
        """Returns event string if connection boundary detected, else None."""
        rpm = values.get("rpm", 0)
        torque = values.get("torque_ftlbs", 0)
        state = int(values.get("connection_state", 0))

        if not self.in_connection:
            # Check for connection start
            if (state in self.ACTIVE_STATES or
                    rpm > self.rpm_threshold or
                    torque > self.torque_threshold):
                self.in_connection = True
                self.connection_num += 1
                self._idle_since = None
                return f"CONNECTION_START #{self.connection_num}"
        else:
            # Check for connection end
            is_idle = (state in (self.IDLE, self.COMPLETE) and
                       rpm < self.rpm_threshold and
                       torque < self.torque_threshold)

            if is_idle:
                if self._idle_since is None:
                    self._idle_since = timestamp
                elif (timestamp - self._idle_since) >= self.idle_timeout_s:
                    self.in_connection = False
                    self._idle_since = None
                    return f"CONNECTION_END #{self.connection_num}"
            else:
                self._idle_since = None

        return None


# ═══════════════════════════════════════════════════════════════════
# CSV Writer with Auto-Segmentation
# ═══════════════════════════════════════════════════════════════════

class LiveCSVWriter:
    """Writes real-time sensor data to CSV files.

    Modes:
      - continuous: Single CSV file, runs until stopped
      - segmented: New CSV per connection (detected by ConnectionSegmenter)
    """

    def __init__(self, output_dir: str, mode: str = "continuous",
                 prefix: str = "capture"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.prefix = prefix
        self._file = None
        self._writer = None
        self._row_count = 0
        self._file_count = 0
        self._current_path: Optional[Path] = None

    def _open_new_file(self, suffix: str = ""):
        """Open a new CSV file."""
        self._close_current()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{self.prefix}_{ts}{suffix}.csv"
        self._current_path = self.output_dir / name
        self._file = open(self._current_path, 'w', newline='', buffering=1)
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_COLUMNS,
                                       extrasaction='ignore')
        self._writer.writeheader()
        self._file_count += 1
        self._row_count = 0
        print(f"  [CSV] Opened: {self._current_path}")

    def _close_current(self):
        if self._file:
            self._file.flush()
            self._file.close()
            if self._current_path and self._row_count > 0:
                print(f"  [CSV] Closed: {self._current_path.name} "
                      f"({self._row_count} rows)")
            self._file = None
            self._writer = None

    def start(self):
        """Start writing (opens first file for continuous mode)."""
        if self.mode == "continuous":
            self._open_new_file()

    def new_connection(self, conn_num: int):
        """Start a new file for a detected connection (segmented mode)."""
        if self.mode == "segmented":
            self._open_new_file(suffix=f"_conn{conn_num:04d}")

    def end_connection(self):
        """Close current connection file (segmented mode)."""
        if self.mode == "segmented":
            self._close_current()

    def write_row(self, values: Dict[str, float], timestamp: str,
                  elapsed: float):
        """Write a single row of sensor data."""
        if self._writer is None:
            return
        row = {"timestamp": timestamp, "elapsed_s": f"{elapsed:.4f}"}
        for col in CSV_COLUMNS[2:]:
            row[col] = f"{values.get(col, 0):.6f}"
        # Int columns — write as integers
        for int_col in ["fault_code", "connection_state", "operating_mode",
                        "connection_count"]:
            if int_col in values:
                row[int_col] = str(int(values[int_col]))
        self._writer.writerow(row)
        self._row_count += 1

    def close(self):
        self._close_current()
        print(f"\n  [CSV] Total files written: {self._file_count}")


# ═══════════════════════════════════════════════════════════════════
# Live Console Display
# ═══════════════════════════════════════════════════════════════════

CONNECTION_STATE_NAMES = {
    0: "IDLE", 1: "APPROACH", 2: "SPIN_IN", 3: "SHOULDER",
    4: "POWER_TIGHT", 5: "HOLD", 6: "BREAKOUT", 7: "BACKOFF",
    8: "FAULT", 9: "COMPLETE", 10: "STALL", 11: "E_STOP",
    12: "FAULT_RECOVERY", 13: "HANDOFF",
}

FAULT_CODE_BITS = {
    0x0001: "OVER_TORQUE", 0x0004: "CROSS_THREAD", 0x0008: "GALLING",
    0x0010: "STALL", 0x0040: "STICK_SLIP", 0x0080: "STRIPPED",
    0x0100: "MISALIGNED", 0x0200: "WRONG_COMPOUND", 0x0400: "WASHOUT",
    0x0800: "CONN_JUMP",
}


def format_fault_code(fc: int) -> str:
    if fc == 0:
        return "NONE"
    faults = [name for bit, name in FAULT_CODE_BITS.items() if fc & bit]
    return "|".join(faults) if faults else f"0x{fc:04X}"


def display_live(values: Dict[str, float], elapsed: float, poll_hz: float,
                 conn_num: int, row_count: int, mapped_fields: set = None):
    """Print live sensor dashboard to console."""
    state = int(values.get("connection_state", 0))
    state_name = CONNECTION_STATE_NAMES.get(state, f"UNK({state})")
    fc = int(values.get("fault_code", 0))

    # Mark unmapped fields with '--' instead of misleading 0.0
    def v(key, fmt, unit=""):
        if mapped_fields and key not in mapped_fields:
            return f"{'--':>{len(fmt.format(0))}}{unit}"
        return f"{fmt.format(values.get(key, 0))}{unit}"

    if mapped_fields and "connection_state" not in mapped_fields:
        state_name = "--"
    if mapped_fields and "fault_code" not in mapped_fields:
        fc_str = "--"
    else:
        fc_str = format_fault_code(fc)

    # Build display
    lines = [
        f"\r  [{elapsed:8.1f}s] {poll_hz:5.1f} Hz | "
        f"Conn #{conn_num} | {row_count} rows | "
        f"State: {state_name:12s} | "
        f"Fault: {fc_str:15s}",
        f"    Torque: {v('torque_ftlbs', '{:8.1f}', ' ft-lb')} | "
        f"RPM: {v('rpm', '{:6.1f}')} | "
        f"Pressure: {v('pressure_psi', '{:7.1f}', ' PSI')} | "
        f"Temp: {v('oil_temp_f', '{:5.1f}', ' F')}",
        f"    Turns: {v('turns', '{:6.3f}')} | "
        f"Target: {v('target_torque', '{:8.1f}', ' ft-lb')} | "
        f"Peak: {v('peak_torque', '{:8.1f}', ' ft-lb')} | "
        f"Hookload: {v('hookload_klbs', '{:6.2f}', ' klbs')}",
    ]
    # Move cursor up and overwrite
    sys.stdout.write(f"\033[3A" if elapsed > 1.0 else "")
    for line in lines:
        sys.stdout.write(f"\033[K{line}\n")
    sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════════
# Main Capture Loop
# ═══════════════════════════════════════════════════════════════════

def run_capture(args):
    """Main capture loop with auto-reconnect."""
    # Load register map: profile YAML > JSON > auto-detect by IP > default
    reg_map = None
    word_swap = True   # Default: GE CPE305 convention
    profile_name = "default"
    unit_id = 1  # Default Modbus unit ID

    if hasattr(args, 'profile') and args.profile:
        # Load from MachineProfile YAML
        try:
            profile = MachineProfile.from_yaml(args.profile)
            reg_map, word_swap = reg_map_from_profile(profile)
            profile_name = profile.name
            unit_id = profile.unit_id
            print(f"  Loaded machine profile: {profile.name} ({args.profile})")
        except Exception as e:
            print(f"  WARNING: Failed to load profile {args.profile}: {e}")
            print(f"  Falling back to default register map.")

    if reg_map is None and args.register_map:
        with open(args.register_map) as f:
            reg_map = json.load(f)
        print(f"  Loaded custom register map: {args.register_map}")

    if reg_map is None:
        # Try auto-detect by PLC IP from profiles/ directory
        auto_profile = MachineProfile.find_by_ip("profiles", args.host)
        if auto_profile:
            reg_map, word_swap = reg_map_from_profile(auto_profile)
            profile_name = auto_profile.name
            unit_id = auto_profile.unit_id
            print(f"  Auto-detected profile: {auto_profile.name} "
                  f"(matched IP {args.host})")

    if reg_map is None:
        reg_map = DEFAULT_REGISTER_MAP
        print(f"  Using default R6000 register map")

    # Track which fields are actually mapped (vs defaulting to 0)
    mapped_fields = set(reg_map.keys())

    # Report unmapped critical fields
    critical = ["connection_state", "fault_code", "target_torque",
                "peak_torque", "pressure_psi"]
    missing = [f for f in critical if f not in mapped_fields]
    if missing:
        print(f"\n  WARNING: Unmapped fields (will show '--'): "
              f"{', '.join(missing)}")
        print(f"  Run --discover to find these registers on the PLC.")

    print(f"  Word swap: {'GE (low@N, high@N+1)' if word_swap else 'Standard IEEE (high@N, low@N+1)'}")

    # Compute block reads (supports non-contiguous register maps)
    block_reads = compute_block_reads(reg_map)

    # Also compute simple start/count for single-block mode
    all_addrs = [info["addr"] for info in reg_map.values()]
    max_addr = max(
        info["addr"] + (2 if info["type"] in ("FLOAT32", "INT32", "UINT32") else 1)
        for info in reg_map.values()
    )
    reg_start = min(all_addrs)
    reg_count = max_addr - reg_start

    if args.reg_start is not None:
        reg_start = args.reg_start
    if args.reg_count is not None:
        reg_count = args.reg_count

    # Decide: use multi-block reads if registers are non-contiguous
    # Also force multi-block if single span exceeds 125 (Modbus FC03 limit)
    use_multi_block = len(block_reads) > 1 or reg_count > 125
    if use_multi_block:
        print(f"\n  Non-contiguous register map ({len(block_reads)} blocks):")
        for bstart, bcount in block_reads:
            print(f"    R{bstart}-R{bstart+bcount-1} ({bcount} registers)")
    else:
        print(f"\n  Register block: R{reg_start} - R{reg_start + reg_count - 1} "
              f"({reg_count} registers)")

    # Setup
    client = ModbusTCPClient(args.host, args.port, timeout=args.timeout,
                              unit_id=unit_id)
    segmenter = ConnectionSegmenter()
    writer = LiveCSVWriter(args.output, mode=args.segment_mode,
                           prefix=args.prefix)

    poll_interval = 1.0 / args.hz
    running = True
    total_rows = 0
    poll_count = 0
    poll_errors = 0
    t_start = time.time()

    def signal_handler(sig, frame):
        nonlocal running
        print(f"\n\n  Stopping capture (signal {sig})...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print(f"\n  Target poll rate: {args.hz} Hz ({poll_interval*1000:.1f} ms)")
    print(f"  Output: {args.output} ({args.segment_mode} mode)")
    print(f"  Connecting to {args.host}:{args.port}...")
    print()

    reconnect_delay = 1.0
    max_reconnect_delay = 30.0

    writer.start()

    while running:
        # Connect / reconnect
        if not client.connected:
            if client.connect():
                print(f"  [CONN] Connected to {args.host}:{args.port}")
                reconnect_delay = 1.0
            else:
                print(f"  [CONN] Retry in {reconnect_delay:.0f}s...")
                # Sleep in small increments so we can catch SIGINT
                wait_until = time.time() + reconnect_delay
                while running and time.time() < wait_until:
                    time.sleep(0.5)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                continue

        # Poll — use multi-block reads for non-contiguous register maps
        t_poll_start = time.time()

        if use_multi_block:
            # Multi-block: read each block separately, merge into dict
            reg_dict = {}
            read_ok = True
            for bstart, bcount in block_reads:
                regs = client.read_holding_registers(bstart, bcount)
                if regs is None:
                    read_ok = False
                    break
                for i, val in enumerate(regs):
                    reg_dict[bstart + i] = val

            if not read_ok:
                poll_errors += 1
                if poll_errors > 3:
                    print(f"  [CONN] Lost connection ({poll_errors} errors)")
                    client.close()
                    poll_errors = 0
                continue

            poll_errors = 0
            poll_count += 1
            elapsed = time.time() - t_start
            now_str = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            values = decode_registers_from_dict(reg_dict, reg_map, word_swap)
        else:
            # Single contiguous block read
            raw_regs = client.read_holding_registers(reg_start, reg_count)

            if raw_regs is None:
                poll_errors += 1
                if poll_errors > 3:
                    print(f"  [CONN] Lost connection ({poll_errors} errors)")
                    client.close()
                    poll_errors = 0
                continue

            poll_errors = 0
            poll_count += 1
            elapsed = time.time() - t_start
            now_str = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            values = decode_registers(raw_regs, reg_start, reg_map, word_swap)

        # Connection segmentation
        event = segmenter.update(values, elapsed)
        if event:
            print(f"\n  [SEG] {event} at {elapsed:.1f}s")
            if "START" in event:
                writer.new_connection(segmenter.connection_num)
            elif "END" in event:
                writer.end_connection()

        # Write to CSV
        writer.write_row(values, now_str, elapsed)
        total_rows += 1

        # Live display (throttle to ~4 Hz to avoid console spam)
        if poll_count % max(1, int(args.hz / 4)) == 0:
            actual_hz = poll_count / max(elapsed, 0.001)
            display_live(values, elapsed, actual_hz,
                         segmenter.connection_num, total_rows,
                         mapped_fields)

        # Sleep to maintain target poll rate
        t_poll_end = time.time()
        sleep_time = poll_interval - (t_poll_end - t_poll_start)
        if sleep_time > 0:
            time.sleep(sleep_time)

    # Cleanup
    writer.close()
    client.close()

    elapsed = time.time() - t_start
    actual_hz = poll_count / max(elapsed, 0.001)
    print(f"\n  Capture complete:")
    print(f"    Duration:     {elapsed:.1f}s")
    print(f"    Total rows:   {total_rows}")
    print(f"    Actual rate:  {actual_hz:.1f} Hz")
    print(f"    Connections:  {segmenter.connection_num}")
    print(f"    Output dir:   {args.output}")


# ═══════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Live Modbus TCP Data Capture for PLC via eWon VPN",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Discover registers on PLC
  python capture_live.py --host 10.0.0.1 --discover

  # Capture at default 10 Hz
  python capture_live.py --host 10.0.0.1

  # Capture at 20 Hz with connection segmentation
  python capture_live.py --host 10.0.0.1 --hz 20 --segment-mode segmented

  # Use custom register map
  python capture_live.py --host 10.0.0.1 --register-map my_registers.json
        """)

    parser.add_argument("--host", required=True,
                        help="PLC/eWon IP address")
    parser.add_argument("--port", type=int, default=502,
                        help="Modbus TCP port (default: 502)")
    parser.add_argument("--hz", type=float, default=10.0,
                        help="Poll rate in Hz (default: 10)")
    parser.add_argument("--output", default="./live_captures",
                        help="Output directory (default: ./live_captures)")
    parser.add_argument("--prefix", default="capture",
                        help="CSV filename prefix (default: capture)")
    parser.add_argument("--timeout", type=float, default=5.0,
                        help="Socket timeout in seconds (default: 5)")
    parser.add_argument("--segment-mode", choices=["continuous", "segmented"],
                        default="continuous",
                        help="continuous=single file, segmented=file per connection")
    parser.add_argument("--profile", default=None,
                        help="Path to MachineProfile YAML (auto-detects by IP if not set)")
    parser.add_argument("--register-map", default=None,
                        help="Path to custom register map JSON file (legacy)")
    parser.add_argument("--reg-start", type=int, default=None,
                        help="Override block read start register")
    parser.add_argument("--reg-count", type=int, default=None,
                        help="Override block read register count")

    # Discovery mode
    parser.add_argument("--discover", action="store_true",
                        help="Scan registers to find active ones")
    parser.add_argument("--scan-start", type=int, default=0,
                        help="Discovery scan start register (default: 0)")
    parser.add_argument("--scan-end", type=int, default=200,
                        help="Discovery scan end register (default: 200)")

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  PLC Live Data Capture — Modbus TCP")
    print("=" * 60)
    print(f"  Host:     {args.host}:{args.port}")
    print(f"  Mode:     {'DISCOVERY' if args.discover else 'CAPTURE'}")

    if args.discover:
        client = ModbusTCPClient(args.host, args.port, timeout=args.timeout)
        if not client.connect():
            print("\n  ERROR: Could not connect. Check host/port and VPN.")
            sys.exit(1)
        discover_registers(client, args.scan_start, args.scan_end)
        client.close()
    else:
        run_capture(args)


if __name__ == "__main__":
    main()
