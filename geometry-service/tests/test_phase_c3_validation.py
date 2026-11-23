import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.blender_manifest_smoke import _extract_blender_version
from scripts.phase_c2_authoritative_proof import DEFAULT_CORE_SHA256, REQUIRED_FILES
from scripts.phase_c3_validation import EXPECTED_SOURCE_CORE_SHA256, validate_evidence


class PhaseC3ValidationTests(unittest.TestCase):
    jobs = (
        "config_a",
        "config_b",
        "config_c",
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundle_root = self.root / "bundles"
        self.bundle_root.mkdir()
        self.file_facts = {}
        for filename in REQUIRED_FILES:
            data = filename.encode("utf-8")
            self.file_facts[filename] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "byte_size": len(data),
            }

        results = []
        configs = []
        for index, job_id in enumerate(self.jobs):
            bundle_dir = self.bundle_root / job_id
            render_dir = bundle_dir / "blender_smoke_5_2" / "renders"
            render_dir.mkdir(parents=True)
            artifacts = {}
            for name in ("rgb", "mask", "depth", "normal"):
                path = render_dir / f"{name}.bin"
                path.write_bytes(b"artifact")
                artifacts[name] = {"path": str(path), "byte_size": path.stat().st_size}
            files = {name: {**facts} for name, facts in self.file_facts.items()}
            results.append({
                "job_id": job_id,
                "status": "completed",
                "http_status": 200,
                "response_execution_status": "completed",
                "definition_sha256": DEFAULT_CORE_SHA256,
                "legacy_external_panel_injection": "not_used",
                "files": files,
            })
            configs.append({
                "job_id": job_id,
                "files": {
                    name: {"sha256": f"{index}{name}"}
                    for name in REQUIRED_FILES
                },
            })
            (bundle_dir / "bundle_smoke_c3.json").write_text(
                json.dumps({
                    "status": "pass",
                    "contract": {
                        "status": "valid",
                        "files": files,
                    },
                    "gate_1_closedness": {
                        "status": "pass",
                        "meshes": [{"path": name, "status": "pass"} for name in REQUIRED_FILES],
                    },
                    "official_geometry_gate_state_modified": False,
                }),
                encoding="utf-8",
            )
            (bundle_dir / "blender_smoke_5_2_report.json").write_text(
                json.dumps({
                    "status": "pass",
                    "blender_version": "5.2.0 LTS",
                    "artifacts": artifacts,
                    "mask": {
                        "allowed_values": [0, 1, 2, 3],
                        "observed_values": [0, 1, 2, 3],
                        "missing_visible_classes": [],
                    },
                }),
                encoding="utf-8",
            )

        (self.root / "contract_proof_report.json").write_text(
            json.dumps({
                "status": "pass",
                "expected_core_sha256": DEFAULT_CORE_SHA256,
                "contract": {
                    "json_input": "RH_IN:json_string",
                    "job_id_input": "RH_IN:job_id",
                    "response_output": "RH_OUT:response_json",
                },
                "results": results,
            }),
            encoding="utf-8",
        )
        patch_response = {
            "execution_status": "patched",
            "source_core": {"sha256": EXPECTED_SOURCE_CORE_SHA256},
            "authoritative_model_proof": {
                "existing_core_geometry_nodes_reused": True,
                "source_sha256_pinned": True,
                "synthetic_external_panel_injection": False,
            },
            "contract": {
                "json_input": "RH_IN:json_string",
                "job_id_input": "RH_IN:job_id",
                "response_output": "RH_OUT:response_json",
            },
        }
        (self.root / "patch_report.json").write_text(
            json.dumps({
                "http_status": 200,
                "body": {
                    "values": [{
                        "ParamName": "RH_OUT:response_json",
                        "InnerTree": {"{0}": [{"data": json.dumps(patch_response)}]},
                    }],
                },
            }),
            encoding="utf-8",
        )
        (self.root / "differential_report_c3.json").write_text(
            json.dumps({
                "status": "pass",
                "config_count": 3,
                "configs": configs,
                "pairs": [
                    {
                        "left": self.jobs[0],
                        "right": self.jobs[1],
                        "changed_files": list(REQUIRED_FILES),
                    },
                    {
                        "left": self.jobs[0],
                        "right": self.jobs[2],
                        "changed_files": list(REQUIRED_FILES),
                    },
                    {
                        "left": self.jobs[1],
                        "right": self.jobs[2],
                        "changed_files": list(REQUIRED_FILES),
                    },
                ],
            }),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_blender_version_is_recorded(self):
        self.assertEqual(_extract_blender_version("Blender 5.2.0 LTS\nBlender quit"), "5.2.0 LTS")

    def test_authoritative_evidence_passes(self):
        report = validate_evidence(self.root, repo_root=self.root)
        self.assertEqual(report["status"], "pass", report["failures"])
        self.assertEqual(report["checks"]["credential_hygiene"]["api_key_source"], "environment_only")

    def test_differential_regression_fails(self):
        path = self.root / "differential_report_c3.json"
        differential = json.loads(path.read_text(encoding="utf-8"))
        differential["pairs"][0]["changed_files"] = ["model.stl"]
        path.write_text(json.dumps(differential), encoding="utf-8")
        report = validate_evidence(self.root, repo_root=self.root)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("all four STL files" in item for item in report["failures"]))


if __name__ == "__main__":
    unittest.main()
