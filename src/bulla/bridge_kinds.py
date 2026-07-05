"""Classification of bridges by kind: value-level vs schema-level.

A blind spot in a composition can be repaired in one of two ways:

  * **Value-level** — the disagreeing fields ARE both visible to the agent
    (one says "USD", the other expects "usd"; one emits ISO-8601, the
    other expects Unix seconds). A registered translator in
    ``bulla.bridges`` knows how to map one convention to the other. The
    agent can apply this at runtime by transforming the argument before
    forwarding the call. **Auto-mode safe.**

  * **Schema-level** — at least one of the disagreeing fields is HIDDEN
    (the producer keeps it in internal state, never exposes it). The
    obstruction can only be resolved by editing the producing MCP
    server's manifest to expose the field, then redeploying. **Cannot
    be fixed in the agent loop.** Auto-mode unsafe; surface to a human.

This module is intentionally thin (~100 LOC). The heavy lifting —
computing blind spots, generating ``BridgePatch`` repair instructions,
and the value-translator registry — already exists in
``bulla.diagnostic`` and ``bulla.bridges``. This module classifies the
existing outputs into the runtime / non-runtime split that
``bulla__bridge`` (the meta-tool agents call) needs to return.

See ``bulla/agents/system_prompt_v1.md`` for the agent-facing contract
this classification supports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from bulla.bridges import registered_pairs
from bulla.model import BlindSpot, Diagnostic


@dataclass(frozen=True)
class BridgeAdvice:
    """Classified guidance for one obstruction touching a pending call.

    Attached to a meta-tool response when an agent calls
    ``bulla__bridge``. The agent's reading order:

      1. ``kind == "value"`` and ``applicable``: apply the
         ``advice["translate"]`` instruction to the call arguments
         before forwarding. The composition's fee drops by 1 after
         the translation receipt chains in.

      2. ``kind == "schema"``: do NOT auto-apply. The
         ``advice["patch"]`` describes a manifest edit the operator
         must make. Surface the patch to the human and choose a
         different plan in the meantime.
    """

    kind: Literal["value", "schema"]
    server: str
    tool: str
    dimension: str
    edge: str
    advice: dict[str, Any]
    applicable: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "server": self.server,
            "tool": self.tool,
            "dimension": self.dimension,
            "edge": self.edge,
            "advice": self.advice,
            "applicable": self.applicable,
            "note": self.note,
        }


def _split_fq(fq_name: str) -> tuple[str, str]:
    """Split ``server__tool`` back into ``(server, tool)``.

    The proxy namespaces tools as ``server__tool`` when aggregating
    multiple backends. The diagnostic's ``BlindSpot.from_tool`` /
    ``to_tool`` and ``BridgePatch.target_tool`` use these prefixed
    names, so the classifier reverses the split when returning advice.
    """
    sep = "__"
    if sep not in fq_name:
        return "", fq_name
    server, _, tool = fq_name.partition(sep)
    return server, tool


def _build_patch_for(bs: BlindSpot) -> dict[str, Any]:
    """Build a Bulla Patch v0.1 directly from a blind spot.

    Mirrors ``BridgePatch.to_bulla_patch`` shape but doesn't require
    the receipt-construction path — the BlindSpot already carries all
    the information needed (which tool, which field, which dimension).
    The agent reads this to know exactly which manifest to edit.
    """
    hidden_field = bs.from_field if bs.from_hidden else bs.to_field
    target_tool = bs.from_tool if bs.from_hidden else bs.to_tool
    return {
        "bulla_patch_version": "0.1.0",
        "action": "expose",
        "target_tool": target_tool,
        "field": hidden_field,
        "path": f"/observable_schema/{hidden_field}",
        "dimension": bs.dimension,
        "eliminates": bs.edge,
        "expected_fee_delta": -1,
    }


def _value_advice(
    bs: BlindSpot,
    fq_tool: str,
    arguments: dict[str, Any] | None,
) -> BridgeAdvice | None:
    """Return value-level advice if a translator exists AND both fields
    are visible (neither is hidden — pure naming/encoding mismatch)."""
    if bs.from_hidden or bs.to_hidden:
        return None
    triple = (bs.dimension, bs.from_field, bs.to_field)
    if triple not in registered_pairs():
        return None
    server, tool = _split_fq(fq_tool)
    arg_name = bs.to_field
    arg_value = (arguments or {}).get(arg_name)
    return BridgeAdvice(
        kind="value",
        server=server,
        tool=tool,
        dimension=bs.dimension,
        edge=bs.edge,
        advice={
            "translate": {
                "dimension": bs.dimension,
                "from_convention": bs.from_field,
                "to_convention": bs.to_field,
                "argument": arg_name,
                "current_value": arg_value,
            },
            "note": (
                f"Use bulla.translate({bs.dimension!r}, "
                f"value=<...>, to_convention={bs.to_field!r}) "
                f"before forwarding."
            ),
        },
        applicable=True,
    )


def _schema_advice(bs: BlindSpot, fq_tool: str) -> BridgeAdvice:
    """Return schema-level advice (manifest edit required)."""
    server, tool = _split_fq(fq_tool)
    return BridgeAdvice(
        kind="schema",
        server=server,
        tool=tool,
        dimension=bs.dimension,
        edge=bs.edge,
        advice={
            "patch": _build_patch_for(bs),
            "hidden_field": bs.from_field if bs.from_hidden else bs.to_field,
            "hidden_on_tool": bs.from_tool if bs.from_hidden else bs.to_tool,
            "note": (
                "Cannot be fixed at runtime. Edit the producing server's "
                "manifest to expose the hidden field, then redeploy."
            ),
        },
        applicable=False,
    )


def _call_touches_obstruction(
    bs: BlindSpot,
    fq_tool: str,
    arguments: dict[str, Any] | None,
) -> bool:
    """Whether the pending call actually traverses this obstruction.

    The discriminating idea: a per-tool verdict over-refuses. If the
    agent's call against the *consumer* doesn't pass the obstructed
    field as an argument, this particular call isn't on the seam right
    now — refusing it would be a false positive.

      * If ``arguments is None`` (key absent in the meta-tool call) or
        ``not arguments`` (empty dict): no information to refine on,
        return conservative True. Treating ``{}`` as "obstructed field
        absent" would create the inverse false-negative — the agent
        could call ``should_proceed({"server":X,"tool":Y})`` without
        arguments and get a misleading ``safe`` on a dirty seam.
      * If the call is on the producer side (``fq_tool == bs.from_tool``):
        producing through this tool commits output that the downstream
        consumer must contend with — keep the obstruction visible.
      * If the call is on the consumer side (``fq_tool == bs.to_tool``)
        and the obstructed field name is not in ``arguments``: the call
        isn't actually using the obstructed path — skip.
    """
    if not arguments:
        return True
    if fq_tool == bs.from_tool and fq_tool != bs.to_tool:
        return True
    if fq_tool == bs.to_tool:
        return bs.to_field in arguments
    return True  # fq_tool appears on both sides (self-loop) — conservative


def classify_for_call(
    diag: Diagnostic,
    server: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
) -> list[BridgeAdvice]:
    """Classify obstructions actually traversed by ``(server, tool, args)``.

    Returns ``BridgeAdvice`` entries — value-level advice first (those
    the agent can apply immediately), then schema-level advice (those
    requiring a manifest edit). Empty list if the call doesn't traverse
    any current obstruction (a clean call in a possibly dirty
    composition; check ``Diagnostic.coherence_fee`` for global state).
    """
    fq_tool = f"{server}__{tool}"
    out_value: list[BridgeAdvice] = []
    out_schema: list[BridgeAdvice] = []
    for bs in diag.blind_spots:
        if fq_tool not in (bs.from_tool, bs.to_tool):
            continue
        if not _call_touches_obstruction(bs, fq_tool, arguments):
            continue
        va = _value_advice(bs, fq_tool, arguments)
        if va is not None:
            out_value.append(va)
            continue
        out_schema.append(_schema_advice(bs, fq_tool))
    return out_value + out_schema


def summarize_verdict(
    fee_after: int,
    advices: list[BridgeAdvice],
) -> Literal["safe", "advise", "refuse"]:
    """Ternary verdict for ``bulla__should_proceed``.

    Per-call sensitivity: when the agent's specific arguments don't
    actually traverse any obstruction, return ``safe`` — even if the
    composition's global fee is positive. Callers should also surface
    ``composition_fee`` separately so the agent can reason about
    global state without being false-positively refused on every call.

    - ``safe``: this specific call doesn't traverse any obstruction.
    - ``advise``: at least one obstruction is traversed AND all
      traversed ones are value-level (runtime translation suffices).
    - ``refuse``: at least one schema-level obstruction is traversed
      (runtime fix impossible; the operator must edit a manifest).
    """
    if not advices:
        return "safe"
    if any(a.kind == "schema" for a in advices):
        return "refuse"
    return "advise"


__all__ = [
    "BridgeAdvice",
    "classify_for_call",
    "summarize_verdict",
]
