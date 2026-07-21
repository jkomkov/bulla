#!/usr/bin/env python3
"""One isolated signing role for the routed-inference local handoff demo.

The orchestrator gives this process one seed and one action. The role whitelist keeps
the harness, router, provider, and relier from signing each other's action types.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bulla.action_receipt import build_action_receipt, sign_action_receipt
from bulla.envelope import Authority, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.identity import LocalEd25519Signer


PROFILE = "bulla.routed-inference/0.1-draft"
REMEDY_REF = "remedy://routed-inference-v0.1"
ROLE_ACTIONS = {
    "harness": {"inference.order"},
    "router": {"inference.route"},
    "provider": {"inference.accept", "inference.delivery"},
    "relier": {"bulla.rely"},
}


def _envelope(signer: LocalEd25519Signer) -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(
            principal=signer.verification_method,
            policy="policy://routed-inference@sha256:local-demo",
        ),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(
                log_endpoint="https://witness.example",
                trusted_root_ref="local-demo:independently-pinned-root",
            ),
            remedies=(
                Remedy("recompute", "python3 check.py", "hashes.content"),
                Remedy("escalate", "named routed-inference forum", REMEDY_REF),
            ),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))

    role = request.get("role")
    action = request.get("action")
    if role not in ROLE_ACTIONS or not isinstance(action, dict):
        raise SystemExit("invalid role or action")
    if action.get("profile") != PROFILE or action.get("type") not in ROLE_ACTIONS[role]:
        raise SystemExit(f"role {role!r} cannot sign {action.get('type')!r}")

    seed = bytes.fromhex(request["seed_hex"])
    if len(seed) != 32:
        raise SystemExit("seed must be exactly 32 bytes")
    signer = LocalEd25519Signer(seed=seed)
    receipt = build_action_receipt(
        action=action,
        diagnostic_ref=request.get("diagnostic_ref", {"status": "not_applicable"}),
        envelope=_envelope(signer),
        evidence_refs=tuple(request.get("evidence_refs", [])),
        timestamp=request["timestamp"],
        producer={"bulla_version": "0.44.0", "demo": PROFILE, "role": role},
    )
    signed = sign_action_receipt(receipt, signer).to_dict()
    args.output.write_text(json.dumps(signed, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
