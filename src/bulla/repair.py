"""Composition repair and coordination: guided discovery loop.

Separated from ``diagnostic.py`` (measurement layer) in v0.27.0.
The measurement layer has zero imports from this module, preserving
the anti-reflexivity law.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from bulla.diagnostic import (
    boundary_obligations_from_decomposition,
    decompose_fee,
    diagnose,
)
from bulla.model import (
    BoundaryObligation,
    Composition,
    ObligationVerdict,
    ProbeResult,
    ToolSpec,
)

if TYPE_CHECKING:
    from bulla.discover.adapter import DiscoverAdapter


def repair_composition(
    comp: Composition,
    confirmed: tuple[ProbeResult, ...],
) -> Composition:
    """Produce a new Composition with confirmed fields made observable.

    For each CONFIRMED probe, finds tools matching the obligation's
    target (by server group prefix or source_edge tool name) and adds
    the obligated field to ``observable_schema``.

    Pure function: returns a new ``Composition``, does not mutate the
    original.  Idempotent: applying the same repair twice produces the
    same result.  Verifiable: ``diagnose(repaired).coherence_fee``
    should be strictly less than ``diagnose(original).coherence_fee``
    when at least one probe is confirmed (collective invariant).
    """
    updates: dict[str, set[str]] = {}
    for probe in confirmed:
        if probe.verdict != ObligationVerdict.CONFIRMED:
            continue
        obl = probe.obligation
        group = obl.placeholder_tool

        target_tools: list[str] = []
        if obl.source_edge:
            for part in obl.source_edge.replace(" -> ", "\t").split("\t"):
                part = part.strip()
                if part.startswith(f"{group}__") or part == group:
                    target_tools.append(part)

        if not target_tools:
            for t in comp.tools:
                if t.name.startswith(f"{group}__") or t.name == group:
                    target_tools.append(t.name)

        for tname in target_tools:
            updates.setdefault(tname, set()).add(obl.field)

    if not updates:
        return comp

    new_tools: list[ToolSpec] = []
    for t in comp.tools:
        if t.name in updates:
            extra = updates[t.name]
            new_obs = tuple(
                sorted(set(t.observable_schema) | extra)
            )
            new_tools.append(ToolSpec(
                name=t.name,
                internal_state=t.internal_state,
                observable_schema=new_obs,
            ))
        else:
            new_tools.append(t)

    return Composition(
        name=comp.name,
        tools=tuple(new_tools),
        edges=comp.edges,
    )


@dataclass(frozen=True)
class RepairResult:
    """Result of one round of guided repair.

    ``original_fee`` and ``repaired_fee`` bracket the fee change.
    When ``confirmed_count >= 1``, the collective invariant guarantees
    ``repaired_fee < original_fee``.  The reduction may be less than
    ``confirmed_count`` when obligations share linear dependencies.
    """

    original_fee: int
    repaired_fee: int
    fee_delta: int
    probes: tuple[ProbeResult, ...]
    confirmed_count: int
    repaired_comp: Composition
    remaining_obligations: tuple[BoundaryObligation, ...]


def repair_step(
    comp: Composition,
    partition: list[frozenset[str]],
    tool_schemas: list[dict],
    adapter: DiscoverAdapter,
    pack_context: dict | None = None,
    parent_obligations: tuple[BoundaryObligation, ...] | None = None,
) -> RepairResult:
    """One round of diagnose -> obligations -> guided discover -> repair.

    1. Diagnose ``comp`` and extract obligations from decomposition.
    2. Merge with ``parent_obligations`` (if any).
    3. Run ``guided_discover`` (single batched LLM call).
    4. Apply confirmed repairs via ``repair_composition``.
    5. Re-diagnose the repaired composition.
    """
    from bulla.discover.engine import guided_discover

    diag = diagnose(comp)
    original_fee = diag.coherence_fee

    own_obligations: tuple[BoundaryObligation, ...] = ()
    decomposition = decompose_fee(comp, partition)
    if decomposition.boundary_fee > 0:
        own_obligations = boundary_obligations_from_decomposition(
            comp, list(decomposition.partition), diag,
        )

    all_obligations: list[BoundaryObligation] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for obl in (*(parent_obligations or ()), *own_obligations):
        key = (obl.placeholder_tool, obl.dimension, obl.field)
        if key not in seen_keys:
            seen_keys.add(key)
            all_obligations.append(obl)

    if not all_obligations:
        return RepairResult(
            original_fee=original_fee,
            repaired_fee=original_fee,
            fee_delta=0,
            probes=(),
            confirmed_count=0,
            repaired_comp=comp,
            remaining_obligations=(),
        )

    result = guided_discover(
        tuple(all_obligations), tool_schemas, adapter, pack_context,
    )

    confirmed = result.confirmed
    if confirmed:
        repaired_comp = repair_composition(comp, confirmed)
        repaired_diag = diagnose(repaired_comp)
        repaired_fee = repaired_diag.coherence_fee
    else:
        repaired_comp = comp
        repaired_fee = original_fee

    remaining = tuple(
        p.obligation for p in result.probes
        if p.verdict != ObligationVerdict.CONFIRMED
    )

    return RepairResult(
        original_fee=original_fee,
        repaired_fee=repaired_fee,
        fee_delta=original_fee - repaired_fee,
        probes=result.probes,
        confirmed_count=len(confirmed),
        repaired_comp=repaired_comp,
        remaining_obligations=remaining,
    )


# ── Convention value extraction (v0.28.0) ────────────────────────────


def extract_pack_from_probes(
    probes: tuple[ProbeResult, ...],
    composition_hash: str = "",
) -> dict:
    """Generate a micro-pack dict from confirmed probes with convention values.

    Each confirmed probe with a non-empty ``convention_value`` generates one
    dimension entry.  Multiple probes on the same dimension merge:
    ``known_values`` collects all **distinct** values (deduplicated),
    ``source_tools`` collects all tool names, ``field_patterns`` collects
    all fields.  Exact-match field patterns only -- the LLM confirmed a
    specific field, not a pattern family.

    Returns a dict conforming to the micro-pack schema (``validate_pack``).
    Empty probes (no confirmed values) return an empty-dimensions pack.
    """
    dims: dict[str, dict] = {}

    for probe in probes:
        if probe.verdict != ObligationVerdict.CONFIRMED:
            continue
        if not probe.convention_value:
            continue

        obl = probe.obligation
        dim_key = obl.dimension

        if dim_key not in dims:
            dims[dim_key] = {
                "description": f"Convention for {dim_key} dimension",
                "known_values": [],
                "field_patterns": [],
                "provenance": {
                    "source": "guided_discovery",
                    "confidence": "confirmed",
                    "source_tools": [],
                    "boundary": obl.source_edge or "",
                },
            }

        entry = dims[dim_key]
        if probe.convention_value not in entry["known_values"]:
            entry["known_values"].append(probe.convention_value)
        if obl.field not in entry["field_patterns"]:
            entry["field_patterns"].append(obl.field)

        prov_tools = entry["provenance"]["source_tools"]
        if obl.placeholder_tool not in prov_tools:
            prov_tools.append(obl.placeholder_tool)

        if obl.source_edge and not entry["provenance"]["boundary"]:
            entry["provenance"]["boundary"] = obl.source_edge

    hash_prefix = composition_hash[:8] if composition_hash else "unknown"
    pack: dict = {
        "pack_name": f"discovered_{hash_prefix}",
        "pack_version": "0.1.0",
        "dimensions": dims,
    }

    if dims:
        from bulla.packs.validate import validate_pack
        errors = validate_pack(pack)
        if errors:
            raise ValueError(
                f"extract_pack_from_probes produced invalid pack: {errors}"
            )

    return pack


# ── Iterative convergence (v0.27.0) ─────────────────────────────────


@dataclass(frozen=True)
class ConvergenceResult:
    """Result of iterative guided repair across multiple rounds.

    ``converged`` is True when the fee sequence has stabilized
    (``fee_zero`` or ``fixpoint``).  A fixpoint with ``fee > 0``
    means remaining obligations are unresolvable; check
    ``termination_reason`` to distinguish from full resolution.

    ``total_confirmed``, ``total_denied``, and ``total_uncertain``
    count **probe events across all rounds**, not unique obligations.
    An UNCERTAIN obligation re-probed in round 2 is counted twice.

    ``termination_reason`` distinguishes the three exit paths:
    ``"fee_zero"``, ``"fixpoint"``, ``"max_rounds"``.
    """

    rounds: tuple[RepairResult, ...]
    converged: bool
    final_comp: Composition
    final_fee: int
    total_confirmed: int
    total_denied: int
    total_uncertain: int
    termination_reason: str

    @property
    def discovered_pack(self) -> dict:
        """Derive a micro-pack from all confirmed probes across all rounds."""
        all_probes = tuple(
            p for r in self.rounds for p in r.probes
        )
        return extract_pack_from_probes(
            all_probes,
            self.final_comp.canonical_hash()[:8] if self.rounds else "",
        )


def coordination_step(
    comp: Composition,
    partition: list[frozenset[str]],
    tool_schemas: list[dict],
    adapter: DiscoverAdapter,
    *,
    max_rounds: int = 5,
    pack_context: dict | None = None,
    parent_obligations: tuple[BoundaryObligation, ...] | None = None,
) -> ConvergenceResult:
    """Iterative repair loop: repeat repair_step until convergence.

    Termination conditions (checked in order after each round):
    1. ``repaired_fee == 0``: full resolution (``"fee_zero"``).
    2. ``fee_delta == 0``: fixpoint, nothing new confirmed (``"fixpoint"``).
    3. ``round >= max_rounds``: budget exhausted (``"max_rounds"``).

    Obligation triage between rounds: DENIED obligations are excluded
    (won't change on re-probe). UNCERTAIN obligations are re-probed
    (the repaired composition may provide new context).

    The convergence invariant is a theorem: fee is a non-negative
    integer that strictly decreases on each round with at least one
    confirmation.  The loop terminates in at most ``initial_fee`` rounds.
    """
    current_comp = comp
    current_obligations = parent_obligations
    rounds: list[RepairResult] = []
    total_confirmed = 0
    total_denied = 0
    total_uncertain = 0
    termination_reason = "max_rounds"

    for _ in range(max_rounds):
        result = repair_step(
            current_comp, partition, tool_schemas, adapter,
            pack_context, current_obligations,
        )
        rounds.append(result)

        round_denied = sum(
            1 for p in result.probes if p.verdict == ObligationVerdict.DENIED
        )
        round_uncertain = sum(
            1 for p in result.probes if p.verdict == ObligationVerdict.UNCERTAIN
        )
        total_confirmed += result.confirmed_count
        total_denied += round_denied
        total_uncertain += round_uncertain

        if result.repaired_fee == 0:
            termination_reason = "fee_zero"
            current_comp = result.repaired_comp
            break

        if result.fee_delta == 0:
            termination_reason = "fixpoint"
            current_comp = result.repaired_comp
            break

        remaining = tuple(
            p.obligation for p in result.probes
            if p.verdict == ObligationVerdict.UNCERTAIN
        )
        current_comp = result.repaired_comp
        current_obligations = remaining if remaining else None

    final_fee = rounds[-1].repaired_fee if rounds else diagnose(comp).coherence_fee
    converged = termination_reason in ("fee_zero", "fixpoint")

    return ConvergenceResult(
        rounds=tuple(rounds),
        converged=converged,
        final_comp=current_comp,
        final_fee=final_fee,
        total_confirmed=total_confirmed,
        total_denied=total_denied,
        total_uncertain=total_uncertain,
        termination_reason=termination_reason,
    )
