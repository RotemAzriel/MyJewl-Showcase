#!/usr/bin/env python3
"""Gate the promotion of a Rhino master definition into the production preview registry.

Goal 0014. This gate exists because the same failure class has now recurred three
times in this repository: the M4 Phase C pilot ("emitted identical geometry across
the five configs"), blocker G-6/R-10 (the silent no-op in ``flat_to_canonical``),
and the Goal 0011 V4 realism master, which parses ``json_string`` only to check
``template_id`` and then builds from a frozen, never-mutated parameter dict. A
validating no-op is more dangerous than an obvious one: it returns
``execution_status: "completed"`` with a real receipt and real SHA-256 hashes.

Two independent checks, both required:

A. **Parameter derivation** -- decode the definition's embedded Python, execute its
   parameter-derivation block in isolation (it is pure standard-library arithmetic,
   no Rhino), and probe it along every typed axis the master claims to accept. An
   axis is responsive only if changing it changes the derived geometry. This runs
   without Rhino and without spending anything.

B. **Config differential** -- a definition promoted as a parametric master must
   carry a receipt-backed config-differential produced by
   ``scripts/geometry_differential.py`` (hashes differing pairwise across >= 3
   configurations). ``scripts/goal0011_differential.py`` does **not** satisfy this:
   it compares two *definition versions* of one fixed configuration and
   structurally cannot detect config-invariance.

This gate is a correctness guard on preview provenance. It certifies nothing about
manufacturing, which remains pending / not_run / needs_calibration.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

BLOCK_BEGIN_MARKER = "# --- BEGIN MYJEWL MASTER PARAMETER DERIVATION v1 ---"
BLOCK_END_MARKER = "# --- END MYJEWL MASTER PARAMETER DERIVATION v1 ---"
MINIMUM_DIFFERENTIAL_CONFIGS = 3

_TEXT_ITEM = re.compile(
    r'<item name="Text" type_name="gh_string" type_code="10">(.*?)</item>', re.S
)

#: Baseline probe request. Each axis below perturbs exactly one typed value.
PROBE_BASE: dict[str, Any] = {
    "canonical_spec": {
        "ring": {
            "size": 6.5,
            "size_standard": "US",
            "band_width_mm": 2.05,
            "band_thickness_mm": 1.65,
        },
        "stones": [
            {
                "stone_id": "center",
                "role": "center",
                "shape": "round",
                "quantity": 1,
                "dimensions_mm": {"length": 6.5, "width": 6.5, "depth": 3.9},
            }
        ],
    }
}

#: (axis name, json-pointer-ish path, low value, high value).
PROBE_AXES: tuple[tuple[str, tuple[str, ...], float, float], ...] = (
    ("ring_size", ("ring", "size"), 4.0, 9.0),
    ("band_width", ("ring", "band_width_mm"), 1.6, 3.0),
    ("band_thickness", ("ring", "band_thickness_mm"), 1.2, 2.4),
    ("stone_diameter", ("stone", "length+width"), 5.0, 8.0),
    ("stone_depth", ("stone", "depth"), 0.58, 0.63),
)


class PromotionGateError(RuntimeError):
    """The definition or registry entry could not be evaluated at all."""


def extract_definition_source(ghx_path: Path) -> str:
    """Return the Python source embedded in a Grasshopper definition.

    The source is base64-encoded inside the ``.ghx``, so plain ``grep`` over the
    file reports zero matches for ``json.loads`` and is simply wrong. Decode first.
    """
    raw = Path(ghx_path).read_text(encoding="utf-8", errors="replace")
    candidates: list[str] = []
    for match in _TEXT_ITEM.finditer(raw):
        value = match.group(1).strip()
        if len(value) < 200:
            continue
        try:
            decoded = base64.b64decode(value, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        candidates.append(decoded)
    if not candidates:
        raise PromotionGateError(f"no embedded Python source found in {ghx_path}")
    return max(candidates, key=len)


def _set_axis(request: dict[str, Any], path: tuple[str, ...], value: float) -> None:
    spec = request["canonical_spec"]
    if path[0] == "ring":
        spec["ring"][path[1]] = value
        return
    dimensions = spec["stones"][0]["dimensions_mm"]
    if path[1] == "length+width":
        # Hold the depth ratio constant so this axis probes diameter alone and
        # stays inside any sane typed-input envelope.
        ratio = dimensions["depth"] / dimensions["length"]
        dimensions["length"] = value
        dimensions["width"] = value
        dimensions["depth"] = round(value * ratio, 4)
    elif path[1] == "depth":
        dimensions["depth"] = round(dimensions["length"] * value, 4)
    else:  # pragma: no cover - guarded by PROBE_AXES
        raise PromotionGateError(f"unknown probe axis path: {path}")


def _numeric_signature(parameters: dict[str, Any]) -> str:
    """Return a stable digest of every number in a derived parameter dict."""

    def normalize(value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return round(float(value), 9)
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in sorted(value.items())}
        return value

    payload = json.dumps(normalize(parameters), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def check_parameter_derivation(source: str) -> dict[str, Any]:
    """Check A: the definition derives its parameter dict from the request."""
    if BLOCK_BEGIN_MARKER not in source or BLOCK_END_MARKER not in source:
        return {
            "status": "fail",
            "reason_code": "no_parameter_derivation_block",
            "detail": (
                "the definition embeds no MYJEWL MASTER PARAMETER DERIVATION block, "
                "so its parameter dict cannot be a function of the request"
            ),
            "responsive_axes": [],
            "unresponsive_axes": [axis for axis, _, _, _ in PROBE_AXES],
        }

    begin = source.index(BLOCK_BEGIN_MARKER)
    end = source.index(BLOCK_END_MARKER) + len(BLOCK_END_MARKER)
    namespace: dict[str, Any] = {"__name__": "myjewl_promotion_gate_probe"}
    try:
        exec(compile(source[begin:end], "<derivation-block>", "exec"), namespace)
    except Exception as exc:  # noqa: BLE001 - any failure is a gate failure
        return {
            "status": "fail",
            "reason_code": "derivation_block_not_executable",
            "detail": f"{type(exc).__name__}: {exc}",
            "responsive_axes": [],
            "unresponsive_axes": [axis for axis, _, _, _ in PROBE_AXES],
        }

    derive = namespace.get("derive_parameters")
    if not callable(derive):
        return {
            "status": "fail",
            "reason_code": "derivation_block_exposes_no_derive_parameters",
            "detail": "the block does not define a callable derive_parameters(request)",
            "responsive_axes": [],
            "unresponsive_axes": [axis for axis, _, _, _ in PROBE_AXES],
        }

    responsive: list[str] = []
    unresponsive: list[str] = []
    details: dict[str, Any] = {}
    for axis, path, low, high in PROBE_AXES:
        low_request = copy.deepcopy(PROBE_BASE)
        high_request = copy.deepcopy(PROBE_BASE)
        _set_axis(low_request, path, low)
        _set_axis(high_request, path, high)
        try:
            low_signature = _numeric_signature(derive(low_request))
            high_signature = _numeric_signature(derive(high_request))
        except Exception as exc:  # noqa: BLE001 - a refusing axis is unresponsive
            unresponsive.append(axis)
            details[axis] = f"{type(exc).__name__}: {exc}"
            continue
        if low_signature == high_signature:
            unresponsive.append(axis)
            details[axis] = "identical derived parameters at both probe values"
        else:
            responsive.append(axis)
            details[axis] = "derived parameters change with the request"

    return {
        "status": "pass" if not unresponsive else "fail",
        "reason_code": (
            "parameters_derived_from_request"
            if not unresponsive
            else "parameters_ignore_typed_axes"
        ),
        "detail": details,
        "responsive_axes": responsive,
        "unresponsive_axes": unresponsive,
    }


def check_config_differential(asset: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    """Check B: a parametric master carries a passing receipt-backed config differential."""
    provenance = asset.get("provenance", {})
    approval = asset.get("approval", {})
    kind = provenance.get("definition_kind")
    record = approval.get("config_differential")

    if not isinstance(record, dict):
        return {
            "status": "fail",
            "reason_code": "no_config_differential_record",
            "detail": "the registry entry declares no approval.config_differential",
        }

    if kind == "single_configuration_master":
        if record.get("status") != "absent":
            return {
                "status": "fail",
                "reason_code": "single_configuration_master_claims_a_differential",
                "detail": "a non-parametric master cannot carry a passing config differential",
            }
        if asset.get("match", {}).get("policy") != "discrete_signature":
            return {
                "status": "fail",
                "reason_code": "single_configuration_master_is_not_signature_bound",
                "detail": (
                    "a master that ignores the request may only back one discrete "
                    "geometry signature, never a bounded projection"
                ),
            }
        return {
            "status": "pass",
            "reason_code": "single_configuration_master_bound_to_one_signature",
            "detail": record.get("reason", ""),
            "config_count": 0,
        }

    if kind != "parametric_master":
        return {
            "status": "fail",
            "reason_code": "unknown_definition_kind",
            "detail": f"provenance.definition_kind={kind!r}",
        }

    if record.get("status") != "passed":
        return {
            "status": "fail",
            "reason_code": "config_differential_not_passed",
            "detail": f"config_differential.status={record.get('status')!r}",
        }

    report_path = record.get("report_path")
    if not report_path:
        return {
            "status": "fail",
            "reason_code": "config_differential_has_no_report",
            "detail": "config_differential.report_path is missing",
        }
    resolved = (root / str(report_path)).resolve()
    if not resolved.is_file():
        return {
            "status": "fail",
            "reason_code": "config_differential_report_missing_on_disk",
            "detail": str(report_path),
        }

    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if digest != record.get("report_sha256"):
        return {
            "status": "fail",
            "reason_code": "config_differential_report_hash_mismatch",
            "detail": f"on disk {digest}, registry {record.get('report_sha256')}",
        }

    try:
        report = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "fail",
            "reason_code": "config_differential_report_unreadable",
            "detail": str(exc),
        }

    config_count = int(report.get("config_count", 0))
    if report.get("status") != "pass":
        return {
            "status": "fail",
            "reason_code": "config_differential_report_did_not_pass",
            "detail": f"report status={report.get('status')!r}",
        }
    if config_count < MINIMUM_DIFFERENTIAL_CONFIGS:
        return {
            "status": "fail",
            "reason_code": "config_differential_covers_too_few_configs",
            "detail": f"{config_count} < {MINIMUM_DIFFERENTIAL_CONFIGS}",
        }
    pairs = report.get("pairs") or []
    if not pairs or not all(pair.get("changed_files") for pair in pairs):
        return {
            "status": "fail",
            "reason_code": "config_differential_has_unchanged_pairs",
            "detail": "at least one config pair produced identical geometry",
        }

    return {
        "status": "pass",
        "reason_code": "config_differential_passed",
        "detail": f"{config_count} configs, {len(pairs)} pairs all differing",
        "config_count": config_count,
    }


def evaluate(
    definition_path: Path,
    asset: dict[str, Any] | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Run both promotion checks and return a combined verdict."""
    source = extract_definition_source(definition_path)
    derivation = check_parameter_derivation(source)
    checks: dict[str, Any] = {"parameter_derivation": derivation}
    if asset is not None:
        checks["config_differential"] = check_config_differential(asset, root=root)
    status = "pass" if all(item["status"] == "pass" for item in checks.values()) else "fail"
    return {
        "schema": "myjewl.promotion_gate.v1",
        "definition": str(Path(definition_path).relative_to(root))
        if Path(definition_path).is_relative_to(root)
        else str(definition_path),
        "definition_sha256": hashlib.sha256(
            Path(definition_path).read_bytes()
        ).hexdigest(),
        "asset_id": (asset or {}).get("asset_id"),
        "status": status,
        "checks": checks,
        "manufacturing_authority": False,
        "manufacturing_state": "pending_not_run_needs_calibration",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("definition", type=Path, help="path to the .ghx definition")
    parser.add_argument(
        "--registry",
        type=Path,
        help="preview asset registry providing the entry to gate",
    )
    parser.add_argument("--asset-id", help="registry asset_id to gate")
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(argv)

    asset = None
    if args.registry:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
        assets = registry.get("assets", [])
        if args.asset_id:
            assets = [item for item in assets if item.get("asset_id") == args.asset_id]
        if len(assets) != 1:
            raise SystemExit(
                f"expected exactly one registry asset to gate, found {len(assets)}"
            )
        asset = assets[0]

    report = evaluate(args.definition, asset)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
