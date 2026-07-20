"""The spec is the contract: the stdlib-only independent verifier (which imports
no bulla) must reproduce bulla's verdicts on the golden vectors, and its
recomputed hashes must equal bulla's. If this passes, a second implementer can
verify a receipt from the published spec alone — the definition of a protocol.
"""

from __future__ import annotations

import importlib.util
import copy
import json
from pathlib import Path

import pytest

_SPEC = Path(__file__).resolve().parents[1] / "spec"
_VECTORS = _SPEC / "vectors"


def _load_independent():
    spec = importlib.util.spec_from_file_location("independent_check", _VECTORS / "independent_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pytestmark = pytest.mark.skipif(not (_VECTORS / "expected.json").exists(), reason="vectors not generated")


def test_independent_verifier_reproduces_bulla_verdicts():
    ind = _load_independent()
    expected = json.loads((_VECTORS / "expected.json").read_text())
    assert expected, "no vectors"
    for name, want in expected.items():
        doc = json.loads((_VECTORS / name).read_text())
        if want.get("kind") == "witness_receipt":
            got = ind.verify_witness_receipt(doc)
        else:
            got = ind.verify_action_receipt(doc)
        for key, val in want.items():
            # `identity` is the optional signature rung, checked below — the
            # top-level keys are the stdlib (digest) contract.
            if key in ("kind", "identity"):
                continue
            assert got.get(key) == val, (
                f"{name}: independent {key}={got.get(key)!r} != expected {val!r} ({got['reasons']})"
            )
        # optional ed25519 rung: when a library is present it must reproduce
        # bulla's content/authority verdicts; when absent it must skip honestly.
        if "identity" in want:
            idr = ind.verify_identity_rung(doc)
            if idr.get("available"):
                for key, val in want["identity"].items():
                    assert idr.get(key) == val, (
                        f"{name} identity: {key}={idr.get(key)!r} != expected {val!r}"
                    )
            else:
                assert idr == {"available": False}


def test_authorization_hash_matches_implementation():
    """The stdlib recomputation of envelope_hash / authorization_hash must equal
    bulla's — the authority-binding preimage cannot silently diverge."""
    ind = _load_independent()
    from bulla.action_receipt import ActionReceipt

    doc = json.loads((_VECTORS / "signed-authorized.json").read_text())
    r = ActionReceipt.from_dict(doc)
    c = ind.content_hash(doc)
    assert ind.envelope_hash(doc) == r.envelope_hash
    assert ind.authorization_hash(doc, c) == r.authorization_hash


def test_spec_hashing_equals_implementation():
    """The stdlib recomputation of content/attestation/log_leaf must equal
    bulla's — the spec and the code cannot silently diverge."""
    ind = _load_independent()
    from bulla.action_receipt import ActionReceipt

    for vector in ("valid-release.json", "convention-receipt.json"):
        doc = json.loads((_VECTORS / vector).read_text())
        r = ActionReceipt.from_dict(doc)
        c = ind.content_hash(doc)
        assert c == r.content_hash, vector
        assert ind.attestation_hash(doc, c) == r.attestation_hash, vector
        assert ind.log_leaf(r.attestation_hash) == r.log_leaf, vector
        assert ind.event_hash(c, doc["timestamp"]) == r.event_hash, vector


def test_receipts_validate_against_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((_SPEC / "action-receipt-v0.1.schema.json").read_text())
    doc = json.loads((_VECTORS / "valid-release.json").read_text())
    jsonschema.validate(doc, schema)  # raises on shape drift


def test_v03_signed_receipt_validates_against_draft_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((_SPEC / "action-receipt-v0.3.schema.json").read_text())
    doc = json.loads((_VECTORS / "signed-authorized.json").read_text())
    jsonschema.validate(doc, schema)


def test_v03_delegated_receipt_validates_against_draft_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((_SPEC / "action-receipt-v0.3.schema.json").read_text())
    doc = json.loads((_VECTORS / "delegated-receipt.json").read_text())
    jsonschema.validate(doc, schema)


def test_v03_schema_rejects_delegation_version_confusion_and_unknown_semantics():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((_SPEC / "action-receipt-v0.3.schema.json").read_text())
    honest = json.loads((_VECTORS / "delegated-receipt.json").read_text())

    opaque_under_v03 = copy.deepcopy(honest)
    opaque_under_v03["mandate"]["authority"]["delegation"] = ["grant:opaque"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(opaque_under_v03, schema)

    structured_under_v02 = copy.deepcopy(honest)
    structured_under_v02["mandate"]["deed_schema"] = "0.2"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(structured_under_v02, schema)

    unknown_member = copy.deepcopy(honest)
    unknown_member["mandate"]["authority"]["delegation"][0]["role"] = "admin"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(unknown_member, schema)


def _reauthorize_for_vector_leaf(ind, doc: dict) -> None:
    """Keep the envelope substitution adversarial but cryptographically honest.

    The fixed leaf seed is public vector material. Re-signing authorization removes
    stale-proof failure from these tests so each one exercises the delegation
    dimension named in its assertion.
    """
    from bulla.identity import LocalEd25519Signer

    leaf = LocalEd25519Signer(seed=bytes([3]) + bytes(31))
    digest = ind.authorization_hash(doc, ind.content_hash(doc))
    doc["authorization"] = leaf.sign_domain("authorization", digest)


def test_independent_checker_binds_grants_to_receipt_scope():
    pytest.importorskip("nacl")
    ind = _load_independent()
    doc = json.loads((_VECTORS / "delegated-receipt.json").read_text())
    doc["mandate"]["bounds"]["scope"] = "admin:*"
    _reauthorize_for_vector_leaf(ind, doc)

    got = ind.verify_identity_rung(doc)
    assert got["authority_authentic"] == "verified"
    assert got["chain_integrity"] == "verified"
    assert got["scope_binding"] == "mismatch"


def test_independent_checker_rejects_unsigned_grant_semantics():
    pytest.importorskip("nacl")
    ind = _load_independent()
    doc = json.loads((_VECTORS / "delegated-receipt.json").read_text())
    doc["mandate"]["authority"]["delegation"][0]["role"] = "admin"
    _reauthorize_for_vector_leaf(ind, doc)

    got = ind.verify_identity_rung(doc)
    assert got["authority_authentic"] == "verified"
    assert got["chain_integrity"] == "broken"


def test_independent_checker_fails_closed_on_malformed_policy_reference():
    pytest.importorskip("nacl")
    ind = _load_independent()
    doc = json.loads((_VECTORS / "delegated-receipt.json").read_text())
    doc["mandate"]["authority"]["policy"] = None
    _reauthorize_for_vector_leaf(ind, doc)

    got = ind.verify_identity_rung(doc)
    assert got["authority_authentic"] == "verified"
    assert got["policy_binding"] == "mismatch"


def test_independent_checker_derives_each_upstream_key_from_its_grantor():
    pytest.importorskip("nacl")
    ind = _load_independent()
    from bulla.identity import LocalEd25519Signer

    doc = json.loads((_VECTORS / "delegated-receipt.json").read_text())
    grant = doc["mandate"]["authority"]["delegation"][0]
    attacker = LocalEd25519Signer(seed=bytes([9]) + bytes(31))
    grant["proof"] = attacker.sign_domain("delegation-grant", ind._grant_hash(grant))
    _reauthorize_for_vector_leaf(ind, doc)

    got = ind.verify_identity_rung(doc)
    assert got["authority_authentic"] == "verified"
    assert got["chain_integrity"] == "broken"
