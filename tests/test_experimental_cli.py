from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from bulla.identity import LocalEd25519Signer
from bulla.registry import Deed, DeedLog


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def _run(*arguments: str):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "bulla", *arguments],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_invent_verify_and_apply_commands(tmp_path):
    problem = ROOT / "examples/invention/definable.json"
    result = tmp_path / "result.json"
    invented = _run("experimental", "invent", str(problem), "-o", str(result))
    assert invented.returncode == 0, invented.stderr
    verified = _run(
        "experimental", "verify-invention", str(problem), str(result), "--format", "json"
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    structure = tmp_path / "structure.json"
    structure.write_text(json.dumps({"accepted_evidence": [["d0"]]}), encoding="utf-8")
    applied = _run(
        "experimental", "apply-invention", str(problem), str(result), str(structure),
        "--argument", "d0", "--adapter-version", "delivery-test/1",
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert json.loads(applied.stdout)["status"] == "RELY"


def test_hybrid_candidate_command_honors_budget(tmp_path):
    problem = ROOT / "examples/invention/definable.json"
    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        json.dumps(
            {
                "op": "atom",
                "relation": "accepted_evidence",
                "args": [{"var": "x0"}],
            }
        ),
        encoding="utf-8",
    )
    budget = tmp_path / "budget.json"
    budget.write_text(
        json.dumps(
            {
                "allowed_relations": ["accepted_evidence"],
                "reveal_target_value": False,
                "max_countermodels": 0,
                "max_ground_facts": 0,
            }
        ),
        encoding="utf-8",
    )
    checked = _run(
        "experimental", "check-candidate", str(problem), str(candidate), str(budget),
        "--generator", "fixture", "--generator-version", "1",
        "--prompt-hash", "sha256:" + "ab" * 32,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout)["status"] == "ACCEPTED"


def test_observability_consent_and_refinement_commands(tmp_path):
    corpus = json.loads((ROOT / "bench/invention/corpus.json").read_text())
    problem_doc = next(
        item["problem"]
        for item in corpus["instances"]
        if item["id"] == "null_absent-2"
    )
    problem = tmp_path / "problem.json"
    problem.write_text(json.dumps(problem_doc), encoding="utf-8")
    prior = tmp_path / "prior.json"
    invented = _run("experimental", "invent", str(problem), "-o", str(prior))
    assert invented.returncode == 0, invented.stdout + invented.stderr

    provider = LocalEd25519Signer(seed=bytes([112]) + bytes(31))
    key = tmp_path / "provider-key.json"
    key.write_text(json.dumps(provider.to_keyfile_dict()), encoding="utf-8")
    offers = tmp_path / "offers.json"
    offers.write_text(
        json.dumps(
            [
                {
                    "offer_id": "source-final-disposition",
                    "relation": "source_final_disposition",
                    "sorts": ["Record"],
                    "meaning": {
                        "op": "atom",
                        "relation": "target",
                        "args": [{"var": "x0"}],
                    },
                    "provider": provider.issuer,
                    "warrant_profile": {
                        "kind": "signed_attestation",
                        "evidence_class": "signed_attestation",
                        "verifier": "source-attestation-profile/1",
                        "reveals": "boolean_fact_only",
                    },
                    "burden": {
                        "disclosure_units": 3,
                        "latency_ms": 10,
                        "monetary_microunits": 0,
                        "new_authorities": 0,
                        "institutional_dependencies": 0,
                        "lifecycle_burden": 1,
                    },
                    "consent_subjects": [provider.issuer],
                    "expiry": None,
                }
            ]
        ),
        encoding="utf-8",
    )
    packet = tmp_path / "packet.json"
    planned = _run(
        "experimental",
        "plan-enrichment",
        str(problem),
        str(prior),
        str(offers),
        "-o",
        str(packet),
    )
    assert planned.returncode == 0, planned.stdout + planned.stderr
    packet_doc = json.loads(packet.read_text())
    plan_hash = packet_doc["planning"]["plans"][0]
    from bulla.experimental.observability import VerifiedEnrichmentPlan

    plan_hash = VerifiedEnrichmentPlan.from_dict(plan_hash).plan_hash
    facts = tmp_path / "facts.json"
    facts.write_text(
        json.dumps(
            [
                {
                    "relation": "source_final_disposition",
                    "arguments": [value],
                    "truth": value == "value",
                    "evidence_class": "signed_attestation",
                    "warrant_ref": "sha256:" + byte * 32,
                }
                for value, byte in (("value", "ab"), ("null", "cd"), ("absent", "ef"))
            ]
        ),
        encoding="utf-8",
    )
    response = tmp_path / "response.json"
    responded = _run(
        "experimental",
        "respond-enrichment",
        str(packet),
        "--status",
        "PROVIDE",
        "--plan-hash",
        plan_hash,
        "--facts",
        str(facts),
        "--key",
        str(key),
        "-o",
        str(response),
    )
    assert responded.returncode == 0, responded.stdout + responded.stderr
    bundle = tmp_path / "refinement.json"
    refined = _run(
        "experimental",
        "refine-envelope",
        str(problem),
        str(prior),
        str(packet),
        "--response",
        str(response),
        "--plan-hash",
        plan_hash,
        "-o",
        str(bundle),
    )
    assert refined.returncode == 0, refined.stdout + refined.stderr
    verified = _run("experimental", "verify-refinement", str(bundle))
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert json.loads(verified.stdout)["ok"] is True


def test_checkpoint_cli_archives_and_verifies_extension(tmp_path):
    signer = LocalEd25519Signer(seed=bytes([111]) + bytes(31))
    key = tmp_path / "key.json"
    key.write_text(json.dumps(signer.to_keyfile_dict()), encoding="utf-8")
    registry = tmp_path / "registry.jsonl"
    log = DeedLog(registry)
    log.append(Deed("did:key:zExample", "sha256:" + "01" * 32, "sha256:" + "02" * 32))
    first = tmp_path / "first.json"
    archive = tmp_path / "archive.jsonl"
    issued = _run(
        "experimental", "checkpoint", "issue", "--registry", str(registry),
        "--log-id", "log://cli-test", "--key", str(key), "--archive", str(archive),
        "-o", str(first),
    )
    assert issued.returncode == 0, issued.stderr

    log = DeedLog(registry)
    log.append(Deed("did:key:zExample", "sha256:" + "03" * 32, "sha256:" + "04" * 32))
    second = tmp_path / "second.json"
    consistency = tmp_path / "consistency.json"
    extended = _run(
        "experimental", "checkpoint", "issue", "--registry", str(registry),
        "--log-id", "log://cli-test", "--key", str(key), "--archive", str(archive),
        "--previous", str(first), "--consistency-output", str(consistency), "-o", str(second),
    )
    assert extended.returncode == 0, extended.stderr
    verified = _run(
        "experimental", "checkpoint", "verify", str(second),
        "--previous", str(first), "--consistency", str(consistency),
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert json.loads(verified.stdout)["ok"] is True


def test_semantic_finality_cli_matches_frozen_blind_vector():
    vector = (
        ROOT
        / "bench/invention/semantic-settlement/reproduction-vectors/procurement-provisional.blind.json"
    )
    assessed = _run("experimental", "assess-finality", str(vector))
    assert assessed.returncode == 0, assessed.stdout + assessed.stderr
    payload = json.loads(assessed.stdout)
    assert payload["status"] == "EXECUTE_PROVISIONALLY"
    assert payload["cause"] == "VERIFIED_AMBIGUITY_RESERVE"
