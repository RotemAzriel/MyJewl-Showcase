#!/usr/bin/env python3
"""Measure a ring model -- ours or a reference -- and return comparable numbers.

Goal 0018, closing Goal 0013 Part A. Sibling to ``stl_gates.py``, and deliberately
a *different kind of thing*: ``stl_gates`` answers pass/fail questions about data
integrity, this module answers "what are the numbers".

Design rules, set by the operator:

1. **Report exact values always**, not only threshold violations. A dimension
   inside tolerance still prints its measured value.
2. **Measure, never certify.** Nothing here returns a manufacturing verdict.
   Findings are observations with a severity, not gates. Manufacturing gates stay
   ``pending`` / ``not_run`` / ``needs_calibration`` until a calibrated shop
   profile and a human jeweler review exist.
3. **State the method.** Every derived number records how it was obtained and at
   what resolution, so a figure can be argued with rather than trusted.

Known defects this instrument exists to surface, from the 2026-08-12 audit:

* **F1** -- 12 of 19 metal parts carry inverted normals. ``Gate 1`` passes them
  because they are watertight. ``orientation`` catches them.
* **F2** -- the parts are never booleaned, so the naive volume sum over-counts
  every interpenetration. ``parts_sum_mm3`` and ``union_mm3`` are reported as
  separate, separately-labelled figures and never conflated.
* **F3** -- wire dimensions scale linearly with stone radius toward zero.
  ``min_wall_thickness_mm`` measures it per part.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import trimesh

from .manufacturing_assumptions import (
    ASSUMPTIONS_VERSION,
    DIMENSION_BASIS,
    MANUFACTURING_FLOORS,
)

METRICS_VERSION = "myjewl-geometry-metrics-v1"

#: Parts excluded from the metal assembly. ``model.stl`` is the concatenation of
#: everything, and the centre stone is not metal.
NON_METAL_STEMS = frozenset({"center_stone_round_brilliant"})

#: The Goal 0018 V6 master emits ``castable_solid`` -- a Rhino boolean union of the
#: metal parts. It is a *derivative* of parts already in the bundle, so including
#: it in the assembly sum would double-count the whole ring. It is measured on its
#: own and cross-checked against this module's independently-computed union.
DERIVED_SOLID_STEMS = frozenset({"castable_solid"})

ASSEMBLY_EXCLUDED_STEMS = (
    frozenset({"model"}) | NON_METAL_STEMS | DERIVED_SOLID_STEMS
)

#: Surface-proximity distance below which two parts are treated as touching.
CONNECTIVITY_TOLERANCE_MM = 0.02

#: Minimum |cos| between an inward ray and the far wall's normal for the hit to
#: count as a thickness measurement. 0.5 = within 60 degrees of head-on. Rays that
#: graze a sharp edge return an arbitrarily small distance which is not a
#: thickness; without this filter they poison the reported minimum.
_GRAZING_COS_MIN = 0.5


@dataclass(frozen=True)
class Finding:
    """An observation about the geometry. Never a manufacturing verdict."""

    code: str
    severity: str  # "critical" | "structural" | "note"
    part: str | None
    summary: str
    measured: dict[str, Any] = field(default_factory=dict)


@dataclass
class PartMetrics:
    """Measurements for one part. Every field is a measured or derived number."""

    name: str
    path: str
    triangles: int
    vertices: int
    watertight: bool
    winding_consistent: bool
    oriented_outward: bool
    signed_volume_mm3: float
    absolute_volume_mm3: float
    surface_area_mm2: float
    euler_number: int
    genus: int | None
    bounds_mm: list[list[float]]
    max_dimension_mm: float
    min_wall_thickness_mm: float | None = None
    wall_thickness_method: str | None = None
    wall_thickness_samples: int | None = None
    wall_thickness_p05_mm: float | None = None


@dataclass
class AssemblyMetrics:
    """Measurements for the metal assembly as a whole."""

    part_count: int
    parts_sum_mm3: float
    union_mm3: float | None
    union_engine: str | None
    union_watertight: bool | None
    union_body_count: int | None
    overcount_mm3: float | None
    overcount_fraction: float | None
    connected_components: int | None
    connectivity_tolerance_mm: float
    inverted_part_count: int
    surface_area_mm2: float


@dataclass
class MeasurementReport:
    """The full measurement of one bundle."""

    bundle: str
    metrics_version: str
    assumptions_version: str
    dimension_basis: str
    parts: list[PartMetrics]
    assembly: AssemblyMetrics | None
    findings: list[Finding]
    #: Restated on every report so a consumer cannot read a verdict into it.
    authority: str = "measurement_only_not_manufacturing_certification"
    manufacturing_gates: str = "pending_not_run_needs_calibration"

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle": self.bundle,
            "metrics_version": self.metrics_version,
            "assumptions_version": self.assumptions_version,
            "dimension_basis": self.dimension_basis,
            "authority": self.authority,
            "manufacturing_gates": self.manufacturing_gates,
            "parts": [asdict(part) for part in self.parts],
            "assembly": asdict(self.assembly) if self.assembly else None,
            "findings": [asdict(item) for item in self.findings],
        }

    def masses_g(self, densities: dict[str, float]) -> dict[str, dict[str, float | None]]:
        """Return commercial mass estimates from measured volume.

        Reported from ``union_mm3`` when available. The naive sum is reported
        alongside and labelled, never substituted -- an over-counted volume is an
        over-quoted price. These are commercial estimates from measured geometry,
        **not** a manufacturing verdict.
        """
        if self.assembly is None:
            return {}
        out: dict[str, dict[str, float | None]] = {}
        for alloy, density in densities.items():
            union = self.assembly.union_mm3
            out[alloy] = {
                "from_union_g": None if union is None else union * density / 1000.0,
                "from_parts_sum_g": self.assembly.parts_sum_mm3 * density / 1000.0,
            }
        return out


def _load(path: Path) -> trimesh.Trimesh:
    """Load an STL as a mesh with vertices merged.

    STL stores every triangle independently, so an unmerged load reports a closed
    solid as non-watertight. Merging first is required for any topology claim.
    """
    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(f"Could not load a triangle mesh from {path}")
    mesh.merge_vertices()
    return mesh


def _genus(mesh: trimesh.Trimesh) -> int | None:
    """Return surface genus, or None when the mesh is not a closed manifold."""
    if not mesh.is_watertight:
        return None
    # euler = 2 - 2g  for a closed orientable surface with one component
    if mesh.body_count != 1:
        return None
    return int((2 - mesh.euler_number) // 2)


def _wall_thickness(
    mesh: trimesh.Trimesh, samples: int = 3000, seed: int = 0
) -> tuple[float | None, float | None, str, int]:
    """Estimate minimum wall thickness by inward ray casting.

    For each sampled surface point, a ray is cast along the inward normal and the
    distance to the next surface crossing is the local thickness. For a solid wire
    this measures its diameter, which is what the F3 finding needs.

    Returns ``(minimum, 5th percentile, method, sample_count)``. The 5th percentile
    is reported alongside the minimum because a single grazing ray can under-read;
    the percentile is the robust figure and the minimum is the alarming one.

    **Accuracy.** Validated 2026-08-12 against an analytic torus of known tube
    diameter 0.400 mm: the method returns 0.3990 mm, **-0.25%**. That residual is
    mesh faceting -- a polygonal cross-section inscribes the true circle -- and is
    systematic, not noise. With grazing rejection the minimum and the 5th
    percentile converge on clean geometry, which is the signal that the figure is
    trustworthy; a large gap between them means the part has features the sampling
    is still resolving poorly.

    **Remaining bias, toward reading thin, on tapered features.** On a cone the
    inward normal is not perpendicular to the axis, so the ray crosses a slightly
    shorter chord than the true diameter. Prefer the analytic value from
    ``master_parameters.derive_parameters`` when measuring MyJewl's own output. For
    a reference CAD file, where no derivation exists, this is the available
    measurement and its bias belongs beside the number.
    """
    if not mesh.is_watertight:
        return None, None, "skipped_not_watertight", 0
    try:
        points, face_index = trimesh.sample.sample_surface(mesh, samples, seed=seed)
    except TypeError:  # older trimesh has no seed kwarg
        np.random.seed(seed)
        points, face_index = trimesh.sample.sample_surface(mesh, samples)

    normals = mesh.face_normals[face_index]
    # Step just inside the surface so the origin face is not hit immediately.
    epsilon = 1e-4
    origins = points - normals * epsilon
    directions = -normals

    try:
        hit_tri, index_ray, locations = mesh.ray.intersects_id(
            ray_origins=origins,
            ray_directions=directions,
            multiple_hits=False,
            return_locations=True,
        )
    except Exception:
        return None, None, "unavailable_no_ray_engine", 0
    if len(index_ray) == 0:
        return None, None, "no_ray_hits", int(samples)

    distances = np.linalg.norm(locations - origins[index_ray], axis=1)

    # Reject grazing hits. A ray that clips a sharp edge or runs nearly tangent to
    # the far wall returns an arbitrarily small distance that is not a thickness.
    # A real thickness measurement requires the opposite surface to actually face
    # the ray, so keep only hits where the two are within 60 degrees of head-on.
    # Without this, 0.1-0.6% of rays return near-zero and poison the minimum --
    # measured on this repository's own parts, where an uncorrected minimum read
    # 0.0016 mm on a petal whose true section is ~0.36 mm.
    hit_normals = mesh.face_normals[hit_tri]
    alignment = np.abs((hit_normals * directions[index_ray]).sum(axis=1))
    keep = (distances > epsilon * 2) & (alignment >= _GRAZING_COS_MIN)
    distances = distances[keep]

    if distances.size == 0:
        return None, None, "no_valid_ray_hits", int(samples)
    return (
        float(distances.min()),
        float(np.percentile(distances, 5)),
        f"inward_ray_cast_{samples}_samples_grazing_rejected",
        int(distances.size),
    )


def measure_part(path: str | Path, *, thickness_samples: int = 3000) -> PartMetrics:
    """Measure a single STL part."""
    path = Path(path)
    mesh = _load(path)
    signed = float(mesh.volume)
    bounds = np.asarray(mesh.bounds, dtype=float)

    thickness = p05 = None
    method = "not_attempted"
    used = 0
    if thickness_samples > 0:
        thickness, p05, method, used = _wall_thickness(mesh, thickness_samples)

    return PartMetrics(
        name=path.stem,
        path=str(path),
        triangles=int(len(mesh.faces)),
        vertices=int(len(mesh.vertices)),
        watertight=bool(mesh.is_watertight),
        winding_consistent=bool(mesh.is_winding_consistent),
        oriented_outward=bool(signed > 0),
        signed_volume_mm3=signed,
        absolute_volume_mm3=abs(signed),
        surface_area_mm2=float(mesh.area),
        euler_number=int(mesh.euler_number),
        genus=_genus(mesh),
        bounds_mm=bounds.tolist(),
        max_dimension_mm=float((bounds[1] - bounds[0]).max()),
        min_wall_thickness_mm=thickness,
        wall_thickness_method=method,
        wall_thickness_samples=used,
        wall_thickness_p05_mm=p05,
    )


def _connected_components(
    meshes: dict[str, trimesh.Trimesh], tolerance: float
) -> int | None:
    """Count physically-touching groups among the parts."""
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        return None
    import itertools

    names = list(meshes)
    if not names:
        return 0
    sampled = {}
    trees = {}
    for name, mesh in meshes.items():
        pts = trimesh.sample.sample_surface(mesh, 20000)[0]
        sampled[name] = pts
        trees[name] = cKDTree(pts)

    adjacency: dict[str, set[str]] = {name: set() for name in names}
    for a, b in itertools.combinations(names, 2):
        ba, bb = meshes[a].bounds, meshes[b].bounds
        if (ba[0] > bb[1] + tolerance).any() or (bb[0] > ba[1] + tolerance).any():
            continue
        if trees[a].query(sampled[b])[0].min() <= tolerance:
            adjacency[a].add(b)
            adjacency[b].add(a)

    seen: set[str] = set()
    components = 0
    for start in names:
        if start in seen:
            continue
        components += 1
        stack = [start]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(adjacency[node])
    return components


def _union(meshes: Iterable[trimesh.Trimesh]) -> tuple[trimesh.Trimesh | None, str | None]:
    """Boolean-union the parts, repairing winding in-memory first.

    Winding is repaired on a *copy* for the purpose of measurement only. This does
    not fix the source defect (F1) -- it makes the union computable so the volume
    figure exists while the real fix is sequenced.
    """
    bodies = []
    for mesh in meshes:
        body = mesh.copy()
        if body.volume < 0:
            body.invert()
        bodies.append(body)
    if not bodies:
        return None, None
    if len(bodies) == 1:
        return bodies[0], "single_part_no_union_needed"
    for engine in ("manifold", "blender"):
        try:
            return trimesh.boolean.union(bodies, engine=engine), engine
        except Exception:
            continue
    return None, None


def measure_bundle(
    bundle_dir: str | Path,
    *,
    thickness_samples: int = 3000,
    compute_union: bool = True,
    compute_connectivity: bool = True,
) -> MeasurementReport:
    """Measure every part in a bundle and the metal assembly as a whole."""
    bundle = Path(bundle_dir)
    if not bundle.is_dir():
        raise NotADirectoryError(f"Not a bundle directory: {bundle}")

    stl_paths = sorted(p for p in bundle.glob("*.stl"))
    if not stl_paths:
        raise FileNotFoundError(f"No STL files in {bundle}")

    parts = [measure_part(p, thickness_samples=thickness_samples) for p in stl_paths]
    findings: list[Finding] = []

    # --- F1: orientation -------------------------------------------------
    inverted = [p for p in parts if p.watertight and not p.oriented_outward]
    for part in inverted:
        findings.append(
            Finding(
                "INVERTED_NORMALS", "critical", part.name,
                "Closed mesh with inward-facing normals: a slicer or boolean engine "
                "reads this as a void rather than metal. Watertightness checks pass it.",
                {"signed_volume_mm3": part.signed_volume_mm3},
            )
        )

    for part in parts:
        if not part.watertight:
            findings.append(
                Finding(
                    "NOT_WATERTIGHT", "critical", part.name,
                    "Mesh is not a closed manifold after vertex merge.",
                    {"euler_number": part.euler_number},
                )
            )

    # --- Assembly ---------------------------------------------------------
    metal_paths = [p for p in stl_paths if p.stem not in ASSEMBLY_EXCLUDED_STEMS]
    assembly: AssemblyMetrics | None = None
    if metal_paths:
        meshes = {p.stem: _load(p) for p in metal_paths}
        parts_sum = float(sum(abs(m.volume) for m in meshes.values()))
        area_sum = float(sum(m.area for m in meshes.values()))

        union_mesh = union_engine = None
        if compute_union:
            union_mesh, union_engine = _union(meshes.values())

        union_volume = float(union_mesh.volume) if union_mesh is not None else None
        overcount = None if union_volume is None else parts_sum - union_volume
        overcount_fraction = (
            None if not union_volume else overcount / union_volume
        )

        components = (
            _connected_components(meshes, CONNECTIVITY_TOLERANCE_MM)
            if compute_connectivity
            else None
        )

        assembly = AssemblyMetrics(
            part_count=len(meshes),
            parts_sum_mm3=parts_sum,
            union_mm3=union_volume,
            union_engine=union_engine,
            union_watertight=(
                None if union_mesh is None else bool(union_mesh.is_watertight)
            ),
            union_body_count=(
                None if union_mesh is None else int(union_mesh.body_count)
            ),
            overcount_mm3=overcount,
            overcount_fraction=overcount_fraction,
            connected_components=components,
            connectivity_tolerance_mm=CONNECTIVITY_TOLERANCE_MM,
            inverted_part_count=len(
                [n for n, m in meshes.items() if m.volume < 0]
            ),
            surface_area_mm2=area_sum,
        )

        # --- F2: does the bundle ship a castable solid? -------------------
        # Interpenetrating named parts are correct *if* the bundle also ships a
        # valid union: Blender needs the parts for mask IDs, a shop needs the
        # solid. The defect is shipping the parts with no union at all.
        shipped = next((p for p in parts if p.name in DERIVED_SOLID_STEMS), None)
        ships_valid_union = bool(
            shipped is not None
            and shipped.watertight
            and shipped.oriented_outward
            and union_volume
            and abs(shipped.absolute_volume_mm3 - union_volume) / union_volume <= 0.005
        )
        if union_volume is not None and overcount_fraction and overcount_fraction > 1e-6:
            evidence = {
                "parts_sum_mm3": parts_sum,
                "union_mm3": union_volume,
                "overcount_mm3": overcount,
                "overcount_fraction": overcount_fraction,
            }
            if ships_valid_union:
                findings.append(
                    Finding(
                        "PARTS_OVERLAP_BY_DESIGN", "note", None,
                        "Named parts interpenetrate, which is expected -- the bundle "
                        "ships a validated castable_solid, and the parts exist for "
                        "mask IDs and per-part measurement. Quote from the union.",
                        dict(evidence, castable_solid_mm3=shipped.absolute_volume_mm3),
                    )
                )
            else:
                findings.append(
                    Finding(
                        "PARTS_NOT_UNIONED", "critical", None,
                        "Parts interpenetrate and the bundle ships no valid castable "
                        "solid. The naive sum over-states metal volume, and therefore "
                        "over-states price.",
                        evidence,
                    )
                )
        if components is not None and components > 1:
            findings.append(
                Finding(
                    "DISCONNECTED_PARTS", "critical", None,
                    "Metal parts form more than one physically-touching group: the "
                    "assembly would not cast as a single piece.",
                    {
                        "connected_components": components,
                        "tolerance_mm": CONNECTIVITY_TOLERANCE_MM,
                    },
                )
            )

    # --- Cross-check Rhino's castable_solid against our own union ---------
    # Two independent boolean engines on the same input. Agreement is evidence
    # that neither is wrong; disagreement means one of them is, and which one is
    # not knowable from here -- so it is reported, never silently reconciled.
    castable = next(
        (p for p in parts if p.name in DERIVED_SOLID_STEMS), None
    )
    if castable is not None and assembly is not None and assembly.union_mm3:
        delta = castable.absolute_volume_mm3 - assembly.union_mm3
        fraction = abs(delta) / assembly.union_mm3
        if fraction > 0.005:
            findings.append(
                Finding(
                    "UNION_DISAGREEMENT", "critical", castable.name,
                    "Rhino's castable_solid and this module's independent union "
                    "disagree on volume by more than 0.5%. One of the two booleans "
                    "is wrong; which is not determinable from the mesh alone.",
                    {
                        "rhino_castable_mm3": castable.absolute_volume_mm3,
                        "independent_union_mm3": assembly.union_mm3,
                        "delta_mm3": delta,
                        "delta_fraction": fraction,
                    },
                )
            )
        if not castable.oriented_outward or not castable.watertight:
            findings.append(
                Finding(
                    "CASTABLE_SOLID_INVALID", "critical", castable.name,
                    "The shipped castable solid is not a valid outward-oriented "
                    "closed solid.",
                    {
                        "watertight": castable.watertight,
                        "oriented_outward": castable.oriented_outward,
                    },
                )
            )

    # --- F3: thin sections ------------------------------------------------
    # Reported against every uncalibrated floor MyJewl has no number for, so the
    # measurement lands even though no threshold can be applied.
    floors = {f.key: f for f in MANUFACTURING_FLOORS}
    wire_floor = floors.get("min_castable_wire_diameter_mm")
    # ``model.stl`` is the concatenation of every part, so inward rays cross the
    # gaps between disjoint bodies and return meaningless sub-micron distances.
    # It is measured and reported, but never a thin-section candidate.
    measured = [
        p for p in parts
        if p.min_wall_thickness_mm is not None
        and p.name not in ASSEMBLY_EXCLUDED_STEMS
    ]
    if measured and wire_floor is not None and wire_floor.value_mm is None:
        thinnest = min(measured, key=lambda p: p.min_wall_thickness_mm or math.inf)
        findings.append(
            Finding(
                "THIN_SECTION_UNCHECKED", "structural", thinnest.name,
                "Thinnest measured section in the assembly. MyJewl has no calibrated "
                "minimum castable wire diameter, so this is reported without a "
                "verdict -- it is a number for the casting shop, not a failure.",
                {
                    "min_wall_thickness_mm": thinnest.min_wall_thickness_mm,
                    "p05_mm": thinnest.wall_thickness_p05_mm,
                    "method": thinnest.wall_thickness_method,
                    "floor_status": "not_represented",
                },
            )
        )

    return MeasurementReport(
        bundle=str(bundle),
        metrics_version=METRICS_VERSION,
        assumptions_version=ASSUMPTIONS_VERSION,
        dimension_basis=DIMENSION_BASIS,
        parts=parts,
        assembly=assembly,
        findings=findings,
    )
