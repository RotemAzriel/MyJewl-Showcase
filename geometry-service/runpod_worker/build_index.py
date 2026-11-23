"""Build per-shape HNSW indexes from a fixture or production catalog manifest."""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

import hnswlib
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_mvp.preprocessing import preprocess_for_retrieval  # noqa: E402
from runpod_worker.model_runtime import DinoEmbedder  # noqa: E402


def build_indexes(catalog_dir: Path, *, batch_size: int = 8) -> dict[str, int]:
    manifest_path = catalog_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Catalog manifest must contain a non-empty entries list.")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        grouped[str(entry["stone_shape"])].append(entry)

    embedder = DinoEmbedder()
    output_dir = catalog_dir / "indexes"
    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    for shape, shape_entries in sorted(grouped.items()):
        vectors: list[np.ndarray] = []
        metadata: list[dict] = []
        for start in range(0, len(shape_entries), batch_size):
            chunk = shape_entries[start : start + batch_size]
            images: list[Image.Image] = []
            valid_entries: list[dict] = []
            for entry in chunk:
                image_path = catalog_dir / str(entry["image_path"])
                processed = preprocess_for_retrieval(image_path.read_bytes())
                images.append(Image.open(io.BytesIO(processed.png_bytes)).convert("RGB"))
                valid_entries.append(entry)
            batch_vectors = embedder.embed(images)
            vectors.extend(batch_vectors)
            metadata.extend(valid_entries)

        matrix = np.asarray(vectors, dtype=np.float32)
        index = hnswlib.Index(space="cosine", dim=matrix.shape[1])
        index.init_index(
            max_elements=len(matrix),
            ef_construction=max(100, len(matrix) * 4),
            M=16,
        )
        index.add_items(matrix, np.arange(len(matrix)))
        index.set_ef(max(50, len(matrix)))
        index.save_index(str(output_dir / f"{shape}.hnsw"))

        serialized = []
        for entry in metadata:
            item = {
                key: value
                for key, value in entry.items()
                if key not in {"image_path", "stone_shape"}
            }
            item["embedding_dimensions"] = int(matrix.shape[1])
            serialized.append(item)
        (output_dir / f"{shape}.metadata.json").write_text(
            json.dumps(serialized, indent=2) + "\n",
            encoding="utf-8",
        )
        counts[shape] = len(serialized)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=ROOT / "fast_mvp" / "catalog",
    )
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(build_indexes(args.catalog_dir, batch_size=args.batch_size), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
