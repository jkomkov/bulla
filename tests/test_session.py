"""Tests for ``bulla.Session`` — incremental composition diagnosis.

The load-bearing artifact in this file is the 10,000-seed property
test in ``TestBitwiseEquality``: every random sequence of tool /
edge additions through the Session API must produce a witness Gram
matrix bitwise-identical to a from-scratch computation by
``witness_gram``. Without this test the math claim that incremental
updates preserve fee semantics is unverified.

Additional tests cover:

  - Receipt chaining via ``parent_receipt_hashes``.
  - ``add_tool`` / ``add_edge`` validation (duplicate names, dangling
    edge endpoints).
  - Disposition tracking (PROCEED vs PROCEED_WITH_BRIDGE).
  - ``diagnose()`` produces a full ``WitnessReceipt``.
  - Empty-session checkpoint behaviour.
"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest

import bulla
from bulla.model import (
    Composition,
    Disposition,
    Edge,
    SemanticDimension,
    ToolSpec,
    WitnessReceipt,
)
from bulla.witness_geometry import fee_from_gram, witness_gram


# ── Property test: bitwise equality across 10k random sequences ─────


def _random_field_name(rng: random.Random, *, used: set[str] | None = None) -> str:
    """Generate a field name unlikely to collide with existing fields."""
    pool = "abcdefghijklmnop"
    while True:
        name = rng.choice(["amount", "currency", "country", "ts", "code",
                           "id", "x", "y", "z", "kind"]) + "_" + rng.choice(pool)
        if used is None or name not in used:
            return name


def _random_tool(
    rng: random.Random, name: str, *, max_fields: int = 4
) -> ToolSpec:
    """Build a random ToolSpec with up to ``max_fields`` fields, of which
    a random subset is observable."""
    n_fields = rng.randint(1, max_fields)
    used: set[str] = set()
    fields: list[str] = []
    for _ in range(n_fields):
        fields.append(_random_field_name(rng, used=used))
        used.add(fields[-1])
    n_obs = rng.randint(0, n_fields)
    rng.shuffle(fields)
    obs = tuple(sorted(fields[:n_obs]))
    return ToolSpec(
        name=name,
        internal_state=tuple(sorted(fields)),
        observable_schema=obs,
    )


def _random_edge_between(
    rng: random.Random,
    tools: list[ToolSpec],
    *,
    used_edge_keys: set[tuple[str, str, str]],
) -> Edge | None:
    """Build a random edge that references existing tools and fields.

    The coboundary builder has a structural invariant: each
    ``(from_tool, to_tool, dim_name)`` tuple appears at most once
    across all edges (rows of δ are keyed by it; duplicates would
    violate the signed-incidence invariant). The ``used_edge_keys``
    set tracks already-allocated tuples so the generator never
    constructs a colliding edge.
    """
    if len(tools) < 2:
        return None
    a, b = rng.sample(tools, 2)
    if not a.internal_state or not b.internal_state:
        return None
    n_dims = rng.randint(1, min(2, len(a.internal_state), len(b.internal_state)))
    dims: list[SemanticDimension] = []
    seen_dim_names: set[str] = set()
    for _ in range(n_dims):
        # Pick a dim name that's not yet used for this (from, to) pair
        # (tracked globally via used_edge_keys), nor duplicated within
        # this single edge.
        for _attempt in range(10):
            name = f"dim_{rng.randint(0, 16)}"
            key = (a.name, b.name, name)
            if name in seen_dim_names or key in used_edge_keys:
                continue
            break
        else:
            # Couldn't find a free dim name in 10 tries; bail.
            continue
        seen_dim_names.add(name)
        used_edge_keys.add(key)
        from_field = rng.choice(a.internal_state)
        to_field = rng.choice(b.internal_state)
        dims.append(
            SemanticDimension(
                name=name,
                from_field=from_field,
                to_field=to_field,
            )
        )
    if not dims:
        return None
    return Edge(a.name, b.name, tuple(dims))


def _random_sequence(rng: random.Random, n_steps: int) -> list[
    tuple[str, ToolSpec | Edge]
]:
    """Generate a sequence of (op, payload) where op is "tool" or "edge".

    Bias toward tools early (need tools before edges) and edges later.
    """
    seq: list[tuple[str, ToolSpec | Edge]] = []
    tool_counter = 0
    tools_so_far: list[ToolSpec] = []
    used_edge_keys: set[tuple[str, str, str]] = set()
    for step in range(n_steps):
        # Probability of edge grows with tool count.
        wants_edge = (
            len(tools_so_far) >= 2
            and rng.random() < 0.5
        )
        if wants_edge:
            edge = _random_edge_between(
                rng, tools_so_far, used_edge_keys=used_edge_keys
            )
            if edge is not None:
                seq.append(("edge", edge))
                continue
        # Fallback: add a tool with a unique name.
        tool_counter += 1
        tool = _random_tool(rng, f"t{tool_counter}")
        seq.append(("tool", tool))
        tools_so_far.append(tool)
    return seq


def _bitwise_equal_gram(
    A: list[list[Fraction]], B: list[list[Fraction]]
) -> bool:
    """Compare two Gram matrices entry-by-entry over Fraction."""
    if len(A) != len(B):
        return False
    for ra, rb in zip(A, B):
        if len(ra) != len(rb):
            return False
        for x, y in zip(ra, rb):
            if x != y:
                return False
    return True


@pytest.mark.parametrize("seed", range(10_000))
def test_session_bitwise_equals_full_rebuild(seed: int) -> None:
    """LOAD-BEARING: 10,000 seeded random tool/edge sequences.

    For every seed, build a Session step by step and assert that the
    accumulated witness Gram matrix and hidden basis are bitwise
    identical to a full rebuild via ``witness_gram`` on the final
    composition. A single mismatch fails CI with the seed printed.

    This is the proof that ``IncrementalDiagnostic.extend`` (the
    Session's substrate) preserves fee semantics under arbitrary
    addition sequences. Without it, the incrementality claim is
    unverified.
    """
    rng = random.Random(seed)
    n_steps = rng.randint(2, 12)
    sequence = _random_sequence(rng, n_steps)

    s = bulla.Session(name=f"prop-seed-{seed}")
    full_tools: list[ToolSpec] = []
    full_edges: list[Edge] = []

    for op, payload in sequence:
        if op == "tool":
            assert isinstance(payload, ToolSpec)
            s.add_tool(payload)
            full_tools.append(payload)
        else:
            assert isinstance(payload, Edge)
            s.add_edge(payload)
            full_edges.append(payload)

        # Compare incremental state vs full rebuild on every step
        # (catches bugs that only manifest on intermediate states).
        K_full, basis_full = witness_gram(full_tools, full_edges)
        assert _bitwise_equal_gram(s._inc._K, K_full), (
            f"seed={seed} step={op}: K diverged from witness_gram\n"
            f"  incremental K={s._inc._K}\n  full K=        {K_full}"
        )
        assert s._inc._hidden_basis == list(basis_full), (
            f"seed={seed} step={op}: hidden_basis diverged"
        )
        assert s.fee == fee_from_gram(K_full), (
            f"seed={seed} step={op}: fee diverged: "
            f"session={s.fee} full={fee_from_gram(K_full)}"
        )


# ── Add-tool / add-edge validation ──────────────────────────────────


class TestAddToolValidation:
    def test_duplicate_tool_name_raises(self):
        s = bulla.Session()
        s.add_tool(ToolSpec("a", ("x",), ("x",)))
        with pytest.raises(ValueError, match="already in session"):
            s.add_tool(ToolSpec("a", ("y",), ("y",)))

    def test_edge_to_unknown_tool_raises(self):
        s = bulla.Session()
        s.add_tool(ToolSpec("a", ("x",), ("x",)))
        with pytest.raises(ValueError, match="not in session"):
            s.add_edge(
                Edge("a", "phantom", (SemanticDimension("d", "x", "x"),))
            )

    def test_edge_from_unknown_tool_raises(self):
        s = bulla.Session()
        s.add_tool(ToolSpec("a", ("x",), ("x",)))
        with pytest.raises(ValueError, match="not in session"):
            s.add_edge(
                Edge("phantom", "a", (SemanticDimension("d", "x", "x"),))
            )

    def test_atomic_batch_addition_succeeds(self):
        s = bulla.Session()
        t1 = ToolSpec("a", ("x",), ("x",))
        t2 = ToolSpec("b", ("y",), ("y",))
        e = Edge("a", "b", (SemanticDimension("d", "x", "y"),))
        result = s.add_tools_and_edges(tools=[t1, t2], edges=[e])
        assert result.fee_after == s.fee
        assert len(s.composition.tools) == 2
        assert len(s.composition.edges) == 1


# ── Receipt chaining ─────────────────────────────────────────────────


class TestReceiptChaining:
    def test_translate_chains_to_latest_receipt(self):
        """``s.translate(...)`` after a checkpoint should chain its
        receipt's ``parent_receipt_hashes`` to the checkpoint."""
        s = bulla.Session()
        s.add_tool(ToolSpec("a", ("x",), ("x",)))
        s.add_tool(ToolSpec("b", ("y",), ("y",)))
        cp = s.checkpoint()
        tr = s.translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
            from_convention="iso-4217",
        )
        assert tr.receipt.parent_receipt_hashes == (cp.receipt_hash,)

    def test_checkpoint_chains_to_latest_translate(self):
        s = bulla.Session()
        s.add_tool(ToolSpec("a", ("x",), ("x",)))
        tr = s.translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
            from_convention="iso-4217",
        )
        cp = s.checkpoint()
        assert cp.parent_receipt_hashes == (tr.receipt.receipt_hash,)

    def test_diagnose_chains_to_latest(self):
        s = bulla.Session()
        s.add_tool(ToolSpec("a", ("x",), ("x",)))
        s.add_tool(ToolSpec("b", ("y",), ("y",)))
        s.add_edge(
            Edge("a", "b", (SemanticDimension("dim", "x", "y"),))
        )
        cp = s.checkpoint()
        final = s.diagnose()
        assert final.parent_receipt_hashes == (cp.receipt_hash,)

    def test_receipt_chain_grows_monotonically(self):
        s = bulla.Session()
        s.add_tool(ToolSpec("a", ("x",), ("x",)))
        s.checkpoint()
        s.checkpoint()
        s.translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
            from_convention="iso-4217",
        )
        s.checkpoint()
        # 3 checkpoints + 1 translate = 4 entries
        assert len(s.receipt_chain) == 4
        # Hashes are pairwise distinct (each binds different state).
        assert len(set(s.receipt_chain)) == 4


# ── Disposition tracking ────────────────────────────────────────────


class TestDispositionTracking:
    def test_proceed_when_fee_zero(self):
        s = bulla.Session()
        s.add_tool(ToolSpec("a", ("x",), ("x",)))
        cp = s.checkpoint()
        assert cp.fee == 0
        assert cp.disposition == Disposition.PROCEED

    def test_proceed_with_bridge_when_blind_spot(self):
        # Mirror the canonical "blind pipeline" pattern: both endpoints
        # carry the same internal-only field, the edge claims that
        # dimension, and neither side exposes it. This is the load-
        # bearing fee>0 case (see test_diagnostic.py::_blind_pipeline).
        s = bulla.Session()
        s.add_tool(
            ToolSpec("provider", ("prices", "day_conv"), ("prices",))
        )
        s.add_tool(
            ToolSpec("analysis", ("result", "day_conv"), ("result",))
        )
        s.add_edge(
            Edge(
                "provider",
                "analysis",
                (SemanticDimension("day_match", "day_conv", "day_conv"),),
            )
        )
        cp = s.checkpoint()
        assert cp.fee >= 1
        assert cp.disposition == Disposition.PROCEED_WITH_BRIDGE


# ── Diagnose() produces a full WitnessReceipt ───────────────────────


class TestDiagnose:
    def test_diagnose_on_empty_returns_proceed(self):
        s = bulla.Session()
        r = s.diagnose()
        assert isinstance(r, WitnessReceipt)
        assert r.disposition == Disposition.PROCEED
        assert r.fee == 0

    def test_diagnose_on_real_composition_runs_full_pipeline(self):
        s = bulla.Session(name="real-comp")
        s.add_tool(ToolSpec("a", ("x", "h"), ("x",)))   # h hidden
        s.add_tool(ToolSpec("b", ("x",), ("x",)))
        s.add_edge(
            Edge("a", "b", (SemanticDimension("dim_x", "x", "x"),))
        )
        r = s.diagnose()
        assert isinstance(r, WitnessReceipt)
        # Full witness pipeline produces an active_packs tuple
        # (even if empty); checkpoint() does not.
        assert r.composition_hash == s.composition.canonical_hash()
        assert r.fee == s.fee


# ── Empty-session edge cases ────────────────────────────────────────


class TestEmptySession:
    def test_fee_starts_zero(self):
        s = bulla.Session()
        assert s.fee == 0

    def test_checkpoint_on_empty_works(self):
        s = bulla.Session()
        cp = s.checkpoint()
        assert isinstance(cp, WitnessReceipt)
        assert cp.fee == 0
        assert cp.parent_receipt_hashes is None

    def test_composition_starts_empty(self):
        s = bulla.Session()
        assert s.composition.tools == ()
        assert s.composition.edges == ()
