#!/usr/bin/env python3
"""Batch catalog render driver for the Lane-B pilot (Goal 0005, Stage 1 / plan §3.2).

Turns receipt-backed geometry bundles into the multi-view RGB+mask catalog the
retrieval index is built from, with a per-image provenance manifest.

Two things it does that a bare ``blender_render_batch`` invocation does not:

* **Disjoint catalog / query view draws.** ``blender_render_batch`` seeds its
  camera schedule from ``--config-name``, so the catalog shard renders under the
  config id and the held-out query shard renders under ``<config_id>__query``.
  Different seed, different camera angles *and* different HDRI draws — the query
  views are genuinely held out rather than a subset of the indexed ones.
* **Metal randomised per shard.** Metal is a cost/appearance overlay, never
  geometry (settled decision 6), so the alloy is drawn per shard. The draw is
  recorded as **render provenance, not a label**: metal is a typed customer input,
  the image lane never infers or scores it, and randomising it is what keeps it a
  nuisance variable the embedding must learn to ignore.

"Catalog" means MyJewl's **internal validated catalog** — configs from
``scripts/generate_tier1_configs.py`` through the live gate and receipt-backed
Rhino geometry. No retailer catalog and no retailer style label enters it.

Checkpoint/resume comes from ``blender_render_batch --resume``: a shard whose
views are already on disk is skipped, so an interrupted batch resumes in place.
This script never starts a VM and never touches Rhino; Blender renders are
non-certifying evidence.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import time
import zlib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.blender_manifest_smoke import resolve_blender_bin  # noqa: E402
from scripts.render_geometry_manifest import load_render_manifest  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
RENDER_SCRIPT = REPO_ROOT / "scripts" / "blender_render_batch.py"

CATALOG_SPLIT = "catalog"
QUERY_SPLIT = "query"

# Alloys the Blender material table knows about, grouped by the colour family
# the retrieval lane reports. Family is the label; alloy is the appearance draw.
ALLOYS_BY_FAMILY = {
    "yellow": ("14k_yellow", "18k_yellow"),
    "white": ("14k_white", "18k_white", "pt950"),
    "rose": ("14k_rose", "18k_rose"),
}
FAMILY_BY_ALLOY = {
    alloy: family for family, alloys in ALLOYS_BY_FAMILY.items() for alloy in alloys
}


def bundle_config(bundle_dir: Path) -> dict[str, Any]:
    """Read a bundle's mounting parameters from flat or canonical form."""
    flat_path = bundle_dir / "parameters.json"
    if flat_path.is_file():
        flat = json.loads(flat_path.read_text(encoding="utf-8"))
        return {
            "config_id": str(flat.get("config_id") or bundle_dir.name),
            "stone_shape": str(flat["stone_shape"]).strip().lower(),
            "stone_length_mm": float(flat["stone_length_mm"]),
            "stone_width_mm": float(flat["stone_width_mm"]),
            "stone_depth_mm": float(flat["stone_depth_mm"]),
            "center_setting": str(flat["center_setting"]).strip().lower(),
            "prong_count": int(flat.get("prong_count") or 0),
            "band_width_mm": float(flat["band_width_mm"]),
            "band_thickness_mm": float(flat.get("band_thickness_mm") or 0.0),
            "ring_size_us": float(flat.get("ring_size_us") or 0.0),
        }

    canonical_path = bundle_dir / "canonical_spec.json"
    if not canonical_path.is_file():
        raise FileNotFoundError(
            f"bundle has neither parameters.json nor canonical_spec.json: {bundle_dir}"
        )
    spec = json.loads(canonical_path.read_text(encoding="utf-8"))
    center = next(
        (stone for stone in spec["stones"] if stone.get("role") == "center"),
        spec["stones"][0],
    )
    return {
        "config_id": str(spec["job"]["job_id"]),
        "stone_shape": str(center["shape"]).strip().lower(),
        "stone_length_mm": float(center["dimensions_mm"]["length"]),
        "stone_width_mm": float(center["dimensions_mm"]["width"]),
        "stone_depth_mm": float(center["dimensions_mm"]["depth"]),
        "center_setting": str(spec["setting"]["center_setting"]).strip().lower(),
        "prong_count": int(spec["setting"].get("prong_count") or 0),
        "band_width_mm": float(spec["ring"]["band_width_mm"]),
        "band_thickness_mm": float(spec["ring"].get("band_thickness_mm") or 0.0),
        "ring_size_us": float(spec["ring"].get("size") or 0.0),
    }


def draw_alloy(config_id: str, split: str, seed: int) -> str:
    """Deterministically draw a per-shard alloy (reproducible provenance)."""
    rng = random.Random(f"{seed}:{config_id}:{split}")
    family = rng.choice(sorted(ALLOYS_BY_FAMILY))
    return rng.choice(ALLOYS_BY_FAMILY[family])


def write_params_override(
    bundle_dir: Path,
    shard_dir: Path,
    alloy: str,
) -> Path:
    """Copy the bundle params with the alloy overridden for this shard."""
    source = (
        bundle_dir / "parameters.json"
        if (bundle_dir / "parameters.json").is_file()
        else bundle_dir / "canonical_spec.json"
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    if "metal_alloy" in payload or "config_id" in payload:
        payload["metal_alloy"] = alloy
        payload["metal_type"] = "platinum" if alloy.startswith("pt") else "gold"
    else:
        payload.setdefault("metal", {})
        payload["metal"]["alloy"] = alloy
        payload["metal"]["type"] = "platinum" if alloy.startswith("pt") else "gold"
    shard_dir.mkdir(parents=True, exist_ok=True)
    override = shard_dir / "render_params.json"
    override.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return override


def build_command(
    *,
    blender_bin: str,
    config_name: str,
    manifest_path: Path,
    params_path: Path,
    output_dir: Path,
    hdri_dir: Path | None,
    views: int,
    samples: int,
    resolution: int,
    cycles_device: str,
    view_seed: int,
    resume: bool,
) -> list[str]:
    command = [
        blender_bin,
        "-b",
        "--python",
        str(RENDER_SCRIPT),
        "--",
        "--config-name",
        config_name,
        "--view-seed",
        str(view_seed),
        "--geometry-manifest",
        str(manifest_path),
        "--params",
        str(params_path),
        "--hdri-dir",
        str(hdri_dir or output_dir / "hdri"),
        "--output-dir",
        str(output_dir),
        "--samples",
        str(samples),
        "--max-views",
        str(views),
        "--resolution",
        str(resolution),
        "--cycles-device",
        cycles_device,
    ]
    if hdri_dir is None:
        command.append("--studio-light")
    if resume:
        command.append("--resume")
    return command


def collect_shard_images(
    shard_dir: Path,
    config: dict[str, Any],
    *,
    split: str,
    alloy: str,
    output_root: Path,
) -> list[dict[str, Any]]:
    """Turn a rendered shard into per-image provenance records."""
    renders = shard_dir / "renders"
    if not renders.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for rgb in sorted(renders.glob("view_*.png")):
        view_index = int(rgb.stem.split("_")[-1])
        mask = renders / f"mask_{view_index:04d}.png"
        records.append(
            {
                "image_path": str(rgb.relative_to(output_root)),
                "mask_path": str(mask.relative_to(output_root)) if mask.is_file() else None,
                "config_id": config["config_id"],
                "split": split,
                "view_index": view_index,
                "stone_shape": config["stone_shape"],
                # The typed spec travels with every image so the eval harness
                # can feed the lane typed inputs it never has to infer.
                "stone_length_mm": config["stone_length_mm"],
                "stone_width_mm": config["stone_width_mm"],
                "stone_depth_mm": config["stone_depth_mm"],
                "ring_size_us": config["ring_size_us"],
                "center_setting": config["center_setting"],
                "prong_count": config["prong_count"],
                "band_width_ratio": round(
                    config["band_width_mm"] / config["stone_width_mm"], 5
                ),
                "band_thickness_ratio": round(
                    config["band_thickness_mm"] / config["stone_width_mm"], 5
                ),
                "band_width_mm": config["band_width_mm"],
                "band_thickness_mm": config["band_thickness_mm"],
                # Appearance provenance for this shard — never a retrieval axis.
                "metal_alloy": alloy,
                "metal_color_family": FAMILY_BY_ALLOY.get(alloy, "yellow"),
            }
        )
    return records


def render_bundle(
    bundle_dir: Path,
    output_root: Path,
    *,
    blender_bin: str,
    catalog_views: int,
    query_views: int,
    samples: int,
    resolution: int,
    hdri_dir: Path | None,
    cycles_device: str,
    seed: int,
    resume: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Render one bundle's catalog and query shards."""
    config = bundle_config(bundle_dir)
    manifest = load_render_manifest(bundle_dir / "geometry_manifest.json")
    shards: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []

    for split, views in ((CATALOG_SPLIT, catalog_views), (QUERY_SPLIT, query_views)):
        if views <= 0:
            continue
        # blender_render_batch requires --config-name == manifest job_id, so the
        # shards are separated by an explicit --view-seed instead: same geometry,
        # disjoint camera angles and HDRI draws.
        config_name = config["config_id"]
        view_seed = zlib.crc32(f"{seed}:{config['config_id']}:{split}".encode()) & 0x7FFFFFFF
        shard_dir = output_root / config["config_id"] / split
        alloy = draw_alloy(config["config_id"], split, seed)
        params_path = write_params_override(bundle_dir, shard_dir, alloy)
        command = build_command(
            blender_bin=blender_bin,
            config_name=config_name,
            manifest_path=manifest.path,
            params_path=params_path,
            output_dir=shard_dir,
            hdri_dir=hdri_dir,
            views=views,
            samples=samples,
            resolution=resolution,
            cycles_device=cycles_device,
            view_seed=view_seed,
            resume=resume,
        )
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            returncode = completed.returncode
            # Blender exits 0 even when the embedded Python raises, so the
            # rendered-view count below is the real success signal and stdout is
            # kept for diagnosis.
            output_tail = ((completed.stdout or "") + (completed.stderr or ""))[-2500:]
        except subprocess.TimeoutExpired:
            returncode = -1
            output_tail = f"timed out after {timeout_seconds}s"

        shard_records = collect_shard_images(
            shard_dir, config, split=split, alloy=alloy, output_root=output_root
        )
        records.extend(shard_records)
        shards.append(
            {
                "split": split,
                "config_name": config_name,
                "shard_dir": str(shard_dir),
                "metal_alloy": alloy,
                "requested_views": views,
                "rendered_views": len(shard_records),
                "returncode": returncode,
                "view_seed": view_seed,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "output_tail": (
                    output_tail if (returncode != 0 or not shard_records) else None
                ),
            }
        )

    ok = all(shard["rendered_views"] > 0 for shard in shards) and bool(shards)
    return {
        "config_id": config["config_id"],
        "bundle_dir": str(bundle_dir),
        "export_id": manifest.export_id,
        "status": "pass" if ok else "fail",
        "config": config,
        "shards": shards,
        "records": records,
    }


def discover_bundles(paths: list[str]) -> list[Path]:
    """Resolve bundle directories (a bundle has a geometry_manifest.json)."""
    bundles: list[Path] = []
    for raw in paths:
        candidate = Path(raw)
        if (candidate / "geometry_manifest.json").is_file():
            bundles.append(candidate)
            continue
        bundles.extend(
            sorted(
                found.parent
                for found in candidate.glob("*/geometry_manifest.json")
            )
        )
    return bundles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundles", nargs="+", help="Bundle dirs or a dir containing them.")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--catalog-views", type=int, default=24)
    parser.add_argument("--query-views", type=int, default=12)
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--resolution", type=int, default=256)
    parser.add_argument("--hdri-dir", type=Path, default=None,
                        help="Directory of .hdr files; omit for deterministic studio light.")
    parser.add_argument("--cycles-device", choices=("AUTO", "CPU", "GPU"), default="CPU")
    parser.add_argument("--blender-bin", default=None)
    parser.add_argument("--seed", type=int, default=20260725)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--report-json", type=Path, default=None)
    parser.add_argument("--clean", action="store_true",
                        help="Delete the output root first (discards checkpoints).")
    args = parser.parse_args(argv)

    blender_bin = resolve_blender_bin(args.blender_bin)
    if not blender_bin:
        print(json.dumps({"status": "blocked", "reason": "no Blender executable found"}, indent=2))
        return 2

    bundles = discover_bundles(args.bundles)
    if not bundles:
        print(json.dumps({"status": "fail", "reason": "no bundles found"}, indent=2))
        return 1

    output_root = args.output_root
    if args.clean and output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for bundle in bundles:
        result = render_bundle(
            bundle,
            output_root,
            blender_bin=blender_bin,
            catalog_views=args.catalog_views,
            query_views=args.query_views,
            samples=args.samples,
            resolution=args.resolution,
            hdri_dir=args.hdri_dir,
            cycles_device=args.cycles_device,
            seed=args.seed,
            resume=not args.no_resume,
            timeout_seconds=args.timeout_seconds,
        )
        records.extend(result.pop("records"))
        results.append(result)
        print(
            f"[{result['status']}] {result['config_id']}: "
            + ", ".join(
                f"{shard['split']}={shard['rendered_views']}/{shard['requested_views']}"
                for shard in result["shards"]
            )
        )

    manifest = {
        "schema_version": 1,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "output_root": str(output_root),
        "lighting": "hdri" if args.hdri_dir else "studio",
        "samples": args.samples,
        "resolution": args.resolution,
        "seed": args.seed,
        "non_certifying_note": (
            "Blender renders are non-certifying evidence; they never establish "
            "manufacturability."
        ),
        "metal_note": (
            "metal_alloy / metal_color_family are per-shard appearance provenance, "
            "not labels: metal is a typed customer input and is excluded from "
            "retrieval, consensus, and gate metrics."
        ),
        "records": records,
    }
    (output_root / "render_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    report = {
        "status": "pass" if all(r["status"] == "pass" for r in results) else "fail",
        "bundles": len(results),
        "images": len(records),
        "catalog_images": sum(1 for r in records if r["split"] == CATALOG_SPLIT),
        "query_images": sum(1 for r in records if r["split"] == QUERY_SPLIT),
        "render_manifest": str(output_root / "render_manifest.json"),
        "results": results,
    }
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
