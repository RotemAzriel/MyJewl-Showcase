"""Tests for the RhinoArtisan executor + HTTP client, with fakes — no live Rhino."""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import trimesh

from jewelry_gaas_foundation.artisan_executor import (
    ArtisanExecutor,
    ArtisanMcpClient,
    ArtisanToolError,
    PlanExecutionError,
    ToolResult,
    parse_first_json,
    qa_failures,
    resolve_refs,
)
from jewelry_gaas_foundation.artisan_plan import GEOMETRY_GATES, compile_solitaire_plan

ROOT = Path(__file__).resolve().parents[1]


def _plan():
    return compile_solitaire_plan(json.loads((ROOT / "examples" / "solitaire_valid.json").read_text()))


class FakeArtisan:
    """Records calls and returns canned RhinoArtisan-shaped text outputs."""

    def __init__(self, fail_tool=None, shank_rejects=0):
        self.calls = []
        self.fail_tool = fail_tool
        self.shank_rejects = shank_rejects

    def call(self, tool, args):
        self.calls.append((tool, args))
        if tool == self.fail_tool:
            raise ArtisanToolError(f"{tool}: boom")
        text = self._text(tool, args)
        return ToolResult(tool=tool, text=text, data=parse_first_json(text))

    def _text(self, tool, args):
        if tool == "create_ring":
            if self.shank_rejects:
                self.shank_rejects -= 1
                raise ArtisanToolError("create_ring: Ring computation failed: CHECK_SHANK_PARAMETERS.")
            return 'Ring shank created:\n{"id":"shank-1","children":[],"top_z":10.05}'
        if tool == "add_gem_by_size":
            return 'Gem created:\n{"id":"gem-1","gem":{"carat":0.99},"origin":[0,0,13.4]}'
        if tool == "add_prong_setting":
            return '{"success":true,"kind":"Peghead","ids":["head-1"]}'
        if tool == "boolean_operation":
            return 'UNION result:\n{"id":"bool-1","operation":"UNION","ids":["metal-1"]}'
        if tool == "run_script":
            if "culet_z_mm" in args["code"]:
                return json.dumps(
                    {"metal_bodies": 1, "metal_all_solid": True, "culet_z_mm": 10.5565,
                     "culet_inside_metal": False, "culet_clearance_mm": 0.5,
                     "measured_finger_diameter_mm": 16.913}
                )
            return "2"
        if tool == "calculate_weight":
            return '[{"layer":"(objects)","metal":"GOLD_YELLOW_14","volume":168.0,"grams":2.2,"wax_grams":0.8}]'
        if tool == "calculate_price":
            return ('Price breakdown (detailed):\n{"Currency":"EUR","Total":243.86,"MetalTotal":131.7,'
                    '"ProcessesTotal":112.1,"QuoteNotice":"1 certified stone(s) have no quote"}')
        if tool == "export_stl":
            trimesh.creation.annulus(r_min=8.0, r_max=9.6, height=2.0).export(args["path"])
            return f"STL exported: {args['path']}"
        return "ok"


class ExecutorTest(unittest.TestCase):
    def test_full_run_builds_and_writes_manifest(self):
        fake = FakeArtisan()
        with tempfile.TemporaryDirectory() as tmp:
            result = ArtisanExecutor(fake).run(_plan(), tmp, exports=("3dm", "stl"))
            self.assertEqual(result.status, "built", result.error)
            tools = [t for t, _ in fake.calls]
            self.assertEqual(tools[0], "new_document")  # never touches open work
            union_args = dict(fake.calls)["boolean_operation"]
            self.assertEqual(union_args["target_ids"], ["shank-1", "head-1"])
            head_args = dict(fake.calls)["add_prong_setting"]
            self.assertEqual(head_args["gem_id"], "gem-1")
            weight_args = dict(fake.calls)["calculate_weight"]
            self.assertEqual(weight_args, {"object_ids": ["metal-1"], "metal": "GOLD_YELLOW_14"})
            # operands are moved off the metal layers before weighing/pricing
            self.assertLess(tools.index("run_script"), tools.index("calculate_price"))
            self.assertEqual(dict(fake.calls)["save_document"]["copy"], True)

            m = result.metrics
            self.assertEqual(m["metal_weight_g"], 2.2)
            self.assertEqual(m["culet_clearance_mm"], 0.5)
            self.assertFalse(m["price"]["center_stone_quoted"])
            self.assertEqual(m["qa_mesh_closedness"]["status"], "pass")

            manifest = json.loads(result.manifest_path.read_text())
            self.assertEqual(manifest["status"], "built")
            self.assertEqual(manifest["geometry_gates"], {g: "pending" for g in GEOMETRY_GATES})
            self.assertEqual(manifest["metrics"]["culet_z_drift_mm"], 0.0)
            self.assertEqual(manifest["qa_failures"], [])
            self.assertIn("not a manufacturing certification", manifest["disclaimer"])

    def test_failed_step_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ArtisanExecutor(FakeArtisan(fail_tool="add_prong_setting")).run(
                _plan(), tmp, exports=()
            )
            self.assertEqual(result.status, "failed")
            self.assertIn("'head'", result.error)
            manifest = json.loads(result.manifest_path.read_text())
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(set(manifest["geometry_gates"].values()), {"pending"})

    def test_shank_size_snapping_bug_is_nudged(self):
        fake = FakeArtisan(shank_rejects=1)
        with tempfile.TemporaryDirectory() as tmp:
            result = ArtisanExecutor(fake).run(_plan(), tmp, exports=())
        self.assertEqual(result.status, "built", result.error)
        rings = [a for t, a in fake.calls if t == "create_ring"]
        self.assertEqual(len(rings), 2)
        self.assertAlmostEqual(rings[1]["finger_diameter"] - rings[0]["finger_diameter"], 0.005)
        self.assertEqual(result.metrics["finger_diameter_nudge_mm"], 0.005)

    def test_shank_nudge_gives_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ArtisanExecutor(FakeArtisan(shank_rejects=99)).run(_plan(), tmp, exports=())
        self.assertEqual(result.status, "failed")
        self.assertIn("CHECK_SHANK_PARAMETERS", result.error)


class QaFailuresTest(unittest.TestCase):
    CLEAN = {
        "metal_bodies": 1,
        "metal_all_solid": True,
        "culet_inside_metal": False,
        "measured_finger_diameter_mm": 16.913,
        "qa_mesh_closedness": {"status": "pass"},
    }

    def test_clean_build_has_no_failures(self):
        self.assertEqual(qa_failures(_plan(), dict(self.CLEAN)), [])

    def test_detected_defects_are_listed(self):
        metrics = {
            **self.CLEAN,
            "metal_bodies": 3,
            "culet_inside_metal": True,
            "culet_penetration_mm": 0.035,
            "measured_finger_diameter_mm": 16.95,
            "qa_mesh_closedness": {"status": "fail"},
        }
        failures = qa_failures(_plan(), metrics)
        self.assertEqual(len(failures), 4)
        self.assertTrue(any("metal_bodies=3" in f for f in failures))
        self.assertTrue(any("0.035" in f for f in failures))

    def test_nudged_diameter_within_tolerance(self):
        metrics = {**self.CLEAN, "measured_finger_diameter_mm": 16.913 + 0.01}
        self.assertEqual(qa_failures(_plan(), metrics), [])

    def test_floating_prong_fails_the_build_run(self):
        class Floating(FakeArtisan):
            def _text(self, tool, args):
                if tool == "run_script" and "culet_z_mm" in args["code"]:
                    return json.dumps({"metal_bodies": 3, "metal_all_solid": True,
                                       "culet_inside_metal": False,
                                       "measured_finger_diameter_mm": 16.913})
                return super()._text(tool, args)

        with tempfile.TemporaryDirectory() as tmp:
            result = ArtisanExecutor(Floating()).run(_plan(), tmp, exports=())
        self.assertEqual(result.status, "built")
        self.assertEqual(result.metrics["qa_failures"], ["metal_bodies=3 (expected 1)"])


class RefsAndParsingTest(unittest.TestCase):
    def test_resolve_refs(self):
        bindings = {"gem": {"id": "g"}, "shank": {"id": "s"}, "head": {"ids": ["h1", "h2"]}}
        self.assertEqual(
            resolve_refs({"a": {"$ref": "gem.id"}, "b": {"$refs": ["shank.id", "head.ids"]},
                          "c": [{"$ref": "gem.id"}], "d": 3}, bindings),
            {"a": "g", "b": ["s", "h1", "h2"], "c": ["g"], "d": 3},
        )
        with self.assertRaises(PlanExecutionError):
            resolve_refs({"$ref": "missing.id"}, bindings)
        with self.assertRaises(PlanExecutionError):
            resolve_refs({"$ref": "gem.nope"}, bindings)

    def test_parse_first_json(self):
        self.assertEqual(parse_first_json('Gem created:\n{"id":"x"}'), {"id": "x"})
        self.assertEqual(parse_first_json('[{"a":1}]'), [{"a": 1}])
        self.assertEqual(parse_first_json("a {not json} then {\"k\": 2}"), {"k": 2})
        self.assertIsNone(parse_first_json("STL exported: /tmp/x.stl"))


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        name = body["params"]["name"]
        result = (
            {"content": [{"type": "text", "text": "Unknown gem"}], "isError": True}
            if name == "bad"
            else {"content": [{"type": "text", "text": 'Gem created:\n{"id":"g1"}'}], "isError": False}
        )
        payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class HttpClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/mcp"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_tool_call_parses_text_and_json(self):
        result = ArtisanMcpClient(self.url).call("add_gem", {"shape": "ROUND"})
        self.assertEqual(result.data, {"id": "g1"})
        self.assertIn("Gem created", result.text)

    def test_tool_error_raises(self):
        with self.assertRaises(ArtisanToolError):
            ArtisanMcpClient(self.url).call("bad", {})

    def test_transport_error_raises(self):
        with self.assertRaises(ArtisanToolError):
            ArtisanMcpClient("http://127.0.0.1:9/mcp", timeout_s=1).call("x", {})


if __name__ == "__main__":
    unittest.main()
