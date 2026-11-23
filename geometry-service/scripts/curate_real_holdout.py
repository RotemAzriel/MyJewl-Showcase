#!/usr/bin/env python3
"""Validate and normalize the real mini-holdout (Goal 0005, Stage 0 / plan §4).

The plan calls for **30-50 real photos with mounting ground truth taken from
product specifications, two-source checked** to control label noise. Curation is
operator work: a human finds the photo and reads the setting/prong count off two
independent product pages. This script is the gate on that work — it validates
what the operator produced and refuses anything it cannot substantiate.

It never invents a label. An item whose mounting field is not backed by two
distinct sources is reported as a defect, not silently accepted, because a
holdout with fabricated ground truth would make every G-A/G-C number meaningless.

**Labels cover the mounting axes only** (product contract, 2026-07-27): centre
setting, prong count, and optionally the band-width / band-thickness ratios.
Metal (colour + karat/purity) is a **typed input**, not a label — it belongs in
``typed`` and needs no two-source backing, because the image lane never infers or
scores it. A mounting label for metal is reported as a defect.

Manifest shape (``holdout_manifest.json``, paths relative to the manifest)::

    {
      "schema_version": 1,
      "items": [
        {
          "item_id": "stuller-123456",
          "image_path": "images/stuller-123456.jpg",
          "archetype": "solitaire",
          "typed": {"stone_shape": "round", "stone_length_mm": 6.5,
                    "stone_width_mm": 6.5, "stone_depth_mm": 4.0,
                    "ring_size_us": 6.5, "stone_carat_eq": 1.0,
                    "metal_color_family": "white", "metal_karat": "14k"},
          "labels": {"center_setting": "prong", "prong_count": 6,
                     "band_width_ratio": 0.31, "band_thickness_ratio": 0.25},
          "prong_count_visible": true,
          "sources": [
            {"url": "https://...", "retrieved_utc": "2026-07-25T00:00:00Z",
             "fields": ["center_setting", "prong_count", "archetype"]},
            {"url": "https://...", "retrieved_utc": "2026-07-25T00:00:00Z",
             "fields": ["center_setting", "prong_count", "archetype"]}
          ]
        }
      ]
    }

Distractor/OOD items (``archetype`` other than ``solitaire``) still need a
sourced archetype but carry no mounting labels — they measure OOD rejection.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fast_mvp.image_extractor import ARCHETYPE_CHOICES, SUPPORTED_ARCHETYPES  # noqa: E402
from fast_mvp.pipeline import SETTING_CHOICES, _parse_stone_inputs  # noqa: E402
from fast_mvp.typed_inputs import (  # noqa: E402
    TypedInputError,
    apply_certificate,
    parse_metal_spec,
    stone_spec_source,
)

# Plan §4 Stage 0: 30-50 real photos. Below the floor the holdout is reported as
# under-strength rather than rejected — a partial holdout is still useful for
# wiring, it just cannot carry a gate verdict.
MIN_SUPPORTED_ITEMS = 30
RECOMMENDED_MAX_ITEMS = 50
REQUIRED_SOURCE_COUNT = 2

# Two-source-backed mounting labels: exactly the axes the image lane infers.
MOUNTING_FIELDS = (
    "center_setting",
    "prong_count",
)
OPTIONAL_MOUNTING_FIELDS = ("band_width_ratio", "band_thickness_ratio")

# Typed inputs a source may cite for provenance. They are typed by the operator,
# so they never require two-source backing — and metal is never a label.
TYPED_PROVENANCE_FIELDS = (
    "metal_color_family",
    "metal_karat",
    "metal_purity",
    "stone_shape",
    "stone_dimensions_mm",
    "ring_size_us",
)


def _source_origin(url: str) -> str:
    """Registrable-ish host used to judge source independence."""
    host = (urlsplit(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _validate_labels(labels: Any, defects: list[str]) -> dict[str, Any]:
    if not isinstance(labels, dict):
        defects.append("labels must be an object")
        return {}
    clean: dict[str, Any] = {}

    setting = str(labels.get("center_setting", "")).strip().lower()
    if setting not in SETTING_CHOICES:
        defects.append(f"center_setting must be one of {', '.join(SETTING_CHOICES)}")
    else:
        clean["center_setting"] = setting

    if "metal_color_family" in labels or "metal_karat" in labels:
        defects.append(
            "metal is a typed input (colour + karat/purity), not a mounting label; "
            "move it to typed.metal_color_family / typed.metal_karat"
        )

    try:
        prongs = int(labels["prong_count"])
    except (KeyError, TypeError, ValueError):
        defects.append("prong_count must be an integer")
    else:
        if prongs < 0:
            defects.append("prong_count must be >= 0")
        elif setting == "bezel" and prongs != 0:
            defects.append("a bezel-set item must have prong_count 0")
        else:
            clean["prong_count"] = prongs

    for name in OPTIONAL_MOUNTING_FIELDS:
        raw_ratio = labels.get(name)
        if raw_ratio is None:
            continue
        try:
            ratio = float(raw_ratio)
        except (TypeError, ValueError):
            defects.append(f"{name} must be a number when present")
            continue
        if ratio <= 0:
            defects.append(f"{name} must be positive")
        else:
            clean[name] = ratio
    return clean


def _validate_sources(
    sources: Any,
    labelled_fields: set[str],
    *,
    require_fields: bool,
    defects: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(sources, list) or not sources:
        defects.append("sources must be a non-empty list")
        return []

    clean: list[dict[str, Any]] = []
    coverage: dict[str, set[str]] = {}
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            defects.append(f"sources[{index}] must be an object")
            continue
        url = str(source.get("url", "")).strip()
        origin = _source_origin(url)
        if not url.startswith(("http://", "https://")) or not origin:
            defects.append(f"sources[{index}].url must be an http(s) URL")
            continue
        retrieved = str(source.get("retrieved_utc", "")).strip()
        if not retrieved:
            defects.append(f"sources[{index}].retrieved_utc is required")
        fields = source.get("fields")
        if not isinstance(fields, list) or not fields:
            defects.append(f"sources[{index}].fields must be a non-empty list")
            continue
        named = [str(item).strip() for item in fields]
        unknown = sorted(
            set(named)
            - set(MOUNTING_FIELDS)
            - set(OPTIONAL_MOUNTING_FIELDS)
            - set(TYPED_PROVENANCE_FIELDS)
            - {"archetype"}
        )
        if unknown:
            defects.append(f"sources[{index}] cites unknown field(s): {', '.join(unknown)}")
        for name in named:
            coverage.setdefault(name, set()).add(origin)
        clean.append({"url": url, "retrieved_utc": retrieved, "fields": sorted(set(named))})

    required = set(labelled_fields) if require_fields else {"archetype"}
    for name in sorted(required):
        origins = coverage.get(name, set())
        if len(origins) < REQUIRED_SOURCE_COUNT:
            defects.append(
                f"'{name}' is backed by {len(origins)} independent source(s); "
                f"{REQUIRED_SOURCE_COUNT} distinct origins are required"
            )
    return clean


def _validate_image(path: Path, defects: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        defects.append(f"image not found: {path}")
        return None
    try:
        from PIL import Image

        with Image.open(path) as opened:
            opened.load()
            width, height = opened.size
            fmt = opened.format
    except Exception as exc:  # noqa: BLE001 - Pillow exposes several decode errors
        defects.append(f"image could not be decoded: {exc}")
        return None
    if min(width, height) < 128:
        defects.append(f"image is too small for retrieval ({width}x{height}, min side 128)")
    return {"width": width, "height": height, "format": fmt, "byte_size": path.stat().st_size}


def _resolve_typed(raw_typed: dict[str, Any], defects: list[str]):
    """Fold a supplied GIA/IGI certificate into the typed stone fields."""
    try:
        return apply_certificate(raw_typed)
    except TypedInputError as exc:
        defects.append(f"certificate invalid: {exc}")
        return raw_typed, None


def _resolve_metal(
    raw_typed: dict[str, Any],
    defects: list[str],
    *,
    required: bool,
):
    """Typed metal: colour + karat/purity. Never inferred from the photograph."""
    has_metal = any(
        raw_typed.get(key) not in (None, "")
        for key in ("metal", "metal_color_family", "metal_alloy", "metal_karat")
    )
    if not has_metal:
        if required:
            defects.append(
                "typed.metal_color_family + typed.metal_karat are required "
                "(metal is a typed input, never read from the photo)"
            )
        return None
    try:
        return parse_metal_spec(raw_typed)
    except TypedInputError as exc:
        defects.append(f"typed metal invalid: {exc}")
        return None


def validate_item(raw: Any, root: Path) -> dict[str, Any]:
    """Validate one holdout item; returns a normalized record with defects."""
    defects: list[str] = []
    if not isinstance(raw, dict):
        return {"item_id": None, "status": "invalid", "defects": ["item must be an object"]}

    item_id = str(raw.get("item_id", "")).strip()
    if not item_id:
        defects.append("item_id is required")

    archetype = str(raw.get("archetype", "")).strip().lower()
    if archetype not in ARCHETYPE_CHOICES:
        defects.append(f"archetype must be one of {', '.join(ARCHETYPE_CHOICES)}")
    supported = archetype in SUPPORTED_ARCHETYPES

    image_rel = str(raw.get("image_path", "")).strip()
    image_info = None
    if not image_rel:
        defects.append("image_path is required")
    else:
        image_info = _validate_image((root / image_rel).resolve(), defects)

    typed: dict[str, Any] = {}
    raw_typed = raw.get("typed")
    if not isinstance(raw_typed, dict):
        defects.append(
            "typed inputs (stone spec or certificate + ring size + metal) are required"
        )
    else:
        resolved_typed, certificate = _resolve_typed(raw_typed, defects)
        try:
            stone = _parse_stone_inputs(resolved_typed)
        except ValueError as exc:
            defects.append(f"typed inputs invalid: {exc}")
        else:
            typed = {
                "stone_shape": stone.shape,
                "stone_length_mm": stone.length_mm,
                "stone_width_mm": stone.width_mm,
                "stone_depth_mm": stone.depth_mm,
                "ring_size_us": stone.ring_size_us,
                "stone_carat_eq": stone.carat_eq,
                "stone_spec_source": stone_spec_source(certificate),
            }
            if certificate is not None:
                typed["certificate"] = certificate.to_json()
            metal = _resolve_metal(resolved_typed, defects, required=supported)
            if metal is not None:
                typed["metal_color_family"] = metal.color_family
                typed["metal_karat"] = metal.purity
                typed["metal_alloy"] = metal.alloy

    labels: dict[str, Any] = {}
    if supported:
        labels = _validate_labels(raw.get("labels"), defects)
    elif raw.get("labels"):
        defects.append(
            "distractor items must not carry mounting labels "
            "(they measure OOD rejection, not attribute accuracy)"
        )

    labelled_fields = {name for name in MOUNTING_FIELDS if name in labels}
    labelled_fields.update(
        name for name in OPTIONAL_MOUNTING_FIELDS if name in labels
    )
    sources = _validate_sources(
        raw.get("sources"),
        labelled_fields,
        require_fields=supported,
        defects=defects,
    )

    return {
        "item_id": item_id or None,
        "status": "valid" if not defects else "invalid",
        "archetype": archetype,
        "supported": supported,
        "image_path": image_rel,
        "image": image_info,
        "typed": typed,
        "labels": labels,
        "prong_count_visible": bool(raw.get("prong_count_visible", True)),
        "sources": sources,
        "defects": defects,
    }


def curate(manifest_path: Path) -> dict[str, Any]:
    """Validate a whole holdout manifest and return the curation report."""
    manifest_path = Path(manifest_path)
    root = manifest_path.parent
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ValueError("manifest must be an object with an 'items' list")

    records = [validate_item(item, root) for item in raw["items"]]

    seen_ids: Counter[str] = Counter(
        record["item_id"] for record in records if record["item_id"]
    )
    seen_images: Counter[str] = Counter(
        record["image_path"] for record in records if record["image_path"]
    )
    for record in records:
        if record["item_id"] and seen_ids[record["item_id"]] > 1:
            record["defects"].append("duplicate item_id")
        if record["image_path"] and seen_images[record["image_path"]] > 1:
            record["defects"].append("duplicate image_path")
        record["status"] = "valid" if not record["defects"] else "invalid"

    valid = [record for record in records if record["status"] == "valid"]
    supported = [record for record in valid if record["supported"]]
    distractors = [record for record in valid if not record["supported"]]

    warnings: list[str] = []
    if len(supported) < MIN_SUPPORTED_ITEMS:
        warnings.append(
            f"holdout has {len(supported)} valid solitaire items; the plan calls for "
            f"{MIN_SUPPORTED_ITEMS}-{RECOMMENDED_MAX_ITEMS}. Gate verdicts computed on "
            "an under-strength holdout are indicative only."
        )
    if not distractors:
        warnings.append(
            "no distractor items: OOD rejection rate (gate G-D) cannot be measured."
        )

    return {
        "schema_version": 1,
        "manifest": str(manifest_path),
        "contract": {
            "labelled_mounting_axes": list(MOUNTING_FIELDS + OPTIONAL_MOUNTING_FIELDS),
            "typed_inputs": [
                "stone shape + dimensions, or a GIA/IGI certificate",
                "ring_size_us",
                "metal_color_family + metal_karat",
            ],
            "note": (
                "Metal is a typed input: it is never a label, never two-source "
                "checked, and never scored by the image lane."
            ),
        },
        "status": "pass" if len(valid) == len(records) and records else "fail",
        "counts": {
            "items": len(records),
            "valid": len(valid),
            "invalid": len(records) - len(valid),
            "supported_solitaire": len(supported),
            "distractors": len(distractors),
            "by_archetype": dict(Counter(record["archetype"] for record in records)),
        },
        "gate_ready": len(supported) >= MIN_SUPPORTED_ITEMS and bool(distractors),
        "warnings": warnings,
        "items": records,
    }


def write_normalized(report: dict[str, Any], path: Path) -> None:
    """Emit the validated subset as a normalized manifest for the eval harness."""
    payload = {
        "schema_version": 1,
        "source_manifest": report["manifest"],
        "gate_ready": report["gate_ready"],
        "items": [
            {
                key: record[key]
                for key in (
                    "item_id",
                    "archetype",
                    "supported",
                    "image_path",
                    "typed",
                    "labels",
                    "prong_count_visible",
                )
            }
            for record in report["items"]
            if record["status"] == "valid"
        ],
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Path to holdout_manifest.json")
    parser.add_argument("--report-json", type=Path, default=None)
    parser.add_argument(
        "--normalized-out",
        type=Path,
        default=None,
        help="Write the validated subset as a normalized manifest for eval.",
    )
    args = parser.parse_args(argv)

    report = curate(args.manifest)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.normalized_out:
        write_normalized(report, args.normalized_out)

    print(json.dumps({k: v for k, v in report.items() if k != "items"}, indent=2, sort_keys=True))
    for record in report["items"]:
        for defect in record["defects"]:
            print(f"  [{record['item_id'] or '?'}] {defect}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
