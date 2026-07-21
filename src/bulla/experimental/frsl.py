"""FRSL-1: a deliberately finite relational synthesis language.

The trusted research kernel is small by construction:

* finite named sorts;
* equality and unary/binary relations;
* Boolean connectives;
* bounded quantifiers over named finite sorts.

There are no functions, unbounded quantifiers, floats, regexes, host-language
callbacks, or solver-specific terms.  Every sentence can therefore be checked
by direct finite evaluation, and every small structure can be enumerated by the
reference backend.

This module is experimental and is not re-exported from bulla's stable API.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping

from bulla._canonical import canonical_json

LANGUAGE = "FRSL-1"
SCHEMA_VERSION = "0.1-experimental"


class FRSLError(ValueError):
    """Raised when a document is outside FRSL-1."""


def canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _closed_object(value: Any, *, required: set[str], optional: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        raise FRSLError(f"{where} must be an object")
    missing = required - set(value)
    if missing:
        raise FRSLError(f"{where} is missing required keys {sorted(missing)}")
    unknown = set(value) - required - optional
    if unknown:
        raise FRSLError(f"{where} has unknown keys {sorted(unknown)}")
    return value


@dataclass(frozen=True)
class RelationDecl:
    name: str
    sorts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise FRSLError("relation.name must be non-empty")
        if len(self.sorts) not in (1, 2):
            raise FRSLError(f"relation {self.name!r} must be unary or binary")

    @property
    def arity(self) -> int:
        return len(self.sorts)

    def to_dict(self) -> dict:
        return {"name": self.name, "sorts": list(self.sorts)}

    @classmethod
    def from_dict(cls, value: Any) -> "RelationDecl":
        d = _closed_object(
            value,
            required={"name", "sorts"},
            optional=set(),
            where="relation",
        )
        if not isinstance(d["sorts"], list) or not all(isinstance(x, str) for x in d["sorts"]):
            raise FRSLError("relation.sorts must be a list of sort names")
        return cls(name=d["name"], sorts=tuple(d["sorts"]))


@dataclass(frozen=True)
class Signature:
    sorts: Mapping[str, tuple[str, ...]]
    relations: Mapping[str, RelationDecl]

    def __post_init__(self) -> None:
        normalized_sorts: dict[str, tuple[str, ...]] = {}
        for name, elements in self.sorts.items():
            if not isinstance(name, str) or not name:
                raise FRSLError("sort names must be non-empty strings")
            vals = tuple(elements)
            if not vals:
                raise FRSLError(f"sort {name!r} must have at least one named element")
            if any(not isinstance(x, str) or not x for x in vals):
                raise FRSLError(f"sort {name!r} elements must be non-empty strings")
            if len(set(vals)) != len(vals):
                raise FRSLError(f"sort {name!r} has duplicate elements")
            normalized_sorts[name] = vals
        normalized_relations = dict(self.relations)
        if len(normalized_relations) != len(self.relations):
            raise FRSLError("duplicate relation names")
        for name, rel in normalized_relations.items():
            if name != rel.name:
                raise FRSLError("relation map key must equal relation.name")
            for sort in rel.sorts:
                if sort not in normalized_sorts:
                    raise FRSLError(f"relation {name!r} references unknown sort {sort!r}")
        object.__setattr__(self, "sorts", normalized_sorts)
        object.__setattr__(self, "relations", normalized_relations)

    def to_dict(self) -> dict:
        return {
            "sorts": {name: list(self.sorts[name]) for name in sorted(self.sorts)},
            "relations": [self.relations[name].to_dict() for name in sorted(self.relations)],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "Signature":
        d = _closed_object(
            value,
            required={"sorts", "relations"},
            optional=set(),
            where="signature",
        )
        if not isinstance(d["sorts"], dict):
            raise FRSLError("signature.sorts must be an object")
        sorts: dict[str, tuple[str, ...]] = {}
        for name, elements in d["sorts"].items():
            if not isinstance(elements, list):
                raise FRSLError(f"signature.sorts.{name} must be a list")
            sorts[name] = tuple(elements)
        if not isinstance(d["relations"], list):
            raise FRSLError("signature.relations must be a list")
        rels = [RelationDecl.from_dict(x) for x in d["relations"]]
        if len({r.name for r in rels}) != len(rels):
            raise FRSLError("signature has duplicate relation names")
        return cls(sorts=sorts, relations={r.name: r for r in rels})

    def ground_atoms(self, relation_names: Iterable[str] | None = None) -> tuple[tuple[str, tuple[str, ...]], ...]:
        names = sorted(relation_names if relation_names is not None else self.relations)
        atoms: list[tuple[str, tuple[str, ...]]] = []
        for name in names:
            rel = self.relations.get(name)
            if rel is None:
                raise FRSLError(f"unknown relation {name!r}")
            domains = [self.sorts[sort] for sort in rel.sorts]
            for args in itertools.product(*domains):
                atoms.append((name, tuple(args)))
        return tuple(atoms)


Term = dict[str, str]
Formula = dict[str, Any]
Structure = dict[str, tuple[tuple[str, ...], ...]]


def variable(name: str) -> Term:
    return {"var": name}


def constant(name: str) -> Term:
    return {"const": name}


def atom(relation: str, args: Iterable[Term]) -> Formula:
    return {"op": "atom", "relation": relation, "args": [dict(x) for x in args]}


def truth() -> Formula:
    return {"op": "true"}


def falsity() -> Formula:
    return {"op": "false"}


def negate(body: Formula) -> Formula:
    return {"op": "not", "body": body}


def conjunction(parts: Iterable[Formula]) -> Formula:
    vals = list(parts)
    if not vals:
        return truth()
    if len(vals) == 1:
        return vals[0]
    return {"op": "and", "args": vals}


def disjunction(parts: Iterable[Formula]) -> Formula:
    vals = list(parts)
    if not vals:
        return falsity()
    if len(vals) == 1:
        return vals[0]
    return {"op": "or", "args": vals}


def _validate_term(term: Any, *, signature: Signature, expected_sort: str, bound: Mapping[str, str], where: str) -> None:
    d = _closed_object(
        term,
        required=set(),
        optional={"var", "const"},
        where=where,
    )
    if set(d) not in ({"var"}, {"const"}):
        raise FRSLError(f"{where} must contain exactly one of var or const")
    if "var" in d:
        name = d["var"]
        if not isinstance(name, str) or name not in bound:
            raise FRSLError(f"{where} references unbound variable {name!r}")
        if bound[name] != expected_sort:
            raise FRSLError(
                f"{where} variable {name!r} has sort {bound[name]!r}, expected {expected_sort!r}"
            )
    else:
        name = d["const"]
        if not isinstance(name, str) or name not in signature.sorts[expected_sort]:
            raise FRSLError(
                f"{where} constant {name!r} is not in sort {expected_sort!r}"
            )


def validate_formula(
    formula: Any,
    *,
    signature: Signature,
    free_variables: Mapping[str, str] | None = None,
    where: str = "formula",
) -> Formula:
    """Validate a closed FRSL-1 AST and return it unchanged."""
    bound = dict(free_variables or {})

    def visit(node: Any, env: dict[str, str], path: str) -> None:
        if not isinstance(node, dict):
            raise FRSLError(f"{path} must be an object")
        op = node.get("op")
        if op in ("true", "false"):
            _closed_object(node, required={"op"}, optional=set(), where=path)
            return
        if op == "atom":
            d = _closed_object(
                node,
                required={"op", "relation", "args"},
                optional=set(),
                where=path,
            )
            rel = signature.relations.get(d["relation"])
            if rel is None:
                raise FRSLError(f"{path} references unknown relation {d['relation']!r}")
            if not isinstance(d["args"], list) or len(d["args"]) != rel.arity:
                raise FRSLError(f"{path}.args must have arity {rel.arity}")
            for i, (term, sort) in enumerate(zip(d["args"], rel.sorts)):
                _validate_term(
                    term,
                    signature=signature,
                    expected_sort=sort,
                    bound=env,
                    where=f"{path}.args[{i}]",
                )
            return
        if op == "eq":
            d = _closed_object(
                node,
                required={"op", "sort", "left", "right"},
                optional=set(),
                where=path,
            )
            sort = d["sort"]
            if sort not in signature.sorts:
                raise FRSLError(f"{path} references unknown sort {sort!r}")
            _validate_term(d["left"], signature=signature, expected_sort=sort, bound=env, where=f"{path}.left")
            _validate_term(d["right"], signature=signature, expected_sort=sort, bound=env, where=f"{path}.right")
            return
        if op == "not":
            d = _closed_object(node, required={"op", "body"}, optional=set(), where=path)
            visit(d["body"], env, f"{path}.body")
            return
        if op in ("and", "or"):
            d = _closed_object(node, required={"op", "args"}, optional=set(), where=path)
            if not isinstance(d["args"], list):
                raise FRSLError(f"{path}.args must be a list")
            for i, child in enumerate(d["args"]):
                visit(child, env, f"{path}.args[{i}]")
            return
        if op in ("implies", "iff"):
            d = _closed_object(
                node,
                required={"op", "left", "right"},
                optional=set(),
                where=path,
            )
            visit(d["left"], env, f"{path}.left")
            visit(d["right"], env, f"{path}.right")
            return
        if op in ("forall", "exists"):
            d = _closed_object(
                node,
                required={"op", "var", "sort", "body"},
                optional=set(),
                where=path,
            )
            if not isinstance(d["var"], str) or not d["var"]:
                raise FRSLError(f"{path}.var must be a non-empty string")
            if d["sort"] not in signature.sorts:
                raise FRSLError(f"{path} references unknown sort {d['sort']!r}")
            nested = dict(env)
            nested[d["var"]] = d["sort"]
            visit(d["body"], nested, f"{path}.body")
            return
        raise FRSLError(f"{path}.op {op!r} is not in FRSL-1")

    visit(formula, bound, where)
    return formula


def _term_value(term: Term, env: Mapping[str, str]) -> str:
    if "const" in term:
        return term["const"]
    return env[term["var"]]


def evaluate(
    formula: Formula,
    *,
    signature: Signature,
    structure: Structure,
    environment: Mapping[str, str] | None = None,
) -> bool:
    """Evaluate a previously validated formula in one finite structure."""
    env = dict(environment or {})
    op = formula["op"]
    if op == "true":
        return True
    if op == "false":
        return False
    if op == "atom":
        args = tuple(_term_value(term, env) for term in formula["args"])
        return args in set(structure.get(formula["relation"], ()))
    if op == "eq":
        return _term_value(formula["left"], env) == _term_value(formula["right"], env)
    if op == "not":
        return not evaluate(formula["body"], signature=signature, structure=structure, environment=env)
    if op == "and":
        return all(evaluate(x, signature=signature, structure=structure, environment=env) for x in formula["args"])
    if op == "or":
        return any(evaluate(x, signature=signature, structure=structure, environment=env) for x in formula["args"])
    if op == "implies":
        return (not evaluate(formula["left"], signature=signature, structure=structure, environment=env)) or evaluate(
            formula["right"], signature=signature, structure=structure, environment=env
        )
    if op == "iff":
        return evaluate(formula["left"], signature=signature, structure=structure, environment=env) == evaluate(
            formula["right"], signature=signature, structure=structure, environment=env
        )
    if op in ("forall", "exists"):
        values = []
        for element in signature.sorts[formula["sort"]]:
            nested = dict(env)
            nested[formula["var"]] = element
            values.append(
                evaluate(formula["body"], signature=signature, structure=structure, environment=nested)
            )
        return all(values) if op == "forall" else any(values)
    raise AssertionError(f"validated FRSL-1 formula has impossible op {op!r}")


def normalize_structure(value: Mapping[str, Iterable[Iterable[str]]], signature: Signature) -> Structure:
    unknown = set(value) - set(signature.relations)
    if unknown:
        raise FRSLError(f"structure has unknown relations {sorted(unknown)}")
    result: Structure = {}
    for name, rel in signature.relations.items():
        tuples: set[tuple[str, ...]] = set()
        for raw in value.get(name, ()):
            vals = tuple(raw)
            if len(vals) != rel.arity:
                raise FRSLError(f"structure relation {name!r} tuple has wrong arity")
            for element, sort in zip(vals, rel.sorts):
                if element not in signature.sorts[sort]:
                    raise FRSLError(
                        f"structure relation {name!r} contains {element!r} outside sort {sort!r}"
                    )
            tuples.add(vals)
        result[name] = tuple(sorted(tuples))
    return result


def structure_to_dict(structure: Structure) -> dict:
    return {name: [list(args) for args in structure[name]] for name in sorted(structure)}


def enumerate_structures(
    signature: Signature,
    *,
    relation_names: Iterable[str] | None = None,
    max_ground_atoms: int,
    max_models: int,
) -> Iterator[Structure]:
    """Enumerate interpretations in canonical bit order.

    Relations outside relation_names are present with empty interpretations.
    Callers normally enumerate the whole signature.
    """
    names = tuple(sorted(relation_names if relation_names is not None else signature.relations))
    atoms = signature.ground_atoms(names)
    if len(atoms) > max_ground_atoms:
        raise FRSLError(
            f"reference bound exceeded: {len(atoms)} ground atoms > {max_ground_atoms}"
        )
    count = 1 << len(atoms)
    if count > max_models:
        raise FRSLError(f"reference model bound exceeded: {count} models > {max_models}")
    all_names = sorted(signature.relations)
    for bits in range(count):
        mutable: dict[str, list[tuple[str, ...]]] = {name: [] for name in all_names}
        for i, (name, args) in enumerate(atoms):
            if bits & (1 << i):
                mutable[name].append(args)
        yield {name: tuple(mutable[name]) for name in all_names}


def relation_reduct(structure: Structure, relation_names: Iterable[str]) -> dict:
    return {
        name: [list(args) for args in structure[name]]
        for name in sorted(set(relation_names))
    }


def free_variables(formula: Formula) -> set[str]:
    result: set[str] = set()

    def term_vars(term: Term, bound: set[str]) -> None:
        if "var" in term and term["var"] not in bound:
            result.add(term["var"])

    def visit(node: Formula, bound: set[str]) -> None:
        op = node["op"]
        if op == "atom":
            for term in node["args"]:
                term_vars(term, bound)
        elif op == "eq":
            term_vars(node["left"], bound)
            term_vars(node["right"], bound)
        elif op == "not":
            visit(node["body"], bound)
        elif op in ("and", "or"):
            for child in node["args"]:
                visit(child, bound)
        elif op in ("implies", "iff"):
            visit(node["left"], bound)
            visit(node["right"], bound)
        elif op in ("forall", "exists"):
            visit(node["body"], bound | {node["var"]})

    visit(formula, set())
    return result


def formula_size(formula: Formula) -> int:
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
    raise FRSLError(f"unknown op {op!r}")


def formula_relations(formula: Formula) -> set[str]:
    """Return every relation symbol used by a validated formula."""
    op = formula["op"]
    if op == "atom":
        return {formula["relation"]}
    if op in ("true", "false", "eq"):
        return set()
    if op == "not":
        return formula_relations(formula["body"])
    if op in ("and", "or"):
        result: set[str] = set()
        for child in formula["args"]:
            result.update(formula_relations(child))
        return result
    if op in ("implies", "iff"):
        return formula_relations(formula["left"]) | formula_relations(formula["right"])
    if op in ("forall", "exists"):
        return formula_relations(formula["body"])
    raise FRSLError(f"unknown op {op!r}")


def normalize_formula(formula: Formula) -> Formula:
    """Canonicalize the FRSL-1 AST representation.

    This is structural canonicalization, not a claim to decide logical
    equivalence.  It closes the most important hash instability: associative,
    commutative and duplicate variation in conjunctions/disjunctions.
    """
    op = formula["op"]
    if op in ("true", "false", "atom", "eq"):
        return formula
    if op == "not":
        body = normalize_formula(formula["body"])
        if body["op"] == "not":
            return normalize_formula(body["body"])
        if body["op"] == "true":
            return falsity()
        if body["op"] == "false":
            return truth()
        return {"op": "not", "body": body}
    if op in ("and", "or"):
        children: list[Formula] = []
        for child in formula["args"]:
            normalized = normalize_formula(child)
            if normalized["op"] == op:
                children.extend(normalized["args"])
            else:
                children.append(normalized)
        identity = "true" if op == "and" else "false"
        annihilator = "false" if op == "and" else "true"
        if any(child["op"] == annihilator for child in children):
            return falsity() if op == "and" else truth()
        children = [child for child in children if child["op"] != identity]
        unique = {canonical_json(child): child for child in children}
        ordered = [unique[key] for key in sorted(unique)]
        if not ordered:
            return truth() if op == "and" else falsity()
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
        parts.sort(key=canonical_json)
        return {"op": "iff", "left": parts[0], "right": parts[1]}
    if op in ("forall", "exists"):
        return {
            "op": op,
            "var": formula["var"],
            "sort": formula["sort"],
            "body": normalize_formula(formula["body"]),
        }
    raise FRSLError(f"unknown op {op!r}")
