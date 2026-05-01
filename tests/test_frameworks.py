"""Tests for the framework adapter registry and built-in adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from bulla import frameworks
from bulla.frameworks import FrameworkError, ParseMode, ToolDef


FIXTURES = Path(__file__).parent / "fixtures" / "frameworks"


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------


def test_registry_loads_three_builtins():
    names = {fw.name for fw in frameworks.all_frameworks()}
    assert {"anthropic-messages", "langgraph", "crewai"} <= names


def test_get_unknown_lists_known():
    with pytest.raises(FrameworkError) as exc:
        frameworks.get("nonexistent")
    msg = str(exc.value)
    assert "nonexistent" in msg
    assert "langgraph" in msg


def test_supports_static_only_in_this_sprint():
    for fw in frameworks.all_frameworks():
        assert fw.supports(ParseMode.STATIC)
        assert not fw.supports(ParseMode.RUNTIME)


def test_runtime_mode_raises_not_implemented():
    fw = frameworks.get("anthropic-messages")
    with pytest.raises(NotImplementedError) as exc:
        fw.parse(FIXTURES / "anthropic_tools.json", mode=ParseMode.RUNTIME)
    assert "future sprint" in str(exc.value).lower()


# ------------------------------------------------------------------
# Anthropic Messages adapter
# ------------------------------------------------------------------


def test_anthropic_json_extracts_tools():
    fw = frameworks.get("anthropic-messages")
    tools = fw.parse(FIXTURES / "anthropic_tools.json")
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"get_weather", "send_email"}
    weather = next(t for t in tools if t.name == "get_weather")
    assert "city" in weather.description.lower() or weather.description == "Get current weather for a city."
    assert weather.input_schema.get("required") == ["city"]


def test_anthropic_python_literal_extraction():
    fw = frameworks.get("anthropic-messages")
    tools = fw.parse(FIXTURES / "anthropic_tools.py")
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"fetch_url", "list_files"}


def test_anthropic_unsupported_extension(tmp_path):
    fw = frameworks.get("anthropic-messages")
    bad = tmp_path / "tools.txt"
    bad.write_text("not json")
    with pytest.raises(FrameworkError):
        fw.parse(bad)


def test_anthropic_missing_file():
    fw = frameworks.get("anthropic-messages")
    with pytest.raises(FrameworkError):
        fw.parse(FIXTURES / "does-not-exist.json")


# ------------------------------------------------------------------
# LangGraph adapter
# ------------------------------------------------------------------


def test_langgraph_extracts_decorated_functions():
    fw = frameworks.get("langgraph")
    tools = fw.parse(FIXTURES / "langgraph_sample.py")
    names = {t.name for t in tools}
    # @tool search_web; @tool("custom_name") for divide; BaseTool calculator;
    # StructuredTool.from_function summarize
    assert "search_web" in names
    assert "custom_name" in names  # @tool("custom_name") overrides function name
    assert "calculator" in names
    assert "summarize" in names


def test_langgraph_function_signature_to_schema():
    fw = frameworks.get("langgraph")
    tools = fw.parse(FIXTURES / "langgraph_sample.py")
    search = next(t for t in tools if t.name == "search_web")
    assert search.input_schema["type"] == "object"
    assert "query" in search.input_schema["properties"]
    assert search.input_schema["properties"]["query"]["type"] == "string"
    # max_results has a default → not required
    assert search.input_schema.get("required") == ["query"]


def test_langgraph_basetool_records_pydantic_schema():
    fw = frameworks.get("langgraph")
    tools = fw.parse(FIXTURES / "langgraph_sample.py")
    calc = next(t for t in tools if t.name == "calculator")
    assert calc.description.startswith("Performs")
    assert calc.input_schema.get("x_pydantic") == "CalculatorArgs"


def test_langgraph_directory_scan(tmp_path):
    """Pointing at a directory scans all .py files within."""
    (tmp_path / "a.py").write_text(
        "def tool(f):\n    return f\n@tool\ndef one(x: str) -> str:\n    '''one.'''\n    return x\n"
    )
    (tmp_path / "b.py").write_text(
        "def tool(f):\n    return f\n@tool\ndef two(y: int) -> int:\n    '''two.'''\n    return y\n"
    )
    fw = frameworks.get("langgraph")
    tools = fw.parse(tmp_path)
    names = {t.name for t in tools}
    assert names == {"one", "two"}


# ------------------------------------------------------------------
# CrewAI adapter
# ------------------------------------------------------------------


def test_crewai_extracts_decorated_and_classes():
    fw = frameworks.get("crewai")
    tools = fw.parse(FIXTURES / "crewai_sample.py")
    names = {t.name for t in tools}
    assert "Web Search Tool" in names  # @tool("Web Search Tool") override
    assert "calculate" in names
    assert "file_reader" in names  # BaseTool subclass with explicit name


def test_crewai_basetool_pydantic_schema_recorded():
    fw = frameworks.get("crewai")
    tools = fw.parse(FIXTURES / "crewai_sample.py")
    fr = next(t for t in tools if t.name == "file_reader")
    assert fr.input_schema.get("x_pydantic") == "FileReaderInput"


# ------------------------------------------------------------------
# Manifest emission
# ------------------------------------------------------------------


def test_tools_to_manifests_emits_v01():
    tools = [ToolDef(name="x", description="hi", input_schema={"type": "object"})]
    out = frameworks.tools_to_manifests(tools)
    assert len(out) == 1
    assert out[0]["bulla_manifest"] == "0.1"
    assert out[0]["tool"]["name"] == "x"
    assert out[0]["tool"]["description"] == "hi"


def test_tools_to_raw_dicts_for_manifest_pipeline():
    tools = [ToolDef(name="x", description="hi", input_schema={"type": "object", "properties": {"a": {"type": "string"}}})]
    out = frameworks.tools_to_raw_dicts(tools)
    assert out == [{
        "name": "x",
        "description": "hi",
        "inputSchema": {"type": "object", "properties": {"a": {"type": "string"}}},
    }]


def test_tools_to_raw_dicts_default_schema():
    tools = [ToolDef(name="x")]
    out = frameworks.tools_to_raw_dicts(tools)
    assert out[0]["inputSchema"] == {"type": "object", "properties": {}}
