"""Sprint 29 tests: mismatch display, real manifest audit, canonical demo, receipt integrity."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bulla import (
    BullaGuard,
    boundary_obligations_from_decomposition,
    decompose_fee,
    diagnose,
    verify_receipt_integrity,
)


MANIFESTS_DIR = Path(__file__).parent.parent / "examples" / "canonical-demo" / "manifests"
RECEIPT_PATH = Path(__file__).parent.parent / "examples" / "canonical-demo" / "receipts" / "audit_receipt.json"


def _load_2server_manifests() -> tuple[list[dict], list[str]]:
    """Load filesystem + github manifests, prefix tools by server name."""
    server_names: list[str] = []
    all_tools: list[dict] = []
    for manifest_file in sorted(MANIFESTS_DIR.glob("*.json")):
        with open(manifest_file) as f:
            data = json.load(f)
        tools_data = data.get("tools", data) if isinstance(data, dict) else data
        if not isinstance(tools_data, list):
            continue
        server = manifest_file.stem
        server_names.append(server)
        for t in tools_data:
            t["name"] = f"{server}__{t.get('name', 'unknown')}"
        all_tools.extend(tools_data)
    return all_tools, server_names


# ── Convention mismatch display ──────────────────────────────────────


class TestMismatchDisplay:
    """Phase 1: convention mismatch formatting in _audit_text and _audit_json."""

    def _make_guided_repair_report(self) -> dict:
        return {
            "original_fee": 30,
            "repaired_fee": 29,
            "confirmed": 2,
            "denied": 0,
            "uncertain": 0,
            "rounds": 1,
            "termination_reason": "fixpoint",
            "converged": True,
            "probes": [
                {
                    "obligation": {
                        "placeholder_tool": "filesystem",
                        "dimension": "path_convention",
                        "field": "path",
                        "source_edge": "filesystem__read_file -> github__create_or_update_file",
                    },
                    "verdict": "CONFIRMED",
                    "evidence": "uses absolute paths",
                    "convention_value": "absolute_local",
                },
                {
                    "obligation": {
                        "placeholder_tool": "github",
                        "dimension": "path_convention",
                        "field": "path",
                        "source_edge": "filesystem__read_file -> github__create_or_update_file",
                    },
                    "verdict": "CONFIRMED",
                    "evidence": "uses relative paths",
                    "convention_value": "relative_repo",
                },
            ],
            "discovered_pack": {
                "pack_name": "discovered_test",
                "pack_version": "0.1.0",
                "dimensions": {
                    "path_convention": {
                        "description": "Convention for path_convention dimension",
                        "known_values": ["absolute_local", "relative_repo"],
                        "field_patterns": ["path"],
                        "provenance": {
                            "source": "guided_discovery",
                            "confidence": "confirmed",
                            "source_tools": ["filesystem", "github"],
                            "boundary": "filesystem__read_file -> github__create_or_update_file",
                        },
                    },
                },
            },
        }

    def _make_diag(self, name: str, n_tools: int, n_edges: int, fee: int) -> "Diagnostic":
        from bulla.model import Diagnostic
        return Diagnostic(
            name=name, n_tools=n_tools, n_edges=n_edges, betti_1=0,
            dim_c0_obs=0, dim_c0_full=0, dim_c1=0,
            rank_obs=0, rank_full=0, h1_obs=0, h1_full=0,
            coherence_fee=fee, blind_spots=(), bridges=(),
            h1_after_bridge=0, n_unbridged=0,
        )

    def test_mismatch_in_text_output(self):
        from bulla.cli import _audit_text
        from types import SimpleNamespace

        results = [
            SimpleNamespace(name="filesystem", ok=True, tools=[None] * 14, error=None),
            SimpleNamespace(name="github", ok=True, tools=[None] * 26, error=None),
        ]
        diag = self._make_diag("test", 40, 244, 29)
        report = self._make_guided_repair_report()
        text = _audit_text(results, diag, [], None, None, guided_repair=report)
        assert "MISMATCH" in text
        assert "filesystem" in text
        assert "absolute_local" in text
        assert "relative_repo" in text
        assert "convention mismatch" in text

    def test_mismatch_count_in_json_output(self):
        from bulla.cli import _audit_json
        from types import SimpleNamespace

        results = [
            SimpleNamespace(name="filesystem", ok=True, tools=[None] * 14, error=None),
            SimpleNamespace(name="github", ok=True, tools=[None] * 26, error=None),
        ]
        diag = self._make_diag("test", 40, 244, 29)
        report = self._make_guided_repair_report()
        json_str = _audit_json(results, diag, [], None, None, guided_repair=report)
        obj = json.loads(json_str)
        assert obj["guided_repair"]["mismatches"] == 1

    def test_single_value_no_mismatch(self):
        from bulla.cli import _audit_text
        from types import SimpleNamespace

        results = [
            SimpleNamespace(name="server_a", ok=True, tools=[None] * 5, error=None),
        ]
        diag = self._make_diag("test", 5, 3, 1)
        report = {
            "original_fee": 2,
            "repaired_fee": 1,
            "confirmed": 1,
            "denied": 0,
            "uncertain": 0,
            "probes": [],
            "discovered_pack": {
                "pack_name": "discovered_test",
                "pack_version": "0.1.0",
                "dimensions": {
                    "encoding": {
                        "description": "Encoding convention",
                        "known_values": ["utf8"],
                        "field_patterns": ["encoding"],
                        "provenance": {
                            "source": "guided_discovery",
                            "confidence": "confirmed",
                            "source_tools": ["server_a"],
                            "boundary": "",
                        },
                    },
                },
            },
        }
        text = _audit_text(results, diag, [], None, None, guided_repair=report)
        assert "MISMATCH" not in text
        assert "encoding: utf8" in text


# ── Real manifest audit ──────────────────────────────────────────────


class TestRealManifestAudit:
    """Phase 0 step zero numbers verified as test assertions."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.all_tools, self.server_names = _load_2server_manifests()
        assert len(self.server_names) == 2
        assert "filesystem" in self.server_names
        assert "github" in self.server_names

    def test_server_tool_counts(self):
        fs_count = sum(1 for t in self.all_tools if t["name"].startswith("filesystem__"))
        gh_count = sum(1 for t in self.all_tools if t["name"].startswith("github__"))
        assert fs_count == 14
        assert gh_count == 26

    def test_coherence_fee(self):
        guard = BullaGuard.from_tools_list(self.all_tools, name="test-audit")
        diag = diagnose(guard.composition)
        assert diag.coherence_fee == 30

    def test_boundary_fee(self):
        guard = BullaGuard.from_tools_list(self.all_tools, name="test-audit")
        comp = guard.composition
        tool_to_server = {t.name: t.name.split("__")[0] for t in comp.tools}
        partition: list[frozenset[str]] = []
        for sname in self.server_names:
            tools_in = frozenset(
                tname for tname, srv in tool_to_server.items() if srv == sname
            )
            if tools_in:
                partition.append(tools_in)
        decomposition = decompose_fee(comp, partition)
        assert decomposition.boundary_fee == 1

    def test_boundary_obligations_count(self):
        guard = BullaGuard.from_tools_list(self.all_tools, name="test-audit")
        comp = guard.composition
        diag = diagnose(comp)
        tool_to_server = {t.name: t.name.split("__")[0] for t in comp.tools}
        partition: list[frozenset[str]] = []
        for sname in self.server_names:
            tools_in = frozenset(
                tname for tname, srv in tool_to_server.items() if srv == sname
            )
            if tools_in:
                partition.append(tools_in)
        decomposition = decompose_fee(comp, partition)
        obligations = boundary_obligations_from_decomposition(
            comp, list(decomposition.partition), diag,
        )
        assert len(obligations) == 3
        dims = {o.dimension for o in obligations}
        assert "path_convention_match" in dims


# ── Canonical demo smoke test ────────────────────────────────────────


class TestCanonicalDemo:
    def test_demo_runs_successfully(self):
        demo_path = Path(__file__).parent.parent / "examples" / "canonical-demo" / "run_canonical_demo.py"
        result = subprocess.run(
            [sys.executable, str(demo_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"Demo failed:\n{result.stderr}"
        assert "The Seam Problem" in result.stdout
        assert "Coherence fee: 30" in result.stdout
        assert "boundary fee: 1" in result.stdout
        assert "absolute_local" in result.stdout
        assert "relative_repo" in result.stdout
        assert "VALID" in result.stdout
        assert "path_convention_match" in result.stdout

    def test_live_flag_parses(self):
        """Smoke test: --help exits cleanly, proving --live is registered."""
        demo_path = Path(__file__).parent.parent / "examples" / "canonical-demo" / "run_canonical_demo.py"
        result = subprocess.run(
            [sys.executable, str(demo_path), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"--help failed:\n{result.stderr}"
        assert "--live" in result.stdout


# ── Pre-computed receipt integrity ───────────────────────────────────


class TestPrecomputedReceipt:
    def test_receipt_exists(self):
        assert RECEIPT_PATH.exists(), f"Receipt not found at {RECEIPT_PATH}"

    def test_receipt_integrity(self):
        receipt = json.loads(RECEIPT_PATH.read_text())
        assert verify_receipt_integrity(receipt)

    def test_receipt_has_path_convention(self):
        receipt = json.loads(RECEIPT_PATH.read_text())
        inline = receipt.get("inline_dimensions", {})
        dims = inline.get("dimensions", {})
        assert "path_convention_match" in dims

    def test_receipt_has_both_values(self):
        receipt = json.loads(RECEIPT_PATH.read_text())
        dims = receipt["inline_dimensions"]["dimensions"]
        path_dim = dims["path_convention_match"]
        vals = path_dim.get("known_values", [])
        assert "absolute_local" in vals
        assert "relative_repo" in vals

    def test_receipt_has_boundary_obligations(self):
        receipt = json.loads(RECEIPT_PATH.read_text())
        obls = receipt.get("boundary_obligations", [])
        assert len(obls) > 0
        dims = {o["dimension"] for o in obls}
        assert "path_convention_match" in dims
