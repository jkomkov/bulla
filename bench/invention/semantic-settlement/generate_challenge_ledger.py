#!/usr/bin/env python3
"""Generate deterministic signed hostile-review challenge receipts."""

from __future__ import annotations

import json
from pathlib import Path

from bulla.experimental.frsl import canonical_hash
from bulla.experimental.pilot import (
    ChallengeReceipt,
    PilotAction,
    sign_pilot_artifact,
)
from bulla.identity import LocalEd25519Signer


HERE = Path(__file__).resolve().parent
TIME = "2026-07-18T00:00:00Z"


def main():
    challenger = LocalEd25519Signer(seed=bytes([91]) + bytes(31))
    reviewer = LocalEd25519Signer(seed=bytes([92]) + bytes(31))
    findings = (
        ("authority-expansion", "revision signed by operative rather than supersession authority", "ACCEPTED", "guard rejected before mutation"),
        ("single-witness", "revision carries one inclusion or two aliases", "ACCEPTED", "guard requires two distinct configured operators"),
        ("closure-laundering", "OPEN_WORLD term attempts FINALIZE", "ACCEPTED", "controller routed instead of finalizing"),
        ("reserve-increase", "same-epoch refinement increases required reserve", "ACCEPTED", "release verifier rejected non-antitone transition"),
        ("analysis-leak", "12-seam operational slice attempts efficacy analysis", "ACCEPTED", "300-case gate remained refused"),
    )
    receipts = []
    for finding_id, claim, status, reason in findings:
        challenge = sign_pilot_artifact(
            PilotAction.CHALLENGE,
            {"finding_id": finding_id, "claim": claim, "evidence_hash": canonical_hash({"test": finding_id})},
            signer=challenger, issued_at=TIME,
        )
        disposition = sign_pilot_artifact(
            PilotAction.CHALLENGE_DISPOSE,
            {
                "challenge_hash": challenge.artifact_hash, "status": status,
                "reason": reason, "bounty_reference": f"bounty://internal/{finding_id}",
            },
            signer=reviewer, issued_at=TIME,
        )
        receipt = ChallengeReceipt(challenge, disposition)
        receipts.append({**receipt.to_dict(), "receipt_hash": receipt.receipt_hash})
    payload = {
        "schema_version": "0.1-semantic-settlement-hostile-challenges",
        "frozen_at": TIME, "scope": "internal hostile review; no external bounty payment",
        "stake": None, "challenge_market": "not-implemented", "receipts": receipts,
    }
    payload["ledger_hash"] = canonical_hash(payload)
    (HERE / "hostile-challenge-ledger.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"receipts": len(receipts), "ledger_hash": payload["ledger_hash"]}, indent=2))


if __name__ == "__main__":
    main()
