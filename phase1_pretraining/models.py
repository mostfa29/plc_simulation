"""
Model Architectures for Phase 1 Pretraining
=============================================

Two architectures:
  1. ResNet baseline (Tier 1) — validates data pipeline, ~50K params, 30 min on GPU
  2. InceptionTime (Tier 2) — primary model, ensemble of 5, ~250K total params

Per Section 4 of the Phase 1 spec:
  - Input: [B, 12, 2000] (12 channels, 2000 timesteps)
  - Output: [B, 10] (10 classes)
  - InceptionTime: 6 Inception modules in 2 residual blocks of 3
  - Ensemble: 5 independently-initialized networks, mean of softmax outputs
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List, Optional


# ═══════════════════════════════════════════════════════════════════
# ResNet Baseline (Tier 1)
# ═══════════════════════════════════════════════════════════════════

class ResBlock1d(nn.Module):
    """1D Residual block: Conv-BN-ReLU-Conv-BN + shortcut."""

    def __init__(self, in_channels: int, out_channels: int,
                 kernel_size_1: int = 7, kernel_size_2: int = 5):
        super().__init__()
        self.conv1 = nn.Conv1d(in_channels, out_channels, kernel_size_1,
                               padding=kernel_size_1 // 2)
        self.bn1 = nn.BatchNorm1d(out_channels)
        self.conv2 = nn.Conv1d(out_channels, out_channels, kernel_size_2,
                               padding=kernel_size_2 // 2)
        self.bn2 = nn.BatchNorm1d(out_channels)

        # Shortcut for dimension mismatch
        self.shortcut = nn.Identity()
        if in_channels != out_channels:
            self.shortcut = nn.Sequential(
                nn.Conv1d(in_channels, out_channels, 1),
                nn.BatchNorm1d(out_channels),
            )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = self.shortcut(x)
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        return F.relu(out + residual)


class ResNetBaseline(nn.Module):
    """Simple 3-block ResNet for time series classification.

    ~50K parameters. Target: >80% macro F1.
    If this fails, debug the data pipeline before trying InceptionTime.

    Args:
        c_in: Input channels (default 12).
        c_out: Output classes (default 10).
    """

    def __init__(self, c_in: int = 12, c_out: int = 10):
        super().__init__()
        self.block1 = ResBlock1d(c_in, 64)
        self.block2 = ResBlock1d(64, 128)
        self.block3 = ResBlock1d(128, 128)
        self.gap = nn.AdaptiveAvgPool1d(1)
        self.fc = nn.Linear(128, c_out)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [B, c_in, T] input tensor (channels-first).
        Returns:
            [B, c_out] logits (before softmax).
        """
        out = self.block1(x)
        out = self.block2(out)
        out = self.block3(out)
        out = self.gap(out).squeeze(-1)  # [B, 128]
        return self.fc(out)


# ═══════════════════════════════════════════════════════════════════
# InceptionTime (Tier 2, Primary)
# ═══════════════════════════════════════════════════════════════════

class InceptionModule(nn.Module):
    """Single Inception module with multi-scale parallel convolutions.

    Architecture per Section 4.2.1:
      Bottleneck -> 3 parallel conv branches (k=10, k=20, k=40) + MaxPool branch
      -> Concat -> BatchNorm -> ReLU

    Args:
        in_channels: Input channel count.
        nf: Number of filters per branch (default 32).
        bottleneck_size: Bottleneck output channels (default 32).
    """

    def __init__(self, in_channels: int, nf: int = 32,
                 bottleneck_size: int = 32):
        super().__init__()

        # Bottleneck: reduce channels before parallel convs
        self.bottleneck = nn.Conv1d(in_channels, bottleneck_size, kernel_size=1, bias=False)

        # Branch A: short-range patterns (k=11, ~0.11s at 100Hz)
        self.conv_a = nn.Conv1d(bottleneck_size, nf, kernel_size=11,
                                padding=5, bias=False)

        # Branch B: medium-range patterns (k=21, ~0.21s)
        self.conv_b = nn.Conv1d(bottleneck_size, nf, kernel_size=21,
                                padding=10, bias=False)

        # Branch C: long-range patterns (k=41, ~0.41s)
        self.conv_c = nn.Conv1d(bottleneck_size, nf, kernel_size=41,
                                padding=20, bias=False)

        # MaxPool branch: translation-invariant features
        self.maxpool = nn.MaxPool1d(kernel_size=3, stride=1, padding=1)
        self.conv_mp = nn.Conv1d(in_channels, nf, kernel_size=1, bias=False)

        # Output: concat of 4 branches = 4 * nf channels
        self.bn = nn.BatchNorm1d(4 * nf)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Bottleneck
        bottleneck_out = self.bottleneck(x)

        # Parallel branches
        branch_a = self.conv_a(bottleneck_out)
        branch_b = self.conv_b(bottleneck_out)
        branch_c = self.conv_c(bottleneck_out)
        branch_mp = self.conv_mp(self.maxpool(x))

        # Concat + BN + ReLU
        out = torch.cat([branch_a, branch_b, branch_c, branch_mp], dim=1)
        return F.relu(self.bn(out))


class InceptionBlock(nn.Module):
    """Residual block of 3 Inception modules with shortcut connection.

    Section 4.2.2: Each residual block = 3 Inception modules + skip connection.
    """

    def __init__(self, in_channels: int, nf: int = 32):
        super().__init__()
        out_channels = 4 * nf  # Each Inception module outputs 4*nf channels

        self.module1 = InceptionModule(in_channels, nf)
        self.module2 = InceptionModule(out_channels, nf)
        self.module3 = InceptionModule(out_channels, nf)

        # Shortcut: match dimensions if needed
        self.shortcut = nn.Sequential(
            nn.Conv1d(in_channels, out_channels, kernel_size=1, bias=False),
            nn.BatchNorm1d(out_channels),
        ) if in_channels != out_channels else nn.Identity()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = self.shortcut(x)
        out = self.module1(x)
        out = self.module2(out)
        out = self.module3(out)
        return F.relu(out + residual)


class InceptionTimeNetwork(nn.Module):
    """Single InceptionTime network (one member of the ensemble).

    Architecture (Section 4.2.2):
      Input [B, 12, 2000]
      -> InceptionBlock 1 (3 modules + residual) -> [B, 128, 2000]
      -> InceptionBlock 2 (3 modules + residual) -> [B, 128, 2000]
      -> Global Average Pool -> [B, 128]
      -> FC -> [B, 10]

    ~50K parameters per network.

    Args:
        c_in: Input channels (default 12).
        c_out: Number of classes (default 10).
        nf: Filters per Inception branch (default 32). Output = 4*nf = 128.
    """

    def __init__(self, c_in: int = 12, c_out: int = 10, nf: int = 32):
        super().__init__()
        self.block1 = InceptionBlock(c_in, nf)
        self.block2 = InceptionBlock(4 * nf, nf)
        self.gap = nn.AdaptiveAvgPool1d(1)
        self.fc = nn.Linear(4 * nf, c_out)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.block1(x)
        out = self.block2(out)
        out = self.gap(out).squeeze(-1)
        return self.fc(out)


class InceptionTimeEnsemble(nn.Module):
    """Ensemble of N InceptionTime networks.

    Final prediction = mean of N softmax outputs.
    Per Section 4.2.2: ensemble of 5 reduces variance by ~60%.

    Args:
        c_in: Input channels.
        c_out: Number of classes.
        nf: Filters per branch.
        ensemble_size: Number of networks (default 5).
        seeds: Random seeds for initialization (one per member).
    """

    def __init__(self, c_in: int = 12, c_out: int = 10, nf: int = 32,
                 ensemble_size: int = 5,
                 seeds: Optional[List[int]] = None):
        super().__init__()
        self.ensemble_size = ensemble_size
        seeds = seeds or list(range(ensemble_size))

        self.networks = nn.ModuleList()
        for seed in seeds:
            torch.manual_seed(seed)
            net = InceptionTimeNetwork(c_in, c_out, nf)
            self.networks.append(net)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Mean of softmax outputs from all ensemble members."""
        outputs = []
        for net in self.networks:
            logits = net(x)
            probs = F.softmax(logits, dim=-1)
            outputs.append(probs)
        return torch.stack(outputs).mean(dim=0)

    def forward_logits(self, x: torch.Tensor, member_idx: int) -> torch.Tensor:
        """Forward pass through a single ensemble member (for training)."""
        return self.networks[member_idx](x)


def create_model(architecture: str = 'InceptionTime',
                 c_in: int = 12, c_out: int = 10,
                 nf: int = 32, **kwargs) -> nn.Module:
    """Factory function to create model by name.

    Args:
        architecture: 'InceptionTime', 'InceptionTimeEnsemble', or 'ResNet'.
        c_in: Input channels.
        c_out: Output classes.
        nf: Filters per branch.

    Returns:
        nn.Module instance.
    """
    if architecture == 'ResNet':
        return ResNetBaseline(c_in, c_out)
    elif architecture == 'InceptionTime':
        return InceptionTimeNetwork(c_in, c_out, nf)
    elif architecture == 'InceptionTimeEnsemble':
        return InceptionTimeEnsemble(c_in, c_out, nf, **kwargs)
    else:
        raise ValueError(f"Unknown architecture: {architecture}")


def count_parameters(model: nn.Module) -> int:
    """Count trainable parameters."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)
