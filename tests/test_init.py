"""Tests for the bulla init wizard."""

import io
import tempfile
from pathlib import Path

import yaml

from bulla.init import run_init


class TestInitWizard:
    def test_basic_flow(self):
        """Two tools with shared internal fields."""
        input_text = (
            "test-pipeline\n"
            "parser\n"
            "total_amount, due_date, items\n"
            "amount_unit\n"
            "processor\n"
            "amount, process_date, output\n"
            "amount_unit\n"
            "\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            out_path = Path(f.name)

        try:
            run_init(
                output=out_path,
                input_stream=io.StringIO(input_text),
            )
            content = out_path.read_text()
            data = yaml.safe_load(content)
            assert data["name"] == "test-pipeline"
            assert "parser" in data["tools"]
            assert "processor" in data["tools"]
        finally:
            out_path.unlink(missing_ok=True)

    def test_requires_two_tools(self, capsys):
        """Abort if fewer than 2 tools entered."""
        input_text = "one-tool\nsingle\nfield_a\n\n\n"
        run_init(
            output=None,
            input_stream=io.StringIO(input_text),
        )
        captured = capsys.readouterr()
        assert "at least 2 tools" in captured.out.lower()

    def test_default_name(self):
        """Default composition name is 'my-pipeline'."""
        input_text = (
            "\n"
            "A\n"
            "x, y\n"
            "\n"
            "B\n"
            "x, z\n"
            "\n"
            "\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            out_path = Path(f.name)
        try:
            run_init(output=out_path, input_stream=io.StringIO(input_text))
            data = yaml.safe_load(out_path.read_text())
            assert data["name"] == "my-pipeline"
        finally:
            out_path.unlink(missing_ok=True)
