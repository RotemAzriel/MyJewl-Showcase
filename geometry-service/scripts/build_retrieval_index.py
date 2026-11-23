#!/usr/bin/env python3
"""Embed the catalog renders into a retrieval index (Goal 0005, Stage 2 / plan §3.3).

Reads the provenance manifest written by ``scripts/build_catalog_renders.py``,
embeds the ``catalog``-split images with the selected backend, and writes an
index the app and the eval harness both load.

The embedding backend is recorded in the index. ``--backend dinov3`` fails loudly
if torch/transformers/weights are missing rather than silently degrading, because
a G-B/G-C measurement is only meaningful on the ratified paradigm; ``--backend
auto`` is for wiring and smoke runs and marks the index as non-reference.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fast_mvp.catalog_index import CatalogIndex, try_build_hnsw  # noqa: E402
from fast_mvp.embedding import build_embedder  # noqa: E402
from fast_mvp.preprocessing import preprocess_for_retrieval  # noqa: E402

CATALOG_SPLIT = "catalog"


def load_render_records(manifest_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError(f"render manifest has no records: {manifest_path}")
    return payload, records


def build_index(
    manifest_path: Path,
    *,
    backend: str = "auto",
    split: str = CATALOG_SPLIT,
    preprocess: bool = True,
) -> tuple[CatalogIndex, dict[str, Any]]:
    """Embed the selected split and return the index plus a build report."""
    manifest_path = Path(manifest_path)
    root = manifest_path.parent
    payload, records = load_render_records(manifest_path)
    selected = [record for record in records if record.get("split") == split]
    if not selected:
        raise ValueError(f"no records with split '{split}' in {manifest_path}")

    embedder = build_embedder(backend)
    vectors: list[np.ndarray] = []
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    started = time.monotonic()

    for record in selected:
        image_path = root / str(record["image_path"])
        try:
            raw = image_path.read_bytes()
            payload_bytes = (
                preprocess_for_retrieval(raw).png_bytes if preprocess else raw
            )
            vectors.append(embedder.embed(payload_bytes))
        except Exception as exc:  # noqa: BLE001 - per-image resilience
            skipped.append({"image_path": str(record["image_path"]), "reason": str(exc)})
            continue
        kept.append(dict(record))

    if not vectors:
        raise ValueError("every catalog image failed to embed; nothing to index")

    embeddings = np.vstack(vectors).astype(np.float32)
    structure = try_build_hnsw(embeddings)
    metadata = {
        "schema_version": 1,
        "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "render_manifest": str(manifest_path),
        "split": split,
        "preprocessed": preprocess,
        "embedding": embedder.info.to_json(),
        "structure": structure,
        "render_lighting": payload.get("lighting"),
        "render_resolution": payload.get("resolution"),
    }
    index = CatalogIndex(embeddings, kept, metadata)
    report = {
        "status": "pass",
        "vectors": int(embeddings.shape[0]),
        "dimension": int(embeddings.shape[1]),
        "configs": len({record["config_id"] for record in kept}),
        "shapes": sorted({record["stone_shape"] for record in kept}),
        "skipped": skipped,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "embedding": embedder.info.to_json(),
        "structure": structure,
    }
    return index, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("render_manifest", type=Path)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument(
        "--backend",
        default="auto",
        help="auto | dinov3 | fallback. 'dinov3' errors instead of degrading.",
    )
    parser.add_argument("--split", default=CATALOG_SPLIT)
    parser.add_argument("--no-preprocess", action="store_true")
    parser.add_argument("--report-json", type=Path, default=None)
    args = parser.parse_args(argv)

    index, report = build_index(
        args.render_manifest,
        backend=args.backend,
        split=args.split,
        preprocess=not args.no_preprocess,
    )
    report.update(index.save(args.index_dir))
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
