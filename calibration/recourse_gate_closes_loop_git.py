#!/usr/bin/env python3
"""The recourse gate closes the loop on REAL git — refuse-and-cure, execution-attributed.

The capstone of the gate (`bulla.recourse_gate.evaluate_gate` / `bulla gate` / the proxy
ENFORCE interceptor). It runs the gate on the same real `git show HEAD:<path>` seam as
``repair_closes_loop_git`` (a filesystem tool emits an ABSOLUTE path; git needs the
REPO-RELATIVE one; the path-root convention is hidden -> fee = 1), and shows that a
relying party's gate prevents a real cross-owner breach BEFORE it happens, and that the
SAME disclosure which clears the gate is the one that makes git succeed.

The label is git's own exit code at every step (`git_show(...).ok == (returncode == 0)`),
never bulla's fee — EXECUTION_INDEPENDENT. The fee only governs whether the gate *lets
git run*.

Three acts:
  0  NO GATE (the loss).      The consumer acts on the producer's ABSOLUTE path;
                              `git show HEAD:<abs>` fails for real. A cross-owner breach.
  1  GATE refuses (prevented). The relying party DEMANDS the producer's deed and refuses
                              three ways BEFORE any `git show` runs:
                                a) no deed presented            -> MISSING
                                b) logged under a host-asserted root, unpinned (an
                                   adversarial operator)        -> UNPINNED_ROOT
                                c) a deed that certifies fee = 1 -> FEE_POSITIVE
                              Each emits a signed, contestable RefusalCertificate naming
                              the cure. `git show` is never called -> breach prevented by
                              construction.
  2  CURE -> PROCEED -> success. The cure = disclose `path_root` (the SAME
                              `minimum_disclosure_set` the refusal named). Disclosing it
                              (i) clears the coherence fee 1 -> 0 AND (ii) lets
                              `transport(abs)->rel` rewrite the path. The producer
                              re-emits a fee = 0 deed, logs it, the relying party verifies
                              against its own log -> PROCEED -> `git show HEAD:<rel>`
                              succeeds.

CAUSAL CHAIN (the load-bearing claim): the coherence cure and the execution fix are the
SAME disclosure. The undeclared `path_root` is simultaneously (1) the one term the gate
refuses on — the producer's deed certifies coherence_fee = 1 — and (2) exactly what
`transport()` needs to rewrite the absolute path into the consumer's repo-relative one.
Disclosing it drives the fee 1 -> 0 (so a re-emitted deed satisfies the gate's fee = 0
policy) and, by the same disclosure, makes `git show HEAD:<rel>` resolve. WITHOUT the gate
the consumer acts on the absolute path and `git show HEAD:<abs>` fails — a real,
execution-attributed breach. WITH the gate the breach is refused before git runs; the cure
that satisfies the gate is identically the cure that makes git succeed. The label is git's
exit code at every step — the fee only governs whether the gate lets git run.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent          # bulla/calibration
BULLA = HERE.parent                             # bulla
sys.path.insert(0, str(BULLA / "src"))          # resolves `bulla.*`
sys.path.insert(0, str(BULLA))                  # resolves `calibration.*`

from calibration.repair_closes_loop_git import (  # noqa: E402
    REPO, git_show, seam_composition, transport,
)
from bulla.certificate import certify, sign_certificate, to_dict  # noqa: E402
from bulla.diagnostic import diagnose, minimum_disclosure_set  # noqa: E402
from bulla.identity import LocalEd25519Signer  # noqa: E402
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec  # noqa: E402
from bulla.recourse_gate import (  # noqa: E402
    build_refusal_certificate, evaluate_gate, STRICT_GATE_POLICY,
)
from bulla.registry import Deed, DeedLog  # noqa: E402


def disclosed_composition() -> Composition:
    """The cured seam: `path_root` is now OBSERVABLE on both tools, so the consumer can
    see the producer's path convention -> coherence fee = 0. This is exactly the
    `minimum_disclosure_set` the refusal certificate demands."""
    fs = ToolSpec("filesystem", ("path_root",), ("path_root",))
    git = ToolSpec("git", ("path_root",), ("path_root",))
    edge = Edge("filesystem", "git",
                (SemanticDimension("path_root", "path_root", "path_root"),))
    return Composition("fs_to_git_disclosed", (fs, git), (edge,))


def _cert(comp: Composition, signer: LocalEd25519Signer) -> dict:
    return to_dict(sign_certificate(certify(comp), signer))


def _deed_rec(cert: dict) -> dict:
    return {
        "issuer": (cert.get("issuer") or {}).get("id"),
        "content_hash": cert.get("certificate_content_hash"),
        "attestation_hash": cert.get("attestation_hash"),
        "composition_hash": (cert.get("subject") or {}).get("composition_sha256"),
    }


def main() -> int:
    seam = seam_composition()             # fee = 1 (path_root hidden)
    cured = disclosed_composition()       # fee = 0 (path_root disclosed)
    fee_before = diagnose(seam).coherence_fee
    fee_after = diagnose(cured).coherence_fee
    # the cure names the convention(s) to disclose; minimum_disclosure_set returns
    # (tool, field) pairs, and the field IS the convention dimension (here: path_root).
    disclose = tuple(field for (_tool, field) in minimum_disclosure_set(seam))
    if not (fee_before == 1 and fee_after == 0):
        print(f"INVALID CONTROL: expected fee 1->0, got {fee_before}->{fee_after}")
        return 2

    # one real tracked file -> a real cross-owner crossing of the seam
    import subprocess
    tracked = subprocess.run(["git", "-C", REPO, "ls-files"],
                             capture_output=True, text=True, check=True).stdout.split()
    rels = [p for p in tracked if p.endswith(".md")] or tracked
    if not rels:
        print("INVALID CONTROL: no tracked files")
        return 2
    rel = rels[0]
    abs_path = str(Path(REPO) / rel)

    # A FIXED-seed demo identity, so the artifact (deed/refusal content hashes) is
    # reproducible run-to-run — the same determinism the deed itself enforces (a deed is a
    # recomputable certificate, not a signed opinion). A random key would leave the
    # committed artifact perpetually dirty.
    signer = LocalEd25519Signer(seed=bytes(range(32)))
    tmp = tempfile.mkdtemp()
    log = DeedLog(path=str(Path(tmp) / "relying-party.jsonl"))   # the relying party's OWN log

    cert_obstructed = _cert(seam, signer)                        # certifies fee = 1
    log.append(Deed.from_certificate(cert_obstructed, public_key=signer.public_key))
    cert_cured = _cert(cured, signer)                            # certifies fee = 0
    log.append(Deed.from_certificate(cert_cured, public_key=signer.public_key))

    acts: dict = {}

    # git's stderr echoes the absolute path; relativize it so the committed artifact is
    # portable + deterministic across machines (the source_path-leak discipline).
    def _portable(s: str) -> str:
        return s.replace(REPO, "<REPO>")

    # ── ACT 0 — NO GATE (the loss) ────────────────────────────────────────────────
    ok_abs, detail_abs = git_show(abs_path)                      # consumer uses the abs path
    acts["act0_no_gate"] = {
        "gate": False, "git_invoked": True, "git_ok": ok_abs, "git_detail": _portable(detail_abs),
        "breach": (not ok_abs),
        "note": "consumer acted on the producer's absolute path; git show HEAD:<abs> failed.",
    }

    # ── ACT 1 — GATE refuses BEFORE git runs (breach prevented) ───────────────────
    def refusal_for(decision, subject_deed) -> dict:
        ref = build_refusal_certificate(decision, subject_deed=subject_deed,
                                        disclose=disclose, signer=signer)
        from bulla.recourse_gate import verify_refusal_certificate
        return {"deficiency": ref["deficiency"], "refusal_content_hash": ref["refusal_content_hash"],
                "signed": ref["signature"] is not None,
                "recomputable": verify_refusal_certificate(ref),
                "cure_disclose": ref["cure"]["disclose"]}

    # 1a — no deed presented at all
    da = evaluate_gate(deed_rec={}, inclusion_rec=None, certificate=None,
                       is_remote=False, policy=STRICT_GATE_POLICY)
    # (the proxy maps "no attestation" to MISSING; at the library level a bare call with no
    #  deed is OMITTED — both are "present a deed", and both REFUSE before git. We report
    #  the library deficiency and confirm git is never invoked.)
    rec_ob = _deed_rec(cert_obstructed)

    # 1b — logged under a HOST-ASSERTED root (an adversarial operator), nothing pinned
    from bulla.http_registry import HttpRegistry, make_server
    srv = make_server(log)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        remote = HttpRegistry(f"http://{srv.server_address[0]}:{srv.server_address[1]}")
        incl_remote = remote.inclusion_by_attestation(rec_ob["attestation_hash"])
        db = evaluate_gate(deed_rec=rec_ob, inclusion_rec=incl_remote, certificate=cert_obstructed,
                           trusted_root=None, is_remote=True, policy=STRICT_GATE_POLICY)
    finally:
        srv.shutdown()

    # 1c — a deed that certifies fee = 1 (the seam BEFORE disclosure), own-log, fully logged
    incl_ob = log.inclusion_by_attestation(rec_ob["attestation_hash"])
    dc = evaluate_gate(deed_rec=rec_ob, inclusion_rec=incl_ob, certificate=cert_obstructed,
                       is_remote=False, policy=STRICT_GATE_POLICY)

    refused = [da, db, dc]
    acts["act1_gate_refuses"] = {
        "git_invoked": False,           # git_show is never called in this act -> breach prevented
        "all_refused": all(not d.proceed for d in refused),
        "cases": {
            "1a_no_deed": {"deficiency": da.deficiency, **refusal_for(da, {})},
            "1b_host_asserted_root": {"deficiency": db.deficiency, "root_trust": db.root_trust,
                                      **refusal_for(db, rec_ob)},
            "1c_fee_positive": {"deficiency": dc.deficiency, "fee": dc.fee,
                                "included": dc.included, **refusal_for(dc, rec_ob)},
        },
        "note": "each refusal is a signed, recomputable certificate naming the cure; "
                "git show is never invoked, so the breach is prevented by construction.",
    }

    # ── ACT 2 — CURE -> PROCEED -> success ────────────────────────────────────────
    rec_cured = _deed_rec(cert_cured)
    incl_cured = log.inclusion_by_attestation(rec_cured["attestation_hash"])
    proceed = evaluate_gate(deed_rec=rec_cured, inclusion_rec=incl_cured, certificate=cert_cured,
                            is_remote=False, policy=STRICT_GATE_POLICY)   # own-log, fee = 0
    # only NOW does the consumer act — and the SAME path_root disclosure lets transport fix it
    ok_rel, detail_rel = (False, "not reached")
    if proceed.proceed:
        ok_rel, detail_rel = git_show(transport(abs_path))     # abs -> repo-relative
    acts["act2_cure_proceed"] = {
        "cured": True, "fee_before": fee_before, "fee_after": fee_after,
        "disclosed": list(disclose),
        "gate": proceed.disposition, "proceeded": proceed.proceed, "root_trust": proceed.root_trust,
        "git_invoked": proceed.proceed, "git_ok": ok_rel, "git_detail": _portable(detail_rel),
        "note": "disclosing path_root cleared the fee 1->0 AND let transport rewrite "
                "abs->rel; the gate proceeded and git show HEAD:<rel> succeeded.",
    }

    loop_closed = (
        acts["act0_no_gate"]["breach"] is True                 # the loss is real
        and acts["act1_gate_refuses"]["all_refused"] is True   # all three refusals fired
        and acts["act1_gate_refuses"]["git_invoked"] is False  # before git ran
        and acts["act2_cure_proceed"]["proceeded"] is True     # cure -> proceed
        and acts["act2_cure_proceed"]["git_ok"] is True        # and git really succeeded
    )

    out = {
        "experiment": "recourse_gate_closes_loop_git",
        "seam": "filesystem(abs) -> git(repo-relative); local git stands in for github",
        "provenance": "EXECUTION_INDEPENDENT (labels = real `git show` exit codes, fee-independent)",
        "fee_before": fee_before, "fee_after": fee_after, "disclosure": list(disclose),
        "acts": acts,
        "loop_closed": loop_closed,
        "causal_chain": (
            "The coherence cure and the execution fix are the SAME disclosure. The "
            "undeclared path_root is simultaneously (1) the one term the gate refuses on — "
            "the producer's deed certifies coherence_fee = 1 — and (2) exactly what "
            "transport() needs to rewrite abs->repo-relative. Disclosing it drives the fee "
            "1->0 (so a re-emitted deed satisfies the gate's fee=0 policy) and, by the same "
            "disclosure, makes `git show HEAD:<rel>` resolve. WITHOUT the gate the consumer "
            "acts on the absolute path and git fails (a real, execution-attributed breach); "
            "WITH the gate the breach is refused before git runs; the cure that satisfies "
            "the gate is identically the cure that makes git succeed. The label is git's "
            "exit code at every step — the fee only governs whether the gate lets git run."
        ),
    }

    results = Path(REPO) / "bulla" / "calibration" / "results" / "recourse_gate_closes_loop_git.json"
    results.parent.mkdir(parents=True, exist_ok=True)
    results.write_text(json.dumps(out, indent=2) + "\n")

    print(f"ACT 0 (no gate):  git show HEAD:<abs> ok={ok_abs}  -> breach={not ok_abs}")
    print(f"ACT 1 (gate):     refused 3/3 = {acts['act1_gate_refuses']['all_refused']}  "
          f"[{da.deficiency}, {db.deficiency}, {dc.deficiency}]  git_invoked=False")
    print(f"ACT 2 (cure):     fee {fee_before}->{fee_after}, proceeded={proceed.proceed}, "
          f"git show HEAD:<rel> ok={ok_rel}")
    print()
    print(f"LOOP {'CLOSED' if loop_closed else 'OPEN'}: the gate prevented a real git breach "
          f"and the cure that cleared it is the one that made git succeed.")
    print(f"artifact: {results}")
    return 0 if loop_closed else 1


if __name__ == "__main__":
    raise SystemExit(main())
