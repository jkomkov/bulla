#!/usr/bin/env python3
"""Run the routed-inference profile as isolated local signing handoffs.

This is an offline demonstration, not a provider, witness, settlement, or network
integration. The final verifier runs in isolated Python with no Bulla import path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

from bulla.reliance import PRAGMATIC_RELIANCE_POLICY


HERE = Path(__file__).resolve().parent
BULLA = HERE.parents[1]
CHECKER = BULLA / "spec" / "routed-inference-vectors" / "check.py"
PROFILE = "bulla.routed-inference/0.1-draft"
SLOT_ID = "slot:local-handoff-demo"
REMEDY_REF = "remedy://routed-inference-v0.1"
WITNESS_REF = "witness-policy://two-independent@sha256:local-demo"
UNIT = "usd_micros"
TIMESTAMP = "2026-07-17T01:00:0{}+00:00"
ROLES = ("harness", "router", "provider", "relier")


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _h(value) -> str:
    return "sha256:" + hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


def _ref(receipt: dict) -> dict:
    return {
        "event": receipt["hashes"]["event"],
        "attestation": receipt["hashes"]["attestation"],
    }


def _terms() -> dict:
    return {
        "profile": PROFILE,
        "route_topology": "single_route_single_provider",
        "term_disclosure": "full",
        "request_ref": _h({"demo": "request"}),
        "process_constraints": {
            "permitted_providers": ["provider-a", "provider-b"],
            "permitted_models": ["hermes-4-70b"],
            "min_precision_bits": 8,
            "approved_hardware_classes": ["h100"],
            "randomness_policy": "declared-seed",
            "max_route_depth": 1,
            "resource_ceilings": {"input_tokens": 20000, "output_tokens": 4000},
        },
        "evidence_policy": {
            "minimum_process_grounding": "third_party_anchored",
            "appraisal_policy_ref": "appraisal://process-evidence-v1",
        },
        "budget_policy": {
            "mode": "disclosed_components", "unit": UNIT, "ceiling": 1000,
        },
        "deadline": {"domain": "local-demo", "value": 100},
        "witness_policy_ref": WITNESS_REF,
        "remedy_adapter_ref": REMEDY_REF,
        "forum_ref": "forum://routed-inference-review",
        "reliance_policy_ref": (
            f"{PRAGMATIC_RELIANCE_POLICY.name}@"
            f"{PRAGMATIC_RELIANCE_POLICY.policy_hash}"
        ),
    }


def _selection(provider: str) -> dict:
    return {
        "provider": provider,
        "model": "hermes-4-70b",
        "precision_bits": 8,
        "hardware_class": "h100",
        "randomness_policy": "declared-seed",
        "route_depth": 1,
    }


def _action(action_type: str, term_root: str, subject: dict, parents: list[dict]) -> dict:
    return {
        "type": action_type,
        "profile": PROFILE,
        "parents": parents,
        "slot_id": SLOT_ID,
        "term_root": term_root,
        "subject": subject,
    }


def _seed_map(fixture: bool) -> dict[str, bytes]:
    if fixture:
        return {role: bytes([31 + index]) + bytes(31) for index, role in enumerate(ROLES)}
    return {role: secrets.token_bytes(32) for role in ROLES}


def _sign(
    root: Path,
    *,
    role: str,
    seed: bytes,
    action: dict,
    index: int,
    diagnostic_ref: dict | None = None,
    evidence_refs: list[dict] | None = None,
) -> dict:
    actor_dir = root / role
    actor_dir.mkdir(exist_ok=True)
    stem = f"{index:02d}-{action['type'].replace('.', '-')}"
    request = actor_dir / f"{stem}-request.json"
    output = actor_dir / f"{stem}-receipt.json"
    request.write_text(json.dumps({
        "role": role,
        "seed_hex": seed.hex(),
        "action": action,
        "diagnostic_ref": diagnostic_ref or {"status": "not_applicable"},
        "evidence_refs": evidence_refs or [],
        "timestamp": TIMESTAMP.format(index),
    }, indent=2) + "\n", encoding="utf-8")
    env = os.environ.copy()
    source = str(BULLA / "src")
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    subprocess.run(
        [sys.executable, str(HERE / "actor.py"), str(request), str(output)],
        cwd=actor_dir, env=env, text=True, check=True,
    )
    return json.loads(output.read_text(encoding="utf-8"))


def _run(root: Path, fault: str, fixture_keys: bool) -> tuple[dict, dict]:
    terms = _terms()
    term_root = _h(terms)
    seeds = _seed_map(fixture_keys)
    selection = _selection("provider-a")
    router_ledger = (
        {"unit": UNIT, "charge_to_upstream": 1100,
         "charge_from_downstream": 700, "retained_amount": 400}
        if fault == "budget-overrun"
        else {"unit": UNIT, "charge_to_upstream": 900,
              "charge_from_downstream": 700, "retained_amount": 200}
    )
    provider_ledger = {
        "unit": UNIT, "charge_to_upstream": 700,
        "charge_from_downstream": 0, "retained_amount": 700,
    }

    order = _sign(root, role="harness", seed=seeds["harness"], index=1, action=_action(
        "inference.order", term_root, {
            "slot_id": SLOT_ID, "term_root": term_root,
            "request_ref": terms["request_ref"],
            "budget_ceiling": terms["budget_policy"]["ceiling"],
            "budget_unit": UNIT, "remedy_adapter_ref": REMEDY_REF,
            "witness_policy_ref": WITNESS_REF,
        }, [],
    ))
    route = _sign(root, role="router", seed=seeds["router"], index=2, action=_action(
        "inference.route", term_root, {
            "slot_id": SLOT_ID, "term_root": term_root,
            "selection": selection, "budget_ledger": router_ledger,
        }, [_ref(order)],
    ))
    accept = _sign(root, role="provider", seed=seeds["provider"], index=3, action=_action(
        "inference.accept", term_root, {
            "slot_id": SLOT_ID, "term_root": term_root,
            "accepted_route": _ref(route), "accepted_selection": selection,
            "remedy_adapter_ref": REMEDY_REF,
            "witness_policy_ref": WITNESS_REF,
            "budget_ledger": provider_ledger,
        }, [_ref(route)],
    ))
    delivered_selection = (
        _selection("provider-x") if fault == "provider-substitution" else selection
    )
    delivery = _sign(
        root, role="provider", seed=seeds["provider"], index=4,
        action=_action("inference.delivery", term_root, {
            "slot_id": SLOT_ID, "term_root": term_root,
            "selection": delivered_selection,
            "artifact_ref": _h({"demo": "artifact"}),
            "resource_usage": {
                "deltas": {"input_tokens": 1000, "output_tokens": 250},
                "grounding": "third_party_anchored",
            },
        }, [_ref(accept)]),
        evidence_refs=[{
            "name": "process_evidence", "hash": _h({"demo": "process"}),
            "grounding": "third_party_anchored",
        }],
    )
    relied_ref = _ref(delivery)
    diagnostic = _h({
        "relied_on": relied_ref,
        "policy": terms["reliance_policy_ref"],
        "decision": "rely",
    })
    reliance = _sign(
        root, role="relier", seed=seeds["relier"], index=5,
        action=_action("bulla.rely", term_root, {
            "relied_on": relied_ref,
            "policy": terms["reliance_policy_ref"],
            "decision": "rely",
        }, [relied_ref]),
        diagnostic_ref={"status": "reference", "ref": diagnostic},
        evidence_refs=[{
            "name": "relied_on", "hash": relied_ref["attestation"],
            "grounding": "counterparty_signed",
        }],
    )

    bundle = {
        "profile": PROFILE,
        "trace_id": f"local-handoff-{fault}",
        "term_document": terms,
        "term_root": term_root,
        "receipts": [order, route, accept, delivery, reliance],
        "witness": {"status": "not_exercised", "heads": []},
        "settlement_evidence": [],
    }
    bundle_path = root / "bundle.json"
    bundle_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")

    stranger = root / "stranger"
    stranger.mkdir()
    shutil.copy2(CHECKER, stranger / "check.py")
    shutil.copy2(bundle_path, stranger / "bundle.json")
    verified = subprocess.run(
        [sys.executable, "-I", "check.py", "verify", "bundle.json", "--json"],
        cwd=stranger, text=True, capture_output=True, check=False,
    )
    if verified.returncode not in (0, 2, 3):
        raise RuntimeError(verified.stdout + verified.stderr)
    return bundle, json.loads(verified.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fault", choices=("none", "provider-substitution", "budget-overrun"),
        default="none",
    )
    parser.add_argument("--fixture-keys", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="bulla-routed-demo-") as temp:
        bundle, report = _run(Path(temp), args.fault, args.fixture_keys)
    if args.output:
        args.output.write_text(
            json.dumps({"bundle": bundle, "report": report}, indent=2) + "\n",
            encoding="utf-8",
        )

    expected = {
        "none": ("CONFORMS", set()),
        "provider-substitution": (
            "VIOLATES", {"DELIVERY_ROUTE_SUBSTITUTION", "PROVIDER_NOT_PERMITTED"},
        ),
        "budget-overrun": ("VIOLATES", {"BUDGET_CEILING_EXCEEDED"}),
    }
    outcome, faults = expected[args.fault]
    ok = report["outcome"] == outcome and faults <= set(report["fault_codes"])
    print(json.dumps({
        "demo": "local-handoff",
        "fault": args.fault,
        "expected_outcome": outcome,
        "matched": ok,
        "report": report,
    }, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
