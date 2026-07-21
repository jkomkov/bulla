#!/usr/bin/env python3
"""Fail-closed role separation and sample-size gate for foreign-authored seams."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from bulla.experimental.invention import SeamProblem


EXPECTED = {
    "schema_version", "engine_commit", "frsl_1_spec_sha256", "implementation_team_ids",
    "author_ids", "adjudicator_ids", "target_n_per_domain", "domains", "instances",
}
INSTANCE_EXPECTED = {"id", "domain", "stratum", "author_id", "problem_hash", "problem"}
STRATA = ("hidden_generative_contract", "natural_expert")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(canon(value).encode("utf-8")).hexdigest()


def _identity_list(value, *, name: str, minimum: int, errors: list[str]) -> set[str]:
    if (
        not isinstance(value, list)
        or len(value) < minimum
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        errors.append(
            f"{name} must contain at least {minimum} unique non-empty string identities"
        )
        return set()
    return set(value)


def validate(document: dict, *, allow_incomplete: bool) -> dict:
    errors = []
    if not isinstance(document, dict):
        return {"ok": False, "ready_for_blind_evaluation": False, "errors": ["manifest must be an object"]}
    if set(document) != EXPECTED:
        errors.append(f"manifest fields must be exactly {sorted(EXPECTED)}")
    if errors:
        return {"ok": False, "ready_for_blind_evaluation": False, "errors": errors}
    if document["schema_version"] != "0.2-external-pilot":
        errors.append("schema_version must be 0.2-external-pilot")
    if not isinstance(document["engine_commit"], str) or not COMMIT_RE.fullmatch(document["engine_commit"]):
        errors.append("engine_commit must be a frozen 40-character lowercase Git commit")
    if not isinstance(document["frsl_1_spec_sha256"], str) or not DIGEST_RE.fullmatch(document["frsl_1_spec_sha256"]):
        errors.append("frsl_1_spec_sha256 must be a sha256 digest")
    roles = {
        "implementation": _identity_list(
            document["implementation_team_ids"],
            name="implementation_team_ids",
            minimum=1,
            errors=errors,
        ),
        "authors": _identity_list(
            document["author_ids"], name="author_ids", minimum=3, errors=errors
        ),
        "adjudicators": _identity_list(
            document["adjudicator_ids"],
            name="adjudicator_ids",
            minimum=3,
            errors=errors,
        ),
    }
    for left, right in (("implementation", "authors"), ("implementation", "adjudicators"), ("authors", "adjudicators")):
        overlap = roles[left] & roles[right]
        if overlap:
            errors.append(f"role overlap {left}/{right}: {sorted(overlap)}")
    domains = document["domains"]
    if (
        not isinstance(domains, list)
        or len(domains) < 3
        or any(not isinstance(domain, str) or not domain for domain in domains)
        or len(set(domains)) != len(domains)
    ):
        errors.append("at least three unique domains are required")
        domains = []
    target = document["target_n_per_domain"]
    if isinstance(target, bool) or not isinstance(target, int) or target != 100:
        errors.append("target_n_per_domain must equal the preregistered value 100")
        target = 100
    instances = document["instances"]
    if not isinstance(instances, list):
        errors.append("instances must be an array")
        instances = []
    seen = set()
    counts = Counter()
    for index, item in enumerate(instances):
        if not isinstance(item, dict) or set(item) != INSTANCE_EXPECTED:
            errors.append(f"instance {index} fields must be exactly {sorted(INSTANCE_EXPECTED)}")
            continue
        if not isinstance(item["id"], str) or not item["id"]:
            errors.append(f"instance {index} id must be a non-empty string")
            continue
        if item["id"] in seen:
            errors.append(f"duplicate instance id {item['id']!r}")
        seen.add(item["id"])
        if not isinstance(item["domain"], str) or item["domain"] not in domains:
            errors.append(f"instance {item['id']} has undeclared domain")
        if item["stratum"] not in STRATA:
            errors.append(f"instance {item['id']} has undeclared stratum")
        if not isinstance(item["author_id"], str) or item["author_id"] not in roles["authors"]:
            errors.append(f"instance {item['id']} has undeclared author")
        if not isinstance(item["problem_hash"], str) or not DIGEST_RE.fullmatch(item["problem_hash"]):
            errors.append(f"instance {item['id']} has malformed problem hash")
        try:
            problem = SeamProblem.from_dict(item["problem"])
            if item["problem_hash"] != problem.problem_hash:
                errors.append(f"instance {item['id']} problem hash mismatch")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"instance {item.get('id', index)} invalid problem: {exc}")
        if isinstance(item["domain"], str):
            counts[item["domain"]] += 1
            if item.get("stratum") in STRATA:
                counts[(item["domain"], item["stratum"])] += 1
    short = {domain: target - counts[domain] for domain in domains if counts[domain] < target}
    stratum_target = target // len(STRATA)
    stratum_short = {
        f"{domain}/{stratum}": stratum_target - counts[(domain, stratum)]
        for domain in domains
        for stratum in STRATA
        if counts[(domain, stratum)] < stratum_target
    }
    ready = not errors and not short and not stratum_short
    if (short or stratum_short) and not allow_incomplete:
        errors.append(
            f"sample-size gate not met: domains={short} strata={stratum_short}"
        )
    return {
        "ok": not errors,
        "ready_for_blind_evaluation": ready,
        "errors": errors,
        "domain_counts": {domain: counts[domain] for domain in domains},
        "stratum_counts": {
            f"{domain}/{stratum}": counts[(domain, stratum)]
            for domain in domains
            for stratum in STRATA
        },
        "shortfall": short,
        "stratum_shortfall": stratum_short,
        "corpus_hash": digest(instances) if not errors else None,
        "manifest_hash": digest(document) if not errors else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        document = json.loads(args.manifest.read_text(encoding="utf-8"))
        result = validate(document, allow_incomplete=args.allow_incomplete)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        result = {"ok": False, "ready_for_blind_evaluation": False, "errors": [str(exc)]}
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
