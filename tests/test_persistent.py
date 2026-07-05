"""Tests for bulla.persistent (Sprint G19).

Covers:
  - lcp / jaccard similarity hand-checks
  - canonical_merged_name collision-safety on the Sprint 13 seed-set
  - synthetic positive control fixture: 4 designed generators recovered within +-1 eps-step
  - functoriality: H^1 rank monotonically non-increasing as eps grows
  - total_persistence multiplicity weighting
  - hand-coded Hopcroft-Karp bottleneck distance vs brute force
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

# Prefer the in-worktree bulla source over any globally-installed bulla
# (the global install may predate this sprint's persistent.py module).
_BULLA_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_BULLA_SRC) not in sys.path:
    sys.path.insert(0, str(_BULLA_SRC))

from bulla.cli import _seed_set_compositions
from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.parser import load_composition
from bulla.persistent import (
    Bar,
    PackTagOntology,
    bottleneck_distance,
    canonical_merged_name,
    compute_barcode,
    compute_thresholded_h1,
    jaccard_similarity,
    lcp_similarity,
    perturb_type1_rename,
    perturb_type2_drop_edge,
    stability_check,
    total_persistence,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_FIXTURE = (
    REPO_ROOT / "bulla" / "compositions" / "synthetic_g19_positive_control.yaml"
)


# ── Similarity functions ────────────────────────────────────────────


def test_lcp_similarity_basic() -> None:
    assert lcp_similarity("path_separator", "path_format") == pytest.approx(5 / 14)
    assert lcp_similarity("encoding", "charset") == 0.0
    assert lcp_similarity("encoding_match", "encoding_handoff") == pytest.approx(9 / 16)
    assert lcp_similarity("identical", "identical") == 1.0
    assert lcp_similarity("", "anything") == 0.0


def test_jaccard_similarity_basic() -> None:
    # tokens('data_encoding_match') = {data, encoding, match}
    # tokens('data_charset_match') = {data, charset, match}
    # intersection = 2, union = 4
    assert jaccard_similarity("data_encoding_match", "data_charset_match") == pytest.approx(0.5)
    assert jaccard_similarity("a", "a") == 1.0
    assert jaccard_similarity("a", "b") == 0.0


# ── Canonical naming collision-safety ───────────────────────────────


def test_canonical_merged_name_format() -> None:
    name = canonical_merged_name(("foo", "bar"))
    assert name.startswith("_merged_")
    assert len(name) == len("_merged_") + 8


def test_canonical_merged_name_deterministic() -> None:
    a = canonical_merged_name(("foo", "bar"))
    b = canonical_merged_name(("bar", "foo"))  # different order
    assert a == b


def test_canonical_merged_name_no_collision_seed_set() -> None:
    """Generated merged names must not collide with any existing dim name in the seeds."""
    seeds = _seed_set_compositions(REPO_ROOT)
    existing_names: set[str] = set()
    for comp, _src in seeds:
        for e in comp.edges:
            for d in e.dimensions:
                existing_names.add(d.name)
        for t in comp.tools:
            for f in t.internal_state:
                existing_names.add(f)
            for f in t.observable_schema:
                existing_names.add(f)

    # Sample some realistic merge classes and verify no collisions
    sample_classes = [
        ("data_encoding_match", "data_charset_match"),
        ("encoding", "charset"),
        ("path", "paths", "dir_path"),
        ("audit_log_entry_track", "audit_log_entry_loopback"),
    ]
    for members in sample_classes:
        merged = canonical_merged_name(members)
        assert merged not in existing_names, f"Collision: {merged} clashes with existing"


# ── Synthetic positive control: G19.0 gate ──────────────────────────


@pytest.fixture
def synthetic_comp() -> Composition:
    return load_composition(SYNTHETIC_FIXTURE)


def test_synthetic_baseline_diagnostic(synthetic_comp: Composition) -> None:
    """Verify the fixture has the expected baseline H^1 structure."""
    d = diagnose(synthetic_comp)
    assert d.coherence_fee == 10
    assert d.h1_obs == 10
    assert d.h1_full == 0


def test_synthetic_no_merging_at_eps_zero(synthetic_comp: Composition) -> None:
    """At eps=0, no merging under any filtration; baseline preserved."""
    for filt in ("lcp", "jaccard", "pack_tag"):
        r = compute_thresholded_h1(synthetic_comp, 0.0, filtration=filt)
        assert r.h1_obs == 10, f"{filt}: h1_obs at eps=0 should be 10, got {r.h1_obs}"
        assert len(r.equivalence_classes) == 0, f"{filt}: no merging at eps=0"


def test_synthetic_alpha_lcp_dies_low(synthetic_comp: Composition) -> None:
    """Generator alpha (data_encoding family) merges first at eps approx 0.36 under LCP."""
    bars = compute_barcode(synthetic_comp, filtration="lcp")
    # data_encoding_handoff vs data_encoding_match: sim = 14/22 approx 0.636 -> die ~0.364
    # Find data_encoding_match in bars; should die at <=0.40 (within 1 step of 0.364)
    death_by_dim = {b.contributing_dims[0]: b.death_eps for b in bars}
    enc_match_death = death_by_dim["data_encoding_match"]
    assert enc_match_death <= 0.40, f"data_encoding_match should die <=0.40, got {enc_match_death}"


def test_synthetic_gamma_invariant(synthetic_comp: Composition) -> None:
    """unique_xi_marker_link and payload_blob_special_carry must persist to eps=1 under all filtrations."""
    for filt in ("lcp", "jaccard", "pack_tag"):
        bars = compute_barcode(synthetic_comp, filtration=filt)
        death_by_dim = {b.contributing_dims[0]: b.death_eps for b in bars}
        assert death_by_dim["unique_xi_marker_link"] == 1.0, (
            f"{filt}: unique_xi_marker should be filtration-invariant"
        )
        assert death_by_dim["payload_blob_special_carry"] == 1.0, (
            f"{filt}: payload_blob_special_carry should be filtration-invariant"
        )


def test_synthetic_delta_lcp_dies_at_predicted(synthetic_comp: Composition) -> None:
    """Generator delta (audit_log pair) sim=0.667 under LCP -> dies at eps approx 0.333."""
    bars = compute_barcode(synthetic_comp, filtration="lcp")
    death_by_dim = {b.contributing_dims[0]: b.death_eps for b in bars}
    # The alphabetically-first member (loopback < track) survives; track dies
    track_death = death_by_dim["audit_log_entry_track"]
    assert 0.30 <= track_death <= 0.40, f"audit_track death should be ~0.33, got {track_death}"


def test_synthetic_pack_tag_binary(synthetic_comp: Composition) -> None:
    """Pack-tag merges only at eps=1 (binary endpoint check)."""
    for eps in (0.0, 0.5, 0.95):
        r = compute_thresholded_h1(synthetic_comp, eps, filtration="pack_tag")
        assert r.h1_obs == 10, f"pack_tag at eps={eps} should not merge"
    # At eps=1.0, encoding-pack and path-pack merge
    r1 = compute_thresholded_h1(synthetic_comp, 1.0, filtration="pack_tag")
    assert r1.h1_obs < 10, f"pack_tag at eps=1 should merge, got h1={r1.h1_obs}"


# ── Functoriality (eps <= eps' ==> H^1 rank non-increasing) ─────────


def test_functoriality_h1_monotone_lcp(synthetic_comp: Composition) -> None:
    prev_h1 = None
    for step in range(0, 21):
        eps = step / 20.0
        r = compute_thresholded_h1(synthetic_comp, eps, filtration="lcp")
        if prev_h1 is not None:
            assert r.h1_obs <= prev_h1, (
                f"H^1 not monotone non-increasing at eps={eps}: "
                f"prev={prev_h1}, current={r.h1_obs}"
            )
        prev_h1 = r.h1_obs


def test_functoriality_h1_monotone_jaccard(synthetic_comp: Composition) -> None:
    prev_h1 = None
    for step in range(0, 21):
        eps = step / 20.0
        r = compute_thresholded_h1(synthetic_comp, eps, filtration="jaccard")
        if prev_h1 is not None:
            assert r.h1_obs <= prev_h1, f"jaccard non-monotone at eps={eps}"
        prev_h1 = r.h1_obs


def test_functoriality_seed_set_lcp() -> None:
    """All Sprint 13 seeds produce monotone non-increasing H^1 under LCP filtration."""
    seeds = _seed_set_compositions(REPO_ROOT)
    for comp, _src in seeds:
        prev_h1 = None
        for step in range(0, 21):
            eps = step / 20.0
            r = compute_thresholded_h1(comp, eps, filtration="lcp")
            if prev_h1 is not None:
                assert r.h1_obs <= prev_h1, (
                    f"{comp.name}: H^1 non-monotone at eps={eps}"
                )
            prev_h1 = r.h1_obs


# ── Total persistence with multiplicity weighting ───────────────────


def test_total_persistence_synthetic(synthetic_comp: Composition) -> None:
    """All bars in synthetic fixture have multiplicity=1 (each dim on one edge)."""
    bars = compute_barcode(synthetic_comp, filtration="lcp")
    for b in bars:
        assert b.multiplicity == 1, f"{b.contributing_dims[0]}: mult={b.multiplicity}"
    tp = total_persistence(bars)
    assert tp == sum(b.death_eps - b.birth_eps for b in bars)


def test_total_persistence_filesystem_github_multiplicity() -> None:
    """filesystem+github has dim names appearing on many edges -> multiplicity > 1."""
    seeds = _seed_set_compositions(REPO_ROOT)
    fs_gh = next(c for c, _ in seeds if c.name == "filesystem+github")
    bars = compute_barcode(fs_gh, filtration="lcp")
    multiplicities = [b.multiplicity for b in bars]
    assert max(multiplicities) > 1, "filesystem+github should have multi-edge dims"


# ── Bottleneck distance: hand-coded vs brute force ──────────────────


def _brute_force_bottleneck(bars_a: list[Bar], bars_b: list[Bar]) -> float:
    """Reference implementation: enumerate all permutations and pick best max-cost matching.

    Only feasible for very small barcodes (<=6 per side).
    """
    pts_a = [(b.birth_eps, b.death_eps) for b in bars_a]
    pts_b = [(b.birth_eps, b.death_eps) for b in bars_b]

    def linf(p: tuple[float, float], q: tuple[float, float]) -> float:
        return max(abs(p[0] - q[0]), abs(p[1] - q[1]))

    def diag(p: tuple[float, float]) -> float:
        return abs(p[0] - p[1]) / 2.0

    n = max(len(pts_a), len(pts_b))
    # Pad with diagonal projections
    aug_a = pts_a + [None] * (n - len(pts_a))
    aug_b = pts_b + [None] * (n - len(pts_b))

    best = float("inf")
    for perm in itertools.permutations(range(n)):
        worst = 0.0
        for i in range(n):
            j = perm[i]
            p = aug_a[i]
            q = aug_b[j]
            if p is None and q is None:
                cost = 0.0
            elif p is None:
                cost = diag(q)
            elif q is None:
                cost = diag(p)
            else:
                cost = min(linf(p, q), max(diag(p), diag(q)))
            if cost > worst:
                worst = cost
        if worst < best:
            best = worst
    return best


def test_bottleneck_identity() -> None:
    bars = [
        Bar("g1", 0.0, 0.5, ("g1",)),
        Bar("g2", 0.0, 0.8, ("g2",)),
    ]
    assert bottleneck_distance(bars, bars) == 0.0


def test_bottleneck_small_diagrams_match_brute_force() -> None:
    bars_a = [
        Bar("g1", 0.0, 0.5, ("g1",)),
        Bar("g2", 0.0, 0.8, ("g2",)),
    ]
    bars_b = [
        Bar("g1", 0.0, 0.55, ("g1",)),
        Bar("g2", 0.0, 0.85, ("g2",)),
    ]
    bn = bottleneck_distance(bars_a, bars_b)
    bf = _brute_force_bottleneck(bars_a, bars_b)
    assert bn == pytest.approx(bf, abs=1e-9), f"bn={bn}, bf={bf}"


def test_bottleneck_different_sizes() -> None:
    bars_a = [Bar("g1", 0.0, 0.5, ("g1",))]
    bars_b = [
        Bar("g1", 0.0, 0.5, ("g1",)),
        Bar("g2", 0.0, 0.6, ("g2",)),
    ]
    bn = bottleneck_distance(bars_a, bars_b)
    # Optimal matching: g1_A <-> g2_B (cost 0.1); g1_B <-> its own diagonal (cost 0.25);
    # g2_B's diagonal copy <-> A's diagonal copy (free). Max cost = 0.25.
    # This is lower than the naive "match g1<->g1, g2 to its own diagonal at 0.3"
    # because the algorithm exploits the augmented matching structure.
    assert bn == pytest.approx(0.25, abs=1e-9)


def test_bottleneck_isolated_far_point() -> None:
    """A truly far-from-diagonal isolated point in B must pay its diagonal distance."""
    bars_a = [Bar("g1", 0.0, 0.5, ("g1",))]
    bars_b = [
        Bar("g1", 0.0, 0.5, ("g1",)),
        Bar("g_far", 0.0, 0.9, ("g_far",)),  # diag dist = 0.45
    ]
    bn = bottleneck_distance(bars_a, bars_b)
    bf = _brute_force_bottleneck(bars_a, bars_b)
    assert bn == pytest.approx(bf, abs=1e-9)


# ── Stability check end-to-end ──────────────────────────────────────


def test_stability_synthetic_bounded(synthetic_comp: Composition) -> None:
    """Synthetic fixture: type-1 rename produces bounded perturbation.

    The aggregate promotion target is type-1 <=1 eps-step in >=7/9 well-formed
    compositions; per-fixture is allowed to be larger when the renamed dim is
    in a tight similarity cluster (here audit_log_entry_loopback -> _renamed
    shifts the audit-pair LCP from 0.667 to 0.5, moving death-eps by 2 steps).
    """
    s = stability_check(synthetic_comp, filtration="lcp")
    assert s.type1_bottleneck <= 0.20, f"type-1 bn={s.type1_bottleneck}, expected <=0.20"


def test_perturb_type1_returns_distinct_dim(synthetic_comp: Composition) -> None:
    """Type-1 rename produces a composition with one renamed dim."""
    perturbed = perturb_type1_rename(synthetic_comp)
    orig_dims = {d.name for e in synthetic_comp.edges for d in e.dimensions}
    new_dims = {d.name for e in perturbed.edges for d in e.dimensions}
    diff_orig = orig_dims - new_dims
    diff_new = new_dims - orig_dims
    assert len(diff_orig) == 1
    assert len(diff_new) == 1


def test_perturb_type2_drops_one_edge(synthetic_comp: Composition) -> None:
    perturbed = perturb_type2_drop_edge(synthetic_comp)
    assert len(perturbed.edges) == len(synthetic_comp.edges) - 1


# ── Pack-tag ontology ───────────────────────────────────────────────


def test_pack_tag_loads_default() -> None:
    ont = PackTagOntology.load()
    # Should load at least base.yaml dimensions
    assert ont.lookup("data_encoding") is not None
    assert ont.lookup("data_charset") is not None
    assert ont.lookup("file_path") is not None
    # Truly unknown app-specific field
    assert ont.lookup("audit_log_entry") is None


def test_pack_tag_match_via_field_pattern() -> None:
    ont = PackTagOntology.load()
    # *_encoding pattern should match anything ending in _encoding
    tag = ont.lookup("custom_encoding")
    assert tag == "base.encoding"
