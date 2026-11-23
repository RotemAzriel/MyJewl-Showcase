"""Ring-type slice 1: halo, hidden halo and pave band, from funnel state to plan to build.

Everything here runs without Rhino: the plan compiler is pure, and the executor runs
against a fake RhinoArtisan that answers like the live MCP did on 2026-11-22.
"""

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from demo import funnel
from demo.server import plan_response, spec_from_form
from jewelry_gaas_foundation.artisan_executor import (
    ArtisanExecutor,
    ArtisanToolError,
    ToolResult,
    parse_first_json,
    qa_failures,
)
from jewelry_gaas_foundation.artisan_plan import (
    HALO_BRIDGE_OFFSET_MM,
    HALO_BRIDGE_THICKNESS_MM,
    HALO_CHANNEL_DEPTH_MM,
    HIDDEN_HALO_CHANNEL_POSITION_MM,
    HIDDEN_HALO_PRONG_DROP_MM,
    PAVE_COVERAGE_END_DEG,
    UnsupportedSpecError,
    compile_solitaire_plan,
)
from jewelry_gaas_foundation.constraints import validate as check_constraints
from jewelry_gaas_foundation.validation import validate_job_spec

ROOT = Path(__file__).resolve().parents[1]


def state(**overrides):
    if "style" in overrides and "ring_type" not in overrides:
        overrides["ring_type"] = funnel.STYLE_BY_ID[overrides["style"]].ring_type
    return {**copy.deepcopy(funnel.DEFAULT_STATE), **overrides}


def spec_for(s):
    return spec_from_form(funnel.funnel_to_form(s))


def plan_for(style, **overrides):
    return compile_solitaire_plan(spec_for(state(style=style, **overrides)))


def step(plan, step_id):
    return next(s for s in plan.steps if s.step_id == step_id)


class HaloPlanTest(unittest.TestCase):
    def test_halo_replaces_the_head_and_bridges_into_the_band(self):
        plan = plan_for("classic-halo")
        d = plan.derived
        self.assertEqual(d["head_kernel"], "halo")
        self.assertEqual(d["halo_style"], "single")
        head = step(plan, "head")
        self.assertEqual(head.tool, "create_halo")
        self.assertEqual(head.args["distance_to_gem"], 0.1)  # else the halo floats
        self.assertEqual(head.args["prong_count"], 4)
        self.assertTrue(head.args["extend_to_bridge"])
        self.assertEqual(head.args["stone_size"], 1.2)
        # The bridge's lowest point lands at the head's usual embed depth in the band.
        bridge_bottom = (d["girdle_z_mm"] - HALO_CHANNEL_DEPTH_MM - HALO_BRIDGE_OFFSET_MM
                         - head.args["height_to_bridge"])
        self.assertAlmostEqual(bridge_bottom, d["head_bottom_z_mm"], places=3)
        # ...and the culet clears the bridge's top by the culet clearance.
        self.assertAlmostEqual(
            d["expected_culet_z_mm"] - (d["head_bottom_z_mm"] + HALO_BRIDGE_THICKNESS_MM),
            plan.options.culet_clearance_mm, places=3)
        self.assertEqual(step(plan, "union").args["target_ids"], {"$metal": "all"})
        self.assertNotIn("add_prong_setting", [s.tool for s in plan.steps])

    def test_pear_halo_takes_five_prongs(self):
        s = state(style="classic-halo", shape="pear", **funnel.typical_dimensions("pear", 1.0))
        self.assertEqual(spec_for(s)["setting"]["prong_count"], 5)
        self.assertEqual(step(compile_solitaire_plan(spec_for(s)), "head").args["prong_count"], 5)

    def test_halo_spec_is_a_halo_archetype_with_one_melee_record(self):
        spec = spec_for(state(style="classic-halo", melee=1.4))
        self.assertTrue(validate_job_spec(spec).accepted)
        self.assertEqual(spec["job"]["archetype"], "halo")
        halos = [st for st in spec["stones"] if st["role"] == "halo"]
        self.assertEqual(len(halos), 1)
        self.assertEqual(halos[0]["dimensions_mm"], {"length": 1.4, "width": 1.4})
        self.assertEqual(step(compile_solitaire_plan(spec), "head").args["stone_size"], 1.4)

    def test_unsupported_halo_combinations_are_refused(self):
        spec = spec_for(state(style="classic-halo"))
        bezel = copy.deepcopy(spec)
        bezel["setting"]["center_setting"] = "bezel"
        bezel["setting"].pop("prong_count")
        with self.assertRaises(Exception):  # STONE-005 HARD (or the compiler's guard)
            compile_solitaire_plan(bezel)
        cathedral = copy.deepcopy(spec)
        cathedral["ring"]["shank_style"] = "cathedral"
        with self.assertRaises(UnsupportedSpecError):
            compile_solitaire_plan(cathedral, validate=False)
        princess = copy.deepcopy(spec)
        princess["stones"][0].update(shape="princess",
                                     dimensions_mm={"length": 5.5, "width": 5.5, "depth": 3.9})
        with self.assertRaises(UnsupportedSpecError):
            compile_solitaire_plan(princess, validate=False)
        mismatch = copy.deepcopy(spec)
        mismatch["job"]["archetype"] = "solitaire"
        with self.assertRaises(Exception):
            compile_solitaire_plan(mismatch)


class HiddenHaloPlanTest(unittest.TestCase):
    def test_stone_is_raised_so_the_prongs_end_in_the_band(self):
        plan = plan_for("hidden-halo")
        d = plan.derived
        self.assertEqual(d["head_kernel"], "hidden_halo")
        self.assertAlmostEqual(d["girdle_z_mm"] - HIDDEN_HALO_PRONG_DROP_MM, d["head_bottom_z_mm"],
                               places=3)
        self.assertGreater(d["expected_culet_clearance_mm"], plan.options.culet_clearance_mm)
        self.assertTrue(any("raised" in n for n in plan.notes))
        head = step(plan, "head")
        self.assertEqual(head.tool, "create_hidden_halo")
        self.assertEqual(head.args["channel_position_z"], HIDDEN_HALO_CHANNEL_POSITION_MM)
        self.assertEqual(head.args["rail_shape"], "CIRCLE")
        self.assertNotIn("rail_x_length", head.args)

    def test_rail_follows_the_stone_outline(self):
        dims = funnel.typical_dimensions("oval", 1.0)
        head = step(plan_for("hidden-halo", shape="oval", **dims), "head")
        self.assertEqual(head.args["rail_shape"], "OVAL")
        self.assertAlmostEqual(head.args["rail_x_length"], dims["width"] + 0.2, places=3)
        self.assertAlmostEqual(head.args["rail_y_length"], dims["length"] + 0.2, places=3)
        dims = funnel.typical_dimensions("cushion", 1.0)
        head = step(plan_for("hidden-halo", shape="cushion", **dims), "head")
        self.assertEqual(head.args["rail_shape"], "SQUARE")

    def test_bezel_under_a_hidden_halo_is_a_hard_reject(self):
        spec = spec_for(state(style="hidden-halo"))
        spec["setting"]["center_setting"] = "bezel"
        spec["setting"].pop("prong_count")
        result = check_constraints(spec)
        self.assertIn("STONE-005", [v.rule_id for v in result.hard_violations])


class PavePlanTest(unittest.TestCase):
    def test_pave_band_runs_from_outside_the_head_to_the_coverage(self):
        for coverage, end in PAVE_COVERAGE_END_DEG.items():
            with self.subTest(coverage=coverage):
                plan = plan_for("pave-four-prong", coverage=coverage)
                shank = step(plan, "shank")
                self.assertEqual(shank.tool, "create_pave_shank")
                self.assertEqual(shank.args["rows"], 1)
                self.assertEqual(shank.args["layout_mode"], "SIDE_ANGLE")
                self.assertEqual(shank.args["end_angle"], end)
                self.assertEqual(shank.args["gem_size"], 1.3)
                outer_r = plan.derived["finger_diameter_mm"] / 2 + 1.6
                start = math.radians(shank.args["start_angle"])
                stone_half = plan.derived["stone_mm"]["width"] / 2
                self.assertGreaterEqual(outer_r * math.sin(start), stone_half + 0.5)
                self.assertEqual(plan.derived["pave"]["coverage"], coverage)
                self.assertEqual(step(plan, "head").tool, "add_prong_setting")
                self.assertEqual(step(plan, "union").args["target_ids"], {"$metal": "all"})

    def test_halo_pushes_the_pave_further_out(self):
        plain = step(plan_for("pave-four-prong"), "shank").args["start_angle"]
        halo = step(plan_for("halo-pave"), "shank").args["start_angle"]
        self.assertGreater(halo, plain + 5)

    def test_bezel_pave_keeps_the_bezel_head(self):
        plan = plan_for("pave-bezel")
        self.assertEqual(step(plan, "head").tool, "add_bezel_setting")
        self.assertEqual(plan.derived["shank_style"], "pave")

    def test_spec_carries_coverage_and_an_accent_record(self):
        spec = spec_for(state(style="pave-six-prong", coverage="half", melee=1.1))
        self.assertTrue(validate_job_spec(spec).accepted)
        self.assertEqual(spec["job"]["archetype"], "solitaire")
        self.assertEqual(spec["ring"]["pave_coverage"], "half")
        accents = [s for s in spec["stones"] if s["role"] == "accent"]
        self.assertEqual(accents[0]["dimensions_mm"], {"length": 1.1, "width": 1.1})


class FunnelRingTypeTest(unittest.TestCase):
    def test_shapes_outside_the_type_block_on_the_shape_step(self):
        for style, shape in (("classic-halo", "princess"), ("classic-halo", "marquise"),
                             ("hidden-halo", "pear"), ("hidden-halo", "marquise")):
            with self.subTest(style=style, shape=shape):
                body, plan = plan_response(state(
                    style=style, shape=shape, **funnel.typical_dimensions(shape, 1.0)))
                self.assertIsNone(plan)
                blocks = [i for i in body["issues"] if i["level"] == "block"]
                self.assertEqual(blocks[0]["step"], "shape")
                self.assertNotRegex(blocks[0]["text"], r"(?i)kernel|artisan")

    def test_styles_refuse_shapes_artisan_cannot_build(self):
        # Live 2026-11-23: six prongs on a cushion come out as four corner prongs, and
        # Artisan's bezel rejects cushions at every height.
        for style, shape, word in (("six-prong", "cushion", "corner prongs"),
                                   ("pave-six-prong", "emerald", "corner prongs"),
                                   ("modern-bezel", "cushion", "rim"),
                                   ("pave-bezel", "cushion", "rim"),
                                   ("modern-bezel", "pear", "rim")):
            with self.subTest(style=style, shape=shape):
                body, plan = plan_response(state(
                    style=style, shape=shape, **funnel.typical_dimensions(shape, 1.0)))
                self.assertIsNone(plan)
                block = next(i for i in body["issues"] if i["level"] == "block")
                self.assertEqual(block["step"], "shape")
                self.assertIn(word, block["text"])
        for style, shape in (("six-prong", "pear"), ("modern-bezel", "princess"),
                             ("classic-four-prong", "cushion")):
            body, _ = plan_response(state(style=style, shape=shape,
                                          **funnel.typical_dimensions(shape, 1.0)))
            self.assertTrue(body["ok"], (style, shape, body["issues"]))

    def test_style_and_ring_type_must_agree(self):
        with self.assertRaises(funnel.FunnelError):
            funnel.funnel_to_form({**funnel.DEFAULT_STATE, "ring_type": "halo"})

    def test_slim_pave_band_caps_the_default_melee(self):
        form = funnel.funnel_to_form(state(style="pave-four-prong", band="slim"))
        self.assertEqual(form["band_width"], 1.8)
        self.assertEqual(form["pave_melee"], 1.2)
        body, _ = plan_response(state(style="pave-four-prong", band="slim"))
        self.assertTrue(body["ok"], body["issues"])

    def test_melee_too_big_for_the_band_blocks_on_the_band_step(self):
        body, plan = plan_response(state(style="pave-four-prong", band="slim", melee=1.5))
        self.assertIsNone(plan)
        block = next(i for i in body["issues"] if i["level"] == "block")
        self.assertEqual(block["step"], "band")
        self.assertIn("wider band", block["fix"])

    def test_out_of_range_melee_blocks_on_the_style_step(self):
        body, _ = plan_response(state(style="classic-halo", melee=2.5))
        self.assertEqual(next(i for i in body["issues"] if i["level"] == "block")["step"], "style")

    def test_halo_pear_note_names_the_frame_not_v_tips(self):
        body, _ = plan_response(state(style="classic-halo", shape="pear",
                                      **funnel.typical_dimensions("pear", 1.0)))
        notes = [i["text"] for i in body["issues"] if i["step"] == "shape"]
        self.assertTrue(any("frame" in n for n in notes), notes)
        self.assertFalse(any("V-tip" in n for n in notes), notes)

    def test_melee_and_coverage_map_only_to_their_paths(self):
        base = state(style="pave-four-prong")
        for key, value, paths in (
            ("melee", 1.1, {"stones[1].dimensions_mm"}),
            ("coverage", "three_quarter", {"ring.pave_coverage"}),
        ):
            with self.subTest(key=key):
                before, after = spec_for(base), spec_for({**base, key: value})
                diff = {p for p in paths if _get(before, p) != _get(after, p)}
                self.assertEqual(diff, paths)
                self.assertEqual(before["stones"][0], after["stones"][0])
                self.assertIn(key, funnel.STATE_TO_SPEC)


def _get(spec, dotted):
    node = spec
    for part in dotted.replace("]", "").replace("[", ".").split("."):
        node = node[int(part)] if part.isdigit() else node.get(part)
    return node


class SchemaAdditionsTest(unittest.TestCase):
    def test_additive_fields_validate_and_bad_values_dont(self):
        spec = spec_for(state(style="halo-pave", coverage="half"))
        spec["stones"][1]["count"] = 18
        self.assertTrue(validate_job_spec(spec).accepted)
        hidden = spec_for(state(style="hidden-halo"))
        self.assertEqual(hidden["setting"]["halo_style"], "hidden")
        self.assertTrue(validate_job_spec(hidden).accepted)
        for path, bad in ((("ring", "pave_coverage"), "everywhere"),
                          (("ring", "shank_style"), "split"),
                          (("setting", "halo_style"), "double")):
            broken = copy.deepcopy(spec)
            broken[path[0]][path[1]] = bad
            self.assertFalse(validate_job_spec(broken).accepted, path)
        zero = copy.deepcopy(spec)
        zero["stones"][1]["count"] = 0
        self.assertFalse(validate_job_spec(zero).accepted)

    def test_existing_examples_still_validate(self):
        for name in ("solitaire_valid.json",):
            spec = json.loads((ROOT / "examples" / name).read_text())
            self.assertTrue(validate_job_spec(spec).accepted)


class FakeRingArtisan:
    """Answers like the live MCP for a halo / pave build (2026-11-22)."""

    def __init__(self, one_shot_union_fails=False, inner_top=8.4565):
        self.calls = []
        self.one_shot_union_fails = one_shot_union_fails
        self.inner_top = inner_top
        self.metal = ["shank-1", "prong-1", "prong-2", "channel-1"]

    def call(self, tool, args):
        self.calls.append((tool, copy.deepcopy(args)))
        text = self._text(tool, args)
        return ToolResult(tool=tool, text=text, data=parse_first_json(text))

    def _text(self, tool, args):
        if tool == "create_ring":
            return 'Ring shank created:\n{"id":"shank-1","children":[],"top_z":10.056}'
        if tool == "create_pave_shank":
            return '{"success":true,"kind":"MultiRowShank","id":"shank-1","gem_ids":["m1","m2"]}'
        if tool == "add_gem_by_size":
            return 'Gem created:\n{"id":"gem-1","gem":{"carat":0.99},"origin":[0,0,13.4]}'
        if tool == "add_prong_setting":
            return '{"success":true,"kind":"Peghead","ids":["head-1"]}'
        if tool == "create_halo":
            return '{"success":true,"kind":"Halo","ids":["channel-1","prong-1","prong-2","h1","h2"]}'
        if tool == "boolean_operation":
            ids = args["target_ids"]
            if self.one_shot_union_fails and len(ids) > 2:
                raise ArtisanToolError("boolean_operation: Boolean computation failed. Check ...")
            return 'UNION result:\n{"id":"bool","operation":"UNION","ids":["metal-%d"]}' % len(self.calls)
        if tool == "run_script":
            code = args["code"]
            if "culet_z_mm" in code:
                return json.dumps({"metal_bodies": 1, "metal_all_solid": True, "culet_z_mm": 11.1,
                                   "culet_inside_metal": False, "culet_clearance_mm": 0.54,
                                   "measured_finger_diameter_mm": 16.913,
                                   "measured_inner_top_mm": self.inner_top})
            if "ids.append" in code:
                return json.dumps(self.metal)
            return "3"
        if tool == "list_gems":
            gems = [{"id": "gem-1", "gem": {"carat": 0.99, "sizeX": 6.45}}] + [
                {"id": f"h{i}", "gem": {"carat": 0.006, "sizeX": 1.2}} for i in range(18)]
            return json.dumps({"count": len(gems), "gems": gems})
        if tool == "calculate_weight":
            return '[{"metal":"GOLD_YELLOW_14","volume":205.0,"grams":2.69,"wax_grams":1.0}]'
        if tool == "calculate_price":
            return ('{"Currency":"EUR","Total":413.22,"MetalTotal":160.0,"GemsTotal":64.8,'
                    '"GemsCount":18,"ProcessesSettingCost":54.0,"QuoteNotice":"1 certified stone"}')
        return "ok"


class RingTypeExecutorTest(unittest.TestCase):
    def _run(self, fake, style="classic-halo"):
        plan = compile_solitaire_plan(spec_for(state(style=style)))
        with tempfile.TemporaryDirectory() as tmp:
            result = ArtisanExecutor(fake).run(plan, tmp, exports=())
        return plan, result

    def test_union_takes_every_metal_solid_shank_first(self):
        fake = FakeRingArtisan()
        fake.metal = ["prong-1", "shank-1", "channel-1"]
        _, result = self._run(fake)
        self.assertEqual(result.status, "built", result.error)
        union = [a for t, a in fake.calls if t == "boolean_operation"]
        self.assertEqual(union[0]["target_ids"], ["shank-1", "prong-1", "channel-1"])
        self.assertEqual(result.metrics["union_operands"], 3)
        self.assertNotIn("union_strategy", result.metrics)

    def test_failed_one_shot_union_folds_onto_the_shank(self):
        fake = FakeRingArtisan(one_shot_union_fails=True)
        _, result = self._run(fake)
        self.assertEqual(result.status, "built", result.error)
        unions = [a["target_ids"] for t, a in fake.calls if t == "boolean_operation"]
        self.assertEqual(len(unions[0]), 4)  # the one-shot try
        self.assertEqual([len(u) for u in unions[1:]], [2, 2, 2])
        self.assertEqual(unions[1], ["shank-1", "prong-1"])
        self.assertTrue(unions[2][0].startswith("metal-"))
        self.assertEqual(result.metrics["union_strategy"], "fold")
        final = result.bindings["metal"]["ids"]
        self.assertEqual(len(final), 1)
        self.assertTrue(final[0].startswith("metal-"))
        self.assertNotIn(final[0], unions[3])  # the last fold's result, not an operand

    def test_melee_and_price_are_reported(self):
        _, result = self._run(FakeRingArtisan())
        m = result.metrics
        self.assertEqual(m["melee_count"], 18)
        self.assertEqual(m["melee_carat"], 0.108)
        self.assertEqual(m["melee_sizes_mm"], [1.2])
        self.assertEqual(m["price"]["melee"], 64.8)
        self.assertEqual(m["price"]["setting"], 54.0)
        self.assertEqual(m["qa_failures"], [])

    def test_metal_in_the_finger_hole_fails_the_build(self):
        plan, result = self._run(FakeRingArtisan(inner_top=7.13))
        self.assertTrue(any(f.startswith("metal_inside_finger_hole") for f in result.metrics["qa_failures"]))
        clean = {"metal_bodies": 1, "metal_all_solid": True, "culet_inside_metal": False,
                 "measured_finger_diameter_mm": 16.913, "measured_inner_top_mm": 8.45,
                 "qa_mesh_closedness": {"status": "pass"}}
        self.assertEqual(qa_failures(plan, clean), [])

    def test_head_height_snapping_bug_is_nudged(self):
        class Snappy(FakeRingArtisan):
            rejects = 1

            def _text(self, tool, args):
                if tool == "add_prong_setting" and self.rejects:
                    self.rejects -= 1
                    raise ArtisanToolError(
                        "add_prong_setting: INVALID_PARAMETERS_FOR_BASE_GEOMETRY")
                return super()._text(tool, args)

        fake = Snappy()
        _, result = self._run(fake, style="pave-four-prong")
        self.assertEqual(result.status, "built", result.error)
        heads = [a["height"] for t, a in fake.calls if t == "add_prong_setting"]
        self.assertEqual(len(heads), 2)
        self.assertAlmostEqual(heads[1] - heads[0], 0.005, places=6)
        self.assertEqual(result.metrics["head_height_nudge_mm"], 0.005)

    def test_operands_isolation_moves_every_hidden_metal_solid(self):
        fake = FakeRingArtisan()
        _, result = self._run(fake, style="pave-four-prong")
        self.assertEqual(result.status, "built", result.error)
        isolate = next(a["code"] for t, a in fake.calls
                       if t == "run_script" and "MyJewl Operands" in a["code"])
        self.assertIn("IsHidden", isolate)
        self.assertIn('startswith("Metal")', isolate)


if __name__ == "__main__":
    unittest.main()
