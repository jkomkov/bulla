#!/usr/bin/env python3
"""Run the complete proof-carrying observability/refinement transition."""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from bulla.action_receipt import sign_action_receipt
from bulla.envelope import Authority, Bounds, RecourseEnvelope
from bulla.experimental.checkpoint import issue_checkpoint, verify_checkpoint
from bulla.experimental.control_plane import (
    apply_package,
    mint_selection_receipt,
    verify_selection_receipt,
)
from bulla.experimental.equivocation import (
    EquivocationEvidence,
    log_head_hash,
    verify_equivocation_evidence,
)
from bulla.experimental.frsl import atom, variable
from bulla.experimental.invention import SeamProblem, mint_invention_receipt, synthesize
from bulla.experimental.observability import (
    BurdenVector,
    ConservationManifest,
    EnrichmentResponse,
    LogicPassport,
    ObservableOffer,
    ProvidedFact,
    ResponseStatus,
    build_enrichment_request,
    mint_enrichment_request_receipt,
    mint_enrichment_response_receipt,
    plan_enrichment,
    sign_enrichment_response,
)
from bulla.experimental.refinement import (
    apply_snapshot,
    authority_epoch,
    build_evidence_admission,
    classify_transition,
    mint_admission_receipt,
    mint_refinement_receipt,
    mint_supersession_receipt,
    refine_envelope,
    semantic_state,
    supersede_term,
)
from bulla.identity import LocalEd25519Signer
from bulla.registry import Deed, DeedLog, verify_inclusion_record


HERE = Path(__file__).resolve().parent
BULLA = HERE.parents[1]
CORPUS = BULLA / "bench/invention/corpus.json"
STANDALONE = BULLA / "scripts/verify_invention.py"


def _problem(instance_id: str) -> SeamProblem:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    document = next(
        item["problem"] for item in corpus["instances"] if item["id"] == instance_id
    )
    return SeamProblem.from_dict(document)


def _tool(name: str, request: dict) -> dict:
    completed = subprocess.run(
        [sys.executable, str(HERE / "tool.py"), name],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _envelope(principal: str, policy: str, scope: str) -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(principal=principal, policy=policy),
        bounds=Bounds(scope=scope),
    )


def _append_receipt(log: DeedLog, receipt) -> dict:
    document = receipt.to_dict()
    proof = document["signature"]
    index = log.append(
        Deed(
            issuer=proof["issuer"],
            content_hash=document["hashes"]["content"],
            attestation_hash=document["hashes"]["attestation"],
            signature=proof,
            envelope=receipt.envelope.to_dict(),
        )
    )
    return {
        "action_type": document["action"]["type"],
        "content_hash": document["hashes"]["content"],
        "attestation_hash": document["hashes"]["attestation"],
        "index": index,
    }


def _signed_head(signer: LocalEd25519Signer, *, log_id: str, size: int, root: str, observed: str):
    head = {
        "operator_id": signer.issuer,
        "log_id": log_id,
        "tree_size": size,
        "root": root,
        "observed_at": observed,
    }
    return {**head, "signature": signer.sign(log_head_hash(head))}


def run(*, fixture_keys: bool) -> dict:
    def signer(byte: int, issuer_override: str | None = None) -> LocalEd25519Signer:
        if fixture_keys:
            return LocalEd25519Signer(
                seed=bytes([byte]) + bytes(31),
                issuer_override=issuer_override,
            )
        return LocalEd25519Signer.generate(issuer_override=issuer_override)

    compiler = signer(121)
    provider = signer(122)
    witness = signer(123)

    total_problem = _problem("units-0")
    total_result = synthesize(total_problem)
    total_structure = _tool(
        "shared-adapter",
        {"relations": {"canonical_quantity": [["meter"]]}},
    )
    total_application = apply_package(
        total_problem,
        total_result.package,
        shared_structure=total_structure,
        target_arguments=("meter",),
        adapter_version="units-adapter/1",
    )
    total_route = _tool(
        "decision-router",
        {"decision": total_application.status.value, "decision_hash": total_application.result_hash},
    )

    partial_problem = _problem("null_absent-2")
    partial_result = synthesize(partial_problem)
    partial_structure = _tool(
        "shared-adapter",
        {"relations": {name: [] for name in partial_problem.shared_vocabulary}},
    )
    partial_application = apply_package(
        partial_problem,
        partial_result.package,
        shared_structure=partial_structure,
        target_arguments=("value",),
        adapter_version="null-adapter/1",
    )
    partial_route = _tool(
        "decision-router",
        {"decision": partial_application.status.value, "decision_hash": partial_application.result_hash},
    )

    passport = LogicPassport.for_problem(partial_problem)
    manifest = ConservationManifest.for_problem(partial_problem)
    offer = ObservableOffer(
        offer_id="source-final-disposition",
        relation="source_final_disposition",
        sorts=("Record",),
        meaning=atom("target", [variable("x0")]),
        provider=provider.issuer,
        warrant_profile={
            "kind": "signed_attestation",
            "evidence_class": "signed_attestation",
            "verifier": "source-attestation-profile/1",
            "reveals": "boolean_fact_only",
        },
        burden=BurdenVector(disclosure_units=3, latency_ms=10, lifecycle_burden=1),
        consent_subjects=(provider.issuer,),
    )
    planning = plan_enrichment(
        partial_problem,
        (offer,),
        passport=passport,
        manifest=manifest,
    )
    request = build_enrichment_request(
        partial_problem,
        partial_result,
        planning,
        (offer,),
        passport=passport,
        manifest=manifest,
        requester_authority=partial_problem.authority,
    )
    refs = {"value": "ab", "null": "cd", "absent": "ef"}
    provided_facts = tuple(
        ProvidedFact(
            relation=offer.relation,
            arguments=(value,),
            truth=value == "value",
            evidence_class="signed_attestation",
            warrant_ref="sha256:" + refs[value] * 32,
        )
        for value in partial_problem.signature.sorts["Record"]
    )
    response = sign_enrichment_response(
        EnrichmentResponse(
            request_hash=request.request_hash,
            responder=provider.issuer,
            status=ResponseStatus.PROVIDE,
            selected_plan_hash=planning.plans[0].plan_hash,
            provided_facts=provided_facts,
        ),
        provider,
    )
    epoch = authority_epoch(partial_problem.authority)
    admission = build_evidence_admission(
        partial_problem,
        request,
        selected_plan_hash=planning.plans[0].plan_hash,
        responses=(response,),
        passport=passport,
        manifest=manifest,
        epoch=epoch,
    )
    refinement = refine_envelope(
        partial_problem,
        partial_result,
        admission,
        passport=passport,
        manifest=manifest,
    )
    _, prior_state = semantic_state(partial_problem)
    _, refined_state = semantic_state(partial_problem, (admission,))
    refined_application = apply_snapshot(
        partial_problem,
        refinement.new_result,
        refinement.new_snapshot,
        admissions=(admission,),
        current_authority_epoch=epoch,
        shared_structure=partial_structure,
        target_arguments=("value",),
        adapter_version="null-adapter/1",
        passport=passport,
        manifest=manifest,
    )
    revised_authority = {
        "principal": partial_problem.authority["principal"],
        "policy": partial_problem.authority["policy"] + "/revised",
    }
    supersession = supersede_term(
        refinement.new_snapshot,
        new_authority=revised_authority,
        reason="demo authority epoch rotation",
    )
    stale_application = apply_snapshot(
        partial_problem,
        refinement.new_result,
        refinement.new_snapshot,
        admissions=(admission,),
        current_authority_epoch=supersession.new_authority_epoch,
        shared_structure=partial_structure,
        target_arguments=("value",),
        adapter_version="null-adapter/1",
        passport=passport,
        manifest=manifest,
    )
    revised_state = dataclasses.replace(
        refined_state,
        authority_epoch=supersession.new_authority_epoch,
    )

    choice_problem = _problem("enums-4")
    choice_result = synthesize(choice_problem)
    selector = signer(124, choice_problem.authority["principal"])
    selected = choice_result.alternatives[0]
    selection_receipt = sign_action_receipt(
        mint_selection_receipt(
            choice_problem,
            choice_result,
            selected_package_hash=selected.package_hash,
            envelope=_envelope(
                choice_problem.authority["principal"],
                choice_problem.authority["policy"],
                "select one offered protected-behavior class",
            ),
            timestamp="2026-07-18T12:00:06Z",
            producer={"demo": "certified-semantic-refinement"},
        ),
        selector,
    )
    selection_verified = verify_selection_receipt(
        selection_receipt.to_dict(),
        choice_problem,
        choice_result,
        public_key=selector.public_key,
    )

    receipts = []
    common = _envelope(
        compiler.issuer,
        "policy://semantic-refinement-demo@sha256:01",
        "experimental semantic transition only",
    )
    receipts.append(
        sign_action_receipt(
            mint_invention_receipt(
                total_problem,
                total_result,
                envelope=common,
                timestamp="2026-07-18T12:00:00Z",
                producer={"demo": "certified-semantic-refinement"},
            ),
            compiler,
        )
    )
    receipts.append(
        sign_action_receipt(
            mint_invention_receipt(
                partial_problem,
                partial_result,
                envelope=common,
                timestamp="2026-07-18T12:00:01Z",
                producer={"demo": "certified-semantic-refinement"},
            ),
            compiler,
        )
    )
    receipts.append(
        sign_action_receipt(
            mint_enrichment_request_receipt(
                request,
                envelope=common,
                timestamp="2026-07-18T12:00:02Z",
                producer={"demo": "certified-semantic-refinement"},
            ),
            compiler,
        )
    )
    provider_envelope = _envelope(
        provider.issuer,
        "policy://provide-observable@sha256:02",
        "provide only the requested Boolean observable",
    )
    receipts.append(
        sign_action_receipt(
            mint_enrichment_response_receipt(
                response,
                envelope=provider_envelope,
                timestamp="2026-07-18T12:00:03Z",
                producer={"demo": "certified-semantic-refinement"},
            ),
            provider,
        )
    )
    receipts.append(
        sign_action_receipt(
            mint_admission_receipt(
                admission,
                envelope=common,
                timestamp="2026-07-18T12:00:04Z",
                producer={"demo": "certified-semantic-refinement"},
            ),
            compiler,
        )
    )
    receipts.append(
        sign_action_receipt(
            mint_refinement_receipt(
                refinement,
                envelope=common,
                timestamp="2026-07-18T12:00:05Z",
                producer={"demo": "certified-semantic-refinement"},
            ),
            compiler,
        )
    )
    receipts.append(selection_receipt)
    receipts.append(
        sign_action_receipt(
            mint_supersession_receipt(
                supersession,
                envelope=common,
                timestamp="2026-07-18T12:00:07Z",
                producer={"demo": "certified-semantic-refinement"},
            ),
            compiler,
        )
    )

    with tempfile.TemporaryDirectory(prefix="bulla-refinement-demo-") as directory:
        root = Path(directory)
        log = DeedLog(root / "semantic-receipts.jsonl")
        receipt_records = [_append_receipt(log, receipt) for receipt in receipts]
        inclusion_records = [log.inclusion(record["index"]) for record in receipt_records]
        inclusion_ok = all(
            verify_inclusion_record(item, trusted_root=log.root())
            for item in inclusion_records
        )
        checkpoint = issue_checkpoint(
            log,
            witness,
            log_id="log://certified-semantic-refinement-demo",
            issued_at="2026-07-18T12:00:08Z",
        )
        checkpoint_ok = verify_checkpoint(checkpoint).ok

        fake_root = "sha256:" + "99" * 32
        head_a = _signed_head(
            witness,
            log_id=checkpoint.log_id,
            size=checkpoint.tree_size,
            root=checkpoint.root,
            observed="2026-07-18T12:00:09Z",
        )
        head_b = _signed_head(
            witness,
            log_id=checkpoint.log_id,
            size=checkpoint.tree_size,
            root=fake_root,
            observed="2026-07-18T12:00:10Z",
        )
        equivocation = verify_equivocation_evidence(
            EquivocationEvidence(head_a=head_a, head_b=head_b)
        )

        paths = {
            "problem": partial_problem.to_dict(),
            "passport": passport.to_dict(),
            "manifest": manifest.to_dict(),
            "offers": [offer.to_dict()],
            "planning": planning.to_dict(),
            "refinement": refinement.to_dict(),
        }
        artifact_paths = {}
        for name, document in paths.items():
            path = root / f"{name}.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            artifact_paths[name] = path
        standalone_plan = subprocess.run(
            [
                sys.executable,
                "-I",
                str(STANDALONE),
                "plan-enrichment",
                str(artifact_paths["problem"]),
                str(artifact_paths["passport"]),
                str(artifact_paths["manifest"]),
                str(artifact_paths["offers"]),
                str(artifact_paths["planning"]),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        standalone_refinement = subprocess.run(
            [
                sys.executable,
                "-I",
                str(STANDALONE),
                "verify-refinement",
                str(artifact_paths["refinement"]),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        standalone = {
            "planning": json.loads(standalone_plan.stdout),
            "refinement": json.loads(standalone_refinement.stdout),
        }

    return {
        "profile": "bulla.certified-semantic-refinement/0.1-experimental",
        "mode": "live-local-subprocess-and-transparency-mechanism-demo",
        "decisions": {
            "total": total_application.status.value,
            "partial_residual": partial_application.status.value,
            "after_enrichment": refined_application.status.value,
            "after_epoch_change": stale_application.status.value,
            "stale_cause": stale_application.cause.value,
        },
        "transitions": {
            "unchanged_context": classify_transition(prior_state, prior_state).value,
            "admitted_constraint": classify_transition(prior_state, refined_state).value,
            "unresolved_case": classify_transition(prior_state, routed=True).value,
            "authority_epoch_change": classify_transition(refined_state, revised_state).value,
        },
        "downstream_actions": [total_route["action"], partial_route["action"]],
        "observability": {
            "opposing_pair_count": planning.opposing_pair_count,
            "plan_count": len(planning.plans),
            "indispensable": list(planning.indispensable_observables),
            "request_hash": request.request_hash,
            "admission_hash": admission.admission_hash,
        },
        "refinement": {
            "prior_ambiguous_cells": len(refinement.prior_snapshot.regions.ambiguous),
            "new_ambiguous_cells": len(refinement.new_snapshot.regions.ambiguous),
            "certificate": refinement.certificate.to_dict(),
        },
        "governance": {
            "status": choice_result.status.value,
            "selected_package_hash": selected.package_hash,
            "selection_receipt_ok": selection_verified["ok"],
        },
        "transparency": {
            "receipt_count": len(receipt_records),
            "receipts": receipt_records,
            "inclusion_ok": inclusion_ok,
            "checkpoint_hash": checkpoint.checkpoint_hash,
            "checkpoint_ok": checkpoint_ok,
            "split_view_detected": equivocation["equivocation"],
            "mechanism_validation_not_independent_plurality": True,
        },
        "standalone": standalone,
        "claim_boundary": (
            "Demonstrates local protocol mechanics and replay; it does not establish "
            "external plurality, production recourse, or benchmark generality."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-keys", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = run(fixture_keys=args.fixture_keys)
    if args.output:
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    summary = {
        "mode": payload["mode"],
        "decisions": payload["decisions"],
        "selection_receipt_ok": payload["governance"]["selection_receipt_ok"],
        "inclusion_ok": payload["transparency"]["inclusion_ok"],
        "checkpoint_ok": payload["transparency"]["checkpoint_ok"],
        "split_view_detected": payload["transparency"]["split_view_detected"],
        "standalone_ok": all(item["ok"] for item in payload["standalone"].values()),
    }
    print(json.dumps(summary, indent=2))
    return 0 if all(
        (
            summary["selection_receipt_ok"],
            summary["inclusion_ok"],
            summary["checkpoint_ok"],
            summary["split_view_detected"],
            summary["standalone_ok"],
        )
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
