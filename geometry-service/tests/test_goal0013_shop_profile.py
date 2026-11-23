"""Goal 0013 Part B — shop-profile intake and calibrated constraint thresholds.

Two properties dominate this file:

1. **With no profile loaded, behavior is byte-identical to before profiles existed.**
   The messages below are pinned verbatim, so a refactor cannot quietly reword a
   customer-visible constraint message.
2. **A profile alone can never certify manufacturing.** It supplies thresholds so a
   check can run with real numbers; pass/fail still requires a calibrated profile
   AND human jeweler review, per the brand-protecting rule.
"""

import copy
import json
import unittest
from datetime import date
from pathlib import Path

from jewelry_gaas_foundation import constraints
from jewelry_gaas_foundation.geometry_adapter import _map_gate_status
from jewelry_gaas_foundation.shop_profile import (
    PROFILE_SCHEMA,
    SHOP_PROFILE_SCHEMA_PATH,
    THRESHOLD_RULES,
    UNCALIBRATED_TEMPLATE_PATH,
    ShopProfile,
    ShopProfileError,
    active_profile,
    active_threshold,
    clear_active_profile,
    load_profile,
    set_active_profile,
    uncalibrated_template,
)

ROOT = Path(__file__).resolve().parents[1]
SURVEY = ROOT / "docs" / "casting_shop_survey_2026-08-04.md"


def thin_band_spec() -> dict:
    """A spec that trips BAND-001 and BAND-002 against either threshold set."""
    return {
        "job": {"job_id": "shop-profile-test", "archetype": "solitaire"},
        "ring": {
            "size": 6.5,
            "size_standard": "US",
            "band_width_mm": 1.4,
            "band_thickness_mm": 1.1,
            "profile": "comfort fit",
            "band_profile": "comfort_fit",
        },
        "metal": {"type": "gold", "alloy": "14k_yellow", "purity": "14k"},
        "stones": [
            {
                "stone_id": "center",
                "role": "center",
                "shape": "round",
                "quantity": 1,
                "dimensions_mm": {"length": 6.5, "width": 6.5, "depth": 3.9},
            }
        ],
        "setting": {"center_setting": "prong", "prong_count": 4},
    }


def calibrated_profile(**overrides) -> ShopProfile:
    """Build a schema-valid CALIBRATED profile for testing only."""
    document = {
        "schema": PROFILE_SCHEMA,
        "profile_id": "test-shop",
        "profile_version": "v1.0.0",
        "status": "calibrated",
        "shop": {"name": "Test Shop (fixture)", "anonymized": True},
        "validity": {"valid_from": "2026-01-01", "expires_on": "2099-01-01"},
        "process": {"route": "resin_print_to_investment"},
        "measurement_basis": "as_modeled",
        "minimums": {
            "min_shank_width_mm": [
                {
                    "value": 1.25,
                    "units": "mm",
                    "status": "calibrated",
                    "committed_in_writing": True,
                    "alloy_scope": ["*"],
                    "unsupported_span_mm": 4.0,
                    "measurement_basis": "as_modeled",
                    "provenance": {
                        "source": "Test Shop written spec",
                        "attributed_to": "caster",
                        "recorded_on": "2026-08-04",
                    },
                }
            ],
            "min_shank_thickness_mm": [
                {
                    "value": 1.9,
                    "units": "mm",
                    "status": "calibrated",
                    "committed_in_writing": True,
                    "alloy_scope": ["platinum_950"],
                    "unsupported_span_mm": 4.0,
                    "measurement_basis": "as_modeled",
                    "provenance": {
                        "source": "Test Shop written spec",
                        "recorded_on": "2026-08-04",
                    },
                }
            ],
        },
        "certifies_manufacturing": False,
        "manufacturing_authority": False,
    }
    document.update(overrides)
    return ShopProfile(document)


class ProfileIsolationMixin:
    def setUp(self):
        clear_active_profile()
        self.addCleanup(clear_active_profile)


class SchemaAndTemplateTest(unittest.TestCase):
    def test_schema_is_valid_draft_2020_12(self):
        from jsonschema import Draft202012Validator

        schema = json.loads(SHOP_PROFILE_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertFalse(schema["additionalProperties"])
        Draft202012Validator.check_schema(schema)

    def test_shipped_template_is_explicitly_uncalibrated_and_not_a_real_shop(self):
        raw = json.loads(UNCALIBRATED_TEMPLATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(raw["status"], "uncalibrated")
        self.assertTrue(raw["shop"]["anonymized"])
        self.assertIn("NOT A REAL SHOP", raw["shop"]["name"])
        self.assertIs(raw["certifies_manufacturing"], False)
        self.assertIs(raw["manufacturing_authority"], False)
        for key, entries in raw["minimums"].items():
            for entry in entries:
                with self.subTest(key=key):
                    self.assertEqual(entry["status"], "uncalibrated")
                    self.assertFalse(entry["committed_in_writing"])
                    self.assertEqual(entry["provenance"]["source"], "uncalibrated_placeholder")

    def test_template_yields_no_usable_thresholds(self):
        profile = uncalibrated_template()
        self.assertEqual(profile.usable_thresholds("gold_14k"), {})
        for key in THRESHOLD_RULES:
            with self.subTest(key=key):
                self.assertFalse(profile.threshold(key, alloy="gold_14k").usable)

    def test_a_profile_cannot_declare_itself_certifying(self):
        document = calibrated_profile().document
        document["certifies_manufacturing"] = True
        with self.assertRaises(ShopProfileError):
            ShopProfile(document)

    def test_survey_and_schema_stay_connected(self):
        self.assertTrue(SURVEY.is_file(), "the survey is the intake instrument")
        schema = SHOP_PROFILE_SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertIn("casting_shop_survey_2026-08-04.md", schema)


class ThresholdResolutionTest(ProfileIsolationMixin, unittest.TestCase):
    def test_a_fully_attributed_value_is_usable(self):
        resolved = calibrated_profile().threshold("min_shank_width_mm", alloy="gold_14k")
        self.assertTrue(resolved.usable)
        self.assertEqual(resolved.value, 1.25)
        self.assertEqual(resolved.source, "Test Shop written spec")

    def test_uncommitted_values_are_refused(self):
        document = calibrated_profile().document
        document["minimums"]["min_shank_width_mm"][0]["committed_in_writing"] = False
        resolved = ShopProfile(document).threshold("min_shank_width_mm", alloy="gold_14k")
        self.assertFalse(resolved.usable)
        self.assertEqual(resolved.reason, "not_committed_in_writing")

    def test_values_without_an_unsupported_span_are_refused(self):
        document = calibrated_profile().document
        document["minimums"]["min_shank_width_mm"][0]["unsupported_span_mm"] = None
        resolved = ShopProfile(document).threshold("min_shank_width_mm", alloy="gold_14k")
        self.assertFalse(resolved.usable)
        self.assertEqual(resolved.reason, "no_unsupported_span_qualifier")

    def test_an_uncalibrated_profile_refuses_even_calibrated_values(self):
        document = calibrated_profile().document
        document["status"] = "uncalibrated"
        resolved = ShopProfile(document).threshold("min_shank_width_mm", alloy="gold_14k")
        self.assertFalse(resolved.usable)
        self.assertEqual(resolved.reason, "profile_uncalibrated")

    def test_an_expired_profile_refuses_everything(self):
        document = calibrated_profile().document
        document["validity"] = {"valid_from": "2020-01-01", "expires_on": "2020-02-01"}
        profile = ShopProfile(document)
        self.assertTrue(profile.expired_on(date(2026, 8, 4)))
        resolved = profile.threshold(
            "min_shank_width_mm", alloy="gold_14k", today=date(2026, 8, 4)
        )
        self.assertFalse(resolved.usable)
        self.assertEqual(resolved.reason, "profile_expired")

    def test_alloy_scoping_is_respected(self):
        profile = calibrated_profile()
        platinum = profile.threshold("min_shank_thickness_mm", alloy="platinum_950")
        gold = profile.threshold("min_shank_thickness_mm", alloy="gold_14k")
        self.assertTrue(platinum.usable)
        self.assertEqual(platinum.value, 1.9)
        self.assertFalse(gold.usable)
        self.assertEqual(gold.reason, "no_value_for_alloy")

    def test_missing_keys_and_unknown_keys(self):
        profile = calibrated_profile()
        self.assertEqual(
            profile.threshold("min_gap_between_features_mm").reason, "no_value_recorded"
        )
        with self.assertRaises(ShopProfileError):
            profile.threshold("not_a_real_threshold")

    def test_active_profile_registry_round_trip(self):
        self.assertIsNone(active_profile())
        self.assertEqual(active_threshold("min_shank_width_mm").reason, "no_active_profile")
        profile = calibrated_profile()
        set_active_profile(profile)
        self.assertIs(active_profile(), profile)
        self.assertTrue(active_threshold("min_shank_width_mm", alloy="gold_14k").usable)
        clear_active_profile()
        self.assertIsNone(active_profile())
        with self.assertRaises(ShopProfileError):
            set_active_profile("not a profile")  # type: ignore[arg-type]


class UncalibratedBehaviorIsUnchangedTest(ProfileIsolationMixin, unittest.TestCase):
    """The exact wording these rules produced before profiles existed."""

    PINNED = {
        "BAND-001": (
            "Band width 1.4 mm is below MyJewl's uncalibrated 1.8 mm daily-wear "
            "design baseline. A shop-specific structural minimum is required.",
            "Increase ring.band_width_mm to 1.8 or obtain calibrated shop approval.",
        ),
        "BAND-002": (
            "Band thickness 1.1 mm is below MyJewl's uncalibrated 1.5 mm design "
            "baseline. Production loss depends on the shop process.",
            "Increase ring.band_thickness_mm to 1.5 or obtain calibrated shop approval.",
        ),
        "BAND-004": (
            "Comfort-fit profiles require a minimum center thickness of 1.5 mm. "
            "Current thickness 1.1 mm is insufficient.",
            "Set ring.band_thickness_mm ≥ 1.5.",
        ),
    }

    def test_messages_are_byte_identical_with_no_profile(self):
        result = constraints.validate(thin_band_spec())
        by_id = {item.rule_id: item for item in result.soft_warnings}
        for rule_id, (message, remedy) in self.PINNED.items():
            with self.subTest(rule=rule_id):
                self.assertIn(rule_id, by_id)
                self.assertEqual(by_id[rule_id].message, message)
                self.assertEqual(by_id[rule_id].suggested_fix, remedy)

    def test_loading_the_uncalibrated_template_changes_nothing(self):
        before = constraints.validate(thin_band_spec())
        set_active_profile(uncalibrated_template())
        after = constraints.validate(thin_band_spec())
        self.assertEqual(
            [(v.rule_id, v.tier, v.message) for v in before.soft_warnings],
            [(v.rule_id, v.tier, v.message) for v in after.soft_warnings],
        )
        self.assertEqual(before.accepted, after.accepted)


class CalibratedBehaviorTest(ProfileIsolationMixin, unittest.TestCase):
    def test_a_calibrated_threshold_replaces_the_baseline_and_names_the_profile(self):
        set_active_profile(calibrated_profile())
        result = constraints.validate(thin_band_spec())
        by_id = {item.rule_id: item for item in result.soft_warnings}
        # Shop allows 1.25 mm, the 1.4 mm band now clears BAND-001.
        self.assertNotIn("BAND-001", by_id)
        # Shop gave no gold thickness, so BAND-002 keeps the uncalibrated baseline.
        self.assertIn("BAND-002", by_id)
        self.assertIn("MyJewl's uncalibrated 1.5 mm", by_id["BAND-002"].message)

    def test_a_stricter_calibrated_threshold_is_attributed_in_the_message(self):
        spec = thin_band_spec()
        spec["metal"] = {"type": "platinum", "alloy": "platinum_950", "purity": "950"}
        set_active_profile(calibrated_profile())
        result = constraints.validate(spec)
        band_002 = next(
            item for item in result.soft_warnings if item.rule_id == "BAND-002"
        )
        self.assertIn("calibrated 1.9 mm", band_002.message)
        self.assertIn("test-shop", band_002.message)
        self.assertIn("not a manufacturing verdict", band_002.message)

    def test_rule_ids_and_severities_are_unchanged_by_a_profile(self):
        """A profile may change a threshold; it may never re-tier a rule."""
        spec = thin_band_spec()
        before = {v.rule_id: v.tier for v in constraints.validate(spec).soft_warnings}
        set_active_profile(calibrated_profile())
        after = {v.rule_id: v.tier for v in constraints.validate(spec).soft_warnings}

        shared = set(before) & set(after)
        self.assertTrue(shared, "expected overlapping rules to compare tiers against")
        for rule_id in sorted(shared):
            with self.subTest(rule=rule_id):
                self.assertEqual(before[rule_id], after[rule_id])
        # A profile may only relax or tighten which rules fire, never invent one.
        self.assertTrue(set(after) <= set(before))
        for tier in after.values():
            self.assertEqual(tier, "SOFT")

    def test_validate_signature_is_unchanged(self):
        import inspect

        signature = inspect.signature(constraints.validate)
        self.assertEqual(list(signature.parameters), ["spec"])


class ProfileAloneCannotCertifyTest(ProfileIsolationMixin, unittest.TestCase):
    """The non-negotiable safety property, asserted adversarially."""

    def test_a_profile_cannot_flip_a_geometry_gate_to_passed(self):
        """Only the geometry adapter's own calibrated result can pass a gate."""
        set_active_profile(calibrated_profile())
        claimed = _map_gate_status("structural_floor", {"status": "passed"})
        self.assertEqual(claimed.status, "pending")
        self.assertEqual(claimed.reason_code, "threshold_not_calibrated")

        # The one route to "passed" runs entirely through the adapter response and
        # is unaffected by whether a profile happens to be loaded.
        adapter_calibrated = {
            "status": "passed",
            "executed_check": "structural_floor",
            "calibrated": True,
        }
        with_profile = _map_gate_status("structural_floor", dict(adapter_calibrated))
        clear_active_profile()
        without_profile = _map_gate_status("structural_floor", dict(adapter_calibrated))
        self.assertEqual(with_profile.status, without_profile.status)
        self.assertEqual(with_profile.reason_code, without_profile.reason_code)

    def test_loading_a_profile_never_produces_a_passed_gate(self):
        for payload in (
            {"status": "passed"},
            {"status": "passed", "executed_check": "structural_floor"},
            {"status": "passed", "calibrated": True},
        ):
            with self.subTest(payload=payload):
                clear_active_profile()
                baseline = _map_gate_status("structural_floor", copy.deepcopy(payload))
                set_active_profile(calibrated_profile())
                with_profile = _map_gate_status("structural_floor", copy.deepcopy(payload))
                self.assertEqual(baseline.status, with_profile.status)
                self.assertNotEqual(with_profile.status, "passed")

    def test_a_profile_never_removes_or_downgrades_a_hard_violation(self):
        spec = thin_band_spec()
        spec["setting"]["prong_count"] = 2  # trips a HARD prong-count rule
        before = constraints.validate(spec)
        set_active_profile(calibrated_profile())
        after = constraints.validate(spec)
        self.assertEqual(
            sorted(item.rule_id for item in before.hard_violations),
            sorted(item.rule_id for item in after.hard_violations),
        )
        self.assertEqual(before.accepted, after.accepted)

    def test_stone_metal_collision_gate_stays_needs_calibration(self):
        from jewelry_gaas_foundation import stl_gates

        source = Path(stl_gates.__file__).read_text(encoding="utf-8")
        self.assertIn("needs_calibration", source)
        self.assertNotIn("shop_profile", source)

    def test_threshold_payloads_declare_they_do_not_certify(self):
        resolved = calibrated_profile().threshold("min_shank_width_mm", alloy="gold_14k")
        self.assertIs(resolved.to_json()["certifies_manufacturing"], False)


class LoaderTest(unittest.TestCase):
    def test_load_profile_rejects_malformed_documents(self):
        with self.assertRaises(ShopProfileError):
            load_profile(ROOT / "does_not_exist.json")
        with self.assertRaises(ShopProfileError):
            ShopProfile({"schema": "wrong"})
        with self.assertRaises(ShopProfileError):
            ShopProfile([])  # type: ignore[arg-type]

    def test_document_is_a_defensive_copy(self):
        profile = calibrated_profile()
        snapshot = profile.document
        snapshot["minimums"]["min_shank_width_mm"][0]["value"] = 99.0
        self.assertEqual(
            profile.threshold("min_shank_width_mm", alloy="gold_14k").value, 1.25
        )


if __name__ == "__main__":
    unittest.main()
