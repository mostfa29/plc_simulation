#!/usr/bin/env python3
"""
Smart Machine Discovery & Register Mapping
=============================================
Combines network discovery with intelligent differential scanning
to automatically identify live PLC registers and classify them.

Designed for Steve's GE CPE305 PLCs accessed via eWon VPN tunnels
where latency is ~400ms per Modbus read.

Algorithm:
    1. Find PLCs on the network (eWon VPN subnet 129.168.x.x)
    2. Run differential scan (3 passes with 10s waits)
    3. Classify changing registers heuristically
    4. Generate a MachineProfile YAML automatically

Usage:
    # Full discovery on known PLC
    python discover_machine.py --host 129.168.1.25

    # Scan specific register range
    python discover_machine.py --host 129.168.1.25 --start 1 --end 5000

    # Generate profile YAML
    python discover_machine.py --host 129.168.1.25 --output profiles/rig709.yaml

    # Quick scan (fewer passes, faster)
    python discover_machine.py --host 129.168.1.25 --passes 2 --wait 5
"""
import argparse
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import MachineProfile, RegisterDef


# ═══════════════════════════════════════════════════════════════════
# Raw Modbus TCP Client (minimal, same as capture_live.py)
# ═══════════════════════════════════════════════════════════════════

class ModbusTCPClient:
    """Minimal Modbus TCP client for register scanning."""

    def __init__(self, host: str, port: int = 502, timeout: float = 5.0,
                 unit_id: int = 0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.unit_id = unit_id
        self._sock: Optional[socket.socket] = None
        self._trans_id = 0

    def connect(self) -> bool:
        try:
            self.close()
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self.timeout)
            self._sock.connect((self.host, self.port))
            return True
        except (socket.error, OSError) as e:
            print(f"  [CONN] Failed: {e}")
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

    def read_holding_registers(self, start: int, count: int) -> Optional[List[int]]:
        """FC03: Read Holding Registers."""
        if not self._sock:
            return None
        self._trans_id = (self._trans_id + 1) & 0xFFFF
        pdu = struct.pack('>BHH', 0x03, start, count)
        mbap = struct.pack('>HHHB', self._trans_id, 0, len(pdu) + 1, self.unit_id)
        try:
            self._sock.sendall(mbap + pdu)
            header = self._recv_exact(7)
            if not header:
                return None
            _, _, length, _ = struct.unpack('>HHHB', header)
            resp_pdu = self._recv_exact(length - 1)
            if not resp_pdu:
                return None
            fc = resp_pdu[0]
            if fc & 0x80:
                return None
            byte_count = resp_pdu[1]
            data = resp_pdu[2:2 + byte_count]
            regs = []
            for i in range(0, len(data), 2):
                regs.append(struct.unpack('>H', data[i:i+2])[0])
            return regs
        except (socket.timeout, socket.error, OSError, struct.error):
            self._sock = None
            return None

    def _recv_exact(self, n: int) -> Optional[bytes]:
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
# GE CPE305 FLOAT32 Decoding
# ═══════════════════════════════════════════════════════════════════

def decode_ge_float32(low_word: int, high_word: int) -> float:
    """Decode GE CPE305 word-swapped FLOAT32."""
    packed = struct.pack('>HH', high_word, low_word)
    return struct.unpack('>f', packed)[0]


def is_sane_float32(low_word: int, high_word: int) -> Tuple[bool, float]:
    """Check if two registers form a sane FLOAT32 value."""
    try:
        val = decode_ge_float32(low_word, high_word)
        import math
        if math.isnan(val) or math.isinf(val):
            return False, 0.0
        if abs(val) > 1e8:
            return False, 0.0
        return True, val
    except (struct.error, OverflowError):
        return False, 0.0


# ═══════════════════════════════════════════════════════════════════
# Register Candidate
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RegisterCandidate:
    """A register that was observed to change between scan passes."""
    address: int
    values: List[int] = field(default_factory=list)
    float32_values: List[float] = field(default_factory=list)
    is_float32_pair: bool = False
    classification: str = "unknown"
    confidence: float = 0.0
    data_type: str = "INT16"
    unit: str = ""


# ═══════════════════════════════════════════════════════════════════
# Register Scanner
# ═══════════════════════════════════════════════════════════════════

class RegisterScanner:
    """Identifies live process variables via differential scanning.

    Like a doctor finding a pulse: static registers (config, PID gains)
    don't change between reads. Live sensor data (torque, RPM, pressure)
    changes every scan.

    Algorithm:
        1. Read all registers in blocks (takes ~40s at 400ms/block for R1-R10000)
        2. Wait 10 seconds
        3. Read again
        4. Compare: registers that CHANGED are live sensor data
        5. Registers that changed in ALL diffs are confirmed live variables
    """

    def __init__(self, host: str, port: int = 502, unit_id: int = 0,
                 word_swap: bool = True, timeout: float = 5.0):
        self.client = ModbusTCPClient(host, port, timeout=timeout,
                                       unit_id=unit_id)
        self.word_swap = word_swap
        self.host = host
        self.port = port

    def full_scan(self, start: int = 1, end: int = 10000,
                  block_size: int = 100) -> Dict[int, int]:
        """Read all registers, return {address: value} for non-zero."""
        if not self.client.connected:
            if not self.client.connect():
                return {}

        result = {}
        addr = start
        total_blocks = (end - start + block_size - 1) // block_size
        block_num = 0

        while addr < end:
            count = min(block_size, end - addr)
            block_num += 1
            regs = self.client.read_holding_registers(addr, count)

            if regs is None:
                # Reconnect and retry once
                self.client.close()
                time.sleep(0.5)
                if not self.client.connect():
                    print(f"  [SCAN] Connection lost at R{addr}, skipping block")
                    addr += count
                    continue
                regs = self.client.read_holding_registers(addr, count)
                if regs is None:
                    addr += count
                    continue

            for i, val in enumerate(regs):
                if val != 0:
                    result[addr + i] = val

            if block_num % 10 == 0:
                pct = block_num / total_blocks * 100
                print(f"  [SCAN] {pct:5.1f}%  R{addr:5d}  "
                      f"({len(result)} non-zero so far)")

            addr += count

        return result

    def differential_scan(self, start: int = 1, end: int = 10000,
                          block_size: int = 100, num_passes: int = 3,
                          wait_seconds: int = 10) -> Dict[int, RegisterCandidate]:
        """Run multiple passes and identify changing registers."""
        passes = []

        for pass_num in range(num_passes):
            print(f"\n  === Pass {pass_num + 1}/{num_passes} ===")
            t_start = time.time()
            scan = self.full_scan(start, end, block_size)
            elapsed = time.time() - t_start
            passes.append(scan)
            print(f"  Pass {pass_num + 1}: {len(scan)} non-zero registers "
                  f"({elapsed:.1f}s)")

            if pass_num < num_passes - 1:
                print(f"  Waiting {wait_seconds}s before next pass...")
                time.sleep(wait_seconds)

        # Find registers that changed between passes
        candidates = {}
        all_addrs = set()
        for scan in passes:
            all_addrs.update(scan.keys())

        for addr in sorted(all_addrs):
            values = [scan.get(addr, 0) for scan in passes]
            # Check if value changed across passes
            if len(set(values)) > 1:
                cand = RegisterCandidate(address=addr, values=values)

                # Check if this + next register form a changing FLOAT32
                next_addr = addr + 1
                if next_addr in all_addrs:
                    next_values = [scan.get(next_addr, 0) for scan in passes]
                    float_values = []
                    all_sane = True
                    for low, high in zip(values, next_values):
                        sane, fval = is_sane_float32(low, high)
                        if sane:
                            float_values.append(fval)
                        else:
                            all_sane = False
                    if all_sane and float_values:
                        cand.is_float32_pair = True
                        cand.float32_values = float_values
                        cand.data_type = "FLOAT32"

                candidates[addr] = cand

        print(f"\n  Found {len(candidates)} changing registers")
        return candidates

    def classify_registers(self, candidates: Dict[int, RegisterCandidate]
                           ) -> Dict[int, RegisterCandidate]:
        """Heuristically classify what each live register represents.

        Classification heuristics for live registers:
        - FLOAT32 in range 0-50000, changes slowly -> torque (ft-lbs)
        - FLOAT32 in range 0-100, oscillates rapidly -> RPM
        - FLOAT32 in range 0-5000, tracks torque -> pressure (PSI)
        - FLOAT32 in range 50-200, changes very slowly -> temperature (F)
        - INT32 monotonically increasing -> encoder counts
        - INT16 in range 0-10 -> operating mode / state enum
        """
        for addr, cand in candidates.items():
            if cand.is_float32_pair and cand.float32_values:
                fvals = cand.float32_values
                fmin = min(fvals)
                fmax = max(fvals)
                frange = fmax - fmin
                fmean = sum(fvals) / len(fvals)

                # Torque: FLOAT32, range 0-50000, moderate change rate
                if 0 <= fmin and fmax <= 60000 and frange < 30000:
                    if fmean > 100 and frange > 10:
                        cand.classification = "torque"
                        cand.confidence = 0.5
                        cand.unit = "ft-lbs"

                # RPM: FLOAT32, range 0-100ish, oscillates
                if 0 <= fmin and fmax <= 300 and frange > 0.5:
                    if fmean < 100:
                        cand.classification = "rpm"
                        cand.confidence = 0.4
                        cand.unit = "RPM"

                # Pressure: FLOAT32, range 0-5000
                if 0 <= fmin and fmax <= 6000:
                    if 100 < fmean < 4000:
                        if cand.classification != "torque":
                            cand.classification = "pressure"
                            cand.confidence = 0.4
                            cand.unit = "PSI"

                # Temperature: FLOAT32, range 50-200, very slow change
                if 30 <= fmin and fmax <= 250:
                    if frange < 5:
                        cand.classification = "temperature"
                        cand.confidence = 0.6
                        cand.unit = "degF"

                # Hookload: FLOAT32, range 0-200000
                if fmean > 5000 and fmax < 300000:
                    if cand.classification == "unknown":
                        cand.classification = "hookload"
                        cand.confidence = 0.3
                        cand.unit = "lbs"

            else:
                # INT16 analysis
                vals = cand.values
                vmin = min(vals)
                vmax = max(vals)

                # Monotonically increasing -> encoder counts
                is_monotonic = all(vals[i] <= vals[i+1] for i in range(len(vals)-1))
                if is_monotonic and vmax > vmin + 10:
                    cand.classification = "encoder_counts"
                    cand.confidence = 0.5
                    cand.data_type = "INT32"
                    cand.unit = "counts"

                # Small range 0-10 -> state/mode enum
                elif 0 <= vmin and vmax <= 15:
                    cand.classification = "state_or_mode"
                    cand.confidence = 0.4

        return candidates

    def generate_profile(self, candidates: Dict[int, RegisterCandidate],
                         name: str = "discovered_machine",
                         output_path: Optional[str] = None) -> MachineProfile:
        """Generate a MachineProfile from classified registers."""
        reg_map = {}

        # Pick best candidate for each variable type
        type_candidates = {}
        for addr, cand in candidates.items():
            if cand.classification != "unknown":
                key = cand.classification
                if key not in type_candidates or cand.confidence > type_candidates[key].confidence:
                    type_candidates[key] = cand

        # Map classifications to register names
        name_map = {
            'torque': 'torque',
            'rpm': 'rpm',
            'pressure': 'pressure',
            'temperature': 'temperature',
            'encoder_counts': 'encoder_counts',
            'hookload': 'hookload',
            'state_or_mode': 'operating_mode',
        }

        for class_name, cand in type_candidates.items():
            reg_name = name_map.get(class_name, class_name)
            reg_map[reg_name] = RegisterDef(
                address=cand.address,
                data_type=cand.data_type,
                unit=cand.unit,
                description=f"Auto-discovered (confidence: {cand.confidence:.0%})",
            )

        profile = MachineProfile(
            name=name,
            plc_ip=self.host,
            plc_port=self.port,
            unit_id=self.client.unit_id,
            word_swap=self.word_swap,
            reg_map=reg_map,
        )

        if output_path:
            profile.to_yaml(output_path)
            print(f"\n  Profile saved to: {output_path}")

        return profile


# ═══════════════════════════════════════════════════════════════════
# Network Discovery
# ═══════════════════════════════════════════════════════════════════

def discover_network(subnet: str = "129.168.1", port: int = 502,
                     timeout: float = 1.0) -> List[str]:
    """Scan eWon VPN subnet for hosts with Modbus TCP (port 502)."""
    print(f"\n  Scanning {subnet}.1-254 for Modbus TCP hosts...")
    live_hosts = []

    for i in range(1, 255):
        host = f"{subnet}.{i}"
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                print(f"    FOUND: {host}:{port}")
                live_hosts.append(host)
        except (socket.error, OSError):
            pass

    print(f"\n  Found {len(live_hosts)} Modbus TCP hosts")
    return live_hosts


def find_unit_id(host: str, port: int = 502, timeout: float = 3.0) -> int:
    """Test unit_ids 0-5 to find the working one."""
    for uid in range(6):
        client = ModbusTCPClient(host, port, timeout=timeout, unit_id=uid)
        if client.connect():
            regs = client.read_holding_registers(0, 1)
            client.close()
            if regs is not None:
                print(f"    Unit ID {uid}: OK")
                return uid
    return 0


def verify_word_swap(host: str, port: int = 502, unit_id: int = 0,
                     timeout: float = 3.0) -> bool:
    """Test both byte orders and determine which gives sane floats."""
    client = ModbusTCPClient(host, port, timeout=timeout, unit_id=unit_id)
    if not client.connect():
        return True  # Default to word-swapped (GE standard)

    # Read some registers that might contain floats
    regs = client.read_holding_registers(100, 20)
    client.close()

    if not regs:
        return True

    # Try both orders, count sane results
    ws_sane = 0
    normal_sane = 0
    for i in range(0, len(regs) - 1, 2):
        if regs[i] != 0 or regs[i+1] != 0:
            # Word-swapped (GE): low at N, high at N+1
            sane_ws, _ = is_sane_float32(regs[i], regs[i+1])
            if sane_ws:
                ws_sane += 1
            # Normal: high at N, low at N+1
            sane_normal, _ = is_sane_float32(regs[i+1], regs[i])
            if sane_normal:
                normal_sane += 1

    word_swap = ws_sane >= normal_sane
    print(f"    Word swap test: WS={ws_sane} sane, Normal={normal_sane} sane "
          f"-> {'word-swapped' if word_swap else 'normal'}")
    return word_swap


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Smart Machine Discovery & Register Mapping",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full discovery on known PLC
  python discover_machine.py --host 129.168.1.25

  # Scan specific register range
  python discover_machine.py --host 129.168.1.25 --start 1 --end 5000

  # Generate profile YAML
  python discover_machine.py --host 129.168.1.25 --output profiles/rig709.yaml

  # Network scan to find PLCs
  python discover_machine.py --scan-network --subnet 129.168.1
        """)

    parser.add_argument("--host", type=str, default=None,
                        help="PLC IP address")
    parser.add_argument("--port", type=int, default=502,
                        help="Modbus TCP port (default: 502)")
    parser.add_argument("--unit-id", type=int, default=0,
                        help="Modbus unit ID (default: 0)")
    parser.add_argument("--start", type=int, default=1,
                        help="Scan start register (default: 1)")
    parser.add_argument("--end", type=int, default=5000,
                        help="Scan end register (default: 5000)")
    parser.add_argument("--block-size", type=int, default=100,
                        help="Registers per block read (default: 100, max 125)")
    parser.add_argument("--passes", type=int, default=3,
                        help="Number of differential scan passes (default: 3)")
    parser.add_argument("--wait", type=int, default=10,
                        help="Wait seconds between passes (default: 10)")
    parser.add_argument("--timeout", type=float, default=5.0,
                        help="Socket timeout in seconds (default: 5)")
    parser.add_argument("--name", type=str, default=None,
                        help="Machine profile name")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output YAML profile path")
    parser.add_argument("--scan-network", action="store_true",
                        help="Scan network for Modbus TCP hosts")
    parser.add_argument("--subnet", type=str, default="129.168.1",
                        help="Network subnet to scan (default: 129.168.1)")
    parser.add_argument("--snapshot-only", action="store_true",
                        help="Single pass only (no differential)")

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  TopDrive AI — Machine Discovery")
    print("=" * 60)

    # Network scan mode
    if args.scan_network:
        hosts = discover_network(args.subnet, args.port)
        if not hosts:
            print("\n  No Modbus TCP hosts found.")
            sys.exit(1)
        for host in hosts:
            uid = find_unit_id(host, args.port)
            ws = verify_word_swap(host, args.port, uid)
            print(f"    {host}: unit_id={uid}, word_swap={ws}")
        sys.exit(0)

    if not args.host:
        print("  ERROR: --host required (or use --scan-network)")
        sys.exit(1)

    # Determine unit_id and word swap
    print(f"\n  Target: {args.host}:{args.port}")
    uid = args.unit_id
    ws = verify_word_swap(args.host, args.port, uid, args.timeout)

    scanner = RegisterScanner(
        args.host, args.port, unit_id=uid,
        word_swap=ws, timeout=args.timeout
    )

    if args.snapshot_only:
        # Single-pass scan
        print(f"\n  Running single-pass scan R{args.start}-R{args.end}...")
        scan = scanner.full_scan(args.start, args.end, args.block_size)
        print(f"\n  Non-zero registers: {len(scan)}")
        for addr in sorted(scan.keys()):
            val = scan[addr]
            # Try FLOAT32 with next register
            next_val = scan.get(addr + 1, 0)
            sane, fval = is_sane_float32(val, next_val)
            if sane and next_val != 0:
                print(f"    R{addr:5d} = 0x{val:04X}  "
                      f"(FLOAT32 pair: {fval:.4f})")
            else:
                print(f"    R{addr:5d} = 0x{val:04X}  (raw: {val})")
    else:
        # Differential scan
        print(f"\n  Running differential scan R{args.start}-R{args.end} "
              f"({args.passes} passes, {args.wait}s wait)...")
        candidates = scanner.differential_scan(
            args.start, args.end, args.block_size,
            args.passes, args.wait
        )

        if not candidates:
            print("\n  WARNING: No changing registers found!")
            print("  Is the machine actively threading pipe?")
            print("  Static registers = machine is idle.")
            sys.exit(1)

        # Classify
        candidates = scanner.classify_registers(candidates)

        # Print results
        print(f"\n  {'='*60}")
        print(f"  DISCOVERED REGISTERS")
        print(f"  {'='*60}")
        print(f"  {'Addr':>6s}  {'Type':>8s}  {'Classification':>16s}  "
              f"{'Confidence':>10s}  {'Values'}")
        print(f"  {'-'*6}  {'-'*8}  {'-'*16}  {'-'*10}  {'-'*30}")

        for addr in sorted(candidates.keys()):
            cand = candidates[addr]
            if cand.is_float32_pair:
                vals_str = ", ".join(f"{v:.2f}" for v in cand.float32_values[:3])
            else:
                vals_str = ", ".join(str(v) for v in cand.values[:3])
            print(f"  R{addr:5d}  {cand.data_type:>8s}  "
                  f"{cand.classification:>16s}  "
                  f"{cand.confidence:>9.0%}  [{vals_str}]")

        # Generate profile
        profile_name = args.name or f"rig_{args.host.replace('.', '_')}"
        output_path = args.output
        if not output_path:
            output_path = f"profiles/{profile_name}.yaml"

        profile = scanner.generate_profile(
            candidates, name=profile_name, output_path=output_path
        )

        print(f"\n  Profile: {profile.name}")
        print(f"  PLC: {profile.plc_ip}:{profile.plc_port}")
        print(f"  Registers mapped: {len(profile.reg_map)}")
        for name, rdef in profile.reg_map.items():
            print(f"    {name:20s}  R{rdef.address:5d}  {rdef.data_type}  {rdef.unit}")

        print(f"\n  IMPORTANT: These classifications are heuristic estimates.")
        print(f"  Ask Steve to confirm which register is which.")
        print(f"  Edit {output_path} to correct any mistakes.")

    scanner.client.close()
    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
