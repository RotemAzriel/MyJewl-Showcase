"""Goal 0020 — the review-record contract.

These tests exist because a schema nothing asserts is decoration. Two claims in
docs/review_protocol.md are load-bearing and are pinned here:

  1. The schema is a valid Draft 2020-12 schema and enforces the properties the protocol
     relies on -- most importantly that a review can never certify manufacturing, and that
     a correction or rejection cannot be recorded as a bare mood.
  2. "Migration is additive": every visual-review record MyJewl already holds is readable
     as a stage-1 record under the new schema. If that stops being true, this fails rather
     than the claim quietly rotting in prose.
"""
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schemas" / "review_record_v0_1.schema.json"
REVIEW_MANIFEST_PATH = (
    REPO_ROOT / "fast_mvp" / "preview_assets" / "goal0014_v5_visual_reviews.json"
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def minimal_record(**overrides) -> dict:
    """A valid stage-1 clean approval. Overrides let each test change exactly one thing."""
    record = {
        "schema": "myjewl.review_record.v0_1",
        "record_id": "goal0020-example-us4",
        "stage": "stage_1_per_configuration",
        "scope": "V6 castable master, US 4.0 / 5.00 mm round",
        "subject": {
            "template_id": "goal0018-castable-master-v6",
            "configuration_id": "us4_round5",
            "definition_sha256": None,
            "receipt_sha256": None,
            "inherited_from": None,
        },
        "reviewer": {"reviewer_id": "operator", "role": "operator"},
        "reviewed_at": "2026-08-17",
        "checklist": [
            {
                "item_id": "stone_visibly_seated_and_retained",
                "verdict": "pass",
                "measured": {
                    "metric": "min_wall_thickness_mm",
                    "value": 0.2394,
                    "unit": "mm",
                    "source": "geometry_metrics.wall_thickness_mm",
                },
            }
        ],
        "decision": "approved",
        "corrections": [],
        "calibration": {"is_seeded_probe": False},
        "certifies_manufacturing": False,
        "manufacturing_authority": "pending_not_run_needs_calibration",
    }
    record.update(overrides)
    return record


class ReviewRecordSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = read_json(SCHEMA_PATH)
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def test_minimal_stage_1_record_is_valid(self) -> None:
        self.validator.validate(minimal_record())

    def test_review_can_never_certify_manufacturing(self) -> None:
        """The brand-protecting rule, enforced by the schema rather than by convention."""
        for value in (True, "passed", "true"):
            with self.subTest(value=value):
                record = minimal_record(certifies_manufacturing=value)
                self.assertFalse(self.validator.is_valid(record))

    def test_manufacturing_authority_cannot_be_relaxed(self) -> None:
        record = minimal_record(manufacturing_authority="passed")
        self.assertFalse(self.validator.is_valid(record))

    def test_approved_with_corrections_requires_a_correction(self) -> None:
        """Otherwise the correction rate -- the graduation metric -- is unmeasurable."""
        record = minimal_record(decision="approved_with_corrections", corrections=[])
        self.assertFalse(self.validator.is_valid(record))

        record["corrections"] = [
            {
                "parameter": "seat_depth_mm",
                "current_value": 0.3,
                "proposed_value": 0.4,
                "unit": "mm",
                "reason": "The girdle is not far enough into the seat to be retained.",
                "applies_to": "whole_envelope",
                "severity": "should_fix",
                "requires_new_parameter": False,
            }
        ]
        self.validator.validate(record)

    def test_correction_requires_a_reason(self) -> None:
        """A number without a reason does not generalise to other configurations."""
        record = minimal_record(
            decision="approved_with_corrections",
            corrections=[{"parameter": "seat_depth_mm", "proposed_value": 0.4}],
        )
        self.assertFalse(self.validator.is_valid(record))

    def test_rejection_must_be_actionable(self) -> None:
        """'It looks wrong' costs a paid re-solve and teaches nothing."""
        record = minimal_record(decision="rejected")
        self.assertFalse(self.validator.is_valid(record))

        record["rejection"] = {
            "categories": ["prong_form"],
            "reason": "Prong tips read as melee gauge against a 5 mm centre.",
            "would_accept_if": "Prong wire raised to the shop's minimum castable diameter.",
        }
        self.validator.validate(record)

    def test_seeded_probe_records_its_expected_decision(self) -> None:
        """Probes are how we detect a reviewer who has stopped looking."""
        record = minimal_record(
            decision="rejected",
            rejection={
                "categories": ["manufacturability_concern"],
                "reason": "Prong wire is visibly below anything castable.",
            },
            calibration={
                "is_seeded_probe": True,
                "seeded_defect": "prong wire narrowed to 0.24 mm",
                "expected_decision": "rejected",
                "probe_caught": True,
                "blinded": True,
            },
        )
        self.validator.validate(record)

    def test_unknown_fields_are_refused(self) -> None:
        """additionalProperties: false -- a typo must not silently become unrecorded data."""
        record = minimal_record(reviewer_notes="looks good")
        self.assertFalse(self.validator.is_valid(record))

    def test_checklist_verdict_cannot_default_to_pass(self) -> None:
        record = minimal_record()
        del record["checklist"][0]["verdict"]
        self.assertFalse(self.validator.is_valid(record))


class ExistingReviewsMigrateAdditivelyTests(unittest.TestCase):
    """Pins the protocol's 'migration is additive' claim against the real records on disk."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = Draft202012Validator(read_json(SCHEMA_PATH))
        cls.manifest = read_json(REVIEW_MANIFEST_PATH)

    def test_manifest_shape_is_unchanged(self) -> None:
        """The promoter still reads this file; Goal 0020 must not have touched it."""
        self.assertEqual(self.manifest["schema"], "myjewl.goal0014_v5.visual_reviews.v1")
        self.assertEqual(
            set(self.manifest["reviews"]), {"us4_round5", "us6p5_round6p5", "us9_round8"}
        )

    def test_every_existing_review_is_expressible_as_a_stage_1_record(self) -> None:
        for configuration_id, entry in self.manifest["reviews"].items():
            with self.subTest(configuration=configuration_id):
                source_path = REPO_ROOT / entry["path"]
                if not source_path.exists():
                    # Records live under output/, which is local evidence and gitignored
                    # (Goal 0020). Absence on a fresh clone is expected, not a failure.
                    self.skipTest(f"local evidence not present: {entry['path']}")

                legacy = read_json(source_path)
                migrated = self._migrate(configuration_id, entry, legacy)
                self.validator.validate(migrated)

                # The additive claim: every legacy check survives as a checklist item,
                # and no verdict is invented for an item the operator never answered.
                self.assertEqual(len(migrated["checklist"]), len(legacy["checks"]))
                self.assertEqual(migrated["corrections"], [])
                self.assertFalse(migrated["calibration"]["is_seeded_probe"])

    @staticmethod
    def _migrate(configuration_id: str, entry: dict, legacy: dict) -> dict:
        """Pure, lossless read of a legacy record into the v0.1 shape.

        Deliberately not shipped as production code: nothing promotes from migrated
        records today, so this stays a test fixture until something needs it.
        """
        return {
            "schema": "myjewl.review_record.v0_1",
            "record_id": f"legacy-{configuration_id}",
            "stage": "stage_1_per_configuration",
            "scope": legacy.get("scope") or configuration_id,
            "subject": {
                "template_id": legacy.get("template_id") or "unknown",
                "configuration_id": configuration_id,
                "definition_sha256": None,
                "receipt_sha256": legacy.get("source_receipt_sha256"),
                "inherited_from": entry.get("inherited_from"),
            },
            "reviewer": {
                "reviewer_id": legacy.get("reviewer") or "unknown",
                "role": "operator",
            },
            "reviewed_at": legacy["reviewed_at"],
            "evidence": {
                "rendered_views": legacy.get("rendered_views", []),
                "render_evidence_sha256": legacy.get("render_evidence_sha256"),
            },
            "checklist": [
                {"item_id": item_id, "verdict": "pass" if passed else "fail"}
                for item_id, passed in sorted(legacy["checks"].items())
            ],
            "decision": legacy["decision"],
            "decision_note": entry.get("reason") or legacy.get("note", ""),
            "corrections": [],
            "calibration": {"is_seeded_probe": False},
            "certifies_manufacturing": False,
            "manufacturing_authority": "pending_not_run_needs_calibration",
        }


if __name__ == "__main__":
    unittest.main()
