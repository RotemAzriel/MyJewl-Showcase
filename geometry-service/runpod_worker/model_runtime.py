"""DINOv3 embedding and per-shape HNSW catalog runtime."""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import Any

import hnswlib
import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel


MODEL_ID = os.environ.get(
    "DINOV3_MODEL_ID",
    "facebook/dinov3-vitl16-pretrain-lvd1689m",
)


class DinoEmbedder:
    def __init__(self, model_id: str = MODEL_ID) -> None:
        token = os.environ.get("HF_TOKEN") or None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.processor = AutoImageProcessor.from_pretrained(model_id, token=token)
        dtype = torch.float16 if self.device.type == "cuda" else torch.float32
        self.model = AutoModel.from_pretrained(
            model_id,
            token=token,
            torch_dtype=dtype,
        ).to(self.device)
        self.model.eval()
        self.model_id = model_id

    @torch.inference_mode()
    def embed(self, images: list[Image.Image]) -> np.ndarray:
        inputs = self.processor(images=images, return_tensors="pt")
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        outputs = self.model(**inputs)
        if getattr(outputs, "pooler_output", None) is not None:
            embeddings = outputs.pooler_output
        else:
            embeddings = outputs.last_hidden_state[:, 0]
        embeddings = torch.nn.functional.normalize(embeddings.float(), dim=-1)
        return embeddings.cpu().numpy().astype(np.float32)


class CatalogRuntime:
    def __init__(self, catalog_dir: str | Path, embedder: DinoEmbedder | None = None) -> None:
        self.catalog_dir = Path(catalog_dir)
        self.embedder = embedder or DinoEmbedder()
        self.indexes: dict[str, hnswlib.Index] = {}
        self.entries: dict[str, list[dict[str, Any]]] = {}
        self._load_indexes()

    def _load_indexes(self) -> None:
        index_dir = self.catalog_dir / "indexes"
        for metadata_path in sorted(index_dir.glob("*.metadata.json")):
            shape = metadata_path.name.removesuffix(".metadata.json")
            entries = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(entries, list) or not entries:
                continue
            index_path = index_dir / f"{shape}.hnsw"
            if not index_path.is_file():
                continue
            dimensions = int(entries[0]["embedding_dimensions"])
            index = hnswlib.Index(space="cosine", dim=dimensions)
            index.load_index(str(index_path), max_elements=len(entries))
            index.set_ef(max(50, min(200, len(entries))))
            self.indexes[shape] = index
            self.entries[shape] = entries

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.indexes else "not_ready",
            "model": self.embedder.model_id,
            "device": str(self.embedder.device),
            "shapes": sorted(self.indexes),
            "catalog_dir": str(self.catalog_dir),
        }

    def query(
        self,
        image_base64: str,
        *,
        stone_shape: str,
        top_k: int,
    ) -> dict[str, Any]:
        if stone_shape not in self.indexes:
            raise ValueError(f"No catalog index exists for stone shape '{stone_shape}'.")
        try:
            image_bytes = base64.b64decode(image_base64, validate=True)
            with Image.open(io.BytesIO(image_bytes)) as opened:
                image = opened.convert("RGB")
        except Exception as exc:  # noqa: BLE001 - image boundary
            raise ValueError("image_base64 is not a valid image.") from exc

        vector = self.embedder.embed([image])
        count = min(max(1, int(top_k)), len(self.entries[stone_shape]))
        labels, distances = self.indexes[stone_shape].knn_query(vector, k=count)
        candidates: list[dict[str, Any]] = []
        for label, distance in zip(labels[0], distances[0], strict=True):
            entry = dict(self.entries[stone_shape][int(label)])
            entry.pop("embedding_dimensions", None)
            entry["similarity"] = float(1.0 - distance)
            candidates.append(entry)
        return {
            "model": self.embedder.model_id,
            "stone_shape": stone_shape,
            "candidates": candidates,
        }
