"""Sign coherence attestations under an external agent identity.

Bulla **signs, never mints.** It binds a certificate to whatever identity the
agent already holds; it does not issue identities. The default issuer scheme is
``did:key`` — *self-certifying*: the id is derived from the public key, so
verification recovers the key from the issuer itself and is **forgery-proof by
construction** (no resolution, no key↔issuer trust gap). If you sign with key A
but stamp ``issuer = did:key(B)``, verification derives B from the issuer and the
signature made by A fails.

Other schemes (``did:web``, ``eip155``/ERC-8004, Entra, SPIFFE) are carried as
opaque issuer URIs. Resolving them to a key is out of scope here; verification of
those reports unauthenticated/unresolved unless a public key is supplied
out-of-band. A proof's did:key ``verificationMethod`` can show which key made the
signature, but never by itself binds that key to the external issuer.

Crypto is ed25519 via PyNaCl, behind the optional ``bulla[identity]`` extra.
Without the extra, signing raises a clear error; unsigned certificates keep their
content-hash integrity and verify without this module.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from bulla._canonical import canonical_json

# Proof type tag. The signature is a detached ed25519 signature over the UTF-8
# bytes of the certificate's `certificate_content_hash` (the "sha256:<hex>"
# string), which already commits to the issuer and every meaningful field.
PROOF_TYPE = "bulla/ed25519-2026"

# ── domain separation (v0.3) ─────────────────────────────────────────────────
#
# A v0.2 proof signs the raw "sha256:…" digest string. Distinct digests already
# prevent replay between the content and authorization proofs, but they do not
# CATEGORICALLY separate proof purposes: a signature is only as bound to its role
# as the bytes it commits to. v0.3 proofs therefore sign a canonical preimage that
# carries the purpose IN THE SIGNED BYTES, so a proof minted for one purpose can
# never be replayed for another, regardless of any digest coincidence.
PROOF_CONTEXT = "bulla-proof"
PROOF_SCHEMA = "0.3"
PROOF_PURPOSES = frozenset(
    {"content", "authorization", "delegation-grant", "witness-checkpoint"}
)


def domain_preimage(purpose: str, digest: str) -> bytes:
    """The v0.3 signed bytes: canonical ``{context, schema, purpose, digest}``.
    The purpose is inside the signature, not a mutable label beside it."""
    if purpose not in PROOF_PURPOSES:
        raise ValueError(f"unknown proof purpose {purpose!r}; expected one of {sorted(PROOF_PURPOSES)}")
    return canonical_json(
        {"context": PROOF_CONTEXT, "schema": PROOF_SCHEMA, "purpose": purpose, "digest": digest}
    ).encode("utf-8")

# multicodec varint prefix for ed25519-pub: code 0xED -> unsigned varint [0xed,0x01].
_ED25519_MULTICODEC = b"\xed\x01"

# Bitcoin/IPFS base58btc alphabet (multibase prefix 'z').
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n > 0:
        n, rem = divmod(n, 58)
        out = _B58_ALPHABET[rem] + out
    pad = 0
    for byte in data:
        if byte == 0:
            pad += 1
        else:
            break
    return "1" * pad + out


def _b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        idx = _B58_ALPHABET.find(ch)
        if idx < 0:
            raise ValueError(f"invalid base58 character: {ch!r}")
        n = n * 58 + idx
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n > 0 else b""
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + body


def did_key_from_pubkey(pubkey: bytes) -> str:
    """Encode a 32-byte ed25519 public key as a ``did:key`` (self-certifying)."""
    if len(pubkey) != 32:
        raise ValueError(f"ed25519 public key must be 32 bytes, got {len(pubkey)}")
    return "did:key:z" + _b58encode(_ED25519_MULTICODEC + pubkey)


def pubkey_from_did_key(did: str) -> bytes:
    """Recover the 32-byte ed25519 public key from a ``did:key``. Raises on a
    non-ed25519 or malformed did:key — this is what makes did:key forgery-proof:
    the verifier derives the key from the claimed issuer, not from the proof."""
    prefix = "did:key:z"
    if not did.startswith(prefix):
        raise ValueError("not a base58btc did:key (expected 'did:key:z…')")
    raw = _b58decode(did[len(prefix):])
    if raw[:2] != _ED25519_MULTICODEC:
        raise ValueError("did:key is not an ed25519 key (multicodec prefix mismatch)")
    key = raw[2:]
    if len(key) != 32:
        raise ValueError(f"decoded ed25519 key must be 32 bytes, got {len(key)}")
    return key


def _require_nacl() -> None:
    try:
        import nacl.signing  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "ed25519 signing/verification requires the [identity] extra: "
            "pip install bulla[identity]"
        ) from exc


@dataclass(frozen=True)
class LocalEd25519Signer:
    """A local ed25519 signer.

    ``issuer`` defaults to the ``did:key`` of the keypair (self-certifying). Pass
    ``issuer_override`` to bind to an external identity URI (did:web, eip155,
    Entra, SPIFFE); the proof's ``verificationMethod`` still records the actual
    signing key's did:key, so a resolver can later check that the external
    issuer authorizes this key (deferred).
    """

    seed: bytes  # 32-byte ed25519 seed (SECRET — never serialized into a receipt)
    issuer_override: str | None = None

    @classmethod
    def generate(cls, issuer_override: str | None = None) -> "LocalEd25519Signer":
        _require_nacl()
        from nacl.signing import SigningKey

        return cls(seed=bytes(SigningKey.generate().encode()), issuer_override=issuer_override)

    @property
    def public_key(self) -> bytes:
        _require_nacl()
        from nacl.signing import SigningKey

        return bytes(SigningKey(self.seed).verify_key.encode())

    @property
    def verification_method(self) -> str:
        return did_key_from_pubkey(self.public_key)

    @property
    def issuer(self) -> str:
        return self.issuer_override or self.verification_method

    @property
    def issuer_type(self) -> str:
        if self.issuer.startswith("did:key:"):
            return "did:key"
        if self.issuer.startswith("did:"):
            return "did"
        if self.issuer.startswith("eip155:") or self.issuer.startswith("erc8004:"):
            return "erc8004"
        return "uri"

    def issuer_block(self) -> dict:
        """The certificate ``issuer`` field — committed inside the content hash."""
        return {"type": self.issuer_type, "id": self.issuer}

    def sign(self, content_hash: str) -> dict:
        """Detached ed25519 signature over the content-hash (v0.2 construction:
        the signature covers the raw ``sha256:…`` string). Returns a proof dict."""
        _require_nacl()
        from nacl.signing import SigningKey

        sig = SigningKey(self.seed).sign(content_hash.encode("utf-8")).signature
        return {
            "type": PROOF_TYPE,
            "issuer": self.issuer,
            "verificationMethod": self.verification_method,
            "proofValue": base64.b64encode(bytes(sig)).decode("ascii"),
        }

    def sign_domain(self, purpose: str, digest: str) -> dict:
        """Detached ed25519 signature over the v0.3 domain-separated preimage
        (``{context, schema, purpose, digest}``). The proof carries ``purpose`` as
        a label, but its security comes from the purpose being in the signed
        bytes: :func:`verify_proof_domain` rebuilds the preimage from the purpose
        the caller expects, so a mislabelled or cross-purpose proof fails."""
        _require_nacl()
        from nacl.signing import SigningKey

        sig = SigningKey(self.seed).sign(domain_preimage(purpose, digest)).signature
        return {
            "type": PROOF_TYPE,
            "purpose": purpose,
            "issuer": self.issuer,
            "verificationMethod": self.verification_method,
            "proofValue": base64.b64encode(bytes(sig)).decode("ascii"),
        }

    # -- keyfile (a local convenience; NEVER embedded in a certificate) --

    def to_keyfile_dict(self) -> dict:
        return {
            "version": 1,
            "alg": "ed25519",
            "did": self.verification_method,
            "issuer": self.issuer,
            "secret_key_b64": base64.b64encode(self.seed).decode("ascii"),
            "public_key_b64": base64.b64encode(self.public_key).decode("ascii"),
        }

    @classmethod
    def from_keyfile_dict(cls, data: dict) -> "LocalEd25519Signer":
        seed = base64.b64decode(data["secret_key_b64"])
        override = data.get("issuer")
        # If the stored issuer equals the derived did:key, keep it self-certifying.
        signer = cls(seed=seed)
        if override and override != signer.verification_method:
            return cls(seed=seed, issuer_override=override)
        return signer


@dataclass(frozen=True)
class Authenticity:
    """Result of verifying a proof's signature. ``authentic`` is only meaningful
    alongside ``method``: 'did:key' is forgery-proof; 'supplied-key' trusts the
    caller's key; 'verification-method' proves the VM key signed but not that the
    issuer authorized it; 'unresolved' means the issuer scheme needs resolution
    that is out of scope here."""

    authentic: bool
    method: str  # "did:key" | "supplied-key" | "verification-method" | "unresolved"
    issuer: str
    detail: str = ""

    def __bool__(self) -> bool:
        # ``authentic`` is only meaningful alongside ``method`` — a valid signature
        # under an unbound verification-method key is NOT issuer authenticity. A bare
        # ``if verify_proof(...):`` would read a plain object as always-true and
        # attribute a forgeable signature to the issuer. Force the caller to read
        # ``.authentic`` (and consider ``.method``).
        raise TypeError(
            "The truth value of an Authenticity is ambiguous — read `.authentic` "
            "(and check `.method`: a valid signature under an unbound key is not "
            "issuer authenticity). Do not write `if verify_proof(...):`."
        )


def _authenticate(
    signed_bytes: bytes, proof: dict, public_key: bytes | None
) -> Authenticity:
    """Shared verification core over the exact ``signed_bytes``.

    Key-selection order (the forgery-proof part): a self-certifying ``did:key``
    issuer always derives its own key, and a supplied key may only confirm—not
    override—that binding. For an external issuer, an explicitly supplied key
    asserts the binding; otherwise a ``did:key`` verificationMethod proves only
    that key signed, not that the external issuer authorized it. Used
    by both the v0.2 raw-digest and v0.3 domain-separated verifiers so the
    key-binding logic can never drift between them.
    """
    issuer = str(proof.get("issuer", ""))
    if proof.get("type") != PROOF_TYPE:
        return Authenticity(
            False,
            "unresolved",
            issuer,
            f"unsupported proof type {proof.get('type')!r}; expected {PROOF_TYPE!r}",
        )
    vmethod = str(proof.get("verificationMethod", ""))
    sig_b64 = proof.get("proofValue")
    if not sig_b64:
        return Authenticity(False, "unresolved", issuer, "proof has no proofValue")

    pubkey: bytes | None = None
    method = "unresolved"
    if issuer.startswith("did:key:"):
        try:
            pubkey, method = pubkey_from_did_key(issuer), "did:key"
        except ValueError:
            return Authenticity(False, "did:key", issuer, "malformed did:key issuer")
        if vmethod != issuer:
            return Authenticity(
                False, "did:key", issuer,
                "did:key proof verificationMethod must equal its self-certifying issuer",
            )
        if public_key is not None and public_key != pubkey:
            return Authenticity(
                False, "did:key", issuer,
                "supplied key conflicts with the self-certifying did:key issuer",
            )
    elif public_key is not None:
        pubkey, method = public_key, "supplied-key"
    elif vmethod.startswith("did:key:"):
        try:
            pubkey, method = pubkey_from_did_key(vmethod), "verification-method"
        except ValueError:
            return Authenticity(False, "verification-method", issuer, "malformed did:key verificationMethod")

    if pubkey is None:
        return Authenticity(
            False,
            "unresolved",
            issuer,
            "issuer scheme requires resolution (did:web / eip155 / entra / spiffe) — out of scope; "
            "supply a public key to verify",
        )

    _require_nacl()
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    try:
        VerifyKey(pubkey).verify(signed_bytes, base64.b64decode(sig_b64, validate=True))
    except BadSignatureError:
        return Authenticity(False, method, issuer, "signature does not verify under the key")
    except Exception as exc:  # malformed key/sig
        return Authenticity(False, method, issuer, f"verification error: {exc}")

    # The signature is valid under `pubkey`. ``authentic`` answers the stronger
    # question — did the *claimed issuer* sign? — which holds only when the key is
    # BOUND to the issuer:
    #   did:key issuer    -> key derived from the issuer itself (binding by construction)
    #   supplied-key      -> the caller asserts this key is the issuer's
    #   verification-method (external issuer, key from the proof's VM) -> NOTHING binds
    #     the VM key to the external issuer without resolution. A valid signature here
    #     proves *a* key signed, NOT that the issuer authorized it. Returning
    #     authentic=False is the security boundary: a slasher/consumer must never
    #     attribute a forgeable VM signature to the claimed issuer.
    if method == "verification-method":
        return Authenticity(
            False,
            method,
            issuer,
            "verificationMethod key signed, but its binding to the issuer is unverified "
            "(resolve the issuer or supply its public key)",
        )
    return Authenticity(True, method, issuer)


def verify_proof(
    content_hash: str, proof: dict, public_key: bytes | None = None
) -> Authenticity:
    """v0.2: verify a detached ed25519 proof over the raw ``content_hash`` string."""
    expected = {"type", "issuer", "verificationMethod", "proofValue"}
    if not isinstance(proof, dict) or set(proof) != expected:
        issuer = str(proof.get("issuer", "")) if isinstance(proof, dict) else ""
        return Authenticity(
            False, "unresolved", issuer,
            f"v0.2 proof fields must be exactly {sorted(expected)}",
        )
    return _authenticate(content_hash.encode("utf-8"), proof, public_key)


def verify_proof_domain(
    purpose: str, digest: str, proof: dict, public_key: bytes | None = None
) -> Authenticity:
    """v0.3: verify a domain-separated proof. The preimage is rebuilt from the
    ``purpose`` the CALLER expects, so a proof minted for a different purpose fails
    by construction; the proof's own ``purpose`` label must also match (a clearer
    early error than a bare signature failure)."""
    expected = {"type", "purpose", "issuer", "verificationMethod", "proofValue"}
    if not isinstance(proof, dict) or set(proof) != expected:
        issuer = str(proof.get("issuer", "")) if isinstance(proof, dict) else ""
        return Authenticity(
            False, "unresolved", issuer,
            f"v0.3 proof fields must be exactly {sorted(expected)}",
        )
    issuer = str(proof.get("issuer", ""))
    label = proof.get("purpose")
    if label != purpose:
        return Authenticity(
            False, "unresolved", issuer,
            f"proof purpose {label!r} does not match the expected purpose {purpose!r}",
        )
    try:
        signed = domain_preimage(purpose, digest)
    except ValueError as exc:
        return Authenticity(False, "unresolved", issuer, str(exc))
    return _authenticate(signed, proof, public_key)
