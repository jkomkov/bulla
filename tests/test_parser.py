"""Tests for YAML parser and schema validation."""

import os
import tempfile
from pathlib import Path

import pytest

from bulla.parser import CompositionError, load_composition


def _write_yaml(content: str) -> Path:
    fd, path = tempfile.mkstemp(suffix=".yaml")
    os.write(fd, content.encode())
    os.close(fd)
    return Path(path)


def _cleanup(path: Path):
    path.unlink(missing_ok=True)


VALID_MINIMAL = """\
name: test
tools:
  A:
    internal_state: [x]
    observable_schema: [x]
  B:
    internal_state: [x]
    observable_schema: [x]
edges:
  - from: A
    to: B
    dimensions:
      - name: d1
        from_field: x
        to_field: x
"""


class TestValidFiles:
    def test_minimal(self):
        p = _write_yaml(VALID_MINIMAL)
        try:
            comp = load_composition(p)
            assert comp.name == "test"
            assert len(comp.tools) == 2
            assert len(comp.edges) == 1
        finally:
            _cleanup(p)

    def test_name_defaults_to_stem(self):
        content = VALID_MINIMAL.replace("name: test\n", "")
        p = _write_yaml(content)
        try:
            comp = load_composition(p)
            assert comp.name == p.stem
        finally:
            _cleanup(p)

    def test_real_composition_files(self):
        compositions_dir = (
            Path(__file__).parent.parent / "compositions"
        )
        if not compositions_dir.exists():
            pytest.skip("compositions/ not found")
        for f in compositions_dir.glob("*.yaml"):
            comp = load_composition(f)
            assert len(comp.tools) > 0
            assert len(comp.edges) > 0


class TestInvalidFiles:
    def test_missing_tools(self):
        p = _write_yaml("edges:\n  - from: A\n    to: B\n    dimensions:\n      - name: d")
        try:
            with pytest.raises(CompositionError, match="Missing required field 'tools'"):
                load_composition(p)
        finally:
            _cleanup(p)

    def test_empty_tools(self):
        p = _write_yaml("tools: {}\nedges: []")
        try:
            with pytest.raises(CompositionError, match="must contain at least one tool"):
                load_composition(p)
        finally:
            _cleanup(p)

    def test_missing_internal_state(self):
        p = _write_yaml("tools:\n  A:\n    observable_schema: [x]\nedges:\n  - from: A\n    to: A\n    dimensions:\n      - name: d")
        try:
            with pytest.raises(CompositionError, match="Missing required field 'internal_state'"):
                load_composition(p)
        finally:
            _cleanup(p)

    def test_observable_not_subset(self):
        p = _write_yaml("tools:\n  A:\n    internal_state: [x]\n    observable_schema: [y]\nedges:\n  - from: A\n    to: A\n    dimensions:\n      - name: d")
        try:
            with pytest.raises(CompositionError, match="observable_schema field 'y' is not in internal_state"):
                load_composition(p)
        finally:
            _cleanup(p)

    def test_unknown_tool_in_edge(self):
        p = _write_yaml("tools:\n  A:\n    internal_state: [x]\n    observable_schema: [x]\nedges:\n  - from: A\n    to: Z\n    dimensions:\n      - name: d")
        try:
            with pytest.raises(CompositionError, match="references unknown tool 'Z'"):
                load_composition(p)
        finally:
            _cleanup(p)

    def test_missing_edges(self):
        p = _write_yaml("tools:\n  A:\n    internal_state: [x]\n    observable_schema: [x]")
        try:
            with pytest.raises(CompositionError, match="Missing required field 'edges'"):
                load_composition(p)
        finally:
            _cleanup(p)

    def test_invalid_yaml(self):
        p = _write_yaml("{ invalid yaml [")
        try:
            with pytest.raises(CompositionError, match="Invalid YAML"):
                load_composition(p)
        finally:
            _cleanup(p)

    def test_not_a_mapping(self):
        p = _write_yaml("- a list\n- not a mapping")
        try:
            with pytest.raises(CompositionError, match="expected a YAML mapping"):
                load_composition(p)
        finally:
            _cleanup(p)

    def test_empty_dimensions(self):
        p = _write_yaml("tools:\n  A:\n    internal_state: [x]\n    observable_schema: [x]\nedges:\n  - from: A\n    to: A\n    dimensions: []")
        try:
            with pytest.raises(CompositionError, match="must have at least one dimension"):
                load_composition(p)
        finally:
            _cleanup(p)
