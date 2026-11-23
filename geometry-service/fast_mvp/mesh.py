"""Draft STL generation for the fast MyJewl MVP."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from jewelry_gaas_foundation.stl_gates import check_mesh_closedness


def generate_draft_assets(canonical_spec: dict[str, Any], output_root: str | Path) -> dict[str, Any]:
    """Generate a local draft STL and Rhino handoff JSON for a canonical spec."""
    job_id = str(canonical_spec["job"]["job_id"])
    run_dir = Path(output_root) / job_id
    run_dir.mkdir(parents=True, exist_ok=True)

    canonical_path = run_dir / "canonical_spec.json"
    stl_path = run_dir / "draft_preview.stl"
    handoff_path = run_dir / "rhino_handoff.json"

    canonical_path.write_text(json.dumps(canonical_spec, indent=2, sort_keys=True), encoding="utf-8")
    mesh = build_solitaire_mesh(canonical_spec)
    mesh.export(stl_path)
    gate = check_mesh_closedness(stl_path)

    handoff = {
        "job_id": job_id,
        "mode": "rhino_compute_handoff",
        "definition_contract": "canonical_spec_v0_1",
        "canonical_spec_path": str(canonical_path),
        "requested_assets": ["STL"],
        "notes": [
            "Use this canonical spec for Rhino/Grasshopper generation.",
            "draft_preview.stl is an instant visual approximation, not a shop-certified model.",
        ],
    }
    handoff_path.write_text(json.dumps(handoff, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "job_id": job_id,
        "run_dir": str(run_dir),
        "canonical_spec_path": str(canonical_path),
        "draft_stl_path": str(stl_path),
        "rhino_handoff_path": str(handoff_path),
        "mesh": {
            "faces": int(len(mesh.faces)),
            "vertices": int(len(mesh.vertices)),
            "bounds_mm": np.round(mesh.bounds, 3).tolist(),
        },
        "gate": {
            "gate_id": gate.gate_id,
            "status": gate.status,
            "reason": gate.reason,
            "metrics": gate.metrics,
        },
    }


def build_solitaire_mesh(spec: dict[str, Any]) -> trimesh.Trimesh:
    """Build a fast visual solitaire mesh in millimeters."""
    ring = spec["ring"]
    stone = next(item for item in spec["stones"] if item["role"] == "center")
    setting = spec["setting"]

    inner_diameter = _inner_diameter_mm(ring)
    band_thickness = max(1.2, float(ring.get("band_thickness_mm") or 1.6))
    band_width = max(1.4, float(ring.get("band_width_mm") or 2.0))
    major_radius = inner_diameter / 2.0 + band_thickness / 2.0
    minor_radius = band_thickness / 2.0

    band_transform = trimesh.transformations.rotation_matrix(math.pi / 2.0, [1, 0, 0])
    band = trimesh.creation.torus(
        major_radius=major_radius,
        minor_radius=minor_radius,
        major_sections=128,
        minor_sections=18,
        transform=band_transform,
    )

    dims = stone["dimensions_mm"]
    stone_length = float(dims["length"])
    stone_width = float(dims["width"])
    stone_depth = float(dims.get("depth") or stone_width * 0.6)
    stone_base_z = major_radius + minor_radius * 0.55
    gem = _build_stone(stone["shape"], stone_length, stone_width, stone_depth)
    gem.apply_translation([0, 0, stone_base_z])

    prongs = []
    prong_count = int(setting.get("prong_count") or 0)
    if setting.get("center_setting") == "prong" and prong_count > 0:
        prongs = _build_prongs(prong_count, stone_width, stone_length, stone_depth, stone_base_z)

    bridge = trimesh.creation.cylinder(
        radius=max(0.25, band_width * 0.15),
        height=max(stone_width * 0.86, 3.6),
        sections=24,
        transform=trimesh.transformations.rotation_matrix(math.pi / 2.0, [1, 0, 0]),
    )
    bridge.apply_translation([0, 0, major_radius + minor_radius * 0.25])

    return trimesh.util.concatenate([band, bridge, gem, *prongs])


def _build_stone(shape: str, length: float, width: float, depth: float) -> trimesh.Trimesh:
    sections = {
        "round": 64,
        "oval": 64,
        "cushion": 32,
        "emerald": 4,
        "radiant": 8,
        "princess": 4,
        "pear": 48,
    }.get(shape, 48)
    gem = trimesh.creation.cone(radius=width / 2.0, height=depth, sections=sections)
    if shape in {"oval", "pear", "emerald", "radiant"}:
        gem.apply_scale([length / max(width, 0.01), 1.0, 1.0])
    if shape in {"emerald", "princess"}:
        gem.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 4.0, [0, 0, 1]))
    return gem


def _build_prongs(
    count: int,
    stone_width: float,
    stone_length: float,
    stone_depth: float,
    stone_base_z: float,
) -> list[trimesh.Trimesh]:
    prongs: list[trimesh.Trimesh] = []
    radius = max(0.18, stone_width * 0.035)
    height = max(2.4, stone_depth * 0.78)
    rx = stone_length / 2.0 + radius * 0.8
    ry = stone_width / 2.0 + radius * 0.8
    for index in range(count):
        angle = (math.tau / count) * index + math.pi / count
        x = math.cos(angle) * rx
        y = math.sin(angle) * ry
        prong = trimesh.creation.cylinder(radius=radius, height=height, sections=14)
        prong.apply_translation([x, y, stone_base_z + height / 2.0])
        prongs.append(prong)
    return prongs


def _inner_diameter_mm(ring: dict[str, Any]) -> float:
    if ring.get("size_standard") == "US":
        try:
            return 11.63 + 0.8128 * float(ring["size"])
        except (TypeError, ValueError):
            pass
    return 16.91

