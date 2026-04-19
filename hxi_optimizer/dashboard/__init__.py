"""Operator dashboard - FastAPI + WebSocket.

Contains:
    server        create_app() + start_dashboard() - runs as a 5th asyncio task
                  inside main.py's asyncio.gather.
    static/       index.html (single-page dashboard, no build step).

Endpoints:
    GET  /                       dashboard HTML
    GET  /api/status             current telemetry snapshot
    GET  /api/audit              last 200 audit-log entries
    GET  /api/registers          full register catalog from Register_List.xlsx
    GET  /api/registers/scan     live FC03 scan of all non-spare registers
    GET  /api/simulate/scenarios available simulation scenarios + params
    POST /api/simulate           run a simulation scenario
    POST /api/control/enable     operator: enable adaptive
    POST /api/control/disable    operator: disable adaptive
    POST /api/control/phase      operator: promote/demote phase
    POST /api/control/depth      operator: update drill depth
    WS   /ws                     2 Hz live telemetry push
"""
