# Deployment

Production runs as an **NSSM Windows Service** on the rig PC. Session 0, restart-on-failure with 5 s → 30 s → 60 s backoff, logs to `hxi_optimizer/logs/service_*.log`.

For testing or manual operation, the single-command launcher is `python run.py` from the repo root — see [README.md](../README.md) for the flag reference. The launcher handles SIM vs REAL mode automatically and auto-opens the browser. Service mode (below) is the production path; the launcher is for IT / testers / dev.

---

## Prerequisites

- Windows 10/11 or Server 2019+
- Python 3.14 (matches the rest of the toolchain; do not use 3.11 for the optimizer itself even though asyncua-related tools still need 3.11)
- Administrator shell for service installation
- eCatcher installed + at least one rig tunnel configured
- `Register_List.xlsx` present at repo root (for the register scanner)

Install deps:

```bash
python -m pip install -r requirements.txt
```

Key libraries (if no `requirements.txt`): `pymodbus==3.13.*`, `numpy`, `onnxruntime`, `fastapi`, `uvicorn`, `openpyxl`, `paramiko`, `pytest`, `pytest-asyncio`, `psutil`, `pyyaml`, `pydantic`.

---

## One-time setup

```cmd
REM As Administrator:
powershell -ExecutionPolicy Bypass -File hxi_optimizer\deploy\windows_hardening.ps1
hxi_optimizer\deploy\install_service.bat
sc query HXIOptimizer
```

`windows_hardening.ps1` creates a non-admin service user, firewall rule for the dashboard port, and disables Windows Update automatic reboots for the rig PC. Audit log at `%ProgramData%\HXIOptimizer\install_audit.log`.

To uninstall: `hxi_optimizer\deploy\uninstall_service.bat`.

---

## Configuration

`hxi_optimizer/hxi_config.json` is the production config. A template is in `hxi_optimizer/hxi_config.template.json` — copy it, fill in the fields, restart the service.

### Minimal required fields

```json
{
  "plc_host": "10.0.0.5",
  "plc_port": 502,
  "unit_id": 1,
  "phase": "A",
  "require_verified_word_order": true,
  "safety": {
    "abs_min_lower": 250,
    "abs_max_lower": 500,
    "abs_min_upper": 550,
    "abs_max_upper": 800,
    "min_band_counts": 50
  }
}
```

**Service will refuse to start** if any of the four `abs_*` fields or `VERIFIED_WORD_ORDER` in `hxi_optimizer/comms/register_map.py` is `None`. Both are populated by the commissioning tests (see [SAFETY.md](SAFETY.md)).

### Dashboard auth (recommended)

```json
{
  "dashboard_token": "GENERATE_A_RANDOM_32_CHAR_STRING_HERE",
  "dashboard_host": "0.0.0.0",
  "dashboard_port": 8420,
  "dashboard_endpoint_timeout_s": 30.0,
  "dashboard_max_body_bytes": 1000000,
  "dashboard_max_concurrent": 64
}
```

Generate a token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Alternative: set `HXI_DASHBOARD_TOKEN` in the service environment (NSSM → Edit service → Environment). Env var takes precedence over the config file.

If **neither** is set, auth is disabled. Dashboard browsers reach the UI without a token, which is fine for closed-VPN networks but not for shared/public ones.

### Fleet + eCatcher

```json
{
  "auto_detect_machine": true,
  "ecatcher_poll_interval_s": 30.0,
  "talk2m_account": "myaccount",
  "talk2m_username": "user",
  "talk2m_password": "***",
  "talk2m_developer_id": "DEV-KEY"
}
```

`talk2m_*` fields are optional but recommended — without them the optimizer falls back to log parsing + virtual adapter scan, which works but is less reliable across eCatcher versions. Rig identification also works without any of these if the PLC IP maps uniquely to an eWon in `fleet_catalog.yaml`.

---

## Service control

```cmd
sc start HXIOptimizer
sc stop HXIOptimizer
sc query HXIOptimizer
nssm restart HXIOptimizer
```

Logs:

- Service stdout: `hxi_optimizer/logs/service_stdout.log`
- Service stderr: `hxi_optimizer/logs/service_stderr.log`
- Application: `hxi_optimizer/logs/optimizer.log`
- Audit (only): `hxi_optimizer/logs/audit.log`
- Per-run telemetry CSV: `hxi_optimizer/logs/drill_<epoch>.csv`

The CSV logger rotates naturally (one file per service start). Old files are not auto-deleted — rotate via a scheduled task if disk usage becomes a concern (~30 MB/day at 2 Hz).

---

## Validation after install

1. `sc query HXIOptimizer` → should be `RUNNING`.
2. `curl http://localhost:8420/healthz` → `{"status":"ok", ...}`.
3. Open `http://localhost:8420/` → dashboard loads. If auth is enabled, enter the token once (stored in browser localStorage).
4. Check `hxi_optimizer/logs/optimizer.log` for `MACHINE CHANGE` or `Machine identified` log lines within 30 s of start. If neither, check eCatcher config or set `ewon_name` manually.
5. Run `pytest hxi_optimizer/tests/ -q` on the rig PC — should report 2,145 pass. If any fail, service is deployed against a codebase that doesn't match its tests.

---

## Commissioning (first-time against a real PLC)

**Before promoting to Phase B or C**:

```bash
python -m hxi_optimizer.deploy.commissioning_tests
```

This runs the 8 tests listed in [SAFETY.md](SAFETY.md). All must pass, and the test writes `VERIFIED_WORD_ORDER` into `hxi_optimizer/comms/register_map.py` and `safety.abs_*` into `hxi_config.json`.

Re-running the commissioning tests after a PLC firmware update is required — byte order can shift across firmware revisions.

---

## Upgrade procedure

1. `sc stop HXIOptimizer`
2. `git pull` (or copy new files over)
3. `python -m pip install -r requirements.txt` (if deps changed)
4. `python -m pytest hxi_optimizer/tests/ -q` → 2,145 pass
5. `sc start HXIOptimizer`
6. Watch `optimizer.log` for `Phase A` boot (or C/D if config specifies). Watch `audit.log` for any startup rejections.
7. Verify dashboard `/healthz` + WebSocket connects.

**Rollback**: `sc stop HXIOptimizer`, `git checkout <previous>`, `sc start HXIOptimizer`. `state.json` is forward- and backward-compatible within the same major version.

---

## Disk layout (prod)

```
<install-dir>/
├── hxi_optimizer/
│   ├── main.py
│   ├── hxi_config.json          ← production config (rig-specific)
│   ├── models/
│   │   ├── classifier.onnx      ← sim-trained default, NEVER overwritten
│   │   ├── classifier_meta.json
│   │   ├── autoencoder.onnx
│   │   ├── autoencoder_meta.json
│   │   ├── backup_v1_<ts>/      ← one backup per deploy
│   │   └── per_rig/
│   │       ├── precision_rig_707_3pd_ht/
│   │       │   ├── classifier.onnx      ← fine-tuned for this rig
│   │       │   ├── classifier_meta.json
│   │       │   └── autoencoder_meta.json
│   │       └── <other rigs>/
│   └── logs/
│       ├── optimizer.log
│       ├── audit.log             ← fsync per write
│       ├── service_stdout.log    ← NSSM redirect
│       ├── service_stderr.log    ← NSSM redirect
│       ├── drill_<epoch>.csv     ← one per service run
│       └── dataset/              ← real captured episodes
│           └── <machine_slug>/
│               └── <LABEL>/
│                   └── episode_<ts>.npz
├── training/                     ← optional; safe to exclude from prod image
├── local_test/                   ← optional; safe to exclude from prod image
├── Register_List.xlsx
└── MASTER_CONTEXT_FOR_CLAUDE_CODE.md
```

Minimum prod image: `hxi_optimizer/`, `Register_List.xlsx`, and the config. `training/` + `local_test/` are only needed if you plan to generate data or run fine-tunes on the rig PC (we recommend doing that on a workstation and copying ONNX + meta to `hxi_optimizer/models/per_rig/<slug>/`).
