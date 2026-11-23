#!/usr/bin/env python3
"""Measure a ring bundle and print comparable numbers. Free, local, no paid compute.

    python3 -m scripts.measure_geometry <bundle-dir> [<bundle-dir> ...]
        [--report-json PATH] [--samples N] [--no-union] [--quiet-parts]

Reports **exact measured values for every part**, not only threshold violations --
the operator's standing instruction. Findings are observations, never manufacturing
verdicts: geometry gates stay pending / not_run / needs_calibration until a
calibrated shop profile and human jeweler review exist.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from jewelry_gaas_foundation.geometry_metrics import measure_bundle
    from jewelry_gaas_foundation.manufacturing_assumptions import (
        unrepresented_floors,
    )
except ModuleNotFoundError:  # pragma: no cover - direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from jewelry_gaas_foundation.geometry_metrics import measure_bundle
    from jewelry_gaas_foundation.manufacturing_assumptions import (
        unrepresented_floors,
    )

#: Commercial density estimates (g/cm^3). Appearance/pricing only, never geometry.
DENSITIES = {
    "14k_yellow": 13.07,
    "18k_yellow": 15.58,
    "platinum_950": 21.45,
    "silver_925": 10.36,
}

SEVERITY_ORDER = {"critical": 0, "structural": 1, "note": 2}


def _print_report(report, show_parts: bool) -> None:
    print(f"\n{'=' * 78}\n{report.bundle}\n{'=' * 78}")
    print(f"basis: {report.dimension_basis}   authority: {report.authority}")

    if show_parts:
        print(f"\n{'part':30} {'tris':>7} {'wtr':>4} {'out':>4} "
              f"{'volume_mm3':>11} {'min_thk_mm':>11}")
        print("-" * 78)
        for part in report.parts:
            thickness = (
                f"{part.min_wall_thickness_mm:11.4f}"
                if part.min_wall_thickness_mm is not None else f"{'-':>11}"
            )
            print(
                f"{part.name:30} {part.triangles:7d} "
                f"{'Y' if part.watertight else 'N':>4} "
                f"{'Y' if part.oriented_outward else 'N':>4} "
                f"{part.signed_volume_mm3:11.4f} {thickness}"
            )

    assembly = report.assembly
    if assembly:
        print(f"\n-- metal assembly ({assembly.part_count} parts) --")
        print(f"  parts_sum      {assembly.parts_sum_mm3:10.3f} mm^3")
        if assembly.union_mm3 is not None:
            print(f"  union          {assembly.union_mm3:10.3f} mm^3   "
                  f"[{assembly.union_engine}] watertight={assembly.union_watertight} "
                  f"bodies={assembly.union_body_count}")
            print(f"  over-count     {assembly.overcount_mm3:10.3f} mm^3   "
                  f"({assembly.overcount_fraction * 100:.2f}% too heavy if summed)")
        else:
            print("  union             UNAVAILABLE (no boolean engine succeeded)")
        print(f"  inverted parts {assembly.inverted_part_count:10d}")
        print(f"  touching groups{assembly.connected_components if assembly.connected_components is not None else '-':>10}"
              f"   (at {assembly.connectivity_tolerance_mm} mm)")

        masses = report.masses_g(DENSITIES)
        if masses:
            print("\n-- commercial mass estimate (from measured geometry, not a verdict) --")
            for alloy, values in masses.items():
                union_g = values["from_union_g"]
                naive_g = values["from_parts_sum_g"]
                shown = f"{union_g:.3f} g" if union_g is not None else "unavailable"
                print(f"  {alloy:14} {shown:>12}   (naive sum would say {naive_g:.3f} g)")

    if report.findings:
        print(f"\n-- findings ({len(report.findings)}) --")
        grouped: dict[str, list] = {}
        for finding in report.findings:
            grouped.setdefault(finding.code, []).append(finding)
        for code, items in sorted(
            grouped.items(), key=lambda kv: SEVERITY_ORDER.get(kv[1][0].severity, 9)
        ):
            first = items[0]
            where = (
                f" [{len(items)} parts]" if len(items) > 1
                else f" [{first.part}]" if first.part else ""
            )
            print(f"  {first.severity.upper():11} {code}{where}")
            print(f"              {first.summary}")
    else:
        print("\n-- findings: none --")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundles", nargs="+", type=Path)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--samples", type=int, default=3000,
                        help="surface samples per part for thickness (0 disables)")
    parser.add_argument("--no-union", action="store_true",
                        help="skip the boolean union (faster)")
    parser.add_argument("--quiet-parts", action="store_true",
                        help="suppress the per-part table")
    args = parser.parse_args(argv)

    reports = []
    for bundle in args.bundles:
        report = measure_bundle(
            bundle,
            thickness_samples=args.samples,
            compute_union=not args.no_union,
        )
        reports.append(report)
        _print_report(report, show_parts=not args.quiet_parts)

    missing = unrepresented_floors()
    if missing:
        print(f"\n{'=' * 78}\nNEEDS A REAL NUMBER -- {len(missing)} manufacturing "
              f"floors have no value at all\n{'=' * 78}")
        for floor in missing:
            print(f"  - {floor.label}")

    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "myjewl.geometry_measurement.v1",
            "reports": [r.to_dict() for r in reports],
        }
        args.report_json.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {args.report_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
