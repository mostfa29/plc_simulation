"""
Feature Engineering: 19-Channel Pipeline (Session 2 Fix)
==========================================================
Computes 13 derived channels from 6 raw sensor channels.

Raw channels (from simulator sensor output):
   0: torque_ftlbs
   1: rpm
   2: pressure_psi
   3: oil_temp_f
   4: turns
   5: hookload_klbs

Fixed derived channels (2):
   6: d_torque_dt_savgol     - Savitzky-Golay torque derivative (NOT finite diff)
   7: d_torque_dturns_savgol  - Savitzky-Golay torque-turn slope with safe division

Domain features (5):
   8: torque_trend_q4         - Linear slope of torque in last 25% of window
   9: rpm_collapse_rate       - Max negative RPM derivative (seizure indicator)
  10: torque_oscillation_idx  - High-pass torque RMS (stick-slip / cross-thread)
  11: torque_rpm_ratio        - Effective mechanical resistance (torque / RPM)
  12: mechanical_power        - Power = torque x RPM x 2pi/60

Phase one-hot encoding (6 binary channels):
  13: phase_idle
  14: phase_stab
  15: phase_spin_in
  16: phase_shoulder
  17: phase_power_tight
  18: phase_hold

REMOVED (harmful channels from old pipeline):
  - torque_norm: destroys magnitude info (over_torque vs normal is magnitude)
  - turns_norm: same problem
  - phase (int): meaningless to CNNs — "phase 3 > phase 2" is not valid
  - mask: teaches model to use padding boundaries as features

Total: 19 channels input to the model.
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
CH_TORQUE_TREND_Q4 = 8
CH_RPM_COLLAPSE = 9
CH_TORQUE_OSC = 10
CH_TORQUE_RPM_RATIO = 11
CH_MECH_POWER = 12
CH_PHASE_IDLE = 13
CH_PHASE_STAB = 14
CH_PHASE_SPIN_IN = 15
CH_PHASE_SHOULDER = 16
CH_PHASE_POWER_TIGHT = 17
CH_PHASE_HOLD = 18

NUM_RAW_CHANNELS = 6
NUM_DERIVED_CHANNELS = 13
NUM_TOTAL_CHANNELS = 19

# Clamp range for d_torque_dturns to prevent outliers
SLOPE_CLIP_MIN = -1e4
SLOPE_CLIP_MAX = 1e4

# Phase mapping: ConnectionState enum int -> simplified phase 0-5
# 0=idle, 1=stab/spin_in, 2=shoulder, 3=power_tight/hold, 4=complete, 5=fault
_STATE_TO_PHASE = np.zeros(14, dtype=np.int32)
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

# Number of discrete phases for one-hot encoding
NUM_PHASES = 6


def compute_d_torque_dt(torque: np.ndarray, dt: float = 0.01) -> np.ndarray:
    """Torque rate of change using Savitzky-Golay differentiation.

    Savitzky-Golay fits a local polynomial and differentiates analytically,
    avoiding the 100x noise amplification of finite differences at 100Hz.

    Args:
        torque: 1D array of torque values [N].
        dt: Timestep in seconds (default 0.01 = 100Hz).

    Returns:
        1D array [N] of torque rate (ft-lbs/sec).
    """
    n = len(torque)
    if n < 5:
        return np.zeros(n, dtype=np.float32)

    try:
        from scipy.signal import savgol_filter
        # window_length=31 (0.31s at 100Hz): smooths noise, preserves transients
        win_len = min(31, n if n % 2 == 1 else n - 1)
        if win_len < 5:
            win_len = 5
        return savgol_filter(torque, window_length=win_len, polyorder=3,
                             deriv=1, delta=dt).astype(np.float32)
    except ImportError:
        # Fallback: central difference (noisy but functional)
        k = 2
        result = np.zeros(n, dtype=np.float32)
        if n <= 2 * k:
            return result
        denominator = 2.0 * k * dt
        result[k:n-k] = (torque[2*k:] - torque[:n-2*k]) / denominator
        result[:k] = (torque[k:2*k] - torque[:k]) / (k * dt)
        result[n-k:] = (torque[n-k:] - torque[n-2*k:n-k]) / (k * dt)
        return result


def compute_d_torque_dturns(torque: np.ndarray, turns: np.ndarray,
                             dt: float = 0.01) -> np.ndarray:
    """Torque-turn slope using Savitzky-Golay with safe division.

    Computes d_torque/dt and d_turns/dt independently via SavGol,
    then divides with epsilon guard. This is much more robust than
    the old finite-difference approach.

    Args:
        torque: 1D array [N].
        turns: 1D array [N].
        dt: Timestep in seconds.

    Returns:
        1D array [N], clipped to [-1e4, 1e4].
    """
    n = len(torque)
    if n < 5:
        return np.zeros(n, dtype=np.float32)

    try:
        from scipy.signal import savgol_filter
        win_len = min(31, n if n % 2 == 1 else n - 1)
        if win_len < 5:
            win_len = 5
        d_torque = savgol_filter(torque, window_length=win_len, polyorder=3,
                                  deriv=1, delta=dt).astype(np.float32)
        d_turns = savgol_filter(turns, window_length=win_len, polyorder=3,
                                 deriv=1, delta=dt).astype(np.float32)
    except ImportError:
        # Fallback: finite differences
        d_torque = np.gradient(torque, dt).astype(np.float32)
        d_turns = np.gradient(turns, dt).astype(np.float32)

    eps = 1e-6
    d_turns_safe = np.where(np.abs(d_turns) < eps, eps, d_turns)
    result = d_torque / d_turns_safe

    return np.clip(result, SLOPE_CLIP_MIN, SLOPE_CLIP_MAX).astype(np.float32)


def compute_torque_trend_q4(torque: np.ndarray, window_size: int) -> np.ndarray:
    """Linear slope of torque in the final 25% of the window.

    Physics:
    - Over-torque: POSITIVE slope (torque still rising past target)
    - Stripped thread: NEGATIVE slope (torque drops as threads fail)
    - Galling: ERRATIC / near-zero (torque spikes then stalls from seizure)
    - Normal: NEAR-ZERO (torque holds steady at target)

    Returns constant-value channel (same slope value broadcast across window).
    """
    q4_start = int(window_size * 0.75)
    q4_torque = torque[q4_start:]
    if len(q4_torque) >= 2:
        x = np.arange(len(q4_torque))
        slope = np.polyfit(x, q4_torque, 1)[0]
    else:
        slope = 0.0
    return np.full(window_size, slope, dtype=np.float32)


def compute_rpm_collapse_rate(rpm: np.ndarray, dt: float = 0.01) -> np.ndarray:
    """Maximum rate of RPM decrease (most negative RPM derivative).

    Physics:
    - Galling: VERY HIGH collapse rate (motor seizes, RPM drops instantly)
    - Stall: HIGH collapse rate (motor overloaded, RPM drops to zero)
    - Cross-thread: MODERATE collapse rate (brief RPM dips during thread jumps)
    - Over/under/wrong_compound: NEAR-ZERO (RPM stays stable)

    Returns constant-value channel.
    """
    n = len(rpm)
    if n < 5:
        return np.zeros(n, dtype=np.float32)

    try:
        from scipy.signal import savgol_filter
        win_len = min(31, n if n % 2 == 1 else n - 1)
        if win_len < 5:
            win_len = 5
        d_rpm = savgol_filter(rpm, window_length=win_len, polyorder=3,
                               deriv=1, delta=dt).astype(np.float32)
    except ImportError:
        d_rpm = np.gradient(rpm, dt).astype(np.float32)

    min_d_rpm = float(np.min(d_rpm))
    return np.full(n, min_d_rpm, dtype=np.float32)


def compute_torque_oscillation_index(torque: np.ndarray,
                                      sample_rate: float = 100.0,
                                      cutoff_hz: float = 5.0) -> np.ndarray:
    """RMS of high-pass filtered torque signal.

    Physics:
    - Cross-thread: HIGH oscillation (erratic torque as threads skip)
    - Galling: HIGH oscillation (stick-slip cycles cause torque spikes)
    - Over-torque: LOW oscillation (smooth monotonic torque rise)
    - Normal: LOW oscillation (clean ramp to target)

    Returns constant-value channel.
    """
    n = len(torque)
    if n < 10:
        return np.zeros(n, dtype=np.float32)

    try:
        from scipy.signal import butter, filtfilt
        nyq = sample_rate / 2.0
        b, a = butter(4, cutoff_hz / nyq, btype='high')
        high_passed = filtfilt(b, a, torque)
        osc_rms = float(np.sqrt(np.mean(high_passed ** 2)))
    except ImportError:
        # Fallback: simple high-pass via difference from moving average
        kernel = int(sample_rate / cutoff_hz)
        if kernel < 3:
            kernel = 3
        smoothed = np.convolve(torque, np.ones(kernel) / kernel, mode='same')
        high_passed = torque - smoothed
        osc_rms = float(np.sqrt(np.mean(high_passed ** 2)))

    return np.full(n, osc_rms, dtype=np.float32)


def compute_torque_rpm_ratio(torque: np.ndarray, rpm: np.ndarray) -> np.ndarray:
    """Torque / RPM = effective mechanical resistance.

    Physics:
    - Galling: SPIKES (very high torque, very low RPM = seizure)
    - Normal: PROPORTIONAL (moderate ratio, stable)
    - Over-torque: MODERATELY HIGH (high torque, normal RPM)
    - Under-torque: LOW (low torque, normal RPM)
    """
    eps = 1e-3
    rpm_safe = np.where(np.abs(rpm) < eps, eps, rpm)
    ratio = torque / rpm_safe
    return np.clip(ratio, -1000, 1000).astype(np.float32)


def compute_mechanical_power(torque: np.ndarray, rpm: np.ndarray) -> np.ndarray:
    """Power = Torque x RPM x 2pi/60 (watts from ft-lbs and RPM).

    Physics:
    - Stall: DROPS TO ZERO (RPM = 0, regardless of torque)
    - Normal: SMOOTH RAMP then steady
    - Over-torque: HIGH AND RISING
    - Galling: ERRATIC (torque high but RPM collapsing)
    """
    return (torque * rpm * 2.0 * np.pi / 60.0).astype(np.float32)


def compute_phase_one_hot(connection_state: np.ndarray, n_phases: int = NUM_PHASES) -> np.ndarray:
    """Convert connection_state enum to one-hot phase encoding.

    Phase as categorical integer is meaningless to CNNs — convolution
    computes weighted sums, "phase 3 > phase 2" is not a meaningful
    relationship. One-hot encoding: each phase becomes its own binary channel.

    Args:
        connection_state: 1D int array [N] of ConnectionState values.
        n_phases: Number of discrete phases (default 6).

    Returns:
        2D float32 array [n_phases, N] — one binary channel per phase.
    """
    clipped = np.clip(connection_state.astype(np.int32), 0, len(_STATE_TO_PHASE) - 1)
    phase_ids = _STATE_TO_PHASE[clipped]

    one_hot = np.zeros((n_phases, len(connection_state)), dtype=np.float32)
    for p in range(n_phases):
        one_hot[p] = (phase_ids == p).astype(np.float32)
    return one_hot


def compute_derived_channels(raw: np.ndarray,
                              connection_state: np.ndarray,
                              target_torque: float,
                              expected_turns: float,
                              dt: float = 0.01) -> np.ndarray:
    """Compute all 13 derived channels and append to raw channels.

    New 19-channel pipeline (Session 2 fix):
    - 6 raw channels (unchanged)
    - 2 fixed derivatives (Savitzky-Golay)
    - 5 domain features (physics-based)
    - 6 phase one-hot channels

    Args:
        raw: Array [N, 6] of raw sensor channels.
        connection_state: 1D int array [N] from sensor data.
        target_torque: Optimum makeup torque for this pipe (ft-lbs). [unused now]
        expected_turns: Expected turns-to-shoulder for this pipe. [unused now]
        dt: Timestep (seconds).

    Returns:
        Array [N, 19] with raw + derived channels.
    """
    n = raw.shape[0]
    torque = raw[:, CH_TORQUE]
    rpm = raw[:, CH_RPM]
    turns = raw[:, CH_TURNS]
    sample_rate = 1.0 / dt

    # === Fixed derived channels (2) ===
    d_torque_dt = compute_d_torque_dt(torque, dt=dt)
    d_torque_dturns = compute_d_torque_dturns(torque, turns, dt=dt)

    # === Domain features (5) ===
    torque_trend_q4 = compute_torque_trend_q4(torque, n)
    rpm_collapse = compute_rpm_collapse_rate(rpm, dt=dt)
    torque_osc = compute_torque_oscillation_index(torque, sample_rate=sample_rate)
    torque_rpm_ratio = compute_torque_rpm_ratio(torque, rpm)
    mech_power = compute_mechanical_power(torque, rpm)

    # === Phase one-hot (6 binary channels) ===
    phase_one_hot = compute_phase_one_hot(connection_state)  # [6, N]

    # Stack all 19 channels: [N, 19]
    derived = np.stack([
        d_torque_dt,        # 6
        d_torque_dturns,    # 7
        torque_trend_q4,    # 8
        rpm_collapse,       # 9
        torque_osc,         # 10
        torque_rpm_ratio,   # 11
        mech_power,         # 12
    ], axis=1)

    # Phase one-hot: transpose from [6, N] to [N, 6]
    phase_channels = phase_one_hot.T  # [N, 6]

    return np.concatenate([
        raw.astype(np.float32),  # [N, 6]
        derived,                  # [N, 7]
        phase_channels,           # [N, 6]
    ], axis=1)  # [N, 19]
