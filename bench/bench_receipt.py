#!/usr/bin/env python3
"""Micro-benchmarks for the v0.3 signing/verification path, by delegation depth.

Measures what a relying party actually pays, reported honestly:

  * **sign** and **verify** are timed SEPARATELY — they are different costs borne
    by different parties (the actor signs once; every verifier verifies).
  * **median and p95**, never a bare mean — the tail is what a verifier under load
    feels, and a mean hides it.
  * **wire size** — the serialized receipt in bytes, since a delegation chain grows
    the record every verifier must fetch and store.
  * environment metadata is printed, because a number without a machine is a rumor.

Depth 0 = no delegation (the principal signs directly). Depth N = an N-link chain
P → I₁ → … → L, with the leaf L signing the act. MAX_DEPTH is 8.

    python bulla/bench/bench_receipt.py [--iterations N]

These are micro-benchmarks of the kernel's crypto+canonicalization path on one
machine. They are NOT a throughput claim for a deployed witness pool, which is
dominated by I/O, log anchoring, and network — none of which this measures.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time

from bulla.action_receipt import build_tool_call_receipt, sign_action_receipt, verify_receipt
from bulla.delegation import DelegationGrant, hash_ref, sign_grant
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.identity import LocalEd25519Signer

_POL = "policy://payments@sha256:aa"
_SCOPE = "payments.charge amount<=100000"
# scope_digest binds to the receipt's declared bounds.scope, byte for byte.
_PD, _SD = hash_ref(_POL), hash_ref(_SCOPE)


def _chain(depth: int):
    """(signers, grant_dicts, leaf) for an N-link chain. depth 0 → principal signs."""
    ids = [LocalEd25519Signer(seed=bytes([i + 1]) + bytes(31)) for i in range(depth + 1)]
    grants, parent = [], None
    for i in range(depth):
        g = sign_grant(
            DelegationGrant(ids[i].verification_method, ids[i + 1].verification_method,
                            ids[0].verification_method, parent, _PD, _SD),
            ids[i],
        )
        grants.append(g.to_dict())
        parent = g.grant_hash
    return ids[0], tuple(grants), ids[depth]


def _receipt(principal, grants):
    env = RecourseEnvelope(
        authority=Authority(principal=principal.verification_method, policy=_POL, delegation=grants),
        bounds=Bounds(scope=_SCOPE),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(log_endpoint="https://log.example", trusted_root_ref="ots:root"),
            remedies=(
                Remedy(rung="recompute", verifier="bulla receipt verify", anchor="hashes.content"),
                Remedy(rung="escalate", verifier="maintainer review", anchor=principal.verification_method),
            ),
        ),
        deed_schema="0.3" if grants else "0.2",
    )
    return build_tool_call_receipt(
        tool="payments.charge", call_subject={"amount": 1250},
        diagnostic_ref={"status": "reference", "ref": "sha256:" + "d" * 64},
        envelope=env, timestamp="2026-07-16T00:00:00+00:00",
        producer={"bulla_version": "0.44.0"},
    )


def _stats(samples: list[float]) -> tuple[float, float]:
    """(median_ms, p95_ms) — p95 by nearest-rank on the sorted samples."""
    s = sorted(samples)
    p95 = s[min(len(s) - 1, max(0, int(round(0.95 * len(s))) - 1))]
    return statistics.median(s) * 1e3, p95 * 1e3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=200)
    args = ap.parse_args()
    n = args.iterations

    try:
        import nacl
        nacl_v = getattr(nacl, "__version__", "?")
    except Exception:
        print("bench needs bulla[identity] (PyNaCl)", file=sys.stderr)
        return 1

    print(f"machine      : {platform.machine()} · {platform.system()} {platform.release()}")
    print(f"python       : {platform.python_version()} ({platform.python_implementation()})")
    print(f"pynacl       : {nacl_v}")
    print(f"iterations   : {n} per cell\n")
    print(f"{'depth':>5} {'sign med':>9} {'sign p95':>9} {'verify med':>11} {'verify p95':>11} "
          f"{'wire B':>8} {'vs d0':>7}")
    print("-" * 66)

    base_wire = None
    for depth in (0, 1, 4, 8):
        principal, grants, leaf = _chain(depth)
        r = _receipt(principal, grants)

        sign_s = []
        for _ in range(n):
            t0 = time.perf_counter()
            sign_action_receipt(r, leaf)
            sign_s.append(time.perf_counter() - t0)

        signed = sign_action_receipt(r, leaf).to_dict()
        wire = len(json.dumps(signed, separators=(",", ":")).encode("utf-8"))
        if base_wire is None:
            base_wire = wire

        # sanity: the benchmark must measure a PASSING path, not a fast failure
        v = verify_receipt(signed)
        assert v.ok and v.authority_authentic == "verified", f"depth {depth}: {v.summary()}"
        if depth:
            assert v.chain_integrity == "verified" and v.principal_binding == "verified", v.summary()
            assert v.policy_binding == "verified" and v.scope_binding == "verified", v.summary()

        ver_s = []
        for _ in range(n):
            t0 = time.perf_counter()
            verify_receipt(signed)
            ver_s.append(time.perf_counter() - t0)

        sm, sp = _stats(sign_s)
        vm, vp = _stats(ver_s)
        print(f"{depth:>5} {sm:>8.3f}m {sp:>8.3f}m {vm:>10.3f}m {vp:>10.3f}m "
              f"{wire:>8} {wire / base_wire:>6.2f}x")

    print("\nsign = content + authorization proofs (the actor pays once).")
    print("verify = full verify_receipt: hashes + modality law + both proofs + the")
    print("         delegation chain's six dimensions (every verifier pays).")
    print("Micro-benchmark of the crypto+canonicalization path only — NOT a witness-")
    print("pool throughput claim (that is dominated by I/O and anchoring).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
