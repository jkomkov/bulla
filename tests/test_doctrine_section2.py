"""Composition-doctrine §2 worked example: the permanently-executable oracle.

This file pins the §2 worked example from
``papers/composition-doctrine/paper.md`` (v2.3+) and
``papers/composition-doctrine/BET3-WORKPLAN.md`` §2 — a 3-tool cycle with
``timezone`` hidden on T1 (calendar_local) and T2 (meeting_scheduler) and
observable on T3 (notification_sender). The example was hand-derived in
the doctrine paper; its values (fee = 1, K = (3/2) [[1,-1],[-1,1]],
leverage = [1/2, 1/2], basis_greedy = [(calendar_local, timezone)]) are
theorems about Bulla's encoding given the doctrine's definitions.

The file serves a dual role; do not narrow either:

1. **Bet 3 pre-registration tripwire** — BET3-WORKPLAN §5.2(a). Before
   ``sprint_bet3_pre_registration.md`` can be Mirage-locked, this test
   suite must be green. It is the encoding-vs-theory firewall: if it
   fails, the bug is in the Bulla encoding or the agent's wiring (consult
   BET3-WORKPLAN §3.9), not in BABEL and not in the theory. Any failure
   here forbids the lock.

2. **Composition-doctrine §2 paper-math regression gate.** If a future
   edit to ``paper.md`` (e.g., to the seam partition definition, the
   restriction maps, the cellular-sheaf cochain on Γ(G), or any axiom)
   silently breaks the §2 instance, this suite makes the breakage loud.
   The §2 example is now permanently executable: the assertions are the
   math, not a convenience pin.

If you change ``paper.md`` §2 or §3 in a way that contradicts the
assertions below, you are saying the worked example no longer reflects
the doctrine. Either the paper edit is wrong, or §2 needs to be
refreshed in lockstep — and ``BET3-WORKPLAN.md`` §2 with it. **Do not
silence the assertions to make the test pass.** The assertions ARE the
theorem.

Cross-references:
  - ``papers/composition-doctrine/paper.md`` §3.6 (elementary cycle worked example, v2.3 line ~310)
  - ``papers/composition-doctrine/BET3-WORKPLAN.md`` §2 and §5.2(a)
  - Aristotle Lean run for Theorem 5.1 (numerical uniqueness): ``ad67beb2``
  - Aristotle Lean runs for Theorem 8.4 (communication lower bound): ``b84c8f33``, ``3fd02ee6``
"""

from fractions import Fraction

from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.witness import verify_receipt_integrity, witness
from bulla.witness_geometry import compute_profile


def _section2_composition() -> Composition:
    """The §2 worked example: 3-tool cycle, timezone hidden on T1, T2.

    T1 (calendar_local) → T2 (meeting_scheduler) → T3 (notification_sender) → T1.
    Each edge declares one SemanticDimension on ``timezone``. T3 exposes
    ``timezone`` in its observable_schema; T1 and T2 do not.
    """
    T1 = ToolSpec(
        "calendar_local",
        internal_state=("timezone",),
        observable_schema=(),
    )
    T2 = ToolSpec(
        "meeting_scheduler",
        internal_state=("timezone",),
        observable_schema=(),
    )
    T3 = ToolSpec(
        "notification_sender",
        internal_state=("timezone",),
        observable_schema=("timezone",),
    )
    e0 = Edge(
        "calendar_local",
        "meeting_scheduler",
        (SemanticDimension("tz", "timezone", "timezone"),),
    )
    e1 = Edge(
        "meeting_scheduler",
        "notification_sender",
        (SemanticDimension("tz", "timezone", "timezone"),),
    )
    e2 = Edge(
        "notification_sender",
        "calendar_local",
        (SemanticDimension("tz", "timezone", "timezone"),),
    )
    return Composition("doctrine_section2_cycle", tools=(T1, T2, T3), edges=(e0, e1, e2))


def _section2_repaired() -> Composition:
    """The same composition after disclosing ``(calendar_local, timezone)``."""
    base = _section2_composition()
    T1_repaired = ToolSpec(
        "calendar_local",
        internal_state=("timezone",),
        observable_schema=("timezone",),
    )
    return Composition(base.name, (T1_repaired, base.tools[1], base.tools[2]), base.edges)


# ── Headline assertions: compute_profile reproduces the hand-derived §2 values ──


def test_section2_witness_rank_is_one() -> None:
    """r(G) = 1 by hand-derivation in paper.md §3.6 / BET3-WORKPLAN §2 step 1."""
    p = compute_profile(list(_section2_composition().tools), list(_section2_composition().edges))
    assert p.fee == 1


def test_section2_hidden_basis_is_T1_and_T2() -> None:
    """The two hidden columns of δ_full are timezone on T1 and T2 (T3 is observable)."""
    p = compute_profile(list(_section2_composition().tools), list(_section2_composition().edges))
    assert p.hidden_basis == [
        ("calendar_local", "timezone"),
        ("meeting_scheduler", "timezone"),
    ]


def test_section2_leverage_is_one_half_each() -> None:
    """Both hidden fields have leverage 1/2 — neither is a coloop, neither is a loop.

    By the Schur complement / Kron reduction (paper.md Lemma 3.9): K = (3/2) * [[1,-1],[-1,1]],
    K^+ K = (1/2) * [[1,-1],[-1,1]], so the diagonal (leverage scores) is [1/2, 1/2].
    Sum of leverages = fee = 1 (always; this is the matroid-rank identity).
    """
    p = compute_profile(list(_section2_composition().tools), list(_section2_composition().edges))
    assert p.leverage == [Fraction(1, 2), Fraction(1, 2)]
    assert sum(p.leverage) == p.fee
    assert p.coloops == []
    assert p.loops == []


def test_section2_witness_gram_matches_hand_computation() -> None:
    """K(G) = (3/2) * [[1, -1], [-1, 1]] exactly, over Q, no floats.

    Derivation in BET3-WORKPLAN §2 step 2: H = [[-1,+1],[0,-1],[+1,0]],
    P_O projects onto span{(0,1,-1)^T}/2; (I - P_O)H = [[-1,1],[1/2,-1/2],[1/2,-1/2]];
    K = W^T W = [[3/2, -3/2], [-3/2, 3/2]].
    """
    p = compute_profile(list(_section2_composition().tools), list(_section2_composition().edges))
    assert p.K == [
        [Fraction(3, 2), Fraction(-3, 2)],
        [Fraction(-3, 2), Fraction(3, 2)],
    ]


def test_section2_matroid_greedy_picks_T1_by_tiebreak() -> None:
    """Under unit costs, the matroid greedy picks (calendar_local, timezone) by name tie-break.

    Both hidden fields are equivalent under the matroid (leverage 1/2 each); the greedy
    breaks ties by iteration order, which is alphabetic on tool name. ``calendar_local`` < ``meeting_scheduler``.
    """
    p = compute_profile(list(_section2_composition().tools), list(_section2_composition().edges))
    assert p.basis_greedy == [("calendar_local", "timezone")]
    assert len(p.basis_greedy) == p.fee


# ── Cross-check: diagnose(..., include_witness_geometry=True) surfaces the same values ──


def test_section2_diagnose_coherence_fee() -> None:
    d = diagnose(_section2_composition(), include_witness_geometry=True)
    assert d.coherence_fee == 1
    # Diagnostic identity: coherence_fee = h1_obs - h1_full = rank_full - rank_obs
    assert d.h1_obs == 2
    assert d.h1_full == 1
    assert d.rank_obs == 1
    assert d.rank_full == 2
    assert d.coherence_fee == d.h1_obs - d.h1_full
    assert d.coherence_fee == d.rank_full - d.rank_obs


def test_section2_diagnose_witness_geometry_fields() -> None:
    """diagnose() with include_witness_geometry=True populates the same fields as compute_profile."""
    d = diagnose(_section2_composition(), include_witness_geometry=True)
    assert d.hidden_basis == (
        ("calendar_local", "timezone"),
        ("meeting_scheduler", "timezone"),
    )
    assert d.leverage_scores == (Fraction(1, 2), Fraction(1, 2))
    assert d.coloops == ()
    assert d.loops == ()
    assert d.disclosure_set == (("calendar_local", "timezone"),)


# ── Repair turn: one disclosure to T1.timezone takes fee from 1 to 0 ──


def test_section2_repair_brings_fee_to_zero() -> None:
    """Disclosing ``(calendar_local, timezone)`` reduces fee from 1 to 0.

    This is one full repair turn, exhibiting Theorem 6.2 (repair duality):
    minimum repair cardinality = r(G) = 1.
    """
    d_after = diagnose(_section2_repaired(), include_witness_geometry=True)
    assert d_after.coherence_fee == 0
    assert d_after.hidden_basis == ()
    assert d_after.disclosure_set == ()


# ── Receipt chain: before/after both validate; after is parented to before ──


def test_section2_receipt_before_disposition() -> None:
    """The pre-repair receipt has fee=1 and refuses pending disclosure (default policy)."""
    diag_before = diagnose(_section2_composition(), include_witness_geometry=True)
    receipt_before = witness(diag_before, _section2_composition())
    assert receipt_before.fee == 1
    assert receipt_before.disposition.value == "refuse_pending_disclosure"
    assert verify_receipt_integrity(receipt_before.to_dict())


def test_section2_receipt_after_disposition() -> None:
    """The post-repair receipt has fee=0 and proceeds with bridge.

    The disposition is ``proceed_with_bridge`` rather than ``proceed`` because
    T2's hidden timezone still produces blind-spot bridges (informational only;
    the matroid basis is empty so the obstruction is gone — see BET3-WORKPLAN
    §2 step 6 commentary on ``bridges_required`` vs matroid cardinality).
    """
    diag_after = diagnose(_section2_repaired(), include_witness_geometry=True)
    receipt_after = witness(diag_after, _section2_repaired())
    assert receipt_after.fee == 0
    assert receipt_after.disposition.value == "proceed_with_bridge"
    assert verify_receipt_integrity(receipt_after.to_dict())


def test_section2_receipt_chain_parentage() -> None:
    """The post-repair receipt records the pre-repair receipt as its parent.

    This pairing is the operational realization of Theorem 7.1 (disclosure normal
    form): the pre-repair receipt records G_0 and the cocycle-class to disclose;
    the post-repair receipt records G_k with vanishing obstruction; the chain is
    auditable end-to-end via receipt_hash content addressing.
    """
    diag_before = diagnose(_section2_composition(), include_witness_geometry=True)
    receipt_before = witness(diag_before, _section2_composition())
    diag_after = diagnose(_section2_repaired(), include_witness_geometry=True)
    receipt_after = witness(
        diag_after,
        _section2_repaired(),
        parent_receipt_hashes=(receipt_before.receipt_hash,),
    )
    assert receipt_after.parent_receipt_hashes == (receipt_before.receipt_hash,)
    assert verify_receipt_integrity(receipt_before.to_dict())
    assert verify_receipt_integrity(receipt_after.to_dict())
    # composition_hash differs: G_0 ≠ G_k (T1.observable_schema differs)
    assert receipt_before.composition_hash != receipt_after.composition_hash


# ── Rank spectrum coverage: r=0 (trivial) and r=2 (the iteration case) ──
#
# The r=1 fixture above exercises one-shot repair and is the headline. r=0 and
# r=2 cover the rest of the prediction surface:
#
#   - r=0 catches the "no obstruction, no repair, no questions" no-op path.
#   - r=2 catches the "agent must iterate across distinct cocycle generators"
#     case. This is structurally distinct from r=1: a repair agent that
#     correctly handles r=1 may still fail r=2 if its basis-recompute or
#     state-update logic is wrong. See BET3-WORKPLAN §3.9 failure mode 3
#     ("agent loops with same basis re-emerging"); r=2 is what tests it.


def _section2_r0() -> Composition:
    """§2 cycle with ``timezone`` observable on all three tools. Expected r(G)=0."""
    T1 = ToolSpec("calendar_local",      ("timezone",), ("timezone",))
    T2 = ToolSpec("meeting_scheduler",   ("timezone",), ("timezone",))
    T3 = ToolSpec("notification_sender", ("timezone",), ("timezone",))
    e0 = Edge("calendar_local",      "meeting_scheduler",   (SemanticDimension("tz", "timezone", "timezone"),))
    e1 = Edge("meeting_scheduler",   "notification_sender", (SemanticDimension("tz", "timezone", "timezone"),))
    e2 = Edge("notification_sender", "calendar_local",      (SemanticDimension("tz", "timezone", "timezone"),))
    return Composition("doctrine_section2_r0", tools=(T1, T2, T3), edges=(e0, e1, e2))


def _section2_r2() -> Composition:
    """§2 cycle extended with a second hidden convention (``locale``). Expected r(G)=2.

    Both ``timezone`` and ``locale`` are hidden on T1 and T2, observable on T3.
    Each edge declares two semantic dimensions (one per convention). The two
    fields are independent (no cross-coupling), so the seam complex decomposes
    into two copies of the §2 r=1 case — fee = 1 + 1 = 2.
    """
    T1 = ToolSpec("calendar_local",      ("locale", "timezone"), ())
    T2 = ToolSpec("meeting_scheduler",   ("locale", "timezone"), ())
    T3 = ToolSpec("notification_sender", ("locale", "timezone"), ("locale", "timezone"))

    def _edge(src: str, dst: str) -> Edge:
        return Edge(src, dst, (
            SemanticDimension("loc", "locale", "locale"),
            SemanticDimension("tz", "timezone", "timezone"),
        ))
    return Composition(
        "doctrine_section2_r2",
        tools=(T1, T2, T3),
        edges=(
            _edge("calendar_local", "meeting_scheduler"),
            _edge("meeting_scheduler", "notification_sender"),
            _edge("notification_sender", "calendar_local"),
        ),
    )


def _section2_r2_after_first_disclosure() -> Composition:
    """r=2 cycle after disclosing ``(calendar_local, locale)``. Expected r(G)=1."""
    base = _section2_r2()
    T1_p = ToolSpec("calendar_local", ("locale", "timezone"), ("locale",))
    return Composition(base.name, (T1_p, base.tools[1], base.tools[2]), base.edges)


def _section2_r2_after_second_disclosure() -> Composition:
    """r=2 cycle after disclosing both ``(calendar_local, locale)`` and
    ``(calendar_local, timezone)``. Expected r(G)=0."""
    base = _section2_r2()
    T1_p = ToolSpec("calendar_local", ("locale", "timezone"), ("locale", "timezone"))
    return Composition(base.name, (T1_p, base.tools[1], base.tools[2]), base.edges)


# r=0: trivial no-op


def test_r0_no_obstruction() -> None:
    """Fully observable cycle: fee=0, empty hidden_basis, empty greedy."""
    p = compute_profile(list(_section2_r0().tools), list(_section2_r0().edges))
    assert p.fee == 0
    assert p.hidden_basis == []
    assert p.leverage == []
    assert p.coloops == []
    assert p.loops == []
    assert p.basis_greedy == []


def test_r0_diagnose_matches_no_obstruction() -> None:
    """diagnose() agrees: fee=0; the cycle still has β_1=1 but it is captured equally
    by δ_obs and δ_full, so the obstruction (the *difference*) is zero."""
    d = diagnose(_section2_r0(), include_witness_geometry=True)
    assert d.coherence_fee == 0
    assert d.rank_obs == d.rank_full
    assert d.h1_obs == d.h1_full   # cycle β_1 carried equally
    assert d.disclosure_set == ()


# r=2: the iteration case — agent must repair across two cocycle generators


def test_r2_witness_rank_is_two() -> None:
    """Two independent hidden conventions ⇒ fee = 2 (paper.md Lemma 3.5: additivity over disjoint blocks)."""
    p = compute_profile(list(_section2_r2().tools), list(_section2_r2().edges))
    assert p.fee == 2


def test_r2_hidden_basis_has_four_elements() -> None:
    """Four hidden columns of δ_full: locale and timezone on each of T1, T2.

    Order is alphabetic by (tool, field): T1 = calendar_local before T2 =
    meeting_scheduler; locale before timezone within each tool.
    """
    p = compute_profile(list(_section2_r2().tools), list(_section2_r2().edges))
    assert p.hidden_basis == [
        ("calendar_local", "locale"),
        ("calendar_local", "timezone"),
        ("meeting_scheduler", "locale"),
        ("meeting_scheduler", "timezone"),
    ]


def test_r2_all_leverages_are_one_half_sum_to_fee() -> None:
    """By symmetry across the two independent blocks, each hidden field has leverage 1/2.

    Sum of leverages = fee = 2 (the matroid-rank identity holds across blocks).
    """
    p = compute_profile(list(_section2_r2().tools), list(_section2_r2().edges))
    assert p.leverage == [Fraction(1, 2)] * 4
    assert sum(p.leverage) == p.fee
    assert p.coloops == []
    assert p.loops == []


def test_r2_matroid_greedy_picks_two_by_tiebreak() -> None:
    """The greedy iterates in v_basis order and picks fields until rank reaches fee.

    Both picks land on T1 because T1's columns appear before T2's in v_basis order.
    Within T1, ``locale`` is picked before ``timezone`` (alphabetic on field).
    """
    p = compute_profile(list(_section2_r2().tools), list(_section2_r2().edges))
    assert p.basis_greedy == [
        ("calendar_local", "locale"),
        ("calendar_local", "timezone"),
    ]
    assert len(p.basis_greedy) == p.fee


def test_r2_first_disclosure_drops_fee_to_one_not_zero() -> None:
    """**Iteration case load-bearing assertion.** After one disclosure, fee = 1.

    A repair agent that stops here returns an incoherent composition. The agent's
    invariant (BET3-WORKPLAN §3.6) is "fee monotone non-increasing across confirmed
    turns," which means it MUST recompute compute_profile after each apply and
    re-plan. This test catches the §3.9 failure mode 3 ("agent loops with same
    basis re-emerging") if recomputation is broken.
    """
    p = compute_profile(
        list(_section2_r2_after_first_disclosure().tools),
        list(_section2_r2_after_first_disclosure().edges),
    )
    assert p.fee == 1
    # The remaining hidden_basis no longer contains (calendar_local, locale)
    assert ("calendar_local", "locale") not in p.hidden_basis
    # But it still contains (calendar_local, timezone) AND the meeting_scheduler entries
    assert ("calendar_local", "timezone") in p.hidden_basis


def test_r2_second_disclosure_drops_fee_to_zero() -> None:
    """After both disclosures, fee = 0. Theorem 6.2: minimum repair cardinality = r(G) = 2.

    **Subtle fact worth internalizing.** T2's ``locale`` and ``timezone`` are still
    hidden after the disclosures (T2.observable_schema is unchanged). Yet fee = 0.
    These fields are now *loops* in the witness matroid: leverage = 0, redundant —
    disclosing them would change nothing. The cycle is already closed by T1's
    two disclosures.

    "fee = 0" does not mean "all fields observable." It means "the obstruction
    matroid is empty," equivalently "the cycle closes through observable rows."
    A repair agent that mistakenly tries to keep disclosing T2's fields after
    fee = 0 is asking redundant questions and burning user budget; this is why
    the agent must check ``fee`` between turns, not just iterate over all hidden
    fields.
    """
    p = compute_profile(
        list(_section2_r2_after_second_disclosure().tools),
        list(_section2_r2_after_second_disclosure().edges),
    )
    assert p.fee == 0
    # The matroid basis is empty — no further disclosures are needed.
    assert p.basis_greedy == []
    # But T2's still-hidden fields appear in hidden_basis as loops (leverage 0):
    # they survive in the matroid view but contribute zero to the obstruction.
    assert p.hidden_basis == [
        ("meeting_scheduler", "locale"),
        ("meeting_scheduler", "timezone"),
    ]
    assert p.leverage == [Fraction(0), Fraction(0)]
    assert p.loops == [
        ("meeting_scheduler", "locale"),
        ("meeting_scheduler", "timezone"),
    ]
    assert p.coloops == []
    # Sum-of-leverages identity holds even at fee = 0:
    assert sum(p.leverage) == p.fee == 0


def test_r2_iteration_picks_distinct_generators() -> None:
    """Across two repair turns, the agent must pick two distinct (tool, field) targets.

    Picking the same target twice means the disclosure didn't take effect in the
    composition state (failure mode 3 in BET3-WORKPLAN §3.9). This test asserts
    that after applying the first greedy pick, the second greedy pick on the
    repaired composition is a *different* (tool, field).
    """
    p1 = compute_profile(list(_section2_r2().tools), list(_section2_r2().edges))
    first_pick = p1.basis_greedy[0]
    repaired = _section2_r2_after_first_disclosure()
    p2 = compute_profile(list(repaired.tools), list(repaired.edges))
    second_pick = p2.basis_greedy[0]
    assert first_pick != second_pick
    assert first_pick == ("calendar_local", "locale")
    assert second_pick == ("calendar_local", "timezone")


def test_r2_receipt_chain_across_two_turns() -> None:
    """End-to-end receipt chain across the full r=2 → r=1 → r=0 trajectory.

    Three receipts, each parented to the previous, all validating. This is
    Theorem 7.1 (receipt as disclosure normal form) on a multi-turn instance.
    """
    diag_0 = diagnose(_section2_r2(), include_witness_geometry=True)
    r0 = witness(diag_0, _section2_r2())
    assert r0.fee == 2
    assert verify_receipt_integrity(r0.to_dict())

    diag_1 = diagnose(_section2_r2_after_first_disclosure(), include_witness_geometry=True)
    r1 = witness(
        diag_1,
        _section2_r2_after_first_disclosure(),
        parent_receipt_hashes=(r0.receipt_hash,),
    )
    assert r1.fee == 1
    assert r1.parent_receipt_hashes == (r0.receipt_hash,)
    assert verify_receipt_integrity(r1.to_dict())

    diag_2 = diagnose(_section2_r2_after_second_disclosure(), include_witness_geometry=True)
    r2 = witness(
        diag_2,
        _section2_r2_after_second_disclosure(),
        parent_receipt_hashes=(r1.receipt_hash,),
    )
    assert r2.fee == 0
    assert r2.parent_receipt_hashes == (r1.receipt_hash,)
    assert verify_receipt_integrity(r2.to_dict())

    # Three distinct composition hashes — the receipts trace a real trajectory
    assert len({r0.composition_hash, r1.composition_hash, r2.composition_hash}) == 3
