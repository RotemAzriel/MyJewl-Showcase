"""Shared image preprocessing for mounting retrieval.

The preprocessing intentionally performs no geometric inference. It only reduces
background noise before an image is embedded by DINOv3.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 12_000
MAX_WORKING_SIDE = 1_024
MODEL_INPUT_SIZE = 256


@dataclass(frozen=True)
class PreprocessedImage:
    png_bytes: bytes
    width: int
    height: int
    crop_box: tuple[int, int, int, int]
    mask_source: str
    mask_fraction: float | None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "crop_box": list(self.crop_box),
            "mask_source": self.mask_source,
            "mask_fraction": (
                None if self.mask_fraction is None else round(self.mask_fraction, 4)
            ),
            "model_input_size": MODEL_INPUT_SIZE,
            "background": "black",
        }


def preprocess_for_retrieval(image_bytes: bytes) -> PreprocessedImage:
    """Orient, isolate, crop, and pad an image for DINOv3 retrieval."""
    if not image_bytes:
        raise ValueError("An image is required for retrieval.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the 10 MB upload limit.")

    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - Pillow exposes several decode errors
        raise ValueError("Uploaded image could not be decoded.") from exc

    original_width, original_height = image.size
    if min(original_width, original_height) < 32:
        raise ValueError("Image must be at least 32×32 pixels.")
    if max(original_width, original_height) > MAX_IMAGE_SIDE:
        raise ValueError("Image dimensions are too large.")

    image.thumbnail((MAX_WORKING_SIDE, MAX_WORKING_SIDE), Image.Resampling.LANCZOS)
    width, height = image.size
    arr = np.asarray(image, dtype=np.float32) / 255.0
    mask = _object_mask(arr)
    bbox = _plausible_mask_bbox(mask)

    if bbox is None:
        crop_box = _center_crop_box(width, height)
        cropped = image.crop(crop_box)
        mask_source = "center_crop_fallback"
        mask_fraction = None
    else:
        x0, y0, x1, y1 = _pad_box(bbox, width, height, fraction=0.10)
        crop_box = (x0, y0, x1, y1)
        mask_image = Image.fromarray(mask.astype(np.uint8) * 255)
        mask_image = mask_image.filter(ImageFilter.GaussianBlur(radius=1.25))
        black = Image.new("RGB", image.size, (0, 0, 0))
        isolated = Image.composite(image, black, mask_image)
        cropped = isolated.crop(crop_box)
        mask_source = "heuristic_object_mask"
        mask_fraction = float(mask.mean())

    normalized = ImageOps.pad(
        cropped,
        (MODEL_INPUT_SIZE, MODEL_INPUT_SIZE),
        method=Image.Resampling.LANCZOS,
        color=(0, 0, 0),
        centering=(0.5, 0.5),
    )
    output = io.BytesIO()
    normalized.save(output, format="PNG", optimize=True)
    return PreprocessedImage(
        png_bytes=output.getvalue(),
        width=original_width,
        height=original_height,
        crop_box=crop_box,
        mask_source=mask_source,
        mask_fraction=mask_fraction,
    )


def _object_mask(arr: np.ndarray) -> np.ndarray:
    border = np.concatenate(
        [arr[0, :, :], arr[-1, :, :], arr[:, 0, :], arr[:, -1, :]],
        axis=0,
    )
    background = np.median(border, axis=0)
    distance = np.linalg.norm(arr - background, axis=2)
    lum = _luminance(arr)
    sat = arr.max(axis=2) - arr.min(axis=2)
    distance_threshold = max(0.08, float(np.percentile(distance, 72)))
    return (distance > distance_threshold) | ((sat > 0.13) & (lum < 0.93))


def _plausible_mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask)
    area = int(mask.size)
    selected = len(xs)
    if selected < max(24, int(area * 0.01)):
        return None
    fraction = selected / area
    if fraction > 0.82:
        return None

    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    width = x1 - x0
    height = y1 - y0
    image_height, image_width = mask.shape
    if width < 8 or height < 8:
        return None
    if width / image_width > 0.97 and height / image_height > 0.97:
        return None
    return (x0, y0, x1, y1)


def _pad_box(
    box: tuple[int, int, int, int],
    width: int,
    height: int,
    *,
    fraction: float,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    pad_x = max(2, int(round((x1 - x0) * fraction)))
    pad_y = max(2, int(round((y1 - y0) * fraction)))
    return (
        max(0, x0 - pad_x),
        max(0, y0 - pad_y),
        min(width, x1 + pad_x),
        min(height, y1 + pad_y),
    )


def _center_crop_box(width: int, height: int) -> tuple[int, int, int, int]:
    side = max(32, int(round(min(width, height) * 0.90)))
    left = max(0, (width - side) // 2)
    top = max(0, (height - side) // 2)
    return (left, top, min(width, left + side), min(height, top + side))


def _luminance(arr: np.ndarray) -> np.ndarray:
    return arr[:, :, 0] * 0.2126 + arr[:, :, 1] * 0.7152 + arr[:, :, 2] * 0.0722
