"""eCatcher / Talk2m tunnel detection.

Goal: tell the optimizer which eWon is *currently* connected, without
requiring Steve to edit config files when he switches rigs. The moment
eCatcher opens a tunnel to a different rig, the optimizer should notice
and switch register maps automatically.

Detection strategy (fallback chain — first hit wins):

  1. Talk2m REST API (authoritative, needs credentials)
     https://developer.ewon.biz/content/talk2m-developer-api
     Query m2web.talk2m.com for the list of eWon devices, read the
     "status" field for each, and identify the one that shows tunnel up.

  2. eCatcher log parsing (no credentials, auto-detects log path)
     eCatcher writes tunnel-open/close events to its log file under
     %APPDATA%\\eCatcher\\log\\*.log. We tail the latest log file and
     extract the most recent "Connection opened to <device>" line.

  3. Network adapter scan (last resort)
     The eCatcher tunnel appears as a virtual adapter named something
     like "Talk2m" or "eCatcher". Detects tunnel-up/down but not the
     device name.

All three strategies are optional; each returns `None` if it can't
determine anything, and the caller chains them.

Usage:
    monitor = EcatcherMonitor.from_config(config)
    state = await monitor.detect()
    if state.tunnel_up and state.connected_ewon:
        print(f"eCatcher is connected to {state.connected_ewon}")
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ecatcher")


# ──────────────────────────────────────────────────────────────────────
# State object
# ──────────────────────────────────────────────────────────────────────
@dataclass
class EcatcherState:
    ts: float = field(default_factory=time.time)
    source: str = "unavailable"        # talk2m_api | log_parse | adapter | unavailable
    tunnel_up: bool = False
    connected_ewon: Optional[str] = None
    vpn_ip: Optional[str] = None
    all_online: list[str] = field(default_factory=list)  # all eWons online (API mode)
    error: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "ts": self.ts,
            "source": self.source,
            "tunnel_up": self.tunnel_up,
            "connected_ewon": self.connected_ewon,
            "vpn_ip": self.vpn_ip,
            "all_online": self.all_online,
            "error": self.error,
        }


# ──────────────────────────────────────────────────────────────────────
# Monitor (applies all strategies)
# ──────────────────────────────────────────────────────────────────────
class EcatcherMonitor:
    """Polls every detection strategy and returns the best EcatcherState."""

    def __init__(self,
                 talk2m_account: Optional[str] = None,
                 talk2m_username: Optional[str] = None,
                 talk2m_password: Optional[str] = None,
                 talk2m_developer_id: Optional[str] = None,
                 ecatcher_log_path: Optional[str] = None) -> None:
        self.t2m_account = talk2m_account
        self.t2m_user = talk2m_username
        self.t2m_pass = talk2m_password
        self.t2m_devid = talk2m_developer_id
        # Auto-detect log path if not provided
        self.log_path = (Path(ecatcher_log_path) if ecatcher_log_path
                         else _find_ecatcher_log())

    @classmethod
    def from_config(cls, config) -> "EcatcherMonitor":
        return cls(
            talk2m_account=getattr(config, "talk2m_account", None),
            talk2m_username=getattr(config, "talk2m_username", None),
            talk2m_password=getattr(config, "talk2m_password", None),
            talk2m_developer_id=getattr(config, "talk2m_developer_id", None),
            ecatcher_log_path=getattr(config, "ecatcher_log_path", None),
        )

    # ── Main entry ──────────────────────────────────────────────────

    async def detect(self) -> EcatcherState:
        """Try every strategy, return the highest-confidence result."""
        # Strategy 1: Talk2m API
        if self.t2m_account and self.t2m_user:
            s = await self._detect_via_api()
            if s and s.tunnel_up:
                return s
            if s and s.error:
                # API configured but failing — note but continue
                logger.debug(f"Talk2m API: {s.error}")

        # Strategy 2: log parse
        s = self._detect_via_logs()
        if s:
            return s

        # Strategy 3: adapter scan
        s = self._detect_via_adapter()
        if s:
            return s

        return EcatcherState(tunnel_up=False, source="unavailable")

    # ── Strategy 1: Talk2m REST API ──────────────────────────────────

    async def _detect_via_api(self) -> Optional[EcatcherState]:
        """Query https://m2web.talk2m.com/t2mapi/get/getewons"""
        try:
            import urllib.parse
            import urllib.request
        except ImportError:
            return None

        params = {
            "t2maccount": self.t2m_account,
            "t2musername": self.t2m_user,
            "t2mpassword": self.t2m_pass or "",
            "t2mdeveloperid": self.t2m_devid or "",
        }
        url = ("https://m2web.talk2m.com/t2mapi/get/getewons?"
               + urllib.parse.urlencode(params))

        def _fetch():
            try:
                r = urllib.request.urlopen(url, timeout=5)
                import json
                return json.loads(r.read().decode())
            except Exception as e:
                return {"_error": str(e)}

        data = await asyncio.get_event_loop().run_in_executor(None, _fetch)
        if "_error" in data:
            return EcatcherState(source="talk2m_api", error=data["_error"])

        if not data.get("success"):
            return EcatcherState(source="talk2m_api",
                                 error=data.get("message", "unknown failure"))

        ewons = data.get("ewons", [])
        online = [e for e in ewons if str(e.get("status", "")).lower() == "online"]
        online_names = [e.get("name") for e in online if e.get("name")]

        # "Connected" = online from API perspective. If multiple, pick the
        # one whose VPN IP matches our PLC host (caller handles this match).
        if not online:
            return EcatcherState(source="talk2m_api", tunnel_up=False,
                                 all_online=[])
        picked = online[0]  # caller can cross-reference with plc_host
        return EcatcherState(
            source="talk2m_api",
            tunnel_up=True,
            connected_ewon=picked.get("name"),
            vpn_ip=picked.get("ewonIp") or picked.get("ipaddr"),
            all_online=online_names,
        )

    # ── Strategy 2: eCatcher log parsing ─────────────────────────────

    def _detect_via_logs(self) -> Optional[EcatcherState]:
        if not self.log_path or not self.log_path.exists():
            return None
        try:
            # Tail last ~200 KB to find recent open/close events
            size = self.log_path.stat().st_size
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                if size > 200_000:
                    f.seek(size - 200_000)
                    f.readline()  # skip partial line
                recent = f.read()
        except Exception as e:
            return EcatcherState(source="log_parse", error=str(e))

        # Find the LAST "tunnel opened" / "connection opened to" entry.
        # eCatcher log formats vary by version:
        #   "Tunnel to 'device' established"   (quotes are typical)
        #   "Tunnel to device established"     (sometimes no quotes)
        #   "Connection opened to device"
        #   "Connected to device 'rig name'."
        # When the name is quoted we extract between the quotes; otherwise
        # we take the longest non-whitespace run (a single word).
        patterns = [
            # Quoted name: preferred (captures multi-word names correctly)
            r"[Tt]unnel to ['\"]([^'\"\n]+)['\"] (?:established|opened)",
            r"[Cc]onnection opened to ['\"]([^'\"\n]+)['\"]",
            r"[Cc]onnected to (?:device )?['\"]([^'\"\n]+)['\"]",
            # Unquoted name: single word only
            r"[Tt]unnel to (\S+) (?:established|opened)",
            r"[Cc]onnection opened to (\S+?)[\s.]",
        ]
        last_open = None
        last_close = None
        for line in recent.splitlines():
            for p in patterns:
                m = re.search(p, line)
                if m:
                    last_open = (m.group(1).strip(), line)
                    break
            if re.search(r"(?:tunnel|connection) closed", line, re.I):
                last_close = line

        # If the last event was a close AFTER the last open, tunnel is down
        if last_open and last_close:
            open_pos = recent.rfind(last_open[1])
            close_pos = recent.rfind(last_close)
            if close_pos > open_pos:
                return EcatcherState(source="log_parse", tunnel_up=False)
        if last_open:
            return EcatcherState(
                source="log_parse",
                tunnel_up=True,
                connected_ewon=last_open[0],
            )
        return EcatcherState(source="log_parse", tunnel_up=False)

    # ── Strategy 3: Network adapter detection ────────────────────────

    def _detect_via_adapter(self) -> Optional[EcatcherState]:
        try:
            import psutil
        except ImportError:
            return None
        try:
            for name, addrs in psutil.net_if_addrs().items():
                if any(kw in name.lower() for kw in ("talk2m", "ecatcher", "ewon")):
                    for a in addrs:
                        if a.family.name in ("AF_INET",):
                            return EcatcherState(
                                source="adapter",
                                tunnel_up=True,
                                vpn_ip=a.address,
                                connected_ewon=None,  # adapter can't tell us which
                            )
            return EcatcherState(source="adapter", tunnel_up=False)
        except Exception as e:
            return EcatcherState(source="adapter", error=str(e))


# ──────────────────────────────────────────────────────────────────────
# Log path discovery
# ──────────────────────────────────────────────────────────────────────
def _find_ecatcher_log() -> Optional[Path]:
    """Try common install locations for eCatcher's log directory."""
    candidates = []
    appdata = os.environ.get("APPDATA")
    localappdata = os.environ.get("LOCALAPPDATA")
    programdata = os.environ.get("PROGRAMDATA")
    for base in filter(None, [appdata, localappdata, programdata]):
        candidates.append(Path(base) / "eCatcher" / "log")
        candidates.append(Path(base) / "HMS" / "eCatcher" / "log")
    # Also check common install paths
    candidates.append(Path("C:/Program Files (x86)/eCatcher/log"))
    candidates.append(Path("C:/Program Files/eCatcher/log"))

    for d in candidates:
        if d.exists() and d.is_dir():
            # Newest .log file wins
            logs = sorted(d.glob("*.log"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            if logs:
                return logs[0]
    return None
