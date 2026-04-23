"""Fleet awareness — identify which machine is connected.

Given an eWon device name (from eCatcher) or a PLC IP that's reachable
through the VPN, this module returns:

    - rig identity (customer, name, equipment type)
    - physical specs (HP, gear ratio, max torque, max RPM)
    - correct register map for that equipment class
    - calibration template to use

Backed by fleet_catalog.yaml (~87 eWon devices) and profiles/*.yaml (one
per rig). Both files live at the repo root; this module just reads them.

Usage:
    from hxi_optimizer.comms.fleet import FleetCatalog
    catalog = FleetCatalog.load()
    match = catalog.identify_by_name("Precision Rig 707 3pd HT")
    match = catalog.identify_by_ip("192.168.1.25")
    specs = catalog.specs_for("hxi_ht")
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("fleet")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CATALOG_PATH = REPO_ROOT / "fleet_catalog.yaml"
PROFILES_DIR = REPO_ROOT / "profiles"


# ──────────────────────────────────────────────────────────────────────
# Equipment class specifications
# ──────────────────────────────────────────────────────────────────────
@dataclass
class EquipmentSpec:
    """Physical specs for one equipment class. From README + OEM sheets."""
    equipment_type: str
    display_name: str
    horsepower: float
    gear_ratio: float              # motor:spindle
    max_torque_ft_lbs: float
    max_rpm: float
    plc_type: str                  # CPE305, CompactLogix, Rx3i, ...
    motor_displacement_cc: float   # Rineer MV125H = 3212
    pump_displacement_cc: float    # S90 Series = 130 typical
    n_pumps: int = 4
    # Register map — base offset + convention
    register_convention: str = "R06600"  # or "R160", "R6000"
    # Hydraulic plant defaults
    tau_hydraulic_s: float = 0.35
    rotor_inertia_kgm2: float = 50.0
    noise_floor_60_rpm: float = 1.5
    notes: str = ""


EQUIPMENT_CATALOG: dict[str, EquipmentSpec] = {
    "hxi": EquipmentSpec(
        equipment_type="hxi",
        display_name="TESCO HXI 800HP",
        horsepower=800, gear_ratio=10.5, max_torque_ft_lbs=37500, max_rpm=228,
        plc_type="CPE305",
        motor_displacement_cc=3212, pump_displacement_cc=130,
        register_convention="R06600",
        notes="Primary target. 4x S90 pumps, Rineer MV125H motor.",
    ),
    "hxi_ht": EquipmentSpec(
        equipment_type="hxi_ht",
        display_name="TESCO HXI HT 800HP",
        horsepower=800, gear_ratio=14.0, max_torque_ft_lbs=50000, max_rpm=170,
        plc_type="CPE305",
        motor_displacement_cc=3212, pump_displacement_cc=130,
        register_convention="R06600",
        notes="High-torque variant. Lower RPM cap due to higher gear ratio.",
    ),
    "hxi_smart_slide": EquipmentSpec(
        equipment_type="hxi_smart_slide",
        display_name="TESCO HXI Smart Slide 800HP",
        horsepower=800, gear_ratio=10.5, max_torque_ft_lbs=37500, max_rpm=228,
        plc_type="CPE305",
        motor_displacement_cc=3212, pump_displacement_cc=130,
        register_convention="R06600",
        notes="HXI base + Canadian Global Smart Slide system.",
    ),
    "exi": EquipmentSpec(
        equipment_type="exi",
        display_name="TESCO EXI 800HP",
        horsepower=800, gear_ratio=11.0, max_torque_ft_lbs=40000, max_rpm=210,
        plc_type="CPE305",
        motor_displacement_cc=3212, pump_displacement_cc=130,
        register_convention="R06600",
    ),
    "fds": EquipmentSpec(
        equipment_type="fds",
        display_name="TESCO FDS 800HP",
        horsepower=800, gear_ratio=10.5, max_torque_ft_lbs=37500, max_rpm=228,
        plc_type="CompactLogix",
        motor_displacement_cc=3212, pump_displacement_cc=130,
        register_convention="R160",
        notes="CompactLogix PLC instead of CPE305.",
    ),
    "rostel": EquipmentSpec(
        equipment_type="rostel",
        display_name="Rostel TDS11",
        horsepower=750, gear_ratio=10.0, max_torque_ft_lbs=35000, max_rpm=240,
        plc_type="Rx3i",
        motor_displacement_cc=2800, pump_displacement_cc=125,
        register_convention="R160",
    ),
    "warrior": EquipmentSpec(
        equipment_type="warrior",
        display_name="Warrior 600HP",
        horsepower=600, gear_ratio=9.0, max_torque_ft_lbs=25000, max_rpm=260,
        plc_type="CPE305",
        motor_displacement_cc=2500, pump_displacement_cc=110,
        register_convention="R160",
        tau_hydraulic_s=0.28,
        rotor_inertia_kgm2=35.0,
    ),
    "smart_drive": EquipmentSpec(
        equipment_type="smart_drive",
        display_name="Smart Drive 900HP",
        horsepower=900, gear_ratio=10.5, max_torque_ft_lbs=42000, max_rpm=220,
        plc_type="CPE305",
        motor_displacement_cc=3500, pump_displacement_cc=140,
        register_convention="R06600",
    ),
    "emi": EquipmentSpec(
        equipment_type="emi",
        display_name="EMI 400HP",
        horsepower=400, gear_ratio=8.0, max_torque_ft_lbs=18000, max_rpm=300,
        plc_type="CPE305",
        motor_displacement_cc=1800, pump_displacement_cc=90,
        register_convention="R160",
        tau_hydraulic_s=0.22,
        rotor_inertia_kgm2=22.0,
    ),
    "rx3i": EquipmentSpec(
        equipment_type="rx3i",
        display_name="GE Rx3i (generic)",
        horsepower=0, gear_ratio=0, max_torque_ft_lbs=0, max_rpm=0,
        plc_type="Rx3i",
        motor_displacement_cc=0, pump_displacement_cc=0,
        notes="PLC-only classification — no specific equipment info.",
    ),
    "hxi_ss": EquipmentSpec(
        equipment_type="hxi_ss",
        display_name="TESCO HXI Smart Slide 800HP",
        horsepower=800, gear_ratio=10.5, max_torque_ft_lbs=37500, max_rpm=228,
        plc_type="CPE305",
        motor_displacement_cc=3212, pump_displacement_cc=130,
        register_convention="R06600",
        notes="Same physics as hxi + Canadian Global Smart Slide system.",
    ),
    "hxi_relay": EquipmentSpec(
        equipment_type="hxi_relay",
        display_name="TESCO HXI 800HP (relay-only)",
        horsepower=800, gear_ratio=10.5, max_torque_ft_lbs=37500, max_rpm=228,
        plc_type="Relay",
        motor_displacement_cc=3212, pump_displacement_cc=130,
        register_convention="R06600",
        notes="HXI with older relay logic — limited or no PLC scanning.",
    ),
    "hci": EquipmentSpec(
        equipment_type="hci",
        display_name="HCI (TESCO variant)",
        horsepower=800, gear_ratio=10.5, max_torque_ft_lbs=37500, max_rpm=228,
        plc_type="CPE305",
        motor_displacement_cc=3212, pump_displacement_cc=130,
        register_convention="R06600",
    ),
    "hmi": EquipmentSpec(
        equipment_type="hmi",
        display_name="HMI-only (no top-drive scan)",
        horsepower=0, gear_ratio=0, max_torque_ft_lbs=0, max_rpm=0,
        plc_type="HMI",
        motor_displacement_cc=0, pump_displacement_cc=0,
        notes="Operator interface only — no rig control.",
    ),
    "wireless": EquipmentSpec(
        equipment_type="wireless",
        display_name="Wireless telemetry system",
        horsepower=0, gear_ratio=0, max_torque_ft_lbs=0, max_rpm=0,
        plc_type="Wireless",
        motor_displacement_cc=0, pump_displacement_cc=0,
        notes="Integrated wireless system — not a top drive.",
    ),
    "shop_unit": EquipmentSpec(
        equipment_type="shop_unit",
        display_name="TESCO HXI Shop Unit (legacy)",
        horsepower=800, gear_ratio=10.5, max_torque_ft_lbs=37500, max_rpm=228,
        plc_type="CPE305",
        motor_displacement_cc=3212, pump_displacement_cc=130,
        register_convention="R6000",
        notes="Original shop-floor test unit — legacy %R6000 register layout.",
    ),
    "unknown": EquipmentSpec(
        equipment_type="unknown",
        display_name="Unclassified",
        horsepower=0, gear_ratio=0, max_torque_ft_lbs=0, max_rpm=0,
        plc_type="?",
        motor_displacement_cc=0, pump_displacement_cc=0,
        notes="Needs fleet-catalog review to classify.",
    ),
}


# ──────────────────────────────────────────────────────────────────────
# Fleet catalog & identification
# ──────────────────────────────────────────────────────────────────────
@dataclass
class FleetDevice:
    ewon_name: str
    equipment_type: str
    customer: str
    description: str
    firmware: str
    status: str
    profile_path: Optional[Path] = None


@dataclass
class Identification:
    """Result of identifying a connected PLC."""
    ewon_name: str
    equipment_type: str
    customer: str
    description: str
    spec: EquipmentSpec
    profile_path: Optional[Path] = None
    plc_ip: Optional[str] = None
    confidence: float = 0.0
    source: str = "unknown"  # name_match | ip_match | fingerprint | manual


class FleetCatalog:
    """Loads fleet_catalog.yaml + profiles/*.yaml into one queryable catalog."""

    def __init__(self, devices: list[FleetDevice]) -> None:
        self.devices = devices
        self._by_name = {_slug(d.ewon_name): d for d in devices}
        # Build IP index from profiles
        self._by_ip: dict[str, FleetDevice] = {}
        for d in devices:
            if d.profile_path and d.profile_path.exists():
                ip = _profile_ip(d.profile_path)
                if ip and ip not in ("0.0.0.0", "129.168.1.25"):  # skip placeholder
                    self._by_ip[ip] = d

    @classmethod
    def load(cls, catalog_path: Path = CATALOG_PATH) -> "FleetCatalog":
        if not catalog_path.exists():
            logger.warning(f"{catalog_path} not found — empty fleet catalog")
            return cls([])
        import yaml
        data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
        raw = data.get("devices", [])
        devices: list[FleetDevice] = []
        for d in raw:
            name = d.get("ewon_name", "")
            slug = _slug(name)
            profile = PROFILES_DIR / f"{slug}.yaml"
            devices.append(FleetDevice(
                ewon_name=name,
                equipment_type=d.get("equipment_type", "unknown"),
                customer=d.get("customer", ""),
                description=d.get("description", ""),
                firmware=d.get("firmware", ""),
                status=d.get("status", ""),
                profile_path=profile if profile.exists() else None,
            ))
        logger.info(f"Loaded {len(devices)} fleet devices from {catalog_path.name}")
        return cls(devices)

    # ── identification ──────────────────────────────────────────────

    def identify_by_name(self, ewon_name: str) -> Optional[Identification]:
        d = self._by_name.get(_slug(ewon_name))
        if not d:
            # Try partial match
            s = _slug(ewon_name)
            for k, v in self._by_name.items():
                if s in k or k in s:
                    d = v
                    break
        if not d:
            return None
        return self._to_identification(d, source="name_match", confidence=1.0)

    def identify_by_ip(self, plc_ip: str) -> Optional[Identification]:
        d = self._by_ip.get(plc_ip)
        if not d:
            return None
        id_ = self._to_identification(d, source="ip_match", confidence=0.95)
        id_.plc_ip = plc_ip
        return id_

    def by_equipment_type(self, equipment_type: str) -> list[FleetDevice]:
        return [d for d in self.devices if d.equipment_type == equipment_type]

    def summary(self) -> dict[str, int]:
        """Counts by equipment_type, for training-data weighting."""
        out: dict[str, int] = {}
        for d in self.devices:
            out[d.equipment_type] = out.get(d.equipment_type, 0) + 1
        return dict(sorted(out.items(), key=lambda x: -x[1]))

    # ── internal ────────────────────────────────────────────────────

    def _to_identification(self, d: FleetDevice, source: str,
                           confidence: float) -> Identification:
        spec = EQUIPMENT_CATALOG.get(d.equipment_type,
                                      EQUIPMENT_CATALOG["unknown"])
        return Identification(
            ewon_name=d.ewon_name,
            equipment_type=d.equipment_type,
            customer=d.customer,
            description=d.description,
            spec=spec,
            profile_path=d.profile_path,
            confidence=confidence,
            source=source,
        )

    @staticmethod
    def specs_for(equipment_type: str) -> EquipmentSpec:
        return EQUIPMENT_CATALOG.get(equipment_type, EQUIPMENT_CATALOG["unknown"])


# ──────────────────────────────────────────────────────────────────────
# PLC fingerprinting (identifies equipment class by live-probing)
# ──────────────────────────────────────────────────────────────────────
async def fingerprint_plc(host: str, port: int = 502,
                           timeout: float = 3.0) -> Optional[str]:
    """Attempt to classify a live PLC by probing known register layouts.

    Returns the most likely `equipment_type`, or None if unable to classify.
    Strategy:
      1. Try to read %R06600 block (HXI family CPE305) — non-zero = likely HXI
      2. Try to read %R160 block (older HXI/Warrior/Rostel) — non-zero = that family
      3. Try to read %R6000 block (legacy shop unit)
      4. Cross-check torque + RPM ranges against equipment spec max values
    """
    try:
        from pymodbus.client import AsyncModbusTcpClient
        from pymodbus import FramerType
        import struct
    except ImportError:
        logger.error("pymodbus not installed — can't fingerprint")
        return None

    async def try_block(client, addr: int, count: int) -> Optional[list[int]]:
        try:
            import asyncio
            r = await asyncio.wait_for(
                client.read_holding_registers(address=addr, count=count,
                                               device_id=1),
                timeout=timeout,
            )
            return None if r.isError() else list(r.registers)
        except Exception:
            return None

    client = AsyncModbusTcpClient(host=host, port=port,
                                   framer=FramerType.SOCKET, timeout=timeout)
    await client.connect()
    if not client.connected:
        return None

    # Try %R06600 (modern HXI block)
    r06600 = await try_block(client, 6599, 28)
    # Try %R160 (legacy HXI / Warrior / Rostel)
    r160 = await try_block(client, 162, 40)
    # Try %R6000 (legacy shop unit)
    r6000 = await try_block(client, 5999, 30)
    client.close()

    def _nonzero(regs: list[int] | None) -> bool:
        return bool(regs and any(v != 0 for v in regs))

    # Heuristic classification (conservative — returns 'unknown' if ambiguous)
    if _nonzero(r06600):
        # HXI family. Could be hxi, hxi_ht, hxi_smart_slide — can't distinguish
        # without reading oscillation register patterns; default to 'hxi' and
        # let the fleet catalog (eWon name) refine.
        return "hxi"
    if _nonzero(r160):
        return "hxi"  # legacy layout — upstream profile lookup can refine
    if _nonzero(r6000):
        return "hxi"  # shop-unit layout
    return None


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _slug(name: str) -> str:
    """Slugify eWon name to match profile filename."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s


def _profile_ip(path: Path) -> Optional[str]:
    """Extract plc_ip from a profile YAML (quick regex, avoids yaml parse)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(r'plc_ip:\s*"?(\d+\.\d+\.\d+\.\d+)"?', text)
    return m.group(1) if m else None
