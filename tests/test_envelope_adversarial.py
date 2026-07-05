"""Deed v0.2 — the adversarial suite (the test IS the property).

The host controls the channel: it serves deed records, envelopes, and roots.
Every guarantee claimed for the envelope is tested from that position —
tampered bounds, swapped authority, stripped envelopes, and a hostile issuer
signing a modality-law-violating envelope all must be REFUSED by a consumer
running the shipped verifiers. Plus the byte-exact v0.1 compatibility claim.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from bulla.certificate import _attestation_hash, certify, sign_certificate, to_dict
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.identity import LocalEd25519Signer
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.registry import Deed, DeedLog, verify_deed_record, verify_served_deed


def _comp() -> Composition:
    a = ToolSpec("a", ("amount",), ("amount",))
    b = ToolSpec("b", ("amount",), ("amount",))
    return Composition(
        "demo",
        (a, b),
        (Edge("a", "b", (SemanticDimension("amount_unit", "amount", "amount"),)),),
    )


def _envelope() -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(
            principal="did:web:acme.example#ops",
            policy="sha256:" + "aa" * 32,
            delegation=("mandate:ops-42",),
        ),
        bounds=Bounds(scope="repo:acme/billing", rollback_window="PT72H"),
        recourse=Recourse(
            challenge_window="P30D",
            forum=Forum(
                log_endpoint="https://registry.example/v1",
                trusted_root_ref="ots:root-2026-07-03",
            ),
            remedies=(
                Remedy("recompute", "bulla verify --registry", "attestation:self"),
                Remedy("challenge", "rfc6962-inclusion", "root:pinned"),
                Remedy("escalate", "human-review", "delegation:mandate:ops-42"),
            ),
        ),
        retention_class="operational",
    )


def _signed_v02(signer=None) -> dict:
    signer = signer or LocalEd25519Signer.generate()
    return to_dict(sign_certificate(certify(_comp()), signer, envelope=_envelope()))


class TestBackwardCompat:
    def test_v01_attestation_preimage_is_byte_exact(self):
        """With no envelope, the attestation preimage must be the historical
        two-key object — recomputed here independently, not via the helper."""
        content = "sha256:" + "cd" * 32
        proof = {"type": "Ed25519Signature2020", "proofValue": "zsig"}
        expected_preimage = json.dumps(
            {"certificate_content_hash": content, "signature": proof},
            sort_keys=True,
            separators=(",", ":"),
        )
        expected = f"sha256:{hashlib.sha256(expected_preimage.encode()).hexdigest()}"
        assert _attestation_hash(content, proof) == expected
        assert _attestation_hash(content, proof, None) == expected

    def test_v01_deed_still_verifies_end_to_end(self, tmp_path):
        signed = to_dict(sign_certificate(certify(_comp()), LocalEd25519Signer.generate()))
        deed = Deed.from_certificate(signed)
        assert deed.envelope is None
        log = DeedLog(tmp_path / "log.jsonl")
        log.append(deed)
        rec = log.by_composition(deed.composition_hash)[0]
        assert verify_deed_record(rec)


class TestV02RoundTrip:
    def test_emit_log_reload_serve_verify(self, tmp_path):
        signed = _signed_v02()
        assert signed["recourse_envelope"]["deed_schema"] == "0.2"
        deed = Deed.from_certificate(signed)
        assert deed.envelope is not None

        log = DeedLog(tmp_path / "log.jsonl")
        idx = log.append(deed)

        # cold reload from disk — the JSONL line carries the envelope
        reloaded = DeedLog(tmp_path / "log.jsonl")
        rec = reloaded.by_composition(deed.composition_hash)[0]
        assert rec["envelope"]["bounds"]["scope"] == "repo:acme/billing"
        assert verify_deed_record(rec)

        # the full read-side chain against a pinned root
        incl = reloaded.inclusion(idx)
        assert verify_served_deed(rec, incl, trusted_root=reloaded.root())

    def test_content_hash_unaffected_by_envelope(self):
        """The recomputable core stays pure: same composition, same content
        hash, with or without the appeal path."""
        signer = LocalEd25519Signer.generate()
        plain = sign_certificate(certify(_comp()), signer)
        enveloped = sign_certificate(certify(_comp()), signer, envelope=_envelope())
        assert plain.certificate_content_hash == enveloped.certificate_content_hash
        assert plain.attestation_hash != enveloped.attestation_hash

    def test_invalid_envelope_dict_refused_at_signing(self):
        bad = _envelope().to_dict()
        bad["recourse"]["remedies"][0]["anchor"] = ""
        from bulla.envelope import EnvelopeError

        with pytest.raises(EnvelopeError, match="stateful anchor"):
            sign_certificate(certify(_comp()), LocalEd25519Signer.generate(), envelope=bad)


class TestMaliciousHost:
    def test_tampered_bounds_refused(self, tmp_path):
        signed = _signed_v02()
        deed = Deed.from_certificate(signed)
        log = DeedLog(tmp_path / "log.jsonl")
        log.append(deed)
        rec = log.by_composition(deed.composition_hash)[0]
        rec["envelope"] = json.loads(json.dumps(rec["envelope"]))
        rec["envelope"]["bounds"]["scope"] = "repo:acme/EVERYTHING"  # widened by the host
        assert not verify_deed_record(rec)

    def test_stripped_envelope_refused(self, tmp_path):
        """A host cannot serve a v0.2 deed as if it were v0.1 — removing the
        appeal path breaks the attestation the leaf committed to."""
        signed = _signed_v02()
        deed = Deed.from_certificate(signed)
        log = DeedLog(tmp_path / "log.jsonl")
        log.append(deed)
        rec = log.by_composition(deed.composition_hash)[0]
        rec["envelope"] = None
        assert not verify_deed_record(rec)

    def test_swapped_authority_refused(self, tmp_path):
        """An envelope from one deed cannot be paired with another deed's
        signature — forged mandate chains are caught."""
        signed = _signed_v02()
        other = RecourseEnvelope(
            authority=Authority(principal="did:web:attacker.example", policy="sha256:" + "ee" * 32),
            bounds=Bounds(scope="repo:acme/billing"),
        )
        deed = Deed.from_certificate(signed)
        log = DeedLog(tmp_path / "log.jsonl")
        log.append(deed)
        rec = log.by_composition(deed.composition_hash)[0]
        rec["envelope"] = other.to_dict()
        assert not verify_deed_record(rec)

    def test_hostile_issuer_cannot_ship_process_theater(self):
        """A hostile issuer signs an envelope whose remedy has no stateful
        anchor, hand-building the attestation so the bytes hash correctly.
        The consumer still refuses: a correct hash proves the issuer signed
        it, not that it is a well-formed appeal path (modality law on the
        read side)."""
        signer = LocalEd25519Signer.generate()
        signed = to_dict(sign_certificate(certify(_comp()), signer))
        bad_envelope = {
            "deed_schema": "0.2",
            "recourse": {
                "challenge_window": "P30D",
                "forum": {
                    "log_endpoint": "https://registry.example/v1",
                    "trusted_root_ref": "ots:root",
                },
                # points at the vanished actor; names no artifact or stake
                "remedies": [{"rung": "cure", "verifier": "trust-us", "anchor": ""}],
            },
        }
        rec = {
            "issuer": signed["issuer"]["id"],
            "content_hash": signed["certificate_content_hash"],
            "signature": signed["signature"],
            "envelope": bad_envelope,
            "attestation_hash": _attestation_hash(
                signed["certificate_content_hash"], signed["signature"], bad_envelope
            ),
        }
        assert not verify_deed_record(rec)
