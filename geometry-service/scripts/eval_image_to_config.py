#!/usr/bin/env python3
"""The acceptance instrument for the image lane (Goal 0005, Stage 2 / plan §5-§6).

One command: a labelled holdout in, the full §5 metric suite plus the §6 gate
verdicts out. Built before any training, deliberately — a gate you can only
measure after fine-tuning is not a gate.

Two holdout kinds:

* **synthetic** — the ``query``-split renders from ``build_catalog_renders``.
  Ground truth is the render provenance, and the correct answer is the config the
  view was rendered from, so Recall@K is exact.
* **real** — the operator-curated mini-holdout validated by
  ``curate_real_holdout``. A real photo is not *in* the catalog, so "correct
  retrieval" is defined as a retrieved catalog config whose mounting attributes
  all match the two-source labels. That is a weaker notion than synthetic
  Recall@K and is reported under its own name.

**Verdicts are withheld, not guessed.** A gate is only stamped ``pass``/``fail``
when the run used the ratified embedding paradigm and the holdout is large
enough; otherwise the verdict is ``not_measurable`` with the reason attached. A
number produced by the declared fallback backend is diagnostic, never a gate.

**Metal is not measured here** (product contract, 2026-07-27). It is a typed
input, so there is no metal-colour accuracy metric and metal never counts toward
"correct retrieval". What *is* checked is plumbing: ``typed_metal_passthrough``
confirms the typed alloy reached the emitted config unchanged. Scored mounting
axes are exactly the internal-catalog ones — setting style, prong count, band
width ratio, band thickness ratio.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fast_mvp.catalog_index import CatalogIndex  # noqa: E402
from fast_mvp.embedding import build_embedder  # noqa: E402
from fast_mvp.image_lane import (  # noqa: E402
    STATUS_ACCEPTED,
    AbstentionPolicy,
    propose_from_image,
)

# §6 gate bars, verbatim from the plan.
GATE_BARS = {
    "G-B": {"recall_at_5": 0.85, "setting_top1": 0.90},
    "G-C": {"setting_top1": 0.70, "prong_when_visible": 0.60, "recall_at_5": 0.50},
    "G-D": {"accepted_setting_precision": 0.90, "coverage": 0.60, "ood_rejection": 0.90},
}
# Harness guards, not plan bars. They stop a verdict being stamped on a sample
# too small to generalize: the plan's pilot is ~100 configs, so retrieval quality
# measured over a handful of catalog configs says nothing about the real thing.
MIN_SYNTHETIC_CONFIGS = 20
MIN_REAL_QUERIES = 30
MIN_DISTRACTORS = 5

COVERAGE_SWEEP = tuple(round(0.05 * step, 2) for step in range(0, 20))


def _mean(values: Iterable[float]) -> float | None:
    collected = [float(value) for value in values]
    return sum(collected) / len(collected) if collected else None


def _synthetic_cases(manifest_path: Path, split: str = "query") -> list[dict[str, Any]]:
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    root = Path(manifest_path).parent
    cases: list[dict[str, Any]] = []
    for record in payload.get("records", []):
        if record.get("split") != split:
            continue
        cases.append(
            {
                "case_id": f"{record['config_id']}#{record['view_index']}",
                "image_paths": [root / str(record["image_path"])],
                "typed": {
                    "stone_shape": record["stone_shape"],
                    # Synthetic queries carry the catalog's own typed spec; the
                    # eval never lets the lane infer these.
                    "stone_length_mm": record.get("stone_length_mm"),
                    "stone_width_mm": record.get("stone_width_mm"),
                    "stone_depth_mm": record.get("stone_depth_mm"),
                    "ring_size_us": record.get("ring_size_us"),
                    # The render's alloy stands in for what the customer would
                    # have typed. It is a typed *input* to the lane, never a
                    # target: nothing below scores metal.
                    "metal_alloy": record.get("metal_alloy"),
                },
                "labels": {
                    "center_setting": record["center_setting"],
                    "prong_count": record["prong_count"],
                    "band_width_ratio": record["band_width_ratio"],
                    **(
                        {"band_thickness_ratio": record["band_thickness_ratio"]}
                        if record.get("band_thickness_ratio") is not None
                        else {}
                    ),
                },
                "truth_config_id": record["config_id"],
                "prong_count_visible": True,
                "supported": True,
            }
        )
    return cases


def _hydrate_typed_from_index(
    cases: list[dict[str, Any]],
    index: CatalogIndex,
) -> list[dict[str, Any]]:
    """Fill synthetic typed inputs from the indexed config where absent."""
    by_config: dict[str, dict[str, Any]] = {}
    for record in index.records:
        by_config.setdefault(str(record.get("config_id")), record)
    hydrated: list[dict[str, Any]] = []
    for case in cases:
        typed = dict(case["typed"])
        source = by_config.get(str(case.get("truth_config_id")), {})
        for key, fallback_key in (
            ("stone_width_mm", "stone_width_mm"),
            ("stone_length_mm", "stone_length_mm"),
            ("stone_depth_mm", "stone_depth_mm"),
            ("ring_size_us", "ring_size_us"),
            # Typed metal, standing in for the customer's pick.
            ("metal_alloy", "metal_alloy"),
        ):
            if typed.get(key) is None:
                typed[key] = source.get(fallback_key)
        hydrated.append({**case, "typed": typed})
    return hydrated


def _real_cases(holdout_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(holdout_path).read_text(encoding="utf-8"))
    root = Path(holdout_path).parent
    cases: list[dict[str, Any]] = []
    for item in payload.get("items", []):
        cases.append(
            {
                "case_id": str(item["item_id"]),
                "image_paths": [root / str(item["image_path"])],
                "typed": dict(item.get("typed") or {}),
                "labels": dict(item.get("labels") or {}),
                "truth_config_id": None,
                "prong_count_visible": bool(item.get("prong_count_visible", True)),
                "supported": bool(item.get("supported")),
                "archetype": item.get("archetype"),
            }
        )
    return cases


def _attributes_match(record: dict[str, Any], labels: dict[str, Any]) -> bool:
    """Does a retrieved catalog entry match every labelled mounting attribute?

    Metal is excluded by contract: a catalog entry rendered in white gold is the
    same mounting as one rendered in yellow, so requiring a metal match would
    penalise a correct retrieval.
    """
    for name in ("center_setting", "prong_count"):
        if name in labels and record.get(name) != labels[name]:
            return False
    return True


def run_cases(
    cases: list[dict[str, Any]],
    *,
    index: CatalogIndex,
    embedder: Any,
    policy: AbstentionPolicy,
) -> list[dict[str, Any]]:
    """Run every case through the live image lane and keep the raw outcome."""
    outcomes: list[dict[str, Any]] = []
    for case in cases:
        images: list[bytes] = []
        error: str | None = None
        for path in case["image_paths"]:
            try:
                images.append(Path(path).read_bytes())
            except OSError as exc:
                error = str(exc)
        if error or not images:
            outcomes.append({**case, "error": error or "no readable image", "report": None})
            continue
        try:
            report = propose_from_image(
                dict(case["typed"]),
                index=index,
                embedder=embedder,
                policy=policy,
                images=images,
            )
        except Exception as exc:  # noqa: BLE001 - per-case resilience
            outcomes.append({**case, "error": f"{exc.__class__.__name__}: {exc}", "report": None})
            continue
        outcomes.append({**case, "error": None, "report": report})
    return outcomes


def score(outcomes: list[dict[str, Any]], *, kind: str) -> dict[str, Any]:
    """Compute the §5 metric suite over a set of lane outcomes."""
    supported = [item for item in outcomes if item.get("supported")]
    distractors = [item for item in outcomes if not item.get("supported")]
    scored = [item for item in supported if item.get("report")]

    setting_hits: list[float] = []
    prong_hits: list[float] = []
    prong_visible_hits: list[float] = []
    ratio_errors: list[float] = []
    thickness_errors: list[float] = []
    recall1: list[float] = []
    recall5: list[float] = []
    validity: list[float] = []
    metal_passthrough: list[float] = []
    accepted_setting_hits: list[float] = []
    statuses: dict[str, int] = defaultdict(int)

    for item in scored:
        report = item["report"]
        labels = item["labels"]
        statuses[report["status"]] += 1

        top_k = report.get("top_k") or []
        if item.get("truth_config_id"):
            ids = [entry.get("config_id") for entry in top_k]
            recall1.append(1.0 if ids[:1] == [item["truth_config_id"]] else 0.0)
            recall5.append(1.0 if item["truth_config_id"] in ids[:5] else 0.0)
        elif labels:
            matches = [
                _attributes_match(
                    {**(entry.get("mounting") or {})},
                    labels,
                )
                for entry in top_k
            ]
            recall1.append(1.0 if matches[:1] == [True] else 0.0)
            recall5.append(1.0 if any(matches[:5]) else 0.0)

        attributes = report.get("attributes") or {}
        if "center_setting" in labels and "center_setting" in attributes:
            hit = 1.0 if attributes["center_setting"]["value"] == labels["center_setting"] else 0.0
            setting_hits.append(hit)
            if report["status"] == STATUS_ACCEPTED:
                accepted_setting_hits.append(hit)
        if "prong_count" in labels and "prong_count" in attributes:
            hit = 1.0 if int(attributes["prong_count"]["value"]) == int(labels["prong_count"]) else 0.0
            prong_hits.append(hit)
            if item.get("prong_count_visible", True):
                prong_visible_hits.append(hit)
        if "band_width_ratio" in labels and "band_width_ratio" in attributes:
            ratio_errors.append(
                abs(float(attributes["band_width_ratio"]["value"]) - float(labels["band_width_ratio"]))
            )
        if "band_thickness_ratio" in labels and "band_thickness_ratio" in attributes:
            thickness_errors.append(
                abs(
                    float(attributes["band_thickness_ratio"]["value"])
                    - float(labels["band_thickness_ratio"])
                )
            )

        if report["status"] == STATUS_ACCEPTED:
            gate = report.get("validation") or {}
            validity.append(
                1.0 if gate.get("schema_accepted") and gate.get("constraint_accepted") else 0.0
            )
            # Plumbing check, not an accuracy metric: the typed metal must reach
            # the emitted config untouched by anything the image lane did.
            typed_metal = (
                ((report.get("typed_inputs") or {}).get("metal") or {}).get("alloy")
            )
            emitted_metal = (report.get("flat_parameters") or {}).get("metal_alloy")
            if typed_metal is not None and emitted_metal is not None:
                metal_passthrough.append(1.0 if typed_metal == emitted_metal else 0.0)

    accepted = statuses.get(STATUS_ACCEPTED, 0)
    ood_rejected = [
        1.0 if not (item.get("report") and item["report"]["status"] == STATUS_ACCEPTED) else 0.0
        for item in distractors
        if item.get("report") or item.get("error")
    ]

    return {
        "kind": kind,
        "counts": {
            "cases": len(outcomes),
            "supported": len(supported),
            "scored": len(scored),
            "distractors": len(distractors),
            "errors": sum(1 for item in outcomes if item.get("error")),
        },
        "statuses": dict(statuses),
        # Scored axes = the internal-catalog mounting axes, and only those. Metal
        # is a typed input, so it has no accuracy metric here by contract.
        "attributes": {
            "setting_top1": _mean(setting_hits),
            "prong_exact": _mean(prong_hits),
            "prong_when_visible": _mean(prong_visible_hits),
            "band_width_ratio_mae": _mean(ratio_errors),
            "band_thickness_ratio_mae": _mean(thickness_errors),
        },
        "scored_axes": [
            "center_setting",
            "prong_count",
            "band_width_ratio",
            "band_thickness_ratio",
        ],
        "unscored_typed_inputs": [
            "stone_shape",
            "stone_dimensions_mm",
            "ring_size_us",
            "metal_color_family",
            "metal_purity",
        ],
        "retrieval": {
            "recall_at_1": _mean(recall1),
            "recall_at_5": _mean(recall5),
            "definition": (
                "exact config match within the typed shape"
                if kind == "synthetic"
                else "retrieved config whose mounting attributes match every label"
            ),
        },
        "validity": {
            "emitted_configs": len(validity),
            "gate_pass_rate": _mean(validity),
            "typed_metal_passthrough": _mean(metal_passthrough),
        },
        "abstention": {
            "coverage": (accepted / len(scored)) if scored else None,
            "accepted_setting_precision": _mean(accepted_setting_hits),
            "ood_rejection": _mean(ood_rejected),
        },
    }


def accuracy_coverage_curve(
    cases: list[dict[str, Any]],
    *,
    index: CatalogIndex,
    embedder: Any,
    policy: AbstentionPolicy,
) -> list[dict[str, Any]]:
    """Sweep the OOD threshold to trace accuracy against coverage."""
    curve: list[dict[str, Any]] = []
    for threshold in COVERAGE_SWEEP:
        swept = replace(policy, min_top1_similarity=threshold)
        outcomes = run_cases(cases, index=index, embedder=embedder, policy=swept)
        metrics = score(outcomes, kind="sweep")
        curve.append(
            {
                "min_top1_similarity": threshold,
                "coverage": metrics["abstention"]["coverage"],
                "accepted_setting_precision": metrics["abstention"]["accepted_setting_precision"],
            }
        )
    return curve


def _verdict(
    name: str,
    measured: dict[str, float | None],
    *,
    blockers: list[str],
) -> dict[str, Any]:
    bars = GATE_BARS[name]
    if blockers:
        return {"gate": name, "verdict": "not_measurable", "blockers": blockers,
                "bars": bars, "measured": measured}
    checks = {}
    for key, bar in bars.items():
        value = measured.get(key)
        checks[key] = {
            "bar": bar,
            "measured": value,
            "pass": value is not None and float(value) >= bar,
        }
    passed = all(item["pass"] for item in checks.values())
    return {"gate": name, "verdict": "pass" if passed else "fail", "checks": checks}


def evaluate(
    *,
    index_dir: Path,
    render_manifest: Path | None,
    holdout: Path | None,
    backend: str,
    policy: AbstentionPolicy,
    sweep: bool,
) -> dict[str, Any]:
    index = CatalogIndex.load(index_dir)
    embedder = build_embedder(backend)
    reference = bool(embedder.info.is_reference_paradigm)

    report: dict[str, Any] = {
        "schema_version": 1,
        "index_dir": str(index_dir),
        "index_metadata": index.metadata,
        "embedding": embedder.info.to_json(),
        "policy": policy.to_json(),
        "reference_paradigm": reference,
        "non_certifying_note": (
            "Retrieval quality is a matching measurement. It never establishes "
            "manufacturability; geometry gates stay pending until a calibrated "
            "shop profile and human jeweler review exist."
        ),
    }

    synthetic_metrics: dict[str, Any] | None = None
    real_metrics: dict[str, Any] | None = None

    if render_manifest is not None:
        cases = _hydrate_typed_from_index(_synthetic_cases(render_manifest), index)
        outcomes = run_cases(cases, index=index, embedder=embedder, policy=policy)
        synthetic_metrics = score(outcomes, kind="synthetic")
        report["synthetic"] = synthetic_metrics
        if sweep:
            report["accuracy_coverage_curve"] = accuracy_coverage_curve(
                cases, index=index, embedder=embedder, policy=policy
            )

    if holdout is not None:
        cases = _real_cases(holdout)
        outcomes = run_cases(cases, index=index, embedder=embedder, policy=policy)
        real_metrics = score(outcomes, kind="real")
        report["real"] = real_metrics

    gates: list[dict[str, Any]] = []

    gates.append(
        {
            "gate": "G-A",
            "verdict": "recorded",
            "note": (
                "Stage-0 Claude-vision baseline is recorded by "
                "scripts/run_stage0_baseline.py; it has no bar and is the number "
                "retrieval must beat."
            ),
        }
    )

    synthetic_blockers: list[str] = []
    if synthetic_metrics is None:
        synthetic_blockers.append("no synthetic query holdout supplied")
    else:
        if not reference:
            synthetic_blockers.append(
                f"embedding backend '{embedder.info.backend}' is not the ratified "
                "DINOv3 paradigm; numbers are diagnostic only"
            )
        catalog_configs = len({str(record.get("config_id")) for record in index.records})
        if catalog_configs < MIN_SYNTHETIC_CONFIGS:
            synthetic_blockers.append(
                f"catalog holds only {catalog_configs} configs (harness guard: "
                f"{MIN_SYNTHETIC_CONFIGS}); the plan's pilot is ~100"
            )
    gates.append(
        _verdict(
            "G-B",
            {
                "recall_at_5": (synthetic_metrics or {}).get("retrieval", {}).get("recall_at_5"),
                "setting_top1": (synthetic_metrics or {}).get("attributes", {}).get("setting_top1"),
            },
            blockers=synthetic_blockers,
        )
    )

    real_blockers: list[str] = []
    if real_metrics is None:
        real_blockers.append("no real mini-holdout supplied")
    else:
        if not reference:
            real_blockers.append(
                f"embedding backend '{embedder.info.backend}' is not the ratified "
                "DINOv3 paradigm; numbers are diagnostic only"
            )
        if real_metrics["counts"]["scored"] < MIN_REAL_QUERIES:
            real_blockers.append(
                f"only {real_metrics['counts']['scored']} real queries (min {MIN_REAL_QUERIES})"
            )
    gates.append(
        _verdict(
            "G-C",
            {
                "setting_top1": (real_metrics or {}).get("attributes", {}).get("setting_top1"),
                "prong_when_visible": (real_metrics or {}).get("attributes", {}).get("prong_when_visible"),
                "recall_at_5": (real_metrics or {}).get("retrieval", {}).get("recall_at_5"),
            },
            blockers=real_blockers,
        )
    )

    abstention_source = real_metrics or synthetic_metrics
    abstention_blockers: list[str] = []
    if abstention_source is None:
        abstention_blockers.append("no holdout supplied")
    else:
        if not reference:
            abstention_blockers.append(
                f"embedding backend '{embedder.info.backend}' is not the ratified "
                "DINOv3 paradigm; numbers are diagnostic only"
            )
        if abstention_source["counts"]["distractors"] < MIN_DISTRACTORS:
            abstention_blockers.append(
                f"only {abstention_source['counts']['distractors']} distractors "
                f"(min {MIN_DISTRACTORS}); OOD rejection is not measurable"
            )
    gates.append(
        _verdict(
            "G-D",
            {
                "accepted_setting_precision": (abstention_source or {}).get("abstention", {}).get(
                    "accepted_setting_precision"
                ),
                "coverage": (abstention_source or {}).get("abstention", {}).get("coverage"),
                "ood_rejection": (abstention_source or {}).get("abstention", {}).get("ood_rejection"),
            },
            blockers=abstention_blockers,
        )
    )

    verdicts = {gate["gate"]: gate["verdict"] for gate in gates}
    scale_blocked = [
        name for name in ("G-B", "G-C", "G-D") if verdicts.get(name) != "pass"
    ]
    gates.append(
        {
            "gate": "G-E",
            "verdict": "not_measurable" if scale_blocked else "pass",
            "blockers": [f"{name} is {verdicts.get(name)}" for name in scale_blocked],
            "note": (
                "Scaling to the full Tier-1 factory and the 200-500 photo program is "
                "a separate operator decision and is out of scope for Goal 0005."
            ),
        }
    )

    report["gates"] = gates
    report["status"] = "pass" if all(
        gate["verdict"] in {"pass", "recorded"} for gate in gates
    ) else "incomplete"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--render-manifest", type=Path, default=None,
                        help="Render manifest whose 'query' split is the synthetic holdout.")
    parser.add_argument("--holdout", type=Path, default=None,
                        help="Normalized real mini-holdout from curate_real_holdout.")
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-top1-similarity", type=float, default=None)
    parser.add_argument("--min-attribute-agreement", type=float, default=None)
    parser.add_argument("--min-setting-agreement", type=float, default=None)
    parser.add_argument("--sweep", action="store_true",
                        help="Also trace the accuracy-coverage curve (re-runs the holdout).")
    parser.add_argument("--report-json", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.render_manifest is None and args.holdout is None:
        parser.error("supply --render-manifest and/or --holdout")

    policy = AbstentionPolicy(top_k=args.top_k)
    overrides = {
        "min_top1_similarity": args.min_top1_similarity,
        "min_attribute_agreement": args.min_attribute_agreement,
        "min_setting_agreement": args.min_setting_agreement,
    }
    policy = replace(
        policy, **{key: value for key, value in overrides.items() if value is not None}
    )

    report = evaluate(
        index_dir=args.index_dir,
        render_manifest=args.render_manifest,
        holdout=args.holdout,
        backend=args.backend,
        policy=policy,
        sweep=args.sweep,
    )
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
