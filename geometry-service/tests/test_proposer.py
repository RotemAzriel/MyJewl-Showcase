"""Tests for fast_mvp.proposer — live-gated config proposal (Goal 0002)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_mvp import proposer  # noqa: E402
from jewelry_gaas_foundation import constraints  # noqa: E402
from jewelry_gaas_foundation.validation import validate_job_spec  # noqa: E402


BASE_PAYLOAD = {
    "intent": "classic yellow gold solitaire",
    "stone_shape": "round",
    "stone_length_mm": 6.5,
    "stone_width_mm": 6.5,
    "stone_depth_mm": 3.9,
    "ring_size_us": 6.5,
    "stone_carat_eq": 1.0,
}

GOOD_MOUNTING = {
    "center_setting": "prong",
    "prong_count": 4,
    "metal_alloy": "14k_yellow",
    "band_width_mm": 2.0,
    "band_thickness_mm": 1.6,
    "rationale": "classic four-prong solitaire",
}

# Bezel + prongs violates PRONG-009, a HARD schema/product-contract rule —
# stable across constraint-value re-verification (Goal 0003 adjusts empirical
# values/tiers, not logical contradictions).
BAD_MOUNTING = dict(GOOD_MOUNTING, center_setting="bezel", prong_count=4)


def _response(payload, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
    )


class _StubMessages:
    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _StubClient:
    def __init__(self, items):
        messages = _StubMessages(items)
        self.beta = SimpleNamespace(messages=messages)
        self.messages = messages


class OfflineProposerTests(unittest.TestCase):
    def test_offline_round_solitaire_accepted(self):
        report = proposer.propose_config(BASE_PAYLOAD, allow_llm=False)
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["provider"], "offline_heuristic")
        flat = report["flat_parameters"]
        self.assertEqual(flat["archetype"], "solitaire")
        self.assertEqual(flat["stone_shape"], "round")
        self.assertEqual(flat["ring_size_us"], 6.5)
        self.assertTrue(report["validation"]["constraint_accepted"])
        self.assertTrue(report["validation"]["schema_accepted"])
        for state in flat["geometry_gates"].values():
            self.assertEqual(state, "pending")

    def test_offline_bezel_platinum_intent(self):
        payload = dict(BASE_PAYLOAD, intent="sleek platinum bezel, minimal look")
        report = proposer.propose_config(payload, allow_llm=False)
        self.assertEqual(report["status"], "accepted")
        flat = report["flat_parameters"]
        self.assertEqual(flat["center_setting"], "bezel")
        self.assertEqual(flat["prong_count"], 0)
        self.assertEqual(flat["metal_alloy"], "pt950")
        self.assertEqual(flat["metal_type"], "platinum")

    def test_offline_pear_defaults_to_five_prongs(self):
        payload = dict(
            BASE_PAYLOAD,
            stone_shape="pear",
            stone_length_mm=8.0,
            stone_width_mm=5.5,
            intent="elegant pear solitaire",
        )
        report = proposer.propose_config(payload, allow_llm=False)
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["flat_parameters"]["prong_count"], 5)


class LlmProposerTests(unittest.TestCase):
    def test_valid_first_round_accepted(self):
        client = _StubClient([_response(GOOD_MOUNTING)])
        report = proposer.propose_config(BASE_PAYLOAD, client=client)
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["provider"], proposer.DEFAULT_MODEL)
        self.assertEqual(report["rounds"], 1)
        self.assertEqual(len(client.beta.messages.calls), 1)
        call = client.beta.messages.calls[0]
        self.assertEqual(call["betas"], [proposer.SERVER_SIDE_FALLBACK_BETA])
        self.assertEqual(call["fallbacks"], [{"model": proposer.FALLBACK_MODEL}])
        self.assertNotIn("thinking", call)
        self.assertEqual(
            call["output_config"]["format"]["schema"], proposer.MOUNTING_SCHEMA
        )

    def test_repair_loop_feeds_violations_back(self):
        client = _StubClient([_response(BAD_MOUNTING), _response(GOOD_MOUNTING)])
        report = proposer.propose_config(BASE_PAYLOAD, client=client)
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["rounds"], 2)
        self.assertEqual(len(report["rejected_candidates"]), 1)
        self.assertIn("PRONG-009", report["rejected_candidates"][0]["hard_violations"])
        second_call = client.beta.messages.calls[1]
        feedback = second_call["messages"][-1]["content"]
        self.assertIn("PRONG-009", feedback)

    def test_exhausted_rounds_returns_rejection_without_config(self):
        client = _StubClient([_response(BAD_MOUNTING)] * proposer.MAX_ROUNDS)
        report = proposer.propose_config(BASE_PAYLOAD, client=client)
        self.assertEqual(report["status"], "rejected")
        self.assertIsNone(report["flat_parameters"])
        self.assertIsNone(report["canonical_spec"])
        self.assertEqual(report["rounds"], proposer.MAX_ROUNDS)
        rule_ids = {
            rule
            for candidate in report["rejected_candidates"]
            for rule in candidate["hard_violations"]
        }
        self.assertIn("PRONG-009", rule_ids)

    def test_refusal_falls_back_to_heuristic(self):
        client = _StubClient(
            [SimpleNamespace(stop_reason="refusal", content=[])]
        )
        report = proposer.propose_config(BASE_PAYLOAD, client=client)
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["provider"], "offline_heuristic")
        self.assertIsNotNone(report["fallback_reason"])

    def test_client_error_falls_back_to_heuristic(self):
        client = _StubClient([RuntimeError("network down")])
        report = proposer.propose_config(BASE_PAYLOAD, client=client)
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["provider"], "offline_heuristic")
        self.assertIn("network down", report["fallback_reason"])

    def test_format_error_counts_as_round_then_recovers(self):
        bad_enum = dict(GOOD_MOUNTING, center_setting="tension")
        client = _StubClient([_response(bad_enum), _response(GOOD_MOUNTING)])
        report = proposer.propose_config(BASE_PAYLOAD, client=client)
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["rounds"], 2)
        feedback = client.beta.messages.calls[1]["messages"][-1]["content"]
        self.assertIn("format_error", feedback)

    def test_accepted_config_always_revalidates(self):
        """Whatever the model emits, an accepted report must pass the live gate."""
        adversarial = [
            GOOD_MOUNTING,
            dict(GOOD_MOUNTING, band_width_mm=3.9, band_thickness_mm=2.2),
            dict(GOOD_MOUNTING, center_setting="basket", prong_count=0),
            dict(GOOD_MOUNTING, metal_alloy="18k_rose", prong_count=6),
        ]
        for mounting in adversarial:
            client = _StubClient([_response(mounting)])
            report = proposer.propose_config(BASE_PAYLOAD, client=client)
            if report["status"] != "accepted":
                continue
            canonical = report["canonical_spec"]
            self.assertTrue(validate_job_spec(canonical).accepted)
            self.assertTrue(constraints.validate(canonical).accepted)


class MountingParseTests(unittest.TestCase):
    def test_bezel_with_prongs_passes_parse_but_fails_live_gate(self):
        """The app never reimplements rules — the live engine rejects bezel+prongs."""
        mounting = proposer.parse_mounting(
            dict(GOOD_MOUNTING, center_setting="bezel", prong_count=4)
        )
        self.assertEqual(mounting.prong_count, 4)
        from fast_mvp.pipeline import _parse_stone_inputs

        result = proposer.gate(_parse_stone_inputs(BASE_PAYLOAD), mounting)
        self.assertFalse(result.accepted)
        self.assertIn(
            "PRONG-009",
            [issue.rule_id for issue in result.constraint_result.hard_violations],
        )

    def test_alloy_normalization(self):
        mounting = proposer.parse_mounting(
            dict(GOOD_MOUNTING, metal_alloy="14K Yellow")
        )
        self.assertEqual(mounting.metal_alloy, "14k_yellow")

    def test_invalid_shapes_raise(self):
        for bad in (
            "not a dict",
            dict(GOOD_MOUNTING, center_setting="tension"),
            dict(GOOD_MOUNTING, metal_alloy="brass"),
            dict(GOOD_MOUNTING, band_width_mm="wide"),
        ):
            with self.assertRaises(proposer.ProposalFormatError):
                proposer.parse_mounting(bad)


if __name__ == "__main__":
    unittest.main()
