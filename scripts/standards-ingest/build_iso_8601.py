"""Generate the ISO 8601 / RFC 3339 date-time format pack.

ISO 8601 doesn't enumerate values the way ISO 4217 enumerates currencies
— it specifies a *family* of date/time representations. The pack here
captures the most-used representational variants plus the common
non-ISO alternatives that production systems mix in (Unix epoch, US/EU
locale formats), so a field whose schema declares ``"format": "date-time"``
or whose description mentions "iso 8601" or "rfc 3339" classifies under
``temporal_format``.

The base pack already has a ``date_format`` dimension. This pack adds a
more specific ``temporal_format`` dimension that refines it (Dublin
Core dumb-down style) — keeping the base's coarse classification
working while letting downstream consumers see the finer-grained form
where present.
"""

from __future__ import annotations

import datetime as _dt
import sys

import yaml


def build_pack() -> dict:
    today = _dt.date.today().isoformat()
    pack = {
        "pack_name": "iso-8601",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "CC0-1.0",
            "source_url": "https://www.rfc-editor.org/rfc/rfc3339",
            "registry_license": "open",
        },
        "derives_from": {
            "standard": "ISO-8601 / RFC-3339",
            "version": f"snapshot-{today}",
            "source_uri": "https://www.rfc-editor.org/rfc/rfc3339",
        },
        "dimensions": {
            "temporal_format": {
                "description": (
                    "Date / time / datetime representation format. "
                    "Refines the base pack's ``date_format`` dimension "
                    "with finer-grained ISO-8601 / RFC-3339 variants "
                    "(date-only, time-only, datetime, week-date, "
                    "ordinal-date, duration, interval) plus the common "
                    "non-ISO alternatives that production systems mix in."
                ),
                "refines": "date_format",
                "field_patterns": [
                    "*_at",
                    "*_date",
                    "*_time",
                    "*_datetime",
                    "*_timestamp",
                    "created_at",
                    "updated_at",
                    "deleted_at",
                    "scheduled_at",
                    "expires_at",
                    "occurred_at",
                    "issued_at",
                    "due_at",
                    "due_date",
                    "issue_date",
                    "delivery_date",
                    "departure_date",
                    "arrival_date",
                    "*_dt",
                ],
                "description_keywords": [
                    "iso 8601",
                    "iso-8601",
                    "iso8601",
                    "rfc 3339",
                    "rfc3339",
                    "rfc-3339",
                    "datetime",
                    "date-time",
                    "timestamp",
                    "date format",
                    "datetime format",
                    "yyyy-mm-dd",
                    "epoch",
                    "unix time",
                    "unix epoch",
                    "utc time",
                    "iso week",
                    "ordinal date",
                ],
                "domains": ["universal"],
                "known_values": [
                    {
                        "canonical": "iso-8601-datetime",
                        "aliases": [
                            "rfc-3339",
                            "rfc3339",
                            "iso-datetime",
                            "yyyy-mm-ddthh:mm:ssz",
                            "iso-8601",
                        ],
                        "source_codes": {
                            "ISO-8601": "datetime",
                            "RFC-3339": "datetime",
                        },
                    },
                    {
                        "canonical": "iso-8601-date",
                        "aliases": ["yyyy-mm-dd", "iso-date"],
                        "source_codes": {"ISO-8601": "date"},
                    },
                    {
                        "canonical": "iso-8601-time",
                        "aliases": ["hh:mm:ss", "iso-time"],
                        "source_codes": {"ISO-8601": "time"},
                    },
                    {
                        "canonical": "iso-8601-week-date",
                        "aliases": [
                            "yyyy-www",
                            "iso-week",
                            "iso-8601-week",
                        ],
                        "source_codes": {"ISO-8601": "week-date"},
                    },
                    {
                        "canonical": "iso-8601-ordinal-date",
                        "aliases": ["yyyy-ddd", "iso-ordinal"],
                        "source_codes": {"ISO-8601": "ordinal-date"},
                    },
                    {
                        "canonical": "iso-8601-duration",
                        "aliases": ["pnynmndtnhnmns", "iso-duration"],
                        "source_codes": {"ISO-8601": "duration"},
                    },
                    {
                        "canonical": "iso-8601-interval",
                        "aliases": ["start/end", "iso-interval"],
                        "source_codes": {"ISO-8601": "interval"},
                    },
                    {
                        "canonical": "unix-epoch-seconds",
                        "aliases": [
                            "unix",
                            "unix-time",
                            "unix-epoch",
                            "epoch-seconds",
                            "unix-timestamp",
                            "posix-time",
                        ],
                    },
                    {
                        "canonical": "unix-epoch-millis",
                        "aliases": [
                            "epoch-ms",
                            "unix-millis",
                            "epoch-millis",
                            "java-millis",
                        ],
                    },
                    {
                        "canonical": "unix-epoch-micros",
                        "aliases": [
                            "epoch-us",
                            "unix-micros",
                            "epoch-microseconds",
                        ],
                    },
                    {
                        "canonical": "unix-epoch-nanos",
                        "aliases": [
                            "epoch-ns",
                            "unix-nanos",
                            "epoch-nanoseconds",
                        ],
                    },
                    {
                        "canonical": "us-locale-date",
                        "aliases": [
                            "mm/dd/yyyy",
                            "us-mmddyyyy",
                            "us-date",
                        ],
                    },
                    {
                        "canonical": "eu-locale-date",
                        "aliases": [
                            "dd/mm/yyyy",
                            "eu-ddmmyyyy",
                            "eu-date",
                            "uk-date",
                        ],
                    },
                    {
                        "canonical": "excel-serial",
                        "aliases": [
                            "excel-1900",
                            "excel-date",
                            "excel-day-number",
                        ],
                    },
                    {
                        "canonical": "julian-day",
                        "aliases": ["jd", "jdn", "julian-day-number"],
                    },
                    {
                        "canonical": "trading-days",
                        "aliases": ["business-days", "act-business"],
                    },
                    {
                        "canonical": "calendar-days",
                        "aliases": ["act-act", "act-365", "act-360"],
                    },
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
