"""Server-authoritative preview signatures, registry validation, and resolution.

The preview contract is deliberately narrower than the configurator contract.
Only normalized geometry selects an asset. Appearance, commercial data,
workflow state, browser delivery state, and manufacturing evidence never do.
Rhino/Grasshopper remains geometry authority; every manifest emitted here is
non-certifying.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from jewelry_gaas_foundation.validation import validate_job_spec


ROOT = Path(__file__).resolve().parents[1]
PREVIEW_MANIFEST_SCHEMA_PATH = ROOT / "schemas" / "preview_manifest_v0_1.schema.json"
PREVIEW_REGISTRY_SCHEMA_PATH = (
    ROOT / "schemas" / "preview_asset_registry_v0_1.schema.json"
)
DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent / "preview_assets" / "registry_v0_1.json"

MANIFEST_SCHEMA = "myjewl.preview_manifest.v0_1"
REGISTRY_SCHEMA = "myjewl.preview_asset_registry.v0_1"

#: Goal 0014: a parametric master may not be promoted on fewer configurations.
MINIMUM_DIFFERENTIAL_CONFIGS = 3
SIGNATURE_SCHEMA = "myjewl.preview_geometry_signature.v0_1"
RESOLVER_VERSION = "myjewl-preview-resolver-v0.1"
MANUFACTURING_STATE = "pending_not_run_needs_calibration"


class PreviewResolutionError(ValueError):
    """The canonical/configuration input cannot be normalized safely."""


class PreviewRegistryError(ValueError):
    """The preview registry is structurally or semantically unsafe."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreviewRegistryError(f"cannot read preview registry: {path}") from exc


def _schema(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(payload)
    return payload


def _validate_with_schema(payload: Any, path: Path, label: str) -> None:
    validator = Draft202012Validator(_schema(path))
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        location = "/" + "/".join(str(part) for part in first.path)
        raise PreviewRegistryError(f"invalid {label} at {location}: {first.message}")


def _canonical_number(value: int | float | Decimal) -> int | float:
    if isinstance(value, bool):
        raise PreviewResolutionError("booleans are not geometry numbers")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PreviewResolutionError(f"invalid geometry number: {value!r}") from exc
    if not number.is_finite():
        raise PreviewResolutionError("geometry numbers must be finite")
    if number == 0:
        return 0
    # Do not call Decimal.normalize(): it obeys the active decimal context and
    # can round inputs with more than 28 significant digits before we compare
    # them. Decimal equality already ignores insignificant trailing zeroes.
    if number == number.to_integral_value():
        return int(number)
    try:
        result = float(number)
    except (OverflowError, ValueError) as exc:
        raise PreviewResolutionError("geometry number is not losslessly representable") from exc
    if not math.isfinite(result):
        raise PreviewResolutionError("geometry numbers must be finite")
    # The manifest projection is ordinary JSON and therefore ultimately uses
    # binary64 numbers. Never silently round a higher-precision Decimal into a
    # registered geometry: either its shortest JSON spelling round-trips to the
    # same decimal value or resolution fails closed.
    if isinstance(value, Decimal) and Decimal(str(result)) != number:
        raise PreviewResolutionError("geometry number is not losslessly representable")
    return result


def _normalize_json(value: Any, *, reject_bool: bool = False) -> Any:
    if isinstance(value, bool):
        if reject_bool:
            raise PreviewResolutionError("booleans are not permitted in geometry")
        return value
    if isinstance(value, (int, float, Decimal)):
        return _canonical_number(value)
    if isinstance(value, str) or value is None:
        return value
    if isinstance(value, list):
        return [_normalize_json(item, reject_bool=reject_bool) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _normalize_json(value[key], reject_bool=reject_bool)
            for key in sorted(value)
        }
    raise PreviewResolutionError(f"unsupported JSON value: {type(value).__name__}")


def _stable_bytes(value: Any) -> bytes:
    normalized = _normalize_json(value)
    try:
        return json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PreviewResolutionError("value is not canonical JSON") from exc


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_stable_bytes(value)).hexdigest()


def _stone_projection(stone: dict[str, Any]) -> dict[str, Any]:
    dimensions = stone.get("dimensions_mm") or {}
    placement = stone.get("placement") or {}
    proportions = stone.get("proportions") or {}
    return _normalize_json(
        {
            "stone_id": stone.get("stone_id"),
            "role": stone.get("role"),
            "shape": stone.get("shape"),
            "quantity": stone.get("quantity", 1),
            "dimensions_mm": {
                "length": dimensions.get("length"),
                "width": dimensions.get("width"),
                "depth": dimensions.get("depth"),
            },
            "placement": {
                "region": placement.get("region", "center" if stone.get("role") == "center" else None),
                "pattern": placement.get("pattern", "single" if stone.get("role") == "center" else None),
                "orientation": placement.get("orientation", "upright" if stone.get("role") == "center" else None),
            },
            "proportions": {
                "girdle": proportions.get("girdle"),
                "crown_angle_deg": proportions.get("crown_angle_deg"),
                "pavilion_angle_deg": proportions.get("pavilion_angle_deg"),
            },
        },
        reject_bool=True,
    )


def normalize_geometry_projection(
    canonical_spec: dict[str, Any],
    flat_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the complete geometry-only projection used for preview lookup.

    The canonical schema rejects unknown nested fields before projection. The
    application style IDs come from the server-built flat record, never browser
    reference metadata. Missing style IDs remain explicit ``null`` values so
    older/non-manual runs receive a stable signature but cannot match an exact
    production asset.
    """

    if not isinstance(canonical_spec, dict):
        raise PreviewResolutionError("canonical_spec must be an object")
    validation = validate_job_spec(canonical_spec)
    if not validation.accepted:
        issue = next((item for item in validation.issues if item.severity == "error"), None)
        detail = f" at {issue.path}: {issue.message}" if issue else ""
        raise PreviewResolutionError("canonical spec is not schema-valid" + detail)
    flat = flat_parameters if isinstance(flat_parameters, dict) else {}
    ring = canonical_spec["ring"]
    ring_size = ring["size"]
    if isinstance(ring_size, str):
        try:
            numeric_string = Decimal(ring_size.strip())
        except InvalidOperation:
            numeric_string = None
        if numeric_string is not None:
            raise PreviewResolutionError(
                "numeric ring sizes must be JSON numbers, not strings"
            )
    setting = canonical_spec["setting"]
    pave = ring.get("pave") or {}
    stones = [_stone_projection(stone) for stone in canonical_spec["stones"]]
    stones.sort(key=lambda item: _stable_bytes(item))

    projection = {
        "architecture": canonical_spec["job"]["archetype"],
        "catalog_styles": {
            "head_style_id": flat.get("head_style_id"),
            "band_style_id": flat.get("band_style_id"),
        },
        "ring": {
            "size_standard": ring["size_standard"],
            "size": ring_size,
            "profile": ring.get("profile"),
            "band_profile": ring.get("band_profile"),
            "shoulder_style": ring.get("shoulder_style"),
            "band_width_mm": ring["band_width_mm"],
            "band_thickness_mm": ring["band_thickness_mm"],
            "pave": {
                "coverage": pave.get("coverage", "none"),
                "setting_style": pave.get("setting_style", "none"),
                "accent_stone_ids": sorted(pave.get("accent_stone_ids") or []),
            },
        },
        "setting": {
            "center_setting": setting["center_setting"],
            "prong_count": setting.get("prong_count", 0),
            "halo_style": setting.get("halo_style", "none"),
            "side_stone_layout": setting.get("side_stone_layout", "none"),
            "head_family": setting.get("head_family"),
            "head_geometry": setting.get("head_geometry"),
            "gallery_structure": setting.get("gallery_structure"),
            "side_setting": setting.get("side_setting", "none"),
        },
        "stones": stones,
    }
    return _normalize_json(projection, reject_bool=True)


def geometry_signature(
    canonical_spec: dict[str, Any],
    flat_parameters: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    projection = normalize_geometry_projection(canonical_spec, flat_parameters)
    return projection, _sha256(projection)


def appearance_signature(canonical_spec: dict[str, Any]) -> str | None:
    if not isinstance(canonical_spec, dict) or not isinstance(canonical_spec.get("metal"), dict):
        return None
    components = []
    for component in canonical_spec.get("components") or []:
        components.append(
            {
                "role": component.get("role"),
                "metal": component.get("metal"),
            }
        )
    components.sort(key=lambda item: _stable_bytes(item))
    return _sha256({"metal": canonical_spec["metal"], "components": components})


def _local_asset_url(value: str) -> bool:
    if not isinstance(value, str) or "\\" in value:
        return False
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return False
    path = PurePosixPath(parsed.path)
    return (
        parsed.path.startswith("/static/assets/")
        and ".." not in path.parts
        and "." not in path.parts
        and not parsed.path.endswith("/")
    )


def _require_exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise PreviewRegistryError(f"{label} must contain exactly {sorted(expected)}")
    return value


def _is_finite_registry_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _validate_projection_contract(projection: Any) -> None:
    top = _require_exact_keys(
        projection,
        {"architecture", "catalog_styles", "ring", "setting", "stones"},
        "geometry projection",
    )
    _require_exact_keys(
        top["catalog_styles"],
        {"head_style_id", "band_style_id"},
        "geometry projection catalog_styles",
    )
    ring = _require_exact_keys(
        top["ring"],
        {
            "size_standard", "size", "profile", "band_profile", "shoulder_style",
            "band_width_mm", "band_thickness_mm", "pave",
        },
        "geometry projection ring",
    )
    _require_exact_keys(
        ring["pave"],
        {"coverage", "setting_style", "accent_stone_ids"},
        "geometry projection ring.pave",
    )
    _require_exact_keys(
        top["setting"],
        {
            "center_setting", "prong_count", "halo_style", "side_stone_layout",
            "head_family", "head_geometry", "gallery_structure", "side_setting",
        },
        "geometry projection setting",
    )
    if not isinstance(top["stones"], list) or not top["stones"]:
        raise PreviewRegistryError("geometry projection stones must be a non-empty list")
    for index, stone in enumerate(top["stones"]):
        item = _require_exact_keys(
            stone,
            {
                "stone_id", "role", "shape", "quantity", "dimensions_mm",
                "placement", "proportions",
            },
            f"geometry projection stones[{index}]",
        )
        _require_exact_keys(
            item["dimensions_mm"],
            {"length", "width", "depth"},
            f"geometry projection stones[{index}].dimensions_mm",
        )
        _require_exact_keys(
            item["placement"],
            {"region", "pattern", "orientation"},
            f"geometry projection stones[{index}].placement",
        )
        _require_exact_keys(
            item["proportions"],
            {"girdle", "crown_angle_deg", "pavilion_angle_deg"},
            f"geometry projection stones[{index}].proportions",
        )
    try:
        normalized = _normalize_json(projection, reject_bool=True)
    except PreviewResolutionError as exc:
        raise PreviewRegistryError(str(exc)) from exc
    if normalized != projection:
        raise PreviewRegistryError("registry geometry projection is not canonically normalized")


def _digest_file(path_text: str) -> str:
    digest = hashlib.sha256()
    with Path(path_text).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _registered_file(file_ref: dict[str, Any]) -> Path:
    # URL authority is constrained to /static/assets above; map that route to
    # the Fast MVP's actual static root without trusting string concatenation.
    relative = PurePosixPath(file_ref["url"]).relative_to("/static")
    candidate = (ROOT / "fast_mvp" / "static" / Path(*relative.parts)).resolve()
    static_root = (ROOT / "fast_mvp" / "static" / "assets").resolve()
    if static_root not in candidate.parents or not candidate.is_file():
        raise PreviewRegistryError("registered preview file is missing or outside static assets")
    stat = candidate.stat()
    if stat.st_size != file_ref["byte_size"]:
        raise PreviewRegistryError("registered preview file byte size drifted")
    digest = _digest_file(str(candidate))
    if digest != file_ref["sha256"]:
        raise PreviewRegistryError("registered preview file hash drifted")
    return candidate


def _validate_promotion_provenance(asset: dict[str, Any]) -> None:
    """Enforce the Goal 0014 promotion policy on a registry entry.

    A definition that ignores the configuration request emits byte-identical
    geometry for every request it accepts, so it may back exactly one discrete
    geometry signature and must say so. A definition promoted as a parametric
    master must carry a passing receipt-backed config differential across at least
    three configurations. This is a provenance rule; it certifies nothing about
    manufacturing.
    """
    provenance = asset["provenance"]
    differential = asset["approval"]["config_differential"]
    kind = provenance["definition_kind"]

    if kind == "single_configuration_master":
        if differential["status"] != "absent":
            raise PreviewRegistryError(
                "a single-configuration master cannot claim a config differential"
            )
        if asset["match"]["policy"] != "discrete_signature":
            raise PreviewRegistryError(
                "a single-configuration master may only back one discrete signature"
            )
        return

    if differential["status"] != "passed":
        raise PreviewRegistryError(
            "a parametric master requires a passing config differential"
        )
    if not differential.get("report_path") or not differential.get("report_sha256"):
        raise PreviewRegistryError(
            "a passing config differential must reference a hashed report"
        )
    if int(differential.get("config_count", 0)) < MINIMUM_DIFFERENTIAL_CONFIGS:
        raise PreviewRegistryError(
            "a config differential must cover at least "
            f"{MINIMUM_DIFFERENTIAL_CONFIGS} configurations"
        )


def validate_registry(registry: dict[str, Any], *, production: bool = False) -> dict[str, Any]:
    """Validate registry schema plus ambiguity/security semantics."""

    if not isinstance(registry, dict):
        raise PreviewRegistryError("preview registry must be an object")
    _validate_with_schema(
        registry,
        PREVIEW_REGISTRY_SCHEMA_PATH,
        "preview asset registry",
    )
    if registry["resolver_version"] != RESOLVER_VERSION:
        raise PreviewRegistryError("preview registry resolver_version is incompatible")
    if registry["signature_schema"] != SIGNATURE_SCHEMA:
        raise PreviewRegistryError("preview registry signature_schema is incompatible")

    identities: set[tuple[str, str]] = set()
    enabled_exact = 0
    enabled_estimated = 0
    exact_signatures: set[str] = set()
    for asset in registry["assets"]:
        identity = (asset["asset_id"], asset["asset_version"])
        if identity in identities:
            raise PreviewRegistryError("duplicate preview asset id/version")
        identities.add(identity)
        for file_ref in (asset["delivery"]["manifest"], asset["delivery"]["content"]):
            if not _local_asset_url(file_ref["url"]):
                raise PreviewRegistryError("preview assets must use immutable local /static/assets URLs")
        manifest_path = _registered_file(asset["delivery"]["manifest"])
        _registered_file(asset["delivery"]["content"])
        _validate_projection_contract(asset["match"]["geometry_projection"])
        expected_signature = _sha256(asset["match"]["geometry_projection"])
        if expected_signature != asset["match"]["geometry_signature"]:
            raise PreviewRegistryError("registry projection does not match its geometry signature")
        range_paths = [item["path"] for item in asset["match"]["approved_ranges"]]
        if len(range_paths) != len(set(range_paths)):
            raise PreviewRegistryError("registry asset has duplicate approved range paths")
        for approved_range in asset["match"]["approved_ranges"]:
            if any(
                not _is_finite_registry_number(approved_range[key])
                for key in ("minimum", "maximum")
            ):
                raise PreviewRegistryError("registry approved ranges must be finite numbers")
            if approved_range["minimum"] > approved_range["maximum"]:
                raise PreviewRegistryError("registry approved range minimum exceeds maximum")
        transform = asset["geometry"]["transform"]
        if any(
            not _is_finite_registry_number(transform[key])
            for key in ("rotation_x_radians", "scale")
        ):
            raise PreviewRegistryError("registry transforms must be finite numbers")
        _validate_promotion_provenance(asset)
        approximation_paths = [item["path"] for item in asset["approximated_parameters"]]
        if asset["unsupported_parameters"]:
            raise PreviewRegistryError(
                "selectable preview assets cannot declare unsupported parameters"
            )
        leaf_paths = set(_leaf_paths(asset["match"]["geometry_projection"]))
        represented_paths = set(asset["represented_parameters"])
        if represented_paths & set(approximation_paths):
            raise PreviewRegistryError("represented and approximated parameter paths overlap")
        if represented_paths | set(approximation_paths) != leaf_paths:
            raise PreviewRegistryError("registry parameter declarations do not cover the geometry projection")
        if asset["classification"] == "exact":
            if asset["match"]["policy"] != "discrete_signature":
                raise PreviewRegistryError("exact assets require discrete_signature matching")
            if range_paths or approximation_paths:
                raise PreviewRegistryError("exact assets cannot declare approximations or ranges")
        else:
            if asset["match"]["policy"] != "bounded_projection":
                raise PreviewRegistryError("estimated assets require bounded_projection matching")
            if not range_paths or sorted(range_paths) != sorted(approximation_paths):
                raise PreviewRegistryError("estimated ranges and approximation declarations must agree")
            for approved_range in asset["match"]["approved_ranges"]:
                try:
                    baseline_value = _pointer_get(
                        asset["match"]["geometry_projection"],
                        approved_range["path"],
                    )
                except KeyError as exc:
                    raise PreviewRegistryError(
                        "estimated range path is absent from the baseline projection"
                    ) from exc
                if (
                    not _is_finite_registry_number(baseline_value)
                    or not approved_range["minimum"]
                    <= baseline_value
                    <= approved_range["maximum"]
                ):
                    raise PreviewRegistryError(
                        "estimated range baseline must be a finite number inside its range"
                    )
        if asset["enabled"] and asset["approval"]["status"] == "approved":
            if asset["approval"]["visual_review"] != "approved":
                raise PreviewRegistryError("enabled approved asset lacks approved visual review")
            if asset["classification"] == "exact":
                enabled_exact += 1
                signature = asset["match"]["geometry_signature"]
                if signature in exact_signatures:
                    raise PreviewRegistryError("ambiguous enabled exact geometry signature")
                exact_signatures.add(signature)
            else:
                enabled_estimated += 1
        if asset["delivery"]["format"] == "myjewl_meshbin_v1":
            try:
                delivery_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PreviewRegistryError("registered delivery manifest is malformed") from exc
            if not isinstance(delivery_manifest, dict):
                raise PreviewRegistryError("registered delivery manifest must be an object")
            binary = delivery_manifest.get("binary")
            if not isinstance(binary, dict):
                raise PreviewRegistryError(
                    "registered delivery manifest binary metadata must be an object"
                )
            geometry = asset["geometry"]
            provenance = asset["provenance"]
            parts = delivery_manifest.get("parts")
            manifest_transform = delivery_manifest.get("source_to_preview_transform")
            if (
                binary.get("sha256") != asset["delivery"]["content"]["sha256"]
                or binary.get("byte_size") != asset["delivery"]["content"]["byte_size"]
                or delivery_manifest.get("template_id") != provenance["template_id"]
                or delivery_manifest.get("source_receipt_sha256") != provenance["source_receipt_sha256"]
                or delivery_manifest.get("source_obj_sha256") != provenance["source_obj_sha256"]
                or delivery_manifest.get("vertex_count") != geometry["vertex_count"]
                or delivery_manifest.get("index_count") != geometry["index_count"]
                or delivery_manifest.get("triangle_count") != geometry["triangle_count"]
                or not isinstance(parts, list)
                or len(parts) != geometry["part_count"]
                or not isinstance(manifest_transform, dict)
                or manifest_transform.get("rotation_x_radians")
                != geometry["transform"]["rotation_x_radians"]
                or manifest_transform.get("scale") != geometry["transform"]["scale"]
            ):
                raise PreviewRegistryError("registry and delivery manifest metadata disagree")
            next_index = 0
            for part in parts:
                if (
                    not isinstance(part, dict)
                    or part.get("role") not in geometry["material_roles"]
                    or not isinstance(part.get("index_start"), int)
                    or isinstance(part.get("index_start"), bool)
                    or part.get("index_start") != next_index
                    or not isinstance(part.get("index_count"), int)
                    or isinstance(part.get("index_count"), bool)
                    or part.get("index_count") <= 0
                    or part.get("index_count") % 3 != 0
                    or not isinstance(part.get("triangles"), int)
                    or isinstance(part.get("triangles"), bool)
                    or part.get("triangles") != part.get("index_count") // 3
                ):
                    raise PreviewRegistryError(
                        "registered delivery manifest has invalid named-part ranges"
                    )
                next_index += part["index_count"]
            if next_index != geometry["index_count"]:
                raise PreviewRegistryError(
                    "registered delivery manifest named-part ranges are incomplete"
                )
    # Goal 0015: production may now serve more than one approved exact asset,
    # one per receipt-backed configuration. What must never change is that
    # production serves *no* estimated preview: an unsolved configuration is
    # reported unavailable, never approximated from a neighbouring solve. Exact
    # signature ambiguity is already rejected above, so distinct exact assets
    # cannot silently compete for the same request.
    if production and (enabled_exact < 1 or enabled_estimated != 0):
        raise PreviewRegistryError(
            "production registry must contain at least one approved exact asset and zero estimated assets"
        )
    return copy.deepcopy(registry)


def load_registry(
    path: Path = DEFAULT_REGISTRY_PATH,
    *,
    production: bool | None = None,
) -> dict[str, Any]:
    path = Path(path)
    if production is None:
        production = path.resolve() == DEFAULT_REGISTRY_PATH.resolve()
    return validate_registry(_read_json(path), production=production)


def _pointer_parts(path: str) -> list[str]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise PreviewRegistryError(f"invalid geometry pointer: {path!r}")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _pointer_get(payload: Any, path: str) -> Any:
    current = payload
    for part in _pointer_parts(path):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise KeyError(path) from exc
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(path)
    return current


def _leaf_paths(value: Any, path: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key in sorted(value):
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            paths.extend(_leaf_paths(value[key], f"{path}/{escaped}"))
        return paths
    if isinstance(value, list):
        paths = []
        for index, item in enumerate(value):
            paths.extend(_leaf_paths(item, f"{path}/{index}"))
        return paths or [path]
    return [path or "/value"]


def _bounded_match(
    projection: dict[str, Any],
    asset: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]]]:
    baseline = copy.deepcopy(asset["match"]["geometry_projection"])
    ranges = {item["path"]: item for item in asset["match"]["approved_ranges"]}
    if set(_leaf_paths(projection)) != set(_leaf_paths(baseline)):
        return False, []
    differences: list[dict[str, Any]] = []
    for path in _leaf_paths(projection):
        requested = _pointer_get(projection, path)
        represented = _pointer_get(baseline, path)
        if path in ranges:
            if isinstance(requested, bool) or not isinstance(requested, (int, float)):
                return False, []
            allowed = ranges[path]
            if not allowed["minimum"] <= requested <= allowed["maximum"]:
                return False, []
            declaration = next(
                item for item in asset["approximated_parameters"] if item["path"] == path
            )
            differences.append(
                {
                    "path": path,
                    "represented_value": represented,
                    "requested_value": requested,
                    "reason": declaration["reason"],
                }
            )
        elif requested != represented:
            return False, []
    return True, differences


def _selected_asset(asset: dict[str, Any]) -> dict[str, Any]:
    delivery = asset["delivery"]
    geometry = asset["geometry"]
    provenance = asset["provenance"]
    return {
        "asset_id": asset["asset_id"],
        "asset_version": asset["asset_version"],
        "asset_type": asset["asset_type"],
        "classification": asset["classification"],
        "format": delivery["format"],
        "renderer": delivery["renderer"],
        "delivery_manifest": copy.deepcopy(delivery["manifest"]),
        "content": copy.deepcopy(delivery["content"]),
        "template_id": provenance["template_id"],
        "source_receipt_sha256": provenance["source_receipt_sha256"],
        "vertex_count": geometry["vertex_count"],
        "index_count": geometry["index_count"],
        "triangle_count": geometry["triangle_count"],
        "part_count": geometry["part_count"],
    }


def _finalize_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    result["manifest_id"] = _sha256(result)
    _validate_with_schema(result, PREVIEW_MANIFEST_SCHEMA_PATH, "PreviewManifest")
    return result


def _base_manifest(
    canonical_spec: Any,
    projection: dict[str, Any],
    signature: str,
    registry_version: str,
) -> dict[str, Any]:
    try:
        canonical_hash = _sha256(canonical_spec)
    except PreviewResolutionError:
        canonical_hash = _sha256({"invalid_canonical_spec": True})
    try:
        appearance = appearance_signature(canonical_spec)
    except PreviewResolutionError:
        appearance = None
    return {
        "schema": MANIFEST_SCHEMA,
        "resolver_version": RESOLVER_VERSION,
        "registry_version": registry_version,
        "canonical_spec_sha256": canonical_hash,
        "geometry": {
            "signature_schema": SIGNATURE_SCHEMA,
            "signature": signature,
            "projection": projection,
        },
        "appearance_signature": appearance,
        "preview_status": "unavailable",
        "reason_code": "no_approved_preview_asset",
        "representation_kind": "none",
        "selected_asset": None,
        "represented_parameters": [],
        "approximated_parameters": [],
        "unsupported_parameters": [],
        "approved_ranges": [],
        "warnings": [],
        "manufacturing_authority": False,
        "manufacturing_state": MANUFACTURING_STATE,
    }


def resolve_preview(
    canonical_spec: dict[str, Any],
    flat_parameters: dict[str, Any] | None = None,
    *,
    registry: dict[str, Any] | None = None,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, Any]:
    """Resolve an accepted configuration to exact, estimated, or unavailable.

    Registry and input faults produce a deterministic unavailable manifest. A
    programming error in a manifest we construct still raises, keeping schema
    drift visible during tests rather than silently publishing a bad contract.
    """

    try:
        projection, signature = geometry_signature(canonical_spec, flat_parameters)
    except PreviewResolutionError as exc:
        projection = {}
        signature = _sha256(projection)
        manifest = _base_manifest(canonical_spec, projection, signature, "unavailable")
        manifest["reason_code"] = "invalid_canonical_spec"
        manifest["unsupported_parameters"] = [
            {
                "path": "/canonical_spec",
                "reason_code": "normalization_failed",
                "value": str(exc),
            }
        ]
        return _finalize_manifest(manifest)

    try:
        resolved_registry = (
            validate_registry(registry, production=False)
            if registry is not None
            else load_registry(registry_path)
        )
    except PreviewRegistryError as exc:
        manifest = _base_manifest(canonical_spec, projection, signature, "unavailable")
        manifest["reason_code"] = "registry_invalid"
        manifest["unsupported_parameters"] = [
            {
                "path": "/registry",
                "reason_code": "registry_invalid",
                "value": str(exc),
            }
        ]
        return _finalize_manifest(manifest)

    manifest = _base_manifest(
        canonical_spec,
        projection,
        signature,
        resolved_registry["registry_version"],
    )
    missing_styles = [
        path
        for path in (
            "/catalog_styles/head_style_id",
            "/catalog_styles/band_style_id",
        )
        if _pointer_get(projection, path) in (None, "")
    ]
    if missing_styles:
        manifest["reason_code"] = "unsupported_geometry_parameters"
        manifest["unsupported_parameters"] = [
            {"path": path, "reason_code": "required_style_id_missing"}
            for path in missing_styles
        ]
        return _finalize_manifest(manifest)

    matches: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for asset in resolved_registry["assets"]:
        if not asset["enabled"] or asset["approval"]["status"] != "approved":
            continue
        if asset["approval"]["visual_review"] != "approved":
            continue
        if asset["classification"] == "exact":
            if asset["match"]["geometry_signature"] == signature:
                matches.append((asset, []))
        else:
            matched, differences = _bounded_match(projection, asset)
            if matched:
                matches.append((asset, differences))

    if not matches:
        return _finalize_manifest(manifest)
    if len(matches) != 1:
        manifest["reason_code"] = "ambiguous_preview_asset"
        manifest["warnings"] = ["Multiple approved registry assets matched; resolution failed closed."]
        return _finalize_manifest(manifest)

    asset, differences = matches[0]
    status = asset["classification"]
    manifest.update(
        {
            "preview_status": status,
            "reason_code": "approved_exact_asset" if status == "exact" else "approved_estimated_asset",
            "representation_kind": asset["representation_kind"],
            "selected_asset": _selected_asset(asset),
            "represented_parameters": sorted(asset["represented_parameters"]),
            "approximated_parameters": differences,
            "unsupported_parameters": copy.deepcopy(asset["unsupported_parameters"]),
            "approved_ranges": copy.deepcopy(asset["match"]["approved_ranges"]),
            "warnings": copy.deepcopy(asset["limitations"]),
        }
    )
    return _finalize_manifest(manifest)


def attach_preview_manifest(report: dict[str, Any]) -> dict[str, Any]:
    """Attach one authoritative manifest to an accepted report in place."""

    if report.get("status") != "accepted":
        return report
    flat = report.get("flat_parameters")
    canonical = report.get("canonical_spec")
    if not isinstance(flat, dict) or not isinstance(canonical, dict):
        raise PreviewResolutionError("accepted report lacks flat/canonical parameters")
    manifest = resolve_preview(canonical, flat)
    report["preview_manifest"] = manifest
    report["preview_geometry_signature"] = manifest["geometry"]["signature"]
    preview = report.get("preview")
    if isinstance(preview, dict):
        preview["preview_manifest"] = manifest
    return report


def manifest_sha256(manifest: dict[str, Any]) -> str:
    """Hash the complete immutable PreviewManifest snapshot."""

    return _sha256(manifest)


def geometry_parameter_paths(projection: dict[str, Any]) -> list[str]:
    """Expose deterministic leaf paths for registry generation/tests."""

    return sorted(_leaf_paths(projection))


__all__ = [
    "DEFAULT_REGISTRY_PATH",
    "MANIFEST_SCHEMA",
    "MANUFACTURING_STATE",
    "PREVIEW_MANIFEST_SCHEMA_PATH",
    "PREVIEW_REGISTRY_SCHEMA_PATH",
    "PreviewRegistryError",
    "PreviewResolutionError",
    "REGISTRY_SCHEMA",
    "RESOLVER_VERSION",
    "SIGNATURE_SCHEMA",
    "appearance_signature",
    "attach_preview_manifest",
    "geometry_parameter_paths",
    "geometry_signature",
    "load_registry",
    "manifest_sha256",
    "normalize_geometry_projection",
    "resolve_preview",
    "validate_registry",
]
