#!/usr/bin/env python3
"""Generate the deterministic, occurrence-bound v0.4 reference vector."""

from __future__ import annotations

import json
from pathlib import Path

from bulla.action_receipt import build_action_receipt_v04, sign_action_receipt_v04
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.identity import LocalEd25519Signer


HERE = Path(__file__).resolve().parent


def main() -> None:
    signer = LocalEd25519Signer(seed=bytes(range(32)))
    envelope = RecourseEnvelope(
        authority=Authority(signer.issuer, "policy://payments/v1"),
        bounds=Bounds("payments.charge:usd-micros"),
        recourse=Recourse(
            "P7D",
            Forum("https://forum.example/cases", "sha256:" + "ab" * 32),
            (Remedy("challenge", "bulla experimental challenge replay", "forum:payments"),),
        ),
        retention_class="operational",
        disclosure_class="party",
    )
    receipt = sign_action_receipt_v04(
        build_action_receipt_v04(
            action={
                "type": "payments.charge",
                "subject": {"amount_micros": 12_500_000, "currency": "USD"},
            },
            diagnostic_ref={"status": "not_applicable"},
            envelope=envelope,
            event_id="2f5fe4bd-386d-4d5a-96e8-47ca2c156f25",
            claimed_at="2026-07-21T22:00:00Z",
            evidence_refs=({
                "name": "provider-request",
                "hash": "sha256:" + "cd" * 32,
                "grounding": "counterparty_signed",
            },),
            producer={"bulla_version": "0.44.1+source"},
        ),
        signer,
    )
    path = HERE / "v04-occurrence-bound.json"
    path.write_text(json.dumps(receipt.to_dict(), indent=2, ensure_ascii=False) + "\n")
    print(path)


if __name__ == "__main__":
    main()
