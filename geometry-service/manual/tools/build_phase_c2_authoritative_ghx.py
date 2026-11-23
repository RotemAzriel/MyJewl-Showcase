#!/usr/bin/env python3
from __future__ import annotations
"""Build the Phase C.2 native contract wrapper around the Rhino core model.

The wrapper is intentionally small: Resthopper supplies the stable
``RH_IN:json_string``/``RH_IN:job_id`` values, while the Python 3 component
loads and solves the separately versioned authoritative Grasshopper core on
the Azure Rhino VM.  It does not generate geometry itself.  The core path is
resolved from ``MYJEWL_CORE_DEFINITION_PATH`` on the VM and is never taken
from repository or client input.

The first response from this definition is inspect-only by design.  It
returns the core document's object/parameter inventory so the operator can
wire the existing model's real inputs and exporter outputs without guessing
or falling back to the old temporary panel path.
"""

from pathlib import Path

try:
    from manual.tools.build_ghx import build_python3_ghx
except ModuleNotFoundError:  # direct execution from manual/tools
    from build_ghx import build_python3_ghx


INSPECT_SCRIPT = r'''import hashlib
import json
import os
import traceback


def _text(value):
    try:
        return str(value)
    except Exception:
        return "<unprintable>"


def _guid(value):
    try:
        return str(value)
    except Exception:
        return ""


def _param_info(param):
    info = {
        "name": _text(getattr(param, "Name", "")),
        "nickname": _text(getattr(param, "NickName", "")),
        "type": _text(getattr(getattr(param, "GetType", lambda: None)(), "FullName", "")),
    }
    try:
        info["sources"] = [
            {
                "guid": _guid(getattr(source, "InstanceGuid", "")),
                "type": _text(getattr(getattr(source, "GetType", lambda: None)(), "FullName", "")),
            }
            for source in param.Sources
        ]
    except Exception:
        pass
    return info


def _object_info(obj):
    info = {
        "name": _text(getattr(obj, "Name", "")),
        "nickname": _text(getattr(obj, "NickName", "")),
        "type": _text(getattr(getattr(obj, "GetType", lambda: None)(), "FullName", "")),
        "inputs": [],
        "outputs": [],
    }
    try:
        info["inputs"] = [_param_info(item) for item in obj.Params.Input]
    except Exception:
        pass
    try:
        info["outputs"] = [_param_info(item) for item in obj.Params.Output]
    except Exception:
        pass
    if "GH_Panel" in info["type"]:
        panel_text = _text(getattr(obj, "UserText", ""))
        info["panel_properties"] = {
            name: _text(getattr(obj, name, ""))[:120]
            for name in ("UserText", "Prompt", "Message", "Description")
            if hasattr(obj, name)
        }
        info["guid"] = _guid(getattr(obj, "InstanceGuid", ""))
        info["panel_length"] = len(panel_text)
        info["panel_sha256"] = hashlib.sha256(panel_text.encode("utf-8")).hexdigest()
    return info


def _load_document(path):
    import Grasshopper.Kernel as ghk
    loader = ghk.GH_DocumentIO()
    if not loader.Open(path):
        raise RuntimeError("authoritative core GH document failed to open")
    document = loader.Document
    if document is None:
        raise RuntimeError("authoritative core GH document is empty")
    return document


def _run():
    core_path = os.environ.get(
        "MYJEWL_CORE_DEFINITION_PATH",
        r"C:\MyJewlDefinitions\authoritative_core_v1.gh",
    )
    if not os.path.isfile(core_path):
        raise RuntimeError("authoritative core definition is missing: " + core_path)
    core_hash = hashlib.sha256(open(core_path, "rb").read()).hexdigest()
    document = _load_document(core_path)
    objects = [_object_info(obj) for obj in document.Objects]
    panels = [item for item in objects if "GH_Panel" in item["type"]]
    scripts = [item for item in objects if "Python3Component" in item["type"]]
    links = []
    for target in document.Objects:
        try:
            for param in target.Params.Input:
                for source in param.Sources:
                    owner = getattr(getattr(source, "Attributes", None), "Owner", None)
                    if owner is None or "GH_Panel" not in _text(
                        getattr(getattr(owner, "GetType", lambda: None)(), "FullName", "")
                    ):
                        continue
                    links.append({
                        "panel_guid": _guid(getattr(owner, "InstanceGuid", "")),
                        "target_name": _text(getattr(target, "Name", "")),
                        "target_nickname": _text(getattr(target, "NickName", "")),
                        "target_param": _text(getattr(param, "Name", "")),
                    })
        except Exception:
            pass
    relevant = [
        item for item in objects
        if "GH_Panel" in item["type"]
        or any(token in (item["name"] + " " + item["nickname"]).lower()
               for token in ("script", "parser", "rail", "stone", "prong", "export"))
    ]
    return {
        "schema_version": 1,
        "execution_status": "inspected",
        "reason_code": "authoritative_core_inventory",
        "core": {
            "path": core_path,
            "sha256": core_hash,
            "object_count": len(objects),
            "panels": panels,
            "scripts": scripts,
            "panel_links": links,
        },
        "contract": {
            "json_input": "RH_IN:json_string",
            "job_id_input": "RH_IN:job_id",
            "response_output": "RH_OUT:response_json",
        },
        "panel_injection": {
            "status": "quarantined",
            "used": False,
            "reason": "C.2 inspect phase does not mutate core panels",
        },
    }


try:
    response_json = json.dumps(_run(), sort_keys=True)
except Exception as exc:
    response_json = json.dumps({
        "schema_version": 1,
        "execution_status": "failed",
        "reason_code": "authoritative_core_inventory_failed",
        "error": _text(exc),
        "traceback": traceback.format_exc(),
    }, sort_keys=True)
'''


EXECUTION_SCRIPT = r'''import base64
import datetime
import hashlib
import json
import os
import re
import traceback


CORE_SHA256 = "33e0392357ec56c631ceafc6e5632f724ebe5e9ee9104d769481299314adc4b1"
JSON_PANEL_GUID = "99faebc9-2130-4007-b4c1-673a84a7649c"
JOB_PANEL_GUID = "a9de4d77-bb53-4509-aa19-39ee32a22dc0"
REQUIRED_FILES = ("model.stl", "band.stl", "stone.stl", "prongs.stl")
PENDING_CHECKS = (
    "minimum_structural_floors",
    "stone_stone_collision",
    "stone_metal_collision",
    "mountability_and_setting_access",
    "mesh_closedness_and_manifold_suitability",
)


def _text(value):
    try:
        return str(value)
    except Exception:
        return "<unprintable>"


def _load_document(path):
    import Grasshopper.Kernel as ghk
    loader = ghk.GH_DocumentIO()
    if not loader.Open(path):
        raise RuntimeError("authoritative core GH document failed to open")
    document = loader.Document
    if document is None:
        raise RuntimeError("authoritative core GH document is empty")
    return document


def _set_panel(document, guid, value):
    target = str(guid).lower()
    for obj in document.Objects:
        if str(getattr(obj, "InstanceGuid", "")).lower() != target:
            continue
        if "GH_Panel" not in _text(getattr(getattr(obj, "GetType", lambda: None)(), "FullName", "")):
            raise RuntimeError("core input GUID is not a GH panel: " + guid)
        obj.UserText = value
        return obj
    raise RuntimeError("core input panel GUID is missing: " + guid)


def _find_exporter(document):
    expected = {"band_brep", "stone_brep", "prong_breps", "job_id"}
    matches = []
    for obj in document.Objects:
        try:
            names = {str(param.Name) for param in obj.Params.Input}
        except Exception:
            continue
        if expected.issubset(names):
            matches.append(obj)
    if len(matches) != 1:
        raise RuntimeError("expected one authoritative MeshExporter, found " + str(len(matches)))
    return matches[0]


def _first_data(param):
    try:
        for branch in param.VolatileData:
            for item in branch:
                value = getattr(item, "Value", item)
                if value is not None:
                    return _text(value)
    except Exception:
        pass
    try:
        for path in param.VolatileData.Paths:
            branch = param.VolatileData.get_Branch(path)
            for item in branch:
                value = getattr(item, "Value", item)
                if value is not None:
                    return _text(value)
    except Exception:
        pass
    return ""


def _port_diagnostics(component):
    values = []
    try:
        outputs = component.Params.Output
    except Exception:
        return values
    for index, param in enumerate(outputs):
        item = {
            "index": index,
            "name": _text(getattr(param, "Name", "")),
            "nickname": _text(getattr(param, "NickName", "")),
            "type": _text(getattr(getattr(param, "GetType", lambda: None)(), "FullName", "")),
        }
        for name in ("VolatileDataCount", "DataCount", "IsDataCollected"):
            try:
                item[name] = getattr(param, name)
            except Exception:
                pass
        try:
            item["volatile_data"] = _text(param.VolatileData)[:240]
        except Exception:
            pass
        values.append(item)
    return values


def _read_json(path):
    with open(path, "r") as handle:
        return json.load(handle)


def _find_receipt(root, job_id, started_at):
    job_root = os.path.join(root, job_id)
    if not os.path.isdir(job_root):
        raise RuntimeError("authoritative MeshExporter job directory is missing: " + job_root)
    candidates = []
    for current, _dirs, files in os.walk(job_root):
        if "export_receipt.json" not in files:
            continue
        receipt_path = os.path.join(current, "export_receipt.json")
        try:
            receipt = _read_json(receipt_path)
        except Exception:
            continue
        if receipt.get("job_id") != job_id:
            continue
        if receipt.get("gh_definition_sha256", "").lower() != CORE_SHA256:
            continue
        exported = str(receipt.get("exported_at_utc", ""))
        if exported and exported < started_at:
            continue
        if not all(name in receipt.get("files", {}) for name in REQUIRED_FILES):
            continue
        candidates.append((os.path.getmtime(receipt_path), receipt_path, receipt))
    if not candidates:
        raise RuntimeError("no fresh authoritative export receipt found for " + job_id)
    candidates.sort(key=lambda item: item[0], reverse=True)
    _mtime, receipt_path, receipt = candidates[0]
    return os.path.dirname(receipt_path), receipt


def _facts_and_bytes(export_dir, receipt):
    facts = {}
    encoded = {}
    for filename in REQUIRED_FILES:
        path = os.path.join(export_dir, filename)
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise RuntimeError("authoritative export file missing or empty: " + filename)
        with open(path, "rb") as handle:
            raw = handle.read()
        encoded[filename] = base64.b64encode(raw).decode("ascii")
        facts[filename] = receipt["files"][filename]
    return facts, encoded


def _run():
    spec = json.loads(json_string or "{}")
    job = _text(job_id).strip()
    if not re.match(r"^[A-Za-z0-9_.-]+$", job):
        raise RuntimeError("invalid job_id")
    core_path = os.environ.get("MYJEWL_CORE_DEFINITION_PATH", r"C:\MyJewlDefinitions\authoritative_core_v1.gh")
    if not os.path.isfile(core_path):
        raise RuntimeError("authoritative core definition is missing: " + core_path)
    with open(core_path, "rb") as handle:
        core_hash = hashlib.sha256(handle.read()).hexdigest()
    if core_hash.lower() != CORE_SHA256:
        raise RuntimeError("authoritative core SHA-256 mismatch: " + core_hash)
    started_at = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    document = _load_document(core_path)
    json_panel = _set_panel(document, JSON_PANEL_GUID, json.dumps(spec, sort_keys=True))
    job_panel = _set_panel(document, JOB_PANEL_GUID, job)
    exporter = _find_exporter(document)
    document.NewSolution(True)
    stl_path = _first_data(exporter.Params.Output[0])
    if not stl_path:
        raise RuntimeError(json.dumps({
            "message": "authoritative MeshExporter returned no stl_path",
            "solution_state": _text(getattr(document, "SolutionState", "")),
            "exporter_phase": _text(getattr(exporter, "Phase", "")),
            "exporter_runtime_message": _text(getattr(exporter, "RuntimeMessage", "")),
            "outputs": _port_diagnostics(exporter),
        }, sort_keys=True))
    output_root = os.environ.get("MYJEWL_NATIVE_OUTPUT_DIR", r"C:\MyJewlGenerated")
    export_dir, receipt = _find_receipt(output_root, job, started_at)
    facts, encoded = _facts_and_bytes(export_dir, receipt)
    return {
        "schema_version": 1,
        "execution_status": "completed",
        "reason_code": "authoritative_rhino_core_same_solve",
        "job_id": job,
        "assets": {"STL": {"path": stl_path}},
        "geometry_gates": [
            {"code": code, "status": "pending", "reason_code": "check_not_implemented", "details": {"calibrated": False}}
            for code in PENDING_CHECKS
        ],
        "debug": {
            "core_model": {
                "definition_path": core_path,
                "definition_sha256": core_hash,
                "definition_version": "solitaire_prototype_smoke_v0_rhino_render_authority_v1",
                "source": "loaded_and_solved_inside_Azure_Rhino_Grasshopper",
            },
            "core_input_bridge": {
                "status": "quarantined_compatibility_bridge",
                "json_panel_guid": JSON_PANEL_GUID,
                "job_id_panel_guid": JOB_PANEL_GUID,
                "legacy_external_panel_injection": "not_used",
            },
            "export_id": receipt.get("export_id"),
            "export_dir": export_dir,
            "files": facts,
            "files_base64": encoded,
            "receipt": receipt,
            "json_panel_sha256": hashlib.sha256(_text(getattr(json_panel, "UserText", "")).encode("utf-8")).hexdigest(),
            "job_id_panel_value": _text(getattr(job_panel, "UserText", "")),
        },
    }


try:
    response_json = json.dumps(_run(), sort_keys=True)
except Exception as exc:
    response_json = json.dumps({
        "schema_version": 1,
        "execution_status": "failed",
        "reason_code": "authoritative_rhino_core_failed",
        "error": _text(exc),
        "traceback": traceback.format_exc(),
    }, sort_keys=True)
'''


def build(path: str | Path) -> Path:
    destination = Path(path)
    destination.write_bytes(
        build_python3_ghx(
            EXECUTION_SCRIPT,
            inputs=(
                ("RH_IN:json_string", "json_string", "item"),
                ("RH_IN:job_id", "job_id", "item"),
            ),
            outputs=(("RH_OUT:response_json", "response_json"),),
        )
    )
    return destination


if __name__ == "__main__":
    build(Path("manual/rhino_compute_bridge/phase_c2_authoritative_rh_in_v1.ghx"))
