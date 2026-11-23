"""Tests for the Goal 0002 app workflow: persist → request → publish → review."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_mvp import proposer, workflow  # noqa: E402
from scripts.publish_run_evidence import publish_run_evidence  # noqa: E402


PAYLOAD = {
    "intent": "classic yellow gold solitaire",
    "stone_shape": "round",
    "stone_length_mm": 6.5,
    "stone_width_mm": 6.5,
    "stone_depth_mm": 3.9,
    "ring_size_us": 6.5,
    "stone_carat_eq": 1.0,
}


def _accepted_report():
    report = proposer.propose_config(PAYLOAD, allow_llm=False)
    assert report["status"] == "accepted"
    return report


def _fake_bundle(tmp: Path, job_id: str) -> Path:
    bundle = tmp / "bundle"
    renders = bundle / "renders"
    renders.mkdir(parents=True)
    (renders / "view_0001.png").write_bytes(b"\x89PNG-rgb")
    (renders / "mask_0001.png").write_bytes(b"\x89PNG-mask")
    (bundle / "export_receipt.json").write_text(
        json.dumps(
            {
                "export_id": "test-export-1",
                "job_id": job_id,
                "gh_definition_sha256": "a" * 64,
                "gh_definition_version": "v2-test",
                "files": {"model.stl": {"sha256": "b" * 64, "byte_size": 10}},
            }
        )
    )
    (bundle / "local_smoke_report.json").write_text(
        json.dumps(
            {"status": "pass", "gate_1_closedness": {"status": "pass"}}
        )
    )
    (bundle / "blender_smoke_report.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "artifacts": {
                    "rgb": {"path": str(renders / "view_0001.png")},
                    "mask": {"path": str(renders / "mask_0001.png")},
                },
                "mask": {
                    "observed_values": [0, 1, 2, 3],
                    "required_visible_values": [1, 2, 3],
                    "missing_visible_classes": [],
                },
            }
        )
    )
    return bundle


class WorkflowTests(unittest.TestCase):
    def test_persist_request_and_review(self):
        report = _accepted_report()
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            run = workflow.persist_proposal(report, runs)
            job_id = run["job_id"]
            run_dir = runs / job_id
            for name in (
                workflow.PROPOSAL_FILENAME,
                workflow.PARAMETERS_FILENAME,
                workflow.CANONICAL_FILENAME,
            ):
                self.assertTrue((run_dir / name).is_file(), name)

            request = workflow.request_geometry(job_id, runs)
            self.assertEqual(request["status"], "requested")

            # Idempotent: an operator-updated status is never reset.
            (run_dir / workflow.REQUEST_FILENAME).write_text(
                json.dumps(dict(request, status="fulfilled"))
            )
            again = workflow.request_geometry(job_id, runs)
            self.assertEqual(again["status"], "fulfilled")

            index = workflow.review_index(runs)
            self.assertEqual(len(index["runs"]), 1)
            entry = index["runs"][0]
            self.assertEqual(entry["job_id"], job_id)
            self.assertTrue(entry["validation"]["constraint_accepted"])
            self.assertEqual(
                set(entry["geometry_gates"].values()), {"pending"}
            )
            self.assertIn("non_certifying_note", index)

    def test_persist_rejects_non_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                workflow.persist_proposal({"status": "rejected"}, Path(tmp))

    def test_request_requires_persisted_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                workflow.request_geometry("missing-job", Path(tmp))

    def test_publish_evidence_marks_fulfilled_and_surfaces_in_review(self):
        report = _accepted_report()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runs = tmp_path / "runs"
            run = workflow.persist_proposal(report, runs)
            job_id = run["job_id"]
            workflow.request_geometry(job_id, runs)
            bundle = _fake_bundle(tmp_path, job_id)

            summary = publish_run_evidence(bundle, runs / job_id)
            self.assertEqual(summary["bundle_smoke"], "pass")
            self.assertEqual(summary["gate1"], "pass")
            self.assertEqual(summary["mask_values"], [0, 1, 2, 3])
            self.assertEqual(summary["visible_classes"], [1, 2, 3])
            self.assertEqual(
                sorted(summary["artifacts"]), ["mask", "rgb"]
            )

            request = json.loads(
                (runs / job_id / workflow.REQUEST_FILENAME).read_text()
            )
            self.assertEqual(request["status"], "fulfilled")

            index = workflow.review_index(runs)
            entry = index["runs"][0]
            self.assertEqual(entry["evidence"]["export_id"], "test-export-1")
            self.assertEqual(
                sorted(entry["evidence_artifacts"]), ["mask", "rgb"]
            )
            for url in entry["evidence_artifacts"].values():
                self.assertTrue(url.startswith(f"/runs/{job_id}/evidence/"))
            # Gates stay pending even with evidence published.
            self.assertEqual(
                set(entry["geometry_gates"].values()), {"pending"}
            )


if __name__ == "__main__":
    unittest.main()
