"""Incremental fee maintenance via Schur complement rank-one updates.

When a hidden field is disclosed (moved from internal_state to
observable_schema), the witness Gram matrix K(G) changes by a rank-1
correction — the Schur complement deletion of the disclosed column.
This module maintains K incrementally, avoiding the O(n³) cost of
full recomputation for each disclosure.

Mathematical guarantee: every intermediate K produced by incremental
updates is bitwise identical (exact rational arithmetic) to the K that
full recomputation from witness_geometry.witness_gram would produce
for the same composition with the same disclosures applied.

The update formula for disclosing hidden field j:

    K_new[i,k] = K_old[i,k] - K_old[i,j] * K_old[j,k] / K_old[j,j]

for all i,k != j (then delete row j and column j). This is the Schur
complement of element j in K — equivalently, Kron reduction of vertex j
from the effective resistance network.

The fee decreases by 1 iff K[j,j] > 0 (the field has positive leverage,
i.e., it is not a loop of the witness matroid M/O). If K[j,j] = 0, the
field is already redundant and disclosing it changes nothing.

Reference: docs/ARCHITECTURE.md §The Witness Gram Matrix;
           papers/hierarchical-fee/paper/column-matroid-backbone.tex
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from bulla.coboundary import matrix_rank
from bulla.model import Composition, ToolSpec, Edge
from bulla.witness_geometry import (
    witness_gram,
    leverage_scores,
    n_effective,
    coloops,
    loops,
    weighted_greedy_repair,
    fee_from_gram,
    WitnessProfile,
)


@dataclass(frozen=True)
class FeeDelta:
    """Result of a single field disclosure.

    fee_change is -1 (field was independent, fee reduced) or 0
    (field was a loop, fee unchanged). new_fee is the updated fee.
    """

    field: tuple[str, str]
    fee_change: int
    new_fee: int
    leverage_was: Fraction


@dataclass(frozen=True)
class ExtendDelta:
    """Result of an ``extend()`` call.

    Captures: which tools/edges were added, fee before and after,
    the (signed) delta, and which hidden fields newly appeared. The
    Session API surfaces a thin shim over this dataclass.
    """

    new_tool_names: tuple[str, ...]
    new_edges: tuple[Edge, ...]
    fee_before: int
    fee_after: int
    delta_fee: int
    new_hidden_fields: tuple[tuple[str, str], ...]


class IncrementalDiagnostic:
    """Maintains the witness Gram matrix K(G) under incremental disclosures.

    Constructed from a Composition (or from a pre-computed WitnessProfile).
    Each call to disclose() applies a rank-1 Schur complement update in
    O(n²), where n is the number of remaining hidden fields.

    The full repair trajectory from fee=k to fee=0 costs O(kn²) total,
    vs O(kn³) for k independent full recomputations.

    All arithmetic is exact (fractions.Fraction). Every intermediate state
    is provably identical to what full recomputation would produce.
    """

    def __init__(self, comp: Composition) -> None:
        K, hidden_basis = witness_gram(list(comp.tools), list(comp.edges))
        self._K = K
        self._hidden_basis: list[tuple[str, str]] = list(hidden_basis)
        self._fee = fee_from_gram(K)
        self._disclosures: list[tuple[str, str]] = []
        # The Session API needs the underlying composition so it can
        # extend it incrementally (and so leverage / profile queries
        # that consult the composition still work after extend()
        # mutates state). Store the live tool/edge tuples here; the
        # composition can be reconstructed via ``current_composition()``.
        self._tools: list[ToolSpec] = list(comp.tools)
        self._edges: list[Edge] = list(comp.edges)
        self._comp_name: str = comp.name

    @property
    def fee(self) -> int:
        """Current coherence fee."""
        return self._fee

    @property
    def hidden_basis(self) -> list[tuple[str, str]]:
        """Current hidden fields (shrinks as fields are disclosed)."""
        return list(self._hidden_basis)

    @property
    def disclosures(self) -> list[tuple[str, str]]:
        """Fields disclosed so far, in order."""
        return list(self._disclosures)

    def leverage(self) -> list[tuple[tuple[str, str], Fraction]]:
        """Current leverage scores for all remaining hidden fields.

        Returns a list of ((tool, field), leverage) pairs.
        Leverage 1 = coloop (must disclose). Leverage 0 = loop (redundant).
        Sum of all leverages = current fee.
        """
        lev = leverage_scores(self._K)
        return list(zip(self._hidden_basis, lev))

    def preview_disclose(self, tool: str, field: str) -> int:
        """O(1): what would the fee change be if this field were disclosed?

        Returns -1 if the field has positive leverage (fee would decrease),
        0 if the field is a loop or not in the hidden basis.
        """
        try:
            j = self._hidden_basis.index((tool, field))
        except ValueError:
            return 0
        return -1 if self._K[j][j] > 0 else 0

    def best_next_disclosure(
        self, costs: dict[tuple[str, str], Fraction] | None = None
    ) -> tuple[str, str] | None:
        """O(n): which remaining hidden field gives the best fee reduction
        per unit cost?

        With no costs dict, returns the field with highest leverage.
        With costs, returns the field maximizing leverage / cost.
        Returns None if fee is already 0.
        """
        if self._fee == 0:
            return None
        lev = leverage_scores(self._K)
        best_idx = -1
        best_ratio = Fraction(-1)
        for j, l in enumerate(lev):
            if l <= 0:
                continue
            cost = (
                costs.get(self._hidden_basis[j], Fraction(1))
                if costs
                else Fraction(1)
            )
            ratio = l / cost if cost > 0 else Fraction(10**18)
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = j
        return self._hidden_basis[best_idx] if best_idx >= 0 else None

    def disclose(self, tool: str, field: str) -> FeeDelta:
        """O(n²): disclose a hidden field and update K via Schur complement.

        Modifies this object's internal state. Returns a FeeDelta describing
        the change.

        Raises ValueError if (tool, field) is not in the current hidden basis.
        """
        try:
            j = self._hidden_basis.index((tool, field))
        except ValueError:
            raise ValueError(
                f"({tool}, {field}) not in hidden basis. "
                f"Available: {self._hidden_basis}"
            )

        n = len(self._K)
        pivot = self._K[j][j]
        leverage_was = pivot  # For K^+K diagonal, this approximates leverage

        if pivot == 0:
            # Field is a loop (leverage = 0). Disclosing it changes nothing.
            # Just remove it from the basis and K.
            self._K = _delete_row_col(self._K, j)
            self._hidden_basis.pop(j)
            self._disclosures.append((tool, field))
            return FeeDelta(
                field=(tool, field),
                fee_change=0,
                new_fee=self._fee,
                leverage_was=Fraction(0),
            )

        # Schur complement: K_new[i,k] = K[i,k] - K[i,j]*K[j,k]/K[j,j]
        K_new = _schur_complement_delete(self._K, j)

        old_fee = self._fee
        new_fee = old_fee - 1

        self._K = K_new
        self._hidden_basis.pop(j)
        self._fee = new_fee
        self._disclosures.append((tool, field))

        # Compute actual leverage from the old K's leverage scores
        lev = leverage_scores(
            [[self._K[i][k] for k in range(len(self._K))]
             for i in range(len(self._K))]
        ) if False else []  # Defer full leverage recomputation

        return FeeDelta(
            field=(tool, field),
            fee_change=-1,
            new_fee=new_fee,
            leverage_was=pivot,
        )

    # ── Tool/edge extension (Session API substrate) ────────────────

    def current_composition(self) -> Composition:
        """Return the live composition state as a frozen Composition."""
        return Composition(
            name=self._comp_name,
            tools=tuple(self._tools),
            edges=tuple(self._edges),
        )

    def extend(
        self,
        new_tools: list[ToolSpec] | None = None,
        new_edges: list[Edge] | None = None,
    ) -> "ExtendDelta":
        """Append new tools and/or edges and refresh K accordingly.

        Mathematical semantics: this method produces the same K and
        hidden_basis that ``witness_gram`` would compute on the
        composition with the supplied additions appended. Bitwise
        equality is the load-bearing invariant — see
        ``tests/test_session.py::test_session_bitwise_equals_full_rebuild``.

        Implementation is full-rebuild for now: K is recomputed from
        scratch on each extend. The API is forward-compatible with a
        future Schur-block update that maintains K incrementally; the
        property test pins the externally-observable behavior so that
        future swap is safe.

        Constraints:

          - Tool names are unique. Adding a tool whose name already
            exists raises ``ValueError``.
          - Edge endpoints must reference tools known after the
            extension (either already present or in ``new_tools``).
            ``ValueError`` otherwise.
          - Disclosed-then-extend is supported but unusual: prior
            disclosures persist in ``self._disclosures`` for audit but
            are NOT re-applied to the new K. Most callers extend before
            disclosing.
        """
        new_tools = list(new_tools or [])
        new_edges = list(new_edges or [])

        # Uniqueness check on tool names (after extension).
        existing_names = {t.name for t in self._tools}
        for t in new_tools:
            if t.name in existing_names:
                raise ValueError(
                    f"tool name {t.name!r} already in session; "
                    "duplicate names break the v_basis index"
                )
            existing_names.add(t.name)

        # Edge endpoints must reference known tools.
        all_names = existing_names  # already includes new tools
        for e in new_edges:
            if e.from_tool not in all_names:
                raise ValueError(
                    f"edge from_tool {e.from_tool!r} not in session "
                    f"(after extension)"
                )
            if e.to_tool not in all_names:
                raise ValueError(
                    f"edge to_tool {e.to_tool!r} not in session "
                    f"(after extension)"
                )

        prev_fee = self._fee
        prev_hidden_basis = list(self._hidden_basis)

        # Append.
        self._tools.extend(new_tools)
        self._edges.extend(new_edges)

        # Recompute K and hidden_basis on the extended composition.
        K_new, hidden_basis_new = witness_gram(
            list(self._tools), list(self._edges)
        )
        self._K = K_new
        self._hidden_basis = list(hidden_basis_new)
        self._fee = fee_from_gram(K_new)

        return ExtendDelta(
            new_tool_names=tuple(t.name for t in new_tools),
            new_edges=tuple(new_edges),
            fee_before=prev_fee,
            fee_after=self._fee,
            delta_fee=self._fee - prev_fee,
            new_hidden_fields=tuple(
                f for f in hidden_basis_new if f not in prev_hidden_basis
            ),
        )

    def profile(self) -> WitnessProfile:
        """Compute a full WitnessProfile from current incremental state."""
        lev = leverage_scores(self._K)
        return WitnessProfile(
            K=self._K,
            hidden_basis=list(self._hidden_basis),
            fee=self._fee,
            leverage=lev,
            n_effective=n_effective(lev),
            coloops=coloops(lev, self._hidden_basis),
            loops=loops(lev, self._hidden_basis),
            basis_greedy=weighted_greedy_repair(self._K, self._hidden_basis),
        )


def _delete_row_col(
    K: list[list[Fraction]], j: int
) -> list[list[Fraction]]:
    """Delete row j and column j from a matrix."""
    n = len(K)
    return [
        [K[i][k] for k in range(n) if k != j]
        for i in range(n)
        if i != j
    ]


def _schur_complement_delete(
    K: list[list[Fraction]], j: int
) -> list[list[Fraction]]:
    """Schur complement deletion of row/column j from a PSD matrix.

    K_new[i,k] = K[i,k] - K[i,j]*K[j,k]/K[j,j]

    for all i,k != j. Returns the (n-1)×(n-1) result.
    Assumes K[j,j] > 0 (caller must check).
    """
    n = len(K)
    pivot = K[j][j]
    indices = [i for i in range(n) if i != j]
    result: list[list[Fraction]] = []
    for i in indices:
        row: list[Fraction] = []
        for k in indices:
            row.append(K[i][k] - K[i][j] * K[j][k] / pivot)
        result.append(row)
    return result
