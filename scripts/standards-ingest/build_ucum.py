"""Generate the UCUM (Unified Code for Units of Measure) pack.

UCUM has ~600 base + derived units. The seed inlines the most-common
~40 units across SI, prefixed-SI, customary, and time/duration; the
authoritative ucum-essence.xml lives behind the values_registry pointer.

UCUM is *the* universal unit-of-measure system for science and
medicine — Mars Climate Orbiter, Patriot missile Dhahran, Singapore
lidocaine overdose are all unit-mismatch failures that UCUM-aware
classifiers would have flagged at composition time.
"""

from __future__ import annotations

import datetime as _dt
import sys

import yaml

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from _hash_lookup import lookup as _hash_for  # noqa: E402


# Most-common UCUM units across SI, customary, and clinical. Aliases
# include prefixed forms (kg/g/mg/ug/ng for mass) and the customary
# spellings that production systems mix in.
INLINE_SEED = [
    # Length
    {"canonical": "m",   "aliases": ["meter", "metre"]},
    {"canonical": "km",  "aliases": ["kilometer", "kilometre"]},
    {"canonical": "cm",  "aliases": ["centimeter", "centimetre"]},
    {"canonical": "mm",  "aliases": ["millimeter", "millimetre"]},
    {"canonical": "in",  "aliases": ["inch", "inches"]},
    {"canonical": "ft",  "aliases": ["foot", "feet"]},
    {"canonical": "mi",  "aliases": ["mile", "statute_mile"]},
    {"canonical": "[mi_us]", "aliases": ["us_survey_mile"]},
    # Mass
    {"canonical": "kg",  "aliases": ["kilogram"]},
    {"canonical": "g",   "aliases": ["gram", "gramme"]},
    {"canonical": "mg",  "aliases": ["milligram"]},
    {"canonical": "ug",  "aliases": ["microgram", "mcg", "[u]g"]},
    {"canonical": "ng",  "aliases": ["nanogram"]},
    {"canonical": "[lb_av]", "aliases": ["lb", "pound", "pounds_avoirdupois"]},
    {"canonical": "[oz_av]", "aliases": ["oz", "ounce", "ounces_avoirdupois"]},
    # Time
    {"canonical": "s",   "aliases": ["sec", "second", "seconds"]},
    {"canonical": "min", "aliases": ["minute", "minutes"]},
    {"canonical": "h",   "aliases": ["hr", "hour", "hours"]},
    {"canonical": "d",   "aliases": ["day", "days"]},
    {"canonical": "wk",  "aliases": ["week", "weeks"]},
    {"canonical": "mo",  "aliases": ["month", "months"]},
    {"canonical": "a",   "aliases": ["yr", "year", "years"]},
    # Volume
    {"canonical": "L",   "aliases": ["l", "liter", "litre"]},
    {"canonical": "mL",  "aliases": ["ml", "milliliter", "millilitre"]},
    {"canonical": "uL",  "aliases": ["ul", "microliter", "microlitre"]},
    {"canonical": "[gal_us]", "aliases": ["gal", "gallon", "us_gallon"]},
    # Pressure / force
    {"canonical": "Pa",  "aliases": ["pascal"]},
    {"canonical": "kPa", "aliases": ["kilopascal"]},
    {"canonical": "bar", "aliases": ["bars"]},
    {"canonical": "mm[Hg]", "aliases": ["mmhg", "torr"]},
    # Energy
    {"canonical": "J",   "aliases": ["joule", "joules"]},
    {"canonical": "kJ",  "aliases": ["kilojoule"]},
    {"canonical": "cal", "aliases": ["calorie", "small_calorie"]},
    {"canonical": "kcal","aliases": ["kilocalorie", "Cal", "food_calorie"]},
    {"canonical": "Wh",  "aliases": ["watt_hour"]},
    {"canonical": "kWh", "aliases": ["kilowatt_hour"]},
    # Substance / clinical (where most Mars-Orbiter-class incidents live)
    {"canonical": "mol", "aliases": ["mole"]},
    {"canonical": "mmol","aliases": ["millimole"]},
    {"canonical": "umol","aliases": ["micromole"]},
    {"canonical": "U",   "aliases": ["unit", "international_unit_clinical"]},
    {"canonical": "[iU]","aliases": ["IU", "international_unit"]},
    # Temperature
    {"canonical": "Cel", "aliases": ["C", "celsius", "degC"]},
    {"canonical": "[degF]", "aliases": ["F", "fahrenheit", "degF"]},
    {"canonical": "K",   "aliases": ["kelvin"]},
    # Force (Mars Climate Orbiter dimension)
    {"canonical": "N",   "aliases": ["newton", "newtons"]},
    {"canonical": "[lbf_av]", "aliases": ["lbf", "pound_force", "pounds_force"]},
    {"canonical": "N.s", "aliases": ["newton_second", "n_s", "ns"]},
    {"canonical": "[lbf_av].s", "aliases": ["lbf_s", "pound_force_second"]},
]


def build_pack() -> dict:
    today = _dt.date.today().isoformat()
    pack = {
        "pack_name": "ucum",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "Public-Domain",
            "source_url": "https://ucum.org/",
            "registry_license": "open",
        },
        "derives_from": {
            "standard": "UCUM",
            "version": f"snapshot-{today}",
            "source_uri": "https://ucum.org/ucum-essence.xml",
        },
        "dimensions": {
            "unit_of_measure": {
                "description": (
                    "UCUM (Unified Code for Units of Measure) atomic unit. "
                    "The full registry has ~600 base + derived units; the "
                    "inline seed covers the ~50 most-common units across "
                    "length, mass, time, volume, energy, substance, and "
                    "force. Mars Climate Orbiter (lbf·s vs N·s), Patriot "
                    "missile Dhahran (precision drift), and clinical "
                    "tenfold mg/mcg errors are all UCUM-detectable at "
                    "composition time."
                ),
                "field_patterns": [
                    "unit",
                    "units",
                    "*_unit",
                    "*_units",
                    "unit_of_measure",
                    "uom",
                    "*_uom",
                    "ucum",
                    "*_ucum",
                    "amount_unit",
                    "dose_unit",
                    "rate_unit",
                ],
                "description_keywords": [
                    "ucum",
                    "unit of measure",
                    "unit of measurement",
                    "si unit",
                    "metric unit",
                    "imperial unit",
                ],
                "domains": ["scientific", "universal"],
                "known_values": INLINE_SEED,
                "values_registry": {
                    "uri": "https://ucum.org/ucum-essence.xml",
                    "hash": _hash_for("ucum", "unit_of_measure", "snapshot"),
                    "version": "snapshot",
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
