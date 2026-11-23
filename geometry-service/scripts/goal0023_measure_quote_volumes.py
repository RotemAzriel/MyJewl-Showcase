#!/usr/bin/env python3
"""Measure the quote volume of every promoted preview asset. Free, local, $0.

    python3 -m scripts.goal0023_measure_quote_volumes            # dry run
    python3 -m scripts.goal0023_measure_quote_volumes --write    # write the table

Goal 0023. ``quote_basis`` needs a real ring volume, and the only volume that
belongs to a *promoted* asset is one measured from the exact export bytes that
asset was promoted from. This script produces that table.

Discovery is data-driven, like ``goal0015_promote_v5.py``: every approved exact
asset in the production registry is looked up by its recorded
``provenance.source_receipt_sha256``, so promoting another configuration needs no
edit here -- only its export on disk under one of ``BUNDLE_ROOTS``.

**The basis is the measured mesh union, never the naive parts sum.** See
``docs/quote_basis_2026-08-17.md`` for why, and why the mesh union rather than
Rhino's NURBS union. Every number here is a measurement, never a manufacturing
verdict: gates stay pending / not_run / needs_calibration.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - direct execution
    sys.path.insert(0, str(ROOT))

from fast_mvp import quote_volumes
from fast_mvp.preview_resolution import load_registry
from jewelry_gaas_foundation.geometry_metrics import (
    ASSEMBLY_EXCLUDED_STEMS,
    METRICS_VERSION,
    measure_bundle,
)

#: Where receipt-backed exports live. Adding a root is the only reason to edit
#: this file; the V6 root is already listed so promoting V6 needs no change.
#: ``scripts.goal0025_measure_deliverables`` imports this and the three discovery
#: helpers below rather than repeating them -- two tables that disagreed about
#: where exports live, or about which assets are promoted, would be worse than one.
BUNDLE_ROOTS = (
    Path("output/goal_0011_realism_master_20260730/azure_v4/received"),
    Path("output/goal_0014_parametric_master_20260804/azure_v5/received"),
    Path("output/goal_0018_castable_master_20260812/azure_v6/received"),
)

#: Rhino's own NURBS union, when the definition emits one (V6 does, V4/V5 do not
#: -- that absence *is* finding F2). Recorded as a cross-check, never the basis.
RHINO_UNION_RECEIPT_KEY = "castable_union_volume_mm3"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def index_receipts() -> dict[str, Path]:
    """Map every receipt sha256 on disk to the bundle directory holding it."""
    index: dict[str, Path] = {}
    for root in BUNDLE_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        candidates = [base, *(p for p in sorted(base.iterdir()) if p.is_dir())]
        for bundle in candidates:
            for receipt in sorted(bundle.glob("*_receipt.json")):
                index[sha256_file(receipt)] = bundle
    return index


def promoted_exact_assets() -> list[dict[str, Any]]:
    registry = load_registry()
    return [
        asset
        for asset in registry["assets"]
        if asset["enabled"]
        and asset["approval"]["status"] == "approved"
        and asset["approval"]["visual_review"] == "approved"
        and asset["classification"] == "exact"
    ]


def _verify_part_bytes(bundle: Path, receipt: dict[str, Any]) -> tuple[int, list[str]]:
    """Confirm the STLs we are about to measure are the bytes Rhino emitted.

    Measuring a file the receipt does not vouch for would quote geometry nobody
    approved, so a mismatch is fatal rather than a warning.
    """
    recorded = {
        Path(str(part["file"])).stem: str(part["sha256"])
        for part in receipt.get("parts", [])
        if isinstance(part, dict) and part.get("file") and part.get("sha256")
    }
    problems: list[str] = []
    verified = 0
    for stl in sorted(bundle.glob("*.stl")):
        if stl.stem in ASSEMBLY_EXCLUDED_STEMS:
            continue
        expected = recorded.get(stl.stem)
        if expected is None:
            problems.append(f"{stl.name}: not recorded in the receipt")
            continue
        if sha256_file(stl) != expected:
            problems.append(f"{stl.name}: bytes differ from the receipt hash")
            continue
        verified += 1
    return verified, problems


def _record(asset: dict[str, Any], bundle: Path) -> dict[str, Any]:
    receipt_path = next(iter(sorted(bundle.glob("*_receipt.json"))))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    verified, problems = _verify_part_bytes(bundle, receipt)
    if problems:
        raise SystemExit(
            f"{asset['asset_id']}: export bytes do not match the receipt:\n  "
            + "\n  ".join(problems)
        )

    report = measure_bundle(
        bundle, thickness_samples=0, compute_union=True, compute_connectivity=False
    )
    assembly = report.assembly
    if assembly is None or assembly.union_mm3 is None:
        raise SystemExit(
            f"{asset['asset_id']}: no mesh union could be computed from {bundle}. "
            "A quote needs a union volume; refusing to fall back to the parts sum."
        )

    rhino_union = receipt.get(RHINO_UNION_RECEIPT_KEY)
    rhino_union = float(rhino_union) if isinstance(rhino_union, (int, float)) else None
    cross_check = (
        None
        if rhino_union is None
        else round((rhino_union - assembly.union_mm3) / assembly.union_mm3, 6)
    )
    provenance = asset["provenance"]
    return {
        "asset_id": asset["asset_id"],
        "asset_version": asset["asset_version"],
        "geometry_signature": asset["match"]["geometry_signature"],
        "source_receipt_sha256": provenance["source_receipt_sha256"],
        "definition_path": provenance["definition_path"],
        "definition_sha256": provenance["definition_sha256"],
        "definition_kind": provenance["definition_kind"],
        "template_id": provenance["template_id"],
        "bundle": str(bundle.relative_to(ROOT)),
        "metal_part_count": assembly.part_count,
        "part_bytes_verified": verified,
        "union_mm3": round(assembly.union_mm3, 4),
        "union_engine": assembly.union_engine,
        "union_watertight": assembly.union_watertight,
        "union_body_count": assembly.union_body_count,
        "parts_sum_mm3": round(assembly.parts_sum_mm3, 4),
        "overcount_mm3": round(assembly.overcount_mm3, 4),
        "overcount_fraction": round(assembly.overcount_fraction, 6),
        "rhino_nurbs_union_mm3": (
            None if rhino_union is None else round(rhino_union, 4)
        ),
        "rhino_vs_mesh_fraction": cross_check,
    }


def build_table(*, measured_on: str | None = None) -> tuple[dict[str, Any], list[str]]:
    """Measure every promoted exact asset. Returns the table and any skips."""
    index = index_receipts()
    records: list[dict[str, Any]] = []
    skipped: list[str] = []
    for asset in promoted_exact_assets():
        receipt_sha = asset["provenance"]["source_receipt_sha256"]
        bundle = index.get(receipt_sha)
        if bundle is None:
            skipped.append(
                f"{asset['asset_id']}: no export on disk with receipt "
                f"sha256 {receipt_sha[:16]}… -- it will stay pending_geometry"
            )
            continue
        records.append(_record(asset, bundle))

    table = {
        "schema": quote_volumes.TABLE_SCHEMA,
        "quote_volume_basis": quote_volumes.QUOTE_VOLUME_BASIS,
        "basis_rationale": (
            "The mesh boolean union of the exact metal STL set the promoted asset "
            "was built from. The naive parts sum over-counts by ~6.75% (metal is "
            "double-counted at every overlap), which is outside the agreed +/-3% "
            "volume tolerance and in the customer's disfavour. Rhino's NURBS union "
            "does not exist for any promoted asset -- V4/V5 contain no boolean at "
            "all, which is finding F2 -- and where both exist they agree to 0.65%. "
            "Tessellation inscribes the NURBS surface, so the mesh union is a lower "
            "bound: any residual error is in the customer's favour. "
            "See docs/quote_basis_2026-08-17.md."
        ),
        "metrics_version": METRICS_VERSION,
        "measured_on": measured_on or _dt.date.today().isoformat(),
        "generated_by": "scripts/goal0023_measure_quote_volumes.py",
        "authority": "measurement_only_not_manufacturing_certification",
        "manufacturing_gates": "pending_not_run_needs_calibration",
        "records": sorted(records, key=lambda r: r["geometry_signature"]),
    }
    return table, skipped


def _serialize(table: dict[str, Any]) -> str:
    return json.dumps(table, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the table")
    parser.add_argument("--table", type=Path, default=quote_volumes.DEFAULT_TABLE_PATH)
    args = parser.parse_args(argv)

    existing_on = None
    if args.table.is_file():
        # Keep the recorded measurement date stable so an unchanged re-run is a
        # byte-identical no-op rather than a diff every day.
        try:
            existing_on = json.loads(args.table.read_text(encoding="utf-8")).get(
                "measured_on"
            )
        except (ValueError, OSError):
            existing_on = None

    table, skipped = build_table(measured_on=existing_on)
    quote_volumes.validate_table(table)

    print(f"basis: {table['quote_volume_basis']}   metrics: {table['metrics_version']}")
    print(f"\n{'asset':32} {'union_mm3':>11} {'parts_sum':>11} {'over%':>7} {'rhino Δ%':>9}")
    print("-" * 76)
    for record in table["records"]:
        rhino = record["rhino_vs_mesh_fraction"]
        print(
            f"{record['asset_id']:32} {record['union_mm3']:11.4f} "
            f"{record['parts_sum_mm3']:11.4f} "
            f"{record['overcount_fraction'] * 100:7.3f} "
            f"{'-' if rhino is None else f'{rhino * 100:.3f}':>9}"
        )
    for note in skipped:
        print(f"\nSKIPPED  {note}")

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
