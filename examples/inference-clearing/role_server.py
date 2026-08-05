#!/usr/bin/env python3
"""One-shot localhost role that emits retained Inference Clearing records."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from bulla.action_receipt import verify_receipt
from bulla.experimental.checkpoint import WitnessCheckpoint, issue_checkpoint
from bulla.experimental.inference_clearing import PROFILE as CLEARING_PROFILE, canonical_hash
from bulla.registry import Deed, DeedLog

from closed_task import (
    COMPARISON_GROUP,
    INPUT,
    capital_record,
    coverage_record,
    evaluate_pre_reliance,
    relation_evidence,
    issue_publish_receipt,
    issue_receipt,
    provider_role,
    rail_evidence,
    receipt_ref,
    role_issuer_name,
    settlement_record,
    signer,
    term_document,
)


PROFILE = "bulla.inference-runtime-message/0.2-experimental"
ROLES = {
    "buyer", "router", "provider-opaque", "provider-reproducible", "receiver-delivery",
    "receiver-coverage", "witness-decision", "witness-final", "relier",
    "settlement-authority", "rail-capital", "rail-settlement", "publisher",
}
SCENARIOS = {"opaque", "recheckable", "bypass"}


def _outer_signer_role(role: str) -> str:
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


def _strict(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate member")
            result[key] = value
        return result

    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} fields differ")
    return value


def _prior(receipt: dict[str, Any], action_type: str, issuer_role: str) -> dict[str, Any]:
    verdict = verify_receipt(receipt)
    expected_issuer = signer(issuer_role).issuer
    if (
        not verdict.ok
        or verdict.verified_to != "attestation"
        or receipt["action"]["type"] != action_type
        or receipt["signature"]["issuer"] != expected_issuer
    ):
        raise ValueError(f"prior {action_type} receipt failed verification")
    return receipt


def _common(term: dict[str, Any]) -> dict[str, Any]:
    return {"term_root": canonical_hash(term), "comparison_group": term["comparison_group"]}


def _witness(
    scenario: str,
    receipts: list[dict[str, Any]],
    *,
    previous: dict[str, Any] | None,
    phase: str,
) -> dict[str, Any]:
    if not receipts:
        raise ValueError("witness received no receipts")
    with tempfile.TemporaryDirectory(prefix=f"inference-runtime-witness-{scenario}-") as raw:
        log = DeedLog(Path(raw) / "log.jsonl")
        for receipt in receipts:
            verdict = verify_receipt(receipt)
            if not verdict.ok or verdict.verified_to != "attestation":
                raise ValueError("witness intake receipt failed verification")
            log.append(Deed(
                receipt["signature"]["issuer"],
                receipt["hashes"]["content"],
                receipt["hashes"]["attestation"],
            ))
        checkpoint = issue_checkpoint(
            log,
            signer("witness"),
            log_id=f"inference-clearing:runtime:{scenario}",
            previous=WitnessCheckpoint.from_dict(previous) if previous is not None else None,
            issued_at="2026-08-04T13:07:00Z" if phase == "decision" else "2026-08-04T13:14:00Z",
        ).to_dict()
        inclusions = []
        for index, receipt in enumerate(receipts):
            inclusion = log.inclusion(index)
            inclusion["attestation"] = receipt["hashes"]["attestation"]
            inclusions.append(inclusion)
    return {"checkpoint": checkpoint, "inclusions": inclusions}


def _result(role: str, scenario: str, prior: str | None, facts: dict[str, Any]) -> dict[str, Any]:
    term = term_document(scenario)
    common = _common(term)
    if role == "buyer":
        _exact(facts, set(), "buyer facts")
        if prior is not None:
            raise ValueError("buyer must begin the chain")
        receipt = issue_receipt(
            scenario,
            "buyer",
            "inference.order",
            {**common, "input_hash": term["input_hash"], "price": term["price"]},
            1,
        )
        return {"term": term, "receipt": receipt}

    if role == "router":
        _exact(facts, {"order"}, "router facts")
        order = _prior(facts["order"], "inference.order", "buyer")
        receipt = issue_receipt(
            scenario,
            "router",
            "inference.route",
            {
                **common,
                "parent_ref": receipt_ref(order),
                "provider_kind": "OPAQUE" if scenario == "opaque" else "REPRODUCIBLE",
            },
            2,
        )
        return {"receipt": receipt}

    if role in {"provider-opaque", "provider-reproducible"}:
        _exact(facts, {"route"}, "provider facts")
        route = _prior(facts["route"], "inference.route", "router")
        expected = "provider_opaque" if role == "provider-opaque" else "provider_reproducible"
        if expected != provider_role(scenario):
            raise ValueError("wrong provider for scenario")
        execution, model, output = relation_evidence(scenario)
        receipt = issue_receipt(
            scenario,
            expected,
            "inference.accept",
            {
                **common,
                "parent_ref": receipt_ref(route),
                "accepted_model_hash": term["expected_model_hash"],
                "execution_report_hash": canonical_hash(execution),
                "provider_process_claim": execution["provider_process_claim"],
            },
            3,
        )
        return {"execution": execution, "model": model, "output_bytes": list(output), "receipt": receipt}

    if role == "receiver-delivery":
        _exact(facts, {"accept", "execution", "output_bytes"}, "receiver delivery facts")
        accept = _prior(facts["accept"], "inference.accept", provider_role(scenario))
        output = bytes(facts["output_bytes"])
        execution = facts["execution"]
        if execution["output_hash"] != "sha256:" + hashlib.sha256(output).hexdigest():
            raise ValueError("receiver observed output differs from provider evidence")
        receipt = issue_receipt(
            scenario,
            "receiver",
            "inference.delivery",
            {
                **common,
                "parent_ref": receipt_ref(accept),
                "effect_id": "effect-mediated-001",
                "output_hash": execution["output_hash"],
                "receiver_anchor": term["coverage_anchor"],
            },
            4,
        )
        return {"receipt": receipt}

    if role == "rail-capital":
        _exact(facts, {"delivery"}, "rail capital facts")
        delivery = _prior(facts["delivery"], "inference.delivery", "receiver")
        evidence = rail_evidence(scenario)
        capital = capital_record(scenario, term["transaction_id"], evidence)
        receipt = issue_receipt(
            scenario,
            "rail_observer",
            "assurance.collateral.bind",
            {**common, "parent_ref": receipt_ref(delivery), "capital_hash": canonical_hash(capital)},
            5,
        )
        return {"rail_evidence": evidence, "capital": capital, "receipt": receipt}

    if role == "receiver-coverage":
        _exact(facts, {"capital"}, "receiver coverage facts")
        capital_receipt = _prior(facts["capital"], "assurance.collateral.bind", "rail_observer")
        coverage = coverage_record(scenario, term["transaction_id"])
        receipt = issue_receipt(
            scenario,
            "receiver",
            "inference.coverage.checkpoint",
            {
                **common,
                "parent_ref": receipt_ref(capital_receipt),
                "coverage_hash": canonical_hash(coverage),
                "anchor_id": coverage["anchor_id"],
                "denominator_count": len(coverage["denominator_ids"]),
                "receipted_count": len(coverage["receipted_ids"]),
            },
            6,
        )
        return {"coverage": coverage, "receipt": receipt}

    if role == "witness-decision":
        _exact(facts, {"receipts"}, "decision witness facts")
        return _witness(scenario, facts["receipts"], previous=None, phase="decision")

    if role == "relier":
        expected = {
            "term", "execution", "model", "output_bytes", "coverage", "capital",
            "rail_evidence", "checkpoint", "inclusions", "context", "receipts",
        }
        _exact(facts, expected, "reliance facts")
        decision, unmet = evaluate_pre_reliance(facts)
        coverage_receipt = facts["receipts"][-1]["receipt"]
        receipt = issue_receipt(
            scenario,
            "relier",
            "bulla.rely",
            {
                **common,
                "parent_ref": receipt_ref(coverage_receipt),
                "policy_hash": term["policy_hash"],
                "decision": decision,
                "unmet_requirements": unmet,
                "named_consequence": "RELEASE_PAYMENT",
                "witness_root": facts["checkpoint"]["root"],
                "coverage_hash": canonical_hash(facts["coverage"]),
                "coverage_ref": receipt_ref(coverage_receipt),
            },
            7,
        )
        return {"decision": decision, "unmet_requirements": unmet, "receipt": receipt}

    if role == "settlement-authority":
        _exact(facts, {"rely"}, "settlement authority facts")
        rely = _prior(facts["rely"], "bulla.rely", "relier")
        if rely["action"]["subject"]["decision"] != "RELY":
            raise ValueError("settlement authority cannot authorize a refused result")
        receipt = issue_receipt(
            scenario,
            "settlement_authority",
            "assurance.settlement.authorize",
            {
                **common,
                "parent_ref": receipt_ref(rely),
                "eligibility": "ELIGIBLE",
                "destination": "synthetic:provider-reproducible",
                "amount": term["price"]["amount"],
                "unit": term["price"]["unit"],
                "rail_adapter": "fixture-escrow/1",
            },
            8,
        )
        return {"receipt": receipt}

    if role == "rail-settlement":
        _exact(facts, {"rely", "authorization", "rail_evidence"}, "rail settlement facts")
        rely = _prior(facts["rely"], "bulla.rely", "relier")
        if facts["authorization"] is None:
            return {
                "settlement": settlement_record(term["transaction_id"], facts["rail_evidence"], None),
                "receipt": None,
            }
        authorization = _prior(facts["authorization"], "assurance.settlement.authorize", "settlement_authority")
        settlement = settlement_record(
            term["transaction_id"], facts["rail_evidence"], receipt_ref(authorization)
        )
        receipt = issue_receipt(
            scenario,
            "rail_observer",
            "rail.settlement.report",
            {
                **common,
                "parent_ref": receipt_ref(authorization),
                "status": "EXECUTED",
                "authorization_ref": receipt_ref(authorization),
                "amount": term["price"]["amount"],
                "unit": term["price"]["unit"],
                "rail_adapter": "fixture-escrow/1",
                "settlement_hash": canonical_hash(settlement),
            },
            9,
        )
        return {"settlement": settlement, "receipt": receipt}

    if role == "witness-final":
        _exact(facts, {"receipts", "decision_checkpoint"}, "final witness facts")
        return _witness(
            scenario,
            facts["receipts"],
            previous=facts["decision_checkpoint"],
            phase="final",
        )

    if role == "publisher":
        _exact(facts, {"core", "component_hashes"}, "publisher facts")
        core = facts["core"]
        if core["profile"] != CLEARING_PROFILE or core["transaction_id"] != term["transaction_id"]:
            raise ValueError("publisher received the wrong clearing core")
        return {"receipt": issue_publish_receipt(scenario, core, facts["component_hashes"])}

    raise ValueError("unsupported role phase")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=sorted(ROLES), required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit("port must be between 1024 and 65535")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/runtime-step" or self.client_address[0] not in {"127.0.0.1", "::1"}:
                self.send_error(404)
                return
            if self.headers.get("Content-Type") != "application/json":
                self.send_error(415)
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if length < 0 or length > 262_144:
                    raise ValueError("request length is outside bounds")
                request = _exact(
                    _strict(self.rfile.read(length)),
                    {"profile", "role", "scenario", "input", "prior_commitment", "facts"},
                    "request",
                )
                if (
                    request["profile"] != PROFILE
                    or request["role"] != args.role
                    or request["scenario"] not in SCENARIOS
                    or request["input"] != INPUT
                ):
                    raise ValueError("request is outside the closed runtime profile")
                result = _result(
                    args.role,
                    request["scenario"],
                    request["prior_commitment"],
                    _exact(request["facts"], set(request["facts"]), "facts"),
                )
                response = {
                    "profile": PROFILE,
                    "role": args.role,
                    "scenario": request["scenario"],
                    "prior_commitment": request["prior_commitment"],
                    "result": result,
                }
                commitment = canonical_hash(response)
                proof = signer(_outer_signer_role(args.role)).sign_domain(
                    "content", commitment, schema="0.4"
                )
                envelope = {"response": response, "commitment": commitment, "proof": proof}
                raw = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode()
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError, TypeError, KeyError):
                self.send_error(422)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *values: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    server.handle_request()
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
