"""Tests for STL geometry gates (G-2).

Gate 1 (mesh closedness) is exercised on tiny in-memory trimesh primitives, not
the 10 MB ``assets/model.stl`` — a known-watertight box passes, a box with a
removed face fails. Gates 2/3 are stubs pending G-1.
"""

import tempfile
import unittest
from pathlib import Path

import trimesh

from jewelry_gaas_foundation.stl_gates import (
    GateResult,
    check_mesh_closedness,
    check_stone_metal_collision,
    check_stone_stone_collision,
)


class CheckMeshClosednessTests(unittest.TestCase):
    def _export(self, mesh: trimesh.Trimesh, tmpdir: str, name: str) -> str:
        path = Path(tmpdir) / name
        mesh.export(path)
        return str(path)

    def test_watertight_box_passes(self):
        box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        with tempfile.TemporaryDirectory() as tmp:
            stl = self._export(box, tmp, "box.stl")
            result = check_mesh_closedness(stl)
        self.assertIsInstance(result, GateResult)
        self.assertEqual(result.gate_id, "mesh_closedness")
        self.assertEqual(result.status, "pass")
        self.assertTrue(result.metrics["is_watertight"])
        self.assertEqual(result.metrics["euler_number"], 2)
        self.assertEqual(result.metrics["naked_edges"], 0)

    def test_open_mesh_fails(self):
        box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        # Drop one triangle to open a hole → no longer watertight.
        open_mesh = trimesh.Trimesh(
            vertices=box.vertices.copy(),
            faces=box.faces[1:].copy(),
            process=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            stl = self._export(open_mesh, tmp, "open.stl")
            result = check_mesh_closedness(stl)
        self.assertEqual(result.status, "fail")
        self.assertFalse(result.metrics["is_watertight"])
        self.assertGreater(result.metrics["naked_edges"], 0)

    def test_missing_path_raises(self):
        with self.assertRaises(FileNotFoundError):
            check_mesh_closedness("does/not/exist.stl")


class DeferredGateTests(unittest.TestCase):
    def test_stone_metal_collision_deferred(self):
        result = check_stone_metal_collision()
        self.assertEqual(result.gate_id, "stone_metal_collision")
        self.assertEqual(result.status, "deferred_until_G1")

    def test_stone_stone_collision_deferred(self):
        result = check_stone_stone_collision()
        self.assertEqual(result.gate_id, "stone_stone_collision")
        self.assertEqual(result.status, "deferred_until_G1")


if __name__ == "__main__":
    unittest.main()
