"""lambda_nabla -- the Coherence Types elaborator (papers/refinement-types).

This module presents Bulla's coherence-fee diagnostic *as a refinement
typechecker* for the lambda_nabla calculus.  The dictionary (paper sec 1.2):

    typing context Gamma          <->  the composition (a semantic interface complex)
    coherence fee  r              <->  diagnose(comp).coherence_fee = rank_full - rank_obs
    fee-coherent (under-determ.-free)<->  r == 0   (see SCOPE below: this is the
                                       *under-determination* half of coherence)
    coercion                      <->  an admissible (absolute, column) disclosure (tool, field)
    minimum coercion set          <->  minimum_disclosure_set(comp) -- the disclosure
                                       normal form, of cardinality exactly r (Thm 3.7)
    graded type  box_r tau        <->  a composition carrying r disclosure obligations

SCOPE -- the two failure modes of lambda_nabla coherence must not be conflated
(paper sec 1.2, 3.2): a CLASH (inconsistent declared conventions -- a *value*
condition) and UNDER-DETERMINATION (fee > 0 -- a *structural* dimension). Full
coherence is  Gamma |- e : tau OK  <=>  consistent AND fee == 0  (Def 3.4). Bulla's
`coherence_fee` is the [CD] latent-FIELDS rank; it captures the UNDER-DETERMINATION
half ONLY and is blind to clashes by construction (paper sec 3.2: "a clash can
occur at fee=0 ... fee does not see it"). This module therefore realizes the
fee/coercion (under-determination) half and reports `coherent` to mean
**fee == 0 ASSUMING consistency**; value-level clash detection is out of scope here
(it lives in Bulla's structural diagnostic -- SchemaContradiction / contradiction
detection). "Coercion" is realized as an absolute column-disclosure (expose a
(tool, field)), which suffices for repair duality in Bulla's exact (DFD+CHP)
regime where the disclosure normal form is column-pinnable (paper sec 3.3-3.4).

With that scoping, Bulla's deployed checker is the lambda_nabla
under-determination oracle; this module is the thin typed surface over it:

    typecheck(comp)  -> TypingVerdict   (grade = fee, coercions = disclosure NF)
    elaborate(comp)  -> (coherent composition, the r coercions inserted)

Guarantees we re-check here against the deployed checker (the paper's theorems,
operationalized):

  * Repair duality (Thm 3.7 / [CD] Thm 6.2):  |coercions| == grade.
  * Elaboration soundness (Cor 5.5):  elaborate(comp) has grade 0 -- coherent,
    so by Coherence Soundness (Thm 5.4) its conventions glue: every cross-tool
    reconciliation is forced by the declarations.

This is the B3.4 capstone: the type theory of Parts I-III, run on real MCP
composition machinery.
"""

from __future__ import annotations

from dataclasses import dataclass

from bulla.diagnostic import diagnose, minimum_disclosure_set
from bulla.model import Composition, ToolSpec

Coercion = tuple[str, str]  # (tool_name, field_name) -- an admissible disclosure


@dataclass(frozen=True)
class TypingVerdict:
    """The lambda_nabla typing judgement for a composition.

    ``grade`` is the coherence fee r (the box_r modality's grade).  ``coherent``
    is ``grade == 0`` -- *fee-coherence* (under-determination-free); this equals
    the full Part I judgement ``Gamma |- e : tau OK`` only *assuming consistency*
    (no clash), since full coherence is ``consistent AND fee == 0`` (Def 3.4) and
    Bulla's fee is blind to clashes (see SCOPE in the module docstring).  When
    ``grade > 0`` the term is typed ``box_grade tau`` and ``coercions`` is the
    disclosure normal form -- exactly ``grade`` admissible disclosures whose
    insertion drives the grade to 0.
    """

    name: str
    grade: int
    coherent: bool
    coercions: tuple[Coercion, ...]
    blind_spots: int

    def __str__(self) -> str:
        if self.coherent:
            return (
                f"{self.name}: FEE-COHERENT (grade 0) -- no undeclared load-bearing "
                f"convention; coherent if also consistent (no clash); no coercion needed"
            )
        cs = ", ".join(f"{t}.{f}" for t, f in self.coercions)
        return (
            f"{self.name}: box_{self.grade}  -- {self.grade} disclosure obligation(s); "
            f"elaborate by declaring [{cs}]"
        )


def typecheck(comp: Composition) -> TypingVerdict:
    """Return the lambda_nabla typing verdict, using Bulla's fee as the oracle."""
    diag = diagnose(comp)
    r = diag.coherence_fee
    coercions: tuple[Coercion, ...] = (
        tuple(minimum_disclosure_set(comp)) if r > 0 else ()
    )
    return TypingVerdict(
        name=comp.name,
        grade=r,
        coherent=(r == 0),
        coercions=coercions,
        blind_spots=len(diag.blind_spots),
    )


def _apply_coercions(comp: Composition, coercions: tuple[Coercion, ...]) -> Composition:
    """Insert disclosures: add each (tool, field) to that tool's observable schema."""
    by_tool: dict[str, set[str]] = {}
    for tool, field in coercions:
        by_tool.setdefault(tool, set()).add(field)
    new_tools = []
    for t in comp.tools:
        if t.name in by_tool:
            add = tuple(f for f in sorted(by_tool[t.name]) if f not in t.observable_schema)
            new_tools.append(
                ToolSpec(t.name, t.internal_state, t.observable_schema + add)
            )
        else:
            new_tools.append(t)
    return Composition(
        name=f"{comp.name}+elaborated", tools=tuple(new_tools), edges=comp.edges
    )


def elaborate(comp: Composition) -> tuple[Composition, tuple[Coercion, ...]]:
    """Elaborate to a coherent composition by inserting the disclosure normal form.

    Returns ``(elaborated_comp, coercions)``.  By construction the elaborated
    composition has grade 0 (Cor 5.5 / elaboration soundness); ``coercions`` is
    the minimum set, of size equal to the original grade (repair duality).
    """
    v = typecheck(comp)
    if v.coherent:
        return comp, ()
    return _apply_coercions(comp, v.coercions), v.coercions


# --- operational re-checks of the paper's theorems on the deployed checker ----

def check_repair_duality(comp: Composition) -> bool:
    """Thm 3.7: the minimum coercion set has cardinality exactly the grade."""
    v = typecheck(comp)
    return len(v.coercions) == v.grade


def check_elaboration_soundness(comp: Composition) -> bool:
    """Cor 5.5: the elaborated composition is coherent (grade 0)."""
    elaborated, _ = elaborate(comp)
    return typecheck(elaborated).coherent


def typecheck_report(comps: list[Composition]) -> dict[str, int]:
    """Typecheck a batch; return {coherent, elaborated, total, total_coercions}."""
    coherent = elaborated = total_coercions = 0
    for c in comps:
        v = typecheck(c)
        if v.coherent:
            coherent += 1
        else:
            elaborated += 1
            total_coercions += v.grade
    return {
        "total": len(comps),
        "coherent": coherent,
        "elaborated": elaborated,
        "total_coercions": total_coercions,
    }
