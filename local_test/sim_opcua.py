"""OPC UA simulator on opc.tcp://127.0.0.1:4840.

Mirrors the variable set the OPCUATransport's node map expects, using the same
register-address → NodeId mapping as opcua_nodes.json. Drives RPM dynamics in
a background task so the optimizer sees realistic behaviour just like sim_plc.

Namespace: ns=4 (matches the default node map)

Usage:
    python -m local_test.sim_opcua                 # default 127.0.0.1:4840
    python -m local_test.sim_opcua --port 4841
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import struct
from pathlib import Path

from asyncua import Server, ua

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)-14s %(levelname)-7s %(message)s")
logger = logging.getLogger("sim_opcua")

# Register addresses (pymodbus 0-indexed) that the node map covers
ADDRESSES = {
    6599: ("RPM_Real_Encoder",      "REAL", 60.0),
    6601: ("Swash_Output",          "INT",  500),
    6602: ("Swash_Lower_Threshold", "INT",  400),
    6603: ("Swash_Upper_Limit",     "INT",  600),
    6604: ("Heartbeat",             "INT",  0),
    6609: ("Active_Lower",          "INT",  400),
    6610: ("Active_Upper",          "INT",  600),
    6612: ("Status_Word",           "INT",  1),
    6615: ("SS_Set_Speed_FWD",      "REAL", 60.0),
    6626: ("Bump_Flag_FWD",         "INT",  0),
    6627: ("Bump_Flag_REV",         "INT",  0),
    6645: ("Delivered_Torque_FTLBS","REAL", 1500.0),
    6664: ("Outputs_Word_1",        "INT",  0),
    6669: ("Loop_Temp",             "REAL", 55.0),
}


async def boot(host: str, port: int, map_path: Path) -> None:
    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://{host}:{port}")
    server.set_server_name("HXI Local Simulator")

    # The node map uses ns=4 to mimic PACSystems. Add fillers until we reach 4.
    ns = await server.register_namespace("hxi")
    while ns < 4:
        ns = await server.register_namespace(f"filler_{ns}")
    if ns != 4:
        # We overshot or collided — just use the current ns and record it
        logger.warning(f"Could not reach ns=4 exactly, got ns={ns}. "
                       f"Using ns={ns} in node map.")

    objects = server.nodes.objects

    # Build node map at runtime matching opcua_nodes.json structure
    node_map: dict[int, str] = {}
    nodes_by_address: dict[int, any] = {}

    for addr, (name, dtype, default) in ADDRESSES.items():
        node_id_str = f"ns={ns};s=HXI.{_section_for(addr)}.{name}"
        if dtype == "REAL":
            var = await objects.add_variable(
                ua.NodeId(f"HXI.{_section_for(addr)}.{name}", ns),
                name,
                ua.Variant(float(default), ua.VariantType.Float),
            )
        else:
            var = await objects.add_variable(
                ua.NodeId(f"HXI.{_section_for(addr)}.{name}", ns),
                name,
                ua.Variant(int(default), ua.VariantType.Int16),
            )
        await var.set_writable()
        node_map[addr] = node_id_str
        nodes_by_address[addr] = var

    # Write the node map that OPCUATransport will read
    map_path.parent.mkdir(parents=True, exist_ok=True)
    with open(map_path, "w") as f:
        json.dump({str(k): v for k, v in node_map.items()}, f, indent=2)
    logger.info(f"Wrote node map to {map_path}")

    # Mirroring task — when swash bounds change, mirror to active readback
    async def mirror_loop():
        while True:
            await asyncio.sleep(0.1)
            try:
                lo = await nodes_by_address[6602].read_value()
                up = await nodes_by_address[6603].read_value()
                await nodes_by_address[6609].write_value(ua.Variant(int(lo), ua.VariantType.Int16))
                await nodes_by_address[6610].write_value(ua.Variant(int(up), ua.VariantType.Int16))
            except Exception as e:
                logger.debug(f"Mirror error: {e}")

    # Plant dynamics task — drift RPM toward swash_output + noise
    async def dynamics_loop():
        while True:
            await asyncio.sleep(0.5)
            try:
                rpm = await nodes_by_address[6599].read_value()
                sw = await nodes_by_address[6601].read_value()
                target = (sw - 500) / 500.0 * 200.0
                new_rpm = rpm + 0.15 * (target - rpm) + random.gauss(0, 0.5)
                await nodes_by_address[6599].write_value(
                    ua.Variant(float(new_rpm), ua.VariantType.Float)
                )
                temp = await nodes_by_address[6669].read_value()
                await nodes_by_address[6669].write_value(
                    ua.Variant(float(temp + random.gauss(0, 0.02)), ua.VariantType.Float)
                )
            except Exception as e:
                logger.debug(f"Dynamics error: {e}")

    asyncio.create_task(mirror_loop())
    asyncio.create_task(dynamics_loop())

    async with server:
        logger.info(f"HXI OPC UA sim on opc.tcp://{host}:{port}")
        logger.info(f"Published {len(node_map)} nodes under ns=4")
        logger.info("Press Ctrl-C to stop")
        while True:
            await asyncio.sleep(1)


def _section_for(addr: int) -> str:
    if addr <= 6645:
        return "Smart_Slide" if addr != 6645 else "Torque_Control"
    if addr == 6664:
        return "Main_PLC"
    return "Sensors"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4840)
    p.add_argument("--map", default="hxi_optimizer/comms/opcua_nodes.json")
    args = p.parse_args()
    try:
        asyncio.run(boot(args.host, args.port, Path(args.map)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
