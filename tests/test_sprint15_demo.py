"""Sprint 15 demo regression tests — composition-only obstruction.

Pins both the trigger conditions AND the linear-algebra mechanism so
the demo cannot become a black-box CLI artifact.

The demo's principle:
  Projection can collapse distinct global obligations into identical
  local observations.

In the canonical 3-tool hub-and-spoke fixture:
  A: internal=(p,)  obs=(p,)
  B: internal=(p,)  obs=()
  C: internal=(p,)  obs=()
  edges: A→B (p→p), A→C (p→p)

  Pair fees: AB=0, AC=0, BC=0  (BC has no edges)
  Global fee: 1  (rank_obs=1, rank_internal=2)

  Pairwise exact_disclosure_equivalence: certified
  Global  exact_disclosure_equivalence: not_certified  (CHP fails: A.p
                                                         referenced as
                                                         from-side twice)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bulla" / "src"))
sys.path.insert(0, str(REPO / "papers" / "composition-doctrine" / "sprint15_demo"))

from bulla.certificate import certify
from bulla.regime import classify
from fixture import build_demo_composition, induced_pair_compositions


# ---- 1. Rank-story regression (user-requested specific test) ----

def test_rank_story_pinned():
    """The linear-algebra mechanism must hold exactly:
      rank_obs == 1
      rank_internal == 2
      coherence_fee == rank_internal − rank_obs == 1

    If this drifts, the demo's mechanism has shifted; investigate before
    relaxing the assertions."""
    comp = build_demo_composition()
    cert = certify(comp)
    assert cert.regime.rank_obs == 1, (
        f"Demo's rank_obs drifted from 1 to {cert.regime.rank_obs}. "
        f"The 'projection collapses 2 obligations into 1 obs column' "
        f"narrative depends on rank_obs == 1."
    )
    assert cert.regime.rank_internal == 2, (
        f"Demo's rank_internal drifted from 2 to {cert.regime.rank_internal}. "
        f"The 'distinct internal obligations' narrative depends on rank_internal == 2."
    )
    assert cert.diagnostic["coherence_fee"] == 1, (
        f"Demo's coherence_fee drifted from 1 to {cert.diagnostic['coherence_fee']}."
    )


# ---- 2. Trigger condition (pairwise clean, global obstructed) ----

def test_trigger_condition_fires():
    """The demo's load-bearing trigger:
      max(pairwise_fees) == 0  AND  global_fee > 0
    Both projective + well-formed at every level."""
    comp = build_demo_composition()
    pairs = induced_pair_compositions(comp)

    pair_fees: list[int] = []
    for label, pair_comp in pairs.items():
        cert = certify(pair_comp)
        assert cert.regime.has_projective_observables, (
            f"pair {label} not projective"
        )
        assert cert.regime.is_well_formed_for_fee, (
            f"pair {label} not well-formed for fee"
        )
        pair_fees.append(cert.diagnostic["coherence_fee"])

    global_cert = certify(comp)
    assert global_cert.regime.has_projective_observables
    assert global_cert.regime.is_well_formed_for_fee
    assert max(pair_fees) == 0, (
        f"max(pairwise_fees) = {max(pair_fees)} != 0; trigger broken."
    )
    assert global_cert.diagnostic["coherence_fee"] > 0, (
        f"global_fee = {global_cert.diagnostic['coherence_fee']} not > 0; trigger broken."
    )


# ---- 3. Parent-hash chain (witness-bundle structural test) ----

def test_parent_hash_chain_resolves_to_pairwise_content_hashes():
    """When the global cert is built with parents=pairwise.content_hashes,
    the global cert's parent_certificate_hashes set must equal the set
    of pairwise certs' content hashes. This is the 'parents are evidence'
    structural anchor."""
    comp = build_demo_composition()
    pairs = induced_pair_compositions(comp)

    pair_certs = [certify(p) for p in pairs.values()]
    parent_hashes = tuple(c.certificate_content_hash for c in pair_certs)

    global_cert = certify(comp, parent_certificate_hashes=parent_hashes)

    assert set(global_cert.parent_certificate_hashes) == {
        c.certificate_content_hash for c in pair_certs
    }, "Global cert's parent hashes do not resolve exactly to pairwise content hashes."


# ---- 4. subject_bound certified for all 4 certificates ----

def test_subject_bound_certified_for_all_certs():
    """Each cert is internally consistent (subject hash computable, regime
    classified, no violations). subject_bound is certified everywhere."""
    comp = build_demo_composition()
    pairs = induced_pair_compositions(comp)

    for label, pair_comp in pairs.items():
        cert = certify(pair_comp)
        assert cert.claims["subject_bound"].status == "certified", (
            f"pair {label}: subject_bound status drifted"
        )

    global_cert = certify(comp)
    assert global_cert.claims["subject_bound"].status == "certified"


# ---- 5. No claim asserts parents imply global validity (anti-overclaim guard) ----

def test_no_claim_asserts_parents_imply_global_validity():
    """The whole point of Sprint 15: parents are evidence, NOT proof of
    global validity. v1.0 must not emit any claim asserting otherwise.

    If a future sprint adds `bundle_composes_globally` (planned for
    Sprint 16+ once incremental witness bundles are real), this test
    will need updating — but ONLY when the underlying bundle-merge
    semantics are implemented and tested."""
    comp = build_demo_composition()
    pair_certs = [certify(p) for p in induced_pair_compositions(comp).values()]
    parents = tuple(c.certificate_content_hash for c in pair_certs)
    global_cert = certify(comp, parent_certificate_hashes=parents)

    forbidden_claim_names = {
        "bundle_composes_globally",
        "global_validity_implied_by_parents",
        "parents_prove_global",
        "global_composition_certified",  # renamed in Sprint 14 to subject_bound;
                                          # ensure stale name doesn't sneak back
    }
    for fcn in forbidden_claim_names:
        assert fcn not in global_cert.claims, (
            f"Forbidden claim `{fcn}` emitted in v1.0. The Sprint 15 demo "
            f"depends on the non-existence of this claim. Either the claim "
            f"was added prematurely or its semantics need explicit Sprint 16+ "
            f"design before being shipped."
        )


# ---- 6. Pairwise vs global claim contrast (the bonus narrative) ----

def test_exact_disclosure_certified_pairwise_not_certified_globally():
    """The bonus claim-level story: pairwise exact_disclosure_equivalence
    is certified for every pair, but NOT certified globally. The CHP-
    conservative predicate fails globally because A.p is referenced as
    from-side twice (once per spoke), even though no single pair sees
    more than one such reference."""
    comp = build_demo_composition()
    pairs = induced_pair_compositions(comp)

    for label, pair_comp in pairs.items():
        cert = certify(pair_comp)
        assert cert.claims["exact_disclosure_equivalence"].status == "certified", (
            f"pair {label}: exact_disclosure_equivalence drifted "
            f"(was certified at sprint launch)."
        )

    global_cert = certify(comp)
    assert global_cert.claims["exact_disclosure_equivalence"].status == "not_certified", (
        "Global exact_disclosure_equivalence is now certified — the demo's "
        "claim-level contrast has weakened. Investigate whether CHP-conservative "
        "started passing globally (it should fail because A.p is referenced "
        "from-side twice)."
    )


# ---- 7. Hashes are stable + sensitive (sanity) ----

def test_global_certificate_hash_changes_with_parents():
    """A global cert WITH parents has a different content hash than the
    same composition WITHOUT parents (because parent_certificate_hashes
    is in the hash preimage). This pins parentage as part of identity."""
    comp = build_demo_composition()
    cert_no_parents = certify(comp)
    pair_certs = [certify(p) for p in induced_pair_compositions(comp).values()]
    parents = tuple(c.certificate_content_hash for c in pair_certs)
    cert_with_parents = certify(comp, parent_certificate_hashes=parents)
    assert cert_no_parents.certificate_content_hash != cert_with_parents.certificate_content_hash, (
        "Adding parent_certificate_hashes did NOT change the cert's content hash. "
        "The hash preimage is missing parent_certificate_hashes; parentage is not "
        "structurally bound."
    )


# ---- 7b. Parent permutation invariance (Sprint 15 polish #3) ----

def test_parent_certificate_hashes_are_canonically_sorted():
    """v1.0 parent order is set-semantics: a permutation of the same
    parents must produce identical content hashes. `certify()` sorts
    `parent_certificate_hashes` canonically before hashing.

    Without this, the demo runner is fragile against the dict iteration
    order of `induced_pair_compositions` (and against any caller who
    builds parents from an unordered source). With it, two runs that
    pass the same parent set in different orders are byte-identical."""
    comp = build_demo_composition()
    pair_certs = [certify(p) for p in induced_pair_compositions(comp).values()]
    parents_natural = tuple(c.certificate_content_hash for c in pair_certs)
    parents_reversed = tuple(reversed(parents_natural))

    # Sanity: only meaningful if there's actually >1 parent (else the
    # permutation is trivial)
    assert len(parents_natural) >= 2, (
        f"Demo composition produced fewer than 2 pairs ({len(parents_natural)}); "
        f"permutation-invariance test is degenerate."
    )

    cert_a = certify(comp, parent_certificate_hashes=parents_natural)
    cert_b = certify(comp, parent_certificate_hashes=parents_reversed)

    assert cert_a.parent_certificate_hashes == cert_b.parent_certificate_hashes, (
        "Stored parent_certificate_hashes differ between permutations — "
        "certify() is not sorting parents canonically before storage."
    )
    assert cert_a.certificate_content_hash == cert_b.certificate_content_hash, (
        "Content hashes differ between two parent permutations of the same set. "
        "Either parent sort is missing from certify(), or it sorts AFTER hash "
        "computation."
    )
    # And: the canonical form is `tuple(sorted(...))`
    assert cert_a.parent_certificate_hashes == tuple(sorted(parents_natural)), (
        "Parent canonical form is not sorted ascending."
    )


# ---- 8. The end-to-end runner script exits 0 ----

def test_demo_runner_exits_clean():
    """The deterministic runner script is a self-contained CI artifact;
    it must exit 0 to count as a passing demo."""
    import subprocess
    runner = REPO / "papers" / "composition-doctrine" / "sprint15_demo" / "runner.py"
    assert runner.exists(), f"Demo runner missing: {runner}"
    result = subprocess.run(
        [sys.executable, str(runner)],
        env={**__import__("os").environ, "PYTHONPATH": str(REPO / "bulla" / "src")},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"Sprint 15 demo runner exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Demo verified — local contracts passed" in result.stdout
