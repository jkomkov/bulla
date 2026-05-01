"""Tests for Extension B: values_registry dimension pointer + verify pipeline.

Extension B scope (Standards Ingestion Sprint, Phase 1):

- ``values_registry: { uri, hash, version, license_id }`` on a dimension.
- Validator accepts the new field; rejects malformed pointers.
- Metadata-only invariant: a pack with restricted/research-only license
  CANNOT ship inline ``known_values`` on dimensions that also have a
  ``values_registry`` pointer.
- Pack hash canonicalization: inline ``known_values`` on a dimension with
  ``values_registry`` are stripped before hashing, so authors can curate
  inline documentation without producing pack-hash drift.
- ``inspect_registries()`` walks a parsed pack and returns one
  ``RegistryReference`` per pointer found.
- ``verify_registry()`` checks credentials (raising ``RegistryAccessError``
  when a restricted pack lacks credentials), fetches via a
  ``RegistryFetcher`` interface, and compares the fetched-content hash
  to the pointer's expected hash.
"""

from __future__ import annotations

import hashlib

import pytest

from bulla.infer.classifier import _hash_pack, _canonicalize_pack_for_hash
from bulla.model import RegistryAccessError, RegistryAccessErrorCode
from bulla.packs.validate import validate_pack
from bulla.packs.verify import (
    CredentialProvider,
    DictFetcher,
    RegistryReference,
    RegistryVerification,
    inspect_registries,
    verify_pack_registries,
    verify_registry,
)


# ── values_registry validator ────────────────────────────────────────


class TestValuesRegistryValidation:

    def _pack_with_registry(
        self, registry: dict | object, **dim_overrides
    ) -> dict:
        dim_def: dict = {
            "description": "x",
            "field_patterns": ["*_x"],
        }
        dim_def["values_registry"] = registry
        dim_def.update(dim_overrides)
        return {
            "pack_name": "test",
            "dimensions": {"d": dim_def},
        }

    def test_valid_open_registry(self):
        pack = self._pack_with_registry({
            "uri": "https://example.org/codes.json",
            "hash": "sha256:" + "a" * 64,
            "version": "2024-10",
        })
        assert validate_pack(pack) == []

    def test_valid_with_license_id(self):
        pack = self._pack_with_registry({
            "uri": "https://uts.nlm.nih.gov/uts/umls",
            "hash": "sha256:" + "b" * 64,
            "version": "2024AB",
            "license_id": "NLM-UMLS",
        })
        assert validate_pack(pack) == []

    def test_missing_uri_rejected(self):
        pack = self._pack_with_registry({
            "hash": "sha256:" + "a" * 64,
            "version": "2024-10",
        })
        errors = validate_pack(pack)
        assert any("values_registry" in e and "uri" in e for e in errors)

    def test_missing_hash_rejected(self):
        pack = self._pack_with_registry({
            "uri": "https://example.org/",
            "version": "2024-10",
        })
        errors = validate_pack(pack)
        assert any("values_registry" in e and "hash" in e for e in errors)

    def test_missing_version_rejected(self):
        pack = self._pack_with_registry({
            "uri": "https://example.org/",
            "hash": "sha256:" + "a" * 64,
        })
        errors = validate_pack(pack)
        assert any("values_registry" in e and "version" in e for e in errors)

    def test_non_string_uri_rejected(self):
        pack = self._pack_with_registry({
            "uri": 42,
            "hash": "sha256:" + "a" * 64,
            "version": "2024-10",
        })
        errors = validate_pack(pack)
        assert any(
            "values_registry" in e and "uri" in e and "string" in e
            for e in errors
        )

    def test_non_string_license_id_rejected(self):
        pack = self._pack_with_registry({
            "uri": "https://example.org/",
            "hash": "sha256:" + "a" * 64,
            "version": "2024-10",
            "license_id": ["not", "a", "string"],
        })
        errors = validate_pack(pack)
        assert any(
            "values_registry" in e and "license_id" in e
            for e in errors
        )

    def test_registry_not_a_mapping(self):
        pack = self._pack_with_registry("not-a-mapping")
        errors = validate_pack(pack)
        assert any(
            "values_registry" in e and "mapping" in e
            for e in errors
        )


# ── Metadata-only invariant ──────────────────────────────────────────


class TestMetadataOnlyInvariant:
    """A pack with restricted/research-only license CANNOT ship inline
    known_values on a dimension that also has a values_registry pointer.
    This is the load-bearing single line of defense against accidental
    redistribution of licensed content via PR review oversight."""

    def _restricted_pack(
        self,
        *,
        registry_license: str,
        with_inline: bool,
        with_registry: bool,
    ) -> dict:
        dim_def: dict = {
            "description": "x",
            "field_patterns": ["*_x"],
        }
        if with_inline:
            dim_def["known_values"] = ["A", "B", "C"]
        if with_registry:
            dim_def["values_registry"] = {
                "uri": "https://restricted.example.org/codes.json",
                "hash": "sha256:" + "a" * 64,
                "version": "2024",
                "license_id": "RESTRICTED-X",
            }
        return {
            "pack_name": "test",
            "license": {
                "registry_license": registry_license,
            },
            "dimensions": {"d": dim_def},
        }

    def test_restricted_pack_with_inline_only_is_allowed(self):
        """A restricted pack may have inline values on a dimension that
        does NOT have a values_registry. The invariant fires only on
        the *coexistence* — inline + registry on the same dimension."""
        pack = self._restricted_pack(
            registry_license="restricted",
            with_inline=True,
            with_registry=False,
        )
        assert validate_pack(pack) == []

    def test_restricted_pack_with_registry_only_is_allowed(self):
        pack = self._restricted_pack(
            registry_license="restricted",
            with_inline=False,
            with_registry=True,
        )
        assert validate_pack(pack) == []

    def test_restricted_pack_with_inline_AND_registry_REJECTED(self):
        pack = self._restricted_pack(
            registry_license="restricted",
            with_inline=True,
            with_registry=True,
        )
        errors = validate_pack(pack)
        assert any(
            "metadata-only invariant" in e or "MUST NOT coexist" in e
            for e in errors
        )

    def test_research_only_pack_with_inline_AND_registry_REJECTED(self):
        pack = self._restricted_pack(
            registry_license="research-only",
            with_inline=True,
            with_registry=True,
        )
        errors = validate_pack(pack)
        assert any(
            "metadata-only invariant" in e or "MUST NOT coexist" in e
            for e in errors
        )

    def test_open_pack_with_inline_AND_registry_is_allowed(self):
        """An ``open`` registry license is the documentation-coexistence
        case: inline serves as examples while the registry is
        authoritative. The pack hash strips the inline values so
        curation doesn't produce drift, but the validator does not
        reject the coexistence."""
        pack = self._restricted_pack(
            registry_license="open",
            with_inline=True,
            with_registry=True,
        )
        assert validate_pack(pack) == []


# ── Pack hash canonicalization ───────────────────────────────────────


class TestPackHashCanonicalization:
    """``_hash_pack`` strips inline ``known_values`` from any dimension
    with ``values_registry`` before hashing, so authors can curate the
    inline documentation list without producing pack-hash drift.

    Dimensions without ``values_registry`` are completely unaffected —
    the existing behavior for every base/community/financial pack is
    preserved."""

    def test_pack_without_registry_unchanged(self):
        """A pack without any values_registry pointers must hash
        identically before and after the canonicalization step (the
        canonicalization is supposed to be a no-op for these)."""
        pack = {
            "pack_name": "p",
            "dimensions": {
                "d": {
                    "description": "x",
                    "field_patterns": ["*_x"],
                    "known_values": ["a", "b", "c"],
                }
            },
        }
        canonical = _canonicalize_pack_for_hash(pack)
        assert canonical is pack or canonical == pack
        # Hash is stable across two runs.
        h1 = _hash_pack(pack)
        h2 = _hash_pack(pack)
        assert h1 == h2

    def test_inline_values_stripped_when_registry_present(self):
        """Two packs that differ ONLY in the inline ``known_values`` on
        a registry-backed dimension must hash identically — the
        registry pointer is the binding object, the inline list is
        documentation."""
        base = {
            "pack_name": "p",
            "dimensions": {
                "d": {
                    "description": "x",
                    "field_patterns": ["*_x"],
                    "values_registry": {
                        "uri": "https://r/",
                        "hash": "sha256:" + "a" * 64,
                        "version": "v1",
                    },
                    "known_values": ["only-USD"],
                }
            },
        }
        curated = {
            "pack_name": "p",
            "dimensions": {
                "d": {
                    "description": "x",
                    "field_patterns": ["*_x"],
                    "values_registry": {
                        "uri": "https://r/",
                        "hash": "sha256:" + "a" * 64,
                        "version": "v1",
                    },
                    "known_values": ["USD", "EUR", "JPY", "GBP"],
                }
            },
        }
        assert _hash_pack(base) == _hash_pack(curated)

    def test_registry_field_changes_hash(self):
        """The registry pointer object itself participates in the hash —
        if the URI, hash, version, or license_id changes, the pack
        hash MUST change. This is the binding mechanism."""
        a = {
            "pack_name": "p",
            "dimensions": {
                "d": {
                    "description": "x",
                    "field_patterns": ["*_x"],
                    "values_registry": {
                        "uri": "https://r/",
                        "hash": "sha256:" + "a" * 64,
                        "version": "v1",
                    },
                }
            },
        }
        b = {
            "pack_name": "p",
            "dimensions": {
                "d": {
                    "description": "x",
                    "field_patterns": ["*_x"],
                    "values_registry": {
                        "uri": "https://r/",
                        "hash": "sha256:" + "a" * 64,
                        "version": "v2",  # only difference
                    },
                }
            },
        }
        assert _hash_pack(a) != _hash_pack(b)

    def test_canonicalization_does_not_mutate_input(self):
        pack = {
            "pack_name": "p",
            "dimensions": {
                "d": {
                    "description": "x",
                    "field_patterns": ["*_x"],
                    "values_registry": {
                        "uri": "https://r/",
                        "hash": "sha256:" + "a" * 64,
                        "version": "v1",
                    },
                    "known_values": ["doc-example"],
                }
            },
        }
        before = pack["dimensions"]["d"].get("known_values")
        _canonicalize_pack_for_hash(pack)
        after = pack["dimensions"]["d"].get("known_values")
        assert before == after  # original dict was not mutated


# ── inspect_registries() ─────────────────────────────────────────────


class TestInspectRegistries:

    def test_no_registries_returns_empty_list(self):
        pack = {
            "pack_name": "p",
            "dimensions": {
                "d": {"description": "x", "field_patterns": ["*_x"]},
            },
        }
        assert inspect_registries(pack) == []

    def test_single_registry_pointer(self):
        pack = {
            "pack_name": "fhir-r4",
            "license": {"registry_license": "open"},
            "dimensions": {
                "fhir_resource_type": {
                    "description": "FHIR resource type",
                    "field_patterns": ["resourceType"],
                    "values_registry": {
                        "uri": "https://hl7.org/fhir/R4/resource-types.json",
                        "hash": "sha256:" + "f" * 64,
                        "version": "R4",
                    },
                }
            },
        }
        refs = inspect_registries(pack)
        assert len(refs) == 1
        ref = refs[0]
        assert ref.pack_name == "fhir-r4"
        assert ref.dimension == "fhir_resource_type"
        assert ref.uri == "https://hl7.org/fhir/R4/resource-types.json"
        assert ref.expected_hash == "sha256:" + "f" * 64
        assert ref.version == "R4"
        assert ref.license_id == ""
        assert ref.registry_license == "open"

    def test_multiple_registries_with_license_propagation(self):
        pack = {
            "pack_name": "umls-mappings",
            "license": {
                "registry_license": "restricted",
                "spdx_id": "Proprietary",
            },
            "dimensions": {
                "snomed_to_icd10": {
                    "description": "SNOMED CT to ICD-10 mapping",
                    "field_patterns": ["*"],
                    "values_registry": {
                        "uri": "https://uts.nlm.nih.gov/uts/snomed-icd10",
                        "hash": "sha256:" + "1" * 64,
                        "version": "2024AB",
                        "license_id": "NLM-UMLS",
                    },
                },
                "icd10_to_loinc": {
                    "description": "ICD-10 to LOINC mapping",
                    "field_patterns": ["*"],
                    "values_registry": {
                        "uri": "https://uts.nlm.nih.gov/uts/icd10-loinc",
                        "hash": "sha256:" + "2" * 64,
                        "version": "2024AB",
                        "license_id": "NLM-UMLS",
                    },
                },
            },
        }
        refs = inspect_registries(pack)
        assert len(refs) == 2
        # Pack-level registry_license propagates to every reference.
        for ref in refs:
            assert ref.registry_license == "restricted"
            assert ref.license_id == "NLM-UMLS"

    def test_dimensions_without_registry_are_skipped(self):
        pack = {
            "pack_name": "mixed",
            "dimensions": {
                "with_registry": {
                    "description": "x",
                    "field_patterns": ["*"],
                    "values_registry": {
                        "uri": "https://r/",
                        "hash": "sha256:" + "a" * 64,
                        "version": "v1",
                    },
                },
                "without_registry": {
                    "description": "y",
                    "field_patterns": ["*"],
                    "known_values": ["a", "b"],
                },
            },
        }
        refs = inspect_registries(pack)
        assert len(refs) == 1
        assert refs[0].dimension == "with_registry"


# ── verify_registry() ────────────────────────────────────────────────


def _ref(
    *,
    license_id: str = "",
    registry_license: str = "open",
    expected_hash: str | None = None,
    uri: str = "https://r/codes.json",
) -> RegistryReference:
    if expected_hash is None:
        expected_hash = hashlib.sha256(b"canonical content").hexdigest()
    return RegistryReference(
        pack_name="p",
        dimension="d",
        uri=uri,
        expected_hash=expected_hash,
        version="v1",
        license_id=license_id,
        registry_license=registry_license,
    )


class TestVerifyRegistry:

    def test_open_registry_ok(self):
        content = b"canonical content"
        # Use the canonical prefixed form (validator-required for new
        # packs; verifier tolerates either for backward compat).
        ref = _ref(
            expected_hash="sha256:" + hashlib.sha256(content).hexdigest()
        )
        fetcher = DictFetcher({ref.uri: content})
        result = verify_registry(ref, fetcher)
        assert result.status == "ok"
        assert result.actual_hash == ref.expected_hash

    def test_open_registry_hash_mismatch(self):
        ref = _ref(expected_hash="sha256:" + "0" * 64)
        fetcher = DictFetcher({ref.uri: b"different content"})
        result = verify_registry(ref, fetcher)
        assert result.status == "hash_mismatch"
        assert result.actual_hash != ref.expected_hash
        assert "expected" in result.detail

    def test_open_registry_unavailable(self):
        ref = _ref()
        fetcher = DictFetcher({})  # empty
        result = verify_registry(ref, fetcher)
        assert result.status == "unavailable"

    def test_restricted_registry_without_credential_returns_license_required(
        self,
    ):
        ref = _ref(
            license_id="NLM-UMLS",
            registry_license="restricted",
        )
        fetcher = DictFetcher({})
        # No credential provider supplied — license_required surfaces
        # without raising in default (batch-friendly) mode.
        result = verify_registry(ref, fetcher)
        assert result.status == "license_required"
        assert "NLM-UMLS" in result.detail

    def test_restricted_registry_strict_raises(self):
        ref = _ref(
            license_id="NLM-UMLS",
            registry_license="restricted",
        )
        fetcher = DictFetcher({})
        with pytest.raises(RegistryAccessError) as exc:
            verify_registry(
                ref, fetcher, raise_on_license_required=True
            )
        assert exc.value.code == RegistryAccessErrorCode.LICENSE_REQUIRED
        assert exc.value.license_id == "NLM-UMLS"

    def test_restricted_registry_with_credential_proceeds(self):
        content = b"licensed content"
        ref = _ref(
            license_id="NLM-UMLS",
            registry_license="restricted",
            expected_hash=hashlib.sha256(content).hexdigest(),
        )
        fetcher = DictFetcher({ref.uri: content})
        creds = CredentialProvider(credentials={"NLM-UMLS": "token-abc"})
        result = verify_registry(ref, fetcher, credential_provider=creds)
        assert result.status == "ok"

    def test_research_only_treated_like_restricted(self):
        ref = _ref(
            license_id="WHO-ICD-10",
            registry_license="research-only",
        )
        fetcher = DictFetcher({})
        result = verify_registry(ref, fetcher)
        assert result.status == "license_required"

    def test_open_registry_ignores_credential_provider(self):
        """An open registry never hits the credential gate, even when
        a provider is supplied with no entry."""
        content = b"open data"
        ref = _ref(expected_hash=hashlib.sha256(content).hexdigest())
        fetcher = DictFetcher({ref.uri: content})
        creds = CredentialProvider(credentials={})  # empty
        result = verify_registry(ref, fetcher, credential_provider=creds)
        assert result.status == "ok"

    def test_restricted_with_empty_credential_string(self):
        """An explicitly-empty credential token is treated as no
        credential — guards against silent passthrough."""
        ref = _ref(
            license_id="NLM-UMLS",
            registry_license="restricted",
        )
        fetcher = DictFetcher({})
        creds = CredentialProvider(credentials={"NLM-UMLS": ""})
        result = verify_registry(ref, fetcher, credential_provider=creds)
        assert result.status == "license_required"


class TestVerifyPackRegistries:

    def test_batch_verification_mixed_open_and_restricted(self):
        open_content = b"open data"
        rest_content = b"licensed data"
        pack = {
            "pack_name": "mixed",
            "license": {"registry_license": "open"},
            "dimensions": {
                "open_dim": {
                    "description": "x",
                    "field_patterns": ["*"],
                    "values_registry": {
                        "uri": "https://open/",
                        "hash": hashlib.sha256(open_content).hexdigest(),
                        "version": "v1",
                    },
                },
            },
        }
        # Override one ref to be restricted to test mixed handling.
        rest_pack = {
            "pack_name": "rest",
            "license": {"registry_license": "restricted"},
            "dimensions": {
                "rest_dim": {
                    "description": "y",
                    "field_patterns": ["*"],
                    "values_registry": {
                        "uri": "https://restricted/",
                        "hash": hashlib.sha256(rest_content).hexdigest(),
                        "version": "v1",
                        "license_id": "NLM-UMLS",
                    },
                },
            },
        }
        fetcher = DictFetcher({
            "https://open/": open_content,
            "https://restricted/": rest_content,
        })

        # Verify the open pack with no credentials: should pass.
        results_open = verify_pack_registries(pack, fetcher)
        assert all(r.status == "ok" for r in results_open)

        # Verify the restricted pack without credentials: license_required.
        results_rest = verify_pack_registries(rest_pack, fetcher)
        assert all(r.status == "license_required" for r in results_rest)

        # With credentials: all pass.
        creds = CredentialProvider(credentials={"NLM-UMLS": "token"})
        results_rest_ok = verify_pack_registries(
            rest_pack, fetcher, credential_provider=creds,
        )
        assert all(r.status == "ok" for r in results_rest_ok)

    def test_only_filter_skips_restricted(self):
        """``only`` filter lets a CI run skip restricted registries
        when no credentials are available, without surfacing
        license_required noise."""
        pack = {
            "pack_name": "p",
            "license": {"registry_license": "restricted"},
            "dimensions": {
                "d": {
                    "description": "x",
                    "field_patterns": ["*"],
                    "values_registry": {
                        "uri": "https://r/",
                        "hash": "sha256:" + "a" * 64,
                        "version": "v1",
                        "license_id": "X",
                    },
                },
            },
        }
        fetcher = DictFetcher({})
        results = verify_pack_registries(
            pack,
            fetcher,
            only=lambda ref: ref.registry_license == "open",
        )
        assert len(results) == 1
        assert results[0].status == "skipped"
