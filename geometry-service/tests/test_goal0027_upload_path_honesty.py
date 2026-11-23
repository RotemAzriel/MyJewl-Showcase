"""Goal 0027 — the customer must be told their photograph was not read.

Goal 0026 made the perception lane refuse by name, and put the refusal on the
API. It did not put it on screen. A customer uploaded a picture of a ring they
loved and was shown a mounting derived from the stone dimensions they had typed,
with nothing anywhere saying so — the last place in the product where a claim was
stronger than the evidence behind it, and the only one a customer actually meets.

These tests protect the sentence, not the pixels. Two of them are the ones that
matter:

* ``test_two_different_photographs_produce_the_identical_ring`` proves the claim
  the copy makes. It is the strongest honest statement available and it is an
  invariant, not a description — if a photograph ever *does* change an output
  while the lane is paused, this fails and the copy must change with it.
* ``test_no_shipped_surface_claims_the_photo_is_used`` is the regression guard.
  The false sentences deleted here were written by people building a feature that
  was later paused; nothing stopped them coming back.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fast_mvp import perception_lane, pipeline


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "fast_mvp" / "static"


def _declared(**overrides: Any) -> dict[str, Any]:
    document = json.loads(
        Path(perception_lane.UNDECLARED_TEMPLATE_PATH).read_text(encoding="utf-8")
    )
    document["acknowledgement_id"] = "goal-0027-test"
    document["status"] = "declared"
    document["declared_by"] = {"name": "Test Operator", "role": "operator"}
    document["validity"] = {"valid_from": "2026-08-01", "expires_on": "2999-01-01"}
    document["accepted_conditions"] = list(perception_lane.STANDING_CONDITIONS)
    document.update(overrides)
    return document


class _FakeIndex:
    def __init__(self, *, configs: int = 500, reference: bool = True):
        self.records = [{"config_id": f"cfg-{i}"} for i in range(configs)]
        self.metadata = {
            "embedding": {
                "backend": "fake_v1",
                "model_id": "deterministic/fake_v1",
                "is_reference_paradigm": reference,
            },
            "built_utc": "2026-07-26T22:16:06Z",
            "structure": {"structure": "exact_cosine"},
        }


def _png(color: tuple[int, int, int], size: tuple[int, int] = (96, 96)) -> str:
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", color=color, size=size).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _upload(**overrides: Any) -> dict[str, Any]:
    """The payload /studio's upload path actually posts to /api/extract.

    Note what is *absent*: no ``mounting_overrides``. studio.js omits them on the
    upload path precisely so the mounting can be inferred, which is what makes
    this the path where the false claim lived.
    """
    payload = {
        "image_data_url": _png((250, 215, 80)),
        "filename": "the-ring-i-love.jpg",
        "stone_shape": "round",
        "stone_length_mm": 6.5,
        "stone_width_mm": 6.5,
        "stone_depth_mm": 3.9,
        "ring_size_us": 6.5,
        "metal_color_family": "yellow",
        "metal_karat": "18k",
        "metal_finish": "polished",
    }
    payload.update(overrides)
    return payload


class CustomerDisclosureTests(unittest.TestCase):
    """The sentence, authored once in perception_lane and nowhere else."""

    def test_shipped_state_tells_the_customer_the_photo_is_not_read(self) -> None:
        payload = perception_lane.decide(environ={}).to_json()
        disclosure = payload["customer_disclosure"]
        self.assertIs(disclosure["reads_your_photo"], False)
        self.assertEqual(disclosure["mounting_source"], "typed_shape_prior")
        self.assertTrue(disclosure["headline"])
        self.assertTrue(disclosure["statement"])

    def test_disclosure_shape_is_identical_whether_yes_or_no(self) -> None:
        """The house pattern. A surface renders one shape, not two."""
        refused = perception_lane.decide(environ={}).to_json()["customer_disclosure"]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ack.json"
            path.write_text(json.dumps(_declared()), encoding="utf-8")
            allowed = perception_lane.decide(
                index=_FakeIndex(),
                environ={
                    perception_lane.ACK_PATH_ENV: str(path),
                    perception_lane.INDEX_DIR_ENV: "/index",
                },
            ).to_json()["customer_disclosure"]
        self.assertIs(allowed["reads_your_photo"], True)
        self.assertEqual(set(refused), set(allowed))

    def test_an_acknowledged_lane_still_says_the_suggestion_is_unvalidated(self) -> None:
        """An acknowledgement buys diagnostic output, and must say so.

        This is the customer-facing form of "an acknowledgement never makes a gate
        measurable". Turning the lane on must not turn the caveat off.
        """
        disclosure = perception_lane.customer_disclosure(True)
        self.assertIs(disclosure["reads_your_photo"], True)
        self.assertRegex(
            disclosure["statement"], r"diagnostic|not passed an accuracy gate"
        )

    def test_disclosure_defaults_to_refusing_for_an_unknown_state(self) -> None:
        """Fail closed means fail to "not read", which is the shipped truth."""
        self.assertIs(
            perception_lane.customer_disclosure(False)["reads_your_photo"], False
        )
        self.assertEqual(
            perception_lane.customer_disclosure(False),
            perception_lane.CUSTOMER_DISCLOSURE_REFUSED,
        )

    def test_lane_state_carries_it_so_a_page_can_ask_before_uploading(self) -> None:
        state = perception_lane.lane_state(environ={})
        self.assertIs(state["customer_disclosure"]["reads_your_photo"], False)
        self.assertEqual(
            state["customer_disclosure"],
            perception_lane.decide(environ={}).to_json()["customer_disclosure"],
        )

    def test_disclosure_is_not_the_operator_detail_string(self) -> None:
        """Two audiences, two sentences. A condition slug is not an explanation."""
        payload = perception_lane.decide(environ={}).to_json()
        self.assertNotEqual(payload["detail"], payload["customer_disclosure"]["statement"])
        self.assertNotIn("Track C", payload["customer_disclosure"]["statement"])


class MountingSourceTests(unittest.TestCase):
    """Which of the four providers chose this request's mounting."""

    def test_every_provider_has_a_disclosure(self) -> None:
        """The four values extraction.provider can take, per Goal 0026."""
        self.assertEqual(
            set(pipeline._MOUNTING_SOURCE),
            {"typed", "heuristic_fallback", "perception_refused_typed_prior", "dinov3_hnsw"},
        )
        for name, source in pipeline._MOUNTING_SOURCE.items():
            with self.subTest(provider=name):
                self.assertIn("read_your_photo", source)
                self.assertIsInstance(source["read_your_photo"], bool)
                self.assertTrue(source["headline"])
                self.assertTrue(source["statement"])

    def test_only_the_retrieval_provider_claims_to_have_read_the_photo(self) -> None:
        for name, source in pipeline._MOUNTING_SOURCE.items():
            with self.subTest(provider=name):
                self.assertEqual(source["read_your_photo"], name == "dinov3_hnsw")

    def test_an_undisclosed_provider_raises_rather_than_reassuring(self) -> None:
        """A new provider must not inherit a comfortable default sentence."""
        with self.assertRaises(ValueError) as caught:
            pipeline._mounting_source("some_new_model_v2", None)
        self.assertIn("some_new_model_v2", str(caught.exception))

    def test_upload_path_reports_the_refusal_by_name(self) -> None:
        report = pipeline.analyze_photo_request(_upload())
        source = report["extraction"]["mounting_source"]
        self.assertEqual(source["provider"], "perception_refused_typed_prior")
        self.assertIs(source["read_your_photo"], False)
        self.assertEqual(source["perception_reason"], "lane_paused")
        self.assertEqual(
            source["provider"], report["extraction"]["provider"]
        )

    def test_typed_path_says_the_customer_chose_it(self) -> None:
        report = pipeline.analyze_photo_request(
            _upload(
                mounting_overrides={
                    "center_setting": "bezel",
                    "prong_count": 0,
                    "band_width_mm": 2.4,
                    "band_thickness_mm": 1.7,
                }
            )
        )
        source = report["extraction"]["mounting_source"]
        self.assertEqual(source["provider"], "typed")
        self.assertIs(source["read_your_photo"], False)
        self.assertIsNone(source["perception_reason"])


class SurfacesAgreeTests(unittest.TestCase):
    """F12. The copy a page shows before an upload must match what follows it."""

    def test_health_hands_the_ui_the_request_paths_answer(self) -> None:
        """/api/health's customer sentence is the one /api/extract will produce.

        The operator fields on /api/health may legitimately be more permissive:
        they answer "can this deployment serve perception", asked with an index
        loader. The request path asks without one. Before Goal 0027 that gap was
        invisible; now the pre-upload copy is built on it, so the customer half
        must come from the narrower decision.
        """
        from_health = pipeline.request_path_disclosure()
        from_request = pipeline.analyze_photo_request(_upload())["extraction"]
        self.assertEqual(
            from_health["reads_your_photo"],
            from_request["mounting_source"]["read_your_photo"],
        )
        self.assertEqual(
            from_health,
            perception_lane.decide().to_json()["customer_disclosure"],
        )

    def test_server_health_uses_it_rather_than_the_operator_answer(self) -> None:
        source = (ROOT / "fast_mvp" / "server.py").read_text(encoding="utf-8")
        self.assertIn("request_path_disclosure()", source)
        self.assertIn('"customer_disclosure": request_path_disclosure()', source)


class PhotoChangesNothingTests(unittest.TestCase):
    """The claim the copy makes, held as an invariant rather than a description."""

    def test_two_different_photographs_produce_the_identical_ring(self) -> None:
        """"...the same ones any other photo would have produced."

        This is the strongest honest sentence available on the upload path, so it
        is asserted rather than asserted-in-prose. Everything a customer receives
        is compared: the mounting, the canonical spec, the flat parameters, the
        preview and the geometry signature. Only the job id, which carries a
        timestamp and a random suffix, is allowed to differ.
        """
        gold_ring = pipeline.analyze_photo_request(
            _upload(image_data_url=_png((250, 215, 80)), filename="vintage.jpg")
        )
        blue_square = pipeline.analyze_photo_request(
            _upload(image_data_url=_png((5, 5, 240)), filename="blank.png")
        )

        # The photographs really are different, or this proves nothing.
        self.assertNotEqual(
            gold_ring["extraction"]["image"]["warm_score"],
            blue_square["extraction"]["image"]["warm_score"],
        )

        components = ("center_setting", "prong_count", "band_width_mm", "band_thickness_mm")
        for key in components:
            with self.subTest(component=key):
                self.assertEqual(
                    gold_ring["extraction"]["components"][key]["value"],
                    blue_square["extraction"]["components"][key]["value"],
                )

        self.assertEqual(
            {k: v for k, v in gold_ring["flat_parameters"].items() if k != "config_id"},
            {k: v for k, v in blue_square["flat_parameters"].items() if k != "config_id"},
        )
        # `preview` is what the browser renders; `preview_geometry_signature` is
        # added later by the server's _finalize_extraction_report, so the pipeline
        # output is compared here and the signature over real HTTP.
        self.assertEqual(gold_ring["preview"], blue_square["preview"])
        self.assertEqual(gold_ring["quote"], blue_square["quote"])

        def without_job_id(spec: dict[str, Any]) -> dict[str, Any]:
            copy = json.loads(json.dumps(spec))
            copy.get("job", {}).pop("job_id", None)
            return copy

        self.assertEqual(
            without_job_id(gold_ring["canonical_spec"]),
            without_job_id(blue_square["canonical_spec"]),
        )

    def test_the_photograph_is_not_kept_with_the_design(self) -> None:
        """The copy says the picture is not kept. Nothing may quietly keep it."""
        report = pipeline.analyze_photo_request(_upload())
        serialised = json.dumps(report)
        self.assertNotIn("base64", serialised)
        self.assertNotIn("data:image", serialised)


class ShippedCopyTests(unittest.TestCase):
    """The regression guard on the sentences themselves."""

    def test_no_shipped_surface_claims_the_photo_is_used(self) -> None:
        """The exact claims deleted by this goal, and their near neighbours.

        These were not careless: they were written while the perception lane was
        being built, and they became false when the operator paused it on
        2026-08-11. Nothing but this test stops the same sentence returning the
        next time someone describes the feature they intended to ship.
        """
        forbidden = (
            re.compile(r"we'?ll extract the design", re.I),
            re.compile(r"the photo is used only to match the mounting", re.I),
            re.compile(r"our ai matches the mounting", re.I),
        )
        for name in ("studio.html", "index.html", "manual.html"):
            text = (STATIC / name).read_text(encoding="utf-8")
            for pattern in forbidden:
                with self.subTest(page=name, claim=pattern.pattern):
                    self.assertIsNone(pattern.search(text))

    def test_studio_ships_the_refusing_copy_in_its_markup(self) -> None:
        """The honest text must be in the HTML, not only in the JavaScript.

        If the disclosure only ever arrived from /api/health, a page whose fetch
        failed would render an empty notice and say nothing at all — which is the
        state this goal exists to remove.
        """
        markup = (STATIC / "studio.html").read_text(encoding="utf-8")
        self.assertIn('id="uploadLaneNotice"', markup)
        self.assertIn('id="uploadLaneStatement"', markup)
        self.assertIn("not being read", markup)
        self.assertIn('id="wsMountingSource"', markup)
        landing = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="photoLaneCopy"', landing)
        self.assertIn("currently paused", landing)

    def test_studio_reads_the_live_gate_and_fails_closed(self) -> None:
        source = (STATIC / "studio.js").read_text(encoding="utf-8")
        self.assertIn("refreshPerceptionState", source)
        self.assertIn("customer_disclosure", source)
        self.assertIn("mounting_source", source)
        # The fallback in perceptionDisclosure() must be the refusing answer.
        self.assertRegex(source, r"reads_your_photo:\s*false")

    def test_configure_has_no_photo_path_to_be_dishonest_about(self) -> None:
        """/configure is fully typed. Asserted so it stays that way."""
        markup = (STATIC / "manual.html").read_text(encoding="utf-8")
        self.assertNotIn('type="file"', markup)
        self.assertNotIn("/api/extract", (STATIC / "manual.js").read_text(encoding="utf-8"))

    def test_the_dom_harness_still_passes(self) -> None:
        """Protocol step 4: run the script nothing asserts, and assert its verdict.

        The Python tests above guard the *markup* and the *payload*. This runs the
        shipped studio.js function bodies against a stub DOM built from the real
        studio.html ids, in all three states -- refusing, gate-unreachable, and
        acknowledged -- which is the only part a browser would otherwise have to
        prove. It is skipped rather than failed where node is absent: an optional
        checker that turns the suite red on a machine without a runtime teaches
        people to ignore it.
        """
        node = shutil.which("node")
        if node is None:  # pragma: no cover - environment dependent
            self.skipTest("node is not installed; run scripts/goal0027_check_studio_copy.mjs")
        result = subprocess.run(
            [node, str(ROOT / "scripts" / "goal0027_check_studio_copy.mjs")],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ALL CHECKS PASSED", result.stdout)

    def test_app_js_is_orphaned_and_must_stay_that_way(self) -> None:
        """F11. Goal 0027's brief named app.js:204 as a second live surface.

        It is not one: no shipped page has loaded it since at least 2026-06-21,
        so it was left alone rather than given honest copy — editing dead code to
        look maintained is worse than leaving it visibly dead. This test is the
        trap it needs instead. If anyone ever wires app.js into a page, this fails
        and points them at the honesty pass its copy has never had.
        """
        for page in STATIC.glob("*.html"):
            with self.subTest(page=page.name):
                self.assertNotIn("app.js", page.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
