"""Tests for the CLI: check pass/fail, SARIF output structure."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bulla import __version__

COMPOSITIONS_DIR = Path(__file__).parent.parent / "compositions"
AUTH = COMPOSITIONS_DIR / "auth_pipeline.yaml"
FINANCIAL = COMPOSITIONS_DIR / "financial_pipeline.yaml"


def _run(*args: str) -> subprocess.CompletedProcess:
    # A hard timeout so a slow/blocking subprocess can never hang the whole suite forever
    # (the network-discipline invariant at the test boundary). A real `bulla` command is
    # well under this; tripping it is a genuine failure, not an infinite hang.
    return subprocess.run(
        [sys.executable, "-m", "bulla", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestDiagnoseCommand:
    def test_text_output(self):
        r = _run("diagnose", str(AUTH))
        assert r.returncode == 0
        assert "Auth-Data-Audit Pipeline" in r.stdout
        assert "COHERENCE FEE = 0" in r.stdout

    def test_json_output(self):
        r = _run("diagnose", "--format", "json", str(FINANCIAL))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["coherence_fee"] == 2
        assert data["bulla_version"] == __version__
        assert "composition_sha256" in data
        assert "timestamp" in data

    def test_sarif_output(self):
        r = _run("diagnose", "--format", "sarif", str(FINANCIAL))
        assert r.returncode == 0
        sarif = json.loads(r.stdout)
        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"]) == 1
        run = sarif["runs"][0]
        assert run["tool"]["driver"]["name"] == "bulla"
        results = run["results"]
        blind_spots = [r for r in results if r["ruleId"] == "bulla/blind-spot"]
        bridges = [r for r in results if r["ruleId"] == "bulla/bridge-recommendation"]
        assert len(blind_spots) == 2
        assert len(bridges) == 2

    def test_examples_flag(self):
        r = _run("diagnose", "--examples")
        assert r.returncode == 0
        assert "Summary:" in r.stdout
        assert "10 compositions" in r.stdout

    def test_no_files_error(self):
        r = _run("diagnose")
        assert r.returncode != 0


class TestCheckCommand:
    def test_pass_clean_composition(self):
        r = _run("check", str(AUTH))
        assert r.returncode == 0
        assert "PASS" in r.stdout

    def test_fail_blind_spots(self):
        r = _run("check", str(FINANCIAL))
        assert r.returncode == 1
        assert "FAIL" in r.stderr

    def test_relaxed_threshold_passes(self):
        r = _run("check", "--max-blind-spots", "5", "--max-unbridged", "5", str(FINANCIAL))
        assert r.returncode == 0
        assert "PASS" in r.stdout

    def test_check_json_output(self):
        r = _run("check", "--format", "json", str(AUTH))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["passed"] is True
        assert len(data["compositions"]) == 1

    def test_check_sarif_output(self):
        r = _run("check", "--format", "sarif", str(FINANCIAL))
        assert r.returncode == 1
        sarif = json.loads(r.stdout)
        assert sarif["version"] == "2.1.0"

    def test_certificate_out_single_composition(self, tmp_path):
        """v0.38.0: --certificate-out PATH writes a single CompositionCertificate."""
        cert_path = tmp_path / "auth_cert.json"
        r = _run("check", "--certificate-out", str(cert_path), str(AUTH))
        assert r.returncode == 0
        assert cert_path.exists()
        cert = json.loads(cert_path.read_text())
        # Single-composition input → single object (not array)
        assert isinstance(cert, dict)
        assert "certificate_schema_version" in cert
        assert cert["certificate_schema_version"] == "1.0"
        assert "diagnostic" in cert
        assert "claims" in cert

    def test_certificate_out_multi_composition_array(self, tmp_path):
        """Multiple compositions → JSON array of certificates."""
        cert_path = tmp_path / "multi_cert.json"
        r = _run("check", "--max-blind-spots", "5", "--max-unbridged", "5",
                 "--certificate-out", str(cert_path),
                 str(AUTH), str(FINANCIAL))
        assert r.returncode == 0
        assert cert_path.exists()
        certs = json.loads(cert_path.read_text())
        assert isinstance(certs, list)
        assert len(certs) == 2
        for cert in certs:
            assert "certificate_schema_version" in cert
            assert cert["certificate_schema_version"] == "1.0"

    def test_certificate_out_writes_even_on_fail(self, tmp_path):
        """Certificate is written before exit, even when CI gate fails."""
        cert_path = tmp_path / "fail_cert.json"
        r = _run("check", "--certificate-out", str(cert_path), str(FINANCIAL))
        # CI gate fails (FINANCIAL has blind spots), but certificate should still be written
        assert r.returncode == 1
        assert cert_path.exists(), (
            "Certificate must be written even when CI gate fails — "
            "G24 historical analysis records state at every commit "
            "regardless of pass/fail verdict."
        )
        cert = json.loads(cert_path.read_text())
        assert cert["diagnostic"]["coherence_fee"] >= 1, (
            "FINANCIAL has blind spots → fee >= 1 expected in certificate"
        )

    def test_certificate_out_stderr_message(self, tmp_path):
        """--certificate-out emits a 'Wrote N certificate(s) to ...' stderr message."""
        cert_path = tmp_path / "auth_cert.json"
        r = _run("check", "--certificate-out", str(cert_path), str(AUTH))
        assert "Wrote 1 certificate(s) to" in r.stderr
        assert str(cert_path) in r.stderr


class TestSarifStructure:
    def test_sarif_schema_fields(self):
        r = _run("diagnose", "--format", "sarif", str(FINANCIAL))
        sarif = json.loads(r.stdout)
        assert "$schema" in sarif
        assert sarif["version"] == "2.1.0"
        run = sarif["runs"][0]
        driver = run["tool"]["driver"]
        assert "name" in driver
        assert "version" in driver
        assert "rules" in driver
        assert len(driver["rules"]) == 2
        for rule in driver["rules"]:
            assert "id" in rule
            assert "shortDescription" in rule
        for result in run["results"]:
            assert "ruleId" in result
            assert "level" in result
            assert "message" in result
            assert "locations" in result
            assert len(result["locations"]) > 0
            loc = result["locations"][0]
            assert "physicalLocation" in loc
            assert "artifactLocation" in loc["physicalLocation"]

    def test_sarif_invocation_metadata(self):
        r = _run("diagnose", "--format", "sarif", str(FINANCIAL))
        sarif = json.loads(r.stdout)
        invocations = sarif["runs"][0]["invocations"]
        assert len(invocations) == 1
        inv = invocations[0]
        assert inv["executionSuccessful"] is True
        assert "bulla_version" in inv["properties"]
        assert "timestamp" in inv["properties"]


class TestGaugeCommand:
    MANIFEST = Path(__file__).parent / "fixtures" / "sample_mcp_manifest.json"

    def test_gauge_text_output(self):
        r = _run("gauge", str(self.MANIFEST))
        assert r.returncode == 0
        assert "Coherence fee:" in r.stdout
        assert "Disclosure set" in r.stdout
        assert "Witness basis:" in r.stdout

    def test_gauge_json_output(self):
        r = _run("gauge", "--format", "json", str(self.MANIFEST))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "disclosure_set" in data
        assert "witness_basis" in data
        assert "coherence_fee" in data
        assert isinstance(data["disclosure_set"], list)

    def test_gauge_threshold_fail(self):
        r = _run("gauge", "--max-fee", "0", str(self.MANIFEST))
        assert r.returncode == 1
        assert "FAIL" in r.stderr

    def test_gauge_threshold_pass(self):
        r = _run("gauge", "--max-fee", "999", str(self.MANIFEST))
        assert r.returncode == 0

    def test_gauge_blind_spots_threshold(self):
        r = _run("gauge", "--max-blind-spots", "0", str(self.MANIFEST))
        assert r.returncode == 1
        assert "FAIL" in r.stderr

    def test_gauge_output_composition_roundtrip(self, tmp_path):
        out_file = tmp_path / "comp.yaml"
        r = _run("gauge", "-o", str(out_file), str(self.MANIFEST))
        assert r.returncode == 0
        assert out_file.exists()
        from bulla.parser import load_composition
        comp = load_composition(out_file)
        assert comp.name == "inferred-from-sample_mcp_manifest"
        assert len(comp.tools) > 0


MANIFESTS_DIR = Path(__file__).parent.parent / "examples" / "real_world_audit" / "manifests"


# QUARANTINED (deselected by default; run with `pytest -m quarantine`). These shell out to
# `python -m bulla audit --manifests …` and block >120s ONLY when collected in-suite. It is
# NOT the command: `bulla audit` completes in ~4s standalone, in-process, and with the
# network fully blocked (verified), and the funnel commands (`bulla audit`/`scan`) return
# clean errors in 0.1s offline. Root cause is a pytest-collection/subprocess interaction,
# unconfirmed — tracked separately. (`_run` carries a hard timeout so it can never hang the
# suite forever regardless.)
@pytest.mark.quarantine
class TestAuditManifestsFlag:
    def test_audit_manifests_text(self):
        r = _run("audit", "--manifests", str(MANIFESTS_DIR))
        assert r.returncode == 0
        assert "BOUNDARY FEE" in r.stdout
        assert "within fee" in r.stdout

    def test_audit_manifests_json(self):
        r = _run("audit", "--manifests", str(MANIFESTS_DIR), "--format", "json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["coherence_fee"] > 0
        assert "cross_server_decomposition" in data
        assert data["cross_server_decomposition"]["boundary_fee"] > 0
        assert "audit_report" in data
        assert data["audit_report"]["boundary_fee"] > 0

    def test_audit_manifests_sarif(self):
        r = _run("audit", "--manifests", str(MANIFESTS_DIR), "--format", "sarif")
        assert r.returncode == 0
        sarif = json.loads(r.stdout)
        assert sarif["$schema"].startswith("https://")
        assert len(sarif["runs"]) == 1

    def test_audit_manifests_threshold_fail(self):
        r = _run("audit", "--manifests", str(MANIFESTS_DIR), "--max-fee", "0")
        assert r.returncode == 1

    def test_audit_manifests_threshold_pass(self):
        r = _run("audit", "--manifests", str(MANIFESTS_DIR), "--max-fee", "999")
        assert r.returncode == 0

    def test_audit_manifests_nonexistent_dir(self):
        r = _run("audit", "--manifests", "/nonexistent")
        assert r.returncode == 1
        assert "not a directory" in r.stderr


class TestVersionFlag:
    def test_version(self):
        r = _run("--version")
        assert r.returncode == 0
        assert __version__ in r.stdout


FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_DIR = Path(__file__).parent / "golden"
WITNESS_COSTS = FIXTURES_DIR / "witness_costs.yaml"
GOLDEN_0_34 = GOLDEN_DIR / "diagnose_0.34_financial_pipeline.json"
MANIFESTS_DIR = (
    Path(__file__).parent.parent
    / "calibration"
    / "data"
    / "registry"
    / "manifests"
)
FILESYSTEM_MANIFEST = MANIFESTS_DIR / "filesystem.json"


class TestWitnessGeometry:
    """Coverage target: every new CLI flag has >=1 positive test and
    >=1 regression test (default behavior unchanged when flag absent)."""

    # ── --witness on diagnose ───────────────────────────────────────

    def test_diagnose_witness_text_positive(self):
        r = _run("diagnose", str(FINANCIAL), "--witness")
        assert r.returncode == 0
        assert "Witness Geometry" in r.stdout
        assert "Concentration N_eff" in r.stdout

    def test_diagnose_witness_json_positive(self):
        r = _run("diagnose", str(FINANCIAL), "--witness", "--format", "json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "witness_geometry" in data
        wg = data["witness_geometry"]
        assert isinstance(wg["leverage"], list)
        assert len(wg["leverage"]) == 4
        for entry in wg["leverage"]:
            assert set(entry.keys()) == {"tool", "field", "score"}
            # Score must be a string rational, never a float
            assert isinstance(entry["score"], str)

    def test_diagnose_default_json_regression(self):
        """Default JSON output (no --witness) is byte-identical to the
        pinned 0.34.0 golden fixture, modulo bulla_version + timestamp.
        """
        r = _run("diagnose", str(FINANCIAL), "--format", "json")
        assert r.returncode == 0
        current = json.loads(r.stdout)
        golden = json.loads(GOLDEN_0_34.read_text())
        for key in ("bulla_version", "timestamp"):
            current.pop(key, None)
            golden.pop(key, None)
        assert current == golden, (
            "Default JSON output drifted from 0.34.0 golden fixture. "
            "The --witness flag must be additive-only."
        )

    def test_diagnose_witness_fee_zero_no_block(self):
        """When fee == 0, --witness must not emit a witness_geometry block."""
        r = _run("diagnose", str(AUTH), "--witness", "--format", "json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["coherence_fee"] == 0
        assert "witness_geometry" not in data

    # ── leverage conservation (exact Fraction) ──────────────────────

    def test_leverage_conservation_exact_rational(self):
        """Sum of leverage == coherence_fee in exact Fraction arithmetic."""
        from fractions import Fraction

        r = _run("diagnose", str(FINANCIAL), "--witness", "--format", "json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        total = sum(
            (
                Fraction(entry["score"])
                for entry in data["witness_geometry"]["leverage"]
            ),
            start=Fraction(0),
        )
        assert total == data["coherence_fee"]

    # ── --witness on check ──────────────────────────────────────────

    def test_check_witness_passthrough(self):
        """`bulla check --witness` forwards the flag through to diagnose
        so JSON output carries the witness_geometry block, independent
        of the check's pass/fail verdict."""
        r = _run(
            "check",
            str(FINANCIAL),
            "--max-blind-spots",
            "100",
            "--max-unbridged",
            "100",
            "--witness",
            "--format",
            "json",
        )
        assert r.returncode == 0
        data = json.loads(r.stdout)
        # The check command aggregates compositions; grab the first.
        comp = data["compositions"][0]
        assert "witness_geometry" in comp
        assert isinstance(comp["witness_geometry"]["leverage"], list)

    def test_check_without_witness_no_block(self):
        """Regression: without --witness, check's JSON output must not
        carry the witness_geometry block."""
        r = _run(
            "check",
            str(FINANCIAL),
            "--max-blind-spots",
            "100",
            "--max-unbridged",
            "100",
            "--format",
            "json",
        )
        assert r.returncode == 0
        data = json.loads(r.stdout)
        comp = data["compositions"][0]
        assert "witness_geometry" not in comp

    # ── --leverage on gauge ─────────────────────────────────────────

    def test_gauge_leverage_text_positive(self):
        if not FILESYSTEM_MANIFEST.exists():
            pytest.skip("filesystem manifest not available in this tree")
        r = _run("gauge", str(FILESYSTEM_MANIFEST), "--leverage")
        assert r.returncode == 0
        assert "Witness Geometry" in r.stdout
        assert "Greedy minimum-cost basis" in r.stdout

    def test_gauge_default_no_witness_section(self):
        """`bulla gauge` without --leverage/--substitutes/--costs must
        not include the witness block in text output (regression guard)."""
        if not FILESYSTEM_MANIFEST.exists():
            pytest.skip("filesystem manifest not available in this tree")
        r = _run("gauge", str(FILESYSTEM_MANIFEST))
        assert r.returncode == 0
        assert "Witness Geometry" not in r.stdout

    # ── --substitutes on gauge ──────────────────────────────────────

    def test_gauge_substitutes_positive(self):
        if not FILESYSTEM_MANIFEST.exists():
            pytest.skip("filesystem manifest not available in this tree")
        r = _run(
            "gauge",
            str(FILESYSTEM_MANIFEST),
            "--substitutes",
            "read_file",
            "path",
        )
        assert r.returncode == 0
        assert "Disclosure substitutes for read_file.path" in r.stdout
        assert "R_eff" in r.stdout

    def test_gauge_substitutes_unknown_target_exits_nonzero(self):
        """Unknown (tool, field) targets produce exit code 1 + stderr."""
        if not FILESYSTEM_MANIFEST.exists():
            pytest.skip("filesystem manifest not available in this tree")
        r = _run(
            "gauge",
            str(FILESYSTEM_MANIFEST),
            "--substitutes",
            "bogus_tool",
            "nonexistent_field",
        )
        assert r.returncode == 1
        assert "not a hidden field" in r.stderr

    def test_gauge_substitutes_missing_arg_argparse_error(self):
        """--substitutes expects exactly two positional args."""
        if not FILESYSTEM_MANIFEST.exists():
            pytest.skip("filesystem manifest not available in this tree")
        r = _run(
            "gauge",
            str(FILESYSTEM_MANIFEST),
            "--substitutes",
            "read_file",  # missing FIELD
        )
        assert r.returncode != 0

    # ── --costs on gauge ────────────────────────────────────────────

    def test_gauge_costs_positive(self):
        if not FILESYSTEM_MANIFEST.exists():
            pytest.skip("filesystem manifest not available in this tree")
        r = _run(
            "gauge",
            str(FILESYSTEM_MANIFEST),
            "--costs",
            str(WITNESS_COSTS),
        )
        assert r.returncode == 0
        assert "Minimum-cost disclosure basis" in r.stdout
        assert "Total cost:" in r.stdout

    def test_gauge_costs_missing_file(self):
        target = (
            str(FILESYSTEM_MANIFEST)
            if FILESYSTEM_MANIFEST.exists()
            else str(FINANCIAL)
        )
        r = _run(
            "gauge",
            target,
            "--costs",
            "/tmp/__bulla_nonexistent_costs__.yaml",
        )
        assert r.returncode == 1

    # ── Diagnostic.content_hash backward compat ─────────────────────

    def test_diagnostic_content_hash_empty_witness_stable(self):
        """Empty witness-geometry fields must not perturb the content
        hash, so existing receipts hash identically after D5.1."""
        from bulla.model import Diagnostic
        from fractions import Fraction

        d_no_witness = Diagnostic(
            name="test", n_tools=3, n_edges=3, betti_1=1,
            dim_c0_obs=12, dim_c0_full=16, dim_c1=8,
            rank_obs=6, rank_full=8, h1_obs=2, h1_full=0,
            coherence_fee=2, blind_spots=(), bridges=(), h1_after_bridge=0,
        )
        d_empty_witness = Diagnostic(
            name="test", n_tools=3, n_edges=3, betti_1=1,
            dim_c0_obs=12, dim_c0_full=16, dim_c1=8,
            rank_obs=6, rank_full=8, h1_obs=2, h1_full=0,
            coherence_fee=2, blind_spots=(), bridges=(), h1_after_bridge=0,
            hidden_basis=(),
            leverage_scores=(),
            n_effective=None,
            coloops=(),
            loops=(),
            disclosure_set=(),
        )
        assert d_no_witness.content_hash() == d_empty_witness.content_hash()

        d_populated = Diagnostic(
            name="test", n_tools=3, n_edges=3, betti_1=1,
            dim_c0_obs=12, dim_c0_full=16, dim_c1=8,
            rank_obs=6, rank_full=8, h1_obs=2, h1_full=0,
            coherence_fee=2, blind_spots=(), bridges=(), h1_after_bridge=0,
            hidden_basis=(("a", "x"),),
            leverage_scores=(Fraction(1, 2),),
            n_effective=Fraction(1),
        )
        assert d_populated.content_hash() != d_no_witness.content_hash()


class TestComposeCommand:
    """Tests for `bulla compose` — the developer-facing prescriptive command."""

    def test_prescriptive_coherent(self):
        """A coherent composition reports fee=0 and no fix instructions."""
        r = _run("compose", str(AUTH))
        assert r.returncode == 0
        assert "Bulla Compose Report" in r.stdout
        assert "COMPOSITION IS COHERENT" in r.stdout
        # No fix instructions on a coherent composition.
        assert "To make this composition safe" not in r.stdout
        assert "Apply all bridges automatically" not in r.stdout

    def test_prescriptive_obstructed(self):
        """An obstructed composition reports fee>0 and lists specific fields to expose."""
        r = _run("compose", str(FINANCIAL))
        assert r.returncode == 0
        assert "Bulla Compose Report" in r.stdout
        assert "Witness rank (fee):" in r.stdout
        # FINANCIAL has fee=2; we expect prescriptive output.
        assert "To make this composition safe" in r.stdout
        assert "tool `" in r.stdout
        assert "field `" in r.stdout
        assert "Action: add" in r.stdout
        assert "Apply all bridges automatically" in r.stdout
        assert "bulla bridge" in r.stdout

    def test_json_format_fallback(self):
        """--format json emits the same WitnessReceipt as `bulla witness`."""
        r = _run("compose", "--format", "json", str(FINANCIAL))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        # Same structured receipt schema as bulla witness.
        assert "receipt_version" in data
        assert "fee" in data
        assert "patches" in data
        assert "disposition" in data
        assert data["fee"] == 2

    def test_multi_file_summary(self):
        """Multi-file invocation prints summary footer."""
        r = _run("compose", str(AUTH), str(FINANCIAL))
        assert r.returncode == 0
        # Each file has its own report header.
        assert r.stdout.count("Bulla Compose Report") == 2
        # Summary footer at the end.
        assert "Summary: 2 composition" in r.stdout
        assert "1 coherent" in r.stdout
        assert "1 requiring disclosure" in r.stdout

    def test_no_files_error(self):
        """Compose with no files exits with error."""
        r = _run("compose")
        assert r.returncode != 0


class TestReceiptCreateCommand:
    """`bulla receipt create` — the stranger's ergonomic (v0.2 diet)."""

    def _create(self, tmp_path, *extra: str) -> Path:
        out = tmp_path / "receipt.json"
        r = _run(
            "receipt", "create", "--type", "demo.echo",
            "--subject", "msg=hello", "--subject", "amount=1250",
            "--forum-endpoint", "https://log.example", "--forum-root", "ots:root",
            "--timestamp", "2026-07-13T00:00:00+00:00",
            "--out", str(out), *extra,
        )
        assert r.returncode == 0, r.stderr
        return out

    def test_create_then_verify_roundtrip(self, tmp_path):
        out = self._create(tmp_path)
        doc = json.loads(out.read_text())
        assert doc["schema_version"] == "0.2"
        assert doc["action"]["subject"]["amount"] == 1250  # k=v parsed as JSON scalar
        r = _run("receipt", "verify", str(out))
        assert r.returncode == 0
        assert "verified_to=digest" in r.stdout

    def test_verify_json_exposes_answerability_without_inventing_reliance(self, tmp_path):
        out = self._create(tmp_path)
        r = _run("receipt", "verify", str(out), "--format", "json")
        assert r.returncode == 0, r.stderr
        payload = json.loads(r.stdout)
        assert payload["bounds_conformance"] == "not_applicable"
        assert payload["answerability"] == {
            "integrity": "VERIFIED",
            "authenticity": "UNVERIFIED",
            "authority": "UNAUTHENTICATED",
            "scope": "NOT_APPLICABLE",
            "grounding": "UNRESOLVED",
            "recourse": "NAMED",
            "reachability": "UNVERIFIED",
            "reliance_decision": "NOT_COMPUTED",
        }

    def test_create_with_convention_surfaces_conformance(self, tmp_path):
        conv = tmp_path / "conv.json"
        conv.write_text(json.dumps({
            "name": "amount-in-usd-cents",
            "scope": "seam:demo",
            "kind": "executable",
            "definition": {
                "form": "jsonschema+quantum/1",
                "schema": {"type": "object", "required": ["amount"],
                           "properties": {"amount": {"type": "integer", "minimum": 0}}},
                "quantum": {"amount": {"unit": "USD_cents", "multipleOf": 1}},
            },
        }))
        out = self._create(tmp_path, "--convention", str(conv))
        r = _run("receipt", "verify", str(out))
        assert r.returncode == 0
        assert "convention amount-in-usd-cents: conforms" in r.stdout

    def test_signed_create_uses_v03_full_authority_binding(self, tmp_path):
        from bulla.identity import LocalEd25519Signer

        signer = LocalEd25519Signer.generate()
        key = tmp_path / "signer.json"
        key.write_text(json.dumps(signer.to_keyfile_dict()))
        out = self._create(tmp_path, "--key", str(key))
        doc = json.loads(out.read_text())
        assert doc["schema_version"] == "0.3"
        assert doc["signature"]["issuer"] == doc["authorization"]["issuer"]

        r = _run("receipt", "verify", str(out))
        assert r.returncode == 0, r.stderr
        assert "verified_to=attestation" in r.stdout
        assert "authority=verified" in r.stdout

    def test_create_refuses_missing_forum(self, tmp_path):
        r = _run("receipt", "create", "--type", "demo.echo", "--out", str(tmp_path / "x.json"))
        assert r.returncode == 2  # argparse: forum endpoint+root are required (modality law)

    def test_gate_help_does_not_promise_fee_gating_by_default(self):
        r = _run("gate", "--help")
        assert r.returncode == 0
        assert "OPT IN to fee-gating" in r.stdout
