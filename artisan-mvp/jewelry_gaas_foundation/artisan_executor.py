"""Run a ``BuildPlan`` against the RhinoArtisan MCP server and package the result.

The RhinoArtisan MCP (Rhino 8/9 for Mac) serves stateless JSON-RPC over HTTP on
``127.0.0.1:9280/mcp``, so the pipeline needs no agent in the loop:
``spec -> compile_solitaire_plan -> ArtisanExecutor.run -> 3DM/STL/GLB/PNG + manifest``.

Post-build the executor:

* moves the hidden union operands off the Metal layers — Artisan keeps them for
  re-editing, but its weight/price engine would otherwise count the metal twice;
* measures the built model inside Rhino (culet clearance to the metal below,
  culet-inside-metal, inner diameter, body count/solidity);
* weighs the metal body, prices the design with the shop's Artisan pricing
  settings, exports, and runs Gate 1 (mesh closedness) on the STL as a QA metric.

Brand rule: none of this certifies manufacturability. The manifest's five
``geometry_gates`` stay ``pending``; the measurements are reported as QA signals.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from jewelry_gaas_foundation.artisan_plan import GEOMETRY_GATES, BuildPlan
from jewelry_gaas_foundation.glb_materials import style_glb

DEFAULT_MCP_URL = "http://127.0.0.1:9280/mcp"
OPERANDS_LAYER = "MyJewl Operands"

# Artisan 7.1.0.10 rejects a few exact finger diameters with
# CHECK_SHANK_PARAMETERS (e.g. 16.507 = US 6, 17.73 = US 7.5) while their
# neighbours build fine — a size-table snapping bug, not a real limit. Shank steps
# retry with a nudge of at most 0.01 mm (far below casting tolerance) and the
# manifest records the nudge used.
SHANK_TOOLS = ("create_ring", "create_cathedral", "create_pave_shank")
FINGER_NUDGES_MM = (0.0, 0.005, -0.005, 0.01, -0.01)
# The same snapping bug hits head heights: add_bezel_setting rejects a pear bezel
# of exactly 3.681 mm with INVALID_PARAMETERS_FOR_BASE_GEOMETRY while 3.68 and
# 3.685 build (live 2026-11-23). Head steps retry their height the same way.
HEAD_HEIGHT_ARG = {
    "add_bezel_setting": "height",
    "add_prong_setting": "height",
    "add_basket_setting": "prong_height",
}


class ArtisanToolError(RuntimeError):
    """A RhinoArtisan tool call failed (transport, JSON-RPC or tool error)."""


class PlanExecutionError(RuntimeError):
    """The plan could not be executed (bad reference, failed step...)."""


@dataclass(frozen=True)
class ToolResult:
    tool: str
    text: str
    data: Any  # first JSON value found in ``text``, or None


class ArtisanClient(Protocol):
    def call(self, tool: str, args: dict[str, Any]) -> ToolResult: ...


def parse_first_json(text: str) -> Any:
    """Return the first JSON object/array embedded in a tool's text output."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char in "{[":
            try:
                value, _ = decoder.raw_decode(text[index:])
                return value
            except json.JSONDecodeError:
                continue
    return None


class ArtisanMcpClient:
    """Minimal stdlib JSON-RPC client for the RhinoArtisan MCP HTTP endpoint."""

    def __init__(self, url: str = DEFAULT_MCP_URL, timeout_s: float = 600.0):
        self.url = url
        self.timeout_s = timeout_s
        self._next_id = 0

    def call(self, tool: str, args: dict[str, Any]) -> ToolResult:
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ArtisanToolError(f"{tool}: transport error: {exc}") from exc

        if "error" in body:
            raise ArtisanToolError(f"{tool}: JSON-RPC error: {body['error']}")
        result = body.get("result") or {}
        text = "".join(
            part.get("text", "") for part in result.get("content", []) if isinstance(part, dict)
        )
        if result.get("isError"):
            raise ArtisanToolError(f"{tool}: {text.strip()}")
        return ToolResult(tool=tool, text=text, data=parse_first_json(text))


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------


def _lookup(bindings: dict[str, Any], path: str) -> Any:
    head, _, rest = path.partition(".")
    if head not in bindings:
        raise PlanExecutionError(f"unbound reference {path!r}")
    value: Any = bindings[head]
    for key in rest.split(".") if rest else []:
        if not isinstance(value, dict) or key not in value:
            raise PlanExecutionError(f"reference {path!r} not found in step output")
        value = value[key]
    return value


def resolve_refs(value: Any, bindings: dict[str, Any]) -> Any:
    """Replace ``{"$ref": path}`` / ``{"$refs": [paths]}`` with bound values."""
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return _lookup(bindings, value["$ref"])
        if set(value) == {"$refs"}:
            flat: list[Any] = []
            for path in value["$refs"]:
                item = _lookup(bindings, path)
                flat.extend(item if isinstance(item, list) else [item])
            return flat
        return {key: resolve_refs(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_refs(item, bindings) for item in value]
    return value


# ---------------------------------------------------------------------------
# In-Rhino scripts (CPython 3 via run_script)
# ---------------------------------------------------------------------------

# Every hidden solid on a Metal layer is a union operand (each build runs in a new
# document): the shank, the head, a halo's prongs, a pave band's bead prongs, and
# the intermediate results of a folded union.
_ISOLATE_OPERANDS = """#! python 3
import Rhino
doc = Rhino.RhinoDoc.ActiveDoc
idx = doc.Layers.FindByFullPath(%(layer)r, -1)
if idx < 0:
    layer = Rhino.DocObjects.Layer()
    layer.Name = %(layer)r
    idx = doc.Layers.Add(layer)
settings = Rhino.DocObjects.ObjectEnumeratorSettings()
settings.HiddenObjects = True
moved = 0
for obj in list(doc.Objects.GetObjectList(settings)):
    on_metal = doc.Layers[obj.Attributes.LayerIndex].FullPath.startswith("Metal")
    if on_metal and obj.IsHidden:
        attrs = obj.Attributes.Duplicate()
        attrs.LayerIndex = idx
        if doc.Objects.ModifyAttributes(obj.Id, attrs, True):
            moved += 1
print(moved)
"""

# Visible solids on the Metal layers (``{"$metal": "all"}`` in a plan step).
_LIST_METAL = """#! python 3
import json
import Rhino
doc = Rhino.RhinoDoc.ActiveDoc
ids = []
for obj in doc.Objects.GetObjectList(Rhino.DocObjects.ObjectEnumeratorSettings()):
    layer = doc.Layers[obj.Attributes.LayerIndex].FullPath
    if layer.startswith("Metal") and isinstance(obj.Geometry, Rhino.Geometry.Brep):
        ids.append(str(obj.Id))
print(json.dumps(ids))
"""

_MEASURE = """#! python 3
import json
import Rhino
from Rhino.Geometry import Line, Point3d
from Rhino.Geometry.Intersect import Intersection
doc = Rhino.RhinoDoc.ActiveDoc
tol = doc.ModelAbsoluteTolerance
gem_id = %(gem_id)r
metal_ids = set(%(metal_ids)s)
settings = Rhino.DocObjects.ObjectEnumeratorSettings()
settings.HiddenObjects = True
gem, metal = None, []
for obj in doc.Objects.GetObjectList(settings):
    key = str(obj.Id)
    if key == gem_id:
        gem = obj.Geometry
    elif key in metal_ids:
        metal.append(obj.Geometry)
out = {"metal_bodies": len(metal)}
breps = [g for g in metal if isinstance(g, Rhino.Geometry.Brep)]
out["metal_all_solid"] = bool(breps) and all(b.IsSolid for b in breps)
def hits(a, b):
    pts = []
    crv = Line(a, b).ToNurbsCurve()
    for brep in breps:
        ok, _, found = Intersection.CurveBrep(crv, brep, tol)
        if ok:
            pts.extend(found)
    return pts
if gem is not None:
    gb = gem.GetBoundingBox(True)
    culet = Point3d(0, 0, gb.Min.Z)
    out["culet_z_mm"] = round(gb.Min.Z, 4)
    out["table_z_mm"] = round(gb.Max.Z, 4)
    inside = any(b.IsPointInside(culet, tol, False) for b in breps)
    out["culet_inside_metal"] = inside
    if inside:
        out["culet_clearance_mm"] = 0.0
        above = [p.Z for p in hits(culet, Point3d(0, 0, gb.Min.Z + 40)) if p.Z >= gb.Min.Z]
        if above:
            out["culet_penetration_mm"] = round(min(above) - gb.Min.Z, 4)
    else:
        below = [p.Z for p in hits(culet, Point3d(0, 0, gb.Min.Z - 40)) if p.Z <= gb.Min.Z]
        if below:
            out["culet_clearance_mm"] = round(gb.Min.Z - max(below), 4)
xs = sorted(p.X for p in hits(Point3d(-40, 0, 0), Point3d(40, 0, 0)))
neg = [x for x in xs if x < 0]
pos = [x for x in xs if x > 0]
if neg and pos:
    out["measured_finger_diameter_mm"] = round(min(pos) - max(neg), 4)
# First metal straight above the ring's centre: the band's inner surface, unless a
# head, prong or bridge reaches down into the finger hole.
ups = [p.Z for p in hits(Point3d(0, 0, 0), Point3d(0, 0, 40)) if p.Z > 0]
if ups:
    out["measured_inner_top_mm"] = round(min(ups), 4)
if breps:
    bb = breps[0].GetBoundingBox(True)
    for b in breps[1:]:
        bb.Union(b.GetBoundingBox(True))
    out["metal_bbox_mm"] = [round(v, 3) for v in (bb.Min.X, bb.Min.Y, bb.Min.Z, bb.Max.X, bb.Max.Y, bb.Max.Z)]
print(json.dumps(out))
"""


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


@dataclass
class BuildResult:
    status: str  # "built" | "failed"
    out_dir: Path
    bindings: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    manifest_path: Path | None = None


class ArtisanExecutor:
    """Execute a compiled plan in a fresh Rhino document and package outputs."""

    def __init__(self, client: ArtisanClient):
        self.client = client

    def run(
        self,
        plan: BuildPlan,
        out_dir: str | Path,
        *,
        new_document: bool = True,
        exports: tuple[str, ...] = ("3dm", "stl", "glb", "png"),
    ) -> BuildResult:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        result = BuildResult(status="failed", out_dir=out)
        log: list[dict[str, Any]] = []
        started = time.monotonic()
        current = "new_document"
        try:
            if new_document:
                # Never touch the user's open work: build in a new document.
                self.client.call("new_document", {})

            for step in plan.steps:
                current = step.step_id
                args = resolve_refs(step.args, result.bindings)
                args = self._resolve_doc_metal(args, result)
                t0 = time.monotonic()
                tool_result, args = self._call_step(step.tool, args, result)
                log.append(
                    {
                        "step": step.step_id,
                        "tool": step.tool,
                        "args": args,
                        "seconds": round(time.monotonic() - t0, 3),
                    }
                )
                if step.bind:
                    if not isinstance(tool_result.data, dict):
                        raise PlanExecutionError(
                            f"{step.tool} returned no JSON to bind: {tool_result.text[:200]}"
                        )
                    result.bindings[step.bind] = tool_result.data

            current = "post_build"
            self._post_build(plan, result, exports)
            result.status = "built"
            result.metrics["qa_failures"] = qa_failures(plan, result.metrics)
        except (ArtisanToolError, PlanExecutionError) as exc:
            result.error = f"step {current!r}: {exc}"
        finally:
            result.metrics["build_seconds"] = round(time.monotonic() - started, 2)
            result.manifest_path = self._write_manifest(plan, result, log)
        return result

    def _resolve_doc_metal(self, args: dict[str, Any], result: BuildResult) -> dict[str, Any]:
        """``{"$metal": "all"}`` -> every visible metal solid, the shank first."""
        target = args.get("target_ids")
        if not (isinstance(target, dict) and set(target) == {"$metal"}):
            return args
        ids = parse_first_json(self.client.call("run_script", {"code": _LIST_METAL}).text)
        if not isinstance(ids, list) or not ids:
            raise PlanExecutionError("no metal solids found to union")
        shank = (result.bindings.get("shank") or {}).get("id")
        if shank in ids:
            ids = [shank] + [i for i in ids if i != shank]
        result.metrics["union_operands"] = len(ids)
        return {**args, "target_ids": ids}

    def _union(self, args: dict[str, Any], result: BuildResult) -> ToolResult:
        """Artisan UNION; when the one-shot union fails (e.g. a halo's prongs meet at
        one point), fold the solids onto the first one, one at a time."""
        try:
            return self.client.call("boolean_operation", args)
        except ArtisanToolError as exc:
            ids = list(args.get("target_ids") or [])
            if "Boolean computation failed" not in str(exc) or len(ids) < 3:
                raise
        acc = [ids[0]]
        last: ToolResult | None = None
        for target in ids[1:]:
            last = self.client.call(
                "boolean_operation", {**args, "target_ids": acc + [target]}
            )
            acc = list((last.data or {}).get("ids") or [])
            if not acc:
                raise PlanExecutionError(f"folded union lost its result: {last.text[:200]}")
        result.metrics["union_strategy"] = "fold"
        assert last is not None
        data = {**(last.data or {}), "ids": acc}
        return ToolResult(tool="boolean_operation", text=last.text, data=data)

    def _call_step(
        self, tool: str, args: dict[str, Any], result: BuildResult
    ) -> tuple[ToolResult, dict[str, Any]]:
        if tool == "boolean_operation" and args.get("operation") == "UNION":
            return self._union(args, result), args
        if tool in SHANK_TOOLS and "finger_diameter" in args:
            return self._nudged(tool, args, result, "finger_diameter",
                                "CHECK_SHANK_PARAMETERS", "finger_diameter_nudge_mm")
        key = HEAD_HEIGHT_ARG.get(tool)
        if key and key in args:
            return self._nudged(tool, args, result, key,
                                "INVALID_PARAMETERS_FOR_BASE_GEOMETRY", "head_height_nudge_mm")
        return self.client.call(tool, args), args

    def _nudged(
        self, tool: str, args: dict[str, Any], result: BuildResult,
        key: str, error_code: str, metric: str,
    ) -> tuple[ToolResult, dict[str, Any]]:
        """Call ``tool``, retrying ``args[key]`` +-0.005/0.01 mm on ``error_code``."""
        last_error: ArtisanToolError | None = None
        for nudge in FINGER_NUDGES_MM:
            attempt = {**args, key: round(args[key] + nudge, 4)}
            try:
                tool_result = self.client.call(tool, attempt)
            except ArtisanToolError as exc:
                if error_code not in str(exc):
                    raise
                last_error = exc
                continue
            if nudge or metric == "finger_diameter_nudge_mm":
                result.metrics[metric] = nudge
            return tool_result, attempt
        assert last_error is not None
        raise last_error

    # -- post build ---------------------------------------------------------

    def _post_build(self, plan: BuildPlan, result: BuildResult, exports: tuple[str, ...]) -> None:
        bindings = result.bindings
        metal_ids = list(bindings["metal"].get("ids") or [])
        if not metal_ids:
            raise PlanExecutionError("union produced no metal body")

        self.client.call("run_script", {"code": _ISOLATE_OPERANDS % {"layer": OPERANDS_LAYER}})

        measured = self.client.call(
            "run_script",
            {"code": _MEASURE % {"gem_id": bindings["gem"]["id"], "metal_ids": json.dumps(metal_ids)}},
        ).data
        if isinstance(measured, dict):
            result.metrics.update(measured)

        gems = self.client.call("list_gems", {}).data
        if isinstance(gems, dict) and isinstance(gems.get("gems"), list):
            center = bindings["gem"]["id"]
            melee = [g for g in gems["gems"] if g.get("id") != center]
            result.metrics["melee_count"] = len(melee)
            if melee:
                carats = [float((g.get("gem") or {}).get("carat") or 0.0) for g in melee]
                sizes = {round(float((g.get("gem") or {}).get("sizeX") or 0.0), 2) for g in melee}
                result.metrics["melee_carat"] = round(sum(carats), 3)
                result.metrics["melee_sizes_mm"] = sorted(sizes)

        weight = self.client.call(
            "calculate_weight", {"object_ids": metal_ids, "metal": plan.artisan_metal}
        ).data
        if isinstance(weight, list) and weight:
            row = weight[0]
            result.metrics["metal"] = row.get("metal")
            result.metrics["metal_volume_mm3"] = round(float(row.get("volume", 0.0)), 3)
            result.metrics["metal_weight_g"] = round(float(row.get("grams", 0.0)), 3)
            result.metrics["wax_weight_g"] = round(float(row.get("wax_grams", 0.0)), 3)

        price = self.client.call("calculate_price", {"detailed": True}).data
        if isinstance(price, dict):
            result.metrics["price"] = {
                "currency": price.get("Currency"),
                "mounting_total": price.get("Total"),
                "metal": price.get("MetalTotal"),
                "processes": price.get("ProcessesTotal"),
                "melee": price.get("GemsTotal"),
                "melee_count": price.get("GemsCount"),
                "setting": price.get("ProcessesSettingCost"),
                "center_stone_quoted": not price.get("QuoteNotice"),
                "notice": price.get("QuoteNotice"),
            }

        stem = _safe_stem(plan.job_id)
        if "3dm" in exports:
            path = out_path(result.out_dir, stem, "3dm")
            self.client.call("save_document", {"path": str(path), "copy": True})
            result.files["3dm"] = str(path)
        if "stl" in exports:
            path = out_path(result.out_dir, stem, "stl")
            self.client.call("export_stl", {"path": str(path)})
            result.files["stl"] = str(path)
            result.metrics["qa_mesh_closedness"] = _gate1(path)
        if "glb" in exports:
            path = out_path(result.out_dir, stem, "glb")
            self.client.call("export_glb", {"path": str(path)})
            result.files["glb"] = str(path)
            if path.is_file():
                try:
                    result.metrics["glb_materials"] = style_glb(path, plan.artisan_metal)
                except ValueError as exc:  # unexpected GLB layout: keep Artisan's
                    result.metrics["glb_materials"] = {"error": str(exc)}
        if "png" in exports:
            path = out_path(result.out_dir, stem, "png")
            z = plan.derived["finger_diameter_mm"] / 2.0
            self.client.call(
                "set_view",
                {
                    "camera_location": [58.0, -78.0, 40.0],
                    "camera_target": [0.0, 0.0, z * 0.3],
                    "lens_mm": 85,
                    "display_mode": "Shaded",
                },
            )
            self.client.call(
                "capture_viewport", {"path": str(path), "width": 1600, "height": 1200}
            )
            result.files["png"] = str(path)

    # -- manifest -----------------------------------------------------------

    def _write_manifest(
        self, plan: BuildPlan, result: BuildResult, log: list[dict[str, Any]]
    ) -> Path:
        derived = plan.derived
        metrics = result.metrics
        drift = None
        if "culet_z_mm" in metrics:
            drift = round(metrics["culet_z_mm"] - derived["expected_culet_z_mm"], 4)
        manifest = {
            "job_id": plan.job_id,
            "status": result.status,
            "error": result.error,
            "qa_failures": metrics.get("qa_failures"),
            "niche": plan.niche,
            "engine": "RhinoArtisan MCP",
            "plan": plan.to_dict(),
            "bindings": result.bindings,
            "metrics": {**metrics, "culet_z_drift_mm": drift},
            "files": result.files,
            "step_log": log,
            # Brand rule: MyJewl never certifies manufacturing pass/fail.
            "geometry_gates": {gate: "pending" for gate in GEOMETRY_GATES},
            "disclaimer": (
                "QA measurements are design-time signals, not a manufacturing "
                "certification. Geometry gates stay pending until a calibrated shop "
                "profile validates them. Mounting price uses the Artisan pricing "
                "settings on this machine; the center stone is not quoted."
            ),
        }
        path = result.out_dir / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
        return path


FINGER_DIAMETER_TOLERANCE_MM = 0.02


def qa_failures(plan: BuildPlan, metrics: dict[str, Any]) -> list[str]:
    """Defects the post-build measurement detected (empty = none detected).

    A detected defect fails the build; an empty list is NOT a pass certificate.
    """
    failures: list[str] = []
    if metrics.get("metal_bodies") != 1:
        failures.append(f"metal_bodies={metrics.get('metal_bodies')} (expected 1)")
    if metrics.get("metal_all_solid") is False:
        failures.append("metal_not_closed_solid")
    if metrics.get("culet_inside_metal"):
        failures.append(
            f"culet_inside_metal (penetration {metrics.get('culet_penetration_mm')} mm)"
        )
    expected = plan.derived.get("finger_diameter_mm")
    inner_top = metrics.get("measured_inner_top_mm")
    if inner_top is not None and expected is not None:
        intrusion = expected / 2.0 - inner_top
        if intrusion > FINGER_DIAMETER_TOLERANCE_MM:
            failures.append(f"metal_inside_finger_hole ({intrusion:.2f} mm)")
    measured = metrics.get("measured_finger_diameter_mm")
    if measured is None:
        failures.append("finger_diameter_not_measured")
    elif expected is not None and abs(measured - expected) > FINGER_DIAMETER_TOLERANCE_MM:
        failures.append(f"finger_diameter {measured} vs spec {expected}")
    gate1 = metrics.get("qa_mesh_closedness") or {}
    if gate1.get("status") == "fail":
        failures.append("stl_mesh_not_closed")
    return failures


def out_path(out_dir: Path, stem: str, ext: str) -> Path:
    return (out_dir / f"{stem}.{ext}").resolve()


def _safe_stem(job_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in job_id) or "job"


def _gate1(stl_path: Path) -> dict[str, Any]:
    try:
        from jewelry_gaas_foundation.stl_gates import check_mesh_closedness
    except ImportError as exc:  # trimesh missing
        return {"status": "not_run", "reason": str(exc)}
    gate = check_mesh_closedness(stl_path)
    return {"status": gate.status, "metrics": gate.metrics, "reason": gate.reason}
