from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from bulla.action_receipt import verify_receipt
from bulla._canonical import canonical_json
from bulla.identity import LocalEd25519Signer


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "bulla" / "scripts" / "mint_control_plane_deployment_receipt.py"
OLD_WORKER_VERSION = "65ebf6c2-77e6-4fd6-94c4-9611a4b82ef5"
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


def test_mapping_review_surface_is_sorted_and_relative_import_closed() -> None:
    assert REQUIRED_MCP_IMPLEMENTATION_FILES == tuple(
        sorted(REQUIRED_MCP_IMPLEMENTATION_FILES)
    )
    reviewed = set(REQUIRED_MCP_IMPLEMENTATION_FILES)
    import_pattern = re.compile(
        r"""(?:from\s+|import\s*\()\s*["'](\.{1,2}/[^"']+)["']"""
    )
    for relative in REQUIRED_MCP_IMPLEMENTATION_FILES:
        source = ROOT / relative
        if source.suffix not in {".ts", ".mjs"}:
            continue
        for specifier in import_pattern.findall(
            source.read_text(encoding="utf-8")
        ):
            unresolved = source.parent / specifier
            candidates = (
                unresolved,
                unresolved.with_suffix(".ts"),
                unresolved.with_suffix(".mjs"),
                unresolved.with_suffix(".json"),
                unresolved / "index.ts",
            )
            dependency = next(
                (candidate for candidate in candidates if candidate.is_file()),
                None,
            )
            if dependency is not None:
                assert dependency.resolve().relative_to(ROOT).as_posix() in reviewed


def _evidence(root: Path, name: str, document: dict | None = None) -> Path:
    path = root / name
    path.write_text(
        json.dumps(document or {"status": "verified"}) + "\n",
        encoding="utf-8",
    )
    return path


def _private_jwk(signer: LocalEd25519Signer) -> dict:
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "d": encode(signer.seed),
        "x": encode(signer.public_key),
    }


def _trust_context(
    publisher: LocalEd25519Signer,
    reviewer: LocalEd25519Signer | None = None,
) -> dict:
    roles = (
        "evaluation_authority",
        "boundary",
        "target",
        "incident_commander",
        "publisher",
        "witness",
    )
    signers = {role: LocalEd25519Signer.generate() for role in roles}
    signers["publisher"] = publisher
    if reviewer is not None:
        signers["evaluation_authority"] = reviewer
    accepted = {
        role: [signers[role].issuer]
        for role in roles
    }
    identities = {
        role: {
            "issuer": signers[role].issuer,
            "verification_method": signers[role].verification_method,
            "public_key_sha256": (
                "sha256:" + hashlib.sha256(signers[role].public_key).hexdigest()
            ),
            "public_jwk": {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": base64.urlsafe_b64encode(signers[role].public_key)
                .decode()
                .rstrip("="),
            },
        }
        for role in roles
    }
    return {
        "schema_version": 1,
        "context_kind": "PRODUCTION_CONTROL_PLANE",
        "profile": "bulla.control-plane-alpha/0.1-experimental",
        "key_lifecycle": {
            "schema_version": 1,
            "keyset_version": "urn:uuid:00000000-0000-4000-8000-000000000001",
            "generation": 1,
            "capability_token_derivation": (
                "bulla-control-plane-alpha-capability/hmac-sha256-v1"
            ),
            "rotation_retirement_gate": (
                "BLOCKED_KEY_ROTATION_RETIREMENT_NOT_IMPLEMENTED"
            ),
        },
        "accepted_issuers_by_role": accepted,
        "role_identities": identities,
        "team_controlled_roles": list(roles),
        "trusted_policy_hashes": [
            "sha256:"
            "fe2a76d9dc104e3a4b97fd173e3670e05fd8b79123f5e7126d76e4331b8e810d"
        ],
        "trusted_witness_roots": [
            "witness://bulla-control-plane-alpha/public-alpha-v1"
        ],
    }


def _commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _reviewable_commit(tmp_path: Path) -> str:
    """Create an unreferenced test commit containing the frozen new source set."""
    index = tmp_path / "review.index"
    environment = {
        **os.environ,
        "GIT_INDEX_FILE": str(index),
        "GIT_AUTHOR_NAME": "Bulla deployment test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Bulla deployment test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    subprocess.run(
        ["git", "read-tree", "HEAD"],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    subprocess.run(
        ["git", "add", "--", *REQUIRED_MCP_IMPLEMENTATION_FILES],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    tree = subprocess.run(
        ["git", "write-tree"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return subprocess.run(
        ["git", "commit-tree", tree, "-p", _commit()],
        cwd=ROOT,
        env=environment,
        input="deployment evidence test fixture\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _smoke(version: str, endpoint: str, commit: str, phase: str) -> dict:
    durable_object_assignment = (
        "NOT_ATTESTED_AT_CANDIDATE_ZERO_TRAFFIC"
        if phase == "CANDIDATE_ZERO_TRAFFIC"
        else "ATTESTED_BY_PROMOTED_FULL_LOOP"
    )
    common = {
        "schema_version": 1,
        "profile": "bulla.control-plane-alpha/0.1-experimental",
        "phase": phase,
        "passed": True,
        "worker_version": version,
        "outer_worker_version": version,
        "durable_object_assignment": durable_object_assignment,
        "deployment_url": endpoint,
        "commit": commit,
        "manifest_sha256": "sha256:" + "4" * 64,
        "manifest_trust_config": "VERIFIED",
    }
    if phase == "CANDIDATE_ZERO_TRAFFIC":
        return {
            **common,
            "cors_preflight": "VERIFIED",
            "closed_route_rejection": "VERIFIED",
        }
    return {
        **common,
        "checkpoint_hash": "sha256:" + "5" * 64,
        "checkpoint_tree_size": 3,
        "checkpoint_root_hash": "sha256:" + "7" * 64,
        "witness_inclusion_attestation": "sha256:" + "8" * 64,
        "witness_receipt_sha256": "sha256:" + "9" * 64,
        "packet_manifest_sha256": "sha256:" + "6" * 64,
        "decision_coverage": {"receipted": 2, "total": 2},
        "effect_coverage": {"receipted": 1, "total": 2},
        "exact_receipt_bytes": "VERIFIED",
        "python_packet_verification": "VERIFIED",
        "node_packet_verification": "VERIFIED",
    }


def _routing(
    version: str,
    commit: str,
    phase: str,
) -> dict:
    versions = (
        [
            {"version_id": OLD_WORKER_VERSION, "percentage": 100},
            {"version_id": version, "percentage": 0},
        ]
        if phase == "CANDIDATE_100_0"
        else [{"version_id": version, "percentage": 100}]
    )
    return {
        "schema_version": 1,
        "profile": "bulla.control-plane-alpha/0.1-experimental",
        "phase": phase,
        "commit": commit,
        "baseline_version": OLD_WORKER_VERSION,
        "candidate_version": version,
        "versions": versions,
    }


def _deployment_evidence_arguments(
    tmp_path: Path,
    version: str,
    endpoint: str,
    commit: str,
) -> list[str]:
    return [
        "--old-worker-version",
        OLD_WORKER_VERSION,
        "--candidate-outer-smoke",
        str(
            _evidence(
                tmp_path,
                "candidate-smoke.json",
                _smoke(
                    version,
                    endpoint,
                    commit,
                    "CANDIDATE_ZERO_TRAFFIC",
                ),
            )
        ),
        "--final-smoke",
        str(
            _evidence(
                tmp_path,
                "final-smoke.json",
                _smoke(
                    version,
                    endpoint,
                    commit,
                    "PROMOTED_100_PERCENT",
                ),
            )
        ),
        "--candidate-routing",
        str(
            _evidence(
                tmp_path,
                "candidate-routing.json",
                _routing(version, commit, "CANDIDATE_100_0"),
            )
        ),
        "--final-routing",
        str(
            _evidence(
                tmp_path,
                "final-routing.json",
                _routing(version, commit, "PROMOTED_100"),
            )
        ),
    ]


def _checks(commit: str) -> dict:
    return {
        "schema_version": 1,
        "profile": "bulla.control-plane-alpha/0.1-experimental",
        "commit": commit,
        "passed": True,
        "source_tree_clean": True,
        "commands_sha256": "sha256:" + "7" * 64,
        "results_sha256": "sha256:" + "8" * 64,
    }


def _mapping_review(
    tmp_path: Path,
    commit: str,
    digest: str,
    implementation_paths: tuple[str, ...] = REQUIRED_MCP_IMPLEMENTATION_FILES,
) -> Path:
    final_standard = _evidence(
        tmp_path,
        "final-standard.json",
        {"protocolRevision": "2026-07-28", "fixture": digest},
    )
    digest = "sha256:" + hashlib.sha256(final_standard.read_bytes()).hexdigest()
    implementation_files = []
    for relative in implementation_paths:
        encoded = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        implementation_files.append(
            {
                "path": relative,
                "sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
            }
        )
    return _evidence(
        tmp_path,
        "mapping-review.json",
        {
            "schema_version": 1,
            "profile": "bulla.control-plane-alpha/0.1-experimental",
            "standard": "Model Context Protocol",
            "target_protocol_revision": "2026-07-28",
            "status": "PASSED",
            "final_document_digest": digest,
            "review_commit": commit,
            "implementation_files": implementation_files,
        },
    )


def _standards(
    commit: str,
    reviewer: LocalEd25519Signer,
    mapping_path: Path,
    digit: str = "1",
) -> dict:
    snapshot_commit = "c" * 40
    digest = json.loads(mapping_path.read_text(encoding="utf-8"))[
        "final_document_digest"
    ]
    mapping_sha = (
        "sha256:" + hashlib.sha256(mapping_path.read_bytes()).hexdigest()
    )
    review_digest = (
        "sha256:"
        + hashlib.sha256(
            canonical_json(
                {
                    "profile": (
                        "bulla.control-plane-alpha/0.1-experimental"
                    ),
                    "purpose": "mcp-final-mapping-review",
                    "final_document_digest": digest,
                    "mapping_sha256": mapping_sha,
                    "review_commit": commit,
                }
            ).encode()
        ).hexdigest()
    )
    return {
        "as_of": "2026-07-28",
        "authzen": {
            "standard": "OpenID AuthZEN Authorization API",
            "version": "1.0",
            "status": "FINAL",
            "published": "2026-01-11",
            "official_url": (
                "https://openid.net/specs/authorization-api-1_0.html"
            ),
            "document_sha256": (
                "sha256:"
                "f0ee89cc4a688f9f409dc323c8342579d74c45dc776a6b04a622ba9738de82bc"
            ),
            "context_note": "Pinned test context.",
        },
        "deployment_gate": "READY",
        "final_document_digest": digest,
        "final_review": {
            "status": "PASSED",
            "review_commit": commit,
            "final_document_digest": digest,
            "mapping_sha256": mapping_sha,
            "review_digest": review_digest,
            "reviewer_class": "ROLE_SEPARATED_INTERNAL",
            "reviewer_role": "evaluation_authority",
            "reviewer_issuer": reviewer.issuer,
            "proof": reviewer.sign_domain(
                "authorization", review_digest, schema="0.4"
            ),
        },
        "final_schema_url": (
            "https://github.com/modelcontextprotocol/modelcontextprotocol/"
            "blob/main/schema/2026-07-28/schema.json"
        ),
        "official_draft_url": (
            "https://github.com/modelcontextprotocol/modelcontextprotocol/"
            "blob/main/schema/draft/schema.json"
        ),
        "pinned_snapshot_url": (
            "https://raw.githubusercontent.com/modelcontextprotocol/"
            f"modelcontextprotocol/{snapshot_commit}/"
            "schema/2026-07-28/schema.json"
        ),
        "profile": "bulla.control-plane-alpha/0.1-experimental",
        "promotion_condition": "Final mapping review passed.",
        "schema_version": 1,
        "snapshot_commit": snapshot_commit,
        "snapshot_sha256": digest,
        "source_status": "FINAL",
        "standard": "Model Context Protocol",
        "target_protocol_revision": "2026-07-28",
    }


def test_predeployment_gate_accepts_the_signed_final_mapping_review(
    tmp_path: Path,
):
    publisher = LocalEd25519Signer.generate()
    reviewer = LocalEd25519Signer.generate()
    key = _evidence(tmp_path, "publisher-key.json", publisher.to_keyfile_dict())
    commit = _reviewable_commit(tmp_path)
    trust = _evidence(
        tmp_path,
        "trust.json",
        _trust_context(publisher, reviewer),
    )
    mapping = _mapping_review(
        tmp_path,
        commit,
        "sha256:" + "1" * 64,
    )
    standards = _evidence(
        tmp_path,
        "standards.json",
        _standards(commit, reviewer, mapping),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--preflight-only",
            "--commit",
            commit,
            "--trust-context",
            str(trust),
            "--standards-pin",
            str(standards),
            "--mapping-review",
            str(mapping),
            "--key",
            str(key),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "role-separated mapping review" in result.stdout
    assert list(tmp_path.glob("deployment*.json")) == []


def test_predeployment_gate_rejects_a_modified_reviewed_worktree(
    tmp_path: Path,
):
    publisher = LocalEd25519Signer.generate()
    reviewer = LocalEd25519Signer.generate()
    key = _evidence(tmp_path, "publisher-key.json", publisher.to_keyfile_dict())
    commit = _reviewable_commit(tmp_path)
    trust = _evidence(
        tmp_path,
        "trust.json",
        _trust_context(publisher, reviewer),
    )
    mapping = _mapping_review(
        tmp_path,
        commit,
        "sha256:" + "1" * 64,
    )
    standards = _evidence(
        tmp_path,
        "standards.json",
        _standards(commit, reviewer, mapping),
    )
    reviewed_source = ROOT / "packages/bulla-control-plane-alpha/src/worker.ts"
    original_source = reviewed_source.read_bytes()
    try:
        reviewed_source.write_bytes(
            original_source + b"\n// hostile worktree modification\n"
        )

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--preflight-only",
                "--commit",
                commit,
                "--trust-context",
                str(trust),
                "--standards-pin",
                str(standards),
                "--mapping-review",
                str(mapping),
                "--key",
                str(key),
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "bulla" / "src"),
            },
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        reviewed_source.write_bytes(original_source)

    assert result.returncode == 2
    assert (
        "MCP mapping review source is not unchanged: "
        "packages/bulla-control-plane-alpha/src/worker.ts"
    ) in result.stderr


def test_service_deploy_receipt_requires_and_binds_real_evidence(tmp_path: Path):
    signer = LocalEd25519Signer.generate()
    reviewer = LocalEd25519Signer.generate()
    key = tmp_path / "key.json"
    key.write_text(
        json.dumps(signer.to_keyfile_dict()),
        encoding="utf-8",
    )
    version = "7b682718-9d67-4b2b-9f9e-2973697317e8"
    endpoint = "https://bulla-control-plane-alpha.example.workers.dev"
    commit = _reviewable_commit(tmp_path)
    trust = _evidence(
        tmp_path,
        "trust.json",
        _trust_context(signer, reviewer),
    )
    mapping = _mapping_review(
        tmp_path, commit, "sha256:" + "1" * 64
    )
    standards = _evidence(
        tmp_path,
        "standards.json",
        _standards(commit, reviewer, mapping),
    )
    out = tmp_path / "deployment.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--worker-version",
            version,
            "--deployment-url",
            endpoint,
            "--commit",
            commit,
            "--trust-context",
            str(trust),
            "--standards-pin",
            str(standards),
            "--mapping-review",
            str(mapping),
            *_deployment_evidence_arguments(
                tmp_path,
                version,
                endpoint,
                commit,
            ),
            "--checks",
            str(_evidence(tmp_path, "checks.json", _checks(commit))),
            "--key",
            str(key),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "bulla" / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["schema_version"] == "0.4"
    assert document["action"]["type"] == "service.deploy"
    assert document["action"]["subject"]["maturity"] == "experimental"
    assert document["action"]["subject"]["previous_worker_version"] == (
        OLD_WORKER_VERSION
    )
    assert document["action"]["subject"]["candidate_outer_worker_version"] == (
        version
    )
    assert document["action"]["subject"][
        "candidate_durable_object_assignment"
    ] == "NOT_ATTESTED_AT_CANDIDATE_ZERO_TRAFFIC"
    assert document["action"]["subject"]["final_outer_worker_version"] == version
    assert document["action"]["subject"][
        "final_durable_object_assignment"
    ] == "ATTESTED_BY_PROMOTED_FULL_LOOP"
    for field in (
        "candidate_outer_worker_smoke_report_hash",
        "final_smoke_report_hash",
        "candidate_routing_report_hash",
        "final_routing_report_hash",
    ):
        assert document["action"]["subject"][field].startswith("sha256:")
    assert document["signature"]["issuer"] == signer.issuer
    assert {item["name"] for item in document["evidence_refs"]} == {
        "trust_context",
        "standards_pin",
        "mapping_review",
        "candidate_outer_worker_smoke_report",
        "final_smoke_report",
        "candidate_routing_report",
        "final_routing_report",
        "checks",
    }
    assert {
        item["grounding"] for item in document["evidence_refs"]
    } == {"self_asserted"}
    verification = verify_receipt(document)
    assert verification.ok
    assert verification.verified_to == "attestation"


def test_service_deploy_receipt_has_no_unsigned_fallback(tmp_path: Path):
    commit = _reviewable_commit(tmp_path)
    out = tmp_path / "deployment.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--worker-version",
            "7b682718-9d67-4b2b-9f9e-2973697317e8",
            "--deployment-url",
            "https://example.workers.dev",
            "--commit",
            commit,
            "--trust-context",
            str(_evidence(tmp_path, "trust.json")),
            "--standards-pin",
            str(_evidence(tmp_path, "standards.json")),
            "--mapping-review",
            str(_evidence(tmp_path, "mapping.json")),
            "--checks",
            str(_evidence(tmp_path, "checks.json", _checks(commit))),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        env={
            key: value
            for key, value in os.environ.items()
            if key != "BULLA_CONTROL_PLANE_DEPLOY_KEY"
        }
        | {"PYTHONPATH": str(ROOT / "bulla" / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert not out.exists()
    assert "cannot be unsigned" in result.stderr


def test_service_deploy_receipt_rejects_unpinned_signer_and_blocked_standard(
    tmp_path: Path,
):
    pinned = LocalEd25519Signer.generate()
    actual = LocalEd25519Signer.generate()
    reviewer = LocalEd25519Signer.generate()
    commit = _reviewable_commit(tmp_path)
    key = _evidence(tmp_path, "key.json", actual.to_keyfile_dict())
    trust = _evidence(
        tmp_path,
        "trust.json",
        _trust_context(pinned, reviewer),
    )
    mapping = _mapping_review(
        tmp_path, commit, "sha256:" + "1" * 64
    )
    standards = _evidence(
        tmp_path,
        "standards.json",
        {
            **_standards(commit, reviewer, mapping),
            "source_status": "RELEASE_CANDIDATE_DRAFT",
            "deployment_gate": "BLOCKED_MCP_FINAL_NOT_PUBLISHED",
            "final_document_digest": None,
        },
    )
    version = "7b682718-9d67-4b2b-9f9e-2973697317e8"
    endpoint = "https://bulla-control-plane-alpha.example.workers.dev"
    command = [
        sys.executable,
        str(SCRIPT),
        "--worker-version",
        version,
        "--deployment-url",
        endpoint,
        "--commit",
        commit,
        "--trust-context",
        str(trust),
        "--standards-pin",
        str(standards),
        "--mapping-review",
        str(mapping),
        *_deployment_evidence_arguments(
            tmp_path,
            version,
            endpoint,
            commit,
        ),
        "--checks",
        str(_evidence(tmp_path, "checks.json", _checks(commit))),
        "--key",
        str(key),
        "--out",
        str(tmp_path / "deployment.json"),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "bulla" / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "sole externally pinned publisher" in result.stderr

    trust.write_text(
        json.dumps(_trust_context(actual, reviewer)),
        encoding="utf-8",
    )
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "bulla" / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "deployment gate is not READY" in result.stderr


def test_service_deploy_receipt_accepts_the_runtime_publisher_jwk(
    tmp_path: Path,
):
    signer = LocalEd25519Signer.generate()
    reviewer = LocalEd25519Signer.generate()
    key = _evidence(tmp_path, "publisher.jwk.json", _private_jwk(signer))
    version = "7b682718-9d67-4b2b-9f9e-2973697317e8"
    endpoint = "https://bulla-control-plane-alpha.example.workers.dev"
    commit = _reviewable_commit(tmp_path)
    trust = _evidence(
        tmp_path,
        "trust.json",
        _trust_context(signer, reviewer),
    )
    mapping = _mapping_review(
        tmp_path, commit, "sha256:" + "2" * 64
    )
    standards = _evidence(
        tmp_path,
        "standards.json",
        _standards(commit, reviewer, mapping, "2"),
    )
    out = tmp_path / "deployment.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--worker-version",
            version,
            "--deployment-url",
            endpoint,
            "--commit",
            commit,
            "--trust-context",
            str(trust),
            "--standards-pin",
            str(standards),
            "--mapping-review",
            str(mapping),
            *_deployment_evidence_arguments(
                tmp_path,
                version,
                endpoint,
                commit,
            ),
            "--checks",
            str(_evidence(tmp_path, "checks.json", _checks(commit))),
            "--key",
            str(key),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "bulla" / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text())["signature"]["issuer"] == signer.issuer


def test_service_deploy_receipt_rejects_wrong_policy_and_publisher_review(
    tmp_path: Path,
):
    publisher = LocalEd25519Signer.generate()
    reviewer = LocalEd25519Signer.generate()
    commit = _reviewable_commit(tmp_path)
    version = "7b682718-9d67-4b2b-9f9e-2973697317e8"
    endpoint = "https://bulla-control-plane-alpha.example.workers.dev"
    digest = "sha256:" + "1" * 64
    mapping = _mapping_review(tmp_path, commit, digest)
    standards_document = _standards(commit, reviewer, mapping)
    standards = _evidence(
        tmp_path, "standards.json", standards_document
    )
    context_document = _trust_context(publisher, reviewer)
    context_document["trusted_policy_hashes"] = ["sha256:" + "0" * 64]
    trust = _evidence(tmp_path, "trust.json", context_document)
    command = [
        sys.executable,
        str(SCRIPT),
        "--worker-version",
        version,
        "--deployment-url",
        endpoint,
        "--commit",
        commit,
        "--trust-context",
        str(trust),
        "--standards-pin",
        str(standards),
        "--mapping-review",
        str(mapping),
        *_deployment_evidence_arguments(
            tmp_path,
            version,
            endpoint,
            commit,
        ),
        "--checks",
        str(_evidence(tmp_path, "checks.json", _checks(commit))),
        "--key",
        str(
            _evidence(
                tmp_path, "publisher.json", publisher.to_keyfile_dict()
            )
        ),
        "--out",
        str(tmp_path / "deployment.json"),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "bulla" / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "fixed alpha policy" in result.stderr

    trust.write_text(
        json.dumps(_trust_context(publisher, reviewer)),
        encoding="utf-8",
    )
    review = standards_document["final_review"]
    review["reviewer_issuer"] = publisher.issuer
    review["proof"] = publisher.sign_domain(
        "authorization", review["review_digest"], schema="0.4"
    )
    standards.write_text(json.dumps(standards_document), encoding="utf-8")
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "bulla" / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "role-separated" in result.stderr


def test_predeployment_gate_rejects_a_signed_but_incomplete_mapping_surface(
    tmp_path: Path,
):
    publisher = LocalEd25519Signer.generate()
    reviewer = LocalEd25519Signer.generate()
    commit = _reviewable_commit(tmp_path)
    digest = "sha256:" + "3" * 64
    mapping = _mapping_review(
        tmp_path,
        commit,
        digest,
        (
            "bulla/src/bulla/action_receipt.py",
            "bulla/src/bulla/identity.py",
            "bulla/spec/agent-incident-packet/PROFILE.md",
        ),
    )
    standards = _evidence(
        tmp_path,
        "standards.json",
        _standards(commit, reviewer, mapping, "3"),
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--preflight-only",
            "--commit",
            commit,
            "--trust-context",
            str(
                _evidence(
                    tmp_path,
                    "trust.json",
                    _trust_context(publisher, reviewer),
                )
            ),
            "--standards-pin",
            str(standards),
            "--mapping-review",
            str(mapping),
            "--key",
            str(
                _evidence(
                    tmp_path,
                    "publisher.json",
                    publisher.to_keyfile_dict(),
                )
            ),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "bulla" / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "frozen MCP-facing source set" in result.stderr


def test_service_deploy_receipt_rejects_forged_routing_phase(
    tmp_path: Path,
):
    publisher = LocalEd25519Signer.generate()
    reviewer = LocalEd25519Signer.generate()
    commit = _reviewable_commit(tmp_path)
    version = "7b682718-9d67-4b2b-9f9e-2973697317e8"
    endpoint = "https://bulla-control-plane-alpha.example.workers.dev"
    digest = "sha256:" + "4" * 64
    mapping = _mapping_review(tmp_path, commit, digest)
    standards = _evidence(
        tmp_path,
        "standards.json",
        _standards(commit, reviewer, mapping, "4"),
    )
    deployment_arguments = _deployment_evidence_arguments(
        tmp_path,
        version,
        endpoint,
        commit,
    )
    routing_index = deployment_arguments.index("--candidate-routing") + 1
    routing_path = Path(deployment_arguments[routing_index])
    forged = json.loads(routing_path.read_text(encoding="utf-8"))
    forged["versions"] = [{"version_id": version, "percentage": 100}]
    routing_path.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--worker-version",
            version,
            "--deployment-url",
            endpoint,
            "--commit",
            commit,
            "--trust-context",
            str(
                _evidence(
                    tmp_path,
                    "trust.json",
                    _trust_context(publisher, reviewer),
                )
            ),
            "--standards-pin",
            str(standards),
            "--mapping-review",
            str(mapping),
            *deployment_arguments,
            "--checks",
            str(_evidence(tmp_path, "checks.json", _checks(commit))),
            "--key",
            str(
                _evidence(
                    tmp_path,
                    "publisher.json",
                    publisher.to_keyfile_dict(),
                )
            ),
            "--out",
            str(tmp_path / "deployment.json"),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "bulla" / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "normalized allocation" in result.stderr


def test_candidate_smoke_cannot_claim_candidate_durable_object_assignment(
    tmp_path: Path,
):
    publisher = LocalEd25519Signer.generate()
    reviewer = LocalEd25519Signer.generate()
    commit = _reviewable_commit(tmp_path)
    version = "7b682718-9d67-4b2b-9f9e-2973697317e8"
    endpoint = "https://bulla-control-plane-alpha.example.workers.dev"
    digest = "sha256:" + "5" * 64
    mapping = _mapping_review(tmp_path, commit, digest)
    standards = _evidence(
        tmp_path,
        "standards.json",
        _standards(commit, reviewer, mapping, "5"),
    )
    deployment_arguments = _deployment_evidence_arguments(
        tmp_path,
        version,
        endpoint,
        commit,
    )
    smoke_index = deployment_arguments.index("--candidate-outer-smoke") + 1
    smoke_path = Path(deployment_arguments[smoke_index])
    forged = json.loads(smoke_path.read_text(encoding="utf-8"))
    forged["durable_object_assignment"] = "ATTESTED_BY_PROMOTED_FULL_LOOP"
    smoke_path.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--worker-version",
            version,
            "--deployment-url",
            endpoint,
            "--commit",
            commit,
            "--trust-context",
            str(
                _evidence(
                    tmp_path,
                    "trust.json",
                    _trust_context(publisher, reviewer),
                )
            ),
            "--standards-pin",
            str(standards),
            "--mapping-review",
            str(mapping),
            *deployment_arguments,
            "--checks",
            str(_evidence(tmp_path, "checks.json", _checks(commit))),
            "--key",
            str(
                _evidence(
                    tmp_path,
                    "publisher.json",
                    publisher.to_keyfile_dict(),
                )
            ),
            "--out",
            str(tmp_path / "deployment.json"),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "bulla" / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "does not bind expected durable_object_assignment" in result.stderr
