"""The recourse gate (`bulla.recourse_gate`) — the OBSERVE -> ENFORCE decision core.

The adversarial half: the test where the HOST controls the channel IS the property. A
gate that only catches "deed missing" is a linter; one that catches a lying host (a
host-asserted root, an equivocation, a borrowed inclusion proof) is the moat. Plus the
fee gate (the type-coherence half) and the refuse-and-cure loop.
"""
from __future__ import annotations

import threading

import pytest

from bulla.certificate import certify, sign_certificate, to_dict
from bulla.http_registry import HttpRegistry, make_server
from bulla.identity import LocalEd25519Signer, verify_proof
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.recourse_gate import (
    build_refusal_certificate, evaluate_gate, verify_refusal_certificate,
    DEFAULT_GATE_POLICY, STRICT_GATE_POLICY, GatePolicy,
    BORROWED_INCLUSION, EQUIVOCATED_ROOT, FEE_POSITIVE, FEE_UNVERIFIABLE,
    INAUTHENTIC, OMITTED_FROM_LOG, UNPINNED_ROOT,
)
from bulla.registry import Deed, DeedLog


# ── fixtures ─────────────────────────────────────────────────────────

def _coherent() -> Composition:                       # fee = 0
    return Composition("solo", (ToolSpec("a", (), ()),), ())


def _seam() -> Composition:                            # fee = 1 (path_root hidden)
    fs, git = ToolSpec("filesystem", ("path_root",), ()), ToolSpec("git", ("path_root",), ())
    return Composition("fs_to_git", (fs, git),
                       (Edge("filesystem", "git",
                             (SemanticDimension("path_root", "path_root", "path_root"),)),))


def _disclosed() -> Composition:                       # fee = 0 (path_root disclosed)
    fs = ToolSpec("filesystem", ("path_root",), ("path_root",))
    git = ToolSpec("git", ("path_root",), ("path_root",))
    return Composition("fs_to_git_disclosed", (fs, git),
                       (Edge("filesystem", "git",
                             (SemanticDimension("path_root", "path_root", "path_root"),)),))


def _cert(comp, signer):
    return to_dict(sign_certificate(certify(comp), signer))


def _rec(cert):
    return {
        "issuer": (cert.get("issuer") or {}).get("id"),
        "content_hash": cert.get("certificate_content_hash"),
        "attestation_hash": cert.get("attestation_hash"),
        "composition_hash": (cert.get("subject") or {}).get("composition_sha256"),
        "signature": cert.get("signature"),
    }


def _log_cert(log, cert, signer):
    log.append(Deed.from_certificate(cert, public_key=signer.public_key))


def _serve(reg):
    srv = make_server(reg, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


# ── 1–8: adversarial (the host controls the channel) ─────────────────

def test_gate_refuses_host_asserted_root(tmp_path):
    """A deed genuinely in a REMOTE host's log, but nothing pinned — proceeding would be
    trusting the operator. REFUSE (UNPINNED_ROOT), even though it is authentic + fee=0."""
    signer = LocalEd25519Signer.generate()
    reg = DeedLog(tmp_path / "r.jsonl")
    cert = _cert(_coherent(), signer); _log_cert(reg, cert, signer)
    srv, port = _serve(reg)
    try:
        remote = HttpRegistry(f"http://127.0.0.1:{port}")
        incl = remote.inclusion_by_attestation(cert["attestation_hash"])
        d = evaluate_gate(deed_rec=_rec(cert), inclusion_rec=incl, certificate=cert,
                          trusted_root=None, is_remote=True)
    finally:
        srv.shutdown()
    assert not d.proceed and d.deficiency == UNPINNED_ROOT


def test_gate_refuses_equivocated_root(tmp_path):
    """Pin a root; the host serves a different one — possible equivocation. REFUSE."""
    signer = LocalEd25519Signer.generate()
    reg = DeedLog(tmp_path / "r.jsonl")
    cert = _cert(_coherent(), signer); _log_cert(reg, cert, signer)
    srv, port = _serve(reg)
    try:
        remote = HttpRegistry(f"http://127.0.0.1:{port}")
        incl = remote.inclusion_by_attestation(cert["attestation_hash"])
        d = evaluate_gate(deed_rec=_rec(cert), inclusion_rec=incl, certificate=cert,
                          trusted_root="sha256:" + "0" * 64, is_remote=True)
    finally:
        srv.shutdown()
    assert not d.proceed and d.deficiency == EQUIVOCATED_ROOT


def test_gate_refuses_borrowed_inclusion(tmp_path):
    """The host serves the honest root but answers the inclusion query with a valid proof
    for a DIFFERENT real leaf (borrowed inclusion). The leaf-binding rejects it even with
    the correct root pinned — the omission rung is not borrowable."""
    signer = LocalEd25519Signer.generate()
    reg = DeedLog(tmp_path / "r.jsonl")
    cert_a = _cert(_coherent(), signer); _log_cert(reg, cert_a, signer)
    cert_b = _cert(_seam(), signer); _log_cert(reg, cert_b, signer)   # a second real leaf
    real_root = reg.root()
    borrowed = reg.inclusion_by_attestation(cert_b["attestation_hash"])  # deed_b's proof
    d = evaluate_gate(deed_rec=_rec(cert_a), inclusion_rec=borrowed, certificate=cert_a,
                      trusted_root=real_root, is_remote=True)
    assert not d.proceed and d.deficiency == BORROWED_INCLUSION


def test_gate_refuses_omitted_deed(tmp_path):
    """A deed never logged — not in the registry at all. REFUSE (OMITTED_FROM_LOG)."""
    signer = LocalEd25519Signer.generate()
    cert = _cert(_coherent(), signer)        # never appended anywhere
    d = evaluate_gate(deed_rec=_rec(cert), inclusion_rec=None, certificate=cert,
                      is_remote=False)
    assert not d.proceed and d.deficiency == OMITTED_FROM_LOG


def test_gate_refuses_fee_positive_after_inclusion_and_root_pass(tmp_path):
    """The type-coherence half: a deed that is authentic, included, and under a trusted
    (own-log) root, but certifies fee=1, is still REFUSED. The fee arm fires only AFTER
    inclusion/root pass — proving fee is an independent gate."""
    signer = LocalEd25519Signer.generate()
    reg = DeedLog(tmp_path / "r.jsonl")
    cert = _cert(_seam(), signer); _log_cert(reg, cert, signer)   # fee = 1
    incl = reg.inclusion_by_attestation(cert["attestation_hash"])
    d = evaluate_gate(deed_rec=_rec(cert), inclusion_rec=incl, certificate=cert,
                      is_remote=False, policy=STRICT_GATE_POLICY)  # opt-in fee/disclosure gate
    assert d.included is True                # inclusion passed…
    assert d.fee == 1
    assert not d.proceed and d.deficiency == FEE_POSITIVE   # …and fee still refused


def test_default_policy_proceeds_on_positive_fee_reporting_not_gating(tmp_path):
    """Regression guard (northstar review 2026-07-09): the fee is a disclosure
    signal, NOT a PROCEED precondition. Under the DEFAULT policy, a deed that is
    authentic, included, own-log, and certifies fee=1 must PROCEED — the fee is
    reported, never gated. Only the opt-in STRICT policy refuses on it (see the
    test above). This keeps the retired detection thesis out of the enforcement
    path: fee-gating cannot silently become the default again."""
    signer = LocalEd25519Signer.generate()
    reg = DeedLog(tmp_path / "r.jsonl")
    cert = _cert(_seam(), signer); _log_cert(reg, cert, signer)   # fee = 1
    incl = reg.inclusion_by_attestation(cert["attestation_hash"])
    d = evaluate_gate(deed_rec=_rec(cert), inclusion_rec=incl, certificate=cert,
                      is_remote=False, policy=DEFAULT_GATE_POLICY)
    assert d.included is True and d.fee == 1     # inclusion passed, fee reported…
    assert d.proceed is True and d.deficiency is None   # …and it did NOT gate


def test_gate_refuses_fee_unverifiable_without_cert(tmp_path):
    """A bare deed record (no certificate) cannot prove fee=0 — the leaf carries no fee.
    REFUSE (FEE_UNVERIFIABLE), even though it is authentic + included + own-log."""
    signer = LocalEd25519Signer.generate()
    reg = DeedLog(tmp_path / "r.jsonl")
    cert = _cert(_coherent(), signer); _log_cert(reg, cert, signer)
    incl = reg.inclusion_by_attestation(cert["attestation_hash"])
    d = evaluate_gate(deed_rec=_rec(cert), inclusion_rec=incl, certificate=None,
                      is_remote=False, policy=STRICT_GATE_POLICY)   # opt-in fee/disclosure gate; no certificate
    assert not d.proceed and d.deficiency == FEE_UNVERIFIABLE


def test_gate_refuses_tampered_cert(tmp_path):
    """A logged deed whose certificate is then tampered (same attestation_hash, so
    inclusion still hits): integrity fails, so the signed fee cannot be forged up to 0.
    REFUSE (INAUTHENTIC)."""
    signer = LocalEd25519Signer.generate()
    reg = DeedLog(tmp_path / "r.jsonl")
    cert = _cert(_coherent(), signer); _log_cert(reg, cert, signer)
    incl = reg.inclusion_by_attestation(cert["attestation_hash"])
    tampered = dict(cert, subject=dict(cert["subject"], name="EVIL RENAME"))
    d = evaluate_gate(deed_rec=_rec(cert), inclusion_rec=incl, certificate=tampered,
                      is_remote=False)
    assert d.included is True and d.integrity is False
    assert not d.proceed and d.deficiency == INAUTHENTIC


def test_gate_proceeds_only_on_verified_fee0_inclusion(tmp_path):
    """The single happy path: own-log (independently trusted) + authentic + included +
    fee=0 -> PROCEED."""
    signer = LocalEd25519Signer.generate()
    reg = DeedLog(tmp_path / "r.jsonl")
    cert = _cert(_coherent(), signer); _log_cert(reg, cert, signer)
    incl = reg.inclusion_by_attestation(cert["attestation_hash"])
    d = evaluate_gate(deed_rec=_rec(cert), inclusion_rec=incl, certificate=cert,
                      is_remote=False)
    assert d.proceed and d.deficiency is None and d.fee == 0 and d.root_trust == "own-log"


# ── 9–11: refuse-and-cure (the recourse loop made real) ──────────────

def test_refusal_certificate_names_deficiency_and_cure(tmp_path):
    """On refuse, the contestable artifact names the deficiency AND the cure: present a
    fee=0 deed, disclosing the convention."""
    signer = LocalEd25519Signer.generate()
    reg = DeedLog(tmp_path / "r.jsonl")
    cert = _cert(_seam(), signer); _log_cert(reg, cert, signer)
    incl = reg.inclusion_by_attestation(cert["attestation_hash"])
    d = evaluate_gate(deed_rec=_rec(cert), inclusion_rec=incl, certificate=cert,
                      is_remote=False, policy=STRICT_GATE_POLICY)  # opt-in fee/disclosure gate
    ref = build_refusal_certificate(d, subject_deed=_rec(cert), disclose=("path_root",))
    assert ref["deficiency"] == FEE_POSITIVE
    assert ref["cure"]["disclose"] == ["path_root"]
    assert ref["cure"]["require_fee"] == 0
    assert ref["subject_deed"]["attestation_hash"] == cert["attestation_hash"]


def test_refusal_certificate_is_signed_and_recomputable(tmp_path):
    """Symmetric to the deed: a refusal signed by the relying party is non-repudiable and
    recomputable from its content alone."""
    signer = LocalEd25519Signer.generate()
    cert = _cert(_coherent(), signer)
    d = evaluate_gate(deed_rec=_rec(cert), inclusion_rec=None, certificate=cert, is_remote=False)
    ref = build_refusal_certificate(d, subject_deed=_rec(cert), signer=signer)
    assert ref["signature"] is not None
    assert verify_refusal_certificate(ref)                       # content hash recomputes
    assert verify_proof(ref["refusal_content_hash"], ref["signature"]).authentic
    # tamper the cure -> the content hash no longer matches -> rejected
    ref["cure"]["require_fee"] = 99
    assert not verify_refusal_certificate(ref)


def test_cure_loop_flips_refuse_to_proceed(tmp_path):
    """The recourse loop: a fee=1 deed is REFUSED; disclosing path_root clears the fee
    1->0; a re-emitted fee=0 deed, logged and re-presented, PROCEEDS."""
    signer = LocalEd25519Signer.generate()
    reg = DeedLog(tmp_path / "r.jsonl")
    # before the cure: fee = 1 -> refuse
    seam_cert = _cert(_seam(), signer); _log_cert(reg, seam_cert, signer)
    before = evaluate_gate(deed_rec=_rec(seam_cert),
                           inclusion_rec=reg.inclusion_by_attestation(seam_cert["attestation_hash"]),
                           certificate=seam_cert, is_remote=False, policy=STRICT_GATE_POLICY)
    assert not before.proceed and before.deficiency == FEE_POSITIVE
    # the cure: disclose path_root -> fee = 0; re-emit, re-log, re-present
    cured_cert = _cert(_disclosed(), signer); _log_cert(reg, cured_cert, signer)
    after = evaluate_gate(deed_rec=_rec(cured_cert),
                          inclusion_rec=reg.inclusion_by_attestation(cured_cert["attestation_hash"]),
                          certificate=cured_cert, is_remote=False, policy=STRICT_GATE_POLICY)
    assert after.proceed and after.fee == 0


# ── 12: end-to-end on REAL git (the demo as a test) ──────────────────

def test_recourse_gate_closes_loop_git():
    """Run the execution-attributable demo: the gate prevents a real `git show` breach,
    and the cure that clears it is the one that makes git succeed. Labels are git exit
    codes. Skips only if the environment has no tracked files (INVALID CONTROL)."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    bulla_root = Path(__file__).resolve().parent.parent          # bulla/
    demo = bulla_root / "calibration" / "recourse_gate_closes_loop_git.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(bulla_root / "src"), env.get("PYTHONPATH"))
        if part
    )
    r = subprocess.run([sys.executable, str(demo)], capture_output=True, text=True, env=env)
    if r.returncode == 2:
        pytest.skip(f"demo could not establish its control: {r.stdout.strip() or r.stderr.strip()}")
    assert r.returncode == 0, f"loop OPEN:\n{r.stdout}\n{r.stderr}"

    art = json.loads((bulla_root / "calibration" / "results"
                      / "recourse_gate_closes_loop_git.json").read_text())
    assert art["loop_closed"] is True
    assert art["fee_before"] == 1 and art["fee_after"] == 0
    assert art["acts"]["act0_no_gate"]["breach"] is True               # the loss is real
    assert art["acts"]["act1_gate_refuses"]["all_refused"] is True     # 3/3 refused…
    assert art["acts"]["act1_gate_refuses"]["git_invoked"] is False    # …before git ran
    assert art["acts"]["act2_cure_proceed"]["proceeded"] is True       # cure -> proceed
    assert art["acts"]["act2_cure_proceed"]["git_ok"] is True          # and git really succeeded


# ── 13: determinism — the thesis as an adversarial test ──────────────

# Reconstruct the SAME refusal from pinned inputs and print its content hash. Used in a
# subprocess under a randomized PYTHONHASHSEED to prove no dict-ordering / env leak reaches
# the address — "a deed is a recomputable certificate, not a signed opinion."
_DETERMINISM_SNIPPET = """
import sys; sys.path.insert(0, "src")
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.certificate import certify, sign_certificate, to_dict
from bulla.identity import LocalEd25519Signer
from bulla.recourse_gate import evaluate_gate, build_refusal_certificate
signer = LocalEd25519Signer(seed=bytes(range(32)))
fs = ToolSpec("filesystem", ("path_root",), ()); git = ToolSpec("git", ("path_root",), ())
seam = Composition("fs_to_git", (fs, git),
                   (Edge("filesystem", "git", (SemanticDimension("path_root","path_root","path_root"),)),))
cert = to_dict(sign_certificate(certify(seam), signer))
rec = {"issuer": (cert.get("issuer") or {}).get("id"),
       "content_hash": cert.get("certificate_content_hash"),
       "attestation_hash": cert.get("attestation_hash"),
       "composition_hash": (cert.get("subject") or {}).get("composition_sha256"),
       "signature": cert.get("signature")}
d = evaluate_gate(deed_rec=rec, inclusion_rec=None, certificate=cert, is_remote=False)
ref = build_refusal_certificate(d, subject_deed=rec, disclose=("path_root",))
print(ref["refusal_content_hash"])
"""


def test_refusal_hash_is_recomputable_across_runs_and_hash_seed():
    """The categorical break from a signed opinion: over pinned inputs the refusal's
    content hash is byte-identical across two in-process runs AND across subprocesses with
    DIFFERENT PYTHONHASHSEED values — so no dict iteration order or environment touches the
    address. The adversarial-test-IS-the-property discipline applied to recomputability."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    signer = LocalEd25519Signer(seed=bytes(range(32)))   # pinned identity -> pinned address
    cert = _cert(_seam(), signer)

    def in_process_hash() -> str:
        d = evaluate_gate(deed_rec=_rec(cert), inclusion_rec=None, certificate=cert,
                          is_remote=False)
        return build_refusal_certificate(
            d, subject_deed=_rec(cert), disclose=("path_root",))["refusal_content_hash"]

    h1, h2 = in_process_hash(), in_process_hash()
    assert h1 == h2 and h1.startswith("sha256:")          # two runs -> identical

    bulla_dir = Path(__file__).resolve().parent.parent    # bulla/ (so `src` resolves)
    seen = {h1}
    for seed in ("0", "1", "123456789"):                  # randomized hash seeds
        r = subprocess.run([sys.executable, "-c", _DETERMINISM_SNIPPET],
                           capture_output=True, text=True, cwd=str(bulla_dir),
                           env={**os.environ, "PYTHONHASHSEED": seed}, timeout=60)
        assert r.returncode == 0, r.stderr
        seen.add(r.stdout.strip())
    assert seen == {h1}, f"refusal hash leaked the hash seed / env: {seen}"
