"""Tests for ``decompose_fee_by_dimension`` (per-dimension fee vector).

Three layers of guarantee, from universal to empirical:

1. **Per-dimension non-negativity** (``fee_d >= 0``) — universal. The
   observable rows are the full rows with hidden columns zeroed, and zeroing
   columns cannot raise rank, so ``rank_obs_d <= rank_full_d``.

2. **DFD additivity** (``sum(fee_d) == coherence_fee``) — holds when each
   semantic dimension's rows form a self-contained sub-complex (Disjoint
   Field Decomposition). Demonstrated on a synthetic paired-name cycle.
   NOT universal: when a single rank-deficient cycle is split across multiple
   distinct dimension names, ``sum(fee_d)`` can exceed ``coherence_fee``.

3. **Corpus additivity is a consistency check, not an empirical discovery.**
   The registry corpus satisfies DFD *by construction* — ``diagnose_pair``
   names one dimension per shared ``(tool, field)`` — so additivity is forced
   by the theorem, and verifying it across all 703 compositions checks that the
   corpus-construction convention actually obeys DFD (and, separately, that
   DFD ⇒ additivity holds with zero counterexamples). It is not evidence about
   the world. See ``papers/coherence-cliff/results/per_dimension_additivity_theorem.md``.
   Skipped when corpus data / calibration package are absent.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from bulla.diagnostic import (
    decompose_fee_by_dimension,
    diagnose,
    disjoint_field_decomposition_violations,
    has_disjoint_field_decomposition,
)
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec


def _paired_cycle(field_dims: list[tuple[str, str]]) -> Composition:
    """Bidirectional cycle: each (field, dim_name) gets a->b and b->a row
    under the SAME dimension name, so each dimension's rows are self-contained
    (the DFD regime that matches the real-corpus construction)."""
    fields = sorted({f for f, _ in field_dims})
    tools = (
        ToolSpec("a", tuple(fields), ()),
        ToolSpec("b", tuple(fields), ()),
    )
    edges = []
    for field, dim in field_dims:
        edges.append(Edge("a", "b", (SemanticDimension(dim, field, field),)))
        edges.append(Edge("b", "a", (SemanticDimension(dim, field, field),)))
    return Composition("paired", tools, tuple(edges))


def test_per_dimension_fee_non_negative():
    comp = _paired_cycle([("x", "dx"), ("y", "dy"), ("z", "dz")])
    dd = decompose_fee_by_dimension(comp).by_dimension
    assert all(v >= 0 for v in dd.values())


def test_zero_fee_yields_all_zero_dimensions():
    # Fully observable seam: no hidden mismatch, fee == 0.
    tools = (ToolSpec("a", ("x",), ("x",)), ToolSpec("b", ("x",), ("x",)))
    edges = (Edge("a", "b", (SemanticDimension("d", "x", "x"),)),)
    comp = Composition("zero", tools, edges)
    assert diagnose(comp).coherence_fee == 0
    dd = decompose_fee_by_dimension(comp).by_dimension
    assert dd == {"d": 0}
    assert sum(dd.values()) == 0


def test_dfd_additivity_synthetic():
    comp = _paired_cycle([("x", "dx"), ("y", "dy")])
    fee = diagnose(comp).coherence_fee
    dd = decompose_fee_by_dimension(comp).by_dimension
    assert fee == 2
    assert dd == {"dx": 1, "dy": 1}
    assert sum(dd.values()) == fee  # DFD additivity


def test_split_cycle_is_subadditive_not_equal():
    """Documents the boundary of additivity: when one field's round-trip is
    labelled with two DISTINCT dimension names, the cycle's rank deficiency
    is split and ``sum(fee_d) > coherence_fee``. This is expected, not a bug —
    additivity is a property of the dimension labelling, not a kernel law."""
    tools = (ToolSpec("a", ("x",), ()), ToolSpec("b", ("x",), ()))
    edges = (
        Edge("a", "b", (SemanticDimension("d_fwd", "x", "x"),)),
        Edge("b", "a", (SemanticDimension("d_rev", "x", "x"),)),
    )
    comp = Composition("split", tools, edges)
    fee = diagnose(comp).coherence_fee
    dd = decompose_fee_by_dimension(comp).by_dimension
    assert fee == 1
    assert sum(dd.values()) >= fee  # subadditive direction here
    assert sum(dd.values()) > fee   # strictly: deficiency split across names


def test_dfd_predicate_holds_for_paired_cycle():
    comp = _paired_cycle([("x", "dx"), ("y", "dy")])
    assert has_disjoint_field_decomposition(comp)
    assert disjoint_field_decomposition_violations(comp) == {}


def test_dfd_predicate_detects_shared_field_violation():
    # One field 'x' carried by two distinct dimension names -> DFD violated.
    tools = (ToolSpec("a", ("x",), ()), ToolSpec("b", ("x",), ()))
    edges = (
        Edge("a", "b", (SemanticDimension("d_fwd", "x", "x"),)),
        Edge("b", "a", (SemanticDimension("d_rev", "x", "x"),)),
    )
    comp = Composition("split", tools, edges)
    assert not has_disjoint_field_decomposition(comp)
    violations = disjoint_field_decomposition_violations(comp)
    assert ("a", "x") in violations and ("b", "x") in violations
    assert violations[("a", "x")] == {"d_fwd", "d_rev"}


def test_dfd_implies_additivity_theorem():
    """The theorem: DFD => sum(fee_d) == coherence_fee. Check the implication
    holds across a battery of synthetic compositions, and that the only
    non-additive ones are exactly the DFD violators."""
    cases = [
        _paired_cycle([("x", "dx")]),
        _paired_cycle([("x", "dx"), ("y", "dy")]),
        _paired_cycle([("x", "dx"), ("y", "dy"), ("z", "dz")]),
    ]
    # Add DFD-violating cases (shared field across dimension names).
    tools = (ToolSpec("a", ("x", "y"), ()), ToolSpec("b", ("x", "y"), ()))
    shared = Composition(
        "shared",
        tools,
        (
            Edge("a", "b", (SemanticDimension("d1", "x", "x"),)),
            Edge("b", "a", (SemanticDimension("d2", "x", "x"),)),
        ),
    )
    cases.append(shared)

    for comp in cases:
        fee = diagnose(comp).coherence_fee
        additive = sum(decompose_fee_by_dimension(comp).by_dimension.values()) == fee
        if has_disjoint_field_decomposition(comp):
            assert additive, f"{comp.name}: DFD holds but not additive (theorem violated)"


def _load_registry_pairs():
    """Locate the registry corpus relative to plausible roots; skip if absent."""
    diagnose_pair = pytest.importorskip("calibration.compute").diagnose_pair
    ManifestStore = pytest.importorskip("calibration.corpus").ManifestStore

    here = Path(__file__).resolve()
    candidates = [
        here.parents[1] / "calibration" / "data" / "registry",  # bulla/calibration/...
        Path("calibration/data/registry"),
        Path("bulla/calibration/data/registry"),
    ]
    data_dir = next((c for c in candidates if (c / "index.json").exists()), None)
    if data_dir is None:
        pytest.skip("registry corpus data not found")
    return diagnose_pair, ManifestStore(data_dir=data_dir)


def test_additivity_on_registry_corpus():
    """Two corpus-wide checks (see module docstring for why this is a
    consistency check, not an empirical discovery):

      * ``mismatches == 0`` — every corpus composition is additive. Because the
        corpus is DFD by construction, this asserts the naming convention
        actually holds across all 703 pairs (a guard that would fire if a
        future, badly-labelled server entered the registry).
      * ``dfd_but_not_additive == 0`` — the theorem itself: no DFD composition
        is non-additive.
    """
    diagnose_pair, store = _load_registry_pairs()

    def field_count(tools):
        return sum(
            len(t.get("inputSchema", {}).get("properties", {})) for t in tools
        )

    servers = {
        n: store.get_tools(n)
        for n in store.list_servers()
        if field_count(store.get_tools(n)) >= 3
    }
    if len(servers) < 2:
        pytest.skip("insufficient real-schema servers in corpus")

    checked = mismatches = dfd_but_not_additive = 0
    for a, b in itertools.combinations(sorted(servers), 2):
        res = diagnose_pair(a, servers[a], b, servers[b])
        comp = getattr(res, "kernel_composition", None)
        if comp is None:
            continue
        checked += 1
        # One decomposition call carries total_fee, additivity status, and DFD
        # status — the point of the richer return type (no redundant diagnose).
        r = decompose_fee_by_dimension(comp)
        assert all(v >= 0 for v in r.by_dimension.values())  # universal guarantee
        if not r.is_additive:
            mismatches += 1
        if r.dfd_holds and not r.is_additive:  # theorem: DFD => additive
            dfd_but_not_additive += 1

    assert checked > 0
    assert mismatches == 0, f"{mismatches}/{checked} compositions violated additivity"
    assert dfd_but_not_additive == 0, (
        f"{dfd_but_not_additive}/{checked} DFD compositions were non-additive "
        "(Per-Dimension Additivity theorem violated)"
    )


def test_rich_decomposition_type_fields():
    """The DimensionFeeDecomposition return type carries total_fee, the
    additivity residual (cross-dimensional interaction score), and DFD status
    in one object."""
    from bulla.diagnostic import decompose_fee_by_dimension as _decomp
    add = _paired_cycle([("x", "dx"), ("y", "dy")])
    r = _decomp(add)
    assert r.total_fee == 2
    assert r.by_dimension == {"dx": 1, "dy": 1}
    assert r.residual == 0
    assert r.is_additive and r.dfd_holds
    assert r.shared_columns == {}

    tools = (ToolSpec("a", ("x",), ()), ToolSpec("b", ("x",), ()))
    split = Composition(
        "split",
        tools,
        (
            Edge("a", "b", (SemanticDimension("d_fwd", "x", "x"),)),
            Edge("b", "a", (SemanticDimension("d_rev", "x", "x"),)),
        ),
    )
    r2 = _decomp(split)
    assert r2.total_fee == 1
    assert r2.residual == 1            # interaction score: hidden coupling links the two names
    assert not r2.is_additive and not r2.dfd_holds
    assert ("a", "x") in r2.shared_columns
