"""Tests for the Rhino batch driver (G-7).

Exercises checkpoint/resume and geometry-gate rejection with a fake adapter — no
live Rhino.Compute endpoint and no real batch run.
"""

import json
import tempfile
import unittest
from pathlib import Path

from jewelry_gaas_foundation.geometry_adapter import GeometryAdapterResult
from jewelry_gaas_foundation.output import GeometryGateStatus
from scripts.rhino_batch import PENDING_GEOMETRY_CHECKS, RhinoBatchRunner, discover_configs


def _base_flat(config_id: str, **overrides) -> dict:
    flat = {
        "config_id": config_id,
        "archetype": "solitaire",
        "stone_shape": "round",
        "stone_length_mm": 6.5,
        "stone_width_mm": 6.5,
        "stone_depth_mm": 3.9,
        "stone_carat_eq": 1.0,
        "center_setting": "prong",
        "prong_count": 4,
        "metal_type": "gold",
        "metal_alloy": "14k_yellow",
        "band_width_mm": 2.0,
        "band_thickness_mm": 1.6,
        "ring_size_us": 6.5,
    }
    flat.update(overrides)
    return flat


def _write_config(root: Path, config_id: str, **overrides) -> Path:
    cfg_dir = root / config_id
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "parameters.json"
    path.write_text(json.dumps(_base_flat(config_id, **overrides)), encoding="utf-8")
    return path


class FakeAdapter:
    """Records calls; fails a chosen gate for chosen job_ids; can raise if called
    for a job_id that should have been skipped on resume."""

    def __init__(self, fail_job_ids=(), raise_on=()):
        self.calls: list[str] = []
        self.fail_job_ids = set(fail_job_ids)
        self.raise_on = set(raise_on)

    def evaluate_geometry(self, spec, pending_checks, output_targets):
        job_id = spec["job"]["job_id"]
        self.calls.append(job_id)
        if job_id in self.raise_on:
            raise AssertionError(f"adapter called for skipped config {job_id}")
        gates = []
        for code in pending_checks:
            if job_id in self.fail_job_ids and code == "mesh_closedness_and_manifold_suitability":
                gates.append(GeometryGateStatus(code=code, status="failed", reason_code="not_watertight"))
            else:
                gates.append(GeometryGateStatus(code=code, status="not_run", reason_code="not_executed"))
        return GeometryAdapterResult(
            execution_status="completed",
            assets=(),
            geometry_gates=tuple(gates),
            reason_code=None,
        )


class RhinoBatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.checkpoint = self.root / "ckpt.json"
        self.rejected = self.root / "rejected_configs.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _runner(self, adapter):
        return RhinoBatchRunner(
            config_dir=self.root,
            adapter=adapter,
            checkpoint_path=self.checkpoint,
            rejected_path=self.rejected,
        )

    def test_completes_and_persists_checkpoint(self):
        _write_config(self.root, "cfgA")
        _write_config(self.root, "cfgB")
        summary = self._runner(FakeAdapter()).run()
        self.assertEqual(summary["completed"], 2)
        ledger = json.loads(self.checkpoint.read_text())
        self.assertEqual(ledger["cfgA"]["status"], "completed")
        self.assertEqual(ledger["cfgB"]["status"], "completed")

    def test_geometry_gate_failure_logged_and_batch_continues(self):
        _write_config(self.root, "cfgA")
        _write_config(self.root, "cfgBad")
        _write_config(self.root, "cfgC")
        summary = self._runner(FakeAdapter(fail_job_ids={"cfgBad"})).run()
        self.assertEqual(summary["completed"], 2)
        self.assertEqual(summary["failed"], 1)
        rejected = [json.loads(line) for line in self.rejected.read_text().splitlines()]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["config_id"], "cfgBad")
        self.assertEqual(rejected[0]["gate_id"], "mesh_closedness_and_manifold_suitability")

    def test_resume_skips_already_completed(self):
        path_a = _write_config(self.root, "cfgA")
        _write_config(self.root, "cfgB")
        # First pass: only cfgA processed (simulated interruption).
        self._runner(FakeAdapter()).process_one(path_a)

        # Second pass: a fresh runner loads the checkpoint; its adapter raises if
        # cfgA is re-processed.
        adapter2 = FakeAdapter(raise_on={"cfgA"})
        summary = self._runner(adapter2).run()
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["completed"], 1)
        self.assertNotIn("cfgA", adapter2.calls)
        self.assertIn("cfgB", adapter2.calls)

    def test_constraint_invalid_rejected_before_rhino_call(self):
        # Stone far too wide for US 6.5 / 2.0 mm band -> BAND-005 HARD.
        _write_config(self.root, "cfgHuge", stone_length_mm=12.0, stone_width_mm=12.0)
        adapter = FakeAdapter()
        outcome = self._runner(adapter).run()
        self.assertEqual(outcome["failed"], 1)
        self.assertNotIn("cfgHuge", adapter.calls)  # never POSTed to Rhino
        rejected = [json.loads(line) for line in self.rejected.read_text().splitlines()]
        self.assertEqual(rejected[0]["gate_id"], "constraint")

    def test_discover_configs_finds_nested(self):
        _write_config(self.root, "cfgA")
        _write_config(self.root, "cfgB")
        found = discover_configs(self.root)
        self.assertEqual(len(found), 2)
        self.assertTrue(all(p.name == "parameters.json" for p in found))


if __name__ == "__main__":
    unittest.main()
