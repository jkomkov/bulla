"""Generate the ISO 4217 currency-codes pack from pycountry data.

Run from the bulla repo root:

    python scripts/standards-ingest/build_iso_4217.py \
        > src/bulla/packs/seed/iso-4217.yaml

Output pack uses Extensions A (license metadata), C (derives_from
provenance), and D (alias-form known_values for alpha/numeric
dual-coding).

The ~180 active ISO 4217 currencies fit comfortably inline; no
``values_registry`` pointer is needed at this scale (the threshold for
registry indirection is reached around ICD-10's ~70k codes).

Note: pycountry is a dev/build dependency only — used at ingest time
to produce the deterministic YAML that ships in the pack. The
generated YAML is the canonical artifact; pycountry is not required
at runtime.
"""

from __future__ import annotations

import datetime as _dt
import sys

import pycountry
import yaml


def build_pack() -> dict:
    """Return the parsed pack dict for ISO 4217."""
    today = _dt.date.today().isoformat()

    known_values: list[dict] = []
    for cur in sorted(pycountry.currencies, key=lambda c: c.alpha_3):
        # Each currency becomes one Extension D alias-form entry.
        # Canonical = alpha-3 (the most commonly written form);
        # numeric code lives as both an alias (so a field whose
        # enum is numeric-only classifies under this dimension) and
        # as a source_codes entry (so consumers can read the
        # standard-tagged form directly).
        item: dict = {
            "canonical": cur.alpha_3,
            "aliases": [cur.numeric],
            "source_codes": {
                "ISO-4217-alpha": cur.alpha_3,
                "ISO-4217-numeric": cur.numeric,
            },
        }
        known_values.append(item)

    pack = {
        "pack_name": "iso-4217",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "CC0-1.0",
            "source_url": (
                "https://www.six-group.com/en/products-services/"
                "financial-information/data-standards.html"
            ),
            "registry_license": "open",
            "attribution": "sha256:iso-4217-notices",
        },
        "derives_from": {
            "standard": "ISO-4217",
            "version": (
                # pycountry tracks the official ISO 4217 maintenance
                # agency (SIX) revisions; the version captured here
                # is the pycountry data snapshot we ingest. Bump this
                # whenever the seed pack is regenerated.
                f"pycountry-snapshot-{today}"
            ),
            "source_uri": (
                "https://www.six-group.com/dam/download/financial-information/"
                "data-center/iso-currrency/lists/list-one.xml"
            ),
        },
        "dimensions": {
            "currency_code": {
                "description": (
                    "ISO 4217 currency code. The canonical form is the "
                    "alpha-3 code (USD, EUR, JPY, ...); the numeric "
                    "code (840, 978, 392, ...) is recorded as an alias "
                    "so a field whose enum uses numeric-only codes "
                    "still classifies under this dimension. "
                    "Standards-tagged source_codes preserve the original "
                    "ISO-4217-alpha and ISO-4217-numeric forms for "
                    "consumers that need them."
                ),
                "field_patterns": [
                    "*_currency",
                    "*_currency_code",
                    "currency",
                    "currency_code",
                    "ccy",
                    "ccy_code",
                    "ccyCode",
                ],
                "description_keywords": [
                    "currency code",
                    "iso 4217",
                    "iso-4217",
                    "alpha-3 currency",
                    "numeric currency",
                ],
                "domains": ["financial", "universal"],
                "known_values": known_values,
            },
        },
        "mappings": {
            # Demonstration of Extension E: a passive cross-pack
            # mapping from this pack's canonical alpha-3 form to a
            # hypothetical numeric-only target pack. Real consumers
            # don't need this when both representations live inside
            # the alias structure; the block exists here to exercise
            # the mappings codepath at ingest time and to illustrate
            # the pattern for downstream packs (FIX→SWIFT, GS1→ISO)
            # that will need it more.
            "iso-4217-numeric-only": {
                "currency_numeric": [
                    {
                        "from": cur.alpha_3,
                        "to": cur.numeric,
                        "equivalence": "exact",
                    }
                    for cur in sorted(
                        pycountry.currencies, key=lambda c: c.alpha_3
                    )
                ],
            },
        },
    }
    return pack


def main() -> None:
    pack = build_pack()
    yaml.dump(pack, sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
