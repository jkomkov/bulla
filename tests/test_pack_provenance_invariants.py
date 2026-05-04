"""Phase 3 (Dimension Pack Enhancement Sprint) provenance invariants.

After Phase 1 closed the open-registry hash gaps and Phase 3 wired
``derives_from.source_hash`` for every fetchable open pack, the
provenance triple — ``derives_from.source_uri``,
``derives_from.source_hash``, ``values_registry.uri/hash`` — must
stay internally consistent across the whole seed corpus and against
``calibration/data/registry-hashes.json``.

These tests pin five invariants:

1. **uri-bound source_hash**: when ``derives_from.source_uri ==
   values_registry.uri`` and the pack carries a real
   ``values_registry.hash``, ``derives_from.source_hash`` must equal
   that hash. They bind to the same artifact; they cannot disagree.
2. **registry-hashes round-trip**: every entry in
   ``calibration/data/registry-hashes.json`` with status=ok must
   appear in the corresponding seed pack's ``values_registry.hash``.
   The hash file is the upstream source of truth; the seed packs
   must reflect it.
3. **open-pack hash coverage**: an ``open`` registry-license pack
   that carries a ``values_registry`` pointer must have a real
   ``sha256:...`` hash. Open registries are fetchable; a stuck-on-
   placeholder open pack signals an unclosed Phase-1 gap.
4. **restricted-pack placeholder discipline**: every restricted /
   research-only pack's registry hash must be
   ``placeholder:awaiting-license``. Real hashes don't belong on
   restricted pointers — license-gated values must remain behind
   their license.
5. **provenance consistency at load time**: when a pack carries a
   ``derives_from.source_hash``, that hash must round-trip onto the
   loaded ``PackRef.derives_from.source_hash``.
"""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path

import pytest
import yaml

from bulla.infer.classifier import _reset_taxonomy_cache, load_pack_stack


def _seed_dir() -> Path:
    pkg = importlib.resources.files("bulla")
    return Path(str(pkg / "packs" / "seed"))


def _calibration_dir() -> Path:
    pkg = importlib.resources.files("bulla")
    return Path(str(pkg / ".." / ".." / "calibration")).resolve()


def _load_seed_packs() -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    for p in sorted(_seed_dir().glob("*.yaml")):
        out.append((p, yaml.safe_load(p.read_text(encoding="utf-8"))))
    return out


# ── Invariant 1: source_hash binds to source_uri ─────────────────────


class TestSourceHashUriBinding:
    """``derives_from.source_hash`` claims to bind to
    ``derives_from.source_uri``. When ``source_uri`` matches a
    ``values_registry.uri`` on the same pack and that registry has a
    real hash, the two hashes must agree (they describe the same
    fetched artifact)."""

    def test_when_source_uri_matches_values_registry_uri_hashes_agree(self):
        offenders: list[str] = []
        for path, pack in _load_seed_packs():
            df = pack.get("derives_from") or {}
            src_uri = df.get("source_uri")
            src_hash = df.get("source_hash")
            if not src_uri or not src_hash:
                continue
            for dim_name, dim in (pack.get("dimensions") or {}).items():
                if not isinstance(dim, dict):
                    continue
                reg = dim.get("values_registry")
                if not isinstance(reg, dict):
                    continue
                if reg.get("uri") != src_uri:
                    continue
                reg_hash = reg.get("hash", "")
                # Only assert when the registry hash is REAL. If the
                # registry hash is a placeholder, the source_hash is
                # whatever upstream chose — usually the same placeholder.
                if not reg_hash.startswith("sha256:"):
                    continue
                if src_hash != reg_hash:
                    offenders.append(
                        f"{path.name}::{dim_name}: "
                        f"derives_from.source_hash={src_hash[:24]}... "
                        f"!= values_registry.hash={reg_hash[:24]}... "
                        f"(uri={src_uri})"
                    )
        assert not offenders, (
            "source_hash and values_registry.hash disagree on packs "
            f"where source_uri matches values_registry.uri: {offenders}"
        )


# ── Invariant 2: registry-hashes.json round-trip ─────────────────────


class TestRegistryHashesFileMatchesSeedCorpus:
    """``calibration/data/registry-hashes.json`` is the canonical
    record of what we fetched for which (pack, dimension, version)
    tuple. Every ok entry must round-trip into the seed pack."""

    def _hash_file(self) -> Path:
        return _calibration_dir() / "data" / "registry-hashes.json"

    def test_every_ok_entry_lands_in_seed_pack(self):
        hash_file = self._hash_file()
        if not hash_file.exists():
            pytest.skip(
                "registry-hashes.json missing; run "
                "scripts/standards-ingest/compute_real_hashes.py"
            )
        data = json.loads(hash_file.read_text(encoding="utf-8"))
        seeds = {p.stem: pack for p, pack in _load_seed_packs()}

        offenders: list[str] = []
        for entry in data.get("entries", []):
            if entry.get("status") != "ok":
                continue
            pack_name = entry["pack"]
            dim_name = entry["dimension"]
            expected = entry["hash"]
            seed = seeds.get(pack_name)
            if seed is None:
                offenders.append(
                    f"hash entry pack={pack_name!r} not found in seed corpus"
                )
                continue
            dim = (seed.get("dimensions") or {}).get(dim_name)
            if not isinstance(dim, dict):
                offenders.append(
                    f"{pack_name}::{dim_name} dimension missing on seed pack"
                )
                continue
            reg = dim.get("values_registry")
            if not isinstance(reg, dict):
                offenders.append(
                    f"{pack_name}::{dim_name} has no values_registry pointer"
                )
                continue
            if reg.get("hash") != expected:
                offenders.append(
                    f"{pack_name}::{dim_name} hash drift: "
                    f"seed={reg.get('hash', '')[:24]}... "
                    f"vs registry-hashes={expected[:24]}..."
                )
        assert not offenders, (
            "registry-hashes.json entries did not round-trip into seed "
            f"packs: {offenders}"
        )


# ── Invariant 3: open packs must have real hashes (no placeholders) ─


class TestOpenPackHashCoverage:
    """An ``open`` pack carrying a ``values_registry`` pointer must
    have a real sha256 hash. ``placeholder:awaiting-ingest`` on an
    open pack signals an unclosed hash gap from Phase 1."""

    def test_no_open_pack_left_on_placeholder(self):
        offenders: list[str] = []
        for path, pack in _load_seed_packs():
            license_block = pack.get("license") or {}
            if license_block.get("registry_license") != "open":
                continue
            for dim_name, dim in (pack.get("dimensions") or {}).items():
                if not isinstance(dim, dict):
                    continue
                reg = dim.get("values_registry")
                if not isinstance(reg, dict):
                    continue
                h = reg.get("hash", "")
                if h.startswith("placeholder:"):
                    offenders.append(
                        f"{path.name}::{dim_name} = {h!r}"
                    )
        assert not offenders, (
            "open packs still on placeholder hash (Phase 1 gap): "
            f"{offenders}"
        )


# ── Invariant 4: restricted packs must use awaiting-license ──────────


class TestRestrictedPackPlaceholderDiscipline:
    """The metadata-only invariant for restricted packs: every
    ``values_registry`` on a research-only or restricted pack uses
    ``placeholder:awaiting-license``. Real hashes don't belong on
    restricted pointers — the licensed values stay behind the license."""

    def test_restricted_packs_never_carry_real_hashes(self):
        offenders: list[str] = []
        for path, pack in _load_seed_packs():
            license_block = pack.get("license") or {}
            rl = license_block.get("registry_license")
            if rl not in {"research-only", "restricted"}:
                continue
            for dim_name, dim in (pack.get("dimensions") or {}).items():
                if not isinstance(dim, dict):
                    continue
                reg = dim.get("values_registry")
                if not isinstance(reg, dict):
                    continue
                h = reg.get("hash", "")
                if not h.startswith("placeholder:awaiting-license"):
                    offenders.append(
                        f"{path.name}::{dim_name} = {h!r} "
                        f"(registry_license={rl!r})"
                    )
        assert not offenders, (
            "restricted packs must use placeholder:awaiting-license: "
            f"{offenders}"
        )


# ── Invariant 5: source_hash round-trips through the loader ─────────


class TestSourceHashRoundTripsThroughLoader:
    """When a pack file carries a ``derives_from.source_hash``, the
    loaded ``PackRef.derives_from.source_hash`` must equal it byte-
    for-byte — otherwise downstream receipts won't bind to the right
    underlying-standard revision."""

    def setup_method(self):
        _reset_taxonomy_cache()

    def teardown_method(self):
        _reset_taxonomy_cache()

    def test_loader_preserves_source_hash_on_every_seed_pack(self):
        seeds = {p.stem: pack for p, pack in _load_seed_packs()}
        seed_paths = [p for p, _ in _load_seed_packs()]

        _, refs = load_pack_stack(extra_paths=seed_paths)
        refs_by_name = {r.name: r for r in refs}

        offenders: list[str] = []
        for name, pack in seeds.items():
            df = pack.get("derives_from") or {}
            src_hash = df.get("source_hash")
            if not src_hash:
                continue
            ref = refs_by_name.get(name)
            if ref is None or ref.derives_from is None:
                offenders.append(f"{name}: PackRef.derives_from missing")
                continue
            if ref.derives_from.source_hash != src_hash:
                offenders.append(
                    f"{name}: pack source_hash={src_hash[:24]}... "
                    f"vs loaded source_hash="
                    f"{ref.derives_from.source_hash[:24]}..."
                )
        assert not offenders, (
            f"source_hash drift through loader: {offenders}"
        )
