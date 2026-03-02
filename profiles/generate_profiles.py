#!/usr/bin/env python3
"""Generate individual YAML profile files from ewon_fleet.csv.

For equipment types with known register templates (HXI family),
the register map is populated automatically. Other types get an
empty reg_map to be filled by discovery scan.
"""
import csv
import re
from pathlib import Path

# HXI register template — confirmed on Precision Rig 709 HXI HT (2026-02-26)
# All HXI variants (hxi, hxi_ht, hxi_ss, hxi_relay) share the same PLC program.
HXI_REG_MAP = """\
reg_map:
  torque:
    address: 163
    data_type: FLOAT32
    unit: ft-lbs
    description: "HXI template — confirmed on Rig 709 HXI HT"
  turns:
    address: 165
    data_type: FLOAT32
    unit: turns
    description: "HXI template — confirmed"
  temperature:
    address: 167
    data_type: FLOAT32
    unit: degF
    description: "HXI template — confirmed"
  rpm:
    address: 169
    data_type: FLOAT32
    unit: RPM
    description: "HXI template — confirmed"
  hookload:
    address: 189
    data_type: FLOAT32
    unit: lbs
    scale: 0.001
    description: "HXI template — confirmed"
  encoder_counts:
    address: 2076
    data_type: INT32
    unit: counts
    description: "HXI template — confirmed"
  connection_state:
    address: 175
    data_type: INT16
    unit: enum
    description: "HXI template — likely"
  target_torque:
    address: 178
    data_type: INT16
    unit: ft-lbs
    description: "HXI template — likely"
  shoulder_torque:
    address: 180
    data_type: INT16
    unit: ft-lbs
    description: "HXI template — likely"
  connection_count:
    address: 185
    data_type: INT16
    unit: count
    description: "HXI template — likely\""""

HXI_FAMILY = {"hxi", "hxi_ht", "hxi_ss", "hxi_standard", "hxi_relay"}

# Files that should not be overwritten (hand-crafted with confirmed data)
SKIP_FILES = {
    "precision_rig_709_hxi_ht.yaml",  # has confirmed plc_ip + reg_map
    "shop_unit.yaml",                  # has full R6000 reg_map
}


def sanitize_filename(name):
    """Convert eWON name to a clean filename."""
    s = name.strip().lower()
    s = re.sub(r'[^a-z0-9]+', '_', s)
    s = s.strip('_')
    return s


def main():
    root = Path(__file__).parent
    csv_path = root / 'ewon_fleet.csv'

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Read {len(rows)} machines from {csv_path.name}")

    created = 0
    skipped = 0
    templated = 0
    for row in rows:
        ewon_name = row['ewon_name'].strip()
        if not ewon_name:
            continue

        filename = sanitize_filename(ewon_name) + '.yaml'

        # Don't overwrite hand-crafted profiles
        if filename in SKIP_FILES:
            print(f"  SKIP (hand-crafted): {filename}")
            skipped += 1
            continue

        filepath = root / filename

        status = row['status'].strip()
        description = row.get('description', '').strip()
        customer = row.get('customer', '').strip()
        equipment_type = row.get('equipment_type', '').strip()
        firmware = row.get('firmware', '').strip()

        lines = []
        lines.append(f"# {ewon_name} — eWON Fleet Profile")
        lines.append(f"# Auto-generated from ewon_fleet.csv")
        lines.append(f"name: {sanitize_filename(ewon_name)}")
        lines.append(f'ewon_name: "{ewon_name}"')
        lines.append(f"status: {status.lower()}")
        if customer:
            lines.append(f'customer: "{customer}"')
        if equipment_type:
            lines.append(f'equipment_type: {equipment_type}')
        else:
            lines.append('equipment_type: unknown')
        if description:
            lines.append(f'description: "{description}"')
        if firmware:
            lines.append(f'firmware: "{firmware}"')
        else:
            lines.append('firmware: ""')
        lines.append("")
        lines.append("# Modbus connection (fill in when discovered)")
        lines.append('plc_ip: ""')
        lines.append("plc_port: 502")
        lines.append("unit_id: 0")
        lines.append("word_swap: true")
        lines.append("")
        lines.append("# Equipment specs (fill in per rig)")
        lines.append("motor_displacement_cc: 0.0")
        lines.append("max_pressure_psi: 0.0")
        lines.append("encoder_cpr: 0")
        lines.append("torque_cell_capacity_ftlbs: 0.0")
        lines.append("")

        # Populate register map from template if equipment type is known
        if equipment_type in HXI_FAMILY:
            lines.append("# Register map — HXI family template (confirmed on Rig 709)")
            lines.append(HXI_REG_MAP)
            templated += 1
        else:
            lines.append("# Register map — populate after discovery scan")
            lines.append("reg_map: {}")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')

        created += 1

    print(f"Created {created} YAML profile files in {root}")
    print(f"  With HXI register template: {templated}")
    print(f"  Skipped (hand-crafted): {skipped}")


if __name__ == '__main__':
    main()
