"""
Balanced Batch Sampler
========================
Ensures every mini-batch contains examples from all 10 classes.
Oversamples minority classes, undersamples majority class (normal).

Per Section 5.3: samples_per_class = batch_size // num_classes = 64 // 10 = 6.
Each batch gets ~6 samples per class = 60 samples (padded to 64 randomly).
"""
import numpy as np
import torch
from torch.utils.data import Sampler
from typing import List, Iterator, Dict, Optional
from collections import defaultdict


class BalancedBatchSampler(Sampler):
    """Yields batches with balanced class representation.

    For each batch:
      1. Sample `samples_per_class` indices from each class
      2. Shuffle the combined indices
      3. Yield them as a single batch

    Minority classes are oversampled (with replacement).
    Majority classes are undersampled (without replacement).

    Args:
        labels: List/array of integer class labels for each sample.
        batch_size: Target batch size (may be slightly different due to rounding).
        samples_per_class: How many samples per class per batch.
            Default: batch_size // num_classes.
        drop_last: Whether to drop the last incomplete batch.
    """

    def __init__(self, labels: np.ndarray, batch_size: int = 64,
                 samples_per_class: Optional[int] = None,
                 drop_last: bool = False):
        self.labels = np.asarray(labels)
        self.batch_size = batch_size
        self.drop_last = drop_last

        # Discover classes and build per-class index lists
        self.classes = sorted(set(self.labels.tolist()))
        self.num_classes = len(self.classes)
        self.samples_per_class = samples_per_class or (batch_size // self.num_classes)

        self.class_indices: Dict[int, np.ndarray] = {}
        for cls in self.classes:
            self.class_indices[cls] = np.where(self.labels == cls)[0]

        # Actual batch size = samples_per_class * num_classes
        self.actual_batch_size = self.samples_per_class * self.num_classes

        # Number of batches per epoch: enough to see each majority-class sample ~once
        max_class_size = max(len(idx) for idx in self.class_indices.values())
        self._num_batches = max_class_size // self.samples_per_class

    def __iter__(self) -> Iterator[List[int]]:
        for _ in range(self._num_batches):
            batch = []
            for cls in self.classes:
                idx = self.class_indices[cls]
                # Oversample if class has fewer samples than needed
                replace = len(idx) < self.samples_per_class
                chosen = np.random.choice(idx, self.samples_per_class, replace=replace)
                batch.extend(chosen.tolist())

            np.random.shuffle(batch)
            yield batch

    def __len__(self) -> int:
        return self._num_batches
