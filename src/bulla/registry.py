"""The append-only deed registry — the audit layer under signed deeds.

A signed certificate (`bulla.certificate`) is a non-repudiable **deed**, and OTS
anchoring (`bulla.ots`) proves a *shown* deed is old. Neither helps with
**omission** — a holder can withhold the deeds that count against it. This module
is the enumerable, append-only log that makes omission *checkable*: it closes
deletion and equivocation, and lets a relying party demand an inclusion proof for
each deed it expects. It does NOT, by itself, compel an agent to log a deed — the
registry *records*; it doesn't *compel*.

It is an RFC 6962 (Certificate Transparency) Merkle tree, so it inherits CT's
audit guarantees:

  * **append-only** — `verify_consistency` proves an old root is a prefix of a
    new one (no entry removed or reordered); the auditable leaf list proves it
    directly by prefix.
  * **inclusion** — `verify_inclusion` proves a specific deed is under a given
    root (a counterparty's check).
  * **enumerable** — `DeedLog.deeds(issuer=…)` returns the full set of *logged*
    deeds under an issuer by auditing the log, so a logged deed cannot be hidden
    from an auditor. (A deed that was never logged is invisible until a relying
    party demands it — that is omission, below, and the registry does not close it.)

Hashing is domain-separated (leaf = `H(0x00 ‖ data)`, node = `H(0x01 ‖ l ‖ r)`,
RFC 6962 §2.1) to foreclose second-preimage attacks. The root is one hash; anchor
it with `bulla.ots` to timestamp the whole log at a checkpoint.

**The submission boundary verifies; the log orders.** A deed enters the log through
the verified path `DeedLog.append_certificate(cert)` (= `Deed.from_certificate` +
`append`), which checks the certificate's content **integrity** AND the issuer's
**signature** before recording — so a forged certificate cannot be *submitted* under
a victim's issuer id (`did:key` issuers are forgery-proof by construction; external
issuers need a supplied key or are refused). The low-level `append(deed)` is the
post-verification Merkle primitive: it commits and orders the leaf but does NOT
re-verify, so untrusted input MUST come through `append_certificate` — raw `append`
(like a direct JSONL write, below) sits at the operator-trust boundary, not the
authenticity one.

**Read-side authenticity is where the adversary actually is.** A consumer reading an
enumeration — `deeds(issuer=X)`, `by_composition`, or the same served over HTTP — must
NOT trust the operator's claimed `issuer`, NOR that an inclusion proof it serves is for
the deed you asked about. Use **`verify_served_deed(deed_rec, incl_rec, trusted_root=…)`**:
it re-authenticates the entry from served data alone (the signature is the one the leaf
committed to, via `attestation_hash = H(content_hash, signature)`, and is genuinely the
issuer's) AND binds the inclusion proof to *this* deed's leaf (`expected_leaf`), so a host
cannot pair an authentic record with a borrowed proof for an unrelated leaf. The result —
*authentic AND included under the root I pinned, and the same deed* — means an operator
that serves a polluted enumeration is caught by the reader, not trusted by it. (The two
halves, `verify_deed_record` and `verify_inclusion_record(expected_leaf=…)`, are separately
available, but use them *together* or the bind is lost.)

**Tamper-evidence is cryptographic, not filesystem.** The JSONL file is the audit
trail, not an immutable store — anyone with write access can edit it, but doing so
changes the Merkle root, so a consistency proof against a previously *anchored*
root fails. Tamper-evidence comes from anchoring roots, not from the file.

**Honest scope (reference implementation).** This is a SINGLE-operator log. It
makes the log append-only and fully auditable, makes equivocation *detectable*
(anchored roots + consistency proofs), and closes **deletion** (no logged deed can
be removed or reordered). It does NOT close **omission**: an agent can simply not
submit a deed. Any party to a binding can submit (via `append_certificate`) a deed
*its issuer signed* — so adverse deeds *can* be relayed in — but nothing here forces
that relay. Omission
is closed only by a relying party that refuses to act on a deed without an
inclusion proof (the counterparty + bond layer, deferred); the registry only makes
that refusal *checkable*. What it also does NOT do here: enforce non-equivocation
across a distributed witness set, or resist **rekey** (a fresh `did:key` is free —
rekey is the external persistent identity's job, not the registry's). Distributed
witnesses + the operated service + the bond are the next layer, the same way the
bond relates to the signed receipt.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# RFC 6962 §2.1 domain-separation prefixes.
_LEAF = b"\x00"
_NODE = b"\x01"


def _sha(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def leaf_hash(data: bytes) -> bytes:
    """RFC 6962 leaf hash: H(0x00 ‖ data)."""
    return _sha(_LEAF + data)


def _node_hash(left: bytes, right: bytes) -> bytes:
    """RFC 6962 node hash: H(0x01 ‖ left ‖ right)."""
    return _sha(_NODE + left + right)


def _lpo2(n: int) -> int:
    """Largest power of two STRICTLY less than n (n >= 2)."""
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def merkle_root(leaves: list[bytes]) -> bytes:
    """RFC 6962 Merkle Tree Hash of a list of leaf hashes."""
    n = len(leaves)
    if n == 0:
        return _sha(b"")  # MTH of the empty list = H("")
    if n == 1:
        return leaves[0]
    k = _lpo2(n)
    return _node_hash(merkle_root(leaves[:k]), merkle_root(leaves[k:]))


def inclusion_proof(leaves: list[bytes], m: int) -> list[bytes]:
    """RFC 6962 audit path for leaf index m in a tree of len(leaves) leaves."""
    n = len(leaves)
    if not 0 <= m < n:
        raise IndexError(f"leaf index {m} out of range for tree size {n}")
    if n == 1:
        return []
    k = _lpo2(n)
    if m < k:
        return inclusion_proof(leaves[:k], m) + [merkle_root(leaves[k:])]
    return inclusion_proof(leaves[k:], m - k) + [merkle_root(leaves[:k])]


def verify_inclusion(leaf: bytes, m: int, n: int, proof: list[bytes], root: bytes) -> bool:
    """Verify `leaf` is at index m in a size-n tree with the given `root`
    (RFC 6962 §2.1.1, trillian RootFromInclusionProof form)."""
    if not 0 <= m < n:
        return False
    fn, sn = m, n - 1
    r = leaf
    for p in proof:
        if sn == 0:
            return False  # proof too long
        if (fn & 1) or fn == sn:
            r = _node_hash(p, r)
            if not (fn & 1):
                while not (fn & 1) and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            r = _node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == root


def consistency_proof(leaves: list[bytes], m: int, n: int) -> list[bytes]:
    """RFC 6962 consistency proof that the size-m prefix is consistent with the
    size-n tree (0 <= m <= n = len(leaves))."""
    if not 0 <= m <= n or n != len(leaves):
        raise ValueError(f"bad consistency range m={m} n={n} (len={len(leaves)})")
    if m == 0 or m == n:
        return []
    return _subproof(m, leaves, True)


def _subproof(m: int, leaves: list[bytes], b: bool) -> list[bytes]:
    n = len(leaves)
    if m == n:
        return [] if b else [merkle_root(leaves)]
    k = _lpo2(n)
    if m <= k:
        return _subproof(m, leaves[:k], b) + [merkle_root(leaves[k:])]
    return _subproof(m - k, leaves[k:], False) + [merkle_root(leaves[:k])]


def _is_pow2(x: int) -> bool:
    return x > 0 and (x & (x - 1)) == 0


def verify_consistency(
    m: int, n: int, proof: list[bytes], root1: bytes, root2: bytes
) -> bool:
    """Verify the size-m tree (root1) is a prefix of the size-n tree (root2)
    (RFC 6962 §2.1.2, trillian VerifyConsistency form)."""
    if m > n:
        return False
    if m == n:
        return len(proof) == 0 and root1 == root2
    if m == 0:
        # Every tree is consistent with the empty tree; proof must be empty.
        return len(proof) == 0
    if len(proof) == 0:
        return False

    node, last = m - 1, n - 1
    while node & 1:
        node >>= 1
        last >>= 1

    if node > 0:
        if not proof:
            return False
        seed = proof[0]
        rest = proof[1:]
    else:
        seed = root1
        rest = proof

    h1 = seed
    h2 = seed
    for c in rest:
        if last == 0:
            return False  # proof too long
        if (node & 1) or node == last:
            h1 = _node_hash(c, h1)
            h2 = _node_hash(c, h2)
            while not (node & 1) and node != 0:
                node >>= 1
                last >>= 1
        else:
            h2 = _node_hash(h2, c)
        node >>= 1
        last >>= 1

    return last == 0 and h1 == root1 and h2 == root2


# ── Deeds ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Deed:
    """One leaf of the registry: a signed coherence deed. `attestation_hash` is
    the deed's unique id (it commits to BOTH the coherence content and the
    signer); `content_hash` is the certificate's content hash; `issuer` keys the
    enumeration."""

    issuer: str
    content_hash: str
    attestation_hash: str
    # Index metadata ONLY — deliberately NOT part of the committed Merkle leaf.
    # `content_hash` already commits to the composition (subject.composition_sha256
    # lives inside the certificate's content-hash preimage), so the leaf stays
    # {issuer, content_hash, attestation_hash} and this field needs no leaf/root
    # migration — existing consistency proofs keep verifying. It keys the
    # composition -> deeds lookup (which issuers certified this exact composition).
    composition_hash: str = ""
    # The ed25519 proof, carried so a READ consumer (e.g. someone served by
    # `bulla registry serve`) can re-verify authenticity from served data alone —
    # without the certificate corpus. Also NOT in the leaf: `attestation_hash` (which
    # IS in the leaf) already equals H(content_hash, signature), so the served
    # signature is bound to the root transitively (see `verify_deed_record`). No
    # leaf/root migration; legacy lines simply carry `signature=None`.
    signature: dict | None = None
    # Deed v0.2: the recourse envelope {deed_schema, authority?, bounds?,
    # recourse?, retention_class?, disclosure_class?}. Carried like `signature`
    # (served-data re-verification) and, like it, NOT in the leaf: when present,
    # `attestation_hash` equals H(content_hash, signature, envelope), so the
    # served envelope is bound to the root transitively. A tampered `bounds` or
    # swapped `authority` breaks `verify_deed_record`. v0.1 deeds carry None and
    # hash exactly as before — no leaf/root migration.
    envelope: dict | None = None

    def canonical(self) -> bytes:
        # composition_hash is excluded on purpose — see the field comment. The
        # leaf must stay stable across this additive change.
        return json.dumps(
            {
                "issuer": self.issuer,
                "content_hash": self.content_hash,
                "attestation_hash": self.attestation_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def leaf(self) -> bytes:
        return leaf_hash(self.canonical())

    @classmethod
    def from_certificate(
        cls,
        cert: dict,
        *,
        require_authentic: bool = True,
        public_key: bytes | None = None,
    ) -> "Deed":
        """Extract a Deed from a SIGNED certificate dict.

        With ``require_authentic`` (the default), the certificate's content
        **integrity** and the issuer's **signature** are both verified before the
        deed is accepted — so a forged certificate cannot pollute a victim's
        history under their issuer id. ``did:key`` issuers self-verify; for an
        external issuer, supply ``public_key`` (otherwise its authenticity is
        unresolved and the deed is refused). Raises ``ValueError`` on any failure.
        """
        issuer = (cert.get("issuer") or {}).get("id")
        att = cert.get("attestation_hash")
        content = cert.get("certificate_content_hash")
        sig = cert.get("signature")
        comp_hash = (cert.get("subject") or {}).get("composition_sha256") or ""
        if not sig or not issuer or not att:
            raise ValueError(
                "certificate is not signed (no issuer/signature/attestation_hash) — "
                "sign it first: `bulla certify --sign`"
            )
        if not content:
            # A deed with an empty content_hash is malformed; never construct one
            # (guards the `content or ""` path below, esp. when require_authentic=False).
            raise ValueError("certificate has no certificate_content_hash")
        if require_authentic:
            from bulla.certificate import verify_certificate_integrity
            from bulla.identity import verify_proof

            if not verify_certificate_integrity(cert):
                raise ValueError("certificate integrity check failed (content hash mismatch)")
            auth = verify_proof(content or "", sig, public_key=public_key)
            if not auth.authentic:
                raise ValueError(
                    f"signature is not attributable to issuer {issuer} "
                    f"({auth.method}: {auth.detail or 'not authentic'}) — the registry "
                    f"only logs deeds verifiably signed by their issuer"
                )
        return cls(
            issuer=issuer,
            content_hash=content or "",
            attestation_hash=att,
            composition_hash=comp_hash,
            signature=sig,
            envelope=cert.get("recourse_envelope"),
        )


def _b2h(b: bytes) -> str:
    return f"sha256:{b.hex()}"


def _h2b(h: str) -> bytes:
    return bytes.fromhex(h.split(":", 1)[1] if h.startswith("sha256:") else h)


# ── The read surface (local or remote) ──────────────────────────────────────

@runtime_checkable
class ReadableRegistry(Protocol):
    """The read surface a relying party needs to (a) demand inclusion of a deed
    and (b) look up deeds-by-composition. Implemented by `DeedLog` (``is_remote``
    False — you hold the leaves and compute the root yourself) and `HttpRegistry`
    (``is_remote`` True — a host serves you the root).

    The difference is load-bearing: a `DeedLog`'s root is computed from leaves you
    hold, so verifying an inclusion proof against it is meaningful. An
    `HttpRegistry`'s root is whatever the host returns — verifying a proof against
    *that* is mere self-consistency. To trust a remote inclusion you must pin the
    root to something the host can't forge (an OTS anchor or a value you obtained
    out of band); see ``verify_inclusion_record(rec, trusted_root=…)``."""

    is_remote: bool

    def root(self) -> str: ...
    def inclusion_by_attestation(self, attestation_hash: str) -> dict | None: ...
    def by_composition(self, composition_hash: str) -> list[dict]: ...


# ── The persisted log ────────────────────────────────────────────────────────

class DeedLog:
    """An append-only, JSONL-persisted deed log. The file is the audit trail:
    one Deed per line, in append order. The Merkle tree is recomputed from the
    leaves (reference scale). Deduplicated by `attestation_hash` — re-appending a
    deed returns its existing index, never a second leaf."""

    is_remote = False  # you hold the leaves and compute the root — locally trustworthy

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._deeds: list[Deed] = []
        self._index: dict[str, int] = {}
        self._by_comp: dict[str, list[int]] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                self._record(
                    Deed(
                        rec["issuer"],
                        rec["content_hash"],
                        rec["attestation_hash"],
                        rec.get("composition_hash", ""),  # legacy lines lack it
                        rec.get("signature"),             # legacy lines lack it
                        rec.get("envelope"),              # v0.1 lines lack it
                    )
                )

    def _record(self, deed: Deed) -> int:
        """Add a deed to the in-memory structures (index + composition index) and
        return its index. Does NOT touch the file — callers that mutate the log
        write the JSONL line themselves."""
        idx = len(self._deeds)
        self._index[deed.attestation_hash] = idx
        if deed.composition_hash:
            self._by_comp.setdefault(deed.composition_hash, []).append(idx)
        self._deeds.append(deed)
        return idx

    def __len__(self) -> int:
        return len(self._deeds)

    def append_certificate(self, cert: dict, *, public_key: bytes | None = None) -> int:
        """The VERIFIED submission boundary — the only path untrusted input should
        take. Verifies the certificate's content integrity AND the issuer's signature
        (`Deed.from_certificate`, `require_authentic=True`) before recording, so a
        forged certificate cannot be submitted under a victim's issuer id. Returns the
        deed's index (idempotent on attestation_hash). Raises ``ValueError`` if the
        certificate is unsigned, tampered, or not authentically signed by its issuer."""
        return self.append(Deed.from_certificate(cert, public_key=public_key))

    def append(self, deed: Deed) -> int:
        """Low-level Merkle primitive: append a `Deed` leaf (idempotent on
        attestation_hash) and return its index. Does NOT re-verify authenticity —
        the submission boundary is `append_certificate`. Use this only for a deed you
        already trust (e.g. produced by `from_certificate`) or for Merkle mechanics;
        never route untrusted input here. (Raw append, like a direct JSONL write, is
        the operator-trust/anchoring boundary, not the authenticity one.)"""
        existing = self._index.get(deed.attestation_hash)
        if existing is not None:
            return existing
        idx = self._record(deed)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "issuer": deed.issuer,
            "content_hash": deed.content_hash,
            "attestation_hash": deed.attestation_hash,
        }
        if deed.composition_hash:  # additive: absent on legacy lines, present now
            rec["composition_hash"] = deed.composition_hash
        if deed.signature:         # additive: lets a read consumer re-verify authenticity
            rec["signature"] = deed.signature
        if deed.envelope:          # v0.2: the recourse envelope, bound via attestation_hash
            rec["envelope"] = deed.envelope
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
        return idx

    def _leaves(self) -> list[bytes]:
        return [d.leaf() for d in self._deeds]

    def root(self) -> str:
        return _b2h(merkle_root(self._leaves()))

    def deeds(self, issuer: str | None = None) -> list[tuple[int, Deed]]:
        """Enumerate (index, deed) pairs, optionally filtered to one issuer.
        This is the completeness query — the full set under an issuer."""
        return [
            (i, d)
            for i, d in enumerate(self._deeds)
            if issuer is None or d.issuer == issuer
        ]

    def inclusion(self, index: int) -> dict:
        """Inclusion proof for the deed at `index`, against the current root."""
        leaves = self._leaves()
        proof = inclusion_proof(leaves, index)
        return {
            "index": index,
            "tree_size": len(leaves),
            "leaf": _b2h(leaves[index]),
            "proof": [_b2h(p) for p in proof],
            "root": _b2h(merkle_root(leaves)),
        }

    def consistency(self, old_size: int) -> dict:
        """Consistency proof from a previous size to the current size."""
        leaves = self._leaves()
        proof = consistency_proof(leaves, old_size, len(leaves))
        return {
            "old_size": old_size,
            "new_size": len(leaves),
            "proof": [_b2h(p) for p in proof],
            "old_root": _b2h(merkle_root(leaves[:old_size])),
            "new_root": _b2h(merkle_root(leaves)),
        }

    def inclusion_by_attestation(self, attestation_hash: str) -> dict | None:
        """Inclusion proof for the deed identified by its `attestation_hash`, or
        `None` if no such deed is logged here. This is the relying-party path: a
        verifier holding only a deed's id can demand the proof that it is in THIS
        registry — the omission-closer's mechanism."""
        idx = self._index.get(attestation_hash)
        return None if idx is None else self.inclusion(idx)

    def by_composition(self, composition_hash: str) -> list[dict]:
        """Every deed certifying the EXACT composition `composition_hash`, as deed
        references in append order: who attested this composition coherent, and under
        whose issuer. A factual enumeration — NOT a score and NOT a verdict. Each entry
        carries its `signature`, so a consumer (incl. a remote one served by `bulla
        registry serve`) can re-authenticate it from served data alone with
        `verify_deed_record` — do NOT trust the operator's claimed issuer without that
        check. Only meaningful because the certificate content-hash is machine-independent."""
        return [
            {
                "index": idx,
                "issuer": self._deeds[idx].issuer,
                "content_hash": self._deeds[idx].content_hash,
                "attestation_hash": self._deeds[idx].attestation_hash,
                "composition_hash": self._deeds[idx].composition_hash,
                "signature": self._deeds[idx].signature,
                "envelope": self._deeds[idx].envelope,
            }
            for idx in self._by_comp.get(composition_hash, [])
        ]


def verify_inclusion_record(
    rec: dict, *, trusted_root: str | None = None, expected_leaf: str | None = None
) -> bool:
    """Verify an inclusion proof produced by `DeedLog.inclusion`.

    CRITICAL — read before trusting the result. Without ``trusted_root`` this checks
    only that the proof is **self-consistent with `rec["root"]`** — the root supplied
    IN THE SAME record. Against a remote host that is pure internal consistency: a
    malicious host fabricates a tree containing whatever it wants, serves a matching
    ``{leaf, proof, root}``, and this returns True. Inclusion then means nothing
    cross-party.

    To make it mean something, pass ``trusted_root`` — a root you obtained
    INDEPENDENTLY of this response (an OTS-anchored checkpoint, a gossiped signed
    head, or one you computed from your own log). The proof must then verify against
    THAT root, and a host that served a different root is rejected as a possible
    equivocation. Pinning the root is what turns "the host says it's logged" into
    "it is logged in the log I committed to."

    Pass ``expected_leaf`` — the deed's own ``_b2h(Deed(issuer, content_hash,
    attestation_hash).leaf())`` — to bind the proof to a SPECIFIC deed. Without it the
    proof only shows *some* leaf is under the root, so a host can answer an inclusion
    query for deed R with a valid proof for an UNRELATED leaf that genuinely sits under
    the root (borrowed inclusion) and a consumer wrongly concludes R is logged. The
    binding closes that — the proven leaf must be THIS deed's leaf."""
    if trusted_root is not None and rec.get("root") != trusted_root:
        return False  # the host served a root you did not pin — reject (equivocation)
    if expected_leaf is not None and rec.get("leaf") != expected_leaf:
        return False  # the proof covers a different leaf than the deed's — borrowed inclusion
    return verify_inclusion(
        _h2b(rec["leaf"]),
        rec["index"],
        rec["tree_size"],
        [_h2b(p) for p in rec["proof"]],
        _h2b(rec["root"]),
    )


def verify_deed_record(rec: dict, *, public_key: bytes | None = None) -> bool:
    """Re-authenticate an enumerated deed entry (from `deeds`/`by_composition`, incl.
    one served remotely by `bulla registry serve`) using ONLY the served data — no
    certificate corpus required. The operator's claimed `issuer` means nothing until
    this passes. Two bindings:

      1. the served `signature` is the one the leaf committed to:
         ``H(content_hash, signature) == attestation_hash`` (and an inclusion proof
         binds `attestation_hash` to the root, so a signature can't be swapped under a
         real leaf), AND the leaf's claimed `issuer` matches the signature's issuer;
      2. the signature is genuinely that issuer's: ``verify_proof(...).authentic``
         (forgery-proof for `did:key`; an external issuer needs ``public_key``).

    Pair with `verify_inclusion_record(rec, trusted_root=…)` for the full chain — *this
    deed is authentic AND under the root I pinned*. An operator serving a polluted
    enumeration cannot fool a consumer that runs both."""
    sig = rec.get("signature")
    content = rec.get("content_hash")
    att = rec.get("attestation_hash")
    issuer = rec.get("issuer")
    if not sig or not content or not att or not issuer:
        return False
    from bulla.certificate import _attestation_hash
    from bulla.identity import verify_proof

    env = rec.get("envelope")
    if _attestation_hash(content, sig, env) != att:
        # the served signature — or, for a v0.2 deed, the served recourse
        # envelope — is not what the leaf committed to. A tampered `bounds`,
        # a swapped `authority`, or a stripped envelope all land here.
        return False
    if env is not None:
        # Re-validate the served envelope's schema AND the modality law: a
        # remedy without a stateful anchor is refused even when its bytes hash
        # correctly — the hash proves the issuer signed it, not that it is a
        # well-formed appeal path.
        from bulla.envelope import EnvelopeError, RecourseEnvelope

        try:
            RecourseEnvelope.from_dict(env)
        except EnvelopeError:
            return False
    auth = verify_proof(content, sig, public_key=public_key)
    return bool(auth.authentic) and auth.issuer == issuer


def deed_leaf(deed_rec: dict) -> str:
    """The Merkle leaf a deed record hashes to — `_b2h(Deed(triple).leaf())`. Pass it
    as `verify_inclusion_record(expected_leaf=…)` to bind an inclusion proof to THIS
    deed (closing borrowed-inclusion)."""
    return _b2h(Deed(deed_rec["issuer"], deed_rec["content_hash"],
                     deed_rec["attestation_hash"]).leaf())


def verify_served_deed(
    deed_rec: dict, incl_rec: dict, *, trusted_root: str,
    public_key: bytes | None = None,
) -> bool:
    """The full read-side chain over served data: *this* deed is AUTHENTIC **and**
    INCLUDED under the root you pinned — and they are the SAME deed. Binds the two
    independent checks: `verify_deed_record` (authentic) + `verify_inclusion_record`
    with `expected_leaf` set to this deed's own leaf, so a host cannot pair an authentic
    record with a borrowed inclusion proof for an unrelated leaf. `deed_rec` comes from
    `by_composition`/`deeds`; `incl_rec` from `inclusion`/`inclusion_by_attestation`;
    `trusted_root` is a root you pinned independently (see `classify_root_trust`)."""
    if not verify_deed_record(deed_rec, public_key=public_key):
        return False
    return verify_inclusion_record(
        incl_rec, trusted_root=trusted_root, expected_leaf=deed_leaf(deed_rec))


def classify_root_trust(
    is_remote: bool,
    served_root: str | None,
    trusted_root: str | None,
    root_ots: str | None,
) -> tuple[str, bool]:
    """How far a served root can be trusted INDEPENDENTLY of the host. Returns
    ``(label, is_trusted)``; only a trusted root may license a `proceed` decision.
    See ``verify_inclusion_record`` for why a host-asserted root proves nothing.

      pinned         — ``trusted_root`` supplied and it matches the served root
      mismatch       — ``trusted_root`` supplied and it DIFFERS (possible equivocation)
      anchored       — ``root_ots`` is a valid OTS proof anchoring the served root
      anchor-invalid — ``root_ots`` does not anchor the served root
      own-log        — a local log whose root you computed from leaves you hold
      host-asserted  — a remote host's bare claim, nothing pinned (NOT trusted)
    """
    if served_root is None:
        return "none", False
    if trusted_root is not None:
        return ("pinned", True) if served_root == trusted_root else ("mismatch", False)
    if root_ots:
        try:
            from bulla.ots import verify_hash
            hexroot = served_root.split(":", 1)[1] if ":" in served_root else served_root
            if verify_hash(hexroot, root_ots).get("valid"):
                return "anchored", True
        except Exception:
            pass
        return "anchor-invalid", False
    if not is_remote:
        return "own-log", True
    return "host-asserted", False


def verify_consistency_record(rec: dict) -> bool:
    """Verify a consistency proof produced by `DeedLog.consistency`."""
    return verify_consistency(
        rec["old_size"],
        rec["new_size"],
        [_h2b(p) for p in rec["proof"]],
        _h2b(rec["old_root"]),
        _h2b(rec["new_root"]),
    )
