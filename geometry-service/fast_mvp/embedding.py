"""Pluggable image embedding backends for Lane B (Goal 0005, Stage 2).

The ratified paradigm is **DINOv3 ViT-L/16 frozen, zero-shot first**
(``docs/research_synthesis.md`` §2.3). That backend needs ``torch`` +
``transformers`` and the model weights; when they are absent — CI, a laptop
without a GPU, this repo's default environment — a deterministic descriptor
backend keeps the whole lane runnable end to end.

**The fallback is not DINOv3 and never claims to be.** Every embedder reports a
``backend`` and ``model_id``, every index and every evaluation report records
them, and ``eval_image_to_config`` refuses to stamp a gate verdict on numbers
produced by a non-DINOv3 backend. That separation is the point: the pipeline can
be proven on real pixels today without anyone mistaking the result for the
G-B/G-C measurement the plan asks for.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from PIL import Image, ImageOps

from fast_mvp.retrieval import DINOV3_MODEL_ID

DESCRIPTOR_GRID = 8
DESCRIPTOR_ORIENTATION_BINS = 12
DESCRIPTOR_INPUT_SIZE = 128


@dataclass(frozen=True)
class EmbeddingBackendInfo:
    backend: str
    model_id: str
    dimension: int
    is_reference_paradigm: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "model_id": self.model_id,
            "dimension": self.dimension,
            "is_reference_paradigm": self.is_reference_paradigm,
        }


class Embedder(Protocol):
    @property
    def info(self) -> EmbeddingBackendInfo: ...

    def embed(self, image_bytes: bytes) -> np.ndarray: ...


def _l2_normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        return vector.astype(np.float32)
    return (vector / norm).astype(np.float32)


class TileStatisticsEmbedder:
    """Deterministic, dependency-light descriptor — the declared fallback.

    Encodes an image as per-tile luminance/colour statistics plus a gradient
    orientation histogram. It is sensitive to silhouette, band-to-stone
    proportion, and metal colour, which is enough to exercise retrieval,
    consensus, and abstention on real renders. It is *not* a learned embedding
    and carries ``is_reference_paradigm=False`` so no gate can be stamped on it.
    """

    backend_name = "tile_statistics_v1"

    def __init__(
        self,
        grid: int = DESCRIPTOR_GRID,
        orientation_bins: int = DESCRIPTOR_ORIENTATION_BINS,
    ) -> None:
        self.grid = int(grid)
        self.orientation_bins = int(orientation_bins)

    @property
    def info(self) -> EmbeddingBackendInfo:
        dimension = self.grid * self.grid * 4 + self.orientation_bins
        return EmbeddingBackendInfo(
            backend=self.backend_name,
            model_id="deterministic/tile-statistics-v1",
            dimension=dimension,
            is_reference_paradigm=False,
        )

    def embed(self, image_bytes: bytes) -> np.ndarray:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
        image = image.resize(
            (DESCRIPTOR_INPUT_SIZE, DESCRIPTOR_INPUT_SIZE), Image.Resampling.LANCZOS
        )
        arr = np.asarray(image, dtype=np.float32) / 255.0

        luminance = arr @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        saturation = arr.max(axis=2) - arr.min(axis=2)
        warmth = arr[:, :, 0] - arr[:, :, 2]

        step = DESCRIPTOR_INPUT_SIZE // self.grid
        tiles: list[float] = []
        for row in range(self.grid):
            for col in range(self.grid):
                ys = slice(row * step, (row + 1) * step)
                xs = slice(col * step, (col + 1) * step)
                patch = luminance[ys, xs]
                tiles.extend(
                    (
                        float(patch.mean()),
                        float(patch.std()),
                        float(saturation[ys, xs].mean()),
                        float(warmth[ys, xs].mean()),
                    )
                )

        gy, gx = np.gradient(luminance)
        magnitude = np.hypot(gx, gy)
        angle = np.mod(np.arctan2(gy, gx), np.pi)
        bins = np.clip(
            (angle / np.pi * self.orientation_bins).astype(np.int32),
            0,
            self.orientation_bins - 1,
        )
        histogram = np.bincount(
            bins.ravel(),
            weights=magnitude.ravel(),
            minlength=self.orientation_bins,
        ).astype(np.float32)
        total = float(histogram.sum())
        if total > 1e-12:
            histogram /= total

        return _l2_normalize(np.concatenate([np.asarray(tiles, dtype=np.float32), histogram]))


class Dinov3Embedder:
    """Frozen DINOv3 ViT-L/16 CLS embedding — the ratified M5-M7 paradigm."""

    backend_name = "dinov3_frozen"

    def __init__(self, model_id: str = DINOV3_MODEL_ID, device: str | None = None) -> None:
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModel
        except ImportError as exc:  # pragma: no cover - exercised only with torch
            raise RuntimeError(
                "DINOv3 backend requires torch + transformers; install them or use "
                "the declared fallback backend."
            ) from exc
        self._torch = torch
        self.model_id = model_id
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._processor = AutoImageProcessor.from_pretrained(model_id)
        self._model = AutoModel.from_pretrained(model_id).to(self.device).eval()
        self._dimension = int(self._model.config.hidden_size)

    @property
    def info(self) -> EmbeddingBackendInfo:
        return EmbeddingBackendInfo(
            backend=self.backend_name,
            model_id=self.model_id,
            dimension=self._dimension,
            is_reference_paradigm=True,
        )

    def embed(self, image_bytes: bytes) -> np.ndarray:  # pragma: no cover - needs torch
        with Image.open(io.BytesIO(image_bytes)) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
        inputs = self._processor(images=image, return_tensors="pt").to(self.device)
        with self._torch.no_grad():
            outputs = self._model(**inputs)
        hidden = outputs.last_hidden_state[:, 0, :]
        return _l2_normalize(hidden.squeeze(0).float().cpu().numpy())


def build_embedder(backend: str = "auto") -> Embedder:
    """Resolve an embedder.

    ``auto`` prefers DINOv3 and falls back to the deterministic descriptor when
    torch/transformers/weights are unavailable. ``dinov3`` fails loudly instead
    of silently degrading, which is what a real G-B/G-C run must use.
    """
    normalized = (backend or "auto").strip().lower()
    if normalized in {"dinov3", "dinov3_frozen"}:
        return Dinov3Embedder()
    if normalized in {"fallback", "tile_statistics", "tile_statistics_v1"}:
        return TileStatisticsEmbedder()
    if normalized == "auto":
        try:
            return Dinov3Embedder()
        except Exception:  # noqa: BLE001 - missing torch/weights is the normal path here
            return TileStatisticsEmbedder()
    raise ValueError(f"unknown embedding backend: {backend}")
