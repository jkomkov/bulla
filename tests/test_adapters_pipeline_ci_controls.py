"""Tests for pipeline_ci_controls.py + encoding-coarseness audit (G24 pre-A3).

Two test groups:

  1. ``TestPipelineSyntheticControls`` — verifies the bulla framework can
     register coordination obstructions on synthetic pipeline-shaped
     compositions. Mirrors G23 A1 controls discipline: known-vanishing
     and known-non-vanishing fixtures with exact recovery on k ∈ {1,2,3,5}.

  2. ``TestEncodeRepoCoarsenessAudit`` — directly inspects the edges
     produced by ``encode_repo()`` to verify whether the production
     encoding is structurally capable of producing fee>0. If every edge
     declares ``from_field`` from the source's observable_schema, then
     δ_full ≡ δ_obs by construction and the encoding cannot register
     obstruction regardless of repo state. This audit ships as the pre-A3
     sanity-check artifact.

Pre-A3 sanity-check verdict (per the user's required gate):
  * Synthetic controls PASS → bulla framework is sound on pipeline-shaped
    compositions; no framework-level fix needed.
  * Encoding coarseness audit FAILS → encode_repo() in pipeline_ci.py is
    structurally incapable of fee>0 with the current edge construction.
    A3 must NOT proceed against this encoding; revision required first.

The right next-step decision goes to the user: either revise encode_repo
to introduce hidden-field cross-edges (e.g., from file-existence
inspection of marker files like research-pull-*.md, sprint_*.md, or
from running verification primitives at encode time), or scope-reduce
A3 to demonstrate the existing encoding's limitations rather than
attempting H_EBL discovery.
"""

from __future__ import annotations

import pytest

from bulla.adapters.pipeline_ci import encode_repo
from bulla.adapters.pipeline_ci_controls import (
    build_known_nonvanishing_pipeline_control,
    build_known_vanishing_pipeline_control,
)
from bulla.diagnostic import diagnose

# Repo root for encode_repo audit
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
assert (REPO_ROOT / "papers").is_dir()


class TestPipelineSyntheticControls:
    """Mirror G23 A1 discipline: synthetic controls verify framework soundness."""

    @pytest.mark.parametrize("n_papers", [2, 3, 4, 8])
    def test_vanishing_fee_zero(self, n_papers):
        comp = build_known_vanishing_pipeline_control(n_papers=n_papers)
        diag = diagnose(comp)
        assert diag.coherence_fee == 0, (
            f"Synthetic vanishing pipeline-control with n_papers={n_papers} "
            f"produced fee={diag.coherence_fee}; expected 0. The framework "
            f"would be producing spurious obstruction on a pipeline-shaped "
            f"identity composition — bulla.coboundary or witness-geometry bug."
        )

    @pytest.mark.parametrize("k", [1, 2, 3, 5, 10])
    def test_nonvanishing_fee_exact_match(self, k):
        """Exact (±0) recovery on designed fee = k via hub-and-spoke."""
        comp = build_known_nonvanishing_pipeline_control(k=k)
        diag = diagnose(comp)
        assert diag.coherence_fee == k, (
            f"Synthetic non-vanishing pipeline-control with k={k} produced "
            f"fee={diag.coherence_fee}; expected EXACTLY {k} (±0). The bulla "
            f"framework is unable to register coordination obstructions on "
            f"pipeline-shaped hub-and-spoke compositions — framework bug."
        )

    def test_vanishing_below_two_rejected(self):
        with pytest.raises(ValueError, match=r"n_papers must be >= 2"):
            build_known_vanishing_pipeline_control(n_papers=1)

    def test_nonvanishing_zero_k_rejected(self):
        with pytest.raises(ValueError, match=r"k must be >= 1"):
            build_known_nonvanishing_pipeline_control(k=0)

    def test_synthetic_pipeline_demonstrates_framework_capability(self):
        """Atomic pre-A3 framework-soundness check.

        If this passes: bulla framework correctly registers obstructions
        on pipeline-shaped compositions. Any inability of encode_repo()
        to produce fee>0 from real repo state is therefore an encoding-
        layer issue, not a framework-layer issue.
        """
        # k=3 is the canonical Sprint 15 / G23 A1 hub-and-spoke at
        # mid-range; clean exact recovery here is the framework-soundness
        # signal.
        comp = build_known_nonvanishing_pipeline_control(k=3)
        diag = diagnose(comp)
        assert diag.coherence_fee == 3, (
            "Framework-soundness check FAILED: bulla cannot register "
            "obstruction on the canonical Sprint 15 / G23 A1 hub-and-"
            "spoke pattern in the pipeline-encoding domain. "
            "Investigate bulla.coboundary or witness-geometry before "
            "any G24 historical sweep."
        )


class TestEncodeRepoCoarsenessAudit:
    """Direct audit of whether encode_repo() can produce fee>0.

    Inspects the structure of edges produced by encode_repo() at HEAD
    to determine whether ANY edge declares from_field from the source's
    HIDDEN schema (i.e., a field in internal_state but not in
    observable_schema). If no such edge exists, δ_full ≡ δ_obs and the
    encoding is structurally incapable of producing fee>0.

    This audit produces the pre-A3 verdict the user required:
    confirms whether the production encoding is sound for A3 historical
    sweep or requires revision first.
    """

    @staticmethod
    def _edges_with_hidden_from_field(comp):
        """Return list of edges whose from_field is in source's hidden_schema."""
        tool_lookup = {t.name: t for t in comp.tools}
        hidden_field_edges = []
        for edge in comp.edges:
            src = tool_lookup[edge.from_tool]
            for dim in edge.dimensions:
                if dim.from_field is None:
                    continue
                # Hidden iff in internal_state but not observable_schema
                in_internal = dim.from_field in src.internal_state
                in_observable = dim.from_field in src.observable_schema
                if in_internal and not in_observable:
                    hidden_field_edges.append((edge, dim))
        return hidden_field_edges

    @staticmethod
    def _edges_with_hidden_to_field(comp):
        """Return list of edges whose to_field is in target's hidden_schema."""
        tool_lookup = {t.name: t for t in comp.tools}
        hidden_field_edges = []
        for edge in comp.edges:
            tgt = tool_lookup[edge.to_tool]
            for dim in edge.dimensions:
                if dim.to_field is None:
                    continue
                in_internal = dim.to_field in tgt.internal_state
                in_observable = dim.to_field in tgt.observable_schema
                if in_internal and not in_observable:
                    hidden_field_edges.append((edge, dim))
        return hidden_field_edges

    def test_encode_repo_at_head_produces_some_edges(self):
        """Sanity: encode_repo at HEAD has edges to audit."""
        comp = encode_repo(REPO_ROOT)
        assert len(comp.edges) >= 5, (
            f"encode_repo at HEAD produced only {len(comp.edges)} edges; "
            "audit cannot meaningfully assess obstruction capability."
        )

    def test_encode_repo_audit_no_hidden_from_field_edges_at_head(self):
        """DIAGNOSTIC: every edge's from_field is in source's observable_schema.

        This test PASSES (the assertion holds) when the audit confirms
        the encoding is too coarse. Failing this assertion would mean
        the encoding has hidden-field edges and IS capable of fee>0
        (better state than current).

        The "passing" of this test is the documented finding: the
        production encoding cannot produce fee>0 from real repo state
        because no edge declares a from_field outside the source's
        observable_schema.
        """
        comp = encode_repo(REPO_ROOT)
        hidden_from = self._edges_with_hidden_from_field(comp)
        assert len(hidden_from) == 0, (
            f"Found {len(hidden_from)} edges with hidden from_field "
            "(unexpected — would indicate encode_repo can produce fee>0). "
            "If this assertion fails, the encoding has been revised; "
            "re-run the spot-check against historical commits."
        )

    def test_encode_repo_audit_no_hidden_to_field_edges_at_head(self):
        """DIAGNOSTIC: every edge's to_field is in target's observable_schema."""
        comp = encode_repo(REPO_ROOT)
        hidden_to = self._edges_with_hidden_to_field(comp)
        assert len(hidden_to) == 0, (
            f"Found {len(hidden_to)} edges with hidden to_field "
            "(unexpected — would indicate encode_repo can produce fee>0). "
            "If this assertion fails, the encoding has been revised; "
            "re-run the spot-check against historical commits."
        )

    def test_encode_repo_at_head_fee_zero_consistent_with_audit(self):
        """Cross-validation: fee=0 at HEAD is consistent with the audit.

        Audit predicts: no hidden-field edges → δ_full ≡ δ_obs → fee = 0.
        diagnose result: fee = 0. Consistent.
        """
        comp = encode_repo(REPO_ROOT)
        diag = diagnose(comp)
        assert diag.coherence_fee == 0

    def test_pre_a3_sanity_check_verdict_documented(self):
        """The pre-A3 sanity check verdict (compound of above tests).

        VERDICT (as of commit 5a74e87 / dae2d4d / pending Step 8):
          * Framework soundness: PASS (synthetic controls produce
            exact fee=k for k ∈ {1,2,3,5,10}; framework registers
            obstructions correctly on pipeline-shaped compositions)
          * Encoding capability: FAIL (production encode_repo() has
            zero hidden-field edges; structurally incapable of fee>0
            regardless of historical commit content)
          * A3 readiness: NOT READY — encoding revision required
            before any historical sweep

        This test exists to make the verdict programmatically
        inspectable: it passes when the verdict is "encoding revision
        required" (the current state). After the encoding is revised,
        update the assertion to reflect the new verdict.
        """
        comp = encode_repo(REPO_ROOT)
        diag = diagnose(comp)
        hidden_from = self._edges_with_hidden_from_field(comp)
        hidden_to = self._edges_with_hidden_to_field(comp)

        # Current verdict assertion: encoding too coarse, A3 NOT READY
        assert diag.coherence_fee == 0, "Verdict assumes fee=0 at HEAD"
        assert len(hidden_from) == 0, "Verdict assumes no hidden from_field"
        assert len(hidden_to) == 0, "Verdict assumes no hidden to_field"

        # Synthetic positive control still works (framework sound)
        synth = build_known_nonvanishing_pipeline_control(k=3)
        synth_diag = diagnose(synth)
        assert synth_diag.coherence_fee == 3, (
            "Framework-soundness regressed: synthetic positive control "
            "no longer produces fee=k=3. Investigate bulla.coboundary."
        )
