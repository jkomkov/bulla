"""Parametric MCP backend for the live-execution discrimination experiment.

A single script that runs as either a *producer* or a *consumer* MCP server
(role + convention config supplied via the ``BULLA_SEAM_SPEC`` env var, JSON).
Both speak the minimal MCP stdio dialect that ``bulla.live_proxy.BackendServer``
expects (``initialize`` / ``notifications/initialized`` / ``tools/list`` /
``tools/call``), matching ``examples/live-mcp-proxy/fake_fetch_backend.py``.

The point of this backend is to make convention mismatches *actually fail at
runtime*, so the experiment's failure labels are execution-derived rather than
schema-derived or LLM-judged. Each semantic dimension has genuine processing
logic that raises a real Python exception on a mismatch it cannot detect:

  * ``encoding`` — bytes encoded by the producer (utf-8 / latin-1); the consumer
    decodes under its own convention. A latin-1 byte stream with a high
    codepoint is invalid utf-8 -> real ``UnicodeDecodeError``.
  * ``index``    — an array index (0-based / 1-based) into a fixed-length list.
    A 1-based last index read as 0-based overruns -> real ``IndexError``.
  * ``unit``     — a temperature (celsius / fahrenheit) the consumer asserts
    lies in a sane celsius range; a fahrenheit value fails the assertion.
  * ``path``     — a filesystem path (posix / windows); the consumer asserts its
    own separator/anchor and rejects the foreign form.

Visibility is the load-bearing link to the witness fee. When a dimension's
convention is *visible* (advertised in the observable schema), the producer
emits an explicit ``<dim>__convention`` tag and the consumer normalizes before
use -> no failure. When *hidden* (in internal state only), no tag is emitted and
the consumer processes the raw value under its own assumed convention -> the
mismatch fails silently, exactly the witness-fee semantics.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _spec() -> dict[str, Any]:
    raw = os.environ.get("BULLA_SEAM_SPEC", "{}")
    return json.loads(raw)


# ── Producer-side convention encoders ────────────────────────────────────
# Each returns the raw transported value (as JSON-serialisable data) for a
# dimension under the producer's convention.

_NON_ASCII = "café-déjà"  # contains U+00E9, distinguishes utf-8 vs latin-1


def _produce_encoding(conv: str) -> dict[str, Any]:
    if conv == "latin-1":
        raw = list(_NON_ASCII.encode("latin-1"))
    else:  # utf-8
        raw = list(_NON_ASCII.encode("utf-8"))
    return {"bytes": raw}


def _produce_index(conv: str, n: int = 5) -> dict[str, Any]:
    # Always point at the logical "last" element under the producer convention.
    return {"index": n if conv == "1-based" else n - 1, "n": n}


def _produce_unit(conv: str) -> dict[str, Any]:
    # Room temperature in exact-inverse values: 20°C = 68°F.
    return {"value": 68.0 if conv == "fahrenheit" else 20.0}


def _produce_path(conv: str) -> dict[str, Any]:
    if conv == "windows":
        return {"path": "C:\\data\\input.txt"}
    return {"path": "/data/input.txt"}


_PRODUCERS = {
    "encoding": _produce_encoding,
    "index": _produce_index,
    "unit": _produce_unit,
    "path": _produce_path,
}


# ── Consumer-side convention logic (raises on undetected mismatch) ────────


def _consume_encoding(payload: dict[str, Any], conv: str) -> None:
    data = bytes(payload["bytes"])
    # .decode raises UnicodeDecodeError on a genuine convention mismatch.
    data.decode(conv)


def _consume_index(payload: dict[str, Any], conv: str) -> None:
    arr = list(range(payload["n"]))  # length n
    idx = payload["index"]
    if conv == "1-based":
        idx = idx - 1
    # Raises IndexError when a 1-based last index is consumed as 0-based.
    _ = arr[idx]
    if idx < 0:
        raise IndexError("negative index after convention shift")


def _consume_unit(payload: dict[str, Any], conv: str) -> None:
    value = payload["value"]
    # The consumer believes the value is already in its own unit and asserts a
    # plausible range for a room temperature in that unit.
    if conv == "celsius":
        if not (10.0 <= value <= 35.0):
            raise ValueError(f"temperature {value} out of celsius range")
    else:  # fahrenheit
        if not (50.0 <= value <= 95.0):
            raise ValueError(f"temperature {value} out of fahrenheit range")


def _consume_path(payload: dict[str, Any], conv: str) -> None:
    path = payload["path"]
    if conv == "windows":
        if not (len(path) >= 2 and path[1] == ":"):
            raise ValueError(f"not a windows path: {path!r}")
    else:  # posix
        if not path.startswith("/"):
            raise ValueError(f"not a posix path: {path!r}")


_CONSUMERS = {
    "encoding": _consume_encoding,
    "index": _consume_index,
    "unit": _consume_unit,
    "path": _consume_path,
}


# ── Permissive consumers (never raise, return computed result) ─────────
#
# These model real-world MCP servers: they don't crash on wrong
# conventions, they silently produce wrong answers. The harness has
# a correctness oracle and classifies the result externally.


def _consume_encoding_permissive(payload: dict[str, Any], conv: str) -> dict[str, Any]:
    data = bytes(payload["bytes"])
    return {"decoded": data.decode(conv, errors="replace")}


def _consume_index_permissive(payload: dict[str, Any], conv: str) -> dict[str, Any]:
    arr = list(range(payload["n"]))
    idx = payload["index"]
    if conv == "1-based":
        idx = idx - 1
    if 0 <= idx < len(arr):
        return {"element": arr[idx]}
    return {"element": -1}


def _consume_unit_permissive(payload: dict[str, Any], conv: str) -> dict[str, Any]:
    return {"value": payload["value"]}


def _consume_path_permissive(payload: dict[str, Any], conv: str) -> dict[str, Any]:
    return {"path": payload["path"]}


_CONSUMERS_PERMISSIVE = {
    "encoding": _consume_encoding_permissive,
    "index": _consume_index_permissive,
    "unit": _consume_unit_permissive,
    "path": _consume_path_permissive,
}


def _normalize(dim: str, payload: dict[str, Any], producer_conv: str) -> dict[str, Any]:
    """Re-encode a payload from the (now known) producer convention into a
    canonical form the consumer can safely accept. Only reachable when the
    dimension is visible and the producer convention tag was transported."""
    if dim == "encoding":
        text = bytes(payload["bytes"]).decode(producer_conv)
        return {"bytes": list(text.encode("utf-8"))}  # canonical utf-8
    if dim == "index":
        idx = payload["index"]
        if producer_conv == "1-based":
            idx -= 1
        return {"index": idx, "n": payload["n"]}  # canonical 0-based
    if dim == "unit":
        v = payload["value"]
        if producer_conv == "fahrenheit":
            v = (v - 32.0) * 5.0 / 9.0
        return {"value": v}  # canonical celsius
    if dim == "path":
        p = payload["path"]
        if producer_conv == "windows":
            p = "/" + p[3:].replace("\\", "/")
        return {"path": p}  # canonical posix
    return payload


# ── Tool manifests ───────────────────────────────────────────────────────


def _producer_tools(spec: dict[str, Any]) -> list[dict[str, Any]]:
    dims = spec["dimensions"]
    props: dict[str, Any] = {}
    for d in dims:
        props[d["name"]] = {"type": "object"}
        if d.get("visible"):
            props[f"{d['name']}__convention"] = {"type": "string"}
    return [
        {
            "name": "produce",
            "description": "Emit a payload across the configured dimensions.",
            "inputSchema": {"type": "object", "properties": {}},
            "outputSchema": {"type": "object", "properties": props},
        }
    ]


def _consumer_tools(spec: dict[str, Any]) -> list[dict[str, Any]]:
    dims = spec["dimensions"]
    props: dict[str, Any] = {}
    for d in dims:
        props[d["name"]] = {"type": "object"}
        if d.get("visible"):
            props[f"{d['name']}__convention"] = {"type": "string"}
    return [
        {
            "name": "consume",
            "description": "Consume an upstream payload under local conventions.",
            "inputSchema": {"type": "object", "properties": props},
            "outputSchema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
            },
        }
    ]


# ── Call handlers ─────────────────────────────────────────────────────────


def _do_produce(spec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for d in spec["dimensions"]:
        name = d["name"]
        conv = d["producer_conv"]
        out[name] = _PRODUCERS[name](conv)
        if d.get("visible"):
            # The convention is advertised, so the tag travels with the value.
            out[f"{name}__convention"] = conv
    return out


def _do_consume(spec: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    mode = spec.get("mode", "strict")
    consumers = _CONSUMERS_PERMISSIVE if mode == "permissive" else _CONSUMERS
    results: dict[str, Any] = {}
    for d in spec["dimensions"]:
        name = d["name"]
        if not d.get("load_bearing", True):
            continue  # consumer does not actually use this dimension
        consumer_conv = d["consumer_conv"]
        value = payload.get(name)
        if value is None:
            continue
        tag = payload.get(f"{name}__convention")
        if tag is not None and tag != consumer_conv:
            # Visible mismatch: the consumer sees the foreign convention and
            # normalizes it before use -> no failure.
            value = _normalize(name, value, tag)
        if mode == "permissive":
            # Never raises; returns the computed result for correctness checking.
            results[name] = consumers[name](value, consumer_conv)
        else:
            # Raises on an undetected (hidden) mismatch; succeeds when
            # conventions agree or were normalized.
            consumers[name](value, consumer_conv)
    if mode == "permissive":
        return {"ok": True, "results": results}
    return {"ok": True}


def _respond(msg_id: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def _respond_error(msg_id: Any, message: str) -> None:
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": message}})
        + "\n"
    )
    sys.stdout.flush()


def main() -> None:
    spec = _spec()
    role = spec.get("role")
    tools = _producer_tools(spec) if role == "producer" else _consumer_tools(spec)

    while True:
        line = sys.stdin.readline()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            _respond(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": f"seam-{role}", "version": "0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _respond(msg_id, {"tools": tools})
        elif method == "tools/call":
            params = msg.get("params", {})
            args = params.get("arguments", {})
            try:
                if role == "producer":
                    payload = _do_produce(spec)
                else:
                    payload = _do_consume(spec, args.get("payload", {}))
                _respond(msg_id, {
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                })
            except Exception as exc:  # genuine execution failure of the seam
                _respond_error(msg_id, f"{type(exc).__name__}: {exc}")
        elif msg_id is not None:
            _respond(msg_id, {})


if __name__ == "__main__":
    main()
