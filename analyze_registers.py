#!/usr/bin/env python3
"""
Detailed Register Analysis for Rig Identification
====================================================
Scans ALL PLC registers, takes multiple snapshots to detect which values
change vs stay constant, decodes every register as INT16/FLOAT32/INT32,
and annotates likely meanings based on domain knowledge.

Output: detailed log file + summary for Steve to verify.

Usage:
  python analyze_registers.py --host 129.168.1.25
  python analyze_registers.py --host 129.168.1.25 --passes 5 --delay 3
"""
import argparse
import struct
import socket
import time
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict


# ═══════════════════════════════════════════════════════════════════
# Minimal Modbus TCP Client (no dependencies)
# ═══════════════════════════════════════════════════════════════════

class ModbusClient:
    def __init__(self, host, port=502, unit_id=0, timeout=5.0):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.sock = None
        self.tid = 0

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((self.host, self.port))
            return True
        except Exception as e:
            print(f"  Connection failed: {e}")
            return False

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def read_holding_registers(self, start, count):
        """FC03 read holding registers. Returns list of uint16 or None."""
        if not self.sock:
            return None
        self.tid = (self.tid + 1) & 0xFFFF
        # MBAP header + FC03 PDU
        pdu = struct.pack('>BHH', 3, start, count)
        mbap = struct.pack('>HHHB', self.tid, 0, len(pdu) + 1, self.unit_id)
        try:
            self.sock.sendall(mbap + pdu)
            header = self._recv_exact(7)
            if not header:
                return None
            _, _, length, _ = struct.unpack('>HHHB', header)
            payload = self._recv_exact(length - 1)
            if not payload or payload[0] != 3:
                return None
            byte_count = payload[1]
            data = payload[2:2 + byte_count]
            return [struct.unpack('>H', data[i:i+2])[0]
                    for i in range(0, len(data), 2)]
        except Exception:
            return None

    def _recv_exact(self, n):
        buf = b''
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf


# ═══════════════════════════════════════════════════════════════════
# Decoding helpers
# ═══════════════════════════════════════════════════════════════════

def decode_int16_signed(val):
    return val - 65536 if val > 32767 else val


def decode_float32_ge(low_word, high_word):
    """GE word-swapped: low word at N, high word at N+1."""
    raw = struct.pack('>HH', high_word, low_word)
    f = struct.unpack('>f', raw)[0]
    return f


def decode_float32_std(high_word, low_word):
    """Standard IEEE: high word at N, low word at N+1."""
    raw = struct.pack('>HH', high_word, low_word)
    f = struct.unpack('>f', raw)[0]
    return f


def is_sane_float(f):
    """Check if float value is in a physically reasonable range."""
    import math
    if math.isnan(f) or math.isinf(f):
        return False
    return -1e6 < f < 1e6


# ═══════════════════════════════════════════════════════════════════
# Domain knowledge — what values look like for top drive rigs
# ═══════════════════════════════════════════════════════════════════

def classify_value(addr, int16_val, f32_ge, f32_std, is_changing, change_range):
    """Guess what a register might be based on value + behavior."""
    guesses = []
    i16 = decode_int16_signed(int16_val)

    # FLOAT32 GE-decoded value analysis
    if is_sane_float(f32_ge):
        v = abs(f32_ge)
        if 500 < v < 50000 and is_changing:
            guesses.append(f"TORQUE? (GE_f32={f32_ge:.1f} ft-lbs, varies)")
        elif 500 < v < 50000 and not is_changing:
            guesses.append(f"TORQUE_SETPOINT? (GE_f32={f32_ge:.1f} ft-lbs, constant)")
        elif 0 < v < 300 and is_changing:
            guesses.append(f"RPM? (GE_f32={f32_ge:.1f}, varies)")
        elif 0 < v < 300 and not is_changing:
            guesses.append(f"RPM_SETPOINT? (GE_f32={f32_ge:.1f}, constant)")
        elif 50 < v < 500:
            if is_changing:
                guesses.append(f"TEMPERATURE? or PRESSURE? (GE_f32={f32_ge:.1f}, varies)")
            else:
                guesses.append(f"TEMPERATURE? (GE_f32={f32_ge:.1f}, constant)")
        elif 0 < v < 50:
            if is_changing:
                guesses.append(f"TURNS? (GE_f32={f32_ge:.3f}, varies)")
            else:
                guesses.append(f"SMALL_FLOAT (GE_f32={f32_ge:.4f}, constant)")

    # INT16 value analysis
    if 0 <= i16 <= 13:
        guesses.append(f"STATE/MODE? (int16={i16})")
    if 500 < i16 < 50000:
        guesses.append(f"TORQUE_INT? or TARGET? (int16={i16})")
    if 0 < i16 < 200:
        guesses.append(f"RPM_INT? or COUNT? (int16={i16})")
    if 50 < abs(i16) < 400:
        guesses.append(f"TEMP_INT? (int16={i16})")

    # Large constant values (config/firmware)
    if not is_changing and int16_val > 10000:
        guesses.append(f"CONFIG/FW? (uint16={int16_val}, constant)")

    # Encoder-like (large, changing, incrementing)
    if is_changing and change_range > 100:
        guesses.append(f"ENCODER/COUNTER? (range={change_range})")

    # Negative constant
    if not is_changing and i16 < 0:
        guesses.append(f"STATUS_FLAG? (int16={i16}, constant)")

    if not guesses:
        guesses.append(f"UNKNOWN (uint16={int16_val}, int16={i16})")

    return guesses


# ═══════════════════════════════════════════════════════════════════
# Main analysis
# ═══════════════════════════════════════════════════════════════════

def scan_range(client, start, end, block_size=50):
    """Read a register range, return {addr: raw_uint16}."""
    regs = {}
    addr = start
    while addr < end:
        count = min(block_size, end - addr)
        result = client.read_holding_registers(addr, count)
        if result:
            for i, val in enumerate(result):
                regs[addr + i] = val
        addr += count
        time.sleep(0.05)
    return regs


def main():
    parser = argparse.ArgumentParser(
        description='Detailed PLC register analysis for rig identification')
    parser.add_argument('--host', required=True, help='PLC IP address')
    parser.add_argument('--port', type=int, default=502)
    parser.add_argument('--unit-id', type=int, default=0)
    parser.add_argument('--passes', type=int, default=5,
                        help='Number of snapshot passes (default: 5)')
    parser.add_argument('--delay', type=float, default=2.0,
                        help='Seconds between passes (default: 2)')
    parser.add_argument('--output', default=None,
                        help='Output log file (default: auto-named)')
    args = parser.parse_args()

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = args.output or f"register_analysis_{args.host.replace('.','_')}_{timestamp}.txt"

    # Register ranges to scan
    scan_ranges = [
        (0, 500, "Main PLC registers (config, I/O, process data)"),
        (500, 1000, "Extended registers"),
        (1000, 1500, "Extended I/O"),
        (2000, 2200, "Encoder / counter registers"),
        (3400, 3600, "RTC / diagnostics"),
        (5000, 5600, "eWON tag registers"),
        (6000, 6100, "Shop unit layout (R6000-6030)"),
    ]

    lines = []
    def log(msg=""):
        lines.append(msg)
        print(msg)

    log("=" * 80)
    log(f"  DETAILED REGISTER ANALYSIS — {args.host}:{args.port}")
    log(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"  Passes: {args.passes} x {args.delay}s delay")
    log("=" * 80)

    client = ModbusClient(args.host, args.port, args.unit_id, timeout=8.0)
    if not client.connect():
        log("FATAL: Cannot connect to PLC")
        return
    log(f"  Connected to {args.host}:{args.port} (unit_id={args.unit_id})")

    # ── Pass 1: Full scan to find all non-zero registers ──
    log(f"\n{'─' * 80}")
    log(f"  PHASE 1: Full register scan")
    log(f"{'─' * 80}")

    all_nonzero = {}
    for start, end, desc in scan_ranges:
        log(f"\n  Scanning R{start}-R{end} ({desc})...")
        regs = scan_range(client, start, end)
        nz = {a: v for a, v in regs.items() if v != 0}
        log(f"    Found {len(nz)} non-zero registers out of {len(regs)} scanned")
        all_nonzero.update(nz)

    log(f"\n  TOTAL: {len(all_nonzero)} non-zero registers across all ranges")

    # ── Pass 2-N: Multiple snapshots to detect changing values ──
    log(f"\n{'─' * 80}")
    log(f"  PHASE 2: Taking {args.passes} snapshots ({args.delay}s apart)")
    log(f"{'─' * 80}")

    # Only re-scan addresses where we found non-zero values (plus neighbors)
    addrs_to_watch = set()
    for a in all_nonzero:
        addrs_to_watch.update(range(max(0, a - 2), a + 3))

    # Build efficient block reads from watch addresses
    sorted_addrs = sorted(addrs_to_watch)
    if not sorted_addrs:
        log("  No non-zero registers found. PLC may not be running.")
        client.close()
        return

    # Cluster into blocks
    clusters = [[sorted_addrs[0]]]
    for a in sorted_addrs[1:]:
        if a - clusters[-1][-1] > 10:
            clusters.append([a])
        else:
            clusters[-1].append(a)
    blocks = [(min(c), max(c) - min(c) + 1) for c in clusters]

    snapshots = []  # list of {addr: val} dicts
    for p in range(args.passes):
        log(f"  Snapshot {p + 1}/{args.passes}...")
        snap = {}
        for bstart, bcount in blocks:
            # Split if > 125
            offset = 0
            while offset < bcount:
                chunk = min(125, bcount - offset)
                result = client.read_holding_registers(bstart + offset, chunk)
                if result:
                    for i, val in enumerate(result):
                        snap[bstart + offset + i] = val
                offset += chunk
            time.sleep(0.02)
        snapshots.append(snap)
        if p < args.passes - 1:
            time.sleep(args.delay)

    client.close()
    log(f"  Done. Collected {len(snapshots)} snapshots.")

    # ── Analysis: classify each register ──
    log(f"\n{'─' * 80}")
    log(f"  PHASE 3: Register analysis")
    log(f"{'─' * 80}")

    # Compute statistics for each non-zero register
    reg_stats = {}
    for addr in sorted(all_nonzero.keys()):
        values = [s.get(addr, 0) for s in snapshots]
        unique = set(values)
        is_changing = len(unique) > 1
        val_min = min(values)
        val_max = max(values)
        change_range = val_max - val_min
        reg_stats[addr] = {
            'values': values,
            'is_changing': is_changing,
            'min': val_min,
            'max': val_max,
            'range': change_range,
            'latest': values[-1] if values else 0,
        }

    # Decode float32 pairs
    sorted_addrs = sorted(all_nonzero.keys())
    float_pairs = {}  # addr -> (ge_float, std_float)
    for addr in sorted_addrs:
        next_addr = addr + 1
        if next_addr in all_nonzero or next_addr in {s for snap in snapshots for s in snap}:
            latest = snapshots[-1] if snapshots else {}
            w0 = latest.get(addr, all_nonzero.get(addr, 0))
            w1 = latest.get(next_addr, all_nonzero.get(next_addr, 0))
            ge = decode_float32_ge(w0, w1)
            std = decode_float32_std(w0, w1)
            float_pairs[addr] = (ge, std)

    # ── Output detailed log ──
    log(f"\n{'=' * 80}")
    log(f"  REGISTER MAP — ALL NON-ZERO REGISTERS")
    log(f"  Legend: [C]=Changing  [S]=Static  GE=GE word-swap  STD=Standard IEEE")
    log(f"{'=' * 80}")
    log("")
    log(f"  {'Addr':<8} {'Raw':>7} {'Int16':>7} {'Hex':>7} "
        f"{'C/S':>3} {'Range':>7}  {'GE Float32':>14} {'STD Float32':>14}  "
        f"{'Best Guess'}")
    log(f"  {'─' * 8} {'─' * 7} {'─' * 7} {'─' * 7} "
        f"{'─' * 3} {'─' * 7}  {'─' * 14} {'─' * 14}  "
        f"{'─' * 30}")

    for addr in sorted_addrs:
        stat = reg_stats.get(addr, {})
        raw = stat.get('latest', all_nonzero[addr])
        i16 = decode_int16_signed(raw)
        is_changing = stat.get('is_changing', False)
        change_range = stat.get('range', 0)
        tag = "[C]" if is_changing else "[S]"

        ge_f, std_f = float_pairs.get(addr, (float('nan'), float('nan')))
        ge_str = f"{ge_f:14.4f}" if is_sane_float(ge_f) else f"{'---':>14}"
        std_str = f"{std_f:14.4f}" if is_sane_float(std_f) else f"{'---':>14}"

        guesses = classify_value(addr, raw, ge_f, std_f, is_changing, change_range)
        guess_str = " | ".join(guesses[:2])

        log(f"  R{addr:<6d} {raw:>7d} {i16:>7d} 0x{raw:04X} "
            f"{tag:>3} {change_range:>7}  {ge_str} {std_str}  "
            f"{guess_str}")

    # ── Summary: grouped by likely meaning ──
    log(f"\n{'=' * 80}")
    log(f"  SUMMARY — LIKELY REGISTER ASSIGNMENTS")
    log(f"{'=' * 80}")

    categories = defaultdict(list)
    for addr in sorted_addrs:
        stat = reg_stats.get(addr, {})
        raw = stat.get('latest', all_nonzero[addr])
        ge_f, _ = float_pairs.get(addr, (float('nan'), float('nan')))
        is_changing = stat.get('is_changing', False)
        change_range = stat.get('range', 0)
        guesses = classify_value(addr, raw, ge_f, float('nan'),
                                  is_changing, change_range)
        for g in guesses:
            key = g.split('?')[0].split('(')[0].strip()
            categories[key].append((addr, raw, ge_f, is_changing))

    priority_order = [
        'TORQUE', 'TORQUE_SETPOINT', 'TORQUE_INT',
        'RPM', 'RPM_SETPOINT', 'RPM_INT',
        'TURNS', 'TEMPERATURE', 'PRESSURE',
        'STATE/MODE', 'ENCODER/COUNTER',
        'CONFIG/FW', 'STATUS_FLAG', 'SMALL_FLOAT', 'UNKNOWN',
    ]
    for cat in priority_order:
        entries = categories.get(cat, [])
        if not entries:
            continue
        log(f"\n  {cat}:")
        for addr, raw, ge_f, changing in entries:
            i16 = decode_int16_signed(raw)
            tag = "VARIES" if changing else "const"
            ge_str = f"GE_f32={ge_f:.2f}" if is_sane_float(ge_f) else ""
            log(f"    R{addr:<6d}  raw={raw:>7d}  int16={i16:>7d}  "
                f"{ge_str:>20s}  [{tag}]")

    # ── HXI template comparison ──
    log(f"\n{'=' * 80}")
    log(f"  HXI TEMPLATE COMPARISON (confirmed on Rig 709)")
    log(f"{'=' * 80}")
    hxi_map = {
        163: ("torque", "FLOAT32", "ft-lbs"),
        165: ("turns", "FLOAT32", "turns"),
        167: ("temperature", "FLOAT32", "degF"),
        169: ("rpm", "FLOAT32", "RPM"),
        175: ("connection_state", "INT16", "enum 0-13"),
        178: ("target_torque", "INT16", "ft-lbs"),
        180: ("shoulder_torque", "INT16", "ft-lbs"),
        185: ("connection_count", "INT16", "count"),
        189: ("hookload", "FLOAT32", "lbs (x0.001 = klbs)"),
        2076: ("encoder_counts_LSW", "INT32", "counts"),
        2077: ("encoder_counts_MSW", "INT16", "MSW"),
    }

    for addr in sorted(hxi_map.keys()):
        name, dtype, unit = hxi_map[addr]
        stat = reg_stats.get(addr, {})
        raw = stat.get('latest', 0)
        is_changing = stat.get('is_changing', False)
        change_range = stat.get('range', 0)
        tag = "VARIES" if is_changing else "const"

        if dtype == "FLOAT32":
            ge_f, std_f = float_pairs.get(addr, (float('nan'), float('nan')))
            ge_str = f"{ge_f:.2f}" if is_sane_float(ge_f) else "---"
            std_str = f"{std_f:.2f}" if is_sane_float(std_f) else "---"
            val_str = f"GE={ge_str}, STD={std_str}"
        else:
            i16 = decode_int16_signed(raw)
            val_str = f"int16={i16}, raw={raw}"

        present = "PRESENT" if raw != 0 else "ZERO"
        log(f"  R{addr:<6d} {name:<22s} {dtype:<8s} {unit:<20s} "
            f"{present:<8s} [{tag}]  {val_str}")

    # ── Raw snapshot dump ──
    log(f"\n{'=' * 80}")
    log(f"  RAW SNAPSHOTS (all {args.passes} passes)")
    log(f"{'=' * 80}")
    header = f"  {'Addr':<8}"
    for i in range(len(snapshots)):
        header += f" {'Pass'+str(i+1):>8}"
    header += f" {'Delta':>8}"
    log(header)
    log(f"  {'─' * 8}" + f" {'─' * 8}" * len(snapshots) + f" {'─' * 8}")

    for addr in sorted_addrs:
        vals = [s.get(addr, 0) for s in snapshots]
        delta = max(vals) - min(vals)
        row = f"  R{addr:<6d}"
        for v in vals:
            row += f" {v:>8d}"
        row += f" {delta:>8d}"
        if delta > 0:
            row += "  <-- CHANGING"
        log(row)

    # ── Write output ──
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    log(f"\n{'=' * 80}")
    log(f"  Analysis saved to: {output_file}")
    log(f"  Send this file to Steve for verification.")
    log(f"{'=' * 80}")


if __name__ == '__main__':
    main()
