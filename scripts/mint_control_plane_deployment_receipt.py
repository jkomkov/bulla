#!/usr/bin/env python3
"""Validate or mint evidence for one Bulla control-plane deployment.

The receipt binds the immutable Worker version to the source commit, external
trust context, final standards mapping review, routing states, the candidate
outer-Worker smoke, and the promoted full-loop smoke. The candidate report
explicitly makes no Durable Object or full-loop claim; the promoted full loop
supplies that attestation. ``--preflight-only`` verifies trust and review inputs
before any Worker version is uploaded. Production minting deliberately has no
unsigned fallback.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bulla" / "src"))

from bulla import __version__  # noqa: E402
from bulla.action_receipt import (  # noqa: E402
    build_action_receipt_v04,
    sign_action_receipt_v04,
    verify_receipt,
)
from bulla.envelope import (  # noqa: E402
    Authority,
    Bounds,
    Forum,
    Recourse,
    RecourseEnvelope,
    Remedy,
)
from bulla.identity import (  # noqa: E402
    LocalEd25519Signer,
    pubkey_from_did_key,
    verify_proof_domain,
)
from bulla._canonical import canonical_json  # noqa: E402

PROFILE = "bulla.control-plane-alpha/0.1-experimental"
POLICY_DIGEST = (
    "sha256:fe2a76d9dc104e3a4b97fd173e3670e05fd8b79123f5e7126d76e4331b8e810d"
)
ROLES = (
    "evaluation_authority",
    "boundary",
    "target",
    "incident_commander",
    "publisher",
    "witness",
)
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
WORKER_VERSION_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
REQUIRED_MCP_IMPLEMENTATION_FILES = (
    ".github/workflows/bulla-control-plane-alpha-deploy.yml",
    "bulla/scripts/mint_control_plane_deployment_receipt.py",
    "bulla/spec/control-plane-alpha/PROFILE.md",
    "bulla/spec/control-plane-alpha/THREAT-MODEL.md",
    "glyph/src/lib/control-plane-alpha.ts",
    "package.json",
    "packages/bulla-control-plane-alpha/fixtures/deployment-blockers.json",
    "packages/bulla-control-plane-alpha/fixtures/mcp-2026-07-28-implemented-subset.json",
    "packages/bulla-control-plane-alpha/fixtures/service-manifest-contract.json",
    "packages/bulla-control-plane-alpha/package.json",
    "packages/bulla-control-plane-alpha/scripts/bounded-fetch.mjs",
    "packages/bulla-control-plane-alpha/scripts/call-fixed-mcp.mjs",
    "packages/bulla-control-plane-alpha/scripts/capability-secret.mjs",
    "packages/bulla-control-plane-alpha/scripts/check-source-deployment-projection.mjs",
    "packages/bulla-control-plane-alpha/scripts/deployment-gate.mjs",
    "packages/bulla-control-plane-alpha/scripts/endpoint-policy.mjs",
    "packages/bulla-control-plane-alpha/scripts/generate-fixtures.mjs",
    "packages/bulla-control-plane-alpha/scripts/key-ceremony.mjs",
    "packages/bulla-control-plane-alpha/scripts/live-witness-verifier.mjs",
    "packages/bulla-control-plane-alpha/scripts/materialize-packet.mjs",
    "packages/bulla-control-plane-alpha/scripts/packet-manifest.mjs",
    "packages/bulla-control-plane-alpha/scripts/policy-contract.mjs",
    "packages/bulla-control-plane-alpha/scripts/run-loopback-walkthrough.mjs",
    "packages/bulla-control-plane-alpha/scripts/run-production-preflight.mjs",
    "packages/bulla-control-plane-alpha/scripts/smoke-live.mjs",
    "packages/bulla-control-plane-alpha/scripts/source-deployment-state.mjs",
    "packages/bulla-control-plane-alpha/scripts/strict-json.mjs",
    "packages/bulla-control-plane-alpha/scripts/verify-mcp-final-subset.mjs",
    "packages/bulla-control-plane-alpha/scripts/wrangler-dry-run.mjs",
    "packages/bulla-control-plane-alpha/src/bootstrap.ts",
    "packages/bulla-control-plane-alpha/src/browser-types.ts",
    "packages/bulla-control-plane-alpha/src/browser.ts",
    "packages/bulla-control-plane-alpha/src/cloudflare.d.ts",
    "packages/bulla-control-plane-alpha/src/constants.ts",
    "packages/bulla-control-plane-alpha/src/crypto.ts",
    "packages/bulla-control-plane-alpha/src/durable.ts",
    "packages/bulla-control-plane-alpha/src/http.ts",
    "packages/bulla-control-plane-alpha/src/keys.ts",
    "packages/bulla-control-plane-alpha/src/merkle.ts",
    "packages/bulla-control-plane-alpha/src/policy.ts",
    "packages/bulla-control-plane-alpha/src/receipt.ts",
    "packages/bulla-control-plane-alpha/src/runtime.ts",
    "packages/bulla-control-plane-alpha/src/strict-json.ts",
    "packages/bulla-control-plane-alpha/src/types.ts",
    "packages/bulla-control-plane-alpha/src/worker.ts",
    "packages/bulla-control-plane-alpha/tsconfig.json",
    "packages/bulla-control-plane-alpha/wrangler.bootstrap.jsonc",
    "packages/bulla-control-plane-alpha/wrangler.jsonc",
    "pnpm-lock.yaml",
)
CANDIDATE_SMOKE_PHASE = "CANDIDATE_ZERO_TRAFFIC"
FINAL_SMOKE_PHASE = "PROMOTED_100_PERCENT"
CANDIDATE_DURABLE_OBJECT_ASSIGNMENT = (
    "NOT_ATTESTED_AT_CANDIDATE_ZERO_TRAFFIC"
)
FINAL_DURABLE_OBJECT_ASSIGNMENT = "ATTESTED_BY_PROMOTED_FULL_LOOP"
CANDIDATE_ROUTING_PHASE = "CANDIDATE_100_0"
FINAL_ROUTING_PHASE = "PROMOTED_100"


def _byte_hash(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _reject_json_value(value: str) -> None:
    raise ValueError(f"unsupported JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def _bounded_integer(value: str) -> int:
    parsed = int(value)
    if abs(parsed) > 9_007_199_254_740_991:
        raise ValueError("JSON integer exceeds the portable safe range")
    return parsed


def _enforce_json_limits(value: object) -> None:
    nodes = 0
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > 4_096 or depth > 24:
            raise ValueError("evidence JSON exceeds structural limits")
        if isinstance(current, str):
            try:
                size = len(current.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise ValueError(
                    "evidence JSON contains a lone Unicode surrogate"
                ) from exc
            if size > 16_384:
                raise ValueError("evidence JSON contains an oversized string")
        elif isinstance(current, dict):
            for key, item in current.items():
                stack.append((key, depth + 1))
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def _load_json_bytes(path: Path) -> tuple[bytes, dict]:
    encoded = path.read_bytes()
    if len(encoded) > 65_536:
        raise ValueError(f"JSON evidence exceeds 65536 bytes: {path}")
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"JSON evidence is not UTF-8: {path}") from exc
    document = json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_int=_bounded_integer,
        parse_float=_reject_json_value,
        parse_constant=_reject_json_value,
    )
    if not isinstance(document, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    _enforce_json_limits(document)
    return encoded, document


def _load_key(path: Path | None) -> dict:
    if path is not None:
        return _load_json_bytes(path)[1]
    encoded = os.environ.get("BULLA_CONTROL_PLANE_DEPLOY_KEY")
    if not encoded:
        raise ValueError(
            "BULLA_CONTROL_PLANE_DEPLOY_KEY or --key is required; "
            "production service receipts cannot be unsigned"
        )
    return json.loads(encoded)


def _base64url_decode(value: object, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"private JWK {field} is required")
    try:
        return base64.b64decode(
            value.replace("-", "+").replace("_", "/")
            + "=" * ((4 - len(value) % 4) % 4),
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"private JWK {field} is malformed") from exc


def _signer_from_key(document: dict) -> LocalEd25519Signer:
    if document.get("kty") == "OKP":
        if document.get("crv") != "Ed25519":
            raise ValueError("private JWK must use Ed25519")
        seed = _base64url_decode(document.get("d"), "d")
        public = _base64url_decode(document.get("x"), "x")
        if len(seed) != 32 or len(public) != 32:
            raise ValueError("private JWK Ed25519 coordinates must be 32 bytes")
        signer = LocalEd25519Signer(seed=seed)
        if not hmac.compare_digest(signer.public_key, public):
            raise ValueError("private JWK public and private coordinates disagree")
        return signer
    return LocalEd25519Signer.from_keyfile_dict(document)


def _https_url(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError("deployment URL contains whitespace or control characters")
    parsed = urlparse(value)
    hostname = parsed.hostname or ""
    if not hostname.endswith(".workers.dev"):
        raise ValueError("deployment URL hostname must end in .workers.dev")
    worker_labels = hostname[: -len(".workers.dev")].split(".")
    dns_label = re.compile(
        r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
    )
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or parsed.port is not None
        or not worker_labels
        or any(not dns_label.fullmatch(label) for label in worker_labels)
    ):
        raise ValueError(
            "deployment URL must be a root workers.dev https URL without "
            "credentials, query, or fragment"
        )
    if value != f"https://{hostname}":
        raise ValueError(
            "deployment URL must use the canonical lowercase origin without "
            "a trailing slash"
        )
    return value


def _require_git_commit(commit: str) -> None:
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("commit is not a commit object in this repository")


def _require_publisher(
    trust_context: dict, signer: LocalEd25519Signer
) -> None:
    expected_fields = {
        "schema_version",
        "context_kind",
        "profile",
        "key_lifecycle",
        "accepted_issuers_by_role",
        "role_identities",
        "team_controlled_roles",
        "trusted_policy_hashes",
        "trusted_witness_roots",
    }
    if set(trust_context) != expected_fields:
        raise ValueError(
            "trust context does not match the production alpha context"
        )
    if (
        trust_context.get("schema_version") != 1
        or trust_context.get("context_kind")
        != "PRODUCTION_CONTROL_PLANE"
        or trust_context.get("profile") != PROFILE
    ):
        raise ValueError("trust context has the wrong control-plane profile")
    key_lifecycle = trust_context.get("key_lifecycle")
    if (
        not isinstance(key_lifecycle, dict)
        or set(key_lifecycle)
        != {
            "schema_version",
            "keyset_version",
            "generation",
            "capability_token_derivation",
            "rotation_retirement_gate",
        }
        or key_lifecycle.get("schema_version") != 1
        or key_lifecycle.get("generation") != 1
        or not isinstance(key_lifecycle.get("keyset_version"), str)
        or re.fullmatch(
            r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            key_lifecycle["keyset_version"],
        )
        is None
        or key_lifecycle.get("capability_token_derivation")
        != "bulla-control-plane-alpha-capability/hmac-sha256-v1"
        or key_lifecycle.get("rotation_retirement_gate")
        != "BLOCKED_KEY_ROTATION_RETIREMENT_NOT_IMPLEMENTED"
    ):
        raise ValueError(
            "trust context key lifecycle is not the versioned blocked contract"
        )
    accepted = trust_context.get("accepted_issuers_by_role")
    identities = trust_context.get("role_identities")
    if (
        not isinstance(accepted, dict)
        or not isinstance(identities, dict)
        or set(accepted) != set(ROLES)
        or set(identities) != set(ROLES)
    ):
        raise ValueError("trust context must pin all six closed roles")
    issuers: list[str] = []
    for role in ROLES:
        role_issuers = accepted.get(role)
        identity = identities.get(role)
        if (
            not isinstance(role_issuers, list)
            or len(role_issuers) != 1
            or not isinstance(role_issuers[0], str)
            or not isinstance(identity, dict)
            or set(identity)
            != {
                "issuer",
                "verification_method",
                "public_key_sha256",
                "public_jwk",
            }
            or identity.get("issuer") != role_issuers[0]
            or identity.get("verification_method") != role_issuers[0]
        ):
            raise ValueError(f"trust context role {role} is malformed")
        try:
            public_key = pubkey_from_did_key(role_issuers[0])
        except ValueError as exc:
            raise ValueError(
                f"trust context role {role} is not an Ed25519 did:key"
            ) from exc
        fingerprint = "sha256:" + hashlib.sha256(public_key).hexdigest()
        if identity.get("public_key_sha256") != fingerprint:
            raise ValueError(f"trust context role {role} fingerprint disagrees")
        public_jwk = identity.get("public_jwk")
        if (
            not isinstance(public_jwk, dict)
            or set(public_jwk) != {"kty", "crv", "x"}
            or public_jwk.get("kty") != "OKP"
            or public_jwk.get("crv") != "Ed25519"
            or _base64url_decode(public_jwk.get("x"), "x") != public_key
        ):
            raise ValueError(f"trust context role {role} public JWK disagrees")
        issuers.append(role_issuers[0])
    if len(set(issuers)) != len(ROLES):
        raise ValueError("trust context roles must use six distinct issuers")
    if trust_context.get("team_controlled_roles") != list(ROLES):
        raise ValueError("trust context team-controlled roles are not canonical")
    policies = trust_context.get("trusted_policy_hashes")
    if (
        not isinstance(policies, list)
        or policies != [POLICY_DIGEST]
    ):
        raise ValueError("trust context does not pin the fixed alpha policy")
    roots = trust_context.get("trusted_witness_roots")
    if (
        roots
        != ["witness://bulla-control-plane-alpha/public-alpha-v1"]
    ):
        raise ValueError("trust context witness root is not canonical")
    if accepted["publisher"] != [signer.issuer]:
        raise ValueError(
            "deployment signer must be the sole externally pinned publisher"
        )


def _git_commit_exists(commit: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def _review_digest(
    *,
    final_document_digest: str,
    mapping_sha256: str,
    review_commit: str,
) -> str:
    preimage = {
        "profile": PROFILE,
        "purpose": "mcp-final-mapping-review",
        "final_document_digest": final_document_digest,
        "mapping_sha256": mapping_sha256,
        "review_commit": review_commit,
    }
    return _byte_hash(canonical_json(preimage).encode("utf-8"))


def _require_mapping_review(
    mapping: dict,
    *,
    mapping_bytes: bytes,
    final_document_digest: str,
    deployed_commit: str,
    trust_context: dict,
    final_review: dict,
) -> str:
    if set(mapping) != {
        "schema_version",
        "profile",
        "standard",
        "target_protocol_revision",
        "status",
        "final_document_digest",
        "review_commit",
        "implementation_files",
    }:
        raise ValueError("MCP mapping review artifact is not closed")
    review_commit = mapping.get("review_commit")
    if (
        mapping.get("schema_version") != 1
        or mapping.get("profile") != PROFILE
        or mapping.get("standard") != "Model Context Protocol"
        or mapping.get("target_protocol_revision") != "2026-07-28"
        or mapping.get("status") != "PASSED"
        or mapping.get("final_document_digest") != final_document_digest
        or not isinstance(review_commit, str)
        or not COMMIT_PATTERN.fullmatch(review_commit)
        or not _git_commit_exists(review_commit)
    ):
        raise ValueError("MCP mapping review artifact is not a passed final review")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", review_commit, deployed_commit],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if ancestor.returncode != 0:
        raise ValueError("MCP mapping review commit is not deployed")
    implementation_files = mapping.get("implementation_files")
    if (
        not isinstance(implementation_files, list)
        or len(implementation_files) != len(REQUIRED_MCP_IMPLEMENTATION_FILES)
    ):
        raise ValueError(
            "MCP mapping review must bind the frozen MCP-facing source set"
        )
    seen: set[str] = set()
    for position, entry in enumerate(implementation_files):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "sha256"}
            or not isinstance(entry.get("path"), str)
            or not isinstance(entry.get("sha256"), str)
            or not SHA256_PATTERN.fullmatch(entry["sha256"])
        ):
            raise ValueError("MCP mapping review source entry is malformed")
        relative = entry["path"]
        if relative != REQUIRED_MCP_IMPLEMENTATION_FILES[position]:
            raise ValueError(
                "MCP mapping review does not bind the frozen MCP-facing source set"
            )
        parts = PurePosixPath(relative).parts
        if (
            PurePosixPath(relative).is_absolute()
            or "\\" in relative
            or not parts
            or any(part in {"", ".", ".."} for part in parts)
            or relative in seen
        ):
            raise ValueError("MCP mapping review source path is unsafe")
        seen.add(relative)
        current = (ROOT / relative).resolve()
        try:
            current.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise ValueError("MCP mapping review source escapes the repository") from exc
        if not current.is_file() or current.is_symlink():
            raise ValueError("MCP mapping review source is not a regular file")
        reviewed = subprocess.run(
            ["git", "show", f"{review_commit}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        deployed = subprocess.run(
            ["git", "show", f"{deployed_commit}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        if (
            reviewed.returncode != 0
            or deployed.returncode != 0
            or _byte_hash(current.read_bytes()) != entry["sha256"]
            or _byte_hash(reviewed.stdout) != entry["sha256"]
            or _byte_hash(deployed.stdout) != entry["sha256"]
        ):
            raise ValueError(
                f"MCP mapping review source is not unchanged: {relative}"
            )
    mapping_sha256 = _byte_hash(mapping_bytes)
    review_digest = _review_digest(
        final_document_digest=final_document_digest,
        mapping_sha256=mapping_sha256,
        review_commit=review_commit,
    )
    expected_reviewer = trust_context["accepted_issuers_by_role"][
        "evaluation_authority"
    ][0]
    publisher = trust_context["accepted_issuers_by_role"]["publisher"][0]
    proof = final_review.get("proof")
    if (
        set(final_review)
        != {
            "status",
            "review_commit",
            "final_document_digest",
            "mapping_sha256",
            "review_digest",
            "reviewer_class",
            "reviewer_role",
            "reviewer_issuer",
            "proof",
        }
        or final_review.get("status") != "PASSED"
        or final_review.get("review_commit") != review_commit
        or final_review.get("final_document_digest") != final_document_digest
        or final_review.get("mapping_sha256") != mapping_sha256
        or final_review.get("review_digest") != review_digest
        or final_review.get("reviewer_class") != "ROLE_SEPARATED_INTERNAL"
        or final_review.get("reviewer_role") != "evaluation_authority"
        or final_review.get("reviewer_issuer") != expected_reviewer
        or expected_reviewer == publisher
        or not isinstance(proof, dict)
    ):
        raise ValueError("MCP final review is not role-separated and byte-bound")
    verification = verify_proof_domain(
        "authorization",
        review_digest,
        proof,
        schema="0.4",
    )
    if (
        not verification.authentic
        or verification.issuer != expected_reviewer
        or proof.get("issuer") != expected_reviewer
    ):
        raise ValueError("MCP final review proof is not authentic")
    return mapping_sha256


def _require_ready_standard(
    standards_pin: dict,
    *,
    deployed_commit: str,
    trust_context: dict,
    mapping: dict,
    mapping_bytes: bytes,
) -> str:
    required_fields = {
        "as_of",
        "authzen",
        "deployment_gate",
        "final_document_digest",
        "final_review",
        "final_schema_url",
        "official_draft_url",
        "pinned_snapshot_url",
        "profile",
        "promotion_condition",
        "schema_version",
        "snapshot_commit",
        "snapshot_sha256",
        "source_status",
        "standard",
        "target_protocol_revision",
    }
    if (
        set(standards_pin) != required_fields
        or standards_pin.get("schema_version") != 1
        or standards_pin.get("profile") != PROFILE
        or standards_pin.get("standard") != "Model Context Protocol"
        or standards_pin.get("target_protocol_revision") != "2026-07-28"
    ):
        raise ValueError("standards pin is not the control-plane MCP pin")
    if standards_pin.get("deployment_gate") != "READY":
        raise ValueError("MCP standards deployment gate is not READY")
    digest = standards_pin.get("final_document_digest")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise ValueError("MCP final document digest is not pinned")
    if standards_pin.get("source_status") != "FINAL":
        raise ValueError("MCP standards source status is not FINAL")
    if standards_pin.get("snapshot_sha256") != digest:
        raise ValueError("MCP final and pinned snapshot digests disagree")
    snapshot_commit = standards_pin.get("snapshot_commit")
    pinned_url = standards_pin.get("pinned_snapshot_url")
    if (
        not isinstance(snapshot_commit, str)
        or not COMMIT_PATTERN.fullmatch(snapshot_commit)
        or pinned_url
        != (
            "https://raw.githubusercontent.com/modelcontextprotocol/"
            f"modelcontextprotocol/{snapshot_commit}/"
            "schema/2026-07-28/schema.json"
        )
        or standards_pin.get("final_schema_url")
        != (
            "https://github.com/modelcontextprotocol/modelcontextprotocol/"
            "blob/main/schema/2026-07-28/schema.json"
        )
    ):
        raise ValueError("MCP final source URLs are not pinned to one commit")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(standards_pin.get("as_of"))):
        raise ValueError("standards pin as_of date is malformed")
    authzen = standards_pin.get("authzen")
    if (
        not isinstance(authzen, dict)
        or authzen.get("official_url")
        != "https://openid.net/specs/authorization-api-1_0.html"
        or authzen.get("document_sha256")
        != (
            "sha256:"
            "f0ee89cc4a688f9f409dc323c8342579d74c45dc776a6b04a622ba9738de82bc"
        )
        or authzen.get("status") != "FINAL"
        or authzen.get("version") != "1.0"
    ):
        raise ValueError("AuthZEN final pin is not canonical")
    review = standards_pin.get("final_review")
    if not isinstance(review, dict):
        raise ValueError("MCP final mapping review is not pinned")
    return _require_mapping_review(
        mapping,
        mapping_bytes=mapping_bytes,
        final_document_digest=digest,
        deployed_commit=deployed_commit,
        trust_context=trust_context,
        final_review=review,
    )


def _require_candidate_outer_smoke(
    smoke: dict,
    *,
    worker_version: str,
    deployment_url: str,
    commit: str,
) -> None:
    expected_fields = {
        "schema_version",
        "profile",
        "phase",
        "passed",
        "worker_version",
        "outer_worker_version",
        "durable_object_assignment",
        "deployment_url",
        "commit",
        "manifest_sha256",
        "manifest_trust_config",
        "cors_preflight",
        "closed_route_rejection",
    }
    if set(smoke) != expected_fields:
        raise ValueError(
            "candidate outer-Worker smoke does not match the closed evidence schema"
        )
    expected = {
        "schema_version": 1,
        "profile": PROFILE,
        "phase": CANDIDATE_SMOKE_PHASE,
        "worker_version": worker_version,
        "outer_worker_version": worker_version,
        "deployment_url": deployment_url,
        "commit": commit,
        "durable_object_assignment": CANDIDATE_DURABLE_OBJECT_ASSIGNMENT,
    }
    for key, value in expected.items():
        if smoke.get(key) != value:
            raise ValueError(
                f"candidate outer-Worker smoke does not bind expected {key}"
            )
    if smoke.get("passed") is not True:
        raise ValueError("candidate outer-Worker smoke did not pass")
    if not SHA256_PATTERN.fullmatch(str(smoke.get("manifest_sha256"))):
        raise ValueError("candidate outer-Worker manifest digest is malformed")
    for field in (
        "manifest_trust_config",
        "cors_preflight",
        "closed_route_rejection",
    ):
        if smoke.get(field) != "VERIFIED":
            raise ValueError(f"candidate outer-Worker {field} is not VERIFIED")


def _require_final_smoke(
    smoke: dict,
    *,
    worker_version: str,
    deployment_url: str,
    commit: str,
) -> None:
    expected_fields = {
        "schema_version",
        "profile",
        "phase",
        "passed",
        "worker_version",
        "outer_worker_version",
        "durable_object_assignment",
        "deployment_url",
        "commit",
        "manifest_sha256",
        "manifest_trust_config",
        "checkpoint_hash",
        "checkpoint_tree_size",
        "checkpoint_root_hash",
        "witness_inclusion_attestation",
        "witness_receipt_sha256",
        "packet_manifest_sha256",
        "decision_coverage",
        "effect_coverage",
        "exact_receipt_bytes",
        "python_packet_verification",
        "node_packet_verification",
    }
    if set(smoke) != expected_fields:
        raise ValueError("smoke report does not match the closed evidence schema")
    expected = {
        "schema_version": 1,
        "profile": PROFILE,
        "phase": FINAL_SMOKE_PHASE,
        "worker_version": worker_version,
        "outer_worker_version": worker_version,
        "deployment_url": deployment_url,
        "commit": commit,
    }
    for key, value in expected.items():
        if smoke.get(key) != value:
            raise ValueError(f"smoke report does not bind expected {key}")
    if smoke.get("passed") is not True:
        raise ValueError("smoke report did not pass")
    if smoke.get("durable_object_assignment") != FINAL_DURABLE_OBJECT_ASSIGNMENT:
        raise ValueError(
            "smoke report overstates the Durable Object version exercised"
        )
    if smoke.get("manifest_trust_config") != "VERIFIED":
        raise ValueError("final smoke manifest trust configuration is not VERIFIED")
    for field in (
        "manifest_sha256",
        "checkpoint_hash",
        "checkpoint_root_hash",
        "witness_inclusion_attestation",
        "witness_receipt_sha256",
        "packet_manifest_sha256",
    ):
        value = smoke.get(field)
        if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
            raise ValueError(f"smoke report {field} is malformed")
    if (
        not isinstance(smoke.get("checkpoint_tree_size"), int)
        or isinstance(smoke.get("checkpoint_tree_size"), bool)
        or smoke["checkpoint_tree_size"] < 1
    ):
        raise ValueError("smoke report checkpoint_tree_size is malformed")
    if smoke.get("decision_coverage") != {"receipted": 2, "total": 2}:
        raise ValueError("smoke report decision coverage is not 2/2")
    if smoke.get("effect_coverage") != {"receipted": 1, "total": 2}:
        raise ValueError("smoke report effect coverage is not 1/2")
    for field in (
        "exact_receipt_bytes",
        "python_packet_verification",
        "node_packet_verification",
    ):
        if smoke.get(field) != "VERIFIED":
            raise ValueError(f"smoke report {field} is not VERIFIED")


def _require_routing(
    routing: dict,
    *,
    phase: str,
    old_version: str,
    worker_version: str,
    commit: str,
) -> None:
    if set(routing) != {
        "schema_version",
        "profile",
        "phase",
        "commit",
        "baseline_version",
        "candidate_version",
        "versions",
    }:
        raise ValueError("routing report does not match the closed evidence schema")
    if (
        routing.get("schema_version") != 1
        or routing.get("profile") != PROFILE
        or routing.get("phase") != phase
        or routing.get("commit") != commit
        or routing.get("baseline_version") != old_version
        or routing.get("candidate_version") != worker_version
    ):
        raise ValueError("routing report does not bind the deployment transaction")
    if phase == CANDIDATE_ROUTING_PHASE:
        expected_versions = [
            {"version_id": old_version, "percentage": 100},
            {"version_id": worker_version, "percentage": 0},
        ]
    elif phase == FINAL_ROUTING_PHASE:
        expected_versions = [
            {"version_id": worker_version, "percentage": 100},
        ]
    else:
        raise ValueError("routing report phase is unsupported")
    if routing.get("versions") != expected_versions:
        raise ValueError(
            f"routing report {phase} does not contain the normalized allocation"
        )


def _require_checks(checks: dict, *, commit: str) -> None:
    if set(checks) != {
        "schema_version",
        "profile",
        "commit",
        "passed",
        "source_tree_clean",
        "commands_sha256",
        "results_sha256",
    }:
        raise ValueError("checks report does not match the closed evidence schema")
    if (
        checks.get("schema_version") != 1
        or checks.get("profile") != PROFILE
        or checks.get("commit") != commit
        or checks.get("passed") is not True
        or checks.get("source_tree_clean") is not True
    ):
        raise ValueError("checks report does not bind a clean passing commit")
    for field in ("commands_sha256", "results_sha256"):
        value = checks.get(field)
        if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
            raise ValueError(f"checks report {field} is malformed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--worker-version")
    parser.add_argument("--deployment-url")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--trust-context", type=Path, required=True)
    parser.add_argument("--standards-pin", type=Path, required=True)
    parser.add_argument("--final-standard", type=Path)
    parser.add_argument("--mapping-review", type=Path, required=True)
    parser.add_argument("--candidate-outer-smoke", type=Path)
    parser.add_argument("--final-smoke", type=Path)
    parser.add_argument("--candidate-routing", type=Path)
    parser.add_argument("--final-routing", type=Path)
    parser.add_argument("--old-worker-version")
    parser.add_argument("--checks", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    try:
        if not COMMIT_PATTERN.fullmatch(args.commit):
            raise ValueError("commit must be a lowercase 40-character SHA-1")
        _require_git_commit(args.commit)
        for path in (
            args.trust_context,
            args.standards_pin,
            args.mapping_review,
        ):
            if not path.is_file():
                raise ValueError(f"required evidence file does not exist: {path}")
        signer = _signer_from_key(_load_key(args.key))
        trust_bytes, trust_context = _load_json_bytes(args.trust_context)
        standards_bytes, standards_pin = _load_json_bytes(args.standards_pin)
        final_standard_path = (
            args.final_standard
            if args.final_standard is not None
            else args.standards_pin.with_name("final-standard.json")
        )
        final_standard_bytes, _ = _load_json_bytes(final_standard_path)
        mapping_bytes, mapping = _load_json_bytes(args.mapping_review)
        _require_publisher(trust_context, signer)
        mapping_sha256 = _require_ready_standard(
            standards_pin,
            deployed_commit=args.commit,
            trust_context=trust_context,
            mapping=mapping,
            mapping_bytes=mapping_bytes,
        )
        if _byte_hash(final_standard_bytes) != standards_pin.get(
            "final_document_digest"
        ):
            raise ValueError(
                "vendored final MCP standard bytes do not match final_document_digest"
            )
        if args.preflight_only:
            print(
                "production preflight passed: trust context, publisher, "
                "final standards pin, and role-separated mapping review"
            )
            return 0
        if (
            args.worker_version is None
            or args.deployment_url is None
            or args.candidate_outer_smoke is None
            or args.final_smoke is None
            or args.candidate_routing is None
            or args.final_routing is None
            or args.old_worker_version is None
            or args.checks is None
            or args.out is None
        ):
            raise ValueError(
                "minting requires --worker-version, --deployment-url, "
                "--candidate-outer-smoke, --final-smoke, --candidate-routing, "
                "--final-routing, --old-worker-version, --checks, and --out"
            )
        deployment_url = _https_url(args.deployment_url)
        if not WORKER_VERSION_PATTERN.fullmatch(args.worker_version):
            raise ValueError("worker version must be a lowercase UUID")
        if not WORKER_VERSION_PATTERN.fullmatch(args.old_worker_version):
            raise ValueError("old worker version must be a lowercase UUID")
        if args.old_worker_version == args.worker_version:
            raise ValueError("old and candidate worker versions must be distinct")
        for path in (
            args.candidate_outer_smoke,
            args.final_smoke,
            args.candidate_routing,
            args.final_routing,
            args.checks,
        ):
            if not path.is_file():
                raise ValueError(f"required evidence file does not exist: {path}")
        candidate_smoke_bytes, candidate_smoke = _load_json_bytes(
            args.candidate_outer_smoke
        )
        final_smoke_bytes, final_smoke = _load_json_bytes(args.final_smoke)
        candidate_routing_bytes, candidate_routing = _load_json_bytes(
            args.candidate_routing
        )
        final_routing_bytes, final_routing = _load_json_bytes(
            args.final_routing
        )
        checks_bytes, checks = _load_json_bytes(args.checks)
        _require_candidate_outer_smoke(
            candidate_smoke,
            worker_version=args.worker_version,
            deployment_url=deployment_url,
            commit=args.commit,
        )
        _require_final_smoke(
            final_smoke,
            worker_version=args.worker_version,
            deployment_url=deployment_url,
            commit=args.commit,
        )
        _require_routing(
            candidate_routing,
            phase=CANDIDATE_ROUTING_PHASE,
            old_version=args.old_worker_version,
            worker_version=args.worker_version,
            commit=args.commit,
        )
        _require_routing(
            final_routing,
            phase=FINAL_ROUTING_PHASE,
            old_version=args.old_worker_version,
            worker_version=args.worker_version,
            commit=args.commit,
        )
        _require_checks(checks, commit=args.commit)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    evidence = {
        "trust_context": _byte_hash(trust_bytes),
        "standards_pin": _byte_hash(standards_bytes),
        "mapping_review": mapping_sha256,
        "candidate_outer_worker_smoke_report": _byte_hash(candidate_smoke_bytes),
        "final_smoke_report": _byte_hash(final_smoke_bytes),
        "candidate_routing_report": _byte_hash(candidate_routing_bytes),
        "final_routing_report": _byte_hash(final_routing_bytes),
        "checks": _byte_hash(checks_bytes),
    }
    claimed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    envelope = RecourseEnvelope(
        authority=Authority(
            principal=signer.issuer,
            policy="policy://bulla/control-plane-alpha/deployment",
        ),
        bounds=Bounds(
            scope=(
                "bulla.control-plane-alpha/0.1-experimental "
                f"commit:{args.commit} worker-version:{args.worker_version}"
            )
        ),
        recourse=Recourse(
            challenge_window="P30D",
            forum=Forum(
                log_endpoint="https://github.com/jkomkov/res-agentica/issues",
                trusted_root_ref=f"git:{args.commit}",
            ),
            remedies=(
                Remedy(
                    rung="recompute",
                    verifier=(
                        "pnpm --dir packages/bulla-control-plane-alpha check"
                    ),
                    anchor=f"git:{args.commit}",
                ),
                Remedy(
                    rung="revert",
                    verifier="wrangler rollback",
                    anchor=f"cloudflare-worker-version:{args.worker_version}",
                ),
                Remedy(
                    rung="escalate",
                    verifier="repository maintainer review",
                    anchor="github:jkomkov",
                ),
            ),
        ),
        retention_class="authority-permanent",
        disclosure_class="public",
    )
    receipt = sign_action_receipt_v04(
        build_action_receipt_v04(
            action={
                "type": "service.deploy",
                "subject": {
                    "profile": PROFILE,
                    "maturity": "experimental",
                    "control": "team-operated",
                    "classification": "synthetic-public",
                    "git_commit": args.commit,
                    "previous_worker_version": args.old_worker_version,
                    "worker_version": args.worker_version,
                    "deployment_url": deployment_url,
                    "trust_context_hash": evidence["trust_context"],
                    "standards_pin_hash": evidence["standards_pin"],
                    "mapping_review_hash": evidence["mapping_review"],
                    "candidate_outer_worker_smoke_report_hash": evidence[
                        "candidate_outer_worker_smoke_report"
                    ],
                    "candidate_outer_worker_version": args.worker_version,
                    "candidate_durable_object_assignment": (
                        CANDIDATE_DURABLE_OBJECT_ASSIGNMENT
                    ),
                    "final_smoke_report_hash": evidence["final_smoke_report"],
                    "final_outer_worker_version": args.worker_version,
                    "final_durable_object_assignment": (
                        FINAL_DURABLE_OBJECT_ASSIGNMENT
                    ),
                    "candidate_routing_report_hash": evidence[
                        "candidate_routing_report"
                    ],
                    "final_routing_report_hash": evidence[
                        "final_routing_report"
                    ],
                    "checks_hash": evidence["checks"],
                },
            },
            diagnostic_ref={
                "status": "reference",
                "ref": evidence["checks"],
            },
            envelope=envelope,
            event_id=str(uuid.uuid4()),
            claimed_at=claimed_at,
            anchor_ref={
                "kind": "cloudflare-worker-version",
                "ref": args.worker_version,
                "url": deployment_url,
                "git_commit": args.commit,
            },
            evidence_refs=[
                {
                    "name": name,
                    "hash": digest,
                    "grounding": "self_asserted",
                }
                for name, digest in evidence.items()
            ],
            conventions=[],
            producer={
                "bulla_version": __version__,
                "workflow": os.environ.get("GITHUB_WORKFLOW", "local"),
            },
        ),
        signer,
    )
    if (
        not isinstance(receipt.signature, dict)
        or receipt.signature.get("issuer") != signer.issuer
    ):
        print("deployment receipt proof issuer changed unexpectedly", file=sys.stderr)
        return 1
    verification = verify_receipt(receipt.to_dict())
    if not verification.ok or verification.verified_to != "attestation":
        print(
            "deployment receipt failed verification: "
            + "; ".join(verification.reasons),
            file=sys.stderr,
        )
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(receipt.to_json() + "\n", encoding="utf-8")
    print(
        f"wrote {args.out} verified_to={verification.verified_to} "
        f"worker_version={args.worker_version}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
