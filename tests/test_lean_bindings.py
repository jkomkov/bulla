"""Property tests binding the TarskiCoherence Lean theorems to shipped code.

Each test names the Lean theorem whose CODE-LEVEL identification it
demonstrates (papers/tarski-coherence/lean/TarskiCoherence/). Per the sprint's
honest-labeling contract these upgrade the bindings from `asserted` to
`demonstrated (property-tested)` in lean/LEAN-FILE-CONVENTION.md — they never
make the code "verified": the theorems are verified for their abstractions;
these tests demonstrate the shipped operators satisfy those abstractions'
hypotheses on real runs.

Bindings:
  f = one ``coordination_step`` round (state: the observable-field set of the
      working composition), φ = ``diagnose(...).coherence_fee``,
  M = the quotient witness matroid behind ``minimum_disclosure_set``.

Extends (does not duplicate) existing coverage: monotone fee descent is
test_sprint27.py::test_convergence_invariant_monotonic; single-round fee drop
is test_sprint26.py::test_collective_invariant_fee_drops. New here: disclosure
⊆-growth, strict-descent-on-confirming-rounds, the ≤-initial-fee round bound,
and the disclosure-set-is-a-matroid-basis check.

A FAILING property here is a finding (a real divergence between code and
theorem hypotheses) — bank it, never weaken the assertion silently.

Helpers copied (attributed) from test_sprint27.py to stay self-contained.
"""
from __future__ import annotations

import dataclasses
import re

from bulla.diagnostic import diagnose, minimum_disclosure_set
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.repair import coordination_step


# ── helpers (copied from tests/test_sprint27.py — keep in sync) ──────────────
def _fee2_composition() -> tuple[Composition, list[frozenset[str]], list[dict]]:
    """Fee-2 composition: two independent hidden dimensions across two seams."""
    alpha = (
        ToolSpec("alpha__read", ("path", "encoding"), ("path",)),
        ToolSpec("alpha__write", ("path", "mode"), ("path",)),
    )
    beta = (
        ToolSpec("beta__fetch", ("url", "timeout"), ("url",)),
        ToolSpec("beta__post", ("url", "payload"), ("url",)),
    )
    edges = (
        Edge("alpha__read", "beta__fetch",
             (SemanticDimension("transport", "encoding", "timeout"),)),
        Edge("alpha__write", "beta__post",
             (SemanticDimension("protocol", "mode", "payload"),)),
    )
    comp = Composition("lean-bindings-fee2", alpha + beta, edges)
    partition = [frozenset(t.name for t in alpha), frozenset(t.name for t in beta)]
    tool_dicts = []
    for t in alpha + beta:
        props: dict = {}
        for f in t.internal_state:
            props[f] = {"type": "string"}
            if f in t.observable_schema:
                props[f]["description"] = f"Observable: {f}"
        tool_dicts.append({
            "name": t.name,
            "description": f"Tool {t.name}",
            "inputSchema": {"type": "object", "properties": props},
        })
    return comp, partition, tool_dicts


class DimensionAwareMockAdapter:
    """Deterministic adapter confirming one new dimension per round
    (copied from test_sprint27.py)."""

    def __init__(self) -> None:
        self._confirmed_dims: set[str] = set()

    def complete(self, prompt: str) -> str:
        n_obls = len(re.findall(r"OBLIGATION \d+:", prompt))
        if n_obls == 0:
            return ""
        dims: list[str] = []
        for idx in range(1, n_obls + 1):
            m = re.search(rf"OBLIGATION {idx}:.*?Dimension:\s*(\S+)", prompt, re.DOTALL)
            dims.append(m.group(1) if m else "")
        confirmed_this_round = False
        blocks = []
        for idx in range(1, n_obls + 1):
            dim = dims[idx - 1]
            if not confirmed_this_round and dim not in self._confirmed_dims:
                self._confirmed_dims.add(dim)
                confirmed_this_round = True
                blocks.append(
                    f"---BEGIN_VERDICT_{idx}---\nverdict: CONFIRMED\n"
                    f"evidence: field is observable\nconvention_value: standard\n"
                    f"---END_VERDICT_{idx}---")
            else:
                blocks.append(
                    f"---BEGIN_VERDICT_{idx}---\nverdict: UNCERTAIN\n"
                    f"evidence: cannot determine\nconvention_value:\n"
                    f"---END_VERDICT_{idx}---")
        return "\n\n".join(blocks)


def _observables(comp: Composition) -> set[tuple[str, str]]:
    return {(t.name, f) for t in comp.tools for f in t.observable_schema}


def _run() -> tuple:
    comp, partition, tool_dicts = _fee2_composition()
    result = coordination_step(
        comp, partition, tool_dicts, DimensionAwareMockAdapter(), max_rounds=10)
    return comp, result


# ── the bindings ─────────────────────────────────────────────────────────────
def test_disclosures_inflationary_across_rounds():
    """Binds TarskiCoherence.CureLoop.Inflationary / orbit_monotone:
    the shipped round operator only ADDS observable fields — the state chain
    initial ⊆ round₀ ⊆ round₁ ⊆ … is exactly the theorem's hypothesis
    `∀ s, s ⊆ f s` instantiated at the realized orbit."""
    comp, result = _run()
    chain = [_observables(comp)] + [_observables(r.repaired_comp) for r in result.rounds]
    for k in range(1, len(chain)):
        assert chain[k - 1] <= chain[k], (
            f"disclosure set SHRANK at round {k}: "
            f"{sorted(chain[k - 1] - chain[k])} disappeared — "
            "violates the Inflationary hypothesis of CureLoop.orbit_monotone")


def test_fee_strictly_decreases_on_confirming_rounds():
    """Binds TarskiCoherence.CureLoop.exists_fixed_le_fee's descent hypothesis
    (∀ s, f s ≠ s → φ (f s) < φ s): every round that confirms ≥ 1 obligation
    strictly lowers the fee; non-confirming rounds never raise it. (The weak
    monotone version is already covered by test_sprint27 — this is the STRICT
    clause the Lean hypothesis needs.)"""
    _, result = _run()
    for k, rnd in enumerate(result.rounds):
        if rnd.confirmed_count >= 1:
            assert rnd.repaired_fee < rnd.original_fee, (
                f"confirming round {k} did not strictly decrease the fee "
                f"({rnd.original_fee} → {rnd.repaired_fee}) — violates the "
                "descent hypothesis of CureLoop.exists_fixed_le_fee")
        else:
            assert rnd.repaired_fee <= rnd.original_fee


def test_termination_within_initial_fee_confirming_rounds():
    """Binds TarskiCoherence.CureLoop.exists_fixed_le_fee's conclusion
    (a fixed point within φ ∅ steps): the number of CONFIRMING rounds is
    bounded by the initial fee, and the loop reaches fee 0 here."""
    comp, result = _run()
    initial_fee = diagnose(comp).coherence_fee
    confirming = sum(1 for r in result.rounds if r.confirmed_count >= 1)
    assert confirming <= initial_fee, (
        f"{confirming} confirming rounds > initial fee {initial_fee} — "
        "violates the round bound of CureLoop.exists_fixed_le_fee")
    assert result.termination_reason == "fee_zero"
    assert result.final_fee == 0


def test_minimum_disclosure_set_is_matroid_basis():
    """Binds TarskiCoherence.DisclosureNuclei.Sufficient +
    minimal_sufficient_iff_isBase: the shipped greedy's output D is
    (a) SUFFICIENT — disclosing D drives the fee to 0 — and
    (b) MINIMAL — dropping any single element leaves the fee positive —
    i.e. D is a basis of the quotient witness matroid, the Minimal-⟺-IsBase
    direction realized on shipped code."""
    comp, _, _ = _fee2_composition()

    def disclose(c: Composition, pairs: set[tuple[str, str]]) -> Composition:
        tools = tuple(
            dataclasses.replace(
                t, observable_schema=tuple(
                    dict.fromkeys(t.observable_schema +
                                  tuple(f for (tn, f) in pairs if tn == t.name))))
            for t in c.tools)
        return dataclasses.replace(c, tools=tools)

    D = set(minimum_disclosure_set(comp))
    assert D, "expected a nonempty disclosure set on the fee-2 composition"
    # (a) sufficiency
    assert diagnose(disclose(comp, D)).coherence_fee == 0, (
        "minimum_disclosure_set is not sufficient — disclosing it does not "
        "drive the fee to 0 (violates DisclosureNuclei.Sufficient)")
    # (b) minimality: every proper subset is insufficient
    for drop in D:
        rest = D - {drop}
        assert diagnose(disclose(comp, rest)).coherence_fee > 0, (
            f"dropping {drop} still yields fee 0 — the greedy output is not "
            "minimal (violates minimal_sufficient_iff_isBase: a basis has no "
            "spanning proper subset)")
