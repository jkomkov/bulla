"""Pinned SMTInterpol candidate backend for FRSL-1.

SMTInterpol is never the trust root here.  This adapter:

1. checks the exact solver artifact hash and version output;
2. retains the grounded two-copy query and raw output;
3. parses only a closed Boolean interpolant subset; and
4. sends the candidate through the exhaustive FRSL-1 verifier.

Unsupported output, timeout, missing binaries, SAT where extraction expected,
and solver unknown all produce INDETERMINATE.  None are converted into a
non-definability certificate.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from bulla._canonical import canonical_json
from bulla.experimental.frsl import (
    Formula,
    atom,
    conjunction,
    constant,
    disjunction,
    falsity,
    negate,
    normalize_formula,
    truth,
    variable,
)
from bulla.experimental.invention import (
    FailureCertificate,
    FailureKind,
    GateReport,
    GateStatus,
    PredicatePackage,
    SeamProblem,
    SynthesisResult,
    SynthesisStatus,
    _admissible_models,
    _feature_atoms,
    _make_package,
    synthesize,
    verify_package,
)


@dataclass(frozen=True)
class SMTInterpolConfig:
    jar_path: Path
    jar_sha256: str
    version_contains: str
    java_command: str = "java"
    timeout_seconds: float = 10.0
    require_resolute_proof: bool = True
    fallback_to_reference: bool = False

    def __post_init__(self) -> None:
        if not self.jar_sha256.startswith("sha256:"):
            raise ValueError("jar_sha256 must use the sha256:<hex> form")
        if not self.version_contains:
            raise ValueError("version_contains must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class SolverArtifact:
    target_arguments: tuple[str, ...]
    input_smt2: str
    stdout: str
    stderr: str
    returncode: int
    solver_version: str
    jar_sha256: str
    proof_input_smt2: str | None = None
    proof_stdout: str = ""
    proof_stderr: str = ""
    proof_returncode: int | None = None
    proof_checker_stdout: str = ""
    proof_checker_stderr: str = ""
    proof_checker_returncode: int | None = None
    resolute_verified: bool = False

    def to_dict(self) -> dict:
        return {
            "target_arguments": list(self.target_arguments),
            "input_smt2": self.input_smt2,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
            "solver_version": self.solver_version,
            "jar_sha256": self.jar_sha256,
            "proof_input_smt2": self.proof_input_smt2,
            "proof_stdout": self.proof_stdout,
            "proof_stderr": self.proof_stderr,
            "proof_returncode": self.proof_returncode,
            "proof_checker_stdout": self.proof_checker_stdout,
            "proof_checker_stderr": self.proof_checker_stderr,
            "proof_checker_returncode": self.proof_checker_returncode,
            "resolute_verified": self.resolute_verified,
        }


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _probe(config: SMTInterpolConfig) -> str:
    if not config.jar_path.is_file():
        raise RuntimeError(f"SMTInterpol jar not found: {config.jar_path}")
    actual = _file_hash(config.jar_path)
    if actual != config.jar_sha256:
        raise RuntimeError(
            f"SMTInterpol jar pin mismatch: expected {config.jar_sha256}, got {actual}"
        )
    completed = subprocess.run(
        [config.java_command, "-jar", str(config.jar_path), "-version"],
        capture_output=True,
        text=True,
        timeout=config.timeout_seconds,
        check=False,
    )
    version = (completed.stdout + "\n" + completed.stderr).strip()
    if completed.returncode != 0:
        raise RuntimeError(
            f"SMTInterpol version probe failed with exit {completed.returncode}: {version}"
        )
    if config.version_contains not in version:
        raise RuntimeError(
            f"SMTInterpol version mismatch: expected output containing "
            f"{config.version_contains!r}, got {version!r}"
        )
    return version


def _symbol_maps(problem: SeamProblem):
    atoms = problem.signature.ground_atoms()
    shared = set(problem.shared_vocabulary)
    shared_symbols = {}
    left_symbols = {}
    right_symbols = {}
    reverse_shared = {}
    for index, ground in enumerate(atoms):
        relation, _ = ground
        if relation in shared:
            symbol = f"s{index}"
            shared_symbols[ground] = symbol
            reverse_shared[symbol] = ground
        else:
            left_symbols[ground] = f"a{index}"
            right_symbols[ground] = f"b{index}"
    return shared_symbols, left_symbols, right_symbols, reverse_shared


def _term(term: Mapping[str, str], env: Mapping[str, str]) -> str:
    return term["const"] if "const" in term else env[term["var"]]


def _nary(token: str, children: Sequence[str], identity: str) -> str:
    """Emit a well-formed SMT-LIB Boolean connective at every finite arity."""
    if not children:
        return identity
    if len(children) == 1:
        return children[0]
    return f"({token} {' '.join(children)})"


def _to_smt(
    formula: Formula,
    *,
    problem: SeamProblem,
    env: Mapping[str, str],
    copy_symbols: Mapping[tuple[str, tuple[str, ...]], str],
    shared_symbols: Mapping[tuple[str, tuple[str, ...]], str],
) -> str:
    op = formula["op"]
    if op == "true":
        return "true"
    if op == "false":
        return "false"
    if op == "atom":
        ground = (
            formula["relation"],
            tuple(_term(x, env) for x in formula["args"]),
        )
        return shared_symbols.get(ground) or copy_symbols[ground]
    if op == "eq":
        return "true" if _term(formula["left"], env) == _term(formula["right"], env) else "false"
    if op == "not":
        return f"(not {_to_smt(formula['body'], problem=problem, env=env, copy_symbols=copy_symbols, shared_symbols=shared_symbols)})"
    if op in ("and", "or"):
        children = [
            _to_smt(x, problem=problem, env=env, copy_symbols=copy_symbols, shared_symbols=shared_symbols)
            for x in formula["args"]
        ]
        identity = "true" if op == "and" else "false"
        return _nary(op, children, identity)
    if op in ("implies", "iff"):
        left = _to_smt(
            formula["left"],
            problem=problem,
            env=env,
            copy_symbols=copy_symbols,
            shared_symbols=shared_symbols,
        )
        right = _to_smt(
            formula["right"],
            problem=problem,
            env=env,
            copy_symbols=copy_symbols,
            shared_symbols=shared_symbols,
        )
        token = "=>" if op == "implies" else "="
        return f"({token} {left} {right})"
    if op in ("forall", "exists"):
        children = []
        for element in problem.signature.sorts[formula["sort"]]:
            nested = dict(env)
            nested[formula["var"]] = element
            children.append(
                _to_smt(
                    formula["body"],
                    problem=problem,
                    env=nested,
                    copy_symbols=copy_symbols,
                    shared_symbols=shared_symbols,
                )
            )
        token = "and" if op == "forall" else "or"
        identity = "true" if op == "forall" else "false"
        return _nary(token, children, identity)
    raise ValueError(f"unsupported FRSL-1 op {op!r}")


def build_two_copy_query(
    problem: SeamProblem, target_arguments: Sequence[str]
) -> tuple[str, Mapping[str, tuple[str, tuple[str, ...]]]]:
    shared, left, right, reverse_shared = _symbol_maps(problem)
    declarations = sorted(set(shared.values()) | set(left.values()) | set(right.values()))
    constraints = [
        constraint
        for theory in problem.local_theories
        for constraint in theory.constraints
    ]
    left_theory = conjunction(constraints)
    right_theory = conjunction(constraints)
    left_text = _to_smt(
        left_theory,
        problem=problem,
        env={},
        copy_symbols=left,
        shared_symbols=shared,
    )
    right_text = _to_smt(
        right_theory,
        problem=problem,
        env={},
        copy_symbols=right,
        shared_symbols=shared,
    )
    target_ground = (problem.target_predicate, tuple(target_arguments))
    target_left = left[target_ground]
    target_right = right[target_ground]
    a_formula = f"(and {left_text} {target_left})"
    b_formula = f"(and {right_text} (not {target_right}))"
    lines = [
        "(set-option :produce-proofs true)",
        "(set-option :produce-interpolants true)",
        "(set-logic QF_UF)",
    ]
    lines.extend(f"(declare-fun {symbol} () Bool)" for symbol in declarations)
    lines.extend(
        [
            f"(assert (! {a_formula} :named A))",
            f"(assert (! {b_formula} :named B))",
            "(check-sat)",
            "(get-interpolants A B)",
            "(exit)",
        ]
    )
    return "\n".join(lines) + "\n", reverse_shared


def _run_query(
    problem: SeamProblem,
    args: tuple[str, ...],
    config: SMTInterpolConfig,
    version: str,
) -> tuple[SolverArtifact, Mapping[str, tuple[str, tuple[str, ...]]]]:
    query, reverse = build_two_copy_query(problem, args)
    completed = subprocess.run(
        [config.java_command, "-jar", str(config.jar_path)],
        input=query,
        capture_output=True,
        text=True,
        timeout=config.timeout_seconds,
        check=False,
    )
    proof_fields: dict[str, Any] = {}
    if config.require_resolute_proof and "unsat" in _sexpressions(completed.stdout):
        proof_fields = _run_resolute_check(query, config)
    return (
        SolverArtifact(
            target_arguments=args,
            input_smt2=query,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
            solver_version=version,
            jar_sha256=config.jar_sha256,
            **proof_fields,
        ),
        reverse,
    )


def _run_resolute_check(query: str, config: SMTInterpolConfig) -> dict[str, Any]:
    """Generate a RESOLUTE refutation and run the jar's separate checker CLI."""
    proof_query = "(set-option :print-success false)\n" + query.replace(
        "(get-interpolants A B)", "(get-proof)"
    )
    with tempfile.TemporaryDirectory(prefix="bulla-smtinterpol-") as directory:
        script_path = Path(directory) / "query.smt2"
        proof_path = Path(directory) / "query.resolute"
        script_path.write_text(proof_query, encoding="utf-8")
        generated = subprocess.run(
            [config.java_command, "-jar", str(config.jar_path), str(script_path)],
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
            check=False,
        )
        proof_path.write_text(generated.stdout, encoding="utf-8")
        checked = subprocess.run(
            [
                config.java_command,
                "-cp",
                str(config.jar_path),
                "de.uni_freiburg.informatik.ultimate.smtinterpol.proof.checker.Main",
                "-q",
                str(script_path),
                str(proof_path),
            ],
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
            check=False,
        )
    verified = (
        generated.returncode == 0
        and checked.returncode == 0
        and checked.stderr.strip() == ""
        and checked.stdout.splitlines()[:1] == ["valid"]
    )
    return {
        "proof_input_smt2": proof_query,
        "proof_stdout": generated.stdout,
        "proof_stderr": generated.stderr,
        "proof_returncode": generated.returncode,
        "proof_checker_stdout": checked.stdout,
        "proof_checker_stderr": checked.stderr,
        "proof_checker_returncode": checked.returncode,
        "resolute_verified": verified,
    }


_TOKEN = re.compile(r"\s*(\(|\)|[^\s()]+)")


def _sexpressions(text: str) -> list[Any]:
    tokens = [m.group(1) for m in _TOKEN.finditer(text)]
    expressions = []
    stack: list[list[Any]] = []
    for token in tokens:
        if token == "(":
            stack.append([])
        elif token == ")":
            if not stack:
                raise ValueError("unbalanced SMT output")
            value = stack.pop()
            if stack:
                stack[-1].append(value)
            else:
                expressions.append(value)
        elif stack:
            stack[-1].append(token)
        else:
            expressions.append(token)
    if stack:
        raise ValueError("unbalanced SMT output")
    return expressions


def _ground_atom_formula(ground: tuple[str, tuple[str, ...]]) -> Formula:
    relation, args = ground
    return atom(relation, (constant(x) for x in args))


def _from_sexpr(
    expression: Any,
    reverse_shared: Mapping[str, tuple[str, tuple[str, ...]]],
) -> Formula:
    if expression == "true":
        return truth()
    if expression == "false":
        return falsity()
    if isinstance(expression, str):
        if expression not in reverse_shared:
            raise ValueError(f"interpolant used non-shared or unknown symbol {expression!r}")
        return _ground_atom_formula(reverse_shared[expression])
    if not isinstance(expression, list) or not expression:
        raise ValueError("invalid interpolant expression")
    # SMTInterpol 2.5 prints a one-interpolant sequence as ``(I)``.  This is
    # sequence framing, not an application of an unknown operator.
    if len(expression) == 1:
        return _from_sexpr(expression[0], reverse_shared)
    op = expression[0]
    if op == "not" and len(expression) == 2:
        return negate(_from_sexpr(expression[1], reverse_shared))
    if op in ("and", "or"):
        children = [_from_sexpr(x, reverse_shared) for x in expression[1:]]
        return conjunction(children) if op == "and" else disjunction(children)
    if op == "=>" and len(expression) == 3:
        return disjunction(
            [
                negate(_from_sexpr(expression[1], reverse_shared)),
                _from_sexpr(expression[2], reverse_shared),
            ]
        )
    if op == "=" and len(expression) == 3:
        left = _from_sexpr(expression[1], reverse_shared)
        right = _from_sexpr(expression[2], reverse_shared)
        return disjunction(
            [
                conjunction([left, right]),
                conjunction([negate(left), negate(right)]),
            ]
        )
    raise ValueError(f"unsupported interpolant form {op!r}")


def parse_interpolant(
    stdout: str,
    reverse_shared: Mapping[str, tuple[str, tuple[str, ...]]],
) -> tuple[str, Formula | None]:
    expressions = _sexpressions(stdout)
    statuses = [x for x in expressions if x in ("sat", "unsat", "unknown")]
    if not statuses:
        raise ValueError("SMTInterpol output contains no check-sat status")
    status = statuses[0]
    if status != "unsat":
        return status, None
    candidates = [
        x
        for x in expressions
        if isinstance(x, list)
        or (isinstance(x, str) and x not in ("sat", "unsat", "unknown", "success"))
    ]
    if not candidates:
        raise ValueError("unsat output contains no interpolant")
    return status, _from_sexpr(candidates[-1], reverse_shared)


def _guard(problem: SeamProblem, args: tuple[str, ...]) -> Formula:
    equalities = []
    for index, (sort, element) in enumerate(zip(problem.target_decl.sorts, args)):
        equalities.append(
            {
                "op": "eq",
                "sort": sort,
                "left": variable(f"x{index}"),
                "right": constant(element),
            }
        )
    return conjunction(equalities)


def _indeterminate(problem: SeamProblem, reason: str, artifacts=()) -> SynthesisResult:
    return SynthesisResult(
        status=SynthesisStatus.INDETERMINATE,
        problem_hash=problem.problem_hash,
        gate_report=GateReport(
            gluing=GateStatus.UNRESOLVED,
            conservativity=GateStatus.UNRESOLVED,
            definability=GateStatus.UNRESOLVED,
            preserved_refusals=GateStatus.UNRESOLVED,
            minimality=GateStatus.UNRESOLVED,
            receipt_binding=GateStatus.NOT_APPLICABLE,
            reasons=(reason,),
        ),
        certificate=FailureCertificate(
            kind=FailureKind.RESOURCE_LIMIT,
            statement=(
                "The SMTInterpol candidate path did not produce an independently "
                "verified package. This is not a mathematical impossibility result."
            ),
            witness={
                "reason": reason,
                "solver_artifacts": [x.to_dict() for x in artifacts],
            },
            backend="smtinterpol",
            complete_within_bound=False,
        ),
        backend="smtinterpol",
        verifier={
            "candidate": "SMTInterpol",
            "checker": "bulla.experimental.invention.reference",
        },
    )


def _reference_fallback(
    problem: SeamProblem,
    reference: SynthesisResult,
    reason: str,
    artifacts: Sequence[SolverArtifact],
) -> SynthesisResult:
    """Preserve a reference negative/choice exit; never credit it to the solver."""
    return dataclasses.replace(
        reference,
        backend="smtinterpol+exhaustive-reference-fallback",
        verifier={
            **dict(reference.verifier),
            "candidate": "SMTInterpol",
            "solver_role": "non-authoritative candidate only",
            "fallback_reason": reason,
            "solver_query_count": len(artifacts),
            "resolute_verified_count": sum(x.resolute_verified for x in artifacts),
            "solver_artifact_hashes": [
                "sha256:"
                + hashlib.sha256(canonical_json(x.to_dict()).encode("utf-8")).hexdigest()
                for x in artifacts
            ],
        },
    )


def synthesize_with_smtinterpol(
    problem: SeamProblem, config: SMTInterpolConfig
) -> SynthesisResult:
    """Extract a finite ground interpolant, then replay it exhaustively."""
    artifacts: list[SolverArtifact] = []
    reference = synthesize(problem) if config.fallback_to_reference else None
    try:
        version = _probe(config)
        domains = [problem.signature.sorts[x] for x in problem.target_decl.sorts]
        guarded_definitions = []
        for args_ in itertools.product(*domains):
            args = tuple(args_)
            artifact, reverse = _run_query(problem, args, config, version)
            artifacts.append(artifact)
            if artifact.returncode != 0:
                if reference is not None and reference.status is not SynthesisStatus.COMPILED:
                    return _reference_fallback(
                        problem,
                        reference,
                        f"SMTInterpol exited {artifact.returncode}",
                        artifacts,
                    )
                return _indeterminate(
                    problem,
                    f"SMTInterpol exited {artifact.returncode}",
                    artifacts,
                )
            if config.require_resolute_proof and not artifact.resolute_verified:
                if reference is not None and reference.status is not SynthesisStatus.COMPILED:
                    return _reference_fallback(
                        problem,
                        reference,
                        "RESOLUTE checker rejected or could not verify the candidate path",
                        artifacts,
                    )
                return _indeterminate(
                    problem,
                    "SMTInterpol RESOLUTE refutation did not pass the pinned checker",
                    artifacts,
                )
            status, interpolant = parse_interpolant(artifact.stdout, reverse)
            if status != "unsat" or interpolant is None:
                if reference is not None and reference.status is not SynthesisStatus.COMPILED:
                    return _reference_fallback(
                        problem,
                        reference,
                        f"two-copy query returned {status}",
                        artifacts,
                    )
                return _indeterminate(
                    problem,
                    f"two-copy query returned {status}; no checked extractor exit",
                    artifacts,
                )
            guarded_definitions.append(conjunction([_guard(problem, args), interpolant]))
        definition = normalize_formula(disjunction(guarded_definitions))
        models = _admissible_models(problem)
        package = _make_package(
            problem,
            mode="full",
            definition=definition,
            rely_when=None,
            refuse_when=None,
            model_count=len(models),
            feature_count=len(_feature_atoms(problem)),
            exact_minimality=False,
        )
        package = dataclasses.replace(
            package,
            verifier={
                "candidate": "SMTInterpol",
                "candidate_version": version,
                "jar_sha256": config.jar_sha256,
                "checker": "bulla.experimental.invention.reference",
            },
            proof_references=tuple(
                {
                    "kind": "smtinterpol-query",
                    "artifact": artifact.to_dict(),
                    "artifact_hash": "sha256:"
                    + hashlib.sha256(
                        canonical_json(artifact.to_dict()).encode("utf-8")
                    ).hexdigest(),
                }
                for artifact in artifacts
            ),
        )
        report = verify_package(problem, package)
        if not (
            report.gluing is GateStatus.PASS
            and report.conservativity is GateStatus.PASS
            and report.definability is GateStatus.PASS
            and report.preserved_refusals is GateStatus.PASS
        ):
            return _indeterminate(
                problem,
                "SMTInterpol candidate failed independent finite verification",
                artifacts,
            )
        if reference is not None and reference.status is not SynthesisStatus.COMPILED:
            return _reference_fallback(
                problem,
                reference,
                (
                    "solver found one full definition, but exhaustive governance "
                    f"classified the problem as {reference.status.value}"
                ),
                artifacts,
            )
        return SynthesisResult(
            status=SynthesisStatus.COMPILED,
            problem_hash=problem.problem_hash,
            gate_report=report,
            package=package,
            backend="smtinterpol+exhaustive-verifier",
            verifier=dict(package.verifier),
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        if reference is not None and reference.status is not SynthesisStatus.COMPILED:
            return _reference_fallback(problem, reference, str(exc), artifacts)
        return _indeterminate(problem, str(exc), artifacts)
