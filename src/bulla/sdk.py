"""Compose SDK: one-function entry points for agent framework integration.

``compose()`` and ``compose_multi()`` collapse diagnosis, obligation
checking, contradiction detection, policy enforcement, and receipt
issuance into a single call.  No guided discovery, no LLM calls --
pure structural analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bulla.diagnostic import (
    FeeDecomposition,
    boundary_obligations_from_decomposition,
    check_obligations,
    decompose_fee,
    diagnose,
)
from bulla.guard import BullaGuard
from bulla.model import (
    BoundaryObligation,
    ContradictionReport,
    DEFAULT_POLICY_PROFILE,
    Diagnostic,
    PolicyProfile,
    WitnessReceipt,
)
from bulla.repair import detect_contradictions
from bulla.witness import witness


@dataclass(frozen=True)
class ComposeResult:
    """Result of a composition: receipt + diagnostic + optional decomposition."""

    receipt: WitnessReceipt
    diagnostic: Diagnostic
    decomposition: FeeDecomposition | None = None


def _obligations_from_chain(chain: dict) -> tuple[BoundaryObligation, ...]:
    """Reconstruct BoundaryObligation objects from a serialized chain receipt."""
    raw = chain.get("boundary_obligations")
    if not raw:
        return ()
    return tuple(
        BoundaryObligation(
            placeholder_tool=d["placeholder_tool"],
            dimension=d["dimension"],
            field=d["field"],
            source_edge=d.get("source_edge", ""),
            expected_value=d.get("expected_value", ""),
        )
        for d in raw
    )


def compose(
    tools: list[dict[str, Any]],
    *,
    policy: PolicyProfile = DEFAULT_POLICY_PROFILE,
    chain: dict | None = None,
    name: str = "composition",
) -> ComposeResult:
    """Diagnose a tool set and issue a witness receipt.

    If *chain* is a serialized receipt dict from an upstream witness
    event, ``compose()`` extracts ``inline_dimensions``,
    ``parent_receipt_hashes``, and ``boundary_obligations`` from it,
    auto-computes ``unmet_obligations`` via ``check_obligations()``,
    and threads everything into the receipt.

    Returns a ``ComposeResult`` with the receipt, diagnostic, and
    ``decomposition=None`` (single-server compositions have no
    partition to decompose).
    """
    guard = BullaGuard.from_tools_list(tools, name=name)
    diag = guard.diagnose()
    comp = guard.composition

    inline_dimensions: dict | None = None
    parent_receipt_hashes: tuple[str, ...] | None = None
    boundary_obligations: tuple[BoundaryObligation, ...] | None = None
    contradictions: tuple[ContradictionReport, ...] | None = None
    unmet_obligations = 0

    if chain is not None:
        inline_dimensions = chain.get("inline_dimensions")
        chain_hash = chain.get("receipt_hash")
        if chain_hash:
            parent_receipt_hashes = (chain_hash,)

        chain_obligations = _obligations_from_chain(chain)
        if chain_obligations:
            boundary_obligations = chain_obligations
            _met, _unmet, _irrelevant = check_obligations(
                chain_obligations, comp,
            )
            unmet_obligations = len(_unmet)

    receipt = witness(
        diag,
        comp,
        policy_profile=policy,
        witness_basis=guard.witness_basis,
        inline_dimensions=inline_dimensions,
        parent_receipt_hashes=parent_receipt_hashes,
        boundary_obligations=boundary_obligations,
        contradictions=contradictions,
        unmet_obligations=unmet_obligations,
    )

    return ComposeResult(receipt=receipt, diagnostic=diag)


def compose_multi(
    server_tools: dict[str, list[dict[str, Any]]],
    *,
    policy: PolicyProfile = DEFAULT_POLICY_PROFILE,
    chain: dict | None = None,
) -> ComposeResult:
    """Diagnose a multi-server tool set with partition decomposition.

    Each key in *server_tools* is a server name; its value is the
    ``tools/list`` response from that server.  Tool names are prefixed
    with ``server_name__`` to avoid collisions.

    If *chain* is provided, ``inline_dimensions`` are extracted for
    contradiction detection via ``detect_contradictions()``, and the
    chain receipt hash is recorded as a parent.

    Returns a ``ComposeResult`` with ``decomposition`` populated.
    """
    combined_tools: list[dict[str, Any]] = []
    server_groups: dict[str, list[str]] = {}

    for server_name, tools_list in server_tools.items():
        group_names: list[str] = []
        for tool in tools_list:
            prefixed = dict(tool)
            original_name = tool.get("name", "unknown")
            prefixed_name = f"{server_name}__{original_name}"
            prefixed["name"] = prefixed_name
            combined_tools.append(prefixed)
            group_names.append(prefixed_name)
        server_groups[server_name] = group_names

    comp_name = "multi_" + "_".join(sorted(server_tools.keys()))
    guard = BullaGuard.from_tools_list(combined_tools, name=comp_name)
    diag = guard.diagnose()
    comp = guard.composition

    partition = [frozenset(names) for names in server_groups.values()]
    decomposition = decompose_fee(comp, partition)

    boundary_obs = boundary_obligations_from_decomposition(
        comp, partition, diag,
    )
    _met, _unmet, _irrelevant = check_obligations(boundary_obs, comp)
    unmet_obligations = len(_unmet)

    inline_dimensions: dict | None = None
    parent_receipt_hashes: tuple[str, ...] | None = None
    contradictions: tuple[ContradictionReport, ...] | None = None

    if chain is not None:
        inline_dimensions = chain.get("inline_dimensions")
        chain_hash = chain.get("receipt_hash")
        if chain_hash:
            parent_receipt_hashes = (chain_hash,)

        chain_dims = chain.get("inline_dimensions")
        if chain_dims:
            contradictions = detect_contradictions({"dimensions": chain_dims})
            if not contradictions:
                contradictions = None

    receipt = witness(
        diag,
        comp,
        policy_profile=policy,
        witness_basis=guard.witness_basis,
        inline_dimensions=inline_dimensions,
        parent_receipt_hashes=parent_receipt_hashes,
        boundary_obligations=boundary_obs if boundary_obs else None,
        contradictions=contradictions,
        unmet_obligations=unmet_obligations,
    )

    return ComposeResult(
        receipt=receipt, diagnostic=diag, decomposition=decomposition,
    )
