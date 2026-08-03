"""Release-boundary gates for the Bulla 0.45 release line."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import bulla
import pytest

from bulla.cli import main


ROOT = Path(__file__).resolve().parents[1]
CONTROLS_SPEC = importlib.util.spec_from_file_location(
    "verify_release_repository_controls",
    ROOT / "scripts/verify_release_repository_controls.py",
)
assert CONTROLS_SPEC is not None and CONTROLS_SPEC.loader is not None
CONTROLS = importlib.util.module_from_spec(CONTROLS_SPEC)
CONTROLS_SPEC.loader.exec_module(CONTROLS)


def _workflow_job(workflow: str, name: str) -> str:
    marker = f"  {name}:\n"
    start = workflow.index(marker)
    tail = workflow[start + len(marker) :]
    boundaries = [
        position
        for candidate in ("\n  verify:\n", "\n  sign:\n", "\n  prepare:\n")
        if (position := tail.find(candidate)) >= 0
    ]
    return tail[: min(boundaries)] if boundaries else tail


def test_release_version_and_status_language_are_synchronized() -> None:
    assert bulla.__version__ == "0.45.0"
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.45.0 — 2026-08-02 (release candidate)" in changelog
    assert "package version and the ActionReceipt format version are separate clocks" in changelog
    assert "## 0.44.4 — 2026-08-01" in changelog
    assert "## 0.44.3 — 2026-08-01 (unpublished)" in changelog
    assert "## 0.44.2 — 2026-07-29 (unpublished)" in changelog
    assert "## 0.44.1 — 2026-07-20" in changelog
    v04 = (ROOT / "spec/action-receipt-v0.4-draft.md").read_text(encoding="utf-8")
    spec_index = (ROOT / "spec/README.md").read_text(encoding="utf-8")
    assert "opt-in experimental draft included in Bulla 0.44.4" in v04
    assert "opt-in experimental draft included in Bulla 0.44.4" in spec_index
    assert "PyPI 0.44.4" not in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "release candidate" not in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "v0.4 reference implementation and vectors are source-only" not in v04
    assert "## 0.44.0 — 2026-07-19" in changelog
    spec = (ROOT / "spec/README.md").read_text(encoding="utf-8")
    assert "**Normative version:** `0.2`" in spec
    assert "**Opt-in released draft:** `0.3`" in spec


def test_publication_contract_binds_two_clocks_and_final_main_commit() -> None:
    contract = (ROOT / "docs/RELEASE-0.45.0.md").read_text(encoding="utf-8")
    assert "package version and the receipt\nformat version are separate clocks" in contract
    assert "A PR head, synthetic merge commit, pre-rebase commit, or" in contract
    assert "The exact green `main` commit is recorded as the sole `source_commit`" in contract
    assert "PyPI publication consumes the version." in contract
    assert "APPROVE BULLA 0.45.0 PUBLICATION" in contract
    assert "source_commit: <40-lowercase-hex>" in contract
    assert contract.count(
        "kit_sha256: 4ea268d7e1d7b99a30a3db5a4acfb240fcdb0d906391dda1f1a2fb9e4a3bc51b"
    ) == 2
    assert "bulla.glyph-deployment-evidence/0.1" in contract
    assert "deployment_evidence_sha256: <64-lowercase-hex>" in contract
    assert "glyph_commit: <40-lowercase-hex>" in contract
    assert contract.index("APPROVE BULLA 0.45.0 PUBLICATION") < contract.index(
        "APPROVE GLYPH VERIFICATION-KIT DEPLOYMENT"
    )
    assert contract.index("published-CLI evidence") > contract.index(
        "APPROVE BULLA 0.45.0 PUBLICATION"
    )
    assert contract.index("Verify PyPI's accepted wheel") < contract.index(
        "APPROVE GLYPH VERIFICATION-KIT DEPLOYMENT"
    )
    assert "approval is not transferable to\ncorrected assets or another release" in contract


def test_release_workflow_is_publish_then_verify_then_receipt() -> None:
    workflow = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    assert "\n  workflow_dispatch:\n" in workflow
    assert "\n  push:\n" not in workflow
    assert "authenticate default-branch preparation" in workflow
    assert "prepare_run_id:" in workflow
    assert "actions/runs/$PREPARE_RUN_ID" in workflow
    assert 'run.get("path") != ".github/workflows/prepare-release.yml"' in workflow
    assert 'run.get("conclusion") != "success"' in workflow
    assert 'run.get("head_branch") != "main"' in workflow
    assert 'os.environ["WORKFLOW_REF"] != "refs/heads/main"' in workflow
    assert "build:\n" in workflow
    assert "verify-slot:\n" in workflow
    assert "publish:\n" in workflow
    assert "verify-pypi:\n" in workflow
    assert "prepare-finalization:\n" in workflow
    assert "needs: [build, verify-slot]" in workflow
    assert "needs: publish" in workflow
    assert "needs: [build, verify-slot, verify-pypi]" in workflow
    assert "Build into a new empty candidate directory" in workflow
    assert "Verify exact candidate inventory" in workflow
    assert "action-receipt-v0.2-verification-kit.zip" in workflow
    assert "bulla receipt kit" in workflow
    assert "packages-dir: packages" in workflow
    assert workflow.count("id-token: write") == 1
    assert workflow.count("persist-credentials: false") >= 4
    assert "git status --porcelain=v1 --untracked-files=all" in workflow
    assert 'tee "$RUNNER_TEMP/pytest-summary.txt"' in workflow
    assert "tail -1 release-candidate/pytest-summary.txt" in workflow
    assert "tee pytest-summary.txt" not in workflow
    assert "BULLA_RELEASE_KEY" not in workflow
    assert "RELEASE_ADMIN_READ_TOKEN" not in workflow
    assert "open_release_slot.py" not in workflow
    assert "--clobber" not in workflow
    assert "--repository jkomkov/bulla" in workflow
    assert "--expected-commit \"$GITHUB_SHA\"" in workflow
    assert "--slot \"release-slot/$version.slot.json\"" in workflow
    assert "--context releases/release-trust-context.json" in workflow
    assert "mint_release_receipt.py" in workflow
    assert '--git-tag "$RELEASE_REF"' in workflow
    prepare_finalization = _workflow_job(workflow, "prepare-finalization")
    assert "fetch-depth: 0" in prepare_finalization
    assert "release-finalization-requirements.txt" in workflow
    assert workflow.count(
        "python -m pip install --require-hashes"
    ) >= 2
    assert 'gh release download "release-slot-$RELEASE_REF"' in workflow
    assert 'git rev-list -n 1 "$RELEASE_REF"' in workflow
    prepare = (ROOT / ".github" / "workflows" / "prepare-release.yml").read_text(encoding="utf-8")
    assert "persist-credentials: false" in prepare
    assert "gh auth setup-git" in prepare
    assert 'git tag -a "v$RELEASE_VERSION" "$SOURCE_COMMIT"' in prepare
    assert 'git push origin "refs/tags/v$RELEASE_VERSION"' in prepare
    assert (
        prepare.index('gh release verify "$slot_tag"')
        < prepare.index("gh auth setup-git")
        < prepare.index('git tag -a "v$RELEASE_VERSION"')
        < prepare.index('git push origin "refs/tags/v$RELEASE_VERSION"')
    )


def test_release_finalizer_recovers_without_republishing() -> None:
    workflow = (ROOT / ".github/workflows/finalize-release.yml").read_text(
        encoding="utf-8"
    )
    assert "verify_pypi_release.py" in workflow
    assert "release-finalization-requirements.txt" in workflow
    assert "trusted_release_signer.py sign-receipt" in workflow
    assert "verification/release-candidate/action-receipt-v0.2-verification-kit.zip" in workflow
    assert "environment: release-signing" in workflow
    assert "--expected-commit \"$SOURCE_COMMIT\"" in workflow
    assert "release-preimage/$RELEASE_VERSION.unsigned.json" in workflow
    assert "bulla.release-preimage-recovery/0.1" in workflow
    assert '"old": "main"' in workflow
    assert '"new": expected_tag' in workflow
    assert "archived preimage hashes do not recompute" in workflow
    assert "archived preimage is not the allowed historical repair" in workflow
    assert "342e66ce4dd67f3b080e982dc0e7d2b5580afb3bece490bcf7c9cdaa47107d74" in workflow
    assert '"30711907118"' in workflow
    assert "publish-run-preimage" in workflow
    assert "$RELEASE_VERSION.publish-run.unsigned.json" in workflow
    assert "$RELEASE_VERSION.recovered.unsigned.json" in workflow
    assert "$RELEASE_VERSION.preimage-recovery.json" in workflow
    assert "source_commit:" in workflow
    assert "publish_run_id:" in workflow
    assert "test_result:" not in workflow
    assert "release-finalization-${{ github.sha }}" not in workflow
    assert "verified-finalization-${{ inputs.source_commit }}" in workflow
    sign_job = _workflow_job(workflow, "sign")
    verify_job = _workflow_job(workflow, "verify")
    assert "python -m pip install --no-deps" not in sign_job
    assert "TRUSTED_SIGNER_SHA256:" in sign_job
    assert "refs/heads/main" in workflow
    assert "python -I scripts/trusted_release_signer.py sign-receipt" in workflow
    assert 'git rev-list -n 1 "v$RELEASE_VERSION"' in workflow
    assert "immutable-releases" in workflow
    assert 'gh release verify "v$RELEASE_VERSION"' in workflow
    assert 'gh release verify-asset "v$RELEASE_VERSION" "$asset"' in workflow
    assert "--jq '.immutable'" in workflow
    assert "release-repository-controls.json" in workflow
    assert "rulesets?targets=tag" in workflow
    assert "RELEASE_ADMIN_READ_TOKEN" in sign_job
    assert "environment: release-signing" in sign_job
    assert 'releases?per_page=100' not in verify_job
    assert 'releases?per_page=100' in sign_job
    assert "if len(matches) != 1" in sign_job
    assert 'release.get("tag_name")' in sign_job
    assert 'release.get("target_commitish")' in sign_job
    assert 'release.get("draft") is not True' in sign_job
    assert "GH_TOKEN: ${{ secrets.RELEASE_ADMIN_READ_TOKEN }}" in sign_job
    assert "--existing" in sign_job
    assert "existing-release-receipt" in sign_job
    assert "--context releases/release-trust-context.json" in sign_job
    assert "--clobber" not in workflow
    assert 'repos/$GITHUB_REPOSITORY/releases/assets/$asset_id' in sign_job
    assert "https://uploads.github.com/repos/$GITHUB_REPOSITORY/releases/$release_id/assets?name=$name" in sign_job
    assert "--hostname uploads.github.com" not in sign_job
    assert "GH_TOKEN: ${{ secrets.RELEASE_TAG_TOKEN }}" in sign_job
    assert '-f tag_name="v$RELEASE_VERSION"' in sign_job
    assert '-f target_commitish="$SOURCE_COMMIT"' in sign_job
    assert "-F draft=false" in sign_job
    assert "gh release edit \"v$RELEASE_VERSION\" --draft=false" not in workflow
    assert "pypa/gh-action-pypi-publish" not in workflow
    assert "twine upload" not in workflow
    assert "id-token: write" not in workflow


def test_pypi_verifier_is_standalone_before_candidate_install() -> None:
    source = (ROOT / "scripts/verify_pypi_release.py").read_text(encoding="utf-8")
    assert "from bulla" not in source
    assert "import bulla" not in source


def test_default_branch_slot_ceremony_precedes_tag_creation() -> None:
    workflow = (ROOT / ".github/workflows/prepare-release.yml").read_text(
        encoding="utf-8"
    )
    sign = workflow.index("trusted_release_signer.py open-slot")
    create = workflow.index('gh release create "v$RELEASE_VERSION"')
    upload = workflow.index('gh release upload "v$RELEASE_VERSION"')
    public_slot = workflow.index('gh release create "$slot_tag"')
    dispatch = workflow.index("gh workflow run publish.yml")
    assert sign < public_slot < create < upload < dispatch
    assert "BULLA_RELEASE_KEY" in workflow
    assert "environment: release-signing" in workflow
    assert "python -m pip install --require-hashes" in workflow
    assert "python -m pip install --no-deps" not in workflow
    assert "refs/heads/main" in workflow
    assert 'test "$SOURCE_COMMIT" = "$GITHUB_SHA"' in workflow
    assert "python -I scripts/trusted_release_signer.py open-slot" in workflow
    assert "--context releases/release-trust-context.json" in workflow
    assert "RELEASE_ADMIN_READ_TOKEN" in workflow
    assert "RELEASE_TAG_TOKEN" in workflow
    assert "actions: write" in workflow
    assert "contents: read" in workflow
    assert 'gh release verify "$slot_tag"' in workflow
    assert "--ref main" in workflow
    assert '-f prepare_run_id="$GITHUB_RUN_ID"' in workflow
    assert "--clobber" not in workflow


def test_external_release_controls_are_closed_and_machine_checked(
    tmp_path: Path,
) -> None:
    expected = ROOT / "releases/release-repository-controls.json"
    immutable = tmp_path / "immutable.json"
    immutable_ruleset = tmp_path / "immutable-ruleset.json"
    creation_ruleset = tmp_path / "creation-ruleset.json"
    immutable.write_text(json.dumps({"enabled": True, "enforced_by_owner": False}))
    immutable_ruleset.write_text(
        json.dumps(
            {
                "name": "immutable-bulla-release-tags",
                "target": "tag",
                "enforcement": "active",
                "bypass_actors": [],
                "conditions": {
                    "ref_name": {
                        "include": [
                            "refs/tags/release-slot-v*",
                            "refs/tags/v*",
                        ],
                        "exclude": [],
                    }
                },
                "rules": [
                    {"type": "deletion"},
                    {
                        "type": "update",
                        "parameters": {"update_allows_fetch_and_merge": False},
                    },
                ],
            }
        )
    )
    creation_ruleset.write_text(
        json.dumps(
            {
                "name": "maintainer-bulla-release-tag-creation",
                "target": "tag",
                "enforcement": "active",
                "bypass_actors": [
                    {
                        "actor_type": "User",
                        "actor_id": 4755091,
                        "bypass_mode": "always",
                    }
                ],
                "conditions": {
                    "ref_name": {
                        "include": [
                            "refs/tags/release-slot-v*",
                            "refs/tags/v*",
                        ],
                        "exclude": [],
                    }
                },
                "rules": [{"type": "creation"}],
            }
        )
    )
    CONTROLS.verify(expected, immutable, [immutable_ruleset, creation_ruleset])
    tampered = json.loads(creation_ruleset.read_text())
    tampered["bypass_actors"][0]["actor_id"] = 5
    creation_ruleset.write_text(json.dumps(tampered))
    with pytest.raises(CONTROLS.ControlError):
        CONTROLS.verify(expected, immutable, [immutable_ruleset, creation_ruleset])


def test_public_slot_audit_is_scheduled_and_read_only() -> None:
    workflow = (ROOT / ".github/workflows/audit-release-slots.yml").read_text(
        encoding="utf-8"
    )
    assert "\n  schedule:\n" in workflow
    assert "\n  pull_request:" not in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "release-slot-v" in workflow
    assert "slot_enforcement_epoch" in workflow
    assert "https://pypi.org/pypi/bulla/json" in workflow
    assert "slot-tag-refs.txt" in workflow
    assert "validate_release_inventory" in workflow
    assert 'gh release verify "$slot_tag"' in workflow
    assert 'gh release verify "v$version"' in workflow
    assert "--context releases/release-trust-context.json" in workflow


def test_composite_action_treats_inputs_as_data() -> None:
    action = (ROOT / "action.yml").read_text(encoding="utf-8")
    assert "eval " not in action
    assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in action
    assert (
        "github/codeql-action/upload-sarif@4187e74d05793876e9989daffde9c3e66b4acd07"
        in action
    )
    for unsafe in (
        'MODE="${{ inputs.mode }}"',
        'AUDIT_CMD="$AUDIT_CMD --manifests ${{ inputs.manifests-dir }}"',
        'AUDIT_CMD="$AUDIT_CMD ${{ inputs.mcp-config }}"',
        'bulla certify-update "${{ inputs.old-path }}"',
        'CHECK_CMD="$CHECK_CMD --format sarif ${{ inputs.path }}"',
    ):
        assert unsafe not in action
    lines = action.splitlines()
    run_blocks: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() != "run: |":
            continue
        indentation = len(line) - len(line.lstrip())
        body: list[str] = []
        for candidate in lines[index + 1 :]:
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= indentation:
                break
            body.append(candidate)
        run_blocks.append("\n".join(body))
    assert run_blocks
    assert all("${{ inputs." not in block for block in run_blocks)
    assert 'AUDIT_TARGET=(-- "$INPUT_MCP_CONFIG")' in action
    assert 'certify-update --format json -- "$INPUT_OLD_PATH" "$INPUT_NEW_PATH"' in action
    assert 'CHECK_CMD+=(--format sarif -- "$INPUT_PATH")' in action
    assert "uses: actions/setup-python@v" not in action
    assert "uses: github/codeql-action/upload-sarif@v" not in action


def test_experimental_modules_are_not_reexported_from_stable_root() -> None:
    assert all("experimental" not in name for name in bulla.__all__)


def test_cli_front_door_uses_receipt_first_truth_boundary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["bulla", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "Portable, recomputable receipts for consequential agent actions" in help_text
    assert "does not establish worldly truth" in help_text
    assert "authorless agent action" not in help_text
    assert "coherence fee" in help_text
    assert "disclosure and omission signals" in help_text


def test_package_copy_is_grammatical_and_keeps_the_legacy_diagnostic_bounded() -> None:
    module_copy = " ".join((bulla.__doc__ or "").split())
    assert "one measurable diagnostic a receipt can carry" in module_copy
    assert "one measurable a receipt can carry" not in module_copy
    assert "not an execution-failure predictor" in module_copy
