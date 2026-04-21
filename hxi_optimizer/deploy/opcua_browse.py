"""One-time OPC UA address-space browser.

Connects to a live CPE305 on port 4840, walks the address space, prints every
reachable NodeId + BrowseName + DataType. Use the output to populate
hxi_optimizer/comms/opcua_nodes.json.

Usage:
    python -m hxi_optimizer.deploy.opcua_browse --host 192.168.1.10 --depth 5

Options:
    --host          PLC IP
    --port          OPC UA port (default 4840)
    --depth         Max recursion depth (default 4)
    --filter        Only print nodes whose BrowseName matches this substring
    --output        Write a starter JSON map to this path
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from asyncua import Client
from asyncua.ua import VariantType

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("opcua_browse")


async def walk(node, depth: int, max_depth: int, out: list, filter_substr: str) -> None:
    if depth > max_depth:
        return
    try:
        name = (await node.read_browse_name()).Name
    except Exception:
        name = "<unreadable>"
    try:
        node_id_str = node.nodeid.to_string()
    except Exception:
        node_id_str = str(node.nodeid)
    dtype = ""
    try:
        klass = await node.read_node_class()
        if klass.name == "Variable":
            val = await node.read_value()
            dtype = type(val).__name__
            if filter_substr and filter_substr.lower() not in name.lower():
                pass
            else:
                print(f"  {'  ' * depth}{name:40s} {node_id_str:50s} {dtype}")
                out.append({"browse_name": name, "node_id": node_id_str,
                            "type": dtype})
    except Exception:
        pass
    try:
        for child in await node.get_children():
            await walk(child, depth + 1, max_depth, out, filter_substr)
    except Exception:
        pass


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=4840)
    p.add_argument("--depth", type=int, default=4)
    p.add_argument("--filter", default="")
    p.add_argument("--output", default="")
    args = p.parse_args()

    url = f"opc.tcp://{args.host}:{args.port}"
    logger.info(f"Connecting to {url}...")
    async with Client(url=url, timeout=5.0) as client:
        logger.info("Connected. Walking address space...")
        print(f"\n{'Name':42s} {'NodeId':52s} {'Type':20s}")
        print("-" * 114)
        out: list[dict] = []
        root = client.get_objects_node()
        await walk(root, 0, args.depth, out, args.filter)
        print("-" * 114)
        logger.info(f"Discovered {len(out)} variable nodes")

        if args.output:
            # Starter map — operator fills in the address → node_id pairs
            starter = {
                "_comment": "Auto-generated starter map — pair up register addresses with NodeIds",
                "_discovered": out,
            }
            with open(args.output, "w") as f:
                json.dump(starter, f, indent=2)
            logger.info(f"Wrote starter map to {args.output}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
