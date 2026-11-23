#!/usr/bin/env python3
"""What a named person *said* about a manufacturing limit -- and why that is not calibration.

Goal 0028. Every one of the last five goals ended with the same sentence: the
remaining blockers are conversations with people. This module is the intake for
the first of those conversations to become reachable, and it exists because of a
gap in the repository's own rule.

``docs/geometry_research_brief_2026-08-12.md`` Part 3 states: *"Every number lands
in ``jewelry_gaas_foundation.manufacturing_assumptions`` with its source and
date. Nothing enters the codebase as an uncited constant."* That rule could not be
followed. ``ManufacturingFloor`` has no date field, no attribution, and no evidence
class; ``envelope_sweep.CandidateFloor`` has a prose ``source`` and a ``provenance``
enum but likewise no date and no named person. So an answer arriving from a live
conversation had two possible homes, both bad: a hand-edited Python tuple (uncited,
undated, attributed to nobody) or prose in a document that no code reads.

**The distinction this module exists to hold.** A shop *measuring its own process*
is calibration and belongs in ``shop_profile`` -- that is the only path by which a
number becomes usable for a pass/fail. An experienced person *telling you a number
over a call* is testimony. Testimony is genuinely valuable: it is how you find out
that a figure you shipped is half the trade standard. It is not a measurement of
the process that will cast your ring, and the two must never converge in the code.

So a declared finding here can do exactly one thing: **become another uncalibrated
candidate floor** -- one more column in the sensitivity table a casting shop is
asked to pick from, now carrying who said it and when. It can never set
``calibrated=True``, never supersede a ``ShopProfile``, and never move a geometry
gate. ``CALIBRATES`` is ``False`` and there is no code path that reads otherwise;
a test asserts it adversarially.

**A finding is usable only if all of these hold**, and each failure is named:

1. a record is loaded at all;
2. it parses and validates against the schema;
3. the record's ``status`` is ``declared``;
4. the finding key has an entry;
5. the entry's ``status`` is ``declared``;
6. it carries a number -- a single value or a range;
7. its ``evidence_class`` is not ``unknown``;
8. it is attributed to a *named* person, because "someone in the trade says" is
   not evidence and cannot be followed up;
9. it is dated -- undated testimony cannot be aged out or revalidated;
10. its ``process_scope`` is known, because a fabricated-prong minimum is not a
    cast-prong minimum and conflating them is the exact error the brief warns of;
11. its ``measurement_basis`` is known, because MyJewl ships finished-nominal
    geometry (``DIMENSION_BASIS``) and a pre-finishing minimum is not comparable.

The shipped template at ``profiles/manufacturing_evidence_undeclared_template_v0_1.json``
declares nothing and refuses every one of its own findings, deliberately -- the same
posture as the shop profile, the alloy price sheet, and the perception acknowledgement.

Nothing here certifies manufacturability. Geometry gates stay ``pending`` /
``not_run`` / ``needs_calibration`` until a calibrated shop profile *and* human
jeweler review exist.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_SCHEMA_PATH = ROOT / "schemas" / "manufacturing_evidence_v0_1.schema.json"
UNDECLARED_TEMPLATE_PATH = (
    ROOT / "profiles" / "manufacturing_evidence_undeclared_template_v0_1.json"
)
RECORD_SCHEMA = "myjewl.manufacturing_evidence.v0_1"
RECORD_PATH_ENV = "MYJEWL_MANUFACTURING_EVIDENCE"

#: The invariant. Testimony never calibrates anything, whatever it says. Only a
#: calibrated ShopProfile plus human jeweler review makes a number usable for a
#: verdict, and this module has no path to either.
CALIBRATES = False

#: How a declared finding is labelled once it reaches the sensitivity table. It
#: joins the existing "third_party_unverified" / "question_for_the_shop" columns
#: rather than outranking them.
EVIDENCE_PROVENANCE = "named_testimony_uncalibrated"


class EvidenceError(RuntimeError):
    """Raised when an evidence record cannot be read or does not validate."""


@dataclass(frozen=True)
class EvidenceFinding:
    """One answer, and whether it may be used as a candidate floor.

    ``usable`` false always carries a ``reason`` naming which condition failed.
    The payload has the same shape either way, so a caller never has to branch on
    presence -- the house pattern established by ``alloy_prices.active_price``,
    ``quote_volumes.volume_for_manifest``, ``geometry_delivery.decide`` and
    ``perception_lane.decide``.
    """

    key: str
    usable: bool
    reason: str
    detail: str
    value_mm: float | None = None
    range_low_mm: float | None = None
    range_high_mm: float | None = None
    evidence_class: str = "unknown"
    attributed_name: str = ""
    attributed_role: str = ""
    attributed_affiliation: str = ""
    heard_on: str | None = None
    conversation_kind: str = "unknown"
    alloy_scope: tuple[str, ...] = ()
    process_scope: str = "unknown"
    measurement_basis: str = "unknown"
    verbatim: str = ""
    notes: str = ""

    @property
    def calibrated(self) -> bool:
        """Always False. Testimony is not calibration, however well sourced."""
        return CALIBRATES

    def values_mm(self) -> tuple[float, ...]:
        """Every distinct figure this finding puts on the table, ascending.

        A range contributes *both* ends, because the low end is the one that
        decides whether our envelope clears it and the high end is the one a
        shop may actually insist on.
        """
        found: list[float] = []
        for candidate in (self.value_mm, self.range_low_mm, self.range_high_mm):
            if candidate is not None and candidate not in found:
                found.append(candidate)
        return tuple(sorted(found))

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "usable": self.usable,
            "reason": self.reason,
            "detail": self.detail,
            "calibrated": self.calibrated,
            "certifies_manufacturing": False,
            "value_mm": self.value_mm,
            "range_mm": (
                None
                if self.range_low_mm is None and self.range_high_mm is None
                else {"low": self.range_low_mm, "high": self.range_high_mm}
            ),
            "values_mm": list(self.values_mm()),
            "evidence_class": self.evidence_class,
            "attributed_to": {
                "name": self.attributed_name,
                "role": self.attributed_role,
                "affiliation": self.attributed_affiliation,
            },
            "heard_on": self.heard_on,
            "conversation_kind": self.conversation_kind,
            "alloy_scope": list(self.alloy_scope),
            "process_scope": self.process_scope,
            "measurement_basis": self.measurement_basis,
            "verbatim": self.verbatim,
            "notes": self.notes,
        }


class EvidenceRecord:
    """A validated evidence document. Refuses its own findings by name."""

    def __init__(self, document: dict[str, Any]) -> None:
        schema = json.loads(EVIDENCE_SCHEMA_PATH.read_text(encoding="utf-8"))
        errors = sorted(
            Draft202012Validator(schema).iter_errors(document),
            key=lambda err: list(err.path),
        )
        if errors:
            joined = "; ".join(
                f"{'/'.join(str(part) for part in err.path) or '<root>'}: {err.message}"
                for err in errors[:5]
            )
            raise EvidenceError(f"evidence record failed schema validation: {joined}")
        self._document = document

    @property
    def record_id(self) -> str:
        return str(self._document["record_id"])

    @property
    def record_version(self) -> str:
        return str(self._document["record_version"])

    @property
    def status(self) -> str:
        return str(self._document["status"])

    @property
    def declared(self) -> bool:
        return self.status == "declared"

    @property
    def recorded_on(self) -> str:
        return str(self._document["validity"]["recorded_on"])

    @property
    def recorded_by(self) -> dict[str, str]:
        return dict(self._document["recorded_by"])

    def finding_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._document.get("findings", {})))

    def finding(self, key: str) -> EvidenceFinding:
        """Resolve one finding, naming the first condition that fails."""

        def refuse(reason: str, detail: str, **extra: Any) -> EvidenceFinding:
            return EvidenceFinding(
                key=key, usable=False, reason=reason, detail=detail, **extra
            )

        if not self.declared:
            return refuse(
                "record_undeclared",
                f"Evidence record '{self.record_id}' has status "
                f"'{self.status}'. Nobody has confirmed that any of this was "
                "actually said, so none of its findings is offered as a candidate "
                "floor. Nothing was assumed.",
            )

        entry = self._document.get("findings", {}).get(key)
        if entry is None:
            return refuse(
                "no_finding_for_key",
                f"Evidence record '{self.record_id}' records nothing about "
                f"'{key}'. There is no wildcard finding -- a number given about "
                "one section says nothing about another.",
            )

        attributed = entry.get("attributed_to", {}) or {}
        fields: dict[str, Any] = {
            "value_mm": entry.get("value_mm"),
            "range_low_mm": (entry.get("range_mm") or {}).get("low"),
            "range_high_mm": (entry.get("range_mm") or {}).get("high"),
            "evidence_class": entry.get("evidence_class", "unknown"),
            "attributed_name": str(attributed.get("name", "") or ""),
            "attributed_role": str(attributed.get("role", "") or ""),
            "attributed_affiliation": str(attributed.get("affiliation", "") or ""),
            "heard_on": entry.get("heard_on"),
            "conversation_kind": entry.get("conversation_kind", "unknown"),
            "alloy_scope": tuple(entry.get("alloy_scope", []) or ()),
            "process_scope": entry.get("process_scope", "unknown"),
            "measurement_basis": entry.get("measurement_basis", "unknown"),
            "verbatim": str(entry.get("verbatim", "") or ""),
            "notes": str(entry.get("notes", "") or ""),
        }

        if entry.get("status") != "declared":
            return refuse(
                "finding_undeclared",
                f"The '{key}' finding is marked "
                f"'{entry.get('status')}'. It is a placeholder showing the shape "
                "of a real answer, not an answer.",
                **fields,
            )

        if fields["value_mm"] is None and fields["range_low_mm"] is None:
            return refuse(
                "no_value_recorded",
                f"The '{key}' finding is declared but carries no number -- "
                "neither a value_mm nor a range_mm. A qualitative answer is worth "
                "recording and is kept in 'verbatim', but it cannot become a floor.",
                **fields,
            )

        if fields["evidence_class"] == "unknown":
            return refuse(
                "evidence_class_unknown",
                f"The '{key}' finding does not say what kind of evidence it is. "
                "A published trade standard and one bench jeweler's habit are both "
                "worth having and are not the same thing.",
                **fields,
            )

        if not fields["attributed_name"].strip():
            return refuse(
                "attribution_missing",
                f"The '{key}' finding names nobody. 'Someone in the trade says' "
                "cannot be followed up, cannot be weighed against a conflicting "
                "answer, and cannot be revalidated later.",
                **fields,
            )

        if not fields["heard_on"]:
            return refuse(
                "undated_evidence",
                f"The '{key}' finding carries no date, so it can never be aged "
                "out or revalidated. The brief's own rule is 'source and date'.",
                **fields,
            )

        if fields["process_scope"] == "unknown":
            return refuse(
                "process_scope_unknown",
                f"The '{key}' finding does not say whether it applies to cast or "
                "fabricated work. MyJewl casts; a fabricated-wire minimum is a "
                "different number and assuming they match is the error "
                "docs/geometry_research_brief_2026-08-12.md warns about.",
                **fields,
            )

        if fields["measurement_basis"] == "unknown":
            return refuse(
                "measurement_basis_unknown",
                f"The '{key}' finding does not say whether the number is before "
                "or after finishing. MyJewl ships finished-nominal geometry "
                "(DIMENSION_BASIS), so a pre-finishing minimum is not directly "
                "comparable to what we export.",
                **fields,
            )

        return EvidenceFinding(
            key=key,
            usable=True,
            reason="declared",
            detail=(
                f"Recorded from {fields['attributed_name']}"
                + (f" ({fields['attributed_role']})" if fields["attributed_role"] else "")
                + f" on {fields['heard_on']}. This is "
                f"{fields['evidence_class'].replace('_', ' ')}, NOT calibration: it "
                "adds an uncalibrated candidate floor and moves no gate."
            ),
            **fields,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "record_version": self.record_version,
            "status": self.status,
            "declared": self.declared,
            "recorded_on": self.recorded_on,
            "recorded_by": self.recorded_by,
            "calibrates": CALIBRATES,
            "certifies_manufacturing": False,
            "findings": {
                key: self.finding(key).to_json() for key in self.finding_keys()
            },
        }


def load_record(path: Path | str) -> EvidenceRecord:
    """Load and validate an evidence record from disk."""
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read evidence record: {path}") from exc
    return EvidenceRecord(document)


def active_record(path: Path | str | None = None) -> EvidenceRecord:
    """Resolve the record in force: explicit path, then env var, then the template.

    The shipped template declares nothing, so the default state of the repository
    is that every finding refuses itself.
    """
    if path is not None:
        return load_record(path)
    from_env = os.environ.get(RECORD_PATH_ENV)
    if from_env:
        return load_record(from_env)
    return load_record(UNDECLARED_TEMPLATE_PATH)


def usable_findings(path: Path | str | None = None) -> tuple[EvidenceFinding, ...]:
    """Every finding that survives all eleven conditions. Empty as shipped."""
    record = active_record(path)
    resolved = (record.finding(key) for key in record.finding_keys())
    return tuple(item for item in resolved if item.usable)


def evidence_candidate_floors(
    path: Path | str | None = None,
) -> tuple[tuple[str, float, str, str, str], ...]:
    """Declared testimony as ``(key, value_mm, label, source, provenance)`` rows.

    Consumed by ``envelope_sweep`` to widen the floor-sensitivity table. Returned
    as plain tuples rather than ``CandidateFloor`` instances so the dependency
    runs one way only and this module stays importable on its own.

    Every row is uncalibrated by construction -- ``provenance`` is
    ``EVIDENCE_PROVENANCE`` and the consumer builds the floor with its own
    ``calibrated=False`` default, which has no override.
    """
    rows: list[tuple[str, float, str, str, str]] = []
    for finding in usable_findings(path):
        values = finding.values_mm()
        for value in values:
            bound = ""
            if len(values) > 1:
                bound = " (low end)" if value == values[0] else " (high end)"
            attribution = finding.attributed_name
            if finding.attributed_role:
                attribution += f", {finding.attributed_role}"
            if finding.attributed_affiliation:
                attribution += f" ({finding.attributed_affiliation})"
            rows.append(
                (
                    f"testimony_{finding.key}_{str(value).replace('.', 'p')}",
                    float(value),
                    f"{value:g} mm",
                    (
                        f"Stated by {attribution} on {finding.heard_on}, for "
                        f"{finding.process_scope} work, "
                        f"{finding.measurement_basis.replace('_', '-')}"
                        f"{bound}. Recorded as "
                        f"{finding.evidence_class.replace('_', ' ')} and NOT "
                        "calibrated -- a person's answer, not a measurement of the "
                        "process that will cast this ring."
                    ),
                    EVIDENCE_PROVENANCE,
                )
            )
    return tuple(rows)


def record_state(path: Path | str | None = None) -> dict[str, Any]:
    """Operator-facing summary of what testimony has been recorded, if any."""
    try:
        record = active_record(path)
    except EvidenceError as exc:
        return {
            "status": "evidence_unreadable",
            "usable_findings": 0,
            "calibrates": CALIBRATES,
            "certifies_manufacturing": False,
            "detail": str(exc),
        }
    findings = {key: record.finding(key) for key in record.finding_keys()}
    usable = {key: item for key, item in findings.items() if item.usable}
    return {
        "status": "declared" if record.declared else "record_undeclared",
        "record_id": record.record_id,
        "recorded_on": record.recorded_on,
        "usable_findings": len(usable),
        "refused_findings": {
            key: item.reason for key, item in findings.items() if not item.usable
        },
        "calibrates": CALIBRATES,
        "certifies_manufacturing": False,
        "detail": (
            "Testimony recorded here adds uncalibrated candidate floors only. It "
            "never calibrates a threshold, never supersedes a shop profile, and "
            "never moves a geometry gate."
        ),
    }
