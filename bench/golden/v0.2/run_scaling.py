#!/usr/bin/env python3
"""Run the frozen 240-case checked-compression scaling suite."""

from __future__ import annotations

import argparse
import itertools
import json
import random
import resource
import time
from collections import Counter
from pathlib import Path

from bulla.experimental.frsl import atom, formula_size, variable
from bulla.experimental.golden_v02 import ComplexityFingerprint
from bulla.experimental.invention import _bounded_disjunction, _safe_generalized_dnf
from bulla.experimental.frsl import canonical_hash


HERE = Path(__file__).resolve().parent
MAX_NODES = 4096


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def formula_mask(formula: dict, features: tuple[dict, ...], vectors: tuple[tuple[bool, ...], ...]) -> int:
    """Independently evaluate an emitted Boolean FRSL formula as a bitset."""
    universe = (1 << len(vectors)) - 1
    feature_masks = {}
    for feature_index, feature in enumerate(features):
        mask = 0
        for vector_index, vector in enumerate(vectors):
            if vector[feature_index]:
                mask |= 1 << vector_index
        feature_masks[json.dumps(feature, sort_keys=True, separators=(",", ":"))] = mask

    def visit(node: dict) -> int:
        key = json.dumps(node, sort_keys=True, separators=(",", ":"))
        if key in feature_masks:
            return feature_masks[key]
        op = node["op"]
        if op == "true": return universe
        if op == "false": return 0
        if op == "not": return universe ^ visit(node["body"])
        if op == "and":
            result = universe
            for child in node["args"]: result &= visit(child)
            return result
        if op == "or":
            result = 0
            for child in node["args"]: result |= visit(child)
            return result
        raise ValueError(f"non-Boolean formula node {op}")

    return visit(formula)


def truth_label(family: str, vector: tuple[bool, ...], seed: int) -> bool | None:
    width = len(vector)
    if family == "literal":
        return vector[seed % width]
    if family == "sparse_dnf":
        a, b, c, d = ((seed + offset) % width for offset in range(4))
        return (vector[a] and vector[b]) or (vector[c] and not vector[d])
    if family == "threshold_majority":
        threshold = width // 2 + (seed % 2)
        return sum(vector) >= threshold
    if family == "parity":
        return (sum(vector) + seed) % 2 == 1
    if family == "random_balanced":
        ordinal = sum((1 << index) for index, value in enumerate(vector) if value)
        rng = random.Random((seed + 1) * 1_000_003 + width * 97 + ordinal)
        return bool(rng.getrandbits(1))
    if family == "partial_ambiguous":
        if width > 1 and vector[-1] == vector[-2]:
            return None
        return vector[seed % width]
    raise ValueError(f"unknown function family {family}")


def run_case(family: str, width: int, seed: int) -> dict:
    case_id = f"scale-{family}-w{width:02d}-s{seed}"
    features = tuple(atom(f"x{index}", (variable("x0"),)) for index in range(width))
    vectors = tuple(itertools.product((False, True), repeat=width))
    positives = {vector for vector in vectors if truth_label(family, vector, seed) is True}
    negatives = {vector for vector in vectors if truth_label(family, vector, seed) is False}
    residual = set(vectors) - positives - negatives
    started = time.perf_counter_ns()
    positive_formula, positive_terms = _safe_generalized_dnf(features, positives, negatives)
    negative_formula, negative_terms = _safe_generalized_dnf(features, negatives, positives)
    positive_size = formula_size(positive_formula)
    negative_size = formula_size(negative_formula)
    complete = not residual
    if complete and positive_size <= MAX_NODES:
        emitted_positive = positive_formula
        emitted_negative = negative_formula
        status = "COMPILED"
    else:
        emitted_positive = _bounded_disjunction(positive_terms, MAX_NODES)
        emitted_negative = _bounded_disjunction(negative_terms, MAX_NODES)
        status = "PARTIAL"
    elapsed_ns = time.perf_counter_ns() - started
    vector_index = {vector: index for index, vector in enumerate(vectors)}
    positive_mask = sum(1 << vector_index[vector] for vector in positives)
    negative_mask = sum(1 << vector_index[vector] for vector in negatives)
    emitted_positive_mask = formula_mask(emitted_positive, features, vectors)
    emitted_negative_mask = formula_mask(emitted_negative, features, vectors)
    positive_false_accepts = emitted_positive_mask & negative_mask
    negative_false_accepts = emitted_negative_mask & positive_mask
    covered_positive = (emitted_positive_mask & positive_mask).bit_count()
    covered_negative = (emitted_negative_mask & negative_mask).bit_count()
    safe = positive_false_accepts == 0 and negative_false_accepts == 0
    emitted_nodes = formula_size(emitted_positive)
    minterm_upper_bound = 1 + len(positives) * (1 + 2 * width)
    reduction = 1.0 - emitted_nodes / max(1, minterm_upper_bound)
    peak_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if peak_memory < 10_000_000:  # Linux reports KiB; macOS reports bytes.
        peak_memory *= 1024
    fingerprint = ComplexityFingerprint(
        case_id=case_id,
        hypothesis_count=len(vectors),
        opposing_pair_count=len(positives) * len(negatives),
        candidate_observable_count=width,
        vocabulary_width=width,
        authority_branching=1,
        proof_nodes=emitted_nodes + formula_size(emitted_negative),
        peak_memory_bytes=peak_memory,
        elapsed_ns=elapsed_ns,
        best_certified_partial_state={
            "positive_covered": covered_positive,
            "positive_total": len(positives),
            "negative_covered": covered_negative,
            "negative_total": len(negatives),
            "residual_total": len(residual),
        },
    )
    semantic_record = {
        "family": family,
        "width": width,
        "seed": seed,
        "status": status,
        "safe": safe,
        "positive_formula_hash": canonical_hash(emitted_positive),
        "negative_formula_hash": canonical_hash(emitted_negative),
        "positive_nodes": emitted_nodes,
        "negative_nodes": formula_size(emitted_negative),
        "minterm_positive_node_upper_bound": minterm_upper_bound,
        "ast_reduction": reduction,
        "partition": "holdout" if seed == 4 else "design",
        "fingerprint": fingerprint.to_dict(),
    }
    return {
        "case_id": case_id,
        "case_hash": canonical_hash({
            "family": family,
            "width": width,
            "seed": seed,
            "positive_vectors": [list(v) for v in sorted(positives)],
            "negative_vectors": [list(v) for v in sorted(negatives)],
        }),
        **semantic_record,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "scaling-report.json")
    args = parser.parse_args()
    families = (
        "literal",
        "sparse_dnf",
        "threshold_majority",
        "parity",
        "random_balanced",
        "partial_ambiguous",
    )
    cases = [
        run_case(family, width, seed)
        for family in families
        for width in range(5, 13)
        for seed in range(5)
    ]
    holdout = [case for case in cases if case["partition"] == "holdout"]
    structured = [
        case for case in cases
        if case["family"] in {"sparse_dnf", "threshold_majority", "parity", "random_balanced"}
        and case["minterm_positive_node_upper_bound"] > MAX_NODES
    ]
    converted = [case for case in structured if case["status"] in {"COMPILED", "PARTIAL"}]
    planted_small = [case for case in cases if case["family"] in {"literal", "sparse_dnf"}]
    reductions = sorted(case["ast_reduction"] for case in planted_small)
    median_reduction = reductions[len(reductions) // 2]
    report = {
        "schema_version": "0.2-scaling",
        "profile": "bulla.golden-suite/0.2-experimental",
        "case_count": len(cases),
        "design_count": len(cases) - len(holdout),
        "holdout_count": len(holdout),
        "by_exit": dict(sorted(Counter(case["status"] for case in cases).items())),
        "unsafe_count": sum(not case["safe"] for case in cases),
        "ast_bound_structured_count": len(structured),
        "ast_bound_converted_count": len(converted),
        "ast_bound_conversion_share": len(converted) / max(1, len(structured)),
        "median_ast_reduction_planted_at_most_32": median_reduction,
        "gates": {
            "zero_unsafe": all(case["safe"] for case in cases),
            "structured_conversion_at_least_30_percent": len(converted) / max(1, len(structured)) >= 0.30,
            "median_reduction_over_80_percent": median_reduction > 0.80,
        },
        "claim_rule": "If gains do not extend beyond literal pathologies, report pathology repair only.",
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)
    print(json.dumps({key: report[key] for key in ("case_count", "by_exit", "unsafe_count", "ast_bound_conversion_share")}, sort_keys=True))
    return 0 if report["gates"]["zero_unsafe"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
