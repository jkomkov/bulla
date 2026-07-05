"""Receipt coverage against a declared anchor — omission detection.

    coverage = |receipted ∩ anchored| / |anchored|

A plain set difference. The point is not the arithmetic; it is the DENOMINATOR.
A bare "coverage: 88%" is decorative, because a gateway can only count what
routes through it — route less through it and coverage climbs to 100% while
accountability falls to zero. Coverage is only meaningful RELATIVE to an anchor:
a record you did NOT mint — git tags, an MCP transport log, a cloud audit trail —
against which missing receipts become visible.

v0.1 ships exactly one anchor (git). The report is a MIN over declared anchors
(one, for now) and names the weakest, so the headline stays quotable and honest:

    Coverage: 17% (weakest anchor: git)   +   the unreceipted-delta list

Two denominators are coming (deferred, named so they land right): ``observed``
(receipted / gateway-observed — cheap, continuous) and ``reconciled`` (receipted
/ state-diff-derived — the number an underwriter buys). This module computes the
reconciled kind against a concrete anchor; it never reports a gateway-only ratio
as if it were the whole story.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _normalize_version(tag_or_version: str) -> str:
    """``v0.40.0`` -> ``0.40.0`` (strip a leading ``v``). The join key between a
    git tag and a receipt's ``version``."""
    t = tag_or_version.strip()
    return t[1:] if t[:1] == "v" and t[1:2].isdigit() else t


def git_release_tags(match: str = "v[0-9]*", *, repo: str = ".") -> list[str]:
    """Anchored actions from the git anchor: tags matching ``match`` (default
    version tags). This is a record bulla did not mint — the honest denominator."""
    out = subprocess.run(
        ["git", "-C", repo, "tag", "--list", match],
        capture_output=True, text=True, check=False,
    )
    return sorted({t.strip() for t in out.stdout.splitlines() if t.strip()})


def receipted_release_versions(receipts_dir: str | Path) -> dict[str, str]:
    """``version -> receipt path`` for every ``package.release`` ActionReceipt in
    a directory. Keyed by normalized version (so ``v0.40.0`` tag joins the
    ``0.40.0`` receipt)."""
    d = Path(receipts_dir)
    out: dict[str, str] = {}
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if doc.get("kind") != "action_receipt":
            continue
        action = doc.get("action") or {}
        if action.get("type") != "package.release":
            continue
        subj = action.get("subject") or {}
        ver = subj.get("version") or _normalize_version(subj.get("git_tag") or "")
        if ver:
            out[_normalize_version(ver)] = str(p)
    return out


def coverage_report(anchor: str, anchored: list[str], receipted: dict[str, str]) -> dict:
    """Pure core: coverage of ``anchored`` (a list of action ids) by ``receipted``
    (id -> evidence). Returns the ratio and the unreceipted delta — the actions
    the anchor records but no receipt covers. This is the audit product."""
    keys = {_normalize_version(a): a for a in anchored}
    covered = sorted(orig for k, orig in keys.items() if k in receipted)
    missing = sorted(orig for k, orig in keys.items() if k not in receipted)
    total = len(keys)
    ratio = (len(covered) / total) if total else 1.0
    return {
        "anchor": anchor,
        "total_anchored": total,
        "receipted": len(covered),
        "coverage": round(ratio, 4),
        "unreceipted_delta": missing,
        "covered": covered,
    }


def coverage_headline(reports: list[dict]) -> str:
    """Min over declared anchors — the quotable, honest headline. Names the
    weakest anchor so the number is never read as "we saw everything"."""
    if not reports:
        return "Coverage: n/a (no anchors declared)"
    weakest = min(reports, key=lambda r: r["coverage"])
    pct = round(weakest["coverage"] * 100)
    return f"Coverage: {pct}% (weakest anchor: {weakest['anchor']})"


def git_coverage(
    receipts_dir: str | Path, *, match: str = "v[0-9]*", repo: str = "."
) -> dict:
    """Convenience: coverage of the git-tag anchor by a receipts directory."""
    anchored = git_release_tags(match, repo=repo)
    receipted = receipted_release_versions(receipts_dir)
    return coverage_report("git", anchored, receipted)
