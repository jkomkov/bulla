"""bulla.Session — incremental composition diagnosis with per-tool delta-fee.

A long-lived object that tracks a composition as it grows tool by
tool, edge by edge. Every ``add_tool`` / ``add_edge`` updates the
witness Gram matrix and reports the delta-fee. ``checkpoint()`` and
``diagnose()`` emit ``WitnessReceipt`` snapshots chained through
``parent_receipt_hashes``.

This is the *online* counterpart to ``bulla.compose()`` (full-rebuild
on each call). The mathematical contract is bitwise-identical results
between the two paths, validated by ``test_session.py``'s 10k-seed
property test.

Composition vs. proxy: ``BullaProxySession`` (in ``bulla.proxy``) is
*call tracing* — it records observed MCP traffic against an
already-built composition. ``Session`` is *composition building* —
incremental tool/edge additions before any calls happen. They model
orthogonal concerns; a future glue layer can produce a
``BullaProxySession`` from a ``Session``.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass

from bulla.bridges import (
    TranslationResult,
    translate as _translate,
)
from bulla.diagnostic import diagnose as _diagnose
from bulla.incremental import ExtendDelta, IncrementalDiagnostic
from bulla.model import (
    DEFAULT_POLICY_PROFILE,
    Composition,
    Disposition,
    Edge,
    PolicyProfile,
    ToolSpec,
    WitnessReceipt,
)
from bulla.witness import witness as _witness


@dataclass(frozen=True)
class AddToolResult:
    """Outcome of one ``Session.add_tool`` (or ``add_edge``) call.

    ``delta_fee`` is the signed change to the coherence fee. Adding
    a tool that resolves an existing seam can lower the fee; adding
    one that introduces a new seam raises it.
    """

    delta_fee: int
    fee_after: int
    new_hidden_fields: tuple[tuple[str, str], ...]
    new_tool_names: tuple[str, ...] = ()
    new_edges: tuple[Edge, ...] = ()


class Session:
    """Long-lived composition-building session.

    Usage::

        s = bulla.Session(name="checkout")
        s.add_tool(stripe_charge_spec)
        s.add_tool(quickbooks_invoice_spec)
        s.add_edge(Edge(...))            # delta_fee may > 0 if a new seam
        tr = s.translate("currency_code", value="USD",
                         to_convention="stripe-lower")
        cp = s.checkpoint()                  # mini-receipt
        final = s.diagnose()                 # full WitnessReceipt
    """

    def __init__(
        self,
        *,
        policy: PolicyProfile = DEFAULT_POLICY_PROFILE,
        name: str = "session",
    ) -> None:
        self.session_id: str = str(uuid.uuid4())
        self.name: str = name
        self.policy: PolicyProfile = policy
        self._inc: IncrementalDiagnostic = IncrementalDiagnostic(
            Composition(name=name, tools=(), edges=())
        )
        self._receipt_chain: list[str] = []

    # ── State accessors ──────────────────────────────────────────────

    @property
    def fee(self) -> int:
        return self._inc.fee

    @property
    def composition(self) -> Composition:
        return self._inc.current_composition()

    @property
    def hidden_basis(self) -> list[tuple[str, str]]:
        return self._inc.hidden_basis

    @property
    def receipt_chain(self) -> tuple[str, ...]:
        """Receipt hashes produced so far, in order."""
        return tuple(self._receipt_chain)

    # ── Mutators ─────────────────────────────────────────────────────

    def add_tool(self, spec: ToolSpec) -> AddToolResult:
        """Append a tool. Tool name must be unique in the session."""
        delta = self._inc.extend(new_tools=[spec])
        return _result_from_delta(delta)

    def add_edge(self, edge: Edge) -> AddToolResult:
        """Append an edge. Endpoints must already be in the session."""
        delta = self._inc.extend(new_edges=[edge])
        return _result_from_delta(delta)

    def add_tools_and_edges(
        self,
        tools: list[ToolSpec] | None = None,
        edges: list[Edge] | None = None,
    ) -> AddToolResult:
        """Atomic batch addition. Either both succeed or both abort.

        Useful when adding a tool plus the edges that connect it,
        avoiding the intermediate state where the tool is in the
        session but its inbound edge isn't yet (which would falsely
        change leverage queries).
        """
        delta = self._inc.extend(new_tools=tools, new_edges=edges)
        return _result_from_delta(delta)

    # ── Translation (delegates to bulla.translate) ───────────────────

    def translate(
        self,
        dimension: str,
        *,
        value: str,
        to_convention: str,
        from_convention: str | None = None,
    ) -> TranslationResult:
        """Translate a value through the session's bridge runtime.

        The translation receipt's ``parent_receipt_hashes`` is set to
        the latest checkpoint hash (or empty if no checkpoint exists),
        and the translation's receipt hash is appended to the chain.
        """
        parent_hashes: tuple[str, ...] | None = None
        if self._receipt_chain:
            parent_hashes = (self._receipt_chain[-1],)
        result = _translate(
            dimension,
            value=value,
            to_convention=to_convention,
            from_convention=from_convention,
            session_id=self.session_id,
            parent_receipt_hashes=parent_hashes,
        )
        self._receipt_chain.append(result.receipt.receipt_hash)
        return result

    # ── Receipt emission ─────────────────────────────────────────────

    def checkpoint(self) -> WitnessReceipt:
        """Produce a receipt for the session's current state.

        The receipt is small and content-addressed; its
        ``parent_receipt_hashes`` chains to the previous checkpoint
        (or translation receipt) so the session's history is fully
        reconstructable from the chain.
        """
        receipt = self._build_state_receipt()
        self._receipt_chain.append(receipt.receipt_hash)
        return receipt

    def diagnose(self) -> WitnessReceipt:
        """Produce a full WitnessReceipt covering the entire session.

        Currently identical to ``checkpoint()`` plus the witness-
        signing pipeline; the distinction matters only when a future
        terminal-receipt convention diverges (e.g., a final receipt
        could carry a digest of the full chain, not just the last
        link). Callers should treat ``diagnose()`` as the
        authoritative end-of-session artifact.
        """
        # For the v1 sprint, diagnose() == checkpoint() with the
        # witness layer's full signing path. The receipt's parent
        # chain still threads back through every prior step.
        comp = self._inc.current_composition()
        if not comp.tools and not comp.edges:
            # Empty composition: emit an honest empty-state receipt
            # rather than calling the full diagnostic pipeline (which
            # asserts a non-empty composition).
            return self._build_state_receipt(disposition=Disposition.PROCEED)
        diag = _diagnose(comp)
        parent: tuple[str, ...] | None = None
        if self._receipt_chain:
            parent = (self._receipt_chain[-1],)
        receipt = _witness(
            diag,
            comp,
            policy_profile=self.policy,
            parent_receipt_hashes=parent,
        )
        self._receipt_chain.append(receipt.receipt_hash)
        return receipt

    # ── Internal ─────────────────────────────────────────────────────

    def _build_state_receipt(
        self,
        disposition: Disposition | None = None,
    ) -> WitnessReceipt:
        """Build a lightweight WitnessReceipt for the session's
        current state without running the full witness signing path.

        Used by ``checkpoint()``. The receipt records fee, hidden
        basis size, and chains to the prior link if any. It does NOT
        run the structural-contradiction scan; that's reserved for
        ``diagnose()``.
        """
        comp = self._inc.current_composition()
        # Disposition heuristic: PROCEED if fee=0, else PROCEED_WITH_BRIDGE.
        if disposition is None:
            disposition = (
                Disposition.PROCEED if self._inc.fee == 0
                else Disposition.PROCEED_WITH_BRIDGE
            )
        parent: tuple[str, ...] | None = None
        if self._receipt_chain:
            parent = (self._receipt_chain[-1],)

        from bulla import __version__ as bulla_version
        return WitnessReceipt(
            receipt_version="0.1.0",
            kernel_version=f"bulla-{bulla_version}",
            composition_hash=comp.canonical_hash(),
            diagnostic_hash=_session_diagnostic_hash(comp, self._inc.fee),
            policy_profile=self.policy,
            fee=self._inc.fee,
            blind_spots_count=len(self._inc.hidden_basis),
            bridges_required=self._inc.fee,
            unknown_dimensions=0,
            disposition=disposition,
            timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
            parent_receipt_hashes=parent,
            inline_dimensions={
                "kind": "session_checkpoint",
                "session_id": self.session_id,
                "session_name": self.name,
                "n_tools": len(comp.tools),
                "n_edges": len(comp.edges),
                "fee": self._inc.fee,
                "hidden_basis": [
                    [t, f] for (t, f) in self._inc.hidden_basis
                ],
            },
        )


def _result_from_delta(delta: ExtendDelta) -> AddToolResult:
    return AddToolResult(
        delta_fee=delta.delta_fee,
        fee_after=delta.fee_after,
        new_hidden_fields=delta.new_hidden_fields,
        new_tool_names=delta.new_tool_names,
        new_edges=delta.new_edges,
    )


def _session_diagnostic_hash(comp: Composition, fee: int) -> str:
    """Lightweight diagnostic hash for session checkpoints.

    Not a full Diagnostic.content_hash() — we deliberately don't run
    the full pipeline at every checkpoint. The hash binds composition
    + fee, which is enough for a checkpoint's identity.
    """
    import hashlib
    import json
    payload = {
        "kind": "session_checkpoint",
        "composition": comp.canonical_hash(),
        "fee": fee,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()


__all__ = [
    "AddToolResult",
    "Session",
]
