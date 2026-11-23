#!/usr/bin/env python3
"""Rehearse the whole fulfillment chain for one unsolved, in-envelope configuration.

Goal 0016 step 3. Three solved rings prove the master is parametric. They do not
prove that a *fourth* one can travel from an honest ``unavailable`` to a promoted
``exact`` asset without someone editing the pipeline. This script answers that
question without spending anything, by driving the real chain end to end:

    Studio configure -> honest ``unavailable`` + queued derived work order
      -> scripts.build_goal0014_browser_assets
      -> scripts.goal0015_promote_v5
      -> scripts.promotion_gate
      -> resolve the same configuration again -> ``exact``

**The geometry is a placeholder and is never served.** No Rhino solve happened, so
the fourth configuration's OBJ is copied from a real export and its receipt is
marked ``rehearsal: true``. That marking is load-bearing:
``goal0015_promote_v5.build_registry`` refuses to promote a rehearsal receipt
unless explicitly told to, and its CLI refuses ``--allow-rehearsal`` against the
production registry at all. Everything this script writes goes to a sandbox
registry and a sandbox asset directory, both removed before it exits, and the
production registry is hashed before and after to prove it was untouched.

What is really under test is the *plumbing*: that no script needs a code edit to
accept one more configuration. What is deliberately not under test is geometry —
a real fourth configuration still needs an approved Azure solve, its own visual
review, and a passing promotion gate over real receipts.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_mvp import manual_configurator
from fast_mvp.preview_resolution import (
    DEFAULT_REGISTRY_PATH,
    resolve_preview,
    validate_registry,
)
from scripts import goal0015_promote_v5 as promoter
from scripts.build_goal0014_browser_assets import (
    OBJ_NAME,
    RECEIPT_NAME,
    build_all,
    config_slug,
)
from scripts.promotion_gate import evaluate

#: An unsolved configuration the master accepts: one full ring size above the
#: US 6.5 control, with a stone between the control and the large extreme.
REHEARSAL_RING_SIZE_US = 7.0
REHEARSAL_STONE_MM = (7.0, 7.0, 4.2)

#: Copied for its OBJ only. Its measurements are wrong for the rehearsal
#: configuration by construction — that is why nothing here may be promoted.
DONOR_EXPORT = "goal0014-parametric-master-control"

SANDBOX_ASSET_DIRNAME = "goal0016_rehearsal_sandbox"


def _payload(length: float, width: float, depth: float, ring_size: float) -> dict[str, Any]:
    return {
        "stone_shape": "round",
        "stone_length_mm": length,
        "stone_width_mm": width,
        "stone_depth_mm": depth,
        "ring_size_us": ring_size,
        "metal": {"color_family": "white", "purity": "pt950", "finish": "polished"},
        "head_style_id": manual_configurator._V5_HEAD_ID,
        "band_style_id": manual_configurator._V5_BAND_ID,
    }


def write_rehearsal_export(received_root: Path, sandbox_root: Path) -> str:
    """Copy the real exports and add one placeholder-geometry rehearsal export."""
    shutil.copytree(received_root, sandbox_root, dirs_exist_ok=True)
    donor = sandbox_root / DONOR_EXPORT
    receipt = json.loads((donor / RECEIPT_NAME).read_text(encoding="utf-8"))

    spec = copy.deepcopy(receipt["request"]["canonical_spec"])
    spec["ring"]["size"] = REHEARSAL_RING_SIZE_US
    length, width, depth = REHEARSAL_STONE_MM
    spec["stones"][0]["dimensions_mm"] = {
        "length": length,
        "width": width,
        "depth": depth,
    }
    receipt["request"]["canonical_spec"] = spec
    slug = config_slug(
        {
            "ring": {"size": REHEARSAL_RING_SIZE_US},
            "center_stone": {"length_mm": length},
        }
    )
    receipt["job_id"] = f"goal0016-rehearsal-{slug}"
    receipt["rehearsal"] = True
    receipt["authority"] = "rehearsal_placeholder_geometry_not_a_solve"
    receipt["rehearsal_note"] = (
        "Pipeline rehearsal only. No Rhino solve produced this geometry: the mesh is "
        f"copied verbatim from {DONOR_EXPORT} and does NOT correspond to the ring size "
        "or stone recorded above. It must never be promoted or served."
    )

    target = sandbox_root / f"goal0016-rehearsal-{slug}"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(donor / OBJ_NAME, target / OBJ_NAME)
    target.joinpath(RECEIPT_NAME).write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return slug


def rehearse(work_dir: Path) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    production_before = promoter.sha256_file(DEFAULT_REGISTRY_PATH)
    sandbox_assets = ROOT / "fast_mvp" / "static" / "assets" / SANDBOX_ASSET_DIRNAME
    sandbox_received = work_dir / "received"
    sandbox_registry = work_dir / "registry_sandbox.json"

    length, width, depth = REHEARSAL_STONE_MM
    payload = _payload(length, width, depth, REHEARSAL_RING_SIZE_US)

    try:
        # 1. The customer expresses it, and is told the truth.
        before = manual_configurator.configure(payload)
        manifest_before = before["preview_manifest"]
        request = before["fulfillment"]["geometry_request"]
        steps.append({
            "step": "studio_configure_before_solve",
            "accepted": before["status"] == "accepted",
            "preview_status": manifest_before["preview_status"],
            "reason_code": manifest_before["reason_code"],
            "selected_asset": manifest_before["selected_asset"],
            "availability": before["fulfillment"]["availability"],
            "solve_status": request["solve_status"],
            "derived_inner_diameter_mm": request["derived_parameters"]["inner_diameter"],
            "derivation_version": request["derived_parameters"]["derivation_version"],
        })
        if manifest_before["preview_status"] != "unavailable":
            raise SystemExit("an unsolved configuration must resolve unavailable")
        if manifest_before["selected_asset"] is not None:
            raise SystemExit("an unsolved configuration must select no asset")

        # 2. A (placeholder) solve arrives beside the real ones.
        slug = write_rehearsal_export(promoter.RECEIVED_ROOT, sandbox_received)
        asset_id = f"goal0014-v5-{slug}"
        steps.append({
            "step": "stub_solve_received",
            "configuration_id": slug,
            "geometry": "PLACEHOLDER copied from " + DONOR_EXPORT,
            "received_configurations": sorted(
                path.name for path in sandbox_received.iterdir() if path.is_dir()
            ),
        })

        # 3. The unmodified asset builder emits one asset per configuration.
        built = build_all(sandbox_received, sandbox_assets)
        steps.append({
            "step": "build_goal0014_browser_assets",
            "script_edits_required": 0,
            "assets_built": [item["configuration_id"] for item in built],
        })

        # 4. The unmodified promoter promotes all four.
        reviews = json.loads(
            promoter.VISUAL_REVIEWS_PATH.read_text(encoding="utf-8")
        )
        reviews["reviews"][slug] = {
            "path": "output/goal_0011_realism_master_20260730/azure_v4/visual_review.json",
            "inherited_from": "rehearsal_placeholder",
            "reason": (
                "Rehearsal only: a real fourth configuration requires its own operator "
                "visual review or an explicitly recorded inheritance."
            ),
        }
        sandbox_reviews = work_dir / "visual_reviews_sandbox.json"
        sandbox_reviews.write_text(
            json.dumps(reviews, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        shutil.copy2(DEFAULT_REGISTRY_PATH, sandbox_registry)
        registry = promoter.build_registry(
            sandbox_registry,
            received_root=sandbox_received,
            asset_dir=sandbox_assets,
            visual_reviews_path=sandbox_reviews,
            registry_version="myjewl-preview-assets-rehearsal-sandbox",
            allow_rehearsal=True,
        )
        _repoint(registry, sandbox_assets)
        sandbox_registry.write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        validate_registry(registry, production=False)
        steps.append({
            "step": "goal0015_promote_v5",
            "script_edits_required": 0,
            "assets": [asset["asset_id"] for asset in registry["assets"]],
            "estimated_assets": sum(
                1 for asset in registry["assets"] if asset["classification"] != "exact"
            ),
        })

        # 5. The unmodified promotion gate must still pass on the new entry.
        gate = evaluate(
            ROOT / promoter.V5_DEFINITION_PATH,
            next(
                asset
                for asset in registry["assets"]
                if asset["asset_id"] == asset_id
            ),
        )
        steps.append({
            "step": "promotion_gate",
            "script_edits_required": 0,
            "asset_id": asset_id,
            "status": gate["status"],
        })
        if gate["status"] != "pass":
            raise SystemExit(f"promotion gate did not pass for {asset_id}")

        # 6. The same request now resolves exact — to its own asset.
        after = resolve_preview(
            before["canonical_spec"], before["flat_parameters"], registry=registry
        )
        steps.append({
            "step": "studio_resolve_after_promotion",
            "preview_status": after["preview_status"],
            "reason_code": after["reason_code"],
            "asset_id": (after["selected_asset"] or {}).get("asset_id"),
            "geometry_signature": after["geometry"]["signature"],
        })
        if after["preview_status"] != "exact":
            raise SystemExit("the promoted configuration did not resolve exact")
        if (after["selected_asset"] or {}).get("asset_id") != asset_id:
            raise SystemExit("the promoted configuration resolved to the wrong asset")

        # 7. The production registry still refuses a rehearsal receipt.
        try:
            promoter.build_registry(
                DEFAULT_REGISTRY_PATH,
                received_root=sandbox_received,
                asset_dir=sandbox_assets,
                visual_reviews_path=sandbox_reviews,
            )
        except ValueError as exc:
            refusal = str(exc)
        else:
            raise SystemExit("promotion accepted a rehearsal receipt without being asked")
        steps.append({"step": "rehearsal_refused_by_default", "refusal": refusal})
    finally:
        shutil.rmtree(sandbox_assets, ignore_errors=True)

    production_after = promoter.sha256_file(DEFAULT_REGISTRY_PATH)
    return {
        "goal": "0016",
        "rehearsal": True,
        "geometry_authority": "none — placeholder mesh, no Rhino solve",
        "spend_usd": 0,
        "cloud_resources_started": [],
        "configuration": {
            "ring_size_us": REHEARSAL_RING_SIZE_US,
            "stone_mm": list(REHEARSAL_STONE_MM),
        },
        "steps": steps,
        "scripts_edited_to_accept_a_fourth_configuration": 0,
        "production_registry_sha256_before": production_before,
        "production_registry_sha256_after": production_after,
        "production_registry_untouched": production_before == production_after,
        "sandbox_assets_removed": not sandbox_assets.exists(),
        "manufacturing_state": "pending_not_run_needs_calibration",
    }


def _repoint(registry: dict[str, Any], sandbox_assets: Path) -> None:
    """Point the rebuilt V5 entries at the sandbox copies of their files."""
    prefix = f"/static/assets/{sandbox_assets.name}"
    for asset in registry["assets"]:
        if asset["provenance"]["template_id"] != promoter.V5_TEMPLATE_ID:
            continue
        for file_ref in (asset["delivery"]["manifest"], asset["delivery"]["content"]):
            file_ref["url"] = f"{prefix}/{Path(file_ref['url']).name}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=ROOT / "output" / "goal_0016_fulfillable_coverage" / "rehearsal",
    )
    parser.add_argument("--report-json", type=Path, default=None)
    args = parser.parse_args(argv)

    args.work_dir.mkdir(parents=True, exist_ok=True)
    report = rehearse(args.work_dir)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["production_registry_untouched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
