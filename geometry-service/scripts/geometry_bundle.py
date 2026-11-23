#!/usr/bin/env python3
"""Geometry-bundle construction and validation primitives.

This module validates data integrity only. It never updates the official
manufacturing-gate ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import trimesh


SCHEMA_VERSION = 1
DEFAULT_TRIANGLE_TOLERANCE_MM = 1e-5
REQUIRED_SOLITAIRE_ROLES = ("band", "center_stone", "prongs")
ROLE_CONTRACT = {
    "band": ("metal", 1),
    "setting": ("metal", 1),
    "gallery": ("metal", 1),
    "bezel": ("metal", 1),
    "basket": ("metal", 1),
    "center_stone": ("diamond", 2),
    "prongs": ("metal", 3),
    "halo": ("diamond", 4),
}


class BundleValidationError(ValueError):
    """Raised when an authoritative geometry bundle is invalid."""


@dataclass(frozen=True)
class MeshFacts:
    path: Path
    sha256: str
    byte_size: int
    vertex_count: int
    face_count: int
    bounds_mm: tuple[tuple[float, float, float], tuple[float, float, float]]
    triangles: np.ndarray

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path.name,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "vertex_count": self.vertex_count,
            "face_count": self.face_count,
            "triangle_count": self.face_count,
            "bounds_mm": [list(row) for row in self.bounds_mm],
        }


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BundleValidationError(f"Required JSON file is missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleValidationError(f"Could not read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BundleValidationError(f"JSON root must be an object: {path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_mesh(path: Path) -> MeshFacts:
    if not path.is_file():
        raise BundleValidationError(f"Required mesh is missing: {path}")
    if path.stat().st_size <= 84:
        raise BundleValidationError(f"Mesh is empty or too small to be a binary STL: {path}")

    try:
        loaded = trimesh.load(path, force="mesh", process=False)
    except Exception as exc:  # trimesh exposes several parser exception types
        raise BundleValidationError(f"Could not read mesh {path}: {exc}") from exc
    if not isinstance(loaded, trimesh.Trimesh):
        raise BundleValidationError(f"File did not load as one triangle mesh: {path}")

    vertices = np.asarray(loaded.vertices, dtype=np.float64)
    faces = np.asarray(loaded.faces)
    triangles = np.asarray(loaded.triangles, dtype=np.float64)
    if not len(vertices) or not len(faces) or triangles.shape != (len(faces), 3, 3):
        raise BundleValidationError(f"Mesh has no usable vertices/faces: {path}")
    if not np.isfinite(vertices).all() or not np.isfinite(triangles).all():
        raise BundleValidationError(f"Mesh contains non-finite coordinates: {path}")

    bounds = np.asarray(loaded.bounds, dtype=np.float64)
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
        raise BundleValidationError(f"Mesh has invalid bounds: {path}")
    extents = bounds[1] - bounds[0]
    if np.any(extents < 0) or float(np.max(extents)) > 1000.0:
        raise BundleValidationError(
            f"Mesh bounds are not plausible millimeter-scale geometry: {path}"
        )

    return MeshFacts(
        path=path,
        sha256=sha256_file(path),
        byte_size=path.stat().st_size,
        vertex_count=len(vertices),
        face_count=len(faces),
        bounds_mm=(
            tuple(float(value) for value in bounds[0]),
            tuple(float(value) for value in bounds[1]),
        ),
        triangles=triangles,
    )


def canonical_triangle_hash(
    triangle_sets: Iterable[np.ndarray],
    tolerance_mm: float = DEFAULT_TRIANGLE_TOLERANCE_MM,
) -> tuple[int, str]:
    """Hash a winding- and ordering-independent triangle multiset.

    Coordinates are quantized to a fixed integer grid. Duplicate triangles are
    intentionally retained, so missing or duplicated faces change the hash.
    """
    if not np.isfinite(tolerance_mm) or tolerance_mm <= 0:
        raise ValueError("tolerance_mm must be a finite positive number")
    arrays = [np.asarray(value, dtype=np.float64) for value in triangle_sets]
    if not arrays:
        raise ValueError("At least one triangle array is required")
    triangles = np.concatenate(arrays, axis=0)
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3):
        raise ValueError("Triangle arrays must have shape (N, 3, 3)")
    if not np.isfinite(triangles).all():
        raise BundleValidationError("Cannot hash non-finite triangle coordinates")

    quantized = np.rint(triangles / tolerance_mm).astype("<i8")
    vertex_dtype = np.dtype([("x", "<i8"), ("y", "<i8"), ("z", "<i8")])
    vertices = np.ascontiguousarray(quantized).view(vertex_dtype).reshape(-1, 3)
    vertices.sort(axis=1, order=("x", "y", "z"))
    flattened = np.ascontiguousarray(vertices.view("<i8").reshape(-1, 9))
    records = flattened.view(np.dtype((np.void, flattened.dtype.itemsize * 9))).ravel()
    records.sort()
    return len(records), hashlib.sha256(records.tobytes()).hexdigest()


def receipt_file_entry(facts: MeshFacts) -> dict[str, Any]:
    return {
        "sha256": facts.sha256,
        "byte_size": facts.byte_size,
        "triangle_count": facts.face_count,
        "bounds_mm": [list(row) for row in facts.bounds_mm],
    }


def _require_keys(value: dict[str, Any], keys: Iterable[str], label: str) -> None:
    missing = [key for key in keys if key not in value]
    if missing:
        raise BundleValidationError(f"{label} missing required key(s): {', '.join(missing)}")


def _validate_receipt(
    receipt: dict[str, Any],
    job_id: str,
    export_id: str,
    facts_by_name: dict[str, MeshFacts],
    numeric_tolerance_mm: float,
) -> None:
    _require_keys(
        receipt,
        (
            "schema_version",
            "export_id",
            "job_id",
            "gh_definition_sha256",
            "gh_definition_version",
            "exported_at_utc",
            "files",
        ),
        "export_receipt.json",
    )
    if receipt["schema_version"] != SCHEMA_VERSION:
        raise BundleValidationError("Unsupported export receipt schema_version")
    if receipt["job_id"] != job_id or receipt["export_id"] != export_id:
        raise BundleValidationError("Receipt job_id/export_id does not match manifest")
    definition_hash = receipt["gh_definition_sha256"]
    if (
        not isinstance(definition_hash, str)
        or len(definition_hash) != 64
        or any(char not in "0123456789abcdefABCDEF" for char in definition_hash)
    ):
        raise BundleValidationError("Receipt gh_definition_sha256 is not a SHA-256 digest")

    files = receipt["files"]
    if not isinstance(files, dict):
        raise BundleValidationError("Receipt files must be an object")
    for name, facts in facts_by_name.items():
        recorded = files.get(name)
        if not isinstance(recorded, dict):
            raise BundleValidationError(f"Receipt is missing facts for {name}")
        _require_keys(
            recorded,
            ("sha256", "byte_size", "triangle_count", "bounds_mm"),
            f"receipt files.{name}",
        )
        if recorded["sha256"] != facts.sha256:
            raise BundleValidationError(f"Receipt SHA-256 mismatch for {name}")
        if recorded["byte_size"] != facts.byte_size:
            raise BundleValidationError(f"Receipt byte-size mismatch for {name}")
        if recorded["triangle_count"] != facts.face_count:
            raise BundleValidationError(f"Receipt triangle-count mismatch for {name}")
        try:
            recorded_bounds = np.asarray(recorded["bounds_mm"], dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise BundleValidationError(f"Receipt bounds are invalid for {name}") from exc
        if recorded_bounds.shape != (2, 3) or not np.allclose(
            recorded_bounds,
            np.asarray(facts.bounds_mm),
            rtol=0.0,
            atol=numeric_tolerance_mm,
        ):
            raise BundleValidationError(f"Receipt bounds mismatch for {name}")

    extra = set(files) - set(facts_by_name)
    if extra:
        raise BundleValidationError(
            "Receipt contains files not declared by the manifest: " + ", ".join(sorted(extra))
        )


def validate_bundle(
    bundle_dir: Path | str,
    *,
    triangle_tolerance_mm: float = DEFAULT_TRIANGLE_TOLERANCE_MM,
    numeric_tolerance_mm: float = 1e-4,
) -> dict[str, Any]:
    root = Path(bundle_dir)
    manifest_path = root / "geometry_manifest.json"
    canonical_path = root / "canonical_spec.json"
    manifest = read_json(manifest_path)
    canonical = read_json(canonical_path)

    _require_keys(
        manifest,
        (
            "schema_version",
            "job_id",
            "export_id",
            "units",
            "coordinate_system",
            "receipt",
            "combined",
            "parts",
        ),
        "geometry_manifest.json",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise BundleValidationError("Unsupported geometry manifest schema_version")
    if manifest["units"] != "mm" or manifest["coordinate_system"] != "Rhino_Z_up":
        raise BundleValidationError("Bundle must use millimeters and Rhino_Z_up")
    job_id = manifest["job_id"]
    export_id = manifest["export_id"]
    if canonical.get("job", {}).get("job_id") != job_id:
        raise BundleValidationError("canonical_spec.json job ID does not match manifest")

    combined = manifest["combined"]
    parts = manifest["parts"]
    if not isinstance(combined, dict) or not isinstance(parts, list):
        raise BundleValidationError("Manifest combined/parts values have invalid types")
    _require_keys(combined, ("path",), "manifest combined")
    if not parts:
        raise BundleValidationError("Manifest must contain at least one part")

    roles: set[str] = set()
    paths: set[str] = set()
    facts_by_name: dict[str, MeshFacts] = {}
    part_facts: list[MeshFacts] = []
    combined_name = str(combined["path"])
    paths.add(combined_name)
    combined_facts = inspect_mesh(root / combined_name)
    facts_by_name[combined_name] = combined_facts

    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            raise BundleValidationError(f"Manifest part {index} must be an object")
        _require_keys(
            part,
            ("role", "path", "material_role", "mask_class"),
            f"manifest part {index}",
        )
        role = str(part["role"])
        path_name = str(part["path"])
        if role in roles:
            raise BundleValidationError(f"Duplicate manifest role: {role}")
        if path_name in paths:
            raise BundleValidationError(f"Duplicate manifest path: {path_name}")
        roles.add(role)
        paths.add(path_name)

        expected = ROLE_CONTRACT.get(role)
        if expected is None:
            raise BundleValidationError(f"Unsupported geometry role: {role}")
        if (part["material_role"], part["mask_class"]) != expected:
            raise BundleValidationError(
                f"Role {role} must use material/class {expected[0]}/{expected[1]}"
            )
        facts = inspect_mesh(root / path_name)
        facts_by_name[path_name] = facts
        part_facts.append(facts)

    missing_roles = set(REQUIRED_SOLITAIRE_ROLES) - roles
    if missing_roles:
        raise BundleValidationError(
            "Solitaire bundle missing required role(s): " + ", ".join(sorted(missing_roles))
        )
    if "halo" in roles:
        raise BundleValidationError("Halo class 4 is reserved and not valid in a solitaire bundle")

    receipt_name = str(manifest["receipt"])
    if receipt_name in paths:
        raise BundleValidationError("Receipt path collides with a mesh path")
    receipt = read_json(root / receipt_name)
    _validate_receipt(
        receipt,
        str(job_id),
        str(export_id),
        facts_by_name,
        numeric_tolerance_mm,
    )

    part_bounds = np.asarray([facts.bounds_mm for facts in part_facts], dtype=np.float64)
    union_bounds = np.vstack(
        (np.min(part_bounds[:, 0, :], axis=0), np.max(part_bounds[:, 1, :], axis=0))
    )
    combined_bounds = np.asarray(combined_facts.bounds_mm, dtype=np.float64)
    if np.any(union_bounds[0] < combined_bounds[0] - numeric_tolerance_mm) or np.any(
        union_bounds[1] > combined_bounds[1] + numeric_tolerance_mm
    ):
        raise BundleValidationError("Combined bounds do not contain all part bounds")
    if not np.allclose(
        union_bounds,
        combined_bounds,
        rtol=0.0,
        atol=numeric_tolerance_mm,
    ):
        raise BundleValidationError("Combined bounds do not match the union of part bounds")

    combined_count, combined_hash = canonical_triangle_hash(
        [combined_facts.triangles],
        triangle_tolerance_mm,
    )
    parts_count, parts_hash = canonical_triangle_hash(
        [facts.triangles for facts in part_facts],
        triangle_tolerance_mm,
    )
    if combined_count != parts_count:
        raise BundleValidationError(
            f"Combined triangle count {combined_count} != parts count {parts_count}"
        )
    if combined_hash != parts_hash:
        raise BundleValidationError(
            "Combined STL triangle multiset does not match the concatenated parts"
        )

    return {
        "status": "valid",
        "job_id": job_id,
        "export_id": export_id,
        "units": "mm",
        "coordinate_system": "Rhino_Z_up",
        "triangle_tolerance_mm": triangle_tolerance_mm,
        "combined_triangle_hash": combined_hash,
        "combined_triangle_count": combined_count,
        "union_bounds_mm": union_bounds.tolist(),
        "files": {
            name: facts.to_json()
            for name, facts in sorted(facts_by_name.items())
        },
        "official_geometry_gate_state_modified": False,
    }

