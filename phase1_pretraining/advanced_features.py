"""
Advanced Feature Engineering (Session 6)
==========================================
Phase-segmented features, wavelet packet energy, and CWT scalograms
for pushing past F1=0.80 to the 0.85+ target.

Features:
  1. Phase-segmented statistics (per-phase torque/RPM/hookload stats)
  2. Wavelet packet energy bands (frequency decomposition)
  3. CWT scalogram generation (for 2D CNN alternative)
  4. Spectral kurtosis (impulsive content detection)

Usage:
  These features are optional add-ons to the 19-channel pipeline.
  They can be appended as additional channels or used as tabular features
  in an XGBoost/SVM classifier on top of InceptionTime embeddings.
"""
import numpy as np
import logging

logger = logging.getLogger(__name__)

# Phase mapping (must match features.py)
PHASE_NAMES = ['idle', 'stab', 'spin_in', 'shoulder', 'power_tight', 'hold']


def compute_phase_segmented_features(torque, rpm, turns, hookload, phase_ids):
    """Extract per-phase statistics that capture fault signatures.

    Key insight: misaligned_stab has anomalous STAB PHASE behavior
    (torque irregularity, hookload variation during first 1-2 seconds).
    Whole-window features dilute this signal.

    Args:
        torque: 1D array [N]
        rpm: 1D array [N]
        turns: 1D array [N]
        hookload: 1D array [N]
        phase_ids: 1D int array [N] with values 0-5

    Returns:
        dict of scalar features: {phase_name}_{stat_name} -> float
        Total: 6 phases x 6 stats = 36 features
    """
    features = {}

    for phase_id, phase_name in enumerate(PHASE_NAMES):
        mask = phase_ids == phase_id
        count = mask.sum()

        if count < 5:
            features[f'{phase_name}_torque_mean'] = 0.0
            features[f'{phase_name}_torque_std'] = 0.0
            features[f'{phase_name}_torque_kurtosis'] = 0.0
            features[f'{phase_name}_rpm_cv'] = 0.0
            features[f'{phase_name}_hookload_range'] = 0.0
            features[f'{phase_name}_duration_frac'] = 0.0
            continue

        t = torque[mask]
        r = rpm[mask]
        h = hookload[mask]

        features[f'{phase_name}_torque_mean'] = float(np.mean(t))
        features[f'{phase_name}_torque_std'] = float(np.std(t))

        # Kurtosis: high = impulsive events (galling spikes, cross-thread jumps)
        t_std = np.std(t)
        if t_std > 1e-8:
            features[f'{phase_name}_torque_kurtosis'] = float(
                np.mean(((t - np.mean(t)) / t_std) ** 4) - 3.0)
        else:
            features[f'{phase_name}_torque_kurtosis'] = 0.0

        # RPM coefficient of variation: high = unstable (stick-slip)
        r_mean = np.mean(r)
        features[f'{phase_name}_rpm_cv'] = float(np.std(r) / (abs(r_mean) + 1e-8))

        # Hookload range: high during misalignment
        features[f'{phase_name}_hookload_range'] = float(np.max(h) - np.min(h))

        # Duration as fraction of total window
        features[f'{phase_name}_duration_frac'] = float(count / len(phase_ids))

    return features


def compute_wavelet_features(torque, wavelet='db5', max_level=4):
    """Wavelet packet decomposition energy features for torque channel.

    Level 4 with db5 at 100Hz produces energy in bands:
    - 25-50Hz: high-frequency transients (galling impacts)
    - 12.5-25Hz: vibration patterns (cross-threading)
    - 6.25-12.5Hz: stick-slip oscillations
    - 0-6.25Hz: trend changes (shoulder detection, torque ramp)

    Returns dict of energy values per subband.
    """
    try:
        import pywt
    except ImportError:
        logger.warning("pywt not installed. Install with: pip install PyWavelets")
        return {}

    wp = pywt.WaveletPacket(data=torque, wavelet=wavelet, maxlevel=max_level)

    energies = {}
    for node in wp.get_level(max_level, order='natural'):
        band_data = node.data
        energy = float(np.sum(band_data ** 2))
        energies[f'wp_{node.path}'] = energy

    # Spectral kurtosis of torque (high = impulsive content)
    t_mean = np.mean(torque)
    t_std = np.std(torque)
    if t_std > 1e-8:
        energies['spectral_kurtosis'] = float(
            np.mean(((torque - t_mean) / t_std) ** 4) - 3.0)
    else:
        energies['spectral_kurtosis'] = 0.0

    return energies


def compute_cwt_scalogram(signal, scales=None, wavelet='morl',
                           sample_rate=100, target_size=(128, 128)):
    """Convert 1D time series to 2D scalogram image.

    Published results: CWT + ResNet-18 achieved 99.88% on CWRU bearing faults.

    Args:
        signal: 1D array
        scales: wavelet scales (default: 1-128)
        wavelet: mother wavelet (default: Morlet)
        sample_rate: Hz
        target_size: output image size

    Returns:
        2D float32 array of shape target_size
    """
    try:
        import pywt
    except ImportError:
        logger.warning("pywt not installed for CWT scalograms")
        return np.zeros(target_size, dtype=np.float32)

    if scales is None:
        scales = np.arange(1, 129)

    coefficients, _ = pywt.cwt(signal, scales, wavelet, sampling_period=1.0/sample_rate)
    scalogram = np.abs(coefficients)

    # Resize to target size
    if scalogram.shape != target_size:
        try:
            from scipy.ndimage import zoom
            zoom_factors = (target_size[0] / scalogram.shape[0],
                           target_size[1] / scalogram.shape[1])
            scalogram = zoom(scalogram, zoom_factors, order=1)
        except ImportError:
            # Simple resize via slicing/padding
            scalogram = scalogram[:target_size[0], :target_size[1]]

    return scalogram.astype(np.float32)


def compute_advanced_scalar_features(torque, rpm, turns, hookload, phase_ids,
                                      sample_rate=100):
    """Compute all advanced scalar features for a single window.

    Combines phase-segmented + wavelet features into a single feature dict.
    These can be used as:
      1. Additional channels (broadcast as constants like torque_trend_q4)
      2. Tabular features for XGBoost/SVM on top of InceptionTime embeddings

    Returns:
        dict of feature_name -> float value
    """
    features = {}

    # Phase-segmented features (36 features)
    phase_feats = compute_phase_segmented_features(
        torque, rpm, turns, hookload, phase_ids)
    features.update(phase_feats)

    # Wavelet features
    wavelet_feats = compute_wavelet_features(torque)
    features.update(wavelet_feats)

    return features


def extract_advanced_features_dataset(windows, connection_states=None, sample_rate=100):
    """Extract advanced scalar features for an entire dataset.

    Args:
        windows: shape [N, T, C] where C >= 6 (raw channels)
        connection_states: shape [N, T] int array of phase IDs (optional)
        sample_rate: Hz

    Returns:
        feature_matrix: shape [N, n_features] numpy array
        feature_names: list of feature name strings
    """
    n_samples = windows.shape[0]

    # First pass: compute features for first sample to get feature names
    torque = windows[0, :, 0]
    rpm = windows[0, :, 1]
    turns = windows[0, :, 4]
    hookload = windows[0, :, 5]

    # If connection_states not provided, try to extract from phase one-hot
    # channels (indices 13-18 in the 19-channel pipeline)
    if connection_states is not None:
        phase_ids = connection_states[0]
    elif windows.shape[2] >= 19:
        # Reconstruct phase from one-hot: argmax of channels 13-18
        phase_one_hot = windows[0, :, 13:19]
        phase_ids = np.argmax(phase_one_hot, axis=1)
    else:
        phase_ids = np.zeros(windows.shape[1], dtype=np.int32)

    sample_feats = compute_advanced_scalar_features(
        torque, rpm, turns, hookload, phase_ids, sample_rate)
    feature_names = sorted(sample_feats.keys())
    n_features = len(feature_names)

    logger.info(f"Extracting {n_features} advanced features for {n_samples} windows...")

    # Second pass: compute for all samples
    feature_matrix = np.zeros((n_samples, n_features), dtype=np.float32)

    for i in range(n_samples):
        torque = windows[i, :, 0]
        rpm = windows[i, :, 1]
        turns = windows[i, :, 4]
        hookload = windows[i, :, 5]

        if connection_states is not None:
            phase_ids = connection_states[i]
        elif windows.shape[2] >= 19:
            phase_one_hot = windows[i, :, 13:19]
            phase_ids = np.argmax(phase_one_hot, axis=1)
        else:
            phase_ids = np.zeros(windows.shape[1], dtype=np.int32)

        feats = compute_advanced_scalar_features(
            torque, rpm, turns, hookload, phase_ids, sample_rate)

        for j, name in enumerate(feature_names):
            feature_matrix[i, j] = feats.get(name, 0.0)

        if (i + 1) % 1000 == 0:
            logger.info(f"  Processed {i+1}/{n_samples} windows")

    return feature_matrix, feature_names


def train_xgboost_on_advanced_features(X_train, y_train, X_test, y_test,
                                        class_names):
    """Train XGBoost on advanced scalar features as a diagnostic baseline.

    Can also be used as a stacking classifier on top of InceptionTime embeddings.
    """
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.metrics import f1_score, classification_report
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        logger.warning("scikit-learn not available for XGBoost baseline")
        return None

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    clf = GradientBoostingClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.1,
        random_state=42, verbose=0)
    clf.fit(X_train_scaled, y_train)

    y_pred = clf.predict(X_test_scaled)
    macro_f1 = f1_score(y_test, y_pred, average='macro')

    print(f"\n{'='*60}")
    print(f"  Advanced Features + GradientBoosting Results")
    print(f"{'='*60}")
    print(f"  Test Macro F1: {macro_f1:.4f}")
    print(f"\n{classification_report(y_test, y_pred, target_names=class_names)}")

    return macro_f1
