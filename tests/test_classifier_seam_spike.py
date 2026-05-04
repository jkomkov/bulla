"""Classifier seam spike: URL/path operational seam evidence and gates."""

from __future__ import annotations

import json
import re
from pathlib import Path

from bulla.diagnostic import decompose_fee
from bulla.guard import BullaGuard
from bulla.infer import classifier as classifier_mod
from bulla.infer.classifier import classify_field_by_name


MANIFESTS_DIR = (
    Path(__file__).parent.parent / "examples" / "real_world_audit" / "manifests"
)


def _server_of(tool_name: str) -> str:
    return tool_name.split("__", 1)[0]


def _boundary_groups(diag) -> set[tuple[str, str, str]]:
    groups: set[tuple[str, str, str]] = set()
    for bs in diag.blind_spots:
        a = _server_of(bs.from_tool)
        b = _server_of(bs.to_tool)
        if a == b:
            continue
        lo, hi = sorted((a, b))
        groups.add((bs.dimension, lo, hi))
    return groups


def _load_prefixed_tools(server_names: list[str]) -> list[dict]:
    tools: list[dict] = []
    for server in server_names:
        data = json.loads((MANIFESTS_DIR / f"{server}.json").read_text())
        for t in data.get("tools", []):
            tt = dict(t)
            tt["name"] = f"{server}__{t['name']}"
            tools.append(tt)
    return tools


def _partition_for_servers(comp, server_names: list[str]) -> list[frozenset[str]]:
    parts: list[frozenset[str]] = []
    for server in server_names:
        names = frozenset(
            t.name for t in comp.tools if t.name.startswith(f"{server}__")
        )
        if names:
            parts.append(names)
    return parts


def _set_path_regex(*, include_url_uri: bool) -> None:
    token = (
        r"(^)(path|filepath|file_path|dir_path|directory|dirname|folder|url|uri)($|_)"
        if include_url_uri
        else r"(^)(path|filepath|file_path|dir_path|directory|dirname|folder)($|_)"
    )
    patched = []
    for dim, pat in classifier_mod._CORE_NAME_PATTERNS:
        if dim == "path_convention":
            patched.append((dim, re.compile(token, re.IGNORECASE)))
        else:
            patched.append((dim, pat))
    classifier_mod._CORE_NAME_PATTERNS = patched
    classifier_mod._reset_taxonomy_cache()


def _analyze(server_names: list[str], *, include_url_uri: bool) -> tuple:
    original = list(classifier_mod._CORE_NAME_PATTERNS)
    _set_path_regex(include_url_uri=include_url_uri)
    try:
        guard = BullaGuard.from_tools_list(
            _load_prefixed_tools(server_names),
            name="classifier-seam-spike",
        )
        diag = guard.diagnose()
        decomp = decompose_fee(
            guard.composition,
            _partition_for_servers(guard.composition, server_names),
        )
        return diag, decomp
    finally:
        classifier_mod._CORE_NAME_PATTERNS = original
        classifier_mod._reset_taxonomy_cache()


class TestClassifierSeamSpike:
    def test_baseline_four_server_snapshot(self):
        """Baseline evidence: current classifier should show puppeteer seam edges."""
        servers = ["filesystem", "github", "memory", "puppeteer"]
        diag, decomp = _analyze(servers, include_url_uri=True)
        groups = _boundary_groups(diag)

        assert decomp.boundary_fee == 2
        assert groups == {
            ("path_convention_match", "filesystem", "github"),
            ("path_convention_match", "filesystem", "puppeteer"),
            ("path_convention_match", "github", "puppeteer"),
        }

        # Memory remains out of path seam detection in this sprint.
        assert not any("memory" in (a, b) for _, a, b in groups)

    def test_operational_seam_schema_evidence_url_vs_path(self):
        """Operational seam test: schema-level confusion is plausible, not impossible."""
        fs = json.loads((MANIFESTS_DIR / "filesystem.json").read_text())
        gh = json.loads((MANIFESTS_DIR / "github.json").read_text())
        pp = json.loads((MANIFESTS_DIR / "puppeteer.json").read_text())

        def tool(tools: list[dict], name: str) -> dict:
            return next(t for t in tools if t["name"] == name)

        fs_read = tool(fs["tools"], "read_file")
        gh_write = tool(gh["tools"], "create_or_update_file")
        pp_nav = tool(pp["tools"], "puppeteer_navigate")

        fs_path = fs_read["inputSchema"]["properties"]["path"]
        gh_path = gh_write["inputSchema"]["properties"]["path"]
        pp_url = pp_nav["inputSchema"]["properties"]["url"]

        # All three are unconstrained strings at schema level.
        assert fs_path.get("type") == "string"
        assert gh_path.get("type") == "string"
        assert pp_url.get("type") == "string"
        assert "format" not in fs_path and "pattern" not in fs_path
        assert "format" not in gh_path and "pattern" not in gh_path
        assert "format" not in pp_url and "pattern" not in pp_url

        # Descriptions distinguish URL vs path semantics but do not add machine constraints.
        assert "url" in pp_nav["description"].lower()
        assert "url" in pp_url.get("description", "").lower()
        assert "path" in gh_path.get("description", "").lower()

    def test_prototype_gate_new_seam_and_limited_unrelated_growth(self):
        """Compare baseline (without url/uri token) vs current path regex."""
        servers = [p.stem for p in sorted(MANIFESTS_DIR.glob("*.json"))]
        old_diag, _ = _analyze(servers, include_url_uri=False)
        new_diag, _ = _analyze(servers, include_url_uri=True)

        old_groups = _boundary_groups(old_diag)
        new_groups = _boundary_groups(new_diag)
        added = new_groups - old_groups

        # At least one new explainable puppeteer seam appears.
        assert any(
            dim == "path_convention_match"
            and a in {"filesystem", "github"}
            and b == "puppeteer"
            for dim, a, b in added
        )

        # Unrelated server-pair growth should stay tightly bounded.
        unrelated = {
            g for g in added if g[1] != "puppeteer" and g[2] != "puppeteer"
        }
        assert len(unrelated) <= 2

    def test_canonical_filesystem_github_groups_unchanged(self):
        """Canonical two-server receipt should stay stable for this seam update."""
        servers = ["filesystem", "github"]
        old_diag, old_decomp = _analyze(servers, include_url_uri=False)
        new_diag, new_decomp = _analyze(servers, include_url_uri=True)

        assert old_decomp.boundary_fee == new_decomp.boundary_fee
        assert _boundary_groups(old_diag) == _boundary_groups(new_diag)

    def test_url_field_classifies_under_path_convention(self):
        hit = classify_field_by_name("url")
        assert hit is not None
        assert hit.dimension == "path_convention"
