"""Source-only verifier for the experimental bonded witness covenant.

The covenant covers one objective service invariant: an accepted witness must
not sign different Merkle roots for the same log, authority epoch, and tree
size.  A verified conflict may open a bounded remedy after the authored
challenge checkpoint.  It does not establish receipt truth, occurrence,
complete observation, witness independence, custody, collectibility, or
actual recovery.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from bulla.action_receipt import verify_receipt
from bulla.experimental.checkpoint import WitnessCheckpoint, verify_checkpoint
from bulla.receipt_parser import ReceiptParseLimits, parse_action_receipt_json
from bulla.registry import deed_leaf, verify_inclusion_record


PROFILE = "bulla.witness-covenant/0.1-experimental"
CONTEXT_PROFILE = "bulla.witness-covenant-context/0.1-experimental"
PREDICATE_PROFILE = "bulla.same-size-log-equivocation/1"
UNIT = "USD_CENTS"
INFERENCE_PRICE = 12_500
BOND = 20_000
MAX_REMEDY = 12_500
RAIL = "test-ledger/1"
SAFE_INTEGER = 9_007_199_254_740_991

ROLE_ACTIONS = {
    "provider": "inference.delivery",
    "witness_operator": "assurance.promise.accept",
    "rail_observer": "assurance.collateral.bind",
    "challenge_authority": "assurance.challenge.state",
    "settlement_authority": "assurance.settlement.authorize",
    "settlement_observer": "assurance.fixture-settlement.report",
}

SUBJECT_FIELDS = {
    "inference.delivery": {
        "profile", "issuer_role", "covenant_id", "covenant_hash",
        "proposition_digest", "claim_class", "claim_value",
    },
    "assurance.promise.accept": {
        "profile", "issuer_role", "covenant_id", "covenant_hash",
    },
    "assurance.collateral.bind": {
        "profile", "issuer_role", "covenant_id", "covenant_hash",
        "binding_id", "unit", "locked_amount", "allocation_manifest",
        "reported_unspent", "checkpoint_ref", "rail_adapter",
    },
    "assurance.challenge.state": {
        "profile", "issuer_role", "covenant_id", "covenant_hash",
        "finding_ref", "finding_class", "challenge_state",
        "challenge_checkpoint", "observed_checkpoint", "prior_challenge_ref",
    },
    "assurance.settlement.authorize": {
        "profile", "issuer_role", "covenant_id", "covenant_hash",
        "finding_ref", "finding_class", "challenge_attestation",
        "consequence", "amount", "unit", "destination",
        "capital_binding_id", "challenge_state", "rail_checkpoint",
        "authority_grant",
    },
    "assurance.fixture-settlement.report": {
        "profile", "issuer_role", "covenant_id", "covenant_hash",
        "fixture_report_hash", "authorization_attestation", "amount", "unit",
        "destination", "status", "synthetic", "actual_funds",
    },
}


class WitnessCovenantError(ValueError):
    """The covenant dossier or external context is malformed or unsafe."""


@dataclass(frozen=True)
class WitnessCovenantReport:
    scenario: str
    dossier_integrity: str
    receipt_integrity: str
    signature_status: str
    issuer_role_status: str
    covenant_binding: str
    checkpoint_authenticity: str
    joint_observation: str
    claim_inclusion: str
    history_consistency: str
    same_size_equivocation: str
    witness_trust: str
    witness_control: str
    capital_lock: str
    capital_allocation: str
    challenge: str
    witness_remedy: str
    settlement_authorization: str
    test_ledger_attempt: str
    substantive_truth: str = "NOT_ESTABLISHED"
    occurrence: str = "NOT_ESTABLISHED"
    deterrence: str = "NOT_COMPUTED"
    custody: str = "NOT_COMPUTED"
    collectibility: str = "NOT_COMPUTED"
    actual_funds: str = "NOT_ESTABLISHED"
    suppressed_conclusions: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        raise TypeError("WitnessCovenantReport has no global truth value")

    @property
    def exit_code(self) -> int:
        hard = {
            self.dossier_integrity,
            self.receipt_integrity,
            self.signature_status,
            self.issuer_role_status,
            self.covenant_binding,
            self.checkpoint_authenticity,
            self.joint_observation,
            self.capital_lock,
            self.capital_allocation,
        }
        return 1 if self.errors or "FAILED" in hard or "UNTRUSTED" in hard else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "scenario": self.scenario,
            "dossier_integrity": self.dossier_integrity,
            "receipt_integrity": self.receipt_integrity,
            "signature_status": self.signature_status,
            "issuer_role_status": self.issuer_role_status,
            "covenant_binding": self.covenant_binding,
            "checkpoint_authenticity": self.checkpoint_authenticity,
            "joint_observation": self.joint_observation,
            "claim_inclusion": self.claim_inclusion,
            "history_consistency": self.history_consistency,
            "same_size_equivocation": self.same_size_equivocation,
            "witness_trust": self.witness_trust,
            "witness_control": self.witness_control,
            "capital_lock": self.capital_lock,
            "capital_allocation": self.capital_allocation,
            "challenge": self.challenge,
            "witness_remedy": self.witness_remedy,
            "settlement_authorization": self.settlement_authorization,
            "test_ledger_attempt": self.test_ledger_attempt,
            "substantive_truth": self.substantive_truth,
            "occurrence": self.occurrence,
            "deterrence": self.deterrence,
            "custody": self.custody,
            "collectibility": self.collectibility,
            "actual_funds": self.actual_funds,
            "suppressed_conclusions": list(self.suppressed_conclusions),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "exit_code": self.exit_code,
        }


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_hash(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        return False
    return all(character in "0123456789abcdef" for character in value[7:])


def _is_safe_int(value: Any, *, nonnegative: bool = False) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) <= SAFE_INTEGER
        and (not nonnegative or value >= 0)
    )


def _exact(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise WitnessCovenantError(f"{label} must contain exactly {sorted(fields)}")
    return value


def _parse_json(raw: bytes, label: str) -> Any:
    if len(raw) > 262_144:
        raise WitnessCovenantError(f"{label} exceeds 256 KiB")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise WitnessCovenantError(f"{label} contains duplicate member {key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WitnessCovenantError(f"{label} is not strict UTF-8 JSON") from exc

    nodes = 0

    def walk(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 10_000 or depth > 20:
            raise WitnessCovenantError(f"{label} exceeds structural limits")
        if isinstance(item, str):
            if len(item) > 4_096 or len(item.encode("utf-8")) > 16_384:
                raise WitnessCovenantError(f"{label} contains an oversized string")
            return
        if isinstance(item, bool) or item is None:
            return
        if isinstance(item, int):
            if abs(item) > SAFE_INTEGER:
                raise WitnessCovenantError(f"{label} contains an unsafe integer")
            return
        if isinstance(item, float):
            raise WitnessCovenantError(f"{label} contains a non-integer")
        if isinstance(item, list):
            for child in item:
                walk(child, depth + 1)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                walk(key, depth + 1)
                walk(child, depth + 1)
            return
        raise WitnessCovenantError(f"{label} contains unsupported JSON")

    walk(value, 1)
    return value


def _read_dossier(directory: Path) -> dict[str, bytes]:
    if directory.is_symlink() or not directory.is_dir():
        raise WitnessCovenantError("dossier root must be a real directory")
    files: dict[str, bytes] = {}
    total = 0
    for root, dirs, names in os.walk(directory, followlinks=False):
        root_path = Path(root)
        for name in dirs:
            child = root_path / name
            if child.is_symlink():
                raise WitnessCovenantError("dossier contains a symlink directory")
        for name in names:
            child = root_path / name
            mode = child.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise WitnessCovenantError("dossier contains a non-regular member")
            relative = child.relative_to(directory).as_posix()
            pure = PurePosixPath(relative)
            if (
                "\\" in relative
                or pure.is_absolute()
                or any(part in {"", ".", ".."} for part in pure.parts)
            ):
                raise WitnessCovenantError("dossier contains an unsafe path")
            raw = child.read_bytes()
            if len(raw) > 262_144:
                raise WitnessCovenantError(f"{relative} exceeds the per-member byte limit")
            total += len(raw)
            if len(files) >= 64 or total > 4_194_304:
                raise WitnessCovenantError("dossier exceeds resource limits")
            files[relative] = raw
    return files


def _enforce_receipt_string_limits(value: Any, label: str) -> None:
    """Mirror the standalone kernel's scalar and UTF-8 limits after stable parsing."""
    if isinstance(value, str):
        if len(value) > 4_096 or len(value.encode("utf-8")) > 16_384:
            raise WitnessCovenantError(f"{label} contains an oversized string")
        return
    if isinstance(value, list):
        for item in value:
            _enforce_receipt_string_limits(item, label)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _enforce_receipt_string_limits(key, label)
            _enforce_receipt_string_limits(item, label)


def _base_report(scenario: str, error: str) -> WitnessCovenantReport:
    return WitnessCovenantReport(
        scenario=scenario,
        dossier_integrity="FAILED",
        receipt_integrity="NOT_COMPUTED",
        signature_status="NOT_COMPUTED",
        issuer_role_status="NOT_COMPUTED",
        covenant_binding="NOT_COMPUTED",
        checkpoint_authenticity="NOT_COMPUTED",
        joint_observation="NOT_COMPUTED",
        claim_inclusion="NOT_COMPUTED",
        history_consistency="NOT_COMPUTED",
        same_size_equivocation="NOT_COMPUTED",
        witness_trust="NOT_COMPUTED",
        witness_control="PROJECT_OPERATED",
        capital_lock="NOT_COMPUTED",
        capital_allocation="NOT_COMPUTED",
        challenge="NOT_COMPUTED",
        witness_remedy="NOT_COMPUTED",
        settlement_authorization="NOT_COMPUTED",
        test_ledger_attempt="NOT_COMPUTED",
        suppressed_conclusions=("all protected conclusions",),
        errors=(error,),
    )


def _parse_context(raw: bytes) -> Mapping[str, Any]:
    context = _exact(
        _parse_json(raw, "verification context"),
        {
            "profile", "accepted_operator", "accepted_log", "authority_epoch",
            "accepted_covenant_hash", "accepted_issuers_by_role",
            "accepted_authority_grants", "accepted_rail_adapters",
            "accepted_capital_checkpoint", "controlled_roles",
        },
        "verification context",
    )
    if context["profile"] != CONTEXT_PROFILE:
        raise WitnessCovenantError("unsupported verification context")
    roles = set(ROLE_ACTIONS)
    issuers = _exact(context["accepted_issuers_by_role"], roles, "accepted issuers")
    if any(not isinstance(issuers[role], list) or len(issuers[role]) != 1 for role in roles):
        raise WitnessCovenantError("each protected role needs one accepted issuer")
    if context["accepted_rail_adapters"] != [RAIL]:
        raise WitnessCovenantError("unsupported rail adapter")
    if not _is_hash(context["accepted_capital_checkpoint"]):
        raise WitnessCovenantError("accepted capital checkpoint is invalid")
    if (
        not isinstance(context["accepted_authority_grants"], list)
        or not context["accepted_authority_grants"]
        or any(not _is_hash(item) for item in context["accepted_authority_grants"])
    ):
        raise WitnessCovenantError("accepted authority grants are invalid")
    if (
        not isinstance(context["controlled_roles"], list)
        or set(context["controlled_roles"]) != roles
        or len(context["controlled_roles"]) != len(roles)
    ):
        raise WitnessCovenantError("canonical roles must be disclosed as project-operated")
    return context


def _verify_receipts(
    core: Mapping[str, Any], files: Mapping[str, bytes], context: Mapping[str, Any]
) -> tuple[list[Mapping[str, Any]], list[str]]:
    receipts: list[Mapping[str, Any]] = []
    errors: list[str] = []
    for manifest in core["receipt_manifest"]:
        path = manifest["path"]
        try:
            document = parse_action_receipt_json(
                files[path],
                limits=ReceiptParseLimits(
                    max_bytes=262_144, max_depth=20, max_nodes=10_000,
                    max_string_bytes=16_384,
                ),
            ).to_dict()
            _enforce_receipt_string_limits(document, path)
            verification = verify_receipt(document)
            if (
                not verification.ok
                or verification.verified_to != "attestation"
                or verification.authority_authentic != "verified"
            ):
                raise WitnessCovenantError(f"{path} does not verify to authorized attestation")
            action = _exact(document["action"], {"type", "subject"}, f"{path} action")
            role = manifest["role"]
            if action["type"] != ROLE_ACTIONS.get(role) or action["type"] != manifest["action_type"]:
                raise WitnessCovenantError(f"{path} action/role pair is unsupported")
            subject = _exact(action["subject"], SUBJECT_FIELDS[action["type"]], f"{path} subject")
            if (
                not isinstance(document["event_id"], str)
                or re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                    document["event_id"],
                ) is None
                or not isinstance(document["claimed_at"], str)
                or re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
                    document["claimed_at"],
                ) is None
            ):
                raise WitnessCovenantError(f"{path} has malformed event metadata")
            if (
                _exact(document["diagnostic_ref"], {"status"}, f"{path} diagnostic")["status"]
                != "not_applicable"
                or document["evidence_refs"] != []
                or _exact(document["anchor_ref"], set(), f"{path} anchor") != {}
                or document["conventions"] != []
                or document["stake"] is not None
            ):
                raise WitnessCovenantError(f"{path} uses unsupported reserved fields")
            mandate = _exact(document["mandate"], {"authority", "bounds"}, f"{path} mandate")
            authority = _exact(
                mandate["authority"], {"principal", "policy", "delegation"},
                f"{path} authority",
            )
            bounds = _exact(mandate["bounds"], {"scope"}, f"{path} bounds")
            remedy = _exact(
                document["remedy"], {"challenge_window", "forum", "remedies"},
                f"{path} remedy",
            )
            forum = _exact(
                remedy["forum"], {"log_endpoint", "trusted_root_ref"}, f"{path} forum",
            )
            remedies = remedy["remedies"]
            if (
                authority["delegation"] != []
                or authority["policy"] != f"policy://witness-covenant/{action['type']}"
                or bounds["scope"] != f"profile:{PROFILE}"
                or remedy["challenge_window"] != "checkpoint:witness-covenant-5"
                or forum["log_endpoint"]
                != "https://glyphstandard.com/bulla/answerable-computing"
                or not _is_hash(forum["trusted_root_ref"])
                or not isinstance(remedies, list)
                or len(remedies) != 1
                or _exact(remedies[0], {"anchor", "rung", "verifier"}, f"{path} remedy item")
                != {
                    "anchor": "forum:witness-covenant",
                    "rung": "challenge",
                    "verifier": "verify the covenant dossier under separately supplied trust roots",
                }
                or _exact(document["retention"], {"disclosure", "record"}, f"{path} retention")
                != {"disclosure": "public", "record": "operational"}
            ):
                raise WitnessCovenantError(f"{path} has an unsupported covenant envelope")
            if (
                subject["profile"] != PROFILE
                or subject["issuer_role"] != role
                or subject["covenant_id"] != core["covenant_id"]
                or subject["covenant_hash"] != core["covenant"]["covenant_hash"]
            ):
                raise WitnessCovenantError(f"{path} is outside covenant lineage")
            issuer = document["signature"]["issuer"]
            if (
                issuer != core["role_issuers"][role]
                or issuer not in context["accepted_issuers_by_role"][role]
                or document["hashes"]["event"] != manifest["event"]
                or document["hashes"]["attestation"] != manifest["attestation"]
            ):
                raise WitnessCovenantError(f"{path} issuer or manifest binding is unaccepted")
            if authority["principal"] != issuer:
                raise WitnessCovenantError(f"{path} authority is not signer-bound")
            receipts.append(document)
        except Exception as exc:
            errors.append(str(exc))
    return receipts, errors


def _receipt_by(
    receipts: list[Mapping[str, Any]], role: str
) -> list[Mapping[str, Any]]:
    return [item for item in receipts if item["action"]["subject"]["issuer_role"] == role]


def verify_witness_covenant(
    directory: str | Path,
    context_bytes: bytes,
    *,
    limits: Mapping[str, int] | None = None,
) -> WitnessCovenantReport:
    """Verify one closed covenant dossier under separately supplied trust roots."""
    del limits  # v0.1 has frozen internal limits; parameter is reserved source-only API shape.
    scenario = "UNKNOWN"
    try:
        files = _read_dossier(Path(directory))
        if "covenant-core.json" not in files:
            raise WitnessCovenantError("covenant-core.json is missing")
        core = _exact(
            _parse_json(files["covenant-core.json"], "covenant core"),
            {
                "profile", "revision", "scenario", "covenant_id", "covenant",
                "role_issuers", "receipt_manifest", "artifact_manifest",
            },
            "covenant core",
        )
        scenario = core["scenario"]
        if (
            core["profile"] != PROFILE
            or isinstance(core["revision"], bool)
            or not isinstance(core["revision"], int)
            or core["revision"] != 1
        ):
            raise WitnessCovenantError("unsupported covenant profile")
        context = _parse_context(context_bytes)

        covenant = _exact(
            core["covenant"],
            {
                "covenant_hash", "operator", "key_identifier", "log_id",
                "authority_epoch", "checkpoint_profile", "covered_duty",
                "fault_predicate", "challenge_checkpoint", "correction_path",
                "beneficiary", "settlement_authority", "destination",
                "maximum_remedy", "capital",
            },
            "covenant",
        )
        capital = _exact(
            covenant["capital"],
            {
                "binding_id", "unit", "required_allocation", "binding_mode",
                "rail_adapter",
            },
            "capital policy",
        )
        challenge_checkpoint = _exact(
            covenant["challenge_checkpoint"], {"domain", "value"},
            "challenge checkpoint",
        )
        body = {key: value for key, value in covenant.items() if key != "covenant_hash"}
        if covenant["covenant_hash"] != canonical_hash(body):
            raise WitnessCovenantError("covenant hash does not match its canonical body")
        if (
            covenant["fault_predicate"] != PREDICATE_PROFILE
            or covenant["covered_duty"] != "sign no different roots for one log, epoch, and tree size"
            or covenant["checkpoint_profile"] != "bulla.witness-checkpoint/0.1-draft"
            or covenant["key_identifier"] != covenant["operator"]
            or not _is_safe_int(covenant["maximum_remedy"], nonnegative=True)
            or covenant["maximum_remedy"] != MAX_REMEDY
            or challenge_checkpoint["domain"] != "accepted-checkpoint-height"
            or not _is_safe_int(challenge_checkpoint["value"], nonnegative=True)
            or challenge_checkpoint["value"] != 5
            or capital["unit"] != UNIT
            or not _is_safe_int(capital["required_allocation"], nonnegative=True)
            or capital["required_allocation"] != BOND
            or capital["binding_mode"] != "DEDICATED"
            or capital["rail_adapter"] != RAIL
        ):
            raise WitnessCovenantError("covenant differs from the closed v0.1 profile")
        if (
            covenant["operator"] != context["accepted_operator"]
            or covenant["log_id"] != context["accepted_log"]
            or covenant["authority_epoch"] != context["authority_epoch"]
            or covenant["covenant_hash"] != context["accepted_covenant_hash"]
        ):
            raise WitnessCovenantError("external context does not accept this covenant")
        roles = set(ROLE_ACTIONS)
        _exact(core["role_issuers"], roles, "role issuers")
        if len(set(core["role_issuers"].values())) != len(roles):
            raise WitnessCovenantError("protected roles must use distinct fixture issuers")
        if any(
            context["accepted_issuers_by_role"][role] != [core["role_issuers"][role]]
            for role in roles
        ):
            raise WitnessCovenantError("external context does not accept every named role issuer")
        if covenant["settlement_authority"] != core["role_issuers"]["settlement_authority"]:
            raise WitnessCovenantError("named settlement authority is not the accepted role issuer")

        declared = {"covenant-core.json"}
        for manifest in [*core["receipt_manifest"], *core["artifact_manifest"]]:
            entry = _exact(
                manifest,
                {"path", "sha256", "byte_length", "media_type"}
                | ({"role", "action_type", "event", "attestation"} if "role" in manifest else set()),
                "manifest entry",
            )
            path = entry["path"]
            if entry["media_type"] != "application/json":
                raise WitnessCovenantError("the closed covenant permits JSON members only")
            if path in declared or path not in files:
                raise WitnessCovenantError("manifest has a duplicate or missing member")
            raw = files[path]
            _parse_json(raw, path)
            if (
                not _is_safe_int(entry["byte_length"], nonnegative=True)
                or entry["sha256"] != "sha256:" + hashlib.sha256(raw).hexdigest()
                or entry["byte_length"] != len(raw)
            ):
                raise WitnessCovenantError(f"{path} does not match its manifest")
            declared.add(path)
        if set(files) != declared:
            raise WitnessCovenantError("dossier has undeclared members")

        receipts, receipt_errors = _verify_receipts(core, files, context)
        if receipt_errors or len(receipts) != len(core["receipt_manifest"]):
            raise WitnessCovenantError("; ".join(receipt_errors or ["receipt set is incomplete"]))

        acceptance = _receipt_by(receipts, "witness_operator")
        capital_receipts = _receipt_by(receipts, "rail_observer")
        if len(acceptance) != 1 or len(capital_receipts) > 1:
            raise WitnessCovenantError("one covenant acceptance and at most one capital binding are allowed")
        if acceptance[0]["signature"]["issuer"] != covenant["operator"]:
            raise WitnessCovenantError("the accepted witness operator did not sign the covenant")
        allocation = capital_receipts[0]["action"]["subject"] if capital_receipts else None
        capital_adequate = allocation is not None
        if allocation is not None:
            manifest = allocation["allocation_manifest"]
            if not isinstance(manifest, list) or len(manifest) != 1:
                raise WitnessCovenantError("dedicated allocation manifest must contain one item")
            item = _exact(manifest[0], {"covenant_hash", "amount", "active"}, "allocation item")
            if (
                allocation["binding_id"] != capital["binding_id"]
                or allocation["unit"] != UNIT
                or not _is_safe_int(allocation["locked_amount"], nonnegative=True)
                or allocation["locked_amount"] != BOND
                or item["covenant_hash"] != covenant["covenant_hash"]
                or not _is_safe_int(item["amount"], nonnegative=True)
                or item["amount"] != BOND
                or item["active"] is not True
                or allocation["reported_unspent"] is not True
                or allocation["checkpoint_ref"] != context["accepted_capital_checkpoint"]
                or allocation["rail_adapter"] != RAIL
            ):
                raise WitnessCovenantError("dedicated capital allocation is inadequate or misbound")

        heads: list[WitnessCheckpoint] = []
        for name in ("evidence/head-a.json", "evidence/head-b.json"):
            raw = _parse_json(files[name], name)
            if (
                not isinstance(raw, Mapping)
                or not _is_safe_int(raw.get("tree_size"), nonnegative=True)
                or not _is_safe_int(raw.get("position"), nonnegative=True)
                or raw["position"] != raw["tree_size"]
            ):
                raise WitnessCovenantError(f"{name} has invalid checkpoint positions")
            verification = verify_checkpoint(raw)
            if not verification.ok:
                raise WitnessCovenantError(f"{name} is not an authentic checkpoint")
            head = WitnessCheckpoint.from_dict(raw)
            if (
                head.operator != covenant["operator"]
                or head.log_id != covenant["log_id"]
                or head.anchor_evidence != {
                    "authority_epoch": covenant["authority_epoch"],
                    "covenant_hash": covenant["covenant_hash"],
                }
            ):
                raise WitnessCovenantError(f"{name} is outside the accepted covenant")
            heads.append(head)

        head_a, head_b = heads
        same_domain = (
            head_a.operator == head_b.operator
            and head_a.log_id == head_b.log_id
            and head_a.anchor_evidence == head_b.anchor_evidence
            and head_a.tree_size == head_b.tree_size
        )
        if not same_domain:
            raise WitnessCovenantError("checkpoint views are not comparable under the fault predicate")
        equivocation = head_a.root != head_b.root
        equivocation_finding_ref = canonical_hash({
            "predicate": PREDICATE_PROFILE,
            "head_a": head_a.checkpoint_hash,
            "head_b": head_b.checkpoint_hash,
        })

        provider_claims = _receipt_by(receipts, "provider")
        if len(provider_claims) > 1:
            raise WitnessCovenantError("at most one provider claim is supported")
        claim_inclusion = "NOT_APPLICABLE"
        claim_finding_ref: str | None = None
        if provider_claims:
            claim = provider_claims[0]
            claim_subject = claim["action"]["subject"]
            if claim_subject["claim_class"] != "MODEL_IDENTITY_P3":
                raise WitnessCovenantError("the closed profile supports only MODEL_IDENTITY_P3 claims")
            inclusion = _exact(
                _parse_json(files["evidence/model-claim-inclusion.json"], "model claim inclusion"),
                {"attestation", "index", "tree_size", "leaf", "proof", "root"},
                "model claim inclusion",
            )
            proof = inclusion["proof"]
            expected_leaf = deed_leaf({
                "issuer": claim["signature"]["issuer"],
                "content_hash": claim["hashes"]["content"],
                "attestation_hash": claim["hashes"]["attestation"],
            })
            if (
                not _is_hash(inclusion["attestation"])
                or not _is_safe_int(inclusion["index"], nonnegative=True)
                or not _is_safe_int(inclusion["tree_size"], nonnegative=True)
                or inclusion["tree_size"] == 0
                or inclusion["index"] >= inclusion["tree_size"]
                or not _is_hash(inclusion["leaf"])
                or not _is_hash(inclusion["root"])
                or not isinstance(proof, list)
                or any(not _is_hash(item) for item in proof)
                or inclusion["attestation"] != claim["hashes"]["attestation"]
                or inclusion["tree_size"] != head_a.tree_size
                or inclusion["root"] != head_a.root
                or not verify_inclusion_record(
                    dict(inclusion), trusted_root=head_a.root, expected_leaf=expected_leaf,
                )
            ):
                raise WitnessCovenantError("provider model claim lacks leaf-bound inclusion")
            claim_inclusion = "VERIFIED"
            claim_finding_ref = canonical_hash({
                "claim_attestation": claim["hashes"]["attestation"],
                "proposition_digest": claim_subject["proposition_digest"],
                "claim_class": claim_subject["claim_class"],
            })

        challenges = _receipt_by(receipts, "challenge_authority")
        challenge = "NOT_APPLICABLE"
        challenge_attestation: str | None = None
        objective_finding = "SAME_SIZE_LOG_EQUIVOCATION"
        initial_finding: str | None = None
        if challenges:
            if len(challenges) > 2:
                raise WitnessCovenantError("too many challenge transitions")
            prior: str | None = None
            for index, receipt in enumerate(challenges):
                subject = receipt["action"]["subject"]
                finding_class = subject["finding_class"]
                if finding_class == "SAME_SIZE_LOG_EQUIVOCATION":
                    expected_finding_ref = equivocation_finding_ref
                elif finding_class == "MODEL_IDENTITY_P3" and claim_finding_ref is not None:
                    expected_finding_ref = claim_finding_ref
                else:
                    raise WitnessCovenantError("unsupported or ungrounded finding class")
                if (
                    subject["finding_ref"] != expected_finding_ref
                    or subject["challenge_checkpoint"] != covenant["challenge_checkpoint"]
                    or subject["prior_challenge_ref"] != prior
                ):
                    raise WitnessCovenantError("challenge transition is not bound to the finding chronology")
                if initial_finding is None:
                    initial_finding = subject["finding_class"]
                elif subject["finding_class"] != initial_finding:
                    raise WitnessCovenantError("challenge transition changes finding class")
                observed = subject["observed_checkpoint"]
                if not _is_safe_int(observed, nonnegative=True):
                    raise WitnessCovenantError("observed checkpoint must be a non-negative safe integer")
                if index == 0 and subject["challenge_state"] != "OPEN":
                    raise WitnessCovenantError("first challenge transition must be OPEN")
                if index == 1 and subject["challenge_state"] != "EXPIRED":
                    raise WitnessCovenantError("second challenge transition must be EXPIRED")
                if subject["challenge_state"] == "OPEN" and subject["observed_checkpoint"] >= covenant["challenge_checkpoint"]["value"]:
                    raise WitnessCovenantError("OPEN challenge is stale")
                if subject["challenge_state"] == "EXPIRED" and subject["observed_checkpoint"] < covenant["challenge_checkpoint"]["value"]:
                    raise WitnessCovenantError("EXPIRED challenge is premature")
                if subject["finding_class"] != objective_finding:
                    objective_finding = subject["finding_class"]
                prior = receipt["hashes"]["attestation"]
                challenge_attestation = prior
                challenge = subject["challenge_state"]

        protected_fault = equivocation and objective_finding == "SAME_SIZE_LOG_EQUIVOCATION"
        if not equivocation and objective_finding == "SAME_SIZE_LOG_EQUIVOCATION":
            challenge = "NOT_APPLICABLE"
        elif objective_finding != "SAME_SIZE_LOG_EQUIVOCATION":
            challenge = "CHALLENGE_REQUIRED"

        remedy = "INELIGIBLE"
        if objective_finding != "SAME_SIZE_LOG_EQUIVOCATION":
            remedy = "CHALLENGE_REQUIRED"
        elif protected_fault and challenge == "OPEN":
            remedy = "CHALLENGE_REQUIRED"
        elif protected_fault and challenge == "EXPIRED" and capital_adequate:
            remedy = "ELIGIBLE"

        authorizations = _receipt_by(receipts, "settlement_authority")
        authorization_status = "NOT_ISSUED"
        authorization_attestation: str | None = None
        if authorizations:
            if len(authorizations) != 1 or remedy != "ELIGIBLE":
                raise WitnessCovenantError("settlement authorization is not permitted")
            authorization = authorizations[0]
            subject = authorization["action"]["subject"]
            if (
                subject["finding_ref"] != equivocation_finding_ref
                or subject["finding_class"] != "SAME_SIZE_LOG_EQUIVOCATION"
                or subject["challenge_attestation"] != challenge_attestation
                or subject["consequence"] != "TRANSFER_COLLATERAL"
                or not _is_safe_int(subject["amount"], nonnegative=True)
                or subject["amount"] != MAX_REMEDY
                or subject["unit"] != UNIT
                or subject["destination"] != covenant["destination"]
                or allocation is None
                or subject["capital_binding_id"] != capital["binding_id"]
                or subject["challenge_state"] != "EXPIRED"
                or subject["rail_checkpoint"] != allocation["checkpoint_ref"]
                or subject["authority_grant"] not in context["accepted_authority_grants"]
            ):
                raise WitnessCovenantError("settlement authorization is misbound")
            authorization_status = "VERIFIED"
            authorization_attestation = authorization["hashes"]["attestation"]

        observers = _receipt_by(receipts, "settlement_observer")
        attempt = "NOT_REPORTED"
        if observers:
            if len(observers) != 1 or authorization_status != "VERIFIED":
                raise WitnessCovenantError("test-ledger report lacks valid authorization")
            subject = observers[0]["action"]["subject"]
            fixture_bytes = files["settlement/test-ledger-report.json"]
            fixture = _exact(
                _parse_json(fixture_bytes, "test-ledger report"),
                {
                    "profile", "covenant_hash", "authorization_attestation",
                    "status", "amount", "unit", "destination", "synthetic",
                    "actual_funds",
                },
                "test-ledger report",
            )
            if (
                subject["fixture_report_hash"] != "sha256:" + hashlib.sha256(fixture_bytes).hexdigest()
                or subject["authorization_attestation"] != authorization_attestation
                or not _is_safe_int(subject["amount"], nonnegative=True)
                or subject["amount"] != MAX_REMEDY
                or subject["unit"] != UNIT
                or subject["destination"] != covenant["destination"]
                or subject["status"] != "ATTEMPT_REPORTED"
                or subject["synthetic"] is not True
                or subject["actual_funds"] is not False
                or not _is_safe_int(fixture["amount"], nonnegative=True)
                or fixture["synthetic"] is not True
                or fixture["actual_funds"] is not False
                or fixture != {
                    "profile": "bulla.test-ledger/1",
                    "covenant_hash": covenant["covenant_hash"],
                    "authorization_attestation": authorization_attestation,
                    "status": "ATTEMPT_REPORTED",
                    "amount": MAX_REMEDY,
                    "unit": UNIT,
                    "destination": covenant["destination"],
                    "synthetic": True,
                    "actual_funds": False,
                }
            ):
                raise WitnessCovenantError("test-ledger report is misbound")
            attempt = "REPORTED"

        derived_scenario = (
            "model-dispute"
            if objective_finding == "MODEL_IDENTITY_P3"
            else "consistent"
            if not equivocation
            else "fork-open"
            if challenge == "OPEN"
            else "fork-authorized"
            if challenge == "EXPIRED" and authorization_status == "VERIFIED" and attempt == "REPORTED"
            else "fork-closed-no-bond"
            if challenge == "EXPIRED" and not capital_adequate
            else "fork-closed"
            if challenge == "EXPIRED"
            else "UNRESOLVED"
        )
        if scenario != derived_scenario:
            raise WitnessCovenantError("scenario label differs from authenticated facts")

        history = "EQUIVOCATION_ESTABLISHED" if equivocation else "CONSISTENT_AT_COMPARED_SIZE"
        warnings = (
            "bond amount is an authored term, not a risk estimate",
            "checkpoint signatures establish operator statements, not worldly truth",
        )
        suppressed: list[str] = []
        if remedy != "ELIGIBLE":
            suppressed.append("automatic witness remedy")
        if authorization_status != "VERIFIED":
            suppressed.append("settlement authorization")
        return WitnessCovenantReport(
            scenario=scenario,
            dossier_integrity="VERIFIED",
            receipt_integrity="VERIFIED",
            signature_status="VERIFIED",
            issuer_role_status="VERIFIED",
            covenant_binding="VERIFIED",
            checkpoint_authenticity="VERIFIED",
            joint_observation="VERIFIED",
            claim_inclusion=claim_inclusion,
            history_consistency=history,
            same_size_equivocation="ESTABLISHED" if equivocation else "NOT_ESTABLISHED",
            witness_trust="SUPPLIED_CONTEXT_ACCEPTED",
            witness_control="PROJECT_OPERATED",
            capital_lock="REPORTED_UNSPENT" if capital_adequate else "ABSENT",
            capital_allocation="ADEQUATE_DEDICATED" if capital_adequate else "INADEQUATE",
            challenge=challenge,
            witness_remedy=remedy,
            settlement_authorization=authorization_status,
            test_ledger_attempt=attempt,
            suppressed_conclusions=tuple(suppressed),
            warnings=warnings,
        )
    except Exception as exc:
        return _base_report(scenario, str(exc))


def verify_witness_covenant_report(
    report_bytes: bytes,
    directory: str | Path,
    context_bytes: bytes,
    *,
    limits: Mapping[str, int] | None = None,
) -> WitnessCovenantReport:
    """Recompute a covenant report and require exact report equality."""
    computed = verify_witness_covenant(directory, context_bytes, limits=limits)
    try:
        supplied = _parse_json(report_bytes, "witness covenant report")
        supplied_canonical = json.dumps(
            supplied, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        computed_canonical = json.dumps(
            computed.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        if supplied_canonical != computed_canonical:
            return _base_report(computed.scenario, "supplied report differs from recomputation")
    except Exception as exc:
        return _base_report(computed.scenario, str(exc))
    return computed
