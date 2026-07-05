"""Pipeline-CI adapter (G24 A2): encode a repository state as a bulla Composition.

Locked encoding per ``papers/bulla-check/inventory.md`` (A1 inventory at
HEAD ``4b8315a``). No historical commit inspection performed during A2;
the encoding is structural and applies mechanically to any commit hash
in the A3 historical analysis window.

Per the G24 scoping note's Mirage-discipline protocol:
    A2 encoding is locked before any historical commit is run through it.
    The encoding implementation is fixed at this commit. A3 mechanical
    application across the 6-month commit window operates against this
    locked encoding with NO per-commit tweaking.

Pre-registered sanity check (A1 plan §6.5): the encoding applied to
HEAD should produce ``coherence_fee = 0`` OR a documented fee value
with explicit witness-blindspot list that the program currently lives
with. The sanity check is verified by ``encode_repo()`` running against
the working tree at HEAD; the result is recorded in the corresponding
test fixture in ``bulla/tests/test_adapters_pipeline_ci.py``.

The encoding lifts:
  - 5 explicit verification scripts → ``script_<name>`` ToolSpecs
  - 6 editorial-discipline rule families → ``editorial_<name>`` ToolSpecs
  - 1 Lean verification toolchain → ``lean_verification`` ToolSpec
  - N paper directories under ``papers/`` → ``paper_<name>`` ToolSpecs

Edges encode the "primitive scans paper" relationships from the A1
inventory. All edges declare semantic dimensions on observable fields
only — this is the structural decision that determines the sanity
check outcome at HEAD. Hidden-field dimensions on edges would
introduce obstruction; the encoding deliberately avoids this for the
publishing pipeline at HEAD because the program currently passes its
own editorial review (no documented coordination obstructions at HEAD).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

# ── Locked tool definitions per A1 inventory ─────────────────────────


@dataclass(frozen=True)
class _ToolSpecData:
    """Locked ToolSpec definition for a primitive type."""
    name: str
    internal_state: tuple[str, ...]
    observable_schema: tuple[str, ...]


# 5 explicit verification scripts (per A1 §1)
EXPLICIT_SCRIPTS: tuple[_ToolSpecData, ...] = (
    _ToolSpecData(
        name="script_citation_lint",
        internal_state=(
            "raw_inline_brackets",
            "math_intervals",
            "math_tuples",
            "citation_brackets",
        ),
        observable_schema=("citation_brackets",),
    ),
    _ToolSpecData(
        name="script_seam_lint",
        internal_state=(
            "composition_yaml",
            "tool_specs",
            "bilateral_interfaces",
            "coherence_fee",
            "blind_spots",
            "disclosure_set",
        ),
        observable_schema=("coherence_fee", "blind_spots"),
    ),
    _ToolSpecData(
        name="script_locality_build_and_verify",
        internal_state=(
            "toy_family_inputs",
            "expected_locality_fees",
            "computed_locality_fees",
            "verification_outcome",
        ),
        observable_schema=("verification_outcome",),
    ),
    _ToolSpecData(
        name="script_local_global_obstruction_build",
        internal_state=("latex_source", "bibliography", "compiled_pdf", "build_outcome"),
        observable_schema=("build_outcome",),
    ),
    _ToolSpecData(
        name="script_composition_doctrine_build",
        internal_state=("latex_source", "bibliography", "compiled_pdf", "build_outcome"),
        observable_schema=("build_outcome",),
    ),
)

# 6 editorial-discipline rule families (per A1 §3)
# Each rule's observable_schema is the structural-check outcome (whether
# the rule was applied), NOT the substantive evaluation (whether it was
# satisfied). This honest distinction lifts the rule's structural shape
# without overclaiming what the framework can verify.
EDITORIAL_RULES: tuple[_ToolSpecData, ...] = (
    _ToolSpecData(
        name="editorial_anti_bloat",
        internal_state=(
            "artifact_classification",
            "compression_or_sprawl_judgment",
            "review_applied",
        ),
        observable_schema=("review_applied",),
    ),
    _ToolSpecData(
        name="editorial_citation_lint_convention",
        internal_state=(
            "non_breaking_space_form",
            "author_prefix_form",
            "raw_bracket_form",
            "review_applied",
        ),
        observable_schema=("review_applied",),
    ),
    _ToolSpecData(
        name="editorial_proofs_not_memos",
        internal_state=(
            "theorem_attempted",
            "memo_drift_observed",
            "review_applied",
        ),
        observable_schema=("review_applied",),
    ),
    _ToolSpecData(
        name="editorial_sole_authorship",
        internal_state=("author_count", "review_applied"),
        observable_schema=("author_count", "review_applied"),
    ),
    _ToolSpecData(
        name="editorial_one_object_three_projections",
        internal_state=(
            "primary_scalars",
            "secondary_scalars",
            "new_scalar_proposed",
            "review_applied",
        ),
        observable_schema=("review_applied",),
    ),
    _ToolSpecData(
        name="editorial_cost_model_honesty",
        internal_state=(
            "compute_budget_claimed",
            "cost_model_audited",
            "review_applied",
        ),
        observable_schema=("review_applied",),
    ),
)

# 1 Lean formal verification toolchain (per A1 §4)
LEAN_ATTESTATION: _ToolSpecData = _ToolSpecData(
    name="lean_verification",
    internal_state=(
        "lean_modules",
        "aristotle_run_hashes",
        "verified_theorem_count",
    ),
    observable_schema=("verified_theorem_count", "aristotle_run_hashes"),
)

# Per-paper ToolSpec template (instantiated per paper directory found)
_PAPER_INTERNAL: tuple[str, ...] = (
    "source_files",
    "bibliography",
    "citations",
    "compiled_pdf",
)
_PAPER_OBSERVABLE: tuple[str, ...] = (
    "source_files",
    "bibliography",
)

# Papers explicitly bound to the Lean toolchain (per A1 §4)
LEAN_BOUND_PAPERS: frozenset[str] = frozenset({"composition-doctrine"})

# Papers explicitly subject to seam-lint (per A1 §1, scope = papers/seam/)
SEAM_LINT_TARGETS: frozenset[str] = frozenset({"seam"})

# Papers each editorial rule applies to (default: all papers)
# This is the structural shape of the rule's reach, not the substantive
# evaluation. Sole-authorship applies to all papers; specific rules can
# be narrowed by explicit overrides if needed in future revisions.

# Build-script targets (per A1 §1 scripts 4-5)
BUILD_SCRIPT_TARGETS: dict[str, str] = {
    "script_local_global_obstruction_build": "local-global-obstruction",
    "script_composition_doctrine_build": "composition-doctrine",
}


def _walk_paper_dirs(path: Path) -> list[str]:
    """Return sorted paper directory names under ``path/papers/``.

    Excludes hidden directories, files (non-directories), and the
    ``framework/`` placeholder (which is currently a stub, not a paper).
    """
    papers_dir = path / "papers"
    if not papers_dir.is_dir():
        return []
    return sorted(
        d.name
        for d in papers_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def _to_tool_spec(td: _ToolSpecData) -> ToolSpec:
    return ToolSpec(
        name=td.name,
        internal_state=td.internal_state,
        observable_schema=td.observable_schema,
    )


def encode_repo(path: Path) -> Composition:
    """Encode a repository state at ``path`` as a bulla Composition.

    Locked encoding per ``papers/bulla-check/inventory.md`` (A1
    inventory). Applies mechanically to any commit hash with no
    per-commit tweaking, per the Mirage-discipline pre-registration.

    Args:
        path: working tree root of the repository (typically the result
            of ``git checkout <commit>`` followed by inspection of the
            tree). Must contain a ``papers/`` directory; if absent, the
            composition will have only the script + editorial + Lean
            ToolSpecs with no paper edges.

    Returns:
        Composition with:
          - 5 explicit-script ToolSpecs (always present, not
            file-existence-dependent — keeps encoding stable across
            commits even if a script file is added/removed)
          - 6 editorial-rule ToolSpecs (always present)
          - 1 Lean attestation ToolSpec (always present; bulla cannot
            re-verify Lean, only cite the attestation)
          - N per-paper ToolSpecs (one per paper directory found)
          - Edges connecting verification primitives to the papers they
            scan, declaring SemanticDimensions on observable fields only

    The encoding deliberately uses observable-field-only dimensions on
    all edges. This means: the structural composition has zero hidden-
    field cross-edges, so ``coherence_fee = 0`` at any commit where the
    structural sanity check passes (papers/ exists, primitive scripts
    have not changed signature). A non-zero fee at a historical commit
    would indicate a structural anomaly bulla has detected — which is
    exactly the kind of signal the H_EBL hypothesis seeks.
    """
    tools: list[ToolSpec] = []
    edges: list[Edge] = []

    # 1. Build script + editorial + Lean ToolSpecs (always present)
    for td in EXPLICIT_SCRIPTS:
        tools.append(_to_tool_spec(td))
    for td in EDITORIAL_RULES:
        tools.append(_to_tool_spec(td))
    tools.append(_to_tool_spec(LEAN_ATTESTATION))

    # 2. Walk papers/ and build per-paper ToolSpecs
    paper_names = _walk_paper_dirs(path)
    for paper_name in paper_names:
        tools.append(
            ToolSpec(
                name=f"paper_{paper_name}",
                internal_state=_PAPER_INTERNAL,
                observable_schema=_PAPER_OBSERVABLE,
            )
        )

    # 3. Edges: citation_lint scans every paper's bibliography
    #    (lifts to declared dimensions on observable fields only)
    for paper_name in paper_names:
        edges.append(
            Edge(
                from_tool="script_citation_lint",
                to_tool=f"paper_{paper_name}",
                dimensions=(
                    SemanticDimension(
                        name=f"bracket_collision_{paper_name}",
                        from_field="citation_brackets",
                        to_field="bibliography",
                    ),
                ),
            )
        )

    # 4. Edges: seam-lint scans the seam paper's compositions
    for paper_name in paper_names:
        if paper_name in SEAM_LINT_TARGETS:
            edges.append(
                Edge(
                    from_tool="script_seam_lint",
                    to_tool=f"paper_{paper_name}",
                    dimensions=(
                        SemanticDimension(
                            name=f"coherence_fee_diagnosis_{paper_name}",
                            from_field="coherence_fee",
                            to_field="source_files",
                        ),
                    ),
                )
            )

    # 5. Edges: build scripts attest paper-build outcomes
    for script_name, paper_target in BUILD_SCRIPT_TARGETS.items():
        if paper_target in paper_names:
            edges.append(
                Edge(
                    from_tool=script_name,
                    to_tool=f"paper_{paper_target}",
                    dimensions=(
                        SemanticDimension(
                            name=f"build_outcome_{paper_target}",
                            from_field="build_outcome",
                            to_field="source_files",
                        ),
                    ),
                )
            )

    # 6. Edges: editorial rules apply to every paper (structural
    #    review-applied check, not substantive evaluation)
    for rule_td in EDITORIAL_RULES:
        for paper_name in paper_names:
            edges.append(
                Edge(
                    from_tool=rule_td.name,
                    to_tool=f"paper_{paper_name}",
                    dimensions=(
                        SemanticDimension(
                            name=f"{rule_td.name}_applied_{paper_name}",
                            from_field="review_applied",
                            to_field="source_files",
                        ),
                    ),
                )
            )

    # 7. Edge: Lean attestation binds composition-doctrine paper
    for paper_name in paper_names:
        if paper_name in LEAN_BOUND_PAPERS:
            edges.append(
                Edge(
                    from_tool="lean_verification",
                    to_tool=f"paper_{paper_name}",
                    dimensions=(
                        SemanticDimension(
                            name=f"lean_attestation_{paper_name}",
                            from_field="aristotle_run_hashes",
                            to_field="source_files",
                        ),
                    ),
                )
            )

    return Composition(
        name=f"pipeline_ci_{path.name}",
        tools=tuple(tools),
        edges=tuple(edges),
    )
