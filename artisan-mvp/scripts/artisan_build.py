"""Build a solitaire ring in Rhino via the RhinoArtisan MCP from a spec.

Input is either a canonical spec v0.1 JSON file, or a flat dataset config
(``configs/<id>/`` or its ``parameters.json``), which is adapted with
``flat_to_canonical``.

    # print the compiled plan, no Rhino needed
    python3 scripts/artisan_build.py examples/solitaire_valid.json --dry-run

    # build live (Rhino + RhinoArtisan running, MCP on 127.0.0.1:9280)
    python3 scripts/artisan_build.py examples/solitaire_valid.json --out out/artisan
    python3 scripts/artisan_build.py configs/config_solitaire_oval_1.00ct_prong4_18ky_2.0mm_us65 \
        --shank cathedral --head basket

Each build opens a new Rhino document (your open work is untouched), leaves the
parametric model open for editing, and writes <out>/<job>/{.3dm,.stl,.glb,.png,
manifest.json}. Exit code 0 = built, 1 = build failed, 2 = spec rejected,
3 = built but the post-build QA detected a defect (see manifest qa_failures).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jewelry_gaas_foundation.artisan_executor import (  # noqa: E402
    DEFAULT_MCP_URL,
    ArtisanExecutor,
    ArtisanMcpClient,
)
from jewelry_gaas_foundation.artisan_plan import (  # noqa: E402
    MountingOptions,
    SpecRejectedError,
    UnsupportedSpecError,
    compile_solitaire_plan,
)
from jewelry_gaas_foundation.config_adapter import flat_to_canonical  # noqa: E402


def load_spec(path: Path) -> dict:
    if path.is_dir():
        path = path / "parameters.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if "spec_version" in data else flat_to_canonical(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("spec", type=Path, help="canonical spec JSON or flat config dir/file")
    parser.add_argument(
        "--shank", choices=("plain", "cathedral"), help="override ring.shank_style"
    )
    parser.add_argument(
        "--head", choices=("prong", "basket", "bezel"), help="override setting.center_setting"
    )
    parser.add_argument("--prongs", type=int, help="override setting.prong_count")
    parser.add_argument("--culet-clearance", type=float, default=0.5, metavar="MM")
    parser.add_argument("--out", type=Path, default=Path("out/artisan"))
    parser.add_argument("--url", default=DEFAULT_MCP_URL)
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    parser.add_argument(
        "--no-png", action="store_true", help="skip the viewport capture"
    )
    args = parser.parse_args(argv)

    spec = load_spec(args.spec)
    if args.head or args.prongs is not None:
        spec = json.loads(json.dumps(spec))
        if args.head:
            spec["setting"]["center_setting"] = args.head
            if args.head == "bezel":
                spec["setting"].pop("prong_count", None)  # PRONG-009: no prongs on a bezel
        if args.prongs is not None:
            spec["setting"]["prong_count"] = args.prongs

    options = MountingOptions(shank_style=args.shank, culet_clearance_mm=args.culet_clearance)
    try:
        plan = compile_solitaire_plan(spec, options)
    except (SpecRejectedError, UnsupportedSpecError) as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 2

    head = spec["setting"]["center_setting"]
    plan = replace(plan, job_id=f"{plan.job_id}__{head}_{plan.derived['shank_style']}")

    if args.dry_run:
        print(json.dumps(plan.to_dict(), indent=2))
        return 0

    exports = ("3dm", "stl", "glb") if args.no_png else ("3dm", "stl", "glb", "png")
    result = ArtisanExecutor(ArtisanMcpClient(args.url)).run(
        plan, args.out / plan.job_id, exports=exports
    )
    m = result.metrics
    print(f"{plan.job_id}: {result.status.upper()} in {m.get('build_seconds')} s")
    if result.error:
        print(f"  error: {result.error}", file=sys.stderr)
    for key in (
        "metal",
        "metal_weight_g",
        "culet_clearance_mm",
        "culet_inside_metal",
        "measured_finger_diameter_mm",
        "metal_bodies",
        "metal_all_solid",
    ):
        if key in m:
            print(f"  {key}: {m[key]}")
    if "price" in m:
        p = m["price"]
        print(f"  mounting price: {p['mounting_total']} {p['currency']} (stone not quoted)")
    if "qa_mesh_closedness" in m:
        print(f"  qa mesh closedness: {m['qa_mesh_closedness']['status']}")
    for failure in m.get("qa_failures") or []:
        print(f"  QA DEFECT: {failure}", file=sys.stderr)
    print(f"  manifest: {result.manifest_path}")
    if result.status != "built":
        return 1
    return 3 if m.get("qa_failures") else 0


if __name__ == "__main__":
    raise SystemExit(main())
