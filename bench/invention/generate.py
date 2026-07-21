#!/usr/bin/env python3
"""Deterministically freeze the Interpolant Envelope benchmark corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

FAMILIES = (
    ("units", "unit conversion and canonical quantity boundaries"),
    ("bounded_time", "bounded event-time and processing-time policies"),
    ("interval_boundaries", "inclusive and exclusive interval seams"),
    ("enums", "enumeration compatibility and unknown values"),
    ("null_absent", "null, absent, and unavailable distinctions"),
    ("namespaces", "owner-local and shared identifier namespaces"),
    ("integer_rounding", "integer quantum and rounding conventions"),
    ("delivery_acceptance", "delivery evidence and acceptance rules"),
    ("evidence_floors", "minimum evidence-grounding requirements"),
    ("revocation_windows", "revocation and expiry windows"),
    ("authority_scopes", "delegated authority scope boundaries"),
    ("nondefinable", "intentionally non-definable fixed-language seams"),
)

FAMILY_VOCABULARY = {
    "units": {
        "domain": ("meter", "foot", "kilometer"),
        "signal": "canonical_quantity",
        "rely": "unit_convertible",
        "refuse": "dimension_mismatch",
        "left": "source_quantity",
        "right": "normalized_quantity",
    },
    "bounded_time": {
        "domain": ("before", "boundary", "after"),
        "signal": "within_time_bound",
        "rely": "before_deadline",
        "refuse": "after_deadline",
        "left": "source_time_window",
        "right": "consumer_time_window",
    },
    "interval_boundaries": {
        "domain": ("lower", "interior", "upper"),
        "signal": "inside_interval",
        "rely": "included_boundary",
        "refuse": "excluded_boundary",
        "left": "producer_interval",
        "right": "consumer_interval",
    },
    "enums": {
        "domain": ("known_a", "known_b", "unknown"),
        "signal": "recognized_variant",
        "rely": "mapped_variant",
        "refuse": "forbidden_variant",
        "left": "source_enum",
        "right": "target_enum",
    },
    "null_absent": {
        "domain": ("value", "null", "absent"),
        "signal": "value_present",
        "rely": "explicit_value",
        "refuse": "required_field_absent",
        "left": "source_presence",
        "right": "consumer_presence",
    },
    "namespaces": {
        "domain": ("local", "qualified", "foreign"),
        "signal": "namespace_resolved",
        "rely": "qualified_identifier",
        "refuse": "namespace_collision",
        "left": "source_identifier",
        "right": "resolved_identifier",
    },
    "integer_rounding": {
        "domain": ("below_quantum", "exact_quantum", "above_quantum"),
        "signal": "canonical_integer_amount",
        "rely": "exactly_representable",
        "refuse": "rounding_forbidden",
        "left": "source_amount",
        "right": "rounded_amount",
    },
    "delivery_acceptance": {
        "domain": ("offered", "delivered", "accepted"),
        "signal": "acceptance_complete",
        "rely": "delivery_proven",
        "refuse": "delivery_rejected",
        "left": "carrier_delivery",
        "right": "recipient_acceptance",
    },
    "evidence_floors": {
        "domain": ("asserted", "attested", "executed"),
        "signal": "evidence_floor_met",
        "rely": "grounding_sufficient",
        "refuse": "grounding_below_floor",
        "left": "claimed_grounding",
        "right": "required_grounding",
    },
    "revocation_windows": {
        "domain": ("active", "grace", "revoked"),
        "signal": "authority_current",
        "rely": "before_revocation",
        "refuse": "revocation_effective",
        "left": "issuer_revocation_state",
        "right": "reliance_revocation_state",
    },
    "authority_scopes": {
        "domain": ("inside", "edge", "outside"),
        "signal": "within_delegated_scope",
        "rely": "authority_covers_action",
        "refuse": "authority_excludes_action",
        "left": "delegated_scope",
        "right": "requested_scope",
    },
    "nondefinable": {
        "domain": ("public_a", "public_b", "hidden_state"),
        "signal": "public_observation",
        "rely": "public_positive",
        "refuse": "public_negative",
        "left": "public_left",
        "right": "public_right",
    },
}

EXPECTED = ("COMPILED", "COMPILED", "PARTIAL", "ESCALATE", "CHOICE_REQUIRED")


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return "sha256:" + hashlib.sha256(canon(value).encode("utf-8")).hexdigest()


def var(name):
    return {"var": name}


def const(name):
    return {"const": name}


def atom(relation, *args):
    return {"op": "atom", "relation": relation, "args": list(args)}


def forall(body):
    return {"op": "forall", "var": "x", "sort": "Record", "body": body}


def iff(left, right):
    return {"op": "iff", "left": left, "right": right}


def implies(left, right):
    return {"op": "implies", "left": left, "right": right}


def negate(body):
    return {"op": "not", "body": body}


def signature(relations, domain):
    return {
        "sorts": {"Record": list(domain)},
        "relations": [
            {"name": name, "sorts": ["Record"]}
            for name in sorted(relations)
        ],
    }


def problem(
    *,
    problem_id,
    relations,
    shared,
    constraints,
    domain=("r0", "r1"),
    owners=("source",),
    overlaps=(),
):
    return {
        "schema_version": "0.1-experimental",
        "language": "FRSL-1",
        "problem_id": problem_id,
        "signature": signature(relations, domain),
        "local_theories": [
            {
                "owner": owner,
                "constraints": constraints if index == 0 else [],
            }
            for index, owner in enumerate(owners)
        ],
        "overlap_maps": list(overlaps),
        "target_predicate": "target",
        "shared_vocabulary": list(shared),
        "protected_signatures": {
            owner: list(shared) for owner in owners
        },
        "requested_judgment": "rely_refuse_escalate",
        "synthesis_policy": {
            "reference_max_ground_atoms": 12,
            "reference_max_models": 4096,
            "max_candidate_atoms": 12,
            "max_minimal_alternatives": 16,
            "exact_minimality": True,
            "require_unique_minimum": True,
        },
        "authority": {
            "principal": f"did:example:{problem_id}",
            "policy": f"policy:{problem_id}:v1",
        },
        "scope": {"family": problem_id.rsplit("-", 1)[0]},
        "expiry": "2027-07-17T00:00:00Z",
        "evidence_requirements": ["source-record", "overlap-record"],
    }


def family_instance(family, variant):
    x = var("x")
    problem_id = f"{family}-{variant}"
    vocabulary = FAMILY_VOCABULARY[family]
    domain = vocabulary["domain"]
    if variant == 0:
        signal = vocabulary["signal"]
        doc = problem(
            problem_id=problem_id,
            relations=(signal, "target"),
            shared=(signal,),
            constraints=(
                forall(
                    iff(
                        atom("target", x),
                        atom(signal, x),
                    )
                ),
            ),
            domain=domain,
        )
        kind = "direct_definition"
    elif variant == 1:
        signal = vocabulary["signal"]
        doc = problem(
            problem_id=problem_id,
            relations=(signal, "target"),
            shared=(signal,),
            constraints=(
                forall(
                    iff(
                        atom("target", x),
                        negate(atom(signal, x)),
                    )
                ),
            ),
            domain=domain,
        )
        kind = "negated_definition"
    elif variant == 2:
        positive = vocabulary["rely"]
        negative = vocabulary["refuse"]
        doc = problem(
            problem_id=problem_id,
            relations=(positive, negative, "target"),
            shared=(positive, negative),
            constraints=(
                forall(implies(atom(positive, x), atom("target", x))),
                forall(implies(atom(negative, x), negate(atom("target", x)))),
                forall(
                    negate(
                        {
                            "op": "and",
                            "args": [atom(positive, x), atom(negative, x)],
                        }
                    )
                ),
            ),
            domain=domain,
        )
        kind = "partial_envelope"
    elif variant == 3:
        signal = vocabulary["signal"]
        doc = problem(
            problem_id=problem_id,
            relations=(signal, "target"),
            shared=(signal,),
            constraints=(),
            domain=domain,
        )
        kind = "nondefinable"
    else:
        left = vocabulary["left"]
        right = vocabulary["right"]
        doc = problem(
            problem_id=problem_id,
            relations=(left, right, "target"),
            shared=(left, right),
            constraints=(
                forall(iff(atom(left, x), atom(right, x))),
                forall(iff(atom("target", x), atom(left, x))),
            ),
            domain=domain,
        )
        kind = "nonunique_minimum"
    return {
        "id": problem_id,
        "family": family,
        "variant": variant,
        "kind": kind,
        "expected_status": EXPECTED[variant],
        "problem": doc,
        "problem_hash": digest(doc),
    }


def adversarial_controls():
    x = var("x")
    vacuous = problem(
        problem_id="control-vacuity",
        relations=("public", "target"),
        shared=("public",),
        constraints=({"op": "false"},),
        domain=("r0",),
    )
    hidden = problem(
        problem_id="control-hidden-state",
        relations=("public", "secret", "target"),
        shared=("public",),
        constraints=(
            forall(iff(atom("target", x), atom("secret", x))),
        ),
        domain=("r0",),
    )
    contradictory = problem(
        problem_id="control-contradictory-locals",
        relations=("public", "target"),
        shared=("public",),
        constraints=(
            forall(atom("target", x)),
            forall(negate(atom("target", x))),
        ),
        domain=("r0",),
    )
    overlap = problem(
        problem_id="control-topology",
        relations=("left_seen", "right_seen", "target"),
        shared=("left_seen", "right_seen"),
        constraints=(),
        owners=("left", "right"),
        overlaps=(
            {
                "left_owner": "left",
                "right_owner": "right",
                "left_relation": "left_seen",
                "right_relation": "right_seen",
                "argument_map": [0],
            },
        ),
        domain=("r0",),
    )
    return [
        {
            "id": "vacuous-predicate",
            "kind": "problem",
            "expected_status": "INVALID_INPUT",
            "problem": vacuous,
        },
        {
            "id": "hidden-state-leakage",
            "kind": "problem",
            "expected_status": "ESCALATE",
            "problem": hidden,
        },
        {
            "id": "contradictory-local-definitions",
            "kind": "problem",
            "expected_status": "INVALID_INPUT",
            "problem": contradictory,
        },
        {
            "id": "topology-obstruction",
            "kind": "problem",
            "expected_status": "ESCALATE",
            "problem": overlap,
        },
        {
            "id": "query-only-conservativity",
            "kind": "package_mutation",
            "mutation": "target_leakage",
            "expected_gate": "definability",
            "expected_value": "fail",
        },
        {
            "id": "unauthorized-authority-expansion",
            "kind": "package_mutation",
            "mutation": "authority_expansion",
            "expected_gate": "receipt_binding",
            "expected_value": "fail",
        },
        {
            "id": "unstable-equivalent-hash",
            "kind": "package_mutation",
            "mutation": "noncanonical_duplicate",
            "expected_gate": "definability",
            "expected_value": "fail",
        },
        {
            "id": "repair-one-break-another",
            "kind": "package_mutation",
            "mutation": "protected_pin_swap",
            "expected_gate": "conservativity",
            "expected_value": "fail",
        },
    ]


def build_corpus():
    instances = []
    holdout_ids = []
    for family_index, (family, description) in enumerate(FAMILIES):
        holdout_variant = (family_index * 3) % 5
        for variant in range(5):
            instance = family_instance(family, variant)
            instance["family_description"] = description
            instance["split"] = "holdout" if variant == holdout_variant else "design"
            if instance["split"] == "holdout":
                holdout_ids.append(instance["id"])
            instances.append(instance)
    controls = adversarial_controls()
    payload = {
        "schema_version": "0.1-experimental",
        "language": "FRSL-1",
        "frozen_at": "2026-07-17",
        "families": [
            {"id": family, "description": description}
            for family, description in FAMILIES
        ],
        "instances": instances,
        "adversarial_controls": controls,
        "bridge_baseline": {
            "source": "papers/bridge/demos/hidden_repair",
            "frozen_unchanged": True,
            "exact": 14,
            "partial": 1,
            "misses": 0,
            "convergent_scenarios": 4,
            "total_scenarios": 5,
            "use": "candidate-generation baseline only",
        },
    }
    payload["freeze"] = {
        "instance_count": len(instances),
        "family_count": len(FAMILIES),
        "holdout_count": len(holdout_ids),
        "holdout_ids": holdout_ids,
        "design_hash": digest(
            [x for x in instances if x["split"] == "design"]
        ),
        "holdout_hash": digest(
            [x for x in instances if x["split"] == "holdout"]
        ),
        "controls_hash": digest(controls),
    }
    payload["freeze"]["payload_hash"] = digest(payload)
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("corpus.json"),
    )
    args = parser.parse_args()
    corpus = build_corpus()
    args.output.write_text(json.dumps(corpus, indent=2) + "\n", encoding="utf-8")
    print(
        f"froze {corpus['freeze']['instance_count']} instances, "
        f"{corpus['freeze']['holdout_count']} holdout, "
        f"hash={corpus['freeze']['payload_hash']}"
    )


if __name__ == "__main__":
    main()
