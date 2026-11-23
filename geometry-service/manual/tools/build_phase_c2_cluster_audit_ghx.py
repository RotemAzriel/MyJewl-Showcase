#!/usr/bin/env python3
"""Build a headless GHX that inspects authoritative cluster internals.

This definition is diagnostic only. It loads the SHA-pinned authoritative core
inside Rhino.Compute and reports cluster input/output objects and sources; it
does not generate geometry or modify the source definition.
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from manual.tools.build_ghx import build_python3_ghx
except ModuleNotFoundError:
    from build_ghx import build_python3_ghx


SCRIPT = r'''import hashlib
import json
import os
import traceback


CORE_SHA256 = "33e0392357ec56c631ceafc6e5632f724ebe5e9ee9104d769481299314adc4b1"
CORE_PATH = r"C:\MyJewlDefinitions\authoritative_core_v1.gh"


def _text(value):
    try:
        return str(value)
    except Exception:
        return "<unprintable>"


def _type_name(value):
    try:
        return _text(value.GetType().FullName)
    except Exception:
        return _text(type(value))


def _guid(value):
    return _text(getattr(value, "InstanceGuid", ""))


def _param_info(param):
    info = {
        "name": _text(getattr(param, "Name", "")),
        "nickname": _text(getattr(param, "NickName", "")),
        "type": _type_name(param),
        "sources": [],
    }
    try:
        info["sources"] = [
            {"guid": _guid(source), "type": _type_name(source), "name": _text(getattr(source, "Name", ""))}
            for source in param.Sources
        ]
    except Exception:
        pass
    return info


def _object_info(obj):
    info = {
        "name": _text(getattr(obj, "Name", "")),
        "nickname": _text(getattr(obj, "NickName", "")),
        "type": _type_name(obj),
        "guid": _guid(obj),
        "inputs": [],
        "outputs": [],
    }
    try:
        info["inputs"] = [_param_info(param) for param in obj.Params.Input]
    except Exception:
        pass
    try:
        info["outputs"] = [_param_info(param) for param in obj.Params.Output]
    except Exception:
        pass
    if "Python3Component" in info["type"]:
        info["script_members"] = {}
        for name in dir(obj):
            if not any(token in name.lower() for token in ("script", "code", "source", "text")):
                continue
            try:
                value = getattr(obj, name)
                if callable(value):
                    continue
                text = _text(value)
                info["script_members"][name] = {
                    "type": _type_name(value),
                    "length": len(text),
                    "preview": text[:1200],
                }
            except Exception as exc:
                info["script_members"][name] = {"error": _text(exc)}
        try:
            info["script_reflection"] = {
                "properties": [
                    {
                        "name": _text(prop.Name),
                        "type": _text(prop.PropertyType.FullName),
                        "can_read": bool(prop.CanRead),
                        "can_write": bool(prop.CanWrite),
                    }
                    for prop in obj.GetType().GetProperties()
                    if any(token in _text(prop.Name).lower() for token in ("script", "code", "source", "text"))
                ],
                "methods": [
                    {
                        "name": _text(method.Name),
                        "parameters": [_text(param.ParameterType.FullName) for param in method.GetParameters()],
                        "return_type": _text(method.ReturnType.FullName),
                    }
                    for method in obj.GetType().GetMethods()
                    if any(token in _text(method.Name).lower() for token in ("script", "code", "source", "text"))
                ],
            }
        except Exception as exc:
            info["script_reflection"] = {"error": _text(exc)}
    return info


def _safe_member(obj, name):
    try:
        value = getattr(obj, name)
        if name == "Document" and callable(value):
            # GH_Cluster exposes its nested document through an indexed
            # Document member; Python.NET does not call that indexer with no
            # arguments.
            document_id = getattr(obj, "DocumentId", None)
            component_id = getattr(obj, "ClusterComponentId", None)
            arguments = (
                document_id,
                _text(document_id),
                component_id,
                _text(component_id),
                "",
                None,
            )
            for argument in arguments:
                try:
                    result = value(argument)
                    if result is not None:
                        return result
                except Exception:
                    pass
        if callable(value):
            value = value()
        return value
    except Exception as exc:
        return {"error": _text(exc)}


def _cluster_info(obj):
    info = _object_info(obj)
    info["member_types"] = {}
    try:
        safe_name = "".join(
            char if char.isalnum() or char in "._-" else "_"
            for char in _text(getattr(obj, "NickName", "cluster"))
        )
        cluster_path = r"C:\Temp\phase_c2_cluster_" + safe_name + ".ghcluster"
        info["cluster_file"] = {
            "path": cluster_path,
            "written": bool(obj.WriteGhClusterFile(cluster_path)),
        }
        try:
            nested_document = obj.Document(cluster_path)
            info["file_document"] = {
                "type": _type_name(nested_document),
                "objects": [_object_info(child) for child in nested_document.Objects],
            }
            try:
                import Grasshopper.Kernel as ghk
                nested_path = r"C:\Temp\phase_c2_nested_" + safe_name + ".ghx"
                nested_io = ghk.GH_DocumentIO(nested_document)
                info["file_document"]["ghx_path"] = nested_path
                info["file_document"]["ghx_saved"] = bool(nested_io.SaveQuiet(nested_path))
            except Exception as save_exc:
                info["file_document"]["ghx_save_error"] = _text(save_exc)
        except Exception as exc:
            info["file_document"] = {"error": _text(exc)}
    except Exception as exc:
        info["cluster_file"] = {"error": _text(exc)}
    info["available_members"] = sorted(
        name for name in dir(obj)
        if any(token in name.lower() for token in ("cluster", "document", "definition", "input", "output"))
    )
    try:
        info["reflected_properties"] = [
            {
                "name": _text(prop.Name),
                "index_parameters": [_text(param.ParameterType.FullName) for param in prop.GetIndexParameters()],
            }
            for prop in obj.GetType().GetProperties()
            if any(token in _text(prop.Name).lower() for token in ("cluster", "document", "definition", "input", "output"))
        ]
    except Exception as exc:
        info["reflected_properties_error"] = _text(exc)
    try:
        info["reflected_methods"] = [
            {
                "name": _text(method.Name),
                "parameters": [_text(param.ParameterType.FullName) for param in method.GetParameters()],
                "return_type": _text(method.ReturnType.FullName),
            }
            for method in obj.GetType().GetMethods()
            if any(token in _text(method.Name).lower() for token in ("cluster", "document", "definition", "input", "output"))
        ]
    except Exception as exc:
        info["reflected_methods_error"] = _text(exc)
    for name in (
        "Document", "ClusterDocument", "Definition", "SubDocument",
        "Inputs", "Outputs", "InputCount", "OutputCount", "ObjectCount",
    ):
        if name == "Document":
            try:
                document_member = getattr(obj, name)
                info["document_member_repr"] = _text(document_member)
                info["document_member_dir"] = sorted(
                    item for item in dir(document_member)
                    if any(token in item.lower() for token in ("overload", "document", "invoke", "call"))
                )
                info["document_member_overloads"] = _text(getattr(document_member, "__overloads__", "<none>"))
            except Exception as exc:
                info["document_member_error"] = _text(exc)
        if name in ("DocumentId", "ClusterComponentId"):
            try:
                info[name] = _text(getattr(obj, name))
            except Exception as exc:
                info[name + "_error"] = _text(exc)
        value = _safe_member(obj, name)
        if isinstance(value, dict):
            info["member_types"][name] = value
        elif isinstance(value, (str, int, float, bool)) or value is None:
            info["member_types"][name] = {"value": value}
        else:
            info["member_types"][name] = {"type": _type_name(value)}
            try:
                objects = list(value.Objects)
            except Exception:
                objects = None
            if objects is not None:
                info["member_types"][name]["objects"] = [_object_info(child) for child in objects]
    return info


def _run():
    import Grasshopper.Kernel as ghk
    import Grasshopper.Kernel.Special as ghs

    core_path = os.environ.get("MYJEWL_CORE_DEFINITION_PATH", CORE_PATH)
    digest = hashlib.sha256(open(core_path, "rb").read()).hexdigest()
    loader = ghk.GH_DocumentIO()
    if not loader.Open(core_path) or loader.Document is None:
        raise RuntimeError("authoritative core failed to open")
    document = loader.Document
    objects = [_object_info(obj) for obj in document.Objects]
    hook_instance = ghs.GH_ClusterInputHook()
    hook_type = hook_instance.GetType()
    hook_reflection = {
        "type": _type_name(hook_type),
        "constructors": [],
        "properties": [],
        "methods": [],
    }
    try:
        hook_reflection["constructors"] = [
            [_text(param.ParameterType.FullName) for param in ctor.GetParameters()]
            for ctor in hook_type.GetConstructors()
        ]
        hook_reflection["properties"] = [
            _text(prop.Name) for prop in hook_type.GetProperties()
        ]
        hook_reflection["methods"] = [
            {
                "name": _text(method.Name),
                "parameters": [_text(param.ParameterType.FullName) for param in method.GetParameters()],
                "return_type": _text(method.ReturnType.FullName),
            }
            for method in hook_type.GetMethods()
            if any(token in _text(method.Name).lower() for token in ("attribute", "document", "source", "input", "output", "hook"))
        ]
    except Exception as exc:
        hook_reflection["error"] = _text(exc)
    clusters = []
    for obj in document.Objects:
        type_name = _type_name(obj)
        label = (_text(getattr(obj, "Name", "")) + " " + _text(getattr(obj, "NickName", ""))).lower()
        if "cluster" in type_name.lower() or any(token.lower() in label for token in ("universalrail", "centerstoneproxy", "prongarray")):
            clusters.append(_cluster_info(obj))
    return {
        "schema_version": 1,
        "execution_status": "inspected",
        "reason_code": "authoritative_cluster_internal_audit",
        "core": {"path": core_path, "sha256": digest, "sha256_matches_expected": digest.lower() == CORE_SHA256},
        "objects": objects,
        "cluster_input_hook_reflection": hook_reflection,
        "clusters": clusters,
    }


try:
    response_json = json.dumps(_run(), sort_keys=True)
except Exception as exc:
    response_json = json.dumps({
        "schema_version": 1,
        "execution_status": "failed",
        "reason_code": "authoritative_cluster_internal_audit_failed",
        "error": _text(exc),
        "traceback": traceback.format_exc(),
    }, sort_keys=True)
'''


def build(path: str | Path, core_path: str = r"C:\MyJewlDefinitions\authoritative_core_v1.gh") -> Path:
    destination = Path(path)
    script = SCRIPT.replace(
        'CORE_PATH = r"C:\\MyJewlDefinitions\\authoritative_core_v1.gh"',
        'CORE_PATH = r"' + core_path.replace('"', '') + '"',
    )
    destination.write_bytes(
        build_python3_ghx(
            script,
            inputs=(
                ("RH_IN:json_string", "json_string", "item"),
                ("RH_IN:job_id", "job_id", "item"),
            ),
            outputs=(("RH_OUT:response_json", "response_json"),),
        )
    )
    return destination


if __name__ == "__main__":
    build(Path("manual/rhino_compute_bridge/phase_c2_cluster_audit_v1.ghx"))
