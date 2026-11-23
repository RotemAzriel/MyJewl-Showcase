#!/usr/bin/env python3
"""Run and verify a one-view Blender geometry-manifest smoke render."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.render_geometry_manifest import (
    expected_mask_values,
    load_render_manifest,
    required_visible_mask_values,
)
from scripts.verify_mask_png import verify_mask


REPO_ROOT = Path(__file__).resolve().parents[1]
RENDER_SCRIPT = REPO_ROOT / "scripts" / "blender_render_batch.py"


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _tail(value: str | bytes, limit: int = 6000) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-limit:] if len(value) > limit else value


def _extract_blender_version(output: str | bytes) -> str | None:
    """Extract Blender's self-reported version for evidence reports."""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    match = re.search(r"\bBlender\s+(\d+\.\d+\.\d+(?:\s+LTS)?)\b", output)
    return match.group(1) if match else None


def _default_params_path(bundle_dir: Path) -> Path:
    flat = bundle_dir / "parameters.json"
    if flat.is_file():
        return flat
    canonical = bundle_dir / "canonical_spec.json"
    if canonical.is_file():
        return canonical
    raise FileNotFoundError(
        f"Bundle has neither parameters.json nor canonical_spec.json: {bundle_dir}"
    )


def resolve_blender_bin(blender_bin: str | None = None) -> str | None:
    if blender_bin:
        return blender_bin
    env_bin = os.environ.get("BLENDER_BIN")
    if env_bin:
        return env_bin
    return shutil.which("blender")


def build_blender_command(
    *,
    blender_bin: str,
    bundle_dir: Path,
    output_dir: Path,
    samples: int,
    resolution: int,
    hdri_dir: Path | None,
    studio_light: bool,
    cycles_device: str = "AUTO",
) -> list[str]:
    manifest = load_render_manifest(bundle_dir / "geometry_manifest.json")
    params_path = _default_params_path(bundle_dir)
    effective_hdri_dir = hdri_dir or bundle_dir / "hdri"
    command = [
        blender_bin,
        "-b",
        "--python",
        str(RENDER_SCRIPT),
        "--",
        "--config-name",
        manifest.job_id,
        "--geometry-manifest",
        str(manifest.path),
        "--params",
        str(params_path),
        "--hdri-dir",
        str(effective_hdri_dir),
        "--output-dir",
        str(output_dir),
        "--samples",
        str(samples),
        "--max-views",
        "1",
        "--resolution",
        str(resolution),
    ]
    if cycles_device != "AUTO":
        command.extend(["--cycles-device", cycles_device])
    if studio_light:
        command.append("--studio-light")
    return command


def expected_artifact_paths(output_dir: Path, view_idx: int = 1) -> dict[str, Path]:
    renders_dir = output_dir / "renders"
    stem = f"{view_idx:04d}"
    return {
        "rgb": renders_dir / f"view_{stem}.png",
        "mask": renders_dir / f"mask_{stem}.png",
        "depth": renders_dir / f"depth_{stem}.exr",
        "normal": renders_dir / f"normal_{stem}.exr",
    }


def verify_render_artifacts(
    *,
    bundle_dir: Path,
    output_dir: Path,
    view_idx: int = 1,
) -> dict[str, Any]:
    manifest = load_render_manifest(bundle_dir / "geometry_manifest.json")
    artifacts = expected_artifact_paths(output_dir, view_idx)
    missing = [
        f"{name}={path}"
        for name, path in artifacts.items()
        if not path.is_file() or path.stat().st_size == 0
    ]
    if missing:
        raise FileNotFoundError(
            "Missing or empty render artifact(s): " + ", ".join(missing)
        )
    mask_report = verify_mask(
        artifacts["mask"],
        expected_mask_values(manifest),
        required_visible=required_visible_mask_values(manifest),
    )
    return {
        "status": "pass",
        "source_mode": "geometry_manifest",
        "job_id": manifest.job_id,
        "export_id": manifest.export_id,
        "artifacts": {
            name: {
                "path": str(path),
                "byte_size": path.stat().st_size,
            }
            for name, path in artifacts.items()
        },
        "mask": mask_report,
    }


def run_smoke(
    *,
    bundle_dir: Path,
    output_dir: Path,
    blender_bin: str | None,
    samples: int,
    resolution: int,
    hdri_dir: Path | None,
    studio_light: bool,
    timeout_seconds: int,
    cycles_device: str = "AUTO",
) -> dict[str, Any]:
    resolved_bin = resolve_blender_bin(blender_bin)
    if not resolved_bin:
        return {
            "status": "blocked",
            "blocker": {
                "kind": "blender_runtime",
                "detail": (
                    "No Blender executable found. Checked --blender-bin, "
                    "BLENDER_BIN, and PATH."
                ),
            },
            "bundle_dir": str(bundle_dir),
            "output_dir": str(output_dir),
        }

    command = build_blender_command(
        blender_bin=resolved_bin,
        bundle_dir=bundle_dir,
        output_dir=output_dir,
        samples=samples,
        resolution=resolution,
        cycles_device=cycles_device,
        hdri_dir=hdri_dir,
        studio_light=studio_light,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        return {
            "status": "blocked",
            "blocker": {
                "kind": "blender_runtime",
                "detail": f"Blender executable could not be started: {exc}",
            },
            "command": command,
            "bundle_dir": str(bundle_dir),
            "output_dir": str(output_dir),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "fail",
            "reason": f"Blender smoke render timed out after {timeout_seconds}s",
            "command": command,
            "stdout_tail": _tail(exc.stdout or ""),
            "stderr_tail": _tail(exc.stderr or ""),
            "bundle_dir": str(bundle_dir),
            "output_dir": str(output_dir),
        }

    if completed.returncode != 0:
        return {
            "status": "fail",
            "reason": f"Blender exited with code {completed.returncode}",
            "blender_version": _extract_blender_version(completed.stdout),
            "command": command,
            "stdout_tail": _tail(completed.stdout),
            "stderr_tail": _tail(completed.stderr),
            "bundle_dir": str(bundle_dir),
            "output_dir": str(output_dir),
        }

    try:
        verification = verify_render_artifacts(
            bundle_dir=bundle_dir,
            output_dir=output_dir,
            view_idx=1,
        )
    except Exception as exc:
        return {
            "status": "fail",
            "reason": str(exc),
            "command": command,
            "stdout_tail": _tail(completed.stdout),
            "stderr_tail": _tail(completed.stderr),
            "bundle_dir": str(bundle_dir),
            "output_dir": str(output_dir),
        }

    return {
        **verification,
        "blender_version": _extract_blender_version(completed.stdout),
        "command": command,
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
        "bundle_dir": str(bundle_dir),
        "output_dir": str(output_dir),
        "samples": samples,
        "resolution": resolution,
        "lighting": "studio" if studio_light else "hdri",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--blender-bin")
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--resolution", type=int, default=256)
    parser.add_argument(
        "--cycles-device",
        choices=("AUTO", "CPU", "GPU"),
        default="CPU",
        help="Use CPU by default for deterministic local smoke validation.",
    )
    parser.add_argument("--hdri-dir", type=Path)
    parser.add_argument(
        "--hdri",
        action="store_true",
        help="Use HDRI lighting instead of deterministic studio smoke lighting.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args(argv)

    output_dir = args.output_dir or args.bundle_dir / "blender_smoke"
    report = run_smoke(
        bundle_dir=args.bundle_dir,
        output_dir=output_dir,
        blender_bin=args.blender_bin,
        samples=args.samples,
        resolution=args.resolution,
        cycles_device=args.cycles_device,
        hdri_dir=args.hdri_dir,
        studio_light=not args.hdri,
        timeout_seconds=args.timeout_seconds,
    )
    if args.report_json:
        _write_report(args.report_json, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] == "pass":
        return 0
    if report["status"] == "blocked":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
