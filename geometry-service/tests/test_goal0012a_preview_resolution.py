"""Goal 0012A authoritative preview-resolution and approval contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from fast_mvp import manual_configurator, preview_resolution, server, workflow
from fast_mvp.pipeline import analyze_photo_request
from fast_mvp.typed_inputs import parse_measurements
from fast_mvp.preview_resolution import (
    MANUFACTURING_STATE,
    PREVIEW_MANIFEST_SCHEMA_PATH,
    PREVIEW_REGISTRY_SCHEMA_PATH,
    PreviewRegistryError,
    PreviewResolutionError,
    appearance_signature,
    geometry_parameter_paths,
    geometry_signature,
    load_registry,
    normalize_geometry_projection,
    resolve_preview,
    validate_registry,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "fast_mvp" / "preview_assets" / "registry_v0_1.json"
RING3D_PATH = ROOT / "fast_mvp" / "static" / "ring3d.js"

V4_HEAD_ID = "head_v4_mid_petal_4_prong"
V4_BAND_ID = "band_v4_split_solitaire"
V4_ASSET_ID = "goal0011-v4-full-ring"
V5_HEAD_ID = "head_v5_parametric_petal_4_prong"
V5_BAND_ID = "band_v5_parametric_split_solitaire"
V4_GEOMETRY_SIGNATURE = (
    "sha256:e4b84e9fb4797d7b0910d9a9ed810ba6dffe410961f255c43c434e3c58d2042c"
)
V4_DELIVERY_MANIFEST_SHA256 = (
    "7928348f4d4e979623b62cf3498be116a9f5667a2ab918bdd08c8e593140e85d"
)
V4_CONTENT_SHA256 = (
    "2ce64abee441f5649c5a655011bf221060d1bf9f643b19d64aa6e5999be6314c"
)

SHAPE_DIMENSIONS = {
    "round": (6.5, 6.5, 3.9),
    "oval": (8.0, 6.0, 3.8),
    "cushion": (6.5, 6.5, 4.2),
    "pear": (8.5, 5.5, 3.7),
    "emerald": (7.0, 5.0, 3.5),
    "princess": (5.5, 5.5, 4.0),
    "radiant": (6.5, 5.5, 3.8),
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def projection_signature(projection: dict) -> str:
    body = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def local_asset_path(url: str) -> Path:
    if not url.startswith("/static/assets/"):
        raise AssertionError(url)
    return ROOT / "fast_mvp" / url.removeprefix("/")


def base_payload(shape: str = "round") -> dict:
    length, width, depth = SHAPE_DIMENSIONS[shape]
    return {
        "stone_shape": shape,
        "stone_length_mm": length,
        "stone_width_mm": width,
        "stone_depth_mm": depth,
        "stone_carat_eq": 1.0,
        "ring_size_us": 6.5,
        "metal": {
            "color_family": "yellow",
            "purity": "14k",
            "finish": "polished",
        },
    }


def v4_payload(**overrides) -> dict:
    payload = {
        **base_payload(),
        "head_style_id": V4_HEAD_ID,
        "band_style_id": V4_BAND_ID,
    }
    payload.update(overrides)
    return payload


def v4_report(**overrides) -> dict:
    return manual_configurator.configure(v4_payload(**overrides))


def recursively_reverse_keys(value):
    if isinstance(value, dict):
        return {
            key: recursively_reverse_keys(value[key])
            for key in reversed(list(value))
        }
    if isinstance(value, list):
        return [recursively_reverse_keys(item) for item in value]
    return value


def manifest_validator() -> Draft202012Validator:
    return Draft202012Validator(read_json(PREVIEW_MANIFEST_SCHEMA_PATH))


def estimated_registry() -> dict:
    """Return a non-production, bounded test registry backed by the V4 bytes."""

    registry = read_json(REGISTRY_PATH)
    asset = registry["assets"][0]
    ranged_path = "/ring/size"
    asset["asset_id"] = "test-only-v4-ring-size-estimate"
    asset["asset_version"] = "test-v1"
    asset["classification"] = "estimated"
    asset["representation_kind"] = "approved_assembly"
    asset["match"]["policy"] = "bounded_projection"
    asset["match"]["approved_ranges"] = [
        {"path": ranged_path, "minimum": 6.0, "maximum": 7.0}
    ]
    asset["represented_parameters"].remove(ranged_path)
    asset["approximated_parameters"] = [
        {
            "path": ranged_path,
            "reason": "Test-only bounded ring-size representation; not production data.",
        }
    ]
    # Goal 0014 promotion policy: a bounded projection spans configurations, so it
    # may only be backed by a definition proven to respond to the request. A
    # single-configuration master bound to a bounded range is exactly the failure
    # this policy exists to stop, so this test-only asset declares a parametric
    # master with a synthetic passing differential.
    asset["provenance"]["definition_kind"] = "parametric_master"
    asset["approval"]["config_differential"] = {
        "status": "passed",
        "reason": "Test-only synthetic differential; not production evidence.",
        "report_path": "output/test_only/config_differential.json",
        "report_sha256": "f" * 64,
        "config_count": 3,
    }
    return registry


def approval_request(
    job_id: str,
    manifest: dict,
    *,
    decision: str = "approved",
) -> dict:
    selected = manifest["selected_asset"]
    if selected is None:
        delivery = {
            "status": "unavailable",
            "displayed_status": "unavailable",
            "renderer": "none",
            "reason_code": manifest["reason_code"],
            "asset_id": None,
            "asset_version": None,
            "content_sha256": None,
        }
    elif decision == "approved":
        delivery = {
            "status": "ready",
            "displayed_status": manifest["preview_status"],
            "renderer": selected["renderer"],
            "reason_code": manifest["reason_code"],
            "asset_id": selected["asset_id"],
            "asset_version": selected["asset_version"],
            "content_sha256": selected["content"]["sha256"],
        }
    else:
        delivery = {
            "status": "failure",
            "displayed_status": "unavailable",
            "renderer": selected["renderer"],
            "reason_code": "asset_hash_mismatch",
            "asset_id": selected["asset_id"],
            "asset_version": selected["asset_version"],
            "content_sha256": selected["content"]["sha256"],
        }
    return {
        "job_id": job_id,
        "manifest_id": manifest["manifest_id"],
        "decision": decision,
        "delivery": delivery,
    }


class PreviewSchemaAndProductionRegistryTests(unittest.TestCase):
    def test_both_goal0012a_schemas_are_valid_draft_2020_12_contracts(self):
        for path in (PREVIEW_MANIFEST_SCHEMA_PATH, PREVIEW_REGISTRY_SCHEMA_PATH):
            with self.subTest(path=path.name):
                schema = read_json(path)
                self.assertEqual(
                    schema["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertFalse(schema["additionalProperties"])
                Draft202012Validator.check_schema(schema)

    def test_production_registry_contains_only_approved_exact_assets(self):
        # Goal 0015 promoted three receipt-backed V5 configurations beside V4.
        # The invariant that survives is not the count: production must serve no
        # estimated preview at all, and every asset it does serve must be an
        # approved, enabled, exact, signature-bound full ring.
        raw = read_json(REGISTRY_PATH)
        registry = load_registry(REGISTRY_PATH, production=True)
        self.assertEqual(raw, registry)
        self.assertEqual(
            sum(item["classification"] == "estimated" for item in registry["assets"]),
            0,
        )
        self.assertIn(V4_ASSET_ID, {item["asset_id"] for item in registry["assets"]})
        for asset in registry["assets"]:
            with self.subTest(asset=asset["asset_id"]):
                self.assertEqual(asset["classification"], "exact")
                self.assertEqual(asset["match"]["policy"], "discrete_signature")
                self.assertEqual(asset["approval"]["status"], "approved")
                self.assertEqual(asset["approval"]["visual_review"], "approved")
                self.assertTrue(asset["enabled"])
                self.assertEqual(asset["asset_type"], "full_ring")
                self.assertEqual(asset["representation_kind"], "full_ring_asset")
                self.assertFalse(asset["geometry"]["named_parts_are_modules"])
                self.assertFalse(asset["manufacturing_authority"])
                self.assertEqual(asset["manufacturing_state"], MANUFACTURING_STATE)

    def test_registry_pins_real_local_delivery_bytes_and_receipt_metadata(self):
        asset = load_registry()["assets"][0]
        manifest_ref = asset["delivery"]["manifest"]
        content_ref = asset["delivery"]["content"]
        manifest_path = local_asset_path(manifest_ref["url"])
        content_path = local_asset_path(content_ref["url"])
        self.assertEqual(manifest_ref["sha256"], V4_DELIVERY_MANIFEST_SHA256)
        self.assertEqual(content_ref["sha256"], V4_CONTENT_SHA256)
        for reference, path in ((manifest_ref, manifest_path), (content_ref, content_path)):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_size, reference["byte_size"])
                self.assertEqual(sha256_file(path), reference["sha256"])

        delivery = read_json(manifest_path)
        self.assertEqual(delivery["binary"]["sha256"], V4_CONTENT_SHA256)
        self.assertEqual(delivery["binary"]["byte_size"], content_path.stat().st_size)
        self.assertEqual(delivery["source_receipt_sha256"], asset["provenance"]["source_receipt_sha256"])
        self.assertEqual(delivery["vertex_count"], 78281)
        self.assertEqual(delivery["index_count"], 447192)
        self.assertEqual(delivery["triangle_count"], 149064)
        self.assertEqual(len(delivery["parts"]), 20)

    def test_v4_registry_projection_is_complete_and_signature_is_recomputed(self):
        report = v4_report()
        projection, signature = geometry_signature(
            report["canonical_spec"], report["flat_parameters"]
        )
        asset = load_registry()["assets"][0]
        self.assertEqual(projection, asset["match"]["geometry_projection"])
        self.assertEqual(signature, V4_GEOMETRY_SIGNATURE)
        self.assertEqual(asset["match"]["geometry_signature"], signature)
        self.assertEqual(
            geometry_parameter_paths(projection),
            sorted(asset["represented_parameters"]),
        )
        serialized = json.dumps(projection, sort_keys=True, separators=(",", ":"))
        for excluded in (
            "metal",
            "purity",
            "alloy",
            "finish",
            "carat",
            "certificate",
            "catalog_revision",
            "reference_geometry_id",
            "geometry_identity",
        ):
            self.assertNotIn(excluded, serialized)

    def test_manifest_schema_enforces_status_asset_and_non_certifying_invariants(self):
        exact = v4_report()["preview_manifest"]
        other = manual_configurator.configure(
            {
                **base_payload(),
                "head_style_id": "head_classic_4_prong",
                "band_style_id": "band_classic_solitaire",
            }
        )["preview_manifest"]
        validator = manifest_validator()
        self.assertTrue(validator.is_valid(exact))
        self.assertTrue(validator.is_valid(other))

        mutations = []
        invalid = copy.deepcopy(exact)
        invalid["manufacturing_authority"] = True
        mutations.append(invalid)
        invalid = copy.deepcopy(exact)
        invalid["selected_asset"]["classification"] = "estimated"
        mutations.append(invalid)
        invalid = copy.deepcopy(exact)
        invalid["extra_authority"] = "browser"
        mutations.append(invalid)
        invalid = copy.deepcopy(other)
        invalid["selected_asset"] = copy.deepcopy(exact["selected_asset"])
        mutations.append(invalid)
        invalid = copy.deepcopy(other)
        invalid["represented_parameters"] = ["/ring/size"]
        mutations.append(invalid)
        for index, payload in enumerate(mutations):
            with self.subTest(index=index):
                self.assertFalse(validator.is_valid(payload))


class ServerAuthoritativeGeometrySignatureTests(unittest.TestCase):
    def setUp(self):
        report = v4_report()
        self.canonical = report["canonical_spec"]
        self.flat = report["flat_parameters"]
        self.projection, self.signature = geometry_signature(self.canonical, self.flat)

    def test_all_allowed_v4_appearance_variants_keep_the_exact_geometry_signature(self):
        variants = [
            (color, purity, finish)
            for purity in ("14k", "18k")
            for color in ("yellow", "white", "rose")
            for finish in ("polished", "matte", "brushed")
        ] + [
            ("white", "pt950", finish)
            for finish in ("polished", "matte", "brushed")
        ]
        appearances = set()
        for color, purity, finish in variants:
            with self.subTest(color=color, purity=purity, finish=finish):
                report = v4_report(
                    metal={
                        "color_family": color,
                        "purity": purity,
                        "finish": finish,
                    }
                )
                manifest = report["preview_manifest"]
                self.assertEqual(manifest["preview_status"], "exact")
                self.assertEqual(manifest["geometry"]["signature"], self.signature)
                appearances.add(manifest["appearance_signature"])
        self.assertEqual(len(variants), 21)
        self.assertEqual(len(appearances), 21)

    def test_material_components_commercial_and_job_fields_cannot_select_geometry(self):
        changed = copy.deepcopy(self.canonical)
        changed["metal"] = {
            "type": "platinum",
            "alloy": "pt950 white",
            "purity": "pt950",
            "finish": "brushed",
        }
        for component in changed["components"]:
            component["metal"] = copy.deepcopy(changed["metal"])
        changed["stones"][0]["carat"] = 1.27
        changed["stones"][0]["cert"] = {
            "lab": "GIA",
            "report_number": "GOAL0012A-NOT-GEOMETRY",
        }
        changed["job"]["job_id"] = "different-operational-id"
        changed["job"]["created_at"] = "2026-08-01T00:00:00Z"
        changed["job"]["dealer_id"] = "not-geometry"
        changed["job"]["output_targets"] = ["GLB"]
        projection, signature = geometry_signature(changed, self.flat)
        self.assertEqual(projection, self.projection)
        self.assertEqual(signature, self.signature)
        self.assertNotEqual(appearance_signature(changed), appearance_signature(self.canonical))
        self.assertEqual(
            resolve_preview(changed, self.flat)["preview_status"], "exact"
        )

    def test_each_geometry_bearing_mutation_changes_signature_and_is_unavailable(self):
        mutations = {
            "ring_size": lambda c, f: c["ring"].__setitem__("size", 6.5001),
            "band_width": lambda c, f: c["ring"].__setitem__("band_width_mm", 2.051),
            "band_thickness": lambda c, f: c["ring"].__setitem__("band_thickness_mm", 1.651),
            "band_profile": lambda c, f: c["ring"].__setitem__("band_profile", "knife_edge"),
            "shoulder": lambda c, f: c["ring"].__setitem__("shoulder_style", "cathedral"),
            "pave": lambda c, f: c["ring"]["pave"].update(
                {"coverage": "half", "setting_style": "bead_set"}
            ),
            "prongs": lambda c, f: c["setting"].__setitem__("prong_count", 6),
            "head_family": lambda c, f: c["setting"].__setitem__("head_family", "classic"),
            "gallery": lambda c, f: c["setting"].__setitem__("gallery_structure", "open_rail"),
            "stone_shape": lambda c, f: c["stones"][0].__setitem__("shape", "oval"),
            "stone_length": lambda c, f: c["stones"][0]["dimensions_mm"].__setitem__("length", 6.5001),
            "stone_orientation": lambda c, f: c["stones"][0]["placement"].__setitem__("orientation", "radial"),
            "head_style_id": lambda c, f: f.__setitem__("head_style_id", "head_classic_4_prong"),
            "band_style_id": lambda c, f: f.__setitem__("band_style_id", "band_classic_solitaire"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                canonical = copy.deepcopy(self.canonical)
                flat = copy.deepcopy(self.flat)
                mutate(canonical, flat)
                _projection, signature = geometry_signature(canonical, flat)
                self.assertNotEqual(signature, self.signature)
                manifest = resolve_preview(canonical, flat)
                self.assertEqual(manifest["preview_status"], "unavailable")
                self.assertEqual(manifest["reason_code"], "no_approved_preview_asset")

    def test_nearby_floating_point_values_are_not_rounded_or_tolerance_matched(self):
        canonical = copy.deepcopy(self.canonical)
        canonical["ring"]["size"] = math.nextafter(6.5, math.inf)
        _projection, signature = geometry_signature(canonical, self.flat)
        self.assertNotEqual(signature, self.signature)
        self.assertEqual(
            resolve_preview(canonical, self.flat)["preview_status"], "unavailable"
        )

        report = v4_report(stone_depth_mm=3.9001)
        self.assertEqual(report["flat_parameters"]["stone_depth_mm"], 3.9001)
        self.assertEqual(report["preview_manifest"]["preview_status"], "unavailable")

    def test_extraction_preserves_nearby_typed_geometry_before_signature_resolution(self):
        first_payload = {
            **base_payload(),
            "stone_length_mm": 6.5001,
            "stone_depth_mm": 3.9001,
        }
        second_payload = {
            **first_payload,
            "stone_length_mm": 6.5002,
            "stone_depth_mm": 3.9002,
        }
        first = analyze_photo_request(first_payload)
        second = analyze_photo_request(second_payload)

        for report, length, depth in (
            (first, 6.5001, 3.9001),
            (second, 6.5002, 3.9002),
        ):
            self.assertEqual(report["flat_parameters"]["stone_length_mm"], length)
            self.assertEqual(report["flat_parameters"]["stone_depth_mm"], depth)
            center = report["canonical_spec"]["stones"][0]["dimensions_mm"]
            self.assertEqual(center["length"], length)
            self.assertEqual(center["depth"], depth)

        _first_projection, first_signature = geometry_signature(
            first["canonical_spec"], first["flat_parameters"]
        )
        _second_projection, second_signature = geometry_signature(
            second["canonical_spec"], second["flat_parameters"]
        )
        self.assertNotEqual(first_signature, second_signature)

    def test_higher_precision_decimals_and_http_lexemes_fail_closed(self):
        canonical = copy.deepcopy(self.canonical)
        canonical["ring"]["size"] = Decimal("6.5000000000000000000000000001")
        with self.assertRaises(PreviewResolutionError):
            geometry_signature(canonical, self.flat)
        self.assertEqual(
            resolve_preview(canonical, self.flat)["reason_code"],
            "invalid_canonical_spec",
        )

        with self.assertRaises(ValueError):
            server._parse_request_json(
                b'{"stone_length_mm":6.5000000000000000000000000001}'
            )
        with self.assertRaises(ValueError):
            server._parse_request_json(
                b'{"stone_length_mm":12345678901234567890123456789.1}'
            )
        parsed = server._parse_request_json(
            b'{"stone_length_mm":6.5000,"ring_size_us":6.5}'
        )
        self.assertEqual(parsed, {"stone_length_mm": 6.5, "ring_size_us": 6.5})

    def test_quoted_geometry_and_hostile_numeric_exponents_fail_closed(self):
        quoted = "6.5000000000000000000000000001"
        parsed = server._parse_request_json(
            json.dumps({"stone_length_mm": quoted}).encode("utf-8")
        )
        with self.assertRaises(ValueError):
            manual_configurator.configure({**v4_payload(), **parsed})

        self.assertIsNone(
            parse_measurements(f"{quoted} x 6.5 x 3.9 mm")
        )
        self.assertIsNone(
            parse_measurements(
                {"length": quoted, "width": 6.5, "depth": 3.9}
            )
        )
        for raw in (
            b'{"stone_length_mm":1e1000000}',
            b'{"stone_length_mm":9007199254740992}',
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    server._parse_request_json(raw)

    def test_pave_accent_schedule_selection_is_geometry_bearing(self):
        first = copy.deepcopy(self.canonical)
        accent = {
            "stone_id": "accent_schedule_a",
            "role": "accent",
            "shape": "round",
            "dimensions_mm": {"length": 1.15, "width": 1.15, "depth": 0.72},
            "quantity": 14,
            "placement": {
                "region": "band",
                "pattern": "mirrored_row",
                "orientation": "radial",
            },
        }
        first["stones"].append(accent)
        first["ring"]["pave"].update(
            {
                "coverage": "half",
                "setting_style": "bead_set",
                "accent_stone_ids": ["accent_schedule_a"],
            }
        )
        second = copy.deepcopy(first)
        second["stones"][-1]["stone_id"] = "accent_schedule_b"
        first["ring"]["pave"]["accent_stone_ids"] = ["accent_schedule_a"]
        second["ring"]["pave"]["accent_stone_ids"] = ["accent_schedule_b"]
        self.assertNotEqual(
            geometry_signature(first, self.flat)[1],
            geometry_signature(second, self.flat)[1],
        )

        # The identifier-to-schedule binding is geometry-bearing too. Keep the
        # referenced ID fixed while swapping it between two different schedules;
        # a projection that dropped stone_id would alias these configurations.
        bound_first = copy.deepcopy(first)
        second_schedule = copy.deepcopy(accent)
        second_schedule["stone_id"] = "accent_schedule_b"
        second_schedule["dimensions_mm"] = {
            "length": 1.3,
            "width": 1.3,
            "depth": 0.8,
        }
        bound_first["stones"].append(second_schedule)
        bound_second = copy.deepcopy(bound_first)
        bound_second["stones"][-2]["stone_id"] = "accent_schedule_b"
        bound_second["stones"][-1]["stone_id"] = "accent_schedule_a"
        self.assertEqual(
            bound_first["ring"]["pave"]["accent_stone_ids"],
            bound_second["ring"]["pave"]["accent_stone_ids"],
        )
        self.assertNotEqual(
            geometry_signature(bound_first, self.flat)[1],
            geometry_signature(bound_second, self.flat)[1],
        )

    def test_insignificant_decimal_zeroes_and_negative_zero_normalize_stably(self):
        decimal_value = copy.deepcopy(self.canonical)
        decimal_value["ring"]["size"] = Decimal("6.5000")
        self.assertEqual(geometry_signature(decimal_value, self.flat)[1], self.signature)

        positive_zero = copy.deepcopy(self.canonical)
        negative_zero = copy.deepcopy(self.canonical)
        positive_zero["ring"]["size"] = 0.0
        negative_zero["ring"]["size"] = -0.0
        self.assertEqual(
            geometry_signature(positive_zero, self.flat),
            geometry_signature(negative_zero, self.flat),
        )

    def test_boolean_nonfinite_and_numeric_string_geometry_fail_closed(self):
        invalid_values = (True, float("nan"), float("inf"), float("-inf"), "6.5")
        for value in invalid_values:
            with self.subTest(value=repr(value)):
                canonical = copy.deepcopy(self.canonical)
                canonical["ring"]["size"] = value
                with self.assertRaises(PreviewResolutionError):
                    geometry_signature(canonical, self.flat)
                manifest = resolve_preview(canonical, self.flat)
                self.assertEqual(manifest["preview_status"], "unavailable")
                self.assertEqual(manifest["reason_code"], "invalid_canonical_spec")

    def test_forged_client_reference_and_legacy_ids_do_not_authorize_v4(self):
        forged = copy.deepcopy(self.flat)
        forged.update(
            {
                "reference_geometry_id": "goal0011-realism-first-round-solitaire-master-v4",
                "geometry_identity": "geo_v2_forged",
                "visual_reference": {"status": "approved"},
                "preview_status": "exact",
            }
        )
        self.assertEqual(geometry_signature(self.canonical, forged)[1], self.signature)

        for missing, alias in (
            ("head_style_id", "head_id"),
            ("band_style_id", "band_id"),
        ):
            with self.subTest(missing=missing):
                flat = copy.deepcopy(forged)
                value = flat.pop(missing)
                flat[alias] = value
                manifest = resolve_preview(self.canonical, flat)
                self.assertEqual(manifest["preview_status"], "unavailable")
                self.assertEqual(
                    manifest["reason_code"], "unsupported_geometry_parameters"
                )

        untrusted = copy.deepcopy(forged)
        untrusted["head_style_id"] = "client-claims-v4"
        self.assertEqual(
            resolve_preview(self.canonical, untrusted)["preview_status"],
            "unavailable",
        )

    def test_key_order_and_stone_schedule_order_do_not_change_signature(self):
        reordered = recursively_reverse_keys(self.canonical)
        reordered_flat = recursively_reverse_keys(self.flat)
        self.assertEqual(
            geometry_signature(reordered, reordered_flat),
            (self.projection, self.signature),
        )

        trilogy = manual_configurator.configure(
            {
                **base_payload(),
                "head_style_id": "head_petal_trilogy_4_prong",
                "band_style_id": "band_knife_edge_half_pave",
            }
        )
        reversed_stones = copy.deepcopy(trilogy["canonical_spec"])
        reversed_stones["stones"].reverse()
        self.assertGreater(len(reversed_stones["stones"]), 1)
        self.assertEqual(
            geometry_signature(reversed_stones, trilogy["flat_parameters"]),
            geometry_signature(trilogy["canonical_spec"], trilogy["flat_parameters"]),
        )

    def test_neutral_defaults_are_explicit_and_resolve_the_same_exact_asset(self):
        canonical = copy.deepcopy(self.canonical)
        canonical["setting"]["halo_style"] = "none"
        canonical["stones"][0]["proportions"] = {}
        self.assertEqual(geometry_signature(canonical, self.flat)[1], self.signature)

        omitted = copy.deepcopy(canonical)
        omitted["ring"].pop("pave")
        omitted["setting"].pop("halo_style")
        omitted["setting"].pop("side_stone_layout")
        omitted["setting"].pop("side_setting")
        omitted["stones"][0].pop("quantity")
        omitted["stones"][0].pop("placement")
        omitted["stones"][0].pop("proportions")
        projection, signature = geometry_signature(omitted, self.flat)
        self.assertEqual(projection, self.projection)
        self.assertEqual(signature, self.signature)
        self.assertEqual(resolve_preview(omitted, self.flat)["preview_status"], "exact")

    def test_unknown_nested_fields_and_incomplete_trusted_selection_fail_closed(self):
        unknown = copy.deepcopy(self.canonical)
        unknown["ring"]["client_preview_id"] = "claim-v4"
        with self.assertRaises(PreviewResolutionError):
            normalize_geometry_projection(unknown, self.flat)
        self.assertEqual(
            resolve_preview(unknown, self.flat)["reason_code"],
            "invalid_canonical_spec",
        )

        for flat in ({}, {"head_style_id": V4_HEAD_ID}, {"band_style_id": V4_BAND_ID}):
            with self.subTest(flat=flat):
                manifest = resolve_preview(self.canonical, flat)
                self.assertEqual(manifest["preview_status"], "unavailable")
                self.assertEqual(
                    manifest["reason_code"], "unsupported_geometry_parameters"
                )

    def test_normalization_and_resolution_do_not_mutate_inputs(self):
        canonical = copy.deepcopy(self.canonical)
        flat = copy.deepcopy(self.flat)
        expected_canonical = copy.deepcopy(canonical)
        expected_flat = copy.deepcopy(flat)
        normalize_geometry_projection(canonical, flat)
        resolve_preview(canonical, flat)
        self.assertEqual(canonical, expected_canonical)
        self.assertEqual(flat, expected_flat)


class PreviewResolverBehaviorTests(unittest.TestCase):
    def test_every_current_completed_catalog_combination_resolves_exact_or_unavailable(self):
        statuses = []
        exact_selections = []
        for shape in SHAPE_DIMENSIONS:
            payload = base_payload(shape)
            options = manual_configurator.compatible_options(payload)
            for head in options["head_styles"]:
                head_id = head["head_style_id"]
                for band in options["band_styles_by_head"][head_id]:
                    band_id = band["band_style_id"]
                    with self.subTest(shape=shape, head=head_id, band=band_id):
                        report = manual_configurator.configure(
                            {
                                **payload,
                                "head_style_id": head_id,
                                "band_style_id": band_id,
                            }
                        )
                        manifest = report["preview_manifest"]
                        statuses.append(manifest["preview_status"])
                        if manifest["preview_status"] == "exact":
                            exact_selections.append((shape, head_id, band_id))
                        self.assertNotEqual(manifest["preview_status"], "estimated")
                        self.assertFalse(manifest["manufacturing_authority"])
                        self.assertEqual(manifest["manufacturing_state"], MANUFACTURING_STATE)
                        self.assertTrue(manifest_validator().is_valid(manifest))
        # Goal 0015: the round 6.5 x 6.5 x 3.9 / US 6.5 payload is backed twice —
        # once by the Goal 0011 V4 single-configuration master and once by the
        # Goal 0014 V5 parametric master — through two distinct catalog pairs and
        # therefore two distinct geometry signatures. Every other completed
        # combination still resolves unavailable with no substitute.
        self.assertEqual(len(statuses), 52)
        self.assertEqual(statuses.count("exact"), 2)
        self.assertEqual(statuses.count("unavailable"), 50)
        self.assertEqual(
            sorted(exact_selections),
            sorted(
                [
                    ("round", V4_HEAD_ID, V4_BAND_ID),
                    ("round", V5_HEAD_ID, V5_BAND_ID),
                ]
            ),
        )

    def test_test_only_bounded_estimate_reports_every_approved_difference(self):
        registry = estimated_registry()
        validate_registry(registry, production=False)
        report = v4_report()
        canonical = copy.deepcopy(report["canonical_spec"])
        canonical["ring"]["size"] = 6.75
        manifest = resolve_preview(canonical, report["flat_parameters"], registry=registry)
        self.assertEqual(manifest["preview_status"], "estimated")
        self.assertEqual(manifest["representation_kind"], "approved_assembly")
        self.assertEqual(manifest["selected_asset"]["classification"], "estimated")
        self.assertEqual(
            manifest["approved_ranges"],
            [{"path": "/ring/size", "minimum": 6.0, "maximum": 7.0}],
        )
        self.assertEqual(len(manifest["approximated_parameters"]), 1)
        difference = manifest["approximated_parameters"][0]
        self.assertEqual(difference["path"], "/ring/size")
        self.assertEqual(difference["represented_value"], 6.5)
        self.assertEqual(difference["requested_value"], 6.75)
        self.assertFalse(manifest["manufacturing_authority"])
        self.assertTrue(manifest_validator().is_valid(manifest))

    def test_bounded_estimate_rejects_out_of_range_or_undeclared_differences(self):
        registry = estimated_registry()
        report = v4_report()
        for label, mutate in (
            ("below", lambda c: c["ring"].__setitem__("size", 5.9999)),
            ("above", lambda c: c["ring"].__setitem__("size", 7.0001)),
            ("unlisted", lambda c: c["ring"].__setitem__("band_width_mm", 2.06)),
        ):
            with self.subTest(label=label):
                canonical = copy.deepcopy(report["canonical_spec"])
                mutate(canonical)
                manifest = resolve_preview(
                    canonical, report["flat_parameters"], registry=registry
                )
                self.assertEqual(manifest["preview_status"], "unavailable")
                self.assertEqual(manifest["reason_code"], "no_approved_preview_asset")

    def test_ambiguous_bounded_matches_fail_closed(self):
        registry = estimated_registry()
        second = copy.deepcopy(registry["assets"][0])
        second["asset_id"] = "test-only-overlapping-estimate"
        second["asset_version"] = "test-v2"
        registry["assets"].append(second)
        validate_registry(registry, production=False)
        report = v4_report()
        canonical = copy.deepcopy(report["canonical_spec"])
        canonical["ring"]["size"] = 6.75
        manifest = resolve_preview(canonical, report["flat_parameters"], registry=registry)
        self.assertEqual(manifest["preview_status"], "unavailable")
        self.assertEqual(manifest["reason_code"], "ambiguous_preview_asset")
        self.assertIsNone(manifest["selected_asset"])

    def test_disabled_revoked_and_empty_rollback_registries_are_unavailable(self):
        report = v4_report()
        for label, mutate in (
            ("disabled", lambda r: r["assets"][0].__setitem__("enabled", False)),
            (
                "revoked",
                lambda r: r["assets"][0]["approval"].__setitem__("status", "revoked"),
            ),
            ("empty", lambda r: r.__setitem__("assets", [])),
        ):
            with self.subTest(label=label):
                registry = read_json(REGISTRY_PATH)
                mutate(registry)
                validate_registry(registry, production=False)
                manifest = resolve_preview(
                    report["canonical_spec"], report["flat_parameters"], registry=registry
                )
                self.assertEqual(manifest["preview_status"], "unavailable")
                self.assertIsNone(manifest["selected_asset"])

    def test_malformed_registry_becomes_an_explicit_unavailable_manifest(self):
        report = v4_report()
        registry = read_json(REGISTRY_PATH)
        registry["assets"][0]["match"]["geometry_signature"] = "sha256:" + "0" * 64
        manifest = resolve_preview(
            report["canonical_spec"], report["flat_parameters"], registry=registry
        )
        self.assertEqual(manifest["preview_status"], "unavailable")
        self.assertEqual(manifest["reason_code"], "registry_invalid")
        self.assertEqual(manifest["unsupported_parameters"][0]["path"], "/registry")

    def test_resolver_manifests_are_deterministic_and_non_certifying(self):
        report = v4_report()
        first = resolve_preview(report["canonical_spec"], report["flat_parameters"])
        second = resolve_preview(report["canonical_spec"], report["flat_parameters"])
        self.assertEqual(first, second)
        self.assertRegex(first["manifest_id"], r"^sha256:[0-9a-f]{64}$")
        self.assertFalse(first["manufacturing_authority"])
        self.assertEqual(first["manufacturing_state"], MANUFACTURING_STATE)
        self.assertTrue(
            any("not certify manufacturing" in warning for warning in first["warnings"])
        )


class ThreeJsFailClosedDeliveryTests(unittest.TestCase):
    def test_verified_v4_loader_returns_stable_success_and_failure_codes(self):
        manifest = v4_report()["preview_manifest"]
        asset = manifest["selected_asset"]
        delivery_manifest_path = local_asset_path(asset["delivery_manifest"]["url"])
        content_path = local_asset_path(asset["content"]["url"])
        source = f"""
          import {{ readFile }} from 'node:fs/promises';
          import {{ webcrypto }} from 'node:crypto';
          import {{ loadVerifiedAssetPayload }} from {RING3D_PATH.as_uri()!r};

          if (!globalThis.crypto?.subtle) {{
            Object.defineProperty(globalThis, 'crypto', {{ value: webcrypto }});
          }}
          globalThis.location = {{ href: 'http://localhost/', origin: 'http://localhost' }};

          const baseManifest = {json.dumps(manifest, sort_keys=True)};
          const deliveryBytes = new Uint8Array(
            await readFile(new URL({delivery_manifest_path.as_uri()!r})),
          );
          const contentBytes = new Uint8Array(
            await readFile(new URL({content_path.as_uri()!r})),
          );
          const encoder = new TextEncoder();
          const decoder = new TextDecoder();
          const results = {{}};

          const clone = value => JSON.parse(JSON.stringify(value));
          const arrayBuffer = bytes => bytes.buffer.slice(
            bytes.byteOffset,
            bytes.byteOffset + bytes.byteLength,
          );
          const response = (bytes, status = 200) => ({{
            ok: status >= 200 && status < 300,
            status,
            arrayBuffer: async () => arrayBuffer(bytes),
          }});
          const sha256 = async bytes => {{
            const digest = await webcrypto.subtle.digest('SHA-256', arrayBuffer(bytes));
            return Array.from(new Uint8Array(digest))
              .map(value => value.toString(16).padStart(2, '0'))
              .join('');
          }};
          const repinBinary = async bytes => {{
            const contentHash = await sha256(bytes);
            const delivery = JSON.parse(decoder.decode(deliveryBytes));
            delivery.binary.sha256 = contentHash;
            delivery.binary.byte_size = bytes.byteLength;
            const repinnedDeliveryBytes = encoder.encode(JSON.stringify(delivery));
            const previewManifest = clone(baseManifest);
            previewManifest.selected_asset.content.sha256 = contentHash;
            previewManifest.selected_asset.content.byte_size = bytes.byteLength;
            previewManifest.selected_asset.delivery_manifest.sha256 = (
              await sha256(repinnedDeliveryBytes)
            );
            previewManifest.selected_asset.delivery_manifest.byte_size = (
              repinnedDeliveryBytes.byteLength
            );
            return {{ previewManifest, deliveryBytes: repinnedDeliveryBytes }};
          }};
          const validFetch = async url => response(
            String(url).endsWith('.json') ? deliveryBytes : contentBytes,
          );
          const run = async (name, previewManifest, fetcher) => {{
            globalThis.fetch = fetcher;
            try {{
              const loaded = await loadVerifiedAssetPayload(previewManifest, true);
              results[name] = {{
                code: 'ok',
                asset_id: loaded.asset.asset_id,
                vertex_count: loaded.manifest.vertex_count,
                byte_size: loaded.buffer.byteLength,
              }};
            }} catch (error) {{
              results[name] = {{ code: error?.code || error?.name || 'unknown' }};
            }}
          }};

          await run('valid', clone(baseManifest), validFetch);
          await run(
            'not_found',
            clone(baseManifest),
            async () => response(new Uint8Array(), 404),
          );
          await run('timeout', clone(baseManifest), async () => {{
            const error = new Error('aborted test request');
            error.name = 'AbortError';
            throw error;
          }});

          const corruptHashBytes = contentBytes.slice();
          corruptHashBytes[corruptHashBytes.length - 1] ^= 0xff;
          await run(
            'corrupt_content_hash',
            clone(baseManifest),
            async url => response(
              String(url).endsWith('.json') ? deliveryBytes : corruptHashBytes,
            ),
          );
          await run(
            'corrupt_content_size',
            clone(baseManifest),
            async url => response(
              String(url).endsWith('.json')
                ? deliveryBytes
                : contentBytes.slice(0, contentBytes.length - 1),
            ),
          );

          const malformedBytes = encoder.encode('{{malformed');
          const malformedManifest = clone(baseManifest);
          malformedManifest.selected_asset.delivery_manifest.sha256 = await sha256(malformedBytes);
          malformedManifest.selected_asset.delivery_manifest.byte_size = malformedBytes.byteLength;
          await run(
            'malformed_delivery_manifest',
            malformedManifest,
            async url => response(
              String(url).endsWith('.json') ? malformedBytes : contentBytes,
            ),
          );

          const corruptHeaderBytes = contentBytes.slice();
          corruptHeaderBytes[0] ^= 0xff;
          const corruptHeaderHash = await sha256(corruptHeaderBytes);
          const corruptHeaderDelivery = JSON.parse(decoder.decode(deliveryBytes));
          corruptHeaderDelivery.binary.sha256 = corruptHeaderHash;
          corruptHeaderDelivery.binary.byte_size = corruptHeaderBytes.byteLength;
          const corruptHeaderDeliveryBytes = encoder.encode(
            JSON.stringify(corruptHeaderDelivery),
          );
          const corruptHeaderManifest = clone(baseManifest);
          corruptHeaderManifest.selected_asset.content.sha256 = corruptHeaderHash;
          corruptHeaderManifest.selected_asset.content.byte_size = corruptHeaderBytes.byteLength;
          corruptHeaderManifest.selected_asset.delivery_manifest.sha256 = (
            await sha256(corruptHeaderDeliveryBytes)
          );
          corruptHeaderManifest.selected_asset.delivery_manifest.byte_size = (
            corruptHeaderDeliveryBytes.byteLength
          );
          await run(
            'corrupt_binary_header',
            corruptHeaderManifest,
            async url => response(
              String(url).endsWith('.json')
                ? corruptHeaderDeliveryBytes
                : corruptHeaderBytes,
            ),
          );

          const header = new DataView(
            contentBytes.buffer,
            contentBytes.byteOffset,
            contentBytes.byteLength,
          );
          const vertexCount = header.getUint32(8, true);
          const normalOffset = 16 + vertexCount * 3 * Float32Array.BYTES_PER_ELEMENT;
          const indexOffset = normalOffset + vertexCount * 3 * Float32Array.BYTES_PER_ELEMENT;

          const outOfRangeIndexBytes = contentBytes.slice();
          new DataView(outOfRangeIndexBytes.buffer).setUint32(indexOffset, vertexCount, true);
          const outOfRangeIndex = await repinBinary(outOfRangeIndexBytes);
          await run(
            'out_of_range_index',
            outOfRangeIndex.previewManifest,
            async url => response(
              String(url).endsWith('.json')
                ? outOfRangeIndex.deliveryBytes
                : outOfRangeIndexBytes,
            ),
          );

          const nonfinitePositionBytes = contentBytes.slice();
          new DataView(nonfinitePositionBytes.buffer).setFloat32(16, Number.NaN, true);
          const nonfinitePosition = await repinBinary(nonfinitePositionBytes);
          await run(
            'nonfinite_position',
            nonfinitePosition.previewManifest,
            async url => response(
              String(url).endsWith('.json')
                ? nonfinitePosition.deliveryBytes
                : nonfinitePositionBytes,
            ),
          );

          const nonfiniteNormalBytes = contentBytes.slice();
          new DataView(nonfiniteNormalBytes.buffer).setFloat32(normalOffset, Number.NaN, true);
          const nonfiniteNormal = await repinBinary(nonfiniteNormalBytes);
          await run(
            'nonfinite_normal',
            nonfiniteNormal.previewManifest,
            async url => response(
              String(url).endsWith('.json')
                ? nonfiniteNormal.deliveryBytes
                : nonfiniteNormalBytes,
            ),
          );

          console.log(JSON.stringify(results));
        """
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", source],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        results = json.loads(completed.stdout)
        self.assertEqual(
            results["valid"],
            {
                "code": "ok",
                "asset_id": V4_ASSET_ID,
                "vertex_count": 78281,
                "byte_size": asset["content"]["byte_size"],
            },
        )
        self.assertEqual(
            {
                name: result["code"]
                for name, result in results.items()
                if name != "valid"
            },
            {
                "not_found": "asset_unavailable",
                "timeout": "asset_timeout",
                "corrupt_content_hash": "asset_hash_mismatch",
                "corrupt_content_size": "asset_size_mismatch",
                "malformed_delivery_manifest": "delivery_manifest_malformed",
                "corrupt_binary_header": "binary_contract_mismatch",
                "out_of_range_index": "binary_contract_mismatch",
                "nonfinite_position": "binary_contract_mismatch",
                "nonfinite_normal": "binary_contract_mismatch",
            },
        )


class AppendOnlyPreviewApprovalTests(unittest.TestCase):
    def persist(self, runs: Path, report: dict | None = None):
        report = report or v4_report()
        persisted = workflow.persist_proposal(report, runs)
        return report, persisted["job_id"], runs / persisted["job_id"]

    def test_persistence_writes_a_durable_manifest_and_idempotent_approval_event(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            report, job_id, run_dir = self.persist(runs)
            manifest = report["preview_manifest"]
            durable = read_json(run_dir / workflow.PREVIEW_MANIFEST_FILENAME)
            self.assertEqual(durable, manifest)

            request = approval_request(job_id, manifest)
            first = workflow.append_preview_approval(job_id, runs, request)
            retry = workflow.append_preview_approval(job_id, runs, request)
            self.assertEqual(first["status"], "recorded")
            self.assertEqual(retry["status"], "idempotent")
            self.assertEqual(first["approval"], retry["approval"])
            event = first["approval"]
            self.assertEqual(event["schema"], "myjewl.preview_approval.v0_1")
            self.assertEqual(event["append_index"], 1)
            self.assertRegex(event["event_sha256"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual(event["scope"], "preview_only")
            self.assertEqual(event["decision"], "approved")
            self.assertEqual(event["resolved_preview_status"], "exact")
            self.assertEqual(event["displayed_preview_status"], "exact")
            self.assertEqual(event["preview_manifest"], manifest)
            self.assertFalse(event["manufacturing_authority"])
            self.assertIn("Non-certifying", event["non_manufacturing_acknowledgement"])
            approval_files = list(
                (run_dir / workflow.PREVIEW_APPROVALS_DIRNAME).glob("*.json")
            )
            self.assertEqual(len(approval_files), 1)
            self.assertEqual(read_json(approval_files[0]), event)
            head = read_json(run_dir / workflow.PREVIEW_APPROVAL_HEAD_FILENAME)
            self.assertEqual(head["schema"], "myjewl.preview_approval_head.v0_1")
            self.assertEqual(head["job_id"], job_id)
            self.assertEqual(head["count"], 1)
            self.assertEqual(head["latest_approval_id"], event["approval_id"])
            self.assertEqual(head["latest_event_sha256"], event["event_sha256"])
            self.assertRegex(head["history_sha256"], r"^sha256:[0-9a-f]{64}$")

            review = workflow.review_index(runs)["runs"][0]
            self.assertEqual(review["preview_resolution_state"], "resolved")
            self.assertEqual(review["preview_manifest"], manifest)
            self.assertEqual(review["preview_approval"]["status"], "valid")
            self.assertEqual(review["preview_approval"]["count"], 1)
            self.assertEqual(review["preview_approval"]["history"], [event])

    def test_distinct_decisions_append_without_overwriting_prior_event(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            report, job_id, run_dir = self.persist(runs)
            manifest = report["preview_manifest"]
            approved = workflow.append_preview_approval(
                job_id, runs, approval_request(job_id, manifest)
            )
            approvals_dir = run_dir / workflow.PREVIEW_APPROVALS_DIRNAME
            first_path = approvals_dir / f"{approved['approval']['approval_id']}.json"
            first_bytes = first_path.read_bytes()

            continued = workflow.append_preview_approval(
                job_id,
                runs,
                approval_request(
                    job_id,
                    manifest,
                    decision="continued_without_preview",
                ),
            )
            self.assertEqual(continued["status"], "recorded")
            self.assertNotEqual(
                continued["approval"]["approval_id"],
                approved["approval"]["approval_id"],
            )
            self.assertEqual(first_path.read_bytes(), first_bytes)
            self.assertEqual(len(list(approvals_dir.glob("*.json"))), 2)
            self.assertEqual(continued["preview_approval"]["count"], 2)
            self.assertEqual(
                [item["append_index"] for item in continued["preview_approval"]["history"]],
                [1, 2],
            )
            self.assertEqual(
                continued["preview_approval"]["latest"]["approval_id"],
                continued["approval"]["approval_id"],
            )
            self.assertEqual(
                {item["decision"] for item in continued["preview_approval"]["history"]},
                {"approved", "continued_without_preview"},
            )

            geometry_request = workflow.request_geometry(job_id, runs)
            self.assertEqual(geometry_request["preview_decision"]["scope"], "preview_only")
            self.assertFalse(
                geometry_request["preview_decision"]["manufacturing_authority"]
            )
            self.assertEqual(
                geometry_request["preview_decision"]["approval_id"],
                continued["approval"]["approval_id"],
            )

    def test_history_head_detects_suffix_deletion_and_missing_anchor(self):
        for remove in ("latest_event", "head"):
            with self.subTest(remove=remove), tempfile.TemporaryDirectory() as directory:
                runs = Path(directory) / "runs"
                report, job_id, run_dir = self.persist(runs)
                manifest = report["preview_manifest"]
                workflow.append_preview_approval(
                    job_id, runs, approval_request(job_id, manifest)
                )
                latest = workflow.append_preview_approval(
                    job_id,
                    runs,
                    approval_request(
                        job_id,
                        manifest,
                        decision="continued_without_preview",
                    ),
                )["approval"]
                if remove == "latest_event":
                    (
                        run_dir
                        / workflow.PREVIEW_APPROVALS_DIRNAME
                        / f"{latest['approval_id']}.json"
                    ).unlink()
                else:
                    (run_dir / workflow.PREVIEW_APPROVAL_HEAD_FILENAME).unlink()
                review = workflow.review_index(runs)["runs"][0]
                self.assertEqual(review["preview_approval"]["status"], "corrupt")
                with self.assertRaises(workflow.PreviewApprovalConflictError):
                    workflow.append_preview_approval(
                        job_id, runs, approval_request(job_id, manifest)
                    )

    def test_unavailable_preview_can_only_continue_without_preview(self):
        unavailable_report = manual_configurator.configure(
            {
                **base_payload(),
                "head_style_id": "head_classic_4_prong",
                "band_style_id": "band_classic_solitaire",
            }
        )
        self.assertEqual(
            unavailable_report["preview_manifest"]["preview_status"], "unavailable"
        )
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            report, job_id, _run_dir = self.persist(runs, unavailable_report)
            manifest = report["preview_manifest"]
            with self.assertRaises(workflow.PreviewApprovalValidationError):
                workflow.append_preview_approval(
                    job_id,
                    runs,
                    approval_request(job_id, manifest, decision="approved"),
                )
            recorded = workflow.append_preview_approval(
                job_id,
                runs,
                approval_request(
                    job_id,
                    manifest,
                    decision="continued_without_preview",
                ),
            )
            self.assertEqual(recorded["status"], "recorded")
            self.assertEqual(recorded["approval"]["decision"], "continued_without_preview")
            self.assertEqual(recorded["approval"]["displayed_preview_status"], "unavailable")
            self.assertIsNone(
                recorded["approval"]["delivery_observation"]["asset_id"]
            )

    def test_stale_manifest_forged_delivery_and_extra_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            report, job_id, run_dir = self.persist(runs)
            manifest = report["preview_manifest"]
            valid = approval_request(job_id, manifest)
            invalid_requests = []

            forged_manifest = copy.deepcopy(valid)
            forged_manifest["manifest_id"] = "sha256:" + "0" * 64
            invalid_requests.append((workflow.PreviewApprovalConflictError, forged_manifest))

            forged_hash = copy.deepcopy(valid)
            forged_hash["delivery"]["content_sha256"] = "0" * 64
            invalid_requests.append((workflow.PreviewApprovalValidationError, forged_hash))

            failed_approval = copy.deepcopy(valid)
            failed_approval["delivery"]["status"] = "failure"
            failed_approval["delivery"]["displayed_status"] = "unavailable"
            invalid_requests.append((workflow.PreviewApprovalValidationError, failed_approval))

            extra = copy.deepcopy(valid)
            extra["browser_claim"] = "exact"
            invalid_requests.append((workflow.PreviewApprovalValidationError, extra))

            free_reason = approval_request(
                job_id,
                manifest,
                decision="continued_without_preview",
            )
            free_reason["delivery"]["reason_code"] = "customer said maybe later"
            invalid_requests.append(
                (workflow.PreviewApprovalValidationError, free_reason)
            )

            for exception, request in invalid_requests:
                with self.subTest(exception=exception.__name__):
                    with self.assertRaises(exception):
                        workflow.append_preview_approval(job_id, runs, request)

            parameters_path = run_dir / workflow.PARAMETERS_FILENAME
            parameters = read_json(parameters_path)
            parameters["head_style_id"] = "tampered-client-head"
            parameters_path.write_text(json.dumps(parameters), encoding="utf-8")
            with self.assertRaises(workflow.PreviewApprovalConflictError):
                workflow.append_preview_approval(job_id, runs, valid)

    def test_tampered_history_is_reported_corrupt_and_blocks_more_appends(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            report, job_id, run_dir = self.persist(runs)
            request = approval_request(job_id, report["preview_manifest"])
            recorded = workflow.append_preview_approval(job_id, runs, request)
            event_path = (
                run_dir
                / workflow.PREVIEW_APPROVALS_DIRNAME
                / f"{recorded['approval']['approval_id']}.json"
            )
            tampered = read_json(event_path)
            tampered["manufacturing_authority"] = True
            event_path.write_text(json.dumps(tampered), encoding="utf-8")

            review = workflow.review_index(runs)["runs"][0]
            self.assertEqual(review["preview_approval"]["status"], "corrupt")
            self.assertEqual(review["preview_approval"]["count"], 0)
            self.assertEqual(len(review["preview_approval"]["corruption"]), 1)
            with self.assertRaises(workflow.PreviewApprovalConflictError):
                workflow.append_preview_approval(job_id, runs, request)

    def test_timestamp_and_boolean_manifest_tampering_fail_strict_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            report, job_id, run_dir = self.persist(runs)
            request = approval_request(job_id, report["preview_manifest"])
            recorded = workflow.append_preview_approval(job_id, runs, request)
            event_path = (
                run_dir
                / workflow.PREVIEW_APPROVALS_DIRNAME
                / f"{recorded['approval']['approval_id']}.json"
            )
            timestamp_tamper = read_json(event_path)
            timestamp_tamper["recorded_at_utc"] = "9999-01-01T00:00:00.000000Z"
            event_path.write_text(json.dumps(timestamp_tamper), encoding="utf-8")
            self.assertEqual(
                workflow.review_index(runs)["runs"][0]["preview_approval"]["status"],
                "corrupt",
            )

        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            report, job_id, run_dir = self.persist(runs)
            manifest = read_json(run_dir / workflow.PREVIEW_MANIFEST_FILENAME)
            manifest["geometry"]["projection"]["stones"][0]["quantity"] = True
            proposal = read_json(run_dir / workflow.PROPOSAL_FILENAME)
            proposal["preview_manifest"] = copy.deepcopy(manifest)
            proposal["preview"]["preview_manifest"] = copy.deepcopy(manifest)
            (run_dir / workflow.PREVIEW_MANIFEST_FILENAME).write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            (run_dir / workflow.PROPOSAL_FILENAME).write_text(
                json.dumps(proposal), encoding="utf-8"
            )
            review = workflow.review_index(runs)["runs"][0]
            self.assertEqual(review["preview_resolution_state"], "corrupt")

    def test_review_distinguishes_legacy_unresolved_from_corrupt_resolution(self):
        report = v4_report()
        report.pop("preview_manifest")
        report.pop("preview_geometry_signature")
        report["preview"].pop("preview_manifest")
        job_id = report["flat_parameters"]["config_id"]
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            run_dir = runs / job_id
            run_dir.mkdir(parents=True)
            for name, payload in (
                (workflow.PROPOSAL_FILENAME, report),
                (workflow.PARAMETERS_FILENAME, report["flat_parameters"]),
                (workflow.CANONICAL_FILENAME, report["canonical_spec"]),
            ):
                (run_dir / name).write_text(json.dumps(payload), encoding="utf-8")
            legacy = workflow.review_index(runs)["runs"][0]
            self.assertIsNone(legacy["preview_manifest"])
            self.assertEqual(legacy["preview_resolution_state"], "legacy_unresolved")
            self.assertEqual(legacy["preview_approval"]["status"], "not_recorded")

            workflow.persist_proposal(v4_report(), runs)
            manifest_path = run_dir / workflow.PREVIEW_MANIFEST_FILENAME
            manifest = read_json(manifest_path)
            manifest["reason_code"] = "tampered"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            corrupt = workflow.review_index(runs)["runs"][0]
            self.assertEqual(corrupt["preview_resolution_state"], "corrupt")
            self.assertEqual(corrupt["preview_approval"]["status"], "corrupt")

    def test_orphaned_approval_files_never_authenticate_a_legacy_run(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            report, job_id, run_dir = self.persist(runs)
            workflow.append_preview_approval(
                job_id,
                runs,
                approval_request(job_id, report["preview_manifest"]),
            )
            (run_dir / workflow.PREVIEW_MANIFEST_FILENAME).unlink()
            proposal = read_json(run_dir / workflow.PROPOSAL_FILENAME)
            proposal.pop("preview_manifest")
            proposal.pop("preview_geometry_signature")
            proposal["preview"].pop("preview_manifest")
            (run_dir / workflow.PROPOSAL_FILENAME).write_text(
                json.dumps(proposal), encoding="utf-8"
            )
            legacy = workflow.review_index(runs)["runs"][0]
            self.assertEqual(legacy["preview_resolution_state"], "legacy_unresolved")
            self.assertEqual(legacy["preview_approval"]["status"], "not_recorded")
            self.assertEqual(legacy["preview_approval"]["count"], 0)
            geometry_request = workflow.request_geometry(job_id, runs)
            self.assertIsNone(geometry_request["preview_decision"])

    def test_geometry_request_rejects_rejected_proposal_and_review_quarantines_bad_flat(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            _report, job_id, run_dir = self.persist(runs)
            proposal_path = run_dir / workflow.PROPOSAL_FILENAME
            proposal = read_json(proposal_path)
            proposal["status"] = "rejected"
            proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
            with self.assertRaises(workflow.PreviewApprovalConflictError):
                workflow.request_geometry(job_id, runs)

        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            _report, _job_id, run_dir = self.persist(runs)
            proposal_path = run_dir / workflow.PROPOSAL_FILENAME
            proposal = read_json(proposal_path)
            proposal["flat_parameters"] = ["tampered"]
            proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
            review = workflow.review_index(runs)["runs"][0]
            self.assertEqual(review["preview_resolution_state"], "corrupt")
            self.assertEqual(review["preview_approval"]["status"], "corrupt")
            self.assertEqual(review["config_summary"], {
                "archetype": None,
                "stone_shape": None,
                "stone_carat_eq": None,
                "center_setting": None,
                "prong_count": None,
                "metal_alloy": None,
                "band_width_mm": None,
                "band_thickness_mm": None,
                "ring_size_us": None,
                "head_style_id": None,
                "band_style_id": None,
                "band_profile": None,
                "band_profile_code": None,
                "shoulder_style": None,
                "head_family": None,
                "head_geometry": None,
                "gallery_structure": None,
                "side_stone_layout": None,
                "pave": None,
                "stone_schedules": None,
                "metal_finish": None,
            })

    def test_extraction_endpoint_finalization_persists_authoritative_unavailable(self):
        payload = base_payload()
        payload.update(
            {
                "metal_color_family": "yellow",
                "metal_karat": "14k",
                "metal_finish": "polished",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            report = server._finalize_extraction_report(
                analyze_photo_request(payload),
                runs,
            )
            self.assertEqual(report["status"], "accepted")
            self.assertEqual(report["preview_manifest"]["preview_status"], "unavailable")
            self.assertEqual(
                report["preview_manifest"]["reason_code"],
                "unsupported_geometry_parameters",
            )
            persisted = read_json(
                runs / report["run"]["job_id"] / workflow.PREVIEW_MANIFEST_FILENAME
            )
            self.assertEqual(persisted, report["preview_manifest"])

            second_payload = copy.deepcopy(payload)
            second_payload["stone_depth_mm"] = 3.91
            second = server._finalize_extraction_report(
                analyze_photo_request(second_payload),
                runs,
            )
            self.assertEqual(second["status"], "accepted")
            self.assertNotEqual(report["run"]["job_id"], second["run"]["job_id"])
            self.assertEqual(len([path for path in runs.iterdir() if path.is_dir()]), 2)


class AdversarialPreviewRegistryTests(unittest.TestCase):
    def assert_registry_rejected(self, mutate):
        registry = read_json(REGISTRY_PATH)
        mutate(registry)
        with self.assertRaises(PreviewRegistryError):
            validate_registry(registry, production=False)

    def test_non_utf8_registry_path_resolves_unavailable(self):
        report = v4_report()
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "registry.json"
            registry_path.write_bytes(b"\xff\xfe")
            with self.assertRaises(PreviewRegistryError):
                load_registry(registry_path, production=False)

            manifest = resolve_preview(
                report["canonical_spec"],
                report["flat_parameters"],
                registry_path=registry_path,
            )
        self.assertEqual(manifest["preview_status"], "unavailable")
        self.assertEqual(manifest["reason_code"], "registry_invalid")

    def test_duplicate_identity_signature_drift_and_incomplete_coverage_are_rejected(self):
        def duplicate(registry):
            registry["assets"].append(copy.deepcopy(registry["assets"][0]))

        def signature_drift(registry):
            registry["assets"][0]["match"]["geometry_signature"] = "sha256:" + "0" * 64

        def incomplete_coverage(registry):
            registry["assets"][0]["represented_parameters"].remove("/ring/size")

        for name, mutate in (
            ("duplicate", duplicate),
            ("signature_drift", signature_drift),
            ("incomplete_coverage", incomplete_coverage),
        ):
            with self.subTest(name=name):
                self.assert_registry_rejected(mutate)

    def test_external_traversal_query_fragment_and_backslash_urls_are_rejected(self):
        urls = (
            "https://example.com/static/assets/ring.meshbin",
            "/static/assets/../goal0011_v4/solitaire_realism_master_v4.meshbin",
            "/static/assets/goal0011_v4/meshbin?version=4",
            "/static/assets/goal0011_v4/meshbin#fragment",
            "/static/assets/goal0011_v4\\meshbin",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assert_registry_rejected(
                    lambda registry, value=url: registry["assets"][0]["delivery"][
                        "content"
                    ].__setitem__("url", value)
                )

    def test_delivery_hash_size_and_manifest_metadata_drift_are_rejected(self):
        mutations = (
            lambda r: r["assets"][0]["delivery"]["content"].__setitem__(
                "sha256", "0" * 64
            ),
            lambda r: r["assets"][0]["delivery"]["content"].__setitem__(
                "byte_size", 1
            ),
            lambda r: r["assets"][0]["geometry"].__setitem__("triangle_count", 1),
            lambda r: r["assets"][0]["provenance"].__setitem__(
                "source_receipt_sha256", "0" * 64
            ),
            lambda r: r["assets"][0]["provenance"].__setitem__(
                "source_obj_sha256", "0" * 64
            ),
            lambda r: r["assets"][0]["geometry"]["transform"].__setitem__(
                "scale", 0.2
            ),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_registry_rejected(mutate)

    def test_exact_and_estimated_match_policy_invariants_are_rejected(self):
        def exact_range(registry):
            asset = registry["assets"][0]
            asset["match"]["approved_ranges"] = [
                {"path": "/ring/size", "minimum": 6.0, "maximum": 7.0}
            ]

        def estimated_without_declaration(registry):
            asset = registry["assets"][0]
            asset["classification"] = "estimated"
            asset["representation_kind"] = "approved_assembly"
            asset["match"]["policy"] = "bounded_projection"

        for name, mutate in (
            ("exact_range", exact_range),
            ("estimated_without_declaration", estimated_without_declaration),
        ):
            with self.subTest(name=name):
                self.assert_registry_rejected(mutate)

        registry = estimated_registry()
        registry["assets"][0]["approximated_parameters"][0]["path"] = "/ring/band_width_mm"
        with self.assertRaises(PreviewRegistryError):
            validate_registry(registry, production=False)

    def test_unsupported_parameters_and_invalid_estimate_baselines_fail_closed(self):
        report = v4_report()
        registry = read_json(REGISTRY_PATH)
        registry["assets"][0]["unsupported_parameters"] = [
            {"path": "/ring/size", "reason_code": "not_supported"}
        ]
        manifest = resolve_preview(
            report["canonical_spec"],
            report["flat_parameters"],
            registry=registry,
        )
        self.assertEqual(manifest["preview_status"], "unavailable")
        self.assertEqual(manifest["reason_code"], "registry_invalid")

        for baseline in ("not-a-number", 7.5):
            with self.subTest(baseline=baseline):
                registry = estimated_registry()
                projection = registry["assets"][0]["match"]["geometry_projection"]
                projection["ring"]["size"] = baseline
                registry["assets"][0]["match"]["geometry_signature"] = (
                    projection_signature(projection)
                )
                with self.assertRaises(PreviewRegistryError):
                    validate_registry(registry, production=False)

    def test_duplicate_reversed_and_nonfinite_ranges_are_rejected(self):
        registry = estimated_registry()
        registry["assets"][0]["match"]["approved_ranges"].append(
            copy.deepcopy(registry["assets"][0]["match"]["approved_ranges"][0])
        )
        with self.assertRaises(PreviewRegistryError):
            validate_registry(registry, production=False)

        registry = estimated_registry()
        registry["assets"][0]["match"]["approved_ranges"][0].update(
            {"minimum": 7.0, "maximum": 6.0}
        )
        with self.assertRaises(PreviewRegistryError):
            validate_registry(registry, production=False)

        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=repr(value)):
                registry = estimated_registry()
                registry["assets"][0]["match"]["approved_ranges"][0]["minimum"] = value
                with self.assertRaises(PreviewRegistryError):
                    validate_registry(registry, production=False)

    def test_delivery_manifest_wrong_json_shapes_fail_closed(self):
        report = v4_report()
        content_path = local_asset_path(
            report["preview_manifest"]["selected_asset"]["content"]["url"]
        )
        for malformed in ([], 1, {"binary": []}):
            with self.subTest(malformed=malformed):
                with tempfile.TemporaryDirectory() as directory:
                    malformed_path = Path(directory) / "delivery.json"
                    malformed_path.write_text(json.dumps(malformed), encoding="utf-8")
                    registry = read_json(REGISTRY_PATH)
                    with mock.patch.object(
                        preview_resolution,
                        "_registered_file",
                        side_effect=[malformed_path, content_path],
                    ):
                        with self.assertRaises(PreviewRegistryError):
                            validate_registry(registry, production=False)
                    with mock.patch.object(
                        preview_resolution,
                        "_registered_file",
                        side_effect=[malformed_path, content_path],
                    ):
                        manifest = resolve_preview(
                            report["canonical_spec"],
                            report["flat_parameters"],
                            registry=registry,
                        )
                    self.assertEqual(manifest["preview_status"], "unavailable")
                    self.assertEqual(manifest["reason_code"], "registry_invalid")

    def test_arbitrary_size_registry_integers_fail_closed_without_overflow(self):
        import sys
        previous_limit = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(12000)
        self.addCleanup(sys.set_int_max_str_digits, previous_limit)
        enormous = 10**10000
        registries = []

        transform = read_json(REGISTRY_PATH)
        transform["assets"][0]["geometry"]["transform"][
            "rotation_x_radians"
        ] = enormous
        registries.append(transform)

        approved_range = estimated_registry()
        approved_range["assets"][0]["match"]["approved_ranges"][0][
            "minimum"
        ] = -enormous
        registries.append(approved_range)

        baseline = estimated_registry()
        projection = baseline["assets"][0]["match"]["geometry_projection"]
        projection["ring"]["size"] = enormous
        baseline["assets"][0]["match"]["geometry_signature"] = (
            projection_signature(projection)
        )
        baseline["assets"][0]["match"]["approved_ranges"][0].update(
            {"minimum": 6.0, "maximum": 7.0}
        )
        registries.append(baseline)

        report = v4_report()
        for registry in registries:
            with self.subTest(kind=registry["assets"][0]["match"]["policy"]):
                with self.assertRaises(PreviewRegistryError):
                    validate_registry(registry, production=False)
                manifest = resolve_preview(
                    report["canonical_spec"],
                    report["flat_parameters"],
                    registry=registry,
                )
                self.assertEqual(manifest["preview_status"], "unavailable")
                self.assertEqual(manifest["reason_code"], "registry_invalid")

    def test_production_cardinality_rejects_zero_exact_or_any_estimated_asset(self):
        empty = read_json(REGISTRY_PATH)
        empty["assets"] = []
        with self.assertRaises(PreviewRegistryError):
            validate_registry(empty, production=True)

        with self.assertRaises(PreviewRegistryError):
            validate_registry(estimated_registry(), production=True)


if __name__ == "__main__":
    unittest.main()
