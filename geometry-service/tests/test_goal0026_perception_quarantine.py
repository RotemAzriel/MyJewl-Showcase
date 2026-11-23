"""Goal 0026 — the paused perception lane must be honestly unavailable.

These tests are adversarial on purpose. The thing being protected is not the
image lane's accuracy; it is the claim that a lane whose every gate reads
``not_measurable`` cannot answer a customer without a named human having said so.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fast_mvp import perception_lane
from fast_mvp.perception_lane import PerceptionError


ROOT = Path(__file__).resolve().parents[1]


def _declared(**overrides: Any) -> dict[str, Any]:
    """A fully declared acknowledgement accepting every standing condition."""
    document = json.loads(
        Path(perception_lane.UNDECLARED_TEMPLATE_PATH).read_text(encoding="utf-8")
    )
    document["acknowledgement_id"] = "test-acknowledgement"
    document["status"] = "declared"
    document["declared_by"] = {"name": "Test Operator", "role": "operator"}
    document["validity"] = {"valid_from": "2026-08-01", "expires_on": "2999-01-01"}
    document["accepted_conditions"] = list(perception_lane.STANDING_CONDITIONS)
    document.update(overrides)
    return document


class _FakeIndex:
    """Minimal stand-in carrying only what the decision reads."""

    def __init__(self, *, configs: int, reference: bool, backend: str = "fake_v1"):
        self.records = [{"config_id": f"cfg-{i}"} for i in range(configs)]
        self.metadata = {
            "embedding": {
                "backend": backend,
                "model_id": f"deterministic/{backend}",
                "is_reference_paradigm": reference,
            },
            "built_utc": "2026-07-26T22:16:06Z",
            "structure": {"structure": "exact_cosine"},
        }


class PerceptionLaneShippedStateTests(unittest.TestCase):
    """What an unconfigured deployment — the shipped one — actually does."""

    def test_shipped_state_refuses_with_lane_paused(self) -> None:
        decision = perception_lane.decide(environ={})
        self.assertFalse(decision.available)
        self.assertEqual(decision.reason, "lane_paused")
        self.assertIn(perception_lane.PAUSED_ON, decision.detail)

    def test_committed_template_refuses_itself(self) -> None:
        """The template ships undeclared, exactly as the price sheet does."""
        acknowledgement = perception_lane.load_acknowledgement(
            perception_lane.UNDECLARED_TEMPLATE_PATH
        )
        self.assertFalse(acknowledgement.declared)
        self.assertEqual(acknowledgement.accepted_conditions, frozenset())
        decision = perception_lane.decide(
            environ={
                perception_lane.ACK_PATH_ENV: str(
                    perception_lane.UNDECLARED_TEMPLATE_PATH
                )
            }
        )
        self.assertFalse(decision.available)
        self.assertEqual(decision.reason, "acknowledgement_undeclared")

    def test_an_index_alone_never_opens_the_lane(self) -> None:
        """The pre-Goal-0026 hole: MYJEWL_IMAGE_INDEX_DIR used to be sufficient."""
        decision = perception_lane.decide(
            index=_FakeIndex(configs=500, reference=True),
            environ={perception_lane.INDEX_DIR_ENV: "/somewhere/an/index/lives"},
        )
        self.assertFalse(decision.available)
        self.assertEqual(decision.reason, "lane_paused")

    def test_refusal_names_the_gates_and_their_blockers(self) -> None:
        payload = perception_lane.decide(environ={}).to_json()
        gates = {item["gate"]: item for item in payload["goal_0005_gates"]}
        self.assertEqual(set(gates), {"G-B", "G-C", "G-D", "G-E"})
        for gate in gates.values():
            self.assertEqual(gate["verdict"], "not_measurable")
            self.assertTrue(gate["blockers"])
        self.assertEqual(payload["reason"], "lane_paused")
        self.assertFalse(payload["certifies_manufacturing"])

    def test_payload_shape_is_identical_whether_yes_or_no(self) -> None:
        """The house pattern: a caller parses one shape, not two."""
        refused = perception_lane.decide(environ={}).to_json()
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ack.json"
            path.write_text(json.dumps(_declared()), encoding="utf-8")
            allowed = perception_lane.decide(
                index=_FakeIndex(configs=500, reference=True),
                environ={
                    perception_lane.ACK_PATH_ENV: str(path),
                    perception_lane.INDEX_DIR_ENV: "/index",
                },
            ).to_json()
        self.assertTrue(allowed["available"])
        self.assertEqual(set(refused), set(allowed))


class PerceptionLaneAcknowledgementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, document: dict[str, Any]) -> dict[str, str]:
        path = self.tmp / "ack.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return {
            perception_lane.ACK_PATH_ENV: str(path),
            perception_lane.INDEX_DIR_ENV: "/index",
        }

    def test_declared_acknowledgement_opens_the_lane(self) -> None:
        decision = perception_lane.decide(
            index=_FakeIndex(configs=500, reference=True),
            environ=self._write(_declared()),
        )
        self.assertTrue(decision.available)
        self.assertEqual(decision.reason, "acknowledged_diagnostic")

    def test_expired_acknowledgement_refuses_and_does_not_fall_back(self) -> None:
        environ = self._write(
            _declared(validity={"valid_from": "2026-01-01", "expires_on": "2026-02-01"})
        )
        decision = perception_lane.decide(
            index=_FakeIndex(configs=500, reference=True),
            environ=environ,
            today=date(2026, 8, 17),
        )
        self.assertFalse(decision.available)
        self.assertEqual(decision.reason, "acknowledgement_expired")

    def test_missing_file_refuses_rather_than_defaulting_to_open(self) -> None:
        decision = perception_lane.decide(
            environ={perception_lane.ACK_PATH_ENV: str(self.tmp / "absent.json")}
        )
        self.assertFalse(decision.available)
        self.assertEqual(decision.reason, "acknowledgement_unreadable")

    def test_malformed_acknowledgement_refuses(self) -> None:
        path = self.tmp / "ack.json"
        path.write_text("{not json", encoding="utf-8")
        decision = perception_lane.decide(
            environ={perception_lane.ACK_PATH_ENV: str(path)}
        )
        self.assertEqual(decision.reason, "acknowledgement_unreadable")

    def test_schema_rejects_an_acknowledgement_that_denies_what_it_signs(self) -> None:
        """`understands` is const-true; a file cannot be signed while disagreeing."""
        document = _declared()
        document["understands"]["acknowledgement_moves_no_gate"] = False
        path = self.tmp / "ack.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(PerceptionError):
            perception_lane.load_acknowledgement(path)

    def test_unattributed_acknowledgement_is_rejected(self) -> None:
        document = _declared()
        document.pop("declared_by")
        path = self.tmp / "ack.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(PerceptionError):
            perception_lane.load_acknowledgement(path)

    def test_acknowledgement_must_expire(self) -> None:
        document = _declared()
        document["validity"] = {"valid_from": "2026-08-01"}
        path = self.tmp / "ack.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(PerceptionError):
            perception_lane.load_acknowledgement(path)


class PerceptionLaneConditionTests(unittest.TestCase):
    """No wildcard: every live condition must be named individually."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _environ(self, conditions: list[str]) -> dict[str, str]:
        path = self.tmp / "ack.json"
        path.write_text(
            json.dumps(_declared(accepted_conditions=conditions)), encoding="utf-8"
        )
        return {
            perception_lane.ACK_PATH_ENV: str(path),
            perception_lane.INDEX_DIR_ENV: "/index",
        }

    def test_fallback_backend_must_be_acknowledged_by_name(self) -> None:
        decision = perception_lane.decide(
            index=_FakeIndex(configs=500, reference=False),
            environ=self._environ(list(perception_lane.STANDING_CONDITIONS)),
        )
        self.assertFalse(decision.available)
        self.assertEqual(decision.reason, "condition_not_acknowledged")
        self.assertIn("embedding_backend_not_ratified", decision.unacknowledged)

    def test_small_catalog_must_be_acknowledged_by_name(self) -> None:
        decision = perception_lane.decide(
            index=_FakeIndex(configs=4, reference=True),
            environ=self._environ(list(perception_lane.STANDING_CONDITIONS)),
        )
        self.assertFalse(decision.available)
        self.assertIn("catalog_below_pilot_scale", decision.unacknowledged)

    def test_naming_every_condition_opens_the_lane(self) -> None:
        conditions = list(perception_lane.STANDING_CONDITIONS) + [
            "embedding_backend_not_ratified",
            "catalog_below_pilot_scale",
        ]
        decision = perception_lane.decide(
            index=_FakeIndex(configs=4, reference=False),
            environ=self._environ(conditions),
        )
        self.assertTrue(decision.available)

    def test_partial_acknowledgement_never_grants_the_rest(self) -> None:
        decision = perception_lane.decide(
            index=_FakeIndex(configs=4, reference=False),
            environ=self._environ(["gates_not_measurable"]),
        )
        self.assertFalse(decision.available)
        self.assertEqual(
            set(decision.unacknowledged),
            {
                "no_real_holdout",
                "no_distractors_ood_unmeasured",
                "embedding_backend_not_ratified",
                "catalog_below_pilot_scale",
            },
        )

    def test_catalog_guard_matches_the_evaluation_harness(self) -> None:
        """The boundary and the evaluator must not drift apart on 'too small'."""
        source = (ROOT / "scripts" / "eval_image_to_config.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            f"MIN_SYNTHETIC_CONFIGS = {perception_lane.MIN_SYNTHETIC_CONFIGS}",
            source,
        )

    def test_unloadable_index_refuses_by_name(self) -> None:
        def _explode() -> Any:
            raise FileNotFoundError("embeddings.npy is missing")

        decision = perception_lane.decide(
            index_loader=_explode,
            environ=self._environ(list(perception_lane.STANDING_CONDITIONS)),
        )
        self.assertFalse(decision.available)
        self.assertEqual(decision.reason, "index_unreadable")

    def test_index_is_never_loaded_before_permission_is_granted(self) -> None:
        """Permission before capability — the Goal 0025 ordering ruling."""
        calls: list[int] = []

        def _loader() -> Any:
            calls.append(1)
            return _FakeIndex(configs=500, reference=True)

        perception_lane.decide(
            index_loader=_loader,
            environ={perception_lane.INDEX_DIR_ENV: "/index"},
        )
        self.assertEqual(calls, [])


class PerceptionNeverMovesAGateTests(unittest.TestCase):
    """The non-negotiable: a permission is not a result."""

    def test_an_open_lane_still_reports_every_gate_not_measurable(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ack.json"
            path.write_text(
                json.dumps(
                    _declared(
                        accepted_conditions=list(perception_lane.STANDING_CONDITIONS)
                        + [
                            "embedding_backend_not_ratified",
                            "catalog_below_pilot_scale",
                        ]
                    )
                ),
                encoding="utf-8",
            )
            payload = perception_lane.decide(
                index=_FakeIndex(configs=4, reference=False),
                environ={
                    perception_lane.ACK_PATH_ENV: str(path),
                    perception_lane.INDEX_DIR_ENV: "/index",
                },
            ).to_json()
        self.assertTrue(payload["available"])
        self.assertEqual(payload["output_quality"], "diagnostic_not_validated")
        self.assertEqual(
            {item["verdict"] for item in payload["goal_0005_gates"]},
            {"not_measurable"},
        )
        self.assertFalse(payload["certifies_manufacturing"])
        self.assertFalse(payload["affects_geometry"])
        # The accepted conditions travel with the yes, not only with the no.
        self.assertTrue(payload["conditions"])

    def test_schema_forbids_a_condition_slug_it_does_not_know(self) -> None:
        """A typo'd slug cannot silently accept nothing."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ack.json"
            path.write_text(
                json.dumps(_declared(accepted_conditions=["gates_are_fine_honestly"])),
                encoding="utf-8",
            )
            with self.assertRaises(PerceptionError):
                perception_lane.load_acknowledgement(path)


class PerceptionQuarantineIsNotDemolitionTests(unittest.TestCase):
    """Track C is paused, not deleted. The operator may resume it."""

    def test_every_track_c_module_still_imports(self) -> None:
        import importlib

        for name in (
            "fast_mvp.catalog_index",
            "fast_mvp.embedding",
            "fast_mvp.image_lane",
            "fast_mvp.pipeline",
            "fast_mvp.proposer",
        ):
            self.assertIsNotNone(importlib.import_module(name))

    def test_the_switch_does_not_depend_on_the_lane_it_quarantines(self) -> None:
        """``perception_lane`` imports no Track C module.

        Structural, not stylistic: the module that decides whether the paused lane
        may answer must not need that lane importable to say no. The same layering
        argument that keeps ``alloy_prices`` out of the foundation package.
        """
        source = (ROOT / "fast_mvp" / "perception_lane.py").read_text(encoding="utf-8")
        for name in ("catalog_index", "embedding", "image_lane", "proposer"):
            self.assertNotIn(f"import {name}", source)
            self.assertNotIn(f"from fast_mvp.{name}", source)

    def test_lane_state_reports_quarantined_not_removed(self) -> None:
        state = perception_lane.lane_state(environ={})
        self.assertTrue(state["quarantined_not_removed"])
        self.assertFalse(state["available"])
        self.assertEqual(state["status"], "lane_paused")
        self.assertEqual(state["track"], "C")


class PerceptionSecondDoorTests(unittest.TestCase):
    """/api/extract reaches the same paused lane whenever RunPod is configured."""

    def _payload(self) -> dict[str, Any]:
        from tests.test_extractor_v2 import _data_url

        return {
            "image_data_url": _data_url(),
            "stone_shape": "round",
            "stone_length_mm": 6.5,
            "stone_width_mm": 6.5,
            "stone_depth_mm": 3.9,
            "ring_size_us": 6.5,
            "metal_color_family": "white",
            "metal_karat": "14k",
        }

    def test_photo_path_refuses_perception_and_uses_the_typed_prior(self) -> None:
        from fast_mvp.pipeline import analyze_photo_request

        report = analyze_photo_request(self._payload())
        extraction = report["extraction"]
        self.assertEqual(extraction["provider"], "perception_refused_typed_prior")
        self.assertIsNotNone(extraction["perception"])
        self.assertFalse(extraction["perception"]["available"])
        self.assertEqual(extraction["perception"]["reason"], "lane_paused")

    def test_typed_overrides_are_unaffected_by_the_quarantine(self) -> None:
        """A customer who typed their mounting never touched perception."""
        from fast_mvp.pipeline import analyze_photo_request

        payload = self._payload()
        payload["mounting_overrides"] = {
            "center_setting": "prong",
            "prong_count": 6,
            "band_width_mm": 2.0,
            "band_thickness_mm": 1.6,
        }
        report = analyze_photo_request(payload)
        self.assertEqual(report["extraction"]["provider"], "typed")
        self.assertIsNone(report["extraction"]["perception"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
