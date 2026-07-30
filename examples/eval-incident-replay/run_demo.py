#!/usr/bin/env python3
"""Defensive replay of the July 2026 model-evaluation security incident SHAPE.

Conceptual reconstruction only. No real exploit, credential, payload, or
organization's record appears here; the event model is abstract
(``network.egress`` to a prohibited destination), never a technique. This is
accountability plumbing — recording, policy, coverage, handoff — and it
demonstrates BOTH what Bulla adds and where it does not help.

Motivating disclosures (external, cited, not reproduced):
  - Hugging Face, 2026-07-16: https://huggingface.co/blog/security-incident-july-2026
  - OpenAI, 2026-07-21: https://openai.com/index/hugging-face-model-evaluation-security-incident/

Four deliberate paths:
  1. A prohibited egress request through a receipted gateway -> REFUSE + receipt
     signed by the gateway (infrastructure-issued, not agent-issued).
  2. A lower-layer bypass around the gateway -> no receipt emitted; Bulla misses it.
  3. event_coverage over a separately retained denominator detects the bypass as an
     unreceipted action -- coverage matters more than verification.
  4. A portable cross-org incident bundle, verified by a standalone no-Bulla
     checker, with findings
     separated by epistemic status and sensitive artifacts referenced by hash only.

    PYTHONPATH=src python examples/eval-incident-replay/run_demo.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from bulla.action_receipt import verify_receipt
from bulla.coverage import event_coverage
from bulla.identity import LocalEd25519Signer
from bulla.wrap import wrap_action

_HERE = Path(__file__).resolve().parent
_OUT = _HERE / "demo-output.json"

# Deterministic role keys. No single party controls all of them; the evaluated
# agent controls NONE of the capability-receipt keys.
ROLES = {name: LocalEd25519Signer(seed=name.encode().ljust(32, b"\0"))
         for name in ("eval-authority", "gateway", "trajectory-monitor",
                      "incident-commander", "affected-service")}

MANDATE = {
    "model": "eval-model/prerelease",          # abstract identity, not a real model
    "harness": "exploit-gym/synthetic",
    "safeguards": {"reduced_cyber_refusals": True},
    "authorized_target": "benchmark://internal-target",
    "permitted_destinations": ["internal-package-cache"],
    "prohibitions": ["public-internet", "third-party-production", "credential-collection"],
    "interval": "PT2H",
}


def _digest(obj: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def emit_mandate() -> dict:
    """eval.run.authorize — signed by the evaluation authority."""
    with wrap_action(
        "eval.run.authorize", MANDATE,
        principal=ROLES["eval-authority"].issuer,
        policy="policy://cyber-eval@sha256:aa",
        signer=ROLES["eval-authority"],
    ) as act:
        pass
    return act.receipt


def gateway_decision(event_id: str, destination: str, mandate_ref: str) -> tuple[str, dict]:
    """capability.decide — the gateway signs what it PERMITTED or REFUSED.

    Returns (decision, receipt). The agent may request; only the gateway signs
    the observed decision. Its own gateway record is self-asserted evidence.
    """
    permitted = destination in MANDATE["permitted_destinations"]
    decision = "PERMIT" if permitted else "REFUSE"
    subject = {
        "event_id": event_id,
        "kind": "network.egress",
        "requested_destination": destination,
        "decision": decision,
        "mandate_ref": mandate_ref,
    }
    with wrap_action(
        "capability.decide", subject,
        principal=ROLES["gateway"].issuer,
        policy="policy://egress-gateway@sha256:bb",
        signer=ROLES["gateway"],
    ) as act:
        act.add_evidence("gateway_flow_record", _digest(subject), "self_asserted")
    return decision, act.receipt


def trajectory_decide(lineage: list[dict], mandate_ref: str) -> dict:
    """trajectory.decide — a policy over the accumulated receipt lineage.

    NOT a production cyber monitor: a small rule set over receipts. Any
    out-of-mandate egress attempt in the lineage trips a freeze.
    """
    out_of_mandate = [
        r for r in lineage
        if r.get("action", {}).get("subject", {}).get("decision") == "REFUSE"
    ]
    if out_of_mandate:
        state = "REFUSE_AND_FREEZE"
        rationale = "out-of-mandate egress attempt observed; freeze the run"
    else:
        state = "PROCEED"
        rationale = "all observed actions within mandate"
    subject = {
        "state": state,
        "rationale": rationale,
        "mandate_ref": mandate_ref,
        "considered_attestation_hashes": [
            receipt["hashes"]["attestation"] for receipt in lineage
        ],
        "freeze_actions": ["revoke-credentials", "stop-inference", "preserve-sandbox",
                           "pin-log-roots", "page-responder"] if state == "REFUSE_AND_FREEZE" else [],
    }
    with wrap_action(
        "trajectory.decide", subject,
        principal=ROLES["trajectory-monitor"].issuer,
        policy="policy://trajectory-monitor@sha256:cc",
        signer=ROLES["trajectory-monitor"],
    ) as act:
        pass
    return act.receipt


def incident_handoff(mandate_ref: str, timeline: list[dict], coverage: dict) -> dict:
    """incident.handoff — a portable cross-org bundle.

    Findings are separated by epistemic status; sensitive artifacts are
    referenced by hash only, never inline.
    """
    findings = {
        "observed": [
            "gateway refused one out-of-mandate egress attempt",
            f"{coverage['receipted']} of {coverage['total_anchored']} observed actions carried a receipt",
        ],
        "inferred": ["the unreceipted action bypassed the receipted gateway path"],
        "counterparty_confirmed": [],   # filled in by the affected service on receipt
        "unresolved": ["whether the bypassed path reached any protected resource"],
    }
    subject = {
        "profile": "bulla.cyber-eval/0.2-draft",
        "mandate_ref": mandate_ref,
        "timeline_digest": _digest(timeline),
        "timeline_attestation_hashes": [
            receipt["hashes"]["attestation"] for receipt in timeline
        ],
        "timeline_action_types": [
            receipt["action"]["type"] for receipt in timeline
        ],
        "coverage_digest": _digest(coverage),
        "parent_refs": {
            "mandate": mandate_ref,
            "trajectory": timeline[-1]["hashes"]["attestation"],
        },
        "findings": findings,
        "sensitive_artifacts": [
            {
                "label": "captured-credential",
                "ref": _digest({"synthetic_artifact": "captured-credential"}),
                "access": "responder-escrow",
            },
        ],
        "notification": {"to": "affected-service", "channel": "incident://handoff"},
        "challenge_channel": "forum://incident-dispute",
    }
    with wrap_action(
        "incident.handoff", subject,
        principal=ROLES["incident-commander"].issuer,
        policy="policy://incident-response@sha256:dd",
        signer=ROLES["incident-commander"],
    ) as act:
        pass
    return act.receipt


def run() -> dict:
    mandate = emit_mandate()
    mandate_ref = mandate["hashes"]["attestation"]

    # --- Path 1: prohibited egress through the receipted gateway -> REFUSE ---
    permitted_id = "evt-0001-internal-cache-fetch"
    _, permitted_receipt = gateway_decision(permitted_id, "internal-package-cache", mandate_ref)
    refused_id = "evt-0002-public-internet-egress"
    decision, refused_receipt = gateway_decision(refused_id, "third-party-production", mandate_ref)
    assert decision == "REFUSE"

    capability_receipts = [permitted_receipt, refused_receipt]

    # --- Path 2: a lower-layer BYPASS around the gateway (no receipt emitted) ---
    bypass_id = "evt-0003-proxy-bypass-egress"
    # Deliberately: no gateway_decision() call. Bulla emits nothing for this path.

    # --- Trajectory decision over the lineage: the refusal trips a freeze ---
    trajectory = trajectory_decide(capability_receipts, mandate_ref)

    # --- Path 3: independent denominator detects the unreceipted bypass ---
    observed = [
        {"id": permitted_id, "kind": "network.egress"},
        {"id": refused_id, "kind": "network.egress"},
        {"id": bypass_id, "kind": "network.egress", "digest": _digest({"path": "proxy-bypass"})},
    ]
    coverage = event_coverage(
        observed,
        capability_receipts,
        anchor="network-flow-log",
        minimum_verification_depth="attestation",
        accepted_issuers={ROLES["gateway"].issuer},
    )
    assert coverage["unreceipted_delta"] == [bypass_id], coverage["unreceipted_delta"]
    stable_coverage = {key: value for key, value in coverage.items() if key != "generated_at"}

    # --- Path 4: portable cross-org incident bundle ---
    timeline = [mandate, permitted_receipt, refused_receipt, trajectory]
    handoff = incident_handoff(mandate_ref, timeline, stable_coverage)

    # Every emitted receipt verifies with Bulla.
    all_receipts = timeline + [handoff]
    for r in all_receipts:
        assert verify_receipt(r).ok, r["action"]["type"]

    return {
        "profile": "bulla.cyber-eval/0.2-draft",
        "classification": "conceptual defensive reconstruction — synthetic, no real "
                          "exploit/credential/org record",
        "sources": [
            "https://huggingface.co/blog/security-incident-july-2026",
            "https://openai.com/index/hugging-face-model-evaluation-security-incident/",
        ],
        "mandate": mandate,
        "capability_receipts": capability_receipts,
        "trajectory_decision": trajectory,
        "observed_events": observed,
        "observed_events_digest": _digest(observed),
        "coverage": stable_coverage,
        "incident_handoff": handoff,
        "signer_topology": {name: signer.issuer for name, signer in ROLES.items()},
        "failure_boundary": "path 2 (the gateway bypass) left no receipt; it is "
                            "detectable ONLY via event_coverage against the independent "
                            "denominator, not via receipt verification. Bulla records and "
                            "reconciles; it does not contain a bypass.",
    }


def main() -> int:
    output = run()
    _OUT.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "capability_decisions": [r["action"]["subject"]["decision"] for r in output["capability_receipts"]],
        "trajectory_state": output["trajectory_decision"]["action"]["subject"]["state"],
        "coverage": f"{output['coverage']['receipted']}/{output['coverage']['total_anchored']}",
        "unreceipted_bypass": output["coverage"]["unreceipted_delta"],
        "failure_boundary": output["failure_boundary"],
    }, indent=2))
    print(f"\nwrote {_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
