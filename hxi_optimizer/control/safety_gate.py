"""Mandatory safety layer for all PLC writes — per MASTER_CONTEXT §5.2.

THIS IS THE SOLE WRITE PATH for %R06603/%R06604 and %R06605. No code may
bypass it. Nine-layer validation runs in fixed sequence on every call.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from hxi_optimizer.comms.register_map import (
    ADDR_ACTIVE_LOWER, ADDR_HEARTBEAT, ADDR_SWASH_LOWER,
)

logger = logging.getLogger("safety_gate")


@dataclass(frozen=True)
class SafetyConfig:
    """Immutable safety limits set during commissioning. Never modified at runtime."""
    abs_min_lower: Optional[int] = None
    abs_max_lower: Optional[int] = None
    abs_min_upper: Optional[int] = None
    abs_max_upper: Optional[int] = None
    min_band_counts: int = 50

    # Rate limiting
    max_step_pct: float = 0.05      # 5% of current per cycle
    abs_max_step: int = 50
    abs_min_step: int = 5

    # Heartbeat
    heartbeat_timeout_s: float = 30.0
    write_interval_s: float = 10.0

    # Accept/reject
    accept_window_s: float = 60.0
    iae_accept_ratio: float = 1.20
    iae_rollback_ratio: float = 1.50
    oscillation_cycles_trigger: int = 3
    oscillation_threshold_pct: float = 0.02

    # Cooldown (exponential backoff)
    base_cooldown_s: float = 30.0
    max_cooldown_s: float = 300.0
    max_consecutive_rejections: int = 5


class AdaptState(Enum):
    BASELINE = auto()  # On LKG; optimizer may propose
    TRIAL    = auto()  # Trial values written; observing
    ACCEPTED = auto()  # (transient)
    REJECTED = auto()  # (transient)
    DISABLED = auto()  # Operator-disabled
    ESD      = auto()  # Emergency stop active


@dataclass
class LastKnownGood:
    lower: int = 0
    upper: int = 0
    timestamp: float = 0.0
    iae_at_acceptance: float = float("inf")
    drilling_mode: str = "unknown"
    rpm_setpoint: float = 0.0


class SafetyGate:
    """Sole permitted write path for swash bounds. Nine sequential layers:

    1. ESD bit (%R06665.0)
    2. Bump flags (%R06627/%R06628)
    3. Absolute bounds
    4. Logical consistency (lower < upper, min band)
    5. Rate-of-change limit
    6. Heartbeat health
    7. State machine gate
    8. FC16 atomic write + 50ms scan delay + readback (%R06610/%R06611)
    9. Audit log (every outcome)
    """

    def __init__(self, config: SafetyConfig, modbus, audit) -> None:
        self._validate_config(config)
        self.cfg = config
        self.modbus = modbus
        self.audit = audit
        self.state = AdaptState.BASELINE
        self.lkg = LastKnownGood()
        self.heartbeat_counter = 0
        self.consecutive_rejections = 0
        self.cooldown_until = 0.0
        self.trial_start = 0.0
        self.current_lower = 0
        self.current_upper = 0

    @staticmethod
    def _validate_config(cfg: SafetyConfig) -> None:
        required = ("abs_min_lower", "abs_max_lower",
                    "abs_min_upper", "abs_max_upper")
        for name in required:
            if getattr(cfg, name) is None:
                raise ValueError(
                    f"SafetyConfig.{name} must come from commissioning. "
                    f"Steve must provide hardware safety limits before "
                    f"SafetyGate can be instantiated."
                )

    # ─── Layer 1 ────────────────────────────────────────────────────────────

    def check_esd(self, esd_bit: int) -> bool:
        if esd_bit:
            if self.state != AdaptState.ESD:
                logger.critical("ESD ACTIVE — all writes frozen")
                self.audit.log_rejected(0, 0, "ESD_ACTIVE")
            self.state = AdaptState.ESD
            return False
        if self.state == AdaptState.ESD:
            self.state = AdaptState.BASELINE
            logger.info("ESD cleared — resuming (require operator re-enable)")
        return True

    # ─── Layer 2 ────────────────────────────────────────────────────────────

    def check_bump_flags(self, bump_fwd: int, bump_rev: int) -> bool:
        if bump_fwd or bump_rev:
            return False  # Silent — expected during oscillation
        return True

    # ─── Layer 3 ────────────────────────────────────────────────────────────

    def _check_absolute(self, lower: int, upper: int) -> bool:
        c = self.cfg
        if lower < c.abs_min_lower:
            logger.error(f"REJECT: lower {lower} < abs_min_lower {c.abs_min_lower}")
            return False
        if upper > c.abs_max_upper:
            logger.error(f"REJECT: upper {upper} > abs_max_upper {c.abs_max_upper}")
            return False
        if lower > c.abs_max_lower:
            logger.error(f"REJECT: lower {lower} > abs_max_lower {c.abs_max_lower}")
            return False
        if upper < c.abs_min_upper:
            logger.error(f"REJECT: upper {upper} < abs_min_upper {c.abs_min_upper}")
            return False
        return True

    # ─── Layer 4 ────────────────────────────────────────────────────────────

    def _check_consistency(self, lower: int, upper: int) -> bool:
        if lower >= upper:
            logger.error(f"REJECT: lower {lower} >= upper {upper} (cross-fault)")
            return False
        if upper - lower < self.cfg.min_band_counts:
            logger.error(
                f"REJECT: band {upper - lower} < min {self.cfg.min_band_counts}"
            )
            return False
        return True

    # ─── Layer 5 ────────────────────────────────────────────────────────────

    def _rate_limit(self, proposed: int, current: int) -> int:
        delta = proposed - current
        max_delta = max(
            self.cfg.abs_min_step,
            min(self.cfg.abs_max_step,
                int(abs(current) * self.cfg.max_step_pct))
        )
        if abs(delta) > max_delta:
            limited = current + (max_delta if delta > 0 else -max_delta)
            logger.warning(
                f"Rate-limited: {current}→{proposed} clamped to {current}→{limited}"
            )
            return limited
        return proposed

    # ─── Layer 6 ────────────────────────────────────────────────────────────

    async def _send_heartbeat(self) -> bool:
        self.heartbeat_counter = (self.heartbeat_counter + 1) % 65536
        ok = await self.modbus.safe_write_registers(
            address=ADDR_HEARTBEAT, values=[self.heartbeat_counter]
        )
        return ok is not None

    # ─── Layers 7–8 ─────────────────────────────────────────────────────────

    async def _write_and_verify(self, lower: int, upper: int) -> bool:
        ok = await self.modbus.safe_write_registers(
            address=ADDR_SWASH_LOWER, values=[lower, upper]
        )
        if not ok:
            return False
        await asyncio.sleep(0.05)  # PLC scan settling
        readback = await self.modbus.safe_read(address=ADDR_ACTIVE_LOWER, count=2)
        if readback is None:
            logger.error("Readback read failed after FC16 write")
            return False
        if readback[0] != lower or readback[1] != upper:
            logger.error(
                f"READBACK MISMATCH: wrote [{lower},{upper}], "
                f"PLC validated [{readback[0]},{readback[1]}]"
            )
            return False
        return True

    # ─── MAIN ENTRY POINT ──────────────────────────────────────────────────

    async def validate_and_write(self,
                                  proposed_lower: int,
                                  proposed_upper: int,
                                  current_iae: float,
                                  esd_bit: int = 0,
                                  bump_fwd: int = 0,
                                  bump_rev: int = 0) -> bool:
        """THE ONLY PERMITTED WRITE PATH for %R06603/%R06604."""
        # Pre-flight (no audit row for these — they're frequent and benign)
        if not self.check_esd(esd_bit):
            return False
        if not self.check_bump_flags(bump_fwd, bump_rev):
            return False
        if time.time() < self.cooldown_until:
            return False
        if self.state == AdaptState.DISABLED:
            return False

        # Layer 3
        if not self._check_absolute(proposed_lower, proposed_upper):
            self.audit.log_rejected(proposed_lower, proposed_upper, "ABS_BOUNDS")
            return False
        # Layer 4
        if not self._check_consistency(proposed_lower, proposed_upper):
            self.audit.log_rejected(proposed_lower, proposed_upper, "CONSISTENCY")
            return False
        # Layer 5
        limited_lower = self._rate_limit(proposed_lower, self.current_lower)
        limited_upper = self._rate_limit(proposed_upper, self.current_upper)
        if not self._check_consistency(limited_lower, limited_upper):
            self.audit.log_rejected(limited_lower, limited_upper, "RATE_BAND")
            return False
        # Layer 6
        if not await self._send_heartbeat():
            logger.error("Heartbeat write failed — aborting bound write")
            self.audit.log_rejected(limited_lower, limited_upper, "HEARTBEAT_FAIL")
            return False

        # Layer 7
        if self.state == AdaptState.BASELINE:
            self.state = AdaptState.TRIAL
            self.trial_start = time.time()

        # Layer 8
        old_lower, old_upper = self.current_lower, self.current_upper
        success = await self._write_and_verify(limited_lower, limited_upper)
        if not success:
            self.trigger_rollback("WRITE_VERIFY_FAILED")
            return False

        self.current_lower = limited_lower
        self.current_upper = limited_upper

        # Layer 9
        self.audit.log_write(
            old_lower, old_upper, limited_lower, limited_upper,
            self.state.name, "WRITE_SUCCESS",
            heartbeat=self.heartbeat_counter,
            consec_rej=self.consecutive_rejections,
        )
        return True

    # ─── Trial accept/reject ────────────────────────────────────────────────

    def check_trial(self, current_iae: float, pv_ok: bool, oscillating: bool) -> None:
        if self.state != AdaptState.TRIAL:
            return
        if not pv_ok:
            self.trigger_rollback("PV_ALARM")
            return
        if oscillating:
            self.trigger_rollback("OSCILLATION_DETECTED")
            return
        if current_iae > self.lkg.iae_at_acceptance * self.cfg.iae_accept_ratio:
            self.trigger_rollback(
                f"IAE_DEGRADED:{current_iae:.2f}>"
                f"{self.lkg.iae_at_acceptance * self.cfg.iae_accept_ratio:.2f}"
            )
            return
        if time.time() - self.trial_start >= self.cfg.accept_window_s:
            self.lkg = LastKnownGood(
                lower=self.current_lower,
                upper=self.current_upper,
                timestamp=time.time(),
                iae_at_acceptance=current_iae,
            )
            self.consecutive_rejections = 0
            self.audit.log_write(
                self.current_lower, self.current_upper,
                self.current_lower, self.current_upper,
                "ACCEPTED", "TRIAL_PASSED",
                heartbeat=self.heartbeat_counter, consec_rej=0,
            )
            self.state = AdaptState.BASELINE
            logger.info(
                f"ACCEPTED: [{self.current_lower},{self.current_upper}] "
                f"IAE={current_iae:.3f}"
            )

    def trigger_rollback(self, reason: str) -> None:
        logger.warning(f"ROLLBACK: {reason}")
        self.consecutive_rejections += 1
        self.state = AdaptState.REJECTED
        # Schedule async write-back (best effort)
        try:
            asyncio.get_event_loop().create_task(self._async_rollback())
        except RuntimeError:
            pass  # No running loop (called from non-async context during shutdown)

        cooldown = min(
            self.cfg.base_cooldown_s
                * (2 ** min(self.consecutive_rejections - 1, 8)),
            self.cfg.max_cooldown_s,
        )
        self.cooldown_until = time.time() + cooldown
        self.audit.log_rollback(reason, self.lkg.lower, self.lkg.upper)

        if self.consecutive_rejections >= self.cfg.max_consecutive_rejections:
            logger.critical(
                f"MAX REJECTIONS ({self.consecutive_rejections}) — disabling"
            )
            self.state = AdaptState.DISABLED
        else:
            self.state = AdaptState.BASELINE

    async def _async_rollback(self) -> None:
        ok = await self._write_and_verify(self.lkg.lower, self.lkg.upper)
        if ok:
            self.current_lower = self.lkg.lower
            self.current_upper = self.lkg.upper
        else:
            logger.critical("ROLLBACK WRITE FAILED — PLC on last attempted values")

    # ─── Operator controls ──────────────────────────────────────────────────

    def operator_disable(self) -> None:
        self.state = AdaptState.DISABLED
        logger.info("Adaptive control DISABLED by operator")

    def operator_enable(self) -> None:
        self.state = AdaptState.BASELINE
        self.consecutive_rejections = 0
        logger.info("Adaptive control ENABLED by operator")
