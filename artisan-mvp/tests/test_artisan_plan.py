"""Tests for the canonical spec -> RhinoArtisan build-plan compiler (pure, no Rhino)."""

import copy
import json
import math
import unittest
from pathlib import Path

from jewelry_gaas_foundation.artisan_plan import (
    BASKET_OVER_GIRDLE_RATIO,
    CATHEDRAL_ARM_CREST_OFFSET_MM,
    CATHEDRAL_ARM_OVERLAP_MM,
    CATHEDRAL_BRIDGE_TOP_MM,
    CATHEDRAL_DEFAULT_ARM_HEIGHT_MM,
    PAVILION_FRACTION,
    MountingOptions,
    SpecRejectedError,
    UnsupportedSpecError,
    artisan_metal,
    compile_solitaire_plan,
    finger_diameter_mm,
)
from jewelry_gaas_foundation.config_adapter import flat_to_canonical

ROOT = Path(__file__).resolve().parents[1]


def _example() -> dict:
    return json.loads((ROOT / "examples" / "solitaire_valid.json").read_text())


def _step(plan, step_id):
    return next(s for s in plan.steps if s.step_id == step_id)


class PlainProngPlanTest(unittest.TestCase):
    def setUp(self):
        self.plan = compile_solitaire_plan(_example())

    def test_step_order_and_tools(self):
        self.assertEqual(
            [s.tool for s in self.plan.steps],
            [
                "set_design_metal",
                "create_ring",
                "add_gem_by_size",
                "add_prong_setting",
                "boolean_operation",
            ],
        )

    def test_metal_and_ring_size(self):
        self.assertEqual(self.plan.artisan_metal, "GOLD_YELLOW_14")
        shank = _step(self.plan, "shank").args
        self.assertAlmostEqual(shank["finger_diameter"], 11.63 + 0.8128 * 6.5, places=3)
        self.assertEqual(shank["width"], 2.0)
        self.assertEqual(shank["height"], 1.6)

    def test_stone_placed_for_culet_clearance(self):
        d = self.plan.derived
        inner_r = d["finger_diameter_mm"] / 2
        pavilion = PAVILION_FRACTION["round"] * 3.9
        expected_girdle = inner_r + 1.6 + 0.5 + pavilion
        self.assertAlmostEqual(d["girdle_z_mm"], expected_girdle, places=3)
        self.assertAlmostEqual(d["expected_culet_clearance_mm"], 0.5, places=3)
        stone = _step(self.plan, "stone").args
        self.assertEqual(stone["shape"], "ROUND")
        self.assertEqual((stone["size_x"], stone["size_y"], stone["size_z"]), (6.5, 6.5, 3.9))
        self.assertAlmostEqual(stone["plane"][2], expected_girdle, places=3)

    def test_head_base_sinks_into_shank(self):
        head = _step(self.plan, "head").args
        d = self.plan.derived
        base = d["girdle_z_mm"] + head["over_girdle_height"] - head["height"]
        self.assertAlmostEqual(base, d["seat_z_mm"] - 0.4, places=3)
        self.assertGreater(base, d["finger_diameter_mm"] / 2)  # never into the finger
        self.assertEqual(head["gem_id"], {"$ref": "gem.id"})
        self.assertEqual(head["prongs"], 4)
        self.assertEqual(head["gem_inside"], -0.1)

    def test_union_references_shank_and_head(self):
        union = _step(self.plan, "union")
        self.assertEqual(union.args["operation"], "UNION")
        self.assertEqual(union.args["target_ids"], {"$refs": ["shank.id", "head.ids"]})
        self.assertEqual(union.bind, "metal")

    def test_plan_is_json_serialisable(self):
        json.dumps(self.plan.to_dict())


class VariantPlanTest(unittest.TestCase):
    def test_cathedral_arms_and_girdle(self):
        plan = compile_solitaire_plan(_example(), MountingOptions(shank_style="cathedral"))
        shank = _step(plan, "shank")
        self.assertEqual(shank.tool, "create_cathedral")
        self.assertAlmostEqual(shank.args["arm_width"], 6.5 - CATHEDRAL_ARM_OVERLAP_MM)
        self.assertEqual(shank.args["bottom_width"], 2.0)
        inner_r = plan.derived["finger_diameter_mm"] / 2
        crest = inner_r + CATHEDRAL_DEFAULT_ARM_HEIGHT_MM + CATHEDRAL_ARM_CREST_OFFSET_MM
        self.assertGreaterEqual(plan.derived["girdle_z_mm"], round(crest, 4))
        # culet still clears the bridge by at least the requested clearance
        self.assertGreaterEqual(plan.derived["expected_culet_clearance_mm"], 0.5 - 1e-6)
        # embed is capped to half the thin bridge, with a fit note (not a rule warning)
        self.assertAlmostEqual(plan.derived["head_embed_mm"], CATHEDRAL_BRIDGE_TOP_MM / 2)
        self.assertTrue(any("embed reduced" in n for n in plan.notes))
        self.assertFalse(any("embed reduced" in w for w in plan.warnings))

    def test_deep_stone_on_cathedral_rises_above_arm_crest(self):
        spec = _example()
        spec["stones"][0]["dimensions_mm"] = {"length": 8.2, "width": 8.2, "depth": 5.0}
        plan = compile_solitaire_plan(spec, MountingOptions(shank_style="cathedral"))
        self.assertAlmostEqual(plan.derived["expected_culet_clearance_mm"], 0.5, places=3)

    def test_basket_head(self):
        spec = _example()
        spec["setting"]["center_setting"] = "basket"
        spec["setting"]["prong_count"] = 6
        plan = compile_solitaire_plan(spec)
        head = _step(plan, "head")
        self.assertEqual(head.tool, "add_basket_setting")
        self.assertEqual(head.args["gem_ids"], [{"$ref": "gem.id"}])
        self.assertEqual(head.args["prongs"], 6)
        over = head.args["prong_height_over_girdle"]
        self.assertAlmostEqual(over, BASKET_OVER_GIRDLE_RATIO * 6.5, places=3)
        base = plan.derived["girdle_z_mm"] + over - head.args["prong_height"]
        self.assertAlmostEqual(base, plan.derived["seat_z_mm"] - 0.4, places=3)

    def test_elongated_stone_length_runs_along_finger(self):
        spec = _example()
        spec["stones"][0].update(
            shape="oval", dimensions_mm={"length": 8.0, "width": 5.5, "depth": 3.5}
        )
        stone = _step(compile_solitaire_plan(spec), "stone").args
        self.assertEqual((stone["shape"], stone["size_x"], stone["size_y"]), ("OVAL", 5.5, 8.0))

    def test_unset_prong_count_is_left_to_artisan(self):
        spec = _example()
        del spec["setting"]["prong_count"]
        head = _step(compile_solitaire_plan(spec), "head").args
        self.assertNotIn("prongs", head)

    def test_optional_prong_sizes_only_when_given(self):
        plan = compile_solitaire_plan(_example())
        self.assertNotIn("thickness", _step(plan, "head").args)
        plan = compile_solitaire_plan(_example(), MountingOptions(prong_thickness_mm=0.9))
        self.assertEqual(_step(plan, "head").args["thickness"], 0.9)

    def test_every_dataset_config_compiles(self):
        configs = sorted((ROOT / "configs").glob("*/parameters.json"))
        self.assertTrue(configs)
        for path in configs:
            with self.subTest(config=path.parent.name):
                spec = flat_to_canonical(json.loads(path.read_text()))
                plan = compile_solitaire_plan(spec)
                self.assertEqual(plan.steps[-1].tool, "boolean_operation")


class BezelAndVTipPlanTest(unittest.TestCase):
    def _spec(self, **setting):
        spec = _example()
        spec["setting"].update(setting)
        return spec

    def test_bezel_head(self):
        spec = self._spec(center_setting="bezel")
        del spec["setting"]["prong_count"]
        plan = compile_solitaire_plan(spec)
        head = _step(plan, "head")
        self.assertEqual(head.tool, "add_bezel_setting")
        self.assertEqual(head.args["gem_id"], {"$ref": "gem.id"})
        self.assertNotIn("prongs", head.args)
        base = plan.derived["girdle_z_mm"] + head.args["girdle_height"] - head.args["height"]
        self.assertAlmostEqual(base, plan.derived["seat_z_mm"] - 0.4, places=3)
        self.assertEqual(plan.derived["head_kernel"], "bezel")
        self.assertFalse(plan.derived["v_tips"])

    def test_bezel_with_prongs_is_rejected(self):
        with self.assertRaises(SpecRejectedError) as ctx:
            compile_solitaire_plan(self._spec(center_setting="bezel", prong_count=4))
        self.assertTrue(any("PRONG-009" in issue for issue in ctx.exception.issues))

    def test_pear_prong_gets_v_tips_on_basket_kernel(self):
        spec = self._spec(prong_count=6)
        spec["stones"][0].update(
            shape="pear", dimensions_mm={"length": 9.0, "width": 5.5, "depth": 3.5}
        )
        plan = compile_solitaire_plan(spec)
        head = _step(plan, "head")
        self.assertEqual(head.tool, "add_basket_setting")
        self.assertTrue(head.args["v_prongs"])
        self.assertFalse(head.args["upper_rail"])
        self.assertTrue(head.args["base_rail"])
        self.assertEqual(head.args["prongs"], 6)
        self.assertEqual(plan.derived["head_kernel"], "basket")
        self.assertTrue(plan.derived["v_tips"])

    def test_marquise_is_supported_and_basket_keeps_rails(self):
        spec = self._spec(center_setting="basket", prong_count=6)
        spec["stones"][0].update(
            shape="marquise", dimensions_mm={"length": 10.0, "width": 5.0, "depth": 3.1}
        )
        plan = compile_solitaire_plan(spec)  # schema now accepts marquise
        head = _step(plan, "head").args
        self.assertTrue(head["v_prongs"])
        self.assertNotIn("upper_rail", head)
        self.assertNotIn("base_rail", head)
        self.assertAlmostEqual(
            plan.derived["pavilion_depth_mm"], PAVILION_FRACTION["marquise"] * 3.1, places=4
        )
        self.assertEqual(_step(plan, "stone").args["shape"], "MARQUISE")

    def test_round_prong_stays_on_peghead(self):
        plan = compile_solitaire_plan(_example())
        self.assertEqual(_step(plan, "head").tool, "add_prong_setting")
        self.assertEqual(plan.derived["head_kernel"], "peghead")


class ShankStyleTest(unittest.TestCase):
    def test_shank_style_from_spec(self):
        spec = _example()
        spec["ring"]["shank_style"] = "cathedral"
        plan = compile_solitaire_plan(spec)
        self.assertEqual(_step(plan, "shank").tool, "create_cathedral")
        self.assertEqual(plan.derived["shank_style"], "cathedral")

    def test_options_override_spec(self):
        spec = _example()
        spec["ring"]["shank_style"] = "cathedral"
        plan = compile_solitaire_plan(spec, MountingOptions(shank_style="plain"))
        self.assertEqual(_step(plan, "shank").tool, "create_ring")

    def test_default_is_plain(self):
        self.assertEqual(compile_solitaire_plan(_example()).derived["shank_style"], "plain")

    def test_schema_rejects_unknown_shank_style(self):
        spec = _example()
        spec["ring"]["shank_style"] = "split"
        with self.assertRaises(SpecRejectedError):
            compile_solitaire_plan(spec)

    def test_flat_config_carries_shank_style(self):
        flat = json.loads(
            (ROOT / "configs" / "config_solitaire_round_1.00ct_prong4_14ky_2.0mm_us65"
             / "parameters.json").read_text()
        )
        flat["shank_style"] = "cathedral"
        canonical = flat_to_canonical(flat)
        self.assertEqual(canonical["ring"]["shank_style"], "cathedral")
        self.assertEqual(compile_solitaire_plan(canonical).derived["shank_style"], "cathedral")
        del flat["shank_style"]
        self.assertNotIn("shank_style", flat_to_canonical(flat)["ring"])


class RejectionTest(unittest.TestCase):
    def test_hard_constraint_rejects(self):
        spec = _example()
        spec["ring"]["band_width_mm"] = 1.2  # BAND-001
        with self.assertRaises(SpecRejectedError) as ctx:
            compile_solitaire_plan(spec)
        self.assertTrue(any("BAND-001" in issue for issue in ctx.exception.issues))

    def test_schema_failure_rejects(self):
        spec = _example()
        del spec["ring"]
        with self.assertRaises(SpecRejectedError):
            compile_solitaire_plan(spec)

    def test_outside_niche(self):
        for mutate in (
            lambda s: s["setting"].update(center_setting="other"),
            lambda s: s["stones"][0].update(shape="heart"),
            lambda s: s["ring"].update(size_standard="UK", size="M"),
            lambda s: s["ring"].update(shank_style="split"),
        ):
            spec = copy.deepcopy(_example())
            mutate(spec)
            with self.subTest(spec=spec["setting"], stone=spec["stones"][0]["shape"]):
                with self.assertRaises(UnsupportedSpecError):
                    compile_solitaire_plan(spec, validate=False)

    def test_bad_options(self):
        with self.assertRaises(UnsupportedSpecError):
            MountingOptions(shank_style="split")
        with self.assertRaises(ValueError):
            MountingOptions(head_embed_mm=0)


class VocabularyTest(unittest.TestCase):
    def test_metal_mapping(self):
        cases = {
            ("gold", "14k yellow gold"): "GOLD_YELLOW_14",
            ("gold", "18k white"): "GOLD_WHITE_18",
            ("gold", "14k rose"): "GOLD_ROSE_14",
            ("gold", "24k"): "GOLD_24",
            ("platinum", "pt950"): "PLATINUM_950",
            ("platinum", None): "PLATINUM",
            ("silver", "sterling silver"): "SILVER_925",
        }
        for (mtype, alloy), expected in cases.items():
            with self.subTest(alloy=alloy):
                self.assertEqual(artisan_metal({"type": mtype, "alloy": alloy}), expected)

    def test_metal_mapping_rejects_ambiguous(self):
        for metal in ({"type": "gold", "alloy": "yellow gold"}, {"type": "gold", "alloy": "14k"},
                      {"type": "other"}):
            with self.subTest(metal=metal), self.assertRaises(UnsupportedSpecError):
                artisan_metal(metal)

    def test_eu_size_is_circumference(self):
        self.assertAlmostEqual(
            finger_diameter_mm({"size_standard": "EU", "size": 54}), 54 / math.pi
        )


if __name__ == "__main__":
    unittest.main()
