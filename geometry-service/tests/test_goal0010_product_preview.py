"""Goal 0010 seven-shape, material-overlay, and product-preview contracts."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "fast_mvp" / "static"
PROFILE_V1 = STATIC / "preview_profiles_v1.js"
PROFILE_V2 = STATIC / "preview_profiles_v2.js"
RING3D = STATIC / "ring3d.js"
STUDIO_HTML = STATIC / "studio.html"
STUDIO_JS = STATIC / "studio.js"

SHAPE_DIMENSIONS = {
    "round": (6.5, 6.5, 3.9),
    "oval": (8.0, 6.0, 3.8),
    "cushion": (6.5, 6.5, 4.2),
    "pear": (8.5, 5.5, 3.7),
    "emerald": (7.0, 5.0, 3.5),
    "princess": (5.5, 5.5, 4.0),
    "radiant": (6.5, 5.5, 3.8),
}


def node_json(source: str) -> object:
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class VersionedSevenShapeProfileTests(unittest.TestCase):
    def test_goal0008_v1_is_immutable_and_goal0010_uses_v2(self):
        payload = node_json(
            f"""
            import {{ FACET_PROFILES as v1, PREVIEW_PROFILE_VERSION as version1 }} from {PROFILE_V1.as_uri()!r};
            import {{ FACET_PROFILES as v2, PREVIEW_PROFILE_VERSION as version2 }} from {PROFILE_V2.as_uri()!r};
            console.log(JSON.stringify({{version1,version2,v1:Object.keys(v1),v2:Object.keys(v2)}}));
            """
        )
        self.assertEqual(payload["version1"], "myjewl-preview-facets-v1")
        self.assertEqual(set(payload["v1"]), {"round", "oval", "pear"})
        self.assertEqual(payload["version2"], "myjewl-preview-facets-v2")
        self.assertEqual(set(payload["v2"]), set(SHAPE_DIMENSIONS))
        self.assertIn("from './preview_profiles_v2.js'", RING3D.read_text(encoding="utf-8"))

    def test_all_shape_outlines_and_center_meshes_are_distinct_and_dimension_faithful(self):
        payload = node_json(
            f"""
            import {{ buildPreviewReference, defaultPreview, previewOutline }} from {RING3D.as_uri()!r};
            const dimensions = {json.dumps(SHAPE_DIMENSIONS)};
            const result = {{}};
            for (const [shape, dims] of Object.entries(dimensions)) {{
              const points = previewOutline(shape, dims[0], dims[1]);
              const group = await buildPreviewReference({{
                ...defaultPreview(),
                stone_shape: shape,
                stone_dimensions_mm: {{length:dims[0],width:dims[1],depth:dims[2]}},
              }});
              const mesh = group.getObjectByName(`${{shape}}-faceted-cut`);
              mesh.geometry.computeBoundingBox();
              const box = mesh.geometry.boundingBox;
              result[shape] = {{
                outline: points.map(point => point.map(value => Number(value.toFixed(4)))),
                bounds: [box.max.x-box.min.x,box.max.y-box.min.y,box.max.z-box.min.z],
                closed: mesh.geometry.userData.closed,
                profileId: mesh.geometry.userData.profileId,
              }};
            }}
            console.log(JSON.stringify(result));
            """
        )
        outlines = {json.dumps(value["outline"]) for value in payload.values()}
        self.assertEqual(len(outlines), len(SHAPE_DIMENSIONS))
        for shape, expected in SHAPE_DIMENSIONS.items():
            with self.subTest(shape=shape):
                actual = payload[shape]
                self.assertTrue(actual["closed"])
                self.assertIn(shape, actual["profileId"])
                for measured, requested in zip(actual["bounds"], expected):
                    self.assertAlmostEqual(measured, requested, places=4)

    def test_fallback_reuses_the_same_shape_outline_and_typed_proportions(self):
        source = RING3D.read_text(encoding="utf-8")
        self.assertIn("const points = previewOutline(shape, length, width", source)
        self.assertIn("typedLength / dimensionMaximum", source)
        self.assertIn("typedWidth / dimensionMaximum", source)
        self.assertNotIn("const stoneW = stoneL * 0.78", source)


class TypedMaterialAppearanceTests(unittest.TestCase):
    def test_colour_purity_and_finish_each_change_material_not_geometry(self):
        payload = node_json(
            f"""
            import {{ previewGeometrySignature, previewMaterialSignature }} from {PROFILE_V2.as_uri()!r};
            const base = {{architecture:'solitaire',stone_shape:'round',stone_dimensions_mm:{{length:6.5,width:6.5,depth:3.9}},ring_size_us:6.5,band_width_mm:2,band_thickness_mm:1.6,metal_family:'yellow',metal_purity:'14k',metal_finish:'polished'}};
            const variants = [
              base,
              {{...base,metal_family:'white'}},
              {{...base,metal_purity:'18k'}},
              {{...base,metal_finish:'brushed'}},
            ];
            console.log(JSON.stringify({{
              geometry: variants.map(previewGeometrySignature),
              material: variants.map(previewMaterialSignature),
            }}));
            """
        )
        self.assertEqual(len(set(payload["geometry"])), 1)
        self.assertEqual(len(set(payload["material"])), 4)

    def test_purity_presets_are_bounded_visual_overlays(self):
        payload = node_json(
            f"""
            import {{ PURITY_PRESETS, metalPresetFor }} from {PROFILE_V2.as_uri()!r};
            console.log(JSON.stringify({{
              keys:Object.keys(PURITY_PRESETS),
              yellow14:metalPresetFor('yellow','14k','polished'),
              yellow18:metalPresetFor('yellow','18k','polished'),
              platinum:metalPresetFor('white','pt950','polished'),
            }}));
            """
        )
        self.assertEqual(set(payload["keys"]), {"14k", "18k", "pt950"})
        self.assertNotEqual(payload["yellow14"]["color"], payload["yellow18"]["color"])
        self.assertNotEqual(payload["yellow18"]["color"], payload["platinum"]["color"])


class InteractionAndFramingSourceTests(unittest.TestCase):
    def test_renderer_has_bounded_framing_continuous_rotation_and_keyboard_controls(self):
        source = RING3D.read_text(encoding="utf-8")
        for marker in (
            "fitCameraToRing",
            "getBoundingSphere",
            "fit: 'bounding_sphere'",
            "AUTO_ROTATE_RADIANS_PER_SECOND",
            "event.key === 'ArrowLeft'",
            "event.key === 'ArrowRight'",
            "event.key === 'ArrowUp'",
            "event.key === 'ArrowDown'",
            "previewRotation",
            "previewFraming",
        ):
            self.assertIn(marker, source)


class PremiumStudioContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = STUDIO_HTML.read_text(encoding="utf-8")
        cls.javascript = STUDIO_JS.read_text(encoding="utf-8")

    def test_visible_progress_is_one_semantic_three_stage_journey(self):
        self.assertIn('<ol class="flow-bar"', self.html)
        for stage, label in enumerate(
            ("Choose your diamond", "Build your setting", "Complete your ring"),
            start=1,
        ):
            self.assertIn(f'data-flow-step="{stage}"', self.html)
            self.assertIn(label, self.html)
        self.assertIn("item.setAttribute('aria-current', 'step')", self.javascript)
        self.assertIn("updateJourneyProgress(3)", self.javascript)
        self.assertNotIn("Step 1 of 6", self.html)

    def test_all_bounded_shape_tiles_are_enabled_and_accessible(self):
        for shape in SHAPE_DIMENSIONS:
            with self.subTest(shape=shape):
                marker = f'class="shape-option'
                button = self.html.split(f'data-shape="{shape}"', 1)[0].rsplit("<button", 1)[1]
                self.assertIn(marker, "<button" + button)
                self.assertNotIn("disabled", button)
                self.assertIn('aria-pressed="', self.html.split(f'data-shape="{shape}"', 1)[1][:80])

    def test_selection_persistence_and_current_step_error_are_explicit(self):
        self.assertIn("headStillCompatible", self.javascript)
        self.assertIn("bandStillCompatible", self.javascript)
        self.assertIn('id="headOptionsError" role="alert"', self.html)
        self.assertIn("errorElement?.focus()", self.javascript)
        self.assertIn("forceFallback: studioParams.get('fallback') === '1'", self.javascript)

    def test_stone_validation_is_visible_and_workspace_status_is_reset(self):
        self.assertIn('id="manualStoneError" role="alert"', self.html)
        stone_handler = self.javascript.split(
            "document.getElementById('step3Next')?.addEventListener('click', () => {",
            1,
        )[1].split("// Step 4: Metal", 1)[0]
        self.assertIn("set('manualStoneError', error)", stone_handler)
        self.assertIn("set('manualStoneError', '')", stone_handler)
        self.assertNotIn("setStatus(error", stone_handler)
        self.assertIn(
            "Design brief validated. Preview resolution is separate from manufacturing review.",
            self.javascript,
        )
        self.assertIn(
            "The viewport stays empty until the registered asset passes integrity checks.",
            self.javascript,
        )
        self.assertIn("Verified registered full-ring asset · Manufacturing pending", self.javascript)
        self.assertIn("No procedural or 2D substitute is shown", self.javascript)

    def test_final_surface_is_non_certifying_and_keeps_review_actions(self):
        for marker in (
            "Production preview",
            "Only a verified registered asset can appear here.",
            "BRIEF READY",
            'id="wsSpecMetal"',
            'id="wsSpecFinish"',
            'id="wsRequestGeometry"',
            'href="/review"',
            'id="workspacePreviewAuthority"',
        ):
            self.assertIn(marker, self.html)
        self.assertNotIn("CAD Viewport", self.html)
        self.assertNotIn(">VALID<", self.html)

    def test_preview_controls_expose_pointer_keyboard_and_auto_rotate_state(self):
        self.assertEqual(self.html.count('tabindex="0"'), 2)
        self.assertEqual(self.html.count("Drag or use arrow keys to rotate"), 2)
        self.assertEqual(self.html.count("Drag or use the arrow keys to rotate"), 1)
        self.assertIn('id="wsSpinToggle" type="button" aria-pressed="false"', self.html)
        self.assertIn("btn.setAttribute('aria-pressed', String(state.spin))", self.javascript)
        self.assertIn("state.preview.metal_purity = state.metalPurity", self.javascript)
        self.assertIn("state.preview.head_style_id = head.head_style_id", self.javascript)
        self.assertIn(
            "state.preview.reference_geometry_id = head.visual_reference?.template_id || null",
            self.javascript,
        )
        self.assertIn("purpose: 'production_preview'", self.javascript)
        self.assertIn("canvas?.setAttribute('tabindex', '-1')", self.javascript)


if __name__ == "__main__":
    unittest.main()
