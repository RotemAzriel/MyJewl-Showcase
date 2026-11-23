#!/usr/bin/env python3
"""Patch the saved GHX MeshExporter provenance and geometry-list guards."""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
from pathlib import Path


PROVENANCE_FUNCTION = """def _definition_info():
    # Compute can mark a posted GH document Modified even when it is the exact
    # saved definition supplied by the operator. Prefer the self-contained
    # pinned VM path, then fall back to machine/user provenance.
    pinned_path = r"__PINNED_PATH__"
    pinned_version = "__PINNED_VERSION__"

    if pinned_path and os.path.isfile(pinned_path):
        return (pinned_path, _sha256_file(pinned_path), pinned_version)

    def _vm_value(name):
        value = str(os.environ.get(name, \"\")).strip()
        if value:
            return value
        try:
            import System
            return str(System.Environment.GetEnvironmentVariable(
                name, System.EnvironmentVariableTarget.Machine
            ) or \"\").strip()
        except Exception:
            return \"\"

    fallback_path = _vm_value(\"MYJEWL_GH_DEFINITION_PATH\")
    fallback_hash = _vm_value(\"MYJEWL_GH_DEFINITION_SHA256\").lower()
    fallback_version = _vm_value(\"MYJEWL_GH_DEFINITION_VERSION\") or \"phase_c2_authoritative_core_rh_in_v2_provenance\"
    if fallback_path and os.path.isfile(fallback_path):
        return (fallback_path, fallback_hash or _sha256_file(fallback_path), fallback_version)

    definition_document = ghenv.Component.OnPingDocument()
    definition_path = \"\"
    if definition_document is not None:
        try:
            definition_path = str(definition_document.FilePath or \"\").strip()
        except Exception:
            definition_path = \"\"
    if definition_path and os.path.isfile(definition_path):
        return (definition_path, _sha256_file(definition_path), os.path.splitext(os.path.basename(definition_path))[0])
    if len(fallback_hash) == 64 and all(char in \"0123456789abcdef\" for char in fallback_hash):
        return (\"embedded:\" + fallback_version, fallback_hash, fallback_version)
    raise ValueError(\"Save the candidate Grasshopper definition before exporting\")
"""


BREP_READER_FUNCTION = """def get_breps_from_param(param_index):
    \"\"\"Extract Breps recursively from a GH input tree.

    Flattened native Python components can deliver a list as one volatile-data
    item.  The authoritative exporter accepts both that shape and the normal
    direct Brep/GH_Brep shapes without changing the geometry contract.
    \"\"\"
    param = ghenv.Component.Params.Input[param_index]
    vdata = param.VolatileData
    breps = []

    def append_value(value):
        if value is None:
            return
        if isinstance(value, rg.Brep):
            breps.append(value)
            return
        if hasattr(value, \"Value\") and value.Value is not None:
            append_value(value.Value)
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                append_value(child)
            return
        try:
            converted = rg.Brep.TryConvertBrep(value)
        except Exception:
            converted = None
        if converted:
            breps.append(converted)

    for path in vdata.Paths:
        for item in vdata.get_Branch(path):
            append_value(item)
    return breps
"""


def _replace_source(source: str, definition_path: str, definition_version: str) -> tuple[str, bool]:
    start = source.find("def _definition_info():")
    if start < 0:
        return source, False
    end = source.find("\ndef _point_tuple", start)
    if end < 0:
        raise ValueError("MeshExporter provenance function terminator not found")
    replacement = PROVENANCE_FUNCTION.replace("__PINNED_PATH__", definition_path.replace('"', '')).replace(
        "__PINNED_VERSION__", definition_version.replace('"', '')
    )
    return source[:start] + replacement.rstrip("\n") + source[end:], True


def _replace_brep_reader(source: str) -> tuple[str, bool]:
    start = source.find("def get_breps_from_param(param_index):")
    if start < 0:
        return source, False
    end = source.find("\ndef _sha256_file", start)
    if end < 0:
        raise ValueError("MeshExporter Brep-reader terminator not found")
    return source[:start] + BREP_READER_FUNCTION.rstrip("\n") + source[end:], True


def patch(
    input_path: Path,
    output_path: Path,
    definition_path: str = "",
    definition_version: str = "",
) -> dict[str, object]:
    text = input_path.read_text(encoding="utf-8")
    pattern = re.compile(r'(<item name="Text"[^>]*>)([^<]*)(</item>)')
    patched = 0
    brep_reader_patched = 0
    candidates = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal patched, candidates, brep_reader_patched
        try:
            source = base64.b64decode(match.group(2)).decode("utf-8")
        except Exception:
            return match.group(0)
        if "def _definition_info():" not in source:
            return match.group(0)
        candidates += 1
        source, brep_changed = _replace_brep_reader(source)
        source, provenance_changed = _replace_source(source, definition_path, definition_version)
        if not brep_changed or not provenance_changed:
            return match.group(0)
        patched += 1
        brep_reader_patched += 1
        encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
        return match.group(1) + encoded + match.group(3)

    output_text = pattern.sub(replace, text)
    if patched != 1:
        raise ValueError(f"expected exactly one MeshExporter source patch, found {patched} candidates={candidates}")
    output_path.write_text(output_text, encoding="utf-8")
    return {
        "input": str(input_path),
        "output": str(output_path),
        "patched_sources": patched,
        "brep_reader_patched": brep_reader_patched,
        "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "output_sha256": hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
        "pinned_definition_path": definition_path,
        "pinned_definition_version": definition_version,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--definition-path", default="")
    parser.add_argument("--definition-version", default="")
    args = parser.parse_args()
    print(patch(args.input, args.output, args.definition_path, args.definition_version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
