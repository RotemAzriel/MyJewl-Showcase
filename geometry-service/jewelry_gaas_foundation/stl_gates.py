"""STL geometry gates for Jewelry GaaS (G-2).

Gate 1 (mesh closedness) is a topological data-integrity check on an STL using
``trimesh.is_watertight`` + ``euler_number``. It is a developer/QA tool: a
``"pass"`` here means the mesh is a closed 2-manifold, NOT a manufacturing
certification. The official ``geometry_gates`` ledger in ``parameters.json``
stays ``pending`` until a calibrated shop profile validates it (brand rule).

Gates 2 (stone-metal collision) and 3 (stone-stone collision) now expose a
local, conservative AABB broad phase for per-part STLs. Those checks are useful
for repeatable developer smoke tests, but they are not calibrated manufacturing
certifications: any candidate overlap returns ``"needs_calibration"`` until a
shop-approved exact-intersection/contact-tolerance profile exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import trimesh


@dataclass(frozen=True)
class GateResult:
    """Result of a single STL geometry gate."""

    gate_id: str
    status: str  # "pass" | "fail" | "deferred_until_G1" | "needs_calibration"
    metrics: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def _load_mesh(stl_path: str | Path) -> trimesh.Trimesh:
    path = Path(stl_path)
    if not path.is_file():
        raise FileNotFoundError(f"STL not found: {path}")

    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(f"Could not load a triangle mesh from {path}")
    return mesh


def _bounds_metrics(
    mesh_a: trimesh.Trimesh,
    mesh_b: trimesh.Trimesh,
) -> dict[str, Any]:
    bounds_a = np.asarray(mesh_a.bounds, dtype=float)
    bounds_b = np.asarray(mesh_b.bounds, dtype=float)
    minimum = np.maximum(bounds_a[0], bounds_b[0])
    maximum = np.minimum(bounds_a[1], bounds_b[1])
    depth = np.maximum(maximum - minimum, 0.0)
    overlaps = bool(np.all(depth > 0.0))
    volume = float(np.prod(depth)) if overlaps else 0.0
    return {
        "aabb_overlap": overlaps,
        "overlap_depth_mm": [float(value) for value in depth],
        "overlap_volume_mm3": volume,
    }


def _mesh_entry(path: Path, mesh: trimesh.Trimesh) -> dict[str, Any]:
    bounds = np.asarray(mesh.bounds, dtype=float)
    return {
        "path": str(path),
        "triangle_count": int(len(mesh.faces)),
        "bounds_mm": bounds.tolist(),
    }


def check_mesh_closedness(stl_path: str | Path) -> GateResult:
    """Gate 1 — mesh closedness / manifold suitability.

    Reports ``is_watertight`` + ``euler_number`` (+ naked-edge count). Status is
    ``"pass"`` iff the mesh is watertight (a closed 2-manifold), else ``"fail"``.
    Tool-only: this does not write to any ``parameters.json`` gate ledger.
    """
    mesh = _load_mesh(stl_path)
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
    *,
    metal_stl_paths: Iterable[str | Path] | None = None,
) -> GateResult:
    """Gate 2 — stone-metal collision broad phase.

    With no per-part paths this preserves the historical ``deferred_until_G1``
    behavior. With paths, the check reports AABB overlap candidates. A pass means
    the broad phase found no possible overlap; ``needs_calibration`` means a
    downstream exact/contact-tolerance gate must decide whether the overlap is
    invalid, intended prong contact, or acceptable seating geometry.
    """
    metal_paths: list[Path] = []
    for candidate in (band_stl_path, prongs_stl_path):
        if candidate is not None:
            metal_paths.append(Path(candidate))
    if metal_stl_paths:
        metal_paths.extend(Path(path) for path in metal_stl_paths)

    if stone_stl_path is None or not metal_paths:
        return GateResult(
            gate_id="stone_metal_collision",
            status="deferred_until_G1",
            reason="Requires per-part STLs (stone plus at least one metal part) from G-1.",
        )

    stone_path = Path(stone_stl_path)
    stone_mesh = _load_mesh(stone_path)
    pairs: list[dict[str, Any]] = []
    any_overlap = False
    for metal_path in metal_paths:
        metal_mesh = _load_mesh(metal_path)
        pair = {
            "stone": _mesh_entry(stone_path, stone_mesh),
            "metal": _mesh_entry(metal_path, metal_mesh),
            **_bounds_metrics(stone_mesh, metal_mesh),
        }
        pairs.append(pair)
        any_overlap = any_overlap or bool(pair["aabb_overlap"])

    metrics = {
        "method": "aabb_broad_phase",
        "calibrated": False,
        "pair_count": len(pairs),
        "pairs": pairs,
    }
    if any_overlap:
        return GateResult(
            gate_id="stone_metal_collision",
            status="needs_calibration",
            metrics=metrics,
            reason=(
                "AABB broad phase found stone-metal candidate overlap; exact "
                "intersection/contact tolerance requires a calibrated gate."
            ),
        )
    return GateResult(
        gate_id="stone_metal_collision",
        status="pass",
        metrics=metrics,
        reason="No stone-metal AABB overlap candidates found by the local broad phase.",
    )


def check_stone_stone_collision(
    stone_stl_paths: list[str | Path] | None = None,
) -> GateResult:
    """Gate 3 — stone-stone collision broad phase."""
    if stone_stl_paths is None:
        return GateResult(
            gate_id="stone_stone_collision",
            status="deferred_until_G1",
            reason="Requires per-part stone STLs from G-1.",
        )

    paths = [Path(path) for path in stone_stl_paths]
    if len(paths) < 2:
        return GateResult(
            gate_id="stone_stone_collision",
            status="pass",
            metrics={
                "method": "aabb_broad_phase",
                "calibrated": False,
                "stone_count": len(paths),
                "pair_count": 0,
                "pairs": [],
            },
            reason="Fewer than two stone parts; no stone-stone collision is possible.",
        )

    meshes = [(path, _load_mesh(path)) for path in paths]
    pairs: list[dict[str, Any]] = []
    any_overlap = False
    for (path_a, mesh_a), (path_b, mesh_b) in combinations(meshes, 2):
        pair = {
            "stone_a": _mesh_entry(path_a, mesh_a),
            "stone_b": _mesh_entry(path_b, mesh_b),
            **_bounds_metrics(mesh_a, mesh_b),
        }
        pairs.append(pair)
        any_overlap = any_overlap or bool(pair["aabb_overlap"])

    metrics = {
        "method": "aabb_broad_phase",
        "calibrated": False,
        "stone_count": len(paths),
        "pair_count": len(pairs),
        "pairs": pairs,
    }
    if any_overlap:
        return GateResult(
            gate_id="stone_stone_collision",
            status="needs_calibration",
            metrics=metrics,
            reason=(
                "AABB broad phase found stone-stone candidate overlap; exact "
                "intersection tolerance requires a calibrated gate."
            ),
        )
    return GateResult(
        gate_id="stone_stone_collision",
        status="pass",
        metrics=metrics,
        reason="No stone-stone AABB overlap candidates found by the local broad phase.",
    )
