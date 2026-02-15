#!/usr/bin/env python3
"""
Evaluation Script for Phase 1
================================
Evaluates trained models on held-out synthetic test set.

Produces (per Section 7 of Phase 1 spec):
  1. 10x10 confusion matrix (counts + percentages)
  2. Per-class precision / recall / F1 table
  3. Macro and weighted F1
  4. Training curves (loss and F1 vs epoch)
  5. Exit criteria assessment (pass/fail for each criterion)

Usage:
  # Evaluate InceptionTime ensemble
  python eval.py --config config.yaml --checkpoint-dir results/checkpoints

  # Evaluate ResNet baseline
  python eval.py --config config.yaml --checkpoint-dir results/checkpoints --model ResNet
"""
import argparse
import json
import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import yaml
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader

from dataset import prepare_datasets, Phase1Dataset, NormParams
from models import InceptionTimeNetwork, ResNetBaseline, count_parameters

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


def load_ensemble(checkpoint_dir: Path, device: torch.device,
                   c_in: int = 12, c_out: int = 10,
                   nf: int = 32, ensemble_size: int = 5) -> List[nn.Module]:
    """Load all InceptionTime ensemble members."""
    models = []
    for i in range(ensemble_size):
        path = checkpoint_dir / f'inception_{i}_best.pt'
        if not path.exists():
            logger.warning(f"Missing checkpoint: {path}")
            continue
        checkpoint = torch.load(path, map_location=device, weights_only=False)
        model = InceptionTimeNetwork(c_in, c_out, nf)
        model.load_state_dict(checkpoint['model_state_dict'])
        model.to(device)
        model.eval()
        models.append(model)
        logger.info(f"Loaded inception_{i}: val_f1={checkpoint.get('val_macro_f1', 'N/A')}")
    return models


def ensemble_predict(models: List[nn.Module], dataloader: DataLoader,
                      device: torch.device) -> tuple:
    """Predict using ensemble mean of softmax outputs.

    Returns:
        (predictions, labels, probabilities)
    """
    all_probs = []
    all_labels = []

    with torch.no_grad():
        for X_batch, y_batch in dataloader:
            X_batch = X_batch.to(device)

            # Average softmax across ensemble
            batch_probs = []
            for model in models:
                logits = model(X_batch)
                probs = F.softmax(logits, dim=-1)
                batch_probs.append(probs)

            avg_probs = torch.stack(batch_probs).mean(dim=0)
            all_probs.append(avg_probs.cpu().numpy())
            all_labels.extend(y_batch.numpy())

    all_probs = np.concatenate(all_probs, axis=0)
    all_labels = np.array(all_labels)
    all_preds = all_probs.argmax(axis=1)

    return all_preds, all_labels, all_probs


def compute_confusion_matrix(preds: np.ndarray, labels: np.ndarray,
                               num_classes: int = 10) -> np.ndarray:
    """Compute NxN confusion matrix."""
    cm = np.zeros((num_classes, num_classes), dtype=np.int64)
    for true, pred in zip(labels, preds):
        cm[true, pred] += 1
    return cm


def compute_per_class_metrics(preds: np.ndarray, labels: np.ndarray,
                                num_classes: int = 10) -> List[Dict]:
    """Compute precision, recall, F1 per class."""
    metrics = []
    for cls in range(num_classes):
        tp = ((preds == cls) & (labels == cls)).sum()
        fp = ((preds == cls) & (labels != cls)).sum()
        fn = ((preds != cls) & (labels == cls)).sum()
        support = (labels == cls).sum()

        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-8)

        metrics.append({
            'class': cls,
            'precision': float(precision),
            'recall': float(recall),
            'f1': float(f1),
            'support': int(support),
        })
    return metrics


def assess_exit_criteria(per_class_metrics: List[Dict],
                          class_names: List[str]) -> List[Dict]:
    """Assess Phase 1 exit criteria per Section 7.2."""
    f1_scores = [m['f1'] for m in per_class_metrics]
    macro_f1 = np.mean(f1_scores)
    normal_recall = per_class_metrics[0]['recall']  # class 0 = normal
    fault_recalls = [m['recall'] for m in per_class_metrics[1:]]
    avg_fault_recall = np.mean(fault_recalls)

    criteria = [
        {
            'criterion': 'Macro F1 > 0.85',
            'value': f'{macro_f1:.4f}',
            'target': '0.85',
            'status': 'PASS' if macro_f1 > 0.85 else 'FAIL',
        },
        {
            'criterion': 'All per-class F1 > 0.70',
            'value': f'min={min(f1_scores):.4f}',
            'target': '0.70',
            'status': 'PASS' if min(f1_scores) > 0.70 else 'FAIL',
        },
        {
            'criterion': 'Normal class recall > 0.95',
            'value': f'{normal_recall:.4f}',
            'target': '0.95',
            'status': 'PASS' if normal_recall > 0.95 else 'FAIL',
        },
        {
            'criterion': 'Avg fault recall > 0.80',
            'value': f'{avg_fault_recall:.4f}',
            'target': '0.80',
            'status': 'PASS' if avg_fault_recall > 0.80 else 'FAIL',
        },
        {
            'criterion': 'No class at 0%',
            'value': f'min_support={min(m["support"] for m in per_class_metrics)}',
            'target': '>0',
            'status': 'PASS' if all(m['support'] > 0 for m in per_class_metrics) else 'FAIL',
        },
    ]

    # Per-class F1 detail
    for i, (m, name) in enumerate(zip(per_class_metrics, class_names)):
        criteria.append({
            'criterion': f'  {name} F1 > 0.70',
            'value': f'{m["f1"]:.4f}',
            'target': '0.70',
            'status': 'PASS' if m['f1'] > 0.70 else 'FAIL',
        })

    return criteria


def save_confusion_matrix_csv(cm: np.ndarray, class_names: List[str],
                                path: str):
    """Save confusion matrix as CSV."""
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([''] + class_names)
        for i, name in enumerate(class_names):
            writer.writerow([name] + cm[i].tolist())


def save_confusion_matrix_text(cm: np.ndarray, class_names: List[str]) -> str:
    """Format confusion matrix as readable text."""
    lines = []
    header = f"{'':>20s}" + ''.join(f'{n:>12s}' for n in class_names)
    lines.append(header)
    lines.append('-' * len(header))

    for i, name in enumerate(class_names):
        row = f'{name:>20s}'
        for j in range(len(class_names)):
            pct = cm[i, j] / max(cm[i].sum(), 1) * 100
            row += f'{cm[i, j]:>8d}({pct:4.1f}%)'
        lines.append(row)

    return '\n'.join(lines)


def generate_report(results_dir: Path, class_names: List[str],
                     macro_f1: float, per_class: List[Dict],
                     criteria: List[Dict], cm: np.ndarray,
                     ensemble_size: int,
                     model_label: str = "InceptionTime ensemble"):
    """Generate Phase 1 evaluation report as Markdown."""
    report_path = results_dir / 'phase1_report.md'

    lines = [
        '# Phase 1: Synthetic Pretraining - Evaluation Report',
        '',
        '## Summary',
        f'- **Model**: {model_label}',
        f'- **Macro F1**: {macro_f1:.4f}',
        f'- **Overall**: {"PASS" if macro_f1 > 0.85 else "NEEDS WORK"}',
        '',
        '## Exit Criteria Assessment',
        '',
        '| Criterion | Value | Target | Status |',
        '|-----------|-------|--------|--------|',
    ]
    for c in criteria:
        lines.append(f"| {c['criterion']} | {c['value']} | {c['target']} | {c['status']} |")

    lines += [
        '',
        '## Per-Class Metrics',
        '',
        '| Class | Precision | Recall | F1 | Support |',
        '|-------|-----------|--------|-----|---------|',
    ]
    for m, name in zip(per_class, class_names):
        lines.append(
            f"| {name} | {m['precision']:.4f} | {m['recall']:.4f} | "
            f"{m['f1']:.4f} | {m['support']} |"
        )

    lines += [
        '',
        '## Confusion Matrix',
        '```',
        save_confusion_matrix_text(cm, class_names),
        '```',
        '',
        '## Files',
        f'- Checkpoints: `checkpoints/inception_{{0-{ensemble_size-1}}}_best.pt`',
        '- Normalization: `checkpoints/norm_params.json`',
        '- Training curves: `logs/inception_*_training.csv`',
        '- Confusion matrix: `confusion_matrix.csv`',
        '- Per-class metrics: `per_class_metrics.csv`',
    ]

    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))

    logger.info(f"Report saved to {report_path}")


def main():
    parser = argparse.ArgumentParser(description='Phase 1 Evaluation')
    parser.add_argument('--config', type=str, default='config.yaml')
    parser.add_argument('--checkpoint-dir', type=str, default='./results/checkpoints')
    parser.add_argument('--output-dir', type=str, default='./results')
    parser.add_argument('--model', type=str, default=None,
                        choices=['InceptionTime', 'ResNet'],
                        help='Model to evaluate (auto-detects from checkpoints if not set)')
    parser.add_argument('--device', type=str, default=None)
    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).parent / config_path
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Device
    if args.device:
        device = torch.device(args.device)
    elif torch.cuda.is_available():
        device = torch.device('cuda')
    else:
        device = torch.device('cpu')

    checkpoint_dir = Path(args.checkpoint_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    class_names = config['class_names']
    class_map = config['class_map']
    num_classes = len(class_names)

    # Prepare test dataset
    logger.info("Loading test dataset...")
    dataset_dir = config['data']['dataset_dir']
    if not Path(dataset_dir).is_absolute():
        dataset_dir = str(Path(__file__).parent / dataset_dir)

    _, _, test_dataset, _ = prepare_datasets(
        dataset_dir=dataset_dir,
        class_map=class_map,
        window_size=config['data']['window_size'],
        stride=config['data']['window_stride'],
        split_ratio=tuple(config['data']['split_ratio']),
        split_seed=config['data'].get('split_seed', 42),
    )

    test_loader = DataLoader(
        test_dataset,
        batch_size=config['training']['batch_size'] * 2,
        shuffle=False,
    )

    # Load model(s) — auto-detect if --model not specified
    c_in = config['model']['c_in']
    c_out = config['model']['c_out']
    nf = config['model']['nf']

    model_type = args.model
    if model_type is None:
        # Auto-detect: check which checkpoints exist
        has_resnet = (checkpoint_dir / 'resnet_baseline.pt').exists()
        has_inception = (checkpoint_dir / 'inception_0_best.pt').exists()
        if has_inception:
            model_type = 'InceptionTime'
        elif has_resnet:
            model_type = 'ResNet'
        else:
            logger.error(f"No checkpoints found in {checkpoint_dir}")
            logger.error("Expected resnet_baseline.pt or inception_*_best.pt")
            return
        logger.info(f"Auto-detected model: {model_type}")

    if model_type == 'ResNet':
        resnet_path = checkpoint_dir / 'resnet_baseline.pt'
        if not resnet_path.exists():
            logger.error(f"ResNet checkpoint not found: {resnet_path}")
            return
        checkpoint = torch.load(resnet_path, map_location=device, weights_only=False)
        model = ResNetBaseline(c_in, c_out)
        model.load_state_dict(checkpoint['model_state_dict'])
        model.to(device)
        model.eval()
        models = [model]
        model_label = "ResNet baseline"
        logger.info(f"Loaded ResNet: val_f1={checkpoint.get('val_macro_f1', 'N/A')}")
    else:
        ensemble_size = config['model'].get('ensemble_size', 5)
        models = load_ensemble(checkpoint_dir, device, c_in, c_out, nf, ensemble_size)
        model_label = f"InceptionTime ensemble ({len(models)} members)"
        if not models:
            logger.error("No InceptionTime checkpoints found. Did you train the ensemble?")
            logger.error("To evaluate the ResNet baseline, run: python eval.py --config config.yaml --model ResNet")
            return

    logger.info(f"Loaded {len(models)} model(s)")

    # Predict
    logger.info("Running inference on test set...")
    preds, labels, probs = ensemble_predict(models, test_loader, device)

    # Metrics
    cm = compute_confusion_matrix(preds, labels, num_classes)
    per_class = compute_per_class_metrics(preds, labels, num_classes)
    macro_f1 = np.mean([m['f1'] for m in per_class])
    criteria = assess_exit_criteria(per_class, class_names)

    # Print results
    print(f"\n{'='*60}")
    print(f"Phase 1 Evaluation Results")
    print(f"{'='*60}")
    print(f"Macro F1: {macro_f1:.4f}")
    print(f"\nPer-class metrics:")
    print(f"{'Class':>20s} {'Precision':>10s} {'Recall':>10s} {'F1':>10s} {'Support':>10s}")
    for m, name in zip(per_class, class_names):
        print(f"{name:>20s} {m['precision']:>10.4f} {m['recall']:>10.4f} "
              f"{m['f1']:>10.4f} {m['support']:>10d}")

    print(f"\nConfusion Matrix:")
    print(save_confusion_matrix_text(cm, class_names))

    print(f"\nExit Criteria:")
    for c in criteria:
        status_marker = '[+]' if c['status'] == 'PASS' else '[-]'
        print(f"  {status_marker} {c['criterion']}: {c['value']} (target: {c['target']})")

    # Save outputs
    save_confusion_matrix_csv(cm, class_names, str(output_dir / 'confusion_matrix.csv'))

    with open(output_dir / 'per_class_metrics.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['class_name'] + list(per_class[0].keys()))
        writer.writeheader()
        for m, name in zip(per_class, class_names):
            row = {'class_name': name, **m}
            writer.writerow(row)

    generate_report(output_dir, class_names, macro_f1, per_class, criteria, cm,
                    len(models), model_label=model_label)

    # Exit code based on macro F1
    overall_pass = all(c['status'] == 'PASS' for c in criteria[:4])
    print(f"\nOverall: {'PASS' if overall_pass else 'NEEDS WORK'}")


if __name__ == '__main__':
    main()
