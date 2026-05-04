"""Truth-set gates for URL/path seam precision and recall."""

from __future__ import annotations

import json
from pathlib import Path

from bulla.guard import BullaGuard


TRUTH_SET_PATH = Path(__file__).parent / "fixtures" / "url_path_truth_set.json"


def _load_truth_set() -> dict:
    return json.loads(TRUTH_SET_PATH.read_text(encoding="utf-8"))


def _server_of(tool_name: str) -> str:
    return tool_name.split("__", 1)[0]


def _build_tools(case: dict) -> list[dict]:
    tools: list[dict] = []
    for t in case["tools"]:
        tool = {
            "name": f"{t['server']}__{t['name']}",
            "description": t.get("description", ""),
            "inputSchema": t["inputSchema"],
        }
        tools.append(tool)
    return tools


def _predict_path_seam(case: dict) -> tuple[bool, set[str]]:
    guard = BullaGuard.from_tools_list(_build_tools(case), name=case["id"])
    diag = guard.diagnose()
    boundary_dims: set[str] = set()
    for bs in diag.blind_spots:
        if _server_of(bs.from_tool) == _server_of(bs.to_tool):
            continue
        boundary_dims.add(bs.dimension)
    return ("path_convention_match" in boundary_dims), boundary_dims


class TestUrlPathTruthSet:
    def test_fixture_contains_positive_and_negative_cases(self):
        payload = _load_truth_set()
        cases = payload["cases"]
        assert len(cases) >= 5
        positives = [c for c in cases if c["expect_seam"]]
        negatives = [c for c in cases if not c["expect_seam"]]
        assert len(positives) >= 2
        assert len(negatives) >= 2

    def test_each_case_matches_expected_seam_outcome(self):
        payload = _load_truth_set()
        for case in payload["cases"]:
            predicted, boundary_dims = _predict_path_seam(case)
            assert predicted == case["expect_seam"], (
                f"{case['id']} expected {case['expect_seam']} got {predicted}; "
                f"boundary_dims={sorted(boundary_dims)}; "
                f"rationale={case['rationale']}"
            )

    def test_precision_recall_gate(self):
        payload = _load_truth_set()
        gates = payload["gates"]
        tp = fp = fn = tn = 0
        for case in payload["cases"]:
            predicted, _ = _predict_path_seam(case)
            expected = bool(case["expect_seam"])
            if predicted and expected:
                tp += 1
            elif predicted and not expected:
                fp += 1
            elif not predicted and expected:
                fn += 1
            else:
                tn += 1

        assert tp >= gates["min_true_positives"], (
            f"tp={tp} below min_true_positives={gates['min_true_positives']}"
        )
        assert fp <= gates["max_false_positives"], (
            f"fp={fp} exceeds max_false_positives={gates['max_false_positives']}"
        )
        assert fn <= gates["max_false_negatives"], (
            f"fn={fn} exceeds max_false_negatives={gates['max_false_negatives']}"
        )
