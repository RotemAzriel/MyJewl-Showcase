#!/usr/bin/env python3
"""Sweep the master's declared envelope and write the table the shop needs.

    python3 -m scripts.sweep_envelope [--steps N] [--report-json PATH]
        [--markdown PATH] [--validate BUNDLE ...] [--measured-json PATH]
        [--samples N]

Free, local, offline, **$0**. ``master_parameters.derive_parameters`` is pure
Python with explicit bounds, so the whole envelope can be sampled densely with no
Rhino and no Azure.

Reports **exact values, never verdicts** -- the same discipline as
``scripts.measure_geometry``. Every floor it prints is a *candidate* a casting
shop is being asked to confirm or correct, explicitly uncalibrated. Manufacturing
gates stay pending / not_run / needs_calibration.

``--validate`` checks the analytic table against real measured geometry. If the
two disagree the analytic table is wrong and must not be trusted; the script says
so and exits non-zero rather than reconciling by adjustment.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

try:
    from jewelry_gaas_foundation import envelope_sweep as es
    from jewelry_gaas_foundation.geometry_metrics import measure_bundle
except ModuleNotFoundError:  # pragma: no cover - direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from jewelry_gaas_foundation import envelope_sweep as es
    from jewelry_gaas_foundation.geometry_metrics import measure_bundle

RECEIPT_GLOB = "*_receipt.json"


def _bundle_request(bundle: Path) -> dict[str, Any]:
    """Return the typed request a solved bundle was built from, off its receipt."""
    receipts = sorted(bundle.glob(RECEIPT_GLOB))
    if not receipts:
        raise FileNotFoundError(f"No {RECEIPT_GLOB} in {bundle}")
    payload = json.loads(receipts[0].read_text(encoding="utf-8"))
    request = payload.get("request")
    if not isinstance(request, dict):
        raise ValueError(f"Receipt in {bundle} carries no typed request")
    return request


def _measured_parts(
    bundle: Path, cache: dict[str, list[dict[str, Any]]], samples: int
) -> list[dict[str, Any]]:
    """Return one bundle's measured parts, from the cache when it is there."""
    for key in (str(bundle), bundle.name, str(bundle.resolve())):
        if key in cache:
            return cache[key]
    report = measure_bundle(bundle, thickness_samples=samples, compute_union=False)
    return [part for part in report.to_dict()["parts"]]


def _load_cache(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    cache: dict[str, list[dict[str, Any]]] = {}
    for report in payload.get("reports", []):
        name = str(report.get("bundle", ""))
        parts = report.get("parts") or []
        cache[name] = parts
        cache[Path(name).name] = parts
    return cache


def _print_sweep(report: es.SweepReport) -> None:
    print(f"\n{'=' * 78}\nENVELOPE SWEEP -- {es.SWEEP_VERSION}\n{'=' * 78}")
    print(f"derivation: {report.to_dict()['derivation_version']}")
    print(f"authority:  {es.AUTHORITY}   gates: {es.MANUFACTURING_GATES}")
    print(
        f"\ngrid: {report.steps} steps/axis over 5 axes -> "
        f"{report.points_evaluated:,} points  "
        f"({report.accepted:,} accepted, {report.refused:,} refused)"
    )

    print("\n-- refusals (the envelope's real shape, measured not assumed) --")
    for item in report.refusals:
        print(f"  {item['count']:7,}  {item['reason']}")
    if not report.refusals:
        print("  none -- no probe point fell outside a declared bound")

    refused_in_bounds = (
        report.points_in_declared_bounds - report.accepted_in_declared_bounds
    )
    print(
        f"\n-- refused INSIDE the declared bounds: {refused_in_bounds:,} of "
        f"{report.points_in_declared_bounds:,} grid points --"
    )
    for item in report.boundary_refusals:
        print(f"  {item['count']:7,}  {item['reason']}")
        print(f"           example: {item['example']}")
    if not report.boundary_refusals:
        print("  none at this grid resolution -- see the probe below, which does "
              "not depend on it")

    print("\n-- can a customer specify a value exactly AT a bound? --")
    for item in report.boundary_attainability:
        example = item["example_refused_diameter_mm"]
        print(
            f"  {item['bound']:28} {item['value']:g}   refused for "
            f"{item['diameters_refused']:,} of {item['diameters_probed']:,} stone "
            f"diameters ({item['fraction_refused'] * 100:.1f}%)"
        )
        if example is not None:
            print(f"  {'':28} example diameter: {example!r} mm")
            print(f"  {'':28} {item['explanation']}")

    print(f"\n-- sections across {report.accepted:,} accepted configurations --")
    print(f"{'feature':26} {'min_mm':>9} {'max_mm':>9} {'scales with':>20} {'coeff':>10}")
    print("-" * 78)
    for summary in sorted(report.features, key=lambda f: f.min_section_mm):
        driver = summary.proportional_to or "(not proportional)"
        coefficient = (
            f"{summary.coefficient:10.6f}" if summary.coefficient is not None
            else f"{'-':>10}"
        )
        print(
            f"{summary.key:26} {summary.min_section_mm:9.4f} "
            f"{summary.max_section_mm:9.4f} {driver:>20} {coefficient}"
        )

    thin = report.thinnest
    exposed = report.thinnest_free_end
    print(
        f"\nthinnest section on any single part: {thin['section_mm']:.4f} mm  "
        f"[{thin['feature']}] at stone {thin['at']['stone_diameter_mm']:g} mm"
    )
    print(
        f"thinnest EXPOSED on the fused solid: {exposed['section_mm']:.4f} mm  "
        f"[{exposed['feature']}] at stone {exposed['at']['stone_diameter_mm']:g} mm"
    )

    print("\n-- floor sensitivity (NO floor here is calibrated) --")
    print(
        f"{'candidate':>10}  {'every section':>14}  {'exposed only':>13}  "
        f"{'prong base clears at stone Ø':>30}"
    )
    print("-" * 78)
    for item in report.floor_sensitivity:
        prong = next(r for r in item.per_feature if r["feature"] == "prong_root")
        required = prong["required_driver_value"]
        if required is None:
            needed = "-"
        else:
            marker = "" if prong["required_driver_value_in_envelope"] else "  OUT OF ENVELOPE"
            needed = f"{required:.2f} mm{marker}"
        share = item.fraction_of_configurations_all_features_at_or_above
        free = item.fraction_of_configurations_free_end_features_at_or_above
        print(
            f"{item.floor_mm:9.2f}  {share * 100:13.1f}%  {free * 100:12.1f}%  "
            f"{needed:>30}"
        )

    if es.SOURCE_DIVERGENCES:
        print("\n-- exported geometry vs what the source says it builds --")
        for item in es.SOURCE_DIVERGENCES:
            print(f"  {item.key}\n     {item.summary}")
            print(f"     intent:   {item.source_intent}")
            print(f"     as built: {item.as_built}")
            print(f"     evidence: {item.evidence}")

    crosscheck = [
        row for row in report.baseline_crosscheck if row["envelope_admits_below_floor"]
    ]
    if crosscheck:
        print("\n-- the envelope admits bands below MyJewl's own (uncalibrated) baseline --")
        for row in crosscheck:
            print(
                f"  {row['floor_key']:32} baseline {row['floor_mm']:.2f} mm  "
                f"envelope minimum {row['envelope_minimum_mm']:.2f} mm  "
                f"({row['fraction_of_envelope_below_floor'] * 100:.1f}% of the grid below it)"
            )
            print(f"  {'':32} applies: {row['applies']}")


def _print_validation(validation: dict[str, Any]) -> None:
    print(f"\n{'=' * 78}\nVALIDATION -- analytic sections vs measured geometry\n{'=' * 78}")
    band = validation["agreement_band"]
    print(
        f"agreement band: {band['low'] * 100:+.1f}% .. {band['high'] * 100:+.1f}% "
        f"of the analytic section"
    )
    for entry in validation["bundles"]:
        worst = entry["worst_residual_fraction"]
        worst_text = "-" if worst is None else f"{worst * 100:+.2f}%"
        print(
            f"\n  {entry['bundle']}\n"
            f"    parts compared {entry['parts_compared']:3d}   "
            f"outside band {entry['parts_outside_band']:3d}   worst {worst_text}"
        )
        for comparison in entry["comparisons"]:
            if comparison["measured_mm"] is None:
                print(f"    {comparison['part']:26} {'no measurement':>26}")
                continue
            flag = "" if comparison["within_band"] else "   <-- OUTSIDE BAND"
            print(
                f"    {comparison['part']:26} "
                f"analytic {comparison['analytic_mm']:7.4f}  "
                f"measured {comparison['measured_mm']:7.4f}  "
                f"{comparison['residual_fraction'] * 100:+7.2f}%{flag}"
            )
    if validation["agrees"]:
        print("\nAGREES -- the analytic table describes the geometry Rhino built.")
    else:
        print(
            "\nDISAGREES -- at least one part falls outside the stated band.\n"
            "The analytic sweep is wrong and must not be trusted. Do not reconcile\n"
            "it by widening the band; find out which model is incorrect."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=7,
                        help="grid points per axis inside the bounds (default 7)")
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--markdown", type=Path,
                        help="write the shop-facing document here")
    parser.add_argument("--validate", nargs="*", type=Path, default=None,
                        help="solved bundle directories to check the sweep against")
    parser.add_argument("--measured-json", type=Path,
                        help="reuse a myjewl.geometry_measurement.v1 report instead "
                             "of re-measuring the bundles")
    parser.add_argument("--samples", type=int, default=8000,
                        help="surface samples per part when measuring (default 8000)")
    parser.add_argument("--generated", default=date.today().isoformat(),
                        help="date stamped into the markdown document")
    args = parser.parse_args(argv)

    report = es.sweep(steps=args.steps)
    _print_sweep(report)

    exit_code = 0
    if args.validate:
        cache = _load_cache(args.measured_json)
        entries = []
        for bundle in args.validate:
            entry = es.validate_against_measurement(
                _bundle_request(bundle),
                _measured_parts(bundle, cache, args.samples),
                bundle=bundle.name,
            )
            entries.append(entry)
        report.validation = {
            "agrees": all(entry["agrees"] for entry in entries),
            "bundles": entries,
            "agreement_band": entries[0]["agreement_band"] if entries else None,
        }
        _print_validation(report.validation)
        if not report.validation["agrees"]:
            exit_code = 1

    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.report_json}")

    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            es.to_markdown(report, generated=args.generated), encoding="utf-8"
        )
        print(f"wrote {args.markdown}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
