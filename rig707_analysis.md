# Precision Rig 707 3pd HT — Register Analysis
**Date:** 2026-03-02 17:58
**PLC IP:** 129.168.1.25:502 (via eWON VPN)
**Method:** 5 snapshots, 3s apart, scanned R0-6100
**Total non-zero registers:** 504

## CONFIRMED REGISTER MAP (HXI Template — matches Rig 709)

All HXI template registers are PRESENT and reading sane values:

| Register | Name | Type | Value | Changing? | Notes |
|----------|------|------|-------|-----------|-------|
| **R163-164** | **Torque** | FLOAT32 GE | **18,144 ft-lbs** | YES (range=12288 raw) | Active torque. Fluctuating = live analog. |
| **R165-166** | **Turns** | FLOAT32 GE | **0.567 turns** | YES (range=61342 raw) | Active turns count. |
| **R167-168** | **Temperature** | FLOAT32 GE | **164.4 °F** | YES (range=14253 raw) | Oil temp. Reasonable for active rig. |
| **R169-170** | **RPM** | FLOAT32 GE | **124.4 RPM** | YES (range=28506 raw) | **Steve: Is this correct during hold?** |
| **R175** | **Connection State** | INT16 | **5 (HOLD)** | NO (constant) | PLC says HOLD phase. |
| **R178** | **Target Torque** | INT16 | **17,297 ft-lbs** | NO (constant) | Makeup target. |
| **R180** | **Shoulder Torque** | INT16 | **16,928 ft-lbs** | NO (constant) | Shoulder detect threshold. |
| **R185** | **Connection Count** | INT16 | **20** | NO (constant) | 20 connections made so far. |
| **R189-190** | **Hookload** | FLOAT32 GE | **19,376 lbs** (19.4 klbs) | YES (range=36864 raw) | Reasonable for casing. |
| **R2076-2077** | **Encoder** | INT32 | **~1,048,799** (16×65536+6223) | YES (incrementing) | Encoder counts. MSW=16, LSW varies. |

## KEY QUESTION FOR STEVE

**RPM = 124 during HOLD (R175=5).** Is the rig actually:
- (a) Spinning at 124 RPM while holding torque? (rotary hold / 3-speed mode?)
- (b) Idle and R169 is NOT rpm on the 707?
- (c) In a different phase than "HOLD"? (Maybe state=5 means something else on this PLC?)

The torque (18,144) is above target (17,297) which is consistent with hold/power-tight. But 124 RPM during hold is unusual — normally hold means near-zero RPM.

## OTHER INTERESTING CHANGING REGISTERS

These are live process values NOT in the HXI template — could be useful:

| Register | Raw Value | GE Float32 | Behavior | Likely Meaning |
|----------|-----------|------------|----------|----------------|
| R140-141 | 57344/17539 | 1,055.0 | Changing (range=49152) | **Pressure?** (PSI) |
| R157 | 46 | — | Changing (range=1) | Mirror of connection count? Or small sensor |
| R158-159 | 7360/14720 | 0.0002 | Changing | PID term? Control output? |
| R162 | 18144 | -2.0 (GE) | Changing (range=24) | INT16 mirror of torque? (18144 ≈ 18144 ft-lbs) |
| R181 | 46 | — | Changing (range=1) | Same as R157 — possibly turns×80? |
| R187 | 6621 | — | Changing (range=595) | **PID output?** Oscillating control variable |
| R192 | 17747 | — | Changing (range=12) | Torque setpoint variant? |
| R195-196 | 4194/15832 | 0.106 | Changing | Tiny float — calibration offset? |
| R256-263 | varies | 335k-771k | Changing | **Cumulative counters?** Large incrementing values |
| R266-267 | varies | 9,414 | Changing | **Peak torque history?** |
| R270-271 | varies | 7,531 | Changing | **Previous connection torque?** |
| R431-440 | varies | 19,832 / 0.62 / 179.7 / 139.7 | Changing | **Duplicate process block?** torque/turns/rpm copies |
| R502-512 | varies | 8,090 / 0.25 / 0.22 | Changing | **Another process block?** Different time window? |
| R607-608 | varies | 8,234 | Changing | More torque history |
| R2070-2078 | varies | — | Changing | Encoder/counter area |

## NOTABLE STATIC REGISTERS (Configuration)

| Register | Value | Likely Meaning |
|----------|-------|----------------|
| R0 | 4 | PLC config — number of I/O modules? |
| R14 | 7,500 | Spin-in RPM limit? Or max speed setting |
| R130-131 | GE_f32=22.79 | Encoder CPR config? Or gear ratio |
| R145 | 12,700 | Under-torque alarm? (INT16) |
| R153 | 76 | Speed config? |
| R154 | 32,767 | Max INT16 — sentinel/disabled flag |
| R208 | 1,012 | PID parameter? |
| R297-301 | 80 | RPM limits table (multiple speeds) |
| R380 | GE_f32=(-40.0) | Temp offset / zero calibration |
| R392 | STD_f32=1,800 | Max torque capacity? |
| R417-418 | GE_f32=321.0 | Max temperature alarm? (321°F) |
| R498-499 | GE_f32=21,460 | Absolute torque limit? |
| R632-633 | GE_f32=28,000 | Emergency torque cutoff? |
| R642-643 | GE_f32=18,000 | Shoulder torque setpoint (config)? |
| R1271-1272 | GE_f32=18,000 | Duplicate of above |

## R431-440: POSSIBLE SECONDARY PROCESS BLOCK

This block looks like a mirror/copy of the main process data:

| Register | GE Float32 | Comparison |
|----------|------------|------------|
| R433-434 | 19,832 ft-lbs | vs R163: 18,144 (slightly different = different sample?) |
| R435-436 | 0.620 turns | vs R165: 0.567 (close) |
| R437-438 | 179.7 | **Higher than R167 temp (164.4)** — different sensor? |
| R439-440 | 139.7 | vs R169 RPM: 124.4 — **Different!** Could this be actual RPM? |

**Steve: Are R431-440 a secondary/diagnostic copy of process data? If R439=139.7 is "true RPM" and R169=124.4 is something else, that would explain the discrepancy.**

## SUMMARY

1. HXI register template is **CONFIRMED working** on Rig 707 — same addresses as Rig 709
2. Torque (18,144), turns (0.567), temperature (164.4°F), hookload (19.4 klbs) all look correct
3. **RPM (124.4 at R169) needs Steve verification** — is 124 RPM normal during HOLD on a 3-speed HT?
4. R140 (GE_f32=1,055) could be **pressure** — not currently captured
5. R431-440 block may have **duplicate/secondary process data** worth investigating
6. R6000-6100 (shop unit layout) is completely **empty** on this rig — confirms it uses the R160+ HXI layout
