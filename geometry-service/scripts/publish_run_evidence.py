#!/usr/bin/env python3
"""Publish geometry/render evidence into a fast_mvp run directory (Goal 0002).

Operator-side bridge: after an explicitly approved Azure Rhino.Compute run and
the local evidence chain (``scripts.geometry_bundle_smoke`` →
``scripts.blender_manifest_smoke``), this script copies the reviewable summary
and preview artifacts into ``fast_mvp/runs/<job_id>/evidence/`` and marks the
run's ``geometry_request.json`` fulfilled. It is never called by the app —
paid compute stays operator-driven.

Everything published is non-certifying evidence; geometry gates stay
``pending``/``not_run``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_mvp.workflow import (  # noqa: E402
    EVIDENCE_DIRNAME,
    EVIDENCE_SUMMARY_FILENAME,
    NON_CERTIFYING_NOTE,
    REQUEST_FILENAME,
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def publish_run_evidence(
    bundle_dir: Path,
    run_dir: Path,
    *,
    bundle_smoke_report: Path | None = None,
    blender_report: Path | None = None,
) -> dict[str, Any]:
    bundle_dir = Path(bundle_dir)
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run directory does not exist: {run_dir}")

    receipt = _read_json(bundle_dir / "export_receipt.json")
    bundle_report = _read_json(
        bundle_smoke_report
        if bundle_smoke_report is not None
        else bundle_dir / "local_smoke_report.json"
    )
    render_report = _read_json(
        blender_report
        if blender_report is not None
        else bundle_dir / "blender_smoke_report.json"
    )

    evidence_dir = run_dir / EVIDENCE_DIRNAME
    evidence_dir.mkdir(parents=True, exist_ok=True)

    artifacts: dict[str, str] = {}
    render_artifacts = render_report.get("artifacts") or {}
    for label in ("rgb", "mask"):
        entry = render_artifacts.get(label) or {}
        source = Path(str(entry.get("path") or ""))
        if not source.is_absolute():
            source = ROOT / source
        if source.is_file():
            target_name = f"{label}_{source.name}"
            shutil.copyfile(source, evidence_dir / target_name)
            artifacts[label] = target_name

    mask = render_report.get("mask") or {}
    summary = {
        "schema_version": 1,
        "published_at_utc": _utc_now(),
        "bundle_dir": str(bundle_dir),
        "export_id": receipt.get("export_id"),
        "job_id": receipt.get("job_id"),
        "definition_sha256": receipt.get("gh_definition_sha256"),
        "definition_version": receipt.get("gh_definition_version"),
        "files": receipt.get("files"),
        "bundle_smoke": bundle_report.get("status"),
        "gate1": (bundle_report.get("gate_1_closedness") or {}).get("status"),
        "blender_smoke": render_report.get("status"),
        "mask_values": mask.get("observed_values"),
        "visible_classes": [
            value
            for value in (mask.get("required_visible_values") or [])
            if value not in (mask.get("missing_visible_classes") or [])
        ],
        "artifacts": artifacts,
        "note": NON_CERTIFYING_NOTE,
    }
    (evidence_dir / EVIDENCE_SUMMARY_FILENAME).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    request_path = run_dir / REQUEST_FILENAME
    try:
        request = _read_json(request_path)
    except (OSError, json.JSONDecodeError):
        request = {
            "schema_version": 1,
            "job_id": run_dir.name,
            "note": NON_CERTIFYING_NOTE,
        }
    request["status"] = "fulfilled"
    request["fulfilled_at_utc"] = _utc_now()
    request["bundle_dir"] = str(bundle_dir)
    request_path.write_text(
        json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", help="Collected bundle directory (receipt + STLs).")
    parser.add_argument("run_dir", help="fast_mvp/runs/<job_id> directory.")
    parser.add_argument("--bundle-smoke-report", default=None)
    parser.add_argument("--blender-report", default=None)
    args = parser.parse_args()
    summary = publish_run_evidence(
        Path(args.bundle_dir),
        Path(args.run_dir),
        bundle_smoke_report=(
            Path(args.bundle_smoke_report) if args.bundle_smoke_report else None
        ),
        blender_report=Path(args.blender_report) if args.blender_report else None,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
