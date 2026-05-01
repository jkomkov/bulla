"""The Specification Gap: measuring what agents cannot produce unaided.

Core thesis:
    The coherence fee is a lower bound on information that must be acquired
    to make safe composition constructible. Agents recognize convention seams
    but do not reliably produce the disclosure set that closes them.

Experimental structure:
    One task. Three information conditions. Stratified by fee.

    Task: "Given this composition, what fields must be disclosed to make it safe?"
    Ground truth: Bulla's minimum_disclosure_set (|set| = fee, exact).

    Conditions:
        SCHEMA  — tool schemas only (JSON, no descriptions)
        NATURAL — schemas + natural-language descriptions
        ASSISTED — schemas + descriptions + Bulla diagnostic (blind spots enumerated)

    Independent variable: fee ∈ {0, 1, 2, 3, 4, ..., max}
    Dependent variable: Jaccard(produced_set, ground_truth_set)

Design principles:
    - One function generates the prompt. One function scores. One runs the trial.
    - Bulla provides all ground truth. No manual annotation.
    - The 240 nonzero-fee compositions from the real MCP corpus ARE the test set.
    - Zero synthetic data. Zero mock tools. Everything is real infrastructure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# Ground Truth
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CompositionCase:
    """A single test case: a real server-pair composition with known ground truth."""

    pair_name: str
    fee: int
    betti_1: int
    n_tools: int
    n_edges: int
    disclosure_set: frozenset[tuple[str, str]]  # (tool, field) pairs
    blind_spots: tuple[dict[str, str], ...]
    tool_schemas: dict[str, Any]  # raw MCP tool definitions by server
    composition_yaml: str  # the Bulla composition (for reference)


def load_corpus(
    manifests_dir: Path,
    pairs_jsonl: Path,
) -> list[CompositionCase]:
    """Load the 240 nonzero-fee cases with ground-truth disclosure sets.

    This is the only data-loading function. Everything downstream
    operates on CompositionCase objects.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

    from bulla.guard import BullaGuard
    from bulla.diagnostic import minimum_disclosure_set

    # Load manifests
    server_tools: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(manifests_dir.glob("*.json")):
        data = json.loads(path.read_text())
        tools = data.get("tools", []) if isinstance(data, dict) else data
        if isinstance(tools, list):
            server_tools[path.stem] = tools

    # Load pair metadata
    pairs = [
        json.loads(line)
        for line in pairs_jsonl.read_text().splitlines()
        if line.strip()
    ]

    cases: list[CompositionCase] = []
    for row in pairs:
        if row["n_edges"] == 0:
            continue  # skip edge-free (fee=0 by construction)

        left, right = row["left_server"], row["right_server"]
        if left not in server_tools or right not in server_tools:
            continue

        # Build composition via Bulla
        prefixed: list[dict[str, Any]] = []
        for server_name in (left, right):
            for tool in server_tools[server_name]:
                clone = dict(tool)
                clone["name"] = f"{server_name}__{tool['name']}"
                prefixed.append(clone)

        try:
            guard = BullaGuard.from_tools_list(prefixed, name=f"{left}+{right}")
            diag = guard.diagnose()
        except Exception:
            continue

        if diag.coherence_fee == 0:
            continue

        # Get ground truth: minimum disclosure set
        disclosure = minimum_disclosure_set(guard.composition)

        cases.append(CompositionCase(
            pair_name=row["pair_name"],
            fee=diag.coherence_fee,
            betti_1=diag.betti_1,
            n_tools=diag.n_tools,
            n_edges=diag.n_edges,
            disclosure_set=frozenset(tuple(d) for d in disclosure),
            blind_spots=tuple(
                {
                    "dimension": bs.dimension,
                    "edge": bs.edge,
                    "from_field": bs.from_field,
                    "to_field": bs.to_field,
                    "from_tool": bs.from_tool,
                    "to_tool": bs.to_tool,
                    "from_hidden": str(bs.from_hidden),
                    "to_hidden": str(bs.to_hidden),
                }
                for bs in diag.blind_spots
            ),
            tool_schemas={left: server_tools[left], right: server_tools[right]},
            composition_yaml="",  # omit for now
        ))

    return cases


# ═══════════════════════════════════════════════════════════════════════════════
# Prompt Generation (the single most important design choice)
# ═══════════════════════════════════════════════════════════════════════════════


TASK_PREAMBLE = """\
You are analyzing a composition of two tool servers for hidden convention conflicts.

Two servers expose tools that share parameter names. Some shared parameters \
have implicit conventions (expected formats, value ranges, semantic assumptions) \
that are not expressed in the schema. When these conventions differ, the \
composition has "blind spots" — semantic dimensions where bilateral inspection \
passes but the combined system may fail.

Your task: identify which (tool, field) pairs must be explicitly disclosed \
(made visible in the composition contract) to eliminate all blind spots.

Return ONLY a JSON array of [tool_name, field_name] pairs. Example:
[["server_a__tool_x", "path"], ["server_b__tool_y", "format"]]

Return the MINIMUM set needed. Do not over-disclose.\
"""


def prompt_schema_only(case: CompositionCase) -> str:
    """Condition 1: schemas only, no descriptions."""
    schemas = {}
    for server, tools in case.tool_schemas.items():
        for tool in tools:
            name = f"{server}__{tool['name']}"
            schema = tool.get("inputSchema", tool.get("input_schema", {}))
            schemas[name] = {
                "parameters": schema.get("properties", {}),
                "required": schema.get("required", []),
            }

    return f"""{TASK_PREAMBLE}

## Tool Schemas (parameters only, no descriptions)

{json.dumps(schemas, indent=2)}

## Your answer (JSON array of [tool_name, field_name] pairs):"""


def prompt_natural(case: CompositionCase) -> str:
    """Condition 2: schemas + natural-language descriptions."""
    tools_info = {}
    for server, tools in case.tool_schemas.items():
        for tool in tools:
            name = f"{server}__{tool['name']}"
            schema = tool.get("inputSchema", tool.get("input_schema", {}))
            tools_info[name] = {
                "description": tool.get("description", ""),
                "parameters": schema.get("properties", {}),
                "required": schema.get("required", []),
            }

    return f"""{TASK_PREAMBLE}

## Tool Definitions (with descriptions)

{json.dumps(tools_info, indent=2)}

## Your answer (JSON array of [tool_name, field_name] pairs):"""


def prompt_assisted(case: CompositionCase) -> str:
    """Condition 3: schemas + descriptions + Bulla diagnostic."""
    tools_info = {}
    for server, tools in case.tool_schemas.items():
        for tool in tools:
            name = f"{server}__{tool['name']}"
            schema = tool.get("inputSchema", tool.get("input_schema", {}))
            tools_info[name] = {
                "description": tool.get("description", ""),
                "parameters": schema.get("properties", {}),
                "required": schema.get("required", []),
            }

    diagnostic_info = {
        "coherence_fee": case.fee,
        "betti_1": case.betti_1,
        "blind_spots": [
            {
                "dimension": bs["dimension"],
                "edge": bs["edge"],
                "from_field": bs["from_field"],
                "to_field": bs["to_field"],
                "from_hidden": bs["from_hidden"],
                "to_hidden": bs["to_hidden"],
            }
            for bs in case.blind_spots
        ],
        "interpretation": (
            f"This composition has {case.fee} hidden convention dimension(s) "
            f"that are invisible to bilateral checking. "
            f"Exactly {case.fee} field disclosures are needed to eliminate all blind spots."
        ),
    }

    return f"""{TASK_PREAMBLE}

## Tool Definitions (with descriptions)

{json.dumps(tools_info, indent=2)}

## Bulla Diagnostic (structural analysis of this composition)

{json.dumps(diagnostic_info, indent=2)}

## Your answer (JSON array of [tool_name, field_name] pairs):"""


PROMPT_FNS = {
    "schema": prompt_schema_only,
    "natural": prompt_natural,
    "assisted": prompt_assisted,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Scoring (exact and beautiful)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Score:
    """Score for a single trial."""

    case_name: str
    condition: str
    fee: int
    betti_1: int
    produced_set: frozenset[tuple[str, str]]
    ground_truth: frozenset[tuple[str, str]]

    @property
    def jaccard(self) -> float:
        """Jaccard similarity: |intersection| / |union|."""
        if not self.ground_truth and not self.produced_set:
            return 1.0
        union = self.ground_truth | self.produced_set
        if not union:
            return 1.0
        return len(self.ground_truth & self.produced_set) / len(union)

    @property
    def recall(self) -> float:
        """What fraction of required disclosures did the agent find?"""
        if not self.ground_truth:
            return 1.0
        return len(self.ground_truth & self.produced_set) / len(self.ground_truth)

    @property
    def precision(self) -> float:
        """What fraction of produced disclosures are actually needed?"""
        if not self.produced_set:
            return 1.0 if not self.ground_truth else 0.0
        return len(self.ground_truth & self.produced_set) / len(self.produced_set)

    @property
    def exact_match(self) -> bool:
        """Did the agent produce exactly the minimum disclosure set?"""
        return self.produced_set == self.ground_truth

    @property
    def size_ratio(self) -> float:
        """Ratio of produced size to ground truth size. 1.0 = correct cardinality."""
        if self.fee == 0:
            return 1.0 if len(self.produced_set) == 0 else float("inf")
        return len(self.produced_set) / self.fee

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "condition": self.condition,
            "fee": self.fee,
            "betti_1": self.betti_1,
            "jaccard": round(self.jaccard, 4),
            "recall": round(self.recall, 4),
            "precision": round(self.precision, 4),
            "exact_match": self.exact_match,
            "size_ratio": round(self.size_ratio, 4),
            "produced_size": len(self.produced_set),
            "ground_truth_size": len(self.ground_truth),
        }


def parse_agent_response(text: str) -> frozenset[tuple[str, str]]:
    """Parse agent's JSON response into a set of (tool, field) pairs.

    Robust to common formatting issues (markdown fences, trailing text).
    """
    # Strip markdown code fences
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines)

    # Find the JSON array
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return frozenset()

    try:
        arr = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return frozenset()

    pairs: set[tuple[str, str]] = set()
    for item in arr:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            pairs.add((str(item[0]), str(item[1])))
    return frozenset(pairs)


def score_response(
    case: CompositionCase,
    condition: str,
    agent_text: str,
) -> Score:
    """Score a single agent response against ground truth."""
    produced = parse_agent_response(agent_text)
    return Score(
        case_name=case.pair_name,
        condition=condition,
        fee=case.fee,
        betti_1=case.betti_1,
        produced_set=produced,
        ground_truth=case.disclosure_set,
    )
