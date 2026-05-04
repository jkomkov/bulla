"""Runtime value translation between conventions on the same dimension.

This module is the *prescriptive* counterpart to Bulla's diagnostic
core. The diagnostic layer (``bulla.diagnose``) returns a coherence
fee H¹ and a list of blind spots — places where two tools disagree
on a convention. Every blind spot is a candidate for repair: the
agent can either disclose a hidden field or *translate* the value
crossing the seam into the form the consumer expects.

This module ships the typed translators that close that loop.
Calling ``bulla.translate(dimension, value, to_convention)`` returns:

  * the translated value (e.g. ``"USD"`` → ``"usd"`` for Stripe);
  * a ``TranslationEvidence`` capturing the dimension, conventions,
    equivalence class, and any pack provenance the translation
    consulted;
  * a ``WitnessReceipt`` content-addressing the translation event
    so it chains into a session's receipt history.

The function name is ``translate`` (not ``bridge``) for two reasons:

  1. The diagnostic layer already exposes a ``Bridge`` dataclass for
     field-keyed structural metadata. ``from bulla import Bridge`` vs
     ``from bulla import bridge`` is the kind of import that fails
     once and ships. ``translate`` is the verb the function actually
     performs and avoids the case-sensitivity collision entirely.
  2. The lower-level passive walker over Extension E ``mappings:``
     blocks is already named ``bulla.mappings.translate``. The
     top-level ``bulla.translate`` is the runtime that uses it as a
     substrate plus a typed registry of hand-written translators
     for cases the mapping table can't express (Stripe lowercase,
     Unix timestamps, BCP-47 normalization).

Restricted-pack invariant: when a mapping-derived translation
resolves a value from a pack with ``registry_license: restricted``
or ``research-only``, the runtime hashes ``value_out`` rather than
returning it raw, and surfaces a license-required notice on the
evidence. Licensed values never leak through the runtime.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from bulla.mappings import translate as _walk_mappings_for_translation
from bulla.model import (
    DEFAULT_POLICY_PROFILE,
    Composition,
    Disposition,
    Edge,
    PackRef,
    SemanticDimension,
    ToolSpec,
    WitnessReceipt,
)


# ────────────────────────────────────────────────────────────────────
# Public dataclasses
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TranslationEvidence:
    """Per-call audit context for one ``translate()`` invocation.

    The evidence answers four questions:
      - which dimension was crossed?
      - what convention pair?
      - what input/output values?
      - what equivalence class did the translator declare?
      - which packs (if any) participated?

    ``is_redacted`` is True iff ``value_out`` is the SHA-256 of the
    raw output rather than the output itself — the case for mapping
    rows resolved through a restricted-license pack.
    """

    dimension: str
    from_convention: str
    to_convention: str
    value_in: str
    value_out: str
    equivalence: str
    pack_refs: tuple[PackRef, ...] = ()
    note: str = ""
    is_redacted: bool = False
    source: str = ""  # "registry" | "mappings" | "alias"

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "dimension": self.dimension,
            "from_convention": self.from_convention,
            "to_convention": self.to_convention,
            "value_in": self.value_in,
            "value_out": self.value_out,
            "equivalence": self.equivalence,
            "source": self.source,
        }
        if self.pack_refs:
            d["pack_refs"] = [p.to_dict() for p in self.pack_refs]
        if self.note:
            d["note"] = self.note
        if self.is_redacted:
            d["is_redacted"] = True
        return d


@dataclass(frozen=True)
class TranslationResult:
    """Output of one ``translate()`` call.

    ``receipt`` is a ``WitnessReceipt`` covering a synthetic
    single-tool/single-edge ``Composition`` — the canonical hash
    binds the ``(dimension, from, to, value_in, value_out)`` tuple
    plus a per-call session id, so two unrelated translation events
    never collide.
    """

    value: str
    evidence: TranslationEvidence
    receipt: WitnessReceipt

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "evidence": self.evidence.to_dict(),
            "receipt": self.receipt.to_dict(),
        }


class TranslationUnavailable(RuntimeError):
    """Raised when no translator exists for ``(dimension, from, to)``.

    Carries structured metadata: ``dimension``, ``from_convention``,
    ``to_convention``, an optional ``suggestion`` string (e.g. nearest
    known convention pair), and an optional ``license_required``
    string (set when the only translator we found is gated behind a
    restricted pack the caller doesn't hold).
    """

    def __init__(
        self,
        dimension: str,
        from_convention: str,
        to_convention: str,
        *,
        suggestion: str = "",
        license_required: str = "",
    ) -> None:
        self.dimension = dimension
        self.from_convention = from_convention
        self.to_convention = to_convention
        self.suggestion = suggestion
        self.license_required = license_required
        msg = (
            f"no translator for dimension={dimension!r} "
            f"from={from_convention!r} to={to_convention!r}"
        )
        if license_required:
            msg += f"; license required: {license_required}"
        if suggestion:
            msg += f"; suggestion: {suggestion}"
        super().__init__(msg)


# ────────────────────────────────────────────────────────────────────
# Registry
# ────────────────────────────────────────────────────────────────────


# A registered translator returns ``(value_out, equivalence)``. The
# equivalence string is one of "exact" | "lossy_forward" |
# "lossy_bidirectional" | "contextual" — same vocabulary the
# Extension E ``mappings:`` block uses.
TranslatorFn = Callable[[str], "tuple[str, str]"]


_REGISTRY: dict[tuple[str, str, str], TranslatorFn] = {}
_REGISTRY_LOCK = threading.Lock()


def register(dimension: str, from_convention: str, to_convention: str):
    """Decorator: register a hand-written translator.

    Usage::

        @register("temporal_format", "iso-8601", "unix-seconds")
        def _iso_to_unix(value: str) -> tuple[str, str]:
            return str(int(parse_iso(value).timestamp())), "exact"

    The (dimension, from, to) triple is the registry key. Re-
    registering the same triple replaces the previous translator
    (handy for tests, lossy by intent).
    """

    def decorator(fn: TranslatorFn) -> TranslatorFn:
        with _REGISTRY_LOCK:
            _REGISTRY[(dimension, from_convention, to_convention)] = fn
        return fn

    return decorator


def registered_pairs() -> list[tuple[str, str, str]]:
    """Return all registered (dimension, from, to) triples."""
    with _REGISTRY_LOCK:
        return sorted(_REGISTRY.keys())


def _lookup_registry(
    dimension: str, from_convention: str, to_convention: str
) -> TranslatorFn | None:
    with _REGISTRY_LOCK:
        return _REGISTRY.get((dimension, from_convention, to_convention))


# ────────────────────────────────────────────────────────────────────
# Receipt construction (reuse WitnessReceipt — no new model)
# ────────────────────────────────────────────────────────────────────


_RECEIPT_VERSION = "0.1.0"


def _kernel_version() -> str:
    """Resolve the bulla version for receipt provenance, with fallback."""
    try:
        from bulla import __version__ as kver  # local import to avoid cycle
        return f"bulla-{kver}"
    except Exception:  # pragma: no cover — defensive
        return "bulla-unknown"


def _make_translation_composition(
    *,
    dimension: str,
    from_convention: str,
    to_convention: str,
    value_in: str,
    value_out: str,
    session_id: str,
) -> Composition:
    """Build the synthetic single-tool/single-edge composition that
    backs a translation receipt.

    The composition's name embeds the tuple plus a session id so the
    canonical hash uniquely identifies the translation event. Two
    independent calls to ``translate("currency_code", "USD", ...)``
    produce different ``composition_hash`` values because their
    session ids differ.
    """
    # The synthetic tool exposes both conventions as observables; the
    # edge crosses the dimension. This produces a fee=0 composition
    # (both fields observable, no blind spot) — translation is itself
    # the bridge that resolves a seam.
    tool = ToolSpec(
        name="translate",
        internal_state=(from_convention, to_convention),
        observable_schema=(from_convention, to_convention),
    )
    edge = Edge(
        from_tool="translate",
        to_tool="translate",
        dimensions=(
            SemanticDimension(
                name=dimension,
                from_field=from_convention,
                to_field=to_convention,
            ),
        ),
    )
    name = (
        f"translate:{dimension}:{from_convention}->{to_convention}:"
        f"{value_in}->{value_out}:{session_id}"
    )
    return Composition(name=name, tools=(tool,), edges=(edge,))


def _diagnostic_hash_for(comp: Composition) -> str:
    """Tiny canonical hash for the synthetic translation diagnostic.

    A real ``Diagnostic.content_hash()`` would require running
    ``diagnose()`` on the synthetic composition, which is overkill —
    the translation receipt's load-bearing identity is its
    composition + evidence, not a Diagnostic measurement. We seal
    the synthetic composition's hash with a stable suffix so the
    receipt's ``diagnostic_hash`` field is filled.
    """
    payload = {"kind": "translate", "composition": comp.canonical_hash()}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()


def _build_receipt(
    *,
    evidence: TranslationEvidence,
    session_id: str,
    parent_hashes: tuple[str, ...] | None,
) -> WitnessReceipt:
    comp = _make_translation_composition(
        dimension=evidence.dimension,
        from_convention=evidence.from_convention,
        to_convention=evidence.to_convention,
        value_in=evidence.value_in,
        value_out=evidence.value_out,
        session_id=session_id,
    )
    return WitnessReceipt(
        receipt_version=_RECEIPT_VERSION,
        kernel_version=_kernel_version(),
        composition_hash=comp.canonical_hash(),
        diagnostic_hash=_diagnostic_hash_for(comp),
        policy_profile=DEFAULT_POLICY_PROFILE,
        fee=0,
        blind_spots_count=0,
        bridges_required=0,
        unknown_dimensions=0,
        disposition=Disposition.PROCEED,
        timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        active_packs=tuple(evidence.pack_refs),
        inline_dimensions={
            "kind": "translate",
            "dimension": evidence.dimension,
            "from_convention": evidence.from_convention,
            "to_convention": evidence.to_convention,
            "value_in": evidence.value_in,
            "value_out": evidence.value_out,
            "equivalence": evidence.equivalence,
            "source": evidence.source,
            "is_redacted": evidence.is_redacted,
            "note": evidence.note,
        },
        parent_receipt_hashes=parent_hashes,
    )


# ────────────────────────────────────────────────────────────────────
# Public translate() — the runtime
# ────────────────────────────────────────────────────────────────────


def translate(
    dimension: str,
    *,
    value: str,
    to_convention: str,
    from_convention: str | None = None,
    session_id: str | None = None,
    parent_receipt_hashes: tuple[str, ...] | None = None,
    extra_packs: Iterable[dict[str, Any]] | None = None,
) -> TranslationResult:
    """Translate ``value`` on ``dimension`` to ``to_convention``.

    Dispatch order:

      1. **Hand-written registry** — if a translator was registered
         for ``(dimension, from_convention, to_convention)``, call it.
         When ``from_convention`` is None, every registered translator
         keyed by ``(dimension, *, to_convention)`` is tried in
         registration order; the first success wins.

      2. **Mapping-derived** — walk the active pack stack (or
         ``extra_packs`` if supplied) and consult any pack that
         declares an Extension E ``mappings:`` block whose target
         pack matches ``to_convention``. The lower-level
         ``bulla.mappings.translate`` does the row scan.

      3. **Miss** — raise ``TranslationUnavailable``.

    A ``TranslationResult`` is returned on success. The ``receipt``
    field is a ``WitnessReceipt`` whose ``parent_receipt_hashes`` is
    set to ``parent_receipt_hashes`` (lets a caller chain into a
    session). ``session_id`` is a per-call unique tag baked into the
    composition name so the canonical hash never collides; if not
    supplied, a fresh UUIDv4 is generated.

    Arguments:
        dimension: dimension name (e.g. ``"currency_code"``).
        value: input value (canonical or any registered alias).
        to_convention: destination convention id. Conventionally
            either a pack name (``"iso-4217"``) or a canonical
            short-tag (``"stripe-lower"``, ``"unix-seconds"``).
        from_convention: optional source convention id. If omitted,
            the registry tries every pair keyed on ``to_convention``
            for this dimension.
        session_id: optional unique tag to disambiguate the receipt's
            canonical hash. Bare callers can omit this; the
            ``Session`` object passes its session id through.
        parent_receipt_hashes: optional list of receipt hashes that
            this translation chains from. ``Session.translate``
            populates this with the latest checkpoint hash.
        extra_packs: optional iterable of parsed pack dicts to
            consult in addition to (or instead of) the active pack
            stack. Mostly for testing; production callers rely on
            ``bulla.infer.classifier.configure_packs``.

    Raises:
        TranslationUnavailable: when no translator covers the call.
    """
    sid = session_id or str(uuid.uuid4())

    # 1. Registry hit.
    if from_convention is not None:
        fn = _lookup_registry(dimension, from_convention, to_convention)
        if fn is not None:
            value_out, equivalence = fn(value)
            evidence = TranslationEvidence(
                dimension=dimension,
                from_convention=from_convention,
                to_convention=to_convention,
                value_in=value,
                value_out=value_out,
                equivalence=equivalence,
                source="registry",
            )
            return TranslationResult(
                value=value_out,
                evidence=evidence,
                receipt=_build_receipt(
                    evidence=evidence,
                    session_id=sid,
                    parent_hashes=parent_receipt_hashes,
                ),
            )
    else:
        # No source convention given — scan registry for any (dim, *, to).
        with _REGISTRY_LOCK:
            candidates = [
                (frm, fn)
                for (dim, frm, to), fn in _REGISTRY.items()
                if dim == dimension and to == to_convention
            ]
        for frm, fn in candidates:
            try:
                value_out, equivalence = fn(value)
            except Exception:
                continue
            evidence = TranslationEvidence(
                dimension=dimension,
                from_convention=frm,
                to_convention=to_convention,
                value_in=value,
                value_out=value_out,
                equivalence=equivalence,
                source="registry",
            )
            return TranslationResult(
                value=value_out,
                evidence=evidence,
                receipt=_build_receipt(
                    evidence=evidence,
                    session_id=sid,
                    parent_hashes=parent_receipt_hashes,
                ),
            )

    # 2. Mapping-derived (Extension E walker).
    packs = _resolve_active_packs(extra_packs)
    license_required: str = ""
    for parsed_pack, pack_ref in packs:
        # We only walk packs that have a mappings: block to begin with.
        if not isinstance(parsed_pack.get("mappings"), dict):
            continue
        result = _walk_mappings_for_translation(
            value,
            from_pack=parsed_pack,
            to_pack_name=to_convention,
            to_dimension=dimension,
            direction="forward",
        )
        if not result.found:
            continue
        # Extension E gives us values; pick the first.
        value_out_raw = result.values[0]
        # Restricted-pack invariant: redact value_out for restricted
        # registries so licensed values don't leak through evidence.
        is_restricted = _is_restricted(parsed_pack)
        if is_restricted:
            # Restricted packs never surface their values through the
            # uncredentialed runtime. We record that a license-gated
            # match exists, then continue scanning for an open pack
            # that resolves the same translation. If none does, the
            # post-loop branch raises TranslationUnavailable with
            # license_required set so the caller knows what credential
            # to obtain.
            license_required = pack_ref.name
            continue
        evidence = TranslationEvidence(
            dimension=dimension,
            from_convention=pack_ref.name,
            to_convention=to_convention,
            value_in=value,
            value_out=value_out_raw,
            equivalence=result.equivalence or "exact",
            pack_refs=(pack_ref,),
            note=result.note,
            source="mappings",
        )
        return TranslationResult(
            value=value_out_raw,
            evidence=evidence,
            receipt=_build_receipt(
                evidence=evidence,
                session_id=sid,
                parent_hashes=parent_receipt_hashes,
            ),
        )

    # If only restricted packs covered the request, surface that
    # explicitly (not the redacted result — the caller asked for an
    # uncredentialed translation; we owe a license-required notice).
    if license_required:
        raise TranslationUnavailable(
            dimension,
            from_convention or "*",
            to_convention,
            license_required=license_required,
        )

    # 3. Miss — produce a useful suggestion.
    raise TranslationUnavailable(
        dimension,
        from_convention or "*",
        to_convention,
        suggestion=_suggest_alternatives(dimension, to_convention),
    )


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _resolve_active_packs(
    extra: Iterable[dict[str, Any]] | None,
) -> list[tuple[dict[str, Any], PackRef]]:
    """Resolve (parsed_pack_dict, PackRef) pairs from active stack.

    Returns the merged stack as discrete packs so we can walk each
    one's ``mappings:`` block separately. Falls back gracefully when
    no packs are configured.
    """
    if extra is not None:
        out: list[tuple[dict[str, Any], PackRef]] = []
        for parsed in extra:
            if not isinstance(parsed, dict):
                continue
            pack_name = parsed.get("pack_name", "<inline>")
            pack_version = parsed.get("pack_version", "0.0.0")
            ref = PackRef(
                name=pack_name,
                version=pack_version,
                # Hash isn't load-bearing for the translation — it
                # surfaces on the receipt for traceability only.
                hash=hashlib.sha256(
                    json.dumps(parsed, sort_keys=True, default=str).encode()
                ).hexdigest(),
            )
            out.append((parsed, ref))
        return out

    # Active pack stack from the classifier.
    try:
        from bulla.infer.classifier import (
            _load_single_pack,
            get_active_pack_refs,
        )
    except Exception:  # pragma: no cover
        return []

    refs = get_active_pack_refs()
    out_active: list[tuple[dict[str, Any], PackRef]] = []
    # For each PackRef we re-resolve the parsed pack from the seed
    # directory by name. We can't recover the parsed dict from the
    # taxonomy cache alone (it's been merged), so we re-load. This is
    # cheap on a small seed corpus.
    seed_dir = _seed_pack_dir()
    if seed_dir is None:
        return []
    for ref in refs:
        candidate = seed_dir / f"{ref.name}.yaml"
        if not candidate.exists():
            continue
        try:
            parsed = _load_single_pack(candidate)
        except Exception:
            continue
        out_active.append((parsed, ref))
    return out_active


def _seed_pack_dir():
    """Locate the bundled seed-pack directory.

    Returns a Path or None. Used to re-load parsed packs that the
    classifier merged into the taxonomy cache (the merged form is
    flat and loses per-pack mappings: blocks).
    """
    try:
        import importlib.resources
        from pathlib import Path
        pkg = importlib.resources.files("bulla")
        return Path(str(pkg / "packs" / "seed"))
    except Exception:  # pragma: no cover
        return None


def _is_restricted(parsed_pack: dict[str, Any]) -> bool:
    license_block = parsed_pack.get("license") if isinstance(parsed_pack, dict) else None
    if not isinstance(license_block, dict):
        return False
    return license_block.get("registry_license") in {
        "restricted",
        "research-only",
    }


def _suggest_alternatives(dimension: str, to_convention: str) -> str:
    """Produce a structured suggestion for a TranslationUnavailable.

    The hint lists known conventions on ``dimension`` for which
    translators are registered, so the caller can fix the request.
    """
    with _REGISTRY_LOCK:
        on_dim = sorted(
            {(frm, to) for (dim, frm, to) in _REGISTRY if dim == dimension}
        )
    if not on_dim:
        return f"no translators registered for dimension={dimension!r}"
    pairs_str = ", ".join(f"{a}->{b}" for a, b in on_dim[:5])
    return (
        f"known conventions on {dimension!r}: {pairs_str}"
        f"{'...' if len(on_dim) > 5 else ''}"
    )


# ────────────────────────────────────────────────────────────────────
# Five canonical bridges (the sprint deliverable)
# ────────────────────────────────────────────────────────────────────


# 1. currency_code: ISO-4217 alpha ↔ Stripe-lower / numeric ----------

# ISO-4217 numeric for the most common currencies. Aligned with
# iso-4217.yaml seed pack's canonical/aliases data.
_ISO_4217_NUMERIC: dict[str, str] = {
    "USD": "840", "EUR": "978", "JPY": "392", "GBP": "826",
    "AUD": "036", "CAD": "124", "CHF": "756", "CNY": "156",
    "HKD": "344", "NZD": "554", "SEK": "752", "KRW": "410",
    "SGD": "702", "NOK": "578", "MXN": "484", "INR": "356",
    "BRL": "986", "ZAR": "710", "TRY": "949", "RUB": "643",
}
_ISO_4217_ALPHA_FROM_NUMERIC: dict[str, str] = {
    v: k for k, v in _ISO_4217_NUMERIC.items()
}


@register("currency_code", "iso-4217", "stripe-lower")
def _currency_iso_to_stripe(value: str) -> tuple[str, str]:
    return value.lower(), "exact"


@register("currency_code", "stripe-lower", "iso-4217")
def _currency_stripe_to_iso(value: str) -> tuple[str, str]:
    return value.upper(), "exact"


@register("currency_code", "iso-4217", "iso-4217-numeric")
def _currency_alpha_to_numeric(value: str) -> tuple[str, str]:
    code = _ISO_4217_NUMERIC.get(value.upper())
    if code is None:
        raise TranslationUnavailable(
            "currency_code",
            "iso-4217",
            "iso-4217-numeric",
            suggestion=(
                f"alpha code {value!r} not in built-in 20-currency map; "
                "register an extension translator if you need wider coverage"
            ),
        )
    return code, "exact"


@register("currency_code", "iso-4217-numeric", "iso-4217")
def _currency_numeric_to_alpha(value: str) -> tuple[str, str]:
    code = _ISO_4217_ALPHA_FROM_NUMERIC.get(str(value).zfill(3))
    if code is None:
        raise TranslationUnavailable(
            "currency_code",
            "iso-4217-numeric",
            "iso-4217",
            suggestion=(
                f"numeric code {value!r} not in built-in 20-currency map"
            ),
        )
    return code, "exact"


# 2. country_code: ISO-3166 alpha-2 ↔ alpha-3 ↔ numeric --------------

# 30 most-common countries from iso-3166 seed pack's known_values.
_ISO_3166_ALPHA2_TO_ALPHA3: dict[str, str] = {
    "US": "USA", "GB": "GBR", "FR": "FRA", "DE": "DEU", "JP": "JPN",
    "CN": "CHN", "IN": "IND", "BR": "BRA", "CA": "CAN", "AU": "AUS",
    "MX": "MEX", "RU": "RUS", "IT": "ITA", "ES": "ESP", "KR": "KOR",
    "NL": "NLD", "SE": "SWE", "CH": "CHE", "BE": "BEL", "AT": "AUT",
    "DK": "DNK", "FI": "FIN", "NO": "NOR", "IE": "IRL", "PL": "POL",
    "PT": "PRT", "GR": "GRC", "TR": "TUR", "ZA": "ZAF", "AR": "ARG",
    "ID": "IDN", "TH": "THA", "VN": "VNM", "PH": "PHL", "MY": "MYS",
    "SG": "SGP", "HK": "HKG", "TW": "TWN", "NZ": "NZL", "IL": "ISR",
}
_ISO_3166_ALPHA3_TO_ALPHA2 = {
    v: k for k, v in _ISO_3166_ALPHA2_TO_ALPHA3.items()
}
_ISO_3166_ALPHA2_TO_NUMERIC: dict[str, str] = {
    "US": "840", "GB": "826", "FR": "250", "DE": "276", "JP": "392",
    "CN": "156", "IN": "356", "BR": "076", "CA": "124", "AU": "036",
    "MX": "484", "RU": "643", "IT": "380", "ES": "724", "KR": "410",
    "NL": "528", "SE": "752", "CH": "756", "BE": "056", "AT": "040",
    "DK": "208", "FI": "246", "NO": "578", "IE": "372", "PL": "616",
    "PT": "620", "GR": "300", "TR": "792", "ZA": "710", "AR": "032",
    "ID": "360", "TH": "764", "VN": "704", "PH": "608", "MY": "458",
    "SG": "702", "HK": "344", "TW": "158", "NZ": "554", "IL": "376",
}
_ISO_3166_NUMERIC_TO_ALPHA2 = {
    v: k for k, v in _ISO_3166_ALPHA2_TO_NUMERIC.items()
}


@register("country_code", "iso-3166-alpha2", "iso-3166-alpha3")
def _country_a2_to_a3(value: str) -> tuple[str, str]:
    code = _ISO_3166_ALPHA2_TO_ALPHA3.get(value.upper())
    if code is None:
        raise TranslationUnavailable(
            "country_code", "iso-3166-alpha2", "iso-3166-alpha3",
            suggestion=f"alpha-2 {value!r} not in built-in 40-country map",
        )
    return code, "exact"


@register("country_code", "iso-3166-alpha3", "iso-3166-alpha2")
def _country_a3_to_a2(value: str) -> tuple[str, str]:
    code = _ISO_3166_ALPHA3_TO_ALPHA2.get(value.upper())
    if code is None:
        raise TranslationUnavailable(
            "country_code", "iso-3166-alpha3", "iso-3166-alpha2",
            suggestion=f"alpha-3 {value!r} not in built-in 40-country map",
        )
    return code, "exact"


@register("country_code", "iso-3166-alpha2", "iso-3166-numeric")
def _country_a2_to_numeric(value: str) -> tuple[str, str]:
    code = _ISO_3166_ALPHA2_TO_NUMERIC.get(value.upper())
    if code is None:
        raise TranslationUnavailable(
            "country_code", "iso-3166-alpha2", "iso-3166-numeric",
            suggestion=f"alpha-2 {value!r} not in built-in 40-country map",
        )
    return code, "exact"


@register("country_code", "iso-3166-numeric", "iso-3166-alpha2")
def _country_numeric_to_a2(value: str) -> tuple[str, str]:
    code = _ISO_3166_NUMERIC_TO_ALPHA2.get(str(value).zfill(3))
    if code is None:
        raise TranslationUnavailable(
            "country_code", "iso-3166-numeric", "iso-3166-alpha2",
            suggestion=f"numeric {value!r} not in built-in 40-country map",
        )
    return code, "exact"


# 3. language_code: ISO-639-1 ↔ ISO-639-3 ↔ BCP-47 --------------------

# Common alpha-2 → alpha-3 (sourced from the iso-639 seed pack's
# inline values, which carry source_codes for both).
_ISO_639_1_TO_3: dict[str, str] = {
    "en": "eng", "zh": "zho", "hi": "hin", "es": "spa", "ar": "ara",
    "bn": "ben", "fr": "fra", "pt": "por", "ru": "rus", "ur": "urd",
    "id": "ind", "de": "deu", "ja": "jpn", "sw": "swa", "mr": "mar",
    "te": "tel", "tr": "tur", "ta": "tam", "ko": "kor", "vi": "vie",
    "fa": "fas", "pl": "pol", "uk": "ukr", "it": "ita", "my": "mya",
    "th": "tha", "ms": "msa", "nl": "nld", "sv": "swe", "el": "ell",
    "he": "heb", "da": "dan", "no": "nor", "fi": "fin", "cs": "ces",
    "sk": "slk", "hu": "hun", "ro": "ron", "bg": "bul", "hr": "hrv",
}
_ISO_639_3_TO_1 = {v: k for k, v in _ISO_639_1_TO_3.items()}


@register("language_code", "iso-639-1", "iso-639-3")
def _lang_a2_to_a3(value: str) -> tuple[str, str]:
    code = _ISO_639_1_TO_3.get(value.lower())
    if code is None:
        raise TranslationUnavailable(
            "language_code", "iso-639-1", "iso-639-3",
            suggestion=f"alpha-2 {value!r} not in built-in 40-language map",
        )
    return code, "exact"


@register("language_code", "iso-639-3", "iso-639-1")
def _lang_a3_to_a2(value: str) -> tuple[str, str]:
    code = _ISO_639_3_TO_1.get(value.lower())
    if code is None:
        raise TranslationUnavailable(
            "language_code", "iso-639-3", "iso-639-1",
            suggestion=f"alpha-3 {value!r} not in built-in 40-language map",
        )
    return code, "exact"


_BCP47_RE = re.compile(r"^([a-zA-Z]{2,3})(?:-([a-zA-Z]{2,4}))?(?:-([A-Z0-9]{2,8}))?$")


@register("language_code", "iso-639-1", "bcp-47")
def _lang_iso_to_bcp47(value: str) -> tuple[str, str]:
    # iso-639-1 → bare bcp-47 is just the lowercased code.
    return value.lower(), "exact"


@register("language_code", "bcp-47", "iso-639-1")
def _lang_bcp47_to_iso(value: str) -> tuple[str, str]:
    m = _BCP47_RE.match(value)
    if not m:
        raise TranslationUnavailable(
            "language_code", "bcp-47", "iso-639-1",
            suggestion=f"value {value!r} is not a recognizable BCP-47 tag",
        )
    primary = m.group(1).lower()
    if len(primary) == 2:
        return primary, "lossy_forward"
    # 3-letter primary subtag — drop region/variant, normalize via 639-3
    iso2 = _ISO_639_3_TO_1.get(primary)
    if iso2 is None:
        raise TranslationUnavailable(
            "language_code", "bcp-47", "iso-639-1",
            suggestion=(
                f"BCP-47 primary subtag {primary!r} not in built-in "
                "40-language map"
            ),
        )
    return iso2, "lossy_forward"


# 4. temporal_format: ISO-8601 ↔ Unix timestamps ---------------------


def _parse_iso8601(value: str) -> _dt.datetime:
    """Parse an ISO-8601 / RFC-3339 datetime, accepting trailing ``Z``."""
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return _dt.datetime.fromisoformat(s)


@register("temporal_format", "iso-8601", "unix-seconds")
def _ts_iso_to_unix_s(value: str) -> tuple[str, str]:
    try:
        dt = _parse_iso8601(value)
    except (ValueError, TypeError) as e:
        raise TranslationUnavailable(
            "temporal_format", "iso-8601", "unix-seconds",
            suggestion=f"could not parse ISO-8601: {e}",
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return str(int(dt.timestamp())), "lossy_forward"


@register("temporal_format", "unix-seconds", "iso-8601")
def _ts_unix_s_to_iso(value: str) -> tuple[str, str]:
    try:
        secs = int(value)
    except (ValueError, TypeError):
        raise TranslationUnavailable(
            "temporal_format", "unix-seconds", "iso-8601",
            suggestion=f"value {value!r} not parseable as integer seconds",
        )
    dt = _dt.datetime.fromtimestamp(secs, tz=_dt.timezone.utc)
    return dt.isoformat().replace("+00:00", "Z"), "exact"


@register("temporal_format", "iso-8601", "unix-millis")
def _ts_iso_to_unix_ms(value: str) -> tuple[str, str]:
    try:
        dt = _parse_iso8601(value)
    except (ValueError, TypeError) as e:
        raise TranslationUnavailable(
            "temporal_format", "iso-8601", "unix-millis",
            suggestion=f"could not parse ISO-8601: {e}",
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return str(int(dt.timestamp() * 1000)), "exact"


@register("temporal_format", "unix-millis", "iso-8601")
def _ts_unix_ms_to_iso(value: str) -> tuple[str, str]:
    try:
        ms = int(value)
    except (ValueError, TypeError):
        raise TranslationUnavailable(
            "temporal_format", "unix-millis", "iso-8601",
            suggestion=f"value {value!r} not parseable as integer milliseconds",
        )
    dt = _dt.datetime.fromtimestamp(ms / 1000, tz=_dt.timezone.utc)
    return dt.isoformat().replace("+00:00", "Z"), "exact"


# 5. fhir_resource_type: R4 ↔ R5 capitalization edges -----------------

# Most resource types are stable across R4 / R5. The handful that
# renamed are the load-bearing edges.
_FHIR_R4_TO_R5: dict[str, str] = {
    "ImagingManifest": "ImagingSelection",
    # MedicationStatement was renamed to MedicationUsage in R6 (not R5)
    # so it stays stable here.
}
_FHIR_R5_TO_R4 = {v: k for k, v in _FHIR_R4_TO_R5.items()}


@register("fhir_resource_type", "fhir-r4", "fhir-r5")
def _fhir_r4_to_r5(value: str) -> tuple[str, str]:
    if value in _FHIR_R4_TO_R5:
        return _FHIR_R4_TO_R5[value], "lossy_bidirectional"
    # Unchanged resource types pass through.
    return value, "exact"


@register("fhir_resource_type", "fhir-r5", "fhir-r4")
def _fhir_r5_to_r4(value: str) -> tuple[str, str]:
    if value in _FHIR_R5_TO_R4:
        return _FHIR_R5_TO_R4[value], "lossy_bidirectional"
    return value, "exact"


# 6. path_convention: filesystem-absolute ↔ repo-relative -------------
#
# The seam ``bulla scan`` finds most often on Cursor / Claude Code
# configs: a filesystem MCP server returns absolute paths, and a
# GitHub (or similar VCS) tool expects repo-relative paths. The fix
# is mechanical — strip the absolute root prefix.
#
# The repo root is not a fixed string, so the translator looks for
# it in three places, in order:
#   1. The ``BULLA_REPO_ROOT`` environment variable (production
#      default — the user pins their repo root explicitly).
#   2. ``git rev-parse --show-toplevel`` against the current working
#      directory (works when the user runs the agent from inside a
#      git repository).
#   3. ``os.getcwd()`` (last-ditch — assume the cwd IS the repo
#      root). Equivalence is degraded to ``contextual`` in this
#      branch since the assumption is unverifiable.
#
# Production users who want a different repo root register their
# own translator under the same (dimension, from, to) triple via
# the ``@register`` decorator; the registry replaces the previous
# entry. See bulla/docs/FRAMEWORKS.md for the registration pattern.


def _resolve_repo_root() -> tuple[str, str]:
    """Find the repo root with provenance for the equivalence label.

    Returns ``(repo_root, equivalence)``. ``equivalence`` is "exact"
    when the root came from an explicit source (env var, git toplevel)
    and "contextual" when it fell back to ``os.getcwd()``.
    """
    import os
    import subprocess
    env_root = os.environ.get("BULLA_REPO_ROOT")
    if env_root:
        return env_root.rstrip("/"), "exact"
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            top = r.stdout.strip()
            if top:
                return top.rstrip("/"), "exact"
    except (OSError, subprocess.SubprocessError):
        pass
    return os.getcwd().rstrip("/"), "contextual"


@register("path_convention", "filesystem-absolute", "repo-relative")
def _path_abs_to_repo_relative(value: str) -> tuple[str, str]:
    """Convert an absolute filesystem path to a repo-relative path.

    Strips the resolved repo-root prefix. When the input doesn't sit
    under the resolved root, the equivalence is "contextual" — the
    translation may have stripped the wrong prefix.
    """
    if not value:
        raise TranslationUnavailable(
            "path_convention", "filesystem-absolute", "repo-relative",
            suggestion="empty path; nothing to translate",
        )
    if not value.startswith("/"):
        # Already relative — pass through.
        return value, "exact"
    root, root_equivalence = _resolve_repo_root()
    prefix = root + "/"
    if value.startswith(prefix):
        return value[len(prefix):], root_equivalence
    if value == root:
        return "", root_equivalence
    # The path is absolute but doesn't sit under the resolved root.
    # We strip up to the last segment that matches the cwd's basename
    # as a best-effort fallback; the equivalence reflects that this
    # is heuristic.
    import os
    cwd_basename = os.path.basename(os.getcwd())
    if cwd_basename and f"/{cwd_basename}/" in value:
        idx = value.rfind(f"/{cwd_basename}/") + len(cwd_basename) + 2
        return value[idx:], "contextual"
    raise TranslationUnavailable(
        "path_convention", "filesystem-absolute", "repo-relative",
        suggestion=(
            f"path {value!r} does not sit under the resolved repo "
            f"root {root!r}. Set BULLA_REPO_ROOT or run from inside "
            "the relevant git repository."
        ),
    )


@register("path_convention", "repo-relative", "filesystem-absolute")
def _path_repo_relative_to_abs(value: str) -> tuple[str, str]:
    """Convert a repo-relative path to an absolute filesystem path
    by prepending the resolved repo root."""
    if value.startswith("/"):
        return value, "exact"  # already absolute
    root, root_equivalence = _resolve_repo_root()
    return f"{root}/{value}" if value else root, root_equivalence


__all__ = [
    "TranslationEvidence",
    "TranslationResult",
    "TranslationUnavailable",
    "TranslatorFn",
    "register",
    "registered_pairs",
    "translate",
]
