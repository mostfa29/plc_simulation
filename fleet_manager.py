#!/usr/bin/env python3
"""
Fleet Manager — eWon Auto-Detection, Fleet Discovery & Smart Testing
======================================================================
Top-level orchestrator that replaces manual workflow for Steve's rigs.

Detect → Discover → Identify → Test → Capture — zero manual config.

Architecture (Three-Layer Detection Stack):

    Layer 1: VPN Tunnel Detection
      - Detect 129.168.x.x route/adapter appearing on local machine
      - Detect Talk2m eCatcher process + active connection name
      - Periodic heartbeat to known eWon gateway IPs

    Layer 2: LAN Discovery (behind the eWon)
      - Ping sweep 129.168.1.0/24
      - Port scan for 502 (Modbus TCP)
      - Modbus handshake: find working unit_id, verify word_swap
      - Fingerprint PLC type from register patterns

    Layer 3: Machine Identification & Profile Loading
      - Check PLC IP + eWon serial against known profiles/
      - If known machine → load YAML profile, start capture
      - If unknown machine → run full discovery, generate new profile
      - Smart tests: validate sensors, check data quality, report health

Usage:
    python fleet_manager.py --daemon
    python fleet_manager.py --scan-now
    python fleet_manager.py --test <machine_name>
    python fleet_manager.py --onboard --host 129.168.1.25
    python fleet_manager.py --fleet-report
"""
import argparse
import json
import logging
import math
import os
import platform
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from config import MachineProfile, RegisterDef, EquipmentType, EQUIPMENT_SPECS
from discover_machine import (
    ModbusTCPClient,
    RegisterScanner,
    decode_ge_float32,
    discover_network,
    find_unit_id,
    is_sane_float32,
    verify_word_swap,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TestResult:
    """Result of a single smart test."""
    name: str
    passed: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        s = f"  [{status}] {self.name}: {self.message}"
        for w in self.warnings:
            s += f"\n         WARNING: {w}"
        return s


@dataclass
class TestReport:
    """Collection of test results for one machine."""
    machine: str
    timestamp: str = ""
    results: List[TestResult] = field(default_factory=list)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat(timespec='seconds')

    def add(self, result: TestResult):
        self.results.append(result)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    def summary(self) -> str:
        total = len(self.results)
        lines = [
            f"\n  {'='*60}",
            f"  TEST REPORT: {self.machine}",
            f"  Time: {self.timestamp}",
            f"  Result: {self.pass_count}/{total} passed",
            f"  {'='*60}",
        ]
        for r in self.results:
            lines.append(str(r))
        lines.append(f"  {'='*60}")
        overall = "ALL TESTS PASSED" if self.passed else "SOME TESTS FAILED"
        lines.append(f"  {overall}")
        lines.append(f"  {'='*60}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Fleet Catalog (Talk2m eWon Device Database)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FleetCatalogEntry:
    """One device from the Talk2m fleet catalog."""
    ewon_name: str
    equipment_type: str          # EquipmentType.value string
    customer: str
    description: str = ""
    firmware: str = ""
    status: str = "offline"      # online, offline, connected
    notes: str = ""

    @property
    def equipment_type_enum(self) -> EquipmentType:
        """Convert string to EquipmentType enum."""
        try:
            return EquipmentType(self.equipment_type)
        except ValueError:
            return EquipmentType.UNKNOWN

    @property
    def equipment_spec(self):
        """Get the EquipmentSpec for this device's type."""
        return EQUIPMENT_SPECS.get(self.equipment_type_enum)


class FleetCatalog:
    """Loads and queries the Talk2m fleet catalog.

    The catalog is a YAML file with all ~130 eWon devices from Steve's
    Talk2m account. Each device is classified by equipment_type so the
    physics engine can generate representative synthetic data.

    Usage:
        catalog = FleetCatalog()
        entry = catalog.find_by_ewon_name("Precision Rig 709 HXI HT")
        entries = catalog.find_by_customer("Precision")
        entries = catalog.find_by_equipment_type("hxi_ht")
    """

    def __init__(self, catalog_path: str = "fleet_catalog.yaml"):
        self.catalog_path = Path(catalog_path)
        self.entries: List[FleetCatalogEntry] = []
        self._load()

    def _load(self):
        """Load fleet catalog from YAML."""
        if not self.catalog_path.exists():
            logger.warning(f"Fleet catalog not found: {self.catalog_path}")
            return

        try:
            import yaml
            with open(self.catalog_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)

            devices = data.get('devices', [])
            for d in devices:
                self.entries.append(FleetCatalogEntry(
                    ewon_name=d.get('ewon_name', ''),
                    equipment_type=d.get('equipment_type', 'unknown'),
                    customer=d.get('customer', ''),
                    description=d.get('description', ''),
                    firmware=d.get('firmware', ''),
                    status=d.get('status', 'offline'),
                    notes=d.get('notes', ''),
                ))
            logger.info(f"  Loaded {len(self.entries)} devices from fleet catalog")
        except Exception as e:
            logger.error(f"Failed to load fleet catalog: {e}")

    def find_by_ewon_name(self, name: str) -> Optional[FleetCatalogEntry]:
        """Find a device by exact or partial eWon name match."""
        # Exact match first
        for entry in self.entries:
            if entry.ewon_name == name:
                return entry
        # Case-insensitive partial match
        name_lower = name.lower()
        for entry in self.entries:
            if name_lower in entry.ewon_name.lower():
                return entry
        return None

    def find_by_customer(self, customer: str) -> List[FleetCatalogEntry]:
        """Find all devices for a customer."""
        customer_lower = customer.lower()
        return [e for e in self.entries if e.customer.lower() == customer_lower]

    def find_by_equipment_type(self, eq_type: str) -> List[FleetCatalogEntry]:
        """Find all devices of a given equipment type."""
        return [e for e in self.entries if e.equipment_type == eq_type]

    def find_online(self) -> List[FleetCatalogEntry]:
        """Find all devices currently online or connected."""
        return [e for e in self.entries
                if e.status in ('online', 'connected')]

    def summary(self) -> str:
        """Generate a fleet summary grouped by equipment type and customer."""
        if not self.entries:
            return "  Fleet catalog: empty (no fleet_catalog.yaml found)"

        # Count by equipment type
        type_counts: Dict[str, int] = {}
        for e in self.entries:
            type_counts[e.equipment_type] = type_counts.get(e.equipment_type, 0) + 1

        # Count by customer
        cust_counts: Dict[str, int] = {}
        for e in self.entries:
            cust_counts[e.customer] = cust_counts.get(e.customer, 0) + 1

        # Online count
        online = len(self.find_online())

        lines = [
            "",
            "  " + "=" * 58,
            "  Fleet Catalog Summary",
            "  " + "=" * 58,
            f"  Total devices: {len(self.entries)}",
            f"  Online/Connected: {online}",
            "",
            "  By Equipment Type:",
        ]
        for eq_type in sorted(type_counts.keys()):
            count = type_counts[eq_type]
            spec = EQUIPMENT_SPECS.get(EquipmentType(eq_type) if eq_type != 'unknown'
                                       else EquipmentType.UNKNOWN)
            hp = f"{spec.rated_hp:.0f}HP" if spec else "?"
            lines.append(f"    {eq_type:<20s} {count:>3d}  ({hp})")

        lines.append("")
        lines.append("  By Customer:")
        for cust in sorted(cust_counts.keys()):
            lines.append(f"    {cust:<20s} {cust_counts[cust]:>3d}")

        lines.append("  " + "=" * 58)
        return "\n".join(lines)

    def equipment_type_distribution(self) -> Dict[str, int]:
        """Return dict of equipment_type -> count for training data weighting."""
        counts: Dict[str, int] = {}
        for e in self.entries:
            et = e.equipment_type
            if et == 'unknown' or et == 'shop_unit':
                continue  # Skip non-field units
            counts[et] = counts.get(et, 0) + 1
        return counts


# ═══════════════════════════════════════════════════════════════════
# Layer 1: EWon VPN Tunnel Detection
# ═══════════════════════════════════════════════════════════════════

class EWonDetector:
    """Detects active eWon VPN tunnels on the local machine.

    Like a network manager watching for new interfaces. When someone
    clicks 'Connect' in eCatcher, a VPN tunnel appears as a new route
    to 129.168.x.x. We detect that and react.

    Detection methods (try all, use first that works):
      1. Network route scan (ipconfig / ip route)
      2. eCatcher process detection (tasklist / ps)
      3. Brute-force heartbeat (ping known gateways)
    """

    EWON_SUBNET_PREFIX = "129.168."

    def __init__(self, known_gateways: Optional[List[str]] = None):
        self.known_gateways = known_gateways or ["129.168.1.19"]
        self._active_gateway: Optional[str] = None
        self._active_device_name: Optional[str] = None

    def detect_active_tunnel(self) -> Optional[Dict]:
        """Returns info about active eWon tunnel, or None.

        Returns:
            {
                'gateway_ip': '129.168.1.19',
                'device_name': 'Precision Rig 709',  # if detectable
                'detection_method': 'route_scan',
                'latency_ms': 347.5,
            }
        """
        # Method 1: Route scan
        gw = self._detect_via_routes()
        if gw:
            latency = self._measure_latency(gw)
            device = self._detect_via_ecatcher()
            self._active_gateway = gw
            return {
                'gateway_ip': gw,
                'device_name': device.get('device_name') if device else None,
                'detection_method': 'route_scan',
                'latency_ms': latency,
            }

        # Method 2: eCatcher process
        ecatcher = self._detect_via_ecatcher()
        if ecatcher:
            self._active_device_name = ecatcher.get('device_name')
            # Try heartbeat to confirm connectivity
            gw = self._detect_via_heartbeat()
            if gw:
                self._active_gateway = gw
                return {
                    'gateway_ip': gw,
                    'device_name': self._active_device_name,
                    'detection_method': 'ecatcher_process',
                    'latency_ms': self._measure_latency(gw),
                }

        # Method 3: Heartbeat
        gw = self._detect_via_heartbeat()
        if gw:
            self._active_gateway = gw
            return {
                'gateway_ip': gw,
                'device_name': None,
                'detection_method': 'heartbeat',
                'latency_ms': self._measure_latency(gw),
            }

        return None

    def _detect_via_routes(self) -> Optional[str]:
        """Check OS routing table for 129.168.x.x entries.

        Windows `route PRINT` columns:
          Network Destination  Netmask  Gateway  Interface  Metric
          129.168.1.0    255.255.255.0  On-link  129.168.1.X  25

        We extract all 129.168.x.x IPs from the line, skip network (.0)
        and broadcast (.255) addresses, and return the best candidate
        (the Interface IP is our local VPN address on the eWon subnet).
        """
        system = platform.system().lower()
        try:
            if system == 'windows':
                result = subprocess.run(
                    ['route', 'PRINT'],
                    capture_output=True, text=True, timeout=10
                )
                candidates = []
                for line in result.stdout.splitlines():
                    if self.EWON_SUBNET_PREFIX in line:
                        parts = line.split()
                        for part in parts:
                            if part.startswith(self.EWON_SUBNET_PREFIX):
                                # Skip network (.0) and broadcast (.255)
                                last_octet = part.rsplit('.', 1)[-1]
                                if last_octet not in ('0', '255'):
                                    candidates.append(part)
                if candidates:
                    # Prefer known gateway IPs (.19, .1), then any host IP
                    for preferred in ('.19', '.1'):
                        for c in candidates:
                            if c.endswith(preferred):
                                return c
                    return candidates[0]
                # No host IP found — return the network address and let
                # the caller derive the subnet from it
                for line in result.stdout.splitlines():
                    if self.EWON_SUBNET_PREFIX in line:
                        parts = line.split()
                        for part in parts:
                            if part.startswith(self.EWON_SUBNET_PREFIX):
                                return part
            elif system == 'linux':
                result = subprocess.run(
                    ['ip', 'route', 'show'],
                    capture_output=True, text=True, timeout=10
                )
                for line in result.stdout.splitlines():
                    if self.EWON_SUBNET_PREFIX in line:
                        parts = line.split()
                        for part in parts:
                            if part.startswith(self.EWON_SUBNET_PREFIX):
                                # Extract the gateway/network address
                                network = part.split('/')[0]
                                return network
            elif system == 'darwin':
                result = subprocess.run(
                    ['netstat', '-rn'],
                    capture_output=True, text=True, timeout=10
                )
                for line in result.stdout.splitlines():
                    if self.EWON_SUBNET_PREFIX in line:
                        parts = line.split()
                        if parts:
                            return parts[0].split('/')[0]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug(f"Route detection failed: {e}")

        return None

    def _detect_via_ecatcher(self) -> Optional[Dict]:
        """Parse eCatcher state for active device info.

        Tries multiple methods to get the connected device name:
          1. eCatcher window title (via PowerShell Get-Process)
          2. eCatcher log files (last connected device)
          3. Talk2m VPN adapter name
        """
        system = platform.system().lower()
        info = {}

        try:
            if system == 'windows':
                # Check if eCatcher is running
                result = subprocess.run(
                    ['tasklist', '/FI', 'IMAGENAME eq eCatcher.exe', '/FO', 'CSV'],
                    capture_output=True, text=True, timeout=10
                )
                if 'eCatcher.exe' in result.stdout:
                    info['ecatcher_running'] = True

                    # Method 1: Window title
                    try:
                        ps_cmd = (
                            'Get-Process -Name eCatcher -ErrorAction SilentlyContinue '
                            '| Select-Object -ExpandProperty MainWindowTitle'
                        )
                        ps_result = subprocess.run(
                            ['powershell', '-Command', ps_cmd],
                            capture_output=True, text=True, timeout=10
                        )
                        title = ps_result.stdout.strip()
                        if title and title.lower() != 'ecatcher':
                            # Title format: "eCatcher - Device Name" or just "Device Name"
                            name = title.replace('eCatcher - ', '').replace('eCatcher -', '').strip()
                            if name:
                                info['device_name'] = name
                                return info
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        pass

                    # Method 2: VPN adapter description (often contains device name)
                    try:
                        ps_cmd = (
                            'Get-NetAdapter | Where-Object {$_.InterfaceDescription '
                            '-like \"*TAP*\" -or $_.InterfaceDescription -like \"*eWon*\" '
                            '-or $_.InterfaceDescription -like \"*Talk2m*\" -or '
                            '$_.Name -like \"*eWon*\" -or $_.Name -like \"*Talk2m*\"} '
                            '| Select-Object -ExpandProperty Name'
                        )
                        ps_result = subprocess.run(
                            ['powershell', '-Command', ps_cmd],
                            capture_output=True, text=True, timeout=10
                        )
                        adapter = ps_result.stdout.strip()
                        if adapter:
                            info['vpn_adapter'] = adapter
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        pass

                    # Method 3: eCatcher log files
                    try:
                        import glob as _glob
                        log_dirs = [
                            os.path.expandvars(r'%LOCALAPPDATA%\eWon\eCatcher'),
                            os.path.expandvars(r'%APPDATA%\eWon\eCatcher'),
                            os.path.expandvars(r'%LOCALAPPDATA%\Talk2m'),
                            os.path.expandvars(r'%PROGRAMDATA%\eWon'),
                        ]
                        for d in log_dirs:
                            logs = sorted(_glob.glob(os.path.join(d, '*.log')),
                                          key=os.path.getmtime, reverse=True)
                            for log_path in logs[:3]:
                                try:
                                    with open(log_path, 'r', errors='ignore') as lf:
                                        # Read last 50 lines
                                        lines = lf.readlines()[-50:]
                                    for line in reversed(lines):
                                        # Look for connection patterns
                                        if 'connect' in line.lower() and (
                                                'rig' in line.lower() or
                                                'ewon' in line.lower() or
                                                'device' in line.lower()):
                                            # Try to extract device name
                                            import re as _re
                                            m = _re.search(
                                                r'(?:to|device|connecting)\s*[:\s]+\s*"?([^"]+?)"?\s*$',
                                                line, _re.IGNORECASE
                                            )
                                            if m:
                                                info['device_name'] = m.group(1).strip()
                                                return info
                                except (OSError, IOError):
                                    continue
                    except Exception:
                        pass

                    return info
            else:
                result = subprocess.run(
                    ['pgrep', '-f', 'eCatcher'],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    info['ecatcher_running'] = True
                    return info
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug(f"eCatcher detection failed: {e}")

        return None

    def _detect_via_heartbeat(self) -> Optional[str]:
        """Ping all known gateways, return first that responds."""
        for gw in self.known_gateways:
            # Try TCP connect on port 80 (eWon web UI) — more reliable than ICMP
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                result = sock.connect_ex((gw, 80))
                sock.close()
                if result == 0:
                    return gw
            except (socket.error, OSError):
                pass

            # Fall back to ICMP ping
            try:
                param = '-n' if platform.system().lower() == 'windows' else '-c'
                result = subprocess.run(
                    ['ping', param, '1', '-w', '2000', gw],
                    capture_output=True, timeout=5
                )
                if result.returncode == 0:
                    return gw
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        return None

    def _measure_latency(self, host: str) -> Optional[float]:
        """Measure TCP connect latency in ms."""
        try:
            start = time.monotonic()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((host, 80))
            elapsed = (time.monotonic() - start) * 1000
            sock.close()
            return round(elapsed, 1)
        except (socket.error, OSError):
            return None

    def watch(self, callback: Callable, poll_interval: float = 5.0):
        """Continuously monitor for tunnel changes.

        Calls callback(tunnel_info) when:
          - A new tunnel appears (eWon connected)
          - An existing tunnel disappears (eWon disconnected)
          - A different eWon is connected (tunnel switched)
        """
        previous = None
        logger.info(f"  Watching for eWon tunnels (poll every {poll_interval}s)...")

        while True:
            current = self.detect_active_tunnel()
            current_gw = current['gateway_ip'] if current else None
            prev_gw = previous['gateway_ip'] if previous else None

            if current_gw != prev_gw:
                if current and not previous:
                    logger.info(f"  Tunnel UP: {current['gateway_ip']}")
                elif not current and previous:
                    logger.info(f"  Tunnel DOWN: {previous['gateway_ip']}")
                else:
                    logger.info(f"  Tunnel SWITCHED: {prev_gw} -> {current_gw}")
                callback(current)
                previous = current

            time.sleep(poll_interval)


# ═══════════════════════════════════════════════════════════════════
# Layer 2: LAN Discovery
# ═══════════════════════════════════════════════════════════════════

class LANDiscovery:
    """Discovers PLCs and devices on the eWon LAN segment.

    Once we know the eWon gateway IP (e.g. 129.168.1.19), we scan
    its /24 subnet to find all Modbus-capable devices.

    The eWon LAN is typically small (2-10 devices):
      - eWon itself (129.168.1.19) — has Modbus but usually empty tags
      - GE CPE305 PLC (129.168.1.25) — the main target
      - Maybe an HMI panel, VFD drive, or secondary PLC
    """

    def __init__(self, gateway_ip: str, modbus_port: int = 502):
        self.gateway_ip = gateway_ip
        self.modbus_port = modbus_port
        self.subnet = self._derive_subnet(gateway_ip)

    def _derive_subnet(self, ip: str) -> str:
        """Extract /24 subnet from IP (e.g. '129.168.1.19' -> '129.168.1')."""
        parts = ip.rsplit('.', 1)
        return parts[0] if len(parts) == 2 else ip

    def discover_hosts(self, timeout: float = 1.0) -> List[Dict]:
        """Ping sweep + port scan the LAN.

        Returns list of dicts with host info including Modbus capability.
        """
        hosts = []
        print(f"\n  Scanning {self.subnet}.1-254 ...")

        for i in range(1, 255):
            ip = f"{self.subnet}.{i}"

            # TCP connect on Modbus port
            has_modbus = False
            latency = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                start = time.monotonic()
                result = sock.connect_ex((ip, self.modbus_port))
                elapsed = (time.monotonic() - start) * 1000
                sock.close()
                if result == 0:
                    has_modbus = True
                    latency = round(elapsed, 1)
            except (socket.error, OSError):
                pass

            # Check HTTP (port 80) for web UI
            has_http = False
            if has_modbus or ip == self.gateway_ip:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(timeout)
                    result = sock.connect_ex((ip, 80))
                    sock.close()
                    has_http = (result == 0)
                except (socket.error, OSError):
                    pass

            if has_modbus or has_http:
                host_info = {
                    'ip': ip,
                    'has_modbus': has_modbus,
                    'has_http': has_http,
                    'modbus_port': self.modbus_port,
                    'latency_ms': latency,
                }
                hosts.append(host_info)
                print(f"    FOUND: {ip}  Modbus={'YES' if has_modbus else 'no'}  "
                      f"HTTP={'YES' if has_http else 'no'}  "
                      f"latency={latency}ms")

        print(f"\n  Found {len(hosts)} devices on {self.subnet}.0/24")
        return hosts

    def fingerprint_plc(self, host: str, port: int = 502,
                        timeout: float = 5.0) -> Dict:
        """Identify PLC type and capabilities via Modbus probing.

        Returns dict with PLC fingerprint details.
        """
        info = {
            'ip': host,
            'port': port,
            'unit_id': 0,
            'word_swap': True,
            'plc_type': 'unknown',
            'plc_state': 'unknown',
            'rtc_detected': False,
            'register_range': (0, 0),
            'total_nonzero': 0,
        }

        # Step 1: Find working unit_id
        uid = find_unit_id(host, port, timeout)
        info['unit_id'] = uid

        # Step 2: Verify word swap
        ws = verify_word_swap(host, port, uid, timeout)
        info['word_swap'] = ws

        # Step 3: Quick register probe
        client = ModbusTCPClient(host, port, timeout=timeout, unit_id=uid)
        if not client.connect():
            info['plc_state'] = 'unreachable'
            return info

        # Read R1 — PLC run state on GE CPE305
        r1 = client.read_holding_registers(0, 10)
        if r1 is not None:
            info['plc_state'] = 'running'
            if r1[0] == 4:
                info['plc_type'] = 'GE_CPE305'

        # Check for RTC pattern (R3447=2026 = year on Rig 709)
        rtc_regs = client.read_holding_registers(3446, 8)
        if rtc_regs is not None:
            year = rtc_regs[1]  # R3447
            if 2020 <= year <= 2030:
                info['rtc_detected'] = True
                info['plc_type'] = 'GE_CPE305'

        # Quick count of non-zero registers in key ranges
        total_nonzero = 0
        max_addr = 0
        for start in range(0, 5000, 100):
            regs = client.read_holding_registers(start, 100)
            if regs:
                for i, v in enumerate(regs):
                    if v != 0:
                        total_nonzero += 1
                        max_addr = max(max_addr, start + i + 1)

        info['total_nonzero'] = total_nonzero
        info['register_range'] = (1, max_addr) if max_addr > 0 else (0, 0)

        client.close()
        return info


# ═══════════════════════════════════════════════════════════════════
# Layer 3: Machine Identification
# ═══════════════════════════════════════════════════════════════════

class MachineIdentifier:
    """Maps a discovered PLC to a known (or new) machine profile.

    Like device driver matching. Given a PLC fingerprint, find the
    right MachineProfile YAML. If no match, trigger discovery to
    create a new profile.

    Matching strategies (in priority order):
      1. Exact IP match
      2. Register fingerprint
      3. No match → new machine
    """

    def __init__(self, profiles_dir: str = "profiles",
                 catalog_path: str = "fleet_catalog.yaml"):
        self.profiles_dir = Path(profiles_dir)
        self.known_profiles: Dict[str, MachineProfile] = {}
        self.catalog = FleetCatalog(catalog_path)
        self._load_all_profiles()

    def _load_all_profiles(self):
        """Load all YAML profiles from the profiles directory."""
        self.known_profiles = MachineProfile.load_all_profiles(
            str(self.profiles_dir)
        )
        logger.info(f"  Loaded {len(self.known_profiles)} profiles from "
                     f"{self.profiles_dir}/")

    def identify(self, fingerprint: Dict,
                 ewon_name: Optional[str] = None) -> Tuple[str, Optional[MachineProfile]]:
        """Match a PLC fingerprint to a profile.

        Returns: (match_type, profile)
          match_type: 'exact_ip', 'catalog_match', 'new_machine'
          profile: loaded MachineProfile or None for new machine
        """
        ip = fingerprint.get('ip', '')

        # Strategy 1: Exact IP match against known profiles
        profile = self._match_by_ip(ip)
        if profile:
            logger.info(f"  Matched {ip} to profile '{profile.name}' (exact IP)")
            return ('exact_ip', profile)

        # Strategy 2: Fleet catalog match by eWon name
        if ewon_name:
            catalog_entry = self.catalog.find_by_ewon_name(ewon_name)
            if catalog_entry:
                logger.info(f"  Catalog match: {ewon_name} -> "
                            f"{catalog_entry.equipment_type} "
                            f"({catalog_entry.customer})")
                return ('catalog_match', None)

        # Strategy 3: No match
        logger.info(f"  No profile matches {ip} — new machine")
        return ('new_machine', None)

    def _match_by_ip(self, ip: str) -> Optional[MachineProfile]:
        """Match by PLC IP address."""
        for name, profile in self.known_profiles.items():
            if profile.plc_ip == ip:
                return profile
        return None


# ═══════════════════════════════════════════════════════════════════
# Smart Tester
# ═══════════════════════════════════════════════════════════════════

class SmartTester:
    """Runs intelligent diagnostic tests on a connected machine.

    Not just 'can I read registers' — a comprehensive health check
    that validates the entire data pipeline from sensor to AI model.

    Like a pre-flight checklist for an aircraft. Run these tests
    every time a new machine connects before starting data capture.

    Test categories:
      1. Connectivity     — Can we talk to the PLC reliably?
      2. Latency profile  — What's the network performance?
      3. Register access  — Are all mapped registers readable?
      4. Data sanity      — Do values make physical sense?
      5. Sensor ranges    — Are sensors alive (not railed/dead)?
      6. Dynamics         — Does data change when machine operates?
      7. Noise floor      — What's the actual sensor noise profile?
      8. Word swap        — Confirm FLOAT32 byte order
      9. Sample rate      — What poll rate can we achieve?
    """

    def __init__(self, profile: MachineProfile, timeout: float = 5.0):
        self.profile = profile
        self.timeout = timeout
        self._client: Optional[ModbusTCPClient] = None

    def _get_client(self) -> Optional[ModbusTCPClient]:
        """Get or create a connected Modbus client."""
        if self._client and self._client.connected:
            return self._client
        self._client = ModbusTCPClient(
            self.profile.plc_ip,
            self.profile.plc_port,
            timeout=self.timeout,
            unit_id=self.profile.unit_id,
        )
        if self._client.connect():
            return self._client
        self._client = None
        return None

    def _close(self):
        if self._client:
            self._client.close()
            self._client = None

    def run_all(self) -> TestReport:
        """Run complete test suite, return structured report."""
        report = TestReport(machine=self.profile.name)

        report.add(self.test_connectivity())
        report.add(self.test_latency_profile())
        report.add(self.test_register_readability())
        report.add(self.test_data_sanity())
        report.add(self.test_sensor_ranges())
        report.add(self.test_dynamics())
        report.add(self.test_noise_floor())
        report.add(self.test_word_swap())
        report.add(self.test_sample_rate())

        self._close()
        return report

    def test_connectivity(self) -> TestResult:
        """Basic Modbus TCP connectivity.

        Tests:
        - TCP connect to PLC IP:port within 5s
        - Read R1 successfully
        - No Modbus exception codes
        - Verify unit_id from profile works

        Pass: all succeed within 3 attempts.
        """
        for attempt in range(3):
            client = self._get_client()
            if client:
                regs = client.read_holding_registers(0, 1)
                if regs is not None:
                    return TestResult(
                        name="Connectivity",
                        passed=True,
                        message=f"Connected to {self.profile.plc_ip}:{self.profile.plc_port} "
                                f"(unit_id={self.profile.unit_id})",
                        details={'attempts': attempt + 1, 'r1_value': regs[0]},
                    )
            self._close()
            time.sleep(1)

        return TestResult(
            name="Connectivity",
            passed=False,
            message=f"Cannot reach {self.profile.plc_ip}:{self.profile.plc_port}",
        )

    def test_latency_profile(self) -> TestResult:
        """Measure and characterize network latency.

        20 sequential reads, compute min/avg/max/p95/p99.
        Pass: avg < 1000ms, p99 < 3000ms.
        """
        client = self._get_client()
        if not client:
            return TestResult(name="Latency Profile", passed=False,
                              message="No connection")

        latencies = []
        for _ in range(20):
            start = time.monotonic()
            regs = client.read_holding_registers(0, 1)
            elapsed = (time.monotonic() - start) * 1000
            if regs is not None:
                latencies.append(elapsed)
            else:
                # Reconnect
                self._close()
                client = self._get_client()
                if not client:
                    break

        if len(latencies) < 10:
            return TestResult(name="Latency Profile", passed=False,
                              message=f"Only {len(latencies)}/20 reads succeeded")

        latencies.sort()
        avg = sum(latencies) / len(latencies)
        mn = latencies[0]
        mx = latencies[-1]
        p95_idx = int(len(latencies) * 0.95)
        p99_idx = int(len(latencies) * 0.99)
        p95 = latencies[min(p95_idx, len(latencies) - 1)]
        p99 = latencies[min(p99_idx, len(latencies) - 1)]
        jitter = mx - mn
        recommended_hz = min(10.0, 1000.0 / avg * 0.8) if avg > 0 else 1.0

        passed = avg < 1000 and p99 < 3000
        warnings = []
        if avg > 500:
            warnings.append(f"High average latency ({avg:.0f}ms) — VPN connection?")
        if jitter > avg * 2:
            warnings.append(f"High jitter ({jitter:.0f}ms) — unstable connection")

        return TestResult(
            name="Latency Profile",
            passed=passed,
            message=f"avg={avg:.0f}ms  min={mn:.0f}ms  max={mx:.0f}ms  "
                    f"p95={p95:.0f}ms  recommended={recommended_hz:.1f}Hz",
            details={
                'avg_ms': round(avg, 1), 'min_ms': round(mn, 1),
                'max_ms': round(mx, 1), 'p95_ms': round(p95, 1),
                'p99_ms': round(p99, 1), 'jitter_ms': round(jitter, 1),
                'recommended_hz': round(recommended_hz, 1),
                'samples': len(latencies),
            },
            warnings=warnings,
        )

    def test_register_readability(self) -> TestResult:
        """Verify all registers in the profile's reg_map are readable.

        Pass: 100% of mapped registers return data (not error).
        If any mapped register returns an exception, the profile
        is wrong for this machine — flag for re-discovery.
        """
        client = self._get_client()
        if not client:
            return TestResult(name="Register Readability", passed=False,
                              message="No connection")

        readable = {}
        failed = {}

        for var_name, reg_def in self.profile.reg_map.items():
            addr = reg_def.address
            count = 2 if reg_def.data_type in ('FLOAT32', 'INT32') else 1
            regs = client.read_holding_registers(addr, count)
            if regs is not None:
                readable[var_name] = regs
            else:
                failed[var_name] = addr

        total = len(self.profile.reg_map)
        ok = len(readable)
        passed = ok == total

        warnings = []
        if failed:
            for var, addr in failed.items():
                warnings.append(f"R{addr} ({var}) — unreadable, profile may be wrong")

        return TestResult(
            name="Register Readability",
            passed=passed,
            message=f"{ok}/{total} registers readable",
            details={'readable': list(readable.keys()),
                     'failed': list(failed.keys())},
            warnings=warnings,
        )

    def test_data_sanity(self) -> TestResult:
        """Check that register values make physical sense.

        For each mapped variable, verify the decoded value is within
        expected physical range.
        Pass: all values in expected range for their type.
        """
        client = self._get_client()
        if not client:
            return TestResult(name="Data Sanity", passed=False,
                              message="No connection")

        # Expected ranges per variable type
        ranges = {
            'torque': (0, 100_000),
            'rpm': (0, 500),
            'pressure': (0, 10_000),
            'temperature': (-40, 400),
            'hookload': (0, 500_000),
            'encoder_counts': (-2**31, 2**31),
            'operating_mode': (0, 20),
            'pid_setpoint': (0, 100_000),
            'pid_error': (-50_000, 50_000),
            'pid_output': (0, 100),
            'target_torque': (0, 100_000),
            'turns': (-1000, 100_000),
            'fault_code': (0, 65535),
            'connection_state': (0, 20),
            'peak_torque': (0, 100_000),
            'shoulder_torque': (0, 100_000),
            'slope_dT_dN': (-100_000, 100_000),
            'connection_count': (0, 100_000),
        }

        sane = {}
        insane = {}
        warnings = []

        for var_name, reg_def in self.profile.reg_map.items():
            addr = reg_def.address
            count = 2 if reg_def.data_type in ('FLOAT32', 'INT32') else 1
            regs = client.read_holding_registers(addr, count)
            if regs is None:
                continue

            if reg_def.data_type == 'FLOAT32' and len(regs) >= 2:
                if self.profile.word_swap:
                    value = decode_ge_float32(regs[0], regs[1])
                else:
                    packed = struct.pack('>HH', regs[0], regs[1])
                    value = struct.unpack('>f', packed)[0]

                if math.isnan(value) or math.isinf(value):
                    insane[var_name] = f"NaN/Inf (raw: 0x{regs[0]:04X} 0x{regs[1]:04X})"
                    warnings.append(f"{var_name}: NaN/Inf — broken FLOAT32 decode")
                    continue
            elif reg_def.data_type == 'INT32' and len(regs) >= 2:
                value = (regs[0] << 16) | regs[1]
                if value > 2**31:
                    value -= 2**32
            else:
                value = regs[0]

            lo, hi = ranges.get(var_name, (-1e12, 1e12))
            if lo <= value <= hi:
                sane[var_name] = value
            else:
                insane[var_name] = f"{value} (expected {lo} to {hi})"
                warnings.append(f"{var_name}: {value} out of range [{lo}, {hi}]")

        total = len(sane) + len(insane)
        passed = len(insane) == 0

        return TestResult(
            name="Data Sanity",
            passed=passed,
            message=f"{len(sane)}/{total} values in expected range",
            details={'sane': sane, 'insane': insane},
            warnings=warnings,
        )

    def test_sensor_ranges(self) -> TestResult:
        """Verify sensors are not railed or dead.

        Read 20 samples over ~10s and check:
        - No sensor stuck at exactly 0 (dead/unmapped)
        - No sensor stuck at full-scale (railed/saturated)
        - No sensor returns NaN/Inf
        - Values show at least SOME variation (even idle has noise)

        Pass: all mapped analog sensors show non-zero variance.
        """
        client = self._get_client()
        if not client:
            return TestResult(name="Sensor Ranges", passed=False,
                              message="No connection")

        # Analog sensor variables
        analog_vars = ['torque', 'rpm', 'pressure', 'temperature', 'hookload']
        sensor_readings: Dict[str, List[float]] = {
            v: [] for v in analog_vars if v in self.profile.reg_map
        }

        for _ in range(20):
            for var_name in sensor_readings:
                reg_def = self.profile.reg_map[var_name]
                regs = client.read_holding_registers(reg_def.address, 2)
                if regs and len(regs) >= 2:
                    val = decode_ge_float32(regs[0], regs[1])
                    if not (math.isnan(val) or math.isinf(val)):
                        sensor_readings[var_name].append(val)
            time.sleep(0.5)

        warnings = []
        dead_sensors = []
        alive_sensors = []

        for var_name, vals in sensor_readings.items():
            if len(vals) < 10:
                dead_sensors.append(var_name)
                warnings.append(f"{var_name}: fewer than 10 valid reads")
                continue

            unique = len(set(vals))
            variance = max(vals) - min(vals)

            if unique == 1:
                # Exact same value every time — likely static config register
                dead_sensors.append(var_name)
                warnings.append(
                    f"{var_name}: stuck at {vals[0]:.2f} — "
                    f"static register? (not a live sensor)"
                )
            elif all(v == 0 for v in vals):
                dead_sensors.append(var_name)
                warnings.append(f"{var_name}: always zero — dead or unmapped")
            else:
                alive_sensors.append(var_name)

        total = len(sensor_readings)
        passed = len(dead_sensors) == 0 and total > 0

        return TestResult(
            name="Sensor Ranges",
            passed=passed,
            message=f"{len(alive_sensors)}/{total} analog sensors alive",
            details={
                'alive': alive_sensors, 'dead': dead_sensors,
                'readings_per_sensor': {k: len(v) for k, v in sensor_readings.items()},
            },
            warnings=warnings,
        )

    def test_dynamics(self) -> TestResult:
        """Check if the machine is currently operating.

        Read sensors twice with 10s gap. Compare for motion indicators.
        Pass: at least 2 of 4 motion indicators show change
        (or explicit 'idle' status returned with warning).
        """
        client = self._get_client()
        if not client:
            return TestResult(name="Dynamics", passed=False,
                              message="No connection")

        def read_sensors() -> Dict[str, float]:
            vals = {}
            for var_name in ['torque', 'rpm', 'pressure', 'encoder_counts']:
                if var_name not in self.profile.reg_map:
                    continue
                reg = self.profile.reg_map[var_name]
                count = 2 if reg.data_type in ('FLOAT32', 'INT32') else 1
                regs = client.read_holding_registers(reg.address, count)
                if regs and len(regs) >= count:
                    if reg.data_type == 'FLOAT32':
                        vals[var_name] = decode_ge_float32(regs[0], regs[1])
                    elif reg.data_type == 'INT32':
                        v = (regs[0] << 16) | regs[1]
                        vals[var_name] = float(v if v < 2**31 else v - 2**32)
                    else:
                        vals[var_name] = float(regs[0])
            return vals

        read1 = read_sensors()
        time.sleep(10)
        read2 = read_sensors()

        motion_thresholds = {
            'torque': 10.0,       # ft-lbs
            'rpm': 0.5,           # RPM
            'pressure': 50.0,     # PSI
            'encoder_counts': 10, # counts
        }

        moving = []
        static = []
        for var_name, threshold in motion_thresholds.items():
            if var_name in read1 and var_name in read2:
                delta = abs(read2[var_name] - read1[var_name])
                if delta > threshold:
                    moving.append(var_name)
                else:
                    static.append(var_name)

        is_operating = len(moving) >= 2
        warnings = []
        if not is_operating:
            warnings.append(
                "Machine appears idle. Start a threading operation "
                "and re-run for full validation."
            )

        return TestResult(
            name="Dynamics",
            passed=True,  # Always pass — idle is info, not failure
            message=f"{'OPERATING' if is_operating else 'IDLE'} — "
                    f"{len(moving)} motion indicators active "
                    f"({', '.join(moving) if moving else 'none'})",
            details={'moving': moving, 'static': static,
                     'read1': read1, 'read2': read2},
            warnings=warnings,
        )

    def test_noise_floor(self) -> TestResult:
        """Characterize actual sensor noise for domain randomization.

        Read 50 samples, compute std dev and SNR per analog channel.
        Pass: noise levels within reasonable range.
        """
        client = self._get_client()
        if not client:
            return TestResult(name="Noise Floor", passed=False,
                              message="No connection")

        analog_vars = ['torque', 'pressure', 'temperature']
        readings: Dict[str, List[float]] = {
            v: [] for v in analog_vars if v in self.profile.reg_map
        }

        for _ in range(50):
            for var_name in readings:
                reg = self.profile.reg_map[var_name]
                regs = client.read_holding_registers(reg.address, 2)
                if regs and len(regs) >= 2:
                    val = decode_ge_float32(regs[0], regs[1])
                    if not (math.isnan(val) or math.isinf(val)):
                        readings[var_name].append(val)
            time.sleep(0.2)

        noise_profile = {}
        warnings = []

        for var_name, vals in readings.items():
            if len(vals) < 20:
                continue
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            std = math.sqrt(variance) if variance > 0 else 0
            snr_db = 20 * math.log10(abs(mean) / std) if std > 0 and mean != 0 else 999

            noise_profile[var_name] = {
                'mean': round(mean, 2),
                'std': round(std, 4),
                'snr_db': round(snr_db, 1),
            }

            if snr_db < 30:
                warnings.append(f"{var_name}: low SNR ({snr_db:.1f} dB)")

        return TestResult(
            name="Noise Floor",
            passed=True,  # Informational — always passes
            message=f"Profiled {len(noise_profile)} channels",
            details={'noise_profile': noise_profile},
            warnings=warnings,
        )

    def test_word_swap(self) -> TestResult:
        """Definitively confirm FLOAT32 byte order.

        Read each FLOAT32 register both ways. Check which interpretation
        gives physically sane values.
        Pass: all FLOAT32 registers agree on one byte order.
        """
        client = self._get_client()
        if not client:
            return TestResult(name="Word Swap", passed=False,
                              message="No connection")

        ws_votes = 0
        normal_votes = 0
        checked = 0

        for var_name, reg_def in self.profile.reg_map.items():
            if reg_def.data_type != 'FLOAT32':
                continue
            regs = client.read_holding_registers(reg_def.address, 2)
            if not regs or len(regs) < 2:
                continue
            if regs[0] == 0 and regs[1] == 0:
                continue

            checked += 1

            # Word-swapped (GE standard)
            sane_ws, val_ws = is_sane_float32(regs[0], regs[1])
            # Normal order
            sane_normal, val_normal = is_sane_float32(regs[1], regs[0])

            if sane_ws and not sane_normal:
                ws_votes += 1
            elif sane_normal and not sane_ws:
                normal_votes += 1
            elif sane_ws and sane_normal:
                # Both sane — pick the one in more reasonable range
                ws_votes += 1  # Default to GE standard

        expected = self.profile.word_swap
        detected = ws_votes >= normal_votes
        passed = (detected == expected) or checked == 0

        warnings = []
        if not passed:
            warnings.append(
                f"Profile says word_swap={expected} but detected "
                f"{'word-swapped' if detected else 'normal'} "
                f"(WS={ws_votes}, Normal={normal_votes})"
            )

        return TestResult(
            name="Word Swap",
            passed=passed,
            message=f"{'word-swapped' if detected else 'normal'} "
                    f"(WS={ws_votes}, Normal={normal_votes}, "
                    f"checked={checked} registers)",
            details={'word_swap_detected': detected,
                     'ws_votes': ws_votes, 'normal_votes': normal_votes},
            warnings=warnings,
        )

    def test_sample_rate(self) -> TestResult:
        """Determine the actual achievable sample rate.

        Read all mapped registers in one pass, time it, repeat 20 times.
        Pass: achievable_hz >= 1.0.
        """
        client = self._get_client()
        if not client:
            return TestResult(name="Sample Rate", passed=False,
                              message="No connection")

        # Compute optimal block reads
        addresses = []
        for reg_def in self.profile.reg_map.values():
            count = 2 if reg_def.data_type in ('FLOAT32', 'INT32') else 1
            addresses.append((reg_def.address, count))

        if not addresses:
            return TestResult(name="Sample Rate", passed=True,
                              message="No registers mapped")

        # Group into contiguous blocks (max gap of 10 for efficiency)
        addresses.sort()
        blocks = []
        block_start = addresses[0][0]
        block_end = addresses[0][0] + addresses[0][1]

        for addr, count in addresses[1:]:
            if addr <= block_end + 10:
                block_end = max(block_end, addr + count)
            else:
                blocks.append((block_start, block_end - block_start))
                block_start = addr
                block_end = addr + count
        blocks.append((block_start, block_end - block_start))

        # Time 20 full read cycles
        cycle_times = []
        for _ in range(20):
            start = time.monotonic()
            ok = True
            for block_addr, block_count in blocks:
                regs = client.read_holding_registers(block_addr, block_count)
                if regs is None:
                    ok = False
                    break
            elapsed = (time.monotonic() - start) * 1000
            if ok:
                cycle_times.append(elapsed)

        if len(cycle_times) < 5:
            return TestResult(name="Sample Rate", passed=False,
                              message=f"Only {len(cycle_times)}/20 cycles succeeded")

        avg_cycle = sum(cycle_times) / len(cycle_times)
        achievable_hz = 1000.0 / avg_cycle if avg_cycle > 0 else 0
        min_cycle = min(cycle_times)
        max_cycle = max(cycle_times)

        passed = achievable_hz >= 1.0
        warnings = []
        if achievable_hz < 1.0:
            warnings.append("Sample rate below 1 Hz — data quality will suffer")
        if achievable_hz > 5:
            warnings.append("High sample rate — likely local network (not VPN)")

        return TestResult(
            name="Sample Rate",
            passed=passed,
            message=f"{achievable_hz:.1f} Hz  "
                    f"(cycle: avg={avg_cycle:.0f}ms  "
                    f"min={min_cycle:.0f}ms  max={max_cycle:.0f}ms  "
                    f"blocks={len(blocks)})",
            details={
                'achievable_hz': round(achievable_hz, 2),
                'avg_cycle_ms': round(avg_cycle, 1),
                'min_cycle_ms': round(min_cycle, 1),
                'max_cycle_ms': round(max_cycle, 1),
                'num_blocks': len(blocks),
                'block_ranges': blocks,
            },
            warnings=warnings,
        )


# ═══════════════════════════════════════════════════════════════════
# Fleet Manager (Top-Level Orchestrator)
# ═══════════════════════════════════════════════════════════════════

class FleetManager:
    """Top-level orchestrator: detect → discover → test → capture.

    Run as a daemon on Steve's manufacturing PC and it handles everything.

    State machine:
      WATCHING → TUNNEL_DETECTED → DISCOVERING → IDENTIFYING →
      TESTING → READY → TUNNEL_LOST → WATCHING
    """

    STATES = [
        'WATCHING', 'TUNNEL_DETECTED', 'DISCOVERING',
        'IDENTIFYING', 'TESTING', 'READY', 'TUNNEL_LOST',
    ]

    def __init__(self, profiles_dir: str = "profiles",
                 data_dir: str = "data",
                 catalog_path: str = "fleet_catalog.yaml"):
        self.profiles_dir = profiles_dir
        self.data_dir = data_dir
        self.catalog_path = catalog_path
        self.detector = EWonDetector()
        self.identifier = MachineIdentifier(profiles_dir, catalog_path)
        self.catalog = self.identifier.catalog
        self.state = "WATCHING"
        self._active_machine: Optional[MachineProfile] = None
        self._last_report: Optional[TestReport] = None

    def run_daemon(self, poll_interval: float = 10.0):
        """Main daemon loop — watch for tunnels and react."""
        print("\n" + "=" * 60)
        print("  TopDrive AI — Fleet Manager Daemon")
        print("=" * 60)
        print(f"  Profiles dir: {self.profiles_dir}/")
        print(f"  Data dir: {self.data_dir}/")
        print(f"  Known machines: {len(self.identifier.known_profiles)}")
        print(f"  Poll interval: {poll_interval}s")
        print("=" * 60)

        def on_tunnel_change(tunnel_info):
            if tunnel_info:
                self._handle_tunnel_up(tunnel_info)
            else:
                self._handle_tunnel_down()

        self.detector.watch(on_tunnel_change, poll_interval)

    def _handle_tunnel_up(self, tunnel_info: Dict):
        """React to a new VPN tunnel appearing."""
        self.state = "TUNNEL_DETECTED"
        gw = tunnel_info['gateway_ip']
        print(f"\n  VPN Tunnel UP: {gw}")
        print(f"  Device: {tunnel_info.get('device_name', 'unknown')}")
        print(f"  Latency: {tunnel_info.get('latency_ms', '?')}ms")

        # Discover PLCs on the LAN
        self.state = "DISCOVERING"
        lan = LANDiscovery(gw)
        hosts = lan.discover_hosts(timeout=2.0)

        modbus_hosts = [h for h in hosts if h['has_modbus']]
        if not modbus_hosts:
            print("  No Modbus hosts found on LAN")
            self.state = "WATCHING"
            return

        # Fingerprint and identify each PLC
        self.state = "IDENTIFYING"
        for host_info in modbus_hosts:
            ip = host_info['ip']
            print(f"\n  Fingerprinting {ip}...")
            fingerprint = lan.fingerprint_plc(ip)
            match_type, profile = self.identifier.identify(fingerprint)

            if profile:
                print(f"  Matched: {profile.name} ({match_type})")
                self.state = "TESTING"
                tester = SmartTester(profile)
                report = tester.run_all()
                self._last_report = report
                print(report.summary())

                if report.passed:
                    self._active_machine = profile
                    self.state = "READY"
                    print(f"\n  Machine '{profile.name}' is READY for capture")
                else:
                    print(f"\n  Machine '{profile.name}' has test failures")
                    self.state = "WATCHING"
            else:
                print(f"  New machine at {ip} — needs onboarding")
                print(f"  Run: python fleet_manager.py --onboard --host {ip}")
                self.state = "WATCHING"

    def _handle_tunnel_down(self):
        """React to VPN tunnel disappearing."""
        if self._active_machine:
            print(f"\n  VPN Tunnel DOWN — disconnected from "
                  f"{self._active_machine.name}")
        else:
            print("\n  VPN Tunnel DOWN")
        self._active_machine = None
        self.state = "WATCHING"

    def scan_now(self) -> Dict:
        """One-shot: scan for active tunnel, discover, test, report."""
        print("\n" + "=" * 60)
        print("  TopDrive AI — Fleet Scan")
        print("=" * 60)

        result = {
            'tunnel': None,
            'hosts': [],
            'machines': [],
            'test_reports': [],
        }

        # Detect tunnel
        tunnel = self.detector.detect_active_tunnel()
        result['tunnel'] = tunnel

        if not tunnel:
            print("  No eWon VPN tunnel detected")
            print("  Open eCatcher and connect to a device, then retry.")
            return result

        print(f"  Tunnel: {tunnel['gateway_ip']} "
              f"(via {tunnel['detection_method']})")

        # Discover LAN
        lan = LANDiscovery(tunnel['gateway_ip'])
        hosts = lan.discover_hosts(timeout=2.0)
        result['hosts'] = hosts

        # Test each Modbus host
        for host_info in hosts:
            if not host_info['has_modbus']:
                continue

            ip = host_info['ip']
            fingerprint = lan.fingerprint_plc(ip)
            match_type, profile = self.identifier.identify(fingerprint)

            machine_info = {
                'ip': ip,
                'fingerprint': fingerprint,
                'match_type': match_type,
                'profile_name': profile.name if profile else None,
            }

            if profile:
                tester = SmartTester(profile)
                report = tester.run_all()
                machine_info['test_report'] = report
                result['test_reports'].append(report)
                print(report.summary())
            else:
                print(f"\n  Unknown machine at {ip} — run --onboard")

            result['machines'].append(machine_info)

        return result

    def onboard_new_machine(self, plc_ip: str, plc_port: int = 502,
                            name: Optional[str] = None) -> Optional[MachineProfile]:
        """Full onboarding workflow for a machine we've never seen.

        Steps:
          1. Fingerprint the PLC
          2. Run differential register scan (requires machine operating)
          3. Classify registers heuristically
          4. Run smart tests to validate
          5. Save profile YAML
          6. Run a short capture to verify data quality
        """
        print("\n" + "=" * 60)
        print(f"  TopDrive AI — Onboarding New Machine")
        print(f"  Target: {plc_ip}:{plc_port}")
        print("=" * 60)

        # Step 1: Fingerprint
        print("\n  Step 1: Fingerprinting PLC...")
        lan = LANDiscovery(plc_ip)
        fingerprint = lan.fingerprint_plc(plc_ip, plc_port)

        print(f"    Type: {fingerprint.get('plc_type', 'unknown')}")
        print(f"    State: {fingerprint.get('plc_state', 'unknown')}")
        print(f"    Unit ID: {fingerprint.get('unit_id', 0)}")
        print(f"    Word swap: {fingerprint.get('word_swap', True)}")
        print(f"    RTC: {'detected' if fingerprint.get('rtc_detected') else 'not found'}")
        print(f"    Non-zero regs: {fingerprint.get('total_nonzero', 0)}")

        # Step 2: Differential register scan
        print("\n  Step 2: Differential register scan...")
        print("  NOTE: Machine must be ACTIVELY THREADING for best results.")

        scanner = RegisterScanner(
            plc_ip, plc_port,
            unit_id=fingerprint.get('unit_id', 0),
            word_swap=fingerprint.get('word_swap', True),
        )

        candidates = scanner.differential_scan(
            start=1, end=5000, block_size=100,
            num_passes=3, wait_seconds=10,
        )

        if not candidates:
            print("\n  WARNING: No changing registers found!")
            print("  Machine may be idle. Try again while threading pipe.")
            scanner.client.close()
            return None

        # Step 3: Classify
        print("\n  Step 3: Classifying registers...")
        candidates = scanner.classify_registers(candidates)

        # Print discovered registers
        print(f"\n  {'Addr':>6s}  {'Type':>8s}  {'Class':>16s}  "
              f"{'Conf':>6s}  {'Values'}")
        print(f"  {'-'*6}  {'-'*8}  {'-'*16}  {'-'*6}  {'-'*30}")
        for addr in sorted(candidates.keys()):
            c = candidates[addr]
            if c.is_float32_pair:
                vals = ", ".join(f"{v:.2f}" for v in c.float32_values[:3])
            else:
                vals = ", ".join(str(v) for v in c.values[:3])
            print(f"  R{addr:5d}  {c.data_type:>8s}  "
                  f"{c.classification:>16s}  {c.confidence:>5.0%}  [{vals}]")

        # Step 4: Generate profile
        profile_name = name or f"rig_{plc_ip.replace('.', '_')}"
        output_path = f"{self.profiles_dir}/{profile_name}.yaml"

        profile = scanner.generate_profile(
            candidates, name=profile_name, output_path=output_path,
        )
        scanner.client.close()

        # Step 5: Run smart tests
        print(f"\n  Step 4: Running smart tests on '{profile.name}'...")
        tester = SmartTester(profile)
        report = tester.run_all()
        self._last_report = report
        print(report.summary())

        # Reload profiles
        self.identifier._load_all_profiles()

        print(f"\n  Onboarding complete for '{profile.name}'")
        print(f"  Profile saved: {output_path}")
        print(f"  IMPORTANT: Ask Steve to confirm register assignments!")
        print(f"  Edit {output_path} to correct any mistakes.")

        return profile

    def test_machine(self, machine_name: str) -> Optional[TestReport]:
        """Run smart tests on a specific known machine."""
        profile = self.identifier.known_profiles.get(machine_name)
        if not profile:
            print(f"  Unknown machine: {machine_name}")
            print(f"  Known: {', '.join(self.identifier.known_profiles.keys())}")
            return None

        print(f"\n  Testing '{profile.name}' at {profile.plc_ip}:{profile.plc_port}")
        tester = SmartTester(profile)
        report = tester.run_all()
        self._last_report = report
        print(report.summary())
        return report

    def generate_fleet_report(self) -> str:
        """Generate a summary of all known machines and their status."""
        lines = [
            "",
            "  " + "=" * 58,
            "  TopDrive AI Fleet Status",
            "  " + "=" * 58,
            f"  {'Machine':<22s} {'IP':<18s} {'Status':<12s} {'Regs':>4s}",
            "  " + "-" * 58,
        ]

        for name, profile in self.identifier.known_profiles.items():
            # Quick connectivity check
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                result = sock.connect_ex((profile.plc_ip, profile.plc_port))
                sock.close()
                status = "CONNECTED" if result == 0 else "OFFLINE"
            except (socket.error, OSError):
                status = "OFFLINE"

            regs = len(profile.reg_map)
            lines.append(
                f"  {name:<22s} {profile.plc_ip:<18s} {status:<12s} {regs:>4d}"
            )

        lines.append("  " + "=" * 58)

        # Add fleet catalog stats if available
        if self.catalog.entries:
            lines.append(self.catalog.summary())

        report = "\n".join(lines)
        print(report)
        return report


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="TopDrive AI — Fleet Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Daemon mode — watch for VPN tunnels and auto-handle
  python fleet_manager.py --daemon

  # One-shot scan — detect tunnel, discover, test, report
  python fleet_manager.py --scan-now

  # Test a specific known machine
  python fleet_manager.py --test shop_unit

  # Onboard a new machine
  python fleet_manager.py --onboard --host 129.168.1.25

  # Fleet status report
  python fleet_manager.py --fleet-report

  # Detect VPN tunnel only
  python fleet_manager.py --detect-tunnel
        """)

    parser.add_argument("--daemon", action="store_true",
                        help="Run in daemon mode (watch for tunnels)")
    parser.add_argument("--scan-now", action="store_true",
                        help="One-shot: detect, discover, test, report")
    parser.add_argument("--test", type=str, metavar="MACHINE",
                        help="Run smart tests on a known machine")
    parser.add_argument("--onboard", action="store_true",
                        help="Onboard a new machine (requires --host)")
    parser.add_argument("--fleet-report", action="store_true",
                        help="Print fleet status report")
    parser.add_argument("--detect-tunnel", action="store_true",
                        help="Detect active eWon VPN tunnel")
    parser.add_argument("--fleet-catalog", action="store_true",
                        help="Show fleet catalog summary (from Talk2m export)")

    parser.add_argument("--host", type=str, default=None,
                        help="PLC IP address (for --onboard)")
    parser.add_argument("--port", type=int, default=502,
                        help="Modbus TCP port (default: 502)")
    parser.add_argument("--name", type=str, default=None,
                        help="Machine profile name (for --onboard)")
    parser.add_argument("--profiles-dir", type=str, default="profiles",
                        help="Profiles directory (default: profiles/)")
    parser.add_argument("--data-dir", type=str, default="data",
                        help="Data directory (default: data/)")
    parser.add_argument("--poll-interval", type=float, default=10.0,
                        help="Daemon poll interval in seconds (default: 10)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose logging")

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    fm = FleetManager(profiles_dir=args.profiles_dir, data_dir=args.data_dir)

    if args.daemon:
        fm.run_daemon(poll_interval=args.poll_interval)

    elif args.scan_now:
        fm.scan_now()

    elif args.test:
        fm.test_machine(args.test)

    elif args.onboard:
        if not args.host:
            print("  ERROR: --onboard requires --host <ip>")
            sys.exit(1)
        fm.onboard_new_machine(args.host, args.port, name=args.name)

    elif args.fleet_report:
        fm.generate_fleet_report()

    elif args.fleet_catalog:
        catalog = FleetCatalog()
        print(catalog.summary())
        # Also show equipment type distribution for training
        dist = catalog.equipment_type_distribution()
        if dist:
            total = sum(dist.values())
            print("\n  Training Data Weighting (field units only):")
            for eq_type, count in sorted(dist.items(), key=lambda x: -x[1]):
                pct = count / total * 100
                spec = EQUIPMENT_SPECS.get(
                    EquipmentType(eq_type), EQUIPMENT_SPECS[EquipmentType.UNKNOWN])
                print(f"    {eq_type:<20s} {count:>3d} ({pct:4.1f}%)  "
                      f"gear={spec.gear_ratio}  torque={spec.max_torque_ftlbs:,.0f} ft-lbs")

    elif args.detect_tunnel:
        print("\n  Detecting eWon VPN tunnel...")
        detector = EWonDetector()
        tunnel = detector.detect_active_tunnel()
        if tunnel:
            print(f"  Tunnel detected!")
            print(f"    Gateway: {tunnel['gateway_ip']}")
            print(f"    Device: {tunnel.get('device_name', 'unknown')}")
            print(f"    Method: {tunnel['detection_method']}")
            print(f"    Latency: {tunnel.get('latency_ms', '?')}ms")
        else:
            print("  No active tunnel detected.")
            print("  Open eCatcher and connect to a device.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
