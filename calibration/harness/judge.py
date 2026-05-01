"""Scoring and classification for agent tool-selection responses.

Three-level evaluation:
  1. Tool selection — did the agent pick the ground-truth tool?
  2. Convention compliance — correct tool + correct parameter conventions?
  3. Error classification — if wrong, WHY was it wrong?

The error taxonomy is critical for the paper claim: we need to show that
errors in the cyclic arm are specifically *convention confusion*, not
random noise or generic tool-selection difficulty.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ErrorType(Enum):
    CORRECT = "correct"
    CONVENTION_CONFUSION = "convention_confusion"
    DEPRECATED_TOOL_USE = "deprecated_tool_use"
    CONTENT_TYPE_MISMATCH = "content_type_mismatch"
    PARAM_CONVENTION_ERROR = "param_convention_error"
    RANDOM_ERROR = "random_error"
    REFUSAL = "refusal"


@dataclass(frozen=True)
class JudgeResult:
    task_id: str
    arm: str
    repetition: int
    selected_tool: str | None
    selected_params: dict[str, Any]
    ground_truth_tool: str
    ground_truth_params: dict[str, Any]
    tool_correct: bool
    params_correct: bool
    error_type: ErrorType
    convention_violated: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "arm": self.arm,
            "repetition": self.repetition,
            "selected_tool": self.selected_tool,
            "selected_params": self.selected_params,
            "ground_truth_tool": self.ground_truth_tool,
            "ground_truth_params": self.ground_truth_params,
            "tool_correct": self.tool_correct,
            "params_correct": self.params_correct,
            "error_type": self.error_type.value,
            "convention_violated": self.convention_violated,
            "notes": self.notes,
        }


# ─── Tool-name equivalence maps for scoring across arms ──────────────────────

# In arm A (cyclic): deprecated tool
CYCLIC_DEPRECATED = {"read_file"}
CYCLIC_TEXT = {"read_text_file"}
CYCLIC_MEDIA = {"read_media_file"}

# In arm C (disambiguated): deprecated tool
DISAMBIGUATED_DEPRECATED = {"read_file_legacy"}
DISAMBIGUATED_TEXT = {"read_file_text"}
DISAMBIGUATED_MEDIA = {"read_file_media"}


def _classify_cyclic_error(
    selected: str,
    ground_truth: str,
    task_hidden_dimension: str,
) -> tuple[ErrorType, str | None]:
    """Classify error type for cyclic arm."""
    if selected in CYCLIC_DEPRECATED:
        return ErrorType.DEPRECATED_TOOL_USE, "deprecation"

    if ground_truth in CYCLIC_TEXT and selected in CYCLIC_MEDIA:
        return ErrorType.CONTENT_TYPE_MISMATCH, "content_type"

    if ground_truth in CYCLIC_MEDIA and selected in CYCLIC_TEXT:
        return ErrorType.CONTENT_TYPE_MISMATCH, "content_type"

    return ErrorType.CONVENTION_CONFUSION, task_hidden_dimension


def _classify_disambiguated_error(
    selected: str,
    ground_truth: str,
    task_hidden_dimension: str,
) -> tuple[ErrorType, str | None]:
    """Classify error type for disambiguated arm."""
    if selected in DISAMBIGUATED_DEPRECATED:
        return ErrorType.DEPRECATED_TOOL_USE, "deprecation"

    if ground_truth in DISAMBIGUATED_TEXT and selected in DISAMBIGUATED_MEDIA:
        return ErrorType.CONTENT_TYPE_MISMATCH, "content_type"

    if ground_truth in DISAMBIGUATED_MEDIA and selected in DISAMBIGUATED_TEXT:
        return ErrorType.CONTENT_TYPE_MISMATCH, "content_type"

    return ErrorType.CONVENTION_CONFUSION, task_hidden_dimension


def _check_params(selected_params: dict, ground_truth_params: dict) -> bool:
    """Check parameter convention compliance.

    For tool selection experiments, we primarily care about:
    - path matches exactly
    - head/tail used when expected (partial read convention)

    We do NOT require exact string matching on param values for things
    like email bodies or search queries — only structural params matter.
    """
    # Path must match
    if "path" in ground_truth_params:
        if selected_params.get("path") != ground_truth_params["path"]:
            return False

    # head/tail must be present if expected
    for param in ("head", "tail"):
        if param in ground_truth_params:
            if param not in selected_params:
                return False

    return True


def judge_response(
    task_id: str,
    arm: str,
    repetition: int,
    ground_truth_tool: str,
    ground_truth_params: dict[str, Any],
    hidden_dimension: str,
    agent_response: dict[str, Any] | None,
) -> JudgeResult:
    """Score a single agent response.

    Parameters
    ----------
    agent_response : dict or None
        Expected shape: {"tool": "tool_name", "params": {...}}
        None indicates the agent refused or failed to produce a tool call.
    """
    if agent_response is None:
        return JudgeResult(
            task_id=task_id,
            arm=arm,
            repetition=repetition,
            selected_tool=None,
            selected_params={},
            ground_truth_tool=ground_truth_tool,
            ground_truth_params=ground_truth_params,
            tool_correct=False,
            params_correct=False,
            error_type=ErrorType.REFUSAL,
        )

    selected_tool = agent_response.get("tool", "")
    selected_params = agent_response.get("params", {})

    tool_correct = selected_tool == ground_truth_tool
    params_correct = _check_params(selected_params, ground_truth_params) if tool_correct else False

    if tool_correct and params_correct:
        error_type = ErrorType.CORRECT
        convention_violated = None
    elif tool_correct and not params_correct:
        error_type = ErrorType.PARAM_CONVENTION_ERROR
        convention_violated = "partial_read" if ("head" in ground_truth_params or "tail" in ground_truth_params) else None
    else:
        # Wrong tool — classify why
        if arm == "cyclic":
            error_type, convention_violated = _classify_cyclic_error(
                selected_tool, ground_truth_tool, hidden_dimension
            )
        elif arm == "disambiguated":
            error_type, convention_violated = _classify_disambiguated_error(
                selected_tool, ground_truth_tool, hidden_dimension
            )
        elif arm == "overlap_control":
            # Overlap control — errors are about operation-type confusion
            # (read vs write vs metadata), not hidden conventions
            error_type = ErrorType.RANDOM_ERROR
            convention_violated = None
        else:
            # Acyclic arm — any error is random
            error_type = ErrorType.RANDOM_ERROR
            convention_violated = None

    return JudgeResult(
        task_id=task_id,
        arm=arm,
        repetition=repetition,
        selected_tool=selected_tool,
        selected_params=selected_params,
        ground_truth_tool=ground_truth_tool,
        ground_truth_params=ground_truth_params,
        tool_correct=tool_correct,
        params_correct=params_correct,
        error_type=error_type,
        convention_violated=convention_violated,
    )
