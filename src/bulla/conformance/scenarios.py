"""The v0 scenario table. Each scenario is a self-contained check over bulla's
own primitives, run from the RELYING PARTY's position with the host adversarial.

Groups:
  R — recomputation        (the verdict re-derives from pinned inputs)
  L — log integrity        (omission, deletion, equivocation, borrowed proofs)
  A — appeal path          (the modality law on the v0.2 envelope)
  C — cure                 (refuse-and-cure completes; the cure is the fix)
  G — gate                 (refusals fire before execution, contestably)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Scenario:
    id: str
    group: str
    title: str
    check: Callable[[], bool]


def _comp(disclosed: bool):
    from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

    obs = ("path_root",) if disclosed else ()
    a = ToolSpec("producer", ("path_root",), obs)
    b = ToolSpec("consumer", ("path_root",), obs)
    e = Edge("producer", "consumer",
             (SemanticDimension("path_root", "path_root", "path_root"),))
    return Composition("conf_seam" + ("_d" if disclosed else ""), (a, b), (e,))


def _signer():
    from bulla.identity import LocalEd25519Signer

    return LocalEd25519Signer(seed=bytes(range(1, 33)))


def _signed(disclosed: bool, envelope=None) -> dict:
    from bulla.certificate import certify, sign_certificate, to_dict

    return to_dict(sign_certificate(certify(_comp(disclosed)), _signer(), envelope=envelope))


def _envelope():
    from bulla.envelope import (
        Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy,
    )

    return RecourseEnvelope(
        authority=Authority(principal="did:web:ops.example", policy="sha256:" + "aa" * 32),
        bounds=Bounds(scope="conformance"),
        recourse=Recourse(
            challenge_window="P30D",
            forum=Forum(log_endpoint="local", trusted_root_ref="pin"),
            remedies=(
                Remedy("recompute", "bulla verify", "attestation:self"),
                Remedy("challenge", "rfc6962-inclusion", "root:pinned"),
                Remedy("escalate", "human-review", "delegation:did:web:ops.example"),
            ),
        ),
    )


def _log(tmp_suffix: str):
    import tempfile
    from pathlib import Path

    from bulla.registry import DeedLog

    return DeedLog(Path(tempfile.mkdtemp()) / f"conf-{tmp_suffix}.jsonl")


# ── R: recomputation ─────────────────────────────────────────────────


def r1_integrity_recomputes() -> bool:
    from bulla.certificate import verify_certificate_integrity

    return verify_certificate_integrity(_signed(True))


def r2_content_tamper_detected() -> bool:
    from bulla.certificate import verify_certificate_integrity

    cert = _signed(True)
    cert["diagnostic"]["coherence_fee"] = 0 if cert["diagnostic"]["coherence_fee"] else 1
    return not verify_certificate_integrity(cert)


def r3_issuer_swap_detected() -> bool:
    from bulla.certificate import verify_certificate_integrity

    cert = _signed(True)
    cert["issuer"] = {"type": "did:key", "id": "did:key:zVictim"}
    return not verify_certificate_integrity(cert)


def r4_fee_is_recomputable_not_attested() -> bool:
    from bulla.diagnostic import diagnose

    cert = _signed(False)
    return diagnose(_comp(False)).coherence_fee == cert["diagnostic"]["coherence_fee"]


# ── L: log integrity ─────────────────────────────────────────────────


def l1_inclusion_verifies_under_pinned_root() -> bool:
    from bulla.registry import Deed, verify_inclusion_record

    log = _log("l1")
    idx = log.append(Deed.from_certificate(_signed(True), public_key=_signer().public_key))
    return verify_inclusion_record(log.inclusion(idx), trusted_root=log.root())


def l2_host_asserted_root_refused() -> bool:
    from bulla.registry import classify_root_trust

    label, trusted = classify_root_trust(True, "sha256:" + "ab" * 32, None, None)
    return label == "host-asserted" and not trusted


def l3_pinned_root_mismatch_refused() -> bool:
    from bulla.registry import Deed, verify_inclusion_record

    log = _log("l3")
    idx = log.append(Deed.from_certificate(_signed(True), public_key=_signer().public_key))
    return not verify_inclusion_record(log.inclusion(idx), trusted_root="sha256:" + "00" * 32)


def l4_borrowed_inclusion_refused() -> bool:
    from bulla.registry import Deed, deed_leaf, verify_inclusion_record

    log = _log("l4")
    d1 = Deed.from_certificate(_signed(True), public_key=_signer().public_key)
    d2 = Deed.from_certificate(_signed(False), public_key=_signer().public_key)
    i1 = log.append(d1)
    log.append(d2)
    other_leaf = deed_leaf({"issuer": d2.issuer, "content_hash": d2.content_hash,
                            "attestation_hash": d2.attestation_hash})
    return not verify_inclusion_record(
        log.inclusion(i1), trusted_root=log.root(), expected_leaf=other_leaf
    )


def l5_deletion_breaks_consistency() -> bool:
    from bulla.registry import Deed, verify_consistency_record

    log = _log("l5")
    log.append(Deed.from_certificate(_signed(True), public_key=_signer().public_key))
    old_size = len(log)
    log.append(Deed.from_certificate(_signed(False), public_key=_signer().public_key))
    honest = log.consistency(old_size)
    ok_honest = verify_consistency_record(honest)
    # a host that dropped the old prefix serves a different old_root — refused
    forged = dict(honest)
    forged["old_root"] = "sha256:" + "11" * 32
    ok_forged = verify_consistency_record(forged)
    return ok_honest and not ok_forged


def l6_omission_is_checkable() -> bool:
    """The relying party can enumerate an issuer's deeds and demand inclusion
    for each — a deed the host serves but cannot prove included is refusable."""
    from bulla.registry import Deed

    log = _log("l6")
    d = Deed.from_certificate(_signed(True), public_key=_signer().public_key)
    log.append(d)
    return log.inclusion_by_attestation(d.attestation_hash) is not None and (
        log.inclusion_by_attestation("sha256:" + "ff" * 32) is None
    )


# ── A: appeal path (the modality law) ────────────────────────────────


def a1_remedy_without_anchor_unconstructible() -> bool:
    from bulla.envelope import EnvelopeError, Remedy

    try:
        Remedy("cure", "bulla repair", "")
        return False
    except EnvelopeError:
        return True


def a2_remedy_pointing_at_actor_unconstructible() -> bool:
    """There is no rung for 'summon the agent' — the ladder has no respondent."""
    from bulla.envelope import REMEDY_RUNGS, EnvelopeError, Remedy

    if "summon" in REMEDY_RUNGS or "sue" in REMEDY_RUNGS:
        return False
    try:
        Remedy("summon", "court", "the-agent")
        return False
    except EnvelopeError:
        return True


def a3_escalate_requires_surviving_principal() -> bool:
    from bulla.envelope import (
        EnvelopeError, Forum, Recourse, RecourseEnvelope, Remedy,
    )

    try:
        RecourseEnvelope(recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(log_endpoint="x", trusted_root_ref="pin"),
            remedies=(Remedy("escalate", "human-review", "delegation:x"),),
        ))
        return False
    except EnvelopeError:
        return True


def a4_forum_must_pin_the_root() -> bool:
    from bulla.envelope import EnvelopeError, Forum

    try:
        Forum(log_endpoint="https://host.example", trusted_root_ref="")
        return False
    except EnvelopeError:
        return True


def a5_served_process_theater_refused() -> bool:
    """A hash-correct envelope violating the modality law is refused on read."""
    from bulla.certificate import _attestation_hash
    from bulla.registry import verify_deed_record

    cert = _signed(True)
    bad = {"deed_schema": "0.2",
           "recourse": {"challenge_window": "P30D",
                        "forum": {"log_endpoint": "x", "trusted_root_ref": "pin"},
                        "remedies": [{"rung": "cure", "verifier": "trust-us", "anchor": ""}]}}
    rec = {"issuer": cert["issuer"]["id"], "content_hash": cert["certificate_content_hash"],
           "signature": cert["signature"], "envelope": bad,
           "attestation_hash": _attestation_hash(
               cert["certificate_content_hash"], cert["signature"], bad)}
    return not verify_deed_record(rec)


def a6_tampered_bounds_refused() -> bool:
    from bulla.registry import Deed, verify_deed_record

    cert = _signed(True, envelope=_envelope())
    deed = Deed.from_certificate(cert, public_key=_signer().public_key)
    log = _log("a6")
    log.append(deed)
    rec = log.by_composition(deed.composition_hash)[0]
    rec["envelope"] = json.loads(json.dumps(rec["envelope"]))
    rec["envelope"]["bounds"]["scope"] = "EVERYTHING"
    return not verify_deed_record(rec)


def a7_stripped_envelope_refused() -> bool:
    from bulla.registry import Deed, verify_deed_record

    cert = _signed(True, envelope=_envelope())
    deed = Deed.from_certificate(cert, public_key=_signer().public_key)
    log = _log("a7")
    log.append(deed)
    rec = log.by_composition(deed.composition_hash)[0]
    rec["envelope"] = None
    return not verify_deed_record(rec)


# ── C: cure ──────────────────────────────────────────────────────────


def c1_minimum_disclosure_names_the_cure() -> bool:
    from bulla.diagnostic import minimum_disclosure_set

    fields = [f for (_t, f) in minimum_disclosure_set(_comp(False))]
    return fields == ["path_root"]


def c2_disclosure_clears_the_fee() -> bool:
    from bulla.diagnostic import diagnose

    return (diagnose(_comp(False)).coherence_fee, diagnose(_comp(True)).coherence_fee) == (1, 0)


def c3_cured_deed_satisfies_the_gate() -> bool:
    from bulla.recourse_gate import DEFAULT_GATE_POLICY, evaluate_gate
    from bulla.registry import Deed

    cert = _signed(True)
    log = _log("c3")
    deed = Deed.from_certificate(cert, public_key=_signer().public_key)
    log.append(deed)
    incl = log.inclusion_by_attestation(deed.attestation_hash)
    d = evaluate_gate(deed_rec=log.by_composition(deed.composition_hash)[0],
                      inclusion_rec=incl, certificate=cert,
                      is_remote=False, policy=DEFAULT_GATE_POLICY)
    return not d.disposition.lower().startswith("refuse")


# ── G: gate ──────────────────────────────────────────────────────────


def g1_no_deed_refused_before_execution() -> bool:
    from bulla.recourse_gate import DEFAULT_GATE_POLICY, evaluate_gate

    d = evaluate_gate(deed_rec={}, inclusion_rec=None, certificate=None,
                      is_remote=False, policy=DEFAULT_GATE_POLICY)
    return d.disposition.lower().startswith("refuse")


def g2_fee_positive_deed_refused() -> bool:
    from bulla.recourse_gate import DEFAULT_GATE_POLICY, evaluate_gate
    from bulla.registry import Deed

    cert = _signed(False)  # fee = 1
    log = _log("g2")
    deed = Deed.from_certificate(cert, public_key=_signer().public_key)
    log.append(deed)
    incl = log.inclusion_by_attestation(deed.attestation_hash)
    d = evaluate_gate(deed_rec=log.by_composition(deed.composition_hash)[0],
                      inclusion_rec=incl, certificate=cert,
                      is_remote=False, policy=DEFAULT_GATE_POLICY)
    return d.disposition.lower().startswith("refuse")


def g3_refusal_is_signed_and_recomputable() -> bool:
    from bulla.recourse_gate import (
        DEFAULT_GATE_POLICY, build_refusal_certificate, evaluate_gate,
        verify_refusal_certificate,
    )

    d = evaluate_gate(deed_rec={}, inclusion_rec=None, certificate=None,
                      is_remote=False, policy=DEFAULT_GATE_POLICY)
    ref = build_refusal_certificate(d, subject_deed={}, disclose=("path_root",),
                                    signer=_signer())
    return ref["signature"] is not None and verify_refusal_certificate(ref)


def g4_refusal_names_the_cure() -> bool:
    from bulla.recourse_gate import (
        DEFAULT_GATE_POLICY, build_refusal_certificate, evaluate_gate,
    )

    d = evaluate_gate(deed_rec={}, inclusion_rec=None, certificate=None,
                      is_remote=False, policy=DEFAULT_GATE_POLICY)
    ref = build_refusal_certificate(d, subject_deed={}, disclose=("path_root",),
                                    signer=_signer())
    return list(ref["cure"]["disclose"]) == ["path_root"]


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("R1", "recompute", "certificate integrity recomputes from served bytes", r1_integrity_recomputes),
    Scenario("R2", "recompute", "content tamper (fee flip) detected", r2_content_tamper_detected),
    Scenario("R3", "recompute", "issuer swap without re-signing detected", r3_issuer_swap_detected),
    Scenario("R4", "recompute", "the fee re-derives from pinned inputs (not attested)", r4_fee_is_recomputable_not_attested),
    Scenario("L1", "log", "inclusion verifies under a pinned root", l1_inclusion_verifies_under_pinned_root),
    Scenario("L2", "log", "host-asserted root never trusted", l2_host_asserted_root_refused),
    Scenario("L3", "log", "pinned-root mismatch (equivocation) refused", l3_pinned_root_mismatch_refused),
    Scenario("L4", "log", "borrowed inclusion proof refused", l4_borrowed_inclusion_refused),
    Scenario("L5", "log", "deletion breaks consistency proofs", l5_deletion_breaks_consistency),
    Scenario("L6", "log", "omission is checkable by enumeration + inclusion demand", l6_omission_is_checkable),
    Scenario("A1", "appeal", "remedy without a stateful anchor is unconstructible", a1_remedy_without_anchor_unconstructible),
    Scenario("A2", "appeal", "no rung points at the vanished actor", a2_remedy_pointing_at_actor_unconstructible),
    Scenario("A3", "appeal", "escalate requires a surviving principal", a3_escalate_requires_surviving_principal),
    Scenario("A4", "appeal", "the forum must pin the root", a4_forum_must_pin_the_root),
    Scenario("A5", "appeal", "hash-correct process theater refused on read", a5_served_process_theater_refused),
    Scenario("A6", "appeal", "tampered bounds refused", a6_tampered_bounds_refused),
    Scenario("A7", "appeal", "stripped envelope refused", a7_stripped_envelope_refused),
    Scenario("C1", "cure", "minimum disclosure names the cure", c1_minimum_disclosure_names_the_cure),
    Scenario("C2", "cure", "the disclosure clears the fee", c2_disclosure_clears_the_fee),
    Scenario("C3", "cure", "a cured deed satisfies the gate", c3_cured_deed_satisfies_the_gate),
    Scenario("G1", "gate", "no deed ⇒ refuse before execution", g1_no_deed_refused_before_execution),
    Scenario("G2", "gate", "fee-positive deed ⇒ refuse", g2_fee_positive_deed_refused),
    Scenario("G3", "gate", "refusal certificate is signed and recomputable", g3_refusal_is_signed_and_recomputable),
    Scenario("G4", "gate", "the refusal names the cure", g4_refusal_names_the_cure),
)


def run_all() -> dict[str, bool]:
    return {s.id: bool(s.check()) for s in SCENARIOS}


if __name__ == "__main__":
    results = run_all()
    for s in SCENARIOS:
        print(f"{'PASS' if results[s.id] else 'FAIL'}  {s.id:3} [{s.group}] {s.title}")
    failed = [k for k, v in results.items() if not v]
    print(f"\nrecourse-conformance v0: {len(results) - len(failed)}/{len(results)} pass")
    raise SystemExit(1 if failed else 0)
