"""Thin, read-only HTTP transport for a deed registry — the *online* surface.

A relying party on another machine can demand a deed's inclusion and look up
deeds-by-composition over plain HTTP GET. The server returns a Merkle inclusion
proof and the root.

**Trust boundary — read this.** The proof is verifiable, but the *root it verifies
against is whatever this host returns*. A malicious host can fabricate a
self-consistent tree and serve a matching proof, so checking a proof against the
host's own root proves only internal consistency, NOT that the deed is in the real
log. To trust a remote inclusion you must PIN the root to something the host can't
forge — an OTS anchor, or a root you obtained out of band — via
``verify_inclusion_record(rec, trusted_root=…)``. Absent a pinned root, a remote
``included`` means "the operator asserts it," and the verify path declines to
recommend *proceed*. The single-operator case (you run the server, you verify
against your own log) is sound; the cross-party case needs the pin.

The server is a single-operator **reference** primitive, read-only by design. It
does not issue signed tree heads. A separate experimental
``bulla.witness-checkpoint/0.1-draft`` surface can sign and transport ordering
checkpoints, but federation, plurality, and an operated writable / multi-tenant /
hosted registry remain out of scope for this stable transport.

Routes (all GET, all return JSON):
  GET /root                           -> {"root": "sha256:..", "tree_size": N}
  GET /inclusion?attestation=<id>     -> inclusion-proof record, or 404
  GET /by-composition?composition=<h> -> {"composition_hash": h, "deeds": [...]}
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from bulla.registry import DeedLog


# ── client: a ReadableRegistry over HTTP ─────────────────────────────────────

class HttpRegistry:
    """A read-only ``ReadableRegistry`` backed by a remote ``bulla registry serve``
    endpoint. Same read interface as a local ``DeedLog``, but the root it returns is
    the HOST's claim — see the module docstring. A remote inclusion is only
    trustworthy once you pin the root (``verify_inclusion_record(rec,
    trusted_root=…)``); the verify path will not recommend *proceed* otherwise."""

    is_remote = True  # the host serves the root — a remote inclusion needs a pinned root

    def __init__(self, base_url: str, *, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, **params: str) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=self.timeout) as resp:  # noqa: S310 (operator-named URL)
            return json.loads(resp.read().decode("utf-8"))

    def root(self) -> str:
        return self._get("/root")["root"]

    def inclusion_by_attestation(self, attestation_hash: str) -> dict | None:
        try:
            return self._get("/inclusion", attestation=attestation_hash)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def by_composition(self, composition_hash: str) -> list[dict]:
        return self._get("/by-composition", composition=composition_hash).get("deeds", [])


# ── server: read-only GET over a DeedLog ─────────────────────────────────────

def _make_handler(log: DeedLog):
    class _Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/root":
                self._send(200, {"root": log.root(), "tree_size": len(log)})
            elif parsed.path == "/inclusion":
                att = (q.get("attestation") or [""])[0]
                proof = log.inclusion_by_attestation(att)
                if proof is None:
                    self._send(404, {"error": "not found", "attestation": att})
                else:
                    self._send(200, proof)
            elif parsed.path == "/by-composition":
                comp = (q.get("composition") or [""])[0]
                self._send(200, {"composition_hash": comp, "deeds": log.by_composition(comp)})
            else:
                self._send(404, {"error": "unknown route", "path": parsed.path})

        def do_POST(self) -> None:  # noqa: N802 — the reference registry is read-only
            self._send(405, {"error": "registry is read-only (reference server)"})

        def log_message(self, *args: Any) -> None:  # keep the proxy/CLI quiet
            pass

    return _Handler


def make_server(log: DeedLog, host: str = "127.0.0.1", port: int = 0) -> HTTPServer:
    """Build a read-only HTTP server over ``log``. The caller runs
    ``.serve_forever()`` (the CLI blocks on it; tests run it in a thread).
    ``port=0`` picks a free port — read it from ``.server_address[1]``."""
    return HTTPServer((host, port), _make_handler(log))
