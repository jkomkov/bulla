from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/bulla-control-plane-alpha-deploy.yml"
SOURCE_WORKFLOW = ROOT / ".github/workflows/bulla-control-plane-alpha.yml"
BLOCKERS = (
    ROOT
    / "packages/bulla-control-plane-alpha/fixtures/deployment-blockers.json"
)
FALSIFICATIONS = ROOT / "bulla/FALSIFICATIONS.md"


def test_production_entrypoint_is_manual_validation_only_and_exact_commit_gated() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "\n  push:" not in source
    assert "environment: bulla-control-plane-alpha-production" in source
    assert 'test "$EXPECTED_COMMIT" = "$(git rev-parse HEAD)"' in source
    assert "git merge-base --is-ancestor" in source
    assert "origin/main" in source
    assert 'test -z "$(git status --porcelain --untracked-files=all)"' in source
    assert "Production remains blocked" in source
    assert "exit 1" in source
    assert "persist-credentials: false" in source

    commit_proof = source.index("Prove exact merged commit")
    setup_node = source.index("actions/setup-node@", commit_proof)
    setup_pnpm = source.index("pnpm/action-setup@", setup_node)
    install = source.index("pnpm install --frozen-lockfile", setup_pnpm)
    strict_parse = source.index("parseStrictJsonBytes", install)
    checks = source.index(
        "pnpm --dir packages/bulla-control-plane-alpha test", strict_parse
    )
    refusal = source.index("Production remains blocked", checks)
    assert commit_proof < setup_node < setup_pnpm < install < strict_parse < checks < refusal


def test_blocked_workflow_contains_no_remote_mutation_command() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    forbidden = (
        r"\bwrangler\s+(?:deploy|versions\s+upload|versions\s+deploy|rollback)\b",
        r"\bvercel\s+(?:deploy|promote|rollback)\b",
        r"\bgh\s+release\s+(?:create|edit|upload|delete)\b",
        r"\bgh\s+api\s+--method\s+(?:POST|PATCH|PUT|DELETE)\b",
    )
    for pattern in forbidden:
        assert re.search(pattern, source) is None
    assert "contents: read" in source
    assert "contents: write" not in source


def test_withdrawn_glyph_deployment_receipt_has_a_public_correction_record() -> None:
    source = FALSIFICATIONS.read_text(encoding="utf-8")
    normalized = " ".join(source.split())
    required = (
        "Withdrawn: Glyph website deployment receipt automation (2026-07-27)",
        "Binding to deployed output is `NOT_COMPUTED`.",
        "digest of a CI-local rebuild",
        "It did not hash the bytes deployed by Vercel",
        "event-selected code to access the signing capability",
        "does **not** establish that the secret was exfiltrated",
        "the isolation boundary was unsound",
        "selected the verifier and minter",
        "repository and its release pages are access-controlled",
        "glyphstandard.com/status/deployment-receipt-withdrawal",
        "`CP-B4-DEPLOY-13`",
        "commit `9636a67a`",
    )
    for statement in required:
        assert statement in normalized


def test_control_plane_packet_vectors_disable_git_text_conversion() -> None:
    vector_root = ROOT / "bulla/spec/control-plane-alpha/vectors"
    members = sorted(
        path.relative_to(ROOT).as_posix()
        for path in vector_root.rglob("*")
        if path.is_file()
    )
    assert members
    result = subprocess.run(
        ["git", "check-attr", "text", "--", *members],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    attributes = {
        line.split(": ", 2)[0]: line.split(": ", 2)[2]
        for line in result.stdout.splitlines()
    }
    assert attributes == {member: "unset" for member in members}


def test_blocker_contract_is_closed_and_names_every_unimplemented_safety_boundary() -> None:
    value = json.loads(BLOCKERS.read_text(encoding="utf-8"))
    assert value == {
        "schema_version": 1,
        "profile": "bulla.control-plane-alpha/0.1-experimental",
        "production_status": "BLOCKED_NO_REMOTE_MUTATION_AUTHORIZED",
        "blockers": {
            "external_transaction_reconciler": (
                "BLOCKED_EXTERNAL_RECONCILER_NOT_IMPLEMENTED"
            ),
            "glyph_provenance": (
                "BLOCKED_EXACT_OVERLAY_PROJECT_DEPLOYMENT_BINDING_NOT_IMPLEMENTED"
            ),
            "key_rotation_retirement": (
                "BLOCKED_KEY_ROTATION_RETIREMENT_NOT_IMPLEMENTED"
            ),
            "signed_release_asset_manifest": (
                "BLOCKED_COMPLETE_SIGNED_MANIFEST_NOT_IMPLEMENTED"
            ),
            "retirement_export": (
                "BLOCKED_EXPORTER_SOURCE_TESTS_EVIDENCE_NOT_IMPLEMENTED"
            ),
        },
        "recovery": {
            "key_mode": "RECOVERY_PUBLIC_READ_ONLY",
            "status": (
                "BLOCKED_KNOWN_READ_COMPATIBLE_RELEASE_NOT_IMPLEMENTED"
            ),
        },
    }


def test_external_reconciler_and_key_lifecycle_terms_are_exact_across_surfaces() -> None:
    exact_reconciler = "BLOCKED_EXTERNAL_RECONCILER_NOT_IMPLEMENTED"
    retired_reconciler = "BLOCKED_DURABLE_RECONCILER_NOT_IMPLEMENTED"
    lifecycle = "BLOCKED_KEY_ROTATION_RETIREMENT_NOT_IMPLEMENTED"
    surfaces = (
        BLOCKERS,
        WORKFLOW,
        ROOT / "packages/bulla-control-plane-alpha/wrangler.jsonc",
        ROOT / "packages/bulla-control-plane-alpha/wrangler.bootstrap.jsonc",
        ROOT
        / "packages/bulla-control-plane-alpha/fixtures/service-manifest-contract.json",
        ROOT / "glyph/data/control-plane-alpha.json",
    )
    for path in surfaces:
        source = path.read_text(encoding="utf-8")
        assert retired_reconciler not in source
        assert exact_reconciler in source
        assert lifecycle in source

    ceremony = (
        ROOT / "packages/bulla-control-plane-alpha/scripts/key-ceremony.mjs"
    ).read_text(encoding="utf-8")
    gate = (
        ROOT / "packages/bulla-control-plane-alpha/scripts/deployment-gate.mjs"
    ).read_text(encoding="utf-8")
    policy = (
        ROOT / "packages/bulla-control-plane-alpha/scripts/policy-contract.mjs"
    ).read_text(encoding="utf-8")
    assert "keyset_version" in ceremony
    assert "capability_token_derivation" in ceremony
    assert lifecycle in policy
    assert "KEY_ROTATION_RETIREMENT_BLOCKER" in gate
    assert "production deployment is blocked" in gate


def test_required_source_ci_executes_the_exact_two_state_projection_checker() -> None:
    source = SOURCE_WORKFLOW.read_text(encoding="utf-8")
    clean_source = source[source.index("clean-source:") :]
    assert (
        "scripts/check-source-deployment-projection.mjs" in clean_source
    )
    assert (
        "packages/bulla-control-plane-alpha/wrangler.jsonc" in clean_source
    )
    assert "glyph/data/control-plane-alpha.json" in clean_source
    assert 'evidence["deployment_status"] == "NOT_DEPLOYED"' not in clean_source
    assert (
        'evidence["deployment_gate"] == "BLOCKED_MCP_FINAL_NOT_PUBLISHED"'
        not in clean_source
    )
