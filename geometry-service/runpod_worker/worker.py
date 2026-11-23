"""RunPod Serverless worker entrypoint."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import runpod

from runpod_worker.model_runtime import CatalogRuntime


CATALOG_DIR = Path(os.environ.get("CATALOG_DIR", "/app/fast_mvp/catalog"))
_runtime: CatalogRuntime | None = None


def runtime() -> CatalogRuntime:
    global _runtime
    if _runtime is None:
        _runtime = CatalogRuntime(CATALOG_DIR)
    return _runtime


def handler(job: dict[str, Any]) -> dict[str, Any]:
    payload = job.get("input")
    if not isinstance(payload, dict):
        raise ValueError("RunPod input must be an object.")
    operation = str(payload.get("operation") or "query")
    if operation == "health":
        return runtime().health()
    if operation != "query":
        raise ValueError(f"Unsupported operation: {operation}.")
    return runtime().query(
        str(payload.get("image_base64") or ""),
        stone_shape=str(payload.get("stone_shape") or ""),
        top_k=int(payload.get("top_k") or 5),
    )


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
