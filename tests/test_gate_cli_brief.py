"""Exact public-contract tests for ``bulla gate --format brief``."""

from __future__ import annotations

import json
import subprocess
import sys
import threading

from bulla.certificate import certify, sign_certificate, to_dict
from bulla.http_registry import make_server
from bulla.identity import LocalEd25519Signer
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.registry import DeedLog


def _fee_positive_composition() -> Composition:
    filesystem = ToolSpec("filesystem", ("path_root",), ())
    git = ToolSpec("git", ("path_root",), ())
    return Composition(
        "fs_to_git",
        (filesystem, git),
        (
            Edge(
                "filesystem",
                "git",
                (SemanticDimension("path_root", "path_root", "path_root"),),
            ),
        ),
    )


def _run_gate(certificate, registry_url: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "bulla",
            "gate",
            "--certificate",
            str(certificate),
            "--registry",
            registry_url,
            "--format",
            "brief",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_brief_unpinned_refusal_and_pinned_proceed_are_exact(tmp_path):
    signer = LocalEd25519Signer.generate()
    certificate = to_dict(sign_certificate(certify(_fee_positive_composition()), signer))
    certificate_path = tmp_path / "certificate.json"
    certificate_path.write_text(json.dumps(certificate))
    log = DeedLog(tmp_path / "log.jsonl")
    log.append_certificate(certificate)
    server = make_server(log)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    registry_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        refused = _run_gate(certificate_path, registry_url)
        proceeded = _run_gate(
            certificate_path,
            registry_url,
            "--trusted-root",
            log.root(),
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert refused.returncode == 1
    assert refused.stderr == ""
    assert refused.stdout == (
        "REFUSE   UNPINNED_ROOT · included=true · root_trust=host-asserted\n"
        "CURE     present the deed under an independently trusted root\n"
    )

    # This certificate has coherence_fee=1. It still proceeds because fee gating
    # remains opt-in unless --require-fee is explicitly supplied.
    assert proceeded.returncode == 0
    assert proceeded.stderr == ""
    assert proceeded.stdout == "PROCEED  included=true · root_trust=pinned\n"
