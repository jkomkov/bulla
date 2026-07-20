"""Sprint 13/14 — canonical seed-set certificate regression.

Re-generates the current versioned seed-certificate fixture
in-memory and asserts byte-identity with the committed fixture, modulo
the inherently nondeterministic fields (`timestamp`, `bulla_version`,
`certificate_hash`).

The fixture currently encodes Sprint 14 v1.0 schema (top-level subject /
method / claims / scope / display, structured Claim objects with
{value, status, licensed_by[, not_licensed]}, and a stable
certificate_hash).

If a future sprint shifts the certificate schema, this test fails loudly
with the regenerated payload available for diff review. The same gate
pattern as Sprint 12's `test_diagnose_default_json_regression`.

**This golden is also the verdict-change GUARD for canonicity.** Because it pins
canonical `certificate_content_hash` values, a verdict-affecting change to
`diagnose`/`classify`/`coboundary`/`witness_geometry` that is NOT accompanied by an
`ALGORITHM_VERSION` bump (`bulla._canonical`) makes these hashes drift and fails
here — forcing the bump. NOTE: this is a *stopgap* for the missing auto-coupling
between `f`'s source and its version (it only covers the seed set); the canonical
fix is to derive `algorithm_version` from `f`'s content / the Lean-spec hash. See
the canonicity ladder in `bulla/src/bulla/_canonical.py`.

Re-generate the fixture (when intentional schema drift):
    python -m bulla certify --seed-set --format json \
        --output calibration/fixtures/sprint13_seed_certificates-v044.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BULLA_ROOT = Path(__file__).resolve().parents[1]
REPO = BULLA_ROOT.parent if (BULLA_ROOT.parent / "bulla").is_dir() else BULLA_ROOT
sys.path.insert(0, str(BULLA_ROOT / "src"))

from bulla.cli import _seed_set_compositions  # noqa: E402
from bulla.certificate import certify, to_dict  # noqa: E402

CANONICAL_FIXTURE = BULLA_ROOT / "calibration/fixtures/sprint13_seed_certificates-v044.json"
RETIRED_FIXTURE = (
    BULLA_ROOT
    / "calibration/fixtures/archive/epoch-pre-completeness/sprint13_seed_certificates.json"
)
FORMER_CANONICAL_FIXTURE = (
    BULLA_ROOT / "calibration/fixtures/sprint13_seed_certificates.json"
)
LIFECYCLE_RECORD = BULLA_ROOT / "calibration/fixtures/sprint13-seed-lifecycle-v044.json"


def _strip_nondeterministic(certs: list[dict]) -> list[dict]:
    """Remove version-dependent and time-dependent fields so byte-
    identity can be asserted across runs and across release bumps.

    Strips only producer/environment provenance that lives in the certificate
    BODY but is NOT part of the deed's content-address:
      - timestamp (clock-dependent)
      - bulla_version (release-dependent)
      - method (versioned producer strings)
      - attestation_hash / receipt_hash (filled only at signing; None here)

    PINNED (deliberately NOT stripped):
      - certificate_content_hash — now a true content-address: it excludes all of
        the above plus subject.source_path, so it is machine- AND version-
        independent. This test therefore *pins* it (stronger coverage than the
        old behavior, which popped it because it used to depend on bulla_version).
      - subject.source_path — now a portable basename (never an absolute path), so
        it is compared rather than stripped.

    A drift in the pinned hash or the basename is a real regression (or an
    intended semantic change); regenerate the fixture only then.
    """
    out = []
    for c in certs:
        c2 = dict(c)
        c2.pop("timestamp", None)
        c2.pop("bulla_version", None)
        c2.pop("method", None)
        c2.pop("certificate_hash", None)  # legacy v0 name; harmless
        c2.pop("attestation_hash", None)
        c2.pop("receipt_hash", None)
        # Compare the wire representation. Producer helpers may retain tuples
        # in memory, while JSON canonically materializes them as arrays.
        out.append(json.loads(json.dumps(c2, sort_keys=True)))
    return out


def test_seed_set_canonical_output_matches_fixture():
    """Re-generate the seed-set certificates in-memory and assert
    byte-identity with the committed fixture.

    If this fails:
      1. Inspect the diff between fixture and current output.
      2. If the change is intentional (e.g., schema bump, regime label
         refinement, or **legitimate manifest drift** — registry
         manifests on disk under
         `bulla/calibration/data/registry/manifests/` may evolve as
         upstream MCP servers add tools or change schemas, which
         legitimately changes pair-composition fees), regenerate the
         fixture per the docstring above.
      3. If the change is unintentional, investigate which producer
         (regime / diagnose / certify / lookup table) drifted.

    Note: certificates 1 and 2 (filesystem+github, github+notion) are
    derived from real registry manifests; certificates 3-9 are derived
    from version-controlled YAMLs and synthetic generators that do not
    drift. So fixture failures localized to certs 1-2 are most likely
    manifest drift; failures on certs 3-9 indicate a code drift.
    """
    fixture = json.loads(CANONICAL_FIXTURE.read_text())
    pairs = _seed_set_compositions(REPO)
    current = [to_dict(certify(comp, source_path=src)) for comp, src in pairs]

    fixture_stripped = _strip_nondeterministic(fixture)
    current_stripped = _strip_nondeterministic(current)

    assert len(fixture_stripped) == len(current_stripped), (
        f"Seed-set size drifted: fixture has {len(fixture_stripped)} certs, "
        f"current has {len(current_stripped)}. Either the seed set "
        f"definition changed (cli._SEED_SET_*) or registry/curated fixtures "
        f"shifted on disk."
    )

    for i, (f_cert, c_cert) in enumerate(zip(fixture_stripped, current_stripped)):
        # Compare top-level keys first for clearer error messages
        assert sorted(f_cert.keys()) == sorted(c_cert.keys()), (
            f"Cert {i} ({f_cert.get('name')}): keys drifted. "
            f"Fixture: {sorted(f_cert.keys())}. "
            f"Current: {sorted(c_cert.keys())}."
        )
        # Then per-cert structural equality
        assert f_cert == c_cert, (
            f"Cert {i} ({f_cert.get('name')}): content drifted from fixture. "
            f"If change is intentional, regenerate the fixture per the test docstring."
        )


def test_retired_fixture_is_archived_and_not_canonical() -> None:
    assert RETIRED_FIXTURE.is_file()
    assert not FORMER_CANONICAL_FIXTURE.exists()
    lifecycle = json.loads(LIFECYCLE_RECORD.read_text(encoding="utf-8"))
    assert lifecycle["status"] == "SUPERSEDED_FOR_CURRENT_TOOLING"
    assert lifecycle["archive"]["historical_results_remain_valid"] is True
    assert lifecycle["replacement"]["path"].endswith(
        "fixtures/sprint13_seed_certificates-v044.json"
    )


def test_seed_set_size_is_ten():
    """The Sprint 13 seed set has exactly 10 compositions covering
    the regime lattice. If this drifts, update both the seed set
    constants in cli.py and the canonical fixture."""
    pairs = _seed_set_compositions(REPO)
    assert len(pairs) == 10, (
        f"Seed-set size = {len(pairs)}, expected 10. "
        f"Compositions: {[c.name for c, _ in pairs]}"
    )


def test_seed_set_regime_distribution():
    """Empirical Sprint 13 anchor: the seed set must cover the regime
    lattice diversely. Asserts at least one cert in each of the major
    regime states."""
    pairs = _seed_set_compositions(REPO)
    certs = [certify(comp, source_path=src) for comp, src in pairs]

    # At least one exact-conservative
    n_exact = sum(1 for c in certs if c.regime.is_exact_regime_conservative)
    assert n_exact >= 2, f"Need >=2 exact-conservative seeds, got {n_exact}"

    # At least one well-formed but not exact-conservative
    n_wf_not_exact = sum(
        1 for c in certs
        if c.regime.is_well_formed_for_fee
        and not c.regime.is_exact_regime_conservative
    )
    assert n_wf_not_exact >= 1, (
        f"Need >=1 well-formed-but-not-exact-conservative seed, got {n_wf_not_exact}"
    )

    # At least one ill-formed (negative control)
    n_ill = sum(1 for c in certs if not c.regime.is_well_formed_for_fee)
    assert n_ill == 1, (
        f"Expected exactly 1 ill-formed seed (negative control), got {n_ill}"
    )

    # At least one all-hidden (cycle family)
    n_all_hidden = sum(1 for c in certs if c.regime.is_all_hidden)
    assert n_all_hidden >= 1, f"Need >=1 all-hidden seed (cycle), got {n_all_hidden}"

    # At least one multi-server pair (now under diagnostic block in v1.0)
    n_multi_server = sum(
        1 for c in certs
        if c.diagnostic.get("cross_server_decomposition") is not None
    )
    assert n_multi_server >= 1, (
        f"Need >=1 multi-server seed (registry pair), got {n_multi_server}"
    )
