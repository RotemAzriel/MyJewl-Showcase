"""Aureon ring builder — local demo server.

A thin, stdlib-only web app over the RhinoArtisan builder, driven by a guided funnel
(ring type -> style -> one question per screen -> Review -> Reveal; see ``demo/funnel.py``):

    GET  /api/styles   ring types, style presets + thumbnails, shapes, metals, bands,
                       Fine-tune choices (melee, pave coverage), defaults
    POST /api/plan     funnel state (or the legacy flat form) -> validate + compile,
                       no Rhino; plain-language ``issues`` tagged by step
    POST /api/build    build in Rhino via the RhinoArtisan MCP (~3-5 s), from Review only
    GET  /files/...    GLB for the 3D reveal, STL/3DM/manifest downloads
    GET  /static/...   page assets (viewer modules, style thumbnails)

Run (Rhino + RhinoArtisan open, MCP on 127.0.0.1:9280):

    python3 demo/server.py            # then open http://127.0.0.1:8765

Local only: it binds to 127.0.0.1, runs one build at a time (Rhino is single
threaded), and each build opens a new Rhino document (close them without saving).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import time
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jewelry_gaas_foundation.artisan_executor import (  # noqa: E402
    DEFAULT_MCP_URL,
    ArtisanClient,
    ArtisanExecutor,
    ArtisanMcpClient,
)
from jewelry_gaas_foundation.artisan_plan import (  # noqa: E402
    HEAD_STYLES,
    PAVILION_FRACTION,
    SHANK_STYLES,
    SpecRejectedError,
    UnsupportedSpecError,
    compile_solitaire_plan,
)
from jewelry_gaas_foundation.constraints import _derive_carat  # noqa: E402
from jewelry_gaas_foundation.constraints import validate as validate_constraints  # noqa: E402

from demo import funnel  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
STYLES_DIR = STATIC_DIR / "styles"

# Alloys the page offers -> canonical metal block (artisan_metal maps these).
ALLOYS: dict[str, dict[str, str]] = {
    "14k yellow gold": {"type": "gold", "alloy": "14k yellow gold"},
    "14k white gold": {"type": "gold", "alloy": "14k white gold"},
    "14k rose gold": {"type": "gold", "alloy": "14k rose gold"},
    "18k yellow gold": {"type": "gold", "alloy": "18k yellow gold"},
    "18k white gold": {"type": "gold", "alloy": "18k white gold"},
    "18k rose gold": {"type": "gold", "alloy": "18k rose gold"},
    "platinum 950": {"type": "platinum", "alloy": "pt950"},
}

DEFAULT_FORM: dict[str, Any] = {
    "shape": "round",
    "length": 6.5,
    "width": 6.5,
    "depth": 3.9,
    "cert": "",
    "head": "prong",
    "prong_count": 4,
    "shank": "plain",
    "size_standard": "US",
    "size": 6.5,
    "band_width": 2.0,
    "band_thickness": 1.6,
    "alloy": "14k yellow gold",
}


class FormError(ValueError):
    """The form payload is malformed (missing / non-numeric field)."""


def _number(form: dict[str, Any], key: str) -> float:
    try:
        return float(form[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise FormError(f"{key} must be a number") from exc


def spec_from_form(form: dict[str, Any], job_id: str = "demo") -> dict[str, Any]:
    """Turn the page's flat form into a canonical spec v0.1."""
    alloy = str(form.get("alloy") or "")
    if alloy not in ALLOYS:
        raise FormError(f"unknown alloy {alloy!r}")
    shape = str(form.get("shape") or "")
    head = str(form.get("head") or "")
    shank = str(form.get("shank") or "plain")

    stone: dict[str, Any] = {
        "stone_id": "center_001",
        "role": "center",
        "shape": shape,
        "dimensions_mm": {
            "length": _number(form, "length"),
            "width": _number(form, "width"),
            "depth": _number(form, "depth"),
        },
    }
    cert = str(form.get("cert") or "").strip()
    if cert:
        stone["cert"] = {"lab": "GIA", "report_number": cert}

    halo = str(form.get("halo") or "none")
    if halo not in ("none", "single", "hidden"):
        raise FormError(f"unknown halo {halo!r}")
    setting: dict[str, Any] = {
        "center_setting": head,
        "halo_style": halo,
        "side_stone_layout": "none",
    }
    prongs = form.get("prong_count")
    if head != "bezel" and prongs not in (None, "", 0, "0"):
        try:
            setting["prong_count"] = int(prongs)
        except (TypeError, ValueError) as exc:
            raise FormError("prong_count must be an integer") from exc

    stones = [stone]
    if halo != "none":
        melee = _number(form, "halo_melee")
        stones.append({"stone_id": "halo_melee", "role": "halo", "shape": "round",
                       "dimensions_mm": {"length": melee, "width": melee}})
    ring_extra: dict[str, Any] = {}
    if shank == "pave":
        melee = _number(form, "pave_melee")
        stones.append({"stone_id": "pave_melee", "role": "accent", "shape": "round",
                       "dimensions_mm": {"length": melee, "width": melee}})
        if form.get("pave_coverage"):
            ring_extra["pave_coverage"] = str(form["pave_coverage"])

    size_standard = str(form.get("size_standard") or "US")
    return {
        "spec_version": "0.1",
        "job": {
            "job_id": job_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "archetype": "halo" if halo != "none" else "solitaire",
            "output_targets": ["GLB", "STL"],
        },
        "ring": {
            "size_standard": size_standard,
            "size": _number(form, "size"),
            "band_width_mm": _number(form, "band_width"),
            "band_thickness_mm": _number(form, "band_thickness"),
            "shank_style": shank,
            **ring_extra,
        },
        "metal": dict(ALLOYS[alloy]),
        "stones": stones,
        "setting": setting,
        "manufacturing_profile": {
            "process": "casting",
            "compensation_policy": "calibrated_shop_profile",
        },
        "metadata": {"source": "guided_funnel", "notes": "Aureon local demo"},
    }


def funnel_plan_response(
    state: dict[str, Any], job_id: str = "demo"
) -> tuple[dict[str, Any], Any]:
    """Guided-funnel state -> plain-language issues by step (+ the compiled plan)."""
    body: dict[str, Any] = {"ok": False, "issues": [], "errors": [], "warnings": [],
                            "advisories": [], "carat_estimate": None}
    try:
        form = funnel.funnel_to_form(state)
    except (funnel.FunnelError, TypeError, ValueError) as exc:
        issue = funnel._issue("review", "block", "Pick an option on each step.", str(exc))
        body.update(issues=[issue], errors=[str(exc)])
        return body, None

    spec = None
    if not any(i["level"] == "block" for i in funnel.input_issues(state)):
        try:
            spec = spec_from_form(form, job_id)
        except FormError:
            spec = None
    issues = funnel.funnel_issues(state, spec)
    body.update(
        carat_estimate=funnel.carat_estimate(state),
        stone_mm=funnel.stone_dimensions(state),
        band_mm={"width": form["band_width"], "thickness": form["band_thickness"]},
        prong_count=form["prong_count"],
        melee_mm={"halo": form["halo_melee"], "pave": form["pave_melee"]},
        spec=spec,
    )

    plan = None
    if spec is not None and not any(i["level"] == "block" for i in issues):
        try:
            plan = compile_solitaire_plan(spec)
        except (SpecRejectedError, UnsupportedSpecError) as exc:
            details = getattr(exc, "issues", None) or [str(exc)]
            issues.append(funnel._issue(
                "review", "block", "This combination can't be built yet.",
                "Try another style or shape.", "; ".join(details),
            ))
    body["issues"] = issues
    body["errors"] = [i["text"] for i in issues if i["level"] == "block"]
    body["warnings"] = [i["text"] for i in issues if i["level"] == "warn"]
    if plan is not None:
        body.update(
            ok=True,
            notes=list(plan.notes),
            derived=plan.derived,
            metal=plan.artisan_metal,
            steps=[{"tool": s.tool, "purpose": s.purpose} for s in plan.steps],
        )
    return body, plan


def plan_response(form: dict[str, Any], job_id: str = "demo") -> tuple[dict[str, Any], Any]:
    """Validate + compile. Returns (json body, plan or None).

    Accepts the guided-funnel state (has a ``style`` key) or the legacy flat form.
    """
    if funnel.is_funnel_state(form):
        return funnel_plan_response(form, job_id)
    try:
        spec = spec_from_form(form, job_id)
    except FormError as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": []}, None

    carat = _derive_carat(spec["stones"][0])
    body: dict[str, Any] = {
        "ok": False,
        "errors": [],
        "warnings": [],
        "advisories": [],
        "carat_estimate": round(carat, 3) if carat else None,
    }
    try:
        plan = compile_solitaire_plan(spec)
    except SpecRejectedError as exc:
        body["errors"] = list(exc.issues)
        return body, None
    except UnsupportedSpecError as exc:
        body["errors"] = [str(exc)]
        return body, None

    advisories = validate_constraints(spec).advisory_notes
    body.update(
        ok=True,
        warnings=list(plan.warnings),
        notes=list(plan.notes),
        advisories=[f"{v.rule_id}: {v.message}" for v in advisories],
        derived=plan.derived,
        metal=plan.artisan_metal,
        steps=[{"tool": s.tool, "purpose": s.purpose} for s in plan.steps],
    )
    return body, plan


class DemoApp:
    """Request-independent state: output root, the Rhino client, the build lock."""

    def __init__(
        self,
        out_root: Path,
        client_factory: Callable[[], ArtisanClient],
        mcp_url: str = DEFAULT_MCP_URL,
    ):
        self.out_root = out_root.resolve()
        self.out_root.mkdir(parents=True, exist_ok=True)
        self.client_factory = client_factory
        self.mcp_url = mcp_url
        self.build_lock = threading.Lock()

    def health(self) -> dict[str, Any]:
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                        "clientInfo": {"name": "aureon-demo", "version": "0"}}}
        ).encode()
        try:
            request = urllib.request.Request(
                self.mcp_url, data=payload, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                info = json.loads(response.read())["result"]["serverInfo"]
            return {"rhino": True, "server": f"{info.get('title')} {info.get('version')}"}
        except Exception as exc:  # noqa: BLE001 - health probe
            return {"rhino": False, "server": None, "error": str(exc)}

    def build(self, form: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        job_id = time.strftime("aureon_%Y%m%d_%H%M%S") + f"_{form.get('shape', 'x')}"
        if funnel.is_funnel_state(form) and str(form.get("style")) in funnel.STYLE_BY_ID:
            job_id += f"_{form['style']}"
        body, plan = plan_response(form, job_id)
        if plan is None:
            return HTTPStatus.UNPROCESSABLE_ENTITY, body
        if not self.build_lock.acquire(blocking=False):
            return HTTPStatus.CONFLICT, {"ok": False, "errors": ["A build is already running."]}
        try:
            result = ArtisanExecutor(self.client_factory()).run(plan, self.out_root / job_id)
        finally:
            self.build_lock.release()

        files = {
            kind: f"/files/{job_id}/{Path(path).name}" for kind, path in result.files.items()
        }
        if result.manifest_path:
            files["manifest"] = f"/files/{job_id}/{result.manifest_path.name}"
        gem = result.bindings.get("gem", {}).get("gem", {})
        return HTTPStatus.OK, {
            **body,
            "ok": result.status == "built" and not result.metrics.get("qa_failures"),
            "status": result.status,
            "error": result.error,
            "job_id": job_id,
            "metrics": result.metrics,
            "artisan_carat": gem.get("carat"),
            "files": files,
        }

    def resolve_file(self, rel: str) -> Path | None:
        target = (self.out_root / rel).resolve()
        if self.out_root not in target.parents or not target.is_file():
            return None
        return target


def resolve_static(rel: str) -> Path | None:
    """A file under ``demo/static`` (never outside it)."""
    root = STATIC_DIR.resolve()
    target = (root / rel).resolve()
    if root not in target.parents or not target.is_file():
        return None
    return target


def styles_catalog() -> dict[str, Any]:
    """``GET /api/styles``: presets with thumbnail / GLB URLs when those files exist."""
    available = {p.name for p in STYLES_DIR.glob("*")} if STYLES_DIR.is_dir() else set()
    return funnel.catalog("/static/styles", available)


def make_handler(app: DemoApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AureonDemo/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
            sys.stderr.write("[aureon] " + (fmt % args) + "\n")

        def _json(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _file(self, path: Path) -> None:
            data = path.read_bytes()
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".glb":
                ctype = "model/gltf-binary"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if path.suffix in (".stl", ".3dm"):
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return None
            return body if isinstance(body, dict) else None

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._file(STATIC_DIR / "index.html")
            if path == "/api/health":
                return self._json(HTTPStatus.OK, app.health())
            if path == "/api/styles":
                return self._json(HTTPStatus.OK, styles_catalog())
            if path.startswith("/static/"):
                target = resolve_static(path[len("/static/"):])
                if target is not None:
                    return self._file(target)
            if path == "/api/options":
                return self._json(
                    HTTPStatus.OK,
                    {
                        "shapes": sorted(PAVILION_FRACTION),
                        "heads": list(HEAD_STYLES),
                        "shanks": list(SHANK_STYLES),
                        "alloys": list(ALLOYS),
                        "defaults": DEFAULT_FORM,
                    },
                )
            if path.startswith("/files/"):
                target = app.resolve_file(path[len("/files/"):])
                if target is not None:
                    return self._file(target)
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            form = self._read_json()
            if form is None:
                return self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "errors": ["bad JSON"]})
            if self.path == "/api/plan":
                body, _ = plan_response(form)
                return self._json(HTTPStatus.OK, body)
            if self.path == "/api/build":
                status, body = app.build(form)
                return self._json(status, body)
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    return Handler


def make_server(app: DemoApp, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(app))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aureon solitaire builder (local demo).")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", type=Path, default=ROOT / "out" / "demo")
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    args = parser.parse_args(argv)

    app = DemoApp(args.out, lambda: ArtisanMcpClient(args.mcp_url), args.mcp_url)
    server = make_server(app, port=args.port)
    print(f"Aureon demo on http://127.0.0.1:{args.port}  (Rhino MCP: {args.mcp_url})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
