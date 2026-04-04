"""Data model for compositions, diagnostics, and recommendations.

Constitutional objects:
  - Diagnostic: measurement (what the kernel observes)
  - BridgePatch: repair (machine-actionable change)
  - Disposition: judgment (what an agent should do)
  - WitnessReceipt: binding (canonical record of a witness event)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


@dataclass(frozen=True)
class ToolSpec:
    name: str
    internal_state: tuple[str, ...]
    observable_schema: tuple[str, ...]

    @property
    def projected_away(self) -> tuple[str, ...]:
        return tuple(d for d in self.internal_state
                     if d not in self.observable_schema)


@dataclass(frozen=True)
class SemanticDimension:
    name: str
    from_field: str | None = None
    to_field: str | None = None


@dataclass(frozen=True)
class Edge:
    from_tool: str
    to_tool: str
    dimensions: tuple[SemanticDimension, ...]


@dataclass(frozen=True)
class Composition:
    name: str
    tools: tuple[ToolSpec, ...]
    edges: tuple[Edge, ...]

    def canonical_hash(self) -> str:
        """Canonical identity hash of composition structure.

        Hashes the parsed semantic structure, not raw YAML bytes.
        Two compositions with identical tools, edges, and dimensions
        produce the same hash regardless of YAML formatting, key order,
        or whitespace.
        """
        obj = {
            "name": self.name,
            "tools": sorted(
                [
                    {
                        "name": t.name,
                        "internal_state": sorted(t.internal_state),
                        "observable_schema": sorted(t.observable_schema),
                    }
                    for t in self.tools
                ],
                key=lambda t: t["name"],
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
                            key=lambda d: d["name"],
                        ),
                    }
                    for e in self.edges
                ],
                key=lambda e: (e["from_tool"], e["to_tool"]),
            ),
        }
        return hashlib.sha256(
            json.dumps(obj, sort_keys=True).encode()
        ).hexdigest()


@dataclass(frozen=True)
class BlindSpot:
    dimension: str
    edge: str
    from_field: str
    to_field: str
    from_hidden: bool
    to_hidden: bool
    from_tool: str = ""
    to_tool: str = ""


@dataclass(frozen=True)
class Bridge:
    field: str
    add_to: tuple[str, ...] = ()
    eliminates: str = ""


@dataclass(frozen=True)
class Diagnostic:
    name: str
    n_tools: int
    n_edges: int
    betti_1: int
    dim_c0_obs: int
    dim_c0_full: int
    dim_c1: int
    rank_obs: int
    rank_full: int
    h1_obs: int
    h1_full: int
    coherence_fee: int
    blind_spots: tuple[BlindSpot, ...]
    bridges: tuple[Bridge, ...]
    h1_after_bridge: int
    n_unbridged: int = 0

    def content_hash(self) -> str:
        """Deterministic hash of measurement content (excludes timestamps)."""
        obj = {
            "name": self.name,
            "n_tools": self.n_tools,
            "n_edges": self.n_edges,
            "betti_1": self.betti_1,
            "dim_c0_obs": self.dim_c0_obs,
            "dim_c0_full": self.dim_c0_full,
            "dim_c1": self.dim_c1,
            "rank_obs": self.rank_obs,
            "rank_full": self.rank_full,
            "h1_obs": self.h1_obs,
            "h1_full": self.h1_full,
            "coherence_fee": self.coherence_fee,
            "blind_spots": [
                {
                    "dimension": bs.dimension,
                    "edge": bs.edge,
                    "from_field": bs.from_field,
                    "to_field": bs.to_field,
                    "from_hidden": bs.from_hidden,
                    "to_hidden": bs.to_hidden,
                }
                for bs in self.blind_spots
            ],
            "h1_after_bridge": self.h1_after_bridge,
            "n_unbridged": self.n_unbridged,
        }
        return hashlib.sha256(
            json.dumps(obj, sort_keys=True).encode()
        ).hexdigest()


# ── Errors ───────────────────────────────────────────────────────────


class WitnessErrorCode(Enum):
    """Machine-readable error vocabulary for witness operations."""

    INVALID_COMPOSITION = "invalid_composition"
    INVALID_PARAMS = "invalid_params"
    RECURSION_LIMIT = "recursion_limit"
    INTERNAL = "internal"


class WitnessError(Exception):
    """Typed error from the witness kernel.

    Carries a machine-readable error code alongside the human message.
    Used by the MCP server to return structured errors.
    """

    def __init__(self, code: WitnessErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code.name}] {message}")


# ── Lexical constitution ──────────────────────────────────────────────


@dataclass(frozen=True)
class PackRef:
    """Reference to a convention pack active during a witness event.

    Stored in precedence order on the receipt. Order is semantics:
    later packs override earlier ones on dimension collisions, so
    [base, financial] and [financial, base] produce different active
    vocabularies and different receipt hashes.
    """

    name: str
    version: str
    hash: str

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version, "hash": self.hash}


@dataclass(frozen=True)
class WitnessBasis:
    """Epistemic provenance of a witness event.

    Counts how many convention dimensions were established by each
    epistemic act. The kernel does not compute this — the caller
    attests it, and the receipt records it.

    ``discovered`` counts dimensions from LLM-discovered micro-packs
    (a subset of inferred). Defaults to 0 for backward compatibility.
    """

    declared: int
    inferred: int
    unknown: int
    discovered: int = 0

    def to_dict(self) -> dict:
        d: dict = {
            "declared": self.declared,
            "inferred": self.inferred,
            "unknown": self.unknown,
        }
        if self.discovered > 0:
            d["discovered"] = self.discovered
        return d


# ── Policy ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BoundaryObligation:
    """Convention that an unspecified tool must declare observably.

    Produced by conditional diagnosis on a partial composition.
    Each obligation says: "the tool at this port must expose *field*
    in its observable schema for the coherence fee to decrease."
    """

    placeholder_tool: str
    dimension: str
    field: str


@dataclass(frozen=True)
class PolicyProfile:
    """Named, versioned policy that maps measurement to disposition.

    The policy parameters are the explicit thresholds that determine
    judgment. Recording them in the receipt makes every witness event
    self-describing — a consumer can verify that the disposition follows
    from the measurement under the stated policy without trusting the
    kernel's internal logic.
    """

    name: str  # e.g. "witness.default.v1"
    max_blind_spots: int = 0
    max_fee: int = 0
    max_unknown: int = -1  # -1 = unlimited
    require_bridge: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "max_blind_spots": self.max_blind_spots,
            "max_fee": self.max_fee,
            "max_unknown": self.max_unknown,
            "require_bridge": self.require_bridge,
        }


DEFAULT_POLICY_PROFILE = PolicyProfile(name="witness.default.v1")


# ── Constitutional objects ───────────────────────────────────────────


class Disposition(Enum):
    """Agent-actionable judgment derived from a Diagnostic.

    Layer C (judgment): maps kernel measurement to a decision an agent
    can act on without interpreting the mathematics.
    """

    PROCEED = "proceed"
    PROCEED_WITH_RECEIPT = "proceed_with_receipt"
    PROCEED_WITH_BRIDGE = "proceed_with_bridge"
    REFUSE_PENDING_DISCLOSURE = "refuse_pending_disclosure"
    REFUSE_PENDING_HUMAN_REVIEW = "refuse_pending_human_review"


@dataclass(frozen=True)
class BridgePatch:
    """Machine-actionable repair for a blind spot.

    Layer A output: deterministic, no policy. Tells exactly which field
    to expose in which tool's observable_schema. An agent can apply this
    without understanding sheaf cohomology.
    """

    target_tool: str
    dimension: str
    field: str
    action: str  # "expose" — extensible to "normalize", "convert"
    eliminates_blind_spot: str  # edge description (e.g. "A → B")
    expected_fee_delta: int  # how much coherence_fee drops (≤ 0)

    def to_bulla_patch(self) -> dict:
        """Bulla Patch v0.1 — NOT RFC 6902 JSON Patch.

        A typed patch object specific to composition repair.
        Agents consume this to know exactly which field to expose
        in which tool's observable_schema.
        """
        return {
            "bulla_patch_version": "0.1.0",
            "action": self.action,
            "target_tool": self.target_tool,
            "field": self.field,
            "path": f"/observable_schema/{self.field}",
            "dimension": self.dimension,
            "eliminates": self.eliminates_blind_spot,
            "expected_fee_delta": self.expected_fee_delta,
        }


@dataclass(frozen=True)
class WitnessReceipt:
    """Canonical record of a witness event.

    Layer B (binding): content-addressable, tamper-evident. Links a
    specific composition state to its diagnostic measurement and the
    disposition judgment. The receipt_hash covers everything except
    itself and external anchors.
    """

    receipt_version: str  # "0.1.0"
    kernel_version: str  # bulla version
    composition_hash: str  # Composition.canonical_hash()
    diagnostic_hash: str  # Diagnostic.content_hash()
    policy_profile: PolicyProfile
    fee: int
    blind_spots_count: int
    bridges_required: int
    unknown_dimensions: int
    disposition: Disposition
    timestamp: str  # ISO-8601 UTC
    patches: tuple[BridgePatch, ...] = ()
    anchor_ref: str | None = None  # future: OTS/blockchain anchor
    parent_receipt_hash: str | None = None
    active_packs: tuple[PackRef, ...] = ()
    witness_basis: WitnessBasis | None = None
    inline_dimensions: dict | None = None

    def _hash_input(self) -> dict:
        """Single source of truth for the receipt's hashable content.

        Every field that should be covered by ``receipt_hash`` appears
        here and nowhere else. ``to_dict()`` extends this with
        ``receipt_hash`` and ``anchor_ref``; ``verify_receipt_integrity``
        reconstructs this from a serialized dict by excluding those
        same two keys.

        ``inline_dimensions`` is included ONLY when not None to preserve
        backward compatibility: pre-v0.23.0 receipts (which lack this
        field) must produce the same hash when verified by new code.
        """
        d: dict = {
            "receipt_version": self.receipt_version,
            "kernel_version": self.kernel_version,
            "composition_hash": self.composition_hash,
            "diagnostic_hash": self.diagnostic_hash,
            "policy_profile": self.policy_profile.to_dict(),
            "fee": self.fee,
            "blind_spots_count": self.blind_spots_count,
            "bridges_required": self.bridges_required,
            "unknown_dimensions": self.unknown_dimensions,
            "disposition": self.disposition.value,
            "timestamp": self.timestamp,
            "patches": [p.to_bulla_patch() for p in self.patches],
            "parent_receipt_hash": self.parent_receipt_hash,
            "active_packs": [p.to_dict() for p in self.active_packs],
            "witness_basis": (
                self.witness_basis.to_dict()
                if self.witness_basis is not None
                else None
            ),
        }
        if self.inline_dimensions is not None:
            d["inline_dimensions"] = self.inline_dimensions
        return d

    @property
    def receipt_hash(self) -> str:
        """Content-addressable hash of the receipt.

        Covers everything in ``_hash_input()`` — all fields except
        ``receipt_hash`` itself and ``anchor_ref`` (external publication
        proof added after the witness event). For deduplication of
        measurement results, use ``diagnostic_hash`` instead.

        Cached after first computation. Safe because the dataclass is
        frozen — the hash input cannot change.
        """
        try:
            return object.__getattribute__(self, "_cached_receipt_hash")
        except AttributeError:
            h = hashlib.sha256(
                json.dumps(self._hash_input(), sort_keys=True).encode()
            ).hexdigest()
            object.__setattr__(self, "_cached_receipt_hash", h)
            return h

    def to_dict(self) -> dict:
        """Serialize to dict for JSON output / MCP response."""
        d = self._hash_input()
        d["receipt_hash"] = self.receipt_hash
        d["anchor_ref"] = self.anchor_ref
        return d
