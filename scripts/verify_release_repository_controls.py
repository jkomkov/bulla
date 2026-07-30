#!/usr/bin/env python3
"""Verify the external GitHub controls required by the release ceremony."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class ControlError(ValueError):
    """A live repository control differs from the pinned release contract."""


def _read_object(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ControlError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise ControlError(f"{label} fields differ from the closed contract")


def verify(
    expected_path: Path,
    immutable_path: Path,
    ruleset_paths: list[Path],
) -> None:
    expected = _read_object(expected_path, "expected controls")
    immutable = _read_object(immutable_path, "immutable release state")
    _exact_keys(
        expected,
        {
            "schema_version",
            "immutable_releases",
            "slot_enforcement_epoch",
            "tag_rulesets",
        },
        "expected controls",
    )
    if (
        expected["schema_version"] != "bulla.release-repository-controls/0.2"
        or expected["immutable_releases"] is not True
        or expected["slot_enforcement_epoch"] != "0.44.2"
        or immutable.get("enabled") is not True
    ):
        raise ControlError("immutable releases are not enabled as required")

    tags = expected["tag_rulesets"]
    if not isinstance(tags, list) or len(tags) != 2:
        raise ControlError("expected controls must name two tag rulesets")
    live_rulesets = [
        _read_object(path, f"tag ruleset {path.name}") for path in ruleset_paths
    ]
    if len(live_rulesets) != len(tags):
        raise ControlError("live tag ruleset count differs")
    by_name = {value.get("name"): value for value in live_rulesets}
    if len(by_name) != len(live_rulesets):
        raise ControlError("live tag ruleset names are duplicated")
    for tag in tags:
        if not isinstance(tag, dict):
            raise ControlError("expected tag ruleset must be an object")
        _exact_keys(
            tag,
            {
                "name",
                "target",
                "enforcement",
                "bypass_actors",
                "include",
                "exclude",
                "required_rules",
            },
            "expected tag ruleset",
        )
        ruleset = by_name.get(tag["name"])
        if ruleset is None:
            raise ControlError(f"live tag ruleset {tag['name']!r} is absent")
        conditions = ruleset.get("conditions")
        ref_name = (
            conditions.get("ref_name") if isinstance(conditions, dict) else None
        )
        if not isinstance(ref_name, dict):
            raise ControlError("live tag ruleset lacks ref-name conditions")
        live_types = {
            rule.get("type")
            for rule in ruleset.get("rules", [])
            if isinstance(rule, dict)
        }
        if (
            ruleset.get("target") != tag["target"]
            or ruleset.get("enforcement") != tag["enforcement"]
            or ruleset.get("bypass_actors") != tag["bypass_actors"]
            or ref_name.get("include") != tag["include"]
            or ref_name.get("exclude") != tag["exclude"]
            or live_types != set(tag["required_rules"])
        ):
            raise ControlError(
                f"live tag ruleset {tag['name']!r} differs from the contract"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--immutable", type=Path, required=True)
    parser.add_argument("--ruleset", type=Path, action="append", required=True)
    args = parser.parse_args()
    try:
        verify(args.expected, args.immutable, args.ruleset)
    except (ControlError, OSError, json.JSONDecodeError) as exc:
        print(f"release repository controls rejected: {exc}")
        return 1
    print("release repository controls verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
