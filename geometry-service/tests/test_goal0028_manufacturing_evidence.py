#!/usr/bin/env python3
"""Goal 0028 -- named testimony intake, and the line it must never cross.

The point of these tests is not that the loader parses JSON. It is that an
answer from a person can widen the *question* (one more uncalibrated candidate
floor for a shop to react to) and can never widen the *claim*.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from jewelry_gaas_foundation import manufacturing_evidence as me
from jewelry_gaas_foundation.envelope_sweep import (
    CANDIDATE_FLOORS,
    active_candidate_floors,
)

ROOT = Path(__file__).resolve().parents[1]


def _declared_document() -> dict:
    """A record with one fully-specified finding. Synthetic, never committed."""
    document = json.loads(me.UNDECLARED_TEMPLATE_PATH.read_text(encoding="utf-8"))
    document["record_id"] = "test-fixture"
    document["status"] = "declared"
    document["recorded_by"] = {"name": "Test Fixture", "role": "test"}
    finding = document["findings"]["min_prong_base_diameter_mm"]
    finding.update(
        {
            "status": "declared",
            "value_mm": None,
            "range_mm": {"low": 1.0, "high": 1.2},
            "evidence_class": "expert_testimony",
            "attributed_to": {
                "name": "Fixture Person",
                "role": "bench jeweler",
                "affiliation": "",
            },
            "heard_on": "2026-08-18",
            "conversation_kind": "in_person",
            "alloy_scope": ["18k_yellow"],
            "process_scope": "cast",
            "measurement_basis": "post_finishing",
            "verbatim": "fixture",
        }
    )
    return document


class _TempRecord:
    """Write a document to a temp file and point the env var at it."""

    def __init__(self, document: dict) -> None:
        self.document = document

    def __enter__(self) -> str:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(self.document, handle)
        handle.close()
        self.path = handle.name
        self._previous = os.environ.get(me.RECORD_PATH_ENV)
        os.environ[me.RECORD_PATH_ENV] = self.path
        return self.path

    def __exit__(self, *exc: object) -> None:
        if self._previous is None:
            os.environ.pop(me.RECORD_PATH_ENV, None)
        else:
            os.environ[me.RECORD_PATH_ENV] = self._previous
        Path(self.path).unlink(missing_ok=True)


class ShippedStateTests(unittest.TestCase):
    """The repository ships recording nothing, and says so by name."""

    def setUp(self) -> None:
        self._previous = os.environ.pop(me.RECORD_PATH_ENV, None)

    def tearDown(self) -> None:
        if self._previous is not None:
            os.environ[me.RECORD_PATH_ENV] = self._previous

    def test_shipped_template_declares_nothing(self) -> None:
        record = me.active_record()
        self.assertFalse(record.declared)
        self.assertEqual(record.record_id, "undeclared-template")

    def test_every_shipped_finding_refuses_by_name(self) -> None:
        record = me.active_record()
        self.assertTrue(record.finding_keys(), "template should carry finding slots")
        for key in record.finding_keys():
            finding = record.finding(key)
            self.assertFalse(finding.usable, key)
            self.assertEqual(finding.reason, "record_undeclared", key)
            self.assertTrue(finding.detail.strip(), key)

    def test_shipped_state_contributes_no_candidate_floors(self) -> None:
        self.assertEqual(me.evidence_candidate_floors(), ())
        self.assertEqual(len(active_candidate_floors()), len(CANDIDATE_FLOORS))

    def test_template_validates_against_its_own_schema(self) -> None:
        me.load_record(me.UNDECLARED_TEMPLATE_PATH)  # raises on failure

    def test_unknown_finding_key_is_refused_not_defaulted(self) -> None:
        with _TempRecord(_declared_document()):
            finding = me.active_record().finding("min_shank_width_mm")
        self.assertFalse(finding.usable)
        self.assertEqual(finding.reason, "no_finding_for_key")


class RefusalTests(unittest.TestCase):
    """Each of the eleven conditions is named individually."""

    def _refusal_for(self, mutate) -> str:
        document = _declared_document()
        mutate(document["findings"]["min_prong_base_diameter_mm"])
        with _TempRecord(document):
            return me.active_record().finding("min_prong_base_diameter_mm").reason

    def test_declared_finding_is_usable(self) -> None:
        with _TempRecord(_declared_document()):
            finding = me.active_record().finding("min_prong_base_diameter_mm")
        self.assertTrue(finding.usable)
        self.assertEqual(finding.reason, "declared")

    def test_undeclared_record_refuses_even_a_complete_finding(self) -> None:
        document = _declared_document()
        document["status"] = "undeclared"
        with _TempRecord(document):
            finding = me.active_record().finding("min_prong_base_diameter_mm")
        self.assertFalse(finding.usable)
        self.assertEqual(finding.reason, "record_undeclared")

    def test_finding_undeclared(self) -> None:
        self.assertEqual(
            self._refusal_for(lambda f: f.update({"status": "undeclared"})),
            "finding_undeclared",
        )

    def test_no_value_recorded(self) -> None:
        self.assertEqual(
            self._refusal_for(
                lambda f: f.update({"value_mm": None, "range_mm": None})
            ),
            "no_value_recorded",
        )

    def test_evidence_class_unknown(self) -> None:
        self.assertEqual(
            self._refusal_for(lambda f: f.update({"evidence_class": "unknown"})),
            "evidence_class_unknown",
        )

    def test_attribution_missing(self) -> None:
        self.assertEqual(
            self._refusal_for(
                lambda f: f.update({"attributed_to": {"name": "  ", "role": "x"}})
            ),
            "attribution_missing",
        )

    def test_undated_evidence(self) -> None:
        self.assertEqual(
            self._refusal_for(lambda f: f.update({"heard_on": None})),
            "undated_evidence",
        )

    def test_process_scope_unknown(self) -> None:
        self.assertEqual(
            self._refusal_for(lambda f: f.update({"process_scope": "unknown"})),
            "process_scope_unknown",
        )

    def test_measurement_basis_unknown(self) -> None:
        self.assertEqual(
            self._refusal_for(lambda f: f.update({"measurement_basis": "unknown"})),
            "measurement_basis_unknown",
        )

    def test_unreadable_record_is_reported_not_raised(self) -> None:
        previous = os.environ.get(me.RECORD_PATH_ENV)
        os.environ[me.RECORD_PATH_ENV] = "/nonexistent/evidence.json"
        try:
            state = me.record_state()
        finally:
            if previous is None:
                os.environ.pop(me.RECORD_PATH_ENV, None)
            else:
                os.environ[me.RECORD_PATH_ENV] = previous
        self.assertEqual(state["status"], "evidence_unreadable")
        self.assertEqual(state["usable_findings"], 0)

    def test_schema_rejects_a_negative_measurement(self) -> None:
        document = _declared_document()
        document["findings"]["min_prong_base_diameter_mm"]["range_mm"] = {
            "low": -1.0,
            "high": 1.2,
        }
        with self.assertRaises(me.EvidenceError):
            me.EvidenceRecord(document)


class CalibrationBoundaryTests(unittest.TestCase):
    """The invariant: testimony never becomes calibration, however it is dressed."""

    def test_module_declares_it_does_not_calibrate(self) -> None:
        self.assertFalse(me.CALIBRATES)

    def test_a_usable_finding_is_still_uncalibrated(self) -> None:
        with _TempRecord(_declared_document()):
            finding = me.active_record().finding("min_prong_base_diameter_mm")
        self.assertTrue(finding.usable)
        self.assertFalse(finding.calibrated)
        self.assertFalse(finding.to_json()["calibrated"])
        self.assertFalse(finding.to_json()["certifies_manufacturing"])

    def test_record_claiming_calibration_cannot_smuggle_it_in(self) -> None:
        """A record asserting extra authority is refused by the closed schema."""
        for smuggled in ({"calibrated": True}, {"certifies_manufacturing": True}):
            document = _declared_document()
            document.update(smuggled)
            with self.assertRaises(me.EvidenceError, msg=smuggled):
                me.EvidenceRecord(document)

    def test_declared_testimony_reaches_the_table_still_uncalibrated(self) -> None:
        with _TempRecord(_declared_document()):
            floors = active_candidate_floors()
        self.assertGreater(len(floors), len(CANDIDATE_FLOORS))
        for floor in floors:
            self.assertFalse(floor.calibrated, floor.key)
        added = [f for f in floors if f.provenance == me.EVIDENCE_PROVENANCE]
        self.assertEqual(len(added), 2, "a range contributes both of its ends")
        for floor in added:
            self.assertIn("NOT", floor.source)
            self.assertIn("Fixture Person", floor.source)
            self.assertIn("2026-08-18", floor.source)

    def test_testimony_never_touches_the_shop_profile(self) -> None:
        """Calibration stays shop_profile's alone -- no active profile appears."""
        from jewelry_gaas_foundation import shop_profile

        with _TempRecord(_declared_document()):
            me.usable_findings()
            self.assertIsNone(shop_profile.active_profile())

    def test_testimony_moves_no_geometry_gate(self) -> None:
        """A declared record leaves every constraint verdict byte-identical."""
        from jewelry_gaas_foundation import constraints

        spec = json.loads(
            (ROOT / "examples" / "solitaire_valid.json").read_text(encoding="utf-8")
        )
        def verdict() -> tuple:
            result = constraints.validate(spec)
            return (
                result.accepted,
                tuple(result.hard_violations),
                tuple(result.soft_warnings),
                tuple(result.advisory_notes),
                tuple(result.schema_gaps),
            )

        before = verdict()
        with _TempRecord(_declared_document()):
            after = verdict()
        self.assertEqual(before, after)

    def test_module_does_not_import_fast_mvp(self) -> None:
        """The foundation package stays free of the app layer."""
        source = (
            ROOT / "jewelry_gaas_foundation" / "manufacturing_evidence.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("fast_mvp", source)


class CaptureSheetTests(unittest.TestCase):
    """The operator-facing sheet must stay in step with the fields it maps to."""

    SHEET = ROOT / "docs" / "prong_wire_call_sheet_2026-08-18.md"

    def test_sheet_exists(self) -> None:
        self.assertTrue(self.SHEET.is_file())

    def test_sheet_names_every_finding_the_template_carries(self) -> None:
        text = self.SHEET.read_text(encoding="utf-8")
        for key in me.active_record().finding_keys():
            self.assertIn(key, text, f"capture sheet does not cover {key}")

    def test_sheet_names_the_scope_fields_that_cause_refusals(self) -> None:
        text = self.SHEET.read_text(encoding="utf-8")
        for field in (
            "process_scope",
            "measurement_basis",
            "evidence_class",
            "alloy_scope",
        ):
            self.assertIn(field, text)

    def test_sheet_states_it_does_not_calibrate(self) -> None:
        text = self.SHEET.read_text(encoding="utf-8").lower()
        self.assertIn("moves no gate", text)


if __name__ == "__main__":
    unittest.main()
