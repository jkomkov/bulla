"""Bulla composition certificate — witness-ready schema (v1.0, Sprint 14+15).

A composition certificate is a structured attestation: it bundles
identity (subject hash), method versioning, regime evidence, diagnostic
measurements, structured claims, the active parent-certificate slot,
and reserved slots for future witness infrastructure (issuer, signature,
supersession, attestation, receipt).

Sprint 14 principle: "Can this certificate be replayed, hashed,
parented, signed later, and interpreted without reading display strings?"

Sprint 15 principle: "Local certificates compose by hash but not by
claim." Parent hashes are evidence of ancestry; they are NOT proof of
global validity.

Schema layout (v1.0, locked):

    {
      "certificate_schema_version": "1.0",
      "subject":     { name, source_path, composition_sha256, pack_stack_sha256, manifest_hashes },
      "method":      { regime_classifier, diagnostic, witness_geometry, cross_server_decomposition },
      "regime":      { full RegimeReport — evidence, NOT claims },
      "diagnostic":  { coherence_fee, blind_spots_count, n_unbridged, bridges_count,
                       cross_server_decomposition?, witness_geometry? },
      "claims":      { schema_shape_valid, fee_is_nonnegative, fee_is_interpretable,
                       exact_disclosure_equivalence, repair_basis_status,
                       subject_bound },                     # all structured {value, status, licensed_by}
      "scope":       { tools (sorted), edges (sorted) },
      "parent_certificate_hashes": [],                      # active in Sprint 15 via certify(..., parent_certificate_hashes=...);
                                                            # sorted canonically inside certify()
      "issuer":      {"type": "local", "id": null},         # reserved for future signing
      "signature":   null,                                  # reserved
      "supersedes":  null,                                  # reserved
      "violations":  [],                                    # Sprint 10 schema-shape findings
      "display":     { fee_interpretation, repair_semantics },  # v0 free-text labels (UI-only)
      "timestamp":   "<UTC ISO>",
      "bulla_version": "<X.Y.Z>",
      "certificate_content_hash": "sha256:<hex64>",         # over canonical JSON minus timestamp,
                                                            # signature, the hash itself,
                                                            # attestation_hash, receipt_hash, display
      "attestation_hash": null,                             # reserved for future signed envelope
      "receipt_hash": null                                  # reserved for future operational receipts
    }

Design discipline:

  * `claims` is the source of truth for machine consumers.
  * `display` is UI-only; never read it programmatically; deliberately
    excluded from the content-hash preimage so wording edits never
    invalidate parent-cert hashes.
  * `regime` is evidence (predicate values); claims are derived FROM it
    but distinct.
  * `certificate_content_hash` is determined by every meaningful field
    except timestamp, signature, the hash itself, the future
    attestation/receipt slots, and display — so two `certify()` calls
    on the same composition (with the same parents) produce identical
    content hashes regardless of when run.
  * `parent_certificate_hashes` are sorted canonically inside `certify()`
    so parent permutations do not change the content hash. v1.0 treats
    parent order as set-semantics, not causal/supersession order.
  * No signing, no networking, no parent-bundle merging. Parents are
    structurally bound to identity (via the content hash) but no claim
    in v1.0 asserts that parents prove global validity. The active
    surface is hash-level only.

Public surface:

  CompositionCertificate          dataclass (frozen)
  Claim                           structured assertion {value, status, licensed_by, ...}
  certify(comp, ..., parent_certificate_hashes=())
                                  build a certificate from a Composition
  to_dict(cert)                   JSON-ready dict (canonical key order)
  to_json(cert, indent=2)         JSON text
  CERTIFICATE_SCHEMA_VERSION      "1.0"
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from bulla import __version__
from bulla.diagnostic import decompose_fee, diagnose
from bulla.model import Composition
from bulla.regime import RegimeReport, RegimeViolation, classify, validate_regime


CERTIFICATE_SCHEMA_VERSION = "1.0"


# ---- v0 display lookup tables (preserved for UI; demoted from source-of-truth) ----

def _fee_interpretation(report: RegimeReport) -> str:
    """v0 free-text label — preserved for the `display` block.
    Machine consumers should read `claims.fee_is_interpretable.status` instead."""
    if not report.is_well_formed_for_fee:
        return "signed obstruction imbalance (NOT a fee — see regime warning)"
    if report.is_exact_regime_conservative and report.fee_formula == 0:
        return "no obstruction (exact-regime certified)"
    if report.is_exact_regime_conservative and report.fee_formula > 0:
        return "true non-negative fee (theorem regime)"
    if report.fee_formula == 0:
        return "no obstruction (well-formed regime)"
    return "true non-negative fee (well-formed regime)"


def _repair_semantics(report: RegimeReport) -> str:
    """v0 free-text label — preserved for the `display` block.
    Machine consumers should read `claims.repair_basis_status.status` instead.

    Sprint 13 wording discipline preserved: well-formed-but-not-exact
    explicitly does NOT claim disclosure-set equivalence; only exact-
    conservative does."""
    if not report.is_well_formed_for_fee:
        return (
            "fix schema definition — observable_schema must be a subset of "
            "internal_state per tool (Sprint 9 schema-shape invariant)"
        )
    if report.fee_formula == 0:
        return "no repair needed; coherence_fee = 0"
    if report.is_exact_regime_conservative:
        return (
            "repairable; matroid basis = minimum disclosure set "
            "(strongest theorem-regime guarantees apply)"
        )
    return (
        "repairable as a non-negative fee; exact disclosure-set equivalence "
        "is NOT certified outside exact-conservative regime — use the "
        "matroid basis as a repair candidate, but verify against "
        "minimum_disclosure_set if the specific repair fields matter "
        "(see bulla/docs/REGIME.md regime lattice)"
    )


# ---- Structured Claim ----

@dataclass(frozen=True)
class Claim:
    """A structured assertion within a certificate's `claims` block.

    Sprint 14 v1.0 schema:
      value:        the substantive claim payload (bool, str enum, etc.)
      status:       "certified" | "candidate" | "not_certified" | "not_applicable"
      licensed_by:  list of regime predicate names (or `certificate_subject_hash`)
                    that justify the claim. Empty list when status != "certified".
      not_licensed: optional list of related claims that this regime does
                    NOT license (used by `repair_basis_status` when status
                    is "candidate" to make the gap explicit). Empty by default.
    """
    value: Any
    status: str  # "certified" | "candidate" | "not_certified" | "not_applicable"
    licensed_by: tuple[str, ...] = ()
    not_licensed: tuple[str, ...] = ()


def _claim_to_dict(c: Claim) -> dict:
    out: dict[str, Any] = {
        "value": c.value,
        "status": c.status,
        "licensed_by": list(c.licensed_by),
    }
    if c.not_licensed:
        out["not_licensed"] = list(c.not_licensed)
    return out


# ---- Claim derivation (v1.0 set; locked in plan) ----

def _build_claims(report: RegimeReport, *, has_subject_hash: bool) -> dict[str, Claim]:
    """Derive the v1.0 claim set from a RegimeReport. Pure function over
    the regime — no side effects, no external state. The `has_subject_hash`
    parameter is true iff `composition_sha256` was successfully computed
    (it always is in v1.0; reserved for future failure modes)."""

    # 1. schema_shape_valid — Sprint 9 structural predicate
    schema_shape_valid = Claim(
        value=report.has_projective_observables,
        status="certified" if report.has_projective_observables else "not_certified",
        licensed_by=("has_projective_observables",) if report.has_projective_observables else (),
    )

    # 2. fee_is_nonnegative — Sprint 8 measured rank predicate
    fee_is_nonnegative = Claim(
        value=report.is_well_formed_for_fee,
        status="certified" if report.is_well_formed_for_fee else "not_certified",
        licensed_by=("is_well_formed_for_fee",) if report.is_well_formed_for_fee else (),
    )

    # 3. fee_is_interpretable — separate claim, identical derivation in v0/v1
    # (preserved as separate claim per plan: future regime extensions may
    # license one without the other; claim names are part of witness contract).
    fee_is_interpretable = Claim(
        value=report.is_well_formed_for_fee,
        status="certified" if report.is_well_formed_for_fee else "not_certified",
        licensed_by=("is_well_formed_for_fee",) if report.is_well_formed_for_fee else (),
    )

    # 4. exact_disclosure_equivalence — Sprint 11/12 theorem-regime claim
    #
    # Plan said: licensed_by = ["is_exact_regime_conservative"]. But the
    # exact-conservative predicates (DFD-conservative + CHP-conservative)
    # are structurally INDEPENDENT of well-formedness — an ill-formed
    # composition can satisfy DFD + CHP yet have negative fee. Sprint 12's
    # `test_exact_regime_disclosure_agreement.py` only verified the
    # disclosure-equivalence theorem on well-formed exact-conservative
    # compositions; claiming it on an ill-formed composition would be
    # overclaiming.
    #
    # Fix (refinement on top of plan): require BOTH well-formed-for-fee
    # AND exact-conservative. licensed_by carries both predicates.
    exact_disclosure_certified = (
        report.is_exact_regime_conservative
        and report.is_well_formed_for_fee
    )
    exact_disclosure_equivalence = Claim(
        value=exact_disclosure_certified,
        status=("certified" if exact_disclosure_certified else "not_certified"),
        licensed_by=(
            ("is_well_formed_for_fee", "is_exact_regime_conservative")
            if exact_disclosure_certified else ()
        ),
    )

    # 5. repair_basis_status — bridge to Sprint 15 repair logic
    if not report.is_well_formed_for_fee:
        repair = Claim(
            value="not_certified", status="not_certified", licensed_by=()
        )
    elif report.fee_formula == 0:
        repair = Claim(
            value="not_applicable",
            status="not_applicable",
            licensed_by=("is_well_formed_for_fee",),
        )
    elif report.is_exact_regime_conservative:
        repair = Claim(
            value="certified",
            status="certified",
            licensed_by=("is_well_formed_for_fee", "is_exact_regime_conservative"),
        )
    else:
        # well-formed ∧ fee>0 ∧ ¬exact-conservative
        repair = Claim(
            value="candidate",
            status="candidate",
            licensed_by=("is_well_formed_for_fee",),
            not_licensed=("exact_disclosure_equivalence",),
        )

    # 6. subject_bound — internal-consistency claim
    # True iff regime is classified AND subject hash computable AND no
    # violations blocking. NOTE: this is NOT a "global composition is
    # valid/coherent" claim — that name is reserved for Sprint 15+ when
    # pairwise parents are actually compared to a global certificate.
    # `subject_bound` says: "this certificate's claims are bound to a
    # specific subject hash, the regime classifier ran, and the
    # certificate is internally consistent."
    bound = (
        has_subject_hash
        and report.has_projective_observables
    )
    subject_bound = Claim(
        value=bound,
        status="certified" if bound else "not_certified",
        licensed_by=("certificate_subject_hash",) if bound else (),
    )

    return {
        "schema_shape_valid": schema_shape_valid,
        "fee_is_nonnegative": fee_is_nonnegative,
        "fee_is_interpretable": fee_is_interpretable,
        "exact_disclosure_equivalence": exact_disclosure_equivalence,
        "repair_basis_status": repair,
        "subject_bound": subject_bound,
    }


# ---- Certificate dataclass ----

@dataclass(frozen=True)
class CompositionCertificate:
    """Sprint 14 v1.0 per-composition certificate.

    Field order in `to_dict` matches the canonical schema layout. Fields
    omitted when None (e.g., `cross_server_decomposition`, `witness_geometry`,
    `pack_stack_sha256`) but always present (with explicit `null`) when
    they are reserved slots for future infrastructure (e.g., `signature`,
    `supersedes`).
    """

    # --- header ---
    certificate_schema_version: str

    # --- subject (identity) ---
    subject: dict
    # subject = {
    #   "name": str,
    #   "source_path": str | None,
    #   "composition_sha256": str,
    #   "pack_stack_sha256": str | None,
    #   "manifest_hashes": list[dict],
    # }

    # --- method (producer versioning) ---
    method: dict

    # --- regime (Sprint 8/9/11 evidence) ---
    regime: RegimeReport

    # --- diagnostic (measurements) ---
    diagnostic: dict

    # --- claims (structured assertions) ---
    claims: dict[str, Claim]

    # --- scope (sorted; for future parentage comparison) ---
    scope: dict

    # --- reserved slots for incremental bundles / signing / supersession ---
    parent_certificate_hashes: tuple[str, ...]
    issuer: dict
    signature: Optional[str]
    supersedes: Optional[str]

    # --- schema-shape violations (Sprint 10) ---
    violations: tuple[RegimeViolation, ...]

    # --- display (UI-only; v0 free-text labels) ---
    display: dict

    # --- temporal / version ---
    timestamp: str
    bulla_version: str

    # --- hash anchors (Sprint 14 refinement) ---
    # `certificate_content_hash` is the deterministic semantic anchor.
    # It is stable under display/timestamp/signature changes, but changes
    # under subject, method, regime, diagnostic, claims, scope, parent,
    # issuer, supersession, or violation changes.
    #
    # `attestation_hash` is reserved for the future signed timed artifact
    # (full envelope including timestamp + signature). Slot only in v1.0.
    #
    # `receipt_hash` is reserved for future operational receipts (e.g.,
    # LiveSession-emitted updates). Slot only in v1.0.
    certificate_content_hash: str
    attestation_hash: Optional[str]
    receipt_hash: Optional[str]


# ---- Helpers ----

def _composition_sha256(comp: Composition) -> str:
    """Reuse Composition.canonical_hash() if available; otherwise a
    minimal structural fallback."""
    if hasattr(comp, "canonical_hash"):
        return comp.canonical_hash()
    h = hashlib.sha256()
    h.update(comp.name.encode("utf-8"))
    h.update(b"\n")
    for t in comp.tools:
        h.update(t.name.encode("utf-8"))
        h.update(repr(sorted(t.internal_state)).encode("utf-8"))
        h.update(repr(sorted(t.observable_schema)).encode("utf-8"))
        h.update(b"\n")
    for e in comp.edges:
        h.update(f"{e.from_tool}->{e.to_tool}".encode("utf-8"))
        for d in e.dimensions:
            h.update(f"{d.name}|{d.from_field}|{d.to_field}".encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _detect_servers(comp: Composition) -> list[str]:
    """Distinct server prefixes (the `xxx` in `xxx__tool`)."""
    seen: list[str] = []
    for t in comp.tools:
        if "__" in t.name:
            prefix = t.name.split("__", 1)[0]
            if prefix not in seen:
                seen.append(prefix)
    return seen


def _build_subject(comp: Composition, source_path: Optional[str]) -> dict:
    return {
        "name": comp.name,
        "source_path": source_path,
        "composition_sha256": _composition_sha256(comp),
        "pack_stack_sha256": None,  # reserved; populated by future pack-aware certify
        "manifest_hashes": [],       # reserved; populated when callers supply manifest provenance
    }


def _build_method(bulla_version: str) -> dict:
    """Producer versioning. Each entry names the producer module + the
    Bulla version that produced it. Future sprints may make these
    independently versioned; for now they all track `bulla_version`."""
    suffix = f"@{bulla_version}"
    return {
        "regime_classifier":          f"bulla.regime.classify{suffix}",
        "diagnostic":                 f"bulla.diagnostic.diagnose{suffix}",
        "witness_geometry":           f"bulla.witness_geometry.compute_all{suffix}",
        "cross_server_decomposition": f"bulla.diagnostic.decompose_fee{suffix}",
    }


def _regime_to_dict(report: RegimeReport) -> dict:
    return {
        "rank_obs": report.rank_obs,
        "rank_internal": report.rank_internal,
        "fee_formula": report.fee_formula,
        "is_all_hidden": report.is_all_hidden,
        "is_all_observable": report.is_all_observable,
        "has_internal_dominance": report.has_internal_dominance,
        "has_balanced_ranks": report.has_balanced_ranks,
        "has_obs_dominance": report.has_obs_dominance,
        "is_well_formed_for_fee": report.is_well_formed_for_fee,
        "has_projective_observables": report.has_projective_observables,
        "has_dfd_conservative": report.has_dfd_conservative,
        "has_chp_conservative": report.has_chp_conservative,
        "is_exact_regime_conservative": report.is_exact_regime_conservative,
    }


def _violation_to_dict(v: RegimeViolation) -> dict:
    return {
        "kind": v.kind,
        "tool_name": v.tool_name,
        "fields": list(v.fields),
        "description": v.description,
    }


def _build_diagnostic_block(diag, comp: Composition) -> dict:
    """Build the `diagnostic` block: measurements + optional cross-server +
    optional witness-geometry. Cross-server populated only for multi-server
    compositions; witness-geometry populated only when leverage scores are
    available (`fee > 0` and `include_witness_geometry=True` at certify time)."""
    out: dict[str, Any] = {
        "coherence_fee": diag.coherence_fee,
        "blind_spots_count": len(diag.blind_spots),
        "n_unbridged": diag.n_unbridged,
        "bridges_count": len(diag.bridges),
        "cross_server_decomposition": None,
        "witness_geometry": None,
    }

    # cross-server decomposition (multi-server only)
    servers = _detect_servers(comp)
    if len(servers) >= 2:
        partition: list[frozenset[str]] = []
        for s in servers:
            members = frozenset(t.name for t in comp.tools if t.name.startswith(s + "__"))
            partition.append(members)
        try:
            decomp = decompose_fee(comp, partition)
            out["cross_server_decomposition"] = {
                "n_servers": len(servers),
                "servers": list(servers),
                "total_fee": decomp.total_fee,
                "local_fees": list(decomp.local_fees),
                "boundary_fee": decomp.boundary_fee,
            }
        except Exception:
            pass  # leave None on failure

    # witness-geometry (only when leverage scores are available)
    if diag.leverage_scores:
        out["witness_geometry"] = {
            "schema_note": (
                "All leverage scores and n_effective are exact rational "
                "strings ('p/q'), never floats."
            ),
            "n_effective": (
                None if diag.n_effective is None else str(diag.n_effective)
            ),
            "leverage": [
                {"tool": tool, "field": field, "score": str(score)}
                for (tool, field), score in zip(
                    diag.hidden_basis, diag.leverage_scores
                )
            ],
            "coloops": [list(p) for p in diag.coloops],
            "loops": [list(p) for p in diag.loops],
            "disclosure_set": [list(p) for p in diag.disclosure_set],
        }

    return out


def _canonicalize_scope(comp: Composition) -> dict:
    """Sorted, canonical scope (for future parent-scope comparison)."""
    tool_names = sorted(t.name for t in comp.tools)
    edges_canon: list[dict] = []
    for e in comp.edges:
        dims = sorted(d.name for d in e.dimensions if d.name)
        edges_canon.append({
            "from_tool": e.from_tool,
            "to_tool": e.to_tool,
            "dimensions": dims,
        })
    edges_canon.sort(key=lambda e: (e["from_tool"], e["to_tool"], tuple(e["dimensions"])))
    return {"tools": tool_names, "edges": edges_canon}


def _certificate_dict_for_content_hash(cert: CompositionCertificate) -> dict:
    """Build the canonical dict over which `certificate_content_hash` is
    computed.

    Sprint 14 refined preimage: excludes `timestamp` (clock-dependent),
    `signature` (mutable post-issuance), `certificate_content_hash` itself
    (cannot be self-referential), the future signed-envelope slots
    `attestation_hash` and `receipt_hash` (will be filled later without
    invalidating content hash), and `display` (UI-only — wording edits
    must not change parent-cert hashes).

    The preimage IS sensitive to: subject, method, regime, diagnostic,
    claims, scope, parent_certificate_hashes, issuer, supersedes,
    violations, certificate_schema_version, bulla_version."""
    d = _to_dict_internal(cert)
    d.pop("timestamp", None)
    d.pop("signature", None)
    d.pop("certificate_content_hash", None)
    d.pop("attestation_hash", None)
    d.pop("receipt_hash", None)
    d.pop("display", None)
    return d


def _compute_certificate_content_hash(cert: CompositionCertificate) -> str:
    """SHA-256 over `json.dumps(canonical_dict, sort_keys=True, separators=(',', ':'))`.
    Format: `"sha256:<64 hex chars>"`. The prefix is mandatory so future
    hash-algorithm bumps are detectable.

    See `_certificate_dict_for_content_hash` for the preimage discipline.
    """
    canonical = _certificate_dict_for_content_hash(cert)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{h}"


def _to_dict_internal(cert: CompositionCertificate) -> dict:
    """Internal serializer used by both `to_dict` and the hash preimage.
    Field order matches the canonical v1.0 schema layout (Sprint 14)."""
    out: dict[str, Any] = {
        "certificate_schema_version": cert.certificate_schema_version,
        "subject": dict(cert.subject),
        "method": dict(cert.method),
        "regime": _regime_to_dict(cert.regime),
        "diagnostic": dict(cert.diagnostic),
        "claims": {k: _claim_to_dict(c) for k, c in cert.claims.items()},
        "scope": dict(cert.scope),
        "parent_certificate_hashes": list(cert.parent_certificate_hashes),
        "issuer": dict(cert.issuer),
        "signature": cert.signature,
        "supersedes": cert.supersedes,
        "violations": [_violation_to_dict(v) for v in cert.violations],
        "display": dict(cert.display),
        "timestamp": cert.timestamp,
        "bulla_version": cert.bulla_version,
        "certificate_content_hash": cert.certificate_content_hash,
        "attestation_hash": cert.attestation_hash,
        "receipt_hash": cert.receipt_hash,
    }
    return out


# ---- Public API ----

def certify(
    comp: Composition,
    *,
    source_path: Optional[str] = None,
    include_witness_geometry: bool = True,
    parent_certificate_hashes: tuple[str, ...] | list[str] = (),
) -> CompositionCertificate:
    """Sprint 14: build a v1.0 CompositionCertificate from a Composition.

    Pure orchestration of existing producers (`bulla.regime.classify`,
    `bulla.diagnostic.diagnose`, `bulla.diagnostic.decompose_fee`).

    Sprint 15 extension: callers may pass `parent_certificate_hashes` to
    populate the active parent slot. Parents are recorded as evidence —
    they are NOT proof of global validity. The global certificate's
    claims are still computed fresh from the global composition; parent
    hashes only pin the ancestry. The discipline:

      "Local certificates compose by hash but not by claim."

    See `papers/composition-doctrine/sprint15_demo/` for the canonical
    demo of this principle.

    Parent ordering: the input `parent_certificate_hashes` is sorted
    canonically inside `certify()` before being stored and hashed. v1.0
    treats parent order as set-semantics — a permutation of the same
    parents must produce the same content hash. (Future schema versions
    that introduce ordered parentage — e.g., supersession chains — would
    bump the schema version and revisit this.)

    The returned certificate's `certificate_content_hash` is computed
    AFTER all other fields (including the canonically-sorted
    `parent_certificate_hashes`) are assembled, over the canonical JSON
    serialization excluding `timestamp`, `signature`,
    `certificate_content_hash` itself, `attestation_hash`,
    `receipt_hash`, and `display`.
    """
    report = classify(comp)
    diag = diagnose(comp, include_witness_geometry=include_witness_geometry)
    violations = validate_regime(comp)

    subject = _build_subject(comp, source_path)
    has_subject_hash = bool(subject.get("composition_sha256"))

    # v1.0 parent order is set-semantics: sort canonically so permutations
    # of the same parents produce identical content hashes. Sprint 15
    # demos rely on this — `papers/composition-doctrine/sprint15_demo/`
    # builds parents from a dict iteration whose order is incidental.
    canonical_parents = tuple(sorted(parent_certificate_hashes))

    cert_no_hash = CompositionCertificate(
        certificate_schema_version=CERTIFICATE_SCHEMA_VERSION,
        subject=subject,
        method=_build_method(__version__),
        regime=report,
        diagnostic=_build_diagnostic_block(diag, comp),
        claims=_build_claims(report, has_subject_hash=has_subject_hash),
        scope=_canonicalize_scope(comp),
        parent_certificate_hashes=canonical_parents,
        issuer={"type": "local", "id": None},
        signature=None,
        supersedes=None,
        violations=tuple(violations),
        display={
            "fee_interpretation": _fee_interpretation(report),
            "repair_semantics": _repair_semantics(report),
        },
        timestamp=datetime.now(timezone.utc).isoformat(),
        bulla_version=__version__,
        certificate_content_hash="",  # placeholder; computed next
        attestation_hash=None,         # reserved for future signed envelope
        receipt_hash=None,             # reserved for future operational receipts
    )

    # Compute certificate_content_hash over the canonical preimage
    # (excludes timestamp, signature, the hash itself, attestation_hash,
    # receipt_hash, and display — the last because UI rewording must not
    # change parent-cert hashes).
    final_hash = _compute_certificate_content_hash(cert_no_hash)

    # Rebuild with the hash filled in (frozen dataclass requires re-construction)
    return CompositionCertificate(
        certificate_schema_version=cert_no_hash.certificate_schema_version,
        subject=cert_no_hash.subject,
        method=cert_no_hash.method,
        regime=cert_no_hash.regime,
        diagnostic=cert_no_hash.diagnostic,
        claims=cert_no_hash.claims,
        scope=cert_no_hash.scope,
        parent_certificate_hashes=cert_no_hash.parent_certificate_hashes,
        issuer=cert_no_hash.issuer,
        signature=cert_no_hash.signature,
        supersedes=cert_no_hash.supersedes,
        violations=cert_no_hash.violations,
        display=cert_no_hash.display,
        timestamp=cert_no_hash.timestamp,
        bulla_version=cert_no_hash.bulla_version,
        certificate_content_hash=final_hash,
        attestation_hash=cert_no_hash.attestation_hash,
        receipt_hash=cert_no_hash.receipt_hash,
    )


def to_dict(cert: CompositionCertificate) -> dict:
    """JSON-ready dict in the canonical v1.0 layout."""
    return _to_dict_internal(cert)


def to_json(cert: CompositionCertificate, *, indent: int = 2) -> str:
    """JSON text in the canonical v1.0 layout. `indent=2` matches existing
    `bulla diagnose --format json` convention."""
    return json.dumps(to_dict(cert), indent=indent)
