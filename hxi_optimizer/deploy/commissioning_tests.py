"""Phase A commissioning tests — per MASTER_CONTEXT §8.3.

Run BEFORE any Phase B advisory or Phase C writes. Uses spare register
%R06630 (do NOT use the live %R06603/%R06604 swash-bound registers).

Run with:
    python -m hxi_optimizer.deploy.commissioning_tests
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import struct
import time

from pymodbus.client import AsyncModbusTcpClient
from pymodbus import FramerType

from hxi_optimizer.hxi_config import load_config
from hxi_optimizer.comms.register_map import set_verified_word_order

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("commissioning")

SPARE_FLOAT_ADDR = 6629   # %R06630 — NOT a live control register


async def test_byte_order(client, unit_id: int) -> str | None:
    """TEST 1: empirically determine FLOAT32 word order. Returns 'ABCD' / 'CDAB' / None."""
    test_value = 1234.5
    expected_raw = struct.pack(">f", test_value)
    abcd_words = struct.unpack(">HH", expected_raw)
    cdab_words = (abcd_words[1], abcd_words[0])

    logger.info(f"TEST 1: byte-order verification (writing {test_value})")
    logger.info(f"  ABCD words: [{hex(abcd_words[0])}, {hex(abcd_words[1])}]")
    logger.info(f"  CDAB words: [{hex(cdab_words[0])}, {hex(cdab_words[1])}]")

    # Try ABCD first
    await client.write_registers(address=SPARE_FLOAT_ADDR,
                                 values=list(abcd_words), device_id=unit_id)
    await asyncio.sleep(0.1)
    result = await client.read_holding_registers(address=SPARE_FLOAT_ADDR,
                                                 count=2, device_id=unit_id)
    actual = tuple(result.registers)
    decoded_abcd = struct.unpack(">f", struct.pack(">HH", actual[0], actual[1]))[0]
    decoded_cdab = struct.unpack(">f", struct.pack(">HH", actual[1], actual[0]))[0]
    logger.info(f"  Read back: {actual}  decoded ABCD={decoded_abcd}  CDAB={decoded_cdab}")

    if abs(decoded_abcd - test_value) < 0.01:
        logger.info("  PASS: ABCD (big-endian word order, MASTER_CONTEXT §3.4)")
        return "ABCD"
    if abs(decoded_cdab - test_value) < 0.01:
        logger.info("  PASS: CDAB (low-word-first — note: differs from §3.4)")
        return "CDAB"
    logger.error(f"  FAIL: neither order recovered {test_value}")
    return None


async def test_fc16_atomicity(client, unit_id: int, n_trials: int = 1000) -> bool:
    """TEST 2: FC16 paired-write atomicity (uses spare regs %R06630/06631)."""
    logger.info(f"TEST 2: FC16 atomicity ({n_trials} trials)")
    lower_addr = SPARE_FLOAT_ADDR
    cross_faults = 0
    for i in range(n_trials):
        lo = 200 + (i % 50)
        up = lo + 100
        await client.write_registers(address=lower_addr,
                                     values=[lo, up], device_id=unit_id)
        result = await client.read_holding_registers(address=lower_addr,
                                                     count=2, device_id=unit_id)
        if result.registers[0] >= result.registers[1]:
            cross_faults += 1
            logger.error(f"  Cross-fault at trial {i}: {tuple(result.registers)}")
        if i % 100 == 0:
            logger.info(f"  Trial {i}/{n_trials}...")
    if cross_faults == 0:
        logger.info(f"  PASS: 0 cross-faults across {n_trials} writes")
        return True
    logger.error(f"  FAIL: {cross_faults} cross-faults detected")
    return False


async def test_vpn_latency(client, unit_id: int, n_reads: int = 100) -> dict:
    """TEST 3: characterise VPN RTT distribution."""
    logger.info(f"TEST 3: VPN latency ({n_reads} reads)")
    rtts: list[float] = []
    for _ in range(n_reads):
        t0 = time.perf_counter()
        await client.read_holding_registers(address=6599, count=28, device_id=unit_id)
        rtts.append((time.perf_counter() - t0) * 1000.0)
        await asyncio.sleep(0.05)
    rtts_sorted = sorted(rtts)
    stats = {
        "mean_ms": statistics.mean(rtts),
        "p95_ms":  rtts_sorted[int(0.95 * len(rtts))],
        "p99_ms":  rtts_sorted[int(0.99 * len(rtts))],
        "max_ms":  max(rtts),
        "achievable_hz": 1000.0 / max(statistics.mean(rtts), 1.0),
    }
    for k, v in stats.items():
        logger.info(f"  {k}: {v:.1f}")
    if stats["p95_ms"] < 450:
        logger.info("  PASS: P95 < 450ms (2 Hz achievable)")
    else:
        logger.warning("  NOTE: P95 > 450ms — actual rate will be < 2 Hz")
    return stats


async def test_noise_floor(client, unit_id: int,
                           rpm_setpoint: float = 60.0,
                           duration: float = 60.0) -> dict:
    """TEST 4: RPM noise floor at steady state. Operator must hold setpoint."""
    logger.info(f"TEST 4: noise floor at {rpm_setpoint} RPM ({duration}s)")
    logger.info("  → Top drive must be at steady speed, no load")
    await asyncio.sleep(5.0)
    readings: list[float] = []
    t_start = time.monotonic()
    while time.monotonic() - t_start < duration:
        result = await client.read_holding_registers(address=6599, count=2,
                                                     device_id=unit_id)
        raw = struct.pack(">HH", result.registers[0], result.registers[1])
        readings.append(struct.unpack(">f", raw)[0])
        await asyncio.sleep(0.5)
    mean = statistics.mean(readings)
    std = statistics.stdev(readings) if len(readings) > 1 else 0.0
    logger.info(f"  Mean RPM: {mean:.2f}  Std: {std:.3f}  ±3σ: ±{3*std:.2f}")
    logger.info(f"  → Set deadband_rpm = {max(2.0, 2*std):.1f}")
    return {"mean_rpm": mean, "std_rpm": std,
            "recommended_deadband": max(2.0, 2 * std)}


TESTS = ("byte_order", "fc16_atomicity", "vpn_latency", "noise_floor", "all")


async def _connect(host: str, port: int):
    client = AsyncModbusTcpClient(
        host=host, port=port, framer=FramerType.SOCKET, timeout=3.0, retries=3,
    )
    await client.connect()
    if not client.connected:
        logger.error(f"Cannot connect to PLC at {host}:{port}")
        return None
    return client


async def run_selected(test: str, host: str, port: int, unit_id: int,
                       trials: int, rpm: float, duration: float) -> None:
    client = await _connect(host, port)
    if client is None:
        return
    try:
        if test in ("byte_order", "all"):
            order = await test_byte_order(client, unit_id)
            if order:
                set_verified_word_order(order)
                logger.info(f"  → COMMIT VERIFIED_WORD_ORDER = {order!r} "
                            f"into hxi_optimizer/comms/register_map.py")
        if test in ("fc16_atomicity", "all"):
            await test_fc16_atomicity(client, unit_id, n_trials=trials)
        if test in ("vpn_latency", "all"):
            await test_vpn_latency(client, unit_id)
        if test in ("noise_floor", "all"):
            await test_noise_floor(client, unit_id,
                                   rpm_setpoint=rpm, duration=duration)
    finally:
        client.close()


def _parse_args():
    import argparse
    cfg = load_config()
    p = argparse.ArgumentParser(description="HXI Optimizer Phase A commissioning tests")
    p.add_argument("--test", choices=TESTS, default="all",
                   help="which test to run (default: all)")
    p.add_argument("--host", default=cfg.plc_host,
                   help="PLC IP through eWon VPN (overrides hxi_config.json)")
    p.add_argument("--port", type=int, default=cfg.plc_port)
    p.add_argument("--unit-id", type=int, default=cfg.unit_id)
    p.add_argument("--trials", type=int, default=1000,
                   help="FC16 atomicity trials (default 1000)")
    p.add_argument("--rpm", type=float, default=60.0,
                   help="setpoint for noise-floor test (default 60)")
    p.add_argument("--duration", type=float, default=60.0,
                   help="noise-floor duration seconds (default 60)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.host in ("", "CONFIGURE_ME"):
        logger.error("PLC host not set. Pass --host <ip> or edit hxi_config.json")
        raise SystemExit(2)
    asyncio.run(run_selected(
        test=args.test, host=args.host, port=args.port,
        unit_id=args.unit_id, trials=args.trials,
        rpm=args.rpm, duration=args.duration,
    ))
