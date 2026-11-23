"""Goal 0007 modular ring-design contract tests."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from fast_mvp import manual_configurator, workflow
from jewelry_gaas_foundation import constraints
from jewelry_gaas_foundation.config_adapter import flat_to_canonical
from jewelry_gaas_foundation.validation import validate_job_spec


BASE = {
    "stone_shape": "round",
    "stone_length_mm": 6.5,
    "stone_width_mm": 6.5,
    "stone_depth_mm": 3.9,
    "stone_carat_eq": 1.0,
    "ring_size_us": 6.5,
    "metal": {
        "color_family": "yellow",
        "purity": "14k",
        "finish": "polished",
    },
}

TRILOGY_SELECTION = {
    "head_style_id": "head_petal_trilogy_4_prong",
    "band_style_id": "band_knife_edge_half_pave",
}


class ModularCatalogContractTests(unittest.TestCase):
    def test_bounded_reference_is_internal_and_fully_structured(self):
        options = manual_configurator.compatible_options(BASE)
        reference = next(
            item
            for item in options["reference_configurations"]
            if item["reference_configuration_id"]
            == "reference_petal_trilogy_knife_pave_v1"
        )
        self.assertEqual(reference["head_style_id"], TRILOGY_SELECTION["head_style_id"])
        self.assertEqual(reference["band_style_id"], TRILOGY_SELECTION["band_style_id"])
        self.assertEqual(reference["authority"], "myjewl_internal")
        self.assertNotIn("blue nile", str(reference).lower())

        head = next(
            item
            for item in options["head_styles"]
            if item["head_style_id"] == TRILOGY_SELECTION["head_style_id"]
        )
        self.assertEqual(head["architecture"], "trilogy")
        self.assertEqual(head["head_family"], "petal")
        self.assertEqual(head["head_geometry"], "petal_prong")
        self.assertEqual(head["gallery_structure"], "petal_open")
        side = head["side_stone_schedules"][0]
        self.assertEqual(side["role"], "side")
        self.assertEqual(side["shape"], "pear")
        self.assertEqual(side["quantity"], 2)
        self.assertEqual(side["placement"]["pattern"], "symmetric_pair")
        self.assertEqual(side["placement"]["orientation"], "points_outward")

        band = next(
            item
            for item in options["band_styles_by_head"][head["head_style_id"]]
            if item["band_style_id"] == TRILOGY_SELECTION["band_style_id"]
        )
        self.assertEqual(band["band_profile"], "knife_edge")
        self.assertEqual(band["shoulder_style"], "tapered_cathedral")
        self.assertEqual(band["pave"]["coverage"], "half")
        accent = band["pave"]["accent_stone_schedule"]
        self.assertEqual(accent["role"], "accent")
        self.assertEqual(accent["shape"], "round")
        self.assertGreater(accent["quantity"], 0)
        self.assertEqual(accent["placement"]["region"], "band")

    def test_unknown_or_incompatible_ids_are_rejected_without_substitution(self):
        with self.assertRaisesRegex(ValueError, "unknown head_style_id"):
            manual_configurator.configure(
                {**BASE, **TRILOGY_SELECTION, "head_style_id": "retailer_item_123"}
            )
        with self.assertRaisesRegex(ValueError, "not compatible"):
            manual_configurator.configure(
                {
                    **BASE,
                    "head_style_id": "head_low_bezel",
                    "band_style_id": "band_knife_edge_half_pave",
                }
            )


class CanonicalModularMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = manual_configurator.configure({**BASE, **TRILOGY_SELECTION})

    def test_trilogy_maps_every_module_into_canonical_authority(self):
        self.assertEqual(self.report["status"], "accepted")
        canonical = self.report["canonical_spec"]
        self.assertEqual(canonical["job"]["archetype"], "trilogy")
        self.assertEqual(canonical["setting"]["head_family"], "petal")
        self.assertEqual(canonical["setting"]["head_geometry"], "petal_prong")
        self.assertEqual(canonical["setting"]["gallery_structure"], "petal_open")
        self.assertEqual(canonical["setting"]["prong_count"], 4)
        self.assertEqual(canonical["setting"]["side_stone_layout"], "left_right_pair")
        self.assertEqual(canonical["ring"]["band_profile"], "knife_edge")
        self.assertEqual(canonical["ring"]["shoulder_style"], "tapered_cathedral")
        self.assertEqual(canonical["ring"]["pave"]["coverage"], "half")

        by_role = {stone["role"]: stone for stone in canonical["stones"]}
        self.assertEqual(by_role["center"]["quantity"], 1)
        self.assertEqual(by_role["center"]["placement"]["region"], "center")
        self.assertEqual(by_role["side"]["shape"], "pear")
        self.assertEqual(by_role["side"]["quantity"], 2)
        self.assertEqual(by_role["accent"]["shape"], "round")
        self.assertGreater(by_role["accent"]["quantity"], 0)
        self.assertEqual(
            canonical["ring"]["pave"]["accent_stone_ids"],
            [by_role["accent"]["stone_id"]],
        )

        component_roles = {item["role"] for item in canonical["components"]}
        self.assertEqual(component_roles, {"band", "head", "prongs", "pave"})
        for component in canonical["components"]:
            self.assertEqual(component["metal"]["purity"], "14k")
            self.assertEqual(component["metal"]["finish"], "polished")

        result = validate_job_spec(canonical)
        self.assertTrue(result.accepted, result.issues)
        self.assertTrue(self.report["validation"]["constraint_accepted"])
        self.assertEqual(
            set(self.report["flat_parameters"]["geometry_gates"].values()),
            {"pending"},
        )

    def test_solitaire_and_trilogy_are_distinct_geometry_contracts(self):
        solitaire = manual_configurator.configure(
            {
                **BASE,
                "head_style_id": "head_classic_4_prong",
                "band_style_id": "band_classic_solitaire",
            }
        )
        self.assertEqual(solitaire["canonical_spec"]["job"]["archetype"], "solitaire")
        self.assertEqual(self.report["canonical_spec"]["job"]["archetype"], "trilogy")
        self.assertNotEqual(solitaire["geometry_identity"], self.report["geometry_identity"])
        self.assertFalse(any(s["role"] == "side" for s in solitaire["canonical_spec"]["stones"]))
        self.assertTrue(any(s["role"] == "side" for s in self.report["canonical_spec"]["stones"]))

    def test_flat_adapter_preserves_modular_schedule(self):
        adapted = flat_to_canonical(self.report["flat_parameters"])
        self.assertEqual(adapted, self.report["canonical_spec"])

    def test_aggregate_side_quantity_counts_as_a_trilogy_pair(self):
        canonical = copy.deepcopy(self.report["canonical_spec"])
        side = next(stone for stone in canonical["stones"] if stone["role"] == "side")
        side["quantity"] = 1
        result = validate_job_spec(canonical)
        self.assertFalse(result.accepted)
        self.assertIn(
            "archetype_role_count_invalid",
            {issue.code for issue in result.issues},
        )

    def test_live_constraints_prefer_structured_band_and_shoulder_fields(self):
        canonical = copy.deepcopy(self.report["canonical_spec"])
        canonical["ring"]["profile"] = "legacy label without geometry tokens"
        canonical["ring"]["band_width_mm"] = 1.9
        result = constraints.validate(canonical)
        self.assertIn("BAND-003", {item.rule_id for item in result.soft_warnings})
        self.assertIn("BAND-009", {item.rule_id for item in result.advisory_notes})

    def test_component_material_and_finish_do_not_change_geometry_identity(self):
        rose = manual_configurator.configure(
            {
                **BASE,
                **TRILOGY_SELECTION,
                "metal": {
                    "color_family": "rose",
                    "purity": "18k",
                    "finish": "brushed",
                },
            }
        )
        self.assertEqual(self.report["geometry_identity"], rose["geometry_identity"])
        self.assertNotEqual(
            self.report["flat_parameters"]["config_id"],
            rose["flat_parameters"]["config_id"],
        )
        self.assertEqual(rose["canonical_spec"]["metal"]["finish"], "brushed")

    def test_run_persists_full_contract_to_review_and_only_explicit_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            persisted = workflow.persist_proposal(self.report, runs)
            review = workflow.review_index(runs)["runs"][0]
            self.assertEqual(review["config_summary"]["archetype"], "trilogy")
            self.assertEqual(review["modular_contract"]["head_family"], "petal")
            self.assertEqual(review["modular_contract"]["pave"]["coverage"], "half")
            self.assertIsNone(review["geometry_request"])
            requested = workflow.request_geometry(persisted["job_id"], runs)
            self.assertEqual(requested["status"], "requested")
            self.assertEqual(
                requested["required_geometry_contract"]["architecture"], "trilogy"
            )
            self.assertIn("does not establish support", requested["operator_preflight"])


class StudioAndPreviewContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.html = (root / "fast_mvp/static/studio.html").read_text(encoding="utf-8")
        cls.js = (root / "fast_mvp/static/studio.js").read_text(encoding="utf-8")
        cls.ring3d = (root / "fast_mvp/static/ring3d.js").read_text(encoding="utf-8")

    def test_gradual_flow_has_authoritative_finish_and_read_only_module_results(self):
        self.assertIn("Stage 3 of 3: Complete your ring", self.js)
        self.assertIn('id="metalFinishInput"', self.html)
        for element_id in (
            "wsArchitecture",
            "wsGallery",
            "wsShoulders",
            "wsPave",
            "wsSideStones",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertNotIn('id="wsArchitectureInput"', self.html)
        self.assertIn("postJson('/api/manual-configure'", self.js)
        self.assertIn("postJson('/api/request-geometry'", self.js)
        self.assertNotIn("'/api/rhino-handoff'", self.js)

    def test_preview_draws_only_contract_backed_modular_geometry(self):
        for marker in (
            "preview.head_geometry === 'petal_prong'",
            "preview.band_profile === 'knife_edge'",
            "preview.shoulder_style === 'tapered_cathedral'",
            "preview.side_stones || []",
            "preview.accent_stones || []",
            "pearOutline",
        ):
            self.assertIn(marker, self.ring3d)


if __name__ == "__main__":
    unittest.main()
