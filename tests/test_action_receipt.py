"""ActionReceipt v0.1 — construction, hashing, and the adversarial forgery suite.

The forgery tests are FIRST-CLASS, not an afterthought: for anything claimed
unforgeable, the adversary-controls-the-served-bytes test IS the property. Each
one hands the verifier a mutated receipt and asserts it fails closed at the right
depth.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bulla.action_receipt import (
    ActionReceipt,
    ActionReceiptError,
    build_action_receipt,
    build_release_receipt,
    build_tool_call_receipt,
    verify_receipt,
)
from bulla.envelope import (
    Authority,
    Bounds,
    EnvelopeError,
    Forum,
    Recourse,
    RecourseEnvelope,
    Remedy,
    ladder_ordered,
)

_HAS_NACL = True
try:  # signing tests need bulla[identity]
    from bulla.identity import LocalEd25519Signer
except Exception:  # pragma: no cover
    _HAS_NACL = False


def _envelope(scope: str = "s", *, escalate: bool = True) -> RecourseEnvelope:
    remedies = [Remedy(rung="recompute", verifier="bulla receipt verify", anchor="the receipt")]
    if escalate:
        remedies.append(Remedy(rung="escalate", verifier="maintainer review", anchor="did:key:zP"))
    return RecourseEnvelope(
        authority=Authority(principal="did:key:zP", policy="policy://p@sha256:aa"),
        bounds=Bounds(scope=scope, rollback_window="P7D"),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(log_endpoint="https://log", trusted_root_ref="ots:root"),
            remedies=tuple(remedies),
        ),
        retention_class="operational",
        disclosure_class="party",
    )


def _receipt(**over) -> ActionReceipt:
    kw = dict(
        action={"type": "github.create_file", "subject": {"repo": "acme/site", "path": "docs/x.md"}},
        diagnostic_ref={"status": "reference", "ref": "sha256:witness"},
        envelope=_envelope(),
        anchor_ref={"kind": "git", "ref": "commit:abc"},
        evidence_refs=({"name": "diff", "hash": "sha256:1111", "grounding": "self_asserted"},),
        timestamp="2026-07-04T00:00:00+00:00",
        producer={"bulla_version": "0.41.0"},
    )
    kw.update(over)
    return build_action_receipt(**kw)


# ── construction + hashing ───────────────────────────────────────────────────

def test_roundtrip_hashes_stable():
    r = _receipt()
    d = r.to_dict()
    assert ActionReceipt.from_dict(d).hashes() == r.hashes()


def test_v02_rejects_v03_authorization_member_even_when_null():
    d = _receipt().to_dict()
    d["authorization"] = None
    with pytest.raises(ActionReceiptError, match="only valid in schema_version '0.3'"):
        ActionReceipt.from_dict(d)


def test_missing_stored_hash_fails_closed():
    d = _receipt().to_dict()
    del d["hashes"]["content"]
    v = verify_receipt(d)
    assert not v.ok
    assert v.checks["hash_content"] is False
    assert any("content hash missing" in reason for reason in v.reasons)


def test_four_hashes_present_and_distinct():
    h = _receipt().hashes()
    assert set(h) == {"content", "event", "attestation", "log_leaf"}
    assert len(set(h.values())) == 4  # each answers a different question


def test_content_hash_is_envelope_free_and_time_free():
    """The recomputable core must not move when the appeal path or the clock
    changes — that is what makes 'recompute the verdict' machine-independent."""
    base = _receipt()
    other_env = _receipt(envelope=_envelope(scope="DIFFERENT SCOPE"))
    later = _receipt(timestamp="2027-01-01T00:00:00+00:00")
    assert base.content_hash == other_env.content_hash == later.content_hash
    # ...but the attestation (which commits the envelope) and the event (which
    # commits the time) DO move:
    assert base.attestation_hash != other_env.attestation_hash
    assert base.event_hash != later.event_hash


def test_mandate_remedy_retention_are_named_views():
    d = _receipt().to_dict()
    assert set(d["mandate"]) == {"authority", "bounds"}
    assert set(d["remedy"]) == {"challenge_window", "forum", "remedies"}
    assert d["retention"] == {"record": "operational", "disclosure": "party"}


# ── schema invariants ────────────────────────────────────────────────────────

def test_diagnostic_ref_never_bare_null():
    with pytest.raises(ActionReceiptError):
        _receipt(diagnostic_ref={})  # missing status
    with pytest.raises(ActionReceiptError):
        _receipt(diagnostic_ref={"status": "bogus"})
    with pytest.raises(ActionReceiptError):
        _receipt(diagnostic_ref={"status": "reference"})  # reference needs a ref


def test_stake_is_reserved():
    r = _receipt()
    assert r.to_dict()["stake"] is None
    with pytest.raises(ActionReceiptError):
        ActionReceipt(action=r.action, diagnostic_ref=r.diagnostic_ref, envelope=r.envelope,
                      stake={"bond_id": "x"})


def test_evidence_refs_well_formed():
    with pytest.raises(ActionReceiptError):
        _receipt(evidence_refs=({"name": "x"},))  # no hash


# ── verification depth ───────────────────────────────────────────────────────

def test_unsigned_verifies_to_digest():
    v = verify_receipt(_receipt().to_dict())
    assert v.ok and v.verified_to == "digest"


@pytest.mark.skipif(not _HAS_NACL, reason="needs bulla[identity]")
def test_signed_verifies_to_attestation():
    env = _envelope()
    r0 = _receipt(envelope=env)
    signer = LocalEd25519Signer.generate()
    r = _receipt(envelope=env, signature=signer.sign(r0.content_hash))
    v = verify_receipt(r.to_dict())
    assert v.ok and v.verified_to == "attestation" and v.checks["signature"]


# ── the adversarial forgery suite (the property) ─────────────────────────────

def test_forge_tamper_evidence_fails_digest():
    d = _receipt().to_dict()
    d["evidence_refs"][0]["hash"] = "sha256:9999"  # attacker edits served bytes
    v = verify_receipt(d)
    assert not v.ok and not v.checks["hash_content"]


def test_forge_swap_verdict_fails():
    """Swapping diagnostic_ref to a different verdict shifts content_hash — you
    cannot keep the verdict and change what it points at."""
    d = _receipt().to_dict()
    d["diagnostic_ref"] = {"status": "reference", "ref": "sha256:a-DIFFERENT-verdict"}
    assert not verify_receipt(d).ok


@pytest.mark.skipif(not _HAS_NACL, reason="needs bulla[identity]")
def test_forge_recompute_hashes_stale_signature_fails_at_attestation():
    """The strongest forgery: the adversary mutates content AND recomputes all
    four hashes to sail past the digest rung — but cannot re-sign. Caught at
    attestation."""
    env = _envelope()
    r0 = _receipt(envelope=env)
    signer = LocalEd25519Signer.generate()
    d = _receipt(envelope=env, signature=signer.sign(r0.content_hash)).to_dict()
    d["action"]["subject"]["path"] = "docs/EVIL.md"
    d["hashes"] = ActionReceipt.from_dict(d).hashes()  # recompute — passes digest
    v = verify_receipt(d)
    assert not v.ok
    assert v.verified_to == "digest"           # got past digest...
    assert v.checks["signature"] is False      # ...died at attestation


# ── authority binding: the envelope-swap attack and its fix ──────────────────
#
# content_hash is envelope-free by design, so a signature over content says
# nothing about the mandate/remedy. authorization_hash = H(content, envelope)
# closes that gap: the issuer signs it to vouch for THIS envelope.

def test_authorization_hash_binds_content_and_envelope():
    r = _receipt()
    from bulla.action_receipt import _canon_hash  # type: ignore
    assert r.authorization_hash == _canon_hash(
        {"content_hash": r.content_hash, "envelope_hash": r.envelope_hash}
    )
    # invariant to the proofs themselves (so it is stable to sign against)
    r2 = _receipt(signature={"type": "x", "proofValue": "y"})
    assert r2.authorization_hash == r.authorization_hash


def test_authorization_hash_moves_with_the_envelope():
    """Swapping any envelope field moves authorization_hash — the whole point."""
    r = _receipt()
    swapped = _receipt(envelope=_envelope(scope="repo:victim/* path:*"))
    assert swapped.content_hash == r.content_hash          # content unmoved...
    assert swapped.authorization_hash != r.authorization_hash  # ...authority moved


def test_content_only_signature_leaves_authority_unauthenticated():
    """The honest split: signing content alone verifies the CLAIM, not the
    mandate. The verifier says so instead of implying the envelope is vouched."""
    if not _HAS_NACL:
        pytest.skip("needs bulla[identity]")
    env = _envelope()
    r0 = _receipt(envelope=env)
    signer = LocalEd25519Signer.generate()
    r = _receipt(envelope=env, signature=signer.sign(r0.content_hash))
    v = verify_receipt(r.to_dict(), public_key=signer.public_key)
    assert v.ok and v.verified_to == "attestation"
    assert v.checks["signature"] is True
    assert v.authority_authentic == "unauthenticated"


@pytest.mark.skipif(not _HAS_NACL, reason="needs bulla[identity]")
def test_full_signing_verifies_both_content_and_authority():
    from bulla.action_receipt import sign_action_receipt
    signer = LocalEd25519Signer.generate()
    r = sign_action_receipt(_receipt(), signer)
    assert r.schema_version == "0.3"
    v = verify_receipt(r.to_dict(), public_key=signer.public_key)
    assert v.ok and v.verified_to == "attestation"
    assert v.checks["signature"] is True
    assert v.checks["authorization"] is True
    assert v.authority_authentic == "verified"


@pytest.mark.skipif(not _HAS_NACL, reason="needs bulla[identity]")
def test_authorization_resigned_by_different_key_is_rejected():
    """The strongest signer-substitution attack under v0.3 domain separation:
    keep the honest content proof, swap the envelope, and attach a *cryptographically
    valid* authorization proof made by the attacker's own key over the swapped
    envelope (a proper domain-separated proof, so it clears verify_proof_domain).
    Both proofs verify individually; the same-signer check rejects the pair."""
    from bulla.action_receipt import sign_action_receipt

    honest = LocalEd25519Signer.generate()
    attacker = LocalEd25519Signer.generate()
    d = sign_action_receipt(_receipt(), honest).to_dict()
    d["mandate"]["bounds"]["scope"] = "repo:victim/* path:*"
    forged = ActionReceipt.from_dict(d)
    d["authorization"] = attacker.sign_domain("authorization", forged.authorization_hash)
    d["hashes"] = ActionReceipt.from_dict(d).hashes()

    v = verify_receipt(d)
    assert not v.ok
    assert v.checks["signature"] is True             # honest content proof still valid
    assert v.checks["authorization"] is True         # attacker's proof is cryptographically valid…
    assert v.checks["authorization_same_signer"] is False  # …but it is not the content signer
    assert v.authority_authentic == "forged"


@pytest.mark.skipif(not _HAS_NACL, reason="needs bulla[identity]")
def test_authorization_without_content_signature_fails_closed():
    from bulla.action_receipt import sign_action_receipt

    signer = LocalEd25519Signer.generate()
    d = sign_action_receipt(_receipt(), signer).to_dict()
    d["signature"] = None
    d["hashes"] = ActionReceipt.from_dict(d).hashes()
    v = verify_receipt(d)
    assert not v.ok
    assert v.checks["proof_pair"] is False


@pytest.mark.skipif(not _HAS_NACL, reason="needs bulla[identity]")
def test_v03_stripped_authorization_is_a_failed_downgrade():
    from bulla.action_receipt import sign_action_receipt

    signer = LocalEd25519Signer.generate()
    d = sign_action_receipt(_receipt(), signer).to_dict()
    d["authorization"] = None
    d["hashes"] = ActionReceipt.from_dict(d).hashes()
    v = verify_receipt(d)
    assert not v.ok
    assert v.checks["authorization_required"] is False
    assert v.authority_authentic == "unauthenticated"


@pytest.mark.skipif(not _HAS_NACL, reason="needs bulla[identity]")
def test_forge_swap_envelope_recompute_hashes_fails_authorization():
    """THE strongest authority forgery: keep the issuer's valid content
    signature, swap the mandate/remedy envelope, and recompute EVERY unsigned
    downstream hash (attestation, log_leaf) so the receipt sails past digest.

    Before authorization binding this verified to `attestation` with forged
    authority. Now the authorization proof — over H(content, envelope) — no
    longer binds the swapped envelope, and the receipt fails as `forged`."""
    from bulla.action_receipt import sign_action_receipt
    signer = LocalEd25519Signer.generate()
    r = sign_action_receipt(_receipt(), signer)
    d = r.to_dict()

    # attacker rewrites the mandate to a different principal and a wider scope
    d["mandate"]["authority"]["principal"] = "did:key:zATTACKER"
    d["mandate"]["bounds"]["scope"] = "repo:victim/* path:*"
    d["hashes"] = ActionReceipt.from_dict(d).hashes()  # recompute — passes digest

    v = verify_receipt(d, public_key=signer.public_key)
    assert not v.ok
    assert v.checks["signature"] is True          # content signature still valid...
    assert v.checks["authorization"] is False     # ...but authority does not bind
    assert v.authority_authentic == "forged"


@pytest.mark.skipif(not _HAS_NACL, reason="needs bulla[identity]")
def test_swap_envelope_without_authorization_is_flagged_not_vouched():
    """A content-only receipt whose envelope is swapped is NOT silently accepted:
    the verifier reports `unauthenticated`, never presenting the swapped mandate
    as issuer-vouched. (The strong FAIL requires an authorization proof to bind
    against — see the test above.)"""
    env = _envelope()
    r0 = _receipt(envelope=env)
    signer = LocalEd25519Signer.generate()
    d = _receipt(envelope=env, signature=signer.sign(r0.content_hash)).to_dict()
    d["mandate"]["authority"]["principal"] = "did:key:zATTACKER"
    d["hashes"] = ActionReceipt.from_dict(d).hashes()
    v = verify_receipt(d, public_key=signer.public_key)
    assert v.authority_authentic == "unauthenticated"  # content signed, authority is not


def test_forge_blank_remedy_anchor_refused_by_modality_law():
    """A remedy that names no stateful anchor is process theater — the envelope
    refuses to reconstruct it, so verify fails at the envelope rung."""
    d = _receipt().to_dict()
    d["remedy"]["remedies"][0]["anchor"] = ""
    v = verify_receipt(d)
    assert not v.ok and v.verified_to == "none" and not v.checks["envelope_valid"]


def test_forge_escalate_without_authority_refused():
    d = _receipt().to_dict()
    d["mandate"].pop("authority")  # escalate remedy now has no delegation chain
    assert not verify_receipt(d).ok


def test_ladder_ordering_predicate():
    good = (Remedy("recompute", "v", "a"), Remedy("revert", "v", "a"))
    bad = (Remedy("revert", "v", "a"), Remedy("recompute", "v", "a"))
    assert ladder_ordered(good) and not ladder_ordered(bad)


# ── the two golden instances ─────────────────────────────────────────────────

def test_release_receipt_shape():
    r = build_release_receipt(
        package="bulla", version="0.41.0", git_commit="abc", git_tag="v0.41.0",
        wheel_sha256="sha256:w", sdist_sha256="sha256:s",
        diagnostic_ref={"status": "reference", "ref": "sha256:gate"},
        envelope=_envelope("pypi:bulla version:0.41.0"),
        root_of_trust={"scheme": "sigstore-pep740", "rekor_log_index": 1, "attestation_bundle_sha256": "sha256:b"},
        timestamp="2026-07-04T00:00:00+00:00",
    )
    d = r.to_dict()
    assert d["action"]["type"] == "package.release"
    assert d["anchor_ref"]["root_of_trust"]["scheme"] == "sigstore-pep740"
    assert verify_receipt(d).ok


def test_tool_call_receipt_shape():
    r = build_tool_call_receipt(
        tool="github.create_file",
        call_subject={"repo": "acme/site", "path": "docs/x.md"},
        diagnostic_ref={"status": "reference", "ref": "sha256:witness"},
        envelope=_envelope(), result_hash="sha256:res",
    )
    d = r.to_dict()
    assert d["action"]["type"] == "github.create_file"
    assert verify_receipt(d).ok


# ── conventions: predicate invention, auditable (spec v0.2 §5) ──────────────

EXEC_CONVENTION = {
    "name": "amount-in-usd-cents",
    "scope": "seam:caller->payments.charge",
    "kind": "executable",
    "definition": {
        "form": "jsonschema+quantum/1",
        "schema": {
            "type": "object",
            "required": ["amount", "currency"],
            "properties": {
                "amount": {"type": "integer", "minimum": 0},
                "currency": {"const": "USD"},
            },
        },
        "quantum": {"amount": {"unit": "USD_cents", "multipleOf": 1}},
    },
}

SEMANTIC_CONVENTION = {
    "name": "gdpr-erasure-honored",
    "scope": "seam:caller->crm.delete_record",
    "kind": "semantic",
    "definition": "Erasure is complete when no primary or derived record remains.",
    "forum": {"log_endpoint": "https://log.example", "trusted_root_ref": "ots:root"},
}


def _charge_receipt(subject: dict, conventions=(EXEC_CONVENTION,)) -> ActionReceipt:
    return build_tool_call_receipt(
        tool="payments.charge",
        call_subject=subject,
        diagnostic_ref={"status": "reference", "ref": "sha256:witness"},
        envelope=_envelope(),
        conventions=conventions,
        timestamp="2026-07-13T00:00:00+00:00",
    )


def test_convention_conforming_act_surfaced():
    d = _charge_receipt({"amount": 1250, "currency": "USD"}).to_dict()
    # the builder computed the pin
    assert d["conventions"][0]["definition_hash"].startswith("sha256:")
    v = verify_receipt(d)
    assert v.ok
    assert v.conventions == {"amount-in-usd-cents": "conforms"}


def test_convention_violating_act_surfaced_not_gating():
    """A dollars-float act against a cents-integer convention: the record's
    integrity holds (ok), the act's non-conformance is surfaced."""
    d = _charge_receipt({"amount": 12.50, "currency": "USD"}).to_dict()
    v = verify_receipt(d)
    assert v.ok  # the record of a non-conforming act is still a valid record
    assert v.conventions == {"amount-in-usd-cents": "violates"}
    assert any("does not conform" in r for r in v.reasons)


def test_semantic_convention_pinned_and_forum_required():
    d = _charge_receipt({"amount": 1, "currency": "USD"},
                        conventions=(SEMANTIC_CONVENTION,)).to_dict()
    v = verify_receipt(d)
    assert v.ok and v.conventions == {"gdpr-erasure-honored": "pinned"}
    bad = dict(SEMANTIC_CONVENTION)
    bad.pop("forum")
    with pytest.raises(ActionReceiptError, match="forum"):
        _charge_receipt({"amount": 1, "currency": "USD"}, conventions=(bad,))


def test_forge_mutate_convention_fails_digest():
    """THE forgery test: a coined rule lives inside the content hash, so an
    adversary cannot relax the convention after the fact."""
    from bulla.action_receipt import convention_definition_hash
    d = _charge_receipt({"amount": 1250, "currency": "USD"}).to_dict()
    d["conventions"][0]["definition"]["quantum"]["amount"]["multipleOf"] = 100
    # the adversary keeps the entry internally consistent (pin recomputed)...
    d["conventions"][0]["definition_hash"] = convention_definition_hash(
        d["conventions"][0]["definition"]
    )
    # ...but the content hash was minted over the ORIGINAL convention.
    v = verify_receipt(d)
    assert not v.ok and not v.checks["hash_content"]


def test_forge_strip_conventions_fails_digest():
    d = _charge_receipt({"amount": 1250, "currency": "USD"}).to_dict()
    d["conventions"] = []
    v = verify_receipt(d)
    assert not v.ok and not v.checks["hash_content"]


def test_convention_definition_hash_mismatch_refused():
    c = dict(EXEC_CONVENTION)
    c["definition_hash"] = "sha256:" + "0" * 64
    with pytest.raises(ActionReceiptError, match="definition_hash"):
        _receipt(conventions=(c,))


def test_executable_convention_unknown_keyword_fails_closed():
    c = {
        "name": "x", "scope": "s", "kind": "executable",
        "definition": {
            "form": "jsonschema+quantum/1",
            "schema": {"type": "object", "properties": {"a": {"format": "uri"}}},
        },
    }
    with pytest.raises(ActionReceiptError, match="fail closed"):
        _receipt(conventions=(c,))


@pytest.mark.parametrize(
    "schema",
    [
        {"properties": []},
        {"properties": {}, "required": "amount"},
        {"properties": {}, "additionalProperties": "no"},
        {"properties": {"amount": {"type": "integer", "minimum": "zero"}}},
        {"properties": {"name": {"type": "string", "pattern": "["}}},
    ],
)
def test_executable_convention_malformed_containers_fail_closed(schema):
    c = {
        "name": "x", "scope": "s", "kind": "executable",
        "definition": {"form": "jsonschema+quantum/1", "schema": schema},
    }
    with pytest.raises(ActionReceiptError):
        _receipt(conventions=(c,))


def test_portable_executable_subset_rejects_float_regex_and_unsafe_integer():
    from bulla.executable_form import (
        ExecutableFormError,
        validate_portable_executable_definition,
    )

    for prop in (
        {"type": "number"},
        {"type": "string", "pattern": "^ok$"},
        {"type": "integer", "maximum": 2**53},
    ):
        definition = {
            "form": "jsonschema+quantum/1",
            "schema": {"type": "object", "properties": {"value": prop}},
        }
        with pytest.raises(ExecutableFormError):
            validate_portable_executable_definition(definition)

    validate_portable_executable_definition({
        "form": "jsonschema+quantum/1",
        "schema": {
            "type": "object",
            "properties": {"amount": {"type": "integer", "minimum": 0, "maximum": 10}},
        },
        "quantum": {"amount": {"unit": "usd_micros", "multipleOf": 1}},
    })


def test_effective_grounding_is_min_over_evidence():
    r = _receipt(evidence_refs=(
        {"name": "log", "hash": "sha256:aa", "grounding": "execution_verified"},
        {"name": "note", "hash": "sha256:bb", "grounding": "self_asserted"},
    ))
    v = verify_receipt(r.to_dict())
    assert v.effective_grounding == "self_asserted"
    assert any("attested testimony" in x for x in v.reasons)


def test_v02_requires_grounding_on_evidence():
    with pytest.raises(ActionReceiptError, match="grounding"):
        _receipt(evidence_refs=({"name": "diff", "hash": "sha256:11"},))


def test_v01_receipts_still_verify_without_grounding():
    """A served v0.1 receipt (schema_version 0.1, no grounding, no conventions)
    recomputes with its OWN schema_version — the golden vectors' guarantee."""
    vectors = Path(__file__).resolve().parents[1] / "spec" / "vectors"
    d = json.loads((vectors / "valid-release.json").read_text())
    assert d["schema_version"] == "0.1"
    v = verify_receipt(d)
    assert v.ok and v.effective_grounding is None


def test_witness_receipt_carries_conventions():
    from bulla.diagnostic import diagnose
    from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
    from bulla.witness import verify_receipt_integrity, witness
    comp = Composition(
        name="c", tools=(ToolSpec("a", ("x",), ("x",)), ToolSpec("b", ("x",), ("x",))),
        edges=(Edge("a", "b", (SemanticDimension("d", "x", "x"),)),),
    )
    diag = diagnose(comp)
    filled = dict(EXEC_CONVENTION)
    from bulla.action_receipt import convention_definition_hash
    filled["definition_hash"] = convention_definition_hash(filled["definition"])
    r = witness(diag, comp, conventions=(filled,))
    d = r.to_dict()
    assert d["conventions"][0]["name"] == "amount-in-usd-cents"
    assert verify_receipt_integrity(d)
    d["conventions"][0]["scope"] = "somewhere-else"   # inside the hash
    assert not verify_receipt_integrity(d)


# ── the committed retroactive corpus stays valid ─────────────────────────────

_RELEASES = Path(__file__).resolve().parents[1] / "releases"


@pytest.mark.skipif(not _RELEASES.is_dir(), reason="no releases corpus")
@pytest.mark.parametrize(
    "path",
    [
        path
        for path in sorted(_RELEASES.glob("*.json"))
        if json.loads(path.read_text()).get("kind") == "action_receipt"
    ],
    ids=lambda p: p.name,
)
def test_corpus_receipt_verifies(path):
    v = verify_receipt(json.loads(path.read_text()))
    assert v.ok and v.verified_to == "digest"  # unsigned reconstructions, honestly
