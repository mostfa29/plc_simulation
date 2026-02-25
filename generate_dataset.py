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
from runner import SimulationRunner, SimulationResult

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


def compute_ground_truth_label(result: SimulationResult, scenario_type: str,
                               pipe_min_torque: float = 0.0) -> dict:
    """Determine label from simulation events, not intent.

    Like a doctor diagnosing from symptoms, not from what disease the
    patient was exposed to. A "cross_thread" scenario where the trigger
    wasn't reached is actually a NORMAL connection.

    Returns dict with:
        ground_truth_label: str - the actual label based on what happened
        intended_label: str - the original scenario_type
        label_match: bool - whether ground_truth == intended
        fault_onset_time: float - when fault first detected (seconds)
        fault_onset_turns: float - turns at fault onset
        final_torque_ftlbs: float - torque at end of connection
        total_turns: float - total turns accumulated
        connection_duration_s: float - time from start to end
        torque_at_shoulder: float - torque when shoulder detected
        torque_gradient_ftlbs_per_turn: float - slope in power-tight zone

    Priority order for fault classification:
        1. over_torque event → "over_torque"
        2. cross_thread event → "cross_thread"
        3. galling event → "galling"
        4. stripped_thread event → "stripped_thread"
        5. misaligned_stab event → "misaligned_stab"
        6. wrong_compound event → "wrong_compound"
        7. stall (RPM < 1 for > 2s while valve > 50%) → "stall"
        8. under_torque (final torque < min_torque) → "under_torque"
        9. normal connection → "normal"
    """
    events = result.events or []
    metadata = result.metadata
    ground_truth = result.ground_truth

    # Collect fault events by type
    fault_events = {}
    for e in events:
        etype = e.get('event', '')
        if etype in ('over_torque', 'cross_thread', 'galling', 'stripped_thread',
                     'misaligned_stab', 'wrong_compound', 'stick_slip',
                     'emergency_stop'):
            if etype not in fault_events:
                fault_events[etype] = e

    # Extract timing info
    fault_onset_time = 0.0
    fault_onset_turns = 0.0
    final_torque = 0.0
    total_turns = 0.0
    connection_duration = metadata.get('duration_s', 0.0)
    torque_at_shoulder = metadata.get('shoulder_torque_ftlbs', 0.0)
    torque_gradient = metadata.get('max_slope_dT_dN', 0.0)

    if ground_truth:
        final_torque = ground_truth[-1].get('torque_ftlbs', 0.0)
        total_turns = ground_truth[-1].get('turns', 0.0)

    # Determine ground truth label in priority order
    gt_label = 'normal'

    if 'over_torque' in fault_events:
        gt_label = 'over_torque'
        e = fault_events['over_torque']
        fault_onset_time = e.get('time', 0.0)
        fault_onset_turns = e.get('turns', 0.0)
    elif 'cross_thread' in fault_events:
        gt_label = 'cross_thread'
        e = fault_events['cross_thread']
        fault_onset_time = e.get('time', 0.0)
        fault_onset_turns = e.get('turns', 0.0)
    elif 'galling' in fault_events:
        gt_label = 'galling'
        e = fault_events['galling']
        fault_onset_time = e.get('time', 0.0)
        fault_onset_turns = e.get('turns', 0.0)
    elif 'stripped_thread' in fault_events:
        gt_label = 'stripped_thread'
        e = fault_events['stripped_thread']
        fault_onset_time = e.get('time', 0.0)
        fault_onset_turns = e.get('turns', 0.0)
    elif 'misaligned_stab' in fault_events:
        gt_label = 'misaligned_stab'
        e = fault_events['misaligned_stab']
        fault_onset_time = e.get('time', 0.0)
        fault_onset_turns = e.get('turns', 0.0)
    elif 'wrong_compound' in fault_events:
        gt_label = 'wrong_compound'
        e = fault_events['wrong_compound']
        fault_onset_time = e.get('time', 0.0)
        fault_onset_turns = e.get('turns', 0.0)
    elif 'emergency_stop' in fault_events:
        reason = fault_events['emergency_stop'].get('reason', '')
        if reason == 'over_torque':
            gt_label = 'over_torque'
        else:
            gt_label = 'stall'
        fault_onset_time = fault_events['emergency_stop'].get('time', 0.0)
    else:
        # Check for stall: look for sustained zero RPM in ground truth
        if ground_truth and len(ground_truth) > 20:
            stall_count = 0
            for row in ground_truth:
                if row.get('rpm', 1.0) < 1.0 and row.get('valve_command_pct', 0) > 50:
                    stall_count += 1
                else:
                    stall_count = 0
            # Stall if RPM<1 for >2s worth of samples (200 at 100Hz)
            if stall_count > 200:
                gt_label = 'stall'

        # Check for under_torque: final torque below min threshold
        if gt_label == 'normal' and pipe_min_torque > 0 and final_torque > 0:
            if final_torque < pipe_min_torque:
                gt_label = 'under_torque'

    # Map intended scenario_type to comparable label
    intended_class = FAULT_CLASS_MAP.get(scenario_type, 0)
    gt_class = GROUND_TRUTH_CLASS_MAP.get(gt_label, 0)
    label_match = (intended_class == gt_class)

    return {
        'ground_truth_label': gt_label,
        'ground_truth_class': gt_class,
        'intended_label': scenario_type,
        'intended_class': intended_class,
        'label_match': label_match,
        'fault_onset_time': round(fault_onset_time, 4),
        'fault_onset_turns': round(fault_onset_turns, 4),
        'final_torque_ftlbs': round(final_torque, 1),
        'total_turns': round(total_turns, 4),
        'connection_duration_s': round(connection_duration, 2),
        'torque_at_shoulder': round(torque_at_shoulder, 1),
        'torque_gradient_ftlbs_per_turn': round(torque_gradient, 1),
    }


# Ground truth label -> numeric class (matches FAULT_CLASS_MAP numbering)
GROUND_TRUTH_CLASS_MAP: Dict[str, int] = {
    'normal': 0,
    'cross_thread': 1,
    'galling': 2,
    'stripped_thread': 3,
    'over_torque': 4,
    'under_torque': 5,
    'wrong_compound': 6,
    'misaligned_stab': 7,
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

            # Compute ground truth label from what actually happened
            gt_info = compute_ground_truth_label(
                result,
                scenario.scenario_type.value,
                pipe_min_torque=scenario.pipe.min_torque_ftlbs,
            )

            # Manifest entry with ground truth labels
            manifest.append({
                'scenario_id': i,
                'filename': f"{base_name}{file_ext}",
                'ground_truth_label': gt_info['ground_truth_label'],
                'ground_truth_class': gt_info['ground_truth_class'],
                'intended_label': gt_info['intended_label'],
                'intended_class': gt_info['intended_class'],
                'label_match': gt_info['label_match'],
                'fault_onset_time': gt_info['fault_onset_time'],
                'fault_onset_turns': gt_info['fault_onset_turns'],
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
                'final_torque_ftlbs': gt_info['final_torque_ftlbs'],
                'total_turns': gt_info['total_turns'],
                'connection_duration_s': gt_info['connection_duration_s'],
                'torque_at_shoulder': gt_info['torque_at_shoulder'],
                'torque_gradient_ftlbs_per_turn': gt_info['torque_gradient_ftlbs_per_turn'],
                'shoulder_torque_ftlbs': round(result.metadata.get('shoulder_torque_ftlbs', 0), 1),
                'has_fault': result.metadata['has_fault'],
                'fault_code': result.metadata.get('fault_code', 0),
            })

            total_samples += result.metadata['total_samples']
            if result.metadata['has_fault']:
                fault_count += 1

        except Exception as e:
            logger.error(f"  FAILED: {e}", exc_info=True)
            manifest.append({
                'scenario_id': i, 'filename': f"{base_name}{file_ext}",
                'ground_truth_label': f"FAILED",
                'ground_truth_class': -1,
                'intended_label': scenario.scenario_type.value,
                'intended_class': FAULT_CLASS_MAP.get(scenario.scenario_type.value, 0),
                'label_match': False,
                'fault_onset_time': 0, 'fault_onset_turns': 0,
                'machine_type': scenario.machine_type.value,
                'pipe_name': scenario.pipe.name,
                'connection_type': scenario.pipe.connection_type,
                'pipe_od_in': scenario.pipe.od_inches,
                'target_torque_ftlbs': 0, 'expected_turns': 0,
                'seed': scenario.seed,
                'num_samples': 0, 'duration_s': 0,
                'peak_torque_ftlbs': 0, 'peak_rpm': 0,
                'final_torque_ftlbs': 0, 'total_turns': 0,
                'connection_duration_s': 0,
                'torque_at_shoulder': 0,
                'torque_gradient_ftlbs_per_turn': 0,
                'shoulder_torque_ftlbs': 0,
                'has_fault': False, 'fault_code': 0,
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

    type_counts = Counter(m.get('intended_label', m.get('scenario_type', 'unknown')) for m in manifest)
    gt_counts = Counter(m.get('ground_truth_label', 'unknown') for m in manifest)
    pipe_counts = Counter(m['pipe_name'] for m in manifest)
    machine_counts = Counter(m.get('machine_type', 'unknown') for m in manifest)
    conn_type_counts = Counter(m.get('connection_type', 'unknown') for m in manifest)
    successful = sum(1 for m in manifest if m.get('num_samples', 0) and int(m.get('num_samples', 0)) > 0)

    # Label match stats
    total_with_labels = sum(1 for m in manifest if m.get('ground_truth_label', '') != 'FAILED')
    match_count = sum(1 for m in manifest if m.get('label_match', False))
    match_rate = match_count / max(total_with_labels, 1)

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
        f"Label Match Rate:   {match_count}/{total_with_labels} ({match_rate:.0%})",
        f"",
        f"Ground Truth Label Distribution (USE THIS FOR TRAINING):",
    ]
    for gt_label, count in sorted(gt_counts.items(), key=lambda x: -x[1]):
        gt_class = GROUND_TRUTH_CLASS_MAP.get(gt_label, -1)
        lines.append(f"  [{gt_class:2d}] {gt_label:20s}  {count:4d}  ({count/len(manifest):.1%})")

    lines.append(f"\nIntended Scenario Distribution (for reference):")
    for stype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {stype:30s}  {count:4d}  ({count/len(manifest):.0%})")

    # Show mismatch details
    mismatches = [m for m in manifest if not m.get('label_match', True) and m.get('ground_truth_label', '') != 'FAILED']
    if mismatches:
        lines.append(f"\nLabel Mismatches ({len(mismatches)} total):")
        mismatch_types = Counter(
            f"{m['intended_label']} -> {m['ground_truth_label']}" for m in mismatches
        )
        for desc, count in sorted(mismatch_types.items(), key=lambda x: -x[1]):
            lines.append(f"  {desc:50s}  {count:4d}")

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


def verify_labels(args):
    """Generate a batch and report ground truth vs intended label distribution.

    This is the first thing to run before training to verify the simulator
    produces the expected fault distribution.
    """
    from collections import Counter

    count = args.count or 1000
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

    runner = SimulationRunner(csv_output_rate_hz=args.output_rate)

    scenarios = gen.generate_batch(count)
    logger.info(f"Verifying labels on {len(scenarios)} scenarios...")

    intended_counts = Counter()
    gt_counts = Counter()
    match_count = 0
    mismatch_details = Counter()
    total = 0
    failed = 0

    for i, scenario in enumerate(scenarios):
        if (i + 1) % 50 == 0:
            logger.info(f"  [{i+1}/{len(scenarios)}]...")

        try:
            result = runner.run(scenario)
            gt_info = compute_ground_truth_label(
                result,
                scenario.scenario_type.value,
                pipe_min_torque=scenario.pipe.min_torque_ftlbs,
            )

            intended = gt_info['intended_label']
            gt = gt_info['ground_truth_label']
            intended_counts[intended] += 1
            gt_counts[gt] += 1
            total += 1

            if gt_info['label_match']:
                match_count += 1
            else:
                mismatch_details[f"{intended} -> {gt}"] += 1

        except Exception as e:
            failed += 1
            logger.error(f"  [{i+1}] FAILED: {e}")

    # Report
    print(f"\n{'='*70}")
    print(f"  LABEL VERIFICATION REPORT")
    print(f"{'='*70}")
    print(f"  Total scenarios:  {total}")
    print(f"  Failed:           {failed}")
    print(f"  Label match rate: {match_count}/{total} ({match_count/max(total,1):.1%})")
    print()

    print(f"  Ground Truth Distribution (what the AI will learn):")
    for label, cnt in sorted(gt_counts.items(), key=lambda x: -x[1]):
        gt_class = GROUND_TRUTH_CLASS_MAP.get(label, -1)
        pct = cnt / total * 100
        bar = '#' * int(pct)
        warn = ""
        if cnt < 50:
            warn = " *** WARNING: <50 samples ***"
        print(f"    [{gt_class:2d}] {label:20s}  {cnt:4d}  ({pct:5.1f}%)  {bar}{warn}")

    print()
    print(f"  Intended Distribution (what we requested):")
    for label, cnt in sorted(intended_counts.items(), key=lambda x: -x[1]):
        pct = cnt / total * 100
        print(f"    {label:30s}  {cnt:4d}  ({pct:5.1f}%)")

    if mismatch_details:
        print()
        print(f"  Label Mismatches (intended -> actual):")
        for desc, cnt in sorted(mismatch_details.items(), key=lambda x: -x[1]):
            print(f"    {desc:50s}  {cnt:4d}")
        print()
        print(f"  NOTE: Mismatches mean the fault trigger condition wasn't reached.")
        print(f"  These scenarios are correctly labeled as 'normal' by ground truth.")

    # Warnings
    warnings = []
    for label, cnt in gt_counts.items():
        if cnt < 50:
            warnings.append(f"  - {label}: only {cnt} samples (need >=50)")
    max_class = max(gt_counts.values()) if gt_counts else 0
    min_class = min(gt_counts.values()) if gt_counts else 0
    if max_class > 3 * min_class and min_class > 0:
        warnings.append(f"  - Severe imbalance: max={max_class}, min={min_class} (>{3}x ratio)")
    match_rate = match_count / max(total, 1)
    if match_rate < 0.80:
        warnings.append(f"  - Label match rate {match_rate:.0%} < 80% — too many faults not triggering")

    if warnings:
        print(f"\n  WARNINGS:")
        for w in warnings:
            print(w)
    else:
        print(f"\n  All checks passed.")

    print(f"{'='*70}")


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
    parser.add_argument('--verify-labels', action='store_true',
                        help='Generate batch and report ground-truth vs intended label distribution')

    args = parser.parse_args()

    if args.verify_labels:
        verify_labels(args)
    elif args.realtime:
        realtime_mode(args)
    elif args.single:
        generate_single(args)
    else:
        generate_batch(args)


if __name__ == '__main__':
    main()
