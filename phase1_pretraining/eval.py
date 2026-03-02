"""
Evaluation Script for Phase 1
================================
Evaluates trained models on held-out test set.
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)


def load_ensemble(checkpoint_dir, device, c_in=12, c_out=9, nf=32, ensemble_size=5, dropout=0.0):
    models = []
    for i in range(ensemble_size):
        path = checkpoint_dir / f'inception_{i}_best.pt'
        if not path.exists():
            logger.warning(f"Missing checkpoint: {path}")
            continue
        checkpoint = torch.load(path, map_location=device, weights_only=False)
        model = InceptionTimeNetwork(c_in, c_out, nf, dropout=dropout)
        model.load_state_dict(checkpoint['model_state_dict'])
        model.to(device).eval()
        models.append(model)
        logger.info(f"Loaded inception_{i}: val_f1={checkpoint.get('val_macro_f1', 'N/A')}")
    return models


def ensemble_predict(models, dataloader, device):
    all_probs, all_labels = [], []
    with torch.no_grad():
        for X_batch, y_batch in dataloader:
            X_batch = X_batch.to(device)
            batch_probs = []
            for model in models:
                logits = model(X_batch)
                batch_probs.append(F.softmax(logits, dim=-1))
            avg_probs = torch.stack(batch_probs).mean(dim=0)
            all_probs.append(avg_probs.cpu().numpy())
            all_labels.extend(y_batch.numpy())

    all_probs = np.concatenate(all_probs, axis=0)
    all_labels = np.array(all_labels)
    all_preds = all_probs.argmax(axis=1)
    return all_preds, all_labels, all_probs


def compute_confusion_matrix(preds, labels, num_classes):
    cm = np.zeros((num_classes, num_classes), dtype=np.int64)
    for true, pred in zip(labels, preds):
        cm[true, pred] += 1
    return cm


def compute_per_class_metrics(preds, labels, num_classes):
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
            'class': cls, 'precision': float(precision),
            'recall': float(recall), 'f1': float(f1), 'support': int(support),
        })
    return metrics


def assess_exit_criteria(per_class_metrics, class_names):
    f1_scores = [m['f1'] for m in per_class_metrics]
    macro_f1 = np.mean(f1_scores)
    normal_recall = per_class_metrics[0]['recall']
    fault_recalls = [m['recall'] for m in per_class_metrics[1:]]
    avg_fault_recall = np.mean(fault_recalls) if fault_recalls else 0.0

    criteria = [
        {'criterion': 'Macro F1 > 0.85', 'value': f'{macro_f1:.4f}',
         'target': '0.85', 'status': 'PASS' if macro_f1 > 0.85 else 'FAIL'},
        {'criterion': 'All per-class F1 > 0.70', 'value': f'min={min(f1_scores):.4f}',
         'target': '0.70', 'status': 'PASS' if min(f1_scores) > 0.70 else 'FAIL'},
        {'criterion': 'Normal class recall > 0.95', 'value': f'{normal_recall:.4f}',
         'target': '0.95', 'status': 'PASS' if normal_recall > 0.95 else 'FAIL'},
        {'criterion': 'Avg fault recall > 0.80', 'value': f'{avg_fault_recall:.4f}',
         'target': '0.80', 'status': 'PASS' if avg_fault_recall > 0.80 else 'FAIL'},
    ]
    for i, (m, name) in enumerate(zip(per_class_metrics, class_names)):
        criteria.append({
            'criterion': f'  {name} F1 > 0.70', 'value': f'{m["f1"]:.4f}',
            'target': '0.70', 'status': 'PASS' if m['f1'] > 0.70 else 'FAIL',
        })
    return criteria


def save_confusion_matrix_csv(cm, class_names, path):
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([''] + class_names)
        for i, name in enumerate(class_names):
            writer.writerow([name] + cm[i].tolist())


def save_confusion_matrix_text(cm, class_names):
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


def generate_report(results_dir, class_names, macro_f1, per_class, criteria, cm,
                     ensemble_size, model_label='InceptionTime ensemble'):
    lines = [
        '# Phase 1 Evaluation Report', '',
        f'**Model**: {model_label}', f'**Ensemble size**: {ensemble_size}',
        f'**Macro F1**: {macro_f1:.4f}', '',
        '## Exit Criteria', '| Criterion | Value | Target | Status |',
        '|-----------|-------|--------|--------|',
    ]
    for c in criteria:
        lines.append(f"| {c['criterion']} | {c['value']} | {c['target']} | {c['status']} |")

    lines.extend(['', '## Per-Class Metrics',
        '| Class | Precision | Recall | F1 | Support |',
        '|-------|-----------|--------|-----|---------|'])
    for m, name in zip(per_class, class_names):
        lines.append(f"| {name} | {m['precision']:.4f} | {m['recall']:.4f} | {m['f1']:.4f} | {m['support']} |")

    lines.extend(['', '## Confusion Matrix', '```',
                  save_confusion_matrix_text(cm, class_names), '```'])

    report_path = results_dir / 'phase1_report.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"Report saved to {report_path}")


def main():
    parser = argparse.ArgumentParser(description='Phase 1 Evaluation')
    parser.add_argument('--config', type=str, default='config.yaml')
    parser.add_argument('--checkpoint-dir', type=str, default='results/checkpoints')
    parser.add_argument('--model', type=str, default=None)
    parser.add_argument('--output-dir', type=str, default='./results')
    args = parser.parse_args()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    class_names = config['class_names']
    num_classes = len(class_names)

    logger.info("Loading test dataset...")
    dataset_dir = config['data']['dataset_dir']
    if not Path(dataset_dir).is_absolute():
        dataset_dir = str(Path(__file__).parent / dataset_dir)

    channels_mode = config['data'].get('channels_mode', 'all')
    _, _, test_dataset, _ = prepare_datasets(
        dataset_dir=dataset_dir, class_map=config['class_map'],
        window_size=config['data']['window_size'],
        stride=config['data']['window_stride'],
        split_ratio=tuple(config['data']['split_ratio']),
        split_seed=config['data'].get('split_seed', 42),
        channels_mode=channels_mode)

    test_loader = DataLoader(test_dataset, batch_size=128, shuffle=False)

    arch = config['model']['architecture']
    c_in, c_out, nf = config['model']['c_in'], config['model']['c_out'], config['model']['nf']

    dropout = config['model'].get('dropout', 0.0)

    if arch == 'ResNet':
        ckpt_path = Path(args.checkpoint_dir) / 'resnet_baseline.pt'
        checkpoint = torch.load(ckpt_path, map_location=device, weights_only=False)
        model = ResNetBaseline(c_in, c_out, dropout=dropout)
        model.load_state_dict(checkpoint['model_state_dict'])
        model.to(device).eval()
        models = [model]
    else:
        ensemble_size = config['model'].get('ensemble_size', 5)
        models = load_ensemble(Path(args.checkpoint_dir), device, c_in, c_out, nf,
                               ensemble_size, dropout=dropout)

    preds, labels, probs = ensemble_predict(models, test_loader, device)

    cm = compute_confusion_matrix(preds, labels, num_classes)
    per_class = compute_per_class_metrics(preds, labels, num_classes)
    macro_f1 = np.mean([m['f1'] for m in per_class])
    criteria = assess_exit_criteria(per_class, class_names)

    print(f"\n{'='*60}")
    print(f"Macro F1: {macro_f1:.4f}")
    for c in criteria:
        marker = '[PASS]' if c['status'] == 'PASS' else '[FAIL]'
        print(f"  {marker} {c['criterion']}: {c['value']} (target: {c['target']})")

    generate_report(output_dir, class_names, macro_f1, per_class, criteria, cm, len(models))


if __name__ == '__main__':
    main()