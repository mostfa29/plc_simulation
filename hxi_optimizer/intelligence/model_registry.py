"""Per-machine model registry.

Discovers which ONNX models are deployed for which rigs and resolves the
right pair (classifier + autoencoder) for any given rig name. Falls back
to the sim-trained default when no rig-specific fine-tune exists.

Filesystem layout:

    hxi_optimizer/models/
    ├── classifier.onnx                ← sim-trained default
    ├── classifier_meta.json
    ├── autoencoder.onnx               ← sim-trained default
    ├── autoencoder_meta.json
    └── per_rig/
        ├── precision_rig_707_3pd_ht/
        │   ├── classifier.onnx        ← fine-tuned for 707
        │   ├── classifier_meta.json
        │   ├── autoencoder.onnx       (optional — falls back to default if missing)
        │   └── autoencoder_meta.json
        └── panther_rig_2/
            ├── classifier.onnx
            └── classifier_meta.json

Key principle: the default sim model is NEVER overwritten by fine-tune
deploys. Fine-tunes write into per_rig/<slug>/. The optimizer always
prefers a per-rig model when available, default otherwise.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("model_registry")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MODELS_DIR = REPO_ROOT / "hxi_optimizer" / "models"


def slugify(name: str) -> str:
    """Match the slug convention used by realtime_dataset + fleet."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "unknown"


@dataclass
class ModelPair:
    """One classifier + one optional autoencoder, with provenance."""
    rig_slug: str                      # "precision_rig_707_3pd_ht" or "default"
    source: str                        # "per_rig" | "default" | "missing"
    classifier_path: Optional[Path] = None
    classifier_meta_path: Optional[Path] = None
    autoencoder_path: Optional[Path] = None
    autoencoder_meta_path: Optional[Path] = None

    @property
    def has_classifier(self) -> bool:
        return self.classifier_path is not None and self.classifier_path.exists()

    @property
    def has_autoencoder(self) -> bool:
        return self.autoencoder_path is not None and self.autoencoder_path.exists()

    def to_json(self) -> dict:
        out = {
            "rig_slug": self.rig_slug,
            "source": self.source,
            "has_classifier": self.has_classifier,
            "has_autoencoder": self.has_autoencoder,
        }
        for k, p in [
            ("classifier_path", self.classifier_path),
            ("classifier_meta_path", self.classifier_meta_path),
            ("autoencoder_path", self.autoencoder_path),
            ("autoencoder_meta_path", self.autoencoder_meta_path),
        ]:
            out[k] = str(p) if p else None
        return out


class ModelRegistry:
    """Discovers, lists, and resolves per-machine models.

    Read-only — never writes. fine_tune.py is the only writer.
    """

    def __init__(self, models_dir: Path | str = DEFAULT_MODELS_DIR) -> None:
        self.models_dir = Path(models_dir)
        self.per_rig_dir = self.models_dir / "per_rig"

    # ── Path helpers ────────────────────────────────────────────────

    def _default_pair(self) -> ModelPair:
        cls = self.models_dir / "classifier.onnx"
        ae = self.models_dir / "autoencoder.onnx"
        return ModelPair(
            rig_slug="default",
            source="default",
            classifier_path=cls if cls.exists() else None,
            classifier_meta_path=(self.models_dir / "classifier_meta.json"
                                   if cls.exists() else None),
            autoencoder_path=ae if ae.exists() else None,
            autoencoder_meta_path=(self.models_dir / "autoencoder_meta.json"
                                    if ae.exists() else None),
        )

    def _rig_dir(self, rig_slug: str) -> Path:
        return self.per_rig_dir / rig_slug

    # ── Public API ──────────────────────────────────────────────────

    def resolve(self, rig_name: str | None) -> ModelPair:
        """Return the best ModelPair for this rig.

        Lookup order:
          1. Per-rig classifier exists at per_rig/<slug>/classifier.onnx → use it
             (prefer per-rig AE too, fall back to default AE if missing)
          2. Default sim models
          3. Empty pair (no models at all — system runs heuristic-only)
        """
        default_pair = self._default_pair()
        if not rig_name:
            return default_pair

        slug = slugify(rig_name)
        rig_dir = self._rig_dir(slug)
        rig_classifier = rig_dir / "classifier.onnx"
        rig_ae = rig_dir / "autoencoder.onnx"

        if not rig_classifier.exists():
            return default_pair  # no per-rig model → default

        # Per-rig classifier exists; AE falls through to default if missing
        return ModelPair(
            rig_slug=slug,
            source="per_rig",
            classifier_path=rig_classifier,
            classifier_meta_path=(rig_dir / "classifier_meta.json"
                                    if (rig_dir / "classifier_meta.json").exists()
                                    else default_pair.classifier_meta_path),
            autoencoder_path=(rig_ae if rig_ae.exists()
                              else default_pair.autoencoder_path),
            autoencoder_meta_path=(rig_dir / "autoencoder_meta.json"
                                    if (rig_dir / "autoencoder_meta.json").exists()
                                    else default_pair.autoencoder_meta_path),
        )

    def has_per_rig(self, rig_name: str) -> bool:
        """True if a fine-tuned classifier exists for this specific rig."""
        slug = slugify(rig_name)
        return (self._rig_dir(slug) / "classifier.onnx").exists()

    def list_all(self) -> list[ModelPair]:
        """Every available pair: default + every rig with a per-rig model."""
        out: list[ModelPair] = [self._default_pair()]
        if self.per_rig_dir.exists():
            for rig_dir in sorted(self.per_rig_dir.iterdir()):
                if not rig_dir.is_dir():
                    continue
                cls = rig_dir / "classifier.onnx"
                if not cls.exists():
                    continue
                ae = rig_dir / "autoencoder.onnx"
                out.append(ModelPair(
                    rig_slug=rig_dir.name,
                    source="per_rig",
                    classifier_path=cls,
                    classifier_meta_path=(rig_dir / "classifier_meta.json"
                                           if (rig_dir / "classifier_meta.json").exists()
                                           else None),
                    autoencoder_path=ae if ae.exists() else None,
                    autoencoder_meta_path=(rig_dir / "autoencoder_meta.json"
                                            if (rig_dir / "autoencoder_meta.json").exists()
                                            else None),
                ))
        return out

    def per_rig_summary(self) -> dict:
        """Compact summary for dashboard: which rigs have fine-tuned models."""
        pairs = self.list_all()
        per_rig = [p for p in pairs if p.source == "per_rig"]
        default = next((p for p in pairs if p.source == "default"), None)
        return {
            "default_available": default is not None and default.has_classifier,
            "default_has_classifier": default.has_classifier if default else False,
            "default_has_autoencoder": default.has_autoencoder if default else False,
            "n_per_rig": len(per_rig),
            "per_rig_slugs": [p.rig_slug for p in per_rig],
            "per_rig": [p.to_json() for p in per_rig],
            "default": default.to_json() if default else None,
        }

    def meta_for(self, pair: ModelPair) -> dict:
        """Read meta.json for a pair (classifier + autoencoder), if present."""
        out = {}
        if pair.classifier_meta_path and pair.classifier_meta_path.exists():
            try:
                out["classifier"] = json.loads(
                    pair.classifier_meta_path.read_text())
            except Exception as e:
                out["classifier"] = {"error": str(e)}
        if pair.autoencoder_meta_path and pair.autoencoder_meta_path.exists():
            try:
                out["autoencoder"] = json.loads(
                    pair.autoencoder_meta_path.read_text())
            except Exception as e:
                out["autoencoder"] = {"error": str(e)}
        return out
