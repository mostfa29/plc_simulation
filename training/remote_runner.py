"""SSH orchestrator — connect to remote GPU, upload data, train, download models.

Uses paramiko for SSH/SFTP. All operations are idempotent and resumable.
"""
from __future__ import annotations

import fnmatch
import glob
import logging
import os
import stat
import time
from pathlib import Path, PurePosixPath
from typing import Optional

import paramiko

from training.remote_config import GPUMachineConfig

logger = logging.getLogger("remote_runner")


class RemoteRunner:
    """Manages SSH lifecycle and file transfers to/from the GPU machine."""

    def __init__(self, cfg: GPUMachineConfig) -> None:
        self.cfg = cfg
        self.client: Optional[paramiko.SSHClient] = None
        self.sftp: Optional[paramiko.SFTPClient] = None

    # ─── Connection ─────────────────────────────────────────────────────

    def connect(self) -> None:
        logger.info(f"Connecting to {self.cfg.user}@{self.cfg.host}:{self.cfg.port}")
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        key = paramiko.Ed25519Key.from_private_key_file(self.cfg.ssh_key_path) \
            if "ed25519" in self.cfg.ssh_key_path.lower() \
            else paramiko.RSAKey.from_private_key_file(
                self.cfg.ssh_key_path,
                password=self.cfg.ssh_key_passphrase or None,
            )
        self.client.connect(
            hostname=self.cfg.host,
            port=self.cfg.port,
            username=self.cfg.user,
            pkey=key,
            timeout=30,
        )
        self.sftp = self.client.open_sftp()
        logger.info("SSH connected successfully")

    def disconnect(self) -> None:
        if self.sftp:
            self.sftp.close()
        if self.client:
            self.client.close()
        logger.info("SSH disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()

    # ─── Remote command execution ───────────────────────────────────────

    def run(self, cmd: str, timeout: int = 600, check: bool = True) -> str:
        """Execute command on remote. Returns stdout. Raises on non-zero exit."""
        logger.info(f"REMOTE> {cmd}")
        stdin, stdout, stderr = self.client.exec_command(cmd, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if out.strip():
            for line in out.strip().split("\n")[-20:]:
                logger.info(f"  stdout: {line}")
        if err.strip():
            for line in err.strip().split("\n")[-10:]:
                logger.warning(f"  stderr: {line}")
        if check and exit_code != 0:
            raise RuntimeError(
                f"Remote command failed (exit {exit_code}): {cmd}\n{err[-500:]}"
            )
        return out

    def run_bg(self, cmd: str) -> None:
        """Fire-and-forget command (nohup)."""
        full = f"nohup {cmd} > /dev/null 2>&1 &"
        self.client.exec_command(full)

    # ─── Remote filesystem ──────────────────────────────────────────────

    def mkdir_remote(self, remote_path: str) -> None:
        path = PurePosixPath(remote_path)
        parts_to_create = []
        check = path
        while True:
            try:
                self.sftp.stat(str(check))
                break
            except FileNotFoundError:
                parts_to_create.append(check)
                check = check.parent
                if str(check) in ("/", "."):
                    break
        for p in reversed(parts_to_create):
            self.sftp.mkdir(str(p))

    def upload_file(self, local: str | Path, remote: str) -> None:
        local = Path(local)
        if not local.exists():
            raise FileNotFoundError(f"Local file not found: {local}")
        remote_dir = str(PurePosixPath(remote).parent)
        self.mkdir_remote(remote_dir)
        logger.info(f"  Uploading {local.name} ({local.stat().st_size:,} bytes)")
        self.sftp.put(str(local), remote)

    def upload_dir(self, local_dir: str | Path, remote_dir: str,
                   pattern: str = "*") -> int:
        """Upload all files matching pattern from local_dir to remote_dir."""
        local_dir = Path(local_dir)
        count = 0
        for f in sorted(local_dir.glob(pattern)):
            if f.is_file():
                remote_path = f"{remote_dir}/{f.name}"
                self.upload_file(f, remote_path)
                count += 1
        logger.info(f"Uploaded {count} files to {remote_dir}")
        return count

    def download_file(self, remote: str, local: str | Path) -> None:
        local = Path(local)
        local.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"  Downloading → {local.name}")
        self.sftp.get(remote, str(local))

    def download_dir(self, remote_dir: str, local_dir: str | Path,
                     pattern: str = "*") -> int:
        """Download all files matching pattern from remote_dir."""
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for entry in self.sftp.listdir_attr(remote_dir):
            if stat.S_ISREG(entry.st_mode) and fnmatch.fnmatch(entry.filename, pattern):
                remote_path = f"{remote_dir}/{entry.filename}"
                local_path = local_dir / entry.filename
                self.download_file(remote_path, local_path)
                count += 1
        logger.info(f"Downloaded {count} files to {local_dir}")
        return count

    def remote_file_exists(self, remote_path: str) -> bool:
        try:
            self.sftp.stat(remote_path)
            return True
        except FileNotFoundError:
            return False

    # ─── Environment setup ──────────────────────────────────────────────

    def setup_remote_env(self) -> None:
        """One-time setup: create venv, install deps on the GPU machine."""
        base = self.cfg.remote_base
        venv = self.cfg.remote_venv
        logger.info("Setting up remote training environment...")
        self.run(f"mkdir -p {base}/data {base}/models {base}/logs {base}/scripts")
        if not self.remote_file_exists(f"{venv}/bin/activate"):
            logger.info("Creating Python venv on remote...")
            self.run(f"python3 -m venv {venv}", timeout=120)
        self.run(
            f"source {venv}/bin/activate && pip install --upgrade pip && "
            f"pip install tensorflow numpy pandas scikit-learn pyyaml matplotlib",
            timeout=600,
        )
        logger.info("Remote environment ready")

    # ─── Training job execution ─────────────────────────────────────────

    def upload_training_scripts(self) -> None:
        """Upload all training/*.py scripts to the remote machine."""
        scripts_dir = Path("training")
        remote_scripts = f"{self.cfg.remote_base}/scripts"
        for f in scripts_dir.glob("*.py"):
            self.upload_file(f, f"{remote_scripts}/{f.name}")
        # Also upload configs
        configs_dir = scripts_dir / "configs"
        if configs_dir.exists():
            for f in configs_dir.glob("*.yaml"):
                self.upload_file(f, f"{remote_scripts}/configs/{f.name}")

    def upload_csv_logs(self) -> int:
        """Upload rig PC's CSV logs to remote for training."""
        local = Path(self.cfg.local_logs_dir)
        remote = f"{self.cfg.remote_base}/data/csv_logs"
        return self.upload_dir(local, remote, pattern="drill_*.csv")

    def run_training_job(self, script: str, args: str = "",
                         timeout: int = 7200) -> str:
        """Run a training script on the remote GPU machine."""
        venv = self.cfg.remote_venv
        base = self.cfg.remote_base
        gpu = self.cfg.gpu_id
        cmd = (
            f"source {venv}/bin/activate && "
            f"cd {base}/scripts && "
            f"CUDA_VISIBLE_DEVICES={gpu} "
            f"python {script} {args}"
        )
        return self.run(cmd, timeout=timeout)

    def download_models(self) -> int:
        """Download trained TFLite models from remote to local."""
        remote = f"{self.cfg.remote_base}/models"
        local = Path(self.cfg.local_models_dir)
        return self.download_dir(remote, local, pattern="*.tflite")

    def download_training_logs(self) -> int:
        """Download training logs (metrics, plots) from remote."""
        remote = f"{self.cfg.remote_base}/logs"
        local = Path(self.cfg.local_data_dir) / "training_logs"
        return self.download_dir(remote, local, pattern="*")

    # ─── Health check ───────────────────────────────────────────────────

    def check_gpu(self) -> dict:
        """Check GPU availability on the remote machine."""
        try:
            out = self.run("nvidia-smi --query-gpu=name,memory.total,memory.free,utilization.gpu "
                           "--format=csv,noheader,nounits", check=False)
            if out.strip():
                parts = out.strip().split(",")
                return {
                    "gpu_name": parts[0].strip(),
                    "memory_total_mb": int(parts[1].strip()),
                    "memory_free_mb": int(parts[2].strip()),
                    "utilization_pct": int(parts[3].strip()),
                    "available": True,
                }
        except Exception as e:
            logger.warning(f"GPU check failed: {e}")
        return {"available": False}

    def check_disk_space(self) -> dict:
        """Check disk space on remote machine."""
        out = self.run(f"df -BG {self.cfg.remote_base} | tail -1", check=False)
        parts = out.split()
        if len(parts) >= 4:
            return {
                "total_gb": parts[1].rstrip("G"),
                "used_gb": parts[2].rstrip("G"),
                "avail_gb": parts[3].rstrip("G"),
            }
        return {}
