import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.phase_c2_authoritative_proof import (
    _copy_and_verify,
    _extract_response,
    _load_contract_spec,
)


class PhaseC2AuthoritativeProofTests(unittest.TestCase):
    def test_extract_response_accepts_nested_json_and_zero_branch(self):
        payload = {"execution_status": "completed", "reason_code": "ok"}
        body = {
            "values": [{
                "ParamName": "RH_OUT:response_json",
                "InnerTree": {"{0}": [{"data": json.dumps(json.dumps(payload))}]},
            }]
        }
        self.assertEqual(_extract_response(body), payload)

    def test_copy_and_verify_preserves_receipt_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            files = {}
            for name, content in {
                "model.stl": b"model",
                "band.stl": b"band",
                "stone.stl": b"stone",
                "prongs.stl": b"prongs",
            }.items():
                (source / name).write_bytes(content)
                files[name] = {
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "byte_size": len(content),
                    "bounds_mm": [[0, 0, 0], [1, 1, 1]],
                    "triangle_count": 1,
                }
            copied = _copy_and_verify(source, {"files": files}, destination)
            self.assertEqual(set(copied), set(files))
            self.assertTrue((destination / "model.stl").is_file())

    def test_load_contract_spec_adapts_flat_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "parameters.json"
            path.write_text(
                json.dumps(
                    {
                        "config_id": "flat-job",
                        "archetype": "solitaire",
                        "stone_shape": "round",
                        "stone_length_mm": 6.5,
                        "stone_width_mm": 6.5,
                        "stone_depth_mm": 3.9,
                        "center_setting": "prong",
                        "metal_type": "gold",
                        "band_width_mm": 2.0,
                        "band_thickness_mm": 1.6,
                        "ring_size_us": 6.5,
                        "prong_count": 4,
                    }
                ),
                encoding="utf-8",
            )
            spec = _load_contract_spec(path)
            self.assertEqual(spec["job"]["job_id"], "flat-job")
            self.assertEqual(spec["stones"][0]["shape"], "round")


if __name__ == "__main__":
    unittest.main()
