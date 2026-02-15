"""
Loss Functions for Phase 1 Pretraining
========================================
Focal loss with per-class alpha weighting and label smoothing.

Per Section 5.1 of the Phase 1 spec:
  - Focal loss down-weights well-classified examples via (1 - p_t)^gamma
  - Per-class alpha inversely proportional to class frequency, adjusted for safety
  - Label smoothing (0.1) prevents overconfidence on synthetic data
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List, Optional


class FocalLoss(nn.Module):
    """Focal loss with per-class alpha weighting and label smoothing.

    Args:
        alpha: Per-class weight tensor [num_classes]. Higher = more weight.
        gamma: Focusing parameter. gamma=0 -> standard CE. gamma=2 -> standard focal.
        label_smoothing: Smoothing factor (0-1). 0.1 recommended for synthetic data.
    """

    def __init__(self, alpha: List[float], gamma: float = 2.0,
                 label_smoothing: float = 0.1):
        super().__init__()
        self.register_buffer('alpha', torch.tensor(alpha, dtype=torch.float32))
        self.gamma = gamma
        self.label_smoothing = label_smoothing

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        """
        Args:
            logits: [B, num_classes] raw model output (before softmax).
            targets: [B] integer class labels.

        Returns:
            Scalar loss.
        """
        num_classes = logits.size(-1)

        # Label smoothing: spread (1-eps) on true class, eps/(K-1) on others
        with torch.no_grad():
            smooth_targets = torch.zeros_like(logits)
            smooth_targets.fill_(self.label_smoothing / (num_classes - 1))
            smooth_targets.scatter_(1, targets.unsqueeze(1), 1.0 - self.label_smoothing)

        # Log-probabilities
        log_probs = F.log_softmax(logits, dim=-1)
        probs = torch.exp(log_probs)

        # Focal modulation: (1 - p_t)^gamma
        focal_weight = (1.0 - probs) ** self.gamma

        # Per-class alpha weighting
        alpha_weight = self.alpha[targets].unsqueeze(-1)  # [B, 1]

        # Combine: -alpha * (1-p)^gamma * smooth_target * log(p)
        loss = -alpha_weight * focal_weight * smooth_targets * log_probs
        return loss.sum(dim=-1).mean()


class MixupFocalLoss(nn.Module):
    """Focal loss adapted for mixup augmentation.

    When mixup is applied, we get two targets (y_a, y_b) and a lambda.
    Loss = lam * FocalLoss(logits, y_a) + (1 - lam) * FocalLoss(logits, y_b).
    """

    def __init__(self, alpha: List[float], gamma: float = 2.0,
                 label_smoothing: float = 0.1):
        super().__init__()
        self.focal = FocalLoss(alpha, gamma, label_smoothing)

    def forward(self, logits: torch.Tensor,
                targets_a: torch.Tensor, targets_b: torch.Tensor,
                lam: float) -> torch.Tensor:
        return lam * self.focal(logits, targets_a) + (1 - lam) * self.focal(logits, targets_b)


def mixup_data(x: torch.Tensor, y: torch.Tensor,
               alpha: float = 0.2) -> tuple:
    """Apply mixup augmentation to a batch.

    Args:
        x: Input tensor [B, C, T].
        y: Target labels [B].
        alpha: Beta distribution parameter. 0.2 = mostly pure samples.

    Returns:
        (mixed_x, y_a, y_b, lam)
    """
    if alpha > 0:
        lam = float(torch.distributions.Beta(alpha, alpha).sample())
    else:
        lam = 1.0

    batch_size = x.size(0)
    index = torch.randperm(batch_size, device=x.device)

    mixed_x = lam * x + (1 - lam) * x[index]
    y_a, y_b = y, y[index]

    return mixed_x, y_a, y_b, lam
