#!/usr/bin/env python3
"""Zero-Bulla-import verifier for experimental FRSL-1 invention results.

This file intentionally imports only the Python standard library.  It
duplicates the small finite semantic checker so a shipped result can be replayed
without trusting the package that emitted it.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
from pathlib import Path

LANGUAGE = "FRSL-1"
SCHEMA_VERSION = "0.1-experimental"
RESULT_SCHEMA_VERSION = "0.2-experimental"


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return "sha256:" + hashlib.sha256(canon(value).encode("utf-8")).hexdigest()


def require_closed(value, required, optional, where):
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    missing = set(required) - set(value)
    unknown = set(value) - set(required) - set(optional)
    if missing:
        raise ValueError(f"{where} missing keys {sorted(missing)}")
    if unknown:
        raise ValueError(f"{where} unknown keys {sorted(unknown)}")
    return value


def parse_problem(value):
    keys = {
        "schema_version",
        "language",
        "problem_id",
        "signature",
        "local_theories",
        "overlap_maps",
        "target_predicate",
        "shared_vocabulary",
        "protected_signatures",
        "requested_judgment",
        "synthesis_policy",
        "authority",
        "scope",
        "expiry",
        "evidence_requirements",
    }
    problem = require_closed(value, keys, set(), "seam_problem")
    if problem["schema_version"] != SCHEMA_VERSION or problem["language"] != LANGUAGE:
        raise ValueError("unsupported problem schema or language")
    if not isinstance(problem["problem_id"], str) or not problem["problem_id"]:
        raise ValueError("problem_id must be non-empty")
    signature = problem["signature"]
    require_closed(signature, {"sorts", "relations"}, set(), "signature")
    if not isinstance(signature["sorts"], dict) or not signature["sorts"]:
        raise ValueError("signature.sorts must be a non-empty object")
    sorts = {}
    for name, elements in signature["sorts"].items():
        if not isinstance(name, str) or not name:
            raise ValueError("sort names must be non-empty strings")
        if (
            not isinstance(elements, list)
            or not elements
            or any(not isinstance(x, str) or not x for x in elements)
            or len(elements) != len(set(elements))
        ):
            raise ValueError(f"signature.sorts.{name} must be unique named elements")
        sorts[name] = tuple(elements)
    if not isinstance(signature["relations"], list):
        raise ValueError("signature.relations must be a list")
    relations = {}
    for relation in signature["relations"]:
        require_closed(relation, {"name", "sorts"}, set(), "relation")
        if not isinstance(relation["name"], str) or not relation["name"]:
            raise ValueError("relation.name must be non-empty")
        if relation["name"] in relations:
            raise ValueError("signature has duplicate relation names")
        if (
            not isinstance(relation["sorts"], list)
            or len(relation["sorts"]) not in (1, 2)
        ):
            raise ValueError("FRSL-1 relations must be unary or binary")
        if any(sort not in sorts for sort in relation["sorts"]):
            raise ValueError("relation references an unknown sort")
        relations[relation["name"]] = tuple(relation["sorts"])
    if problem["target_predicate"] not in relations:
        raise ValueError("unknown target predicate")
    if (
        not isinstance(problem["shared_vocabulary"], list)
        or len(problem["shared_vocabulary"])
        != len(set(problem["shared_vocabulary"]))
        or any(name not in relations for name in problem["shared_vocabulary"])
        or problem["target_predicate"] in problem["shared_vocabulary"]
    ):
        raise ValueError("shared_vocabulary must be unique, declared, and exclude target")
    if not isinstance(problem["local_theories"], list) or not problem["local_theories"]:
        raise ValueError("local_theories must be a non-empty list")
    owners = []
    for index, theory in enumerate(problem["local_theories"]):
        require_closed(theory, {"owner", "constraints"}, set(), "local_theory")
        if not isinstance(theory["owner"], str) or not theory["owner"]:
            raise ValueError("local_theory.owner must be non-empty")
        if not isinstance(theory["constraints"], list):
            raise ValueError("local_theory.constraints must be a list")
        owners.append(theory["owner"])
        for constraint_index, constraint in enumerate(theory["constraints"]):
            validate_formula(
                constraint,
                sorts,
                relations,
                where=(
                    f"local_theories[{index}].constraints[{constraint_index}]"
                ),
            )
    if len(owners) != len(set(owners)):
        raise ValueError("local_theory owners must be unique")
    if not isinstance(problem["protected_signatures"], dict):
        raise ValueError("protected_signatures must be an object")
    if set(problem["protected_signatures"]) - set(owners):
        raise ValueError("protected_signatures has an unknown owner")
    for owner, names in problem["protected_signatures"].items():
        if (
            not isinstance(names, list)
            or len(names) != len(set(names))
            or any(name not in relations for name in names)
            or problem["target_predicate"] in names
        ):
            raise ValueError(f"protected_signatures.{owner} is invalid")
    for owner in owners:
        problem["protected_signatures"].setdefault(owner, [])
    if not isinstance(problem["overlap_maps"], list):
        raise ValueError("overlap_maps must be a list")
    for overlap in problem["overlap_maps"]:
        require_closed(
            overlap,
            {
                "left_owner",
                "right_owner",
                "left_relation",
                "right_relation",
                "argument_map",
            },
            set(),
            "overlap_map",
        )
        if overlap["left_owner"] not in owners or overlap["right_owner"] not in owners:
            raise ValueError("overlap_map references an unknown owner")
        if (
            overlap["left_relation"] not in relations
            or overlap["right_relation"] not in relations
        ):
            raise ValueError("overlap_map references an unknown relation")
        mapping = overlap["argument_map"]
        left_sorts = relations[overlap["left_relation"]]
        right_sorts = relations[overlap["right_relation"]]
        if (
            not isinstance(mapping, list)
            or any(not isinstance(x, int) or isinstance(x, bool) for x in mapping)
            or len(mapping) != len(left_sorts)
            or sorted(mapping) != list(range(len(right_sorts)))
            or any(left_sorts[i] != right_sorts[j] for i, j in enumerate(mapping))
        ):
            raise ValueError("overlap_map.argument_map is not a sort-preserving permutation")
    if problem["requested_judgment"] not in ("boolean", "rely_refuse_escalate"):
        raise ValueError("unsupported requested_judgment")
    policy = require_closed(
        problem["synthesis_policy"],
        set(),
        {
            "reference_max_ground_atoms",
            "reference_max_models",
            "max_candidate_atoms",
            "max_minimal_alternatives",
            "exact_minimality",
            "require_unique_minimum",
        },
        "synthesis_policy",
    )
    defaults = {
        "reference_max_ground_atoms": 16,
        "reference_max_models": 65536,
        "max_candidate_atoms": 10,
        "max_minimal_alternatives": 16,
        "exact_minimality": True,
        "require_unique_minimum": True,
    }
    for name, default in defaults.items():
        policy.setdefault(name, default)
    for name in (
        "reference_max_ground_atoms",
        "reference_max_models",
        "max_candidate_atoms",
        "max_minimal_alternatives",
    ):
        if (
            not isinstance(policy[name], int)
            or isinstance(policy[name], bool)
            or policy[name] <= 0
        ):
            raise ValueError(f"synthesis_policy.{name} must be a positive integer")
    if policy["max_minimal_alternatives"] < 2:
        raise ValueError("max_minimal_alternatives must be at least 2")
    if not isinstance(policy["exact_minimality"], bool) or not isinstance(
        policy["require_unique_minimum"], bool
    ):
        raise ValueError("synthesis policy flags must be boolean")
    if not policy["require_unique_minimum"]:
        raise ValueError(
            "require_unique_minimum must be true; non-unique minima require authority"
        )
    if not isinstance(problem["authority"], dict) or not isinstance(problem["scope"], dict):
        raise ValueError("authority and scope must be objects")
    if (
        not isinstance(problem["evidence_requirements"], list)
        or any(not isinstance(x, str) or not x for x in problem["evidence_requirements"])
    ):
        raise ValueError("evidence_requirements must contain non-empty strings")
    return problem, sorts, relations


def parse_result(value):
    result = require_closed(
        value,
        {
            "schema_version",
            "status",
            "cause",
            "problem_hash",
            "gate_report",
            "package",
            "certificate",
            "alternatives",
            "backend",
            "verifier",
            "next_actions",
            "choice_analysis",
            "enrichment_plans",
        },
        set(),
        "synthesis_result",
    )
    if result["schema_version"] != RESULT_SCHEMA_VERSION:
        raise ValueError("unsupported synthesis result schema")
    if result["status"] not in {
        "COMPILED",
        "PARTIAL",
        "ESCALATE",
        "CHOICE_REQUIRED",
        "INDETERMINATE",
        "INVALID_INPUT",
    }:
        raise ValueError("unknown synthesis result status")
    expected_causes = {
        "COMPILED": {"total_definition"},
        "PARTIAL": {"partial_definition"},
        "ESCALATE": {
            "topology_obstruction",
            "fixed_language_non_definability",
            "non_conservativity",
        },
        "CHOICE_REQUIRED": {"non_unique_minimum"},
        "INDETERMINATE": {"resource_limit"},
        "INVALID_INPUT": {"invalid_problem"},
    }
    if result["cause"] not in expected_causes[result["status"]]:
        raise ValueError("synthesis result cause is incompatible with its status")
    if not isinstance(result["next_actions"], list) or not result["next_actions"]:
        raise ValueError("synthesis_result.next_actions must be a non-empty list")
    next_action_kinds = {
        "apply",
        "supply_evidence",
        "repair_overlap",
        "select_with_authority",
        "extend_resource_budget",
        "correct_input",
    }
    for action in result["next_actions"]:
        require_closed(
            action,
            {"kind", "statement", "artifact_refs"},
            set(),
            "next_action",
        )
        if action["kind"] not in next_action_kinds:
            raise ValueError("next_action.kind is unknown")
        if not isinstance(action["statement"], str) or not action["statement"]:
            raise ValueError("next_action.statement must be non-empty")
        if not isinstance(action["artifact_refs"], list) or any(
            not isinstance(x, str) or not x for x in action["artifact_refs"]
        ):
            raise ValueError("next_action.artifact_refs must contain strings")
    if not isinstance(result["enrichment_plans"], list):
        raise ValueError("synthesis_result.enrichment_plans must be a list")
    enrichment_axes = {
        "evidence", "language", "policy", "authority", "recourse",
        "topology", "resource",
    }
    for plan in result["enrichment_plans"]:
        require_closed(
            plan,
            {"axis", "statement", "requirements", "cost", "minimality"},
            set(),
            "enrichment_plan",
        )
        if plan["axis"] not in enrichment_axes:
            raise ValueError("enrichment_plan.axis is unknown")
        if not isinstance(plan["statement"], str) or not plan["statement"]:
            raise ValueError("enrichment_plan.statement must be non-empty")
        if not isinstance(plan["requirements"], list) or any(
            not isinstance(x, dict) for x in plan["requirements"]
        ):
            raise ValueError("enrichment_plan.requirements must be objects")
        if not isinstance(plan["cost"], dict) or any(
            not isinstance(k, str)
            or not isinstance(v, int)
            or isinstance(v, bool)
            or v < 0
            for k, v in plan["cost"].items()
        ):
            raise ValueError("enrichment_plan.cost is invalid")
        if plan["minimality"] not in (
            "exact-declared-candidate-space", "unresolved"
        ):
            raise ValueError("enrichment_plan.minimality is unknown")
    require_closed(
        result["gate_report"],
        {
            "gluing",
            "conservativity",
            "definability",
            "preserved_refusals",
            "minimality",
            "receipt_binding",
            "reasons",
        },
        set(),
        "gate_report",
    )
    gate_values = {"pass", "fail", "unresolved", "not_applicable"}
    for name in (
        "gluing",
        "conservativity",
        "definability",
        "preserved_refusals",
        "minimality",
        "receipt_binding",
    ):
        if result["gate_report"][name] not in gate_values:
            raise ValueError(f"gate_report.{name} is invalid")
    if not isinstance(result["gate_report"]["reasons"], list):
        raise ValueError("gate_report.reasons must be a list")
    if result["package"] is not None:
        parse_package(result["package"])
    if not isinstance(result["alternatives"], list):
        raise ValueError("synthesis_result.alternatives must be a list")
    for package in result["alternatives"]:
        parse_package(package)
    certificate = result["certificate"]
    if certificate is not None:
        require_closed(
            certificate,
            {"kind", "statement", "witness", "backend", "complete_within_bound"},
            set(),
            "failure_certificate",
        )
        if certificate["kind"] not in {
            "topology_obstruction",
            "fixed_language_non_definability",
            "non_conservativity",
            "non_unique_minimum",
            "resource_limit",
            "invalid_problem",
        }:
            raise ValueError("unknown failure certificate kind")
        if not isinstance(certificate["statement"], str) or not certificate["statement"]:
            raise ValueError("failure_certificate.statement must be non-empty")
        if not isinstance(certificate["witness"], dict):
            raise ValueError("failure_certificate.witness must be an object")
        if not isinstance(certificate["complete_within_bound"], bool):
            raise ValueError("failure_certificate.complete_within_bound must be boolean")
    if result["status"] in ("COMPILED", "PARTIAL") and result["package"] is None:
        raise ValueError(f"{result['status']} requires a package")
    if result["package"] is not None and result["package"]["problem_hash"] != result["problem_hash"]:
        raise ValueError("result package does not bind result.problem_hash")
    if any(
        package["problem_hash"] != result["problem_hash"]
        for package in result["alternatives"]
    ):
        raise ValueError("result alternative does not bind result.problem_hash")
    if result["status"] == "COMPILED" and (
        certificate is not None or result["alternatives"]
    ):
        raise ValueError("COMPILED cannot carry a failure or choice exit")
    if result["status"] == "PARTIAL" and certificate is None:
        raise ValueError("PARTIAL requires a residual certificate")
    if result["status"] == "PARTIAL" and (
        certificate["kind"] != "fixed_language_non_definability"
        or result["alternatives"]
    ):
        raise ValueError("PARTIAL requires only a fixed-language residual certificate")
    if result["status"] == "CHOICE_REQUIRED" and (
        certificate is None
        or certificate["kind"] != "non_unique_minimum"
        or len(result["alternatives"]) < 2
    ):
        raise ValueError("CHOICE_REQUIRED requires two alternatives and its certificate")
    if result["status"] == "CHOICE_REQUIRED" and result["package"] is not None:
        raise ValueError("CHOICE_REQUIRED cannot silently select a package")
    choice = result["choice_analysis"]
    if result["status"] == "CHOICE_REQUIRED":
        if not isinstance(choice, dict):
            raise ValueError("CHOICE_REQUIRED requires choice_analysis")
        require_closed(
            choice,
            {
                "kind", "classes", "cost_order", "selector_authority",
                "disagreement_witness",
            },
            set(),
            "choice_analysis",
        )
        if choice["kind"] not in {"economic", "normative"}:
            raise ValueError("choice_analysis.kind is unknown")
        if not isinstance(choice["classes"], list) or len(choice["classes"]) < 2:
            raise ValueError("choice_analysis requires at least two classes")
        if not isinstance(choice["cost_order"], list) or not choice["cost_order"]:
            raise ValueError("choice_analysis.cost_order must be non-empty")
        if not isinstance(choice["selector_authority"], dict) or not choice["selector_authority"]:
            raise ValueError("choice_analysis.selector_authority must be declared")
        if not isinstance(choice["disagreement_witness"], dict):
            raise ValueError("choice_analysis.disagreement_witness must be an object")
        classified = []
        class_ids = []
        for item in choice["classes"]:
            require_closed(
                item,
                {
                    "class_id", "package_hashes", "protected_behavior_hash",
                    "cost_vector",
                },
                set(),
                "choice_class",
            )
            if not isinstance(item["package_hashes"], list) or not item["package_hashes"]:
                raise ValueError("choice_class.package_hashes must be non-empty")
            if not isinstance(item["cost_vector"], dict):
                raise ValueError("choice_class.cost_vector must be an object")
            class_ids.append(item["class_id"])
            classified.extend(item["package_hashes"])
        if len(class_ids) != len(set(class_ids)) or len(classified) != len(set(classified)):
            raise ValueError("choice classes must be disjoint with unique ids")
        if set(classified) != {digest(package) for package in result["alternatives"]}:
            raise ValueError("choice classes must partition all alternatives")
    elif choice is not None:
        raise ValueError("choice_analysis is only valid for CHOICE_REQUIRED")
    if result["status"] in ("ESCALATE", "INDETERMINATE", "INVALID_INPUT") and certificate is None:
        raise ValueError(f"{result['status']} requires a certificate")
    if result["status"] == "ESCALATE" and (
        result["package"] is not None
        or result["alternatives"]
        or certificate["kind"]
        not in {
            "topology_obstruction",
            "fixed_language_non_definability",
            "non_conservativity",
        }
    ):
        raise ValueError("ESCALATE has an incompatible exit artifact")
    if result["status"] == "INDETERMINATE" and (
        result["package"] is not None
        or result["alternatives"]
        or certificate["kind"] != "resource_limit"
    ):
        raise ValueError("INDETERMINATE requires only a resource limit")
    if result["status"] == "INVALID_INPUT" and (
        result["package"] is not None
        or result["alternatives"]
        or certificate["kind"] != "invalid_problem"
    ):
        raise ValueError("INVALID_INPUT requires only an invalid-problem exit")
    return result


def parse_package(value):
    package = require_closed(
        value,
        {
            "schema_version",
            "language",
            "problem_hash",
            "mode",
            "definition",
            "rely_when",
            "refuse_when",
            "local_definitions",
            "bridge_constraints",
            "evidence_requirements",
            "protected_signature_pins",
            "verifier",
            "authority",
            "scope",
            "expiry",
            "cost",
            "proof_references",
        },
        set(),
        "predicate_package",
    )
    if package["schema_version"] != SCHEMA_VERSION or package["language"] != LANGUAGE:
        raise ValueError("unsupported predicate package schema or language")
    if package["mode"] not in ("full", "partial"):
        raise ValueError("predicate_package.mode must be full or partial")
    if package["mode"] == "full" and package["definition"] is None:
        raise ValueError("full package requires definition")
    if package["mode"] == "partial" and (
        package["rely_when"] is None or package["refuse_when"] is None
    ):
        raise ValueError("partial package requires rely_when and refuse_when")
    return package


def validate_term(term, sorts, expected_sort, bound, where):
    if not isinstance(term, dict) or set(term) not in ({"const"}, {"var"}):
        raise ValueError(f"{where} must contain exactly one of const or var")
    if "var" in term:
        name = term["var"]
        if not isinstance(name, str) or name not in bound:
            raise ValueError(f"{where} references an unbound variable")
        if bound[name] != expected_sort:
            raise ValueError(f"{where} uses a variable of the wrong sort")
    else:
        name = term["const"]
        if not isinstance(name, str) or name not in sorts[expected_sort]:
            raise ValueError(f"{where} constant is outside sort {expected_sort}")


def validate_formula(formula, sorts, relations, free_variables=None, where="formula"):
    """Strictly validate the closed FRSL-1 AST without importing Bulla."""
    bound = dict(free_variables or {})

    def visit(node, env, path):
        if not isinstance(node, dict):
            raise ValueError(f"{path} must be an object")
        op = node.get("op")
        if op in ("true", "false"):
            require_closed(node, {"op"}, set(), path)
            return
        if op == "atom":
            require_closed(node, {"op", "relation", "args"}, set(), path)
            relation = node["relation"]
            if relation not in relations:
                raise ValueError(f"{path} references an unknown relation")
            if not isinstance(node["args"], list) or len(node["args"]) != len(
                relations[relation]
            ):
                raise ValueError(f"{path}.args has the wrong arity")
            for index, (term, sort) in enumerate(
                zip(node["args"], relations[relation])
            ):
                validate_term(term, sorts, sort, env, f"{path}.args[{index}]")
            return
        if op == "eq":
            require_closed(node, {"op", "sort", "left", "right"}, set(), path)
            sort = node["sort"]
            if sort not in sorts:
                raise ValueError(f"{path} references an unknown sort")
            validate_term(node["left"], sorts, sort, env, f"{path}.left")
            validate_term(node["right"], sorts, sort, env, f"{path}.right")
            return
        if op == "not":
            require_closed(node, {"op", "body"}, set(), path)
            visit(node["body"], env, f"{path}.body")
            return
        if op in ("and", "or"):
            require_closed(node, {"op", "args"}, set(), path)
            if not isinstance(node["args"], list):
                raise ValueError(f"{path}.args must be a list")
            for index, child in enumerate(node["args"]):
                visit(child, env, f"{path}.args[{index}]")
            return
        if op in ("implies", "iff"):
            require_closed(node, {"op", "left", "right"}, set(), path)
            visit(node["left"], env, f"{path}.left")
            visit(node["right"], env, f"{path}.right")
            return
        if op in ("forall", "exists"):
            require_closed(node, {"op", "var", "sort", "body"}, set(), path)
            if not isinstance(node["var"], str) or not node["var"]:
                raise ValueError(f"{path}.var must be non-empty")
            if node["sort"] not in sorts:
                raise ValueError(f"{path} references an unknown sort")
            nested = dict(env)
            nested[node["var"]] = node["sort"]
            visit(node["body"], nested, f"{path}.body")
            return
        raise ValueError(f"{path}.op is not in FRSL-1")

    visit(formula, bound, where)
    return formula


def term_value(term, env):
    if set(term) == {"const"}:
        return term["const"]
    if set(term) == {"var"}:
        return env[term["var"]]
    raise ValueError("term must contain exactly one of const or var")


def normalize_structure(value, sorts, relations):
    if not isinstance(value, dict):
        raise ValueError("structure must be an object")
    unknown = set(value) - set(relations)
    if unknown:
        raise ValueError(f"structure has unknown relations {sorted(unknown)}")
    result = {name: set() for name in relations}
    for name, tuples in value.items():
        if not isinstance(tuples, list):
            raise ValueError(f"structure relation {name} must be a list")
        for raw in tuples:
            if not isinstance(raw, list) or len(raw) != len(relations[name]):
                raise ValueError(f"structure relation {name} tuple has wrong arity")
            item = tuple(raw)
            if any(
                element not in sorts[sort]
                for element, sort in zip(item, relations[name])
            ):
                raise ValueError(f"structure relation {name} contains an out-of-sort value")
            result[name].add(item)
    return result


def evaluate(formula, structure, sorts, env=None):
    env = dict(env or {})
    op = formula.get("op")
    if op == "true":
        return True
    if op == "false":
        return False
    if op == "atom":
        args = tuple(term_value(x, env) for x in formula["args"])
        return args in structure[formula["relation"]]
    if op == "eq":
        return term_value(formula["left"], env) == term_value(formula["right"], env)
    if op == "not":
        return not evaluate(formula["body"], structure, sorts, env)
    if op == "and":
        return all(evaluate(x, structure, sorts, env) for x in formula["args"])
    if op == "or":
        return any(evaluate(x, structure, sorts, env) for x in formula["args"])
    if op == "implies":
        return (not evaluate(formula["left"], structure, sorts, env)) or evaluate(
            formula["right"], structure, sorts, env
        )
    if op == "iff":
        return evaluate(formula["left"], structure, sorts, env) == evaluate(
            formula["right"], structure, sorts, env
        )
    if op in ("forall", "exists"):
        values = []
        for element in sorts[formula["sort"]]:
            nested = dict(env)
            nested[formula["var"]] = element
            values.append(evaluate(formula["body"], structure, sorts, nested))
        return all(values) if op == "forall" else any(values)
    raise ValueError(f"unsupported FRSL-1 operator {op!r}")


def formula_relations(formula):
    op = formula["op"]
    if op == "atom":
        return {formula["relation"]}
    if op in ("true", "false", "eq"):
        return set()
    if op == "not":
        return formula_relations(formula["body"])
    if op in ("and", "or"):
        result = set()
        for child in formula["args"]:
            result.update(formula_relations(child))
        return result
    if op in ("implies", "iff"):
        return formula_relations(formula["left"]) | formula_relations(formula["right"])
    if op in ("forall", "exists"):
        return formula_relations(formula["body"])
    raise ValueError(f"unsupported FRSL-1 operator {op!r}")


def normalize_formula(formula):
    op = formula["op"]
    if op in ("true", "false", "atom", "eq"):
        return formula
    if op == "not":
        body = normalize_formula(formula["body"])
        if body["op"] == "not":
            return normalize_formula(body["body"])
        if body["op"] == "true":
            return {"op": "false"}
        if body["op"] == "false":
            return {"op": "true"}
        return {"op": "not", "body": body}
    if op in ("and", "or"):
        children = []
        for child in formula["args"]:
            child = normalize_formula(child)
            if child["op"] == op:
                children.extend(child["args"])
            else:
                children.append(child)
        identity = "true" if op == "and" else "false"
        annihilator = "false" if op == "and" else "true"
        if any(child["op"] == annihilator for child in children):
            return {"op": annihilator}
        children = [child for child in children if child["op"] != identity]
        unique = {canon(child): child for child in children}
        ordered = [unique[key] for key in sorted(unique)]
        if not ordered:
            return {"op": identity}
        if len(ordered) == 1:
            return ordered[0]
        return {"op": op, "args": ordered}
    if op == "implies":
        return {
            "op": "implies",
            "left": normalize_formula(formula["left"]),
            "right": normalize_formula(formula["right"]),
        }
    if op == "iff":
        parts = [
            normalize_formula(formula["left"]),
            normalize_formula(formula["right"]),
        ]
        parts.sort(key=canon)
        return {"op": "iff", "left": parts[0], "right": parts[1]}
    if op in ("forall", "exists"):
        return {
            "op": op,
            "var": formula["var"],
            "sort": formula["sort"],
            "body": normalize_formula(formula["body"]),
        }
    raise ValueError(f"unsupported FRSL-1 operator {op!r}")


def formula_size(formula):
    op = formula["op"]
    if op in ("true", "false", "atom", "eq"):
        return 1
    if op == "not":
        return 1 + formula_size(formula["body"])
    if op in ("and", "or"):
        return 1 + sum(formula_size(x) for x in formula["args"])
    if op in ("implies", "iff"):
        return 1 + formula_size(formula["left"]) + formula_size(formula["right"])
    if op in ("forall", "exists"):
        return 1 + formula_size(formula["body"])
    raise ValueError(f"unsupported FRSL-1 operator {op!r}")


def choice_cost_vector(package):
    return {
        "predicate_ast_nodes": int(package["cost"].get("predicate_ast_nodes", 0)),
        "evidence_items": len(package["evidence_requirements"]),
        "disclosure_relations": len(formula_relations(package["definition"])),
        "authority_changes": 0,
    }


def ground_atoms(sorts, relations):
    atoms = []
    for name in sorted(relations):
        domains = [sorts[sort] for sort in relations[name]]
        for args in itertools.product(*domains):
            atoms.append((name, tuple(args)))
    return atoms


def admissible_models(problem, sorts, relations):
    atoms = ground_atoms(sorts, relations)
    policy = problem["synthesis_policy"]
    if len(atoms) > policy["reference_max_ground_atoms"]:
        raise ValueError("reference ground-atom bound exceeded")
    count = 1 << len(atoms)
    if count > policy["reference_max_models"]:
        raise ValueError("reference model bound exceeded")
    constraints = [
        constraint
        for theory in problem["local_theories"]
        for constraint in theory["constraints"]
    ]
    models = []
    for bits in range(count):
        structure = {name: set() for name in relations}
        for index, (name, args) in enumerate(atoms):
            if bits & (1 << index):
                structure[name].add(args)
        if all(evaluate(x, structure, sorts) for x in constraints):
            models.append(structure)
    return models


def shared_structures(problem, sorts, relations):
    names = set(problem["shared_vocabulary"])
    atoms = [atom_ for atom_ in ground_atoms(sorts, relations) if atom_[0] in names]
    policy = problem["synthesis_policy"]
    if len(atoms) > policy["reference_max_ground_atoms"]:
        raise ValueError("reference shared ground-atom bound exceeded")
    count = 1 << len(atoms)
    if count > policy["reference_max_models"]:
        raise ValueError("reference shared model bound exceeded")
    for bits in range(count):
        structure = {name: set() for name in relations}
        for index, (name, args) in enumerate(atoms):
            if bits & (1 << index):
                structure[name].add(args)
        yield structure


def choice_analysis_valid(problem, result, sorts, relations):
    if result["status"] != "CHOICE_REQUIRED":
        return result["choice_analysis"] is None
    choice = result["choice_analysis"]
    target_sorts = relations[problem["target_predicate"]]
    target_domains = [sorts[sort] for sort in target_sorts]
    groups = {}
    behavior_hashes = set()
    metadata = {}
    for package in result["alternatives"]:
        behavior = []
        for structure in shared_structures(problem, sorts, relations):
            for args in itertools.product(*target_domains):
                env = {f"x{i}": value for i, value in enumerate(args)}
                behavior.append(evaluate(package["definition"], structure, sorts, env))
        behavior_hash = digest(behavior)
        cost = choice_cost_vector(package)
        key = (behavior_hash, digest(cost))
        groups.setdefault(key, []).append(package)
        metadata[key] = (behavior_hash, cost)
        behavior_hashes.add(behavior_hash)
    expected_classes = []
    for key in sorted(groups):
        behavior_hash, cost = metadata[key]
        package_hashes = sorted(digest(package) for package in groups[key])
        expected_classes.append(
            {
                "class_id": digest(
                    {
                        "protected_behavior_hash": behavior_hash,
                        "cost_vector": cost,
                    }
                ),
                "package_hashes": package_hashes,
                "protected_behavior_hash": behavior_hash,
                "cost_vector": cost,
            }
        )
    expected_authority = problem["authority"] or {
        "status": "undeclared",
        "problem_hash": digest(problem),
    }
    return bool(
        len(expected_classes) >= 2
        and choice["classes"] == expected_classes
        and choice["kind"] == ("normative" if len(behavior_hashes) > 1 else "economic")
        and choice["cost_order"] == [
            "predicate_ast_nodes",
            "evidence_items",
            "disclosure_relations",
            "authority_changes",
        ]
        and choice["selector_authority"] == expected_authority
    )


def gluing_ok(problem, models, sorts, relations):
    for overlap in problem["overlap_maps"]:
        right_domains = [sorts[x] for x in relations[overlap["right_relation"]]]
        for structure in models:
            for right_args in itertools.product(*right_domains):
                left_args = tuple(right_args[i] for i in overlap["argument_map"])
                if (left_args in structure[overlap["left_relation"]]) != (
                    tuple(right_args) in structure[overlap["right_relation"]]
                ):
                    return False
    return True


def protected_pins(problem, relations):
    pins = {}
    for owner, names in problem["protected_signatures"].items():
        payload = {
            "language": LANGUAGE,
            "owner": owner,
            "relations": [
                {"name": name, "sorts": list(relations[name])}
                for name in sorted(names)
            ],
        }
        pins[owner] = digest(payload)
    return pins


def feature_atoms(problem, sorts, relations):
    target_sorts = relations[problem["target_predicate"]]
    variables = [(f"x{i}", sort) for i, sort in enumerate(target_sorts)]
    features = {}
    for name, sort in variables:
        for element in sorts[sort]:
            formula = {
                "op": "eq",
                "sort": sort,
                "left": {"var": name},
                "right": {"const": element},
            }
            features[canon(formula)] = formula
    for relation_name in sorted(problem["shared_vocabulary"]):
        choices = []
        for sort in relations[relation_name]:
            terms = [{"const": x} for x in sorts[sort]]
            terms.extend(
                {"var": name}
                for name, variable_sort in variables
                if variable_sort == sort
            )
            choices.append(terms)
        for terms in itertools.product(*choices):
            formula = {
                "op": "atom",
                "relation": relation_name,
                "args": list(terms),
            }
            features[canon(formula)] = formula
    return [features[key] for key in sorted(features)]


def feature_vector(features, structure, sorts, args):
    env = {f"x{i}": value for i, value in enumerate(args)}
    return tuple(evaluate(x, structure, sorts, env) for x in features)


def cube_matches(cube, vector):
    return all(want is None or want == got for want, got in zip(cube, vector))


def cube_term_size(cube):
    literals = [value for value in cube if value is not None]
    if not literals:
        return 1
    size = sum(1 if value else 2 for value in literals)
    return size if len(literals) == 1 else size + 1


def minimal_dnf_size(feature_count, positives, excluded):
    if not positives or not excluded:
        return 1
    positive_list = sorted(positives)
    cubes = []
    for cube in itertools.product((None, False, True), repeat=feature_count):
        coverage = 0
        for index, point in enumerate(positive_list):
            if cube_matches(cube, point):
                coverage |= 1 << index
        if coverage and not any(cube_matches(cube, point) for point in excluded):
            cubes.append((coverage, cube_term_size(cube)))
    full = (1 << len(positive_list)) - 1
    by_point = {index: [] for index in range(len(positive_list))}
    for cube_index, (coverage, _) in enumerate(cubes):
        for point_index in by_point:
            if coverage & (1 << point_index):
                by_point[point_index].append(cube_index)
    best = None
    visited = set()

    def search(mask, chosen):
        nonlocal best
        chosen = tuple(sorted(chosen))
        state = (mask, chosen)
        if state in visited:
            return
        visited.add(state)
        if mask == full:
            size = sum(cubes[index][1] for index in chosen)
            if len(chosen) > 1:
                size += 1
            if best is None or size < best:
                best = size
            return
        partial = sum(cubes[index][1] for index in chosen)
        if len(chosen) > 1:
            partial += 1
        if best is not None and partial >= best:
            return
        first = next(
            index
            for index in range(len(positive_list))
            if not (mask & (1 << index))
        )
        for cube_index in by_point[first]:
            if cube_index in chosen:
                continue
            search(mask | cubes[cube_index][0], chosen + (cube_index,))

    search(0, ())
    if best is None:
        raise ValueError("no exact DNF cover")
    return best


def package_gates(problem, package, sorts, relations, models):
    reasons = []
    gluing = "pass" if gluing_ok(problem, models, sorts, relations) else "fail"
    pins_match = package["protected_signature_pins"] == protected_pins(problem, relations)
    if not pins_match:
        reasons.append("protected signature pins do not match the problem")
    binding = package["problem_hash"] == digest(problem)
    if not binding:
        reasons.append("package problem_hash does not bind the supplied problem")
    expected_metadata = (
        (package["authority"], problem["authority"], "authority"),
        (package["scope"], problem["scope"], "scope"),
        (package["expiry"], problem["expiry"], "expiry"),
        (
            package["evidence_requirements"],
            problem["evidence_requirements"],
            "evidence requirements",
        ),
        (
            package["bridge_constraints"],
            problem["overlap_maps"],
            "bridge constraints",
        ),
    )
    for actual, expected, label in expected_metadata:
        if actual != expected:
            binding = False
            reasons.append(f"package {label} differ from the problem")
    if set(package["local_definitions"]) != {
        theory["owner"] for theory in problem["local_theories"]
    }:
        binding = False
        reasons.append("package local definitions do not cover declared owners")
    expected_local = (
        package["definition"]
        if package["mode"] == "full"
        else {
            "op": "envelope",
            "rely_when": package["rely_when"],
            "refuse_when": package["refuse_when"],
            "otherwise": "ESCALATE",
        }
    )
    if any(
        package["local_definitions"].get(theory["owner"]) != expected_local
        for theory in problem["local_theories"]
    ):
        binding = False
        reasons.append("package local definitions differ from the global surface")
    verifier = package["verifier"]
    if not (
        (
            verifier.get("id") == "bulla.experimental.invention.reference"
            and verifier.get("version") == "0.1-experimental"
        )
        or verifier.get("checker") == "bulla.experimental.invention.reference"
    ):
        binding = False
        reasons.append("package names no supported independent reference checker")
    formulas = [
        formula
        for formula in (
            package["definition"],
            package["rely_when"],
            package["refuse_when"],
        )
        if formula is not None
    ]
    formula_valid = True
    for formula in formulas:
        try:
            validate_formula(
                formula,
                sorts,
                relations,
                free_variables={
                    f"x{i}": sort
                    for i, sort in enumerate(relations[problem["target_predicate"]])
                },
                where="predicate_package.formula",
            )
            leaked = formula_relations(formula) - set(problem["shared_vocabulary"])
            if leaked:
                reasons.append(
                    f"package formula leaks non-shared relations {sorted(leaked)}"
                )
                pins_match = False
                formula_valid = False
            if formula != normalize_formula(formula):
                reasons.append("package formula is not in canonical FRSL-1 form")
                pins_match = False
                formula_valid = False
        except (KeyError, TypeError, ValueError) as exc:
            reasons.append(str(exc))
            pins_match = False
            formula_valid = False
    expected_cost_keys = {
        "admissible_models",
        "candidate_atoms",
        "predicate_ast_nodes",
        "minimality",
    }
    if set(package["cost"]) != expected_cost_keys:
        binding = False
        reasons.append("package cost has missing or unknown fields")
    features = feature_atoms(problem, sorts, relations)
    feature_bound_ok = (
        len(features) <= problem["synthesis_policy"]["max_candidate_atoms"]
    )
    if not feature_bound_ok:
        binding = False
        reasons.append("candidate feature count exceeds the declared reference bound")
    actual_nodes = sum(formula_size(formula) for formula in formulas)
    if package["cost"].get("admissible_models") != len(models):
        binding = False
        reasons.append("package admissible-model cost is not reproducible")
    if package["cost"].get("candidate_atoms") != len(features):
        binding = False
        reasons.append("package candidate-atom cost is not reproducible")
    if package["cost"].get("predicate_ast_nodes") != actual_nodes:
        binding = False
        reasons.append("package predicate-size cost is not reproducible")
    definability = "pass" if formula_valid else "fail"
    preserved = "pass" if formula_valid else "fail"
    target = problem["target_predicate"]
    target_sorts = relations[target]
    domains = [sorts[x] for x in target_sorts]
    for structure in models:
        for args in itertools.product(*domains):
            env = {f"x{i}": value for i, value in enumerate(args)}
            expected = tuple(args) in structure[target]
            if package["mode"] == "full":
                got = evaluate(package["definition"], structure, sorts, env)
                if got != expected:
                    definability = "fail"
                    if got and not expected:
                        preserved = "fail"
            else:
                rely = evaluate(package["rely_when"], structure, sorts, env)
                refuse = evaluate(package["refuse_when"], structure, sorts, env)
                if rely and refuse:
                    reasons.append(f"RELY and REFUSE overlap at target tuple {args}")
                    definability = "fail"
                if rely and not expected:
                    preserved = "fail"
                    definability = "fail"
                if refuse and expected:
                    definability = "fail"
    if package["mode"] == "partial":
        definability = "fail"
        reasons.append("partial envelope does not explicitly define the residual")
    labels = {}
    target_domains = [sorts[x] for x in relations[target]]
    for structure in models:
        for args in itertools.product(*target_domains):
            vector = feature_vector(features, structure, sorts, tuple(args))
            labels.setdefault(vector, set()).add(tuple(args) in structure[target])
    stable_true = {v for v, values in labels.items() if values == {True}}
    stable_false = {v for v, values in labels.items() if values == {False}}
    ambiguous = {v for v, values in labels.items() if len(values) > 1}
    minimality = "unresolved"
    if (
        package["cost"].get("minimality") == "exact-finite-candidate-space"
        and feature_bound_ok
    ):
        if package["mode"] == "full" and not ambiguous:
            expected_size = minimal_dnf_size(
                len(features), stable_true, stable_false
            )
            minimality = (
                "pass"
                if formula_size(package["definition"]) == expected_size
                else "fail"
            )
        elif package["mode"] == "partial" and ambiguous:
            expected_size = minimal_dnf_size(
                len(features), stable_true, stable_false | ambiguous
            ) + minimal_dnf_size(
                len(features), stable_false, stable_true | ambiguous
            )
            minimality = "pass" if actual_nodes == expected_size else "fail"
        else:
            minimality = "fail"
        if minimality == "fail":
            reasons.append("package exact-minimality claim did not replay")
    elif package["cost"].get("minimality") == "exact-finite-candidate-space":
        minimality = "unresolved"
    elif package["cost"].get("minimality") != "unresolved":
        binding = False
        reasons.append("package minimality status is unknown")
    return {
        "gluing": gluing,
        "conservativity": "pass" if pins_match else "fail",
        "definability": definability,
        "preserved_refusals": preserved,
        "minimality": minimality,
        "receipt_binding": "pass" if binding else "fail",
        "reasons": list(dict.fromkeys(reasons)),
    }


def cert_valid(problem, certificate, sorts, relations, alternatives=()):
    if certificate is None:
        return None
    kind = certificate["kind"]
    witness = certificate["witness"]
    if kind == "resource_limit":
        return False
    if kind == "fixed_language_non_definability":
        try:
            require_closed(
                witness,
                {
                    "target_arguments",
                    "shared_vocabulary",
                    "shared_reduct",
                    "expansion_true",
                    "expansion_false",
                    "feature_vector",
                },
                set(),
                "fixed_language_non_definability.witness",
            )
            if witness["shared_vocabulary"] != problem["shared_vocabulary"]:
                return False
            args = tuple(witness["target_arguments"])
            true_model = normalize_structure(
                witness["expansion_true"], sorts, relations
            )
            false_model = normalize_structure(
                witness["expansion_false"], sorts, relations
            )
            target_sorts = relations[problem["target_predicate"]]
            if len(args) != len(target_sorts):
                return False
            if any(value not in sorts[sort] for value, sort in zip(args, target_sorts)):
                return False
        except (KeyError, TypeError, ValueError):
            return False
        constraints = [
            constraint
            for theory in problem["local_theories"]
            for constraint in theory["constraints"]
        ]
        if not all(evaluate(x, true_model, sorts) for x in constraints):
            return False
        if not all(evaluate(x, false_model, sorts) for x in constraints):
            return False
        for name in problem["shared_vocabulary"]:
            if true_model[name] != false_model[name]:
                return False
        shared_reduct = {
            name: [list(args) for args in sorted(true_model[name])]
            for name in sorted(problem["shared_vocabulary"])
        }
        if witness["shared_reduct"] != shared_reduct:
            return False
        features = feature_atoms(problem, sorts, relations)
        true_vector = list(feature_vector(features, true_model, sorts, args))
        false_vector = list(feature_vector(features, false_model, sorts, args))
        if witness["feature_vector"] != true_vector or true_vector != false_vector:
            return False
        return (
            args in true_model[problem["target_predicate"]]
            and args not in false_model[problem["target_predicate"]]
        )
    if kind == "topology_obstruction":
        try:
            require_closed(
                witness,
                {"overlap_map", "right_arguments", "structure"},
                set(),
                "topology_obstruction.witness",
            )
            overlap = witness["overlap_map"]
            if overlap not in problem["overlap_maps"]:
                return False
            structure = normalize_structure(witness["structure"], sorts, relations)
            args = tuple(witness["right_arguments"])
            right_sorts = relations[overlap["right_relation"]]
            if len(args) != len(right_sorts) or any(
                value not in sorts[sort] for value, sort in zip(args, right_sorts)
            ):
                return False
            left_args = tuple(args[i] for i in overlap["argument_map"])
            constraints = [
                constraint
                for theory in problem["local_theories"]
                for constraint in theory["constraints"]
            ]
            if not all(evaluate(x, structure, sorts) for x in constraints):
                return False
            return (left_args in structure[overlap["left_relation"]]) != (
                args in structure[overlap["right_relation"]]
            )
        except (KeyError, TypeError, IndexError, ValueError):
            return False
    if kind == "non_unique_minimum":
        try:
            require_closed(
                witness,
                {
                    "alternative_hashes",
                    "disagreement_pair",
                    "disagreement",
                    "candidate_space",
                },
                set(),
                "non_unique_minimum.witness",
            )
            require_closed(
                witness["disagreement"],
                {
                    "target_arguments",
                    "shared_structure",
                    "first_value",
                    "second_value",
                },
                set(),
                "non_unique_minimum.disagreement",
            )
        except (KeyError, TypeError, ValueError):
            return False
        hashes = witness.get("alternative_hashes")
        pair = witness.get("disagreement_pair")
        disagreement = witness.get("disagreement")
        if not (
            isinstance(hashes, list)
            and len(hashes) >= 2
            and len(hashes) == len(set(hashes))
            and isinstance(pair, list)
            and len(pair) == 2
            and set(pair).issubset(set(hashes))
            and isinstance(disagreement, dict)
            and isinstance(alternatives, list)
            and witness.get("candidate_space")
            == "FRSL-1 DNF cubes over declared feature atoms"
        ):
            return False
        by_hash = {digest(package): package for package in alternatives}
        if set(by_hash) != set(hashes):
            return False
        first = by_hash.get(pair[0])
        second = by_hash.get(pair[1])
        if first is None or second is None:
            return False
        try:
            structure = normalize_structure(
                disagreement["shared_structure"], sorts, relations
            )
            args = tuple(disagreement["target_arguments"])
            target_sorts = relations[problem["target_predicate"]]
            if len(args) != len(target_sorts) or any(
                value not in sorts[sort] for value, sort in zip(args, target_sorts)
            ):
                return False
            env = {f"x{i}": value for i, value in enumerate(args)}
            first_value = evaluate(first["definition"], structure, sorts, env)
            second_value = evaluate(second["definition"], structure, sorts, env)
        except (KeyError, TypeError, ValueError):
            return False
        if (
            first_value == second_value
            or first_value != disagreement.get("first_value")
            or second_value != disagreement.get("second_value")
        ):
            return False
        models = admissible_models(problem, sorts, relations)
        for package in alternatives:
            gates = package_gates(problem, package, sorts, relations, models)
            if (
                package["mode"] != "full"
                or package["cost"].get("minimality")
                != "exact-finite-candidate-space"
                or gates["gluing"] != "pass"
                or gates["definability"] != "pass"
                or gates["conservativity"] != "pass"
                or gates["preserved_refusals"] != "pass"
                or gates["minimality"] != "pass"
                or gates["receipt_binding"] != "pass"
            ):
                return False
        return True
    return False


def verify(problem_doc, result_doc):
    problem, sorts, relations = parse_problem(problem_doc)
    result = parse_result(result_doc)
    binding = result["problem_hash"] == digest(problem)
    models = admissible_models(problem, sorts, relations)
    gates = (
        package_gates(problem, result["package"], sorts, relations, models)
        if result["package"] is not None
        else None
    )
    certificate_valid = cert_valid(
        problem,
        result["certificate"],
        sorts,
        relations,
        result["alternatives"],
    )
    choice_valid = choice_analysis_valid(problem, result, sorts, relations)
    status = result["status"]
    minimality_ok = bool(
        gates
        and result["package"]
        and (
            (
                result["package"]["cost"].get("minimality")
                == "exact-finite-candidate-space"
                and gates["minimality"] == "pass"
            )
            or (
                result["package"]["cost"].get("minimality") == "unresolved"
                and gates["minimality"] == "unresolved"
            )
        )
    )
    if status == "COMPILED":
        ok = bool(
            binding
            and gates
            and gates["gluing"] == "pass"
            and gates["conservativity"] == "pass"
            and gates["definability"] == "pass"
            and gates["preserved_refusals"] == "pass"
            and gates["receipt_binding"] == "pass"
            and minimality_ok
        )
    elif status == "PARTIAL":
        ok = bool(
            binding
            and gates
            and gates["gluing"] == "pass"
            and gates["conservativity"] == "pass"
            and gates["preserved_refusals"] == "pass"
            and gates["receipt_binding"] == "pass"
            and minimality_ok
            and certificate_valid
        )
    elif status in ("ESCALATE", "CHOICE_REQUIRED"):
        ok = bool(binding and certificate_valid and choice_valid)
    else:
        ok = False
    return {
        "ok": ok,
        "status": status,
        "problem_binding": binding,
        "package_gates": gates,
        "certificate_valid": certificate_valid,
        "choice_analysis_valid": choice_valid,
        "result_hash": digest(result),
    }


# ── Certified observability and refinement replay ───────────────────────────

BURDEN_FIELDS = (
    "disclosure_units",
    "latency_ms",
    "monetary_microunits",
    "new_authorities",
    "institutional_dependencies",
    "lifecycle_burden",
)


def _ordered_tuples(structure, name, sorts, relations):
    domains = [sorts[sort] for sort in relations[name]]
    return [
        list(arguments)
        for arguments in itertools.product(*domains)
        if tuple(arguments) in structure[name]
    ]


def structure_document(structure, sorts, relations):
    return {
        name: _ordered_tuples(structure, name, sorts, relations)
        for name in sorted(structure)
    }


def reduct_document(structure, names, sorts, relations):
    return {
        name: _ordered_tuples(structure, name, sorts, relations)
        for name in sorted(set(names))
    }


def authority_epoch_document(authority):
    if not isinstance(authority, dict) or not authority:
        raise ValueError("authority epoch requires a non-empty authority object")
    return digest(
        {"kind": "bulla.semantic-authority-epoch/0.1", "authority": authority}
    )


def semantic_epoch_document(authority_epoch, closure_warrant_hash):
    return digest(
        {
            "profile": "bulla.semantic-epoch/0.1-experimental",
            "authority_epoch": authority_epoch,
            "closure_warrant_hash": closure_warrant_hash,
        }
    )


def parse_observability_context(problem, sorts, relations, passport, manifest, offers):
    require_closed(
        passport,
        {
            "schema_version", "frsl_version", "finite_semantics", "extractor",
            "checker", "resource_bounds", "supported_guarantees",
            "unsupported_constructs",
        },
        set(),
        "logic_passport",
    )
    if (
        passport["schema_version"] != "0.1-experimental"
        or passport["frsl_version"] != LANGUAGE
        or passport["finite_semantics"] != "closed-finite-structures/1"
    ):
        raise ValueError("unsupported logic passport")
    bounds = passport["resource_bounds"]
    bound_fields = {
        "max_ground_atoms", "max_models", "max_observable_offers",
        "max_opposing_pairs", "max_minimal_plans",
    }
    require_closed(bounds, bound_fields, set(), "logic_passport.resource_bounds")
    if any(
        not isinstance(bounds[name], int)
        or isinstance(bounds[name], bool)
        or bounds[name] <= 0
        for name in bound_fields
    ):
        raise ValueError("logic passport bounds must be positive integers")
    if bounds["max_observable_offers"] > 16:
        raise ValueError("exact observability planning is capped at sixteen offers")
    if (
        bounds["max_ground_atoms"]
        != problem["synthesis_policy"]["reference_max_ground_atoms"]
        or bounds["max_models"]
        != problem["synthesis_policy"]["reference_max_models"]
    ):
        raise ValueError("logic passport does not pin problem enumeration bounds")
    require_closed(
        manifest,
        {
            "schema_version", "owner", "protected_relations", "protected_queries",
            "forbidden_disclosures", "permitted_evidence_classes",
            "authority_constraints",
        },
        set(),
        "conservation_manifest",
    )
    if manifest["schema_version"] != "0.1-experimental" or not manifest["owner"]:
        raise ValueError("unsupported conservation manifest")
    if manifest["authority_constraints"] != problem["authority"]:
        raise ValueError("manifest authority does not bind the problem")
    protected = {
        name
        for names in problem["protected_signatures"].values()
        for name in names
    }
    if not protected.issubset(set(manifest["protected_relations"])):
        raise ValueError("manifest omits a protected relation")
    if problem["target_predicate"] not in manifest["forbidden_disclosures"]:
        raise ValueError("manifest must forbid direct target disclosure")
    if not isinstance(offers, list) or len(offers) > bounds["max_observable_offers"]:
        raise ValueError("observable catalog exceeds the exact planning bound")
    ids = []
    parsed = []
    for offer in offers:
        require_closed(
            offer,
            {
                "offer_id", "relation", "sorts", "meaning", "provider",
                "warrant_profile", "burden", "consent_subjects", "expiry",
            },
            set(),
            "observable_offer",
        )
        ids.append(offer["offer_id"])
        if (
            not offer["offer_id"]
            or not offer["provider"]
            or offer["relation"] == problem["target_predicate"]
            or offer["relation"] in manifest["forbidden_disclosures"]
        ):
            raise ValueError("observable offer identity or disclosure is invalid")
        if (
            not isinstance(offer["sorts"], list)
            or len(offer["sorts"]) not in (1, 2)
            or any(sort not in sorts for sort in offer["sorts"])
        ):
            raise ValueError("observable offer sorts are invalid")
        existing = relations.get(offer["relation"])
        if existing is not None and list(existing) != offer["sorts"]:
            raise ValueError("observable offer changes an existing relation sort")
        warrant = offer["warrant_profile"]
        require_closed(
            warrant,
            {"kind", "evidence_class", "verifier", "reveals"},
            set(),
            "observable_offer.warrant_profile",
        )
        if (
            warrant["kind"] != warrant["evidence_class"]
            or warrant["evidence_class"] not in manifest["permitted_evidence_classes"]
            or warrant["reveals"] != "boolean_fact_only"
        ):
            raise ValueError("observable warrant is invalid")
        require_closed(offer["burden"], set(BURDEN_FIELDS), set(), "burden")
        if any(
            not isinstance(offer["burden"][name], int)
            or isinstance(offer["burden"][name], bool)
            or offer["burden"][name] < 0
            for name in BURDEN_FIELDS
        ):
            raise ValueError("observable burden must be a non-negative vector")
        subjects = offer["consent_subjects"]
        if (
            not isinstance(subjects, list)
            or offer["provider"] not in subjects
            or len(subjects) != len(set(subjects))
        ):
            raise ValueError("observable consent subjects are invalid")
        validate_formula(
            offer["meaning"],
            sorts,
            relations,
            {f"x{i}": sort for i, sort in enumerate(offer["sorts"])},
            "observable_offer.meaning",
        )
        parsed.append(offer)
    if len(ids) != len(set(ids)):
        raise ValueError("observable offer ids must be unique")
    return parsed


def offer_extension(offer, model, sorts):
    extension = []
    domains = [sorts[sort] for sort in offer["sorts"]]
    for arguments in itertools.product(*domains):
        env = {f"x{i}": value for i, value in enumerate(arguments)}
        if evaluate(offer["meaning"], model, sorts, env):
            extension.append(tuple(arguments))
    return tuple(extension)


def burden_sum(candidate, by_id):
    return {
        name: sum(by_id[offer_id]["burden"][name] for offer_id in candidate)
        for name in BURDEN_FIELDS
    }


def burden_dominates(left, right):
    return all(left[name] <= right[name] for name in BURDEN_FIELDS) and any(
        left[name] < right[name] for name in BURDEN_FIELDS
    )


def verify_enrichment_planning(
    problem_doc,
    passport,
    manifest,
    offers,
    planning,
):
    problem, sorts, relations = parse_problem(problem_doc)
    offers = parse_observability_context(
        problem, sorts, relations, passport, manifest, offers
    )
    if planning.get("status") != "PLANNED":
        raise ValueError("standalone planning replay currently accepts exact PLANNED artifacts")
    models = admissible_models(problem, sorts, relations)
    groups = {}
    for model in models:
        reduct = reduct_document(
            model, problem["shared_vocabulary"], sorts, relations
        )
        groups.setdefault(canon(reduct), []).append(model)
    pairs = []
    for reduct_key in sorted(groups):
        for left, right in itertools.combinations(groups[reduct_key], 2):
            target = problem["target_predicate"]
            if left[target] == right[target]:
                continue
            left_hash = digest(structure_document(left, sorts, relations))
            right_hash = digest(structure_document(right, sorts, relations))
            symmetric = sorted(left[target] ^ right[target])
            pair_id = digest(
                {
                    "problem_hash": digest(problem),
                    "shared_reduct_hash": digest(
                        reduct_document(
                            left, problem["shared_vocabulary"], sorts, relations
                        )
                    ),
                    "model_hashes": sorted((left_hash, right_hash)),
                    "target_difference_hash": digest(symmetric),
                }
            )
            pairs.append((pair_id, left, right))
    pairs.sort(key=lambda item: item[0])
    if len(pairs) > passport["resource_bounds"]["max_opposing_pairs"]:
        raise ValueError("opposing-pair bound exceeded")
    pair_ids = tuple(item[0] for item in pairs)
    pair_digest = digest(list(pair_ids))
    coverage = {}
    for offer in offers:
        coverage[offer["offer_id"]] = frozenset(
            pair_id
            for pair_id, left, right in pairs
            if offer_extension(offer, left, sorts) != offer_extension(offer, right, sorts)
        )
    all_pairs = frozenset(pair_ids)
    if not all_pairs:
        raise ValueError("PLANNED artifact has no opposing pairs")
    if all_pairs - frozenset().union(*coverage.values()):
        raise ValueError("catalog is not sufficient")
    minimal = []
    offer_ids = tuple(sorted(coverage))
    for size in range(1, len(offer_ids) + 1):
        for candidate in itertools.combinations(offer_ids, size):
            candidate_set = frozenset(candidate)
            if any(frozenset(existing).issubset(candidate_set) for existing in minimal):
                continue
            covered = frozenset().union(*(coverage[item] for item in candidate))
            if covered == all_pairs:
                minimal.append(candidate)
                if len(minimal) > passport["resource_bounds"]["max_minimal_plans"]:
                    raise ValueError("minimal-plan bound exceeded")
    indispensable = tuple(sorted(set.intersection(*(set(item) for item in minimal))))
    by_id = {offer["offer_id"]: offer for offer in offers}
    burdens = {candidate: burden_sum(candidate, by_id) for candidate in minimal}
    frontier = {
        candidate
        for candidate in minimal
        if not any(
            other != candidate and burden_dominates(burdens[other], burdens[candidate])
            for other in minimal
        )
    }
    plans = []
    for candidate in minimal:
        selected_coverage = {
            offer_id: sorted(coverage[offer_id]) for offer_id in candidate
        }
        covered = frozenset().union(*(coverage[item] for item in candidate))
        consent = tuple(
            sorted(
                {
                    subject
                    for offer_id in candidate
                    for subject in by_id[offer_id]["consent_subjects"]
                }
            )
        )
        plan = {
            "observable_ids": list(candidate),
            "opposing_pair_digest": pair_digest,
            "separation_digest": digest(selected_coverage),
            "sufficiency_certificate": {
                "pair_count": len(pairs),
                "covered_pair_count": len(covered),
                "coverage_digest": digest(sorted(covered)),
                "sufficient": True,
            },
            "minimality": "exact-declared-candidate-space",
            "pareto_status": "frontier" if candidate in frontier else "dominated",
            "indispensable_observables": list(indispensable),
            "consent_subjects": list(consent),
            "predicted_envelope_reduction": {
                "opposing_pairs_before": len(pairs),
                "opposing_pairs_after": 0,
                "coverage_ppm": 1000000,
            },
            "burden": burdens[candidate],
        }
        plans.append(plan)
    plans.sort(key=digest)
    expected = {
        "schema_version": "0.1-experimental",
        "status": "PLANNED",
        "problem_hash": digest(problem),
        "passport_hash": digest(passport),
        "manifest_hash": digest(manifest),
        "catalog_hash": digest(sorted(offers, key=lambda item: item["offer_id"])),
        "opposing_pair_digest": pair_digest,
        "opposing_pair_count": len(pairs),
        "plans": plans,
        "indispensable_observables": list(indispensable),
        "reason": "all target-disagreeing same-reduct pairs are exactly covered",
    }
    ok = expected == planning
    return {
        "ok": ok,
        "planning_hash": digest(planning),
        "expected_hash": digest(expected),
        "mismatched_fields": [
            key for key in sorted(expected) if expected.get(key) != planning.get(key)
        ],
        "expected_field_hashes": {
            key: digest(expected[key])
            for key in sorted(expected)
            if expected.get(key) != planning.get(key)
        },
        "actual_field_hashes": {
            key: digest(planning.get(key))
            for key in sorted(expected)
            if expected.get(key) != planning.get(key)
        },
        "opposing_pair_count": len(pairs),
        "plan_count": len(plans),
    }


def effective_problem_document(base_problem, admissions):
    effective = json.loads(json.dumps(base_problem))
    effective["local_theories"][0]["constraints"].extend(
        admission["constraint"] for admission in admissions
    )
    return effective


def parse_admission(admission, problem, sorts, relations, epoch):
    require_closed(
        admission,
        {
            "schema_version", "kind", "constraint", "provenance", "authority_epoch",
            "response_hashes", "request_hash", "plan_hash", "evidence_classes",
        },
        set(),
        "constraint_admission",
    )
    if admission["schema_version"] != "0.1-experimental":
        raise ValueError("unsupported constraint admission")
    if admission["kind"] not in {"EVIDENCE", "PRECEDENT"}:
        raise ValueError("unknown admission kind")
    if admission["authority_epoch"] != epoch:
        raise ValueError("admission changes authority inside a refinement")
    validate_formula(admission["constraint"], sorts, relations, where="admission.constraint")
    if admission["constraint"] != normalize_formula(admission["constraint"]):
        raise ValueError("admitted constraint is not canonical FRSL-1")
    if admission["kind"] == "EVIDENCE":
        if not (
            admission["request_hash"]
            and admission["plan_hash"]
            and admission["response_hashes"]
            and admission["evidence_classes"]
        ):
            raise ValueError("evidence admission lacks consent/warrant bindings")
    else:
        if any(
            (
                admission["request_hash"] is not None,
                admission["plan_hash"] is not None,
                bool(admission["response_hashes"]),
                bool(admission["evidence_classes"]),
            )
        ):
            raise ValueError("precedent admission impersonates evidence")
    return admission


def semantic_state_document(base_problem, admissions, epoch):
    effective = effective_problem_document(base_problem, admissions)
    parsed, sorts, relations = parse_problem(effective)
    models = admissible_models(parsed, sorts, relations)
    if not models:
        raise ValueError("admitted constraints eliminate every semantic world")
    model_hashes = sorted(
        digest(structure_document(model, sorts, relations)) for model in models
    )
    commitment = {
        "schema_version": "0.1-experimental",
        "base_problem_hash": digest(base_problem),
        "effective_problem_hash": digest(parsed),
        "authority_epoch": epoch,
        "admission_hashes": [digest(admission) for admission in admissions],
        "model_hashes": model_hashes,
    }
    return parsed, sorts, relations, models, commitment


def package_regions_document(problem, sorts, relations, models, package):
    decisions = {}
    domains = [sorts[sort] for sort in relations[problem["target_predicate"]]]
    for model in models:
        reduct_hash = digest(
            reduct_document(
                model, problem["shared_vocabulary"], sorts, relations
            )
        )
        for arguments in itertools.product(*domains):
            key = digest(
                {"shared_reduct_hash": reduct_hash, "target_arguments": list(arguments)}
            )
            env = {f"x{i}": value for i, value in enumerate(arguments)}
            if package["mode"] == "full":
                decision = "RELY" if evaluate(package["definition"], model, sorts, env) else "REFUSE"
            else:
                rely = evaluate(package["rely_when"], model, sorts, env)
                refuse = evaluate(package["refuse_when"], model, sorts, env)
                if rely and refuse:
                    raise ValueError("package overlaps RELY and REFUSE")
                decision = "RELY" if rely else "REFUSE" if refuse else "ESCALATE"
            if key in decisions and decisions[key] != decision:
                raise ValueError("package is not a function of the shared reduct")
            decisions[key] = decision
    return {
        "reachable": sorted(decisions),
        "rely": sorted(key for key, value in decisions.items() if value == "RELY"),
        "refuse": sorted(key for key, value in decisions.items() if value == "REFUSE"),
        "ambiguous": sorted(key for key, value in decisions.items() if value == "ESCALATE"),
    }


def snapshot_document(base_problem, effective, result, models, sorts, relations, state, passport, manifest, closure_warrant_hash):
    package = result["package"]
    return {
        "schema_version": "0.1-experimental",
        "base_problem_hash": digest(base_problem),
        "effective_problem_hash": digest(effective),
        "result_hash": digest(result),
        "package_hash": digest(package),
        "package_mode": package["mode"],
        "semantic_state_hash": digest(state),
        "passport_hash": digest(passport),
        "manifest_hash": digest(manifest),
        "authority_epoch": state["authority_epoch"],
        "closure_warrant_hash": closure_warrant_hash,
        "semantic_epoch": semantic_epoch_document(
            state["authority_epoch"], closure_warrant_hash
        ),
        "regions": package_regions_document(
            effective, sorts, relations, models, package
        ),
    }


def verify_refinement_bundle(bundle):
    require_closed(
        bundle,
        {
            "schema_version", "base_problem", "prior_result", "prior_admissions",
            "admission", "passport", "manifest", "new_result", "prior_snapshot",
            "new_snapshot", "certificate",
        },
        set(),
        "refinement_bundle",
    )
    if bundle["schema_version"] != "0.1-experimental":
        raise ValueError("unsupported refinement bundle")
    base, base_sorts, base_relations = parse_problem(bundle["base_problem"])
    epoch = authority_epoch_document(base["authority"])
    prior_admissions = [
        parse_admission(item, base, base_sorts, base_relations, epoch)
        for item in bundle["prior_admissions"]
    ]
    admission = parse_admission(
        bundle["admission"], base, base_sorts, base_relations, epoch
    )
    prior_problem, prior_sorts, prior_relations, prior_models, prior_state = (
        semantic_state_document(base, prior_admissions, epoch)
    )
    new_admissions = prior_admissions + [admission]
    new_problem, new_sorts, new_relations, new_models, new_state = (
        semantic_state_document(base, new_admissions, epoch)
    )
    prior_verification = verify(prior_problem, bundle["prior_result"])
    new_verification = verify(new_problem, bundle["new_result"])
    if not prior_verification["ok"] or not new_verification["ok"]:
        return {"ok": False, "error": "embedded invention result failed replay"}
    prior_snapshot = snapshot_document(
        base,
        prior_problem,
        bundle["prior_result"],
        prior_models,
        prior_sorts,
        prior_relations,
        prior_state,
        bundle["passport"],
        bundle["manifest"],
        bundle["prior_snapshot"]["closure_warrant_hash"],
    )
    new_snapshot = snapshot_document(
        base,
        new_problem,
        bundle["new_result"],
        new_models,
        new_sorts,
        new_relations,
        new_state,
        bundle["passport"],
        bundle["manifest"],
        bundle["prior_snapshot"]["closure_warrant_hash"],
    )
    prior_reachable = set(prior_snapshot["regions"]["reachable"])
    new_reachable = set(new_snapshot["regions"]["reachable"])
    still_reachable = prior_reachable & new_reachable
    expected_certificate = {
        "schema_version": "0.1-experimental",
        "prior_state_hash": digest(prior_state),
        "new_state_hash": digest(new_state),
        "admitted_constraint_hash": digest(admission["constraint"]),
        "prior_snapshot_hash": digest(prior_snapshot),
        "new_snapshot_hash": digest(new_snapshot),
        "state_inclusion": set(new_state["model_hashes"]).issubset(prior_state["model_hashes"]),
        "retained_rely": (
            set(prior_snapshot["regions"]["rely"]) & still_reachable
        ).issubset(new_snapshot["regions"]["rely"]),
        "retained_refuse": (
            set(prior_snapshot["regions"]["refuse"]) & still_reachable
        ).issubset(new_snapshot["regions"]["refuse"]),
        "ambiguity_narrowed": set(new_snapshot["regions"]["ambiguous"]).issubset(
            prior_snapshot["regions"]["ambiguous"]
        ),
        "authority_preserved": True,
        "verifier": {
            "id": "bulla.experimental.refinement.reference",
            "version": "0.1-experimental",
            "trust": "direct-finite-enumeration",
        },
    }
    checks = {
        "prior_snapshot": prior_snapshot == bundle["prior_snapshot"],
        "new_snapshot": new_snapshot == bundle["new_snapshot"],
        "certificate": expected_certificate == bundle["certificate"],
        "all_gates": all(
            expected_certificate[name]
            for name in (
                "state_inclusion", "retained_rely", "retained_refuse",
                "ambiguity_narrowed", "authority_preserved",
            )
        ),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "bundle_hash": digest(bundle),
        "certificate_hash": digest(bundle["certificate"]),
        "prior_result_hash": prior_verification["result_hash"],
        "new_result_hash": new_verification["result_hash"],
    }


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "plan-enrichment":
        parser = argparse.ArgumentParser()
        parser.add_argument("command")
        parser.add_argument("problem", type=Path)
        parser.add_argument("passport", type=Path)
        parser.add_argument("manifest", type=Path)
        parser.add_argument("offers", type=Path)
        parser.add_argument("planning", type=Path)
        args = parser.parse_args()
        try:
            payload = verify_enrichment_planning(
                json.loads(args.problem.read_text(encoding="utf-8")),
                json.loads(args.passport.read_text(encoding="utf-8")),
                json.loads(args.manifest.read_text(encoding="utf-8")),
                json.loads(args.offers.read_text(encoding="utf-8")),
                json.loads(args.planning.read_text(encoding="utf-8")),
            )
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 1
    if len(sys.argv) > 1 and sys.argv[1] == "verify-refinement":
        parser = argparse.ArgumentParser()
        parser.add_argument("command")
        parser.add_argument("bundle", type=Path)
        args = parser.parse_args()
        try:
            payload = verify_refinement_bundle(
                json.loads(args.bundle.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 1
    parser = argparse.ArgumentParser()
    parser.add_argument("problem", type=Path)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    try:
        problem = json.loads(args.problem.read_text(encoding="utf-8"))
        result = json.loads(args.result.read_text(encoding="utf-8"))
        payload = verify(problem, result)
    except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
