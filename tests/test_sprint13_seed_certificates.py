"""Sprint 13/14 — canonical seed-set certificate regression.

Re-generates `papers/composition-doctrine/sprint13_seed_certificates.json`
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

Re-generate the fixture (when intentional schema drift):
    PYTHONPATH=bulla/src python3.11 -m bulla certify --seed-set --format json \
        --output papers/composition-doctrine/sprint13_seed_certificates.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bulla" / "src"))

from bulla.cli import _seed_set_compositions  # noqa: E402
from bulla.certificate import certify, to_dict  # noqa: E402

CANONICAL_FIXTURE = (
    REPO / "papers" / "composition-doctrine" / "sprint13_seed_certificates.json"
)


def _strip_nondeterministic(certs: list[dict]) -> list[dict]:
    """Remove version-dependent and time-dependent fields so byte-
    identity can be asserted across runs and across release bumps.

    Fields stripped:
      - timestamp (clock-dependent)
      - bulla_version (release-dependent)
      - certificate_content_hash (depends on bulla_version via method block)
      - method (versioned strings inside)
      - attestation_hash (currently None; future signed envelopes will populate)
      - receipt_hash (currently None; future operational receipts will populate)
    """
    out = []
    for c in certs:
        c2 = dict(c)
        c2.pop("timestamp", None)
        c2.pop("bulla_version", None)
        c2.pop("certificate_content_hash", None)
        c2.pop("certificate_hash", None)  # legacy v0 name; harmless
        c2.pop("method", None)
        c2.pop("attestation_hash", None)
        c2.pop("receipt_hash", None)
        out.append(c2)
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
    if not CANONICAL_FIXTURE.exists():
        pytest.fail(
            f"Canonical fixture missing: {CANONICAL_FIXTURE}. "
            f"Generate it via `bulla certify --seed-set --format json --output ...`."
        )

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
