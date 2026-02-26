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

# Late imports for auto-discovery (only used in --multi mode)
# These are imported inside functions to avoid breaking --host mode
# if fleet_manager.py or discover_machine.py are missing.


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

    Args:
        word_a: Register at address N.
        word_b: Register at address N+1.
        word_swap: True = GE convention (low word at N, high word at N+1).
                   Same swap logic as FLOAT32 on GE CPE305 PLCs.
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
            # GE CPE305 word-swaps all 32-bit values (float AND int)
            val = float(decode_int32(raw_regs[offset], raw_regs[offset + 1],
                                     word_swap=word_swap,
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
            # GE CPE305 word-swaps all 32-bit values (float AND int)
            val = float(decode_int32(word_a, word_b, word_swap=word_swap,
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
    """Scan register range to find active (non-zero) registers.

    Two-pass approach:
      1. Read all blocks, collect active registers into a dict.
      2. Decode pairs using the full dict (no block-boundary blind spots).
    """
    print(f"\n{'='*60}")
    print(f"  REGISTER DISCOVERY: scanning {scan_start}-{scan_end}")
    print(f"{'='*60}\n")

    # Pass 1: read all blocks, build full register map
    all_regs = {}  # addr -> raw value
    addr = scan_start
    while addr < scan_end:
        count = min(block_size, scan_end - addr)
        regs = client.read_holding_registers(addr, count)
        if regs is None:
            print(f"  [{addr:5d}-{addr+count-1:5d}] READ FAILED")
            addr += count
            continue
        for i, val in enumerate(regs):
            all_regs[addr + i] = val
        addr += count
        time.sleep(0.05)  # Rate limit over VPN

    # Pass 2: display active registers with cross-block pair decoding
    active = []
    for reg_addr in sorted(all_regs):
        val = all_regs[reg_addr]
        if val == 0:
            continue
        active.append((reg_addr, val))

        next_val = all_regs.get(reg_addr + 1, 0)
        if next_val != 0:
            # Both this register and next are nonzero — decode as 32-bit pair
            f32_ge = decode_float32(val, next_val, word_swap=True)
            f32_std = decode_float32(val, next_val, word_swap=False)
            i32_std = decode_int32(val, next_val, word_swap=False)
            i32_ge = decode_int32(val, next_val, word_swap=True)
            print(f"  R{reg_addr:5d} = 0x{val:04X} "
                  f"(raw={val}, GE_f32={f32_ge:.4f}, "
                  f"STD_f32={f32_std:.4f}, i32={i32_std}, i32_ge={i32_ge})")
        else:
            print(f"  R{reg_addr:5d} = 0x{val:04X} "
                  f"(raw={val}, int16s={decode_int16_signed(val)})")

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
# State Inference Engine (Smart Capture)
# ═══════════════════════════════════════════════════════════════════

class StateInferenceEngine:
    """Infers connection phase and computes derived fields from available signals.

    Replaces ConnectionSegmenter with a full state machine that:
      - Estimates connection_state from torque/rpm/turns patterns
      - Detects shoulder via dT/dN gradient spike
      - Tracks peak_torque, shoulder_torque, slope in real-time
      - Heuristic fault detection (cross-thread, stall, over-torque)

    State machine:
      IDLE(0) -> SPIN_IN(2) -> SHOULDER(3) -> POWER_TIGHT(4)
        -> HOLD(5) -> COMPLETE(9) -> IDLE(0)
    """

    # Thresholds (tuned for oilfield top drives / iron roughnecks)
    RPM_ACTIVE = 2.0          # RPM > this = motor spinning
    TORQUE_ACTIVE = 50.0      # ft-lbs > this = engaging threads
    RPM_STOPPED = 1.0         # RPM < this = motor stopped
    IDLE_TIMEOUT = 3.0        # seconds of low activity -> IDLE
    SHOULDER_SLOPE_MULT = 5.0 # dT/dN must exceed baseline × this
    HOLD_TORQUE_FRAC = 0.90   # torque > peak * this = holding
    STALL_RPM = 0.5           # RPM below this = potential stall
    STALL_TORQUE = 100.0      # torque above this during stall
    STALL_TIMEOUT = 3.0       # seconds to confirm stall
    SLOPE_WINDOW = 10         # samples for rolling dT/dN

    def __init__(self, target_torque: float = 0.0):
        self.state = 0           # ConnectionState enum value (from signal inference)
        self.plc_state = None    # Raw PLC state for display comparison
        self.connection_num = 0
        self.in_connection = False

        # Per-connection tracking
        self.peak_torque = 0.0
        self.shoulder_torque = 0.0
        self.shoulder_turns = 0.0
        self.slope_dT_dN = 0.0
        self.target_torque_est = target_torque
        self.total_turns_at_start = 0.0
        self.max_rpm = 0.0
        self.hold_torque = 0.0
        self.inferred_fault = 0
        self.state_path = []
        self._conn_start_time = 0.0

        # Rolling buffers for gradient computation
        self._torque_buf = []
        self._turns_buf = []
        self._time_buf = []
        self._baseline_slope = 0.0
        self._baseline_samples = 0
        self._shoulder_detected = False

        # Stall detection
        self._stall_timer = 0.0

        # Idle detection
        self._idle_timer = 0.0

        # Previous values for rate computation
        self._prev_torque = 0.0
        self._prev_time = 0.0

    def _reset_connection(self):
        """Reset per-connection state."""
        self.state = 0  # Reset so next _transition() records the entry state
        self.peak_torque = 0.0
        self.shoulder_torque = 0.0
        self.shoulder_turns = 0.0
        self.slope_dT_dN = 0.0
        self.max_rpm = 0.0
        self.hold_torque = 0.0
        self.inferred_fault = 0
        self.state_path = [0]
        self._torque_buf.clear()
        self._turns_buf.clear()
        self._time_buf.clear()
        self._baseline_slope = 0.0
        self._baseline_samples = 0
        self._shoulder_detected = False
        self._stall_timer = 0.0

    def _transition(self, new_state: int):
        """Transition to a new state."""
        if new_state != self.state:
            self.state = new_state
            if not self.state_path or self.state_path[-1] != new_state:
                self.state_path.append(new_state)

    def _compute_rolling_slope(self) -> float:
        """Compute dT/dN from rolling buffer."""
        if len(self._torque_buf) < 3:
            return 0.0
        dt = self._torque_buf[-1] - self._torque_buf[0]
        dn = self._turns_buf[-1] - self._turns_buf[0]
        if abs(dn) < 1e-6:
            return 0.0
        return dt / dn

    def update(self, values: Dict[str, float], timestamp: float,
               mapped_fields: set = None) -> Tuple[Optional[str], Dict[str, float]]:
        """Update state machine and return (event, inferred_fields).

        ALWAYS uses signal-based inference (torque/rpm/turns patterns).
        PLC state is stored for display comparison but never drives the
        state machine — unverified register mappings can be wrong.

        Args:
            values: Decoded register values for this poll.
            timestamp: Elapsed time in seconds.
            mapped_fields: Set of field names actually read from PLC.

        Returns:
            (event_str_or_None, dict_of_inferred_values)
        """
        torque = values.get("torque_ftlbs", 0.0)
        rpm = values.get("rpm", 0.0)
        turns = values.get("turns", 0.0)
        hookload = values.get("hookload_klbs", 0.0)

        # Store PLC state for display/logging — NOT used for state machine
        if mapped_fields and "connection_state" in mapped_fields:
            self.plc_state = int(values.get("connection_state", 0))

        # Use PLC target_torque if available (reasonable to trust set-points)
        if mapped_fields and "target_torque" in mapped_fields:
            tt = values.get("target_torque", 0.0)
            if tt > 0:
                self.target_torque_est = tt

        dt = timestamp - self._prev_time if self._prev_time > 0 else 0.1
        dt = max(dt, 0.001)  # Clamp to avoid division by zero

        # d_torque/dt
        d_torque_dt = (torque - self._prev_torque) / dt if dt > 0 else 0.0
        self._prev_torque = torque
        self._prev_time = timestamp

        # Rolling buffer for dT/dN
        self._torque_buf.append(torque)
        self._turns_buf.append(turns)
        self._time_buf.append(timestamp)
        if len(self._torque_buf) > self.SLOPE_WINDOW:
            self._torque_buf.pop(0)
            self._turns_buf.pop(0)
            self._time_buf.pop(0)

        current_slope = self._compute_rolling_slope()
        event = None

        # ── Signal-Based State Machine (always runs) ───────────
        if self.state == 0:  # IDLE
            if rpm > self.RPM_ACTIVE and torque > self.TORQUE_ACTIVE:
                self.in_connection = True
                self.connection_num += 1
                self._reset_connection()
                self.total_turns_at_start = turns
                self._conn_start_time = timestamp
                self._transition(2)  # SPIN_IN
                # Track first sample's peak immediately after connection start
                self.peak_torque = torque
                self.max_rpm = rpm
                event = f"CONNECTION_START #{self.connection_num}"

        elif self.state == 2:  # SPIN_IN
            # Build baseline slope from early spin-in
            if self._baseline_samples < 20 and abs(current_slope) > 0:
                self._baseline_slope = (
                    (self._baseline_slope * self._baseline_samples + abs(current_slope))
                    / (self._baseline_samples + 1)
                )
                self._baseline_samples += 1

            # Shoulder detection: slope spike
            threshold = max(self._baseline_slope * self.SHOULDER_SLOPE_MULT,
                            500.0)  # Minimum 500 ft-lbs/turn
            if abs(current_slope) > threshold and not self._shoulder_detected:
                self._shoulder_detected = True
                self.shoulder_torque = torque
                self.shoulder_turns = turns
                self._transition(3)  # SHOULDER

            # Fallback: if torque exceeds 30% of target, assume shoulder passed
            if (self.target_torque_est > 0 and
                    torque > 0.30 * self.target_torque_est):
                if not self._shoulder_detected:
                    self._shoulder_detected = True
                    self.shoulder_torque = torque
                    self.shoulder_turns = turns
                self._transition(3)

            self._check_stall(rpm, torque, dt)

        elif self.state == 3:  # SHOULDER
            conn_turns = turns - self.total_turns_at_start
            shoulder_turns = self.shoulder_turns - self.total_turns_at_start
            if conn_turns > shoulder_turns + 0.1:
                self._transition(4)  # POWER_TIGHT
            self._check_stall(rpm, torque, dt)

        elif self.state == 4:  # POWER_TIGHT
            self.slope_dT_dN = current_slope

            # Transition to HOLD: rpm drops, torque near peak
            if (rpm < self.RPM_STOPPED and self.peak_torque > 0 and
                    torque > self.HOLD_TORQUE_FRAC * self.peak_torque):
                self.hold_torque = torque
                self._transition(5)  # HOLD
            self._check_stall(rpm, torque, dt)

        elif self.state == 5:  # HOLD
            self.hold_torque = max(self.hold_torque, torque)
            # Transition to COMPLETE: torque drops or breakout
            if torque < 0.80 * self.hold_torque or rpm > self.RPM_ACTIVE:
                self._transition(9)  # COMPLETE

        elif self.state == 9:  # COMPLETE
            self._idle_timer += dt
            if (self._idle_timer > self.IDLE_TIMEOUT and
                    torque < self.TORQUE_ACTIVE and rpm < self.RPM_STOPPED):
                self._transition(0)  # IDLE
                self.in_connection = False
                self._idle_timer = 0.0
                event = f"CONNECTION_END #{self.connection_num}"
            elif torque > self.TORQUE_ACTIVE or rpm > self.RPM_ACTIVE:
                self._idle_timer = 0.0

        elif self.state == 10:  # STALL
            if rpm > 2.0:
                self._transition(3)  # Back to SHOULDER
                self._stall_timer = 0.0

        # ── Track peak AFTER state machine (catches first sample) ──
        if self.in_connection:
            if torque > self.peak_torque:
                self.peak_torque = torque
            if rpm > self.max_rpm:
                self.max_rpm = rpm

        # ── Heuristic fault detection ──────────────────────────
        self._detect_faults(torque, rpm, turns, timestamp)

        # ── Estimate target_torque if not available ────────────
        if self.target_torque_est == 0 and self.state == 5:
            self.target_torque_est = self.peak_torque

        # ── Build inferred fields dict ─────────────────────────
        # Always override connection_state with signal-based inference
        # (PLC state is unverified and saved as raw_RXXX for comparison)
        inferred = {
            "connection_state": float(self.state),
            "peak_torque": self.peak_torque,
            "operating_mode": 1.0 if self.in_connection else 0.0,
        }
        # These fields: use PLC if mapped, otherwise use engine's values
        if not mapped_fields or "shoulder_torque" not in mapped_fields:
            inferred["shoulder_torque"] = self.shoulder_torque
        if not mapped_fields or "slope_dT_dN" not in mapped_fields:
            inferred["slope_dT_dN"] = self.slope_dT_dN
        if not mapped_fields or "fault_code" not in mapped_fields:
            inferred["fault_code"] = float(self.inferred_fault)
        if not mapped_fields or "target_torque" not in mapped_fields:
            inferred["target_torque"] = self.target_torque_est
        if not mapped_fields or "pressure_psi" not in mapped_fields:
            inferred["pressure_psi"] = 0.0  # No sensor = leave as 0

        return event, inferred

    def _check_stall(self, rpm: float, torque: float, dt: float):
        """Check for stall condition: low RPM + high torque sustained."""
        if rpm < self.STALL_RPM and torque > self.STALL_TORQUE:
            self._stall_timer += dt
            if self._stall_timer > self.STALL_TIMEOUT:
                self._transition(10)  # STALL
                self.inferred_fault |= 0x0010  # FaultCode.STALL
        else:
            self._stall_timer = 0.0

    def _detect_faults(self, torque: float, rpm: float,
                       turns: float, timestamp: float):
        """Heuristic fault detection from signal patterns."""
        conn_turns = turns - self.total_turns_at_start if self.in_connection else 0
        conn_time = timestamp - self._conn_start_time if self.in_connection else 0

        # Cross-thread: abnormally high torque early in threading.
        # Guards to prevent false positives:
        #   1) conn_turns >= 0.5: must see actual turns advancement
        #      (avoids false alarm when capture starts mid-connection)
        #   2) conn_turns < 1.5: only flag during early threading
        #   3) conn_time >= 3.0: wait for enough data to judge
        #   4) torque > 80% of target at that low turn count
        if (self.in_connection and self.state in (2, 3, 4) and
                conn_turns >= 0.5 and conn_turns < 1.5 and
                conn_time >= 3.0 and
                self.target_torque_est > 0 and
                torque > 0.80 * self.target_torque_est):
            self.inferred_fault |= 0x0004  # CROSS_THREAD

        # Over-torque: exceeds target by 5%+
        if (self.target_torque_est > 0 and
                torque > 1.05 * self.target_torque_est):
            self.inferred_fault |= 0x0001  # OVER_TORQUE

    def get_connection_metadata(self, timestamp: float) -> Dict:
        """Return metadata dict for the completed connection."""
        return {
            "connection_num": self.connection_num,
            "start_time": self._conn_start_time,
            "duration_s": round(timestamp - self._conn_start_time, 2),
            "peak_torque_ftlbs": round(self.peak_torque, 1),
            "shoulder_torque_ftlbs": round(self.shoulder_torque, 1),
            "target_torque_ftlbs": round(self.target_torque_est, 1),
            "total_turns": round(
                self._turns_buf[-1] - self.total_turns_at_start
                if self._turns_buf else 0, 3),
            "max_rpm": round(self.max_rpm, 1),
            "inferred_state_path": list(self.state_path),
            "inferred_fault_code": self.inferred_fault,
            "quality_score": self._compute_quality_score(),
        }

    def _compute_quality_score(self) -> float:
        """Heuristic quality score 0-1 for the connection."""
        score = 1.0

        # Penalize if shoulder not detected
        if not self._shoulder_detected:
            score -= 0.3

        # Penalize if target not reached
        if self.target_torque_est > 0:
            ratio = self.peak_torque / self.target_torque_est
            if ratio < 0.80:
                score -= 0.3
            elif ratio > 1.10:
                score -= 0.2

        # Penalize if any fault detected
        if self.inferred_fault != 0:
            score -= 0.2

        # Penalize if state path incomplete
        expected = {2, 3, 4, 5}  # SPIN_IN, SHOULDER, POWER_TIGHT, HOLD
        actual = set(self.state_path)
        missing = expected - actual
        score -= 0.1 * len(missing)

        return max(0.0, min(1.0, round(score, 2)))


# ═══════════════════════════════════════════════════════════════════
# CSV Writer with Auto-Segmentation
# ═══════════════════════════════════════════════════════════════════

class LiveCSVWriter:
    """Writes real-time sensor data to CSV files.

    Modes:
      - continuous: Single CSV file, runs until stopped
      - segmented: New CSV per connection (detected by ConnectionSegmenter)

    In smart mode, extra raw_RXXX columns are captured dynamically.
    """

    def __init__(self, output_dir: str, mode: str = "continuous",
                 prefix: str = "capture", extra_columns: List[str] = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.prefix = prefix
        self._file = None
        self._writer = None
        self._row_count = 0
        self._file_count = 0
        self._current_path: Optional[Path] = None
        # Dynamic extra columns (discovered at runtime)
        self._extra_cols: List[str] = list(extra_columns) if extra_columns else []
        self._all_cols: List[str] = CSV_COLUMNS + self._extra_cols

    def add_extra_columns(self, cols: List[str]):
        """Add newly discovered columns. Re-opens file with updated header."""
        new_cols = [c for c in cols if c not in self._extra_cols
                    and c not in CSV_COLUMNS]
        if new_cols:
            self._extra_cols.extend(new_cols)
            self._all_cols = CSV_COLUMNS + self._extra_cols

    def _open_new_file(self, suffix: str = ""):
        """Open a new CSV file."""
        self._close_current()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{self.prefix}_{ts}{suffix}.csv"
        self._current_path = self.output_dir / name
        self._file = open(self._current_path, 'w', newline='', buffering=1)
        self._writer = csv.DictWriter(self._file, fieldnames=self._all_cols,
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

        # Discover new raw_RXXX columns on first appearance
        raw_cols = [k for k in values if k.startswith("raw_R")
                    and k not in self._extra_cols]
        if raw_cols:
            self.add_extra_columns(sorted(raw_cols))
            # Re-open file with updated header (only at start of capture)
            if self._row_count == 0:
                self._open_new_file()  # Re-open with new columns

        row = {"timestamp": timestamp, "elapsed_s": f"{elapsed:.4f}"}
        for col in CSV_COLUMNS[2:]:
            row[col] = f"{values.get(col, 0):.6f}"
        # Int columns — write as integers
        for int_col in ["fault_code", "connection_state", "operating_mode",
                        "connection_count"]:
            if int_col in values:
                row[int_col] = str(int(values[int_col]))
        # Extra raw register columns
        for col in self._extra_cols:
            if col in values:
                row[col] = f"{values[col]:.0f}"  # INT16: no decimals
        self._writer.writerow(row)
        self._row_count += 1

    def close(self):
        self._close_current()
        print(f"\n  [CSV] Total files written: {self._file_count}")
        if self._extra_cols:
            print(f"  [CSV] Extra raw columns captured: {', '.join(self._extra_cols)}")


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
                 conn_num: int, row_count: int, mapped_fields: set = None,
                 engine: 'StateInferenceEngine' = None):
    """Print live sensor dashboard to console."""
    state = int(values.get("connection_state", 0))
    state_name = CONNECTION_STATE_NAMES.get(state, f"UNK({state})")
    fc = int(values.get("fault_code", 0))

    # Mark unmapped fields with '--' instead of misleading 0.0
    def v(key, fmt, unit=""):
        if mapped_fields and key not in mapped_fields and engine is None:
            return f"{'--':>{len(fmt.format(0))}}{unit}"
        return f"{fmt.format(values.get(key, 0))}{unit}"

    if mapped_fields and "connection_state" not in mapped_fields and engine is None:
        state_name = "--"
    if mapped_fields and "fault_code" not in mapped_fields and engine is None:
        fc_str = "--"
    else:
        fc_str = format_fault_code(fc)

    # Smart mode: always show signal-inferred state, PLC state alongside
    if engine is not None:
        inferred_state = engine.state
        inferred_name = CONNECTION_STATE_NAMES.get(inferred_state, f"?{inferred_state}")
        if engine.plc_state is not None:
            plc_name = CONNECTION_STATE_NAMES.get(engine.plc_state, f"?{engine.plc_state}")
            state_label = f"{inferred_name} [PLC:{plc_name}]"
        else:
            state_label = f"{inferred_name}"
    else:
        state_label = state_name

    # Build display
    lines = [
        f"\r  [{elapsed:8.1f}s] {poll_hz:5.1f} Hz | "
        f"Conn #{conn_num} | {row_count} rows | "
        f"State: {state_label:20s} | "
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

    # Smart mode: 4th + 5th lines with derived metrics and raw register count
    if engine is not None:
        q = engine._compute_quality_score() if engine.in_connection else 0.0
        lines.append(
            f"    dT/dN: {engine.slope_dT_dN:8.1f} ft-lb/turn | "
            f"Shoulder: {'YES' if engine._shoulder_detected else 'no ':3s} @ "
            f"{engine.shoulder_torque:7.0f} | "
            f"Quality: {q:.2f} | "
            f"Path: {'>'.join(CONNECTION_STATE_NAMES.get(s, '?')[:3] for s in engine.state_path[-6:])}"
        )
        # Show raw register count and shoulder/conn_count from PLC
        raw_count = sum(1 for k in values if k.startswith("raw_R"))
        extra = f"    Shoulder: {v('shoulder_torque', '{:8.1f}', ' ft-lb')} | "
        extra += f"ConnCount: {v('connection_count', '{:4.0f}')} | "
        extra += f"Encoder: {v('encoder_counts', '{:10.0f}')} | "
        extra += f"Raw regs: {raw_count}"
        lines.append(extra)
        num_lines = 5
    else:
        num_lines = 3

    # Move cursor up and overwrite
    sys.stdout.write(f"\033[{num_lines}A" if elapsed > 1.0 else "")
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
    smart_mode = getattr(args, 'smart', False)
    critical = ["connection_state", "fault_code", "target_torque",
                "peak_torque", "pressure_psi"]
    missing = [f for f in critical if f not in mapped_fields]
    if missing and not smart_mode:
        print(f"\n  WARNING: Unmapped fields (will show '--'): "
              f"{', '.join(missing)}")
        print(f"  Run --discover to find these registers on the PLC.")
    elif missing and smart_mode:
        print(f"\n  SMART MODE: Will infer missing fields: "
              f"{', '.join(missing)}")

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

    # Smart mode: widen block reads to capture ALL registers in range
    # (not just mapped ones). Unknown registers saved as raw_RXXX columns.
    if smart_mode:
        # Group addresses into clusters (split when gap > 50 registers)
        sorted_addrs = sorted(all_addrs)
        clusters = [[sorted_addrs[0]]]
        for a in sorted_addrs[1:]:
            if a - clusters[-1][-1] > 50:
                clusters.append([a])
            else:
                clusters[-1].append(a)

        # Pad each cluster: 3 before, 12 after
        padded_blocks = []
        for cluster in clusters:
            pad_start = max(0, min(cluster) - 3)
            # Find max addr including type size
            pad_end = max(cluster) + 12
            for info in reg_map.values():
                if info["addr"] in cluster and info["type"] in ("FLOAT32", "INT32", "UINT32"):
                    pad_end = max(pad_end, info["addr"] + 2 + 10)
            padded_blocks.append((pad_start, pad_end - pad_start))

        # Split any block > 125 registers (Modbus FC03 limit)
        final_blocks = []
        for bs, bc in padded_blocks:
            while bc > 125:
                final_blocks.append((bs, 125))
                bs += 125
                bc -= 125
            final_blocks.append((bs, bc))
        block_reads = final_blocks
        print(f"\n  SMART: Extended block reads to capture all registers:")
        for bstart, bcount in block_reads:
            print(f"    R{bstart}-R{bstart+bcount-1} ({bcount} registers)")

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

    # Smart mode: use StateInferenceEngine; legacy: use ConnectionSegmenter
    engine = None
    if smart_mode:
        # Get initial target_torque estimate from profile if available
        init_target = 0.0
        if "target_torque" in mapped_fields:
            init_target = 0.0  # Will read from PLC
        engine = StateInferenceEngine(target_torque=init_target)
        segmenter = engine  # engine has .connection_num and .in_connection
        print(f"  Smart capture: StateInferenceEngine active")
    else:
        segmenter = ConnectionSegmenter()

    # Force segmented mode when smart capture is on
    seg_mode = args.segment_mode
    if smart_mode and seg_mode == "continuous":
        seg_mode = "segmented"
        print(f"  Smart capture: auto-switched to segmented mode")

    writer = LiveCSVWriter(args.output, mode=seg_mode,
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

            # Smart mode: capture ALL non-zero unmapped registers as raw_RXXX
            if smart_mode:
                # Build set of addresses already decoded by reg_map
                decoded_addrs = set()
                for info in reg_map.values():
                    a = info["addr"]
                    decoded_addrs.add(a)
                    if info["type"] in ("FLOAT32", "INT32", "UINT32"):
                        decoded_addrs.add(a + 1)
                # Save any non-zero register not in decoded set
                for addr, val in sorted(reg_dict.items()):
                    if addr not in decoded_addrs and val != 0:
                        values[f"raw_R{addr}"] = float(decode_int16_signed(val))
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

        # Connection segmentation + state inference
        if engine is not None:
            # Smart mode: run inference engine
            event, inferred = engine.update(values, elapsed, mapped_fields)
            # Merge inferred fields into values (fills missing columns)
            values.update(inferred)
        else:
            # Legacy mode: basic segmentation
            event = segmenter.update(values, elapsed)

        if event:
            print(f"\n  [SEG] {event} at {elapsed:.1f}s")
            if "START" in event:
                writer.new_connection(segmenter.connection_num)
            elif "END" in event:
                # Write JSON sidecar with connection metadata
                if engine is not None:
                    meta = engine.get_connection_metadata(elapsed)
                    sidecar_path = writer._current_path
                    if sidecar_path:
                        json_path = sidecar_path.with_suffix('.json')
                        try:
                            with open(json_path, 'w') as jf:
                                json.dump(meta, jf, indent=2)
                            print(f"  [META] {json_path.name} "
                                  f"(quality={meta['quality_score']:.2f})")
                        except Exception as e:
                            print(f"  [META] Write error: {e}")
                writer.end_connection()

        # Write to CSV (smart mode: skip idle rows to save disk)
        if engine is not None and smart_mode:
            # In smart mode, skip IDLE rows unless near connection boundary
            if engine.state == 0 and not engine.in_connection:
                # Only write 1 in every 10 idle rows (decimation)
                if total_rows % 10 != 0:
                    # Still count for display but skip CSV write
                    total_rows += 1
                    # Jump to display
                    if poll_count % max(1, int(args.hz / 4)) == 0:
                        actual_hz = poll_count / max(elapsed, 0.001)
                        display_live(values, elapsed, actual_hz,
                                     segmenter.connection_num, total_rows,
                                     mapped_fields, engine=engine)
                    t_poll_end = time.time()
                    sleep_time = poll_interval - (t_poll_end - t_poll_start)
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    continue

        writer.write_row(values, now_str, elapsed)
        total_rows += 1

        # Live display (throttle to ~4 Hz to avoid console spam)
        if poll_count % max(1, int(args.hz / 4)) == 0:
            actual_hz = poll_count / max(elapsed, 0.001)
            display_live(values, elapsed, actual_hz,
                         segmenter.connection_num, total_rows,
                         mapped_fields, engine=engine)

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
# Dataset Builder (post-capture processing)
# ═══════════════════════════════════════════════════════════════════

# Expected turns lookup by target torque range (same as dataset.py)
_EXPECTED_TURNS_TABLE = [
    (2000,  8.0),
    (4000,  5.5),
    (7000,  4.0),
    (15000, 3.5),
    (999999, 6.0),
]


def _estimate_expected_turns(target_torque: float) -> float:
    """Estimate expected turns from target torque range."""
    for threshold, turns in _EXPECTED_TURNS_TABLE:
        if target_torque < threshold:
            return turns
    return 6.0


def _resample_to_100hz(df: 'pd.DataFrame', actual_hz: float) -> 'pd.DataFrame':
    """Resample captured data from actual_hz to 100 Hz via interpolation.

    Continuous signals (torque, rpm, etc.): linear interpolation.
    Discrete signals (connection_state, fault_code, etc.): nearest-neighbor.
    """
    import numpy as np

    if actual_hz >= 95:  # Close enough to 100 Hz, skip resampling
        return df

    n_orig = len(df)
    duration_s = n_orig / actual_hz
    n_target = int(duration_s * 100)

    if n_target < 10:
        return df  # Too short to resample

    t_orig = np.linspace(0, duration_s, n_orig)
    t_target = np.linspace(0, duration_s, n_target)

    discrete_cols = {"fault_code", "connection_state", "operating_mode",
                     "connection_count"}

    result = {}
    for col in df.columns:
        vals = df[col].values.astype(np.float64)
        if col in discrete_cols:
            # Nearest-neighbor for discrete signals
            indices = np.searchsorted(t_orig, t_target, side='right') - 1
            indices = np.clip(indices, 0, n_orig - 1)
            result[col] = vals[indices]
        elif col == "timestamp":
            # Skip timestamp — will regenerate
            continue
        elif col == "elapsed_s":
            result[col] = t_target
        else:
            # Linear interpolation for continuous signals
            result[col] = np.interp(t_target, t_orig, vals)

    import pandas as pd
    resampled = pd.DataFrame(result)

    # Regenerate timestamp column
    resampled.insert(0, "timestamp", [f"{t:.4f}" for t in t_target])

    return resampled


def _auto_label_connection(meta: Dict) -> Tuple[str, int]:
    """Heuristic auto-labeling based on connection metadata.

    Returns (scenario_type, fault_class).
    Conservative: defaults to normal unless clear fault signal.
    """
    target = meta.get("target_torque_ftlbs", 0)
    peak = meta.get("peak_torque_ftlbs", 0)
    fault = meta.get("inferred_fault_code", 0)
    quality = meta.get("quality_score", 1.0)

    # Over-torque: peak exceeds target by 5%+
    if target > 0 and peak > 1.05 * target:
        return "over_torque", 4

    # Under-torque: peak never reached 65% of target
    if target > 0 and peak < 0.65 * target:
        return "under_torque", 5

    # Stall detected
    if fault & 0x0010:
        return "stall", 8

    # Cross-thread detected
    if fault & 0x0004:
        return "cross_thread", 1

    # Default: normal
    return "normal_casing_ltc", 0


def build_dataset(args):
    """Convert captured connection CSVs into training-ready dataset.

    Reads segmented capture CSVs + JSON sidecars from --input dir,
    resamples to 100 Hz, fills missing columns, auto-labels, and
    writes training-format files to --output dir.
    """
    import pandas as pd
    import numpy as np

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    min_duration = getattr(args, 'min_duration', 5.0)
    min_quality = getattr(args, 'min_quality', 0.3)
    rig_name = getattr(args, 'rig_name', 'unknown_rig')

    if not input_dir.exists():
        print(f"  ERROR: Input directory not found: {input_dir}")
        sys.exit(1)

    # Find all connection CSVs (segmented captures have _conn#### suffix)
    csv_files = sorted(input_dir.glob("*_conn*.csv"))
    if not csv_files:
        # Also try any CSV files
        csv_files = sorted(input_dir.glob("*.csv"))

    if not csv_files:
        print(f"  ERROR: No CSV files found in {input_dir}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Dataset Builder — Live Capture to Training Format")
    print(f"{'='*60}")
    print(f"  Input:  {input_dir} ({len(csv_files)} CSV files)")
    print(f"  Output: {output_dir}")
    print(f"  Min duration: {min_duration}s | Min quality: {min_quality}")

    # Create output structure
    sensor_dir = output_dir / "sensor"
    sensor_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows = []
    accepted = 0
    rejected = 0

    for csv_path in csv_files:
        # Load JSON sidecar if available
        json_path = csv_path.with_suffix('.json')
        meta = {}
        if json_path.exists():
            try:
                with open(json_path) as jf:
                    meta = json.load(jf)
            except Exception:
                pass

        # Load CSV
        try:
            df = pd.read_csv(csv_path)
        except Exception as e:
            print(f"  SKIP: {csv_path.name} — load error: {e}")
            rejected += 1
            continue

        if len(df) < 5:
            print(f"  SKIP: {csv_path.name} — too few rows ({len(df)})")
            rejected += 1
            continue

        # Estimate actual sample rate from elapsed_s
        if "elapsed_s" in df.columns and len(df) > 10:
            elapsed = df["elapsed_s"].iloc[-1] - df["elapsed_s"].iloc[0]
            actual_hz = len(df) / max(elapsed, 0.1)
        else:
            actual_hz = 10.0  # Default assumption

        duration = len(df) / max(actual_hz, 0.1)

        # Quality / duration filter
        quality = meta.get("quality_score", 0.5)
        if duration < min_duration:
            print(f"  SKIP: {csv_path.name} — too short ({duration:.1f}s)")
            rejected += 1
            continue
        if quality < min_quality:
            print(f"  SKIP: {csv_path.name} — low quality ({quality:.2f})")
            rejected += 1
            continue

        # Resample to 100 Hz
        df_100 = _resample_to_100hz(df, actual_hz)

        # Fill any missing columns with defaults
        for col in CSV_COLUMNS:
            if col not in df_100.columns:
                if col == "timestamp":
                    df_100[col] = [f"{i*0.01:.4f}" for i in range(len(df_100))]
                elif col == "elapsed_s":
                    df_100[col] = np.arange(len(df_100)) * 0.01
                else:
                    df_100[col] = 0.0

        # Ensure column order matches CSV_COLUMNS
        df_100 = df_100[CSV_COLUMNS]

        # Auto-label
        scenario_type, fault_class = _auto_label_connection(meta)

        # Compute summary stats
        peak_torque = float(df_100["torque_ftlbs"].max()) if "torque_ftlbs" in df_100 else 0
        target_torque = meta.get("target_torque_ftlbs", 0)
        if target_torque == 0 and "target_torque" in df_100.columns:
            tt_vals = df_100["target_torque"]
            tt_nonzero = tt_vals[tt_vals > 0]
            target_torque = float(tt_nonzero.median()) if len(tt_nonzero) > 0 else 0

        has_fault = fault_class != 0
        fault_code_max = int(df_100["fault_code"].max()) if "fault_code" in df_100 else 0

        # Write sensor CSV
        scenario_id = accepted
        out_name = f"live_conn_{scenario_id:04d}.csv"
        out_path = sensor_dir / out_name
        df_100.to_csv(out_path, index=False)

        # Manifest row
        manifest_rows.append({
            "scenario_id": scenario_id,
            "filename": out_name,
            "scenario_type": scenario_type,
            "fault_class": fault_class,
            "target_torque_ftlbs": round(target_torque, 1),
            "expected_turns": _estimate_expected_turns(target_torque),
            "num_samples": len(df_100),
            "duration_s": round(len(df_100) / 100.0, 2),
            "peak_torque_ftlbs": round(peak_torque, 1),
            "has_fault": has_fault,
            "fault_code": fault_code_max,
            "source": "live_capture",
            "rig_name": rig_name,
            "capture_file": csv_path.name,
            "quality_score": quality,
        })

        accepted += 1
        print(f"  OK: {csv_path.name} -> {out_name} "
              f"({len(df_100)} samples @ 100Hz, "
              f"label={scenario_type}, quality={quality:.2f})")

    # Write manifest
    if manifest_rows:
        manifest_df = pd.DataFrame(manifest_rows)
        manifest_path = output_dir / "manifest.csv"
        manifest_df.to_csv(manifest_path, index=False)
        print(f"\n  Dataset built:")
        print(f"    Accepted: {accepted} connections")
        print(f"    Rejected: {rejected} connections")
        print(f"    Manifest: {manifest_path}")
        print(f"    Sensor:   {sensor_dir}")

        # Class distribution
        print(f"\n  Class distribution:")
        for _, row in manifest_df.groupby("fault_class").size().reset_index(
                name="count").iterrows():
            print(f"    Class {row['fault_class']}: {row['count']} connections")
    else:
        print(f"\n  WARNING: No connections accepted (rejected {rejected})")


# ═══════════════════════════════════════════════════════════════════
# Multi-Rig Concurrent Capture
# ═══════════════════════════════════════════════════════════════════

def _quick_register_scan(client: 'ModbusTCPClient',
                         scan_ranges: List[Tuple[int, int]] = None
                         ) -> Dict[int, int]:
    """Quick scan to find all non-zero registers. Returns {addr: value}.

    Scans key GE CPE305 ranges in ~15-20 seconds at eWon latency.
    """
    if scan_ranges is None:
        # Common GE CPE305 register ranges
        scan_ranges = [
            (1, 500),       # System/config registers
            (1000, 2000),   # Extended I/O
            (2000, 2200),   # Encoders, counters
            (3400, 3500),   # RTC, diagnostics
            (6000, 6100),   # Shop unit layout (R6000-6030)
        ]
    result = {}
    for start, end in scan_ranges:
        addr = start
        while addr < end:
            count = min(100, end - addr)
            regs = client.read_holding_registers(addr, count)
            if regs:
                for i, val in enumerate(regs):
                    if val != 0:
                        result[addr + i] = val
            addr += count
    return result


def _build_adaptive_blocks(nonzero_regs: Dict[int, int],
                           reg_map: Dict = None) -> List[Tuple[int, int]]:
    """Build optimal block reads from discovered non-zero registers.

    Clusters nearby registers into blocks with padding, splits >125.
    """
    all_addrs = set(nonzero_regs.keys())
    if reg_map:
        for info in reg_map.values():
            all_addrs.add(info["addr"])
            if info["type"] in ("FLOAT32", "INT32", "UINT32"):
                all_addrs.add(info["addr"] + 1)

    if not all_addrs:
        return [(0, 10)]  # Fallback

    sorted_addrs = sorted(all_addrs)
    # Cluster: split when gap > 30 registers
    clusters = [[sorted_addrs[0]]]
    for a in sorted_addrs[1:]:
        if a - clusters[-1][-1] > 30:
            clusters.append([a])
        else:
            clusters[-1].append(a)

    # Pad each cluster: 3 before, 5 after
    blocks = []
    for cluster in clusters:
        pad_start = max(0, min(cluster) - 3)
        pad_end = max(cluster) + 5
        blocks.append((pad_start, pad_end - pad_start))

    # Split blocks > 125 (Modbus FC03 limit)
    final = []
    for bs, bc in blocks:
        while bc > 125:
            final.append((bs, 125))
            bs += 125
            bc -= 125
        if bc > 0:
            final.append((bs, bc))
    return final


def _rig_worker(profile_path: str, profile: 'MachineProfile',
                shared_state: dict, running_flag: list,
                args) -> None:
    """Capture worker thread for one rig. Updates shared_state dict."""
    name = profile.name
    reg_map, word_swap = reg_map_from_profile(profile)
    unit_id = profile.unit_id
    mapped_fields = set(reg_map.keys())
    smart_mode = args.smart

    # Initial block reads from profile (will be replaced by adaptive scan)
    block_reads = compute_block_reads(reg_map)
    scan_done = False

    # Client, engine, writer — all per-rig
    client = ModbusTCPClient(profile.plc_ip, profile.plc_port or 502,
                              timeout=args.timeout, unit_id=unit_id)
    engine = StateInferenceEngine() if smart_mode else None
    segmenter = engine if engine else ConnectionSegmenter()
    rig_output = os.path.join(args.output, name)
    seg_mode = "segmented" if smart_mode else args.segment_mode
    writer = LiveCSVWriter(rig_output, mode=seg_mode, prefix="capture")
    writer.start()

    poll_interval = 1.0 / args.hz
    poll_count = 0
    poll_errors = 0
    total_rows = 0
    t_start = time.time()
    reconnect_delay = 1.0

    while running_flag[0]:
        # Connect / reconnect
        if not client.connected:
            if client.connect():
                shared_state["connected"] = True
                shared_state["error"] = None
                reconnect_delay = 1.0

                # Adaptive scan on first connect: discover ALL registers
                if not scan_done and smart_mode:
                    shared_state["error"] = "Scanning..."
                    try:
                        nonzero = _quick_register_scan(client)
                        if nonzero:
                            block_reads = _build_adaptive_blocks(nonzero, reg_map)
                            shared_state["scan_regs"] = len(nonzero)
                        scan_done = True
                    except Exception:
                        scan_done = True  # Don't retry, use profile blocks
            else:
                shared_state["connected"] = False
                shared_state["error"] = f"Retry {reconnect_delay:.0f}s"
                wait_until = time.time() + reconnect_delay
                while running_flag[0] and time.time() < wait_until:
                    time.sleep(0.5)
                reconnect_delay = min(reconnect_delay * 2, 30.0)
                continue

        t_poll_start = time.time()

        # Read all blocks
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
                client.close()
                shared_state["connected"] = False
                poll_errors = 0
            continue

        poll_errors = 0
        poll_count += 1
        elapsed = time.time() - t_start
        now_str = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        values = decode_registers_from_dict(reg_dict, reg_map, word_swap)

        # Capture ALL non-zero unmapped registers as raw_RXXX
        if smart_mode:
            decoded_addrs = set()
            for info in reg_map.values():
                a = info["addr"]
                decoded_addrs.add(a)
                if info["type"] in ("FLOAT32", "INT32", "UINT32"):
                    decoded_addrs.add(a + 1)
            for addr, val in sorted(reg_dict.items()):
                if addr not in decoded_addrs and val != 0:
                    values[f"raw_R{addr}"] = float(decode_int16_signed(val))

        # State inference
        if engine:
            event, inferred = engine.update(values, elapsed, mapped_fields)
            values.update(inferred)
        else:
            event = segmenter.update(values, elapsed)

        if event:
            if "START" in event:
                writer.new_connection(segmenter.connection_num)
            elif "END" in event:
                if engine:
                    meta = engine.get_connection_metadata(elapsed)
                    sp = writer._current_path
                    if sp:
                        try:
                            with open(sp.with_suffix('.json'), 'w') as jf:
                                json.dump(meta, jf, indent=2)
                        except Exception:
                            pass
                writer.end_connection()

        # Idle decimation in smart mode
        skip_write = False
        if smart_mode and engine and engine.state == 0 and not engine.in_connection:
            if total_rows % 10 != 0:
                skip_write = True

        if not skip_write:
            writer.write_row(values, now_str, elapsed)
        total_rows += 1

        # Update shared state for display
        shared_state["values"] = values
        shared_state["elapsed"] = elapsed
        shared_state["hz"] = poll_count / max(elapsed, 0.001)
        shared_state["conn_num"] = segmenter.connection_num
        shared_state["rows"] = total_rows
        shared_state["engine"] = engine
        shared_state["mapped_fields"] = mapped_fields

        # Sleep to maintain target rate
        sleep_time = poll_interval - (time.time() - t_poll_start)
        if sleep_time > 0:
            time.sleep(sleep_time)

    # Cleanup
    writer.close()
    client.close()


def display_multi_rigs(rig_states: dict, first_draw: list):
    """Render compact multi-rig dashboard."""
    lines_per_rig = 3
    total_lines = len(rig_states) * lines_per_rig + 1  # +1 for header

    # Move cursor up to overwrite (skip on first draw)
    if first_draw[0]:
        first_draw[0] = False
    else:
        sys.stdout.write(f"\033[{total_lines}A")

    sys.stdout.write(f"\033[K  {'─' * 70}\n")

    for name, state in sorted(rig_states.items()):
        conn_str = "OK" if state.get("connected") else state.get("error", "---")
        elapsed = state.get("elapsed", 0.0)
        hz = state.get("hz", 0.0)
        rows = state.get("rows", 0)
        conn_num = state.get("conn_num", 0)
        vals = state.get("values", {})
        engine = state.get("engine")

        # State display
        if engine:
            st = engine.state
            st_name = CONNECTION_STATE_NAMES.get(st, f"?{st}")
            if engine.plc_state is not None:
                plc_name = CONNECTION_STATE_NAMES.get(engine.plc_state, "?")
                state_str = f"{st_name}[PLC:{plc_name}]"
            else:
                state_str = st_name
            path_str = ">".join(
                CONNECTION_STATE_NAMES.get(s, "?")[:3]
                for s in engine.state_path[-5:]
            )
        else:
            st = int(vals.get("connection_state", 0))
            state_str = CONNECTION_STATE_NAMES.get(st, f"?{st}")
            path_str = ""

        torque = vals.get("torque_ftlbs", 0.0)
        rpm_val = vals.get("rpm", 0.0)
        turns = vals.get("turns", 0.0)
        peak = vals.get("peak_torque", 0.0)
        target = vals.get("target_torque", 0.0)
        raw_count = sum(1 for k in vals if k.startswith("raw_R"))

        scan_regs = state.get("scan_regs", 0)
        scan_str = f" [{scan_regs}regs]" if scan_regs else ""
        sys.stdout.write(
            f"\033[K  {name[:25]:25s} | {conn_str:5s}{scan_str} | "
            f"{elapsed:7.0f}s {hz:4.1f}Hz | "
            f"Conn #{conn_num} {rows}rows | "
            f"State: {state_str}\n"
        )
        sys.stdout.write(
            f"\033[K    Torque:{torque:8.0f} RPM:{rpm_val:5.1f} "
            f"Turns:{turns:6.3f} Peak:{peak:8.0f} "
            f"Target:{target:8.0f} Raw:{raw_count}\n"
        )
        sys.stdout.write(
            f"\033[K    Path: {path_str:30s}\n"
        )

    sys.stdout.flush()


def _auto_discover_rigs(profile_dir: str, timeout: float = 5.0
                        ) -> List[Tuple[str, 'MachineProfile']]:
    """Auto-discover all reachable PLCs: VPN detection → subnet scan → identify.

    Returns list of (profile_path, MachineProfile) for each discovered rig.
    """
    try:
        from fleet_manager import EWonDetector, LANDiscovery, MachineIdentifier
        from discover_machine import find_unit_id, verify_word_swap
    except ImportError as e:
        print(f"  WARNING: Auto-discovery unavailable ({e})")
        print(f"  Falling back to profile directory scan.")
        return _load_profiles_from_dir(profile_dir)

    print(f"\n  Phase 1: Detecting eWon VPN tunnel...")
    detector = EWonDetector()
    tunnel = detector.detect_active_tunnel()

    if not tunnel:
        print(f"  No active eWon VPN tunnel detected.")
        print(f"  Falling back to profile directory scan.")
        return _load_profiles_from_dir(profile_dir)

    gw = tunnel['gateway_ip']
    device = tunnel.get('device_name') or 'unknown'
    latency = tunnel.get('latency_ms') or 0
    print(f"  VPN tunnel active: gateway={gw} device=\"{device}\" "
          f"latency={latency:.0f}ms")

    # Phase 2: Scan subnet for Modbus hosts
    print(f"\n  Phase 2: Scanning LAN for Modbus devices...")
    lan = LANDiscovery(gw)
    hosts = lan.discover_hosts(timeout=1.0)
    modbus_hosts = [h for h in hosts if h.get('has_modbus')]

    if not modbus_hosts:
        print(f"  No Modbus devices found on subnet.")
        return _load_profiles_from_dir(profile_dir)

    print(f"\n  Found {len(modbus_hosts)} Modbus device(s)")

    # Phase 3: Match each host to a profile or create a new one
    print(f"\n  Phase 3: Identifying machines...")
    identifier = MachineIdentifier(profile_dir)
    results = []

    for host_info in modbus_hosts:
        ip = host_info['ip']
        port = host_info.get('modbus_port', 502)

        # Skip the eWon gateway itself (usually has Modbus but no real data)
        if ip == gw:
            print(f"    {ip} — eWon gateway (skipped)")
            continue

        match_type, profile = identifier.identify({'ip': ip},
                                                   ewon_name=device)

        if match_type == 'exact_ip' and profile:
            # Known machine — use existing profile
            profile_path = str(Path(profile_dir) / f"{profile.name}.yaml")
            print(f"    {ip} — KNOWN: {profile.name} (loaded profile)")
            results.append((profile_path, profile))
        else:
            # Unknown machine — quick fingerprint and create temp profile
            print(f"    {ip} — NEW machine, creating profile...")
            try:
                uid = find_unit_id(ip, port, timeout)
                ws = verify_word_swap(ip, port, uid, timeout)
                # Create minimal profile
                rig_name = f"auto_{ip.replace('.', '_')}"
                new_profile = MachineProfile(
                    name=rig_name,
                    plc_ip=ip,
                    plc_port=port,
                    unit_id=uid,
                    word_swap=ws,
                    equipment_type="unknown",
                    customer="auto_discovered",
                    ewon_name=device,
                )
                # Save to profiles/ for future use
                out_path = Path(profile_dir) / f"{rig_name}.yaml"
                new_profile.to_yaml(str(out_path))
                print(f"    {ip} — Created: {out_path.name} "
                      f"(uid={uid}, ws={ws})")
                results.append((str(out_path), new_profile))
            except Exception as e:
                print(f"    {ip} — Failed to onboard: {e}")

    return results


def _load_profiles_from_dir(profile_dir: str
                            ) -> List[Tuple[str, 'MachineProfile']]:
    """Fallback: load all profiles with real IPs from directory."""
    profiles = []
    for f in sorted(Path(profile_dir).glob("*.yaml")):
        try:
            p = MachineProfile.from_yaml(str(f))
            if p.plc_ip and p.plc_ip != "127.0.0.1":
                profiles.append((str(f), p))
        except Exception as e:
            print(f"  WARNING: Skipping {f.name}: {e}")
    return profiles


def run_multi_capture(args):
    """Auto-discover and capture from all reachable PLCs concurrently.

    Pipeline: Detect VPN → Scan subnet → Identify PLCs → Capture all.
    Falls back to loading profiles from directory if discovery unavailable.
    """
    profile_dir = args.profile_dir

    print("\n" + "=" * 60)
    print("  PLC Live Data Capture — Auto Multi-Rig")
    print("=" * 60)

    # Auto-discover or load profiles
    profiles = _auto_discover_rigs(profile_dir, timeout=args.timeout)

    if not profiles:
        print(f"\n  No reachable PLCs found. Check VPN connection.")
        return

    mode_str = "SMART" if args.smart else "STANDARD"
    print(f"\n  Ready to capture {len(profiles)} rig(s) [{mode_str}]")
    print(f"  Target Hz:  {args.hz}")
    print(f"  Output:     {args.output}/")
    for path, p in profiles:
        print(f"    - {p.name:25s} @ {p.plc_ip}:{p.plc_port or 502}")
    print()

    # Shared state dicts and running flag
    running_flag = [True]
    rig_states = {}

    threads = []
    for path, profile in profiles:
        state = {
            "connected": False, "values": {}, "elapsed": 0.0,
            "hz": 0.0, "conn_num": 0, "rows": 0, "engine": None,
            "error": "Starting...", "mapped_fields": set(),
            "scan_regs": 0,
        }
        rig_states[profile.name] = state
        t = threading.Thread(
            target=_rig_worker,
            args=(path, profile, state, running_flag, args),
            daemon=True, name=f"rig-{profile.name}"
        )
        threads.append(t)

    for t in threads:
        t.start()

    # Display loop (main thread)
    first_draw = [True]
    try:
        time.sleep(1)
        print("  Capturing... (Ctrl+C to stop)\n")
        total_display_lines = len(rig_states) * 3 + 1
        for _ in range(total_display_lines):
            print()
        while running_flag[0]:
            display_multi_rigs(rig_states, first_draw)
            time.sleep(0.5)
    except KeyboardInterrupt:
        running_flag[0] = False
        print(f"\n\n  Stopping all captures...")

    for t in threads:
        t.join(timeout=5)

    print(f"\n  Multi-rig capture complete:")
    for name, state in sorted(rig_states.items()):
        scan_info = f" ({state['scan_regs']} regs)" if state.get('scan_regs') else ""
        print(f"    {name:25s}: {state['rows']:6d} rows, "
              f"{state['elapsed']:.0f}s, "
              f"{state.get('conn_num', 0)} conns{scan_info}")
    print(f"    Output: {args.output}/")


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

  # Smart capture: infers state, segments connections, writes metadata
  python capture_live.py --host 10.0.0.1 --smart

  # Multi-rig: capture all profiles concurrently
  python capture_live.py --multi --smart

  # Multi-rig with custom profiles directory
  python capture_live.py --multi --smart --profile-dir ./profiles

  # Build training dataset from captured connections
  python capture_live.py --build-dataset --input ./live_captures --output ./data/live_rig709

  # Use custom register map
  python capture_live.py --host 10.0.0.1 --register-map my_registers.json
        """)

    parser.add_argument("--host", default=None,
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

    # Smart capture mode
    parser.add_argument("--smart", action="store_true",
                        help="Enable smart capture: state inference, auto-segmentation, "
                             "JSON metadata, idle decimation")

    # Multi-rig concurrent capture
    parser.add_argument("--multi", action="store_true",
                        help="Capture from all rigs concurrently (reads all profiles)")
    parser.add_argument("--profile-dir", default="./profiles",
                        help="Directory with .yaml profiles for --multi (default: ./profiles)")

    # Dataset builder mode
    parser.add_argument("--build-dataset", action="store_true",
                        help="Build training dataset from captured connection CSVs")
    parser.add_argument("--input", default="./live_captures",
                        help="Input directory for --build-dataset (default: ./live_captures)")
    parser.add_argument("--min-duration", type=float, default=5.0,
                        help="Min connection duration in seconds for --build-dataset")
    parser.add_argument("--min-quality", type=float, default=0.3,
                        help="Min quality score for --build-dataset (0-1)")
    parser.add_argument("--rig-name", default="unknown_rig",
                        help="Rig name tag for manifest metadata")

    args = parser.parse_args()

    # Dataset builder mode (no PLC connection needed)
    if args.build_dataset:
        print("\n" + "=" * 60)
        print("  PLC Live Data Capture — Dataset Builder")
        print("=" * 60)
        build_dataset(args)
        return

    # Multi-rig mode (no --host needed, uses all profiles)
    if args.multi:
        run_multi_capture(args)
        return

    # All other modes require --host
    if not args.host:
        parser.error("--host is required for capture and discovery modes "
                     "(or use --multi for all rigs)")

    print("\n" + "=" * 60)
    print("  PLC Live Data Capture — Modbus TCP")
    print("=" * 60)
    print(f"  Host:     {args.host}:{args.port}")
    mode_str = "DISCOVERY" if args.discover else ("SMART CAPTURE" if args.smart else "CAPTURE")
    print(f"  Mode:     {mode_str}")

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
