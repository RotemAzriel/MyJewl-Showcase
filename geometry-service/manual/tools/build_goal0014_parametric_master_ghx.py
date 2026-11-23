#!/usr/bin/env python3
"""Build the Goal 0014 parametric master (v5) from the approved Goal 0011 V4 shape.

V5 changes exactly one thing about V4: where its numbers come from. The approved
V4 silhouette, part list, part names, and construction calls are preserved
verbatim; the frozen ``P`` literal and every hard-coded coordinate in
``build_parts()`` are replaced by values derived from the request.

The derivation itself is not authored here. It is inlined **verbatim** from
``jewelry_gaas_foundation/master_parameters.py`` so the definition and the test
suite share one source of truth, and
``tests/test_goal0014_parametric_master.py`` asserts the two copies are
byte-identical. Geometry authorship stays in Rhino: this module moves arithmetic
out of the GHX blob, not geometry.
"""

from __future__ import annotations

from pathlib import Path

try:
    from manual.tools.build_ghx import build_python3_ghx
    from manual.tools.build_goal0011_realism_master_v4_ghx import SCRIPT as V4_SCRIPT
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from manual.tools.build_ghx import build_python3_ghx
    from manual.tools.build_goal0011_realism_master_v4_ghx import SCRIPT as V4_SCRIPT

from jewelry_gaas_foundation.master_parameters import (
    BLOCK_END_MARKER,
    derivation_block_source,
)

TEMPLATE_ID = "goal0014-parametric-round-solitaire-master-v5"
MASTER_FILE_STEM = "solitaire_parametric_master_v5"
V4_TEMPLATE_ID = "goal0011-realism-first-round-solitaire-master-v4"

_FROZEN_PARAMETER_BLOCK_HEAD = 'P = {\n    "template_id": "' + V4_TEMPLATE_ID + '",'

_V4_SHOULDER_BLOCK = '''        front = [
            rg.Point3d(side * 5.35, -0.25, 7.42),
            rg.Point3d(side * 4.82, -P["shoulder_split_half_gap_root"], P["shoulder_contact_z"]),
            rg.Point3d(side * 4.35, -0.42, 8.30),
            rg.Point3d(side * 3.55, -0.66, 9.05),
            rg.Point3d(side * 2.45, -0.98, 9.78),
            rg.Point3d(side * 1.30815, -P["shoulder_split_half_gap_neck"], 10.38),
        ]
        rear = [rg.Point3d(point.X, -point.Y, point.Z) for point in front]
        radii = [P["shoulder_root_radius"], P["shoulder_root_radius"], 0.52, 0.45, 0.35, P["shoulder_neck_radius"]]'''

_V5_SHOULDER_BLOCK = '''        front = [
            rg.Point3d(side * point[0], -point[1], point[2])
            for point in P["shoulder_path"]
        ]
        rear = [rg.Point3d(point.X, -point.Y, point.Z) for point in front]
        radii = [float(value) for value in P["shoulder_radii"]]'''

_V4_PETAL_BLOCK = '''        supports = [
            rg.Point3d(radial.X * 1.32, radial.Y * 1.32, P["basket_lower_z"]),
            rg.Point3d(radial.X * 1.50 + tangent.X * 0.08, radial.Y * 1.50 + tangent.Y * 0.08, 9.94),
            rg.Point3d(radial.X * 1.85, radial.Y * 1.85, 10.38),
            rg.Point3d(radial.X * 2.24, radial.Y * 2.24, P["basket_upper_z"]),
        ]
        parts.append(("basket_petal_{:02d}".format(index + 1), "metal", tapered_sweep(supports, [0.25, 0.235, 0.21, 0.18])))'''

_V5_PETAL_BLOCK = '''        supports = [
            rg.Point3d(
                radial.X * item[0] + tangent.X * item[1],
                radial.Y * item[0] + tangent.Y * item[1],
                item[2],
            )
            for item in P["basket_petal_profile"]
        ]
        parts.append(("basket_petal_{:02d}".format(index + 1), "metal", tapered_sweep(supports, [float(value) for value in P["basket_petal_radii"]])))'''

_V4_PRONG_BLOCK = '''        prongs = [
            rg.Point3d(radial.X * 1.38, radial.Y * 1.38, 9.72),
            rg.Point3d(radial.X * 1.66, radial.Y * 1.66, 10.02),
            rg.Point3d(radial.X * 2.24, radial.Y * 2.24, 10.82),
            rg.Point3d(radial.X * 3.06, radial.Y * 3.06, P["stone_girdle_z"] - 0.18),
            rg.Point3d(radial.X * 2.86, radial.Y * 2.86, P["stone_girdle_z"] + 0.18),
            rg.Point3d(radial.X * 2.68, radial.Y * 2.68, P["stone_girdle_z"] + P["prong_tip_height"]),
        ]
        parts.append(("prong_{:02d}".format(index + 1), "metal", tapered_sweep(prongs, [P["prong_root_radius"], 0.29, 0.27, P["prong_seat_radius"], 0.20, P["prong_tip_radius"]])))'''

_V5_PRONG_BLOCK = '''        prongs = [
            rg.Point3d(radial.X * item[0], radial.Y * item[0], item[1])
            for item in P["prong_profile"]
        ]
        parts.append(("prong_{:02d}".format(index + 1), "metal", tapered_sweep(prongs, [float(value) for value in P["prong_radii"]])))'''

_V4_RUN_HEAD = '''def run():
    request = json.loads(str(json_string)) if json_string else {}
    if str(request.get("template_id", P["template_id"])) != P["template_id"]:
        raise ValueError("unsupported template_id")'''

_V5_RUN_HEAD = '''def run():
    request = json.loads(str(json_string)) if json_string else {}
    if str(request.get("template_id", TEMPLATE_ID)) != TEMPLATE_ID:
        raise ValueError("unsupported template_id")
    P.clear()
    P.update(derive_parameters(request))
    P["template_id"] = TEMPLATE_ID
    metrics = assembly_metrics(P)'''

_V4_RECEIPT = '''    receipt = {"schema": "myjewl.goal0011.realism_master_receipt.v4", "generated_by": "Azure Rhino.Compute 8 /grasshopper from MyJewl-owned RhinoCommon source", "job_id": safe_job, "template": P, "authority": "rhino_master_candidate_not_manufacturing", "manufacturing_gates": "pending_not_run_needs_calibration", "parts": files}'''

_V5_RECEIPT = '''    receipt = {"schema": "myjewl.goal0014.parametric_master_receipt.v5", "generated_by": "Azure Rhino.Compute 8 /grasshopper from MyJewl-owned RhinoCommon source", "job_id": safe_job, "request": request, "template": P, "assembly_metrics": metrics, "derivation_version": P["derivation_version"], "authority": "rhino_master_candidate_not_manufacturing", "manufacturing_gates": "pending_not_run_needs_calibration", "parts": files}'''

_V4_RESPONSE = '''"template_id": P["template_id"], "output_dir": output_dir, "part_count": len(parts),'''

_V5_RESPONSE = '''"template_id": P["template_id"], "derivation_version": P["derivation_version"], "source_preflight_ready": metrics["source_preflight_ready"], "output_dir": output_dir, "part_count": len(parts),'''


def _replace_once(source: str, old: str, new: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(
            "expected one v4 source fragment, found {}: {!r}".format(count, old[:90])
        )
    return source.replace(old, new, 1)


def _strip_frozen_parameters(source: str) -> str:
    """Replace the frozen ``P = {...}`` literal with an empty runtime-filled dict."""
    start = source.index(_FROZEN_PARAMETER_BLOCK_HEAD)
    end = source.index("\n}\n", start) + len("\n}\n")
    frozen = source[start:end]
    if "stone_diameter" not in frozen or "inner_diameter" not in frozen:
        raise RuntimeError("did not locate the frozen V4 parameter dict")
    return source[:start] + "P = {}\n" + source[end:]


def _rewrite_source() -> str:
    source = V4_SCRIPT
    source = _strip_frozen_parameters(source)
    source = _replace_once(
        source,
        "import Rhino\nimport Rhino.Geometry as rg\n",
        "import Rhino\nimport Rhino.Geometry as rg\n\n\n"
        + derivation_block_source()
        + "\n",
    )
    source = _replace_once(
        source,
        'OUTPUT_ROOT = r"C:\\MyJewlGenerated\\goal0011_realism_master"',
        'OUTPUT_ROOT = r"C:\\MyJewlGenerated\\goal0014_parametric_master"\n'
        'TEMPLATE_ID = "' + TEMPLATE_ID + '"',
    )
    source = _replace_once(source, _V4_SHOULDER_BLOCK, _V5_SHOULDER_BLOCK)
    source = _replace_once(source, _V4_PETAL_BLOCK, _V5_PETAL_BLOCK)
    source = _replace_once(source, _V4_PRONG_BLOCK, _V5_PRONG_BLOCK)
    source = _replace_once(source, _V4_RUN_HEAD, _V5_RUN_HEAD)
    source = _replace_once(source, _V4_RECEIPT, _V5_RECEIPT)
    source = _replace_once(source, _V4_RESPONSE, _V5_RESPONSE)
    source = _replace_once(
        source, "MyJewl Goal0011 ", "MyJewl Goal0014 "
    )
    for suffix in (".mtl", ".3dm", ".obj", "_receipt.json"):
        source = _replace_once(
            source,
            "solitaire_realism_master_v4" + suffix,
            MASTER_FILE_STEM + suffix,
        )
    source = _replace_once(
        source,
        "goal0011_realism_master_source_ready",
        "goal0014_parametric_master_source_ready",
    )
    source = _replace_once(
        source,
        "goal0011_realism_master_source_failed",
        "goal0014_parametric_master_source_failed",
    )
    # The inlined derivation block legitimately contains the V4 reference literals
    # -- that is how its ratios are calibrated. The *geometry* code after the block
    # must contain none of them.
    geometry_source = source[source.index(BLOCK_END_MARKER) + len(BLOCK_END_MARKER):]
    survivors = [
        literal
        for literal in ("5.35", "4.82", "1.30815", "12.10", "9.70", "10.90", "16.91")
        if literal in geometry_source
    ]
    if survivors:
        raise RuntimeError(
            "frozen geometry literals survived the v5 rewrite: " + ", ".join(survivors)
        )
    return source


SCRIPT = _rewrite_source()


def build(path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(
        build_python3_ghx(
            SCRIPT,
            inputs=(
                ("RH_IN:json_string", "json_string", "item"),
                ("RH_IN:job_id", "job_id", "item"),
            ),
            outputs=(("RH_OUT:response_json", "response_json"),),
        )
    )
    return destination


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    built = build(
        root / "manual" / "rhino_compute_bridge" / "goal0014_parametric_master_v5.ghx"
    )
    print(built)
