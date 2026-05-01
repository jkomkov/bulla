"""Generate the ISO 3166-1 country-codes pack from pycountry data.

Covers the ~250 ISO 3166-1 country codes with alpha-2, alpha-3, and
numeric forms, all wired through Extension D's alias structure so a
field whose enum is alpha-2-only ("US", "GB", "FR") classifies the
same as alpha-3-only ("USA", "GBR", "FRA") or numeric-only
("840", "826", "250").

Subdivisions (ISO 3166-2) are deferred to a follow-on pack — too many
codes (~5k) and too domain-specific to live in the country-code pack.
A dedicated ``iso-3166-2.yaml`` should ship as a separate seed when
the use case appears.
"""

from __future__ import annotations

import datetime as _dt
import sys

import pycountry
import yaml


def build_pack() -> dict:
    today = _dt.date.today().isoformat()

    known_values: list[dict] = []
    for c in sorted(pycountry.countries, key=lambda c: c.alpha_2):
        # Some historic codes lack numeric; skip those for safety.
        numeric = getattr(c, "numeric", "")
        aliases: list[str] = [c.alpha_3]
        if numeric:
            aliases.append(numeric)
        source_codes: dict[str, str] = {
            "ISO-3166-1-alpha-2": c.alpha_2,
            "ISO-3166-1-alpha-3": c.alpha_3,
        }
        if numeric:
            source_codes["ISO-3166-1-numeric"] = numeric
        known_values.append({
            "canonical": c.alpha_2,
            "aliases": aliases,
            "source_codes": source_codes,
        })

    pack = {
        "pack_name": "iso-3166",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "CC0-1.0",
            "source_url": "https://www.iso.org/iso-3166-country-codes.html",
            "registry_license": "open",
        },
        "derives_from": {
            "standard": "ISO-3166-1",
            "version": f"pycountry-snapshot-{today}",
            "source_uri": "https://www.iso.org/iso-3166-country-codes.html",
        },
        "dimensions": {
            "country_code": {
                "description": (
                    "ISO 3166-1 country code. Canonical form is alpha-2 "
                    "(US, GB, FR, ...); alpha-3 and numeric forms are "
                    "recorded as aliases so a field whose enum uses any "
                    "single form classifies under this dimension."
                ),
                "field_patterns": [
                    "country",
                    "country_code",
                    "country_id",
                    "*_country",
                    "*_country_code",
                    "country_of_origin",
                    "origin_country",
                    "destination_country",
                    "ship_country",
                    "billing_country",
                    "shipping_country",
                    "iso_country",
                    "iso_country_code",
                    "nationality",
                    "domicile",
                ],
                "description_keywords": [
                    "country code",
                    "iso 3166",
                    "iso-3166",
                    "alpha-2 country",
                    "alpha-3 country",
                    "numeric country",
                    "country of origin",
                ],
                "domains": ["universal"],
                "known_values": known_values,
            },
        },
    }
    return pack


def main() -> None:
    pack = build_pack()
    yaml.dump(pack, sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
