"""Tests for the placeholder-sentinel hash format.

Step #2 in the post-feedback sprint sequence introduced
``placeholder:<reason>`` as the not-yet-ingested sentinel for
``values_registry.hash``. The previous ``sha256:000...000`` form was
a valid-shaped hash that the verifier would silently treat as
"checked, mismatched" — worse than "not yet checkable" because it
masks the un-verified state behind a verification-failure signal.

This file pins:
  1. The validator rejects literal ``sha256:0...0``.
  2. The validator accepts well-formed ``sha256:<64-hex>`` and
     ``placeholder:<reason>``.
  3. The verifier returns ``status="placeholder"`` (not
     ``"hash_mismatch"``) when the pointer carries the sentinel.
  4. ``raise_on_placeholder=True`` raises ``RegistryAccessError``
     with code ``PLACEHOLDER_HASH``.
  5. The seed-pack corpus uses the sentinel correctly: every
     ``values_registry`` is either a real ``sha256:...`` or a
     ``placeholder:<reason>``; no literal-zero hashes leak through.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest
import yaml

from bulla.model import RegistryAccessError, RegistryAccessErrorCode
from bulla.packs.validate import validate_pack
from bulla.packs.verify import (
    DictFetcher,
    RegistryReference,
    inspect_registries,
    verify_pack_registries,
    verify_registry,
)


def _seed_dir() -> Path:
    pkg = importlib.resources.files("bulla")
    return Path(str(pkg / "packs" / "seed"))


# ── Validator: hash format ──────────────────────────────────────────


class TestHashFormatValidation:
    def _pack(self, hash_value: str) -> dict:
        return {
            "pack_name": "test",
            "dimensions": {
                "d": {
                    "description": "x",
                    "field_patterns": ["*"],
                    "values_registry": {
                        "uri": "https://example.org/",
                        "hash": hash_value,
                        "version": "v1",
                    },
                },
            },
        }

    def test_real_sha256_accepted(self):
        pack = self._pack("sha256:" + "a" * 64)
        assert validate_pack(pack) == []

    def test_placeholder_awaiting_ingest_accepted(self):
        pack = self._pack("placeholder:awaiting-ingest")
        assert validate_pack(pack) == []

    def test_placeholder_awaiting_license_accepted(self):
        pack = self._pack("placeholder:awaiting-license")
        assert validate_pack(pack) == []

    def test_placeholder_with_arbitrary_reason_accepted(self):
        pack = self._pack("placeholder:custom-reason-x")
        assert validate_pack(pack) == []

    def test_literal_zero_sha256_rejected(self):
        pack = self._pack("sha256:" + "0" * 64)
        errors = validate_pack(pack)
        assert any(
            "sha256:000" in e.lower() or "sha256:0..." in e
            or "placeholder" in e
            for e in errors
        )

    def test_short_sha256_rejected(self):
        pack = self._pack("sha256:abc")
        errors = validate_pack(pack)
        assert any("64 hex" in e or "sha256" in e for e in errors)

    def test_non_hex_sha256_rejected(self):
        pack = self._pack("sha256:" + "z" * 64)
        errors = validate_pack(pack)
        assert any("sha256" in e for e in errors)

    def test_unprefixed_string_rejected(self):
        pack = self._pack("just-a-string")
        errors = validate_pack(pack)
        assert any("sha256:" in e or "placeholder:" in e for e in errors)

    def test_empty_placeholder_reason_rejected(self):
        pack = self._pack("placeholder:")
        errors = validate_pack(pack)
        assert any("placeholder" in e and "reason" in e for e in errors)


# ── Verifier: placeholder status ─────────────────────────────────────


def _ref(hash_value: str, registry_license: str = "open") -> RegistryReference:
    return RegistryReference(
        pack_name="test",
        dimension="d",
        uri="https://example.org/",
        expected_hash=hash_value,
        version="v1",
        license_id="" if registry_license == "open" else "TEST-LIC",
        registry_license=registry_license,
    )


class TestPlaceholderVerificationStatus:
    def test_placeholder_returns_status_placeholder_not_mismatch(self):
        """The load-bearing claim: a placeholder-hash pointer must
        produce status='placeholder', distinct from hash_mismatch."""
        ref = _ref("placeholder:awaiting-ingest")
        result = verify_registry(ref, fetcher=DictFetcher({}))
        assert result.status == "placeholder"
        assert result.status != "hash_mismatch"
        assert "awaiting-ingest" in result.detail

    def test_placeholder_does_not_attempt_fetch(self):
        """The placeholder short-circuits before any fetch — the
        empty fetcher would otherwise produce 'unavailable'."""
        ref = _ref("placeholder:awaiting-license", registry_license="restricted")
        result = verify_registry(ref, fetcher=DictFetcher({}))
        # Without short-circuit, the missing credential would produce
        # license_required; with short-circuit, placeholder takes
        # precedence (it's structurally not-yet-checkable, deeper than
        # the credential question).
        assert result.status == "placeholder"

    def test_strict_mode_raises_with_placeholder_hash_code(self):
        ref = _ref("placeholder:awaiting-ingest")
        with pytest.raises(RegistryAccessError) as exc:
            verify_registry(
                ref, fetcher=DictFetcher({}),
                raise_on_placeholder=True,
            )
        assert exc.value.code == RegistryAccessErrorCode.PLACEHOLDER_HASH

    def test_real_sha256_skips_placeholder_path(self):
        """A pointer with a real sha256 hash continues to the fetch
        path — the placeholder short-circuit only fires on the
        sentinel format."""
        import hashlib
        content = b"canonical content"
        real_hash = "sha256:" + hashlib.sha256(content).hexdigest()
        ref = _ref(real_hash)
        result = verify_registry(
            ref,
            fetcher=DictFetcher({"https://example.org/": content}),
        )
        assert result.status == "ok"


# ── Seed-pack corpus invariant ───────────────────────────────────────


class TestSeedCorpusHashFormatInvariant:
    """Every values_registry hash across all 19 seed packs must be
    either a real sha256:<64-hex> or a placeholder:<reason>. No
    literal-zero hashes, no other formats. This is the load-bearing
    audit that the post-feedback fix actually landed."""

    def test_no_literal_zero_hashes_in_seed_corpus(self):
        offenders = []
        for path in _seed_dir().glob("*.yaml"):
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
            for dim_name, dim_def in parsed.get("dimensions", {}).items():
                reg = dim_def.get("values_registry")
                if not isinstance(reg, dict):
                    continue
                h = reg.get("hash", "")
                if h == "sha256:" + "0" * 64:
                    offenders.append(f"{path.name}::{dim_name}")
        assert not offenders, (
            f"literal sha256:0...0 found in: {offenders}"
        )

    def test_every_hash_is_well_formed(self):
        bad = []
        for path in _seed_dir().glob("*.yaml"):
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
            for dim_name, dim_def in parsed.get("dimensions", {}).items():
                reg = dim_def.get("values_registry")
                if not isinstance(reg, dict):
                    continue
                h = reg.get("hash", "")
                ok = (
                    h.startswith("placeholder:")
                    or (h.startswith("sha256:") and len(h) == 71)
                )
                if not ok:
                    bad.append(f"{path.name}::{dim_name}: {h!r}")
        assert not bad, f"malformed hashes: {bad}"

    def test_at_least_some_real_hashes_present(self):
        """Step #2 fetched real hashes for several open standards;
        confirm at least 4 real sha256 hashes survive in the
        committed pack files."""
        real_count = 0
        for path in _seed_dir().glob("*.yaml"):
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
            for dim_def in parsed.get("dimensions", {}).values():
                reg = dim_def.get("values_registry")
                if isinstance(reg, dict):
                    h = reg.get("hash", "")
                    if h.startswith("sha256:") and len(h) == 71:
                        real_count += 1
        assert real_count >= 4, (
            f"only {real_count} real sha256 hashes in seed packs; "
            f"expected ≥ 4 (UCUM, NAICS, ISO 639, IANA MIME, FHIR R4, FHIR R5)"
        )

    def test_restricted_packs_use_awaiting_license(self):
        """All 5 restricted packs must use placeholder:awaiting-license
        (not awaiting-ingest) — the reason field is what tells a
        consumer they need to obtain a license."""
        restricted_files = {
            "who-icd-10.yaml", "swift-mt-mx.yaml", "hl7-v2.yaml",
            "umls-mappings.yaml", "iso-20022.yaml",
        }
        for filename in restricted_files:
            path = _seed_dir() / filename
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
            for dim_name, dim_def in parsed["dimensions"].items():
                reg = dim_def.get("values_registry")
                assert reg is not None, (
                    f"{filename}: every dimension on a restricted "
                    f"pack must use values_registry"
                )
                h = reg["hash"]
                assert h == "placeholder:awaiting-license", (
                    f"{filename}::{dim_name}: hash={h!r} "
                    f"(expected 'placeholder:awaiting-license')"
                )


# ── batch verify_pack_registries handles placeholder cleanly ─────────


class TestBatchVerifyWithPlaceholders:
    def test_seed_pack_verify_surfaces_placeholder_status(self):
        """Verifying a seed pack with placeholder hashes must produce
        clean status='placeholder' results — not unavailable, not
        hash_mismatch, not license_required."""
        path = _seed_dir() / "ucum.yaml"
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        # UCUM has a real hash now (post-fetch), so this test uses an
        # explicitly-placeholder pack instead.
        path = _seed_dir() / "fix-4.4.yaml"  # still placeholder
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        results = verify_pack_registries(parsed, fetcher=DictFetcher({}))
        # FIX is open + still on placeholder.
        assert all(r.status == "placeholder" for r in results), (
            f"expected all placeholder, got {[(r.reference.dimension, r.status) for r in results]}"
        )

    def test_strict_mode_aborts_on_first_placeholder(self):
        path = _seed_dir() / "fix-4.4.yaml"
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        with pytest.raises(RegistryAccessError) as exc:
            verify_pack_registries(
                parsed,
                fetcher=DictFetcher({}),
                raise_on_placeholder=True,
            )
        assert exc.value.code == RegistryAccessErrorCode.PLACEHOLDER_HASH
