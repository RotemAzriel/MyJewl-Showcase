import json
import sys
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator

from jewelry_gaas_foundation import (
    ARCHETYPE_CONTRACTS,
    GeometryGateStatus,
    RhinoComputeHttpAdapter,
    solve_job,
    solve_solitaire,
    validate_job_spec,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "canonical_spec_v0_1.schema.json"
SOLITAIRE_EXAMPLE_PATH = ROOT / "examples" / "solitaire_valid.json"


def solitaire_spec():
    return json.loads(SOLITAIRE_EXAMPLE_PATH.read_text(encoding="utf-8"))


def stone(stone_id, role, length=2.0, width=2.0, depth=1.2):
    return {
        "stone_id": stone_id,
        "role": role,
        "shape": "round",
        "dimensions_mm": {
            "length": length,
            "width": width,
            "depth": depth,
        },
        "carat": 0.05,
    }


def halo_spec():
    spec = solitaire_spec()
    spec["job"]["job_id"] = "job_halo_001"
    spec["job"]["archetype"] = "halo"
    spec["setting"]["halo_style"] = "single"
    spec["stones"].extend(
        [
            stone("halo_001", "halo", 1.2, 1.2, 0.8),
            stone("halo_002", "halo", 1.2, 1.2, 0.8),
        ]
    )
    return spec


def trilogy_spec():
    spec = solitaire_spec()
    spec["job"]["job_id"] = "job_trilogy_001"
    spec["job"]["archetype"] = "trilogy"
    spec["setting"]["side_stone_layout"] = "left_right_pair"
    spec["stones"].extend(
        [
            stone("side_left_001", "side", 4.0, 4.0, 2.4),
            stone("side_right_001", "side", 4.0, 4.0, 2.4),
        ]
    )
    return spec


class SpyGeometryAdapter:
    name = "spy_geometry_adapter"

    def __init__(self):
        self.calls = []

    def evaluate_geometry_gates(self, spec, pending_geometry_checks):
        self.calls.append(
            (
                spec["job"]["job_id"],
                spec["job"]["archetype"],
                tuple(pending_geometry_checks),
            )
        )
        return tuple(
            GeometryGateStatus(
                code=code,
                status="pending",
                reason_code="spy_adapter_no_real_geometry",
            )
            for code in pending_geometry_checks
        )


class FakeHttpClient:
    def __init__(self, response=None, exception=None):
        self.response = response
        self.exception = exception
        self.calls = []

    def post(self, url, json, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if self.exception is not None:
            raise self.exception
        return self.response


def rhino_compute_adapter(fake_client, timeout_seconds=12.5):
    return RhinoComputeHttpAdapter(
        base_url="https://compute.example",
        api_key="secret-api-key",
        definition_id="launch-ring-v0",
        timeout_seconds=timeout_seconds,
        http_client=fake_client,
    )


class FoundationTests(unittest.TestCase):
    def assert_rejected_with(self, spec, expected_code, expected_path=None):
        result = validate_job_spec(spec)

        self.assertFalse(result.accepted)
        self.assertIn(expected_code, {issue.code for issue in result.issues})
        if expected_path is not None:
            self.assertIn(expected_path, {issue.path for issue in result.issues})

    def test_valid_launch_archetypes_validate(self):
        for spec in (solitaire_spec(), halo_spec(), trilogy_spec()):
            with self.subTest(archetype=spec["job"]["archetype"]):
                result = validate_job_spec(spec)

                self.assertTrue(result.accepted, result.issues)
                self.assertIn(
                    "mesh_closedness_and_manifold_suitability",
                    result.pending_geometry_checks,
                )

    def test_solitaire_solver_returns_pending_assets(self):
        package = solve_solitaire(solitaire_spec())

        self.assertEqual(package.validation_status, "accepted")
        self.assertEqual(package.job_id, "job_solitaire_001")
        self.assertEqual({asset.target for asset in package.assets}, {"GLB", "STL"})
        self.assertEqual({asset.status for asset in package.assets}, {"pending_generation"})
        self.assertIn("pending_geometry_checks", package.technical_summary)
        self.assertEqual(
            set(package.technical_summary["pending_geometry_checks"]),
            {gate.code for gate in package.geometry_gates},
        )
        self.assertEqual({gate.status for gate in package.geometry_gates}, {"not_run"})
        self.assertEqual(
            {gate.reason_code for gate in package.geometry_gates},
            {"rhino_adapter_not_configured"},
        )
        self.assertEqual(package.debug_trace["adapter_name"], "noop_rhino_adapter")
        self.assertFalse(package.debug_trace["adapter_invoked"])

    def test_solve_job_routes_valid_launch_archetypes_through_adapter(self):
        adapter = SpyGeometryAdapter()

        for spec in (solitaire_spec(), halo_spec(), trilogy_spec()):
            with self.subTest(archetype=spec["job"]["archetype"]):
                package = solve_job(spec, geometry_adapter=adapter)

                self.assertEqual(package.validation_status, "accepted")
                self.assertEqual(package.archetype, spec["job"]["archetype"])
                self.assertEqual({asset.status for asset in package.assets}, {"pending_generation"})
                self.assertEqual({gate.status for gate in package.geometry_gates}, {"pending"})
                self.assertEqual(package.debug_trace["adapter_name"], "spy_geometry_adapter")
                self.assertTrue(package.debug_trace["adapter_invoked"])

        self.assertEqual(
            [call[1] for call in adapter.calls],
            ["solitaire", "halo", "trilogy"],
        )

    def test_invalid_specs_never_reach_geometry_adapter(self):
        spec = solitaire_spec()
        spec["stones"][0]["role"] = "accent"
        adapter = SpyGeometryAdapter()

        package = solve_job(spec, geometry_adapter=adapter)

        self.assertEqual(package.validation_status, "rejected")
        self.assertEqual(adapter.calls, [])
        self.assertIn("archetype_role_count_invalid", package.rejection_reasons)
        self.assertEqual(package.debug_trace["adapter_name"], "spy_geometry_adapter")
        self.assertFalse(package.debug_trace["adapter_invoked"])

    def test_default_solve_job_is_deterministic_except_timestamp(self):
        first = solve_job(solitaire_spec())
        second = solve_job(solitaire_spec())

        self.assertEqual(
            replace(first, generated_at="fixed"),
            replace(second, generated_at="fixed"),
        )

    def test_default_solve_job_does_not_import_rhino_runtime_modules(self):
        rhino_module_names = {
            "Rhino",
            "rhino3dm",
            "rhinoinside",
            "compute_rhino3d",
            "RhinoCompute",
            "Grasshopper",
        }

        solve_job(solitaire_spec())

        self.assertTrue(rhino_module_names.isdisjoint(sys.modules))

    def test_invalid_specs_never_reach_rhino_compute_http_adapter(self):
        spec = solitaire_spec()
        spec["stones"][0]["role"] = "accent"
        fake_client = FakeHttpClient(
            {
                "execution_status": "completed",
                "assets": {},
                "geometry_gates": [],
            }
        )

        package = solve_job(spec, geometry_adapter=rhino_compute_adapter(fake_client))

        self.assertEqual(package.validation_status, "rejected")
        self.assertEqual(fake_client.calls, [])
        self.assertFalse(package.debug_trace["adapter_invoked"])

    def test_rhino_compute_http_adapter_sends_contract_payload(self):
        fake_client = FakeHttpClient(
            {
                "execution_status": "completed",
                "assets": {},
                "geometry_gates": [],
            }
        )
        spec = solitaire_spec()

        package = solve_job(spec, geometry_adapter=rhino_compute_adapter(fake_client))

        self.assertEqual(package.validation_status, "accepted")
        self.assertEqual(len(fake_client.calls), 1)
        call = fake_client.calls[0]
        self.assertEqual(call["url"], "https://compute.example/solve")
        self.assertEqual(call["headers"]["x-api-key"], "secret-api-key")
        self.assertEqual(call["headers"]["content-type"], "application/json")
        self.assertEqual(call["timeout"], 12.5)
        self.assertEqual(
            call["json"],
            {
                "definition_id": "launch-ring-v0",
                "job_id": "job_solitaire_001",
                "archetype": "solitaire",
                "spec": spec,
                "pending_geometry_checks": package.technical_summary[
                    "pending_geometry_checks"
                ],
                "output_targets": ["GLB", "STL"],
            },
        )
        self.assertEqual(
            package.debug_trace["adapter_execution_status"],
            "completed",
        )
        self.assertEqual(
            package.debug_trace["adapter_definition_id"],
            "launch-ring-v0",
        )
        self.assertTrue(package.debug_trace["adapter_invoked"])

    def test_rhino_compute_success_maps_generated_and_pending_assets(self):
        fake_client = FakeHttpClient(
            {
                "execution_status": "completed",
                "assets": {
                    "GLB": {"path": "s3://example/job_solitaire_001.glb"},
                },
                "geometry_gates": [
                    {
                        "code": "minimum_structural_floors",
                        "status": "passed",
                        "executed_check": "minimum_structural_floors_v0",
                    },
                    {
                        "code": "stone_stone_collision",
                        "status": "passed",
                        "executed_check": "stone_collision_v0",
                        "calibrated": True,
                    },
                    {
                        "code": "future_non_pending_gate",
                        "status": "failed",
                        "reason_code": "ignored_extra_gate",
                    },
                ],
            }
        )

        package = solve_job(
            solitaire_spec(),
            geometry_adapter=rhino_compute_adapter(fake_client),
        )

        asset_by_target = {asset.target: asset for asset in package.assets}
        self.assertEqual(asset_by_target["GLB"].status, "generated")
        self.assertEqual(
            asset_by_target["GLB"].path,
            "s3://example/job_solitaire_001.glb",
        )
        self.assertEqual(asset_by_target["STL"].status, "pending_generation")

        gate_by_code = {gate.code: gate for gate in package.geometry_gates}
        self.assertEqual(gate_by_code["stone_stone_collision"].status, "passed")
        self.assertEqual(
            gate_by_code["stone_stone_collision"].reason_code,
            "calibrated_check_passed",
        )
        self.assertEqual(gate_by_code["minimum_structural_floors"].status, "pending")
        self.assertEqual(
            gate_by_code["minimum_structural_floors"].reason_code,
            "threshold_not_calibrated",
        )
        self.assertIn(
            "future_non_pending_gate",
            package.debug_trace["ignored_gate_codes"],
        )

    def test_rhino_compute_timeout_and_failure_map_to_failed_outputs(self):
        cases = [
            (
                {
                    "execution_status": "timeout",
                },
                "timeout",
                "rhino_compute_timeout",
            ),
            (
                {
                    "execution_status": "failed",
                    "reason_code": "rhino_definition_not_executed",
                },
                "failed",
                "rhino_definition_not_executed",
            ),
        ]
        for response, status, reason_code in cases:
            with self.subTest(status=status):
                fake_client = FakeHttpClient(response)

                package = solve_job(
                    solitaire_spec(),
                    geometry_adapter=rhino_compute_adapter(fake_client),
                )

                self.assertEqual(package.debug_trace["adapter_execution_status"], status)
                self.assertEqual(package.debug_trace["adapter_reason_code"], reason_code)
                self.assertEqual({asset.status for asset in package.assets}, {"failed"})
                self.assertEqual(
                    {gate.status for gate in package.geometry_gates},
                    {"failed"},
                )
                self.assertEqual(
                    {gate.reason_code for gate in package.geometry_gates},
                    {reason_code},
                )

    def test_rhino_compute_malformed_responses_fail_deterministically(self):
        malformed_responses = (
            None,
            {},
            {
                "execution_status": "completed",
                "assets": "not an asset mapping",
                "geometry_gates": [],
            },
        )
        for response in malformed_responses:
            with self.subTest(response=response):
                fake_client = FakeHttpClient(response)

                package = solve_job(
                    solitaire_spec(),
                    geometry_adapter=rhino_compute_adapter(fake_client),
                )

                self.assertEqual(
                    package.debug_trace["adapter_reason_code"],
                    "rhino_compute_response_malformed",
                )
                self.assertEqual({asset.status for asset in package.assets}, {"failed"})
                self.assertEqual(
                    {gate.reason_code for gate in package.geometry_gates},
                    {"rhino_compute_response_malformed"},
                )

    def test_solitaire_rejects_zero_center_stones(self):
        spec = solitaire_spec()
        spec["stones"][0]["role"] = "accent"

        self.assert_rejected_with(spec, "archetype_role_count_invalid", "$.stones")

    def test_solitaire_rejects_multiple_center_stones(self):
        spec = solitaire_spec()
        spec["stones"].append(stone("center_002", "center", 5.0, 5.0, 3.0))

        self.assert_rejected_with(spec, "archetype_role_count_invalid", "$.stones")

    def test_halo_rejects_missing_halo_stones(self):
        spec = halo_spec()
        spec["stones"] = [
            existing for existing in spec["stones"] if existing["role"] != "halo"
        ]

        self.assert_rejected_with(spec, "archetype_role_count_too_low", "$.stones")

    def test_trilogy_rejects_invalid_side_stone_counts(self):
        side_stone_counts = (0, 1, 3)
        for count in side_stone_counts:
            with self.subTest(side_stones=count):
                spec = trilogy_spec()
                spec["stones"] = [
                    existing for existing in spec["stones"] if existing["role"] != "side"
                ]
                spec["stones"].extend(
                    stone(f"side_extra_{index}", "side", 4.0, 4.0, 2.4)
                    for index in range(count)
                )

                self.assert_rejected_with(
                    spec,
                    "archetype_role_count_invalid",
                    "$.stones",
                )

    def test_rejects_zero_or_negative_numeric_fields(self):
        cases = [
            ("zero length", ("stones", 0, "dimensions_mm", "length"), 0, "$.stones[0].dimensions_mm.length"),
            ("negative width", ("stones", 0, "dimensions_mm", "width"), -1, "$.stones[0].dimensions_mm.width"),
            ("zero depth", ("stones", 0, "dimensions_mm", "depth"), 0, "$.stones[0].dimensions_mm.depth"),
            ("invalid carat", ("stones", 0, "carat"), -0.5, "$.stones[0].carat"),
            ("invalid constraint", ("constraints", "min_band_width_mm"), 0, "$.constraints.min_band_width_mm"),
        ]
        for label, path, value, issue_path in cases:
            with self.subTest(label=label):
                spec = solitaire_spec()
                if path[0] == "constraints":
                    spec["constraints"] = {}
                cursor = spec
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = value

                self.assert_rejected_with(
                    spec,
                    "schema_validation_failed",
                    issue_path,
                )

    def test_rejects_invalid_enum_like_inputs(self):
        cases = [
            ("archetype", ("job", "archetype"), "cluster", "$.job.archetype"),
            ("output target", ("job", "output_targets", 0), "OBJ", "$.job.output_targets[0]"),
            ("metal type", ("metal", "type"), "titanium", "$.metal.type"),
            ("center setting", ("setting", "center_setting"), "tension", "$.setting.center_setting"),
            ("stone shape", ("stones", 0, "shape"), "kite", "$.stones[0].shape"),
            ("finish", ("metal", "finish"), "hammered", "$.metal.finish"),
            ("ring size standard", ("ring", "size_standard"), "AU", "$.ring.size_standard"),
        ]
        for label, path, value, issue_path in cases:
            with self.subTest(label=label):
                spec = solitaire_spec()
                cursor = spec
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = value

                self.assert_rejected_with(
                    spec,
                    "schema_validation_failed",
                    issue_path,
                )

    def test_rejects_additional_properties_and_duplicate_targets(self):
        spec_with_extra_field = solitaire_spec()
        spec_with_extra_field["unexpected"] = True
        self.assert_rejected_with(
            spec_with_extra_field,
            "schema_validation_failed",
            "$",
        )

        spec_with_duplicate_targets = solitaire_spec()
        spec_with_duplicate_targets["job"]["output_targets"] = ["GLB", "GLB"]
        self.assert_rejected_with(
            spec_with_duplicate_targets,
            "schema_validation_failed",
            "$.job.output_targets",
        )

    def test_missing_manufacturing_profile_is_info_only(self):
        spec = solitaire_spec()
        del spec["manufacturing_profile"]

        result = validate_job_spec(spec)

        self.assertTrue(result.accepted, result.issues)
        self.assertIn("manufacturing_profile_unverified", {issue.code for issue in result.issues})
        self.assertEqual(
            {"info"},
            {issue.severity for issue in result.issues},
        )

    def test_local_json_schema_accepts_valid_launch_archetypes(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)

        for spec in (solitaire_spec(), halo_spec(), trilogy_spec()):
            with self.subTest(archetype=spec["job"]["archetype"]):
                self.assertEqual([], list(validator.iter_errors(spec)))

    def test_validator_uses_local_json_schema(self):
        spec = solitaire_spec()
        spec["metadata"]["unsupported"] = "not in schema"

        result = validate_job_spec(spec)

        self.assertFalse(result.accepted)
        self.assertIn("schema_validation_failed", {issue.code for issue in result.issues})
        self.assertIn("$.metadata", {issue.path for issue in result.issues})

    def test_contracts_cover_launch_archetypes(self):
        self.assertEqual(set(ARCHETYPE_CONTRACTS), {"solitaire", "halo", "trilogy"})

    def test_schema_and_contract_archetypes_match(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        schema_archetypes = set(schema["properties"]["job"]["properties"]["archetype"]["enum"])

        self.assertEqual(schema_archetypes, set(ARCHETYPE_CONTRACTS))

    def test_valid_specs_do_not_mutate_fixture_builders(self):
        original = solitaire_spec()
        mutated = deepcopy(original)
        mutated["job"]["job_id"] = "changed"

        self.assertEqual(solitaire_spec()["job"]["job_id"], original["job"]["job_id"])


if __name__ == "__main__":
    unittest.main()
