"""Guided funnel: style presets, step state -> canonical spec mapping, plain-language checks."""

import copy
import http.client
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path

from demo import funnel
from demo.server import DemoApp, make_server, plan_response, spec_from_form
from jewelry_gaas_foundation.validation import validate_job_spec
from test_artisan_executor import FakeArtisan

CAD_JARGON = re.compile(
    r"\b(mesh|boolean|kernel|peghead|nurbs|stl|glb|3dm|spec|parametric|union|_mm|"
    r"ring\.|setting\.|stones\[)\b",
    re.IGNORECASE,
)


def state(**overrides):
    """Default state + overrides; a style override brings its ring type, as the page does."""
    if "style" in overrides and "ring_type" not in overrides:
        style = funnel.STYLE_BY_ID.get(overrides["style"])
        if style is not None:
            overrides["ring_type"] = style.ring_type
    return {**copy.deepcopy(funnel.DEFAULT_STATE), **overrides}


def spec_for(s):
    return spec_from_form(funnel.funnel_to_form(s))


def get_path(spec, dotted):
    node = spec
    for part in re.findall(r"[a-z_]+|\[\d+\]", dotted):
        if part.startswith("["):
            node = node[int(part[1:-1])]
        else:
            node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return None
    return node


class StylePresetTest(unittest.TestCase):
    def test_unique_styles_with_plain_names(self):
        self.assertEqual(len(funnel.STYLES), 13)
        self.assertEqual(len({s.id for s in funnel.STYLES}), 13)
        solitaires = [s.id for s in funnel.STYLES if s.ring_type == "solitaire"]
        self.assertEqual(solitaires, ["classic-four-prong", "six-prong", "cathedral",
                                      "secure-basket", "modern-bezel", "bezel-cathedral"])
        for t in funnel.RING_TYPES:
            self.assertGreaterEqual(len([s for s in funnel.STYLES if s.ring_type == t.id]), 2)
            self.assertNotRegex(t.blurb, CAD_JARGON)
        for s in funnel.STYLES:
            self.assertNotRegex(f"{s.name} {s.blurb}".lower(), r"tiffany|cartier|harry winston")
            self.assertNotRegex(s.blurb, CAD_JARGON)

    def test_every_style_compiles_to_a_valid_spec_and_plan(self):
        expected_kernel = {"prong": "peghead", "basket": "basket", "bezel": "bezel"}
        halo_kernel = {"single": "halo", "hidden": "hidden_halo"}
        for s in funnel.STYLES:
            with self.subTest(style=s.id):
                spec = spec_for(state(style=s.id))
                self.assertTrue(validate_job_spec(spec).accepted)
                self.assertEqual(spec["setting"]["center_setting"], s.head)
                self.assertEqual(spec["ring"]["shank_style"], s.shank)
                self.assertEqual(spec["setting"]["halo_style"], s.halo)
                self.assertEqual(spec["job"]["archetype"], "halo" if s.halo != "none" else "solitaire")
                self.assertEqual(spec["metadata"]["source"], "guided_funnel")
                body, plan = plan_response(state(style=s.id))
                self.assertTrue(body["ok"], body["issues"])
                self.assertEqual(plan.derived["head_kernel"],
                                 halo_kernel.get(s.halo) or expected_kernel[s.head])
                self.assertFalse([i for i in body["issues"] if i["level"] == "block"])

    def test_bezel_styles_never_carry_prongs(self):
        for style_id in ("modern-bezel", "bezel-cathedral"):
            for shape in ("round", "pear", "marquise"):
                spec = spec_for(state(style=style_id, shape=shape,
                                      **funnel.typical_dimensions(shape, 1.0)))
                self.assertNotIn("prong_count", spec["setting"])

    def test_pointed_stones_get_six_v_tip_prongs(self):
        for style_id in ("classic-four-prong", "cathedral", "secure-basket"):
            for shape in ("pear", "marquise"):
                s = state(style=style_id, shape=shape, **funnel.typical_dimensions(shape, 1.0))
                self.assertEqual(spec_for(s)["setting"]["prong_count"], 6)
                body, plan = plan_response(s)
                self.assertTrue(body["ok"], body["issues"])
                self.assertTrue(plan.derived["v_tips"])
                self.assertTrue(any(i["step"] == "shape" and "V-tip" in i["text"]
                                    for i in body["issues"]))

    def test_all_styles_shapes_bands_and_carat_chips_plan_cleanly(self):
        # Every style x shape x band at 1 ct, then every carat chip at both extremes
        # of band width (a full 720-way grid was checked once; this keeps tests fast).
        for s in funnel.STYLES:
            for shape in funnel.RING_TYPE_BY_ID[s.ring_type].shapes:
                if shape in s.exclude_shapes:
                    continue
                for band in funnel.BANDS:
                    body, _ = plan_response(state(
                        style=s.id, shape=shape, band=band,
                        **funnel.typical_dimensions(shape, 1.0)))
                    self.assertTrue(body["ok"], (s.id, shape, band, body["issues"]))
        for shape in funnel.SHAPES:
            for carat in funnel.CARAT_CHIPS:
                for band in ("slim", "bold"):
                    body, _ = plan_response(state(
                        shape=shape, band=band, **funnel.typical_dimensions(shape, carat)))
                    self.assertTrue(body["ok"], (shape, band, carat, body["issues"]))

    def test_unknown_style_is_a_plain_error_not_a_crash(self):
        with self.assertRaises(funnel.FunnelError):
            funnel.funnel_to_form(state(style="halo"))
        body, plan = plan_response(state(style="halo"))
        self.assertFalse(body["ok"])
        self.assertIsNone(plan)
        self.assertEqual(body["issues"][0]["level"], "block")


class StateToSpecMappingTest(unittest.TestCase):
    """Each state key drives exactly the spec path(s) STATE_TO_SPEC documents."""

    CHANGES = {
        "style": "bezel-cathedral",
        "shape": "oval",
        "length": 7.1,
        "depth": 4.2,
        "report_no": "2141438171",
        "alloy": "platinum 950",
        "size": 7.25,
        "band": "bold",
        "band_width": 2.3,
        "band_thickness": 1.8,
    }

    def test_every_mapped_key_changes_only_its_documented_paths(self):
        base_spec = spec_for(state())
        tracked = sorted({p for paths in funnel.STATE_TO_SPEC.values() for p in paths})
        for key, value in self.CHANGES.items():
            with self.subTest(key=key):
                changed = spec_for(state(**{key: value}))
                moved = {p for p in tracked if get_path(base_spec, p) != get_path(changed, p)}
                documented = set(funnel.STATE_TO_SPEC[key])
                self.assertTrue(moved, f"{key} changed nothing")
                self.assertLessEqual(moved, documented | {"stones[0].dimensions_mm"})

    def test_round_width_follows_length(self):
        spec = spec_for(state(length=7.0, width=5.0))
        self.assertEqual(spec["stones"][0]["dimensions_mm"]["width"], 7.0)

    def test_metal_and_size_mapping(self):
        spec = spec_for(state(alloy="18k rose gold", size_standard="EU", size=52))
        self.assertEqual(spec["metal"], {"type": "gold", "alloy": "18k rose gold"})
        self.assertEqual((spec["ring"]["size_standard"], spec["ring"]["size"]), ("EU", 52.0))

    def test_band_presets_and_cathedral_minimum(self):
        for band, (w, t) in {"slim": (1.8, 1.5), "classic": (2.0, 1.6), "bold": (2.6, 1.9)}.items():
            ring = spec_for(state(band=band))["ring"]
            self.assertEqual((ring["band_width_mm"], ring["band_thickness_mm"]), (w, t))
        ring = spec_for(state(style="cathedral", band="slim"))["ring"]
        self.assertEqual(ring["band_width_mm"], funnel.CATHEDRAL_MIN_WIDTH)

    def test_advanced_band_override(self):
        ring = spec_for(state(band="slim", band_width="2.2", band_thickness=""))["ring"]
        self.assertEqual((ring["band_width_mm"], ring["band_thickness_mm"]), (2.2, 1.5))

    def test_report_mode_reads_the_measurements_line(self):
        s = state(size_mode="report", report_no="1234567890",
                  report_measurements="Measurements: 6.52 - 6.55 x 3.98 mm")
        stone = spec_for(s)["stones"][0]
        self.assertEqual(stone["dimensions_mm"], {"length": 6.55, "width": 6.55, "depth": 3.98})
        self.assertEqual(stone["cert"], {"lab": "GIA", "report_number": "1234567890"})


class StoneHelperTest(unittest.TestCase):
    def test_report_measurement_formats(self):
        parse = funnel.parse_report_measurements
        self.assertEqual(parse("8.02 x 5.51 x 3.45 mm", "oval"),
                         {"length": 8.02, "width": 5.51, "depth": 3.45})
        self.assertEqual(parse("6,52–6,55×3,98", "round"),
                         {"length": 6.55, "width": 6.55, "depth": 3.98})
        self.assertIsNone(parse("about one carat", "round"))
        self.assertIsNone(parse("", "round"))

    def test_typical_dimensions_round_trip_to_the_carat(self):
        for shape in funnel.SHAPES:
            for carat in funnel.CARAT_CHIPS:
                dims = funnel.typical_dimensions(shape, carat)
                estimate = funnel.carat_estimate(state(shape=shape, **dims))
                self.assertAlmostEqual(estimate, carat, delta=0.02 + 0.01 * carat)
        one = funnel.typical_dimensions("round", 1.0)
        self.assertAlmostEqual(one["length"], 6.45, delta=0.1)  # trade reference ~6.4-6.5 mm

    def test_us_size_from_diameter_inverts_the_formula(self):
        for size in (4, 6.5, 7.25, 10):
            self.assertEqual(funnel.us_size_from_diameter(11.63 + 0.8128 * size), size)


class PlainLanguageIssueTest(unittest.TestCase):
    def issues(self, **overrides):
        body, _ = plan_response(state(**overrides))
        return body

    def assertIssue(self, body, step, level, rule=None):
        match = [i for i in body["issues"] if i["step"] == step and i["level"] == level
                 and (rule is None or i["rule"] == rule)]
        self.assertTrue(match, body["issues"])
        return match[0]

    def test_thin_band_blocks_on_the_band_step(self):
        body = self.issues(band_width=1.2)
        self.assertFalse(body["ok"])
        issue = self.assertIssue(body, "band", "block", "BAND-001")
        self.assertIn("Classic or Bold", issue["fix"])

    def test_typo_in_stone_mm_blocks_on_the_stone_step(self):
        body = self.issues(length=65)
        self.assertFalse(body["ok"])
        self.assertIn("decimal", self.assertIssue(body, "stone", "block")["fix"])

    def test_unreadable_report_line(self):
        body = self.issues(size_mode="report", report_measurements="one carat-ish")
        self.assertFalse(body["ok"])
        self.assertIssue(body, "stone", "block")

    def test_stone_too_wide_for_finger_points_to_ring_size(self):
        body = self.issues(length=12.0, depth=7.3, size=3)
        self.assertIssue(body, "size", "block", "BAND-005")

    def test_large_stone_on_four_prongs_suggests_six(self):
        body = self.issues(**funnel.typical_dimensions("round", 1.5))
        self.assertTrue(body["ok"])
        self.assertIssue(body, "style", "tip", "PRONG-008")

    def test_princess_corner_note(self):
        body = self.issues(shape="princess", **funnel.typical_dimensions("princess", 1.0))
        self.assertIssue(body, "shape", "note", "PRONG-004")

    def test_out_of_range_ring_size(self):
        self.assertIssue(self.issues(size=21), "size", "block")
        self.assertIssue(self.issues(size=""), "size", "block")

    def test_main_path_messages_carry_no_cad_jargon_and_always_say_how_to_fix(self):
        cases = [dict(band_width=1.2), dict(length=65), dict(size=21), dict(length=12.0, size=3),
                 dict(size_mode="report", report_measurements="?"),
                 dict(shape="pear", **funnel.typical_dimensions("pear", 1.0)),
                 dict(shape="princess", **funnel.typical_dimensions("princess", 1.0)),
                 funnel.typical_dimensions("round", 2.0)]
        for case in cases:
            for issue in self.issues(**case)["issues"]:
                self.assertNotRegex(issue["text"] + " " + issue["fix"], CAD_JARGON, issue)
                if issue["level"] == "block":
                    self.assertTrue(issue["fix"], issue)


class FunnelServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = DemoApp(Path(self.tmp.name), FakeArtisan, mcp_url="http://127.0.0.1:9/mcp")
        self.server = make_server(self.app, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response.status, payload

    def test_styles_endpoint(self):
        status, raw = self._request("GET", "/api/styles")
        self.assertEqual(status, 200)
        body = json.loads(raw)
        self.assertEqual([s["id"] for s in body["styles"]], [s.id for s in funnel.STYLES])
        for s in body["styles"]:
            self.assertIn("thumbnail", s)
            self.assertIn("glb", s)
            if s["thumbnail"]:
                img_status, img = self._request("GET", s["thumbnail"])
                self.assertEqual(img_status, 200)
                self.assertGreater(len(img), 1000)
        self.assertEqual(len(body["shapes"]), 8)
        self.assertEqual(len(body["alloys"]), 7)
        self.assertEqual([b["id"] for b in body["bands"]], ["slim", "classic", "bold"])
        self.assertEqual(body["defaults"]["style"], "classic-four-prong")
        self.assertEqual(body["defaults"]["ring_type"], "solitaire")
        self.assertEqual([s["id"] for s in body["steps"]][:2], ["type", "style"])
        self.assertLessEqual(len(body["steps"]), 8)
        self.assertEqual([t["id"] for t in body["ring_types"]],
                         ["solitaire", "hidden-halo", "pave", "halo"])
        for t in body["ring_types"]:
            self.assertTrue(t["styles"])
            self.assertEqual(t["thumbnail"], next(
                s["thumbnail"] for s in body["styles"] if s["id"] == t["styles"][0]))
        self.assertEqual([c["id"] for c in body["coverages"]], ["shoulders", "half", "three_quarter"])
        self.assertIn(1.2, body["melee"]["choices"])

    def test_static_cannot_escape(self):
        status, _ = self._request("GET", "/static/../server.py")
        self.assertEqual(status, 404)

    def test_plan_then_build_from_funnel_state(self):
        # The fake Artisan client fakes peghead builds only; basket/bezel run live.
        s = state(style="six-prong", shape="oval", **funnel.typical_dimensions("oval", 1.0))
        status, raw = self._request("POST", "/api/plan", s)
        self.assertEqual(status, 200)
        plan = json.loads(raw)
        self.assertTrue(plan["ok"], plan["issues"])
        self.assertEqual(plan["spec"]["setting"]["prong_count"], 6)
        status, raw = self._request("POST", "/api/build", s)
        self.assertEqual(status, 200, raw)
        body = json.loads(raw)
        self.assertEqual(body["status"], "built")
        self.assertTrue(body["job_id"].endswith("_oval_six-prong"))
        self.assertEqual(body["metrics"]["qa_failures"], [])

    def test_build_refuses_a_blocked_state(self):
        status, raw = self._request("POST", "/api/build", state(band_width=1.2))
        self.assertEqual(status, 422)
        self.assertTrue(json.loads(raw)["issues"])


if __name__ == "__main__":
    unittest.main()
