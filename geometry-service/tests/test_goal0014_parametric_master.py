"""Goal 0014 — parametric master derivation and the promotion gate.

The gate tests below are the artifact that prevents a fourth recurrence of the
"validating no-op" failure class. A guard never observed failing is not a guard,
so ``test_promotion_gate_rejects_the_frozen_v4_definition`` pins the gate's
verdict on the real, unmodified Goal 0011 V4 definition on disk.
"""

import copy
import hashlib
import json
import unittest
from pathlib import Path

from jewelry_gaas_foundation.constraints import _inner_diameter_mm
from jewelry_gaas_foundation.master_parameters import (
    BLOCK_BEGIN_MARKER,
    BLOCK_END_MARKER,
    GEOMETRIC_PARAMETER_KEYS,
    MASTER_PARAMETER_DERIVATION_VERSION,
    DIFFERENTIAL_BATCH,
    REFERENCE_REQUEST,
    MasterParameterError,
    assembly_metrics,
    configuration_request,
    derivation_block_source,
    derive_parameters,
    inner_diameter_mm,
)
from fast_mvp.preview_resolution import (
    MINIMUM_DIFFERENTIAL_CONFIGS,
    PreviewRegistryError,
    load_registry,
    validate_registry,
)
from scripts.promotion_gate import (
    PROBE_AXES,
    check_config_differential,
    check_parameter_derivation,
    evaluate,
    extract_definition_source,
)

ROOT = Path(__file__).resolve().parents[1]
FROZEN_V4_GHX = ROOT / "manual" / "rhino_compute_bridge" / "goal0011_realism_master_v4.ghx"
PARAMETRIC_V5_GHX = (
    ROOT / "manual" / "rhino_compute_bridge" / "goal0014_parametric_master_v5.ghx"
)
REGISTRY_PATH = ROOT / "fast_mvp" / "preview_assets" / "registry_v0_1.json"

#: The approved Goal 0011 V4 parameter literals, transcribed from the definition.
FROZEN_V4_PARAMETERS = {
    "inner_diameter": 16.91,
    "shank_width": 2.05,
    "shank_radial_thickness": 1.65,
    "shoulder_root_radius": 0.56,
    "shoulder_neck_radius": 0.28,
    "shoulder_split_half_gap_root": 0.28,
    "shoulder_split_half_gap_neck": 1.30815,
    "shoulder_contact_z": 7.72,
    "stone_diameter": 6.50,
    "stone_total_depth": 3.90,
    "stone_table_ratio": 0.56,
    "stone_crown_height": 0.96,
    "stone_girdle_thickness": 0.18,
    "stone_girdle_z": 12.10,
    "basket_lower_radius": 1.32,
    "basket_upper_radius": 2.42,
    "basket_lower_z": 9.70,
    "basket_upper_z": 10.90,
    "gallery_wire_radius": 0.20,
    "prong_root_radius": 0.30,
    "prong_seat_radius": 0.235,
    "prong_tip_radius": 0.17,
    "prong_tip_height": 0.48,
}

#: The inner-diameter correction is the only intended departure from frozen V4.
INNER_DIAMETER_CORRECTION_MM = 0.0032


request_for = configuration_request


class FrozenV4DefectTest(unittest.TestCase):
    """Pin the defect itself, so a future edit cannot quietly reintroduce it."""

    def test_frozen_v4_source_uses_the_request_only_for_template_id(self):
        source = extract_definition_source(FROZEN_V4_GHX)
        request_lines = [
            line.strip() for line in source.splitlines() if "request" in line
        ]
        self.assertEqual(len(request_lines), 2, request_lines)
        self.assertIn("request = json.loads(str(json_string))", request_lines[0])
        self.assertIn("template_id", request_lines[1])
        self.assertNotIn(BLOCK_BEGIN_MARKER, source)

    def test_frozen_v4_parameter_dict_is_never_mutated(self):
        source = extract_definition_source(FROZEN_V4_GHX)
        for mutation in ("P.update(", "P.clear()", "P[", "global P"):
            with self.subTest(mutation=mutation):
                if mutation == "P[":
                    # P[...] appears as reads; assert none of them are assignments.
                    assignments = [
                        line
                        for line in source.splitlines()
                        if line.strip().startswith("P[") and "=" in line
                    ]
                    self.assertEqual(assignments, [])
                else:
                    self.assertNotIn(mutation, source)


class PromotionGateTest(unittest.TestCase):
    def test_promotion_gate_rejects_the_frozen_v4_definition(self):
        """The guard, observed failing against the real frozen V4 definition."""
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        report = evaluate(FROZEN_V4_GHX, registry["assets"][0])
        self.assertEqual(report["status"], "fail")
        derivation = report["checks"]["parameter_derivation"]
        self.assertEqual(derivation["status"], "fail")
        self.assertEqual(derivation["reason_code"], "no_parameter_derivation_block")
        self.assertEqual(
            sorted(derivation["unresponsive_axes"]),
            sorted(axis for axis, _, _, _ in PROBE_AXES),
        )
        self.assertEqual(derivation["responsive_axes"], [])
        self.assertFalse(report["manufacturing_authority"])

    def test_promotion_gate_accepts_the_parametric_definition(self):
        report = evaluate(PARAMETRIC_V5_GHX)
        derivation = report["checks"]["parameter_derivation"]
        self.assertEqual(derivation["status"], "pass")
        self.assertEqual(derivation["unresponsive_axes"], [])
        self.assertEqual(
            sorted(derivation["responsive_axes"]),
            sorted(axis for axis, _, _, _ in PROBE_AXES),
        )

    def test_gate_detects_a_block_that_ignores_one_axis(self):
        """The axis probe is not vacuous: neutering a single axis fails the gate."""
        block = derivation_block_source()
        neutered = block.replace(
            'us_size = _in_range(\n        _number(ring, "size", "ring.size")',
            'us_size = _in_range(\n        REF_US_SIZE + 0.0 * _number(ring, "size", "ring.size")',
        )
        self.assertNotEqual(neutered, block)
        result = check_parameter_derivation(neutered)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["reason_code"], "parameters_ignore_typed_axes")
        self.assertEqual(result["unresponsive_axes"], ["ring_size"])

    def test_gate_rejects_a_definition_with_no_executable_block(self):
        source = BLOCK_BEGIN_MARKER + "\nthis is not python\n" + BLOCK_END_MARKER
        result = check_parameter_derivation(source)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["reason_code"], "derivation_block_not_executable")

    def test_gate_rejects_a_block_without_derive_parameters(self):
        source = BLOCK_BEGIN_MARKER + "\nx = 1\n" + BLOCK_END_MARKER
        result = check_parameter_derivation(source)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(
            result["reason_code"], "derivation_block_exposes_no_derive_parameters"
        )


class ConfigDifferentialCheckTest(unittest.TestCase):
    def base_asset(self, kind, differential):
        return {
            "asset_id": "probe",
            "match": {"policy": "discrete_signature"},
            "provenance": {"definition_kind": kind},
            "approval": {"config_differential": differential},
        }

    def test_single_configuration_master_must_be_signature_bound(self):
        asset = self.base_asset(
            "single_configuration_master", {"status": "absent", "reason": "frozen"}
        )
        self.assertEqual(check_config_differential(asset)["status"], "pass")
        asset["match"]["policy"] = "bounded_projection"
        result = check_config_differential(asset)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(
            result["reason_code"], "single_configuration_master_is_not_signature_bound"
        )

    def test_parametric_master_without_a_differential_is_rejected(self):
        asset = self.base_asset(
            "parametric_master", {"status": "absent", "reason": "not run yet"}
        )
        result = check_config_differential(asset)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["reason_code"], "config_differential_not_passed")

    def test_parametric_master_report_must_exist_and_match_its_hash(self):
        asset = self.base_asset(
            "parametric_master",
            {
                "status": "passed",
                "reason": "azure batch",
                "report_path": "output/does_not_exist.json",
                "report_sha256": "0" * 64,
                "config_count": 3,
            },
        )
        result = check_config_differential(asset)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(
            result["reason_code"], "config_differential_report_missing_on_disk"
        )


class RegistryPromotionPolicyTest(unittest.TestCase):
    def test_production_registry_records_v4_as_a_single_configuration_master(self):
        asset = load_registry(REGISTRY_PATH, production=True)["assets"][0]
        provenance = asset["provenance"]
        self.assertEqual(
            provenance["definition_kind"], "single_configuration_master"
        )
        self.assertEqual(
            provenance["definition_path"],
            "manual/rhino_compute_bridge/goal0011_realism_master_v4.ghx",
        )
        self.assertEqual(
            provenance["definition_sha256"],
            hashlib.sha256(FROZEN_V4_GHX.read_bytes()).hexdigest(),
        )
        differential = asset["approval"]["config_differential"]
        self.assertEqual(differential["status"], "absent")
        self.assertEqual(differential["config_count"], 0)
        self.assertIn("byte-identical geometry", differential["reason"])
        # The single-configuration master stays bound to one discrete signature.
        self.assertEqual(asset["match"]["policy"], "discrete_signature")

    def mutated_registry(self, mutate):
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        mutate(registry["assets"][0])
        return registry

    def test_a_parametric_master_cannot_be_promoted_without_a_differential(self):
        def mutate(asset):
            asset["provenance"]["definition_kind"] = "parametric_master"

        with self.assertRaises(PreviewRegistryError):
            validate_registry(self.mutated_registry(mutate), production=False)

    def test_a_parametric_master_cannot_be_promoted_on_too_few_configs(self):
        def mutate(asset):
            asset["provenance"]["definition_kind"] = "parametric_master"
            asset["approval"]["config_differential"] = {
                "status": "passed",
                "reason": "two configs only",
                "report_path": "output/report.json",
                "report_sha256": "a" * 64,
                "config_count": MINIMUM_DIFFERENTIAL_CONFIGS - 1,
            }

        with self.assertRaises(PreviewRegistryError):
            validate_registry(self.mutated_registry(mutate), production=False)

    def test_a_single_configuration_master_cannot_claim_a_differential(self):
        def mutate(asset):
            asset["approval"]["config_differential"] = {
                "status": "passed",
                "reason": "claimed",
                "report_path": "output/report.json",
                "report_sha256": "a" * 64,
                "config_count": 3,
            }

        with self.assertRaises(PreviewRegistryError):
            validate_registry(self.mutated_registry(mutate), production=False)


class DerivationTest(unittest.TestCase):
    def test_reference_request_reproduces_the_approved_v4_shape(self):
        parameters = derive_parameters(REFERENCE_REQUEST)
        band_anchored = {
            "basket_lower_z",
            "basket_upper_z",
            "stone_girdle_z",
            "shoulder_contact_z",
        }
        for key, frozen in sorted(FROZEN_V4_PARAMETERS.items()):
            with self.subTest(parameter=key):
                delta = abs(parameters[key] - frozen)
                if key == "inner_diameter":
                    self.assertAlmostEqual(delta, INNER_DIAMETER_CORRECTION_MM, places=9)
                elif key in band_anchored:
                    # Half the inner-diameter correction reaches the band's top.
                    self.assertLessEqual(delta, INNER_DIAMETER_CORRECTION_MM / 2.0 + 1e-9)
                else:
                    self.assertLess(delta, 1e-5)

    def test_inner_diameter_matches_the_verified_constraint_formula(self):
        for us_size in (3.0, 4.0, 6.5, 9.0, 13.5):
            with self.subTest(us_size=us_size):
                self.assertAlmostEqual(
                    inner_diameter_mm(us_size),
                    _inner_diameter_mm({"size": us_size, "size_standard": "US"}),
                    places=12,
                )
        self.assertAlmostEqual(inner_diameter_mm(6.5), 16.9132, places=9)

    def test_every_geometric_parameter_responds_to_the_typed_request(self):
        small = derive_parameters(request_for(4.0, 5.00, 3.00, 1.6, 1.2))
        large = derive_parameters(request_for(9.0, 8.00, 4.80, 3.0, 2.4))
        for key in GEOMETRIC_PARAMETER_KEYS:
            with self.subTest(parameter=key):
                self.assertNotEqual(small[key], large[key])

    def test_the_planned_azure_batch_configs_differ_pairwise(self):
        derived = {
            name: derive_parameters(request) for name, request in DIFFERENTIAL_BATCH.items()
        }
        names = sorted(derived)
        for index, left in enumerate(names):
            for right in names[index + 1 :]:
                with self.subTest(pair=(left, right)):
                    self.assertNotEqual(
                        json.dumps(derived[left], sort_keys=True),
                        json.dumps(derived[right], sort_keys=True),
                    )
                    for key in ("inner_diameter", "stone_diameter", "stone_girdle_z"):
                        self.assertNotEqual(derived[left][key], derived[right][key])

    def test_assembly_holds_together_across_the_approved_envelope(self):
        cases = {
            "control": request_for(6.5, 6.50, 3.90),
            "small_extreme": request_for(4.0, 5.00, 3.00),
            "large_extreme": request_for(9.0, 8.00, 4.80),
            "min_envelope": request_for(3.0, 3.00, 1.80, 1.20, 0.90),
            "max_envelope": request_for(13.5, 11.00, 6.60, 4.00, 3.00),
        }
        for name, request in cases.items():
            with self.subTest(case=name):
                metrics = assembly_metrics(derive_parameters(request))
                self.assertTrue(metrics["source_preflight_ready"], metrics)
                self.assertFalse(metrics["manufacturing_authority"])
                self.assertLess(metrics["shoulder_end_petal_centerline_error_mm"], 1e-9)

    def test_head_scales_with_the_stone_and_rises_with_the_band(self):
        base = derive_parameters(request_for(6.5, 6.50, 3.90))
        bigger_stone = derive_parameters(request_for(6.5, 8.00, 4.80))
        bigger_finger = derive_parameters(request_for(9.0, 6.50, 3.90))
        self.assertGreater(
            bigger_stone["basket_upper_radius"], base["basket_upper_radius"]
        )
        self.assertAlmostEqual(
            bigger_finger["basket_upper_radius"], base["basket_upper_radius"], places=9
        )
        self.assertGreater(bigger_finger["stone_girdle_z"], base["stone_girdle_z"])
        self.assertAlmostEqual(
            bigger_finger["stone_girdle_z"] - base["stone_girdle_z"],
            (bigger_finger["inner_diameter"] - base["inner_diameter"]) / 2.0,
            places=9,
        )

    def test_metal_never_reaches_the_derived_geometry(self):
        request = request_for(6.5, 6.50, 3.90)
        baseline = derive_parameters(request)
        request["canonical_spec"]["metal"] = {
            "color": "yellow",
            "karat": 18,
            "finish": "brushed",
        }
        self.assertEqual(derive_parameters(request), baseline)


class DerivationRefusalTest(unittest.TestCase):
    def assert_refused(self, request):
        with self.assertRaises(MasterParameterError):
            derive_parameters(request)

    def test_sizes_outside_the_approved_envelope_are_refused(self):
        for us_size in (2.0, 14.0, -1.0):
            with self.subTest(us_size=us_size):
                self.assert_refused(request_for(us_size, 6.50, 3.90))

    def test_stone_dimensions_outside_the_envelope_are_refused(self):
        self.assert_refused(request_for(6.5, 2.0, 1.2))
        self.assert_refused(request_for(6.5, 12.0, 7.2))
        self.assert_refused(request_for(6.5, 6.5, 2.6))  # depth ratio 0.40
        self.assert_refused(request_for(6.5, 6.5, 5.2))  # depth ratio 0.80

    def test_non_round_and_non_solitaire_requests_are_refused(self):
        oval = request_for(6.5, 6.50, 3.90)
        oval["canonical_spec"]["stones"][0]["shape"] = "oval"
        self.assert_refused(oval)

        mismatched = request_for(6.5, 6.50, 3.90)
        mismatched["canonical_spec"]["stones"][0]["dimensions_mm"]["width"] = 5.0
        self.assert_refused(mismatched)

        trilogy = request_for(6.5, 6.50, 3.90)
        side = copy.deepcopy(trilogy["canonical_spec"]["stones"][0])
        side["stone_id"] = "side"
        side["role"] = "side"
        trilogy["canonical_spec"]["stones"].append(side)
        self.assert_refused(trilogy)

    def test_malformed_and_non_finite_requests_are_refused(self):
        self.assert_refused({})
        self.assert_refused({"canonical_spec": {}})
        for bad in (float("nan"), float("inf"), "6.5", True, None):
            with self.subTest(size=bad):
                request = request_for(6.5, 6.50, 3.90)
                request["canonical_spec"]["ring"]["size"] = bad
                self.assert_refused(request)

    def test_non_us_size_standard_is_refused(self):
        request = request_for(6.5, 6.50, 3.90)
        request["canonical_spec"]["ring"]["size_standard"] = "JP"
        self.assert_refused(request)


class DefinitionEmbeddingTest(unittest.TestCase):
    def test_v5_embeds_the_derivation_block_byte_identically(self):
        embedded = extract_definition_source(PARAMETRIC_V5_GHX)
        begin = embedded.index(BLOCK_BEGIN_MARKER)
        end = embedded.index(BLOCK_END_MARKER) + len(BLOCK_END_MARKER)
        self.assertEqual(embedded[begin:end], derivation_block_source())

    def test_v5_geometry_code_holds_no_frozen_coordinates(self):
        embedded = extract_definition_source(PARAMETRIC_V5_GHX)
        geometry = embedded[embedded.index(BLOCK_END_MARKER) + len(BLOCK_END_MARKER):]
        for literal in ("5.35", "4.82", "1.30815", "12.10", "9.70", "10.90", "16.91"):
            with self.subTest(literal=literal):
                self.assertNotIn(literal, geometry)

    def test_v5_derives_and_stamps_the_parameter_dict_at_run_time(self):
        embedded = extract_definition_source(PARAMETRIC_V5_GHX)
        self.assertIn("P.clear()", embedded)
        self.assertIn("P.update(derive_parameters(request))", embedded)
        self.assertIn("metrics = assembly_metrics(P)", embedded)
        self.assertIn(MASTER_PARAMETER_DERIVATION_VERSION, embedded)
        self.assertIn('"request": request', embedded)
        self.assertIn("P = {}", embedded)

    def test_v5_preserves_the_approved_v4_part_names(self):
        v4 = extract_definition_source(FROZEN_V4_GHX)
        v5 = extract_definition_source(PARAMETRIC_V5_GHX)
        for name in (
            '("shank", "metal"',
            '"shoulder_front_"',
            '"shoulder_rear_"',
            '("basket_lower_rail", "metal"',
            '("stone_seat_rail", "metal"',
            '"basket_petal_{:02d}"',
            '"prong_{:02d}"',
            '"prong_tip_{:02d}"',
            '("center_stone_round_brilliant", "stone"',
        ):
            with self.subTest(part=name):
                self.assertIn(name, v4)
                self.assertIn(name, v5)

    def test_v5_rebuild_reproduces_the_committed_source(self):
        """GHX wrappers carry fresh component GUIDs; the embedded source must not drift."""
        from manual.tools.build_goal0014_parametric_master_ghx import build

        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            first = extract_definition_source(build(Path(directory) / "a.ghx"))
            second = extract_definition_source(build(Path(directory) / "b.ghx"))
        self.assertEqual(first, second)
        self.assertEqual(first, extract_definition_source(PARAMETRIC_V5_GHX))


class DifferentialToolBoundaryTest(unittest.TestCase):
    """The version differential structurally cannot stand in for a config differential."""

    def test_goal0011_differential_is_bounded_to_one_configuration(self):
        source = (ROOT / "scripts" / "goal0011_differential.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("deliberately bounded", source)
        self.assertIn("6.5 mm round / US 6.5", source)
        # It compares two definition *versions* of one configuration.
        self.assertIn("V2_TEMPLATE_ID", source)
        self.assertIn("V4_TEMPLATE_ID", source)

    def test_geometry_differential_compares_across_configs(self):
        source = (ROOT / "scripts" / "geometry_differential.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("across configs", source)


if __name__ == "__main__":
    unittest.main()
