#!/usr/bin/env python3
"""Verify the approved Goal 0011 V4 visual reference in the local Studio UI.

The proof checks the exact bounded catalog option, live canonical/constraint
gates, the receipt-backed browser-mesh derivative used by WebGL, and the named
procedural scene retained only as its safe fallback. Manufacturing gates remain
non-certifying.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_mvp import manual_configurator


V4_HEAD_ID = "head_v4_mid_petal_4_prong"
V4_BAND_ID = "band_v4_split_solitaire"
V4_TEMPLATE_ID = "goal0011-realism-first-round-solitaire-master-v4"
V4_REFERENCE_ID = "reference_goal0011_v4_mid_petal_solitaire"
V4_ASSET_MANIFEST = (
    ROOT
    / "fast_mvp"
    / "static"
    / "assets"
    / "goal0011_v4"
    / "solitaire_realism_master_v4.browser_mesh.json"
)

#: What the procedural reconstruction must declare itself to be.
#:
#: This audit originally pinned ``preview_only_not_rhino``. Goal 0012A (2026-08-01)
#: renamed it to the string below when it made procedural geometry a
#: selection-time operational aid *outside* the preview contract — a more precise
#: name for the same property, not a weakening of it. The audit was not updated
#: and silently reported ``not_ready`` from that day until 2026-08-06, while
#: `SESSION_GOALS.md`'s Goal 0011 entry still recorded it as ``ready``. Nothing asserted the verdict,
#: so nothing caught it; ``test_the_ui_promotion_audit_still_reports_ready`` now
#: does. Pin one current value: accepting the retired string as well would let a
#: resurrected vocabulary pass unnoticed.
PROCEDURAL_FALLBACK_AUTHORITY = "operational_out_of_preview_design_aid"
BOUNDED_PAYLOAD = {
    "stone_shape": "round",
    "stone_length_mm": 6.5,
    "stone_width_mm": 6.5,
    "stone_depth_mm": 3.9,
    "stone_carat_eq": 1.0,
    "ring_size_us": 6.5,
    "metal": {
        "color_family": "yellow",
        "purity": "14k",
        "finish": "polished",
    },
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scene_counts(preview: dict[str, Any]) -> dict[str, Any]:
    module_uri = (ROOT / "fast_mvp" / "static" / "ring3d.js").as_uri()
    source = f"""
      import {{ buildPreviewReference }} from {module_uri!r};
      const group = await buildPreviewReference({json.dumps(preview)});
      const counts = {{}};
      group.traverse(object => {{ counts[object.name] = (counts[object.name] || 0) + 1; }});
      console.log(JSON.stringify({{
        geometryAuthority: group.userData.geometryAuthority,
        geometrySignature: group.userData.geometrySignature,
        counts,
      }}));
    """
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise ValueError("preview scene inspection did not return an object")
    return value


def _browser_asset(receipt_path: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    manifest = _load(V4_ASSET_MANIFEST)
    binary = manifest.get("binary") if isinstance(manifest.get("binary"), dict) else {}
    binary_path = V4_ASSET_MANIFEST.parent / str(binary.get("file") or "")
    source_obj_path = receipt_path.parent / str(manifest.get("source_obj") or "")
    manifest_parts = manifest.get("parts") if isinstance(manifest.get("parts"), list) else []
    receipt_parts = receipt.get("parts") if isinstance(receipt.get("parts"), list) else []
    declared_parts = [
        {
            "name": part.get("name"),
            "role": part.get("role"),
            "triangles": part.get("triangles"),
        }
        for part in manifest_parts
        if isinstance(part, dict)
    ]
    expected_parts = [
        {
            "name": part.get("name"),
            "role": part.get("role"),
            "triangles": part.get("triangles"),
        }
        for part in receipt_parts
        if isinstance(part, dict)
    ]
    header_ready = False
    if binary_path.is_file() and binary_path.stat().st_size >= 16:
        with binary_path.open("rb") as stream:
            magic, vertex_count, index_count = struct.unpack("<8sII", stream.read(16))
        header_ready = (
            magic == b"MYJV4B1\0"
            and vertex_count == manifest.get("vertex_count")
            and index_count == manifest.get("index_count")
        )
    ready = (
        manifest.get("schema") == "myjewl.goal0011.receipt_backed_browser_mesh.v1"
        and manifest.get("authority") == "receipt_backed_rhino_derived_browser_preview"
        and manifest.get("manufacturing_authority") is False
        and manifest.get("manufacturing_gates") == "pending_not_run_needs_calibration"
        and manifest.get("template_id") == V4_TEMPLATE_ID
        and manifest.get("source_receipt_sha256") == _sha256(receipt_path)
        and source_obj_path.is_file()
        and manifest.get("source_obj_sha256") == _sha256(source_obj_path)
        and binary_path.is_file()
        and binary.get("byte_size") == binary_path.stat().st_size
        and binary.get("sha256") == _sha256(binary_path)
        and manifest.get("vertex_count") == 78281
        and manifest.get("triangle_count") == 149064
        and manifest.get("index_count") == 447192
        and declared_parts == expected_parts
        and header_ready
    )
    return {
        "ready": ready,
        "manifest": str(V4_ASSET_MANIFEST.relative_to(ROOT)),
        "manifest_sha256": _sha256(V4_ASSET_MANIFEST),
        "binary": str(binary_path.relative_to(ROOT)) if binary_path.is_file() else str(binary_path),
        "binary_sha256": _sha256(binary_path) if binary_path.is_file() else None,
        "source_obj": str(source_obj_path),
        "source_obj_sha256": _sha256(source_obj_path) if source_obj_path.is_file() else None,
        "source_receipt_sha256": manifest.get("source_receipt_sha256"),
        "part_count": len(manifest_parts),
        "vertex_count": manifest.get("vertex_count"),
        "triangle_count": manifest.get("triangle_count"),
        "geometry_authority": manifest.get("authority"),
        "manufacturing_authority": manifest.get("manufacturing_authority"),
    }


def build_report(receipt_path: Path, differential_path: Path) -> dict[str, Any]:
    differential = _load(differential_path)
    receipt = _load(receipt_path)
    receipt_sha = _sha256(receipt_path)
    options = manual_configurator.compatible_options(dict(BOUNDED_PAYLOAD))
    heads = {
        item["head_style_id"]: item
        for item in options.get("head_styles", [])
        if isinstance(item, dict) and isinstance(item.get("head_style_id"), str)
    }
    bands = options.get("band_styles_by_head", {}).get(V4_HEAD_ID, [])
    references = {
        item.get("reference_configuration_id"): item
        for item in options.get("reference_configurations", [])
        if isinstance(item, dict)
    }
    configured = manual_configurator.configure({
        **BOUNDED_PAYLOAD,
        "head_style_id": V4_HEAD_ID,
        "band_style_id": V4_BAND_ID,
    })
    scene = _scene_counts(configured.get("preview") or {})
    browser_asset = _browser_asset(receipt_path, receipt)
    counts = scene.get("counts") if isinstance(scene.get("counts"), dict) else {}
    v4_head = heads.get(V4_HEAD_ID) or {}
    visual_reference = (
        v4_head.get("visual_reference")
        if isinstance(v4_head.get("visual_reference"), dict)
        else {}
    )
    exact_band_pair = [item.get("band_style_id") for item in bands] == [V4_BAND_ID]
    live_gate_ready = (
        configured.get("status") == "accepted"
        and configured.get("validation", {}).get("schema_accepted") is True
        and configured.get("validation", {}).get("constraint_accepted") is True
        and set((configured.get("flat_parameters", {}).get("geometry_gates") or {}).values())
        == {"pending"}
    )
    procedural_fallback_ready = (
        scene.get("geometryAuthority") == PROCEDURAL_FALLBACK_AUTHORITY
        and counts.get("v4-mid-petal-split-shoulder") == 4
        and counts.get("v4-petal-gallery-support") == 4
        and counts.get("v4-center-prong") == 20
        and counts.get("v4-rounded-prong-tip") == 4
        and counts.get("v4-basket-lower-rail") == 1
        and counts.get("v4-stone-seat-rail") == 1
    )
    reference_ready = (
        visual_reference.get("status") == "approved"
        and visual_reference.get("template_id") == V4_TEMPLATE_ID
        and visual_reference.get("receipt_sha256") == receipt_sha
        and V4_REFERENCE_ID in references
        and configured.get("selection", {}).get("reference_configuration_id")
        == V4_REFERENCE_ID
    )
    differential_ready = (
        differential.get("status") == "ready"
        and differential.get("ready_for_visual_reference_promotion") is True
        and differential.get("after", {}).get("receipt_sha256") == receipt_sha
    )
    ready = (
        options.get("catalog_revision") == "modular-rings-v5"
        and V4_HEAD_ID in heads
        and exact_band_pair
        and reference_ready
        and differential_ready
        and live_gate_ready
        and procedural_fallback_ready
        and browser_asset.get("ready") is True
    )
    source_files = [
        ROOT / "fast_mvp" / "manual_configurator.py",
        ROOT / "fast_mvp" / "static" / "ring3d.js",
        ROOT / "fast_mvp" / "static" / "studio.js",
        ROOT / "fast_mvp" / "static" / "studio.css",
        ROOT / "scripts" / "build_goal0011_browser_asset.py",
    ]
    return {
        "schema": "myjewl.goal0011.ui_promotion.v2",
        "authority": "verified_receipt_backed_rhino_derived_browser_preview",
        "status": "ready" if ready else "not_ready",
        "catalog_revision": options.get("catalog_revision"),
        "target_receipt": str(receipt_path),
        "target_receipt_sha256": receipt_sha,
        "differential_evidence": str(differential_path),
        "differential_evidence_sha256": _sha256(differential_path),
        "head_style_id": V4_HEAD_ID,
        "band_style_id": V4_BAND_ID,
        "reference_configuration_id": V4_REFERENCE_ID,
        "exact_bounded_compatibility_ready": exact_band_pair,
        "reference_metadata_ready": reference_ready,
        "receipt_differential_ready": differential_ready,
        "live_schema_and_constraint_gate_ready": live_gate_ready,
        "receipt_backed_browser_asset_ready": browser_asset.get("ready"),
        "receipt_backed_browser_asset": browser_asset,
        "procedural_fallback_scene_ready": procedural_fallback_ready,
        "procedural_fallback_scene_counts": counts,
        "geometry_signature": scene.get("geometrySignature"),
        "geometry_authority": browser_asset.get("geometry_authority"),
        "source_files": [
            {"file": str(path.relative_to(ROOT)), "sha256": _sha256(path)}
            for path in source_files
        ],
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("differential", type=Path)
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(argv)
    report = build_report(args.receipt, args.differential)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
