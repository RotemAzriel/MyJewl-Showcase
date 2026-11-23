"""Proposal persistence, geometry-request queue, and review index (Goal 0002).

The app never triggers paid compute. An accepted proposal is persisted under
``fast_mvp/runs/<config_id>/``; a designer may then queue an explicit
``geometry_request.json``. A human operator fulfills queued requests with the
authorized Azure Rhino.Compute flow and publishes evidence back into the run
directory via ``scripts.publish_run_evidence``. Geometry gates remain
``pending``/``not_run`` — nothing here certifies manufacturability.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fast_mvp import geometry_delivery
from fast_mvp.preview_resolution import (
    attach_preview_manifest,
    manifest_sha256,
    resolve_preview,
)
from fast_mvp.quote_volumes import reconcile_quote

PROPOSAL_FILENAME = "proposal_report.json"
PARAMETERS_FILENAME = "parameters.json"
CANONICAL_FILENAME = "canonical_spec.json"
REQUEST_FILENAME = "geometry_request.json"
EVIDENCE_DIRNAME = "evidence"
EVIDENCE_SUMMARY_FILENAME = "summary.json"
QUERY_DIRNAME = "query"
PREVIEW_MANIFEST_FILENAME = "preview_manifest.json"
PREVIEW_APPROVALS_DIRNAME = "preview_approvals"
PREVIEW_APPROVAL_HEAD_FILENAME = "preview_approval_head.json"

PREVIEW_APPROVAL_SCHEMA = "myjewl.preview_approval.v0_1"
PREVIEW_APPROVAL_HEAD_SCHEMA = "myjewl.preview_approval_head.v0_1"
PREVIEW_APPROVAL_SCOPE = "preview_only"

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PREVIEW_APPROVAL_LOCK = threading.RLock()

NON_CERTIFYING_NOTE = (
    "Non-certifying: geometry gates stay pending/not_run until a calibrated "
    "shop profile and human jeweler review exist. MyJewl never certifies "
    "manufacturing pass/fail."
)


class RunNotFoundError(FileNotFoundError):
    """A requested persisted run does not exist inside the runs root."""


class PreviewApprovalValidationError(ValueError):
    """A preview-decision request is malformed or semantically impossible."""


class PreviewApprovalConflictError(RuntimeError):
    """Stored/current preview authority differs from the requested snapshot."""


class GeometryDeliveryRefused(RuntimeError):
    """Geometry was requested for a configuration that may not be delivered.

    Carries the decision so the caller reports the same named reason a decision
    request would have returned. There is no path that downgrades this into
    serving something else instead.
    """

    def __init__(self, decision: Any) -> None:
        super().__init__(decision.detail)
        self.decision = decision


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _approval_utc_now() -> str:
    """Return a sortable server timestamp precise enough for append ordering."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _json_bytes(payload: Any) -> bytes:
    try:
        return (
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PreviewApprovalValidationError(
            "preview approval data must be finite JSON"
        ) from exc


def _payload_sha256(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(_json_bytes(payload).rstrip(b"\n")).hexdigest()


def _strict_json_equal(left: Any, right: Any) -> bool:
    """Compare JSON tokens without Python's ``True == 1`` coercion."""

    try:
        return _json_bytes(left) == _json_bytes(right)
    except PreviewApprovalValidationError:
        return False


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_json_durable(path: Path, payload: Any) -> None:
    """Atomically replace one JSON file and fsync both file and directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    body = _json_bytes(payload)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_name = stream.name
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        _fsync_directory(path.parent)
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def _write_json_exclusive(path: Path, payload: Any) -> None:
    """Atomically publish one complete JSON file without replacing a name."""

    body = _json_bytes(payload)
    temporary_dir = (
        path.parent.parent
        if path.parent.name == PREVIEW_APPROVALS_DIRNAME
        else path.parent
    )
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=temporary_dir,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_name = stream.name
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_name, path)
        _fsync_directory(path.parent)
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
                _fsync_directory(temporary_dir)
            except FileNotFoundError:
                pass


def _safe_run_dir(
    runs_dir: Path,
    job_id: str,
    *,
    require_exists: bool,
) -> Path:
    if not isinstance(job_id, str) or job_id != job_id.strip():
        raise PreviewApprovalValidationError("job_id must be a normalized string")
    if not _JOB_ID_RE.fullmatch(job_id) or job_id in {".", ".."}:
        raise PreviewApprovalValidationError("job_id contains unsafe characters")
    root = Path(runs_dir).resolve()
    unresolved = root / job_id
    if unresolved.is_symlink():
        raise PreviewApprovalValidationError("run directory cannot be a symbolic link")
    candidate = unresolved.resolve()
    if candidate.parent != root:
        raise PreviewApprovalValidationError("job_id must name a direct child run")
    if require_exists and not candidate.is_dir():
        raise RunNotFoundError(f"no persisted proposal for job '{job_id}'")
    return candidate


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def persist_proposal(report: dict[str, Any], runs_dir: Path) -> dict[str, Any]:
    """Persist an accepted proposer report as a run directory."""
    if report.get("status") != "accepted":
        raise ValueError("only accepted proposals are persisted")
    # Recompute at the server persistence boundary even when a caller supplied
    # a preview_manifest. Browser/reference fields are never resolution input.
    attach_preview_manifest(report)
    # Goal 0023: the quote is re-derived from that manifest for the same reason.
    # A volume becomes a price, so a caller must not be able to supply one.
    reconcile_quote(report)
    flat = report.get("flat_parameters")
    canonical = report.get("canonical_spec")
    if not isinstance(flat, dict) or not isinstance(canonical, dict):
        raise ValueError("accepted proposal is missing flat/canonical payloads")
    manifest = report.get("preview_manifest")
    if not isinstance(manifest, dict):
        raise ValueError("accepted proposal is missing its authoritative PreviewManifest")
    job_id = str(flat.get("config_id") or "").strip()
    if not job_id:
        raise ValueError("accepted proposal is missing config_id")

    run_dir = _safe_run_dir(runs_dir, job_id, require_exists=False)
    with _PREVIEW_APPROVAL_LOCK:
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / PREVIEW_MANIFEST_FILENAME
        approvals_dir = run_dir / PREVIEW_APPROVALS_DIRNAME
        approval_head_path = run_dir / PREVIEW_APPROVAL_HEAD_FILENAME
        if approvals_dir.is_symlink():
            raise PreviewApprovalConflictError(
                "preview approval directory cannot be a symbolic link"
            )
        existing_manifest = _read_json(manifest_path)
        has_approvals = (
            approval_head_path.exists()
            or approval_head_path.is_symlink()
            or (approvals_dir.is_dir() and any(approvals_dir.iterdir()))
        )
        if has_approvals and not _strict_json_equal(existing_manifest, manifest):
            raise PreviewApprovalConflictError(
                "cannot replace a PreviewManifest after preview decisions were recorded"
            )
        _write_json(run_dir / PROPOSAL_FILENAME, report)
        _write_json(run_dir / PARAMETERS_FILENAME, flat)
        _write_json(run_dir / CANONICAL_FILENAME, canonical)
        _write_json_durable(manifest_path, manifest)
    return {"job_id": job_id, "run_dir": str(run_dir)}


def persist_image_proposal(
    report: dict[str, Any],
    runs_dir: Path,
    query_images: list[bytes] | None = None,
) -> dict[str, Any]:
    """Persist an accepted image-lane proposal plus the query photo(s).

    The query image is stored alongside the run so ``/review`` can show a
    jeweler what was matched against what — an image-lane decision is not
    reviewable without the photo that produced it.
    """
    persisted = persist_proposal(report, runs_dir)
    run_dir = Path(persisted["run_dir"])
    stored: list[str] = []
    for index, image_bytes in enumerate(query_images or [], start=1):
        query_dir = run_dir / QUERY_DIRNAME
        query_dir.mkdir(parents=True, exist_ok=True)
        name = f"query_{index:02d}.png"
        (query_dir / name).write_bytes(image_bytes)
        stored.append(name)
    if stored:
        report = {**report, "query_images": stored}
        _write_json(run_dir / PROPOSAL_FILENAME, report)
    return {**persisted, "query_images": stored}


def _read_required_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise PreviewApprovalConflictError(f"persisted {label} is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreviewApprovalConflictError(f"persisted {label} is unreadable") from exc
    if not isinstance(payload, dict):
        raise PreviewApprovalConflictError(f"persisted {label} is not a JSON object")
    return payload


def _load_authoritative_preview_state(
    job_id: str,
    runs_dir: Path,
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Reload and cross-check every persisted input before recording a decision."""

    run_dir = _safe_run_dir(runs_dir, job_id, require_exists=True)
    proposal_path = run_dir / PROPOSAL_FILENAME
    if not proposal_path.is_file():
        raise RunNotFoundError(f"no persisted proposal for job '{job_id}'")
    proposal = _read_required_object(proposal_path, "proposal")
    flat = _read_required_object(run_dir / PARAMETERS_FILENAME, "flat parameters")
    canonical = _read_required_object(run_dir / CANONICAL_FILENAME, "canonical spec")
    stored_manifest = _read_required_object(
        run_dir / PREVIEW_MANIFEST_FILENAME,
        "PreviewManifest",
    )

    if proposal.get("status") != "accepted":
        raise PreviewApprovalConflictError("persisted proposal is not accepted")
    if not _strict_json_equal(proposal.get("flat_parameters"), flat):
        raise PreviewApprovalConflictError("proposal and parameters.json differ")
    if not _strict_json_equal(proposal.get("canonical_spec"), canonical):
        raise PreviewApprovalConflictError("proposal and canonical_spec.json differ")
    if str(flat.get("config_id") or "") != job_id:
        raise PreviewApprovalConflictError("persisted config_id does not match the run")
    canonical_job = canonical.get("job") if isinstance(canonical.get("job"), dict) else {}
    if str(canonical_job.get("job_id") or "") != job_id:
        raise PreviewApprovalConflictError("canonical job_id does not match the run")
    if not _strict_json_equal(proposal.get("preview_manifest"), stored_manifest):
        raise PreviewApprovalConflictError(
            "proposal and durable PreviewManifest snapshots differ"
        )
    proposal_preview = proposal.get("preview")
    if isinstance(proposal_preview, dict) and (
        not _strict_json_equal(proposal_preview.get("preview_manifest"), stored_manifest)
    ):
        raise PreviewApprovalConflictError(
            "proposal preview and durable PreviewManifest snapshots differ"
        )
    geometry = stored_manifest.get("geometry")
    stored_signature = geometry.get("signature") if isinstance(geometry, dict) else None
    if proposal.get("preview_geometry_signature") != stored_signature:
        raise PreviewApprovalConflictError("persisted preview signature differs")

    current_manifest = resolve_preview(canonical, flat)
    if not _strict_json_equal(current_manifest, stored_manifest):
        raise PreviewApprovalConflictError(
            "stored PreviewManifest is stale, tampered, or no longer registry-resolvable"
        )
    return run_dir, proposal, flat, canonical, current_manifest


_APPROVAL_REQUEST_KEYS = {"job_id", "manifest_id", "decision", "delivery"}
_DELIVERY_KEYS = {
    "status",
    "displayed_status",
    "renderer",
    "reason_code",
    "asset_id",
    "asset_version",
    "content_sha256",
}
_DELIVERY_REASON_CODES = {
    "approved_exact_asset",
    "approved_estimated_asset",
    "no_approved_preview_asset",
    "no_registry_match",
    "unsupported_geometry_parameters",
    "ambiguous_preview_asset",
    "invalid_canonical_spec",
    "registry_invalid",
    "legacy_preview_manifest_missing",
    "preview_unavailable",
    "preview_manifest_unavailable",
    "manifest_invalid",
    "representation_mismatch",
    "asset_metadata_invalid",
    "asset_url_rejected",
    "asset_loading",
    "asset_unavailable",
    "asset_timeout",
    "asset_size_mismatch",
    "asset_hash_mismatch",
    "delivery_manifest_malformed",
    "delivery_manifest_mismatch",
    "binary_header_invalid",
    "binary_contract_mismatch",
    "crypto_unavailable",
    "asset_delivery_failed",
    "forced_renderer_unavailable",
    "webgl_unavailable",
    "webgl_initialization_failed",
    "threejs_unavailable",
    "renderer_initialization_failed",
    "webgl_render_failed",
    "webgl_context_lost",
}


def _optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise PreviewApprovalValidationError(
            f"delivery.{field} must be null or a normalized string"
        )
    if len(value) > 256:
        raise PreviewApprovalValidationError(f"delivery.{field} is too long")
    return value


def _validated_approval_request(
    job_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PreviewApprovalValidationError("preview approval body must be a JSON object")
    if set(payload) != _APPROVAL_REQUEST_KEYS:
        raise PreviewApprovalValidationError(
            "preview approval body must contain only job_id, manifest_id, decision, and delivery"
        )
    if payload.get("job_id") != job_id:
        raise PreviewApprovalValidationError("request job_id does not match the target run")
    manifest_id = payload.get("manifest_id")
    if not isinstance(manifest_id, str) or not _PREFIXED_SHA256_RE.fullmatch(manifest_id):
        raise PreviewApprovalValidationError("manifest_id must be a sha256 identifier")
    decision = payload.get("decision")
    if decision not in {"approved", "continued_without_preview"}:
        raise PreviewApprovalValidationError(
            "decision must be approved or continued_without_preview"
        )
    raw_delivery = payload.get("delivery")
    if not isinstance(raw_delivery, dict) or set(raw_delivery) != _DELIVERY_KEYS:
        raise PreviewApprovalValidationError(
            "delivery must contain only status, displayed_status, renderer, reason_code, "
            "asset_id, asset_version, and content_sha256"
        )
    status = _optional_string(raw_delivery.get("status"), "status")
    displayed_status = _optional_string(
        raw_delivery.get("displayed_status"),
        "displayed_status",
    )
    renderer = _optional_string(raw_delivery.get("renderer"), "renderer")
    reason_code = _optional_string(raw_delivery.get("reason_code"), "reason_code")
    asset_id = _optional_string(raw_delivery.get("asset_id"), "asset_id")
    asset_version = _optional_string(raw_delivery.get("asset_version"), "asset_version")
    content_sha256 = _optional_string(
        raw_delivery.get("content_sha256"),
        "content_sha256",
    )
    if content_sha256 is not None and not _SHA256_RE.fullmatch(content_sha256):
        raise PreviewApprovalValidationError(
            "delivery.content_sha256 must be a lowercase hexadecimal SHA-256"
        )
    if reason_code is not None and reason_code not in _DELIVERY_REASON_CODES:
        raise PreviewApprovalValidationError(
            "delivery.reason_code must be a stable preview delivery code"
        )
    return {
        "job_id": job_id,
        "manifest_id": manifest_id,
        "decision": decision,
        "delivery": {
            "status": status,
            "displayed_status": displayed_status,
            "renderer": renderer,
            "reason_code": reason_code,
            "asset_id": asset_id,
            "asset_version": asset_version,
            "content_sha256": content_sha256,
        },
    }


def _validate_delivery_decision(
    decision: str,
    delivery: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    resolved_status = manifest.get("preview_status")
    selected = manifest.get("selected_asset")
    if decision == "approved":
        if resolved_status not in {"exact", "estimated"} or not isinstance(selected, dict):
            raise PreviewApprovalValidationError(
                "only an exact or estimated registered preview can be approved"
            )
        if delivery["status"] != "ready":
            raise PreviewApprovalValidationError(
                "approved requires delivery.status ready"
            )
        if delivery["displayed_status"] != resolved_status:
            raise PreviewApprovalValidationError(
                "displayed preview status does not match server resolution"
            )
        if delivery["reason_code"] != manifest.get("reason_code"):
            raise PreviewApprovalValidationError(
                "delivery reason_code does not match server resolution"
            )
    else:
        if delivery["status"] not in {"unavailable", "failure"}:
            raise PreviewApprovalValidationError(
                "continued_without_preview requires unavailable or failure delivery"
            )
        if delivery["displayed_status"] != "unavailable":
            raise PreviewApprovalValidationError(
                "continued_without_preview requires an unavailable displayed state"
            )
        if delivery["reason_code"] is None:
            raise PreviewApprovalValidationError(
                "continued_without_preview requires a delivery reason_code"
            )

    if isinstance(selected, dict):
        expected_content = selected.get("content")
        expected_sha256 = (
            expected_content.get("sha256") if isinstance(expected_content, dict) else None
        )
        expected = {
            "renderer": selected.get("renderer"),
            "asset_id": selected.get("asset_id"),
            "asset_version": selected.get("asset_version"),
            "content_sha256": expected_sha256,
        }
        observed = {key: delivery.get(key) for key in expected}
        if observed != expected:
            raise PreviewApprovalValidationError(
                "delivery asset identity/hash does not match the selected registry asset"
            )
    else:
        if any(delivery.get(key) is not None for key in (
            "asset_id",
            "asset_version",
            "content_sha256",
        )):
            raise PreviewApprovalValidationError(
                "an unavailable manifest cannot report a selected asset"
            )
        if delivery.get("renderer") not in {None, "none"}:
            raise PreviewApprovalValidationError(
                "an unavailable manifest cannot report an asset renderer"
            )
        if delivery["status"] == "unavailable" and (
            delivery["reason_code"] != manifest.get("reason_code")
        ):
            raise PreviewApprovalValidationError(
                "unavailable delivery reason_code does not match server resolution"
            )


def _approval_fingerprint(
    job_id: str,
    manifest: dict[str, Any],
    decision: str,
    delivery: dict[str, Any],
) -> str:
    return _payload_sha256(
        {
            "job_id": job_id,
            "manifest_id": manifest["manifest_id"],
            "preview_manifest_sha256": manifest_sha256(manifest),
            "decision": decision,
            "delivery": delivery,
        }
    )


def _approval_id(fingerprint: str) -> str:
    return "preview-approval-" + fingerprint.removeprefix("sha256:")


def _approval_event(
    job_id: str,
    manifest: dict[str, Any],
    decision: str,
    delivery: dict[str, Any],
    fingerprint: str,
    append_index: int,
) -> dict[str, Any]:
    event = {
        "schema": PREVIEW_APPROVAL_SCHEMA,
        "approval_id": _approval_id(fingerprint),
        "append_index": append_index,
        "recorded_at_utc": _approval_utc_now(),
        "job_id": job_id,
        "decision": decision,
        "scope": PREVIEW_APPROVAL_SCOPE,
        "request_fingerprint": fingerprint,
        "preview_manifest": copy.deepcopy(manifest),
        "preview_manifest_sha256": manifest_sha256(manifest),
        "resolved_preview_status": manifest["preview_status"],
        "displayed_preview_status": delivery["displayed_status"],
        "delivery_observation": copy.deepcopy(delivery),
        "manufacturing_authority": False,
        "non_manufacturing_acknowledgement": NON_CERTIFYING_NOTE,
    }
    event["event_sha256"] = _payload_sha256(event)
    return event


_APPROVAL_EVENT_KEYS = {
    "schema",
    "approval_id",
    "append_index",
    "recorded_at_utc",
    "job_id",
    "decision",
    "scope",
    "request_fingerprint",
    "preview_manifest",
    "preview_manifest_sha256",
    "resolved_preview_status",
    "displayed_preview_status",
    "delivery_observation",
    "manufacturing_authority",
    "non_manufacturing_acknowledgement",
    "event_sha256",
}


def _validated_approval_event(
    payload: Any,
    *,
    job_id: str,
    filename: str,
    current_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _APPROVAL_EVENT_KEYS:
        raise PreviewApprovalConflictError("approval event has an invalid shape")
    if payload.get("schema") != PREVIEW_APPROVAL_SCHEMA:
        raise PreviewApprovalConflictError("approval event schema is unsupported")
    approval_id = payload.get("approval_id")
    if not isinstance(approval_id, str) or filename != f"{approval_id}.json":
        raise PreviewApprovalConflictError("approval filename and id differ")
    if payload.get("job_id") != job_id:
        raise PreviewApprovalConflictError("approval job_id differs from its run")
    if payload.get("scope") != PREVIEW_APPROVAL_SCOPE:
        raise PreviewApprovalConflictError("approval scope is not preview_only")
    append_index = payload.get("append_index")
    if (
        not isinstance(append_index, int)
        or isinstance(append_index, bool)
        or append_index < 1
    ):
        raise PreviewApprovalConflictError("approval append_index is invalid")
    event_without_hash = copy.deepcopy(payload)
    recorded_event_hash = event_without_hash.pop("event_sha256", None)
    try:
        expected_event_hash = _payload_sha256(event_without_hash)
    except PreviewApprovalValidationError as exc:
        raise PreviewApprovalConflictError("approval event is not finite JSON") from exc
    if recorded_event_hash != expected_event_hash:
        raise PreviewApprovalConflictError("approval event hash differs")
    recorded_at = payload.get("recorded_at_utc")
    if not isinstance(recorded_at, str):
        raise PreviewApprovalConflictError("approval timestamp is invalid")
    try:
        parsed_timestamp = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PreviewApprovalConflictError("approval timestamp is invalid") from exc
    if not recorded_at.endswith("Z") or parsed_timestamp.tzinfo != timezone.utc:
        raise PreviewApprovalConflictError("approval timestamp is invalid")
    if payload.get("manufacturing_authority") is not False:
        raise PreviewApprovalConflictError("approval claims manufacturing authority")
    if payload.get("non_manufacturing_acknowledgement") != NON_CERTIFYING_NOTE:
        raise PreviewApprovalConflictError(
            "approval non-manufacturing acknowledgement changed"
        )
    manifest = payload.get("preview_manifest")
    if not isinstance(manifest, dict):
        raise PreviewApprovalConflictError("approval lacks a PreviewManifest snapshot")
    try:
        expected_manifest_sha256 = manifest_sha256(manifest)
    except (KeyError, TypeError, ValueError) as exc:
        raise PreviewApprovalConflictError(
            "approval PreviewManifest snapshot is invalid"
        ) from exc
    if payload.get("preview_manifest_sha256") != expected_manifest_sha256:
        raise PreviewApprovalConflictError("approval PreviewManifest hash differs")
    if current_manifest is not None and not _strict_json_equal(manifest, current_manifest):
        raise PreviewApprovalConflictError(
            "approval PreviewManifest is not the current persisted manifest"
        )
    decision = payload.get("decision")
    delivery = payload.get("delivery_observation")
    try:
        normalized = _validated_approval_request(
            job_id,
            {
                "job_id": job_id,
                "manifest_id": manifest.get("manifest_id"),
                "decision": decision,
                "delivery": delivery,
            },
        )
        if not _strict_json_equal(normalized["delivery"], delivery):
            raise PreviewApprovalValidationError(
                "approval delivery observation is not normalized"
            )
        _validate_delivery_decision(decision, delivery, manifest)
    except PreviewApprovalValidationError as exc:
        raise PreviewApprovalConflictError(str(exc)) from exc
    if payload.get("resolved_preview_status") != manifest.get("preview_status"):
        raise PreviewApprovalConflictError("approval resolved status differs")
    if payload.get("displayed_preview_status") != delivery.get("displayed_status"):
        raise PreviewApprovalConflictError("approval displayed status differs")
    try:
        fingerprint = _approval_fingerprint(job_id, manifest, decision, delivery)
    except (KeyError, TypeError, ValueError) as exc:
        raise PreviewApprovalConflictError(
            "approval request fingerprint cannot be recomputed"
        ) from exc
    if payload.get("request_fingerprint") != fingerprint:
        raise PreviewApprovalConflictError("approval request fingerprint differs")
    if approval_id != _approval_id(fingerprint):
        raise PreviewApprovalConflictError("approval id is not deterministic")
    return copy.deepcopy(payload)


def _approval_history_head(
    job_id: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    if not history:
        raise PreviewApprovalValidationError(
            "an approval-history head requires at least one event"
        )
    latest = history[-1]
    chain = [
        {
            "append_index": event["append_index"],
            "approval_id": event["approval_id"],
            "event_sha256": event["event_sha256"],
        }
        for event in history
    ]
    return {
        "schema": PREVIEW_APPROVAL_HEAD_SCHEMA,
        "job_id": job_id,
        "count": len(history),
        "latest_approval_id": latest["approval_id"],
        "latest_event_sha256": latest["event_sha256"],
        "history_sha256": _payload_sha256(chain),
    }


def _approval_summary_locked(
    run_dir: Path,
    job_id: str,
    current_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    approvals_dir = run_dir / PREVIEW_APPROVALS_DIRNAME
    head_path = run_dir / PREVIEW_APPROVAL_HEAD_FILENAME
    approvals_present = approvals_dir.exists() or approvals_dir.is_symlink()
    head_present = head_path.exists() or head_path.is_symlink()
    if not approvals_present:
        corruption = (
            [
                {
                    "file": PREVIEW_APPROVAL_HEAD_FILENAME,
                    "reason": "approval-history head exists without approval storage",
                }
            ]
            if head_present
            else []
        )
        return {
            "status": "corrupt" if corruption else "not_recorded",
            "latest": None,
            "history": [],
            "count": 0,
            "corruption": corruption,
        }
    if not approvals_dir.is_dir() or approvals_dir.is_symlink():
        return {
            "status": "corrupt",
            "latest": None,
            "history": [],
            "count": 0,
            "corruption": [
                {
                    "file": PREVIEW_APPROVALS_DIRNAME,
                    "reason": "approval storage is not a real directory",
                }
            ],
        }

    history: list[dict[str, Any]] = []
    corruption: list[dict[str, str]] = []
    try:
        candidates = sorted(approvals_dir.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        return {
            "status": "corrupt",
            "latest": None,
            "history": [],
            "count": 0,
            "corruption": [
                {"file": PREVIEW_APPROVALS_DIRNAME, "reason": str(exc)}
            ],
        }
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_file() or candidate.suffix != ".json":
            corruption.append(
                {"file": candidate.name, "reason": "unexpected approval storage entry"}
            )
            continue
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            event = _validated_approval_event(
                raw,
                job_id=job_id,
                filename=candidate.name,
                current_manifest=current_manifest,
            )
        except (OSError, json.JSONDecodeError, PreviewApprovalConflictError) as exc:
            corruption.append({"file": candidate.name, "reason": str(exc)})
            continue
        history.append(event)
    history.sort(key=lambda event: event["append_index"])
    observed_indices = [event["append_index"] for event in history]
    if observed_indices != list(range(1, len(history) + 1)):
        corruption.append(
            {
                "file": PREVIEW_APPROVALS_DIRNAME,
                "reason": "approval append indices are duplicated or non-contiguous",
            }
        )
    if not head_present:
        if candidates:
            corruption.append(
                {
                    "file": PREVIEW_APPROVAL_HEAD_FILENAME,
                    "reason": "approval history is missing its durable head",
                }
            )
    elif head_path.is_symlink() or not head_path.is_file():
        corruption.append(
            {
                "file": PREVIEW_APPROVAL_HEAD_FILENAME,
                "reason": "approval-history head is not a real file",
            }
        )
    elif not history:
        if not corruption:
            corruption.append(
                {
                    "file": PREVIEW_APPROVAL_HEAD_FILENAME,
                    "reason": "approval-history head exists without a valid event",
                }
            )
    else:
        try:
            recorded_head = json.loads(head_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            corruption.append(
                {"file": PREVIEW_APPROVAL_HEAD_FILENAME, "reason": str(exc)}
            )
        else:
            expected_head = _approval_history_head(job_id, history)
            if not _strict_json_equal(recorded_head, expected_head):
                corruption.append(
                    {
                        "file": PREVIEW_APPROVAL_HEAD_FILENAME,
                        "reason": (
                            "approval-history head count, latest event, or digest differs"
                        ),
                    }
                )
    latest = copy.deepcopy(history[-1]) if history else None
    return {
        "status": "corrupt" if corruption else ("valid" if history else "not_recorded"),
        "latest": latest,
        "history": copy.deepcopy(history),
        "count": len(history),
        "corruption": corruption,
    }


def _approval_summary(
    run_dir: Path,
    job_id: str,
    current_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    with _PREVIEW_APPROVAL_LOCK:
        return _approval_summary_locked(run_dir, job_id, current_manifest)


def append_preview_approval(
    job_id: str,
    runs_dir: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Append one immutable preview-only decision, with deterministic retries."""

    request = _validated_approval_request(job_id, payload)
    with _PREVIEW_APPROVAL_LOCK:
        run_dir, _proposal, _flat, _canonical, manifest = (
            _load_authoritative_preview_state(job_id, runs_dir)
        )
        if request["manifest_id"] != manifest.get("manifest_id"):
            raise PreviewApprovalConflictError(
                "requested manifest_id is stale or does not belong to this run"
            )
        decision = request["decision"]
        delivery = request["delivery"]
        _validate_delivery_decision(decision, delivery, manifest)
        fingerprint = _approval_fingerprint(job_id, manifest, decision, delivery)
        approvals_dir = run_dir / PREVIEW_APPROVALS_DIRNAME
        if approvals_dir.is_symlink():
            raise PreviewApprovalConflictError(
                "preview approval directory cannot be a symbolic link"
            )
        if approvals_dir.exists() and not approvals_dir.is_dir():
            raise PreviewApprovalConflictError(
                "preview approval storage is not a directory"
            )
        approvals_dir.mkdir(parents=False, exist_ok=True)
        existing_summary = _approval_summary(run_dir, job_id, manifest)
        if existing_summary["status"] == "corrupt":
            raise PreviewApprovalConflictError(
                "preview approval history is corrupt and cannot accept decisions"
            )
        event = _approval_event(
            job_id,
            manifest,
            decision,
            delivery,
            fingerprint,
            len(existing_summary["history"]) + 1,
        )
        event_path = approvals_dir / f"{event['approval_id']}.json"
        outcome = "recorded"
        try:
            _write_json_exclusive(event_path, event)
        except FileExistsError:
            existing = _read_required_object(event_path, "preview approval event")
            existing = _validated_approval_event(
                existing,
                job_id=job_id,
                filename=event_path.name,
                current_manifest=manifest,
            )
            if existing.get("request_fingerprint") != fingerprint:
                raise PreviewApprovalConflictError(
                    "deterministic approval id collides with different content"
                )
            event = existing
            outcome = "idempotent"
        if outcome == "recorded":
            updated_history = [*existing_summary["history"], event]
            _write_json_durable(
                run_dir / PREVIEW_APPROVAL_HEAD_FILENAME,
                _approval_history_head(job_id, updated_history),
            )
        summary = _approval_summary(run_dir, job_id, manifest)
        if summary["status"] == "corrupt":
            raise PreviewApprovalConflictError(
                "preview approval history is corrupt and cannot accept decisions"
            )
    return {
        "status": outcome,
        "approval": event,
        "preview_approval": summary,
    }


_MODULAR_PREFLIGHT = (
    "The current C.3 Rhino proof does not establish support for Goal 0007 "
    "modular head, side-stone, shoulder, or pave fields. Fulfillment must "
    "bind and verify every requested canonical field before geometry is published."
)


def _operator_preflight(
    proposal: dict[str, Any],
    master_fulfillment: dict[str, Any] | None,
) -> str | None:
    """Tell the operator what this particular request still needs from them."""
    notes: list[str] = []
    if proposal.get("modular_contract"):
        notes.append(_MODULAR_PREFLIGHT)
    request = (master_fulfillment or {}).get("geometry_request")
    if isinstance(request, dict) and request.get("solve_status") == "not_solved":
        master = request.get("master") or {}
        notes.append(
            "This configuration is inside the envelope of parametric master "
            f"{master.get('template_id')} but has not been solved. The derived "
            "parameters are attached; solving needs explicit operator approval for "
            "paid Azure compute, a visual review of the result, and a passing "
            "promotion gate before it can preview. Deallocate the VM afterward."
        )
    return " ".join(notes) if notes else None


def request_geometry(job_id: str, runs_dir: Path) -> dict[str, Any]:
    """Queue an explicit geometry request for a persisted, accepted proposal.

    Idempotent: an existing request (any status) is returned unchanged so the
    app can never reset an operator-updated request back to ``requested``.
    """
    with _PREVIEW_APPROVAL_LOCK:
        run_dir = _safe_run_dir(runs_dir, str(job_id), require_exists=True)
        proposal = _read_json(run_dir / PROPOSAL_FILENAME)
        if not isinstance(proposal, dict):
            raise RunNotFoundError(f"no persisted proposal for job '{job_id}'")
        if proposal.get("status") != "accepted":
            raise PreviewApprovalConflictError(
                "persisted proposal is not accepted and cannot request geometry"
            )
        request_path = run_dir / REQUEST_FILENAME
        existing = _read_json(request_path)
        if isinstance(existing, dict):
            return existing
        if request_path.exists():
            raise PreviewApprovalConflictError(
                "geometry request exists but is not readable JSON"
            )
        latest_preview_decision = None
        try:
            _run_dir, _proposal, _flat, _canonical, authoritative_manifest = (
                _load_authoritative_preview_state(str(job_id), runs_dir)
            )
        except PreviewApprovalConflictError:
            # Legacy or corrupt preview state may still enter the explicit
            # operator queue, but it cannot authenticate a preview decision.
            pass
        else:
            preview_approval = _approval_summary(
                run_dir,
                str(job_id),
                authoritative_manifest,
            )
            if preview_approval["status"] == "valid":
                latest_preview_decision = preview_approval["latest"]
        # Goal 0016: a configuration the parametric master can build but nobody has
        # solved yet must leave here as a fulfillable work order, not just a wish.
        # The configurator already derived the master's parameters from the typed
        # request; carrying them through is what lets an operator solve it without
        # reconstructing the customer's intent.
        fulfillment = proposal.get("fulfillment")
        master_fulfillment = (
            copy.deepcopy(fulfillment) if isinstance(fulfillment, dict) else None
        )
        request = {
            "schema_version": 1,
            "job_id": str(job_id),
            "status": "requested",
            "requested_at_utc": _utc_now(),
            "required_geometry_contract": proposal.get("modular_contract"),
            "master_fulfillment": master_fulfillment,
            "preview_decision": (
                {
                    "approval_id": latest_preview_decision["approval_id"],
                    "decision": latest_preview_decision["decision"],
                    "recorded_at_utc": latest_preview_decision["recorded_at_utc"],
                    "manifest_id": latest_preview_decision["preview_manifest"]["manifest_id"],
                    "scope": PREVIEW_APPROVAL_SCOPE,
                    "manufacturing_authority": False,
                }
                if latest_preview_decision
                else None
            ),
            "operator_preflight": _operator_preflight(proposal, master_fulfillment),
            "note": (
                "Paid Rhino.Compute geometry requires explicit operator approval; "
                "the app never triggers it. " + NON_CERTIFYING_NOTE
            ),
        }
        try:
            _write_json_exclusive(request_path, request)
        except FileExistsError:
            concurrent = _read_json(request_path)
            if not isinstance(concurrent, dict):
                raise PreviewApprovalConflictError(
                    "geometry request was concurrently created but is unreadable"
                )
            return concurrent
        return request


def geometry_delivery_decision(
    job_id: str, runs_dir: Path
) -> tuple[Any, dict[str, Any]]:
    """Decide whether a persisted run's geometry may be handed over.

    Goal 0025. The decision is made from the **durably persisted** PreviewManifest
    and a freshly re-derived quote, never from anything a caller sends: the same
    reason ``persist_proposal`` recomputes both. A caller who could nominate the
    manifest could nominate which ring's STL they receive.

    Returns ``(decision, quote)``; the quote travels into the archive so the file
    arrives with the grams it takes.
    """
    run_dir = _safe_run_dir(runs_dir, str(job_id), require_exists=True)
    proposal = _read_json(run_dir / PROPOSAL_FILENAME)
    if not isinstance(proposal, dict):
        raise RunNotFoundError(f"no persisted proposal for job '{job_id}'")
    manifest = _read_json(run_dir / PREVIEW_MANIFEST_FILENAME)
    if not isinstance(manifest, dict):
        raise RunNotFoundError(f"no persisted PreviewManifest for job '{job_id}'")

    # Re-derive rather than reading the stored quote: the volume table may have
    # been regenerated since this run was persisted, and a delivery must carry
    # today's measurement, not the one that happened to be current in August.
    refreshed = copy.deepcopy(proposal)
    refreshed["preview_manifest"] = manifest
    reconcile_quote(refreshed)
    quote = refreshed.get("quote") if isinstance(refreshed.get("quote"), dict) else {}
    decision = geometry_delivery.decide(manifest, quote=quote)
    return decision, quote


def deliver_geometry(job_id: str, runs_dir: Path) -> dict[str, Any]:
    """The delivery decision for a persisted run, as the API reports it."""
    decision, _quote = geometry_delivery_decision(job_id, runs_dir)
    payload = decision.to_json()
    payload["job_id"] = str(job_id)
    if decision.deliverable:
        payload["archive_url"] = f"/api/deliverable/{job_id}.zip"
        payload["archive_name"] = geometry_delivery.archive_name(decision)
    return payload


def deliver_geometry_archive(job_id: str, runs_dir: Path) -> tuple[str, bytes, str]:
    """Build the archive for a persisted run. Returns ``(name, bytes, sha256)``.

    The decision is taken again here rather than trusted from a previous call: a
    download must not be authorised by an earlier request's answer.
    """
    decision, quote = geometry_delivery_decision(job_id, runs_dir)
    if not decision.deliverable:
        raise GeometryDeliveryRefused(decision)
    blob, digest = geometry_delivery.build_archive(
        decision, quote=quote, job_id=str(job_id)
    )
    return geometry_delivery.archive_name(decision), blob, digest


def _review_preview_state(
    run_dir: Path,
    job_id: str,
    proposal: dict[str, Any],
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    manifest_path = run_dir / PREVIEW_MANIFEST_FILENAME
    embedded_manifest = proposal.get("preview_manifest")
    if not manifest_path.exists() and embedded_manifest is None:
        return (
            None,
            "legacy_unresolved",
            {
                "status": "not_recorded",
                "latest": None,
                "history": [],
                "count": 0,
                "corruption": [],
            },
        )

    problems: list[dict[str, str]] = []
    manifest: dict[str, Any] | None = None
    if not manifest_path.is_file() or manifest_path.is_symlink():
        problems.append(
            {
                "file": PREVIEW_MANIFEST_FILENAME,
                "reason": "durable PreviewManifest is missing or unsafe",
            }
        )
    else:
        try:
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(raw_manifest, dict):
                raise ValueError("PreviewManifest is not an object")
            manifest = raw_manifest
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            problems.append(
                {"file": PREVIEW_MANIFEST_FILENAME, "reason": str(exc)}
            )

    if manifest is not None:
        if not _strict_json_equal(embedded_manifest, manifest):
            problems.append(
                {
                    "file": PROPOSAL_FILENAME,
                    "reason": "embedded and durable PreviewManifest differ",
                }
            )
        preview = proposal.get("preview")
        if isinstance(preview, dict) and not _strict_json_equal(
            preview.get("preview_manifest"), manifest
        ):
            problems.append(
                {
                    "file": PROPOSAL_FILENAME,
                    "reason": "preview and durable PreviewManifest differ",
                }
            )
        try:
            flat = _read_required_object(run_dir / PARAMETERS_FILENAME, "flat parameters")
            canonical = _read_required_object(
                run_dir / CANONICAL_FILENAME,
                "canonical spec",
            )
            if not _strict_json_equal(proposal.get("flat_parameters"), flat):
                raise PreviewApprovalConflictError(
                    "proposal and parameters.json differ"
                )
            if not _strict_json_equal(proposal.get("canonical_spec"), canonical):
                raise PreviewApprovalConflictError(
                    "proposal and canonical_spec.json differ"
                )
            if not _strict_json_equal(resolve_preview(canonical, flat), manifest):
                raise PreviewApprovalConflictError(
                    "PreviewManifest is stale, tampered, or no longer registry-resolvable"
                )
        except (PreviewApprovalConflictError, TypeError, ValueError) as exc:
            problems.append(
                {"file": PREVIEW_MANIFEST_FILENAME, "reason": str(exc)}
            )

    approval = _approval_summary(
        run_dir,
        job_id,
        manifest if manifest is not None and not problems else None,
    )
    if problems:
        approval = copy.deepcopy(approval)
        approval["status"] = "corrupt"
        approval["corruption"] = problems + list(approval.get("corruption") or [])
    return manifest, ("corrupt" if problems else "resolved"), approval


def _review_delivery_state(
    manifest: Any, proposal: dict[str, Any]
) -> dict[str, Any]:
    """A compact delivery answer for the review listing.

    The quote is **re-derived**, not read from the persisted proposal, because
    ``geometry_delivery_decision`` re-derives it too. A run persisted before its
    asset entered the measured-volume table carries a stored quote with no weights;
    reading that would report ``no_metal_weight_table`` here while the endpoint
    happily delivers -- wrong for exactly the runs where the table moved forward.

    A malformed run must not take the whole review page down, so an unexpected
    failure is reported as a refusal rather than raised.
    """
    try:
        refreshed = copy.deepcopy(proposal)
        refreshed["preview_manifest"] = manifest
        reconcile_quote(refreshed)
        quote = refreshed.get("quote") if isinstance(refreshed.get("quote"), dict) else {}
        decision = geometry_delivery.decide(manifest, quote=quote, verify=False)
    except Exception as exc:  # noqa: BLE001 - a listing surface, never a gate
        return {
            "deliverable": False,
            "reason": "delivery_check_failed",
            "detail": f"{exc.__class__.__name__}: {exc}",
            "certifies_manufacturing": False,
        }
    payload = decision.to_json()
    return {
        "deliverable": payload["deliverable"],
        "reason": payload["reason"],
        "detail": payload["detail"],
        "byte_verification": payload["byte_verification"],
        "findings": payload["findings"],
        "warnings": payload["warnings"],
        "mesh_policy_declared": bool(
            (payload["mesh_policy"] or {}).get("declared")
        ),
        "certifies_manufacturing": False,
    }


def review_index(runs_dir: Path) -> dict[str, Any]:
    """Build the review listing across all persisted runs."""
    runs_dir = Path(runs_dir)
    resolved_runs_dir = runs_dir.resolve()
    entries: list[dict[str, Any]] = []
    if runs_dir.is_dir():
        for run_dir in sorted(runs_dir.iterdir()):
            if (
                not run_dir.is_dir()
                or run_dir.is_symlink()
                or run_dir.resolve().parent != resolved_runs_dir
            ):
                continue
            proposal = _read_json(run_dir / PROPOSAL_FILENAME)
            if not isinstance(proposal, dict):
                continue
            flat_payload = proposal.get("flat_parameters")
            flat = flat_payload if isinstance(flat_payload, dict) else {}
            preview_manifest, preview_resolution_state, preview_approval = (
                _review_preview_state(run_dir, run_dir.name, proposal)
            )
            if not isinstance(flat_payload, dict):
                preview_resolution_state = "corrupt"
                preview_approval = copy.deepcopy(preview_approval)
                preview_approval["status"] = "corrupt"
                preview_approval["corruption"] = [
                    {
                        "file": PROPOSAL_FILENAME,
                        "reason": "embedded flat_parameters is not an object",
                    },
                    *list(preview_approval.get("corruption") or []),
                ]
            request = _read_json(run_dir / REQUEST_FILENAME)
            evidence = _read_json(
                run_dir / EVIDENCE_DIRNAME / EVIDENCE_SUMMARY_FILENAME
            )
            artifacts = {}
            if isinstance(evidence, dict):
                for label, name in (evidence.get("artifacts") or {}).items():
                    candidate = run_dir / EVIDENCE_DIRNAME / str(name)
                    if candidate.is_file():
                        artifacts[label] = (
                            f"/runs/{run_dir.name}/{EVIDENCE_DIRNAME}/{name}"
                        )
            query_images = [
                f"/runs/{run_dir.name}/{QUERY_DIRNAME}/{name}"
                for name in (proposal.get("query_images") or [])
                if (run_dir / QUERY_DIRNAME / str(name)).is_file()
            ]
            entries.append(
                {
                    "job_id": run_dir.name,
                    "lane": proposal.get("lane") or "typed",
                    "query_images": query_images,
                    # Image-lane evidence: what was matched, how confidently,
                    # and — when nothing was emitted — why it abstained.
                    "image_lane": (
                        {
                            "top_k": proposal.get("top_k"),
                            "attributes": proposal.get("attributes"),
                            "attribute_sources": proposal.get("attribute_sources"),
                            "signals": proposal.get("signals"),
                            "abstention_reason": proposal.get("abstention_reason"),
                            "embedding": proposal.get("embedding"),
                            "policy": proposal.get("policy"),
                            # The contract, so a reviewer can see which axes the
                            # lane inferred and which the customer typed.
                            "inferred_axes": proposal.get("inferred_axes"),
                            "typed_only_axes": proposal.get("typed_only_axes"),
                            "catalog_scope": proposal.get("catalog_scope"),
                        }
                        if proposal.get("lane") == "image"
                        else None
                    ),
                    # Typed metal and the quote it feeds — same for both lanes.
                    "typed_inputs": proposal.get("typed_inputs"),
                    "quote": proposal.get("quote"),
                    "created_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(run_dir.stat().st_mtime),
                    ),
                    "provider": proposal.get("provider"),
                    "rationale": proposal.get("rationale"),
                    "catalog_revision": proposal.get("catalog_revision"),
                    "geometry_identity": proposal.get("geometry_identity"),
                    "preview_manifest": preview_manifest,
                    "preview_resolution_state": preview_resolution_state,
                    "preview_approval": preview_approval,
                    "selection": proposal.get("selection"),
                    "modular_contract": proposal.get("modular_contract"),
                    "validation": proposal.get("validation"),
                    "config_summary": {
                        "archetype": flat.get("archetype"),
                        "stone_shape": flat.get("stone_shape"),
                        "stone_carat_eq": flat.get("stone_carat_eq"),
                        "center_setting": flat.get("center_setting"),
                        "prong_count": flat.get("prong_count"),
                        "metal_alloy": flat.get("metal_alloy"),
                        "band_width_mm": flat.get("band_width_mm"),
                        "band_thickness_mm": flat.get("band_thickness_mm"),
                        "ring_size_us": flat.get("ring_size_us"),
                        "head_style_id": flat.get("head_style_id"),
                        "band_style_id": flat.get("band_style_id"),
                        "band_profile": flat.get("band_profile"),
                        "band_profile_code": flat.get("band_profile_code"),
                        "shoulder_style": flat.get("shoulder_style"),
                        "head_family": flat.get("head_family"),
                        "head_geometry": flat.get("head_geometry"),
                        "gallery_structure": flat.get("gallery_structure"),
                        "side_stone_layout": flat.get("side_stone_layout"),
                        "pave": flat.get("pave"),
                        "stone_schedules": flat.get("stone_schedules"),
                        "metal_finish": flat.get("metal_finish"),
                    },
                    "geometry_gates": flat.get("geometry_gates"),
                    # Goal 0026. If perception contributed to this run, the
                    # permission that allowed it travels with the run — a reviewer
                    # must be able to see that the mounting came from a lane whose
                    # gates all read not_measurable, without leaving /review.
                    "perception": proposal.get("perception"),
                    # Goal 0025. Whether this run's geometry could be handed over,
                    # for a reviewer scanning the queue. Byte verification is
                    # deferred to the download rather than re-hashing every
                    # bundle for every listed run; the archive re-hashes anyway.
                    "geometry_delivery": _review_delivery_state(
                        preview_manifest, proposal
                    ),
                    "geometry_request": request,
                    "evidence": evidence,
                    "evidence_artifacts": artifacts,
                }
            )
    entries.sort(key=lambda item: item["created_at"], reverse=True)
    return {
        "runs": entries,
        "non_certifying_note": NON_CERTIFYING_NOTE,
    }
