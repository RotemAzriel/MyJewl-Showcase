import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendExtractFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "fast_mvp/static/studio.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "fast_mvp/static/studio.js").read_text(encoding="utf-8")

    def test_confirmation_dialog_contains_all_typed_fields(self):
        for element_id in (
            "stoneDetailsDialog",
            "confirmStoneShape",
            "confirmStoneLength",
            "confirmStoneWidth",
            "confirmStoneDepth",
            "confirmRingSize",
            "confirmStoneCarat",
        ):
            self.assertIn(f'id="{element_id}"', self.html)

    def test_file_picker_and_drop_share_ingestion_path(self):
        self.assertIn("photoInput?.addEventListener('change', (e) => ingestPhoto", self.js)
        self.assertIn("ingestPhoto(event.dataTransfer?.files?.[0])", self.js)

    def test_upload_button_opens_confirmation_instead_of_extracting(self):
        self.assertIn(
            "document.getElementById('stepUploadNext')?.addEventListener('click', openStoneDialog)",
            self.js,
        )
        self.assertIn("form?.addEventListener('submit', async event =>", self.js)
        self.assertIn("await analyzeDesign()", self.js)

    def test_api_payload_sends_explicit_stone_dimensions(self):
        for field in (
            "stone_shape",
            "stone_length_mm",
            "stone_width_mm",
            "stone_depth_mm",
            "ring_size_us",
        ):
            self.assertIn(field, self.js)

    def test_manual_lane_groups_focused_screens_into_three_product_stages(self):
        self.assertIn('id="step6"', self.html)
        self.assertIn('id="headOptions"', self.html)
        self.assertIn('id="bandOptions"', self.html)
        for label in (
            "Choose your diamond",
            "Build your setting",
            "Complete your ring",
        ):
            self.assertIn(label, self.html)
        self.assertIn("updateJourneyProgress(stageForScreen(stepNum))", self.js)
        self.assertNotIn("of 6", self.html)
        self.assertIn("showStep(3)", self.js)

    def test_manual_lane_uses_compatible_catalog_and_explicit_queue(self):
        self.assertIn("postJson('/api/manual-options'", self.js)
        self.assertIn("postJson('/api/manual-configure'", self.js)
        self.assertIn("postJson('/api/request-geometry'", self.js)
        self.assertNotIn("'/api/rhino-handoff'", self.js)
        self.assertNotIn("'/api/generate-stl'", self.js)

    def test_workspace_keeps_catalog_styles_read_only(self):
        for element_id in ("wsHeadStyle", "wsBandStyle", "wsRequestGeometry"):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertNotIn('id="wsSettingInput"', self.html)
        self.assertNotIn('id="wsBandInput"', self.html)


if __name__ == "__main__":
    unittest.main()
