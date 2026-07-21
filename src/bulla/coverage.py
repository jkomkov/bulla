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

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


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
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "anchor": "pypi",
        "project": project,
        "source": PYPI_PROJECT_URL.format(project=project),
        "total_anchored": total,
        "receipted": receipted,
        "coverage": round(receipted / total, 4) if total else 1.0,
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


def git_coverage(
    receipts_dir: str | Path, *, match: str = "v[0-9]*", repo: str = "."
) -> dict:
    anchored = git_release_tags(match, repo=repo)
    receipted = receipted_release_versions(receipts_dir)
    return coverage_report("git", anchored, receipted)
