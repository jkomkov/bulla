"""Familiarity covariate: quantitative proxy for training-data exposure.

The thesis: hiddenness identification accuracy is a function of prior exposure,
not schema complexity. We prove this by showing a monotone relationship between
GitHub stars (proxy for training-data presence) and the model's ability to
identify which fields are hidden in a composition.

Design:
    For each server S in the corpus, compute:
        - familiarity(S) = log10(github_stars(S))
        - identification_rate(S) = fraction of S's hidden fields correctly identified
          across all compositions involving S

    The claim is supported if Spearman(familiarity, identification_rate) > 0
    with p < 0.05.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# GitHub stars as of 2025-04-20 (snapshot for reproducibility)
GITHUB_STARS: dict[str, int] = {
    "filesystem": 84181,
    "github": 84181,
    "memory": 84181,
    "fetch-mcp": 84181,
    "mcp-server-fetch": 84181,
    "puppeteer": 84181,
    "sequential-thinking": 84181,
    "playwright": 5456,
    "mcp-playwright": 5456,
    "notion": 4245,
    "exa": 4279,
    "cognee-mcp-server": 16567,
    "mcp-server-cloudflare": 3648,
    "mcp-server-browserbase": 3276,
    "tavily": 1805,
    "mcp-server-kubernetes": 1378,
    "mcp-server-chatsum": 1035,
    "mcp-server-neon": 587,
    "todoist-mcp-server": 389,
    "twitter-mcp": 387,
    "mcp-mongo-server": 277,
    "search1api-mcp": 172,
    "mcp-pinecone": 149,
    "gtasks-mcp": 125,
    "needle-mcp": 97,
    "needle-mcp_tools": 97,
    "mcp-vegalite-server": 96,
    "airtable-mcp": 73,
    "mcp-xmind": 67,
    "mcp-server-tmdb": 67,
    "flightradar24-mcp-server": 46,
    "mcp-snowflake-server": 46,
    "mcp-server-rememberizer": 35,
    # Not found on GitHub — assign minimum
    "mcp-server-aws": 30,
    "inoyu-mcp-unomi-server": 10,
    "ns-mcp-server": 10,
    "openrpc-mpc-server": 10,
    "x-mcp": 387,
}


def log_familiarity(server: str) -> float:
    """log10(stars) — the quantitative familiarity axis."""
    stars = GITHUB_STARS.get(server, 10)
    return math.log10(max(stars, 1))


@dataclass(frozen=True)
class ServerIdentificationResult:
    """Per-server identification accuracy across all compositions."""

    server: str
    stars: int
    log_stars: float
    total_hidden_fields: int
    correctly_identified: int
    identification_rate: float


@dataclass(frozen=True)
class FamiliarityProbeResult:
    """Result of a single composition probe."""

    pair_name: str
    left_server: str
    right_server: str
    fee: int
    # Per-server breakdown
    left_hidden_fields: frozenset[str]
    right_hidden_fields: frozenset[str]
    left_identified: frozenset[str]
    right_identified: frozenset[str]


COT_IDENTIFICATION_PROMPT = """\
You are analyzing a composition of two MCP tool servers for hidden fields.

A field is "hidden" if:
- It appears as a parameter in at least one tool
- Its semantic convention (format, expected values, meaning) is not observable \
from the schema alone
- Its convention could differ between the two servers without any schema-level \
indication

Your task: identify which fields in each server have hidden conventions that \
could create silent composition failures.

## Server: {left_server}
Tools:
{left_tools_json}

## Server: {right_server}
Tools:
{right_tools_json}

## Instructions
For each server, list the field names that have hidden conventions. \
Return JSON with this exact structure:
{{
  "{left_server}": ["field1", "field2", ...],
  "{right_server}": ["field3", "field4", ...]
}}

Only include fields whose conventions are genuinely ambiguous or \
under-specified by the schema. Do not include fields with obvious semantics \
(like "name" or "description" when their meaning is unambiguous).\
"""


def build_identification_prompt(
    left_server: str,
    right_server: str,
    left_tools: list[dict[str, Any]],
    right_tools: list[dict[str, Any]],
) -> str:
    """Build the CoT identification prompt for a composition."""
    return COT_IDENTIFICATION_PROMPT.format(
        left_server=left_server,
        right_server=right_server,
        left_tools_json=json.dumps(left_tools, indent=2),
        right_tools_json=json.dumps(right_tools, indent=2),
    )


def parse_identification_response(
    text: str,
    left_server: str,
    right_server: str,
) -> tuple[set[str], set[str]]:
    """Parse model's identification response into per-server field sets."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines)

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return set(), set()

    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return set(), set()

    left_fields = set(data.get(left_server, []))
    right_fields = set(data.get(right_server, []))
    return left_fields, right_fields


def compute_server_identification_rates(
    results: list[FamiliarityProbeResult],
) -> list[ServerIdentificationResult]:
    """Aggregate identification accuracy per server."""
    server_total: dict[str, int] = {}
    server_correct: dict[str, int] = {}

    for r in results:
        # Left server
        n_left = len(r.left_hidden_fields)
        if n_left > 0:
            server_total[r.left_server] = server_total.get(r.left_server, 0) + n_left
            correct = len(r.left_identified & r.left_hidden_fields)
            server_correct[r.left_server] = server_correct.get(r.left_server, 0) + correct

        # Right server
        n_right = len(r.right_hidden_fields)
        if n_right > 0:
            server_total[r.right_server] = server_total.get(r.right_server, 0) + n_right
            correct = len(r.right_identified & r.right_hidden_fields)
            server_correct[r.right_server] = server_correct.get(r.right_server, 0) + correct

    out = []
    for server in sorted(server_total.keys()):
        total = server_total[server]
        correct = server_correct.get(server, 0)
        stars = GITHUB_STARS.get(server, 10)
        out.append(ServerIdentificationResult(
            server=server,
            stars=stars,
            log_stars=math.log10(max(stars, 1)),
            total_hidden_fields=total,
            correctly_identified=correct,
            identification_rate=correct / total if total > 0 else 0.0,
        ))

    return out


def spearman_correlation(x: list[float], y: list[float]) -> tuple[float, float]:
    """Spearman rank correlation with p-value.

    Falls back to simple rank correlation if scipy unavailable.
    """
    n = len(x)
    if n < 3:
        return 0.0, 1.0

    try:
        from scipy.stats import spearmanr
        rho, p = spearmanr(x, y)
        return float(rho), float(p)
    except ImportError:
        # Manual Spearman
        def _rank(vals):
            indexed = sorted(enumerate(vals), key=lambda t: t[1])
            ranks = [0.0] * len(vals)
            for rank_val, (idx, _) in enumerate(indexed):
                ranks[idx] = rank_val + 1
            return ranks

        rx = _rank(x)
        ry = _rank(y)
        d_sq = sum((a - b) ** 2 for a, b in zip(rx, ry))
        rho = 1 - (6 * d_sq) / (n * (n * n - 1))
        # Approximate p-value via t-distribution
        import math
        if abs(rho) >= 1.0:
            return rho, 0.0
        t = rho * math.sqrt((n - 2) / (1 - rho * rho))
        # Two-sided p from t with n-2 df (normal approximation for large n)
        from statistics import NormalDist
        p = 2 * (1 - NormalDist().cdf(abs(t)))
        return rho, p
