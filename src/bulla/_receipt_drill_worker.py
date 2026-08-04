#!/usr/bin/env python3
"""Guarded internal checker process for ``bulla receipt drill``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


sys.dont_write_bytecode = True


def _install_network_guard() -> None:
    denied = (
        "socket.",
        "http.client.",
        "urllib.",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
    )

    def guard(event, _args):
        if event.startswith(denied):
            raise RuntimeError(f"network guard denied audit event {event}")

    sys.addaudithook(guard)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--key", type=Path, default=None)
    args = parser.parse_args()

    # The hook is deliberately installed before importing any Bulla parser or
    # verifier module. This file is executed by path rather than ``-m`` so the
    # package initializer cannot run first.
    _install_network_guard()
    try:
        from bulla.receipt_drill import (
            load_public_key,
            package_report,
            parse_receipt_structure,
        )
        from bulla.receipt_parser import ReceiptParseError

        with args.receipt.open("rb") as stream:
            raw = stream.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ReceiptParseError("receipt exceeds 1048576 bytes")
        receipt = parse_receipt_structure(raw)
        if receipt.get("schema_version") != "0.2":
            raise ReceiptParseError(
                "receipt drill supports normative ActionReceipt v0.2 only"
            )
        report = package_report(receipt, raw, load_public_key(args.key))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=False))
    failed = {"FAILED", "VIOLATES", "NOT_CHECKABLE", "NOT_EVALUATED"}
    return int(any(
        row.get("availability") == "RECHECKABLE" and row.get("result") in failed
        for row in report["dimensions"]
    ))


if __name__ == "__main__":
    raise SystemExit(main())
