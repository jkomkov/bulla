"""Boundary verification: forced/residual cost decomposition.

Tests the three-level hierarchy:
  Level 1: Σ* = B_forced + Σ*_ess          (exact, any matroid)
  Level 2: Σ*_ess = Σ_total_ess - A_ess    (exact, uniform-product essential)
  Level 3: A_ess ≤ Σ_total_ess - Σ*_ess    (lower bound, general)

Iterated trimming to essential matroid:
  1. Delete all loops (leverage = 0)
  2. Contract all coloops (leverage = 1)
  3. Repeat until no loops or coloops remain → M_ess

Forced disclosures F = all coloops removed across iterations.
B_forced(M, c) = Σ_{e ∈ F} c(e).
"""

import json
import sys
from fractions import Fraction
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from bulla.coboundary import matrix_rank
from bulla.witness_geometry import (
    _connected_components_of_gram,
    compute_profile,
    leverage_scores,
)


# ── Matroid primitives ──


def column_matroid_rank(W):
    """Rank of the column matroid of W (as list-of-lists of Fraction)."""
    if not W or not W[0]:
        return 0
    return matrix_rank(gram(W))


def gram(W):
    """W^T W."""
    if not W:
        return []
    m, n = len(W), len(W[0])
    K = [[Fraction(0)] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            s = Fraction(0)
            for r in range(m):
                s += W[r][i] * W[r][j]
            K[i][j] = s
    return K


def enumerate_bases(K, rank):
    """Brute-force enumerate all bases of the column matroid of K."""
    n = len(K)
    bases = []
    for combo in combinations(range(n), rank):
        sub = [[K[i][j] for j in combo] for i in combo]
        if matrix_rank(sub) == rank:
            bases.append(combo)
    return bases


def min_cost_basis(K, rank, costs):
    """Brute-force minimum cost basis."""
    bases = enumerate_bases(K, rank)
    best = None
    best_cost = float("inf")
    for b in bases:
        c = sum(costs[i] for i in b)
        if c < best_cost:
            best_cost = c
            best = b
    return best, best_cost


# ── Iterated trimming to essential matroid ──


def iterated_trim(K, costs, labels=None):
    """Trim loops and coloops iteratively until fixed point.

    Returns:
        forced_set: list of (original_index, cost) for all forced disclosures
        essential_K: Gram matrix of the essential matroid
        essential_indices: original indices of remaining elements
        essential_costs: costs of remaining elements
        iterations: number of trim rounds
    """
    n = len(K)
    active = list(range(n))
    forced = []
    iteration = 0

    while True:
        if not active:
            break
        # Build sub-Gram on active indices
        K_sub = [[K[i][j] for j in active] for i in active]
        lev = leverage_scores(K_sub)

        loops_idx = [k for k, l in enumerate(lev) if l == 0]
        coloops_idx = [k for k, l in enumerate(lev) if l == 1]

        if not loops_idx and not coloops_idx:
            break

        iteration += 1
        label = lambda idx: labels[active[idx]] if labels else f"e{active[idx]}"

        if coloops_idx:
            for k in coloops_idx:
                orig = active[k]
                forced.append((orig, costs[orig]))

        # Remove loops and coloops
        remove = set(loops_idx) | set(coloops_idx)
        active = [active[k] for k in range(len(active)) if k not in remove]

    # Build essential Gram
    if active:
        K_ess = [[K[i][j] for j in active] for i in active]
    else:
        K_ess = []
    ess_costs = [costs[i] for i in active]

    return forced, K_ess, active, ess_costs, iteration


def sum_of_maxima(K, costs):
    """Sum-of-maxima formula: Σ_j [Σ c(h) - max c(h)] per nontrivial component."""
    comps = _connected_components_of_gram(K)
    sigma_star = Fraction(0)
    avoidable = Fraction(0)
    for comp in comps:
        if len(comp) > 1:
            comp_costs = [costs[i] for i in comp]
            sigma_star += sum(comp_costs) - max(comp_costs)
            avoidable += max(comp_costs)
    return sigma_star, avoidable


# ── Synthetic matroids ──


def make_synthetic_coloop():
    """4-field matroid where field 3 is a coloop (in every basis).

    W is 3×4: columns 0,1,2 span a 2D subspace, column 3 spans an
    independent 3rd dimension.

    Matroid: rank 3 on 4 elements. Field 3 is in every basis (coloop).
    Bases: {0,1,3}, {0,2,3}, {1,2,3} — only 3 bases out of C(4,3)=4.
    {0,1,2} is NOT a basis because cols 0,1,2 have rank 2.
    """
    W = [
        [Fraction(1), Fraction(0), Fraction(1), Fraction(0)],
        [Fraction(0), Fraction(1), Fraction(1), Fraction(0)],
        [Fraction(0), Fraction(0), Fraction(0), Fraction(1)],
    ]
    costs = [Fraction(1), Fraction(2), Fraction(3), Fraction(10)]
    labels = ["e0(1)", "e1(2)", "e2(3)", "e3(10)*coloop"]
    return W, costs, labels


def make_synthetic_coloop_plus_nonuniform():
    """7-field matroid: coloop (e6) + non-uniform essential matroid.

    W is 5×7: the non-uniform 6-element matroid from Example 3,
    plus a 7th element spanning a unique 5th dimension (coloop).

    This is the hardest case: BOTH correction layers are needed.
    - B_forced = cost(e6) = 8 (coloop, forced disclosure)
    - Essential matroid = 6-element rank-4 non-uniform (Example 3)
    - Sum-of-maxima on essential OVERESTIMATES (15 vs true 11)
    - Naïve formula on full matroid = 15 (ignores e6 singleton)
    - True Σ* = 8 + 11 = 19

    The two errors partially cancel: coloop underestimates by 8,
    non-uniform overestimates by 4, net underestimate = 4.
    """
    W = [
        [Fraction(1), Fraction(0), Fraction(1), Fraction(0), Fraction(0), Fraction(0), Fraction(0)],
        [Fraction(0), Fraction(1), Fraction(1), Fraction(0), Fraction(0), Fraction(1), Fraction(0)],
        [Fraction(0), Fraction(0), Fraction(0), Fraction(1), Fraction(1), Fraction(0), Fraction(0)],
        [Fraction(0), Fraction(0), Fraction(0), Fraction(0), Fraction(1), Fraction(1), Fraction(0)],
        [Fraction(0), Fraction(0), Fraction(0), Fraction(0), Fraction(0), Fraction(0), Fraction(1)],
    ]
    costs = [Fraction(1), Fraction(2), Fraction(4), Fraction(3), Fraction(6), Fraction(5), Fraction(8)]
    labels = ["e0(1)", "e1(2)", "e2(4)", "e3(3)", "e4(6)", "e5(5)", "e6(8)*coloop"]
    return W, costs, labels


def make_synthetic_non_uniform_essential():
    """6-field matroid where the essential matroid is NOT uniform-product.

    After trimming, the residual has a non-uniform structure where the
    sum-of-maxima formula fails as an exact value (only a lower bound).

    W is 4×6:
      col 0 = (1,0,0,0)
      col 1 = (0,1,0,0)
      col 2 = (1,1,0,0)  — dependent on 0,1
      col 3 = (0,0,1,0)
      col 4 = (0,0,1,1)  — partially dependent on 3
      col 5 = (0,1,0,1)  — partially dependent on 1,3

    Rank = 4, no coloops (each element can be omitted in at least one basis).
    But this is NOT uniform — not every 4-subset is a basis.
    """
    W = [
        [Fraction(1), Fraction(0), Fraction(1), Fraction(0), Fraction(0), Fraction(0)],
        [Fraction(0), Fraction(1), Fraction(1), Fraction(0), Fraction(0), Fraction(1)],
        [Fraction(0), Fraction(0), Fraction(0), Fraction(1), Fraction(1), Fraction(0)],
        [Fraction(0), Fraction(0), Fraction(0), Fraction(0), Fraction(1), Fraction(1)],
    ]
    costs = [Fraction(1), Fraction(2), Fraction(4), Fraction(3), Fraction(6), Fraction(5)]
    labels = ["e0(1)", "e1(2)", "e2(4)", "e3(3)", "e4(6)", "e5(5)"]
    return W, costs, labels


# ── Verification harness ──


def verify_synthetic(name, W, costs, labels):
    """Run the full three-level verification on a synthetic matroid."""
    K = gram(W)
    n = len(K)
    rank = matrix_rank(K)
    bases = enumerate_bases(K, rank)
    _, true_min = min_cost_basis(K, rank, costs)

    print(f"\n{'=' * 60}")
    print(f"  {name}")
    print(f"{'=' * 60}")
    print(f"  Ground set: {n} elements, rank {rank}, {len(bases)} bases")

    # Leverage
    lev = leverage_scores(K)
    for i in range(n):
        tag = ""
        if lev[i] == 1:
            tag = " ← COLOOP"
        elif lev[i] == 0:
            tag = " ← LOOP"
        print(f"    {labels[i]:>25}  lev={float(lev[i]):.3f}{tag}")

    # All bases with costs
    print(f"\n  All bases (by cost):")
    basis_costs = []
    for b in bases:
        c = sum(costs[i] for i in b)
        basis_costs.append((c, b))
    basis_costs.sort()
    for c, b in basis_costs:
        field_str = ", ".join(labels[i] for i in b)
        print(f"    cost={float(c):>5.0f}  {field_str}")
    print(f"\n  True Σ* = {float(true_min)}")

    # Sum-of-maxima formula (naïve, on full matroid)
    sigma_naive, avoidable_naive = sum_of_maxima(K, costs)
    print(f"\n  Naïve sum-of-maxima: Σ* = {float(sigma_naive)}")
    print(f"  Naïve geometry dividend: A = {float(avoidable_naive)}")
    if sigma_naive == true_min:
        print(f"  → EXACT (DFD regime)")
    else:
        print(f"  → WRONG by {float(true_min - sigma_naive)} "
              f"(formula underestimates)")

    # Iterated trimming
    forced, K_ess, ess_idx, ess_costs, n_iter = iterated_trim(
        K, costs, labels
    )
    b_forced = sum(c for _, c in forced)

    print(f"\n  Iterated trimming ({n_iter} rounds):")
    print(f"    Forced disclosures: {len(forced)}")
    for orig_i, c in forced:
        print(f"      {labels[orig_i]:>25}  cost={float(c)}")
    print(f"    B_forced = {float(b_forced)}")
    print(f"    Essential matroid: {len(ess_idx)} elements, ", end="")

    if K_ess:
        ess_rank = matrix_rank(K_ess)
        ess_bases = enumerate_bases(K_ess, ess_rank)
        _, ess_min = min_cost_basis(K_ess, ess_rank, ess_costs)
        print(f"rank {ess_rank}, {len(ess_bases)} bases")

        # Check if essential matroid is uniform
        n_ess = len(K_ess)
        expected_uniform_bases = 1
        for i in range(n_ess):
            # C(n_ess, ess_rank) — but check component by component
            pass
        from math import comb
        is_uniform = len(ess_bases) == comb(n_ess, ess_rank)

        # Check component-by-component uniformity
        ess_comps = _connected_components_of_gram(K_ess)
        ess_comps_nontrivial = [c for c in ess_comps if len(c) > 1]
        product_count = 1
        for comp in ess_comps_nontrivial:
            product_count *= len(comp)
        is_uniform_product = len(ess_bases) == product_count

        print(f"    Essential components: "
              f"{[len(c) for c in ess_comps_nontrivial]}")
        print(f"    Uniform-product? {is_uniform_product} "
              f"(bases={len(ess_bases)}, product={product_count})")

        # Sum-of-maxima on essential
        sigma_ess, a_ess = sum_of_maxima(K_ess, ess_costs)
        print(f"\n    Σ*_ess (brute force) = {float(ess_min)}")
        print(f"    Σ*_ess (sum-of-maxima) = {float(sigma_ess)}")
        print(f"    A_ess = {float(a_ess)}")

        # Level 1: exact decomposition
        level1 = b_forced + ess_min
        print(f"\n  LEVEL 1: B_forced + Σ*_ess = "
              f"{float(b_forced)} + {float(ess_min)} = {float(level1)}")
        assert level1 == true_min, \
            f"Level 1 FAILED: {level1} != {true_min}"
        print(f"    ✓ Exact (matches true Σ* = {float(true_min)})")

        # Level 2: closed form if uniform-product
        if is_uniform_product:
            level2 = b_forced + sigma_ess
            print(f"\n  LEVEL 2: B_forced + (Σ_total_ess - A_ess) = "
                  f"{float(b_forced)} + {float(sigma_ess)} = {float(level2)}")
            assert level2 == true_min, \
                f"Level 2 FAILED: {level2} != {true_min}"
            print(f"    ✓ Exact (uniform-product essential matroid)")
        else:
            print(f"\n  LEVEL 2: N/A (essential matroid is NOT uniform-product)")
            print(f"    Sum-of-maxima on essential: {float(sigma_ess)}")
            print(f"    True Σ*_ess: {float(ess_min)}")
            if sigma_ess < ess_min:
                print(f"    Gap: {float(ess_min - sigma_ess)} "
                      f"(formula underestimates)")
            elif sigma_ess == ess_min:
                print(f"    Happens to match (accidental)")

        # Level 3: general case — no universal bound direction
        level3_naive = b_forced + sigma_ess
        gap = float(level3_naive - true_min)
        if gap == 0:
            print(f"\n  LEVEL 3: Naïve formula matches (gap = 0)")
        elif gap > 0:
            print(f"\n  LEVEL 3: Naïve formula OVERESTIMATES by {gap}")
            print(f"    (essential matroid allows omitting more than 1 per component)")
        else:
            print(f"\n  LEVEL 3: Naïve formula UNDERESTIMATES by {-gap}")
            print(f"    (coloops force inclusion of expensive singletons)")

    else:
        print(f"rank 0, 1 basis (empty)")
        print(f"\n  LEVEL 1: B_forced = {float(b_forced)} = Σ*")
        assert b_forced == true_min
        print(f"    ✓ All elements forced")

    return {
        "name": name,
        "n": n,
        "rank": rank,
        "n_bases": len(bases),
        "true_min": float(true_min),
        "naive_formula": float(sigma_naive),
        "b_forced": float(b_forced),
        "n_forced": len(forced),
        "n_essential": len(ess_idx),
        "n_iterations": n_iter,
    }


# ── Corpus verification ──


def verify_corpus():
    """Verify B_forced = 0 for entire DFD corpus."""
    from bulla.guard import BullaGuard
    from bulla.proxy import compute_field_costs

    MANIFESTS_DIR = REPO / "calibration" / "data" / "registry" / "manifests"

    manifests = {}
    for p in sorted(MANIFESTS_DIR.glob("*.json")):
        data = json.loads(p.read_text())
        manifests[p.stem] = data.get("tools", [])

    servers = sorted(manifests.keys())
    n_tested = 0
    n_coloop_free = 0
    n_with_coloops = 0

    print(f"\n{'=' * 60}")
    print(f"  CORPUS: DFD VERIFICATION (B_forced = 0)")
    print(f"{'=' * 60}")

    for i, left in enumerate(servers):
        for right in servers[i + 1:]:
            tools_l, tools_r = manifests[left], manifests[right]
            prefixed = []
            for t in tools_l:
                p = dict(t)
                p["name"] = f"{left}__{t['name']}"
                prefixed.append(p)
            for t in tools_r:
                p = dict(t)
                p["name"] = f"{right}__{t['name']}"
                prefixed.append(p)

            guard = BullaGuard.from_tools_list(prefixed, name=f"{left}+{right}")
            comp = guard.composition
            profile = compute_profile(list(comp.tools), list(comp.edges))

            if profile.fee == 0:
                continue

            n_tested += 1

            if profile.coloops:
                n_with_coloops += 1
                print(f"  ⚠ {left}+{right}: fee={profile.fee}, "
                      f"coloops={len(profile.coloops)}")
            else:
                n_coloop_free += 1

    print(f"\n  Tested: {n_tested} compositions with fee > 0")
    print(f"  Coloop-free (B_forced = 0): {n_coloop_free}")
    print(f"  With coloops: {n_with_coloops}")
    if n_with_coloops == 0:
        print(f"  ✓ Entire corpus is DFD: B_forced = 0 everywhere")
    else:
        print(f"  ⚠ {n_with_coloops} compositions have coloops")

    return n_tested, n_coloop_free, n_with_coloops


# ── Main ──


if __name__ == "__main__":
    print("BOUNDARY VERIFICATION: FORCED/RESIDUAL COST DECOMPOSITION")
    print("=" * 60)
    print("Three-level hierarchy:")
    print("  1. Σ* = B_forced + Σ*_ess          (exact, any matroid)")
    print("  2. Σ*_ess = Σ_total_ess - A_ess    (exact, uniform-product)")
    print("  3. General M_ess: no closed form     (matroid optimization required)")

    results = []

    # Synthetic tests
    W1, c1, l1 = make_synthetic_coloop()
    results.append(verify_synthetic("Single coloop (field 3, cost 10)", W1, c1, l1))

    W2, c2, l2 = make_synthetic_coloop_plus_nonuniform()
    results.append(verify_synthetic(
        "Coloop + non-uniform essential (both corrections)", W2, c2, l2
    ))

    W3, c3, l3 = make_synthetic_non_uniform_essential()
    results.append(verify_synthetic(
        "Non-uniform essential matroid (no coloops, not uniform)", W3, c3, l3
    ))

    # Corpus
    n_tested, n_free, n_coloop = verify_corpus()

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  SUMMARY")
    print(f"{'=' * 60}")
    for r in results:
        status = "✓" if r["naive_formula"] == r["true_min"] else "✗"
        print(f"  {status} {r['name']}")
        print(f"    true={r['true_min']}, naive={r['naive_formula']}, "
              f"forced={r['b_forced']}, essential={r['n_essential']}el, "
              f"{r['n_iterations']} trim rounds")
    print(f"\n  Corpus: {n_tested} tested, {n_free} coloop-free, "
          f"{n_coloop} with coloops")
    print(f"\n  All assertions passed.")
