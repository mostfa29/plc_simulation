"""
Model Architectures for Phase 1 Pretraining
=============================================
Round 1 architecture: GAP-only pooling, no dropout by default.
This is the proven baseline (F1 = 0.54).
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List, Optional


class ResBlock1d(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size_1=7, kernel_size_2=5):
        super().__init__()
        self.conv1 = nn.Conv1d(in_channels, out_channels, kernel_size_1, padding=kernel_size_1 // 2)
        self.bn1 = nn.BatchNorm1d(out_channels)
        self.conv2 = nn.Conv1d(out_channels, out_channels, kernel_size_2, padding=kernel_size_2 // 2)
        self.bn2 = nn.BatchNorm1d(out_channels)
        self.shortcut = nn.Identity()
        if in_channels != out_channels:
            self.shortcut = nn.Sequential(
                nn.Conv1d(in_channels, out_channels, 1),
                nn.BatchNorm1d(out_channels))

    def forward(self, x):
        residual = self.shortcut(x)
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        return F.relu(out + residual)


class ResNetBaseline(nn.Module):
    """3-block ResNet with GAP pooling."""

    def __init__(self, c_in=12, c_out=9, dropout=0.0):
        super().__init__()
        self.block1 = ResBlock1d(c_in, 64)
        self.block2 = ResBlock1d(64, 128)
        self.block3 = ResBlock1d(128, 128)
        self.gap = nn.AdaptiveAvgPool1d(1)
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(128, c_out)

    def forward(self, x):
        out = self.block1(x)
        out = self.block2(out)
        out = self.block3(out)
        out = self.gap(out).squeeze(-1)
        out = self.dropout(out)
        return self.fc(out)


class InceptionModule(nn.Module):
    def __init__(self, in_channels, nf=32, bottleneck_size=32):
        super().__init__()
        self.bottleneck = nn.Conv1d(in_channels, bottleneck_size, kernel_size=1, bias=False)
        self.conv_a = nn.Conv1d(bottleneck_size, nf, kernel_size=11, padding=5, bias=False)
        self.conv_b = nn.Conv1d(bottleneck_size, nf, kernel_size=21, padding=10, bias=False)
        self.conv_c = nn.Conv1d(bottleneck_size, nf, kernel_size=41, padding=20, bias=False)
        self.maxpool = nn.MaxPool1d(kernel_size=3, stride=1, padding=1)
        self.conv_mp = nn.Conv1d(in_channels, nf, kernel_size=1, bias=False)
        self.bn = nn.BatchNorm1d(4 * nf)

    def forward(self, x):
        b = self.bottleneck(x)
        out = torch.cat([self.conv_a(b), self.conv_b(b), self.conv_c(b),
                         self.conv_mp(self.maxpool(x))], dim=1)
        return F.relu(self.bn(out))


class InceptionBlock(nn.Module):
    def __init__(self, in_channels, nf=32):
        super().__init__()
        out_channels = 4 * nf
        self.module1 = InceptionModule(in_channels, nf)
        self.module2 = InceptionModule(out_channels, nf)
        self.module3 = InceptionModule(out_channels, nf)
        self.shortcut = nn.Sequential(
            nn.Conv1d(in_channels, out_channels, kernel_size=1, bias=False),
            nn.BatchNorm1d(out_channels),
        ) if in_channels != out_channels else nn.Identity()

    def forward(self, x):
        residual = self.shortcut(x)
        out = self.module1(x)
        out = self.module2(out)
        out = self.module3(out)
        return F.relu(out + residual)


class InceptionTimeNetwork(nn.Module):
    """InceptionTime with GAP pooling (Round 1 proven architecture)."""

    def __init__(self, c_in=12, c_out=9, nf=32, dropout=0.0):
        super().__init__()
        self.block1 = InceptionBlock(c_in, nf)
        self.block2 = InceptionBlock(4 * nf, nf)
        self.gap = nn.AdaptiveAvgPool1d(1)
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(4 * nf, c_out)

    def forward(self, x):
        out = self.block1(x)
        out = self.block2(out)
        out = self.gap(out).squeeze(-1)
        out = self.dropout(out)
        return self.fc(out)


class InceptionTimeEnsemble(nn.Module):
    def __init__(self, c_in=12, c_out=9, nf=32, ensemble_size=5,
                 seeds=None, dropout=0.0):
        super().__init__()
        self.ensemble_size = ensemble_size
        seeds = seeds or list(range(ensemble_size))
        self.networks = nn.ModuleList()
        for seed in seeds:
            torch.manual_seed(seed)
            self.networks.append(InceptionTimeNetwork(c_in, c_out, nf, dropout))

    def forward(self, x):
        outputs = [F.softmax(net(x), dim=-1) for net in self.networks]
        return torch.stack(outputs).mean(dim=0)

    def forward_logits(self, x, member_idx):
        return self.networks[member_idx](x)


def create_model(architecture='InceptionTime', c_in=12, c_out=9,
                 nf=32, dropout=0.0, **kwargs):
    if architecture == 'ResNet':
        return ResNetBaseline(c_in, c_out, dropout=dropout)
    elif architecture == 'InceptionTime':
        return InceptionTimeNetwork(c_in, c_out, nf, dropout=dropout)
    elif architecture == 'InceptionTimeEnsemble':
        return InceptionTimeEnsemble(c_in, c_out, nf, dropout=dropout, **kwargs)
    else:
        raise ValueError(f"Unknown architecture: {architecture}")


def count_parameters(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)
