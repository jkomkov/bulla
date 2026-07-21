from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bench" / "invention" / "external" / "freeze_external.py"
SPEC = importlib.util.spec_from_file_location("freeze_external", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
FREEZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FREEZE)


def _manifest() -> dict:
    corpus = json.loads((ROOT / "bench" / "invention" / "corpus.json").read_text())
    problem = corpus["instances"][0]["problem"]
    from bulla.experimental.invention import SeamProblem

    checked = SeamProblem.from_dict(problem)
    return {
        "schema_version": "0.2-external-pilot",
        "engine_commit": "1" * 40,
        "frsl_1_spec_sha256": "sha256:" + "2" * 64,
        "implementation_team_ids": ["impl-1"],
        "author_ids": ["author-1", "author-2", "author-3"],
        "adjudicator_ids": ["judge-1", "judge-2", "judge-3"],
        "target_n_per_domain": 100,
        "domains": ["commercial", "identity", "logistics"],
        "instances": [
            {
                "id": "foreign-1",
                "domain": "commercial",
                "stratum": "hidden_generative_contract",
                "author_id": "author-1",
                "problem_hash": checked.problem_hash,
                "problem": problem,
            }
        ],
    }


def test_incomplete_manifest_can_be_hashed_but_not_promoted() -> None:
    result = FREEZE.validate(_manifest(), allow_incomplete=True)
    assert result["ok"] is True
    assert result["ready_for_blind_evaluation"] is False
    assert result["corpus_hash"].startswith("sha256:")


def test_role_overlap_fails_closed() -> None:
    manifest = _manifest()
    manifest["adjudicator_ids"][0] = "author-1"
    result = FREEZE.validate(manifest, allow_incomplete=True)
    assert result["ok"] is False
    assert any("role overlap" in error for error in result["errors"])


def test_answer_fields_and_unfrozen_commit_are_rejected() -> None:
    manifest = _manifest()
    manifest["engine_commit"] = "TO_BE_REPLACED"
    manifest["instances"][0]["safe_to_accept"] = True
    result = FREEZE.validate(manifest, allow_incomplete=True)
    assert result["ok"] is False
    assert result["ready_for_blind_evaluation"] is False


def test_malformed_manifest_types_fail_closed_without_crashing() -> None:
    manifest = _manifest()
    manifest["target_n_per_domain"] = "100"
    manifest["domains"] = {"commercial": True}
    manifest["instances"] = "not-an-array"
    result = FREEZE.validate(manifest, allow_incomplete=True)
    assert result["ok"] is False
    assert result["ready_for_blind_evaluation"] is False
    assert len(result["errors"]) >= 3
