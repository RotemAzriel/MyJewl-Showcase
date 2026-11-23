"""Tests for STL geometry gates (G-2).

Gate 1 (mesh closedness) is exercised on tiny in-memory trimesh primitives, not
the 10 MB ``assets/model.stl`` — a known-watertight box passes, a box with a
removed face fails. Gates 2/3 keep the no-path deferred behavior and expose a
local AABB broad phase when per-part STLs exist.
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


class CollisionScaffoldTests(unittest.TestCase):
    def test_stone_metal_no_aabb_overlap_passes_broad_phase(self):
        stone = trimesh.creation.icosphere(subdivisions=1, radius=1.0)
        metal = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        metal.apply_translation((5.0, 0.0, 0.0))
        with tempfile.TemporaryDirectory() as tmp:
            stone_path = self._export(stone, tmp, "stone.stl")
            metal_path = self._export(metal, tmp, "band.stl")
            result = check_stone_metal_collision(stone_path, band_stl_path=metal_path)
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.metrics["method"], "aabb_broad_phase")
        self.assertFalse(result.metrics["calibrated"])
        self.assertFalse(result.metrics["pairs"][0]["aabb_overlap"])

    def test_stone_metal_aabb_overlap_needs_calibration(self):
        stone = trimesh.creation.icosphere(subdivisions=1, radius=1.0)
        metal = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        with tempfile.TemporaryDirectory() as tmp:
            stone_path = self._export(stone, tmp, "stone.stl")
            metal_path = self._export(metal, tmp, "prongs.stl")
            result = check_stone_metal_collision(stone_path, prongs_stl_path=metal_path)
        self.assertEqual(result.status, "needs_calibration")
        self.assertTrue(result.metrics["pairs"][0]["aabb_overlap"])

    def test_solitaire_single_stone_stone_collision_passes(self):
        stone = trimesh.creation.icosphere(subdivisions=1, radius=1.0)
        with tempfile.TemporaryDirectory() as tmp:
            stone_path = self._export(stone, tmp, "stone.stl")
            result = check_stone_stone_collision([stone_path])
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.metrics["stone_count"], 1)

    def _export(self, mesh: trimesh.Trimesh, tmpdir: str, name: str) -> str:
        path = Path(tmpdir) / name
        mesh.export(path)
        return str(path)


if __name__ == "__main__":
    unittest.main()
