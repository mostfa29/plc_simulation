================================================================================
HXI OPTIMIZER  —  commands/
================================================================================

99% of the time you do NOT come here. Just double-click HXI.bat at the repo
root. It gives you a numbered menu and handles everything.

This folder contains the brain and the advanced single-purpose scripts:

  HXI.bat              (at repo root) — the only thing you normally click
  _auto.py             automation brain (bootstrap/discover/commission/…)
  _helpers.py          small helpers (wait_port, backup, set_phase, …)
  _tail_log.py         live log tail used by the menu
  _common.bat          shared Python detection for the advanced scripts
  advanced/            individual single-purpose .bat files (for power users)
                       — see advanced/COMMANDS.txt for the full list

If something goes wrong that the menu can't handle, an advanced script in
advanced/ probably can. Otherwise: call Moe.
================================================================================
