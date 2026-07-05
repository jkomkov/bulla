#!/usr/bin/env python3
"""LOOP CLOSED on a REAL ticketing intake (sqlite3) — the notification-family entry.

Pattern of ``recourse_gate_closes_loop_git``/``_tar``: a scheduler agent emits a
ticket whose ``due`` field is EPOCH SECONDS (its internal convention); the
ticketing system's intake is a real sqlite3 database whose schema enforces
ISO-8601 (`CHECK (due GLOB '????-??-??T??:??:??')`). The timestamp-format
convention is hidden -> coherence fee = 1. The label is sqlite3's own exit code
at every step — EXECUTION_INDEPENDENT; the fee only governs whether the gate
lets the INSERT run.

Three acts:
  0  NO GATE (the loss).       The intake INSERTs the producer's epoch value;
                               the CHECK constraint fails for real.
  1  GATE refuses (prevented). No deed / fee=1 deed -> signed, contestable
                               refusals BEFORE any INSERT runs.
  2  CURE -> PROCEED -> exit 0. Disclosing ``timestamp_format`` (the SAME
                               minimum_disclosure_set the refusal named) clears
                               the fee 1 -> 0 AND lets transport() rewrite
                               epoch -> ISO; the INSERT succeeds and the row
                               reads back.

CAUSAL CHAIN: the coherence cure and the execution fix are the SAME disclosure.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
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
    """scheduler -> ticketing with `timestamp_format` HIDDEN on both sides."""
    sched = ToolSpec("scheduler", ("timestamp_format",), ())
    tick = ToolSpec("ticketing", ("timestamp_format",), ())
    edge = Edge("scheduler", "ticketing",
                (SemanticDimension("timestamp_format", "timestamp_format", "timestamp_format"),))
    return Composition("sched_to_ticket_seam", (sched, tick), (edge,))


def disclosed_composition() -> Composition:
    sched = ToolSpec("scheduler", ("timestamp_format",), ("timestamp_format",))
    tick = ToolSpec("ticketing", ("timestamp_format",), ("timestamp_format",))
    edge = Edge("scheduler", "ticketing",
                (SemanticDimension("timestamp_format", "timestamp_format", "timestamp_format"),))
    return Composition("sched_to_ticket_disclosed", (sched, tick), (edge,))


def sqlite_insert(db: Path, due_value: str) -> tuple[bool, str]:
    """The REAL executor: sqlite3's exit code, never bulla's fee."""
    r = subprocess.run(
        ["sqlite3", str(db),
         f"INSERT INTO tickets (title, due) VALUES ('renewal', '{due_value}');"],
        capture_output=True, text=True,
    )
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def transport(epoch_seconds: str) -> str:
    """The convention bridge `timestamp_format` unlocks: epoch -> ISO-8601."""
    dt = datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def main() -> int:
    seam, cured = seam_composition(), disclosed_composition()
    fee_before = diagnose(seam).coherence_fee
    fee_after = diagnose(cured).coherence_fee
    disclose = tuple(field for (_t, field) in minimum_disclosure_set(seam))
    if not (fee_before == 1 and fee_after == 0):
        print(f"INVALID CONTROL: expected fee 1->0, got {fee_before}->{fee_after}")
        return 2

    tmp = Path(tempfile.mkdtemp())
    db = tmp / "tickets.db"
    schema = ("CREATE TABLE tickets (id INTEGER PRIMARY KEY, title TEXT NOT NULL, "
              "due TEXT NOT NULL CHECK (due GLOB '????-??-??T??:??:??'));")
    r = subprocess.run(["sqlite3", str(db), schema], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"INVALID CONTROL: schema create failed: {r.stderr}")
        return 2

    epoch_due = "1783468800"  # what the scheduler emits (its hidden convention)

    signer = LocalEd25519Signer(seed=bytes(range(32)))
    log = DeedLog(path=str(tmp / "relying-party.jsonl"))
    cert_obstructed = to_dict(sign_certificate(certify(seam), signer))
    log.append(Deed.from_certificate(cert_obstructed, public_key=signer.public_key))
    cert_cured = to_dict(sign_certificate(certify(cured), signer))
    log.append(Deed.from_certificate(cert_cured, public_key=signer.public_key))

    acts: dict = {}

    # ── ACT 0 — NO GATE ──────────────────────────────────────────────────
    ok0, detail0 = sqlite_insert(db, epoch_due)
    acts["act0_no_gate"] = {
        "gate": False, "insert_invoked": True, "insert_ok": ok0,
        "insert_detail": detail0, "breach": (not ok0),
        "note": "intake INSERTed the scheduler's epoch value; the CHECK constraint failed.",
    }

    # ── ACT 1 — GATE refuses BEFORE any INSERT ───────────────────────────
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
        "gate": True, "insert_invoked": False, "refusals": refusals,
        "breach_prevented": bool(refusals),
    }

    # ── ACT 2 — CURE -> PROCEED -> INSERT succeeds ───────────────────────
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
    iso_due = transport(epoch_due)            # the SAME disclosure fixes execution
    ok2, detail2 = sqlite_insert(db, iso_due)
    readback = subprocess.run(
        ["sqlite3", str(db), "SELECT due FROM tickets WHERE title='renewal';"],
        capture_output=True, text=True,
    )
    acts["act2_cure_proceed"] = {
        "gate": True, "disposition": d_ok.disposition, "proceeded": proceeded,
        "disclosed": list(disclose), "transported": iso_due,
        "insert_ok": ok2, "insert_detail": detail2,
        "row_read_back": iso_due in readback.stdout,
    }

    verdict = (acts["act0_no_gate"]["breach"]
               and acts["act1_gate_refuses"]["breach_prevented"]
               and proceeded and ok2 and acts["act2_cure_proceed"]["row_read_back"])
    print(json.dumps({"family": "notification/ticketing-sqlite",
                      "fee_before": fee_before, "fee_after": fee_after,
                      "acts": acts, "LOOP_CLOSED": verdict}, indent=2))
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
