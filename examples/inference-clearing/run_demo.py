#!/usr/bin/env python3
"""Execute live role processes, materialize their receipts, then verify offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
BULLA = HERE.parents[1]
SOURCE = BULLA / "src"
SPEC = BULLA / "spec" / "inference-clearing"
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(HERE))

from bulla.experimental.inference_clearing import (  # noqa: E402
    InferenceClearingContext,
    canonical_hash,
    component_hashes,
    verify_inference_clearing_bundle,
)
from bulla.identity import verify_proof_domain  # noqa: E402

from closed_task import (  # noqa: E402
    ROLE_NAMES,
    external_context,
    hash_bytes,
    receipt_ref,
    role_issuer_name,
    settlement_record,
    signer,
)


PROFILE = "bulla.inference-runtime-message/0.2-experimental"
SCENARIOS = ("opaque", "recheckable", "bypass")
INPUT = [12, -4, 7, 3, -8, 5, 2, 9]
FAILURE_STAGE = {
    "after-provider-response-before-receiver": "provider",
    "after-receiver-before-witness": "receiver-delivery",
    "before-coverage-reconciliation": "witness-decision",
}
RECEIPT_PATHS = {
    "inference.order": "receipts/01-order.json",
    "inference.route": "receipts/02-route.json",
    "inference.accept": "receipts/03-accept.json",
    "inference.delivery": "receipts/04-delivery.json",
    "assurance.collateral.bind": "receipts/05-capital-bind.json",
    "inference.coverage.checkpoint": "receipts/06-coverage-checkpoint.json",
    "bulla.rely": "receipts/07-rely.json",
    "assurance.settlement.authorize": "receipts/08-settlement-authorize.json",
    "rail.settlement.report": "receipts/09-settlement-report.json",
}


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _artifact(path: str, raw: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "media_type": "application/json" if path.endswith(".json") else "application/octet-stream",
        "byte_length": len(raw),
        "sha256": hash_bytes(raw),
    }


def _port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


def _outer_signer(role: str) -> str:
    return {
        "provider-opaque": "provider_opaque",
        "provider-reproducible": "provider_reproducible",
        "receiver-delivery": "receiver",
        "receiver-coverage": "receiver",
        "witness-decision": "witness",
        "witness-final": "witness",
        "settlement-authority": "settlement_authority",
        "rail-capital": "rail_observer",
        "rail-settlement": "rail_observer",
    }.get(role, role_issuer_name(role))


def _call_role(role: str, scenario: str, prior: str | None, facts: dict[str, Any]) -> dict[str, Any]:
    port = _port()
    process = subprocess.Popen(
        [sys.executable, str(HERE / "role_server.py"), "--role", role, "--port", str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    payload = {
        "profile": PROFILE,
        "role": role,
        "scenario": scenario,
        "input": INPUT,
        "prior_commitment": prior,
        "facts": facts,
    }
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/runtime-step",
        data=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    envelope: dict[str, Any] | None = None
    last_error: Exception | None = None
    startup_deadline = time.monotonic() + 10
    while time.monotonic() < startup_deadline:
        try:
            with urllib.request.urlopen(request, timeout=2) as opened:  # noqa: S310 - fixed loopback
                envelope = json.loads(opened.read().decode("utf-8"))
            break
        except Exception as exc:
            last_error = exc
            if process.poll() is not None:
                break
            time.sleep(0.025)
    stdout, stderr = process.communicate(timeout=10)
    if process.returncode != 0 or envelope is None:
        raise RuntimeError(f"role {role} failed: {last_error}; stdout={stdout!r}; stderr={stderr!r}")
    if set(envelope) != {"response", "commitment", "proof"} or envelope["response"].get("prior_commitment") != prior:
        raise RuntimeError(f"role {role} returned a malformed or out-of-order envelope")
    commitment = canonical_hash(envelope["response"])
    proof = verify_proof_domain("content", commitment, envelope["proof"], schema="0.4")
    if (
        envelope["commitment"] != commitment
        or not proof.authentic
        or envelope["proof"]["issuer"] != signer(_outer_signer(role)).issuer
    ):
        raise RuntimeError(f"role {role} response signature failed")
    return envelope


def _failure(out: Path, injection: str, responses: list[dict[str, Any]]) -> int:
    failure = {
        "profile": "bulla.inference-clearing-failure/0.2-experimental",
        "injection": injection,
        "completed_stages": [item["response"]["role"] for item in responses],
        "signed_role_responses": responses,
        "payment_eligibility": "NOT_COMPUTED",
        "settlement_authorization": "NOT_ISSUED",
        "settlement_execution": "NOT_ATTEMPTED",
        "limitations": ["the stopped transaction produced no clearing bundle"],
    }
    raw = _json(failure)
    (out / "failure-report.json").write_bytes(raw)
    print(json.dumps({
        "output": str(out),
        "injection": injection,
        "payment_eligibility": "NOT_COMPUTED",
        "failure_report_sha256": hash_bytes(raw),
    }, indent=2, sort_keys=True))
    return 0


def _receipt_entry(action_type: str, role: str, receipt: dict[str, Any]) -> dict[str, Any]:
    return {"action_type": action_type, "role": role, "receipt": receipt}


def _materialize_scenario(
    scenario: str,
    out: Path,
    responses: list[dict[str, Any]],
    injection: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    chain: list[dict[str, Any]] = []

    def step(role: str, facts: dict[str, Any]) -> dict[str, Any]:
        prior = chain[-1]["commitment"] if chain else None
        envelope = _call_role(role, scenario, prior, facts)
        chain.append(envelope)
        responses.append(envelope)
        return envelope["response"]["result"]

    buyer = step("buyer", {})
    router = step("router", {"order": buyer["receipt"]})
    provider_name = "provider-opaque" if scenario == "opaque" else "provider-reproducible"
    provider = step(provider_name, {"route": router["receipt"]})
    if injection and FAILURE_STAGE[injection] == "provider":
        return chain, None
    delivery = step("receiver-delivery", {
        "accept": provider["receipt"],
        "execution": provider["execution"],
        "output_bytes": provider["output_bytes"],
    })
    if injection and FAILURE_STAGE[injection] == "receiver-delivery":
        return chain, None
    capital = step("rail-capital", {"delivery": delivery["receipt"]})
    coverage = step("receiver-coverage", {"capital": capital["receipt"]})

    receipt_entries = [
        _receipt_entry("inference.order", "buyer", buyer["receipt"]),
        _receipt_entry("inference.route", "router", router["receipt"]),
        _receipt_entry("inference.accept", "provider", provider["receipt"]),
        _receipt_entry("inference.delivery", "receiver", delivery["receipt"]),
        _receipt_entry("assurance.collateral.bind", "rail_observer", capital["receipt"]),
        _receipt_entry("inference.coverage.checkpoint", "receiver", coverage["receipt"]),
    ]
    decision_witness = step("witness-decision", {
        "receipts": [entry["receipt"] for entry in receipt_entries],
    })
    if injection and FAILURE_STAGE[injection] == "witness-decision":
        return chain, None

    decision_context = external_context(decision_witness["checkpoint"]["root"])
    reliance = step("relier", {
        "term": buyer["term"],
        "execution": provider["execution"],
        "model": provider["model"],
        "output_bytes": provider["output_bytes"],
        "coverage": coverage["coverage"],
        "capital": capital["capital"],
        "rail_evidence": capital["rail_evidence"],
        "checkpoint": decision_witness["checkpoint"],
        "inclusions": decision_witness["inclusions"],
        "context": decision_context,
        "receipts": receipt_entries,
    })
    receipt_entries.append(_receipt_entry("bulla.rely", "relier", reliance["receipt"]))

    authorization: dict[str, Any] | None = None
    rail = step("rail-settlement", {
        "rely": reliance["receipt"],
        "authorization": authorization["receipt"] if authorization else None,
        "rail_evidence": capital["rail_evidence"],
    })
    if rail["receipt"] is not None:
        receipt_entries.append(_receipt_entry(
            "rail.settlement.report", "rail_observer", rail["receipt"]
        ))

    final_witness = step("witness-final", {
        "receipts": [entry["receipt"] for entry in receipt_entries],
        "decision_checkpoint": decision_witness["checkpoint"],
    })

    files: dict[str, bytes] = {
        "terms/term-document.json": _json(buyer["term"]),
        "execution/output.bin": bytes(provider["output_bytes"]),
        "execution/report.json": _json(provider["execution"]),
        "receiver/coverage.json": _json(coverage["coverage"]),
        "witness/decision-checkpoint.json": _json(decision_witness["checkpoint"]),
        "witness/decision-inclusions.json": _json(decision_witness["inclusions"]),
        "witness/final-checkpoint.json": _json(final_witness["checkpoint"]),
        "witness/final-inclusions.json": _json(final_witness["inclusions"]),
        "assurance/capital.json": _json(capital["capital"]),
        "settlement/rail-evidence.json": _json(capital["rail_evidence"]),
        "settlement/report.json": _json(rail["settlement"]),
    }
    if provider["model"] is not None:
        files["execution/model.json"] = _json(provider["model"])
    ordered = []
    for entry in receipt_entries:
        path = RECEIPT_PATHS[entry["action_type"]]
        files[path] = _json(entry["receipt"])
        ordered.append({
            "action_type": entry["action_type"],
            "role": entry["role"],
            "path": path,
            **receipt_ref(entry["receipt"]),
        })
    artifacts = [_artifact(path, files[path]) for path in sorted(files)]
    core = {
        "profile": "bulla.inference-clearing/0.1-experimental",
        "transaction_id": buyer["term"]["transaction_id"],
        "revision": 1,
        "provider_kind": "OPAQUE" if scenario == "opaque" else "REPRODUCIBLE",
        "comparison_group": buyer["term"]["comparison_group"],
        "role_issuers": {role: signer(role).issuer for role in ROLE_NAMES},
        "term_root": canonical_hash(buyer["term"]),
        "ordered_receipts": ordered,
        "artifacts": artifacts,
    }
    publisher = step("publisher", {"core": core, "component_hashes": component_hashes(core)})
    files["clearing-core.json"] = _json(core)
    files["publish-receipt.json"] = _json(publisher["receipt"])
    bundle = out / scenario
    for path, raw in files.items():
        target = bundle / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    context_value = external_context(
        decision_witness["checkpoint"]["root"], final_witness["checkpoint"]["root"]
    )
    return chain, context_value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--story", action="store_true")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--inject-failure", choices=tuple(FAILURE_STAGE))
    args = parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit("--out must be absent or empty")
    args.out.mkdir(parents=True, exist_ok=True)

    responses: list[dict[str, Any]] = []
    transcripts: dict[str, list[dict[str, Any]]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    scenarios = ("recheckable",) if args.inject_failure else SCENARIOS
    for scenario in scenarios:
        chain, context_value = _materialize_scenario(
            scenario, args.out, responses, args.inject_failure
        )
        if context_value is None:
            return _failure(args.out, args.inject_failure or "unknown", responses)
        transcripts[scenario] = chain
        contexts[scenario] = context_value
        context_path = args.out / "contexts" / f"{scenario}.json"
        context_path.parent.mkdir(parents=True, exist_ok=True)
        context_path.write_bytes(_json(context_value))

    reports: dict[str, Any] = {}
    for scenario in SCENARIOS:
        context = InferenceClearingContext.from_dict(contexts[scenario])
        reports[scenario] = verify_inference_clearing_bundle(args.out / scenario, context).to_dict()

    runtime = {
        "profile": "bulla.inference-clearing-runtime/0.2-experimental",
        "boundary": "LOCALHOST_PROCESS_SEPARATED_TEAM_CONTROLLED",
        "providers_terminated_before_verification": True,
        "retained_action_receipts_emitted_by_role_processes": True,
        "relying_process_recomputed_from_retained_evidence": True,
        "signed_stage_transcripts": transcripts,
        "reports": reports,
        "limitations": [
            "process separation does not establish organizational independence",
            "fixture execution does not establish production settlement",
        ],
    }
    raw = _json(runtime)
    (args.out / "runtime-report.json").write_bytes(raw)
    summary = {
        "output": str(args.out),
        "runtime_report_sha256": hash_bytes(raw),
        "opaque": reports["opaque"]["payment_eligibility"],
        "recheckable": reports["recheckable"]["payment_eligibility"],
        "bypass": reports["bypass"]["payment_eligibility"],
        "providers_terminated_before_verification": True,
    }
    if args.format == "json" or not args.story:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("Same answer. Different evidence.\n")
        print("Order       fixed telemetry routing → BACKUP")
        print("Providers   disconnected before offline verification")
        print("Opaque      model unavailable · relation unavailable · REFUSE · INELIGIBLE")
        print("Recheckable term-bound model · relation reproduced · RELY · ELIGIBLE")
        print("Settlement  authorization NOT_ISSUED · execution NOT_ATTEMPTED")
        print("Historical provider execution NOT_ESTABLISHED for both providers")
        print("Bypass      receipts VERIFIED · coverage 1/2 · REFUSE · INELIGIBLE")
        print(f"\nRetained bundle: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
