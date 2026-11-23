import base64
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from fast_mvp.mesh import generate_draft_assets
from fast_mvp.pipeline import analyze_photo_request


def _sample_data_url() -> str:
    image = Image.new("RGB", (240, 180), (246, 242, 232))
    draw = ImageDraw.Draw(image)
    draw.ellipse((54, 48, 186, 138), outline=(203, 162, 79), width=12)
    draw.ellipse((98, 56, 142, 100), fill=(235, 250, 255), outline=(210, 228, 235), width=3)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


class FastMvpTests(unittest.TestCase):
    def test_analyze_photo_returns_valid_canonical_spec(self):
        result = analyze_photo_request(
            {
                "image_data_url": _sample_data_url(),
                "filename": "yellow-round-solitaire.png",
                "stone_shape": "round",
                "stone_length_mm": 6.5,
                "stone_width_mm": 6.5,
                "stone_depth_mm": 3.9,
                "stone_carat_eq": 1.0,
                "ring_size_us": 6.5,
                "mounting_overrides": {
                    "center_setting": "prong",
                    "band_width_mm": 2.0,
                },
            }
        )

        self.assertTrue(result["validation"]["schema_accepted"])
        self.assertTrue(result["validation"]["constraint_accepted"])
        self.assertEqual(result["canonical_spec"]["job"]["archetype"], "solitaire")
        self.assertEqual(result["preview"]["stone_shape"], "round")

    def test_generate_draft_assets_writes_stl_and_handoff(self):
        result = analyze_photo_request(
            {
                "filename": "oval-rose-ring.jpg",
                "stone_shape": "oval",
                "stone_length_mm": 8.1,
                "stone_width_mm": 5.9,
                "stone_depth_mm": 3.7,
                "stone_carat_eq": 1.2,
                "ring_size_us": 6.75,
                "mounting_overrides": {"center_setting": "prong"},
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            assets = generate_draft_assets(result["canonical_spec"], tmp)
            self.assertTrue(Path(assets["draft_stl_path"]).is_file())
            self.assertTrue(Path(assets["rhino_handoff_path"]).is_file())
            self.assertEqual(assets["gate"]["status"], "pass")
            self.assertGreater(assets["mesh"]["faces"], 1000)


if __name__ == "__main__":
    unittest.main()
