"""Tests for the Aureon local demo server (fake Artisan client; no Rhino)."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from demo.server import (
    DEFAULT_FORM,
    DemoApp,
    make_server,
    plan_response,
    spec_from_form,
)
from jewelry_gaas_foundation.validation import validate_job_spec
from test_artisan_executor import FakeArtisan


class FormTest(unittest.TestCase):
    def test_default_form_is_a_valid_canonical_spec(self):
        spec = spec_from_form(dict(DEFAULT_FORM))
        self.assertTrue(validate_job_spec(spec).accepted)
        self.assertEqual(spec["ring"]["shank_style"], "plain")
        self.assertEqual(spec["setting"]["prong_count"], 4)
        self.assertNotIn("cert", spec["stones"][0])

    def test_platinum_and_cert(self):
        spec = spec_from_form({**DEFAULT_FORM, "alloy": "platinum 950", "cert": "2141438171"})
        self.assertEqual(spec["metal"], {"type": "platinum", "alloy": "pt950"})
        self.assertEqual(spec["stones"][0]["cert"]["report_number"], "2141438171")

    def test_bezel_drops_prongs(self):
        body, plan = plan_response({**DEFAULT_FORM, "head": "bezel", "prong_count": 4})
        self.assertTrue(body["ok"], body)
        self.assertEqual(plan.derived["head_kernel"], "bezel")

    def test_plan_ok(self):
        body, plan = plan_response(dict(DEFAULT_FORM))
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["steps"]), 5)
        self.assertAlmostEqual(body["carat_estimate"], 6.5 * 6.5 * 3.9 * 0.0061, places=2)
        self.assertEqual(body["metal"], "GOLD_YELLOW_14")
        self.assertIsNotNone(plan)

    def test_hard_violation_is_reported(self):
        body, plan = plan_response({**DEFAULT_FORM, "band_width": 1.2})
        self.assertFalse(body["ok"])
        self.assertIsNone(plan)
        self.assertTrue(any("BAND-001" in e for e in body["errors"]))

    def test_bad_input_is_reported(self):
        for form in ({**DEFAULT_FORM, "length": "abc"}, {**DEFAULT_FORM, "alloy": "brass"}):
            body, plan = plan_response(form)
            self.assertFalse(body["ok"])
            self.assertTrue(body["errors"])


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = DemoApp(Path(self.tmp.name), FakeArtisan, mcp_url="http://127.0.0.1:9/mcp")
        self.server = make_server(self.app, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response.status, response.getheader("Content-Type"), payload

    def test_page_and_options(self):
        status, ctype, html = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"Aureon", html)
        status, _, body = self._request("GET", "/api/options")
        self.assertIn("marquise", json.loads(body)["shapes"])

    def test_health_reports_unreachable_rhino(self):
        status, _, body = self._request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["rhino"])

    def test_plan_endpoint(self):
        status, _, body = self._request("POST", "/api/plan", DEFAULT_FORM)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_build_then_download(self):
        status, _, raw = self._request("POST", "/api/build", DEFAULT_FORM)
        self.assertEqual(status, 200, raw)
        body = json.loads(raw)
        self.assertEqual(body["status"], "built")
        self.assertTrue(body["ok"])
        self.assertEqual(body["metrics"]["qa_failures"], [])
        stl_status, _, stl = self._request("GET", body["files"]["stl"])
        self.assertEqual(stl_status, 200)
        self.assertGreater(len(stl), 100)
        m_status, _, manifest = self._request("GET", body["files"]["manifest"])
        self.assertEqual(m_status, 200)
        self.assertEqual(json.loads(manifest)["status"], "built")

    def test_rejected_spec_is_422(self):
        status, _, body = self._request("POST", "/api/build", {**DEFAULT_FORM, "band_width": 1.2})
        self.assertEqual(status, 422)
        self.assertFalse(json.loads(body)["ok"])

    def test_one_build_at_a_time(self):
        self.app.build_lock.acquire()
        try:
            status, _, _ = self._request("POST", "/api/build", DEFAULT_FORM)
        finally:
            self.app.build_lock.release()
        self.assertEqual(status, 409)

    def test_files_cannot_escape_the_output_root(self):
        secret = Path(self.tmp.name).parent / "aureon_secret.txt"
        secret.write_text("nope")
        try:
            status, _, _ = self._request("GET", "/files/../aureon_secret.txt")
            self.assertEqual(status, 404)
        finally:
            secret.unlink()


if __name__ == "__main__":
    unittest.main()
