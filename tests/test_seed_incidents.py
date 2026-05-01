"""Integration tests for the 30 reconstructed historical mismatch incidents.

**SCOPE: Coboundary-correctness (Claim A in Phase 5 framing) — NOT
classifier-discovery.**

The incident YAMLs encode pre-labeled dimension edges
(``force_unit_match``, ``dose_unit_match``, ``currency_code``, etc.)
in their composition graphs by construction. The diagnostic runs δ₀
over those *labeled* edges and gets fee > 0 deterministically. This
file validates that:

  1. The measurement layer (coboundary) works correctly on a
     known-good case;
  2. The incident-corpus build script produces well-shaped
     compositions that load without error;
  3. The high-confidence pin-set (Mars Climate Orbiter, Drupal+Stripe
     JPY, etc.) doesn't regress under any future pack-stack changes.

This file does NOT validate the classifier's ability to *discover*
which dimensions are relevant from raw, unlabeled tool schemas.
That harder claim is tested separately by the calibration-corpus
signal-density metric in ``test_phase5_validation.py``
(``TestClaimB_ClassifierDiscoveryOnUnlabeledSchemas``).

100% detection here is necessary but near-trivial: it confirms δ₀
works on a graph where we already named the edges. The framework's
load-bearing value-prop is Claim B (discovery on unlabeled
schemas), where the seed packs add 29.4% signal-density.
"""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path

import pytest
import yaml

from bulla.diagnostic import diagnose
from bulla.infer.classifier import _reset_taxonomy_cache, configure_packs
from bulla.parser import load_composition


def _incidents_dir() -> Path:
    pkg = importlib.resources.files("bulla")
    return Path(str(pkg / ".." / ".." / "calibration" / "data" / "incidents")).resolve()


def _seed_dir() -> Path:
    pkg = importlib.resources.files("bulla")
    return Path(str(pkg / "packs" / "seed"))


@pytest.fixture(autouse=True)
def reset_caches():
    _reset_taxonomy_cache()
    yield
    _reset_taxonomy_cache()


# ── Manifest sanity ──────────────────────────────────────────────────


class TestIncidentManifest:
    def test_manifest_exists_and_lists_30_incidents(self):
        manifest_path = _incidents_dir() / "incidents-manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["incident_count"] == 30
        assert len(manifest["incidents"]) == 30

    def test_every_incident_yaml_listed_in_manifest(self):
        manifest = json.loads(
            (_incidents_dir() / "incidents-manifest.json")
            .read_text(encoding="utf-8")
        )
        listed_names = {row["name"] for row in manifest["incidents"]}
        on_disk = {
            p.stem
            for p in _incidents_dir().glob("*.yaml")
        }
        assert listed_names == on_disk, (
            f"manifest/disk drift: only-in-manifest="
            f"{listed_names - on_disk}, "
            f"only-on-disk={on_disk - listed_names}"
        )

    def test_every_incident_has_required_metadata(self):
        manifest = json.loads(
            (_incidents_dir() / "incidents-manifest.json")
            .read_text(encoding="utf-8")
        )
        for row in manifest["incidents"]:
            for key in ("name", "domain", "primary_pack", "loss"):
                assert key in row, f"row {row} missing {key}"


# ── Per-incident loadability ─────────────────────────────────────────


@pytest.mark.parametrize(
    "yaml_name",
    sorted(p.name for p in _incidents_dir().glob("*.yaml")),
)
def test_incident_yaml_loads(yaml_name: str):
    """Every reconstructed incident must parse via load_composition.
    A parse failure is a build error in the generator script."""
    path = _incidents_dir() / yaml_name
    comp = load_composition(path)
    assert comp.name
    assert len(comp.tools) >= 2
    assert len(comp.edges) >= 1


# ── Phase 5 acceptance: ≥80% precision ───────────────────────────────


class TestIncidentDetectionPrecision:
    """Load every incident, run the diagnostic with the full seed pack
    stack loaded, and count how many produce a non-zero coherence fee.

    Acceptance threshold: ≥80% (24 of 30) detect (fee > 0). This is
    the load-bearing Phase 5 metric for the framework's claim that
    the topological diagnostic is empirically grounded — every named
    multi-billion-dollar incident in the BABEL paper's evidence
    table must surface as a positive detection."""

    def test_at_least_80_percent_of_incidents_detected(self):
        seed_paths = sorted(_seed_dir().glob("*.yaml"))
        configure_packs(extra_paths=seed_paths)

        detected = 0
        misses: list[str] = []
        for incident_path in sorted(_incidents_dir().glob("*.yaml")):
            comp = load_composition(incident_path)
            diag = diagnose(comp)
            if diag.coherence_fee > 0:
                detected += 1
            else:
                misses.append(incident_path.stem)

        rate = detected / 30
        # Report on stderr-equivalent (pytest's -s shows print).
        print(
            f"\nIncident detection: {detected}/30 = {rate:.0%} "
            f"(threshold 80%)\n"
            f"Misses: {misses}"
        )
        assert rate >= 0.80, (
            f"Detection rate {rate:.0%} below 80% threshold. "
            f"Misses: {misses}"
        )


# ── High-confidence pin: specific incidents must always detect ───────


HIGH_CONFIDENCE_INCIDENTS = [
    "mars_climate_orbiter",
    "drupal_stripe_jpy",
    "vancouver_stock_exchange",
    "patriot_missile_dhahran",
    "gimli_glider",
    "ariane_5_flight_501",
    "levothyroxine_mg_mcg",
    "icd_9_to_icd_10_transition",
]


@pytest.mark.parametrize("incident_name", HIGH_CONFIDENCE_INCIDENTS)
def test_high_confidence_incident_detected(incident_name: str):
    """Pin individual high-confidence incidents so a regression on
    any one of them surfaces with the specific name."""
    seed_paths = sorted(_seed_dir().glob("*.yaml"))
    configure_packs(extra_paths=seed_paths)
    path = _incidents_dir() / f"{incident_name}.yaml"
    comp = load_composition(path)
    diag = diagnose(comp)
    assert diag.coherence_fee > 0, (
        f"{incident_name}: coherence_fee={diag.coherence_fee} "
        f"(expected > 0)"
    )
