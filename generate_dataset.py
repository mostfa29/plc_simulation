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
from typing import Optional

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

    for d in [sensor_dir, truth_dir, events_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Initialize
    gen = ScenarioGenerator(seed=args.seed)
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
        base_name = f"scenario_{i:04d}_{scenario.scenario_type.value}"

        logger.info(f"[{i+1}/{len(scenarios)}] {scenario.label}")

        try:
            result = runner.run(scenario)

            # Save data
            runner.save_csv(result, sensor_dir / f"{base_name}.csv", data_type='sensor')
            runner.save_csv(result, truth_dir / f"{base_name}.csv", data_type='truth')
            if result.events:
                runner.save_events(result, events_dir / f"{base_name}.csv")

            # Update manifest
            manifest.append({
                'index': i,
                'filename': f"{base_name}.csv",
                'scenario_type': scenario.scenario_type.value,
                'machine_type': scenario.machine_type.value,
                'pipe': scenario.pipe.name,
                'connection_type': scenario.pipe.connection_type,
                'seed': scenario.seed,
                'samples': result.metadata['total_samples'],
                'duration_s': f"{result.metadata['duration_s']:.2f}",
                'peak_torque': f"{result.metadata['peak_torque_ftlbs']:.0f}",
                'peak_pressure': f"{result.metadata['peak_pressure_psi']:.0f}",
                'peak_rpm': f"{result.metadata['peak_rpm']:.1f}",
                'shoulder_torque': f"{result.metadata.get('shoulder_torque_ftlbs', 0):.0f}",
                'fault_code': result.metadata.get('fault_code', 0),
                'has_fault': result.metadata['has_fault'],
                'label': scenario.label,
            })

            total_samples += result.metadata['total_samples']
            if result.metadata['has_fault']:
                fault_count += 1

        except Exception as e:
            logger.error(f"  FAILED: {e}", exc_info=True)
            manifest.append({
                'index': i, 'filename': f"{base_name}.csv",
                'scenario_type': scenario.scenario_type.value,
                'machine_type': scenario.machine_type.value,
                'pipe': scenario.pipe.name,
                'connection_type': scenario.pipe.connection_type,
                'seed': scenario.seed,
                'samples': 0, 'duration_s': 0, 'has_fault': False,
                'label': f"FAILED: {e}",
            })

    elapsed = time.monotonic() - start_time

    # Save manifest
    if manifest:
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
    pipe_counts = Counter(m['pipe'] for m in manifest)
    machine_counts = Counter(m.get('machine_type', 'unknown') for m in manifest)
    conn_type_counts = Counter(m.get('connection_type', 'unknown') for m in manifest)
    successful = sum(1 for m in manifest if m.get('samples', 0) and int(m.get('samples', 0)) > 0)

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

    args = parser.parse_args()

    if args.realtime:
        realtime_mode(args)
    elif args.single:
        generate_single(args)
    else:
        generate_batch(args)


if __name__ == '__main__':
    main()
