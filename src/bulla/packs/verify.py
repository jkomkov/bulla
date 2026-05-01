"""Registry verification for Bulla convention packs (Extension B).

Verifies that a pack's ``values_registry`` pointers actually resolve to
content matching the recorded hash. This is the on-demand mechanism by
which a consumer materializes the licensed values that a pack only
references via pointer.

Architecture (intentionally minimal):

1. **Pack-side validation.** ``inspect_registries()`` walks a parsed
   pack and returns one ``RegistryReference`` per pointer found.
   No network, no fetch.

2. **Credential gate.** Before attempting any fetch, the verifier
   consults a ``CredentialProvider`` keyed on the pack's
   ``license.registry_license`` and the pointer's ``license_id``. If
   the registry is ``research-only`` or ``restricted`` and no credential
   is registered, the verifier raises ``RegistryAccessError`` with code
   ``LICENSE_REQUIRED``. The caller knows which license to obtain.

3. **Fetch + hash check.** With a credential (or for ``open``
   registries), the verifier fetches the registry contents via a
   ``RegistryFetcher`` interface, computes their SHA-256, and compares
   to the pointer's recorded hash. Mismatch → ``REGISTRY_HASH_MISMATCH``.
   Network failure → ``REGISTRY_UNAVAILABLE``.

4. **No materialization in this module.** Verification confirms a
   pointer is current and accessible. Materialized values (the actual
   list of 70k ICD-10 codes) live in a consumer-owned cache; this
   module only proves the binding holds. Materialization is a separate
   layer that uses the same ``RegistryFetcher`` interface.

The fetcher interface is deliberately abstract — concrete HTTP/IPFS/git
implementations land later as the actual registries to verify against
arrive (Phases 2–4 of the Standards Ingestion sprint). For now we
expose a ``DictFetcher`` for tests and an unimplemented ``HttpFetcher``
stub that ``bulla packs verify`` will swap in for production use.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from bulla.model import RegistryAccessError, RegistryAccessErrorCode


# ── Reference + result shape ──────────────────────────────────────────


@dataclass(frozen=True)
class RegistryReference:
    """A single ``values_registry`` pointer found inside a pack.

    Pure data — describes WHERE the canonical values live, not what
    they are. ``license_id`` is the cross-reference into the pack's
    license block; the verifier uses it to look up credentials.
    """

    pack_name: str
    dimension: str
    uri: str
    expected_hash: str
    version: str
    license_id: str = ""
    registry_license: str = "open"  # from pack license block


@dataclass(frozen=True)
class RegistryVerification:
    """Outcome of verifying a single registry pointer.

    ``status`` values:
    - ``"ok"`` — fetched, hash matched
    - ``"placeholder"`` — pointer carries the ``placeholder:<reason>``
      sentinel; pack is structurally ready to verify but no real
      ingest has happened yet. The reason is in ``detail``.
    - ``"license_required"`` — fetch skipped, credential missing
    - ``"hash_mismatch"`` — fetched but content hash differs from
      a real ``sha256:...`` recorded hash
    - ``"unavailable"`` — fetch failed (network/storage error)
    - ``"skipped"`` — caller asked us not to fetch this one
    """

    reference: RegistryReference
    status: str
    actual_hash: str = ""
    detail: str = ""


# ── Fetcher + credential interfaces ──────────────────────────────────


class RegistryFetcher(Protocol):
    """Abstract registry-content fetcher.

    Concrete implementations (HTTP, IPFS, local cache) plug in here.
    The verifier never touches network/disk directly — all I/O goes
    through this interface so tests and dry-runs can swap in a
    deterministic in-memory fetcher.
    """

    def fetch(self, uri: str, *, credential: str | None = None) -> bytes:
        """Return raw bytes of the registry content at ``uri``.

        Implementations should raise ``RegistryAccessError`` with
        ``REGISTRY_UNAVAILABLE`` on transport failure. The verifier
        translates other exceptions into the same error code.
        """
        ...


@dataclass(frozen=True)
class DictFetcher:
    """In-memory fetcher used by tests and offline verification.

    ``contents`` maps URI → raw bytes. Useful for replaying a captured
    registry response without network access.
    """

    contents: dict[str, bytes]

    def fetch(self, uri: str, *, credential: str | None = None) -> bytes:
        if uri not in self.contents:
            raise RegistryAccessError(
                RegistryAccessErrorCode.REGISTRY_UNAVAILABLE,
                f"DictFetcher has no entry for uri",
                registry_uri=uri,
            )
        return self.contents[uri]


@dataclass(frozen=True)
class CredentialProvider:
    """Maps ``license_id`` → credential token.

    A credential is opaque from this module's perspective; the
    ``RegistryFetcher`` decides how to use it (HTTP basic auth, OAuth
    bearer, signed URL, etc.). Empty string means "no credential
    available." A pack whose registry license is ``research-only`` or
    ``restricted`` requires a non-empty credential to fetch.

    The provider is keyed only on ``license_id`` (per-licensed-source),
    so a consumer that holds e.g. an NLM-UMLS credential can verify
    every pack that references that license without per-pack setup.
    """

    credentials: dict[str, str] = field(default_factory=dict)

    def get(self, license_id: str) -> str | None:
        if not license_id:
            return None
        return self.credentials.get(license_id) or None


# ── Pack walking ──────────────────────────────────────────────────────


def inspect_registries(parsed_pack: dict[str, Any]) -> list[RegistryReference]:
    """Walk a parsed pack dict and return all ``values_registry`` pointers.

    Pure: no network, no fetch. Each returned ``RegistryReference``
    captures the pointer's URI, expected hash, version, and the
    license metadata that controls credential lookup.
    """
    refs: list[RegistryReference] = []
    if not isinstance(parsed_pack, dict):
        return refs

    pack_name = parsed_pack.get("pack_name", "")
    license_block = parsed_pack.get("license", {})
    if not isinstance(license_block, dict):
        license_block = {}
    registry_license = license_block.get("registry_license", "open")
    if not isinstance(registry_license, str):
        registry_license = "open"

    dims = parsed_pack.get("dimensions", {})
    if not isinstance(dims, dict):
        return refs

    for dim_name, dim_def in dims.items():
        if not isinstance(dim_def, dict):
            continue
        registry = dim_def.get("values_registry")
        if not isinstance(registry, dict):
            continue
        uri = registry.get("uri", "")
        h = registry.get("hash", "")
        version = registry.get("version", "")
        license_id = registry.get("license_id", "")
        if not (
            isinstance(uri, str)
            and isinstance(h, str)
            and isinstance(version, str)
        ):
            # Skip malformed pointers; the validator catches these
            # at pack-load time. Verification is a runtime check that
            # assumes the pack already passed validation.
            continue
        refs.append(
            RegistryReference(
                pack_name=pack_name,
                dimension=dim_name,
                uri=uri,
                expected_hash=h,
                version=version,
                license_id=license_id if isinstance(license_id, str) else "",
                registry_license=registry_license,
            )
        )
    return refs


# ── Verification ─────────────────────────────────────────────────────


def verify_registry(
    ref: RegistryReference,
    fetcher: RegistryFetcher,
    *,
    credential_provider: CredentialProvider | None = None,
    raise_on_license_required: bool = False,
    raise_on_placeholder: bool = False,
) -> RegistryVerification:
    """Verify a single registry pointer.

    Returns a ``RegistryVerification`` describing the outcome. By
    default, missing credentials produce a ``license_required`` result
    *without* raising, so a caller running a batch verification can
    surface "obtain these N licenses" guidance rather than aborting on
    the first restricted pointer. Pass ``raise_on_license_required=True``
    for the strict-CI behavior the plan calls for at receipt-publish
    time.

    Open registries skip the credential gate entirely.

    A pointer whose ``expected_hash`` starts with ``placeholder:``
    short-circuits before any fetch — the pack is declaring "I am
    structurally ready to verify but no real ingest has happened
    yet." The result is ``status='placeholder'`` (not
    ``hash_mismatch``). Pass ``raise_on_placeholder=True`` to make
    placeholders a hard error in CI environments that require every
    pack to be fully verified.
    """
    # Short-circuit on the placeholder sentinel: no fetch, no
    # credential check, just surface the not-yet-checkable state
    # distinctly from real verification outcomes.
    if ref.expected_hash.startswith("placeholder:"):
        reason = ref.expected_hash[len("placeholder:"):] or "(no reason)"
        msg = (
            f"pack '{ref.pack_name}' dimension '{ref.dimension}' "
            f"carries placeholder hash (reason: {reason}). The pack "
            f"is structurally ready to verify; a real ingest has "
            f"not yet been performed."
        )
        if raise_on_placeholder:
            raise RegistryAccessError(
                RegistryAccessErrorCode.PLACEHOLDER_HASH,
                msg,
                license_id=ref.license_id,
                registry_uri=ref.uri,
            )
        return RegistryVerification(
            reference=ref,
            status="placeholder",
            detail=msg,
        )

    needs_credential = ref.registry_license in {"research-only", "restricted"}
    credential: str | None = None
    if needs_credential:
        if credential_provider is None or not ref.license_id:
            err = RegistryAccessError(
                RegistryAccessErrorCode.LICENSE_REQUIRED,
                (
                    f"pack '{ref.pack_name}' dimension '{ref.dimension}' "
                    f"has registry_license={ref.registry_license!r} but "
                    "no credential provider or license_id is registered"
                ),
                license_id=ref.license_id,
                registry_uri=ref.uri,
            )
            if raise_on_license_required:
                raise err
            return RegistryVerification(
                reference=ref,
                status="license_required",
                detail=str(err),
            )
        credential = credential_provider.get(ref.license_id)
        if not credential:
            err = RegistryAccessError(
                RegistryAccessErrorCode.LICENSE_REQUIRED,
                (
                    f"pack '{ref.pack_name}' dimension '{ref.dimension}' "
                    f"requires license '{ref.license_id}' to fetch registry"
                ),
                license_id=ref.license_id,
                registry_uri=ref.uri,
            )
            if raise_on_license_required:
                raise err
            return RegistryVerification(
                reference=ref,
                status="license_required",
                detail=str(err),
            )

    try:
        content = fetcher.fetch(ref.uri, credential=credential)
    except RegistryAccessError as e:
        return RegistryVerification(
            reference=ref,
            status="unavailable",
            detail=str(e),
        )
    except Exception as e:  # pragma: no cover — defensive
        return RegistryVerification(
            reference=ref,
            status="unavailable",
            detail=f"fetcher raised {type(e).__name__}: {e}",
        )

    actual_hex = hashlib.sha256(content).hexdigest()
    actual = f"sha256:{actual_hex}"
    # Tolerate both prefixed (``sha256:<hex>``, the canonical form) and
    # bare-hex forms in the recorded hash for backward compatibility
    # with pre-sentinel tests; new packs use the prefixed form per
    # validator rule.
    expected_normalized = (
        ref.expected_hash
        if ref.expected_hash.startswith("sha256:")
        else f"sha256:{ref.expected_hash}"
    )
    if actual != expected_normalized:
        return RegistryVerification(
            reference=ref,
            status="hash_mismatch",
            actual_hash=actual,
            detail=(
                f"expected {expected_normalized[:23]}…, "
                f"got {actual[:23]}…"
            ),
        )

    return RegistryVerification(
        reference=ref,
        status="ok",
        actual_hash=actual,
    )


def verify_pack_registries(
    parsed_pack: dict[str, Any],
    fetcher: RegistryFetcher,
    *,
    credential_provider: CredentialProvider | None = None,
    raise_on_license_required: bool = False,
    raise_on_placeholder: bool = False,
    only: Callable[[RegistryReference], bool] | None = None,
) -> list[RegistryVerification]:
    """Verify every ``values_registry`` pointer in a parsed pack.

    Convenience wrapper around ``inspect_registries`` +
    ``verify_registry``. ``only`` is an optional filter (takes a
    ``RegistryReference``, returns bool) that lets the caller limit
    verification to a subset (e.g. open registries when running CI
    without restricted-license credentials).

    ``raise_on_placeholder`` is for strict CI environments that
    require every pack to be fully verified (i.e. no placeholder
    sentinels in the published artifact set).
    """
    refs = inspect_registries(parsed_pack)
    results: list[RegistryVerification] = []
    for ref in refs:
        if only is not None and not only(ref):
            results.append(
                RegistryVerification(reference=ref, status="skipped")
            )
            continue
        results.append(
            verify_registry(
                ref,
                fetcher,
                credential_provider=credential_provider,
                raise_on_license_required=raise_on_license_required,
                raise_on_placeholder=raise_on_placeholder,
            )
        )
    return results
