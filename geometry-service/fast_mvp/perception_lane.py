"""Whether MyJewl's perception lane may answer at all, and why it will not.

Goal 0026. Track C was paused by the operator on **2026-08-11** — geometry and
perception errors compound and become unattributable, so geometry gets verified
independently first. It was paused, but never unwired: ``fast_mvp/server.py``
still served ``POST /api/propose-from-image`` to anyone who asked, and every Goal
0005 gate behind it reads ``not_measurable``. This module makes the pause true at
the boundary, the way every other unproven claim in this repository already
refuses: by name, with the evidence attached.

It is the perception counterpart of ``alloy_prices`` (a price) and
``geometry_delivery`` (bytes), and copies their shape deliberately — a frozen
decision whose payload is *identical whether the answer is yes or no*, a ``reason``
slug plus a human ``detail``, and evidence either way.

**The lane may answer only if all of these hold:**

1. an acknowledgement file is declared at all (``MYJEWL_PERCEPTION_ACK``);
2. it loads and validates against its schema;
3. its ``status`` is ``declared`` — the committed template is ``undeclared``;
4. it has not expired;
5. a catalog index is configured (``MYJEWL_IMAGE_INDEX_DIR``);
6. that index actually loads;
7. **every live condition is individually named in ``accepted_conditions``.**

**Permission is checked before capability, on purpose.** Whether this deployment
*may* run perception must read the same on every machine; whether it *can* depends
on which laptop has an index directory on disk. That is the same ordering ruling
Goal 0025 made when it checked mesh policy before bytes, and for the same reason.

**An acknowledgement never makes a gate measurable.** It is a permission, not a
result. This is the perception form of the shop-profile rule that a profile alone
can never flip a gate to ``passed``: G-B/G-C/G-D/G-E stay ``not_measurable``
whatever this file says, every accepted response carries that fact with it, and a
test asserts it. What an acknowledgement buys is *diagnostic* output, labelled.

**There is no wildcard condition**, for the reason Goal 0024 refuses a wildcard
alloy: a blanket accept silently covers whatever is discovered next. An operator
who signed before a condition existed has not accepted that condition, so the lane
refuses ``condition_not_acknowledged`` rather than assuming consent.

**Quarantine, not demolition.** Nothing in Track C is deleted. ``image_lane``,
``catalog_index``, ``embedding``, ``pipeline`` and ``proposer`` are untouched and
their tests still pass; the operator may resume the lane after the geometry
conversations land, and this module is the switch.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
ACK_SCHEMA_PATH = ROOT / "schemas" / "perception_acknowledgement_v0_1.schema.json"
UNDECLARED_TEMPLATE_PATH = (
    ROOT / "profiles" / "perception_acknowledgement_undeclared_template_v0_1.json"
)
ACK_SCHEMA = "myjewl.perception_acknowledgement.v0_1"

# The operator's pause. Recorded here so a refusal can state when, not just that.
PAUSED_ON = "2026-08-11"
PAUSE_REASON = (
    "geometry and perception errors compound and become unattributable, so the "
    "geometry layer is verified independently first"
)

ACK_PATH_ENV = "MYJEWL_PERCEPTION_ACK"
INDEX_DIR_ENV = "MYJEWL_IMAGE_INDEX_DIR"
BACKEND_ENV = "MYJEWL_IMAGE_BACKEND"

# Harness guards, read from the evaluation harness rather than restated, so the
# boundary and the evaluator cannot drift into disagreeing about what "too small"
# means. scripts/eval_image_to_config.py is the authority for both numbers.
MIN_SYNTHETIC_CONFIGS = 20

# Goal 0005's gate verdicts, transcribed from the run that produced them.
#
# This is a transcription and says so. `output/` is gitignored (Goal 0020), so the
# report cannot be re-read or re-hashed on another machine the way Goal 0025
# re-reads a delivery receipt. What makes it honest is that the two conditions a
# deployment could actually change -- which backend embedded the index, and how
# many configs it holds -- are NOT taken from here. They are recomputed from the
# index this process loaded. Only the facts about the evaluation itself, which no
# deployment can alter by loading a different index, are pinned.
GATE_STATUS_SOURCE = "output/goal_0005_pilot_20260725/eval_report.json"
GATE_STATUS_MEASURED_ON = "2026-07-27"
GOAL_0005_GATES: tuple[dict[str, Any], ...] = (
    {
        "gate": "G-B",
        "verdict": "not_measurable",
        "blockers": (
            "embedding backend 'tile_statistics_v1' is not the ratified DINOv3 "
            "paradigm; numbers are diagnostic only",
            "catalog holds only 4 configs (harness guard: 20); the plan's pilot "
            "is ~100",
        ),
    },
    {
        "gate": "G-C",
        "verdict": "not_measurable",
        "blockers": ("no real mini-holdout supplied",),
    },
    {
        "gate": "G-D",
        "verdict": "not_measurable",
        "blockers": (
            "embedding backend 'tile_statistics_v1' is not the ratified DINOv3 "
            "paradigm; numbers are diagnostic only",
            "only 0 distractors (min 5); OOD rejection is not measurable",
        ),
    },
    {
        "gate": "G-E",
        "verdict": "not_measurable",
        "blockers": (
            "G-B is not_measurable",
            "G-C is not_measurable",
            "G-D is not_measurable",
        ),
    },
)

# Conditions that hold no matter what this deployment loads, because they are
# properties of the evaluation rather than of the index. An operator must accept
# each by name.
STANDING_CONDITIONS: tuple[str, ...] = (
    "gates_not_measurable",
    "no_real_holdout",
    "no_distractors_ood_unmeasured",
)

CONDITION_DETAIL: dict[str, str] = {
    "gates_not_measurable": (
        "Every Goal 0005 gate (G-B, G-C, G-D, G-E) reads not_measurable. No "
        "retrieval verdict has ever been earned; the harness withholds one rather "
        "than guessing."
    ),
    "embedding_backend_not_ratified": (
        "The loaded index was embedded by a declared fallback backend, not the "
        "ratified DINOv3 paradigm. Its similarity numbers are diagnostic only and "
        "no gate may be stamped on them."
    ),
    "catalog_below_pilot_scale": (
        "The catalog index holds fewer configurations than the evaluation "
        "harness's own guard, and far fewer than the plan's ~100-config pilot. "
        "Recall measured against it does not generalise."
    ),
    "no_real_holdout": (
        "Retrieval has only ever been measured on synthetic renders. No real "
        "photograph holdout has been curated, so nothing here is known to survive "
        "a real camera."
    ),
    "no_distractors_ood_unmeasured": (
        "The evaluation contained no distractors, so out-of-distribution rejection "
        "is unmeasured. How this lane behaves on a photo of something that is not "
        "one of our rings is unknown."
    ),
}

LANE_SCOPE_NOTE = (
    "The perception lane infers MOUNTING ONLY. Stone shape and dimensions, ring "
    "size, and metal are typed inputs on every path and are never read off a "
    "photograph (settled decision 6). Nothing here certifies manufacturing."
)

# Goal 0027. The sentence a *customer* reads, authored here and nowhere else.
#
# Every other surface -- /api/health, /api/extract, /api/propose-from-image,
# /studio, the landing page -- renders this rather than writing its own wording,
# for the reason the four refusal modules already share: a claim restated in a
# second place is a claim that can drift. It is deliberately not the operator
# `detail` string above. An operator needs the condition slug and the pause date;
# a customer needs to know whether their photograph was looked at, and that
# question has exactly two answers.
#
# The shape is identical whether the lane answers or refuses (the house pattern),
# so a surface that renders it cannot accidentally render only the happy path.
CUSTOMER_DISCLOSURE_REFUSED = {
    "reads_your_photo": False,
    "headline": "Your photo is not being read",
    "statement": (
        "MyJewl is not reading photographs right now. Your picture is not used "
        "to choose the setting, the prongs or the band, and it is not kept with "
        "your design. Everything you see is built from the stone size, ring size "
        "and metal you typed."
    ),
    "mounting_source": "typed_shape_prior",
}
CUSTOMER_DISCLOSURE_AVAILABLE = {
    "reads_your_photo": True,
    "headline": "Your photo is read as an unvalidated suggestion",
    "statement": (
        "MyJewl compared your photo against its own catalog to suggest the "
        "setting, the prongs and the band proportions. That suggestion is "
        "diagnostic and has not passed an accuracy gate, so check it before you "
        "submit. Stone size, ring size and metal always come from what you "
        "typed, never from the photo."
    ),
    "mounting_source": "photo_retrieval_diagnostic",
}


def customer_disclosure(available: bool) -> dict[str, Any]:
    """What to tell the person who uploaded the photograph.

    Fail-closed by construction: the refusing text is what a caller gets unless
    the lane positively reported that it answered. A surface that cannot reach
    the gate must therefore say "not read", which is the shipped truth and the
    safe direction -- claiming we looked at a picture we ignored is the harm this
    exists to stop, and the reverse understates rather than overstates.
    """
    return dict(
        CUSTOMER_DISCLOSURE_AVAILABLE if available else CUSTOMER_DISCLOSURE_REFUSED
    )


class PerceptionError(ValueError):
    """The acknowledgement itself is unusable."""


@dataclass(frozen=True)
class PerceptionAcknowledgement:
    """A named operator's dated permission to serve diagnostic perception output."""

    label: str
    status: str
    declared_by: str
    valid_from: str | None
    expires_on: str | None
    accepted_conditions: frozenset[str]
    path: str | None = None

    @property
    def declared(self) -> bool:
        return self.status == "declared"

    def expired_on(self, today: date | None = None) -> bool:
        if not self.expires_on:
            return False
        return (today or date.today()).isoformat() > self.expires_on

    def to_json(self) -> dict[str, Any]:
        return {
            "acknowledgement": self.label,
            "status": self.status,
            "declared_by": self.declared_by,
            "valid_from": self.valid_from,
            "expires_on": self.expires_on,
            "expired": self.expired_on(),
            "accepted_conditions": sorted(self.accepted_conditions),
            "path": self.path,
            "authority": "operator_permission_not_validation",
        }


@dataclass(frozen=True)
class PerceptionDecision:
    """Whether the perception lane may answer on this deployment."""

    available: bool
    reason: str
    detail: str
    conditions: tuple[str, ...] = ()
    unacknowledged: tuple[str, ...] = ()
    acknowledgement: PerceptionAcknowledgement | None = None
    index_evidence: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """The API shape. Identical whether the answer is yes or no."""
        return {
            "lane": "image",
            "available": self.available,
            "reason": self.reason,
            "detail": self.detail,
            # Present on an accepted answer too: an acknowledgement buys labelled
            # diagnostic output, it does not make these conditions go away.
            "conditions": [
                {"condition": name, "detail": CONDITION_DETAIL.get(name, "")}
                for name in self.conditions
            ],
            "unacknowledged_conditions": list(self.unacknowledged),
            "goal_0005_gates": [
                {
                    "gate": item["gate"],
                    "verdict": item["verdict"],
                    "blockers": list(item["blockers"]),
                }
                for item in GOAL_0005_GATES
            ],
            "gate_status_source": GATE_STATUS_SOURCE,
            "gate_status_measured_on": GATE_STATUS_MEASURED_ON,
            "paused_on": PAUSED_ON,
            "pause_reason": PAUSE_REASON,
            "acknowledgement": (
                None if self.acknowledgement is None else self.acknowledgement.to_json()
            ),
            "index": dict(self.index_evidence),
            "scope_note": LANE_SCOPE_NOTE,
            "output_quality": (
                "diagnostic_not_validated" if self.available else "not_served"
            ),
            # Goal 0027. The customer-facing half of the same answer. `detail`
            # above is written for an operator and names a condition slug; this is
            # written for the person who uploaded the picture.
            "customer_disclosure": customer_disclosure(self.available),
            "acknowledgement_env": ACK_PATH_ENV,
            "index_dir_env": INDEX_DIR_ENV,
            "authority": "operator_permission_not_validation",
            "certifies_manufacturing": False,
            "affects_geometry": False,
        }


@lru_cache(maxsize=2)
def _schema(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validate_with_schema(document: Any) -> None:
    if not isinstance(document, dict):
        raise PerceptionError("perception acknowledgement must be a JSON object")
    errors = sorted(
        Draft202012Validator(_schema(str(ACK_SCHEMA_PATH))).iter_errors(document),
        key=lambda err: list(err.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise PerceptionError(
            f"perception acknowledgement is invalid at {location}: {first.message}"
        )


def load_acknowledgement(path: Path | str) -> PerceptionAcknowledgement:
    """Load and validate an acknowledgement file. Raises rather than degrading."""
    resolved = Path(path)
    try:
        document = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PerceptionError(
            f"no perception acknowledgement at {resolved}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise PerceptionError(
            f"perception acknowledgement at {resolved} is not valid JSON: {exc}"
        ) from exc
    _validate_with_schema(document)
    validity = document.get("validity", {})
    declared_by = document.get("declared_by", {})
    return PerceptionAcknowledgement(
        label=f"{document['acknowledgement_id']}@{document['acknowledgement_version']}",
        status=str(document["status"]),
        declared_by=str(declared_by.get("name", "")),
        valid_from=validity.get("valid_from"),
        expires_on=validity.get("expires_on"),
        accepted_conditions=frozenset(document.get("accepted_conditions", ())),
        path=str(resolved),
    )


def active_acknowledgement(
    environ: Any = None,
) -> PerceptionAcknowledgement | None:
    """The acknowledgement this process was pointed at, or None."""
    env = os.environ if environ is None else environ
    path = str(env.get(ACK_PATH_ENV, "") or "").strip()
    if not path:
        return None
    return load_acknowledgement(path)


def _index_conditions(index: Any) -> tuple[list[str], dict[str, Any]]:
    """Conditions recomputed from the index this process actually loaded.

    Not read from the pinned gate table: which backend embedded the index and how
    many configurations it holds are the two things a deployment can change by
    pointing at a different directory, so they are measured here rather than
    assumed.
    """
    metadata = getattr(index, "metadata", {}) or {}
    embedding = metadata.get("embedding", {}) or {}
    records = getattr(index, "records", []) or []
    configs = len({str(record.get("config_id")) for record in records})
    is_reference = bool(embedding.get("is_reference_paradigm"))

    conditions: list[str] = []
    if not is_reference:
        conditions.append("embedding_backend_not_ratified")
    if configs < MIN_SYNTHETIC_CONFIGS:
        conditions.append("catalog_below_pilot_scale")

    evidence = {
        "vectors": len(records),
        "configs": configs,
        "min_configs_guard": MIN_SYNTHETIC_CONFIGS,
        "embedding_backend": embedding.get("backend"),
        "embedding_model_id": embedding.get("model_id"),
        "is_reference_paradigm": is_reference,
        "built_utc": metadata.get("built_utc"),
        "structure": (metadata.get("structure") or {}).get("structure"),
    }
    return conditions, evidence


def decide(
    *,
    index: Any = None,
    index_loader: Any = None,
    environ: Any = None,
    today: date | None = None,
) -> PerceptionDecision:
    """Decide whether the perception lane may answer on this deployment.

    Refusal is the safe answer and, in the state this repository ships in, the
    only one. Every refusal names the condition that failed.

    ``index`` is optional so the question can be asked without paying to load one
    (``/api/health``); when it is None the index-derived conditions are reported
    as unknown rather than as satisfied. ``index_loader`` is a zero-argument
    callable used only once permission has already been granted — so an
    unacknowledged deployment never pays to load an index, and a broken index
    directory refuses by name instead of raising through the request handler.
    """
    env = os.environ if environ is None else environ

    # -- permission, before capability ------------------------------------
    ack_path = str(env.get(ACK_PATH_ENV, "") or "").strip()
    if not ack_path:
        return PerceptionDecision(
            False,
            "lane_paused",
            f"MyJewl's perception lane was paused by the operator on {PAUSED_ON} "
            f"because {PAUSE_REASON}. No operator acknowledgement is declared on "
            "this deployment, so the lane refuses. Track C is quarantined, not "
            "removed.",
            conditions=STANDING_CONDITIONS,
            unacknowledged=STANDING_CONDITIONS,
        )
    try:
        acknowledgement = load_acknowledgement(ack_path)
    except PerceptionError as exc:
        return PerceptionDecision(
            False,
            "acknowledgement_unreadable",
            f"{ACK_PATH_ENV} is set but the acknowledgement cannot be used: {exc}. "
            "An unreadable permission is not a permission.",
            conditions=STANDING_CONDITIONS,
            unacknowledged=STANDING_CONDITIONS,
        )
    if not acknowledgement.declared:
        return PerceptionDecision(
            False,
            "acknowledgement_undeclared",
            f"The acknowledgement {acknowledgement.label} has status "
            f"{acknowledgement.status!r}. The committed template ships undeclared "
            "and refuses the lane on purpose; copy it out of the repository and "
            "declare it deliberately.",
            conditions=STANDING_CONDITIONS,
            unacknowledged=STANDING_CONDITIONS,
            acknowledgement=acknowledgement,
        )
    if acknowledgement.expired_on(today):
        return PerceptionDecision(
            False,
            "acknowledgement_expired",
            f"The acknowledgement {acknowledgement.label} expired on "
            f"{acknowledgement.expires_on}. A permission that never lapses becomes "
            "invisible; there is no fall back to a lapsed one.",
            conditions=STANDING_CONDITIONS,
            unacknowledged=STANDING_CONDITIONS,
            acknowledgement=acknowledgement,
        )

    # -- capability --------------------------------------------------------
    index_dir = str(env.get(INDEX_DIR_ENV, "") or "").strip()
    if not index_dir:
        return PerceptionDecision(
            False,
            "no_catalog_index",
            f"The lane is acknowledged but no catalog index is configured. Set "
            f"{INDEX_DIR_ENV} to an index built by "
            "scripts/build_retrieval_index.py.",
            conditions=STANDING_CONDITIONS,
            unacknowledged=tuple(
                name
                for name in STANDING_CONDITIONS
                if name not in acknowledgement.accepted_conditions
            ),
            acknowledgement=acknowledgement,
        )
    if index is None and index_loader is not None:
        try:
            index = index_loader()
        except Exception as exc:  # noqa: BLE001 - any failure here is a refusal
            return PerceptionDecision(
                False,
                "index_unreadable",
                f"{INDEX_DIR_ENV} names {index_dir}, but the index could not be "
                f"loaded: {exc.__class__.__name__}: {exc}. A stale or partial "
                "index directory refuses; it is never treated as an empty catalog.",
                conditions=STANDING_CONDITIONS,
                unacknowledged=tuple(
                    name
                    for name in STANDING_CONDITIONS
                    if name not in acknowledgement.accepted_conditions
                ),
                acknowledgement=acknowledgement,
                index_evidence={"index_dir": index_dir, "loaded": False},
            )
    if index is None:
        return PerceptionDecision(
            False,
            "index_not_loaded",
            f"{INDEX_DIR_ENV} names {index_dir}, but no index was supplied to this "
            "decision, so the conditions that depend on it are unknown. An unknown "
            "condition is refused, never assumed satisfied.",
            conditions=STANDING_CONDITIONS,
            unacknowledged=tuple(
                name
                for name in STANDING_CONDITIONS
                if name not in acknowledgement.accepted_conditions
            ),
            acknowledgement=acknowledgement,
            index_evidence={"index_dir": index_dir, "loaded": False},
        )

    index_conditions, evidence = _index_conditions(index)
    evidence["index_dir"] = index_dir
    evidence["loaded"] = True
    conditions = tuple(STANDING_CONDITIONS) + tuple(index_conditions)
    unacknowledged = tuple(
        name for name in conditions if name not in acknowledgement.accepted_conditions
    )
    if unacknowledged:
        return PerceptionDecision(
            False,
            "condition_not_acknowledged",
            "This deployment reports condition(s) the acknowledgement does not "
            f"name: {', '.join(unacknowledged)}. There is no wildcard — a "
            "condition discovered after the acknowledgement was signed has not "
            "been accepted by anyone.",
            conditions=conditions,
            unacknowledged=unacknowledged,
            acknowledgement=acknowledgement,
            index_evidence=evidence,
        )

    return PerceptionDecision(
        True,
        "acknowledged_diagnostic",
        f"{acknowledgement.declared_by} accepted all {len(conditions)} reported "
        "condition(s) by name until "
        f"{acknowledgement.expires_on}. Output is DIAGNOSTIC, not validated: every "
        "Goal 0005 gate still reads not_measurable and this acknowledgement moved "
        "none of them.",
        conditions=conditions,
        acknowledgement=acknowledgement,
        index_evidence=evidence,
    )


def lane_state(environ: Any = None, index_loader: Any = None) -> dict[str, Any]:
    """Whether this deployment serves perception at all, and why not.

    Reported on ``/api/health`` beside ``pricing`` and ``delivery``, so "is the
    image lane live here" is one request rather than an inspection.

    The caller supplies ``index_loader`` rather than this module importing the
    index itself. That keeps ``perception_lane`` free of every Track C import, so
    the switch that quarantines the lane does not itself depend on the lane — and
    a health check on an unacknowledged deployment never loads one, because
    permission is decided first.
    """
    decision = decide(environ=environ, index_loader=index_loader)
    payload = decision.to_json()
    return {
        "lane": "image",
        "track": "C",
        "available": payload["available"],
        "status": payload["reason"],
        "detail": payload["detail"],
        "paused_on": PAUSED_ON,
        "pause_reason": PAUSE_REASON,
        "goal_0005_gates": payload["goal_0005_gates"],
        "gate_status_source": GATE_STATUS_SOURCE,
        "unacknowledged_conditions": payload["unacknowledged_conditions"],
        "acknowledgement": payload["acknowledgement"],
        "index": payload["index"],
        "acknowledgement_env": ACK_PATH_ENV,
        "index_dir_env": INDEX_DIR_ENV,
        "quarantined_not_removed": True,
        "scope_note": LANE_SCOPE_NOTE,
        # Goal 0027. /studio and the landing page read the lane's live state from
        # here before a customer is asked for a photo, so the copy they see is
        # driven by the same gate /api/extract obeys rather than hard-coded.
        "customer_disclosure": payload["customer_disclosure"],
        "certifies_manufacturing": False,
        "affects_geometry": False,
    }


__all__ = [
    "ACK_PATH_ENV",
    "ACK_SCHEMA",
    "BACKEND_ENV",
    "CONDITION_DETAIL",
    "CUSTOMER_DISCLOSURE_AVAILABLE",
    "CUSTOMER_DISCLOSURE_REFUSED",
    "GATE_STATUS_SOURCE",
    "GOAL_0005_GATES",
    "INDEX_DIR_ENV",
    "LANE_SCOPE_NOTE",
    "MIN_SYNTHETIC_CONFIGS",
    "PAUSED_ON",
    "PAUSE_REASON",
    "STANDING_CONDITIONS",
    "UNDECLARED_TEMPLATE_PATH",
    "PerceptionAcknowledgement",
    "PerceptionDecision",
    "PerceptionError",
    "active_acknowledgement",
    "customer_disclosure",
    "decide",
    "lane_state",
    "load_acknowledgement",
]
