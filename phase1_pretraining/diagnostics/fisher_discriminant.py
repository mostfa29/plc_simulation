"""
Fisher Discriminant Ratio (FDR) Analysis
==========================================
Computes per-channel separability between all class pairs.

FDR = (mu1 - mu2)^2 / (var1 + var2)

If FDR ~ 0 for ALL channels between two classes, NO model can separate them
from the current representation. This is the definitive test for whether
the problem is data vs. model.

Usage:
  cd phase1_pretraining
  python diagnostics/fisher_discriminant.py --config config_triage.yaml
  python diagnostics/fisher_discriminant.py --config config.yaml  # compare 12ch vs 6ch
"""
import argparse
import sys
import logging
from pathlib import Path
from itertools import combinations

import numpy as np
import yaml

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))
from dataset import prepare_datasets


def compute_fdr_matrix(windows, labels, num_classes, channel_names):
    """Compute Fisher Discriminant Ratio for all class pairs, all channels.

    Returns:
        fdr: [num_pairs, num_channels] array of FDR values
        pairs: list of (class_i, class_j) tuples
    """
    num_channels = windows.shape[-1]

    # Compute per-class statistics: mean and variance per channel
    # Windows are [N, T, C], compute stats over time dimension first
    # Use mean across time for each window, then stats across windows
    window_means = np.mean(windows, axis=1)  # [N, C]

    class_means = {}
    class_vars = {}
    class_counts = {}
    for c in range(num_classes):
        mask = labels == c
        if mask.sum() == 0:
            continue
        data = window_means[mask]  # [N_c, C]
        class_means[c] = np.mean(data, axis=0)
        class_vars[c] = np.var(data, axis=0) + 1e-10
        class_counts[c] = mask.sum()

    pairs = list(combinations(sorted(class_means.keys()), 2))
    fdr = np.zeros((len(pairs), num_channels))

    for idx, (ci, cj) in enumerate(pairs):
        mu_diff_sq = (class_means[ci] - class_means[cj]) ** 2
        var_sum = class_vars[ci] + class_vars[cj]
        fdr[idx] = mu_diff_sq / var_sum

    return fdr, pairs, class_means, class_vars, class_counts


def run_fdr_analysis(config_path: str):
    """Run Fisher Discriminant Ratio analysis."""
    config_path = Path(config_path)
    if not config_path.is_absolute():
        config_path = Path(__file__).parent.parent / config_path
    with open(config_path) as f:
        config = yaml.safe_load(f)

    dataset_dir = config['data']['dataset_dir']
    if not Path(dataset_dir).is_absolute():
        dataset_dir = str(Path(__file__).parent.parent / dataset_dir)

    channels_mode = config['data'].get('channels_mode', 'all')
    num_classes = config['model']['c_out']
    class_names = config.get('class_names', [f'class_{i}' for i in range(num_classes)])

    if channels_mode == 'raw_only':
        channel_names = ['torque', 'rpm', 'pressure', 'oil_temp', 'turns', 'hookload']
    else:
        channel_names = [
            'torque', 'rpm', 'pressure', 'oil_temp', 'turns', 'hookload',
            'd_torque_dt', 'd_torque_dturns',
            'torque_trend_q4', 'rpm_collapse', 'torque_osc',
            'torque_rpm_ratio', 'mech_power',
            'phase_idle', 'phase_stab', 'phase_spin_in',
            'phase_shoulder', 'phase_power_tight', 'phase_hold',
        ]

    print("=" * 70)
    print("  Fisher Discriminant Ratio Analysis")
    print("=" * 70)
    print(f"  Config: {config_path.name}")
    print(f"  Channels: {channels_mode} ({len(channel_names)} channels)")
    print()

    # Load training data only (FDR on training distribution)
    train_ds, _, _, _ = prepare_datasets(
        dataset_dir=dataset_dir,
        class_map=config['class_map'],
        window_size=config['data']['window_size'],
        stride=config['data']['window_stride'],
        split_ratio=tuple(config['data']['split_ratio']),
        split_seed=config['data'].get('split_seed', 42),
        channels_mode=channels_mode,
    )

    windows = train_ds.windows  # [N, T, C]
    labels = train_ds.labels

    print(f"  Windows: {windows.shape}")
    print(f"  Labels: {dict(zip(*np.unique(labels, return_counts=True)))}")

    fdr, pairs, class_means, class_vars, class_counts = compute_fdr_matrix(
        windows, labels, num_classes, channel_names)

    # 1. Overall channel discriminative power (mean FDR across all pairs)
    print(f"\n{'='*70}")
    print(f"  Channel Discriminative Power (mean FDR across all class pairs)")
    print(f"{'='*70}")
    mean_fdr_per_channel = np.mean(fdr, axis=0)
    sorted_ch = np.argsort(mean_fdr_per_channel)[::-1]
    for ch_idx in sorted_ch:
        bar = '#' * min(int(mean_fdr_per_channel[ch_idx] * 10), 50)
        print(f"  {channel_names[ch_idx]:<18s} FDR={mean_fdr_per_channel[ch_idx]:8.4f}  {bar}")

    # 2. Hardest class pairs (lowest max FDR across channels)
    max_fdr_per_pair = np.max(fdr, axis=1)
    sorted_pairs = np.argsort(max_fdr_per_pair)

    print(f"\n{'='*70}")
    print(f"  Hardest Class Pairs (lowest max FDR = least separable)")
    print(f"{'='*70}")
    print(f"  {'Pair':<35s} {'Max FDR':>8s}  {'Best Channel':<18s}  {'Verdict'}")
    print(f"  {'-'*80}")
    for idx in sorted_pairs:
        ci, cj = pairs[idx]
        max_val = max_fdr_per_pair[idx]
        best_ch = channel_names[np.argmax(fdr[idx])]
        if max_val < 0.10:
            verdict = "INSEPARABLE"
        elif max_val < 0.50:
            verdict = "HARD"
        elif max_val < 1.00:
            verdict = "MODERATE"
        else:
            verdict = "EASY"
        print(f"  {class_names[ci]:<16s} vs {class_names[cj]:<16s} {max_val:8.4f}  {best_ch:<18s}  {verdict}")

    # 3. Confusion matrix hot spots (the 5 most confused pairs from original results)
    confused_pairs = [
        (0, 6, 'wrong_compound->normal 46%'),
        (2, 3, 'galling<->stripped_thread'),
        (2, 4, 'galling<->over_torque'),
        (5, 2, 'under_torque->galling 23%'),
        (7, 3, 'misaligned<->stripped 19%'),
    ]
    print(f"\n{'='*70}")
    print(f"  FDR for Most Confused Pairs (from confusion matrix)")
    print(f"{'='*70}")
    for ci, cj, desc in confused_pairs:
        pair_idx = None
        for i, (a, b) in enumerate(pairs):
            if (a == ci and b == cj) or (a == cj and b == ci):
                pair_idx = i
                break
        if pair_idx is not None:
            print(f"\n  {desc}")
            ch_fdrs = fdr[pair_idx]
            sorted_chs = np.argsort(ch_fdrs)[::-1]
            for ch_idx in sorted_chs[:5]:
                bar = '#' * min(int(ch_fdrs[ch_idx] * 10), 40)
                print(f"    {channel_names[ch_idx]:<18s} FDR={ch_fdrs[ch_idx]:8.4f}  {bar}")

    # 4. Summary diagnosis
    inseparable = sum(1 for mf in max_fdr_per_pair if mf < 0.10)
    hard = sum(1 for mf in max_fdr_per_pair if 0.10 <= mf < 0.50)
    total_pairs = len(pairs)

    print(f"\n{'='*70}")
    print(f"  DIAGNOSIS")
    print(f"{'='*70}")
    print(f"  Total class pairs: {total_pairs}")
    print(f"  Inseparable (FDR < 0.10): {inseparable}")
    print(f"  Hard (0.10 < FDR < 0.50): {hard}")
    print(f"  Moderate+ (FDR > 0.50): {total_pairs - inseparable - hard}")

    if inseparable > total_pairs * 0.3:
        print(f"\n  CRITICAL: {inseparable}/{total_pairs} pairs are inseparable.")
        print(f"  The current feature representation CANNOT support 9-class classification.")
        print(f"  ACTION: Domain feature engineering required (torque-turn envelope,")
        print(f"          frequency features, phase segmentation).")
    elif hard > total_pairs * 0.3:
        print(f"\n  WARNING: {hard}/{total_pairs} pairs are hard to separate.")
        print(f"  A stronger model or additional features may be needed for full F1=0.85.")
    else:
        print(f"\n  GOOD: Most pairs are moderately separable.")
        print(f"  Training strategy fixes should yield significant improvement.")

    print("=" * 70)

    # 5. Silhouette score
    try:
        from sklearn.metrics import silhouette_score, silhouette_samples
        from sklearn.decomposition import PCA

        n_samples = windows.shape[0]
        X_flat = windows.reshape(n_samples, -1)
        n_components = min(50, X_flat.shape[1], n_samples - 1)
        pca = PCA(n_components=n_components, random_state=42)
        X_pca = pca.fit_transform(X_flat)

        print(f"\n{'='*70}")
        print(f"  Silhouette Score (PCA: {pca.explained_variance_ratio_.sum():.1%} var in {n_components} dims)")
        print(f"{'='*70}")

        sample_size = min(5000, n_samples)
        sil = silhouette_score(X_pca, labels, sample_size=sample_size, random_state=42)
        print(f"  Overall: {sil:.4f}")

        sil_samples = silhouette_samples(X_pca, labels)
        for cls_idx, cls_name in enumerate(class_names):
            mask = labels == cls_idx
            if mask.sum() > 0:
                cls_sil = sil_samples[mask].mean()
                tag = "OK" if cls_sil > 0.25 else "WEAK" if cls_sil > 0 else "POOR"
                print(f"    [{tag:>4s}] {cls_name:<20s}: {cls_sil:.4f} (n={mask.sum()})")
    except ImportError:
        print("\n  scikit-learn not available — skipping silhouette analysis")

    # 6. Class mean profile plots
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        key_channels = list(range(min(len(channel_names), 13)))
        n_plots = len(key_channels)
        fig, axes = plt.subplots(n_plots, 1, figsize=(14, 3 * n_plots))
        if n_plots == 1:
            axes = [axes]
        colors = plt.cm.tab10(np.linspace(0, 1, len(class_names)))

        for ax_idx, ch in enumerate(key_channels):
            ax = axes[ax_idx]
            for cls_idx, cls_name in enumerate(class_names):
                mask = labels == cls_idx
                if mask.sum() < 5:
                    continue
                cls_data = windows[mask, :, ch]
                mean = cls_data.mean(axis=0)
                std = cls_data.std(axis=0)
                ax.plot(mean, color=colors[cls_idx], label=cls_name, alpha=0.8)
                ax.fill_between(range(len(mean)), mean - std, mean + std,
                                color=colors[cls_idx], alpha=0.15)
            ax.set_title(f"Channel: {channel_names[ch]}")
            ax.legend(fontsize=7, loc='upper right')
        plt.tight_layout()
        plt.savefig('class_mean_profiles.png', dpi=150)
        plt.close()
        print(f"\n  Class mean profiles saved to class_mean_profiles.png")
    except ImportError:
        print("\n  matplotlib not available — skipping class mean plots")

    print(f"\n{'='*70}")
    print(f"  Full diagnostics complete.")
    print(f"{'='*70}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Fisher Discriminant Ratio Analysis')
    parser.add_argument('--config', default='config.yaml',
                        help='Config YAML (default: config.yaml)')
    args = parser.parse_args()
    run_fdr_analysis(args.config)
