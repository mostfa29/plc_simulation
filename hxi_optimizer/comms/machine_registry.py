"""Per-machine register registry.

Different rigs have their PID + torque data at different register addresses
(eWon profiles range from %R160 to %R6000 depending on the PLC program
version and equipment class). This module loads every profile in
profiles/*.yaml, keyed by ewon_name, so the optimizer can switch register
decoders automatically when it connects to a new rig.

Data flow:
    profiles/<rig>.yaml  (reg_map section) ──► MachineRegisterMap
    fleet_catalog.yaml   (ewon_name)       ──► MachineRecord
    live PLC host (IP)                     ──► MachineRecord (via fleet IP index)

Anyone asking "what's the RPM register on THIS rig?" calls:
    rec = registry.get_for_rig("Precision Rig 707 3pd HT")
    addr = rec.register_map.get_address("rpm")   # pymodbus 0-indexed
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from hxi_optimizer.comms.fleet import (
    EQUIPMENT_CATALOG, EquipmentSpec, FleetCatalog, _slug,
)

logger = logging.getLogger("machine_registry")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"

# Default register layout (new HXI optimizer block at %R06600)
# Used when a profile has no reg_map OR for unknown machines.
DEFAULT_HXI_OPTIMIZER_MAP = {
    "rpm":               {"address": 6599, "data_type": "FLOAT32", "ge_address": "%R06600"},
    "swash_output":      {"address": 6601, "data_type": "INT16",   "ge_address": "%R06602"},
    "swash_lower":       {"address": 6602, "data_type": "INT16",   "ge_address": "%R06603"},
    "swash_upper":       {"address": 6603, "data_type": "INT16",   "ge_address": "%R06604"},
    "heartbeat":         {"address": 6604, "data_type": "INT16",   "ge_address": "%R06605"},
    "active_lower":      {"address": 6609, "data_type": "INT16",   "ge_address": "%R06610"},
    "active_upper":      {"address": 6610, "data_type": "INT16",   "ge_address": "%R06611"},
    "status_word":       {"address": 6612, "data_type": "WORD",    "ge_address": "%R06613"},
    "ss_setpoint_fwd":   {"address": 6615, "data_type": "FLOAT32", "ge_address": "%R06616"},
    "bump_flag_fwd":     {"address": 6626, "data_type": "INT16",   "ge_address": "%R06627"},
    "bump_flag_rev":     {"address": 6627, "data_type": "INT16",   "ge_address": "%R06628"},
    "delivered_torque":  {"address": 6645, "data_type": "FLOAT32", "ge_address": "%R06646"},
    "esd_word":          {"address": 6664, "data_type": "WORD",    "ge_address": "%R06665"},
    "loop_temp":         {"address": 6669, "data_type": "FLOAT32", "ge_address": "%R06670"},
}


@dataclass
class RegisterEntry:
    name: str
    address: int                 # pymodbus 0-indexed
    data_type: str               # FLOAT32 | INT16 | INT32 | WORD
    unit: str = ""
    scale: float = 1.0
    description: str = ""
    source: str = "profile"      # profile | default | live

    @property
    def ge_address(self) -> str:
        return f"%R{self.address + 1:05d}"

    @property
    def word_count(self) -> int:
        return 2 if self.data_type.upper() in ("FLOAT32", "INT32") else 1


@dataclass
class MachineRegisterMap:
    """The live per-machine register lookup."""
    source_profile: Optional[Path] = None
    registers: dict[str, RegisterEntry] = field(default_factory=dict)

    def get_address(self, name: str) -> Optional[int]:
        r = self.registers.get(name)
        return r.address if r else None

    def get_entry(self, name: str) -> Optional[RegisterEntry]:
        return self.registers.get(name)

    def compute_read_blocks(self, max_gap: int = 8) -> list[dict]:
        """Merge nearby register addresses into FC03 read blocks."""
        items = sorted(self.registers.values(), key=lambda r: r.address)
        if not items:
            return []
        blocks = []
        block_start = items[0].address
        block_end = items[0].address + items[0].word_count - 1
        for r in items[1:]:
            if r.address - block_end <= max_gap:
                block_end = max(block_end, r.address + r.word_count - 1)
            else:
                blocks.append({"start": block_start,
                               "count": block_end - block_start + 1})
                block_start = r.address
                block_end = r.address + r.word_count - 1
        blocks.append({"start": block_start, "count": block_end - block_start + 1})
        return blocks


@dataclass
class MachineRecord:
    """Full runtime record for one machine (combined from all sources)."""
    ewon_name: str
    equipment_type: str
    customer: str = ""
    description: str = ""
    firmware: str = ""
    status: str = ""
    plc_ip: Optional[str] = None
    plc_port: int = 502
    unit_id: int = 1
    word_swap: bool = True
    profile_path: Optional[Path] = None
    spec: Optional[EquipmentSpec] = None
    register_map: MachineRegisterMap = field(default_factory=MachineRegisterMap)

    def to_json(self) -> dict:
        return {
            "ewon_name": self.ewon_name,
            "equipment_type": self.equipment_type,
            "customer": self.customer,
            "description": self.description,
            "firmware": self.firmware,
            "status": self.status,
            "plc_ip": self.plc_ip,
            "plc_port": self.plc_port,
            "unit_id": self.unit_id,
            "word_swap": self.word_swap,
            "profile_file": self.profile_path.name if self.profile_path else None,
            "spec": {
                "display_name": self.spec.display_name if self.spec else "",
                "horsepower": self.spec.horsepower if self.spec else 0,
                "gear_ratio": self.spec.gear_ratio if self.spec else 0,
                "max_torque_ft_lbs": self.spec.max_torque_ft_lbs if self.spec else 0,
                "max_rpm": self.spec.max_rpm if self.spec else 0,
                "plc_type": self.spec.plc_type if self.spec else "",
                "register_convention": self.spec.register_convention if self.spec else "",
            } if self.spec else None,
            "register_map": {
                name: {
                    "address": r.address, "data_type": r.data_type,
                    "ge_address": r.ge_address, "unit": r.unit,
                    "scale": r.scale, "description": r.description,
                }
                for name, r in self.register_map.registers.items()
            },
            "read_blocks": self.register_map.compute_read_blocks(),
        }


# ──────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────
class MachineRegistry:
    """Loads every profile in profiles/ and indexes by name + IP."""

    def __init__(self, fleet: FleetCatalog, records: dict[str, MachineRecord]) -> None:
        self.fleet = fleet
        self._by_name = records  # key: slug(ewon_name)
        self._by_ip = {r.plc_ip: r for r in records.values() if r.plc_ip}

    @classmethod
    def load(cls) -> "MachineRegistry":
        fleet = FleetCatalog.load()
        records: dict[str, MachineRecord] = {}
        loaded_profiles = 0
        for device in fleet.devices:
            rec = _build_record(device)
            records[_slug(device.ewon_name)] = rec
            if rec.register_map.source_profile:
                loaded_profiles += 1
        logger.info(
            f"MachineRegistry: {len(records)} machines, "
            f"{loaded_profiles} with custom register maps"
        )
        return cls(fleet, records)

    def get_for_rig(self, ewon_name: str) -> Optional[MachineRecord]:
        r = self._by_name.get(_slug(ewon_name))
        if r:
            return r
        # Partial match fallback
        s = _slug(ewon_name)
        for k, v in self._by_name.items():
            if s in k or k in s:
                return v
        return None

    def get_by_ip(self, ip: str) -> Optional[MachineRecord]:
        return self._by_ip.get(ip)

    def all_machines(self) -> list[MachineRecord]:
        return list(self._by_name.values())

    def all_with_register_maps(self) -> list[MachineRecord]:
        return [r for r in self._by_name.values()
                if r.register_map.registers]


# ──────────────────────────────────────────────────────────────────────
# Profile -> MachineRecord builder
# ──────────────────────────────────────────────────────────────────────
def _build_record(device) -> MachineRecord:
    spec = EQUIPMENT_CATALOG.get(device.equipment_type,
                                  EQUIPMENT_CATALOG["unknown"])
    rec = MachineRecord(
        ewon_name=device.ewon_name,
        equipment_type=device.equipment_type,
        customer=device.customer,
        description=device.description,
        firmware=device.firmware,
        status=device.status,
        profile_path=device.profile_path,
        spec=spec,
    )
    if device.profile_path and device.profile_path.exists():
        _apply_profile(rec, device.profile_path)
    else:
        # Use default HXI optimizer map
        rec.register_map = _build_map_from_dict(DEFAULT_HXI_OPTIMIZER_MAP,
                                                 source=None)
    return rec


def _apply_profile(rec: MachineRecord, path: Path) -> None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning(f"Profile load failed {path}: {e}")
        return

    # Connection details
    rec.plc_ip = str(data.get("plc_ip", "")) or None
    rec.plc_port = int(data.get("plc_port", 502))
    rec.unit_id = int(data.get("unit_id", 1))
    rec.word_swap = bool(data.get("word_swap", True))

    reg_map_data = data.get("reg_map") or {}
    if reg_map_data:
        rec.register_map = _build_map_from_dict(reg_map_data, source=path)
    else:
        # Profile exists but has no reg_map → default
        rec.register_map = _build_map_from_dict(DEFAULT_HXI_OPTIMIZER_MAP,
                                                 source=None)


def _build_map_from_dict(reg_data: dict, source: Optional[Path]) -> MachineRegisterMap:
    mm = MachineRegisterMap(source_profile=source)
    for name, info in reg_data.items():
        if not isinstance(info, dict):
            continue
        try:
            addr = int(info.get("address"))
        except (TypeError, ValueError):
            continue
        mm.registers[name] = RegisterEntry(
            name=name,
            address=addr,
            data_type=str(info.get("data_type", "FLOAT32")).upper(),
            unit=str(info.get("unit", "")),
            scale=float(info.get("scale", 1.0)),
            description=str(info.get("description", "")),
            source="profile" if source else "default",
        )
    return mm
