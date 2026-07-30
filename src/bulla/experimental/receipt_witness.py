"""First team-operated ActionReceipt witness — experimental pilot.

The operated deed registry logs composition deeds; nothing before this module
verifies an **ActionReceipt** on intake and commits it to an append-only log.
This is that service, built on the existing substrate: the RFC 6962 Merkle
mechanics and JSONL persistence of :class:`bulla.registry.DeedLog`, and the
signed tree heads of :mod:`bulla.experimental.checkpoint`.

Scope, stated exactly (see ``glyph/data/evidence-contract.json``): a
team-operated instance establishes *retention* — the exact receipt exists
outside the issuer's runtime, with an inclusion proof and a signed checkpoint.
It does not establish independence: instances under one control domain are
fault-domain diversity, not witness plurality, and the independent-witness
counter stays at zero however many of these run.

Temporal claim: intake records ``received_at`` — the one label a witness can
honestly assert. It never upgrades the receipt's own ``timestamp`` claim
(unbound under wire v0.2/v0.3; see ``spec/action-receipt-v0.4-draft.md``).

Profile: ``bulla.receipt-witness/0.1-experimental``. Experimental tier — no
wire stability, no plurality, no stake, no production operation implied.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bulla.action_receipt import verify_receipt
from bulla.identity import LocalEd25519Signer
from bulla.receipt_parser import (
    ReceiptParseError,
    ReceiptParseLimits,
    parse_action_receipt_json,
)
from bulla.registry import Deed, DeedLog
from bulla.experimental.checkpoint import WitnessCheckpoint, issue_checkpoint

SCHEMA_VERSION = "0.1-experimental"
PROFILE = "bulla.receipt-witness/0.1-experimental"
_INTAKE_PARSE_LIMITS = ReceiptParseLimits()

_DEFAULT_RETENTION = {
    "policy": "retain-exact-bytes",
    "duration": "pilot — until the pilot's declared teardown, minimum 90 days",
    "privacy": "receipts are submitted for retention; no fields are redacted, "
               "so submitters must not include undisclosable material",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class IntakeRefused(ValueError):
    """Raised when a submitted document fails receipt verification."""


@dataclass
class ReceiptWitness:
    """A verified-intake, append-only ActionReceipt witness (single operator).

    ``log_path`` holds the Merkle leaves (one deed per line); ``store_path``
    holds the exact submitted bytes keyed by attestation hash, with intake
    metadata. Both are append-only JSONL files.
    """

    operator_signer: LocalEd25519Signer
    log_id: str
    log_path: Path
    store_path: Path
    retention: dict = field(default_factory=lambda: dict(_DEFAULT_RETENTION))

    def __post_init__(self) -> None:
        self.log_path = Path(self.log_path)
        self.store_path = Path(self.store_path)
        self._log = DeedLog(self.log_path)
        self._meta: dict[str, dict] = {}
        self._bytes: dict[str, bytes] = {}
        if self.store_path.exists():
            for line in self.store_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                self._meta[rec["attestation_hash"]] = rec
                self._bytes[rec["attestation_hash"]] = rec["receipt_json"].encode("utf-8")
        self._latest_checkpoint: WitnessCheckpoint | None = None

    # ── intake ────────────────────────────────────────────────────────────

    def intake(self, receipt_bytes: bytes) -> dict:
        """Verify a submitted ActionReceipt and commit it. Fails closed.

        Returns ``{attestation_hash, index, tree_size, root, received_at,
        verified_to}``. Idempotent on attestation hash: re-submission returns
        the original intake record rather than a second leaf.
        """
        try:
            doc = parse_action_receipt_json(
                receipt_bytes, limits=_INTAKE_PARSE_LIMITS
            ).to_dict()
        except ReceiptParseError as exc:
            raise IntakeRefused(f"not a JSON document: {exc}") from exc
        verdict = verify_receipt(doc)
        if not verdict.ok:
            raise IntakeRefused(
                "receipt failed verification: " + "; ".join(verdict.reasons or ("unspecified",))
            )
        hashes = doc.get("hashes") or {}
        attestation = hashes.get("attestation")
        content = hashes.get("content")
        if not attestation or not content:
            raise IntakeRefused("receipt lacks attestation/content hashes")
        existing = self._meta.get(attestation)
        if existing is not None:
            return {k: existing[k] for k in
                    ("attestation_hash", "index", "received_at", "verified_to")} | {
                    "tree_size": len(self._log), "root": self._log.root(), "duplicate": True}

        issuer = (doc.get("signature") or {}).get("issuer") or "unsigned"
        index = self._log.append(Deed(issuer, content, attestation))
        received_at = _now_iso()
        record = {
            "attestation_hash": attestation,
            "index": index,
            "received_at": received_at,
            "verified_to": verdict.verified_to,
            "receipt_json": receipt_bytes.decode("utf-8"),
        }
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with self.store_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        self._meta[attestation] = record
        self._bytes[attestation] = receipt_bytes
        return {
            "attestation_hash": attestation,
            "index": index,
            "tree_size": len(self._log),
            "root": self._log.root(),
            "received_at": received_at,
            "verified_to": verdict.verified_to,
        }

    # ── proofs and retrieval ─────────────────────────────────────────────

    def inclusion(self, attestation_hash: str) -> dict | None:
        record = self._log.inclusion_by_attestation(attestation_hash)
        if record is None:
            return None
        meta = self._meta.get(attestation_hash) or {}
        return {**record, "received_at": meta.get("received_at"),
                "verified_to": meta.get("verified_to")}

    def consistency(self, old_size: int) -> dict:
        return self._log.consistency(old_size)

    def receipt_bytes(self, attestation_hash: str) -> bytes | None:
        return self._bytes.get(attestation_hash)

    def root(self) -> str:
        return self._log.root()

    def __len__(self) -> int:
        return len(self._log)

    # ── signed heads ─────────────────────────────────────────────────────

    def checkpoint(self) -> WitnessCheckpoint:
        """Issue a signed tree head chained to this witness's previous head."""
        head = issue_checkpoint(
            self._log,
            self.operator_signer,
            log_id=self.log_id,
            previous=self._latest_checkpoint,
        )
        self._latest_checkpoint = head
        return head

    def manifest(self) -> dict:
        """The operator manifest a client reads before relying on this pilot."""
        return {
            "profile": PROFILE,
            "schema_version": SCHEMA_VERSION,
            "operator": self.operator_signer.issuer,
            "log_id": self.log_id,
            "control_domain": "team-operated pilot — NOT an independent witness",
            "retention": dict(self.retention),
            "temporal_claims": {
                "received_at": "asserted by this witness at intake",
                "claimed_at": "actor-supplied; not bound into the signed occurrence "
                              "identity under wire v0.2/v0.3",
            },
        }


# ── transport: one writable intake route plus read-only proofs ────────────


def make_witness_server(witness: ReceiptWitness, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """HTTP surface for the pilot. POST /intake is the single writable route;
    everything else is read-only. This is a reference pilot server, not a
    hardened multi-tenant service, and says so in its manifest."""

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — http.server API
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/manifest":
                self._json(200, witness.manifest())
            elif url.path == "/root":
                self._json(200, {"root": witness.root(), "tree_size": len(witness)})
            elif url.path == "/inclusion":
                attestation = (query.get("attestation") or [""])[0]
                record = witness.inclusion(attestation)
                if record is None:
                    self._json(404, {"error": "unknown attestation hash"})
                else:
                    self._json(200, record)
            elif url.path == "/receipt":
                attestation = (query.get("attestation") or [""])[0]
                raw = witness.receipt_bytes(attestation)
                if raw is None:
                    self._json(404, {"error": "unknown attestation hash"})
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
            elif url.path == "/checkpoint/latest":
                head = witness.checkpoint()
                self._json(200, head.to_dict())
            else:
                self._json(404, {"error": "unknown route"})

        def do_POST(self) -> None:  # noqa: N802 — http.server API
            if urlparse(self.path).path != "/intake":
                self._json(404, {"error": "unknown route"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._json(400, {"accepted": False, "error": "invalid Content-Length"})
                return
            if length < 0:
                self._json(400, {"accepted": False, "error": "invalid Content-Length"})
                return
            if length > _INTAKE_PARSE_LIMITS.max_bytes:
                self._json(
                    413,
                    {
                        "accepted": False,
                        "error": (
                            f"receipt exceeds {_INTAKE_PARSE_LIMITS.max_bytes} bytes"
                        ),
                    },
                )
                return
            body = self.rfile.read(length)
            try:
                self._json(200, witness.intake(body))
            except IntakeRefused as exc:
                self._json(422, {"accepted": False, "error": str(exc)})

        def log_message(self, *args: object) -> None:  # quiet test servers
            return

    return ThreadingHTTPServer((host, port), Handler)
