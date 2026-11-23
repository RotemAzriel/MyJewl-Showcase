#!/usr/bin/env python3
"""Stdlib-only geometry-manifest and Blender CLI logic."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROLE_CONTRACT = {
    "band": ("metal", 1),
    "setting": ("metal", 1),
    "gallery": ("metal", 1),
    "bezel": ("metal", 1),
    "basket": ("metal", 1),
    "center_stone": ("diamond", 2),
    "prongs": ("metal", 3),
}


@dataclass(frozen=True)
class RenderPart:
    role: str
    path: Path
    material_role: str
    mask_class: int
    object_name: str


@dataclass(frozen=True)
class RenderManifest:
    path: Path
    job_id: str
    export_id: str
    receipt_path: Path
    parts: tuple[RenderPart, ...]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MyJewl headless Blender render")
    parser.add_argument("--config-name", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--stl", type=Path)
    source.add_argument("--geometry-manifest", type=Path)
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--hdri-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--max-views", type=int, default=50)
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument(
        "--cycles-device",
        choices=("AUTO", "CPU", "GPU"),
        default="AUTO",
        help="Cycles device policy; CPU is useful for deterministic smoke runs.",
    )
    parser.add_argument(
        "--studio-light",
        action="store_true",
        help="Use deterministic built-in lighting instead of requiring HDRI files.",
    )
    parser.add_argument(
        "--view-seed",
        type=int,
        default=None,
        help="Explicit camera/HDRI schedule seed. Omit to seed from --config-name. "
        "Distinct seeds give the same config disjoint view draws.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--export-glb", type=Path)
    return parser


def parse_blender_args(argv: list[str]) -> argparse.Namespace:
    forwarded = argv[argv.index("--") + 1 :] if "--" in argv else argv
    return build_parser().parse_args(forwarded)


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def load_render_manifest(path: Path) -> RenderManifest:
    value = _read_object(path)
    root = path.parent
    for key in ("job_id", "export_id", "receipt", "parts"):
        if key not in value:
            raise ValueError(f"Geometry manifest missing {key}")
    receipt_path = root / str(value["receipt"])
    receipt = _read_object(receipt_path)
    if receipt.get("job_id") != value["job_id"] or receipt.get("export_id") != value["export_id"]:
        raise ValueError("Geometry manifest and export receipt disagree")

    roles: set[str] = set()
    paths: set[Path] = set()
    parts: list[RenderPart] = []
    for item in value["parts"]:
        role = str(item["role"])
        expected = ROLE_CONTRACT.get(role)
        if expected is None:
            raise ValueError(f"Unsupported render role: {role}")
        if (item["material_role"], item["mask_class"]) != expected:
            raise ValueError(f"Invalid material/mask mapping for role {role}")
        resolved = root / str(item["path"])
        if role in roles or resolved in paths:
            raise ValueError("Geometry manifest contains a duplicate role or path")
        if not resolved.is_file():
            raise ValueError(f"Geometry part is missing: {resolved}")
        roles.add(role)
        paths.add(resolved)
        parts.append(
            RenderPart(
                role=role,
                path=resolved,
                material_role=expected[0],
                mask_class=expected[1],
                object_name=f"myjewl_{role}",
            )
        )
    if not {"band", "center_stone", "prongs"}.issubset(roles):
        raise ValueError("Solitaire render manifest requires band, center_stone, and prongs")
    return RenderManifest(
        path=path,
        job_id=str(value["job_id"]),
        export_id=str(value["export_id"]),
        receipt_path=receipt_path,
        parts=tuple(parts),
    )


def expected_mask_values(manifest: RenderManifest) -> set[int]:
    return {0, *(part.mask_class for part in manifest.parts)}


def required_visible_mask_values(manifest: RenderManifest) -> set[int]:
    return {part.mask_class for part in manifest.parts}


def metal_alloy_from_params(params: dict[str, Any]) -> str:
    """Read the alloy from either legacy flat params or canonical specs."""
    flat_alloy = params.get("metal_alloy")
    if isinstance(flat_alloy, str) and flat_alloy.strip():
        return flat_alloy
    metal = params.get("metal")
    if isinstance(metal, dict):
        canonical_alloy = metal.get("alloy")
        if isinstance(canonical_alloy, str) and canonical_alloy.strip():
            return canonical_alloy
    return "14k_yellow"
