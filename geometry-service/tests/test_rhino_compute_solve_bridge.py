import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = ROOT / "manual" / "rhino_compute_bridge" / "solve_bridge.py"
SPEC_PATH = ROOT / "examples" / "solitaire_valid.json"

spec = importlib.util.spec_from_file_location("solve_bridge", BRIDGE_PATH)
solve_bridge = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(solve_bridge)


def solitaire_payload():
    canonical_spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    return {
        "definition_id": "echo_smoke_v0",
        "job_id": canonical_spec["job"]["job_id"],
        "archetype": canonical_spec["job"]["archetype"],
        "spec": canonical_spec,
        "pending_geometry_checks": [
            "minimum_structural_floors",
            "stone_stone_collision",
        ],
        "output_targets": ["GLB", "STL"],
    }


def fake_post(path="/solve", payload=None, api_key="secret-key"):
    body = json.dumps(payload if payload is not None else solitaire_payload()).encode(
        "utf-8"
    )
    headers = {"content-type": "application/json"}
    if api_key is not None:
        headers["x-api-key"] = api_key

    return solve_bridge.handle_solve_request(
        path=path,
        headers=headers,
        body=body,
        api_key="secret-key",
    )


class SolveBridgeTests(unittest.TestCase):
    def test_successful_echo_response_is_adapter_compatible_and_harmless(self):
        status, response = fake_post()

        self.assertEqual(status, 200)
        self.assertEqual(response["execution_status"], "completed")
        self.assertEqual(
            response["reason_code"],
            "rhino_compute_echo_definition_executed",
        )
        self.assertEqual(response["assets"], {})
        self.assertEqual(
            {gate["code"] for gate in response["geometry_gates"]},
            {"minimum_structural_floors", "stone_stone_collision"},
        )
        self.assertEqual(
            {gate["status"] for gate in response["geometry_gates"]},
            {"pending"},
        )
        self.assertEqual(
            {gate["details"]["calibrated"] for gate in response["geometry_gates"]},
            {False},
        )

    def test_missing_or_wrong_api_key_is_unauthorized(self):
        for api_key in (None, "wrong-key"):
            with self.subTest(api_key=api_key):
                status, response = fake_post(api_key=api_key)

                self.assertEqual(status, 401)
                self.assertEqual(response["execution_status"], "failed")
                self.assertEqual(response["reason_code"], "unauthorized")

    def test_wrong_path_is_not_found(self):
        status, response = fake_post(path="/native/solve")

        self.assertEqual(status, 404)
        self.assertEqual(response["execution_status"], "failed")
        self.assertEqual(response["reason_code"], "endpoint_not_found")

    def test_malformed_payload_fails_deterministically(self):
        payload = solitaire_payload()
        del payload["pending_geometry_checks"]

        status, response = fake_post(payload=payload)

        self.assertEqual(status, 400)
        self.assertEqual(response["execution_status"], "failed")
        self.assertEqual(response["reason_code"], "missing_required_fields")

    def test_non_echo_definition_does_not_execute(self):
        payload = solitaire_payload()
        payload["definition_id"] = "solitaire_native_v0"

        status, response = fake_post(payload=payload)

        self.assertEqual(status, 400)
        self.assertEqual(response["execution_status"], "failed")
        self.assertEqual(response["reason_code"], "rhino_definition_not_executed")


if __name__ == "__main__":
    unittest.main()
