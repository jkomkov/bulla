#!/usr/bin/env python3
"""Compile once at contract time; enforce across two live local tools."""

from __future__ import annotations

import argparse
import dataclasses
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from bulla.action_receipt import sign_action_receipt
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.experimental.control_plane import (
    CompiledTermCache,
    apply_package,
    mint_application_receipt,
    verify_application_receipt,
    verify_invention_receipt,
)
from bulla.experimental.invention import SeamProblem, mint_invention_receipt, synthesize
from bulla.identity import LocalEd25519Signer


HERE = Path(__file__).resolve().parent
BULLA = HERE.parents[1]
PROBLEM = BULLA / "examples/invention/definable.json"
STANDALONE = BULLA / "scripts/verify_invention.py"


def _tool(name: str, request: dict) -> dict:
    completed = subprocess.run(
        [sys.executable, str(HERE / "tool.py"), name],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _signer(byte: int | None) -> LocalEd25519Signer:
    return LocalEd25519Signer(seed=(bytes([byte]) + bytes(31)) if byte else secrets.token_bytes(32))


def run(*, fixture_keys: bool) -> dict:
    compiler = _signer(101 if fixture_keys else None)
    relier = _signer(102 if fixture_keys else None)
    original = SeamProblem.from_dict(json.loads(PROBLEM.read_text(encoding="utf-8")))
    problem = dataclasses.replace(
        original,
        authority={"principal": compiler.issuer, "policy": "policy://delivery-acceptance@sha256:demo"},
    )
    with tempfile.TemporaryDirectory(prefix="bulla-semantic-control-") as directory:
        root = Path(directory)
        started = time.perf_counter()
        result = synthesize(problem)
        compilation_seconds = time.perf_counter() - started
        cache = CompiledTermCache(root / "cache")
        cache_key = cache.put(problem, result, adapter_version="delivery-tool/1")
        cached_problem, cached_result = cache.get(cache_key, adapter_version="delivery-tool/1")

        problem_path = root / "problem.json"
        result_path = root / "result.json"
        problem_path.write_text(json.dumps(problem.to_dict(), indent=2) + "\n", encoding="utf-8")
        result_path.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
        checker = root / "verify_invention.py"
        shutil.copy2(STANDALONE, checker)
        checked = subprocess.run(
            [sys.executable, "-I", str(checker), str(problem_path), str(result_path)],
            text=True,
            capture_output=True,
            check=False,
        )
        standalone_report = json.loads(checked.stdout)
        if checked.returncode != 0 or not standalone_report.get("ok"):
            raise RuntimeError("standalone invention verification failed")

        invention_envelope = RecourseEnvelope(
            authority=Authority(
                principal=compiler.issuer,
                policy=problem.authority["policy"],
            ),
            bounds=Bounds(scope="delivery-acceptance seam compilation"),
            recourse=Recourse(
                challenge_window="P30D",
                forum=Forum("https://witness.invalid", "sha256:" + "ab" * 32),
                remedies=(
                    Remedy("recompute", "verify_invention.py", result.result_hash),
                    Remedy("escalate", compiler.issuer, problem.authority["policy"]),
                ),
            ),
        )
        invention_receipt = sign_action_receipt(
            mint_invention_receipt(
                problem,
                result,
                envelope=invention_envelope,
                timestamp="2026-07-18T12:00:00Z",
                producer={"demo": "semantic-control-plane"},
            ),
            compiler,
        ).to_dict()
        if not verify_invention_receipt(invention_receipt, problem, result)["ok"]:
            raise RuntimeError("invention receipt replay failed")

        executions = []
        for index, request in enumerate(
            (
                {"delivery_id": "d0", "signed_acceptance": True},
                {"delivery_id": "d1", "signed_acceptance": False},
            ),
            start=1,
        ):
            structure = _tool("delivery", request)
            eval_started = time.perf_counter()
            application = apply_package(
                cached_problem,
                cached_result.package,
                shared_structure=structure,
                target_arguments=(request["delivery_id"],),
                adapter_version="delivery-tool/1",
            )
            evaluation_seconds = time.perf_counter() - eval_started
            downstream = _tool(
                "claim-router",
                {
                    "decision": application.status.value,
                    "application_result_hash": application.result_hash,
                },
            )
            application_envelope = RecourseEnvelope(
                authority=Authority(
                    principal=relier.issuer,
                    policy="policy://claim-routing@sha256:demo",
                ),
                bounds=Bounds(scope="one application of the pinned delivery predicate"),
            )
            receipt = sign_action_receipt(
                mint_application_receipt(
                    application,
                    envelope=application_envelope,
                    timestamp=f"2026-07-18T12:00:0{index}Z",
                    producer={"tool": "claim-router/1"},
                ),
                relier,
            ).to_dict()
            replay = verify_application_receipt(
                receipt,
                problem,
                result.package,
                shared_structure=structure,
                target_arguments=(request["delivery_id"],),
                adapter_version="delivery-tool/1",
            )
            executions.append(
                {
                    "request": request,
                    "shared_structure": structure,
                    "application": application.to_dict(),
                    "downstream": downstream,
                    "application_receipt": receipt,
                    "receipt_replay": replay,
                    "evaluation_seconds": evaluation_seconds,
                }
            )
    return {
        "profile": "bulla.semantic-invention/0.1-draft",
        "mode": "two-live-local-subprocess-tools",
        "problem_hash": problem.problem_hash,
        "result_hash": result.result_hash,
        "package_hash": result.package.package_hash,
        "compilation_key": cache_key,
        "compilation_seconds": compilation_seconds,
        "standalone_verification": standalone_report,
        "invention_receipt": invention_receipt,
        "executions": executions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-keys", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = run(fixture_keys=args.fixture_keys)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    summary = {
        "mode": payload["mode"],
        "compilation_seconds": payload["compilation_seconds"],
        "standalone_ok": payload["standalone_verification"]["ok"],
        "decisions": [x["application"]["status"] for x in payload["executions"]],
        "downstream_actions": [x["downstream"]["action"] for x in payload["executions"]],
        "receipts_replay": all(x["receipt_replay"]["ok"] for x in payload["executions"]),
    }
    print(json.dumps(summary, indent=2))
    return 0 if summary["standalone_ok"] and summary["receipts_replay"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
