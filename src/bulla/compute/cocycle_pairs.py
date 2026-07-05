"""Controlled composition-pair generation for EvalGap and Semantic SemVer.

This module intentionally hosts two complementary operations:

1) ``generate_pair_at_rank`` creates a pair with shared observable skeleton
   and controlled coherence-fee separation.
2) ``compute_rank_delta`` measures the fee/rank delta between two compositions.

The operations are inverse views of the same object and are shared by:
- G25 EvalGap (generation)
- G26 Semantic SemVer (measurement)
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec


TARGET_RANKS: tuple[int, ...] = (1, 2, 3, 5, 10)
OBSERVABLE_SURFACE: tuple[str, ...] = ("g",)
INTERNAL_FIELDS: tuple[str, ...] = ("f", "g")
INCOHERENT_FIELD = "f"
COHERENT_FIELD = "g"


@dataclass(frozen=True)
class CocyclePair:
    """Pair with matched skeleton and controlled obstruction separation."""

    target_rank: int
    incoherent: Composition
    coherent: Composition
    incoherent_fee: int
    coherent_fee: int
    skeleton_hash: str


def _build_rank_cycle(*, target_rank: int, edge_field: str, label: str) -> Composition:
    """Build synthetic bidirectional 2-cycles with controlled fee profile.

    Construction:
      - target_rank independent 2-cycles.
      - all tools share identical observable surface ``("g",)``.
      - one semantic field per edge, chosen by ``edge_field``:
        - ``f`` (hidden channel) yields fee = target_rank;
        - ``g`` (observable channel) yields fee = 0.
    """
    if target_rank < 1:
        raise ValueError(f"target_rank must be >= 1; got {target_rank}")

    n_tools = target_rank * 2
    tools = tuple(
        ToolSpec(name=f"t{i}", internal_state=INTERNAL_FIELDS, observable_schema=OBSERVABLE_SURFACE)
        for i in range(n_tools)
    )

    edges: list[Edge] = []
    for cycle_idx in range(target_rank):
        left = 2 * cycle_idx
        right = 2 * cycle_idx + 1
        dim_name = f"f_match_{cycle_idx}"
        edges.append(
            Edge(
                from_tool=f"t{left}",
                to_tool=f"t{right}",
                dimensions=(SemanticDimension(name=dim_name, from_field=edge_field, to_field=edge_field),),
            )
        )
        edges.append(
            Edge(
                from_tool=f"t{right}",
                to_tool=f"t{left}",
                dimensions=(
                    SemanticDimension(
                        name=f"{dim_name}_back",
                        from_field=edge_field,
                        to_field=edge_field,
                    ),
                ),
            )
        )

    return Composition(
        name=f"evalgap_rank_{target_rank}_{label}",
        tools=tools,
        edges=tuple(edges),
    )


def _observable_surface_signature(comp: Composition) -> dict:
    """Surface visible to baseline evaluator (schema-level, no hidden channels)."""
    return {
        "tools": sorted(
            [{"name": t.name, "observable_schema": sorted(t.observable_schema)} for t in comp.tools],
            key=lambda x: x["name"],
        ),
        "edges": sorted(
            [
                {
                    "from_tool": e.from_tool,
                    "to_tool": e.to_tool,
                    "dimension_names": sorted(d.name for d in e.dimensions),
                }
                for e in comp.edges
            ],
            key=lambda x: (x["from_tool"], x["to_tool"]),
        ),
    }


def _skeleton_hash(comp: Composition) -> str:
    """Hash that ignores observability labels and captures graph structure."""
    obj = {
        "tools": sorted(
            [
                {
                    "name": t.name,
                    "internal_state": sorted(t.internal_state),
                }
                for t in comp.tools
            ],
            key=lambda x: x["name"],
        ),
        "edges": sorted(
            [
                {
                    "from_tool": e.from_tool,
                    "to_tool": e.to_tool,
                    "dimensions": sorted(
                        [
                            {
                                "name": d.name,
                                "from_field": d.from_field,
                                "to_field": d.to_field,
                            }
                            for d in e.dimensions
                        ],
                    key=lambda x: x["name"],
                    ),
                }
                for e in comp.edges
            ],
            key=lambda x: (x["from_tool"], x["to_tool"]),
        ),
    }
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def generate_pair_at_rank(
    target_rank: int,
    *,
    skeleton: Composition | None = None,
) -> CocyclePair:
    """Generate a pair at a controlled rank.

    The optional ``skeleton`` argument is reserved for future use; current
    implementation uses canonical synthetic skeletons to guarantee target fee.
    """
    _ = skeleton  # Reserved for future corpus-seeded construction.

    incoherent = _build_rank_cycle(
        target_rank=target_rank,
        edge_field=INCOHERENT_FIELD,
        label="hidden_channel",
    )
    coherent = _build_rank_cycle(
        target_rank=target_rank,
        edge_field=COHERENT_FIELD,
        label="observable_channel",
    )

    if _observable_surface_signature(incoherent) != _observable_surface_signature(coherent):
        raise ValueError("EvalGap generator invariant failed: observable surfaces must match")

    incoherent_diag = diagnose(incoherent)
    coherent_diag = diagnose(coherent)

    if incoherent_diag.coherence_fee != target_rank:
        raise ValueError(
            "Synthetic generator failed fee target: "
            f"expected {target_rank}, got {incoherent_diag.coherence_fee}"
        )
    if coherent_diag.coherence_fee != 0:
        raise ValueError(
            "Synthetic coherent twin should have fee 0, "
            f"got {coherent_diag.coherence_fee}"
        )

    return CocyclePair(
        target_rank=target_rank,
        incoherent=incoherent,
        coherent=coherent,
        incoherent_fee=incoherent_diag.coherence_fee,
        coherent_fee=coherent_diag.coherence_fee,
        skeleton_hash=_skeleton_hash(incoherent),
    )


def compute_rank_delta(comp_a: Composition, comp_b: Composition) -> int:
    """Return ``fee(comp_a) - fee(comp_b)``.

    The semantic-semver wrapper can map this to ``delta_r`` and policy classes.
    """
    fee_a = diagnose(comp_a).coherence_fee
    fee_b = diagnose(comp_b).coherence_fee
    return fee_a - fee_b

