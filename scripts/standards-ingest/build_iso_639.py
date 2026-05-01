"""Generate the ISO 639 language-codes pack.

Architecturally consistent with IANA MIME and NAICS: a small inline
seed of the most-used languages (documentation), with the
authoritative ~7700-entry ISO 639-3 registry behind a
``values_registry`` pointer. The pack hash strips inline values
(Extension B canonicalization) so curating the seed doesn't drift
the pack hash; the registry pointer is the binding object.

The previous all-inline form produced a 656 KB YAML file, which is
the exact scale problem ``values_registry`` was designed to solve
per the Extension B rationale ("inline known_values produces 3–5 MB
JSON blobs hashed on every load").

Inline seed = the global top-~35 languages by speaker count (Ethnologue
2024) plus a handful of programmatically common locales (Hebrew,
Czech, Danish, Norwegian, Finnish). pycountry resolves each alpha-2
to its canonical alpha-3 + name; the registry pointer at SIL is the
authoritative source for the remaining ~7700 codes.
"""

from __future__ import annotations

import datetime as _dt
import sys

import pycountry
import yaml

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from _hash_lookup import lookup as _hash_for  # noqa: E402


# Curated global top-~50 by L1+L2 speaker count, plus common-software
# locales (every European language with substantial software presence,
# every G20-language). Each entry MUST resolve via pycountry's alpha_2
# lookup; if any fails, the build script will raise so we notice.
#
# Goal: cover every language a real-world software product is likely
# to localize into without falling into the long tail of ISO 639-3
# entries (which live behind values_registry).
INLINE_SEED_ALPHA_2 = [
    # Global top-30 by speaker count (Ethnologue 2024-ish)
    "en", "zh", "hi", "es", "ar", "bn", "fr", "pt",
    "ru", "ur", "id", "de", "ja", "sw", "mr", "te",
    "tr", "ta", "ko", "vi", "fa", "pl", "uk", "it",
    "my", "th", "ms", "nl", "sv", "el",
    # Additional European languages with strong software-localization
    # presence
    "he", "da", "no", "fi", "cs", "sk", "hu", "ro",
    "bg", "hr", "sr", "sl", "lt", "lv", "et", "is",
    "ca", "ga",
    # Additional widely-localized non-European languages
    "tl",  # Tagalog
    "ne",  # Nepali
    "si",  # Sinhala
    "km",  # Khmer
    "lo",  # Lao
    "ka",  # Georgian
    "hy",  # Armenian
    "az",  # Azerbaijani
    "kk",  # Kazakh
    "uz",  # Uzbek
    "am",  # Amharic
    "yo",  # Yoruba
    "ig",  # Igbo
    "ha",  # Hausa
    "zu",  # Zulu
]


def build_pack() -> dict:
    today = _dt.date.today().isoformat()

    known_values: list[dict] = []
    seen_canonical: set[str] = set()
    for code in INLINE_SEED_ALPHA_2:
        lang = pycountry.languages.get(alpha_2=code)
        if lang is None:
            raise RuntimeError(
                f"INLINE_SEED_ALPHA_2 entry {code!r} not resolvable "
                f"via pycountry — fix the seed list."
            )
        if code in seen_canonical:
            continue
        seen_canonical.add(code)
        known_values.append({
            "canonical": code,
            "aliases": [lang.alpha_3] if lang.alpha_3 != code else [],
            "source_codes": {
                "ISO-639-1": code,
                "ISO-639-3": lang.alpha_3,
            },
        })

    pack = {
        "pack_name": "iso-639",
        "pack_version": "0.2.0",
        "license": {
            "spdx_id": "CC0-1.0",
            "source_url": "https://iso639-3.sil.org/code_tables/639/data",
            "registry_license": "open",
            "attribution": "sha256:iso-639-notices",
        },
        "derives_from": {
            "standard": "ISO-639-3",
            "version": f"sil-snapshot-{today}",
            "source_uri": "https://iso639-3.sil.org/sites/iso639-3/files/downloads/iso-639-3.tab",
        },
        "dimensions": {
            "language_code": {
                "description": (
                    "ISO 639 language code. Canonical form is the "
                    "ISO 639-1 alpha-2 code where one exists "
                    "(en, fr, ja, ...); the ISO 639-3 alpha-3 code "
                    "(eng, fra, jpn, ...) is recorded as an alias "
                    "and as a source_code so either form classifies "
                    "under this dimension. The inline seed covers "
                    "the global top-~35 most-spoken languages plus "
                    "common-software locales for documentation; the "
                    "authoritative ~7700-entry SIL registry lives "
                    "behind the values_registry pointer (per "
                    "Extension B, the inline list is stripped from "
                    "the pack hash so seed curation doesn't produce "
                    "drift)."
                ),
                "field_patterns": [
                    "language",
                    "language_code",
                    "lang",
                    "lang_code",
                    "locale",
                    "locale_code",
                    "*_language",
                    "*_lang",
                    "*_locale",
                    "preferred_language",
                ],
                "description_keywords": [
                    "language code",
                    "iso 639",
                    "iso-639",
                    "bcp-47",
                    "bcp 47",
                    "alpha-2 language",
                    "alpha-3 language",
                    "ietf language tag",
                ],
                "domains": ["universal"],
                "known_values": known_values,
                "values_registry": {
                    "uri": (
                        "https://iso639-3.sil.org/sites/iso639-3/"
                        "files/downloads/iso-639-3.tab"
                    ),
                    "hash": _hash_for("iso-639", "language_code", "sil-snapshot"),
                    "version": "sil-snapshot",
                },
            },
        },
    }
    return pack


def main() -> None:
    pack = build_pack()
    yaml.dump(pack, sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
