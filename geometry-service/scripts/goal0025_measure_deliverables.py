#!/usr/bin/env python3
"""Record what each promoted asset's export bundle IS, for delivery. Free, $0.

    python3 -m scripts.goal0025_measure_deliverables            # dry run
    python3 -m scripts.goal0025_measure_deliverables --write    # write the table

Goal 0025. Handing geometry to a casting shop needs facts the quote-volume table
does not carry: which files a receipt actually vouches for, how many of them are
wound inside-out (F1), and whether anything fuses them (F2). This script measures
those facts once, from the exact receipt-verified bytes, so the delivery gate is a
lookup and a hash check rather than a boolean union per HTTP request.

Discovery is shared with ``goal0023_measure_quote_volumes`` -- same bundle roots,
same promoted-asset query -- so the two tables cannot disagree about what exists.

**This table grants nothing.** Whether inside-out unfused parts are acceptable is a
property of the receiving shop, declared in a calibrated shop profile's ``mesh``
block. Every number here is a measurement; gates stay pending / not_run /
needs_calibration. See ``docs/geometry_delivery_2026-08-17.md``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - direct execution
    sys.path.insert(0, str(ROOT))

from fast_mvp import geometry_delivery
from scripts.goal0023_measure_quote_volumes import (
    index_receipts,
    promoted_exact_assets,
    sha256_file,
)
from jewelry_gaas_foundation.geometry_metrics import (
    DERIVED_SOLID_STEMS,
    METRICS_VERSION,
    measure_part,
)

#: Roles the receipt uses, all three of which are deliverable. ``metal`` is what
#: gets cast; ``stone`` travels as a labelled reference so a modelist can check the
#: seat, and is never metal; ``castable`` is Rhino's own boolean union of the metal
#: parts, which the V6 master emits and V4/V5 do not -- that absence *is* finding
#: F2. The union is a **derivative** of parts already in the bundle, so it is
#: excluded from ``metal_part_count`` exactly as ``geometry_metrics`` excludes it
#: from the assembly sum; including it would double-count the whole ring.
DELIVERABLE_ROLES = ("metal", "stone", "castable")


def _receipt_files(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    """The receipt's own part list, which is the entire deliverable set.

    Anything the receipt does not hash -- ``model.stl``, the ``.3dm``, the ``.obj``
    -- is absent from this list and therefore undeliverable. That is the rule, not
    an omission: we hand over only bytes a receipt vouches for.
    """
    parts = receipt.get("parts")
    if not isinstance(parts, list) or not parts:
        raise SystemExit("receipt carries no parts list")
    out = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        role = str(part.get("role") or "")
        if role not in DELIVERABLE_ROLES:
            continue
        out.append(part)
    return out


def _record(asset: dict[str, Any], bundle: Path) -> dict[str, Any]:
    receipt_path = next(iter(sorted(bundle.glob("*_receipt.json"))))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    files: list[dict[str, Any]] = []
    inverted: list[str] = []
    not_watertight = 0
    triangles = vertices = 0
    problems: list[str] = []

    for part in _receipt_files(receipt):
        name = str(part["file"])
        path = bundle / name
        if not path.is_file():
            problems.append(f"{name}: recorded in the receipt but missing from the bundle")
            continue
        if sha256_file(path) != str(part["sha256"]):
            problems.append(f"{name}: bytes differ from the receipt hash")
            continue
        metrics = measure_part(path, thickness_samples=0)
        role = str(part["role"])
        files.append(
            {
                "file": name,
                "role": role,
                "sha256": str(part["sha256"]),
                "byte_size": int(part["byte_size"]),
                "triangles": int(metrics.triangles),
                "vertices": int(metrics.vertices),
                "watertight": bool(metrics.watertight),
                "oriented_outward": bool(metrics.oriented_outward),
            }
        )
        triangles += int(metrics.triangles)
        vertices += int(metrics.vertices)
        if not metrics.watertight:
            not_watertight += 1
        # An inverted part is a metal part whose normals face inward, whether or
        # not it is also open. The count is derived from exactly the per-file flag
        # recorded above, because ``geometry_delivery.validate_table`` re-derives it
        # the same way -- two rules would let the table disagree with itself.
        if role == "metal" and not metrics.oriented_outward:
            inverted.append(metrics.name)

    if problems:
        raise SystemExit(
            f"{asset['asset_id']}: export bytes do not match the receipt:\n  "
            + "\n  ".join(problems)
        )

    metal = [item for item in files if item["role"] == "metal"]
    stone = [item for item in files if item["role"] == "stone"]
    if not metal:
        raise SystemExit(f"{asset['asset_id']}: receipt records no metal parts")

    castable = next(
        (item for item in files if Path(item["file"]).stem in DERIVED_SOLID_STEMS),
        None,
    )
    # An invalid union is not a union. ``geometry_metrics`` applies the same test
    # before it will call a bundle unioned, and a shop that requires one fused solid
    # must not be handed an open or inside-out one and told the requirement is met.
    ships_valid_union = bool(
        castable is not None
        and castable["watertight"]
        and castable["oriented_outward"]
    )
    findings = _findings(
        sorted(inverted), ships_valid_union, len(metal), castable=castable
    )
    provenance = asset["provenance"]
    return {
        "asset_id": asset["asset_id"],
        "asset_version": asset["asset_version"],
        "geometry_signature": asset["match"]["geometry_signature"],
        "source_receipt_sha256": provenance["source_receipt_sha256"],
        "receipt_file": receipt_path.name,
        "definition_path": provenance["definition_path"],
        "definition_sha256": provenance["definition_sha256"],
        "definition_kind": provenance["definition_kind"],
        "template_id": provenance["template_id"],
        "bundle": str(bundle.relative_to(ROOT)),
        "units": "mm",
        "coordinate_system": str(
            _geometry_manifest(bundle).get("coordinate_system") or "Rhino_Z_up"
        ),
        "metal_part_count": len(metal),
        "reference_part_count": len(stone),
        "inverted_part_count": len(inverted),
        "inverted_parts": sorted(inverted),
        "not_watertight_part_count": not_watertight,
        "ships_castable_solid": ships_valid_union,
        "castable_solid_file": None if not ships_valid_union else castable["file"],
        "triangle_count": triangles,
        "vertex_count": vertices,
        "files": sorted(files, key=lambda item: item["file"]),
        "findings": findings,
    }


def _geometry_manifest(bundle: Path) -> dict[str, Any]:
    path = bundle / "geometry_manifest.json"
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _findings(
    inverted: list[str],
    ships_valid_union: bool,
    metal_count: int,
    *,
    castable: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The delivery-relevant subset of the Goal 0018 findings, as observations.

    They are recorded whether or not any shop cares, because they travel with the
    bytes: a shop that accepts them is entitled to know what it accepted.
    """
    findings: list[dict[str, Any]] = []
    if inverted:
        findings.append(
            {
                "code": "INVERTED_NORMALS",
                "severity": "critical",
                "part": None,
                "summary": (
                    f"F1: {len(inverted)} of {metal_count} metal parts are wound "
                    "inward. A slicer or boolean engine reads them as voids rather "
                    "than metal, and a watertightness check passes them."
                ),
                "measured": {
                    "inverted_part_count": len(inverted),
                    "metal_part_count": metal_count,
                    "parts": inverted,
                },
            }
        )
    if castable is not None and not ships_valid_union:
        findings.append(
            {
                "code": "CASTABLE_SOLID_INVALID",
                "severity": "critical",
                "part": castable["file"],
                "summary": (
                    "The bundle ships a fused solid that is not a valid closed, "
                    "outward-oriented body, so it does not satisfy a shop that "
                    "requires one solid. An invalid union is not a union."
                ),
                "measured": {
                    "watertight": castable["watertight"],
                    "oriented_outward": castable["oriented_outward"],
                },
            }
        )
    if not ships_valid_union and castable is None:
        findings.append(
            {
                "code": "PARTS_NOT_UNIONED",
                "severity": "critical",
                "part": None,
                "summary": (
                    "F2: the bundle ships no boolean union of the metal parts, so it "
                    "is a set of interpenetrating solids. Whoever casts it must fuse "
                    "them, and the naive per-part volume sum over-states metal."
                ),
                "measured": {"metal_part_count": metal_count},
            }
        )
    if ships_valid_union:
        findings.append(
            {
                "code": "PARTS_OVERLAP_BY_DESIGN",
                "severity": "note",
                "part": None,
                "summary": (
                    "The named parts interpenetrate, which is expected: the bundle "
                    "also ships a valid fused solid, and the parts exist for mask "
                    "IDs and per-part measurement. Cast from the fused solid."
                ),
                "measured": {
                    "metal_part_count": metal_count,
                    "castable_solid_file": castable["file"],
                },
            }
        )
    return findings


def build_table(*, measured_on: str | None = None) -> tuple[dict[str, Any], list[str]]:
    """Measure every promoted exact asset's bundle. Returns the table and skips."""
    index = index_receipts()
    records: list[dict[str, Any]] = []
    skipped: list[str] = []
    for asset in promoted_exact_assets():
        receipt_sha = asset["provenance"]["source_receipt_sha256"]
        bundle = index.get(receipt_sha)
        if bundle is None:
            skipped.append(
                f"{asset['asset_id']}: no export on disk with receipt "
                f"sha256 {receipt_sha[:16]}… -- it will refuse to deliver"
            )
            continue
        records.append(_record(asset, bundle))

    table = {
        "schema": geometry_delivery.TABLE_SCHEMA,
        "delivery_source": geometry_delivery.DELIVERY_SOURCE,
        "source_rationale": (
            "The exact export bytes a Rhino receipt vouches for, delivered verbatim. "
            "Rhino/Grasshopper is the sole geometry authority and the model never "
            "hand-authors geometry, so this repository's own manifold3d union -- "
            "watertight, single-bodied, and the basis of the quote -- is measured but "
            "never delivered: it is not Rhino's output, and it is computed from "
            "meshes whose winding was repaired in memory, which would ship a silent "
            "fix for a defect that is still open at the source. "
            "See docs/geometry_delivery_2026-08-17.md."
        ),
        "metrics_version": METRICS_VERSION,
        "measured_on": measured_on or _dt.date.today().isoformat(),
        "generated_by": "scripts/goal0025_measure_deliverables.py",
        "authority": "measurement_only_not_manufacturing_certification",
        "manufacturing_gates": "pending_not_run_needs_calibration",
        "records": sorted(records, key=lambda record: record["geometry_signature"]),
    }
    return table, skipped


def _serialize(table: dict[str, Any]) -> str:
    return json.dumps(table, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the table")
    parser.add_argument(
        "--table", type=Path, default=geometry_delivery.DEFAULT_TABLE_PATH
    )
    args = parser.parse_args(argv)

    existing_on = None
    if args.table.is_file():
        # Keep the recorded date stable so an unchanged re-run is a byte-identical
        # no-op rather than a diff every day.
        try:
            existing_on = json.loads(args.table.read_text(encoding="utf-8")).get(
                "measured_on"
            )
        except (ValueError, OSError):
            existing_on = None

    table, skipped = build_table(measured_on=existing_on)
    geometry_delivery.validate_table(table)

    print(f"source: {table['delivery_source']}   metrics: {table['metrics_version']}")
    print(
        f"\n{'asset':32} {'files':>6} {'metal':>6} {'inverted':>9} "
        f"{'union?':>7} {'triangles':>10}"
    )
    print("-" * 76)
    for record in table["records"]:
        print(
            f"{record['asset_id']:32} {len(record['files']):6} "
            f"{record['metal_part_count']:6} {record['inverted_part_count']:9} "
            f"{('yes' if record['ships_castable_solid'] else 'no'):>7} "
            f"{record['triangle_count']:10}"
        )
    for note in skipped:
        print(f"\nSKIPPED  {note}")

    print(
        "\nThese are measurements, not verdicts. Whether this geometry may be "
        "delivered\ndepends on a calibrated shop profile's mesh block -- see "
        "docs/geometry_delivery_2026-08-17.md."
    )

    payload = _serialize(table)
    current = args.table.read_text(encoding="utf-8") if args.table.is_file() else None
    if current == payload:
        print(f"\n{args.table} is already up to date (byte-identical).")
        return 0
    if not args.write:
        print(f"\nDRY RUN -- would write {args.table}. Re-run with --write.")
        return 0
    args.table.parent.mkdir(parents=True, exist_ok=True)
    args.table.write_text(payload, encoding="utf-8")
    print(f"\nwrote {args.table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
