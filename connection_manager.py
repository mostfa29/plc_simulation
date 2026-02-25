"""
Connection Manager — Multi-Machine Orchestration
===================================================
Manages connections to multiple machines via eWon VPN.

Steve has multiple rigs, each with its own eWon Flexy 205
on Talk2m VPN. Only ONE VPN tunnel is active at a time
(eCatcher limitation — must "Connect" to each device manually).

This manager:
    1. Loads all known MachineProfiles from profiles/
    2. Tests connectivity to each PLC
    3. Reports which machines are reachable
    4. Routes data collection to the active machine

Usage:
    mgr = ConnectionManager("profiles/")
    mgr.scan()
    active = mgr.get_active()
    if active:
        print(f"Connected to {active.name} at {active.plc_ip}")
"""
import socket
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional

from config import MachineProfile

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages connections to multiple machines via eWon VPN.

    Only ONE VPN tunnel is active at a time (eCatcher limitation).
    The manager tests connectivity to determine which machine is reachable.
    """

    def __init__(self, profiles_dir: str = "profiles", timeout: float = 3.0):
        self.profiles_dir = profiles_dir
        self.timeout = timeout
        self.profiles: Dict[str, MachineProfile] = {}
        self.reachable: Dict[str, bool] = {}
        self._active_profile: Optional[MachineProfile] = None
        self._load_profiles()

    def _load_profiles(self):
        """Load all machine profiles from the profiles directory."""
        self.profiles = MachineProfile.load_all_profiles(self.profiles_dir)
        if self.profiles:
            logger.info(f"Loaded {len(self.profiles)} machine profiles:")
            for name, profile in self.profiles.items():
                logger.info(f"  {name}: {profile.plc_ip}:{profile.plc_port}")
        else:
            logger.warning(f"No profiles found in {self.profiles_dir}/")

    def scan(self) -> Dict[str, bool]:
        """Test connectivity to each known machine.

        Returns dict of {profile_name: is_reachable}.
        """
        self.reachable = {}
        self._active_profile = None

        for name, profile in self.profiles.items():
            reachable = self._test_connection(profile)
            self.reachable[name] = reachable
            if reachable:
                self._active_profile = profile
                logger.info(f"  {name} ({profile.plc_ip}): REACHABLE")
            else:
                logger.info(f"  {name} ({profile.plc_ip}): unreachable")

        return self.reachable

    def _test_connection(self, profile: MachineProfile) -> bool:
        """Test if a PLC is reachable via TCP connection."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((profile.plc_ip, profile.plc_port))
            sock.close()
            return result == 0
        except (socket.error, OSError):
            return False

    def get_active(self) -> Optional[MachineProfile]:
        """Get the currently reachable machine profile.

        If multiple are reachable (unlikely with eWon single-tunnel),
        returns the first one found.
        """
        if self._active_profile:
            return self._active_profile

        # Re-scan if no active profile
        self.scan()
        return self._active_profile

    def get_profile(self, name: str) -> Optional[MachineProfile]:
        """Get a specific profile by name."""
        return self.profiles.get(name)

    def get_profile_by_ip(self, ip: str) -> Optional[MachineProfile]:
        """Get a profile by PLC IP address."""
        for profile in self.profiles.values():
            if profile.plc_ip == ip:
                return profile
        return None

    def list_machines(self) -> List[dict]:
        """List all known machines with their status."""
        machines = []
        for name, profile in self.profiles.items():
            machines.append({
                'name': name,
                'plc_ip': profile.plc_ip,
                'plc_port': profile.plc_port,
                'reachable': self.reachable.get(name, False),
                'registers': len(profile.reg_map),
            })
        return machines

    def wait_for_connection(self, poll_interval: float = 10.0,
                            max_wait: float = 300.0) -> Optional[MachineProfile]:
        """Wait until a machine becomes reachable.

        Useful when waiting for eCatcher VPN to connect.
        """
        start = time.time()
        while time.time() - start < max_wait:
            self.scan()
            if self._active_profile:
                return self._active_profile
            logger.info(f"  No machines reachable. Retrying in {poll_interval}s...")
            time.sleep(poll_interval)
        return None
