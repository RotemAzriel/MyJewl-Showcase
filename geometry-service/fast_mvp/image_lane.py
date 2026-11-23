"""Lane B runtime: photo -> retrieved mounting -> live gate (Goal 0005, plan §1-§2).

Pipeline (plan §1): crop -> embed -> retrieve **within the typed shape** ->
attribute consensus across top-K -> confidence & abstention policy -> the same
live gate, geometry queue, and review surface Lane A already uses.

**What the image lane outputs** (product contract, 2026-07-27) — and nothing else:

* the matched mounting from **MyJewl's internal validated catalog** (never a
  retailer catalog);
* the band-width ratio and the band-thickness ratio (dimensionless);
* the centre setting — prong, bezel, or basket;
* the prong count, when the retrieved views agree it is visible.

**What it never outputs:** stone geometry, ring size, or **metal**. Those are
typed inputs. Metal is a cost/appearance overlay (settled decision 6): it is
excluded from the similarity axes, from the consensus vote, and from the gate
metrics, and it reaches only the preview/quote. Two rings identical except for
metal are the same geometry and must retrieve identically.

The abstention policy (plan §2) is the load-bearing part. Thresholds are pilot
*outputs*, not inputs: ``AbstentionPolicy`` carries defaults, the eval harness
calibrates them on a holdout, and the operating point travels in every report.
An unsupported or out-of-catalog design **abstains** — it is labelled
"closest manufacturable inspiration, not a match" and never silently pinned to
the nearest catalog entry.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Sequence

import numpy as np

from fast_mvp.catalog_index import CatalogIndex, Neighbor
from fast_mvp.embedding import Embedder
from fast_mvp.image_extractor import (
    SUPPORTED_ARCHETYPES,
    VisionObservation,
    observation_to_mounting,
)
from fast_mvp.pipeline import StoneInputs, _parse_stone_inputs
from fast_mvp.preprocessing import preprocess_for_retrieval
from fast_mvp.proposer import GateResult, gate
from fast_mvp.typed_inputs import (
    MetalSpec,
    apply_certificate,
    parse_metal_spec,
    preview_metal,
    quote_basis,
    stone_spec_source,
)

STATUS_ACCEPTED = "accepted"
STATUS_NEEDS_VIEW = "needs_view"
STATUS_NEEDS_CHOICE = "needs_choice"
STATUS_ABSTAINED = "abstained"
STATUS_REJECTED = "rejected"

# Which extra view resolves which ambiguity. Product copy, not a rule value.
DISCRIMINATING_VIEW = {
    "prong_count": "a top-down photo looking straight into the stone",
    "center_setting": "a three-quarter photo showing where the metal meets the stone",
    "band_width_ratio": "a side profile of the band next to the stone",
    "band_thickness_ratio": "an edge-on photo of the band so its thickness is visible",
}


@dataclass(frozen=True)
class AbstentionPolicy:
    """Operating point for the §2 policy table.

    Defaults are deliberately conservative starting values. ``calibrate_policy``
    in ``scripts/eval_image_to_config.py`` replaces them with pilot-derived
    values; whatever is used is echoed into the report so a decision can always
    be traced to its operating point.
    """

    min_top1_similarity: float = 0.55
    min_top1_margin: float = 0.01
    min_attribute_agreement: float = 0.60
    min_setting_agreement: float = 0.70
    min_mask_fraction: float = 0.01
    max_mask_fraction: float = 0.92
    top_k: int = 5

    def to_json(self) -> dict[str, Any]:
        return {key: round(value, 5) if isinstance(value, float) else value
                for key, value in asdict(self).items()}


@dataclass(frozen=True)
class AttributeConsensus:
    name: str
    value: Any
    agreement: float
    support: int
    alternatives: tuple[tuple[Any, float], ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "agreement": round(float(self.agreement), 3),
            "support": self.support,
            "alternatives": [
                {"value": value, "agreement": round(float(weight), 3)}
                for value, weight in self.alternatives
            ],
        }


def _weighted_vote(
    name: str,
    neighbors: Sequence[Neighbor],
) -> AttributeConsensus | None:
    """Similarity-weighted vote over a categorical mounting attribute."""
    weights: dict[Any, float] = defaultdict(float)
    support: dict[Any, int] = defaultdict(int)
    for neighbor in neighbors:
        value = neighbor.record.get(name)
        if value is None:
            continue
        weight = max(0.0, float(neighbor.similarity))
        weights[value] += weight
        support[value] += 1
    if not weights:
        return None
    total = sum(weights.values())
    ranked = sorted(weights.items(), key=lambda item: item[1], reverse=True)
    winner, winner_weight = ranked[0]
    agreement = winner_weight / total if total > 1e-12 else 0.0
    alternatives = tuple(
        (value, weight / total if total > 1e-12 else 0.0) for value, weight in ranked[1:3]
    )
    return AttributeConsensus(
        name=name,
        value=winner,
        agreement=agreement,
        support=support[winner],
        alternatives=alternatives,
    )


def _weighted_ratio(
    name: str,
    neighbors: Sequence[Neighbor],
) -> AttributeConsensus | None:
    """Similarity-weighted mean of a continuous attribute, with dispersion as agreement."""
    values: list[float] = []
    weights: list[float] = []
    for neighbor in neighbors:
        raw = neighbor.record.get(name)
        if raw is None:
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
        weights.append(max(1e-6, float(neighbor.similarity)))
    if not values:
        return None
    array = np.asarray(values, dtype=np.float64)
    weight_array = np.asarray(weights, dtype=np.float64)
    mean = float(np.average(array, weights=weight_array))
    if mean <= 1e-9 or len(array) == 1:
        agreement = 1.0 if len(array) == 1 else 0.0
    else:
        spread = float(np.sqrt(np.average((array - mean) ** 2, weights=weight_array)))
        agreement = float(max(0.0, 1.0 - spread / mean))
    return AttributeConsensus(name=name, value=mean, agreement=agreement, support=len(array))


# The only axes the image lane infers: the internal-catalog mounting axes. Metal
# is absent by contract — it is a typed input and a cost/appearance overlay
# (settled decision 6), so it is neither voted nor scored here. (The pilot also
# measured a retrieved neighbour's metal label at chance, 0.25 top-1, because the
# catalog randomises metal per render shard; that randomisation is what makes
# metal a nuisance variable rather than a signal.)
RETRIEVED_CATEGORICAL_ATTRIBUTES = ("center_setting", "prong_count")
RETRIEVED_RATIO_ATTRIBUTES = ("band_width_ratio", "band_thickness_ratio")


def consensus_from_neighbors(
    neighbors: Sequence[Neighbor],
) -> dict[str, AttributeConsensus]:
    """Vote the geometry-bearing mounting attributes across the neighbours."""
    result: dict[str, AttributeConsensus] = {}
    for name in RETRIEVED_CATEGORICAL_ATTRIBUTES:
        voted = _weighted_vote(name, neighbors)
        if voted is not None:
            result[name] = voted
    for name in RETRIEVED_RATIO_ATTRIBUTES:
        ratio = _weighted_ratio(name, neighbors)
        if ratio is not None:
            result[name] = ratio
    return result


def _cross_view_consistency(per_view: Sequence[Sequence[Neighbor]]) -> float | None:
    """Fraction of views whose top-1 agrees with the modal top-1 setting."""
    tops = [
        views[0].record.get("center_setting")
        for views in per_view
        if views and views[0].record.get("center_setting") is not None
    ]
    if len(tops) < 2:
        return None
    counts: dict[Any, int] = defaultdict(int)
    for value in tops:
        counts[value] += 1
    return max(counts.values()) / len(tops)


def _merge_neighbors(
    per_view: Sequence[Sequence[Neighbor]],
    top_k: int,
) -> list[Neighbor]:
    """Fuse per-view neighbour lists by summing similarity per catalog image."""
    if len(per_view) == 1:
        return list(per_view[0])
    best: dict[int, Neighbor] = {}
    totals: dict[int, float] = defaultdict(float)
    for view in per_view:
        for neighbor in view:
            totals[neighbor.row] += float(neighbor.similarity)
            if neighbor.row not in best:
                best[neighbor.row] = neighbor
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:top_k]
    views = max(1, len(per_view))
    return [
        Neighbor(row=row, similarity=total / views, record=best[row].record)
        for row, total in ranked
    ]


def _observation_from_consensus(
    consensus: dict[str, AttributeConsensus],
    fallback_width_ratio: float,
    fallback_thickness_ratio: float,
) -> VisionObservation:
    setting = str(consensus["center_setting"].value) if "center_setting" in consensus else "prong"
    prongs = int(consensus["prong_count"].value) if "prong_count" in consensus else 0
    ratio = (
        float(consensus["band_width_ratio"].value)
        if "band_width_ratio" in consensus
        else fallback_width_ratio
    )
    thickness_ratio = (
        float(consensus["band_thickness_ratio"].value)
        if "band_thickness_ratio" in consensus
        else fallback_thickness_ratio
    )
    return VisionObservation(
        archetype="solitaire",
        center_setting=setting,
        prong_count=prongs,
        prong_count_visible=True,
        band_width_ratio=ratio,
        band_thickness_ratio=thickness_ratio,
        confidences={
            name: float(item.agreement) for name, item in consensus.items()
        },
        observations="retrieval consensus over top-K internal catalog renders",
    )


def _gate_report(result: GateResult) -> dict[str, Any]:
    return {
        "schema_accepted": result.schema_accepted,
        "constraint_accepted": result.constraint_result.accepted,
        "hard_violations": [
            {"rule_id": issue.rule_id, "message": issue.message}
            for issue in result.constraint_result.hard_violations
        ],
        "warnings": [
            {"rule_id": issue.rule_id, "tier": issue.tier, "message": issue.message}
            for issue in (
                result.constraint_result.soft_warnings
                + result.constraint_result.advisory_notes
            )
        ][:12],
        "schema_gaps": result.constraint_result.schema_gaps[:8],
    }


def _gate_candidate(
    stone: StoneInputs,
    metal: MetalSpec,
    consensus: dict[str, AttributeConsensus],
    fallback_width_ratio: float,
    fallback_thickness_ratio: float,
) -> tuple[VisionObservation, GateResult]:
    observation = _observation_from_consensus(
        consensus, fallback_width_ratio, fallback_thickness_ratio
    )
    return observation, gate(stone, observation_to_mounting(observation, stone, metal))


def _consensus_from_single(neighbor: Neighbor) -> dict[str, AttributeConsensus]:
    return consensus_from_neighbors([neighbor])


def propose_from_image(
    payload: dict[str, Any],
    *,
    index: CatalogIndex,
    embedder: Embedder,
    policy: AbstentionPolicy | None = None,
    images: Sequence[bytes] | None = None,
    archetype_hint: str | None = None,
) -> dict[str, Any]:
    """Run one image-lane query end to end and return a decision report.

    ``archetype_hint`` lets the Stage-0 vision extractor (or an operator) mark an
    image as a non-solitaire or non-ring up front; the lane then abstains rather
    than retrieving a nearest solitaire for something that is not one.
    """
    policy = policy or AbstentionPolicy()
    payload, certificate = apply_certificate(payload)
    stone = _parse_stone_inputs(payload)
    metal = parse_metal_spec(payload)
    image_list = list(images or [])
    if not image_list:
        raise ValueError("at least one image is required for the image lane")

    base: dict[str, Any] = {
        "lane": "image",
        "embedding": embedder.info.to_json(),
        "policy": policy.to_json(),
        "typed_inputs": {
            "stone_shape": stone.shape,
            "stone_length_mm": stone.length_mm,
            "stone_width_mm": stone.width_mm,
            "stone_depth_mm": stone.depth_mm,
            "ring_size_us": stone.ring_size_us,
            "stone_carat_eq": stone.carat_eq,
            "stone_spec_source": stone_spec_source(certificate),
            "certificate": certificate.to_json() if certificate else None,
            "metal": metal.to_json(),
        },
        # The contract, echoed into every report so a consumer never has to infer
        # which axes the lane is allowed to speak about.
        "inferred_axes": [
            "catalog_mounting_match",
            *RETRIEVED_CATEGORICAL_ATTRIBUTES,
            *RETRIEVED_RATIO_ATTRIBUTES,
        ],
        "typed_only_axes": [
            "stone_shape",
            "stone_dimensions_mm",
            "ring_size_us",
            "metal_color_family",
            "metal_purity",
            "metal_finish",
        ],
        "catalog_scope": "myjewl_internal_validated_catalog",
    }

    if archetype_hint and archetype_hint not in SUPPORTED_ARCHETYPES:
        return {
            **base,
            "status": STATUS_REJECTED if archetype_hint == "not_a_ring" else STATUS_ABSTAINED,
            "abstention_reason": (
                "not_a_ring" if archetype_hint == "not_a_ring" else "unsupported_archetype"
            ),
            "message": (
                "This does not look like a ring."
                if archetype_hint == "not_a_ring"
                else f"'{archetype_hint}' rings are not in the pilot catalog yet. "
                "Use the typed flow, or try a solitaire photo."
            ),
            "flat_parameters": None,
            "canonical_spec": None,
            "validation": None,
        }

    per_view: list[list[Neighbor]] = []
    crops: list[dict[str, Any]] = []
    for image_bytes in image_list:
        preprocessed = preprocess_for_retrieval(image_bytes)
        crops.append(preprocessed.to_metadata())
        vector = embedder.embed(preprocessed.png_bytes)
        per_view.append(
            index.query(vector, stone_shape=stone.shape, top_k=policy.top_k)
        )

    if not any(per_view):
        return {
            **base,
            "status": STATUS_ABSTAINED,
            "abstention_reason": "empty_catalog_for_shape",
            "message": (
                f"No catalog geometry exists for a {stone.shape} stone yet, so there "
                "is nothing to match against. Use the typed flow."
            ),
            "crops": crops,
            "flat_parameters": None,
            "canonical_spec": None,
            "validation": None,
        }

    neighbors = _merge_neighbors([view for view in per_view if view], policy.top_k)
    top1 = float(neighbors[0].similarity)
    # The margin must be measured against the best *rival config*, not the next
    # view of the same one. A catalog holds many views per config, so a naive
    # top1-vs-top2 margin collapses to ~0 exactly when retrieval is most
    # confident, and would read unanimity as ambiguity.
    top_config = neighbors[0].record.get("config_id")
    rival = next(
        (
            float(neighbor.similarity)
            for neighbor in neighbors[1:]
            if neighbor.record.get("config_id") != top_config
        ),
        None,
    )
    top2 = rival if rival is not None else 0.0
    mask_fractions = [
        crop.get("mask_fraction") for crop in crops if crop.get("mask_fraction") is not None
    ]
    signals = {
        "top1_similarity": round(top1, 5),
        "top1_margin": round(top1 - top2, 5),
        "margin_basis": "best_rival_config" if rival is not None else "no_rival_config",
        "neighbors_considered": len(neighbors),
        "distinct_configs": len({n.record.get("config_id") for n in neighbors}),
        "views": len(per_view),
        "cross_view_consistency": _cross_view_consistency(per_view),
        "mask_fraction_min": min(mask_fractions) if mask_fractions else None,
        "mask_fraction_max": max(mask_fractions) if mask_fractions else None,
    }

    consensus = consensus_from_neighbors(neighbors)
    signals["attribute_agreement"] = {
        name: round(float(item.agreement), 3) for name, item in consensus.items()
    }

    result_base = {
        **base,
        "crops": crops,
        "signals": signals,
        "top_k": [neighbor.to_json() for neighbor in neighbors],
        "attributes": {name: item.to_json() for name, item in consensus.items()},
        # Every inferred attribute comes from retrieval over the internal catalog.
        # Metal is absent on purpose: it is typed, reported under
        # ``typed_inputs.metal``, and never inferred from the photograph.
        "attribute_sources": {name: "retrieval_consensus" for name in consensus},
    }

    # (b) Out-of-distribution: a ring, but nothing in the catalog is close.
    if top1 < policy.min_top1_similarity:
        return {
            **result_base,
            "status": STATUS_ABSTAINED,
            "abstention_reason": "out_of_distribution",
            "message": (
                "Nothing in the manufacturable catalog is close enough to call this a "
                "match. The designs shown are the closest manufacturable inspiration, "
                "not a match — use the typed flow to specify the mounting."
            ),
            "flat_parameters": None,
            "canonical_spec": None,
            "validation": None,
        }

    # (a) The crop is degenerate — nothing isolated, or the whole frame selected.
    if mask_fractions:
        worst = min(mask_fractions)
        widest = max(mask_fractions)
        if worst < policy.min_mask_fraction or widest > policy.max_mask_fraction:
            return {
                **result_base,
                "status": STATUS_NEEDS_VIEW,
                "abstention_reason": "poor_crop",
                "requested_view": "a photo of the ring alone, filling most of the frame",
                "message": (
                    "The ring could not be isolated in this photo. A closer shot "
                    "against a plain background will let us read the mounting."
                ),
                "flat_parameters": None,
                "canonical_spec": None,
                "validation": None,
            }

    # (c)/(d) Ambiguity: setting style split is a choice, other splits ask for a view.
    setting = consensus.get("center_setting")
    if setting is not None and setting.agreement < policy.min_setting_agreement:
        return {
            **result_base,
            "status": STATUS_NEEDS_CHOICE,
            "abstention_reason": "ambiguous_setting_style",
            "choices": [
                {"center_setting": setting.value, "agreement": round(setting.agreement, 3)},
                *[
                    {"center_setting": value, "agreement": round(weight, 3)}
                    for value, weight in setting.alternatives[:1]
                ],
            ],
            "message": (
                "This photo reads as more than one setting style. Pick the one that "
                "matches your ring — your choice overrides what we inferred."
            ),
            "flat_parameters": None,
            "canonical_spec": None,
            "validation": None,
        }

    split = [
        name
        for name, item in consensus.items()
        if name != "center_setting" and item.agreement < policy.min_attribute_agreement
    ]
    if split or (top1 - top2) < policy.min_top1_margin:
        contested = split[0] if split else "prong_count"
        return {
            **result_base,
            "status": STATUS_NEEDS_VIEW,
            "abstention_reason": f"split_attribute:{contested}",
            "requested_view": DISCRIMINATING_VIEW.get(contested, "another angle of the ring"),
            "confirmable_attribute": contested,
            "message": (
                f"We could not settle on the {contested.replace('_', ' ')} from this "
                "angle. Send the suggested view, or confirm it yourself — typed input "
                "always wins."
            ),
            "flat_parameters": None,
            "canonical_spec": None,
            "validation": None,
        }

    # (6) The live gate is authoritative. A rejected consensus falls back to the
    # next retrieved candidate — never to invented off-catalog values.
    fallback_width_ratio = 2.0 / stone.width_mm
    fallback_thickness_ratio = 1.6 / stone.width_mm
    observation, gate_result = _gate_candidate(
        stone, metal, consensus, fallback_width_ratio, fallback_thickness_ratio
    )
    gate_source = "consensus"
    if not gate_result.accepted:
        for neighbor in neighbors:
            observation, gate_result = _gate_candidate(
                stone,
                metal,
                _consensus_from_single(neighbor),
                fallback_width_ratio,
                fallback_thickness_ratio,
            )
            if gate_result.accepted:
                gate_source = f"catalog_candidate:{neighbor.record.get('config_id')}"
                break

    if not gate_result.accepted:
        return {
            **result_base,
            "status": STATUS_REJECTED,
            "abstention_reason": "constraints_rejected_all_candidates",
            "message": (
                "Every close catalog mounting is rejected by the constraint engine for "
                "this stone. Nothing was emitted."
            ),
            "observation": observation.to_json(),
            "flat_parameters": None,
            "canonical_spec": None,
            "validation": _gate_report(gate_result),
        }

    return {
        **result_base,
        "status": STATUS_ACCEPTED,
        "abstention_reason": None,
        "gate_source": gate_source,
        "observation": observation.to_json(),
        "flat_parameters": gate_result.flat,
        "canonical_spec": gate_result.canonical,
        "validation": _gate_report(gate_result),
        # Typed metal lands here — appearance for the 3D preview, and the basis of
        # the quote. It never touched retrieval.
        "preview": _preview(stone, metal, gate_result.flat),
        # Goal 0023: no volume from the request. It is measured from the promoted
        # asset's export bytes and resolved at the persistence boundary.
        "quote": quote_basis(
            metal,
            metal_price_per_gram=_optional_float(payload.get("metal_price_per_gram")),
        ),
    }


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _preview(
    stone: StoneInputs,
    metal: MetalSpec,
    flat: dict[str, Any],
) -> dict[str, Any]:
    """What the 3D preview renders: typed stone + typed metal + matched mounting."""
    return {
        "stone_shape": stone.shape,
        "stone_dimensions_mm": {
            "length": round(stone.length_mm, 2),
            "width": round(stone.width_mm, 2),
            "depth": round(stone.depth_mm, 2),
        },
        "carat": stone.carat_eq,
        "ring_size_us": stone.ring_size_us,
        "metal": preview_metal(metal),
        # Legacy key the existing 3D viewport reads.
        "metal_family": metal.color_family,
        "metal_purity": metal.purity,
        "metal_finish": metal.finish,
        "center_setting": flat.get("center_setting"),
        "prong_count": flat.get("prong_count"),
        "band_width_mm": flat.get("band_width_mm"),
        "band_thickness_mm": flat.get("band_thickness_mm"),
    }


def summarize(report: dict[str, Any]) -> str:
    """One-line human summary for CLI output."""
    status = report.get("status")
    if status == STATUS_ACCEPTED:
        flat = report.get("flat_parameters") or {}
        return (
            f"accepted: {flat.get('center_setting')}{flat.get('prong_count')} "
            f"band {flat.get('band_width_mm')}×{flat.get('band_thickness_mm')}mm "
            f"(top1={report['signals']['top1_similarity']}, "
            f"typed metal {flat.get('metal_alloy')})"
        )
    return f"{status}: {report.get('abstention_reason')} — {report.get('message')}"


__all__ = [
    "AbstentionPolicy",
    "AttributeConsensus",
    "RETRIEVED_CATEGORICAL_ATTRIBUTES",
    "RETRIEVED_RATIO_ATTRIBUTES",
    "consensus_from_neighbors",
    "propose_from_image",
    "summarize",
    "STATUS_ACCEPTED",
    "STATUS_ABSTAINED",
    "STATUS_NEEDS_CHOICE",
    "STATUS_NEEDS_VIEW",
    "STATUS_REJECTED",
]
