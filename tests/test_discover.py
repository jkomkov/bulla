"""Tests for bulla discover: adapter, prompt, engine, CLI integration."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from bulla.discover.adapter import MockAdapter, DiscoverAdapter
from bulla.discover.prompt import build_prompt, parse_response
from bulla.discover.engine import discover_dimensions, DiscoveryResult
from bulla.infer.classifier import _reset_taxonomy_cache


MOCK_VALID_RESPONSE = """\
---BEGIN_PACK---
pack_name: "discovered_test123"
pack_version: "0.1.0"
dimensions:
  entity_namespace:
    description: "Whether numeric entity IDs share a global sequence or are scoped per type"
    known_values: ["global_sequence", "per_type_sequence", "uuid"]
    field_patterns: ["*_number", "*_id"]
    description_keywords: ["issue number", "pull request number", "entity id"]
    refines: "id_offset"
  content_encoding:
    description: "How binary or text content is encoded for transport"
    known_values: ["raw_utf8", "base64", "hex"]
    field_patterns: ["*_content", "*_data"]
    description_keywords: ["base64", "encoded content", "raw text"]
    refines: null
---END_PACK---"""

MOCK_EMPTY_RESPONSE = """\
---BEGIN_PACK---
pack_name: "discovered_empty"
pack_version: "0.1.0"
dimensions: {}
---END_PACK---"""

MOCK_GARBAGE_RESPONSE = "I couldn't find any convention ambiguities in these tools."

SAMPLE_TOOLS = [
    {
        "name": "github__list_issues",
        "description": "List issues in a repository",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "page": {"type": "integer", "description": "Page number (default: 1)"},
                "per_page": {"type": "integer", "description": "Results per page (max 100)"},
            },
        },
    },
    {
        "name": "github__get_pull_request",
        "description": "Get a specific pull request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
            },
        },
    },
    {
        "name": "filesystem__read_file",
        "description": "Read a file from the filesystem",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute file path"},
            },
        },
    },
]


# ── Adapter tests ─────────────────────────────────────────────────────


class TestMockAdapter:
    def test_returns_preset_response(self):
        adapter = MockAdapter("test response")
        assert adapter.complete("anything") == "test response"

    def test_captures_last_prompt(self):
        adapter = MockAdapter("ok")
        adapter.complete("my prompt")
        assert adapter.last_prompt == "my prompt"

    def test_satisfies_protocol(self):
        adapter = MockAdapter("ok")
        assert isinstance(adapter, DiscoverAdapter)


# ── Prompt tests ──────────────────────────────────────────────────────


class TestBuildPrompt:
    def test_contains_tool_names(self):
        prompt = build_prompt(SAMPLE_TOOLS, {})
        assert "github__list_issues" in prompt
        assert "filesystem__read_file" in prompt

    def test_contains_field_info(self):
        prompt = build_prompt(SAMPLE_TOOLS, {})
        assert "pull_number" in prompt
        assert "integer" in prompt

    def test_contains_existing_dimensions(self):
        dims = {"date_format": {"description": "How dates are represented"}}
        prompt = build_prompt(SAMPLE_TOOLS, dims)
        assert "date_format" in prompt
        assert "How dates are represented" in prompt

    def test_contains_output_format(self):
        prompt = build_prompt(SAMPLE_TOOLS, {})
        assert "---BEGIN_PACK---" in prompt
        assert "---END_PACK---" in prompt

    def test_session_id_in_prompt(self):
        prompt = build_prompt(SAMPLE_TOOLS, {}, session_id="abc123")
        assert "discovered_abc123" in prompt


class TestParseResponse:
    def test_parses_begin_end_delimiters(self):
        result = parse_response(MOCK_VALID_RESPONSE)
        assert result is not None
        assert "entity_namespace" in result

    def test_parses_markdown_fence(self):
        raw = "Here's the pack:\n```yaml\npack_name: test\n```\nDone."
        result = parse_response(raw)
        assert result is not None
        assert "pack_name" in result

    def test_returns_none_for_no_block(self):
        assert parse_response(MOCK_GARBAGE_RESPONSE) is None

    def test_prefers_delimiters_over_fences(self):
        raw = "```yaml\nwrong\n```\n---BEGIN_PACK---\ncorrect\n---END_PACK---"
        result = parse_response(raw)
        assert result == "correct"

    def test_empty_delimiters(self):
        raw = "---BEGIN_PACK---\n---END_PACK---"
        result = parse_response(raw)
        assert result == ""


# ── Engine tests ──────────────────────────────────────────────────────


class TestDiscoverEngine:
    def setup_method(self):
        _reset_taxonomy_cache()

    def test_valid_discovery(self):
        adapter = MockAdapter(MOCK_VALID_RESPONSE)
        result = discover_dimensions(SAMPLE_TOOLS, adapter=adapter)
        assert result.valid
        assert result.n_dimensions == 2
        assert "entity_namespace" in result.pack.get("dimensions", {})
        assert "content_encoding" in result.pack.get("dimensions", {})

    def test_raw_response_captured(self):
        adapter = MockAdapter(MOCK_VALID_RESPONSE)
        result = discover_dimensions(SAMPLE_TOOLS, adapter=adapter)
        assert result.raw_response == MOCK_VALID_RESPONSE

    def test_prompt_captured(self):
        adapter = MockAdapter(MOCK_VALID_RESPONSE)
        result = discover_dimensions(SAMPLE_TOOLS, adapter=adapter)
        assert "github__list_issues" in result.prompt

    def test_garbage_response_produces_errors(self):
        adapter = MockAdapter(MOCK_GARBAGE_RESPONSE)
        result = discover_dimensions(SAMPLE_TOOLS, adapter=adapter)
        assert not result.valid
        assert any("No valid YAML" in e for e in result.errors)

    def test_invalid_yaml_produces_errors(self):
        adapter = MockAdapter("---BEGIN_PACK---\n{invalid: [yaml\n---END_PACK---")
        result = discover_dimensions(SAMPLE_TOOLS, adapter=adapter)
        assert not result.valid
        assert any("YAML parse" in e for e in result.errors)

    def test_existing_dimensions_in_prompt(self):
        adapter = MockAdapter(MOCK_VALID_RESPONSE)
        result = discover_dimensions(SAMPLE_TOOLS, adapter=adapter)
        assert "date_format" in result.prompt
        assert "id_offset" in result.prompt

    def test_refines_field_preserved(self):
        adapter = MockAdapter(MOCK_VALID_RESPONSE)
        result = discover_dimensions(SAMPLE_TOOLS, adapter=adapter)
        dims = result.pack["dimensions"]
        assert dims["entity_namespace"]["refines"] == "id_offset"

    def test_empty_discovery_valid(self):
        adapter = MockAdapter(MOCK_EMPTY_RESPONSE)
        result = discover_dimensions(SAMPLE_TOOLS, adapter=adapter)
        assert result.n_dimensions == 0

    def teardown_method(self):
        _reset_taxonomy_cache()


# ── CLI integration tests ─────────────────────────────────────────────


class TestDiscoverCLI:
    """Test bulla discover CLI with MockAdapter via monkeypatching."""

    def _create_manifest_dir(self) -> Path:
        tmpdir = Path(tempfile.mkdtemp())
        tools_github = [
            {"name": "list_issues", "description": "List issues",
             "inputSchema": {"type": "object", "properties": {
                 "owner": {"type": "string"}, "page": {"type": "integer"}}}},
        ]
        tools_fs = [
            {"name": "read_file", "description": "Read a file",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}}}},
        ]
        (tmpdir / "github.json").write_text(json.dumps({"tools": tools_github}))
        (tmpdir / "filesystem.json").write_text(json.dumps({"tools": tools_fs}))
        return tmpdir

    def test_discover_cli_with_mock(self, monkeypatch):
        import subprocess
        import sys

        manifests_dir = self._create_manifest_dir()
        output_path = manifests_dir / "discovered.yaml"

        monkeypatch.setenv("_BULLA_DISCOVER_MOCK", MOCK_VALID_RESPONSE)

        result = subprocess.run(
            [sys.executable, "-c", f"""
import os, sys
os.environ["_BULLA_DISCOVER_MOCK"] = '''{MOCK_VALID_RESPONSE}'''

from bulla.discover.adapter import MockAdapter
from bulla.discover import engine as eng

_orig = eng.discover_dimensions
def _patched(tools, **kw):
    kw["adapter"] = MockAdapter(os.environ["_BULLA_DISCOVER_MOCK"])
    return _orig(tools, **kw)
eng.discover_dimensions = _patched

from bulla.cli import main
sys.argv = ["bulla", "discover", "--manifests", "{manifests_dir}", "-o", "{output_path}"]
main()
"""],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert output_path.exists()

        parsed = yaml.safe_load(output_path.read_text())
        assert "entity_namespace" in parsed.get("dimensions", {})

        raw_path = output_path.with_suffix(".raw.txt")
        assert raw_path.exists()

        import shutil
        shutil.rmtree(manifests_dir)

    def test_full_loop_discover_then_audit(self):
        """The architectural proof: discover -> write pack -> audit with pack."""
        from bulla.discover.engine import discover_dimensions
        from bulla.guard import BullaGuard

        adapter = MockAdapter(MOCK_VALID_RESPONSE)
        manifests_dir = self._create_manifest_dir()

        all_tools: list[dict] = []
        for f in sorted(manifests_dir.glob("*.json")):
            data = json.loads(f.read_text())
            tools_data = data.get("tools", data)
            server = f.stem
            for t in tools_data:
                t["name"] = f"{server}__{t.get('name', 'unknown')}"
            all_tools.extend(tools_data)

        result = discover_dimensions(all_tools, adapter=adapter)
        assert result.valid

        pack_path = manifests_dir / "discovered.yaml"
        pack_path.write_text(
            yaml.dump(result.pack, default_flow_style=False, sort_keys=False)
        )

        from bulla.infer.classifier import configure_packs
        configure_packs(extra_paths=[pack_path])

        guard = BullaGuard.from_tools_list(all_tools, name="discover-audit")
        diag = guard.diagnose()
        assert diag is not None

        import shutil
        shutil.rmtree(manifests_dir)
        _reset_taxonomy_cache()
