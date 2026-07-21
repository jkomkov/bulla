"""SMTInterpol remains an untrusted, pinned candidate source."""

from pathlib import Path

from bulla.experimental.frsl import RelationDecl, Signature, atom, variable
from bulla.experimental.invention import (
    FailureKind,
    LocalTheory,
    SeamProblem,
    SynthesisPolicy,
    SynthesisStatus,
)
from bulla.experimental.smtinterpol import (
    SMTInterpolConfig,
    SolverArtifact,
    build_two_copy_query,
    parse_interpolant,
    synthesize_with_smtinterpol,
)


def _problem() -> SeamProblem:
    x = variable("x")
    constraint = {
        "op": "forall",
        "var": "x",
        "sort": "Item",
        "body": {
            "op": "iff",
            "left": atom("target", (x,)),
            "right": atom("signal", (x,)),
        },
    }
    return SeamProblem(
        problem_id="smt",
        signature=Signature(
            sorts={"Item": ("a",)},
            relations={
                "signal": RelationDecl("signal", ("Item",)),
                "target": RelationDecl("target", ("Item",)),
            },
        ),
        local_theories=(LocalTheory("owner", (constraint,)),),
        overlap_maps=(),
        target_predicate="target",
        shared_vocabulary=("signal",),
        protected_signatures={"owner": ("signal",)},
        requested_judgment="boolean",
        synthesis_policy=SynthesisPolicy(max_candidate_atoms=8),
    )


def test_two_copy_query_shares_only_protected_vocabulary():
    query, reverse = build_two_copy_query(_problem(), ("a",))

    assert "(check-sat)" in query
    assert "(get-interpolants A B)" in query
    assert len(reverse) == 1
    shared_symbol = next(iter(reverse))
    assert query.count(f"(declare-fun {shared_symbol} () Bool)") == 1
    assert "(declare-fun a1 () Bool)" in query
    assert "(declare-fun b1 () Bool)" in query


def test_closed_boolean_interpolant_parser_rejects_private_symbols():
    _, reverse = build_two_copy_query(_problem(), ("a",))
    shared_symbol = next(iter(reverse))
    status, formula = parse_interpolant(f"unsat\n{shared_symbol}\n", reverse)

    assert status == "unsat"
    assert formula == atom("signal", ({"const": "a"},))

    status, wrapped = parse_interpolant(f"unsat\n({shared_symbol})\n", reverse)
    assert status == "unsat"
    assert wrapped == formula


def test_singleton_finite_quantifier_emits_no_unary_boolean_connective():
    query, _ = build_two_copy_query(_problem(), ("a",))

    assert "(assert (! (and (= a1 s0) a1) :named A))" in query
    assert "(assert (! (and (= b1 s0) (not b1)) :named B))" in query
    assert "(and (= a1 s0))" not in query
    assert "(and (= b1 s0))" not in query


def test_missing_solver_is_indeterminate_not_impossibility(tmp_path):
    result = synthesize_with_smtinterpol(
        _problem(),
        SMTInterpolConfig(
            jar_path=tmp_path / "missing.jar",
            jar_sha256="sha256:" + "0" * 64,
            version_contains="SMTInterpol",
        ),
    )

    assert result.status is SynthesisStatus.INDETERMINATE
    assert result.certificate is not None
    assert result.certificate.kind is FailureKind.RESOURCE_LIMIT
    assert result.certificate.complete_within_bound is False


def test_solver_candidate_is_accepted_only_after_reference_replay(
    tmp_path, monkeypatch
):
    problem = _problem()
    query, reverse = build_two_copy_query(problem, ("a",))
    shared_symbol = next(iter(reverse))

    def fake_run(problem_arg, args, config, version):
        assert problem_arg is problem
        return (
            SolverArtifact(
                target_arguments=args,
                input_smt2=query,
                stdout=f"unsat\n{shared_symbol}\n",
                stderr="",
                returncode=0,
                solver_version=version,
                jar_sha256=config.jar_sha256,
                resolute_verified=True,
            ),
            reverse,
        )

    monkeypatch.setattr(
        "bulla.experimental.smtinterpol._probe", lambda config: "SMTInterpol test"
    )
    monkeypatch.setattr("bulla.experimental.smtinterpol._run_query", fake_run)
    result = synthesize_with_smtinterpol(
        problem,
        SMTInterpolConfig(
            jar_path=tmp_path / "unused-by-fake.jar",
            jar_sha256="sha256:" + "0" * 64,
            version_contains="SMTInterpol",
        ),
    )

    assert result.status is SynthesisStatus.COMPILED
    assert result.package is not None
    assert result.package.verifier["candidate"] == "SMTInterpol"
