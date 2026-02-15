#!/usr/bin/env python3
"""
TopDrive AI - Synthetic Dataset Generator
==========================================
Generates diverse training data using physics-based simulation.

Supports multiple machine types (top drive, iron roughneck, power tong,
bucking unit) and connection categories (API 8-Round LTC/STC, BTC,
premium shouldered, drill pipe).

Usage:
  # Generate 100 scenarios, save to output/
  python -m generate_dataset --count 100 --output ./data/synthetic

  # Generate with specific pipe type
  python -m generate_dataset --count 50 --pipe 7in_23lb_N80_LTC

  # Filter by machine type
  python -m generate_dataset --count 50 --machine-type top_drive

  # Filter by connection type
  python -m generate_dataset --count 50 --connection-type LTC

  # Real-time mode with Modbus server (for pipeline testing)
  python -m generate_dataset --realtime --modbus-port 5020

  # Single scenario for debugging
  python -m generate_dataset --single normal_casing_ltc --pipe 7in_23lb_N80_LTC

  # Phase 1 production dataset (Parquet, rebalanced 50/50 normal/fault)
  python generate_dataset.py --count 5000 --output ./data/synthetic_v2 \
    --output-format parquet --class-balance rebalanced --seed 42

Output structure:
  data/synthetic/
  ├── sensor/               # Noisy sensor data (for AI training)
  │   ├── scenario_0000_normal_casing_ltc.csv
  │   ├── scenario_0001_cross_thread.csv
  │   └── ...
  ├── truth/                # Ground truth (for validation)
  │   ├── scenario_0000_normal_casing_ltc.csv
  │   └── ...
  ├── events/               # State transitions and faults
  │   ├── scenario_0000_normal_casing_ltc.csv
  │   └── ...
  ├── manifest.csv          # Index of all scenarios with metadata
  └── stats.txt             # Dataset statistics
"""
import argparse
import json
import csv
import sys
import time
import logging
from pathlib import Path
from typing import Optional, Dict, List

import numpy as np

from config import SimConfig, MachineType, ALL_CONNECTIONS, PIPE_CATALOG, PREMIUM_CATALOG, DRILL_PIPE_CATALOG
from scenario import ScenarioGenerator, ScenarioType
from runner import SimulationRunner

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def _parse_machine_types(machine_type_str: Optional[str]) -> Optional[list]:
    """Parse machine type argument into list of MachineType enums."""
    if not machine_type_str:
        return None
    try:
        return [MachineType(machine_type_str)]
    except ValueError:
        valid = [m.value for m in MachineType]
        print(f"Unknown machine type: {machine_type_str}")
        print(f"Available: {valid}")
        sys.exit(1)


# Rebalanced class distribution for ML training (Section 2.1)
# 50/50 normal/fault split with inverse frequency weighting for rare faults.
REBALANCED_DISTRIBUTION: Dict[str, float] = {
    'normal_casing_ltc': 0.15,
    'normal_casing_btc': 0.10,
    'normal_casing_premium': 0.08,
    'normal_drill_pipe': 0.10,
    'normal_tubing': 0.04,
    'full_cycle': 0.03,
    'cross_thread': 0.065,
    'galling': 0.065,
    'stripped_thread': 0.055,
    'over_torque': 0.055,
    'under_torque': 0.055,
    'wrong_compound': 0.050,
    'misaligned_stabbing': 0.055,
    'stall': 0.050,
    'stick_slip': 0.015,
    'multi_connection': 0.010,
    'washout': 0.005,
    'connection_jump': 0.005,
}

# Fault class mapping (scenario_type -> numeric class for ML)
# 9 classes: 0=normal, 1=cross_thread, 2=galling, 3=stripped_thread,
#            4=over_torque, 5=under_torque, 6=wrong_compound,
#            7=misaligned_stab, 8=stall
# EVERY ScenarioType must appear here to avoid mislabeling faults as normal.
FAULT_CLASS_MAP: Dict[str, int] = {
    # Normal variants (class 0)
    'normal_casing_ltc': 0,
    'normal_casing_btc': 0,
    'normal_casing_premium': 0,
    'normal_drill_pipe': 0,
    'normal_tubing': 0,
    'normal_breakout': 0,
    'full_cycle': 0,
    'multi_connection': 0,
    'cold_start': 0,
    'hot_environment': 0,
    # Fault classes (1-8)
    'cross_thread': 1,
    'connection_jump': 1,
    'stick_slip': 1,
    'staged_fault': 1,
    'galling': 2,
    'stripped_thread': 3,
    'over_torque': 4,
    'washout': 4,
    'under_torque': 5,
    'wrong_compound': 6,
    'misaligned_stabbing': 7,
    'stall': 8,
}


def _save_parquet(data: List[dict], filepath: Path):
    """Save list of dicts as Parquet file using pyarrow."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        # Fallback to CSV if pyarrow not installed
        logger.warning("pyarrow not installed, falling back to CSV")
        filepath = filepath.with_suffix('.csv')
        if data:
            with open(filepath, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)
        return

    import pandas as pd
    df = pd.DataFrame(data)
    df.to_parquet(filepath, index=False, engine='pyarrow')


def _filter_pipes_by_connection(connection_type: Optional[str]) -> Optional[list]:
    """Filter pipe catalog by connection type (LTC, BTC, STC, PREMIUM, DRILL_PIPE)."""
    if not connection_type:
        return None
    ct = connection_type.upper()
    matching = [
        name for name, spec in ALL_CONNECTIONS.items()
        if spec.connection_type.upper() == ct
    ]
    if not matching:
        all_types = sorted(set(s.connection_type for s in ALL_CONNECTIONS.values()))
        print(f"No pipes found for connection type: {connection_type}")
        print(f"Available types: {all_types}")
        sys.exit(1)
    return matching


def generate_batch(args):
    """Generate a batch of synthetic training data."""
    output_dir = Path(args.output)
    sensor_dir = output_dir / 'sensor'
    truth_dir = output_dir / 'truth'
    events_dir = output_dir / 'events'
    use_parquet = getattr(args, 'output_format', 'csv') == 'parquet'
    file_ext = '.parquet' if use_parquet else '.csv'

    for d in [sensor_dir, truth_dir, events_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Initialize
    gen = ScenarioGenerator(seed=args.seed)

    # Apply rebalanced distribution if requested
    class_balance = getattr(args, 'class_balance', 'default')
    if class_balance == 'rebalanced':
        gen.weights = {}
        for stype_str, weight in REBALANCED_DISTRIBUTION.items():
            try:
                gen.weights[ScenarioType(stype_str)] = weight
            except ValueError:
                pass
        logger.info("Using REBALANCED class distribution (50/50 normal/fault)")

    runner = SimulationRunner(csv_output_rate_hz=args.output_rate)

    # Build pipe filter
    pipe_filter = None
    if args.pipe:
        pipe_filter = [args.pipe]
    elif args.connection_type:
        pipe_filter = _filter_pipes_by_connection(args.connection_type)

    # Build machine filter
    machine_filter = _parse_machine_types(args.machine_type)

    scenarios = gen.generate_batch(
        args.count,
        pipe_names=pipe_filter,
        machine_types=machine_filter,
    )

    logger.info(f"Generating {len(scenarios)} scenarios...")
    logger.info(gen.get_distribution_summary())

    # Manifest tracking
    manifest = []
    total_samples = 0
    fault_count = 0
    start_time = time.monotonic()

    for i, scenario in enumerate(scenarios):
        base_name = f"scenario_{i:04d}"

        logger.info(f"[{i+1}/{len(scenarios)}] {scenario.label}")

        try:
            result = runner.run(scenario)

            # Save data (Parquet or CSV)
            if use_parquet:
                _save_parquet(result.sensor_data, sensor_dir / f"{base_name}{file_ext}")
                _save_parquet(result.ground_truth, truth_dir / f"{base_name}{file_ext}")
                if result.events:
                    _save_parquet(result.events, events_dir / f"{base_name}{file_ext}")
            else:
                runner.save_csv(result, sensor_dir / f"{base_name}{file_ext}", data_type='sensor')
                runner.save_csv(result, truth_dir / f"{base_name}{file_ext}", data_type='truth')
                if result.events:
                    runner.save_events(result, events_dir / f"{base_name}{file_ext}")

            # Manifest entry (Section 2.6 schema)
            fault_class = FAULT_CLASS_MAP.get(scenario.scenario_type.value, 0)
            manifest.append({
                'scenario_id': i,
                'filename': f"{base_name}{file_ext}",
                'scenario_type': scenario.scenario_type.value,
                'fault_class': fault_class,
                'machine_type': scenario.machine_type.value,
                'pipe_name': scenario.pipe.name,
                'connection_type': scenario.pipe.connection_type,
                'pipe_od_in': scenario.pipe.od_inches,
                'target_torque_ftlbs': scenario.target_torque or scenario.pipe.optimum_torque_ftlbs,
                'expected_turns': scenario.pipe.turns_to_shoulder,
                'seed': scenario.seed,
                'num_samples': result.metadata['total_samples'],
                'duration_s': round(result.metadata['duration_s'], 2),
                'peak_torque_ftlbs': round(result.metadata['peak_torque_ftlbs'], 1),
                'peak_rpm': round(result.metadata['peak_rpm'], 1),
                'shoulder_torque_ftlbs': round(result.metadata.get('shoulder_torque_ftlbs', 0), 1),
                'has_fault': result.metadata['has_fault'],
                'fault_code': result.metadata.get('fault_code', 0),
                'label': scenario.label,
            })

            total_samples += result.metadata['total_samples']
            if result.metadata['has_fault']:
                fault_count += 1

        except Exception as e:
            logger.error(f"  FAILED: {e}", exc_info=True)
            manifest.append({
                'scenario_id': i, 'filename': f"{base_name}{file_ext}",
                'scenario_type': scenario.scenario_type.value,
                'fault_class': FAULT_CLASS_MAP.get(scenario.scenario_type.value, 0),
                'machine_type': scenario.machine_type.value,
                'pipe_name': scenario.pipe.name,
                'connection_type': scenario.pipe.connection_type,
                'pipe_od_in': scenario.pipe.od_inches,
                'target_torque_ftlbs': 0, 'expected_turns': 0,
                'seed': scenario.seed,
                'num_samples': 0, 'duration_s': 0,
                'peak_torque_ftlbs': 0, 'peak_rpm': 0,
                'shoulder_torque_ftlbs': 0,
                'has_fault': False, 'fault_code': 0,
                'label': f"FAILED: {e}",
            })

    elapsed = time.monotonic() - start_time

    # Save manifest
    if manifest:
        if use_parquet:
            _save_parquet(manifest, output_dir / 'manifest.parquet')
        # Always save CSV manifest too (lightweight, human-readable)
        manifest_path = output_dir / 'manifest.csv'
        with open(manifest_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=manifest[0].keys())
            writer.writeheader()
            writer.writerows(manifest)

    # Save stats
    stats = _compute_stats(manifest, elapsed, total_samples, fault_count, scenarios)
    stats_path = output_dir / 'stats.txt'
    with open(stats_path, 'w') as f:
        f.write(stats)

    # Save generation config for reproducibility
    gen_config = {
        'count': args.count,
        'seed': args.seed,
        'output_format': 'parquet' if use_parquet else 'csv',
        'class_balance': class_balance,
        'output_rate_hz': args.output_rate,
        'pipe_filter': args.pipe,
        'machine_type': args.machine_type,
        'connection_type': args.connection_type,
    }
    with open(output_dir / 'config.json', 'w') as f:
        json.dump(gen_config, f, indent=2)

    print(f"\n{'='*60}")
    print(stats)
    print(f"Output: {output_dir.absolute()}")


def generate_single(args):
    """Generate a single scenario (for debugging)."""
    try:
        stype = ScenarioType(args.single)
    except ValueError:
        print(f"Unknown scenario type: {args.single}")
        print(f"Available: {[s.value for s in ScenarioType]}")
        sys.exit(1)

    gen = ScenarioGenerator(seed=args.seed)

    machine_type = None
    if args.machine_type:
        machine_type = _parse_machine_types(args.machine_type)
        machine_type = machine_type[0] if machine_type else None

    scenario = gen.generate_one(
        stype,
        pipe_name=args.pipe or "7in_23lb_N80_LTC",
        machine_type=machine_type,
    )

    runner = SimulationRunner(
        realtime=args.realtime,
        enable_modbus=args.realtime,
        modbus_port=args.modbus_port,
        csv_output_rate_hz=args.output_rate,
    )

    logger.info(f"Running: {scenario.label}")
    logger.info(f"Machine: {scenario.machine_type.value}")
    if args.realtime:
        logger.info(f"Modbus server on port {args.modbus_port}")

    result = runner.run(scenario)

    # Print summary
    print(f"\n{'='*60}")
    print(f"Scenario:       {scenario.label}")
    print(f"Machine:        {scenario.machine_type.value}")
    print(f"Connection:     {scenario.pipe.connection_type}")
    print(f"Pipe:           {scenario.pipe.name}")
    print(f"Samples:        {result.metadata['total_samples']}")
    print(f"Duration:       {result.metadata['duration_s']:.2f}s")
    print(f"Peak Torque:    {result.metadata['peak_torque_ftlbs']:.0f} ft-lbs")
    print(f"Peak Pressure:  {result.metadata['peak_pressure_psi']:.0f} PSI")
    print(f"Peak RPM:       {result.metadata['peak_rpm']:.1f}")
    print(f"Shoulder Torque:{result.metadata.get('shoulder_torque_ftlbs', 0):.0f} ft-lbs")
    print(f"Fault Code:     0x{result.metadata.get('fault_code', 0):04X}")
    print(f"Events:         {result.metadata['num_events']}")

    if result.events:
        print(f"\nEvent Log:")
        for e in result.events:
            print(f"  t={e['time']:.3f}s  {e['event']}: {e.get('from','')} -> {e.get('to','')}")

    # Save if output specified
    if args.output:
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        runner.save_csv(result, output_dir / 'sensor_data.csv', data_type='sensor')
        runner.save_csv(result, output_dir / 'ground_truth.csv', data_type='truth')
        runner.save_events(result, output_dir / 'events.csv')
        print(f"\nSaved to: {output_dir.absolute()}")


def realtime_mode(args):
    """Run continuous scenarios with live Modbus server."""
    gen = ScenarioGenerator(seed=args.seed)

    machine_filter = _parse_machine_types(args.machine_type)

    runner = SimulationRunner(
        realtime=True,
        enable_modbus=True,
        modbus_port=args.modbus_port,
        csv_output_rate_hz=args.output_rate,
    )

    logger.info(f"Real-time mode - Modbus server on port {args.modbus_port}")
    logger.info(f"Connect your data pipeline to localhost:{args.modbus_port}")
    logger.info("Press Ctrl+C to stop")

    try:
        cycle = 0
        while True:
            scenario = gen.generate_batch(1, machine_types=machine_filter)[0]
            cycle += 1
            logger.info(f"[Cycle {cycle}] {scenario.label} ({scenario.machine_type.value})")

            result = runner.run(scenario)
            logger.info(f"  Complete: {result.metadata['total_samples']} samples, "
                       f"peak {result.metadata['peak_torque_ftlbs']:.0f} ft-lbs, "
                       f"fault=0x{result.metadata.get('fault_code', 0):04X}")

            if args.output:
                output_dir = Path(args.output)
                runner.save_csv(result, output_dir / f'cycle_{cycle:04d}_sensor.csv')

            # Pause between connections (simulates field timing)
            time.sleep(2.0)

    except KeyboardInterrupt:
        logger.info(f"\nStopped after {cycle} cycles")


def _compute_stats(manifest, elapsed, total_samples, fault_count, scenarios):
    """Compute and format dataset statistics."""
    from collections import Counter

    type_counts = Counter(m['scenario_type'] for m in manifest)
    pipe_counts = Counter(m['pipe_name'] for m in manifest)
    machine_counts = Counter(m.get('machine_type', 'unknown') for m in manifest)
    conn_type_counts = Counter(m.get('connection_type', 'unknown') for m in manifest)
    successful = sum(1 for m in manifest if m.get('num_samples', 0) and int(m.get('num_samples', 0)) > 0)

    lines = [
        f"TopDrive AI Synthetic Dataset Statistics",
        f"{'='*50}",
        f"Total scenarios:    {len(manifest)}",
        f"Successful:         {successful}",
        f"Total samples:      {total_samples:,}",
        f"Total duration:     {sum(float(m.get('duration_s', 0)) for m in manifest):.0f}s simulated",
        f"Generation time:    {elapsed:.1f}s wall clock",
        f"Throughput:         {total_samples/max(elapsed,1):.0f} samples/sec",
        f"Fault scenarios:    {fault_count} ({fault_count/max(len(manifest),1):.0%})",
        f"",
        f"Scenario Distribution:",
    ]
    for stype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {stype:30s}  {count:4d}  ({count/len(manifest):.0%})")

    lines.append(f"\nMachine Type Distribution:")
    for machine, count in sorted(machine_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {machine:30s}  {count:4d}  ({count/len(manifest):.0%})")

    lines.append(f"\nConnection Type Distribution:")
    for ct, count in sorted(conn_type_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {ct:30s}  {count:4d}  ({count/len(manifest):.0%})")

    lines.append(f"\nPipe Distribution:")
    for pipe, count in sorted(pipe_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {pipe:40s}  {count:4d}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='TopDrive AI Synthetic Dataset Generator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --count 100 --output ./data/synthetic
  %(prog)s --single normal_casing_ltc --pipe 7in_23lb_N80_LTC
  %(prog)s --count 50 --machine-type top_drive
  %(prog)s --count 50 --connection-type LTC
  %(prog)s --realtime --modbus-port 5020
        """
    )

    parser.add_argument('--count', type=int, default=10,
                        help='Number of scenarios to generate (default: 10)')
    parser.add_argument('--output', '-o', type=str, default='./data/synthetic',
                        help='Output directory')
    parser.add_argument('--pipe', type=str, default=None,
                        help='Restrict to specific pipe type (by name)')
    parser.add_argument('--machine-type', type=str, default=None,
                        choices=[m.value for m in MachineType],
                        help='Filter by machine type')
    parser.add_argument('--connection-type', type=str, default=None,
                        help='Filter by connection type (LTC, BTC, STC, PREMIUM, DRILL_PIPE)')
    parser.add_argument('--output-rate', type=float, default=100.0,
                        help='CSV output rate in Hz (default: 100)')
    parser.add_argument('--seed', type=int, default=42,
                        help='Random seed for reproducibility')
    parser.add_argument('--single', type=str, default=None,
                        help='Run single scenario type')
    parser.add_argument('--realtime', action='store_true',
                        help='Run in real-time with Modbus server')
    parser.add_argument('--modbus-port', type=int, default=5020,
                        help='Modbus TCP port (default: 5020)')
    parser.add_argument('--output-format', type=str, default='csv',
                        choices=['csv', 'parquet'],
                        help='Output file format (default: csv)')
    parser.add_argument('--class-balance', type=str, default='default',
                        choices=['default', 'rebalanced'],
                        help='Class distribution: default (field 65/35) or rebalanced (50/50 normal/fault)')

    args = parser.parse_args()

    if args.realtime:
        realtime_mode(args)
    elif args.single:
        generate_single(args)
    else:
        generate_batch(args)


if __name__ == '__main__':
    main()
