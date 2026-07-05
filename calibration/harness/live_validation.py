"""Minimal live-execution POSITIVE CONTROL for the per-dimension fee.

SCOPE — read this before citing any number from here.
    This is a *construct-validity* check, not a generalization experiment.
    The backends are purpose-built (``seam_backend.py``) so that hidden
    convention mismatches fail at runtime. It therefore establishes that the
    end-to-end encoding works — per-dimension fee localizes to the dimensions
    that actually fail under real subprocess execution, and the observable
    convention-distance baseline misses exactly those (hidden) failures. It
    does NOT establish that fee predicts failure on real-world MCP servers;
    that requires non-constructed failure labels and is explicitly deferred.

Two independent measurement channels per seam:
  * schema channel  — fee / per-dimension fee / observable convention distance,
    computed by the kernel from the advertised schemas (no execution);
  * execution channel — a real producer subprocess emits a payload, a real
    consumer subprocess consumes it, and the binary failure label is read from
    whether the consumer's JSON-RPC reply carried an ``error``. The label never
    looks at the fee, and no LLM judges anything.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bulla.diagnostic import decompose_fee_by_dimension, diagnose
from bulla.live_proxy import BackendServer
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

_BACKEND = str(Path(__file__).resolve().parent / "seam_backend.py")

# Canonical "home" convention each consumer expects.
_CONSUMER_HOME = {"encoding": "utf-8", "index": "0-based", "unit": "celsius"}
_OTHER = {"encoding": "latin-1", "index": "1-based", "unit": "fahrenheit"}


@dataclass(frozen=True)
class Dim:
    name: str
    visible: bool
    mismatch: bool
    load_bearing: bool = True

    @property
    def consumer_conv(self) -> str:
        return _CONSUMER_HOME[self.name]

    @property
    def producer_conv(self) -> str:
        return _OTHER[self.name] if self.mismatch else _CONSUMER_HOME[self.name]


@dataclass(frozen=True)
class Seam:
    label: str
    dims: tuple[Dim, ...]


@dataclass
class SeamResult:
    label: str
    fee: int
    fee_by_dim: dict[str, int]
    observable_distance: int  # mismatches a pairwise schema check would see
    hidden_mismatch_dims: list[str]
    failed: bool  # execution-derived, fee-blind
    error: str | None = None
    dropped: bool = False
    consumer_results: dict[str, Any] | None = None  # per-dim results in permissive mode


# ── schema channel ────────────────────────────────────────────────────────


def build_composition(seam: Seam) -> Composition:
    """Producer 'produce' -> consumer 'consume', one edge per present dimension.
    A dimension is hidden (in internal_state, absent from observable_schema)
    unless marked visible."""
    fields = [d.name for d in seam.dims]
    visible = [d.name for d in seam.dims if d.visible]
    producer = ToolSpec("producer", tuple(fields), tuple(visible))
    consumer = ToolSpec("consumer", tuple(fields), tuple(visible))
    edges = tuple(
        Edge("producer", "consumer", (SemanticDimension(d.name, d.name, d.name),))
        for d in seam.dims
    )
    return Composition(seam.label, (producer, consumer), edges)


def schema_metrics(seam: Seam) -> tuple[int, dict[str, int], int]:
    comp = build_composition(seam)
    fee = diagnose(comp).coherence_fee
    fee_by_dim = decompose_fee_by_dimension(comp).by_dimension
    observable_distance = sum(1 for d in seam.dims if d.visible and d.mismatch)
    return fee, fee_by_dim, observable_distance


# ── execution channel ──────────────────────────────────────────────────────


def _spec(seam: Seam, role: str, *, mode: str = "strict") -> dict[str, Any]:
    spec: dict[str, Any] = {
        "role": role,
        "dimensions": [
            {
                "name": d.name,
                "producer_conv": d.producer_conv,
                "consumer_conv": d.consumer_conv,
                "visible": d.visible,
                "load_bearing": d.load_bearing,
            }
            for d in seam.dims
        ],
    }
    if mode != "strict":
        spec["mode"] = mode
    return spec


async def _execute(
    seam: Seam, *, permissive: bool = False,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """Run producer -> consumer for real.

    Returns (failed, error_message, consumer_results).
    consumer_results is non-None only in permissive mode when the call succeeds.
    """
    consumer_mode = "permissive" if permissive else "strict"
    producer = BackendServer(
        name="producer",
        command=f"{sys.executable} {_BACKEND}",
        env={"BULLA_SEAM_SPEC": json.dumps(_spec(seam, "producer"))},
    )
    consumer = BackendServer(
        name="consumer",
        command=f"{sys.executable} {_BACKEND}",
        env={"BULLA_SEAM_SPEC": json.dumps(_spec(seam, "consumer", mode=consumer_mode))},
    )
    try:
        await producer.start()
        await producer.initialize()
        await consumer.start()
        await consumer.initialize()

        produced = await producer.call_tool("produce", {})
        payload_text = produced["result"]["content"][0]["text"]
        payload = json.loads(payload_text)

        consumed = await consumer.call_tool("consume", {"payload": payload})
        failed = "error" in consumed
        error = consumed.get("error", {}).get("message") if failed else None
        consumer_results = None
        if not failed and permissive:
            result_text = consumed["result"]["content"][0]["text"]
            result_data = json.loads(result_text)
            consumer_results = result_data.get("results")
        return failed, error, consumer_results
    finally:
        for b in (producer, consumer):
            try:
                await b.stop()
            except Exception:
                pass


async def run_seam(seam: Seam, *, permissive: bool = False) -> SeamResult:
    fee, fee_by_dim, obs_dist = schema_metrics(seam)
    hidden_mismatch = [d.name for d in seam.dims if (not d.visible) and d.mismatch and d.load_bearing]
    try:
        failed, error, consumer_results = await _execute(seam, permissive=permissive)
    except Exception as exc:  # spawn/transport failure -> dropped, not a label
        return SeamResult(seam.label, fee, fee_by_dim, obs_dist, hidden_mismatch,
                          failed=False, error=f"{type(exc).__name__}: {exc}", dropped=True)
    return SeamResult(seam.label, fee, fee_by_dim, obs_dist, hidden_mismatch,
                      failed, error, consumer_results=consumer_results)


# ── the fixed seam set (the cells that pin down construct validity) ─────────


def default_seams() -> list[Seam]:
    return [
        # 1. hidden mismatch -> REAL failure; fee flags it; observable dist = 0
        Seam("hidden_mismatch_encoding", (Dim("encoding", visible=False, mismatch=True),)),
        Seam("hidden_mismatch_index", (Dim("index", visible=False, mismatch=True),)),
        Seam("hidden_mismatch_unit", (Dim("unit", visible=False, mismatch=True),)),
        # 2. visible mismatch -> consumer normalizes -> no failure; fee = 0; dist > 0
        Seam("visible_mismatch_encoding", (Dim("encoding", visible=True, mismatch=True),)),
        Seam("visible_mismatch_unit", (Dim("unit", visible=True, mismatch=True),)),
        # 3. hidden match -> no failure, but fee > 0 (honest imperfection:
        #    fee marks the at-risk hidden coupling even when it happens to agree)
        Seam("hidden_match_encoding", (Dim("encoding", visible=False, mismatch=False),)),
        # 4. fully clean -> no failure, fee = 0, dist = 0
        Seam("clean_visible_match", (Dim("unit", visible=True, mismatch=False),)),
        # 5. OBSERVABLE-BLINDSPOT PAIR. Both carry one hidden 'index' coupling
        #    (so identical fee and fee_by_dim) but differ in the *value* the fee
        #    cannot see. The non-failing seam has the HIGHER observable distance
        #    (its visible 'encoding' mismatch is seen and handled); the failing
        #    seam has observable distance 0 (its only mismatch is hidden). This
        #    shows observable convention-distance points the WRONG way, while the
        #    fee correctly marks both as carrying an unobservable at-risk coupling
        #    (perfect recall, by design imperfect precision on value mismatch).
        Seam("pair_visible_handled", (
            Dim("encoding", visible=True, mismatch=True),    # seen + handled
            Dim("index", visible=False, mismatch=False),     # hidden, agrees
        )),
        Seam("pair_hidden_lurks", (
            Dim("encoding", visible=True, mismatch=False),   # seen, agrees
            Dim("index", visible=False, mismatch=True),      # hidden + mismatch -> fails
        )),
    ]


async def run_all(seams: list[Seam] | None = None) -> list[SeamResult]:
    seams = seams or default_seams()
    return [await run_seam(s) for s in seams]


def main() -> None:
    results = asyncio.run(run_all())
    runnable = [r for r in results if not r.dropped]
    dropped = [r for r in results if r.dropped]
    print(f"# Live positive control: {len(runnable)} runnable, {len(dropped)} dropped\n")
    hdr = f"{'seam':32s} {'fee':>3s} {'obsDist':>7s} {'failed':>6s}  fee_by_dim"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        if r.dropped:
            print(f"{r.label:32s}  DROPPED: {r.error}")
            continue
        print(f"{r.label:32s} {r.fee:3d} {r.observable_distance:7d} {str(r.failed):>6s}  {r.fee_by_dim}")
    if dropped:
        print(f"\nDropped {len(dropped)} seam(s) (spawn/transport): "
              f"{[r.label for r in dropped]}")


if __name__ == "__main__":
    main()
