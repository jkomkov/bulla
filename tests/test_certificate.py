"""Sprint 14 — composition certificate tests (v1.0 witness-ready schema).

Verifies:

  - Schema version is "1.0" (literal).
  - certificate_hash is deterministic across multiple certify() calls
    on the same composition (independent of timestamp/signature/itself).
  - certificate_hash is sensitive to composition changes.
  - Claim coverage: every claim's status enum value reachable via
    appropriate regime fixtures.
  - display block matches v0 free-text labels byte-exactly (UI back-compat).
  - method block versioning (each entry contains "@<version>").
  - JSON round-trip stability.
  - Multi-server cross-decomposition + witness-geometry on/off.
  - Schema-shape violations populate from Sprint 10 validate_regime.
  - The Sprint 11/12 epistemic discipline is preserved in `claims.repair_basis_status`
    (not just in display.repair_semantics).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bulla" / "src"))
sys.path.insert(0, str(REPO / "bulla" / "tests"))

from bulla.certificate import (
    CERTIFICATE_SCHEMA_VERSION,
    Claim,
    CompositionCertificate,
    _build_claims,
    _canonicalize_scope,
    _detect_servers,
    _fee_interpretation,
    _repair_semantics,
    certify,
    to_dict,
    to_json,
)
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.parser import load_composition
from bulla.regime import RegimeReport, classify


# ---- Fixtures (synthetic compositions across regime lattice) ----

def _build_cycle(k: int, m: int) -> Composition:
    n = k * m
    tools = tuple(
        ToolSpec(name=f"t{i}", internal_state=("f",), observable_schema=())
        for i in range(n)
    )
    edges = []
    for c in range(k):
        for i in range(m):
            u = c * m + i
            v = c * m + (i + 1) % m
            edges.append(Edge(
                from_tool=f"t{u}", to_tool=f"t{v}",
                dimensions=(SemanticDimension(name="f_match", from_field="f", to_field="f"),),
            ))
    return Composition(name=f"A_{k}_{m}", tools=tools, edges=tuple(edges))


def _build_ill_formed() -> Composition:
    """observable_schema not subset of internal_state → ill-formed."""
    t1 = ToolSpec(name="t1", internal_state=("hidden_a",), observable_schema=("secret",))
    t2 = ToolSpec(name="t2", internal_state=("hidden_b",), observable_schema=("secret",))
    edge = Edge(
        from_tool="t1", to_tool="t2",
        dimensions=(SemanticDimension(name="m", from_field="secret", to_field="secret"),),
    )
    return Composition(name="ill_formed", tools=(t1, t2), edges=(edge,))


def _build_wf_pos_not_exact() -> Composition:
    """well-formed, fee>0, NOT exact-conservative (CHP fails: duplicate dim)."""
    t1 = ToolSpec(name="t1", internal_state=("a",), observable_schema=("a",))
    t2 = ToolSpec(name="t2", internal_state=("a",), observable_schema=())
    t3 = ToolSpec(name="t3", internal_state=("a",), observable_schema=())
    edges = (
        Edge(
            from_tool="t1", to_tool="t2",
            dimensions=(SemanticDimension(name="m1", from_field="a", to_field="a"),),
        ),
        Edge(
            from_tool="t2", to_tool="t3",
            dimensions=(
                SemanticDimension(name="m2", from_field="a", to_field="a"),
                SemanticDimension(name="m3", from_field="a", to_field="a"),  # CHP-bad
            ),
        ),
    )
    return Composition(name="wf_pos_not_exact", tools=(t1, t2, t3), edges=edges)


# ---- 1. Schema version is "1.0" ----

def test_certificate_schema_version_is_1_0():
    """If a future sprint bumps the schema, this test fails loudly so
    consumers see the change is intentional."""
    assert CERTIFICATE_SCHEMA_VERSION == "1.0"
    comp = _build_cycle(2, 4)
    cert = certify(comp)
    d = to_dict(cert)
    assert d["certificate_schema_version"] == "1.0"


# ---- 2. certificate_hash discipline ----

def test_certificate_content_hash_format():
    """Hash is `"sha256:<64 hex chars>"`."""
    comp = _build_cycle(2, 4)
    cert = certify(comp)
    h = cert.certificate_content_hash
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64
    # All chars after prefix are valid hex
    int(h.removeprefix("sha256:"), 16)


def test_certificate_content_hash_deterministic():
    """Two certify() calls on the same composition produce identical
    hashes (timestamps differ; certificate_hash does not)."""
    comp = _build_cycle(2, 4)
    cert1 = certify(comp)
    cert2 = certify(comp)
    assert cert1.timestamp != cert2.timestamp, (
        "Timestamps should differ across calls (otherwise the test is vacuous)"
    )
    assert cert1.certificate_content_hash == cert2.certificate_content_hash, (
        "certificate_hash must be deterministic; the hash function may "
        "have started accidentally including timestamp."
    )


def test_certificate_content_hash_sensitive_to_composition_change():
    """Different compositions produce different hashes."""
    comp_a = _build_cycle(2, 4)
    comp_b = _build_cycle(3, 5)
    cert_a = certify(comp_a)
    cert_b = certify(comp_b)
    assert cert_a.certificate_content_hash != cert_b.certificate_content_hash


def test_certificate_content_hash_excludes_display_field():
    """Sprint 14 refinement: UI rewording in `display` must NOT change
    the certificate_content_hash. Otherwise parent-cert hashes would be
    too brittle for witness bundles."""
    from bulla.certificate import _compute_certificate_content_hash
    comp = _build_cycle(2, 4)
    cert = certify(comp)
    original_hash = cert.certificate_content_hash
    # Forge a new cert with mutated display strings; the canonical preimage
    # used for hash computation must skip the display block entirely.
    cert2 = CompositionCertificate(
        certificate_schema_version=cert.certificate_schema_version,
        subject=cert.subject, method=cert.method, regime=cert.regime,
        diagnostic=cert.diagnostic, claims=cert.claims, scope=cert.scope,
        parent_certificate_hashes=cert.parent_certificate_hashes,
        issuer=cert.issuer, signature=cert.signature, supersedes=cert.supersedes,
        violations=cert.violations,
        display={
            "fee_interpretation": "TOTALLY DIFFERENT WORDING",
            "repair_semantics": "SOME OTHER UI COPY EDIT",
        },
        timestamp=cert.timestamp, bulla_version=cert.bulla_version,
        certificate_content_hash="",
        attestation_hash=cert.attestation_hash,
        receipt_hash=cert.receipt_hash,
    )
    new_hash = _compute_certificate_content_hash(cert2)
    assert new_hash == original_hash, (
        "certificate_content_hash must be invariant under display rewording"
    )


def test_certificate_content_hash_excludes_attestation_and_receipt_hashes():
    """The reserved slots `attestation_hash` and `receipt_hash` must be
    excluded from the content-hash preimage so future signing/operational
    receipts can populate those fields without invalidating parentage."""
    from bulla.certificate import _compute_certificate_content_hash
    comp = _build_cycle(2, 4)
    cert = certify(comp)
    original_hash = cert.certificate_content_hash
    cert2 = CompositionCertificate(
        certificate_schema_version=cert.certificate_schema_version,
        subject=cert.subject, method=cert.method, regime=cert.regime,
        diagnostic=cert.diagnostic, claims=cert.claims, scope=cert.scope,
        parent_certificate_hashes=cert.parent_certificate_hashes,
        issuer=cert.issuer, signature=cert.signature, supersedes=cert.supersedes,
        violations=cert.violations, display=cert.display,
        timestamp=cert.timestamp, bulla_version=cert.bulla_version,
        certificate_content_hash="",
        attestation_hash="future_attestation_hash_set_later",
        receipt_hash="future_receipt_hash_set_later",
    )
    new_hash = _compute_certificate_content_hash(cert2)
    assert new_hash == original_hash, (
        "certificate_content_hash must be invariant under attestation_hash/receipt_hash population"
    )


def test_certificate_content_hash_excludes_signature_field():
    """Modifying signature post-hoc must not change the canonical hash
    (verified by directly mutating a dict and recomputing)."""
    from bulla.certificate import _certificate_dict_for_content_hash, _compute_certificate_content_hash
    comp = _build_cycle(2, 4)
    cert = certify(comp)
    original_hash = cert.certificate_content_hash
    # Forge a new cert with a non-null signature; the canonical preimage
    # used for hash computation must skip the signature field.
    cert2 = CompositionCertificate(
        certificate_schema_version=cert.certificate_schema_version,
        subject=cert.subject, method=cert.method, regime=cert.regime,
        diagnostic=cert.diagnostic, claims=cert.claims, scope=cert.scope,
        parent_certificate_hashes=cert.parent_certificate_hashes,
        issuer=cert.issuer,
        signature="forged_signature",  # changed!
        supersedes=cert.supersedes,
        violations=cert.violations, display=cert.display,
        timestamp=cert.timestamp, bulla_version=cert.bulla_version,
        certificate_content_hash="",
        attestation_hash=cert.attestation_hash,
        receipt_hash=cert.receipt_hash,
    )
    new_hash = _compute_certificate_content_hash(cert2)
    assert new_hash == original_hash, (
        "certificate_content_hash must be invariant under signature changes"
    )


# ---- 3. Claim coverage across regime lattice ----

@pytest.mark.parametrize("comp_factory,expected", [
    # exact-conservative + fee=0 (cycle on 0 tools = empty)
    (lambda: Composition(name="empty", tools=(), edges=()), {
        "schema_shape_valid": "certified",
        "fee_is_nonnegative": "certified",
        "fee_is_interpretable": "certified",
        "exact_disclosure_equivalence": "certified",
        "repair_basis_status": "not_applicable",
        "subject_bound": "certified",
    }),
    # exact-conservative + fee>0 (cycle family)
    (lambda: _build_cycle(2, 4), {
        "schema_shape_valid": "certified",
        "fee_is_nonnegative": "certified",
        "fee_is_interpretable": "certified",
        "exact_disclosure_equivalence": "certified",
        "repair_basis_status": "certified",
        "subject_bound": "certified",
    }),
    # well-formed but NOT exact-conservative + fee>0
    (lambda: _build_wf_pos_not_exact(), {
        "schema_shape_valid": "certified",
        "fee_is_nonnegative": "certified",
        "fee_is_interpretable": "certified",
        "exact_disclosure_equivalence": "not_certified",
        "repair_basis_status": "candidate",
        "subject_bound": "certified",
    }),
    # ill-formed (non-projective)
    (lambda: _build_ill_formed(), {
        "schema_shape_valid": "not_certified",
        "fee_is_nonnegative": "not_certified",
        "fee_is_interpretable": "not_certified",
        "exact_disclosure_equivalence": "not_certified",
        "repair_basis_status": "not_certified",
        "subject_bound": "not_certified",
    }),
])
def test_claims_coverage_per_regime(comp_factory, expected):
    """Every claim's status enum is reachable via the right regime fixture."""
    comp = comp_factory()
    cert = certify(comp)
    for claim_name, expected_status in expected.items():
        actual = cert.claims[claim_name]
        assert actual.status == expected_status, (
            f"comp={comp.name}, claim={claim_name}: "
            f"expected status='{expected_status}', got '{actual.status}'. "
            f"value={actual.value}, licensed_by={actual.licensed_by}"
        )


def test_repair_basis_status_candidate_not_licensed_block():
    """When repair_basis_status is 'candidate', the not_licensed list
    must include 'exact_disclosure_equivalence' to make the gap explicit."""
    cert = certify(_build_wf_pos_not_exact())
    repair = cert.claims["repair_basis_status"]
    assert repair.status == "candidate"
    assert "exact_disclosure_equivalence" in repair.not_licensed


def test_repair_basis_status_certified_includes_both_predicates():
    """When repair_basis_status is 'certified', licensed_by must list
    BOTH is_well_formed_for_fee and is_exact_regime_conservative."""
    cert = certify(_build_cycle(2, 4))
    repair = cert.claims["repair_basis_status"]
    assert repair.status == "certified"
    assert "is_well_formed_for_fee" in repair.licensed_by
    assert "is_exact_regime_conservative" in repair.licensed_by


# ---- 4. display block back-compat (v0 free-text labels preserved) ----

def test_display_labels_match_v0_strings():
    """display.fee_interpretation and display.repair_semantics MUST byte-
    match what the Sprint 13 v0 lookup tables produced. UI consumers
    that read display strings stay backward-compatible."""
    cases = [
        # (composition factory, expected fee_interpretation prefix,
        #  expected repair_semantics prefix)
        (lambda: Composition(name="empty", tools=(), edges=()),
         "no obstruction (exact-regime certified)",
         "no repair needed; coherence_fee = 0"),
        (lambda: _build_cycle(2, 4),
         "true non-negative fee (theorem regime)",
         "repairable; matroid basis = minimum disclosure set"),
        (lambda: _build_ill_formed(),
         "signed obstruction imbalance (NOT a fee — see regime warning)",
         "fix schema definition"),
        (lambda: _build_wf_pos_not_exact(),
         "true non-negative fee (well-formed regime)",
         "repairable as a non-negative fee"),
    ]
    for factory, fee_prefix, repair_prefix in cases:
        comp = factory()
        cert = certify(comp)
        d = to_dict(cert)
        assert d["display"]["fee_interpretation"].startswith(fee_prefix), (
            f"comp={comp.name}: display.fee_interpretation drifted from v0"
        )
        assert d["display"]["repair_semantics"].startswith(repair_prefix), (
            f"comp={comp.name}: display.repair_semantics drifted from v0"
        )
        # Direct comparison with the lookup-table functions:
        report = classify(comp)
        assert d["display"]["fee_interpretation"] == _fee_interpretation(report)
        assert d["display"]["repair_semantics"] == _repair_semantics(report)


# ---- 5. method block versioning ----

def test_method_block_versioned():
    """Every method entry contains '@<version>' so future replay code
    can detect producer-version drift."""
    cert = certify(_build_cycle(2, 4))
    d = to_dict(cert)
    method = d["method"]
    expected_keys = {
        "regime_classifier", "diagnostic",
        "witness_geometry", "cross_server_decomposition",
    }
    assert set(method.keys()) == expected_keys
    for key, val in method.items():
        assert "@" in val, f"method.{key} missing @<version>: {val!r}"


# ---- 6. JSON round-trip ----

def test_to_json_round_trip_stable():
    """to_json → json.loads → comparable dict (structurally identical
    modulo timestamp + bulla_version + certificate_hash, which depend
    on the timestamp ordering)."""
    comp = _build_cycle(2, 4)
    cert = certify(comp)
    s = to_json(cert)
    loaded = json.loads(s)
    direct = to_dict(cert)
    # Both serializations are at the same instant; should match exactly.
    assert loaded == direct


# ---- 7. Multi-server cross-server decomposition ----

def test_cross_server_decomposition_populated_for_multi_server():
    """Compositions with `xxx__tool` prefix convention get a populated
    cross_server_decomposition block in `diagnostic`."""
    sys.path.insert(0, str(REPO / "bulla" / "calibration" / "scripts"))
    # Inline the helper to avoid sprint4 script's import side effects
    manifests_dir = REPO / "bulla" / "calibration" / "data" / "registry" / "manifests"
    if not (manifests_dir / "filesystem.json").exists() or not (manifests_dir / "github.json").exists():
        pytest.skip("filesystem or github manifest missing")
    from bulla.cli import _seed_set_load_registry_manifests, _seed_set_build_pair
    manifests = _seed_set_load_registry_manifests(manifests_dir)
    comp = _seed_set_build_pair("filesystem", manifests["filesystem"],
                                "github", manifests["github"])
    cert = certify(comp, source_path="filesystem+github")
    d = to_dict(cert)
    decomp = d["diagnostic"]["cross_server_decomposition"]
    assert decomp is not None
    assert decomp["n_servers"] == 2
    assert "filesystem" in decomp["servers"]
    assert "github" in decomp["servers"]
    assert sum(decomp["local_fees"]) + decomp["boundary_fee"] == decomp["total_fee"]


def test_cross_server_decomposition_omitted_for_single_server():
    """Single-server (no `__` prefix) → cross_server_decomposition is None."""
    cert = certify(_build_cycle(2, 4))
    d = to_dict(cert)
    assert d["diagnostic"]["cross_server_decomposition"] is None


# ---- 8. Witness-geometry on/off ----

def test_witness_geometry_omitted_when_fee_zero():
    """fee==0 ⇒ witness_geometry is None (no leverage scores produced)."""
    cert = certify(Composition(name="empty", tools=(), edges=()))
    d = to_dict(cert)
    assert d["diagnostic"]["witness_geometry"] is None


def test_witness_geometry_populated_when_fee_positive():
    """fee>0 + include_witness_geometry=True ⇒ witness_geometry block populated."""
    cert = certify(_build_cycle(3, 4))  # fee=9
    d = to_dict(cert)
    wg = d["diagnostic"]["witness_geometry"]
    assert wg is not None
    assert "leverage" in wg
    assert "disclosure_set" in wg


def test_include_witness_geometry_false_disables_block():
    """include_witness_geometry=False ⇒ witness_geometry is None even at fee>0."""
    cert = certify(_build_cycle(3, 4), include_witness_geometry=False)
    d = to_dict(cert)
    assert d["diagnostic"]["witness_geometry"] is None


# ---- 9. Violations from Sprint 10 validate_regime ----

def test_violations_populated_for_ill_formed():
    """Schema-shape violations from Sprint 10 surface in cert.violations."""
    cert = certify(_build_ill_formed())
    d = to_dict(cert)
    assert len(d["violations"]) == 2
    for v in d["violations"]:
        assert v["kind"] == "projective_observables"
        assert "secret" in v["fields"]


def test_violations_empty_for_well_formed():
    cert = certify(_build_cycle(2, 4))
    d = to_dict(cert)
    assert d["violations"] == []


# ---- 10. Sprint 11/12 epistemic discipline preserved in claims ----

def test_well_formed_only_claim_does_not_grant_exact_disclosure():
    """Sprint 11/12 discipline: well-formed-only must NOT certify
    exact_disclosure_equivalence. The claims block enforces this."""
    cert = certify(_build_wf_pos_not_exact())
    assert cert.claims["fee_is_nonnegative"].status == "certified"
    assert cert.claims["exact_disclosure_equivalence"].status == "not_certified"


# ---- 11. Reserved slots are present and null/empty ----

def test_reserved_slots_present_and_null_or_empty():
    """All reserved slots are present in the JSON output with their
    reserved values: parent_certificate_hashes, issuer, signature,
    supersedes (Sprint 14 plan), and attestation_hash, receipt_hash
    (Sprint 14 refinement)."""
    cert = certify(_build_cycle(2, 4))
    d = to_dict(cert)
    assert d["parent_certificate_hashes"] == []
    assert d["issuer"] == {"type": "local", "id": None}
    assert d["signature"] is None
    assert d["supersedes"] is None
    assert d["attestation_hash"] is None
    assert d["receipt_hash"] is None


# ---- 12. Subject block ----

def test_subject_block_has_composition_sha256():
    cert = certify(_build_cycle(2, 4))
    d = to_dict(cert)
    subject = d["subject"]
    assert "composition_sha256" in subject
    assert subject["composition_sha256"]
    assert subject["pack_stack_sha256"] is None  # reserved
    assert subject["manifest_hashes"] == []      # reserved


def test_subject_source_path_passes_through():
    cert = certify(_build_cycle(2, 4), source_path="custom/path.yaml")
    d = to_dict(cert)
    assert d["subject"]["source_path"] == "custom/path.yaml"


# ---- 13. Scope canonicalization ----

def test_scope_tools_sorted():
    """Scope tools are sorted canonically for future parent comparison."""
    # Build with intentionally non-sorted tool names
    tools = (
        ToolSpec(name="zebra", internal_state=("f",), observable_schema=()),
        ToolSpec(name="alpha", internal_state=("f",), observable_schema=()),
        ToolSpec(name="middle", internal_state=("f",), observable_schema=()),
    )
    edge = Edge(
        from_tool="zebra", to_tool="alpha",
        dimensions=(SemanticDimension(name="m", from_field="f", to_field="f"),),
    )
    comp = Composition(name="unordered", tools=tools, edges=(edge,))
    cert = certify(comp)
    d = to_dict(cert)
    assert d["scope"]["tools"] == ["alpha", "middle", "zebra"]


# ---- 14. _detect_servers helper ----

def test_detect_servers_no_prefix():
    assert _detect_servers(_build_cycle(2, 4)) == []


def test_detect_servers_with_prefix():
    t1 = ToolSpec(name="github__create", internal_state=("a",), observable_schema=("a",))
    t2 = ToolSpec(name="notion__page", internal_state=("a",), observable_schema=("a",))
    comp = Composition(name="multi", tools=(t1, t2), edges=())
    servers = _detect_servers(comp)
    assert "github" in servers
    assert "notion" in servers


# ---- 15. parent_certificate_hashes kwarg (Sprint 15 extension) ----

def test_parent_certificate_hashes_kwarg_populates_slot():
    """Sprint 15: certify() accepts a parent_certificate_hashes kwarg
    that populates the reserved slot. The slot defaults to () (no
    parents) — Sprint 14 default behavior preserved."""
    comp = _build_cycle(2, 4)
    # Default: empty parents
    cert_default = certify(comp)
    assert cert_default.parent_certificate_hashes == ()

    # With parents: populated
    fake_parents = ("sha256:fake1", "sha256:fake2")
    cert_with_parents = certify(comp, parent_certificate_hashes=fake_parents)
    assert cert_with_parents.parent_certificate_hashes == fake_parents


def test_parent_certificate_hashes_changes_content_hash():
    """Sprint 15: parent_certificate_hashes is in the content hash
    preimage (per discipline sentence: 'changes under ... parent ...
    changes'). Adding parents changes the certificate_content_hash."""
    comp = _build_cycle(2, 4)
    cert_no_parents = certify(comp)
    cert_with_parents = certify(
        comp, parent_certificate_hashes=("sha256:fake_parent",)
    )
    assert (
        cert_no_parents.certificate_content_hash
        != cert_with_parents.certificate_content_hash
    )


# ---- 16. _build_claims is pure (no side effects) ----

def test_build_claims_pure_function():
    """Same regime → same claim structure across calls."""
    report = classify(_build_cycle(2, 4))
    c1 = _build_claims(report, has_subject_hash=True)
    c2 = _build_claims(report, has_subject_hash=True)
    # Compare via to_dict to handle frozen dataclass equality
    from bulla.certificate import _claim_to_dict
    assert {k: _claim_to_dict(v) for k, v in c1.items()} == {
        k: _claim_to_dict(v) for k, v in c2.items()
    }
