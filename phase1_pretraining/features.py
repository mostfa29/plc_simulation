"""
Feature Engineering: Derived Channels
=======================================
Computes 6 derived channels from 6 raw sensor channels.

Raw channels (from simulator sensor output):
  0: torque_ftlbs
  1: rpm
  2: pressure_psi
  3: oil_temp_f
  4: turns
  5: hookload_klbs

Derived channels (computed here):
  6: d_torque_dt         - Torque rate of change (ft-lbs/sec)
  7: d_torque_dturns     - Torque-turn slope (ft-lbs/turn)
  8: torque_norm         - Torque / target_torque (0-1+)
  9: turns_norm          - Turns / expected_turns (0-1+)
 10: phase               - Connection phase indicator (0-5)
 11: mask                - Validity mask (1=real, 0=padded)

Total: 12 channels input to the model.
"""
import numpy as np


# Channel indices in raw array
CH_TORQUE = 0
CH_RPM = 1
CH_PRESSURE = 2
CH_OIL_TEMP = 3
CH_TURNS = 4
CH_HOOKLOAD = 5

# Derived channel indices (appended after raw)
CH_D_TORQUE_DT = 6
CH_D_TORQUE_DTURNS = 7
CH_TORQUE_NORM = 8
CH_TURNS_NORM = 9
CH_PHASE = 10
CH_MASK = 11

NUM_RAW_CHANNELS = 6
NUM_DERIVED_CHANNELS = 6
NUM_TOTAL_CHANNELS = 12

# Clamp range for d_torque_dturns to prevent outliers
SLOPE_CLIP_MIN = -1e4
SLOPE_CLIP_MAX = 1e4

# Phase mapping: ConnectionState enum int -> simplified phase 0-5
_STATE_TO_PHASE = np.zeros(14, dtype=np.float32)
_STATE_TO_PHASE[0] = 0   # IDLE
_STATE_TO_PHASE[1] = 0   # APPROACH
_STATE_TO_PHASE[2] = 1   # SPIN_IN
_STATE_TO_PHASE[3] = 2   # SHOULDER
_STATE_TO_PHASE[4] = 3   # POWER_TIGHT
_STATE_TO_PHASE[5] = 3   # HOLD
_STATE_TO_PHASE[6] = 1   # BREAKOUT (reverse)
_STATE_TO_PHASE[7] = 1   # BACKOFF (reverse)
_STATE_TO_PHASE[8] = 5   # FAULT
_STATE_TO_PHASE[9] = 4   # COMPLETE
_STATE_TO_PHASE[10] = 5  # STALL
_STATE_TO_PHASE[11] = 5  # E_STOP
_STATE_TO_PHASE[12] = 5  # FAULT_RECOVERY
_STATE_TO_PHASE[13] = 1  # HANDOFF


def compute_d_torque_dt(torque: np.ndarray, dt: float = 0.01, k: int = 2) -> np.ndarray:
    """Central difference torque rate: (T[i+k] - T[i-k]) / (2k * dt).

    Vectorized numpy implementation.

    Args:
        torque: 1D array of torque values [N].
        dt: Timestep in seconds (default 0.01 = 100Hz).
        k: Half-window for central difference.

    Returns:
        1D array [N] of torque rate (ft-lbs/sec).
    """
    n = len(torque)
    result = np.zeros(n, dtype=np.float32)

    if n <= 2 * k:
        return result

    # Central difference for interior points
    denominator = 2.0 * k * dt
    result[k:n-k] = (torque[2*k:] - torque[:n-2*k]) / denominator

    # Forward difference for leading edge
    result[:k] = (torque[k:2*k] - torque[:k]) / (k * dt)

    # Backward difference for trailing edge
    result[n-k:] = (torque[n-k:] - torque[n-2*k:n-k]) / (k * dt)

    return result


def compute_d_torque_dturns(torque: np.ndarray, turns: np.ndarray,
                             k: int = 5, eps: float = 1e-6) -> np.ndarray:
    """Torque-turn slope: (T[i+k] - T[i-k]) / (turns[i+k] - turns[i-k]).

    THE diagnostic feature per Section 3.2:
      - Galling: progressive slope increase
      - Stripped thread: slope plateau/drop
      - Cross-thread: extreme slope at low turns

    Vectorized numpy implementation with zero-division protection.

    Args:
        torque: 1D array [N].
        turns: 1D array [N].
        k: Half-window (default 5 = 0.05 turns at typical RPM).
        eps: Epsilon for zero-division protection.

    Returns:
        1D array [N], clipped to [-1e4, 1e4].
    """
    n = len(torque)
    result = np.zeros(n, dtype=np.float32)

    if n <= 2 * k:
        return result

    d_torque = torque[2*k:] - torque[:n-2*k]
    d_turns = turns[2*k:] - turns[:n-2*k]

    # Safe division: only divide where d_turns is nonzero
    safe_mask = np.abs(d_turns) > eps
    result[k:n-k] = np.where(safe_mask, d_torque / np.where(safe_mask, d_turns, 1.0), 0.0)

    return np.clip(result, SLOPE_CLIP_MIN, SLOPE_CLIP_MAX)


def compute_phase_indicator(connection_state: np.ndarray) -> np.ndarray:
    """Map connection_state enum to simplified phase indicator (0-5).

    Vectorized lookup table implementation.

    Args:
        connection_state: 1D int array [N] of ConnectionState values.

    Returns:
        1D float32 array [N] with phase 0-5.
    """
    # Clip to valid range and use lookup table
    clipped = np.clip(connection_state.astype(np.int32), 0, len(_STATE_TO_PHASE) - 1)
    return _STATE_TO_PHASE[clipped]


def compute_derived_channels(raw: np.ndarray,
                              connection_state: np.ndarray,
                              target_torque: float,
                              expected_turns: float,
                              dt: float = 0.01) -> np.ndarray:
    """Compute all 6 derived channels and append to raw channels.

    Args:
        raw: Array [N, 6] of raw sensor channels.
        connection_state: 1D int array [N] from sensor data.
        target_torque: Optimum makeup torque for this pipe (ft-lbs).
        expected_turns: Expected turns-to-shoulder for this pipe.
        dt: Timestep (seconds).

    Returns:
        Array [N, 12] with raw + derived channels.
    """
    n = raw.shape[0]
    torque = raw[:, CH_TORQUE]
    turns = raw[:, CH_TURNS]

    # Derived channels (all vectorized)
    d_torque_dt = compute_d_torque_dt(torque, dt=dt)
    d_torque_dturns = compute_d_torque_dturns(torque, turns)
    torque_norm = torque / max(target_torque, 1.0)
    turns_norm = turns / max(expected_turns, 0.1)
    phase = compute_phase_indicator(connection_state)
    mask = np.ones(n, dtype=np.float32)  # All real data; padding sets to 0 later

    # Stack all 12 channels
    derived = np.stack([
        d_torque_dt,
        d_torque_dturns,
        torque_norm.astype(np.float32),
        turns_norm.astype(np.float32),
        phase,
        mask,
    ], axis=1)

    return np.concatenate([raw.astype(np.float32), derived], axis=1)
