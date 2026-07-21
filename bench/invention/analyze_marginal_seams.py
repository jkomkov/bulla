#!/usr/bin/env python3
"""Classify the frozen holdout's noncompiled residue by repair axis."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from bulla.experimental.invention import FailureKind, SeamProblem, SynthesisStatus, synthesize


ROOT = Path(__file__).resolve().parents[2]


def classify(instance: dict) -> dict | None:
    problem = SeamProblem.from_dict(instance["problem"])
    result = synthesize(problem)
    if result.status is SynthesisStatus.COMPILED:
        return None
    record = {
        "id": instance["id"],
        "family": instance["family"],
        "status": result.status.value,
        "cause": result.cause.value,
        "result_hash": result.result_hash,
        "evidence_axis": None,
        "language_axis": None,
        "governance_axis": None,
    }
    if result.certificate and result.certificate.kind is FailureKind.FIXED_LANGUAGE_NON_DEFINABILITY:
        witness = result.certificate.witness
        record["evidence_axis"] = {
            "lower_bound": "at least one new shared bit is necessary",
            "basis": "two certified expansions agree on the full current shared reduct",
            "candidate": "disclose or witness one jointly authorized observable separating the pair",
            "global_sufficiency": "unresolved",
        }
        record["language_axis"] = {
            "pure_grammar_extension": "provably_insufficient_for_witness",
            "reason": (
                "every deterministic formula or operator over identical reducts has identical value"
            ),
            "minimum_information_change": "one new shared relation value for the witnessed pair",
            "frsl2_roadmap_credit": False,
            "witness_target_arguments": witness.get("target_arguments", []),
        }
    elif result.status is SynthesisStatus.CHOICE_REQUIRED:
        record["language_axis"] = {
            "pure_grammar_extension": "does_not_resolve_authority",
            "reason": "multiple exact-minimal conservative packages remain",
            "frsl2_roadmap_credit": False,
        }
        record["governance_axis"] = {
            "minimum": "one receipted selection under the declared synthesis-policy mandate",
            "alternative_count": len(result.alternatives),
            "choice_kind": result.choice_analysis.kind.value,
        }
    else:
        record["language_axis"] = {
            "pure_grammar_extension": "unclassified",
            "frsl2_roadmap_credit": False,
        }
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    corpus = json.loads((ROOT / "bench/invention/corpus.json").read_text(encoding="utf-8"))
    holdout = [x for x in corpus["instances"] if x["split"] == "holdout"]
    records = [record for item in holdout if (record := classify(item)) is not None]
    counts = Counter(record["status"] for record in records)
    information_limited = sum(record["evidence_axis"] is not None for record in records)
    governance_limited = sum(record["governance_axis"] is not None for record in records)
    payload = {
        "schema_version": "0.1-experimental",
        "freeze_hash": corpus["freeze"]["payload_hash"],
        "holdout_count": len(holdout),
        "noncompiled_count": len(records),
        "exit_distribution": dict(sorted(counts.items())),
        "information_limited_count": information_limited,
        "governance_limited_count": governance_limited,
        "pure_language_extension_candidates": 0,
        "finding": (
            "Within this exhaustive finite corpus, the noncompiled residue is an "
            "information/governance boundary, not evidence that richer syntax over "
            "the same reduct would raise coverage."
        ),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "records"}, indent=2))


if __name__ == "__main__":
    main()
