#!/usr/bin/env python3
"""
Colab Notebook Builder
========================
Converts the multi-module codebase into a single Colab .ipynb notebook.

The notebook uses %%writefile to write each module to the Colab VM disk,
then runs the training pipeline.

Usage:
  python build_notebook.py
  python build_notebook.py --output colab/02_train_phase1_v3.ipynb
  python build_notebook.py --hierarchical  # Include hierarchical training
"""
import argparse
import json
from pathlib import Path


def read_module(path):
    """Read a Python module file and return its contents."""
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def make_cell(cell_type, source, execution_count=None):
    """Create a notebook cell dict."""
    cell = {
        "cell_type": cell_type,
        "metadata": {},
        "source": source if isinstance(source, str) else source,
    }
    if cell_type == "code":
        cell["execution_count"] = execution_count
        cell["outputs"] = []
    return cell


def build_notebook(output_path, include_hierarchical=False, include_advanced=False):
    """Build the training notebook from the module files."""
    root = Path(__file__).parent
    phase1 = root / 'phase1_pretraining'

    cells = []

    # Title
    cells.append(make_cell("markdown",
        "# Phase 1: Synthetic Pretraining (v3)\n\n"
        "**Session 1+2+3 Fixes Applied:**\n"
        "- CrossEntropyLoss (no FocalLoss, no label smoothing, no mixup)\n"
        "- 19-channel feature pipeline (5 domain features + phase one-hot)\n"
        "- Hierarchical two-stage classifier option\n"
        "- Plain Adam optimizer (no weight decay)\n\n"
        "Run cells in order. Each `%%writefile` cell writes a module to disk."))

    # Step 1: GPU check
    cells.append(make_cell("markdown", "## Step 1 — Check GPU"))
    cells.append(make_cell("code",
        "import torch\n\n"
        "if torch.cuda.is_available():\n"
        "    gpu = torch.cuda.get_device_name(0)\n"
        "    mem = torch.cuda.get_device_properties(0).total_memory / 1e9\n"
        "    print(f\"GPU: {gpu}  ({mem:.1f} GB VRAM)\")\n"
        "else:\n"
        "    print(\"WARNING: No GPU detected. Training will be slow on CPU.\")\n"
        "    print(\"Go to Runtime -> Change runtime type -> T4 GPU\")\n"))

    # Step 2: Dependencies
    cells.append(make_cell("markdown", "## Step 2 — Install Dependencies"))
    cells.append(make_cell("code",
        "!pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118 -q\n"
        "!pip install pandas pyarrow pyyaml tqdm matplotlib seaborn scipy -q\n"
        "print(\"All dependencies installed.\")\n"))

    # Step 3: Mount Drive
    cells.append(make_cell("markdown", "## Step 3 — Mount Google Drive"))
    cells.append(make_cell("code",
        "from google.colab import drive\n"
        "drive.mount('/content/drive')\n"
        "print(\"Drive mounted.\")\n"))

    # Step 4: Configuration
    cells.append(make_cell("markdown",
        "## Step 4 — Configuration\n\n"
        "Set `DRIVE_DATA_DIR` to where the dataset was saved by the generation notebook.\n"))

    config_source = read_module(phase1 / 'config.yaml')
    cells.append(make_cell("code",
        "%%writefile config.yaml\n" + config_source))

    cells.append(make_cell("code",
        "import yaml\n"
        "with open('config.yaml') as f:\n"
        "    config = yaml.safe_load(f)\n\n"
        "# Override dataset path for Colab\n"
        "DRIVE_DATA_DIR = '/content/drive/MyDrive/TopDriveAI/data/synthetic_v2'\n"
        "config['data']['dataset_dir'] = DRIVE_DATA_DIR\n\n"
        "print(f\"Dataset: {config['data']['dataset_dir']}\")\n"
        "print(f\"Channels: {config['data']['total_channels']}\")\n"
        "print(f\"Model: {config['model']['architecture']} (c_in={config['model']['c_in']})\")\n"
        "print(f\"Loss: {config['loss']['type']}\")\n"
        "print(f\"Optimizer: {config['training']['optimizer']}\")\n"))

    # Step 5: Write ML modules
    cells.append(make_cell("markdown",
        "## Step 5 — Write ML Modules\n\n"
        "Each cell writes one Python module to the Colab VM disk.\n"))

    modules = [
        ('features.py', 'features.py'),
        ('dataset.py', 'dataset.py'),
        ('models.py', 'models.py'),
        ('losses.py', 'losses.py'),
        ('sampler.py', 'sampler.py'),
        ('train.py', 'train.py'),
        ('eval.py', 'eval.py'),
    ]

    if include_hierarchical:
        modules.append(('hierarchical.py', 'hierarchical.py'))

    if include_advanced:
        modules.append(('advanced_features.py', 'advanced_features.py'))

    for display_name, filename in modules:
        cells.append(make_cell("markdown", f"### `{display_name}`"))
        source = read_module(phase1 / filename)
        cells.append(make_cell("code", f"%%writefile {filename}\n{source}"))

    # Step 6: Label quality (Session 4)
    cells.append(make_cell("markdown",
        "## Step 6 — Label Quality Check (Session 4)\n\n"
        "Rebuild manifest using ground truth labels and validate."))

    cells.append(make_cell("markdown", "### `label_quality.py`"))
    lq_source = read_module(phase1 / 'label_quality.py')
    cells.append(make_cell("code", f"%%writefile label_quality.py\n{lq_source}"))

    cells.append(make_cell("code",
        "from label_quality import rebuild_manifest, validate_labels\n"
        "from pathlib import Path\n\n"
        "dataset_dir = config['data']['dataset_dir']\n"
        "manifest = rebuild_manifest(dataset_dir, config.get('class_map'))\n"
        "suspicious = validate_labels(manifest, Path(dataset_dir) / 'sensor')\n"
        "print(f'\\nSuspicious labels: {len(suspicious)} / {len(manifest)}')\n"))

    # Step 7: Training
    cells.append(make_cell("markdown",
        "## Step 7 — Training\n\n"
        "Run the training pipeline. Choose flat (standard) or hierarchical mode."))

    # Flat training
    cells.append(make_cell("markdown", "### Option A: Flat 9-class Training"))
    cells.append(make_cell("code",
        "# Standard flat 9-class training (Session 1+2 fixes)\n"
        "!python train.py --config config.yaml --output-dir ./results\n"))

    # Hierarchical training
    if include_hierarchical:
        cells.append(make_cell("markdown", "### Option B: Hierarchical Training (Session 3)"))
        cells.append(make_cell("code",
            "# Two-stage hierarchical classifier\n"
            "!python train.py --config config.yaml --output-dir ./results --hierarchical\n"))

    # Step 8: Evaluation
    cells.append(make_cell("markdown", "## Step 8 — Evaluation"))
    cells.append(make_cell("code",
        "!python eval.py --config config.yaml "
        "--checkpoint-dir ./results/checkpoints --output-dir ./results\n"))

    # Step 9: Diagnostics
    cells.append(make_cell("markdown",
        "## Step 9 — Diagnostics (Session 5)\n\n"
        "MiniRocket baseline and Fisher Discriminant Ratio analysis."))

    cells.append(make_cell("code",
        "!pip install aeon scikit-learn -q\n"))

    # Write diagnostic modules
    cells.append(make_cell("markdown", "### MiniRocket Baseline"))
    mr_source = read_module(phase1 / 'diagnostics' / 'minirocket_baseline.py')
    cells.append(make_cell("code",
        "import os\nos.makedirs('diagnostics', exist_ok=True)\n"))
    cells.append(make_cell("code",
        f"%%writefile diagnostics/minirocket_baseline.py\n{mr_source}"))
    cells.append(make_cell("code",
        "!python diagnostics/minirocket_baseline.py --config config.yaml\n"))

    cells.append(make_cell("markdown", "### Fisher Discriminant + Silhouette"))
    fdr_source = read_module(phase1 / 'diagnostics' / 'fisher_discriminant.py')
    cells.append(make_cell("code",
        f"%%writefile diagnostics/fisher_discriminant.py\n{fdr_source}"))
    cells.append(make_cell("code",
        "!python diagnostics/fisher_discriminant.py --config config.yaml\n"))

    # Step 10: Save results
    cells.append(make_cell("markdown", "## Step 10 — Save Results to Drive"))
    cells.append(make_cell("code",
        "import shutil\n"
        "drive_results = '/content/drive/MyDrive/TopDriveAI/results/phase1_v3'\n"
        "shutil.copytree('./results', drive_results, dirs_exist_ok=True)\n"
        "print(f'Results saved to {drive_results}')\n"))

    # Build notebook JSON
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 0,
        "metadata": {
            "colab": {"provenance": [], "gpuType": "T4"},
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
            "accelerator": "GPU",
        },
        "cells": cells,
    }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(notebook, f, indent=1, ensure_ascii=False)

    n_code = sum(1 for c in cells if c['cell_type'] == 'code')
    n_md = sum(1 for c in cells if c['cell_type'] == 'markdown')
    print(f"Built notebook: {output_path}")
    print(f"  {n_code} code cells, {n_md} markdown cells")
    print(f"  Hierarchical: {'yes' if include_hierarchical else 'no'}")
    print(f"  Advanced features: {'yes' if include_advanced else 'no'}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build Colab training notebook')
    parser.add_argument('--output', default='colab/02_train_phase1.ipynb',
                        help='Output notebook path')
    parser.add_argument('--hierarchical', action='store_true',
                        help='Include hierarchical training cells')
    parser.add_argument('--advanced', action='store_true',
                        help='Include advanced features module')
    args = parser.parse_args()

    build_notebook(args.output,
                   include_hierarchical=args.hierarchical,
                   include_advanced=args.advanced)
