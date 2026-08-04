#!/usr/bin/env python3
"""Run a retained checker under a guard installed outside the retained kit."""

from __future__ import annotations

import argparse
from pathlib import Path
import runpy
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
    parser.add_argument("--checker", type=Path, required=True)
    parser.add_argument("checker_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    checker_args = args.checker_args[1:] if args.checker_args[:1] == ["--"] else args.checker_args

    _install_network_guard()
    sys.argv = [str(args.checker), *checker_args]
    runpy.run_path(str(args.checker), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
