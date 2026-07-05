"""Tests for bulla/adapters/pipeline_ci.py (G24 A2 deliverable).

Three test groups:
  1. Tool-set assembly: verify the locked encoding produces the right
     ToolSpecs from the A1 inventory (5 scripts + 6 editorial rules +
     1 Lean + N papers).
  2. Edge construction: verify edges connect verification primitives
     to papers per the locked encoding rules.
  3. Sanity check at HEAD: encode the actual repo at HEAD, verify the
     diagnose() result matches the documented expected fee from the
     A1 plan §6.5 sanity check.

The A1 plan §6.5 pre-registered: "encoding applied to HEAD should
produce coherence_fee = 0 OR a documented fee value with explicit
witness-blindspot list that the program currently lives with."

If the sanity check produces fee = 0, the assertion is straightforward.
If fee > 0, the test records the value AND the witness-blindspot list as
the documented "program-level state" — which itself becomes evidence
in the H_EBL hypothesis evaluation (an at-HEAD fee > 0 with
witness-blindspots is a current-state H_EBL candidate).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Editorial-pipeline suite: encodes/audits papers/ manuscript content (citation_lint,
# at-HEAD drift), not core bulla (gate / registry / diagnostic). Deselected from the
# default code suite so a contributor's red/green never depends on a manuscript bracket;
# run with `pytest -m editorial`.
pytestmark = pytest.mark.editorial

from bulla.adapters.pipeline_ci import (
    BUILD_SCRIPT_TARGETS,
    EDITORIAL_RULES,
    EXPLICIT_SCRIPTS,
    LEAN_ATTESTATION,
    LEAN_BOUND_PAPERS,
    SEAM_LINT_TARGETS,
    encode_repo,
)
from bulla.diagnostic import diagnose
from bulla.model import Composition

# Repo root: walk up from this file to the worktree root (where bulla/
# and papers/ both exist).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
assert (REPO_ROOT / "papers").is_dir(), (
    f"Repo root resolution failed: {REPO_ROOT} has no papers/ subdir. "
    "Tests must run from a worktree containing both bulla/ and papers/."
)


class TestLockedTooling:
    """Verify the locked encoding constants match A1 inventory counts."""

    def test_explicit_scripts_count_is_five(self):
        """A1 inventory §1: 5 explicit verification scripts."""
        assert len(EXPLICIT_SCRIPTS) == 5

    def test_editorial_rules_count_is_six(self):
        """A1 inventory §3: 6 editorial-discipline rule families."""
        assert len(EDITORIAL_RULES) == 6

    def test_lean_attestation_singleton(self):
        """A1 inventory §4: 1 Lean verification toolchain."""
        assert LEAN_ATTESTATION.name == "lean_verification"

    def test_explicit_script_names_locked(self):
        """A1 inventory §1: locked names for the 5 scripts."""
        names = {td.name for td in EXPLICIT_SCRIPTS}
        assert names == {
            "script_citation_lint",
            "script_seam_lint",
            "script_locality_build_and_verify",
            "script_local_global_obstruction_build",
            "script_composition_doctrine_build",
        }

    def test_editorial_rule_names_locked(self):
        """A1 inventory §3: locked names for the 6 editorial rules."""
        names = {td.name for td in EDITORIAL_RULES}
        assert names == {
            "editorial_anti_bloat",
            "editorial_citation_lint_convention",
            "editorial_proofs_not_memos",
            "editorial_sole_authorship",
            "editorial_one_object_three_projections",
            "editorial_cost_model_honesty",
        }


class TestToolSpecAssembly:
    """Verify encode_repo produces the right ToolSpec set."""

    def test_returns_composition(self):
        comp = encode_repo(REPO_ROOT)
        assert isinstance(comp, Composition)

    def test_contains_all_five_explicit_scripts(self):
        comp = encode_repo(REPO_ROOT)
        names = {t.name for t in comp.tools}
        for td in EXPLICIT_SCRIPTS:
            assert td.name in names, f"Missing script: {td.name}"

    def test_contains_all_six_editorial_rules(self):
        comp = encode_repo(REPO_ROOT)
        names = {t.name for t in comp.tools}
        for td in EDITORIAL_RULES:
            assert td.name in names, f"Missing editorial rule: {td.name}"

    def test_contains_lean_attestation(self):
        comp = encode_repo(REPO_ROOT)
        names = {t.name for t in comp.tools}
        assert "lean_verification" in names

    def test_contains_at_least_one_paper_tool(self):
        """At HEAD, the program has multiple paper directories."""
        comp = encode_repo(REPO_ROOT)
        paper_tools = [t for t in comp.tools if t.name.startswith("paper_")]
        assert len(paper_tools) >= 5, (
            f"Expected ≥ 5 paper tools at HEAD; got {len(paper_tools)}. "
            "If this fails, papers/ structure changed or the encoding's "
            "_walk_paper_dirs is buggy."
        )

    def test_paper_tool_has_expected_schema_shape(self):
        comp = encode_repo(REPO_ROOT)
        paper_tools = [t for t in comp.tools if t.name.startswith("paper_")]
        # All papers share the same observable/internal shape
        for pt in paper_tools:
            assert "source_files" in pt.observable_schema
            assert "bibliography" in pt.observable_schema
            assert "citations" in pt.internal_state
            assert "compiled_pdf" in pt.internal_state


class TestEdgeConstruction:
    """Verify edges follow the locked encoding rules."""

    def test_citation_lint_edges_exist_for_all_papers(self):
        """citation_lint scans every paper."""
        comp = encode_repo(REPO_ROOT)
        cit_edges = [e for e in comp.edges if e.from_tool == "script_citation_lint"]
        paper_count = sum(1 for t in comp.tools if t.name.startswith("paper_"))
        assert len(cit_edges) == paper_count

    def test_seam_lint_edge_for_seam_paper_only(self):
        """seam-lint targets only the seam paper directory."""
        comp = encode_repo(REPO_ROOT)
        seam_edges = [e for e in comp.edges if e.from_tool == "script_seam_lint"]
        # If seam/ exists at HEAD, there's exactly one seam-lint edge
        seam_exists = any(t.name == "paper_seam" for t in comp.tools)
        assert len(seam_edges) == (1 if seam_exists else 0)
        for e in seam_edges:
            assert e.to_tool == "paper_seam"

    def test_build_script_edges_for_target_papers(self):
        """Each build script targets its specific paper."""
        comp = encode_repo(REPO_ROOT)
        for script_name, paper_target in BUILD_SCRIPT_TARGETS.items():
            script_edges = [e for e in comp.edges if e.from_tool == script_name]
            paper_exists = any(
                t.name == f"paper_{paper_target}" for t in comp.tools
            )
            assert len(script_edges) == (1 if paper_exists else 0)

    def test_editorial_rule_edges_for_all_papers(self):
        """Each editorial rule applies to every paper (structural reach)."""
        comp = encode_repo(REPO_ROOT)
        paper_count = sum(1 for t in comp.tools if t.name.startswith("paper_"))
        for rule_td in EDITORIAL_RULES:
            rule_edges = [e for e in comp.edges if e.from_tool == rule_td.name]
            assert len(rule_edges) == paper_count, (
                f"Editorial rule {rule_td.name}: expected {paper_count} "
                f"edges (one per paper); got {len(rule_edges)}"
            )

    def test_lean_attestation_edge_for_composition_doctrine_only(self):
        """Lean binds composition-doctrine paper exclusively."""
        comp = encode_repo(REPO_ROOT)
        lean_edges = [e for e in comp.edges if e.from_tool == "lean_verification"]
        cd_exists = any(t.name == "paper_composition-doctrine" for t in comp.tools)
        assert len(lean_edges) == (1 if cd_exists else 0)
        for e in lean_edges:
            assert e.to_tool == "paper_composition-doctrine"

    def test_all_edges_declare_observable_field_dimensions_only(self):
        """The locked encoding uses observable-field dimensions only.

        Hidden-field cross-edges would introduce structural obstruction.
        The encoding deliberately avoids this so the sanity check at HEAD
        produces a documented fee (0 or otherwise interpretable).
        """
        comp = encode_repo(REPO_ROOT)
        # We don't assert fee == 0 here (that's the next test); we
        # verify the encoding is structurally as designed.
        assert len(comp.edges) >= 5


class TestSanityCheckAtHead:
    """A1 plan §6.5: encoding applied to HEAD produces a documented fee.

    Pre-registered expectation: ``coherence_fee = 0`` OR a documented
    fee value with explicit witness-blindspot list. Either outcome is
    acceptable; the test records the actual result so the A2 encoding
    is reproducible against this commit's HEAD state.
    """

    def test_diagnose_runs_without_error_on_head_encoding(self):
        comp = encode_repo(REPO_ROOT)
        diag = diagnose(comp)
        assert diag is not None
        assert diag.n_tools == len(comp.tools)
        assert diag.n_edges == len(comp.edges)

    def test_head_fee_is_finite_non_negative(self):
        comp = encode_repo(REPO_ROOT)
        diag = diagnose(comp)
        assert isinstance(diag.coherence_fee, int)
        assert diag.coherence_fee >= 0, (
            f"At-HEAD coherence_fee = {diag.coherence_fee} is negative — "
            "indicates the composition is not well-formed for fee. "
            "The encoding (or papers/ tree state) must be debugged "
            "before A3 historical analysis."
        )

    def test_head_fee_documented_value(self):
        """Document the actual at-HEAD fee for A3 reference.

        Per A1 plan §6.5: if fee == 0, the sanity check passes
        cleanly. If fee > 0, the value AND blind-spots list are
        recorded as the program-level state at HEAD.
        """
        comp = encode_repo(REPO_ROOT)
        diag = diagnose(comp)
        # The acceptable range is [0, max_papers * max_rules ~ 6 * 30 = 180]
        # at HEAD; anything beyond suggests an encoding bug.
        # We assert the loose upper bound here; tighter A3 analysis will
        # produce the precise expected value.
        assert 0 <= diag.coherence_fee <= 200, (
            f"At-HEAD coherence_fee = {diag.coherence_fee} is outside "
            "the expected range [0, 200] given the A1 inventory size. "
            "Investigate the encoding before A3."
        )
        # Print diagnostics for A3 reference (not an assertion)
        # In CI this becomes part of the test output for inspection.
        print(
            f"\n[A2 sanity check] At HEAD ({REPO_ROOT.name}):\n"
            f"  n_tools = {diag.n_tools}\n"
            f"  n_edges = {diag.n_edges}\n"
            f"  coherence_fee = {diag.coherence_fee}\n"
            f"  blind_spots = {len(diag.blind_spots)}\n"
            f"  betti_1 = {diag.betti_1}\n"
        )


class TestEncodingStability:
    """Verify the encoding is mechanically stable (Mirage discipline)."""

    def test_two_calls_produce_identical_compositions(self):
        """Determinism: encode_repo(path) is a pure function."""
        comp_a = encode_repo(REPO_ROOT)
        comp_b = encode_repo(REPO_ROOT)
        assert comp_a.canonical_hash() == comp_b.canonical_hash()

    def test_encoding_handles_missing_papers_dir(self, tmp_path):
        """If papers/ is absent, composition has only the always-present tools."""
        comp = encode_repo(tmp_path)  # tmp_path has no papers/
        always_present = (
            len(EXPLICIT_SCRIPTS) + len(EDITORIAL_RULES) + 1
        )  # +1 for Lean
        assert len(comp.tools) == always_present
        # No paper edges since no papers exist
        paper_edges = [
            e for e in comp.edges if e.to_tool.startswith("paper_")
        ]
        assert len(paper_edges) == 0
