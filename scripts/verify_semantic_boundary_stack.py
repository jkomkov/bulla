#!/usr/bin/env python3
"""Replay the signed semantic-boundary stack v0.3 freeze receipt."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from bulla.experimental.frsl import canonical_hash
from bulla.identity import pubkey_from_did_key, verify_proof


BULLA = Path(__file__).resolve().parents[1]
ROOT = BULLA.parent
DEFAULT_RECEIPT = BULLA / "bench/golden/v0.3/bulla-semantic-boundary-stack-v0.3.json"
PUBLIC_KEY = BULLA / "bench/golden/v0.3/research-public-key.json"


def digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def digest_git_blob(commit: str, path: str) -> str:
    """Digest the frozen object rather than requiring later append-only files to stop."""

    completed = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return "sha256:" + hashlib.sha256(completed.stdout).hexdigest()


def stable_surface_digest(commit: str | None = None) -> tuple[int, str]:
    command = (
        ["git", "ls-tree", "-r", "--name-only", commit, "--", "bulla/src/bulla"]
        if commit
        else ["git", "ls-files", "bulla/src/bulla/**"]
    )
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = sorted(
        line for line in completed.stdout.splitlines()
        if line and not line.startswith("bulla/src/bulla/experimental/")
    )
    def path_hash(path: str) -> str:
        if commit:
            blob = subprocess.run(
                ["git", "show", f"{commit}:{path}"],
                cwd=ROOT, check=True, capture_output=True,
            ).stdout
        else:
            blob = (ROOT / path).read_bytes()
        return hashlib.sha256(blob).hexdigest()

    lines = "".join(f"{path_hash(path)}  {path}\n" for path in paths)
    return len(paths), "sha256:" + hashlib.sha256(lines.encode("utf-8")).hexdigest()


def verify(receipt_path: Path) -> dict[str, object]:
    document = json.loads(receipt_path.read_text(encoding="utf-8"))
    if set(document) != {"content", "content_hash", "proof"}:
        raise ValueError("freeze receipt has unknown or missing outer fields")
    content = document["content"]
    if canonical_hash(content) != document["content_hash"]:
        raise ValueError("freeze receipt content hash mismatch")
    authenticity = verify_proof(document["content_hash"], document["proof"])
    if not authenticity.authentic:
        raise ValueError(f"freeze signature rejected: {authentic.reason}")

    public = json.loads(PUBLIC_KEY.read_text(encoding="utf-8"))
    if public.get("private_material_committed") is not False:
        raise ValueError("public-key artifact does not deny committed private material")
    if public.get("did") != document["proof"].get("issuer"):
        raise ValueError("freeze signer does not match committed public identity")
    expected_key = pubkey_from_did_key(public["did"])
    if base64.b64decode(public["public_key_b64"]) != expected_key:
        raise ValueError("public key does not match self-certifying did:key")

    file_bindings = {
        "specification_sha256": ROOT / "bulla/spec/semantic-boundary-v0.3-experimental.md",
    }
    for field, path in file_bindings.items():
        if content[field] != digest_file(path):
            raise ValueError(f"{field} mismatch")
    lean_paths = {
        # These two indices are intentionally append-only across later formal
        # profiles. Replay their exact v0.3 objects from the frozen commit.
        "InterpolantEnvelope.lean": "papers/interpolant-envelope/lean/InterpolantEnvelope.lean",
        "InterpolantEnvelope/Axioms.lean": "papers/interpolant-envelope/lean/InterpolantEnvelope/Axioms.lean",
        # Boundary.lean is the v0.3 theorem body itself and must remain byte
        # identical in the live tree.
        "InterpolantEnvelope/Boundary.lean": "papers/interpolant-envelope/lean/InterpolantEnvelope/Boundary.lean",
    }
    for name, path in lean_paths.items():
        actual = (
            digest_git_blob(content["frozen_main_commit"], path)
            if name in {"InterpolantEnvelope.lean", "InterpolantEnvelope/Axioms.lean"}
            else digest_file(ROOT / path)
        )
        if content["lean_digests"].get(name) != actual:
            raise ValueError(f"Lean digest mismatch: {name}")
    golden_paths = {
        "v0.1_manifest_file_sha256": ROOT / "bulla/bench/golden/v0.1/manifest.json",
        "v0.2_freeze_manifest_file_sha256": ROOT / "bulla/bench/golden/v0.2/freeze-manifest.json",
        "v0.3_manifest_file_sha256": ROOT / "bulla/bench/golden/v0.3/manifest.json",
    }
    for name, path in golden_paths.items():
        if content["golden_roots"].get(name) != digest_file(path):
            raise ValueError(f"Golden root mismatch: {name}")
    if content["oci_observation"]["dockerfile_sha256"] != digest_file(
        ROOT / "bulla/bench/golden/v0.2/container/Dockerfile"
    ):
        raise ValueError("OCI Dockerfile digest mismatch")

    # The receipt freezes a historical surface; later experimental CLI and
    # formal extensions must not make that historical receipt unverifiable.
    file_count, surface_hash = stable_surface_digest(content["frozen_main_commit"])
    if content["stable_surface"]["file_count"] != file_count:
        raise ValueError("stable-surface file count mismatch")
    if content["stable_surface"]["sha256"] != surface_hash:
        raise ValueError("stable-surface digest mismatch")

    previous = None
    for pr in ("pr_171", "pr_172", "pr_173"):
        merge = content["merge_commits"][pr]
        source = content["source_heads"][pr]
        parents = subprocess.run(
            ["git", "show", "-s", "--format=%P", merge],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().split()
        if len(parents) != 2 or parents[1] != source:
            raise ValueError(f"{pr} is not bound to the declared source head")
        if previous is not None and parents[0] != previous:
            raise ValueError(f"{pr} does not follow the prior stack merge")
        previous = merge
    if content["frozen_main_commit"] != content["merge_commits"]["pr_173"]:
        raise ValueError("frozen main commit is not the terminal stack merge")

    return {
        "classification": content["classification"],
        "content_hash": document["content_hash"],
        "frozen_main_commit": content["frozen_main_commit"],
        "ok": True,
        "signer": authenticity.issuer,
        "stable_surface_files": file_count,
    }


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) == 2 else DEFAULT_RECEIPT
    try:
        result = verify(path)
    except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
