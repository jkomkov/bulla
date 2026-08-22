"""Source-only composition verifier for the Answerability Network example.

The profile composes three independently bounded instruments: signed
ActionReceipts for one inference procurement, a Witness Covenant report, and a
Reliance Map report.  It establishes only the exact bindings among supplied
synthetic records under a separately supplied context.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
from typing import Any, Mapping

from bulla.action_receipt import verify_receipt
from bulla.receipt_parser import parse_action_receipt_json, ReceiptParseLimits
from bulla.experimental.reliance_map import compute_reliance_map
from bulla.experimental.witness_covenant import (
    WitnessCovenantError,
    _enforce_receipt_string_limits,
    _exact,
    _parse_json,
    canonical_hash,
    verify_witness_covenant,
)
from bulla.registry import deed_leaf, verify_inclusion_record
from bulla.experimental.checkpoint import verify_checkpoint, verify_checkpoint_extension


PROFILE = "bulla.answerability-network/0.1-experimental"
REPORT_PROFILE = "bulla.answerability-network-report/0.1-experimental"
CONTEXT_PROFILE = "bulla.answerability-network-context/0.1-experimental"
NETWORK_ID = "answerability-network:one-job-one-fork-10000"
TRANSACTION_ID = "inference-job-001"
ARTIFACT_DIGEST = "sha256:57320e94c7670afda62ca39aa76bf96ddfe646a5b960bd750d7e90da24ef7de3"
EVIDENCE_PROFILE = "bulla.fixed-sha256-delivery/1"
EVIDENCE_CLASS = "SUPPLIED_BYTE_PREIMAGE"
WITNESS_COVENANT_HASH = "sha256:f45645e91c063000e8120effa3af63b8238f459c22aab26bdb6bf29d5fb067f5"
PRICE = 12_500
UNIT = "USD_CENTS"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
STAGES = ("job", "published", "fork-open", "fork-closed", "fork-authorized", "model-dispute")
RECEIPT_ACTIONS = {
    "buyer": "inference.promise.accept",
    "provider-a": "inference.delivery",
    "provider-b": "inference.delivery",
    "buyer-selection": "inference.selection",
}


class AnswerabilityNetworkError(ValueError):
    pass


@dataclass(frozen=True)
class AnswerabilityNetworkLimits:
    max_files: int = 160
    max_bytes: int = 32 * 1024 * 1024
    max_member_bytes: int = 4 * 1024 * 1024


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


TASK_DIGEST = _sha(b"fixed delegated inference task")


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(
        character in "0123456789abcdef" for character in value[7:]
    )


def _read_network(directory: Path, limits: AnswerabilityNetworkLimits) -> dict[str, bytes]:
    if directory.is_symlink() or not directory.is_dir():
        raise AnswerabilityNetworkError("network root must be a real directory")
    files: dict[str, bytes] = {}
    total = 0
    for raw_root, dirs, names in os.walk(directory, followlinks=False):
        base = Path(raw_root)
        if any((base / name).is_symlink() for name in dirs):
            raise AnswerabilityNetworkError("network dossier contains a symlink directory")
        for name in names:
            path = base / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise AnswerabilityNetworkError("network dossier contains a non-regular member")
            relative = path.relative_to(directory).as_posix()
            pure = PurePosixPath(relative)
            if "\\" in relative or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
                raise AnswerabilityNetworkError("network dossier contains an unsafe path")
            raw = path.read_bytes()
            if len(raw) > limits.max_member_bytes:
                raise AnswerabilityNetworkError("network dossier member exceeds resource limits")
            total += len(raw); files[relative] = raw
            if len(files) > limits.max_files or total > limits.max_bytes:
                raise AnswerabilityNetworkError("network dossier exceeds resource limits")
    return files


def _canonical_equal(left: Any, right: Any) -> bool:
    return type(left) is type(right) and (
        all(_canonical_equal(left[key], right[key]) for key in left)
        if isinstance(left, dict) and set(left) == set(right)
        else all(_canonical_equal(a, b) for a, b in zip(left, right))
        if isinstance(left, list) and len(left) == len(right)
        else left == right
    )


def _receipt(
    raw: bytes,
    role: str,
    accepted_issuer: str,
    *,
    expected_evidence_refs: list[dict[str, str]] | None = None,
) -> Mapping[str, Any]:
    try:
        doc = parse_action_receipt_json(
            raw,
            limits=ReceiptParseLimits(max_bytes=262_144, max_depth=20, max_nodes=10_000, max_string_bytes=16_384),
        ).to_dict()
        _enforce_receipt_string_limits(doc, role)
    except Exception as exc:
        raise AnswerabilityNetworkError(f"{role} receipt is malformed") from exc
    verification = verify_receipt(doc)
    if not verification.ok or verification.verified_to != "attestation" or verification.authority_authentic != "verified":
        raise AnswerabilityNetworkError(f"{role} receipt does not verify to an authorized attestation")
    action = _exact(doc["action"], {"type", "subject"}, f"{role} action")
    diagnostic = _exact(doc["diagnostic_ref"], {"status"}, f"{role} diagnostic")
    mandate = _exact(doc["mandate"], {"authority", "bounds"}, f"{role} mandate")
    authority = _exact(mandate["authority"], {"principal", "policy", "delegation"}, f"{role} authority")
    bounds = _exact(mandate["bounds"], {"scope"}, f"{role} bounds")
    remedy = _exact(doc["remedy"], {"challenge_window", "forum", "remedies"}, f"{role} remedy")
    forum = _exact(remedy["forum"], {"log_endpoint", "trusted_root_ref"}, f"{role} forum")
    retention = _exact(doc["retention"], {"disclosure", "record"}, f"{role} retention")
    expected_action = RECEIPT_ACTIONS[role]
    if (
        action["type"] != expected_action
        or doc["signature"]["issuer"] != accepted_issuer
        or not UUID_RE.fullmatch(doc["event_id"])
        or not TIMESTAMP_RE.fullmatch(doc["claimed_at"])
        or diagnostic["status"] != "not_applicable"
        or doc["evidence_refs"] != (expected_evidence_refs or [])
        or doc["anchor_ref"] != {}
        or doc["conventions"] != []
        or doc["stake"] is not None
        or authority != {
            "principal": accepted_issuer,
            "policy": f"policy://answerability-network/{expected_action}",
            "delegation": [],
        }
        or bounds["scope"] != f"profile:{PROFILE};transaction:{TRANSACTION_ID}"
        or remedy["challenge_window"] != "checkpoint:answerability-network-5"
        or forum["log_endpoint"] != "https://glyphstandard.com/bulla/answerable-computing"
        or not _is_hash(forum["trusted_root_ref"])
        or remedy["remedies"] != [{
            "anchor": "forum:answerability-network",
            "rung": "challenge",
            "verifier": "verify the retained network under supplied context",
        }]
        or retention != {"disclosure": "public", "record": "operational"}
        or not isinstance(doc["producer"], Mapping)
    ):
        raise AnswerabilityNetworkError(f"{role} action or issuer is not accepted")
    return doc


def _context(raw: bytes) -> Mapping[str, Any]:
    value = _exact(
        _parse_json(raw, "answerability network context"),
        {
            "profile", "accepted_core_hash", "accepted_issuers",
            "accepted_evidence_verifier", "accepted_witness_covenant_hash",
            "witness_context", "reliance_context",
        },
        "answerability network context",
    )
    if value["profile"] != CONTEXT_PROFILE:
        raise AnswerabilityNetworkError("unsupported answerability network context")
    if not _is_hash(value["accepted_core_hash"]):
        raise AnswerabilityNetworkError("accepted network core hash is malformed")
    if (
        value["accepted_evidence_verifier"] != EVIDENCE_PROFILE
        or value["accepted_witness_covenant_hash"] != WITNESS_COVENANT_HASH
    ):
        raise AnswerabilityNetworkError("closed evidence or covenant context is not accepted")
    issuers = _exact(value["accepted_issuers"], set(RECEIPT_ACTIONS), "accepted procurement issuers")
    if any(not isinstance(item, str) or not item for item in issuers.values()) or len(set(issuers.values())) != len(issuers):
        raise AnswerabilityNetworkError("accepted procurement issuers are malformed")
    if value["witness_context"] is not None and not isinstance(value["witness_context"], Mapping):
        raise AnswerabilityNetworkError("witness context is malformed")
    if value["reliance_context"] is not None and not isinstance(value["reliance_context"], Mapping):
        raise AnswerabilityNetworkError("reliance context is malformed")
    return value


def _base(stage: str, error: str) -> dict[str, Any]:
    return {
        "profile": REPORT_PROFILE, "stage": stage, "network_integrity": "FAILED",
        "procurement": "NOT_COMPUTED", "byte_equality": "NOT_COMPUTED",
        "buyer_policy": "NOT_COMPUTED", "selected_provider": None,
        "selected_receipt": "NOT_COMPUTED", "receipt_inclusion": "NOT_COMPUTED",
        "history_extension": "NOT_COMPUTED",
        "history_consistency": "NOT_COMPUTED", "same_size_equivocation": "NOT_COMPUTED",
        "challenge": "NOT_COMPUTED", "witness_remedy": "NOT_COMPUTED",
        "settlement_authorization": "NOT_COMPUTED", "test_ledger_attempt": "NOT_COMPUTED",
        "model_identity_control": "NOT_COMPUTED", "reliance": None,
        "substantive_truth": "NOT_ESTABLISHED", "actual_funds": "NOT_ESTABLISHED",
        "errors": [error], "warnings": [], "exit_code": 1,
    }


def verify_answerability_network(
    directory: str | Path,
    context_bytes: bytes | bytearray | memoryview,
    *,
    limits: AnswerabilityNetworkLimits = AnswerabilityNetworkLimits(),
) -> dict[str, Any]:
    stage = "UNREAD"
    try:
        root = Path(directory)
        files = _read_network(root, limits)
        core = _exact(
            _parse_json(files["network-core.json"], "network core"),
            {"profile", "revision", "stage", "network_id", "transaction_id", "artifact_manifest"},
            "network core",
        )
        stage = core["stage"]
        if (
            core["profile"] != PROFILE
            or type(core["revision"]) is not int
            or core["revision"] != 1
            or core["network_id"] != NETWORK_ID
            or core["transaction_id"] != TRANSACTION_ID
            or stage not in STAGES
        ):
            raise AnswerabilityNetworkError("unsupported network core")
        context = _context(bytes(context_bytes))
        core_hash = canonical_hash(core)
        if context["accepted_core_hash"] != core_hash:
            raise AnswerabilityNetworkError("network core is not accepted by the external context")
        manifest = core["artifact_manifest"]
        if not isinstance(manifest, list) or not manifest:
            raise AnswerabilityNetworkError("network artifact manifest is empty")
        declared: set[str] = set()
        for index, item in enumerate(manifest):
            item = _exact(item, {"path", "sha256", "byte_length", "media_type"}, f"manifest {index}")
            path = item["path"]
            if not isinstance(path, str) or path in declared or path == "network-core.json" or path not in files:
                raise AnswerabilityNetworkError("network manifest path is invalid")
            raw = files[path]
            expected_media = "application/octet-stream" if path == "procurement/provider-a-delivery.bin" else "application/json"
            if type(item["byte_length"]) is not int or item["byte_length"] != len(raw) or item["sha256"] != _sha(raw) or item["media_type"] != expected_media:
                raise AnswerabilityNetworkError(f"network artifact commitment mismatch: {path}")
            if expected_media == "application/json" and path != "reliance/graph.json":
                _parse_json(raw, path)
            declared.add(path)
        if declared | {"network-core.json"} != set(files):
            raise AnswerabilityNetworkError("network dossier has undeclared or missing files")

        issuers = context["accepted_issuers"]
        delivery_assertion_raw = files["procurement/provider-a-delivery-assertion.json"]
        provider_a_evidence_refs = [
            {"name": "delivery_report", "hash": _sha(delivery_assertion_raw), "grounding": "self_asserted"},
            {"name": "delivered_bytes", "hash": ARTIFACT_DIGEST, "grounding": "execution_verified"},
        ]
        buyer = _receipt(files["procurement/promise.json"], "buyer", issuers["buyer"])
        provider_a = _receipt(
            files["procurement/provider-a.json"], "provider-a", issuers["provider-a"],
            expected_evidence_refs=provider_a_evidence_refs,
        )
        provider_b = _receipt(files["procurement/provider-b.json"], "provider-b", issuers["provider-b"])
        selection = _receipt(files["procurement/selection.json"], "buyer-selection", issuers["buyer-selection"])
        promise = buyer["action"]["subject"]
        a = provider_a["action"]["subject"]
        b = provider_b["action"]["subject"]
        selected = selection["action"]["subject"]
        required_promise = {"profile", "transaction_id", "task_digest", "expected_artifact_digest", "accepted_evidence_classes", "price", "unit", "witness_covenant_hash"}
        required_delivery = {"profile", "transaction_id", "provider_id", "artifact_digest", "evidence_class", "evidence_digest", "model_identity"}
        required_selection = {"profile", "transaction_id", "selected_provider", "selected_attestation", "policy_digest"}
        _exact(promise, required_promise, "promise subject"); _exact(a, required_delivery, "provider A subject"); _exact(b, required_delivery, "provider B subject"); _exact(selected, required_selection, "selection subject")
        if any(subject["profile"] != PROFILE or subject["transaction_id"] != core["transaction_id"] for subject in (promise, a, b, selected)):
            raise AnswerabilityNetworkError("procurement records cross a transaction or profile")
        if (
            promise["task_digest"] != TASK_DIGEST
            or promise["expected_artifact_digest"] != ARTIFACT_DIGEST
            or type(promise["price"]) is not int
            or promise["price"] != PRICE
            or promise["unit"] != UNIT
            or promise["witness_covenant_hash"] != context["accepted_witness_covenant_hash"]
            or a["provider_id"] != "provider-a"
            or b["provider_id"] != "provider-b"
            or a["model_identity"] != "SELF_ASSERTED"
            or b["model_identity"] != "SELF_ASSERTED"
            or not _is_hash(a["evidence_digest"])
            or not _is_hash(b["evidence_digest"])
        ):
            raise AnswerabilityNetworkError("procurement terms differ from the closed job")
        if a["artifact_digest"] != b["artifact_digest"] or a["artifact_digest"] != promise["expected_artifact_digest"]:
            raise AnswerabilityNetworkError("provider-reported artifact digests do not match the frozen commitment")
        if promise["accepted_evidence_classes"] != [EVIDENCE_CLASS] or a["evidence_class"] != EVIDENCE_CLASS or b["evidence_class"] != "SELF_ASSERTED":
            raise AnswerabilityNetworkError("buyer evidence policy is not the frozen policy")
        evidence_raw = files["procurement/provider-a-evidence.json"]
        delivery_raw = files["procurement/provider-a-delivery.bin"]
        delivery_assertion = _exact(
            _parse_json(delivery_assertion_raw, "provider A delivery assertion"),
            {"profile", "transaction_id", "provider_id", "artifact_digest", "claim"},
            "provider A delivery assertion",
        )
        evidence = _exact(
            _parse_json(evidence_raw, "provider A evidence"),
            {"profile", "transaction_id", "provider_id", "artifact_digest", "artifact_path"},
            "provider A evidence",
        )
        if (
            not _canonical_equal(delivery_assertion, {
                "profile": PROFILE,
                "transaction_id": TRANSACTION_ID,
                "provider_id": "provider-a",
                "artifact_digest": ARTIFACT_DIGEST,
                "claim": "HISTORICAL_DELIVERY_REPORTED",
            })
            or not _canonical_equal(evidence, {
                "profile": context["accepted_evidence_verifier"],
                "transaction_id": TRANSACTION_ID,
                "provider_id": "provider-a",
                "artifact_digest": ARTIFACT_DIGEST,
                "artifact_path": "procurement/provider-a-delivery.bin",
            })
            or a["evidence_digest"] != _sha(evidence_raw)
            or _sha(delivery_raw) != ARTIFACT_DIGEST
        ):
            raise AnswerabilityNetworkError("provider A evidence does not satisfy the accepted byte-preimage rule")
        if selected["selected_provider"] != a["provider_id"] or selected["selected_attestation"] != provider_a["hashes"]["attestation"]:
            raise AnswerabilityNetworkError("buyer selection is not bound to the qualifying receipt")
        expected_policy = canonical_hash({"accepted_evidence_classes": promise["accepted_evidence_classes"], "rule": "REQUIRE_SHA256_PREIMAGE/1"})
        if selected["policy_digest"] != expected_policy:
            raise AnswerabilityNetworkError("selection policy binding is wrong")

        witness = None
        inclusion_status = "NOT_PRESENTED"
        extension_status = "NOT_PRESENTED"
        if any(path.startswith("witness-covenant/") for path in files):
            witness = verify_witness_covenant(root / "witness-covenant", json.dumps(context["witness_context"], sort_keys=True).encode())
            witness_report = witness.to_dict()
            if witness_report["exit_code"] != 0:
                raise AnswerabilityNetworkError("embedded witness covenant did not verify")
            if "witness-covenant/evidence/external-receipt-inclusion.json" in files:
                head = _parse_json(files["witness-covenant/evidence/head-a.json"], "accepted witness head")
                inclusion = _exact(
                    _parse_json(files["witness-covenant/evidence/external-receipt-inclusion.json"], "receipt inclusion"),
                    {"attestation", "index", "tree_size", "leaf", "proof", "root"},
                    "receipt inclusion",
                )
                if (
                    type(inclusion["index"]) is not int
                    or inclusion["index"] < 0
                    or type(inclusion["tree_size"]) is not int
                    or inclusion["tree_size"] <= inclusion["index"]
                    or not _is_hash(inclusion["leaf"])
                    or not _is_hash(inclusion["root"])
                    or not isinstance(inclusion["proof"], list)
                    or any(not _is_hash(item) for item in inclusion["proof"])
                ):
                    raise AnswerabilityNetworkError("receipt inclusion is malformed")
                if inclusion.get("attestation") != provider_a["hashes"]["attestation"] or inclusion.get("root") != head.get("root") or inclusion.get("tree_size") != head.get("tree_size"):
                    raise AnswerabilityNetworkError("receipt inclusion is not bound to the selected receipt and head")
                leaf = deed_leaf({
                    "issuer": provider_a["signature"]["issuer"],
                    "content_hash": provider_a["hashes"]["content"],
                    "attestation_hash": provider_a["hashes"]["attestation"],
                })
                if not verify_inclusion_record(inclusion, trusted_root=head["root"], expected_leaf=leaf):
                    raise AnswerabilityNetworkError("selected receipt inclusion proof failed")
                prefix = _parse_json(files["witness-covenant/evidence/external-receipt-checkpoint.json"], "receipt checkpoint")
                consistency = _parse_json(files["witness-covenant/evidence/external-receipt-consistency.json"], "receipt history consistency")
                prefix_verification = verify_checkpoint(prefix)
                extension = verify_checkpoint_extension(prefix, head, consistency)
                if (
                    not prefix_verification.ok
                    or not extension.ok
                    or type(prefix.get("tree_size")) is not int
                    or prefix["tree_size"] != 1
                    or prefix.get("root") != leaf
                    or not _canonical_equal(prefix.get("anchor_evidence"), head.get("anchor_evidence"))
                ):
                    raise AnswerabilityNetworkError("selected receipt history extension failed")
                inclusion_status = "VERIFIED"
                extension_status = "VERIFIED"
            if promise["witness_covenant_hash"] != _parse_json(files["witness-covenant/covenant-core.json"], "covenant core")["covenant"]["covenant_hash"]:
                raise AnswerabilityNetworkError("promise does not bind the operated witness covenant")
        else:
            witness_report = None

        reliance = None
        if all(path in files for path in ("reliance/graph.json", "reliance/correction-ledger.json")):
            rc = context["reliance_context"]
            if not isinstance(rc, Mapping):
                raise AnswerabilityNetworkError("reliance context is absent")
            reliance = compute_reliance_map(files["reliance/graph.json"], files["reliance/correction-ledger.json"], json.dumps(rc, sort_keys=True).encode())
            graph = json.loads(files["reliance/graph.json"])
            ledger = json.loads(files["reliance/correction-ledger.json"])
            if not isinstance(ledger.get("corrections"), list) or len(ledger["corrections"]) != 1:
                raise AnswerabilityNetworkError("reliance recall must contain one accepted notice")
            correction_subject = ledger["corrections"][0]["action"]["subject"]
            checkpoint_source = next(item for item in graph["nodes"] if item["node_id"] == "source-00")
            head = _parse_json(files["witness-covenant/evidence/head-a.json"], "accepted witness head")
            other_head = _parse_json(files["witness-covenant/evidence/head-b.json"], "conflicting witness head")
            finding = canonical_hash({
                "predicate": "bulla.same-size-log-equivocation/1",
                "head_a": head["checkpoint_hash"],
                "head_b": other_head["checkpoint_hash"],
            })
            if (
                checkpoint_source["artifact_digest"] != head["checkpoint_hash"]
                or correction_subject.get("target_digest") != head["checkpoint_hash"]
                or correction_subject.get("reason_digest") != finding
                or correction_subject.get("replacement_digest") != canonical_hash({"recheck_notice": finding})
            ):
                raise AnswerabilityNetworkError("reliance notice is not bound to the accepted witness fork")
            if reliance["summary"] != {"declared_decisions": 10000, "graph_nodes": 10004, "graph_edges": 10000, "affected": 2500, "not_affected": 5000, "undetermined": 2500}:
                raise AnswerabilityNetworkError("reliance tri-state closure changed")

        derived = "job"
        if witness_report:
            derived = witness_report["scenario"]
            if derived == "consistent": derived = "published"
            if derived != "model-dispute" and (inclusion_status != "VERIFIED" or extension_status != "VERIFIED"):
                raise AnswerabilityNetworkError("selected receipt lacks its authenticated append-only history")
        if stage != derived:
            raise AnswerabilityNetworkError("stage label differs from verified component facts")
        if stage.startswith("fork-") and reliance is None:
            raise AnswerabilityNetworkError("fork stage omits the bound reliance recall")
        model_control = "CHALLENGE_REQUIRED" if stage == "model-dispute" else "NOT_RUN"
        reliance_report = None
        if reliance is not None:
            selected_result = next(item for item in reliance["results"] if item["node_id"] == "decision-09996")
            selected_path = ["receipt:provider-a", *selected_result["paths"][0]["nodes"]]
            if len(selected_path) != 14 or selected_result["status"] != "AFFECTED":
                raise AnswerabilityNetworkError("selected 13-hop trace changed")
            reliance_report = {
                "graph_digest": reliance["graph_digest"],
                "report_digest": reliance["report_digest"],
                "summary": reliance["summary"],
                "selected_receipt_attestation": provider_a["hashes"]["attestation"],
                "selected_checkpoint": head["checkpoint_hash"],
                "selected_status": selected_result["status"],
                "selected_path": selected_path,
            }
        report = {
            "profile": REPORT_PROFILE, "stage": stage, "network_integrity": "VERIFIED",
            "procurement": "VERIFIED", "byte_equality": "PROVIDER_DIGESTS_MATCH_SUPPLIED_BYTES",
            "buyer_policy": "SELECTED_PROVIDER_A", "selected_provider": a["provider_id"],
            "selected_receipt": "VERIFIED", "receipt_inclusion": inclusion_status,
            "history_extension": extension_status,
            "history_consistency": witness_report["history_consistency"] if witness_report else "NOT_PRESENTED",
            "same_size_equivocation": witness_report["same_size_equivocation"] if witness_report else "NOT_PRESENTED",
            "challenge": witness_report["challenge"] if witness_report else "NOT_PRESENTED",
            "witness_remedy": witness_report["witness_remedy"] if witness_report else "NOT_PRESENTED",
            "settlement_authorization": witness_report["settlement_authorization"] if witness_report else "NOT_PRESENTED",
            "test_ledger_attempt": witness_report["test_ledger_attempt"] if witness_report else "NOT_PRESENTED",
            "model_identity_control": model_control,
            "reliance": reliance_report,
            "substantive_truth": "NOT_ESTABLISHED", "actual_funds": "NOT_ESTABLISHED",
            "errors": [],
            "warnings": ["the remaining 9,999 decisions are synthetic declarations, not transactions", "a witness fork triggers recheck and bounded recourse; it does not make the provider result false"],
            "exit_code": 0,
        }
        return {**report, "report_digest": canonical_hash(report)}
    except (AnswerabilityNetworkError, WitnessCovenantError, KeyError, StopIteration, TypeError, ValueError) as exc:
        return _base(stage, str(exc))


def verify_answerability_network_report(
    report_bytes: bytes | bytearray | memoryview,
    directory: str | Path,
    context_bytes: bytes | bytearray | memoryview,
    *,
    limits: AnswerabilityNetworkLimits = AnswerabilityNetworkLimits(),
) -> dict[str, Any]:
    supplied = _parse_json(bytes(report_bytes), "answerability network report")
    computed = verify_answerability_network(directory, context_bytes, limits=limits)
    if not _canonical_equal(supplied, computed):
        raise AnswerabilityNetworkError("answerability network report differs from recomputation")
    return computed


__all__ = ["AnswerabilityNetworkError", "AnswerabilityNetworkLimits", "verify_answerability_network", "verify_answerability_network_report"]
