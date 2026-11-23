#!/usr/bin/env python3
"""Promote the Goal 0014 V5 parametric master into the production preview registry.

Goal 0015. Every field below is derived, never hand-copied: the geometry
projection and signature come from running the real Studio configurator through
``fast_mvp.preview_resolution``, the delivery hashes come from the built browser
assets on disk, and the provenance hashes come from the definition, receipts, and
evidence files themselves. Re-running this script on unchanged inputs reproduces
the registry byte for byte.

The coverage decision this script encodes: every solved configuration backs one
**exact** discrete signature. No ``estimated`` / ``bounded_projection`` entry is
ever written, because a solved ring cannot honestly stand in for its neighbours —
an unsolved request stays ``unavailable`` rather than being served the nearest
solve. Widening coverage requires a wider Azure batch, not a wider claim.

Goal 0016 made this data-driven. The configurations are **discovered** from the
received exports, their count comes from the differential report, and their human
visual reviews come from a tracked manifest. Promoting an Nth solved
configuration therefore needs no edit to this file: drop in its export, record its
review, and re-run.

This promotes a *preview* asset. It certifies nothing about manufacturing, which
remains pending / not_run / needs_calibration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_mvp import manual_configurator, quote_volumes
from fast_mvp.preview_resolution import (
    DEFAULT_REGISTRY_PATH,
    geometry_parameter_paths,
    geometry_signature,
    load_registry,
)
from scripts.build_goal0014_browser_assets import (
    RECEIPT_NAME,
    config_slug,
    configuration_of,
    load_receipt,
)

V5_HEAD_ID = "head_v5_parametric_petal_4_prong"
V5_BAND_ID = "band_v5_parametric_split_solitaire"
V5_TEMPLATE_ID = "goal0014-parametric-round-solitaire-master-v5"
V5_ASSET_VERSION = "v5.0.0"
V5_DEFINITION_PATH = "manual/rhino_compute_bridge/goal0014_parametric_master_v5.ghx"

ASSET_DIR = ROOT / "fast_mvp" / "static" / "assets" / "goal0014_v5"
ASSET_URL_PREFIX = "/static/assets/goal0014_v5"
EVIDENCE_ROOT = ROOT / "output" / "goal_0014_parametric_master_20260804"
DIFFERENTIAL_REPORT = "output/goal_0014_parametric_master_20260804/config_differential.json"
RECEIVED_ROOT = EVIDENCE_ROOT / "azure_v5" / "received"
VISUAL_REVIEWS_PATH = ROOT / "fast_mvp" / "preview_assets" / "goal0014_v5_visual_reviews.json"

BASE_LIMITATIONS = (
    "Receipt-backed presentation derivative only; Rhino/Grasshopper remains geometry authority.",
    "Visual approval and preview approval do not certify manufacturing pass/fail.",
    "The twenty named parts are material and diagnostic groups, not interchangeable modules.",
)


def coverage_limitation(solved_count: int) -> str:
    return (
        "The V5 master is parametric, but only the "
        f"{solved_count} solved configuration{'s' if solved_count != 1 else ''} carry "
        "receipt-backed geometry. Ring sizes and stone sizes between them are deliberately "
        "unavailable rather than approximated from a neighbouring solve."
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _configured(solved: manual_configurator.SolvedConfiguration) -> dict[str, Any]:
    """Run the real Studio path so the registry and the product agree by construction."""
    length, width, depth = solved.stone_dimensions_mm
    report = manual_configurator.configure(
        {
            "stone_shape": "round",
            "stone_length_mm": length,
            "stone_width_mm": width,
            "stone_depth_mm": depth,
            "ring_size_us": solved.ring_size_us,
            "metal": {"color_family": "white", "purity": "pt950", "finish": "polished"},
            "head_style_id": V5_HEAD_ID,
            "band_style_id": V5_BAND_ID,
        }
    )
    if report["status"] != "accepted":
        raise ValueError(
            f"the V5 configuration {solved.configuration_id} is not gate-accepted: "
            f"{report['validation']}"
        )
    return report


def discover_configurations(
    received_root: Path,
) -> list[tuple[manual_configurator.SolvedConfiguration, Path, bool]]:
    """Find every received configuration and read its identity from its receipt.

    The configurations are not listed anywhere in this file. They are whatever the
    Azure batch actually returned, identified by the request each receipt records —
    which is exactly the value the Goal 0014 fix made the master honour. Returns
    ``(solved, receipt_path, is_rehearsal)`` per configuration, in slug order.
    """
    directories = sorted(
        path
        for path in Path(received_root).iterdir()
        if path.is_dir() and (path / RECEIPT_NAME).is_file()
    )
    if not directories:
        raise ValueError(f"no V5 configuration exports under {received_root}")
    found = []
    for directory in directories:
        receipt_path = directory / RECEIPT_NAME
        receipt = load_receipt(receipt_path)
        configuration = configuration_of(receipt)
        stone = configuration["center_stone"]
        found.append(
            (
                manual_configurator.SolvedConfiguration(
                    config_slug(configuration),
                    (stone["length_mm"], stone["width_mm"], stone["depth_mm"]),
                    configuration["ring"]["size"],
                    sha256_file(receipt_path),
                ),
                receipt_path,
                bool(receipt.get("rehearsal")),
            )
        )
    slugs = [item[0].configuration_id for item in found]
    if len(set(slugs)) != len(slugs):
        raise ValueError("two received configurations produced the same slug")
    return sorted(found, key=lambda item: item[0].configuration_id)


def load_visual_reviews(path: Path) -> dict[str, dict[str, Any]]:
    """Return the per-configuration human review records.

    Human approval is the one promotion input that cannot be derived from files on
    disk, so it is declared. A configuration with no record here cannot be
    promoted — silence is never read as approval.
    """
    document = _read_json(path)
    reviews = document.get("reviews")
    if not isinstance(reviews, dict):
        raise ValueError(f"{path} does not declare a reviews object")
    return reviews


def differential_summary(report: dict[str, Any]) -> str:
    """Describe the differential in the registry using the report's own contents."""
    jobs = ", ".join(str(config["job_id"]) for config in report["configs"])
    pairs = len(report["pairs"])
    complete = all(pair.get("all_files_changed") for pair in report["pairs"])
    return (
        f"scripts/geometry_differential.py over {report['config_count']} receipt-backed "
        f"configurations ({jobs}): status {report['status']}, {pairs} pairs, "
        + ("every pair differs on every exported part." if complete else "see report.")
    )


def build_asset(
    solved: manual_configurator.SolvedConfiguration,
    review: dict[str, Any],
    differential_sha256: str,
    differential_config_count: int,
    differential_reason: str,
    solved_count: int,
    asset_dir: Path = ASSET_DIR,
) -> dict[str, Any]:
    report = _configured(solved)
    projection, signature = geometry_signature(
        report["canonical_spec"], report["flat_parameters"]
    )

    manifest_path = asset_dir / f"solitaire_parametric_master_v5_{solved.configuration_id}.browser_mesh.json"
    delivery_manifest = _read_json(manifest_path)
    binary_path = asset_dir / delivery_manifest["binary"]["file"]

    if delivery_manifest["source_receipt_sha256"] != solved.receipt_sha256:
        raise ValueError(
            f"{solved.configuration_id}: browser asset was built from a different receipt"
        )
    if delivery_manifest["template_id"] != V5_TEMPLATE_ID:
        raise ValueError(f"{solved.configuration_id}: browser asset template mismatch")

    visual_review = ROOT / str(review["path"])
    limitations = list(BASE_LIMITATIONS)
    limitations.append(coverage_limitation(solved_count))
    if review.get("inherited_from"):
        limitations.append(str(review["reason"]))

    return {
        "approval": {
            "config_differential": {
                "config_count": differential_config_count,
                "reason": differential_reason,
                "report_path": DIFFERENTIAL_REPORT,
                "report_sha256": differential_sha256,
                "status": "passed",
            },
            "differential_sha256": differential_sha256,
            "status": "approved",
            "visual_review": "approved",
            "visual_review_sha256": sha256_file(visual_review),
        },
        "approximated_parameters": [],
        "asset_id": f"goal0014-v5-{solved.configuration_id}",
        "asset_type": "full_ring",
        "asset_version": V5_ASSET_VERSION,
        "classification": "exact",
        "delivery": {
            "content": {
                "byte_size": binary_path.stat().st_size,
                "sha256": sha256_file(binary_path),
                "url": f"{ASSET_URL_PREFIX}/{binary_path.name}",
            },
            "format": "myjewl_meshbin_v1",
            "manifest": {
                "byte_size": manifest_path.stat().st_size,
                "sha256": sha256_file(manifest_path),
                "url": f"{ASSET_URL_PREFIX}/{manifest_path.name}",
            },
            "renderer": "threejs-r166",
        },
        "enabled": True,
        "geometry": {
            "index_count": delivery_manifest["index_count"],
            "material_roles": ["metal", "stone"],
            "named_parts_are_modules": False,
            "part_count": len(delivery_manifest["parts"]),
            "preview_axes": "ThreeJS_Y_up",
            "source_axes": "Rhino_Z_up",
            "transform": {
                "rotation_x_radians": delivery_manifest["source_to_preview_transform"][
                    "rotation_x_radians"
                ],
                "scale": delivery_manifest["source_to_preview_transform"]["scale"],
            },
            "triangle_count": delivery_manifest["triangle_count"],
            "units": "mm",
            "vertex_count": delivery_manifest["vertex_count"],
        },
        "limitations": limitations,
        "manufacturing_authority": False,
        "manufacturing_state": "pending_not_run_needs_calibration",
        "match": {
            "approved_ranges": [],
            "geometry_projection": projection,
            "geometry_signature": signature,
            "policy": "discrete_signature",
        },
        "provenance": {
            "authority": "receipt_backed_rhino_derived_browser_preview",
            "definition_kind": "parametric_master",
            "definition_path": V5_DEFINITION_PATH,
            "definition_sha256": sha256_file(ROOT / V5_DEFINITION_PATH),
            "source_obj_sha256": delivery_manifest["source_obj_sha256"],
            "source_receipt_sha256": solved.receipt_sha256,
            "template_id": V5_TEMPLATE_ID,
        },
        "representation_kind": "full_ring_asset",
        "represented_parameters": geometry_parameter_paths(projection),
        "unsupported_parameters": [],
    }


DEFAULT_REGISTRY_VERSION = "myjewl-preview-assets-production-v0.2"


def build_registry(
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    *,
    received_root: Path = RECEIVED_ROOT,
    asset_dir: Path = ASSET_DIR,
    visual_reviews_path: Path = VISUAL_REVIEWS_PATH,
    differential_path: Path | None = None,
    registry_version: str = DEFAULT_REGISTRY_VERSION,
    allow_rehearsal: bool = False,
) -> dict[str, Any]:
    """Return the registry with every received V5 configuration promoted."""
    registry = _read_json(registry_path)
    differential_path = differential_path or (ROOT / DIFFERENTIAL_REPORT)
    differential = _read_json(differential_path)
    if differential.get("status") != "pass":
        raise ValueError(
            f"{differential_path} does not report a passing config differential"
        )
    differential_sha256 = sha256_file(differential_path)
    differential_config_count = int(differential["config_count"])

    discovered = discover_configurations(received_root)
    rehearsals = [item[0].configuration_id for item in discovered if item[2]]
    if rehearsals and not allow_rehearsal:
        # A rehearsal receipt records a configuration whose geometry is a
        # placeholder. It exists to exercise this pipeline, never to be served.
        raise ValueError(
            "refusing to promote rehearsal configurations into a real registry: "
            + ", ".join(rehearsals)
        )
    reviews = load_visual_reviews(visual_reviews_path)
    missing = [
        solved.configuration_id
        for solved, _receipt, _rehearsal in discovered
        if solved.configuration_id not in reviews
    ]
    if missing:
        raise ValueError(
            "these solved configurations have no recorded visual review and cannot "
            "be promoted: " + ", ".join(missing)
        )

    promoted = [
        build_asset(
            solved,
            reviews[solved.configuration_id],
            differential_sha256,
            differential_config_count,
            differential_summary(differential),
            len(discovered),
            asset_dir=asset_dir,
        )
        for solved, _receipt, _rehearsal in discovered
    ]
    promoted_ids = {asset["asset_id"] for asset in promoted}
    retained = [
        asset for asset in registry["assets"] if asset["asset_id"] not in promoted_ids
    ]
    registry["assets"] = sorted(
        retained + promoted, key=lambda asset: asset["asset_id"]
    )
    registry["registry_version"] = registry_version
    return registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--received-root", type=Path, default=RECEIVED_ROOT)
    parser.add_argument("--asset-dir", type=Path, default=ASSET_DIR)
    parser.add_argument("--visual-reviews", type=Path, default=VISUAL_REVIEWS_PATH)
    parser.add_argument("--differential", type=Path, default=None)
    parser.add_argument("--registry-version", default=DEFAULT_REGISTRY_VERSION)
    parser.add_argument(
        "--allow-rehearsal",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the promoted registry back to disk (default: print only)",
    )
    args = parser.parse_args(argv)

    if args.allow_rehearsal and args.registry.resolve() == DEFAULT_REGISTRY_PATH.resolve():
        raise SystemExit(
            "rehearsal configurations may never be promoted into the production registry"
        )

    registry = build_registry(
        args.registry,
        received_root=args.received_root,
        asset_dir=args.asset_dir,
        visual_reviews_path=args.visual_reviews,
        differential_path=args.differential,
        registry_version=args.registry_version,
        allow_rehearsal=args.allow_rehearsal,
    )
    payload = json.dumps(registry, indent=2, sort_keys=True) + "\n"
    if args.write:
        args.registry.write_text(payload, encoding="utf-8")
        # Re-load through the production validator so a bad promotion never
        # survives the script that wrote it.
        load_registry(args.registry, production=True)

    # Goal 0023: promoting an asset makes it previewable, not quotable. The
    # measured-volume table is a separate artefact keyed by geometry signature, and
    # a promoted asset missing from it previews correctly but stays
    # ``pending_geometry`` forever. Name the assets that are in that state so the
    # omission is visible here rather than as a silently unpriced ring.
    quoted = {
        record["geometry_signature"]
        for record in quote_volumes.load_table().get("records", [])
    }
    unquotable = sorted(
        asset["asset_id"]
        for asset in registry["assets"]
        if asset["classification"] == "exact"
        and asset["match"]["geometry_signature"] not in quoted
    )

    print(json.dumps({
        "status": "promoted" if args.write else "preview",
        "registry": str(args.registry),
        "registry_version": registry["registry_version"],
        "assets": [
            {
                "asset_id": asset["asset_id"],
                "classification": asset["classification"],
                "policy": asset["match"]["policy"],
                "definition_kind": asset["provenance"]["definition_kind"],
                "geometry_signature": asset["match"]["geometry_signature"],
            }
            for asset in registry["assets"]
        ],
        "assets_without_a_measured_quote_volume": unquotable,
        "next_step_if_any_are_unquotable": (
            "python3 -m scripts.goal0023_measure_quote_volumes --write"
        ),
        "manufacturing_state": "pending_not_run_needs_calibration",
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
