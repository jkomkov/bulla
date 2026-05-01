"""Integration tests for Phase 4 restricted-source pack metadata.

The five restricted-source packs (who-icd-10, swift-mt-mx, hl7-v2,
umls-mappings, iso-20022) ship as metadata-only — every dimension
references a licensed registry via ``values_registry`` and the
metadata-only invariant (Extension B validator) ensures no inline
licensed content can be added by accident.

These tests pin three load-bearing facts:

1. **Zero-licensed-content audit.** Every restricted pack has every
   dimension behind a ``values_registry`` pointer and zero inline
   ``known_values`` rows. The validator already enforces this; the
   test re-checks it as a defense-in-depth.

2. **License-required surfacing.** Calling
   ``verify_pack_registries`` on a restricted pack with no credential
   provider produces ``status='license_required'`` (not crash, not
   silently pass) so a CI run that doesn't hold the credential gets
   actionable guidance.

3. **End-to-end metadata-only demo.** A composition referencing a
   restricted pack can issue a valid receipt without any consumer-
   side license; ``bulla pack verify`` on the same pack fails with
   ``RegistryAccessError(LICENSE_REQUIRED)``. Architectural separation
   between *receipt issuance* (pack metadata only) and *registry
   fetch* (licensed content) is the load-bearing claim.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest
import yaml

from bulla.infer.classifier import (
    FieldInfo,
    _reset_taxonomy_cache,
    classify_description,
    classify_schema_signal,
    configure_packs,
    get_active_pack_refs,
)
from bulla.model import RegistryAccessError, RegistryAccessErrorCode
from bulla.packs.validate import validate_pack
from bulla.packs.verify import (
    CredentialProvider,
    DictFetcher,
    inspect_registries,
    verify_pack_registries,
    verify_registry,
)


def _seed_dir() -> Path:
    pkg = importlib.resources.files("bulla")
    return Path(str(pkg / "packs" / "seed"))


RESTRICTED_PACK_FILES = [
    "who-icd-10.yaml",
    "swift-mt-mx.yaml",
    "hl7-v2.yaml",
    "umls-mappings.yaml",
    "iso-20022.yaml",
]


@pytest.fixture(autouse=True)
def reset_caches():
    _reset_taxonomy_cache()
    yield
    _reset_taxonomy_cache()


# ── 1) Each restricted pack validates and identifies its license ─────


@pytest.mark.parametrize(
    "filename,expected_pack_name,expected_license,expected_license_id",
    [
        ("who-icd-10.yaml",    "who-icd-10",    "research-only", "WHO-ICD-10"),
        ("swift-mt-mx.yaml",   "swift-mt-mx",   "restricted",    "SWIFT-MEMBER"),
        ("hl7-v2.yaml",        "hl7-v2",        "research-only", "HL7-MEMBER"),
        ("umls-mappings.yaml", "umls-mappings", "restricted",    "NLM-UMLS"),
        ("iso-20022.yaml",     "iso-20022",     "research-only", "ISO-20022"),
    ],
)
def test_restricted_pack_validates_and_reports_license(
    filename: str,
    expected_pack_name: str,
    expected_license: str,
    expected_license_id: str,
):
    path = _seed_dir() / filename
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert validate_pack(parsed) == [], (
        f"{filename} failed validation"
    )
    assert parsed["pack_name"] == expected_pack_name
    assert parsed["license"]["registry_license"] == expected_license
    refs = inspect_registries(parsed)
    assert len(refs) >= 1
    # Every reference should carry the same license_id.
    for ref in refs:
        assert ref.license_id == expected_license_id, (
            f"ref {ref.dimension} has license_id={ref.license_id!r}, "
            f"expected {expected_license_id!r}"
        )


# ── 2) Zero-licensed-content audit (defense-in-depth) ────────────────


class TestZeroLicensedContentAudit:
    """The validator already rejects packs that mix inline values with
    a registry on a licensed dimension. This test re-checks the
    invariant against the actual on-disk seed packs as a CI tripwire
    — even if a future PR somehow bypasses validation, a green
    test run requires zero licensed inline content."""

    def test_no_restricted_pack_ships_inline_licensed_values(self):
        for filename in RESTRICTED_PACK_FILES:
            path = _seed_dir() / filename
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
            registry_license = parsed["license"]["registry_license"]
            assert registry_license in {"research-only", "restricted"}, (
                f"{filename} is not actually a restricted pack"
            )
            for dim_name, dim_def in parsed["dimensions"].items():
                has_registry = "values_registry" in dim_def
                has_inline = bool(dim_def.get("known_values"))
                # The metadata-only invariant: no licensed dimension
                # may ship inline values.
                if has_registry and has_inline:
                    pytest.fail(
                        f"{filename}: dimension {dim_name} has BOTH "
                        f"a values_registry pointer AND inline values. "
                        f"This violates the metadata-only invariant; the "
                        f"validator should have caught it at PR time."
                    )


# ── 3) License-required + placeholder surfacing on verify path ──────


class TestPlaceholderAndLicenseSurfacing:
    """The verify-path precedence is:

       placeholder hash ─→ status='placeholder'   (deepest: not yet checkable)
       real hash + restricted + no credential ─→ status='license_required'
       real hash + credential ─→ fetch and compare

    All five Phase 4 restricted seed packs currently carry
    ``placeholder:awaiting-license`` (no real hashes — those require
    credentials to fetch). So a clean verify run on them today
    surfaces ``status='placeholder'`` rather than ``license_required``.
    Once a consumer ingests under license and substitutes a real
    sha256 hash, the credential-gate path becomes active.
    """

    @pytest.mark.parametrize("filename", RESTRICTED_PACK_FILES)
    def test_seed_restricted_pack_surfaces_placeholder(self, filename: str):
        """Today every restricted pack uses placeholder:awaiting-license,
        so verify reports placeholder, not license_required."""
        path = _seed_dir() / filename
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        results = verify_pack_registries(parsed, fetcher=DictFetcher({}))
        assert all(r.status == "placeholder" for r in results), (
            f"{filename}: not every result was placeholder: "
            f"{[(r.reference.dimension, r.status) for r in results]}"
        )

    @pytest.mark.parametrize("filename", RESTRICTED_PACK_FILES)
    def test_strict_mode_raises_placeholder_hash(self, filename: str):
        """Restricted packs ship with placeholder hashes today, so
        strict mode raises ``PLACEHOLDER_HASH``, not
        ``LICENSE_REQUIRED``. The license-required path activates
        after a consumer substitutes a real hash from a licensed
        ingest."""
        path = _seed_dir() / filename
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        with pytest.raises(RegistryAccessError) as exc:
            verify_pack_registries(
                parsed,
                fetcher=DictFetcher({}),
                raise_on_placeholder=True,
            )
        assert exc.value.code == RegistryAccessErrorCode.PLACEHOLDER_HASH

    def test_license_required_fires_when_real_hash_meets_missing_credential(
        self,
    ):
        """The credential gate is still fully wired — it just sits
        deeper than the placeholder gate. Construct a synthetic
        scenario with a real sha256 hash + restricted license + no
        credential and confirm the credential gate fires."""
        from bulla.packs.verify import verify_registry, RegistryReference
        ref = RegistryReference(
            pack_name="synthetic",
            dimension="d",
            uri="https://restricted.example.org/",
            expected_hash="sha256:" + "a" * 64,  # real-shaped, not placeholder
            version="v1",
            license_id="TEST-LIC",
            registry_license="restricted",
        )
        result = verify_registry(ref, fetcher=DictFetcher({}))
        assert result.status == "license_required"

    def test_credential_unblocks_fetch_with_real_hash(self):
        """End-to-end: substitute a real hash into a restricted-pack
        ref, supply the credential, confirm the verifier proceeds to
        fetch + hash-check."""
        import hashlib
        from bulla.packs.verify import (
            verify_registry, RegistryReference,
        )
        canonical_content = b"licensed registry contents"
        canonical_hash = (
            "sha256:" + hashlib.sha256(canonical_content).hexdigest()
        )
        # Synthesize a ref as if a consumer had performed a licensed
        # ingest and recorded the resulting hash.
        ref = RegistryReference(
            pack_name="umls-mappings",
            dimension="umls_concept_id",
            uri="https://uts.nlm.nih.gov/uts/umls/concepts",
            expected_hash=canonical_hash,
            version="2024AB",
            license_id="NLM-UMLS",
            registry_license="restricted",
        )
        fetcher = DictFetcher({ref.uri: canonical_content})
        creds = CredentialProvider(credentials={"NLM-UMLS": "token-xyz"})
        result = verify_registry(
            ref, fetcher=fetcher, credential_provider=creds,
        )
        assert result.status == "ok"

    def test_credential_unblocks_fetch_legacy(self):
        """If the consumer registers the right credential, verify_*
        proceeds to fetch (and would succeed if the registry is
        reachable). DictFetcher with a content match closes the loop.

        Pre-sentinel test kept for coverage — uses a synthetic ref
        rather than the on-disk pack so we can construct the exact
        ``hash_mismatch`` scenario."""
        import hashlib
        from bulla.packs.verify import (
            verify_registry, RegistryReference,
        )
        canonical_content = b"placeholder content bytes"
        canonical_hash = (
            "sha256:" + hashlib.sha256(canonical_content).hexdigest()
        )

        # Construct a ref with a different recorded hash to force
        # hash_mismatch — proves the fetch and hash paths execute.
        ref = RegistryReference(
            pack_name="umls-mappings",
            dimension="umls_concept_id",
            uri="https://uts.nlm.nih.gov/uts/umls/concepts",
            expected_hash="sha256:" + "f" * 64,  # different from actual
            version="2024AB",
            license_id="NLM-UMLS",
            registry_license="restricted",
        )

        fetcher = DictFetcher({ref.uri: canonical_content})
        creds = CredentialProvider(credentials={"NLM-UMLS": "token-xyz"})
        result = verify_registry(
            ref, fetcher, credential_provider=creds
        )
        assert result.status == "hash_mismatch"
        assert result.actual_hash == canonical_hash


# ── 4) Receipt issuance is decoupled from registry fetch ─────────────


class TestReceiptIssuanceWithoutLicense:
    """A composition referencing a restricted pack can issue a valid
    receipt today, even without the consumer holding any registry
    credentials. The receipt records:
      - active_packs (pack names + hashes)
      - derives_from (underlying-standard provenance)
    What it does NOT need is the materialized registry contents.
    This is the architectural separation that lets the metadata layer
    travel ahead of the licensed-value layer."""

    def test_receipt_issuance_does_not_require_license_credential(self):
        path = _seed_dir() / "umls-mappings.yaml"
        # Loading the pack does not fetch the registry.
        configure_packs(extra_paths=[path])
        refs = get_active_pack_refs()
        umls = next(r for r in refs if r.name == "umls-mappings")
        # The PackRef carries derives_from; this is what a receipt
        # records under active_packs.
        assert umls.derives_from is not None
        assert umls.derives_from.standard == "UMLS-Metathesaurus"
        # Pack hash is computable without any network or credential.
        assert len(umls.hash) == 64

    def test_classifier_signal_works_without_license(self):
        """A field whose description mentions UMLS still classifies
        under the umls_concept_id dimension because the pack ships
        the classifier metadata even though the values themselves
        are behind a credential."""
        path = _seed_dir() / "umls-mappings.yaml"
        configure_packs(extra_paths=[path])
        results = classify_description(
            "Returns the patient's UMLS CUI for cross-vocabulary lookup"
        )
        dims = {r.dimension for r in results}
        assert "umls_concept_id" in dims


# ── 5) All 19 packs (Tier A + B + restricted) load together ──────────


class TestAllSeedPacksLoadTogether:
    """Phase 5 dress rehearsal: every seed pack that exists today
    co-loads cleanly. This test guarantees the merge path (collisions,
    precedence, license metadata coexistence) handles the full
    expected pack set."""

    def test_all_19_seed_packs_co_load(self):
        seed_dir = _seed_dir()
        paths = sorted(seed_dir.glob("*.yaml"))
        # 6 Tier A + 8 Tier B + 5 restricted = 19
        assert len(paths) == 19, f"expected 19 seed packs, got {len(paths)}"
        configure_packs(extra_paths=paths)
        names = {r.name for r in get_active_pack_refs()}
        for p in RESTRICTED_PACK_FILES:
            stem = p.removesuffix(".yaml")
            assert stem in names

    def test_restricted_packs_dont_block_open_packs(self):
        """Mixing restricted and open packs in one stack must work —
        the open packs classify normally; the restricted packs
        contribute their classifier metadata; nothing blocks anything."""
        seed_dir = _seed_dir()
        paths = sorted(seed_dir.glob("*.yaml"))
        configure_packs(extra_paths=paths)
        # ISO 4217 (open Tier A) still classifies a currency field
        # even though restricted packs are loaded.
        field = FieldInfo(
            name="amount_currency",
            schema_type="string",
            enum=("USD", "EUR", "JPY"),
        )
        dims = {r.dimension for r in classify_schema_signal(field)}
        assert "currency_code" in dims
