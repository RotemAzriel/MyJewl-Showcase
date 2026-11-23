"""STL geometry gates for Jewelry GaaS (G-2).

Gate 1 (mesh closedness) is a topological data-integrity check on an STL using
``trimesh.is_watertight`` + ``euler_number``. It is a developer/QA tool: a
``"pass"`` here means the mesh is a closed 2-manifold, NOT a manufacturing
certification. The official ``geometry_gates`` ledger in ``parameters.json``
stays ``pending`` until a calibrated shop profile validates it (brand rule).

Gates 2 (stone-metal collision) and 3 (stone-stone collision) need the per-part
STLs produced by G-1 and are stubbed ``deferred_until_G1`` here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import trimesh


@dataclass(frozen=True)
class GateResult:
    """Result of a single STL geometry gate."""

    gate_id: str
    status: str  # "pass" | "fail" | "deferred_until_G1"
    metrics: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def check_mesh_closedness(stl_path: str | Path) -> GateResult:
    """Gate 1 — mesh closedness / manifold suitability.

    Reports ``is_watertight`` + ``euler_number`` (+ naked-edge count). Status is
    ``"pass"`` iff the mesh is watertight (a closed 2-manifold), else ``"fail"``.
    Tool-only: this does not write to any ``parameters.json`` gate ledger.
    """
    path = Path(stl_path)
    if not path.is_file():
        raise FileNotFoundError(f"STL not found: {path}")

    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(f"Could not load a triangle mesh from {path}")

    is_watertight = bool(mesh.is_watertight)
    euler_number = int(mesh.euler_number)
    naked_edges = int(len(trimesh.grouping.group_rows(mesh.edges_sorted, require_count=1)))

    if is_watertight:
        reason = (
            f"watertight; euler_number={euler_number} "
            "consistent with a closed manifold"
        )
    else:
        reason = (
            f"not watertight; {naked_edges} naked edge(s), "
            f"euler_number={euler_number}"
        )

    return GateResult(
        gate_id="mesh_closedness",
        status="pass" if is_watertight else "fail",
        metrics={
            "is_watertight": is_watertight,
            "euler_number": euler_number,
            "naked_edges": naked_edges,
        },
        reason=reason,
    )


def check_stone_metal_collision(
    stone_stl_path: str | Path | None = None,
    band_stl_path: str | Path | None = None,
    prongs_stl_path: str | Path | None = None,
) -> GateResult:
    """Gate 2 — stone-metal collision. Deferred until per-part STLs exist (G-1)."""
    return GateResult(
        gate_id="stone_metal_collision",
        status="deferred_until_G1",
        reason="Requires per-part STLs (band/stone/prongs) from G-1; not yet implemented.",
    )


def check_stone_stone_collision(
    stone_stl_paths: list[str | Path] | None = None,
) -> GateResult:
    """Gate 3 — stone-stone collision. Deferred until per-part STLs exist (G-1)."""
    return GateResult(
        gate_id="stone_stone_collision",
        status="deferred_until_G1",
        reason="Requires per-part STLs (band/stone/prongs) from G-1; not yet implemented.",
    )
