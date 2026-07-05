"""Static-content lint re-implementation for G24 A3 historical sweep (Path D).

Per project_g24_next_phase.md Step 13 (Path D + magnitude extension):
re-implement each lint primitive's detection logic as **static-content
checks** (regex/AST applied directly to git-extracted file content),
NOT live script execution. This avoids both:

  - **File-existence-based encoding's signal-weakness** (Path A):
    marker files measure review process, not bug presence; a commit with
    a real coordination obstruction AND citation_lint.py present AND
    research-pull memo present would register fee=0.

  - **Live-script-execution's toolchain-replay contamination** (Path B):
    tectonic builds at 6-month-old commits fail for tectonic-version
    drift unrelated to coordination obstructions.

Path D applies the **current** lint logic (this module) to **historical**
file content via simple regex on git-checked-out paper.{md,tex}. Pure
Python, no toolchain dependencies, replayable indefinitely.

# Magnitude extension (locked at commit 5ffaf96 / public utility design)

Each paper-tool gets ONE observable per primitive type (``passes_<name>``
field). At a given commit, the observable_schema includes ``passes_X``
iff primitive X PASSES on that paper. Failed primitives are HIDDEN
(field is in internal_state but not observable_schema).

Per-primitive fee arithmetic (Sprint 15 hub-and-spoke math, applied to
the asymmetric pass/fail spoke set):

  Let n_passes = # papers where primitive passes; n_fails = # papers
  where primitive fails. The primitive-tool is the hub (always exposes
  ``check_outcome`` observably). Each paper-tool is a spoke. The edge
  declares (from_field=check_outcome, to_field=passes_<primitive>).

    rank(δ_obs)  = n_passes + min(1, n_fails)
                   (n_passes distinct +1-column rows + 1 row from any
                   failing paper, contributing -1 at hub.check_outcome)
    rank(δ_full) = n_passes + n_fails
                   (all rows distinct because each paper has a distinct
                   passes_<primitive> column)
    fee_per_primitive = rank(δ_full) - rank(δ_obs)
                      = n_fails - min(1, n_fails)
                      = max(0, n_fails - 1)

Total fee at a commit = sum across primitives of max(0, n_fails - 1).

This is a **continuous** signal proportional to # failing primitive-
paper pairs (modulo the -1 normalisation per primitive, which mirrors
Sprint 15's fee=k-1 convention). Sharper than binary precision/recall
on a thresholded signal — enables Spearman ρ correlation with bug-fix
commit count in subsequent commits.

# Sanity checks at HEAD (G24 plan §6.5)

  - At HEAD where all primitives pass on all papers: fee = 0.
  - On a synthetic hand-built composition where N papers fail one
    primitive: fee = max(0, N - 1).
  - Synthetic positive control via the public utility
    ``bulla.testing.build_known_nonvanishing(k)`` recovers fee=k for
    k ∈ {1, 2, 3, 5, 10}.

# Drift control (Step 14 of project_g24_next_phase.md, deferred)

The static checks here MUST produce identical pass/fail outcomes to the
live scripts on the current papers/ tree. Tested in
``tests/test_adapters_pipeline_ci_static.py::TestDriftControl`` (TODO).
If the re-implementation drifts from the live scripts, fix or document
before A3 historical sweep.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

# ── Static lint primitive: citation_lint (bracket-collision) ─────────

# Multi-element bracket tokens like [1, 10] (cost interval), [1, 1, 1]
# (partition), or [1, 5, 10] (multi-citation). These collide with math
# notation; current convention is to use ~[N] non-breaking-space form
# or author-prefix to disambiguate.
_MULTI_BRACKET_PATTERN = re.compile(r"\[[0-9]+(?:\s*,\s*[0-9]+)+\]")

# Heuristic math-context markers on the same line as a bracket token.
# Aligned with papers/citation_lint.py's is_math_context() drift-control
# (verified 2026-05-06 against composition-doctrine and witness-geometry-
# beyond-fee, both of which use bracket math pervasively).
_MATH_CONTEXT_KEYWORDS = (
    # Math-flavored words
    "interval", "intervals",
    "range", "ranges",
    "between",
    "tuple", "tuples",
    "partition", "partitions",
    # LaTeX math delimiters / commands (any presence on the line is a
    # strong math-context signal)
    "$", "\\frac", "\\in", "\\subset",
    "\\subseteq", "\\subsetneq",
    "\\to", "\\rightarrow", "\\leftarrow", "\\mapsto",
    "\\Rightarrow", "\\Leftarrow", "\\Leftrightarrow",
    "\\equiv", "\\neq", "\\sim", "\\approx", "\\propto",
    "\\le", "\\ge", "\\leq", "\\geq", "\\ll", "\\gg",
    "\\mathrm", "\\mathsf", "\\mathbb", "\\mathcal", "\\mathfrak",
    "\\mathit", "\\mathbf", "\\operatorname",
    "\\cdot", "\\times", "\\div",
    "\\sum", "\\prod", "\\int", "\\bigcup", "\\bigcap",
    "\\forall", "\\exists",
    # ASCII unicode math markers
    "∈",  # ∈
    "→",  # →
    "⊆",  # ⊆
    "∩", "∪",  # ∩, ∪
    "≤", "≥", "≠",
    # Cost / budget interval annotations (witness-geometry-beyond-fee
    # uses [1, 10] pervasively as cost intervals).
    "cost",
    "budget",
    "weight",
    "coordinate",
    "magnitude",
    "axis",
    "fee",
    "rank",
)


def _is_math_context_static(line: str, col: int) -> bool:
    """Conservative math-context check on a line containing a bracket token at `col`.

    Returns True if the bracket is in math context; False if it appears
    citation-like and would trigger the lint. Aligned 2026-05-06 with
    ``papers/citation_lint.py`` after drift-control test on
    composition-doctrine ``\(E: ... \to [0, 1]\)`` revealed missing
    ``\(...\)`` math span + LaTeX command coverage.

    Detects:
      * Inside ``$...$`` math span containing the column.
      * Inside an inline-code backtick span.
      * Inside a LaTeX ``\(...\)`` inline math span containing the column.
      * Common math keywords/operators on the same line.
    """
    # $...$ math span containing this column?
    n_dollars_before = 0
    in_inline_code = False
    for i, c in enumerate(line[:col]):
        if c == "`":
            in_inline_code = not in_inline_code
        elif c == "$" and not in_inline_code:
            n_dollars_before += 1
    if n_dollars_before % 2 == 1 or in_inline_code:
        return True
    # \(...\) LaTeX inline math span containing the column?
    # Count opens (``\(``) and closes (``\)``) before the column. If
    # opens > closes before col, we're inside a math span.
    n_opens_before = 0
    n_closes_before = 0
    i = 0
    while i < col - 1:
        if line[i] == "\\" and i + 1 < len(line):
            if line[i + 1] == "(":
                n_opens_before += 1
                i += 2
                continue
            if line[i + 1] == ")":
                n_closes_before += 1
                i += 2
                continue
        i += 1
    if n_opens_before > n_closes_before:
        return True
    # Same-line math keyword? (case-insensitive substring match)
    line_lower = line.lower()
    for kw in _MATH_CONTEXT_KEYWORDS:
        if kw in line_lower:
            return True
    return False


def check_citation_lint_passes(file_content: str) -> bool:
    """True iff no ambiguous (non-math) multi-bracket tokens found.

    Re-implements ``papers/citation_lint.py`` detection logic as a
    static content check. Used by ``encode_repo_static`` per paper.
    """
    for line in file_content.splitlines():
        for m in _MULTI_BRACKET_PATTERN.finditer(line):
            if not _is_math_context_static(line, m.start()):
                return False  # ambiguous bracket — lint would flag
    return True


# ── Static lint primitive: bibliography orphan ───────────────────────

_BIBITEM_PATTERN = re.compile(r"\\bibitem\{([^}]+)\}")
_CITE_PATTERN = re.compile(r"\\cite[a-z]*\{([^}]+)\}")


def check_bibliography_orphan_passes(file_content: str) -> bool:
    """True iff every \\bibitem{X} in the file has a matching \\cite{X}.

    Re-implements the orphan-biblio diff used in C2 sprint
    (``papers/SPRINT-C1-C2-REVIEW-GATE.md``) as a static content check.
    Detects pre-C2-style bibliography orphans on historical commits.
    """
    bibitem_keys = set(_BIBITEM_PATTERN.findall(file_content))
    cite_keys: set[str] = set()
    for cite_group in _CITE_PATTERN.findall(file_content):
        # \cite{a,b,c} → split on comma, strip whitespace
        for k in cite_group.split(","):
            cite_keys.add(k.strip())
    orphans = bibitem_keys - cite_keys
    return len(orphans) == 0


# ── Primitive registry ───────────────────────────────────────────────

# (primitive_name, file_extension_filter, check_function)
# file_extension_filter: tuple of extensions; primitive applies to
# paper.{ext} files only. Empty tuple = applies to no files (vacuous
# pass).
_LintCheck = Callable[[str], bool]
_PRIMITIVE_REGISTRY: tuple[tuple[str, tuple[str, ...], _LintCheck], ...] = (
    ("citation_lint", (".md", ".tex"), check_citation_lint_passes),
    ("bibliography_orphan", (".tex",), check_bibliography_orphan_passes),
)

# Public read-only accessor for tests + future drift-control validation.
LINT_PRIMITIVES = _PRIMITIVE_REGISTRY


def primitive_names() -> tuple[str, ...]:
    """Names of all static-content lint primitives."""
    return tuple(name for name, _, _ in _PRIMITIVE_REGISTRY)


# ── Per-paper primitive evaluation ───────────────────────────────────


def scan_paper_primitives(paper_dir: Path) -> dict[str, bool]:
    """Run each primitive's static check on the paper directory's content.

    Returns a dict mapping primitive_name → passes (True/False). If a
    paper has no files matching a primitive's extension filter, the
    primitive vacuously passes for that paper (no signal contributed).
    """
    results: dict[str, bool] = {}
    for primitive_name, exts, check_fn in _PRIMITIVE_REGISTRY:
        files: list[Path] = []
        for ext in exts:
            files.extend(paper_dir.rglob(f"paper{ext}"))
        if not files:
            results[primitive_name] = True  # vacuous pass
            continue
        all_content = "\n".join(
            f.read_text(encoding="utf-8", errors="replace") for f in files
        )
        results[primitive_name] = check_fn(all_content)
    return results


# ── encode_repo_static ────────────────────────────────────────────────

# Primitive ToolSpec: hub-side, check_outcome always observable
_PRIMITIVE_INTERNAL = ("input_files", "check_outcome")
_PRIMITIVE_OBSERVABLE = ("check_outcome",)

# Paper ToolSpec base: source_files always observable; passes_<primitive>
# fields conditionally observable (in observable_schema iff primitive
# passes for this paper at this commit)
_PAPER_BASE_INTERNAL = ("source_files",)
_PAPER_BASE_OBSERVABLE = ("source_files",)


def _walk_paper_dirs(path: Path) -> list[Path]:
    """Return sorted paper directory paths under ``path/papers/``."""
    papers_dir = path / "papers"
    if not papers_dir.is_dir():
        return []
    return sorted(
        d for d in papers_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
    )


def encode_repo_static(path: Path) -> Composition:
    """Encode a repository state at ``path`` using static-content lint checks.

    Per-paper observable_schema reflects which primitives PASS for that
    paper at this commit. Edges from each primitive-tool to each paper
    declare a SemanticDimension on (check_outcome, passes_<primitive>).
    If the paper HIDES passes_<primitive> (primitive failed), the edge
    contributes to δ_full but not δ_obs → fee>0.

    Magnitude (per the docstring header): for each primitive,
    fee_per_primitive = max(0, n_failing_papers_for_primitive - 1).
    Total fee at a commit = sum across primitives of fee_per_primitive.

    Args:
        path: working tree root of the repository.

    Returns:
        Composition with N_primitives + N_papers tools and
        N_primitives × N_papers edges.
    """
    tools: list[ToolSpec] = []
    edges: list[Edge] = []

    # 1. Build primitive ToolSpecs (always present)
    for primitive_name, _, _ in _PRIMITIVE_REGISTRY:
        tools.append(
            ToolSpec(
                name=f"primitive_{primitive_name}",
                internal_state=_PRIMITIVE_INTERNAL,
                observable_schema=_PRIMITIVE_OBSERVABLE,
            )
        )

    # 2. Walk papers/ and build per-paper ToolSpecs based on primitive outcomes
    paper_dirs = _walk_paper_dirs(path)
    paper_internal = _PAPER_BASE_INTERNAL + tuple(
        f"passes_{name}" for name, _, _ in _PRIMITIVE_REGISTRY
    )
    for paper_dir in paper_dirs:
        primitive_results = scan_paper_primitives(paper_dir)
        # observable_schema = base + only-passing pass-fields
        paper_observable = _PAPER_BASE_OBSERVABLE + tuple(
            f"passes_{name}"
            for name, _, _ in _PRIMITIVE_REGISTRY
            if primitive_results.get(name, True)
        )
        tools.append(
            ToolSpec(
                name=f"paper_{paper_dir.name}",
                internal_state=paper_internal,
                observable_schema=paper_observable,
            )
        )

    # 3. Edges: each primitive → each paper, declaring dimension on
    #    (check_outcome, passes_<primitive>)
    for primitive_name, _, _ in _PRIMITIVE_REGISTRY:
        for paper_dir in paper_dirs:
            edges.append(
                Edge(
                    from_tool=f"primitive_{primitive_name}",
                    to_tool=f"paper_{paper_dir.name}",
                    dimensions=(
                        SemanticDimension(
                            name=f"{primitive_name}_check_{paper_dir.name}",
                            from_field="check_outcome",
                            to_field=f"passes_{primitive_name}",
                        ),
                    ),
                )
            )

    return Composition(
        name=f"static_pipeline_{path.name}",
        tools=tuple(tools),
        edges=tuple(edges),
    )
