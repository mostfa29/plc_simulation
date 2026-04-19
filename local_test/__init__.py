"""Local test harness — simulator + runbook for offline integration testing.

DO NOT use any of this in production. The simulator runs on 127.0.0.1
loopback only and uses dummy safety limits. Real PLC writes go through
hxi_optimizer/main.py against the live CPE305.
"""
