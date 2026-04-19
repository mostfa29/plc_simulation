"""Adaptive control + safety layer.

Contains:
    safety_gate          9-layer SafetyGate — the sole write path to the PLC
    pid_advisor          Gain-scheduled bounds + sign-based integral trim
    oscillation_tuner    Bump-angle advisor with resonance exclusion
"""
