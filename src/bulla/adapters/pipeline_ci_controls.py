"""Synthetic positive control for pipeline_ci (G24 A2 — pre-A3 sanity check).

REFACTORED 2026-05-06: build_known_*_pipeline_control now wrap the
public ``bulla.testing.synthetic_compositions`` utility (Refinement 2
from project_g24_next_phase.md). The pipeline-specific tool templates
(``_make_script_tool``, ``_make_paper_tool``) remain here because they
encode the pipeline's specific field structure; the cycle/hub-spoke
edge construction is now delegated to the public utility.

Per the G24 scoping note discipline (mirroring G23 A1's known-non-
vanishing positive control): before any historical commit sweep, verify
that the pipeline_ci encoding is *capable* of registering coordination
obstructions. A composition encoding that can only produce fee=0 would
make A3 vacuous — 600 commits of git checkouts would all return fee=0
regardless of whether real obstructions existed at those commits.

This module ships TWO synthetic controls:

  1. ``build_known_vanishing_pipeline_control`` — synthetic pipeline-
     shaped composition with all-observable edges; expected fee = 0.

  2. ``build_known_nonvanishing_pipeline_control`` — synthetic pipeline-
     shaped composition with hub-and-spoke hidden-field edges
     (mirroring Sprint 15 and G23 A1); expected fee = k for
     k ∈ {1, 2, 3, 5}, recovered exactly.

If the framework-soundness check (positive control) passes AND the
encoding-coarseness audit fails (encode_repo cannot produce fee>0), the
right next step is to REVISE encode_repo before A3. This module
provides the testbed for that revision: any future encode_repo update
can be validated against these synthetic controls, OR against the more
general ``bulla.testing.synthetic_compositions.build_known_nonvanishing``
when adapter-specific tool templates aren't required.
"""

from __future__ import annotations

from bulla.model import Composition, ToolSpec
from bulla.testing import (
    build_cycle_from_tools,
    build_hub_spoke_from_tools,
)

# ── Pipeline-shaped tool templates ───────────────────────────────────


def _make_script_tool(
    name: str,
    *,
    expose_check_field: bool = True,
) -> ToolSpec:
    """Synthetic verification-script ToolSpec template.

    internal_state always includes the script's check fields; observable
    only when ``expose_check_field=True``. This asymmetry is what allows
    hidden-field edges to register coordination obstructions in the
    positive control.
    """
    internal = ("input_files", "check_field", "verification_outcome")
    observable = (
        ("input_files", "check_field", "verification_outcome")
        if expose_check_field
        else ("input_files", "verification_outcome")
    )
    return ToolSpec(
        name=name,
        internal_state=internal,
        observable_schema=observable,
    )


def _make_paper_tool(
    name: str,
    *,
    expose_check_field: bool = True,
) -> ToolSpec:
    """Synthetic paper ToolSpec template.

    Mirrors the script template's expose pattern: ``check_field`` is
    observable iff the editorial check was demonstrably applied (marker
    file present at commit, or content actively reviewed).
    """
    internal = ("source_files", "check_field", "compiled_pdf")
    observable = (
        ("source_files", "check_field", "compiled_pdf")
        if expose_check_field
        else ("source_files", "compiled_pdf")
    )
    return ToolSpec(
        name=name,
        internal_state=internal,
        observable_schema=observable,
    )


# ── Controls (now wrap bulla.testing.synthetic_compositions) ─────────


def build_known_vanishing_pipeline_control(
    *,
    n_papers: int = 4,
) -> Composition:
    """Synthetic pipeline composition with all-observable edges.

    Refactored 2026-05-06 to wrap
    ``bulla.testing.build_cycle_from_tools``. Uses the heterogeneous
    (script + papers) tool list because the script and paper templates
    have different ToolSpec shapes, so the higher-level
    ``build_known_vanishing`` (homogeneous) doesn't apply.

    Constructs a script + (n_papers) papers, with cyclic edges declaring
    dimensions on observable fields only. Expected ``coherence_fee = 0``.

    The cyclic structure (β_1 = 1) tests the harder case where rank-
    deficient cycles could produce spurious obstruction if the encoding
    were buggy.

    Args:
        n_papers: number of paper ToolSpecs in the cycle. Must be >= 2.

    Returns:
        Composition with 1 script + n_papers paper tools, all with
        ``check_field`` observable; cyclic edges declaring identity
        dimensions on observable fields.
    """
    if n_papers < 2:
        raise ValueError(f"n_papers must be >= 2 for a cycle; got {n_papers}")

    script = _make_script_tool("script_synth_vanishing", expose_check_field=True)
    papers = tuple(
        _make_paper_tool(f"paper_synth_vanishing_{i}", expose_check_field=True)
        for i in range(n_papers)
    )
    # Cycle: script → paper_0 → paper_1 → ... → paper_{n-1} → script
    tools = (script,) + papers

    return build_cycle_from_tools(
        name=f"pipeline_vanishing_n{n_papers}",
        tools=tools,
        edge_dimension_field="check_field",
        edge_name_prefix="check_match",
    )


def build_known_nonvanishing_pipeline_control(
    *,
    k: int,
) -> Composition:
    """Synthetic pipeline composition with designed ``coherence_fee = k``.

    Refactored 2026-05-06 to wrap
    ``bulla.testing.build_hub_spoke_from_tools``. Uses the heterogeneous
    (script-as-hub + papers-as-spokes) tool list because the script and
    paper templates have different ToolSpec shapes.

    Hub-and-spoke construction (mirrors G23 A1 + Sprint 15 fixture):

      * Hub: 1 verification script with ``check_field`` EXPOSED in
        observable_schema (the script's positive verdict is publicly
        observable).
      * Spokes: (k+1) papers with ``check_field`` HIDDEN (in
        internal_state but NOT observable_schema) — modelling papers
        that have NOT had the editorial check demonstrably applied at
        this commit (marker file absent / review not run).
      * Edges: (k+1) edges from hub script to each spoke paper, each
        declaring a unique semantic dimension on ``check_field``.

    Coboundary mechanics:
        ``δ_obs``: (k+1) rows, each ``[-1 at hub.check_field]`` (hub
        observable; target hidden so no +1 entry on spoke side). Rows
        identical ⇒ rank = 1.

        ``δ_full``: (k+1) rows, each with -1 at hub.check_field and +1
        at distinct spoke.check_field columns ⇒ rank = k+1.

        ``coherence_fee = (k+1) - 1 = k``.

    Args:
        k: target ``coherence_fee``. Must be >= 1.

    Returns:
        Composition with 1 hub script + (k+1) spoke papers + (k+1)
        edges. Expected ``diagnose(comp).coherence_fee == k``.

    Raises:
        ValueError: if ``k < 1``.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1; got {k}")

    n_spokes = k + 1

    hub = _make_script_tool("script_synth_nonvanishing_hub", expose_check_field=True)
    spokes = tuple(
        _make_paper_tool(
            f"paper_synth_nonvanishing_spoke_{i}",
            expose_check_field=False,
        )
        for i in range(n_spokes)
    )

    return build_hub_spoke_from_tools(
        name=f"pipeline_nonvanishing_k{k}",
        hub=hub,
        spokes=spokes,
        obstruction_field="check_field",
        edge_name_prefix="check_match",
    )
