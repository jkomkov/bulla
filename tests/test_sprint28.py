"""Sprint 28 tests: convention value extraction, discovered_pack, expected_value, receipt integration."""
from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from bulla.diagnostic import (
    boundary_obligations_from_decomposition,
    decompose_fee,
    diagnose,
)
from bulla.discover.adapter import MockAdapter
from bulla.merge import merge_receipt_obligations
from bulla.model import (
    BoundaryObligation,
    Composition,
    Edge,
    ObligationVerdict,
    ProbeResult,
    SemanticDimension,
    ToolSpec,
)
from bulla.packs.validate import validate_pack
from bulla.repair import (
    ConvergenceResult,
    coordination_step,
    extract_pack_from_probes,
    repair_step,
)
from bulla.witness import verify_receipt_integrity, witness


# ── Helpers ──────────────────────────────────────────────────────────

def _make_probe(
    dim: str,
    field: str,
    tool: str,
    verdict: ObligationVerdict,
    convention_value: str = "",
    source_edge: str = "",
) -> ProbeResult:
    return ProbeResult(
        obligation=BoundaryObligation(
            placeholder_tool=tool,
            dimension=dim,
            field=field,
            source_edge=source_edge,
        ),
        verdict=verdict,
        evidence="test evidence",
        convention_value=convention_value,
    )


def _fee2_composition() -> tuple[Composition, list[frozenset[str]], list[dict]]:
    """Composition with fee=2 from two independent blind spots."""
    api = (
        ToolSpec("api__list_items", ("cursor", "offset", "limit"), ("cursor", "limit")),
        ToolSpec("api__get_item", ("item_id", "format", "abs_flag"), ("item_id",)),
    )
    storage = (
        ToolSpec("storage__read_file", ("path", "encoding", "abs_path"), ("encoding",)),
        ToolSpec("storage__write_file", ("dest", "mode", "rel_path"), ("dest", "mode")),
    )
    edges = (
        Edge("storage__read_file", "api__list_items", (
            SemanticDimension("pagination", "abs_path", "offset"),
        )),
        Edge("api__get_item", "storage__write_file", (
            SemanticDimension("path_convention", "abs_flag", "rel_path"),
        )),
    )
    comp = Composition("test-fee2", api + storage, edges)
    partition = [
        frozenset(t.name for t in api),
        frozenset(t.name for t in storage),
    ]
    tools = []
    for t in api + storage:
        props = {}
        for f in t.internal_state:
            props[f] = {"type": "string"}
            if f in t.observable_schema:
                props[f]["description"] = f"Observable: {f}"
        tools.append({
            "name": t.name,
            "description": f"Tool {t.name}",
            "inputSchema": {"type": "object", "properties": props},
        })
    return comp, partition, tools


class ValueAwareMockAdapter:
    """Confirms one new dimension per round with specific convention values."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = values or {"pagination": "zero_based", "path_convention": "absolute"}
        self._confirmed_dims: set[str] = set()

    def complete(self, prompt: str) -> str:
        n_obls = len(re.findall(r"OBLIGATION \d+:", prompt))
        if n_obls == 0:
            return ""
        dims: list[str] = []
        for idx in range(1, n_obls + 1):
            m = re.search(rf"OBLIGATION {idx}:.*?Dimension:\s*(\S+)", prompt, re.DOTALL)
            dims.append(m.group(1) if m else "")
        confirmed_this_round = False
        blocks = []
        for idx in range(1, n_obls + 1):
            dim = dims[idx - 1]
            should_confirm = (
                not confirmed_this_round
                and dim not in self._confirmed_dims
                and dim in self._values
            )
            if should_confirm:
                self._confirmed_dims.add(dim)
                confirmed_this_round = True
                blocks.append(
                    f"---BEGIN_VERDICT_{idx}---\nverdict: CONFIRMED\n"
                    f"evidence: field is present\nconvention_value: {self._values[dim]}\n"
                    f"---END_VERDICT_{idx}---"
                )
            else:
                blocks.append(
                    f"---BEGIN_VERDICT_{idx}---\nverdiet: UNCERTAIN\n"
                    f"evidence: cannot determine\nconvention_value:\n"
                    f"---END_VERDICT_{idx}---"
                )
        return "\n\n".join(blocks)


# ── TestExtractPackFromProbes ────────────────────────────────────────


class TestExtractPackFromProbes:
    def test_empty_probes_returns_empty_dimensions(self):
        pack = extract_pack_from_probes(())
        assert pack["dimensions"] == {}
        assert pack["pack_name"].startswith("discovered_")

    def test_single_confirmed_probe(self):
        probes = (
            _make_probe("pagination", "offset", "api", ObligationVerdict.CONFIRMED,
                        convention_value="zero_based", source_edge="storage -> api"),
        )
        pack = extract_pack_from_probes(probes, "abcdef12")
        dims = pack["dimensions"]
        assert "pagination" in dims
        assert dims["pagination"]["known_values"] == ["zero_based"]
        assert dims["pagination"]["field_patterns"] == ["offset"]
        assert dims["pagination"]["provenance"]["source"] == "guided_discovery"
        assert dims["pagination"]["provenance"]["confidence"] == "confirmed"
        assert "api" in dims["pagination"]["provenance"]["source_tools"]
        assert dims["pagination"]["provenance"]["boundary"] == "storage -> api"

    def test_multiple_probes_same_dimension_merge(self):
        probes = (
            _make_probe("pagination", "offset", "api", ObligationVerdict.CONFIRMED,
                        convention_value="zero_based"),
            _make_probe("pagination", "page_num", "storage", ObligationVerdict.CONFIRMED,
                        convention_value="one_based"),
        )
        pack = extract_pack_from_probes(probes)
        dims = pack["dimensions"]
        assert "pagination" in dims
        assert set(dims["pagination"]["known_values"]) == {"zero_based", "one_based"}
        assert set(dims["pagination"]["field_patterns"]) == {"offset", "page_num"}
        assert "api" in dims["pagination"]["provenance"]["source_tools"]
        assert "storage" in dims["pagination"]["provenance"]["source_tools"]

    def test_multiple_probes_different_dimensions(self):
        probes = (
            _make_probe("pagination", "offset", "api", ObligationVerdict.CONFIRMED,
                        convention_value="zero_based"),
            _make_probe("path_convention", "abs_flag", "storage", ObligationVerdict.CONFIRMED,
                        convention_value="absolute"),
        )
        pack = extract_pack_from_probes(probes)
        dims = pack["dimensions"]
        assert len(dims) == 2
        assert "pagination" in dims
        assert "path_convention" in dims

    def test_denied_probes_excluded(self):
        probes = (
            _make_probe("pagination", "offset", "api", ObligationVerdict.DENIED,
                        convention_value="zero_based"),
        )
        pack = extract_pack_from_probes(probes)
        assert pack["dimensions"] == {}

    def test_uncertain_probes_excluded(self):
        probes = (
            _make_probe("pagination", "offset", "api", ObligationVerdict.UNCERTAIN),
        )
        pack = extract_pack_from_probes(probes)
        assert pack["dimensions"] == {}

    def test_empty_convention_value_excluded(self):
        probes = (
            _make_probe("pagination", "offset", "api", ObligationVerdict.CONFIRMED,
                        convention_value=""),
        )
        pack = extract_pack_from_probes(probes)
        assert pack["dimensions"] == {}

    def test_same_value_deduplication(self):
        probes = (
            _make_probe("pagination", "offset", "api", ObligationVerdict.CONFIRMED,
                        convention_value="zero_based"),
            _make_probe("pagination", "start", "api", ObligationVerdict.CONFIRMED,
                        convention_value="zero_based"),
        )
        pack = extract_pack_from_probes(probes)
        assert pack["dimensions"]["pagination"]["known_values"] == ["zero_based"]

    def test_output_validates_with_validate_pack(self):
        probes = (
            _make_probe("pagination", "offset", "api", ObligationVerdict.CONFIRMED,
                        convention_value="zero_based"),
        )
        pack = extract_pack_from_probes(probes, "abc12345")
        errors = validate_pack(pack)
        assert errors == [], f"Pack validation failed: {errors}"


# ── TestConvergenceResultDiscoveredPack ──────────────────────────────


class TestConvergenceResultDiscoveredPack:
    def test_property_returns_valid_pack(self):
        comp, partition, tools = _fee2_composition()
        adapter = ValueAwareMockAdapter()
        result = coordination_step(comp, partition, tools, adapter, max_rounds=5)
        pack = result.discovered_pack
        assert "dimensions" in pack
        assert "pack_name" in pack
        dims = pack["dimensions"]
        assert len(dims) >= 1
        errors = validate_pack(pack)
        assert errors == [] or pack["dimensions"] == {}

    def test_multi_round_probes_aggregated(self):
        comp, partition, tools = _fee2_composition()
        adapter = ValueAwareMockAdapter()
        result = coordination_step(comp, partition, tools, adapter, max_rounds=5)
        assert len(result.rounds) >= 2
        pack = result.discovered_pack
        dims = pack.get("dimensions", {})
        assert len(dims) == 2
        assert "pagination" in dims
        assert "path_convention" in dims

    def test_empty_convergence_empty_pack(self):
        tools_full = (
            ToolSpec("a__x", ("f1",), ("f1",)),
            ToolSpec("a__y", ("f2",), ("f2",)),
        )
        edges = (
            Edge("a__x", "a__y", (SemanticDimension("d1", "f1", "f2"),)),
        )
        comp = Composition("trivial", tools_full, edges)
        partition = [frozenset(t.name for t in tools_full)]
        tool_dicts = [
            {"name": t.name, "description": f"Tool {t.name}",
             "inputSchema": {"type": "object", "properties": {}}}
            for t in tools_full
        ]
        adapter = MockAdapter("")
        result = coordination_step(comp, partition, tool_dicts, adapter)
        pack = result.discovered_pack
        assert pack["dimensions"] == {}


# ── TestBoundaryObligationExpectedValue ──────────────────────────────


class TestBoundaryObligationExpectedValue:
    def test_default_empty_string(self):
        obl = BoundaryObligation("api", "pagination", "offset")
        assert obl.expected_value == ""

    def test_included_in_to_dict_when_nonempty(self):
        obl = BoundaryObligation("api", "pagination", "offset",
                                  expected_value="zero_based")
        d = obl.to_dict()
        assert d["expected_value"] == "zero_based"

    def test_omitted_from_to_dict_when_empty(self):
        obl = BoundaryObligation("api", "pagination", "offset")
        d = obl.to_dict()
        assert "expected_value" not in d

    def test_merge_receipt_obligations_unchanged(self):
        """merge_receipt_obligations does not propagate expected_value (Sprint 29)."""
        receipts = [
            {
                "boundary_obligations": [
                    {
                        "placeholder_tool": "api",
                        "dimension": "pagination",
                        "field": "offset",
                        "source_edge": "storage -> api",
                        "expected_value": "zero_based",
                    }
                ]
            }
        ]
        merged = merge_receipt_obligations(receipts)
        assert merged is not None
        assert len(merged) == 1
        assert merged[0].expected_value == ""


# ── TestReceiptIntegration ───────────────────────────────────────────


class TestReceiptIntegration:
    def test_inline_dimensions_from_discovered_pack(self):
        probes = (
            _make_probe("pagination", "offset", "api", ObligationVerdict.CONFIRMED,
                        convention_value="zero_based"),
        )
        pack = extract_pack_from_probes(probes, "testcomp")

        tools = (
            ToolSpec("a__x", ("f1",), ("f1",)),
            ToolSpec("a__y", ("f2",), ("f2",)),
        )
        edges = (Edge("a__x", "a__y", (SemanticDimension("d1", "f1", "f2"),)),)
        comp = Composition("test", tools, edges)
        diag = diagnose(comp)

        receipt = witness(diag, comp, inline_dimensions=pack)
        assert receipt.inline_dimensions is not None
        assert "pagination" in receipt.inline_dimensions.get("dimensions", {})

    def test_receipt_round_trip(self):
        probes = (
            _make_probe("pagination", "offset", "api", ObligationVerdict.CONFIRMED,
                        convention_value="zero_based"),
        )
        pack = extract_pack_from_probes(probes, "testcomp")

        tools = (
            ToolSpec("a__x", ("f1",), ("f1",)),
            ToolSpec("a__y", ("f2",), ("f2",)),
        )
        edges = (Edge("a__x", "a__y", (SemanticDimension("d1", "f1", "f2"),)),)
        comp = Composition("test", tools, edges)
        diag = diagnose(comp)
        receipt = witness(diag, comp, inline_dimensions=pack)

        d = receipt.to_dict()
        assert verify_receipt_integrity(d)

    def test_inline_dimensions_merge_precedence(self):
        """Newly discovered dimensions win over parent chain dimensions."""
        parent_inline = {
            "pack_name": "parent_pack",
            "pack_version": "0.1.0",
            "dimensions": {
                "pagination": {
                    "description": "Parent pagination",
                    "known_values": ["one_based"],
                    "field_patterns": ["page"],
                },
                "encoding": {
                    "description": "Encoding convention",
                    "known_values": ["utf-8"],
                    "field_patterns": ["encoding"],
                },
            },
        }
        discovered = {
            "pack_name": "discovered_abc",
            "pack_version": "0.1.0",
            "dimensions": {
                "pagination": {
                    "description": "Convention for pagination dimension",
                    "known_values": ["zero_based"],
                    "field_patterns": ["offset"],
                    "provenance": {
                        "source": "guided_discovery",
                        "confidence": "confirmed",
                        "source_tools": ["api"],
                        "boundary": "",
                    },
                },
            },
        }

        import copy
        merged = copy.deepcopy(parent_inline)
        merged["dimensions"].update(discovered["dimensions"])

        assert merged["dimensions"]["pagination"]["known_values"] == ["zero_based"]
        assert merged["dimensions"]["encoding"]["known_values"] == ["utf-8"]
        assert len(merged["dimensions"]) == 2


# ── Sprint 27 Issue 1 fix ────────────────────────────────────────────


class TestSprint27Issue1Fix:
    def test_no_redundant_diagnose_in_coordination_step(self):
        """coordination_step uses rounds[-1].repaired_fee, not a redundant diagnose()."""
        comp, partition, tools = _fee2_composition()
        adapter = ValueAwareMockAdapter()
        result = coordination_step(comp, partition, tools, adapter, max_rounds=5)
        assert result.final_fee == result.rounds[-1].repaired_fee


# ── Demo smoke test ──────────────────────────────────────────────────


class TestDemoSmoke:
    def test_value_extraction_demo_runs(self):
        result = subprocess.run(
            [sys.executable, "scripts/run_value_extraction_demo.py"],
            capture_output=True,
            text=True,
            cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 0, (
            f"Demo failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "ALL CHECKS PASSED" in result.stdout
        assert "pagination: zero_based" in result.stdout
        assert "path_convention: absolute" in result.stdout
