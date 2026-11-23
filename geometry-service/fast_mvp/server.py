"""Local server for the MyJewl fast MVP."""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import os
import sys
from decimal import Decimal, InvalidOperation, Overflow as DecimalOverflow
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_mvp import alloy_prices, geometry_delivery, perception_lane  # noqa: E402

# Goal 0026. ``catalog_index``, ``embedding`` and ``image_lane`` are imported
# lazily, inside the functions that need them, so the quarantined retrieval stack
# is not merely unreachable but unloaded on a deployment that has not
# acknowledged it. The measured import saving is small (~5 ms of ~498 ms: numpy
# and trimesh arrive anyway through ``mesh`` and ``stl_gates``), so speed is not
# the argument — not executing paused code is. ``pipeline`` and ``proposer`` stay
# eager: they serve /api/extract and /api/propose, which are not Track C.
from fast_mvp.manual_configurator import (  # noqa: E402
    compatible_options as manual_compatible_options,
    configure as configure_manual,
)
from fast_mvp.mesh import generate_draft_assets  # noqa: E402
from fast_mvp.pipeline import (  # noqa: E402
    analyze_photo_request,
    decode_image_data_url,
    request_path_disclosure,
)
from fast_mvp.proposer import propose_config  # noqa: E402
from fast_mvp.workflow import (  # noqa: E402
    PreviewApprovalConflictError,
    PreviewApprovalValidationError,
    GeometryDeliveryRefused,
    RunNotFoundError,
    append_preview_approval,
    deliver_geometry,
    deliver_geometry_archive,
    persist_image_proposal,
    persist_proposal,
    request_geometry,
    review_index,
)
from jewelry_gaas_foundation.geometry_adapter import RhinoComputeHttpAdapter  # noqa: E402


STATIC_DIR = Path(__file__).resolve().parent / "static"
RUNS_DIR = Path(__file__).resolve().parent / "runs"

# Lane B is quarantined (Goal 0026). An index alone no longer opens it: an
# operator must also declare a named, dated acknowledgement that every Goal 0005
# gate reads not_measurable. ``perception_lane`` owns that decision and every
# refusal slug; these names are re-exported so existing callers keep working.
IMAGE_INDEX_ENV = perception_lane.INDEX_DIR_ENV
IMAGE_BACKEND_ENV = perception_lane.BACKEND_ENV
_IMAGE_LANE_CACHE: dict[str, Any] = {}
_MAX_SAFE_JSON_INTEGER = 2**53 - 1


def _pricing_state() -> dict[str, Any]:
    """Goal 0024. Whether this deployment can price metal content, and why not.

    Reported on ``/api/health`` so the answer is discoverable without first
    building a configuration. The shipped state is an undeclared sheet that prices
    nothing, and it says so rather than looking merely empty.
    """
    sheet = alloy_prices.active_sheet()
    if sheet is None:
        return {
            "alloy_price_sheet": None,
            "status": "no_price_sheet",
            "priceable_alloys": [],
            "detail": (
                "No alloy price sheet is loaded. Every quote reports "
                "metal_content_cost: null. No price is invented."
            ),
            "sheet_path_env": alloy_prices.SHEET_PATH_ENV,
        }
    usable = sorted(sheet.usable_prices())
    return {
        "alloy_price_sheet": sheet.label,
        "status": sheet.status,
        "currency": sheet.currency,
        "expires_on": sheet.expires_on,
        "expired": sheet.expired_on(),
        "priceable_alloys": usable,
        "detail": (
            f"{len(usable)} alloy(s) can be priced."
            if usable
            else "This sheet prices nothing; every quote reports "
            "metal_content_cost: null with a reason. No price is invented."
        ),
        "sheet_path_env": alloy_prices.SHEET_PATH_ENV,
        "certifies_manufacturing": False,
        "affects_geometry": False,
    }


def _lossless_request_json(value: Any, path: str = "$") -> Any:
    """Convert parsed Decimal tokens without silently rounding client geometry."""

    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"Request number at {path} must be finite.")
        if value == 0:
            return 0
        # Reject hostile exponents before any integer conversion can
        # materialize an enormous value (for example JSON ``1e1000000``).
        if value.adjusted() > 308:
            raise ValueError(
                f"Request number at {path} is outside the supported JSON range."
            )
        # Decimal.normalize() is context-sensitive and can round a long JSON
        # lexeme before this boundary checks it. Compare the original token;
        # Decimal equality already treats insignificant trailing zeroes as equal.
        if value == value.to_integral_value():
            if abs(value) > _MAX_SAFE_JSON_INTEGER:
                raise ValueError(
                    f"Request integer at {path} exceeds the exact JSON range."
                )
            return int(value)
        try:
            binary64 = float(value)
        except (OverflowError, ValueError) as exc:
            raise ValueError(
                f"Request number at {path} is not losslessly representable."
            ) from exc
        if not math.isfinite(binary64) or Decimal(str(binary64)) != value:
            raise ValueError(
                f"Request number at {path} is not losslessly representable."
            )
        return binary64
    if isinstance(value, list):
        return [
            _lossless_request_json(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        return {
            key: _lossless_request_json(item, f"{path}.{key}")
            for key, item in value.items()
        }
    return value


def _parse_request_json(raw: bytes) -> dict[str, Any]:
    def reject_constant(token: str) -> None:
        raise ValueError(f"Request number {token} must be finite.")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        InvalidOperation,
        DecimalOverflow,
    ) as exc:
        raise ValueError("Request body must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Request body must be a JSON object.")
    return _lossless_request_json(parsed)


def _finalize_extraction_report(
    report: dict[str, Any],
    runs_dir: Path,
) -> dict[str, Any]:
    """Attach/persist preview authority for the legacy extraction endpoint."""

    validation = report.get("validation") or {}
    accepted = (
        validation.get("schema_accepted") is True
        and validation.get("constraint_accepted") is True
    )
    report["status"] = "accepted" if accepted else "rejected"
    report["lane"] = "typed"
    report["provider"] = (report.get("extraction") or {}).get("provider")
    report["rationale"] = (
        "Typed stone, size, and metal with mounting-only extraction; "
        "production preview resolution remains server-authoritative."
    )
    if accepted:
        report["run"] = persist_proposal(report, runs_dir)
    return report


def _image_lane_resources() -> tuple[Any, Any] | None:
    """Load (and cache) the catalog index + embedder, or None if unconfigured."""
    index_dir = os.environ.get(IMAGE_INDEX_ENV, "").strip()
    if not index_dir:
        return None
    cached = _IMAGE_LANE_CACHE.get(index_dir)
    if cached is None:
        from fast_mvp.catalog_index import CatalogIndex
        from fast_mvp.embedding import build_embedder

        index = CatalogIndex.load(Path(index_dir))
        embedder = build_embedder(os.environ.get(IMAGE_BACKEND_ENV, "auto"))
        cached = (index, embedder)
        _IMAGE_LANE_CACHE[index_dir] = cached
    return cached


def _image_lane_index() -> Any:
    """The catalog index alone, for the availability decision.

    Separate from :func:`_image_lane_resources` because the decision needs only
    the index's provenance (which backend embedded it, how many configs it holds),
    and because the loading cost is paid once and cached either way.
    """
    resources = _image_lane_resources()
    return None if resources is None else resources[0]


def _decode_query_images(payload: dict[str, Any]) -> list[bytes]:
    """Accept a single data URL or a list of them."""
    raw = payload.get("image_data_urls")
    values = raw if isinstance(raw, list) else [payload.get("image_data_url")]
    images: list[bytes] = []
    for value in values:
        decoded = decode_image_data_url(value) if isinstance(value, str) else None
        if decoded:
            images.append(decoded)
    if not images:
        raise ValueError("image_data_url (or image_data_urls) is required.")
    return images


class MyJewlFastMvpHandler(BaseHTTPRequestHandler):
    server_version = "MyJewlFastMVP/0.1"

    def do_GET(self) -> None:  # noqa: N802
        # do_POST has always had this; do_GET did not, so an unexpected error --
        # a bundle file vanishing between verification and read, a permissions
        # change, a network mount -- aborted the connection with no response at
        # all. A delivery reads several megabytes off disk, which makes that a
        # real path rather than a theoretical one.
        try:
            self._route_get(urlparse(self.path))
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001 - local demo boundary
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"{exc.__class__.__name__}: {exc}",
            )

    def _route_get(self, parsed: Any) -> None:
        if parsed.path in {"/", "/index.html"}:
            self._send_file(STATIC_DIR / "index.html")
            return
        if parsed.path in {"/studio", "/studio.html"}:
            self._send_file(STATIC_DIR / "studio.html")
            return
        if parsed.path in {"/configure", "/configure.html"}:
            self._send_file(STATIC_DIR / "manual.html")
            return
        if parsed.path.startswith("/static/"):
            self._send_file(STATIC_DIR / unquote(parsed.path.removeprefix("/static/")))
            return
        if parsed.path.startswith("/runs/"):
            self._send_file(RUNS_DIR / unquote(parsed.path.removeprefix("/runs/")))
            return
        if parsed.path.startswith("/api/deliverable/") and parsed.path.endswith(".zip"):
            job_id = unquote(
                parsed.path.removeprefix("/api/deliverable/").removesuffix(".zip")
            )
            self._send_delivery_archive(job_id)
            return
        if parsed.path == "/api/health":
            self._send_json(
                {
                    "status": "ok",
                    "mode": "fast_mvp",
                    "pricing": _pricing_state(),
                    "delivery": geometry_delivery.delivery_state(),
                    "perception": {
                        **perception_lane.lane_state(
                            index_loader=_image_lane_index
                        ),
                        # Goal 0027 / F12. The operator fields above answer "can
                        # this deployment serve perception"; this answers "will
                        # the next photo request read the photo", which is the
                        # only question the /studio copy may be built on. They
                        # disagree on an acknowledged deployment with no index
                        # loader wired into the request path, and the customer
                        # must be told the narrower truth.
                        "customer_disclosure": request_path_disclosure(),
                    },
                }
            )
            return
        if parsed.path in {"/review", "/review.html"}:
            self._send_file(STATIC_DIR / "review.html")
            return
        if parsed.path == "/api/review":
            self._send_json(review_index(RUNS_DIR))
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/extract":
                payload = self._read_json()
                self._send_json(
                    _finalize_extraction_report(
                        analyze_photo_request(payload),
                        RUNS_DIR,
                    )
                )
                return
            if parsed.path == "/api/propose":
                payload = self._read_json()
                report = propose_config(
                    payload,
                    allow_llm=not bool(payload.get("offline")),
                )
                if report["status"] == "accepted":
                    report["run"] = persist_proposal(report, RUNS_DIR)
                self._send_json(report)
                return
            if parsed.path == "/api/manual-options":
                self._send_json(manual_compatible_options(self._read_json()))
                return
            if parsed.path == "/api/manual-configure":
                report = configure_manual(self._read_json())
                if report["status"] == "accepted":
                    report["run"] = persist_proposal(report, RUNS_DIR)
                self._send_json(report)
                return
            if parsed.path == "/api/propose-from-image":
                payload = self._read_json()
                # Goal 0026. Permission is decided before an index is touched, so
                # an unacknowledged deployment never loads one, and the refusal
                # reads the same on every machine. Whether we MAY answer must not
                # depend on which laptop has an index directory on disk.
                decision = perception_lane.decide(index_loader=_image_lane_index)
                if not decision.available:
                    self._send_json(
                        decision.to_json(),
                        status=HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return
                from fast_mvp.image_lane import propose_from_image

                index, embedder = _image_lane_resources()
                images = _decode_query_images(payload)
                report = propose_from_image(
                    payload,
                    index=index,
                    embedder=embedder,
                    images=images,
                    archetype_hint=payload.get("archetype_hint"),
                )
                # The permission travels with the answer. An acknowledgement buys
                # labelled diagnostic output, so every served proposal carries the
                # conditions that were accepted to serve it and the gates that are
                # still not_measurable — before it can be persisted or reviewed.
                report["perception"] = decision.to_json()
                # Only an accepted proposal is persisted. An abstention is a
                # product outcome, not a config — nothing reaches the queue.
                if report["status"] == "accepted":
                    report["run"] = persist_image_proposal(report, RUNS_DIR, images)
                self._send_json(report)
                return
            if parsed.path == "/api/request-geometry":
                payload = self._read_json()
                job_id = str(payload.get("job_id") or "").strip()
                if not job_id:
                    raise ValueError("job_id is required.")
                try:
                    self._send_json(request_geometry(job_id, RUNS_DIR))
                except FileNotFoundError as exc:
                    self._send_error(HTTPStatus.NOT_FOUND, str(exc))
                return
            if parsed.path == "/api/deliver-geometry":
                # Goal 0025. Never accepts a manifest, a bundle path, or a file
                # name — only a job id. Everything else is re-derived from the
                # durably persisted, server-authoritative state.
                payload = self._read_json()
                job_id = str(payload.get("job_id") or "").strip()
                if not job_id:
                    raise ValueError("job_id is required.")
                self._send_json(deliver_geometry(job_id, RUNS_DIR))
                return
            if parsed.path == "/api/preview-approval":
                payload = self._read_json()
                job_id = payload.get("job_id")
                if not isinstance(job_id, str) or not job_id:
                    raise PreviewApprovalValidationError("job_id is required.")
                self._send_json(
                    append_preview_approval(job_id, RUNS_DIR, payload)
                )
                return
            if parsed.path == "/api/generate-stl":
                payload = self._read_json()
                spec = payload.get("canonical_spec")
                if not isinstance(spec, dict):
                    raise ValueError("canonical_spec is required.")
                assets = generate_draft_assets(spec, RUNS_DIR)
                self._send_json(_asset_urls(assets))
                return
            if parsed.path == "/api/rhino-handoff":
                payload = self._read_json()
                spec = payload.get("canonical_spec")
                if not isinstance(spec, dict):
                    raise ValueError("canonical_spec is required.")
                assets = generate_draft_assets(spec, RUNS_DIR)
                self._send_json(_with_optional_rhino_result(spec, _asset_urls(assets)))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")
        except RunNotFoundError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, str(exc))
        except PreviewApprovalConflictError as exc:
            self._send_error(HTTPStatus.CONFLICT, str(exc))
        except PreviewApprovalValidationError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001 - local demo boundary
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{exc.__class__.__name__}: {exc}")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        sys.stderr.write("fast_mvp: " + format % args + "\n")

    def _send_delivery_archive(self, job_id: str) -> None:
        """Stream the receipt-verified export archive, or refuse in the same shape.

        A refusal here returns the decision JSON rather than a bare 404: "no" is a
        product answer with a reason, and it is the answer this repository ships.
        """
        try:
            name, blob, digest = deliver_geometry_archive(job_id, RUNS_DIR)
        except GeometryDeliveryRefused as exc:
            payload = exc.decision.to_json()
            payload["job_id"] = job_id
            self._send_json(payload, status=HTTPStatus.CONFLICT)
            return
        except RunNotFoundError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, str(exc))
            return
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "application/zip")
        self.send_header("content-length", str(len(blob)))
        self.send_header("content-disposition", f'attachment; filename="{name}"')
        # The archive is deterministic, so this hash identifies exactly these
        # bytes and a recipient can quote it back to us.
        self.send_header("x-myjewl-delivery-sha256", digest)
        self.send_header("x-myjewl-certifies-manufacturing", "false")
        self.end_headers()
        self.wfile.write(blob)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return _parse_request_json(raw)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        resolved = path.resolve()
        allowed_roots = (STATIC_DIR.resolve(), RUNS_DIR.resolve())
        if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
            self._send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not resolved.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        mime_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        body = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", mime_type)
        self.send_header("content-length", str(len(body)))
        if resolved.suffix in {".stl", ".json"} and RUNS_DIR.resolve() in resolved.parents:
            self.send_header("content-disposition", f'attachment; filename="{resolved.name}"')
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message, "status": status.value}, status=status)


def _asset_urls(assets: dict[str, Any]) -> dict[str, Any]:
    job_id = assets["job_id"]
    return {
        **assets,
        # Goal 0025. draft_preview.stl is built by trimesh in fast_mvp/mesh.py. It
        # is a selection-time operational aid with no receipt and no Rhino
        # provenance, and it must never be mistaken for the thing to cast. Now that
        # a real delivery path exists, saying so is not optional decoration — it is
        # the difference between the two files a customer can end up holding.
        "deliverable": False,
        "authority": "procedural_operational_aid_not_manufacturable",
        "delivery_note": (
            "draft_preview.stl is a fast procedural approximation, not Rhino "
            "geometry and not a deliverable. Manufacturable geometry is served "
            "only by /api/deliver-geometry, from receipt-verified Rhino exports."
        ),
        "delivery_endpoint": "/api/deliver-geometry",
        "urls": {
            "canonical_spec": f"/runs/{job_id}/canonical_spec.json",
            "draft_stl": f"/runs/{job_id}/draft_preview.stl",
            "rhino_handoff": f"/runs/{job_id}/rhino_handoff.json",
        },
    }


def _with_optional_rhino_result(spec: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    base_url = os.environ.get("RHINO_COMPUTE_URL", "")
    api_key = os.environ.get("RHINO_COMPUTE_API_KEY", "")
    definition_id = os.environ.get("RHINO_DEFINITION_ID", "")
    if not (base_url and api_key and definition_id):
        return {
            **response,
            "rhino_compute": {
                "status": "handoff_ready",
                "reason": "Set RHINO_COMPUTE_URL, RHINO_COMPUTE_API_KEY, and RHINO_DEFINITION_ID to POST live.",
            },
        }

    adapter = RhinoComputeHttpAdapter(
        base_url=base_url,
        api_key=api_key,
        definition_id=definition_id,
        timeout_seconds=45.0,
    )
    result = adapter.evaluate_geometry(
        spec,
        (
            "minimum_structural_floors",
            "stone_stone_collision",
            "stone_metal_collision",
            "mountability_and_setting_access",
            "mesh_closedness_and_manifold_suitability",
        ),
        ("STL",),
    )
    return {
        **response,
        "rhino_compute": {
            "status": result.execution_status,
            "reason_code": result.reason_code,
            "assets": [asset.__dict__ for asset in result.assets],
            "geometry_gates": [gate.__dict__ for gate in result.geometry_gates],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the MyJewl fast MVP server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    # Goal 0025. A shop profile is what authorises a delivery. Installing it here,
    # once, keeps the environment read in one visible place — note that the same
    # active profile also feeds constraints.py thresholds by design (Goal 0013),
    # which is why an unloadable profile is fatal rather than a printed warning:
    # starting anyway would silently drop calibrated BAND/METAL thresholds back to
    # the uncalibrated baseline for every later validation, announced only in
    # stdout that nobody is watching.
    try:
        label = geometry_delivery.install_profile_from_env()
    except Exception as exc:  # noqa: BLE001 - report the cause, then refuse to start
        print(
            f"shop profile from {geometry_delivery.SHOP_PROFILE_ENV} could not be "
            f"loaded: {exc.__class__.__name__}: {exc}\n"
            "Refusing to start: a configured profile that does not load would change "
            "constraint thresholds as well as delivery.",
            file=sys.stderr,
        )
        return 2
    if label:
        print(f"shop profile loaded: {label}")

    server = ThreadingHTTPServer((args.host, args.port), MyJewlFastMvpHandler)
    print(f"MyJewl fast MVP running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping MyJewl fast MVP.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
