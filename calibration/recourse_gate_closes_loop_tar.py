#!/usr/bin/env python3
"""LOOP CLOSED on REAL tar — the filesystem-family gallery entry.

Same pattern as ``recourse_gate_closes_loop_git``: a producer (filesystem tool)
emits an ABSOLUTE path; the consumer (archiver) runs ``tar -cf out.tar -C base
<member>`` which needs a member path RELATIVE to ``-C base``. The path-root
convention is hidden -> coherence fee = 1. The label is tar's own exit code at
every step — EXECUTION_INDEPENDENT; the fee only governs whether the gate lets
tar run.

Three acts:
  0  NO GATE (the loss).       Consumer archives the producer's absolute path;
                               ``tar`` fails for real. A cross-owner breach.
  1  GATE refuses (prevented). The relying party demands the producer's deed and
                               refuses (no deed / host-asserted root / fee=1 deed)
                               BEFORE tar runs. Signed, contestable refusal
                               certificates name the cure.
  2  CURE -> PROCEED -> exit 0. Disclosing ``path_root`` (the SAME
                               minimum_disclosure_set the refusal named) clears
                               the fee 1 -> 0 AND lets transport() rewrite the
                               path; the re-emitted fee=0 deed satisfies the
                               gate; ``tar`` succeeds and the member lists.

CAUSAL CHAIN: the coherence cure and the execution fix are the SAME disclosure.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BULLA = HERE.parent
sys.path.insert(0, str(BULLA / "src"))

from bulla.certificate import certify, sign_certificate, to_dict  # noqa: E402
from bulla.diagnostic import diagnose, minimum_disclosure_set  # noqa: E402
from bulla.identity import LocalEd25519Signer  # noqa: E402
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec  # noqa: E402
from bulla.recourse_gate import (  # noqa: E402
    DEFAULT_GATE_POLICY, build_refusal_certificate, evaluate_gate,
    verify_refusal_certificate,
)
from bulla.registry import Deed, DeedLog  # noqa: E402


def seam_composition() -> Composition:
    """filesystem -> archiver with `path_root` HIDDEN on both sides: fee = 1.
    Same encoding as the git twin: both tools hold `path_root` internally,
    neither advertises it (observable schema empty)."""
    fs = ToolSpec("filesystem", ("path_root",), ())
    ar = ToolSpec("archiver", ("path_root",), ())
    edge = Edge("filesystem", "archiver",
                (SemanticDimension("path_root", "path_root", "path_root"),))
    return Composition("fs_to_tar_seam", (fs, ar), (edge,))


def disclosed_composition() -> Composition:
    """The cured seam: `path_root` OBSERVABLE on both tools -> fee = 0."""
    fs = ToolSpec("filesystem", ("path_root",), ("path_root",))
    ar = ToolSpec("archiver", ("path_root",), ("path_root",))
    edge = Edge("filesystem", "archiver",
                (SemanticDimension("path_root", "path_root", "path_root"),))
    return Composition("fs_to_tar_disclosed", (fs, ar), (edge,))


def tar_create(base: Path, member: str, out: Path) -> tuple[bool, str]:
    """The REAL executor. Success is tar's exit code, never bulla's fee."""
    r = subprocess.run(
        ["tar", "-cf", str(out), "-C", str(base), member],
        capture_output=True, text=True,
    )
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def transport(abs_path: str, path_root: str) -> str:
    """The convention bridge `path_root` unlocks: absolute -> base-relative."""
    return str(Path(abs_path).relative_to(path_root))


def main() -> int:
    seam, cured = seam_composition(), disclosed_composition()
    fee_before = diagnose(seam).coherence_fee
    fee_after = diagnose(cured).coherence_fee
    disclose = tuple(field for (_t, field) in minimum_disclosure_set(seam))
    if not (fee_before == 1 and fee_after == 0):
        print(f"INVALID CONTROL: expected fee 1->0, got {fee_before}->{fee_after}")
        return 2

    # The cross-owner setup: the producer emitted a path in ITS OWN namespace
    # (`/agents/producer-0/…` — its container root, which does not exist on the
    # consumer's host); the consumer holds the same content under its own base.
    # The hidden convention is the producer's path root.
    producer_root = "/agents/producer-0"
    base = Path(tempfile.mkdtemp())          # the consumer's base
    (base / "reports").mkdir()
    (base / "reports" / "q3.md").write_text("# Q3\n")
    abs_path = f"{producer_root}/reports/q3.md"   # what the producer emits
    out = base / "out.tar"

    signer = LocalEd25519Signer(seed=bytes(range(32)))
    log = DeedLog(path=str(base / "relying-party.jsonl"))
    cert_obstructed = to_dict(sign_certificate(certify(seam), signer))
    log.append(Deed.from_certificate(cert_obstructed, public_key=signer.public_key))
    cert_cured = to_dict(sign_certificate(certify(cured), signer))
    log.append(Deed.from_certificate(cert_cured, public_key=signer.public_key))

    def _portable(s: str) -> str:
        return s.replace(str(base), "<BASE>")

    acts: dict = {}

    # ── ACT 0 — NO GATE ──────────────────────────────────────────────────
    ok0, detail0 = tar_create(base, abs_path, out)
    acts["act0_no_gate"] = {
        "gate": False, "tar_invoked": True, "tar_ok": ok0,
        "tar_detail": _portable(detail0), "breach": (not ok0),
        "note": "consumer archived the producer's absolute path; tar -C base <abs> failed.",
    }

    # ── ACT 1 — GATE refuses BEFORE tar runs ─────────────────────────────
    rec_ob = {
        "issuer": (cert_obstructed.get("issuer") or {}).get("id"),
        "content_hash": cert_obstructed.get("certificate_content_hash"),
        "attestation_hash": cert_obstructed.get("attestation_hash"),
        "composition_hash": (cert_obstructed.get("subject") or {}).get("composition_sha256"),
    }
    d_missing = evaluate_gate(deed_rec={}, inclusion_rec=None, certificate=None,
                              is_remote=False, policy=DEFAULT_GATE_POLICY)
    incl = log.inclusion_by_attestation(rec_ob["attestation_hash"])
    d_fee = evaluate_gate(deed_rec=rec_ob, inclusion_rec=incl,
                          certificate=cert_obstructed, is_remote=False,
                          policy=DEFAULT_GATE_POLICY)
    refusals = {}
    for label, decision in (("missing", d_missing), ("fee_positive", d_fee)):
        if decision.disposition.lower().startswith("refuse"):
            ref = build_refusal_certificate(decision, subject_deed=rec_ob,
                                            disclose=disclose, signer=signer)
            refusals[label] = {
                "deficiency": ref["deficiency"],
                "signed": ref["signature"] is not None,
                "recomputable": verify_refusal_certificate(ref),
                "cure_disclose": list(ref["cure"]["disclose"]),
            }
    acts["act1_gate_refuses"] = {
        "gate": True, "tar_invoked": False, "refusals": refusals,
        "breach_prevented": bool(refusals),
    }

    # ── ACT 2 — CURE -> PROCEED -> tar succeeds ──────────────────────────
    rec_cured = {
        "issuer": (cert_cured.get("issuer") or {}).get("id"),
        "content_hash": cert_cured.get("certificate_content_hash"),
        "attestation_hash": cert_cured.get("attestation_hash"),
        "composition_hash": (cert_cured.get("subject") or {}).get("composition_sha256"),
    }
    incl2 = log.inclusion_by_attestation(rec_cured["attestation_hash"])
    d_ok = evaluate_gate(deed_rec=rec_cured, inclusion_rec=incl2,
                         certificate=cert_cured, is_remote=False,
                         policy=DEFAULT_GATE_POLICY)
    proceeded = not d_ok.disposition.lower().startswith("refuse")
    rel = transport(abs_path, producer_root)  # the SAME disclosure fixes execution
    ok2, detail2 = tar_create(base, rel, out)
    listed = subprocess.run(["tar", "-tf", str(out)], capture_output=True, text=True)
    acts["act2_cure_proceed"] = {
        "gate": True, "disposition": d_ok.disposition, "proceeded": proceeded,
        "disclosed": list(disclose), "transported": rel,
        "tar_ok": ok2, "tar_detail": _portable(detail2),
        "member_listed": rel in listed.stdout.split(),
    }

    verdict = (acts["act0_no_gate"]["breach"]
               and acts["act1_gate_refuses"]["breach_prevented"]
               and proceeded and ok2 and acts["act2_cure_proceed"]["member_listed"])
    print(json.dumps({"family": "filesystem/tar", "fee_before": fee_before,
                      "fee_after": fee_after, "acts": acts,
                      "LOOP_CLOSED": verdict}, indent=2))
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
