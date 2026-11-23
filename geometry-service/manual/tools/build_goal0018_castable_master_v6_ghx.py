#!/usr/bin/env python3
"""Build the Goal 0018 castable master (v6) from the Goal 0014 parametric master (v5).

V6 changes exactly two things about V5, both of them defects the 2026-08-12 audit
measured. The approved silhouette, the parameter derivation, the part list, and
every construction call are preserved verbatim.

**F1 -- orientation.** ``Brep.CreateFromLoft`` returns inward-facing normals, and
``Mesh.CreateFromBrep`` inherits them, so 12 of 19 metal parts shipped inside-out.
``UnifyNormals()`` made the winding self-*consistent* without orienting it
*outward*, and ``Gate 1`` passed them because they were watertight. V6 repairs
orientation at the **Brep** level, before meshing, by testing
``Brep.SolidOrientation`` and flipping when it reports ``Inward``. Applied to every
metal part uniformly rather than only to the loft, so pipes, the revolve, and the
sphere are guaranteed too rather than merely observed to be correct.

**F2 -- no union.** The parts interpenetrate and were never booleaned, so the
naive volume sum over-counted by 6.75% and every quote built on it was out of the
agreed +/-3% tolerance. V6 emits an additional ``castable_solid`` -- a real
``Brep.CreateBooleanUnion`` of the metal parts only.

The named parts are still exported: Blender needs them for mask IDs, and
``geometry_metrics`` measures them individually. ``castable_solid`` is the thing a
shop would cast, and it **excludes the centre stone** -- a caster receiving a fused
stone gets a diamond-shaped lump of metal.

The union is **non-fatal**. If it fails, the run still succeeds and the receipt
records the failure, because breaking a working pipeline is worse than shipping a
run whose union is honestly reported as unavailable.

Geometry authorship stays in Rhino. This module rewrites source, not shape.
"""

from __future__ import annotations

from pathlib import Path

try:
    from manual.tools.build_ghx import build_python3_ghx
    from manual.tools.build_goal0014_parametric_master_ghx import SCRIPT as V5_SCRIPT
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from manual.tools.build_ghx import build_python3_ghx
    from manual.tools.build_goal0014_parametric_master_ghx import SCRIPT as V5_SCRIPT

TEMPLATE_ID = "goal0018-castable-round-solitaire-master-v6"
MASTER_FILE_STEM = "solitaire_castable_master_v6"
V5_TEMPLATE_ID = "goal0014-parametric-round-solitaire-master-v5"

# --- F1: orientation repair, plus F2: the union helper ----------------------
_ORIENTATION_HELPERS = '''

def ensure_outward(brep):
    """Return ``brep`` with outward-facing normals.

    Goal 0018 F1. ``Brep.CreateFromLoft`` yields inward orientation, which
    survives meshing and makes a slicer read the solid as a void. Repairing here,
    at the Brep level, means every downstream consumer -- mesh, STL, OBJ, 3DM,
    and the boolean union -- inherits the correct orientation from one place.
    Non-solid Breps are returned untouched; ``SolidOrientation`` is only
    meaningful for a closed solid.
    """
    if brep is None:
        return brep
    try:
        if brep.SolidOrientation == rg.BrepSolidOrientation.Inward:
            brep.Flip()
            ORIENTATION_REPAIRS.append(1)
    except Exception:
        pass
    return brep


def union_metal(breps):
    """Boolean-union the metal parts into one castable solid.

    Goal 0018 F2. Returns ``(brep_or_None, status_string)``. Deliberately
    non-fatal: a failed union reports itself instead of failing the run.
    """
    solids = [b for b in breps if b is not None and b.IsSolid]
    if not solids:
        return None, "no_solid_metal_parts"
    if len(solids) == 1:
        return solids[0], "single_part_no_union_needed"
    try:
        result = rg.Brep.CreateBooleanUnion(solids, TOLERANCE)
    except Exception as exc:
        return None, "union_raised_" + type(exc).__name__
    if not result:
        return None, "union_returned_empty"
    result = list(result)
    if len(result) != 1:
        return result[0], "union_returned_{}_bodies".format(len(result))
    return result[0], "ok"

'''

_V5_TAPERED_TAIL = '''    if not lofts:
        raise RuntimeError("tapered sweep loft failed")
    return lofts[0].CapPlanarHoles(TOLERANCE) or lofts[0]'''

_V6_TAPERED_TAIL = '''    if not lofts:
        raise RuntimeError("tapered sweep loft failed")
    capped = lofts[0].CapPlanarHoles(TOLERANCE) or lofts[0]
    return ensure_outward(capped)'''

# Every metal part passes through ``ensure_outward`` regardless of how it was
# built, so correctness does not depend on which constructor was used.
_V5_BUILD_RETURN = '''    parts.append(("center_stone_round_brilliant", "stone", round_brilliant()))
    return parts'''

_V6_BUILD_RETURN = '''    parts = [
        (name, role, ensure_outward(geometry) if role == "metal" else geometry)
        for name, role, geometry in parts
    ]
    parts.append(("center_stone_round_brilliant", "stone", round_brilliant()))
    return parts'''

_V5_RUN_PARTS = '''    parts = build_parts()
    meshes = [(name, role, mesh_geometry(geometry)) for name, role, geometry in parts]'''

_V6_RUN_PARTS = '''    parts = build_parts()
    castable, castable_status = union_metal(
        [geometry for name, role, geometry in parts if role == "metal"]
    )
    castable_volume = None
    if castable is not None:
        try:
            castable_volume = castable.GetVolume()
        except Exception:
            castable_volume = None
    meshes = [(name, role, mesh_geometry(geometry)) for name, role, geometry in parts]'''

# ``castable_solid`` is a union of parts already in ``meshes``. It is added only
# AFTER the OBJ and 3DM are written, so the preview surfaces carry the named parts
# once rather than the parts plus a coincident copy of their union -- which would
# double the triangle count and z-fight. It still gets its own STL and receipt
# entry, because it is the thing a shop would actually cast.
_V5_WRITE_OBJ = '''    write_obj(master_obj, meshes)
    files = []'''

_V6_WRITE_OBJ = '''    write_obj(master_obj, meshes)
    if castable is not None:
        meshes.append(("castable_solid", "castable", mesh_geometry(castable)))
    files = []'''

_V5_RECEIPT = '''    receipt = {"schema": "myjewl.goal0014.parametric_master_receipt.v5",'''
_V6_RECEIPT = '''    receipt = {"schema": "myjewl.goal0018.castable_master_receipt.v6", "castable_union_status": castable_status, "castable_union_volume_mm3": castable_volume, "orientation_repairs": len(ORIENTATION_REPAIRS), "dimension_basis": "finished_nominal_no_compensation_applied", "stone_in_castable_solid": False,'''

_V5_RESPONSE = '''"source_preflight_ready": metrics["source_preflight_ready"],'''
_V6_RESPONSE = '''"source_preflight_ready": metrics["source_preflight_ready"], "castable_union_status": castable_status, "orientation_repairs": len(ORIENTATION_REPAIRS),'''


def _replace_once(source: str, old: str, new: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(
            "expected one v5 source fragment, found {}: {!r}".format(count, old[:90])
        )
    return source.replace(old, new, 1)


def _rewrite_source() -> str:
    source = V5_SCRIPT

    source = _replace_once(source, V5_TEMPLATE_ID, TEMPLATE_ID)
    source = _replace_once(
        source,
        'OUTPUT_ROOT = r"C:\\MyJewlGenerated\\goal0014_parametric_master"',
        'OUTPUT_ROOT = r"C:\\MyJewlGenerated\\goal0018_castable_master"',
    )
    # Counter for orientation repairs, declared beside the other module globals.
    source = _replace_once(source, "\nP = {}\n", "\nP = {}\nORIENTATION_REPAIRS = []\n")
    # Helpers go after ``pipe`` so ``rg`` and ``TOLERANCE`` are already bound.
    source = _replace_once(
        source, "\n\ndef tapered_sweep(points, radii):", _ORIENTATION_HELPERS
        + "\ndef tapered_sweep(points, radii):"
    )
    # Rhino.Compute reuses module state across solves in one session, so a batch
    # of three configurations would otherwise accumulate the repair count.
    source = _replace_once(
        source,
        "    P.clear()\n    P.update(derive_parameters(request))",
        "    P.clear()\n    del ORIENTATION_REPAIRS[:]\n"
        "    P.update(derive_parameters(request))",
    )
    source = _replace_once(source, _V5_TAPERED_TAIL, _V6_TAPERED_TAIL)
    source = _replace_once(source, _V5_BUILD_RETURN, _V6_BUILD_RETURN)
    source = _replace_once(source, _V5_RUN_PARTS, _V6_RUN_PARTS)
    source = _replace_once(source, _V5_WRITE_OBJ, _V6_WRITE_OBJ)
    source = _replace_once(source, _V5_RECEIPT, _V6_RECEIPT)
    source = _replace_once(source, _V5_RESPONSE, _V6_RESPONSE)
    source = _replace_once(source, "MyJewl Goal0014 ", "MyJewl Goal0018 ")
    source = _replace_once(
        source, "goal0014_parametric_master_source_ready",
        "goal0018_castable_master_source_ready",
    )
    source = _replace_once(
        source, "goal0014_parametric_master_source_failed",
        "goal0018_castable_master_source_failed",
    )
    for suffix in (".mtl", ".3dm", ".obj", "_receipt.json"):
        source = _replace_once(
            source, "solitaire_parametric_master_v5" + suffix,
            MASTER_FILE_STEM + suffix,
        )
    # The OBJ writer references the mtl by name in a second place.
    source = _replace_once(
        source, 'mtl_name = "solitaire_parametric_master_v5.mtl"',
        'mtl_name = "' + MASTER_FILE_STEM + '.mtl"',
    ) if 'mtl_name = "solitaire_parametric_master_v5.mtl"' in source else source

    if "ensure_outward" not in source or "union_metal" not in source:
        raise RuntimeError("v6 helpers did not land in the rewritten source")
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
        root / "manual" / "rhino_compute_bridge" / "goal0018_castable_master_v6.ghx"
    )
    print(built)
