"""Receipt coverage against records Bulla did not mint.

Coverage is the set difference between an external record of actions and the
set of valid receipts that claim those actions.  PyPI is the primary release
anchor: it answers which versions actually shipped and which file digests PyPI
accepted.  Git remains available as a secondary, strictly-SemVer anchor.

The module deliberately keeps provenance separate from validity:

``contemporaneous``
    Minted only after PyPI accepted the exact wheel and sdist, with the
    Integrity API recorded in the receipt.
``reconstructed``
    A later, explicitly labelled reconstruction of a historical release.
``candidate``
    A pre-publication build.  Candidates never count as release coverage.
``missing``
    PyPI records the release but no valid receipt covers it.
``invalid``
    A receipt exists but fails schema, hash, signature, or PyPI-artifact checks.

No aggregate is authoritative without the release rows and invalid-artifact
list beside it.  Consumers should render the instrument, not a vanity number.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from bulla.receipt_parser import (
    ReceiptParseError,
    ReceiptParseLimits,
    load_action_receipt_document,
)


# The immutable enforcement epoch: every release from this version forward must
# carry a contemporaneous receipt. Defined normatively in
# docs/EVIDENCE-CONTRACT.md; the epoch never moves backward and history is
# never reclassified to improve a headline number.
ENFORCEMENT_EPOCH = "0.44.0"

PYPI_PROJECT_URL = "https://pypi.org/pypi/{project}/json"
PYPI_INTEGRITY_URL = (
    "https://pypi.org/integrity/{project}/{version}/{filename}/provenance"
)
_SEMVER_RE = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_PACKAGE_RELEASE_TAG_RE = re.compile(
    r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
)


def _normalize_version(tag_or_version: str) -> str:
    """Normalize the leading ``v`` used by Git release tags."""
    value = tag_or_version.strip()
    return value[1:] if value.startswith("v") else value


def _semver_key(value: str) -> tuple:
    match = _SEMVER_RE.fullmatch(value.strip())
    if match is None:
        return (-1, -1, -1, ())
    prerelease = match.group(4)
    # Stable releases sort after prereleases at the same numeric version.
    pre_key = ((1, 0, ""),) if prerelease is None else tuple(
        (0, 0, int(part)) if part.isdigit() else (0, 1, part)
        for part in prerelease.split(".")
    )
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), pre_key)


def is_strict_semver(value: str) -> bool:
    return _SEMVER_RE.fullmatch(value.strip()) is not None


def is_package_release_tag(value: str) -> bool:
    """True only for stable ``vX.Y.Z`` package tags.

    SemVer permits arbitrary prerelease labels, so a generic SemVer parser
    would admit experiment tags such as ``v0.1.0-replication``. Git coverage
    uses this narrower predicate; release candidates remain a separate class.
    """
    return _PACKAGE_RELEASE_TAG_RE.fullmatch(value.strip()) is not None


def git_release_tags(match: str = "v[0-9]*", *, repo: str = ".") -> list[str]:
    """Return only stable ``vX.Y.Z`` package-release tags.

    ``git tag --list v[0-9]*`` also matches SemVer-valid prerelease labels such
    as ``v0.1.0-replication``. The narrower parser prevents workflow,
    candidate, and experiment tags from entering the release denominator.
    """
    result = subprocess.run(
        ["git", "-C", repo, "tag", "--list", match],
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(
        {
            tag.strip()
            for tag in result.stdout.splitlines()
            if is_package_release_tag(tag.strip())
        },
        key=_semver_key,
    )


def _read_json_url(url: str, *, accept: str = "application/json", timeout: int = 30) -> dict:
    request = Request(
        url,
        headers={"Accept": accept, "User-Agent": "bulla-coverage/0.44"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS API
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read {url}: {exc}") from exc


def fetch_pypi_project(project: str = "bulla") -> dict:
    """Fetch PyPI's project JSON record."""
    return _read_json_url(PYPI_PROJECT_URL.format(project=quote(project)))


def load_pypi_project(snapshot: str | Path) -> dict:
    """Load a committed PyPI project snapshot for offline/reproducible checks."""
    return json.loads(Path(snapshot).read_text(encoding="utf-8"))


def pypi_release_versions(project_doc: dict) -> list[str]:
    """Published strict-SemVer releases with at least one PyPI file."""
    releases = project_doc.get("releases") or {}
    versions = [
        version
        for version, files in releases.items()
        if is_strict_semver(version) and isinstance(files, list) and files
    ]
    return sorted(set(versions), key=_semver_key)


def integrity_url(project: str, version: str, filename: str) -> str:
    return PYPI_INTEGRITY_URL.format(
        project=quote(project), version=quote(version), filename=quote(filename)
    )


def fetch_pypi_provenance(project: str, version: str, filename: str) -> dict:
    return _read_json_url(
        integrity_url(project, version, filename),
        accept="application/vnd.pypi.integrity.v1+json",
    )


def _receipt_provenance(doc: dict, path: Path) -> str:
    producer = doc.get("producer") or {}
    if "candidates" in path.parts or producer.get("minted") in {
        "release-candidate-build",
        "candidate",
    }:
        return "candidate"
    if producer.get("minted") == "post-publication":
        return "contemporaneous"
    if producer.get("reconstructed") or "retroactive" in str(producer.get("note", "")).lower():
        return "reconstructed"
    return "invalid"


def _receipt_version(doc: dict) -> str:
    action = doc.get("action") or {}
    subject = action.get("subject") or {}
    return _normalize_version(str(subject.get("version") or subject.get("git_tag") or ""))


def _verify_receipt_doc(doc: dict) -> tuple[bool, str, str]:
    """Return validity, verified rung, and failure detail.

    Signed receipts must reach ``attestation``.  Merely recomputing their hashes
    while skipping an installed crypto verifier is not enough to count them.
    """
    try:
        from bulla.action_receipt import verify_receipt

        result = verify_receipt(doc)
    except (Exception, ImportError) as exc:
        return False, "none", f"verification error: {exc}"
    if not result.ok:
        return False, result.verified_to, "; ".join(result.reasons)
    if doc.get("signature") is not None and result.verified_to != "attestation":
        return False, result.verified_to, "signed receipt did not reach attestation verification"
    return True, result.verified_to, ""


def inspect_release_receipts(receipts_dir: str | Path) -> dict:
    """Validate and classify every JSON receipt under ``receipts_dir``."""
    root = Path(receipts_dir)
    records: list[dict] = []
    invalid: list[dict] = []
    if not root.is_dir():
        return {"receipts": records, "invalid_receipts": invalid}

    for path in sorted(root.rglob("*.json")):
        # Generated coverage/project snapshots are evidence inputs, not receipts.
        if path.name in {"coverage.json", "pypi-project.json"}:
            continue
        relative = str(path.relative_to(root))
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            invalid.append({"path": relative, "reason": f"invalid JSON: {exc}"})
            continue
        if doc.get("kind") != "action_receipt" or (doc.get("action") or {}).get("type") != "package.release":
            continue
        version = _receipt_version(doc)
        if not is_strict_semver(version):
            invalid.append({"path": relative, "version": version, "reason": "release version is not strict SemVer"})
            continue
        ok, verified_to, reason = _verify_receipt_doc(doc)
        provenance = _receipt_provenance(doc, path)
        if not ok or provenance == "invalid":
            invalid.append(
                {
                    "path": relative,
                    "version": version,
                    "verified_to": verified_to,
                    "reason": reason or "receipt has no recognized provenance tier",
                }
            )
            continue
        records.append(
            {
                "path": relative,
                "version": version,
                "provenance": provenance,
                "verified_to": verified_to,
                "document": doc,
            }
        )
    return {"receipts": records, "invalid_receipts": invalid}


def _artifact_match(doc: dict, release_files: list[dict]) -> tuple[bool, list[str]]:
    evidence = {
        str(item.get("name")): str(item.get("hash"))
        for item in (doc.get("evidence_refs") or [])
    }
    by_digest = {
        "sha256:" + str((file.get("digests") or {}).get("sha256")): str(file.get("filename"))
        for file in release_files
        if (file.get("digests") or {}).get("sha256")
    }
    matched: list[str] = []
    for required in ("wheel", "sdist"):
        digest = evidence.get(required)
        if not digest or digest not in by_digest:
            return False, matched
        matched.append(by_digest[digest])
    return True, matched


def _provenance_identity(provenance: dict, expected_repository: str | None) -> tuple[bool, str]:
    bundles = provenance.get("attestation_bundles") or []
    if not bundles:
        return False, "Integrity API returned no attestation bundles"
    publishers = [bundle.get("publisher") or {} for bundle in bundles]
    if expected_repository and not any(
        publisher.get("kind") == "GitHub"
        and publisher.get("repository") == expected_repository
        for publisher in publishers
    ):
        return False, f"no GitHub Trusted Publisher for {expected_repository}"
    if not any(bundle.get("attestations") for bundle in bundles):
        return False, "Integrity API returned an empty attestation bundle"
    return True, ""


def pypi_coverage(
    receipts_dir: str | Path,
    *,
    project: str = "bulla",
    project_doc: dict | None = None,
    verify_integrity: bool = True,
    expected_repository: str | None = "jkomkov/bulla",
    provenance_fetcher: Callable[[str, str, str], dict] = fetch_pypi_provenance,
) -> dict:
    """Reconcile valid release receipts against PyPI's published record."""
    project_doc = project_doc or fetch_pypi_project(project)
    inspected = inspect_release_receipts(receipts_dir)
    release_versions = pypi_release_versions(project_doc)
    release_files = project_doc.get("releases") or {}
    candidates = sorted(
        [
            {k: record[k] for k in ("version", "path", "verified_to")}
            for record in inspected["receipts"]
            if record["provenance"] == "candidate"
        ],
        key=lambda item: _semver_key(item["version"]),
    )
    by_version: dict[str, list[dict]] = {}
    for record in inspected["receipts"]:
        if record["provenance"] != "candidate":
            by_version.setdefault(record["version"], []).append(record)

    rows: list[dict] = []
    invalid_receipts = list(inspected["invalid_receipts"])
    for version in release_versions:
        files = release_files.get(version) or []
        usable: dict | None = None
        artifact_files: list[str] = []
        row_failures: list[dict] = []
        for record in by_version.get(version, []):
            matches, matched = _artifact_match(record["document"], files)
            if not matches:
                row_failures.append(
                    {
                        "path": record["path"],
                        "version": version,
                        "reason": "receipt wheel/sdist digests do not match PyPI",
                    }
                )
                continue
            usable = record
            artifact_files = matched
            # A contemporaneous receipt must record and resolve PyPI provenance.
            if record["provenance"] == "contemporaneous":
                roots = ((record["document"].get("anchor_ref") or {}).get("root_of_trust") or {})
                recorded_urls = roots.get("integrity_api") or []
                if not recorded_urls:
                    row_failures.append(
                        {
                            "path": record["path"],
                            "version": version,
                            "reason": "contemporaneous receipt has no Integrity API references",
                        }
                    )
                    usable = None
                    continue
                if verify_integrity:
                    for filename in matched:
                        try:
                            provenance = provenance_fetcher(project, version, filename)
                            ok, reason = _provenance_identity(provenance, expected_repository)
                        except RuntimeError as exc:
                            ok, reason = False, str(exc)
                        if not ok:
                            row_failures.append(
                                {"path": record["path"], "version": version, "reason": reason}
                            )
                            usable = None
                            break
            if usable is not None:
                break

        invalid_receipts.extend(row_failures)
        if usable is None:
            status = "invalid" if row_failures else "missing"
            rows.append(
                {
                    "version": version,
                    "status": status,
                    "receipt": row_failures[0]["path"] if row_failures else None,
                    "artifacts": [],
                }
            )
        else:
            rows.append(
                {
                    "version": version,
                    "status": usable["provenance"],
                    "receipt": usable["path"],
                    "verified_to": usable["verified_to"],
                    "artifacts": artifact_files,
                }
            )

    counts = {
        status: sum(1 for row in rows if row["status"] == status)
        for status in ("contemporaneous", "reconstructed", "missing", "invalid")
    }
    receipted = counts["contemporaneous"] + counts["reconstructed"]
    total = len(rows)
    epoch_key = _semver_key(ENFORCEMENT_EPOCH)
    epoch_rows = [row for row in rows if _semver_key(row["version"]) >= epoch_key]
    forward_contemporaneous = sum(
        1 for row in epoch_rows if row["status"] == "contemporaneous"
    )

    def _metric(numerator: int, denominator: int) -> dict:
        return {
            "receipted": numerator,
            "total": denominator,
            "coverage": round(numerator / denominator, 4) if denominator else 1.0,
        }

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "anchor": "pypi",
        "project": project,
        "source": PYPI_PROJECT_URL.format(project=project),
        "total_anchored": total,
        "receipted": receipted,
        "coverage": round(receipted / total, 4) if total else 1.0,
        "release_receipt_required_since": ENFORCEMENT_EPOCH,
        "metrics": {
            "forward_contemporaneous_since_epoch": _metric(
                forward_contemporaneous, len(epoch_rows)
            ),
            "all_time_contemporaneous": _metric(counts["contemporaneous"], total),
            "all_time_receipt_availability": _metric(receipted, total),
        },
        "status_counts": counts,
        "unreceipted_delta": [
            row["version"] for row in rows if row["status"] in {"missing", "invalid"}
        ],
        "releases": rows,
        "candidates": candidates,
        "invalid_receipts": invalid_receipts,
    }


def receipted_release_versions(receipts_dir: str | Path) -> dict[str, str]:
    """Backward-compatible valid non-candidate ``version -> receipt`` map."""
    inspected = inspect_release_receipts(receipts_dir)
    return {
        record["version"]: record["path"]
        for record in inspected["receipts"]
        if record["provenance"] != "candidate"
    }


def coverage_report(anchor: str, anchored: list[str], receipted: dict[str, str]) -> dict:
    keys = {_normalize_version(action): action for action in anchored}
    covered = sorted((original for key, original in keys.items() if key in receipted), key=_semver_key)
    missing = sorted((original for key, original in keys.items() if key not in receipted), key=_semver_key)
    total = len(keys)
    ratio = len(covered) / total if total else 1.0
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "anchor": anchor,
        "total_anchored": total,
        "receipted": len(covered),
        "coverage": round(ratio, 4),
        "unreceipted_delta": missing,
        "covered": covered,
    }


def coverage_headline(reports: list[dict]) -> str:
    if not reports:
        return "Coverage: n/a (no anchors declared)"
    weakest = min(reports, key=lambda report: report["coverage"])
    pct = round(weakest["coverage"] * 100)
    return f"Coverage: {pct}% (weakest anchor: {weakest['anchor']})"


# -- general event denominator --------------------------------------------
#
# Release coverage answers "which shipped versions lack a receipt". The same
# set-difference generalizes to any independent record of consequential
# actions: given a denominator observed OUTSIDE the acting workload (a network
# flow log, an IAM audit trail, a gateway's own record) and the receipts an
# actor emitted, the unmatched observed actions are the actions that occurred
# with no receipt. An attacker simply takes a path that emits none, so the
# unmatched set — not the validity of the receipts that exist — is the
# high-severity finding.
#
# The reconciliation always keys on the observed action id verbatim. An
# optional canonical record digest strengthens that correlation into an exact
# retained-record binding; both modes remain explicit in the returned rows.

_RECEIPT_ACTION_ID_PATHS = (
    ("action", "subject", "event_id"),
    ("action", "subject", "action_id"),
)

_EVENT_COVERAGE_DEPTHS = {"digest": 1, "attestation": 2}
_EVENT_RECEIPT_LIMITS = ReceiptParseLimits()
_EVENT_RECEIPT_MAX_FILES = 512
_EVENT_RECEIPT_MAX_TOTAL_BYTES = 16_777_216


def observed_record_sha256(record: dict) -> str:
    """Digest an observed action record without its self-describing digest.

    ``record_sha256`` is optional for compatibility. When it is present,
    event coverage validates it and requires a content-bound receipt result or
    evidence reference to carry the same digest. This prevents an action id
    from clearing a receipt whose retained action facts differ.
    """
    if not isinstance(record, dict):
        raise ValueError("observed action record must be an object")
    preimage = {key: value for key, value in record.items() if key != "record_sha256"}
    try:
        payload = json.dumps(
            preimage,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"observed action record is not canonical JSON: {exc}") from exc
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _receipt_record_digests(doc: dict) -> set[str]:
    """Return content-bound result and evidence digests from one receipt."""
    digests: set[str] = set()
    action = doc.get("action")
    if isinstance(action, dict):
        outcome = action.get("outcome")
        if isinstance(outcome, dict):
            result_hash = outcome.get("result_hash")
            if isinstance(result_hash, str) and result_hash:
                digests.add(result_hash)
    evidence_refs = doc.get("evidence_refs")
    if isinstance(evidence_refs, list):
        for evidence in evidence_refs:
            if not isinstance(evidence, dict):
                continue
            digest = evidence.get("hash")
            if isinstance(digest, str) and digest:
                digests.add(digest)
    return digests


def _read_regular_receipt(path: Path) -> bytes:
    """Read one receipt without following a path swapped to a symlink."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"receipt member must be a regular non-symlink file: {path.name}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ValueError(f"receipt member changed before read: {path.name}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(_EVENT_RECEIPT_LIMITS.max_bytes + 1)
        after = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino, opened.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or len(raw) != after.st_size
        ):
            raise ValueError(f"receipt member changed while read: {path.name}")
        return raw
    finally:
        os.close(descriptor)


def receipt_attested_action_ids(doc: dict) -> set[str]:
    """The observed-action ids a single receipt attests, read from the known
    content-bound locations.

    ``producer`` is deliberately excluded: ActionReceipt defines it as mutable
    provenance rather than identity, so a producer-supplied id is not protected
    by the content hash and cannot cover an observed action.
    """
    ids: set[str] = set()
    for path in _RECEIPT_ACTION_ID_PATHS:
        node: object = doc
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, str) and node:
            ids.add(node)
    return ids


def event_coverage(
    observed: list[dict],
    receipts: str | Path | list[dict],
    *,
    anchor: str = "events",
    minimum_verification_depth: str = "digest",
    accepted_issuers: set[str] | frozenset[str] | None = None,
) -> dict:
    """Reconcile an independent record of observed consequential actions against
    emitted receipts.

    ``observed`` is a list of action records, each with a stable ``id``. An
    optional ``record_sha256`` is the Bulla canonical-JSON digest of every
    other field in that observed record. When supplied, the receipt must carry
    that exact digest in its content-bound result or evidence references; an id
    match alone cannot cover the action. ``receipts`` is a directory of receipt
    JSON, or a list of receipt dicts. Returns the ``coverage_report`` schema
    keyed on observed ids, plus:

      * ``unreceipted`` — observed actions with NO covering receipt (the
        high-severity findings), each with its full observed record;
      * ``phantom_receipt_ids`` — receipt-attested ids not present in the
        denominator (a receipt claiming an action the independent record did
        not observe).

    Only receipts that verify to ``minimum_verification_depth`` count. The
    default ``digest`` rung rejects malformed or hash-invalid records while
    allowing unsigned receipts; security boundaries should require
    ``attestation`` plus an explicit ``accepted_issuers`` allowlist. Otherwise
    a perfectly valid receipt proves only that *some* key signed a claim, not
    that the designated enforcement point observed the action. Invalid,
    insufficiently verified, or unexpected-issuer receipts are reported beside
    the aggregate and never shrink the unreceipted set.

    The denominator fails closed: every observed record must be an object with
    one unique, non-empty string ``id``. Silently dropping a malformed or
    duplicate observation would make the coverage percentage lie upward.

    ``unreceipted_delta`` holds the same ids as ``unreceipted`` for parity with
    the release-coverage report shape.
    """
    if minimum_verification_depth not in _EVENT_COVERAGE_DEPTHS:
        raise ValueError(
            "minimum_verification_depth must be 'digest' or 'attestation'"
        )
    if accepted_issuers is not None:
        if not accepted_issuers or any(
            not isinstance(issuer, str) or not issuer for issuer in accepted_issuers
        ):
            raise ValueError("accepted_issuers must be a non-empty set of issuer ids")

    seen: set[str] = set()
    observed_ids: list[str] = []
    by_id: dict[str, dict] = {}
    for index, record in enumerate(observed):
        if not isinstance(record, dict):
            raise ValueError(f"observed[{index}] must be an object")
        action_id = record.get("id")
        if not isinstance(action_id, str) or not action_id:
            raise ValueError(f"observed[{index}].id must be a non-empty string")
        if action_id in seen:
            raise ValueError(f"duplicate observed action id: {action_id!r}")
        record_digest = record.get("record_sha256")
        if record_digest is None and set(record) != {"id"}:
            raise ValueError(
                f"observed[{index}] carries action facts without record_sha256"
            )
        if record_digest is not None:
            if (
                not isinstance(record_digest, str)
                or record_digest != observed_record_sha256(record)
            ):
                raise ValueError(
                    f"observed[{index}].record_sha256 does not match the observed record"
                )
        seen.add(action_id)
        observed_ids.append(action_id)
        by_id[action_id] = record

    receipt_inputs: list[tuple[str, object]] = []
    invalid_receipts: list[dict] = []
    if isinstance(receipts, (str, Path)):
        root = Path(receipts)
        if root.is_symlink():
            raise ValueError(f"receipts directory must not be a symlink: {root}")
        if root.is_dir():
            paths = sorted(root.glob("*.json"))
            if len(paths) > _EVENT_RECEIPT_MAX_FILES:
                raise ValueError(
                    f"receipts directory exceeds {_EVENT_RECEIPT_MAX_FILES} JSON files"
                )
            aggregate_bytes = 0
            for path in paths:
                try:
                    raw = _read_regular_receipt(path)
                    aggregate_bytes += len(raw)
                    if aggregate_bytes > _EVENT_RECEIPT_MAX_TOTAL_BYTES:
                        raise ValueError(
                            "receipts directory exceeds "
                            f"{_EVENT_RECEIPT_MAX_TOTAL_BYTES} aggregate bytes"
                        )
                    doc = load_action_receipt_document(raw)
                except (ReceiptParseError, OSError) as exc:
                    invalid_receipts.append(
                        {
                            "source": str(path.relative_to(root)),
                            "verified_to": "none",
                            "reason": f"invalid JSON: {exc}",
                        }
                    )
                    continue
                receipt_inputs.append((str(path.relative_to(root)), doc))
        else:
            raise ValueError(f"receipts directory does not exist: {root}")
    else:
        receipt_inputs = [
            (f"receipts[{index}]", doc) for index, doc in enumerate(receipts)
        ]

    attested: set[str] = set()
    receipts_by_id: dict[str, list[tuple[str, set[str]]]] = {}
    for source, raw_doc in receipt_inputs:
        if not isinstance(raw_doc, dict):
            invalid_receipts.append(
                {
                    "source": source,
                    "verified_to": "none",
                    "reason": "receipt must be an object",
                }
            )
            continue
        ok, verified_to, reason = _verify_receipt_doc(raw_doc)
        depth_ok = _EVENT_COVERAGE_DEPTHS.get(verified_to, 0) >= (
            _EVENT_COVERAGE_DEPTHS[minimum_verification_depth]
        )
        if not ok or not depth_ok:
            invalid_receipts.append(
                {
                    "source": source,
                    "verified_to": verified_to,
                    "reason": reason or (
                        f"receipt reached {verified_to!r}; "
                        f"{minimum_verification_depth!r} required"
                    ),
                }
            )
            continue
        if accepted_issuers is not None:
            issuer = (raw_doc.get("signature") or {}).get("issuer")
            if issuer not in accepted_issuers:
                invalid_receipts.append(
                    {
                        "source": source,
                        "verified_to": verified_to,
                        "reason": f"receipt issuer {issuer!r} is not accepted",
                    }
                )
                continue
        ids = receipt_attested_action_ids(raw_doc)
        if len(ids) != 1:
            invalid_receipts.append(
                {
                    "source": source,
                    "verified_to": verified_to,
                    "reason": "receipt must bind exactly one observed action id in action.subject",
                }
            )
            continue
        attested |= ids
        action_id = next(iter(ids))
        receipts_by_id.setdefault(action_id, []).append(
            (source, _receipt_record_digests(raw_doc))
        )

    binding_mismatches: list[dict] = []
    covered: list[str] = []
    for action_id in observed_ids:
        candidates = receipts_by_id.get(action_id, [])
        record_digest = by_id[action_id].get("record_sha256")
        if candidates and (
            record_digest is None
            or any(record_digest in digests for _, digests in candidates)
        ):
            covered.append(action_id)
            continue
        if candidates and isinstance(record_digest, str):
            binding_mismatches.append(
                {
                    "id": action_id,
                    "observed_record_sha256": record_digest,
                    "receipt_sources": sorted(source for source, _ in candidates),
                    "retained_receipt_digests": sorted(
                        {digest for _, digests in candidates for digest in digests}
                    ),
                }
            )
    missing = [aid for aid in observed_ids if aid not in covered]
    total = len(observed_ids)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "anchor": anchor,
        "total_anchored": total,
        "receipted": len(covered),
        "coverage": round(len(covered) / total, 4) if total else 1.0,
        "unreceipted_delta": missing,
        "covered": covered,
        "unreceipted": [by_id[aid] for aid in missing],
        "phantom_receipt_ids": sorted(attested - set(observed_ids)),
        "binding_modes": {
            action_id: (
                "record_sha256"
                if isinstance(by_id[action_id].get("record_sha256"), str)
                else "action_id"
            )
            for action_id in observed_ids
        },
        "binding_mismatches": binding_mismatches,
        "minimum_verification_depth": minimum_verification_depth,
        "accepted_issuers": (
            sorted(accepted_issuers) if accepted_issuers is not None else None
        ),
        "invalid_receipts": invalid_receipts,
    }


def git_coverage(
    receipts_dir: str | Path, *, match: str = "v[0-9]*", repo: str = "."
) -> dict:
    anchored = git_release_tags(match, repo=repo)
    receipted = receipted_release_versions(receipts_dir)
    return coverage_report("git", anchored, receipted)
