#!/usr/bin/env python3
"""
scripts/s3_upload.py
Mac-side S3 upload for synthetic jewelry renders.

Uploads one config's render directory to:
  s3://{bucket}/configs/{archetype}/{config_name}/

Per-config layout:
  parameters.json
  metadata.json
  model.stl
  renders/view_{NNN}.png
  renders/view_{NNN}_mask.png
  renders/view_{NNN}_depth.exr
  renders/view_{NNN}_normal.exr

IMPORTANT: No bpy import — this is a Mac-side script.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import botocore.exceptions
from boto3.s3.transfer import TransferConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MULTIPART_THRESHOLD_BYTES = 8 * 1024 * 1024  # 8 MB
MAX_RETRIES = 3
RENDER_RESOLUTION = [1024, 1024]
TIER1_VALIDATION_LOCAL_PATH = Path("tier1_m3_validation.json")
DATASETS_S3_PREFIX = "datasets/tier1_m3_validation.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------


class _ProgressLogger:
    """Accumulates uploaded bytes and logs progress for a single file."""

    def __init__(self, filename: str, total_bytes: int) -> None:
        self._filename = filename
        self._total = total_bytes
        self._seen = 0

    def __call__(self, bytes_amount: int) -> None:
        self._seen += bytes_amount
        pct = 100.0 * self._seen / self._total if self._total else 0.0
        log.info(
            "  %s: %d / %d bytes (%.1f%%)",
            self._filename,
            self._seen,
            self._total,
            pct,
        )


# ---------------------------------------------------------------------------
# metadata.json builder  (Section E / §4.3)
# ---------------------------------------------------------------------------


def build_metadata_json(
    config_id: str,
    tier: int,
    render_count: int,
    sample_count: int,
    blender_version: str,
    rhino_version: str,
    stl_file_size_bytes: int,
    render_total_seconds: float,
    geometry_gate_results: dict,
    constraint_violations: list,
) -> dict:
    """
    Returns dict matching data_pipeline_architecture.md §4.3 schema.

    render_resolution is always [1024, 1024] (§3.4 fixed spec).
    """
    return {
        "config_id": config_id,
        "tier": tier,
        "render_count": render_count,
        "render_resolution": RENDER_RESOLUTION,
        "sample_count": sample_count,
        "blender_version": blender_version,
        "rhino_version": rhino_version,
        "stl_file_size_bytes": stl_file_size_bytes,
        "render_total_seconds": render_total_seconds,
        "geometry_gate_results": geometry_gate_results,
        "constraint_violations": constraint_violations,
    }


# ---------------------------------------------------------------------------
# tier1_m3_validation.json helpers  (Section F)
# ---------------------------------------------------------------------------


def _load_validation_file(local_path: Path) -> dict:
    """Load existing validation file or return a fresh scaffold."""
    if local_path.exists():
        with local_path.open("r") as fh:
            return json.load(fh)
    return {"tier": 1, "config_count": 0, "configs": []}


def _append_validation_entry(
    local_path: Path,
    config_id: str,
    s3_prefix: str,
    render_count: int,
    accepted: bool = True,
) -> None:
    """
    Append one config entry to the local tier1_m3_validation.json file.
    The caller is responsible for uploading the updated file to S3.
    """
    data = _load_validation_file(local_path)
    # Avoid duplicates: remove existing entry for same config_id if re-running.
    data["configs"] = [c for c in data["configs"] if c["config_id"] != config_id]
    data["configs"].append(
        {
            "config_id": config_id,
            "s3_prefix": s3_prefix,
            "render_count": render_count,
            "accepted": accepted,
        }
    )
    data["config_count"] = len(data["configs"])
    with local_path.open("w") as fh:
        json.dump(data, fh, indent=2)
    log.info("Updated local validation file: %s (%d entries)", local_path, data["config_count"])


# ---------------------------------------------------------------------------
# Low-level upload with retry
# ---------------------------------------------------------------------------


def _upload_file_with_retry(
    s3_client,
    local_path: Path,
    bucket: str,
    s3_key: str,
    transfer_config: TransferConfig,
) -> None:
    """
    Upload local_path → s3://bucket/s3_key.
    Retries up to MAX_RETRIES times with exponential backoff.
    Raises on final failure.
    """
    file_size = local_path.stat().st_size
    callback = _ProgressLogger(local_path.name, file_size)

    for attempt in range(MAX_RETRIES):
        try:
            log.info(
                "Uploading %s → s3://%s/%s (%.2f MB, attempt %d/%d)",
                local_path,
                bucket,
                s3_key,
                file_size / (1024 * 1024),
                attempt + 1,
                MAX_RETRIES,
            )
            # Reset progress counter on retry
            callback._seen = 0
            s3_client.upload_file(
                str(local_path),
                bucket,
                s3_key,
                Config=transfer_config,
                Callback=callback,
            )
            log.info("  OK: %s", s3_key)
            return
        except (boto3.exceptions.S3UploadFailedError, botocore.exceptions.ClientError) as exc:
            wait = 2 ** attempt  # 1s, 2s, 4s
            if attempt + 1 < MAX_RETRIES:
                log.warning("  Upload failed (%s). Retrying in %ds...", exc, wait)
                time.sleep(wait)
            else:
                log.error("  Upload FAILED after %d attempts: %s", MAX_RETRIES, exc)
                raise


# ---------------------------------------------------------------------------
# Core upload function  (Section D)
# ---------------------------------------------------------------------------


def upload_config(
    config_name: str,
    archetype: str,
    local_render_dir: Path,
    stl_path: Path,
    parameters_json_path: Path,
    metadata_dict: dict,
    s3_bucket: str,
    dry_run: bool = False,
) -> list[str]:
    """
    Upload all files for one config to S3.

    Returns list of uploaded S3 keys.
    If dry_run=True, prints keys without uploading.

    S3 prefix: configs/{archetype}/{config_name}/
    """
    base_prefix = f"configs/{archetype}/{config_name}"

    # --- Build the list of (local_path, s3_key) pairs ---
    uploads: list[tuple[Path, str]] = []

    # Static files
    uploads.append((parameters_json_path, f"{base_prefix}/parameters.json"))
    uploads.append((stl_path, f"{base_prefix}/model.stl"))

    # Write metadata.json to a temp location inside local_render_dir's parent
    metadata_local = local_render_dir.parent / "metadata.json"
    if not dry_run:
        with metadata_local.open("w") as fh:
            json.dump(metadata_dict, fh, indent=2)
    uploads.append((metadata_local, f"{base_prefix}/metadata.json"))

    # Render files: upload everything found in the renders directory.
    # Blender output naming uses frame number suffix (e.g. view_0001.png, mask_0001.png)
    # — do not hardcode expected filenames; scan the directory instead.
    for rf in sorted(local_render_dir.glob("*")):
        if rf.is_file():
            uploads.append((rf, f"{base_prefix}/renders/{rf.name}"))

    # --- Dry run: just print keys ---
    if dry_run:
        log.info("[DRY RUN] Would upload %d files to s3://%s/%s/", len(uploads), s3_bucket, base_prefix)
        for _, s3_key in uploads:
            print(f"s3://{s3_bucket}/{s3_key}")
        return [s3_key for _, s3_key in uploads]

    # --- Validate local files exist before starting ---
    missing = [str(lp) for lp, _ in uploads if not lp.exists() and not str(lp).endswith("metadata.json")]
    if missing:
        log.error("Missing local files:\n  %s", "\n  ".join(missing))
        raise FileNotFoundError(f"{len(missing)} required file(s) not found")

    render_files_count = sum(1 for lp, key in uploads if "/renders/" in key)
    log.info("Found %d render files to upload from %s", render_files_count, local_render_dir)

    # --- Upload ---
    s3_client = boto3.client("s3")
    transfer_cfg = TransferConfig(multipart_threshold=MULTIPART_THRESHOLD_BYTES)

    uploaded_keys: list[str] = []
    failed_keys: list[str] = []

    for local_path, s3_key in uploads:
        try:
            _upload_file_with_retry(s3_client, local_path, s3_bucket, s3_key, transfer_cfg)
            uploaded_keys.append(s3_key)
        except Exception:
            failed_keys.append(s3_key)

    if failed_keys:
        log.error("FAILED keys:\n  %s", "\n  ".join(failed_keys))
        raise RuntimeError(f"{len(failed_keys)} file(s) failed to upload")

    return uploaded_keys


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload one jewelry config's renders to S3.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example:
  python3 scripts/s3_upload.py \\
    --config-name config_solitaire_round_1.00ct_prong4_14ky_2.0mm_us65 \\
    --archetype solitaire \\
    --local-dir /path/to/output/config_name/ \\
    --stl /path/to/model.stl \\
    --params /path/to/parameters.json \\
    --render-seconds 1234.5 \\
    --s3-bucket myjewl-dataset
""",
    )
    parser.add_argument("--config-name", required=True, help="Full config identifier string")
    parser.add_argument(
        "--archetype",
        required=True,
        choices=["solitaire", "halo", "trilogy"],
        help="Ring archetype",
    )
    parser.add_argument(
        "--local-dir",
        required=True,
        type=Path,
        help="Directory containing renders/ subfolder and model files",
    )
    parser.add_argument("--stl", required=True, type=Path, help="Path to model.stl")
    parser.add_argument("--params", required=True, type=Path, help="Path to parameters.json")
    parser.add_argument(
        "--render-seconds",
        required=True,
        type=float,
        help="Total render wall-clock time in seconds",
    )
    parser.add_argument("--s3-bucket", required=True, help="Target S3 bucket name")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print S3 keys that would be uploaded without actually uploading",
    )
    # Optional metadata overrides
    parser.add_argument("--tier", type=int, default=1, help="Dataset tier (default: 1)")
    parser.add_argument("--render-count", type=int, default=50, help="Number of renders (default: 50)")
    parser.add_argument("--sample-count", type=int, default=128, help="Blender sample count (default: 128)")
    parser.add_argument("--blender-version", default="4.2.0", help="Blender version string")
    parser.add_argument("--rhino-version", default="8.0.0.0", help="Rhino.Compute version string")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    local_dir = args.local_dir.resolve()
    render_dir = local_dir / "renders"
    stl_path = args.stl.resolve()
    params_path = args.params.resolve()

    if not args.dry_run:
        for p, label in [(local_dir, "--local-dir"), (render_dir, "renders/"), (stl_path, "--stl"), (params_path, "--params")]:
            if not p.exists():
                log.error("%s does not exist: %s", label, p)
                return 1

    # Read STL size for metadata
    stl_size = stl_path.stat().st_size if stl_path.exists() else 0

    # Build geometry_gate_results from parameters.json if available
    geometry_gate_results = {
        "minimum_structural_floors": "pending",
        "stone_metal_collision": "pending",
        "stone_stone_collision": "pending",
        "mountability_and_setting_access": "pending",
        "mesh_closedness_and_manifold_suitability": "pending",
    }
    constraint_violations: list = []
    if params_path.exists():
        try:
            with params_path.open("r") as fh:
                params_data = json.load(fh)
            if "geometry_gates" in params_data:
                geometry_gate_results = params_data["geometry_gates"]
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not parse parameters.json for gate results: %s", exc)

    metadata = build_metadata_json(
        config_id=args.config_name,
        tier=args.tier,
        render_count=args.render_count,
        sample_count=args.sample_count,
        blender_version=args.blender_version,
        rhino_version=args.rhino_version,
        stl_file_size_bytes=stl_size,
        render_total_seconds=args.render_seconds,
        geometry_gate_results=geometry_gate_results,
        constraint_violations=constraint_violations,
    )

    log.info(
        "Starting upload: config=%s archetype=%s bucket=%s dry_run=%s",
        args.config_name,
        args.archetype,
        args.s3_bucket,
        args.dry_run,
    )

    try:
        uploaded_keys = upload_config(
            config_name=args.config_name,
            archetype=args.archetype,
            local_render_dir=render_dir,
            stl_path=stl_path,
            parameters_json_path=params_path,
            metadata_dict=metadata,
            s3_bucket=args.s3_bucket,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        log.error("Upload failed: %s", exc)
        return 1

    # --- Print manifest ---
    print("\n--- Upload Manifest ---")
    for key in uploaded_keys:
        print(f"s3://{args.s3_bucket}/{key}")
    print(f"--- Total: {len(uploaded_keys)} files ---\n")

    if not args.dry_run:
        # --- Update local tier1_m3_validation.json and push to S3 ---
        s3_prefix_url = f"s3://{args.s3_bucket}/configs/{args.archetype}/{args.config_name}/"
        _append_validation_entry(
            local_path=TIER1_VALIDATION_LOCAL_PATH,
            config_id=args.config_name,
            s3_prefix=s3_prefix_url,
            render_count=args.render_count,
            accepted=True,
        )

        # Upload updated validation index to S3
        log.info("Uploading validation index to s3://%s/%s", args.s3_bucket, DATASETS_S3_PREFIX)
        s3_client = boto3.client("s3")
        transfer_cfg = TransferConfig(multipart_threshold=MULTIPART_THRESHOLD_BYTES)
        try:
            _upload_file_with_retry(
                s3_client,
                TIER1_VALIDATION_LOCAL_PATH,
                args.s3_bucket,
                DATASETS_S3_PREFIX,
                transfer_cfg,
            )
        except Exception as exc:
            log.warning("Could not push validation index to S3 (non-fatal): %s", exc)

    log.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
