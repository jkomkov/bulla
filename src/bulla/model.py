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
from fractions import Fraction


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
    # Witness-geometry diagnostics (populated only when
    # diagnose(..., include_witness_geometry=True) is called and
    # coherence_fee > 0; otherwise empty tuples / None).
    hidden_basis: tuple[tuple[str, str], ...] = ()
    leverage_scores: tuple[Fraction, ...] = ()
    n_effective: Fraction | None = None
    coloops: tuple[tuple[str, str], ...] = ()
    loops: tuple[tuple[str, str], ...] = ()
    disclosure_set: tuple[tuple[str, str], ...] = ()

    def content_hash(self) -> str:
        """Deterministic hash of measurement content (excludes timestamps)."""
        obj: dict = {
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
        # Witness-geometry fields enter the hash ONLY when populated,
        # so receipts produced before this field-family existed still
        # hash identically.
        if self.leverage_scores:
            obj["witness_geometry"] = {
                "hidden_basis": [list(p) for p in self.hidden_basis],
                "leverage_scores": [str(x) for x in self.leverage_scores],
                "n_effective": (
                    None if self.n_effective is None else str(self.n_effective)
                ),
                "coloops": [list(p) for p in self.coloops],
                "loops": [list(p) for p in self.loops],
                "disclosure_set": [list(p) for p in self.disclosure_set],
            }
        return hashlib.sha256(
            json.dumps(obj, sort_keys=True).encode()
        ).hexdigest()


# ── Structural diagnostic ────────────────────────────────────────────
#
# Parallel to the cohomological diagnostic (Diagnostic above).
# The coboundary measures the cost of opacity (hidden conventions).
# The structural scan measures the cost of incompatibility (visible
# fields with disagreeing schemas).  Together: total verification bill.


@dataclass(frozen=True)
class SchemaOverlap:
    """A detected schema relationship between two fields on different tools.

    Base type covering both agreements and contradictions.  Agreements
    feed micro-pack generation; contradictions feed the diagnostic.
    Same comparison pipeline, different consumer.
    """

    field_a: str
    field_b: str
    tool_a: str
    tool_b: str
    similarity: float
    name_match: bool
    category: str  # "contradiction" | "homonym" | "synonym" | "agreement"
    details: str

    def to_dict(self) -> dict:
        return {
            "field_a": self.field_a,
            "field_b": self.field_b,
            "tool_a": self.tool_a,
            "tool_b": self.tool_b,
            "similarity": self.similarity,
            "name_match": self.name_match,
            "category": self.category,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SchemaOverlap:
        return cls(
            field_a=d["field_a"],
            field_b=d["field_b"],
            tool_a=d["tool_a"],
            tool_b=d["tool_b"],
            similarity=d["similarity"],
            name_match=d["name_match"],
            category=d["category"],
            details=d["details"],
        )


@dataclass(frozen=True)
class SchemaContradiction:
    """A visible-but-incompatible field pair across tools.

    Structural contradictions are about observable fields that disagree
    on constraints.  This is a different failure class from blind spots
    (hidden fields): the caller CAN see both fields, but the schemas
    are incompatible and the composition will fail at runtime.
    """

    field_a: str
    field_b: str
    tool_a: str
    tool_b: str
    mismatch_type: str  # "type" | "format" | "enum" | "range" | "pattern"
    severity: float  # 0.0–1.0
    details: str

    def to_dict(self) -> dict:
        return {
            "field_a": self.field_a,
            "field_b": self.field_b,
            "tool_a": self.tool_a,
            "tool_b": self.tool_b,
            "mismatch_type": self.mismatch_type,
            "severity": self.severity,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SchemaContradiction:
        return cls(
            field_a=d["field_a"],
            field_b=d["field_b"],
            tool_a=d["tool_a"],
            tool_b=d["tool_b"],
            mismatch_type=d["mismatch_type"],
            severity=d["severity"],
            details=d["details"],
        )


@dataclass(frozen=True)
class StructuralDiagnostic:
    """Parallel to Diagnostic: schema-level findings across tools.

    The coboundary diagnostic measures h1_obs - h1_full (the coherence
    fee from hidden conventions).  The structural diagnostic measures
    visible constraint disagreements (the contradiction score from
    incompatible schemas).

    ``overlaps`` contains ALL findings: agreements, contradictions,
    homonyms, synonyms.  ``contradictions`` is the diagnostic subset.
    Agreements are the micro-pack input.
    """

    overlaps: tuple[SchemaOverlap, ...]
    contradictions: tuple[SchemaContradiction, ...]
    n_overlapping_fields: int
    n_contradicted: int
    contradiction_score: int  # sum of severities, rounded

    def to_dict(self) -> dict:
        return {
            "overlaps": [o.to_dict() for o in self.overlaps],
            "contradictions": [c.to_dict() for c in self.contradictions],
            "n_overlapping_fields": self.n_overlapping_fields,
            "n_contradicted": self.n_contradicted,
            "contradiction_score": self.contradiction_score,
        }

    @classmethod
    def from_dict(cls, d: dict) -> StructuralDiagnostic:
        return cls(
            overlaps=tuple(SchemaOverlap.from_dict(o) for o in d["overlaps"]),
            contradictions=tuple(
                SchemaContradiction.from_dict(c) for c in d["contradictions"]
            ),
            n_overlapping_fields=d["n_overlapping_fields"],
            n_contradicted=d["n_contradicted"],
            contradiction_score=d["contradiction_score"],
        )


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


class RegistryAccessErrorCode(Enum):
    """Machine-readable error vocabulary for values_registry access.

    Raised by the registry-fetch path (``bulla packs verify`` and the
    credential-aware loader) when a pack's ``values_registry`` pointer
    cannot be materialized — typically because the consumer has not
    obtained the license required by the upstream registry.

    LICENSE_REQUIRED is the load-bearing case: a pack with
    ``license.registry_license`` of ``research-only`` or ``restricted``
    needs an explicit license credential before its values can be
    fetched. The error names which license is missing so the caller
    knows what to obtain.

    PLACEHOLDER_HASH surfaces when a pack's values_registry pointer
    uses the ``placeholder:<reason>`` sentinel format instead of a
    real ``sha256:...`` hash. This indicates the pack is structurally
    ready to verify but has not yet had a real ingest performed. The
    distinction is load-bearing because a literal ``sha256:0...0``
    placeholder would be silently treated as ``REGISTRY_HASH_MISMATCH``
    (verification failure), masking the "not yet checkable" state
    behind a "checked, mismatched" signal.
    """

    LICENSE_REQUIRED = "license_required"
    REGISTRY_UNAVAILABLE = "registry_unavailable"
    REGISTRY_HASH_MISMATCH = "registry_hash_mismatch"
    INVALID_REGISTRY_POINTER = "invalid_registry_pointer"
    PLACEHOLDER_HASH = "placeholder_hash"


class RegistryAccessError(Exception):
    """Typed error from the registry-fetch / values_registry path.

    Distinct from ``WitnessError`` because registry-access failures live
    at a different layer (fetch + license + integrity) and are typically
    recoverable by the caller (obtain the license, retry, or fall back
    to metadata-only verification).

    ``license_id`` (when relevant) names the upstream license the
    consumer must obtain — e.g. ``"NLM-UMLS"``, ``"WHO-ICD-10"``,
    ``"SWIFT-MEMBER"``. Empty for license-independent errors
    (registry unreachable, hash mismatch, etc.).
    """

    def __init__(
        self,
        code: RegistryAccessErrorCode,
        message: str,
        *,
        license_id: str = "",
        registry_uri: str = "",
    ) -> None:
        self.code = code
        self.message = message
        self.license_id = license_id
        self.registry_uri = registry_uri
        suffix = ""
        if license_id:
            suffix += f" license_id={license_id!r}"
        if registry_uri:
            suffix += f" registry_uri={registry_uri!r}"
        super().__init__(f"[{code.name}] {message}{suffix}")


# ── Lexical constitution ──────────────────────────────────────────────


@dataclass(frozen=True)
class StandardProvenance:
    """Underlying-standard provenance for a convention pack.

    When a pack's dimension metadata is derived from a published
    standard (ISO 4217, FHIR R4, ICD-10-CM, etc.), this object
    records which revision the pack mirrors, where the canonical
    artifact lives, and a content hash of the source document. The
    hash binds the pack to the exact standard revision so historical
    receipts can be replayed or audited against the right version.

    ``standard`` is the canonical short name (e.g. ``"ISO-4217"``,
    ``"FHIR"``, ``"ICD-10-CM"``). ``version`` is the standard's own
    version label (``"2024"``, ``"R4"``, ``"2024.10"``).
    ``source_uri`` points to the authoritative published artifact;
    ``source_hash`` is the SHA-256 of that artifact's bytes.

    Lives inside ``PackRef`` rather than as a top-level receipt
    field so multi-pack receipts carry per-standard provenance
    naturally.
    """

    standard: str
    version: str
    source_uri: str = ""
    source_hash: str = ""

    def to_dict(self) -> dict:
        d: dict = {"standard": self.standard, "version": self.version}
        if self.source_uri:
            d["source_uri"] = self.source_uri
        if self.source_hash:
            d["source_hash"] = self.source_hash
        return d

    @classmethod
    def from_dict(cls, d: dict) -> StandardProvenance:
        return cls(
            standard=d.get("standard", ""),
            version=d.get("version", ""),
            source_uri=d.get("source_uri", ""),
            source_hash=d.get("source_hash", ""),
        )


@dataclass(frozen=True)
class PackRef:
    """Reference to a convention pack active during a witness event.

    Stored in precedence order on the receipt. Order is semantics:
    later packs override earlier ones on dimension collisions, so
    [base, financial] and [financial, base] produce different active
    vocabularies and different receipt hashes.

    ``derives_from`` (Extension C — Standards Ingest sprint) is the
    optional pointer to the underlying standard the pack mirrors.
    Lives on the ref rather than the receipt so multi-pack receipts
    naturally carry per-standard provenance.  None for packs that are
    not derived from an external standard (e.g. the base pack itself,
    LLM-discovered micro-packs).
    """

    name: str
    version: str
    hash: str
    derives_from: StandardProvenance | None = None

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "version": self.version, "hash": self.hash}
        if self.derives_from is not None:
            d["derives_from"] = self.derives_from.to_dict()
        return d


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

    Each obligation says: "the tool at this port must expose *field*
    in its observable schema for the coherence fee to decrease."

    ``placeholder_tool`` has two production contexts:
    - From ``conditional_diagnose``: the placeholder tool name inserted
      for open ports (e.g. ``"__placeholder_0"``).
    - From ``boundary_obligations_from_decomposition``: the server group
      name at the partition boundary (e.g. ``"github"``).
    """

    placeholder_tool: str
    dimension: str
    field: str
    source_edge: str = ""
    expected_value: str = ""

    def to_dict(self) -> dict:
        d: dict = {
            "placeholder_tool": self.placeholder_tool,
            "dimension": self.dimension,
            "field": self.field,
        }
        if self.source_edge:
            d["source_edge"] = self.source_edge
        if self.expected_value:
            d["expected_value"] = self.expected_value
        return d


class ObligationVerdict(Enum):
    """Verdict from guided discovery probing a single obligation.

    CONFIRMED: the field IS observable in the target tool's output.
    DENIED:    the field is hidden or absent from the tool.
    UNCERTAIN: the LLM cannot determine observability.
    """

    CONFIRMED = "confirmed"
    DENIED = "denied"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class ProbeResult:
    """Result of probing one obligation via guided discovery.

    Pairs a ``BoundaryObligation`` with the LLM's verdict on whether
    the obligated field is observable in the target tool.  When the
    verdict is CONFIRMED, ``convention_value`` may carry the discovered
    convention value (e.g. ``"zero_based"``).
    """

    obligation: BoundaryObligation
    verdict: ObligationVerdict
    evidence: str = ""
    convention_value: str = ""

    def to_dict(self) -> dict:
        d: dict = {
            "obligation": self.obligation.to_dict(),
            "verdict": self.verdict.value,
        }
        if self.evidence:
            d["evidence"] = self.evidence
        if self.convention_value:
            d["convention_value"] = self.convention_value
        return d


class ContradictionSeverity(Enum):
    """Severity of a detected convention contradiction.

    MISMATCH: two or more distinct values for the same dimension.
    """

    MISMATCH = "mismatch"


@dataclass(frozen=True)
class ContradictionReport:
    """A detected convention contradiction on a single dimension.

    ``values`` and ``sources`` are always sorted alphabetically so that
    two reports with the same content are equal regardless of discovery
    order.  Canonical ordering is enforced at construction time in
    ``detect_contradictions()``.
    """

    dimension: str
    values: tuple[str, ...]
    sources: tuple[str, ...]
    severity: ContradictionSeverity

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "values": list(self.values),
            "sources": list(self.sources),
            "severity": self.severity.value,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ContradictionReport:
        return cls(
            dimension=d["dimension"],
            values=tuple(d["values"]),
            sources=tuple(d["sources"]),
            severity=ContradictionSeverity(d["severity"]),
        )


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
    max_unmet_obligations: int = -1  # -1 = disabled, 0 = strict
    max_contradictions: int = -1  # -1 = disabled, 0 = strict (convention contradictions)
    max_structural_contradictions: int = -1  # -1 = disabled, 0 = strict (schema contradictions)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "max_blind_spots": self.max_blind_spots,
            "max_fee": self.max_fee,
            "max_unknown": self.max_unknown,
            "require_bridge": self.require_bridge,
            "max_unmet_obligations": self.max_unmet_obligations,
            "max_contradictions": self.max_contradictions,
            "max_structural_contradictions": self.max_structural_contradictions,
        }


DEFAULT_POLICY_PROFILE = PolicyProfile(name="witness.default.v1")


# ── Constitutional objects ───────────────────────────────────────────


class Disposition(Enum):
    """Agent-actionable judgment derived from a Diagnostic.

    Layer C (judgment): maps kernel measurement to a decision an agent
    can act on without interpreting the mathematics.

    Four quadrants on the 2D risk surface (fee x contradiction_score):
      fee=0, contradictions=0  -> PROCEED
      fee>0, contradictions=0  -> PROCEED_WITH_BRIDGE / REFUSE
      fee=0, contradictions>0  -> PROCEED_WITH_CAUTION
      fee>0, contradictions>0  -> REFUSE
    """

    PROCEED = "proceed"
    PROCEED_WITH_RECEIPT = "proceed_with_receipt"
    PROCEED_WITH_BRIDGE = "proceed_with_bridge"
    PROCEED_WITH_CAUTION = "proceed_with_caution"
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
    parent_receipt_hashes: tuple[str, ...] | None = None
    active_packs: tuple[PackRef, ...] = ()
    witness_basis: WitnessBasis | None = None
    inline_dimensions: dict | None = None
    boundary_obligations: tuple[BoundaryObligation, ...] | None = None
    contradictions: tuple[ContradictionReport, ...] | None = None
    unmet_obligations: int = 0
    structural_contradictions: tuple[SchemaContradiction, ...] | None = None
    contradiction_score: int = 0
    pack_attributions: tuple[str, ...] | None = None
    """Hash-references (e.g. sha256:...) to NOTICES.md entries that the
    standards bodies underlying ``active_packs`` require crediting.

    Hash-references rather than inline text to prevent receipt bloat.
    Resolved via the docs/STANDARDS-INGEST-NOTICES.md attribution master
    file (Phase 6 deliverable). When all active packs carry only ``open``
    registry licenses with no attribution requirement, this is None.
    """

    def _hash_input(self) -> dict:
        """Single source of truth for the receipt's hashable content.

        Every field that should be covered by ``receipt_hash`` appears
        here and nowhere else. ``to_dict()`` extends this with
        ``receipt_hash`` and ``anchor_ref``; ``verify_receipt_integrity``
        reconstructs this from a serialized dict by excluding those
        same two keys.

        ``parent_receipt_hashes``, ``inline_dimensions``,
        ``boundary_obligations``, ``contradictions``,
        ``unmet_obligations``, ``structural_contradictions``,
        ``contradiction_score``, and ``pack_attributions`` are included
        ONLY when non-None/non-zero to preserve backward compatibility:
        pre-existing receipts must produce the same hash when verified
        by new code.
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
            "active_packs": [p.to_dict() for p in self.active_packs],
            "witness_basis": (
                self.witness_basis.to_dict()
                if self.witness_basis is not None
                else None
            ),
        }
        if self.parent_receipt_hashes is not None:
            d["parent_receipt_hashes"] = list(self.parent_receipt_hashes)
        if self.inline_dimensions is not None:
            d["inline_dimensions"] = self.inline_dimensions
        if self.boundary_obligations is not None:
            d["boundary_obligations"] = [o.to_dict() for o in self.boundary_obligations]
        if self.contradictions is not None:
            d["contradictions"] = [c.to_dict() for c in self.contradictions]
        if self.unmet_obligations > 0:
            d["unmet_obligations"] = self.unmet_obligations
        if self.structural_contradictions is not None:
            d["structural_contradictions"] = [
                c.to_dict() for c in self.structural_contradictions
            ]
        if self.contradiction_score > 0:
            d["contradiction_score"] = self.contradiction_score
        if self.pack_attributions is not None:
            d["pack_attributions"] = list(self.pack_attributions)
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
