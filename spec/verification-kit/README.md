# ActionReceipt v0.2 verification kit

This kit carries the normative ActionReceipt v0.2 specification, schema,
checklists, constructed vectors, and a standard-library checker. It is a
retained verification tool, not a new receipt format.

Run one command from the extracted directory:

```sh
python3 verify.py
```

The command checks every extracted payload against `MANIFEST.json`, checks the
manifest digest, and reproduces the included receipt verdicts without importing
Bulla or making a network request.

Verify a retained ActionReceipt v0.2 supplied by the relying party:

```sh
python3 verify.py receipt receipt.json
```

Add `--format json` for the machine-readable dimensional report. Add
`--key PUBLIC_KEY.json` only when a non-`did:key` issuer requires a retained
public key. Receipt mode installs a Python audit hook before loading the
checker and denies network and subprocess operations. The report separates
checks reproduced from retained bytes from claims that still need another
record.

The canonical payment is constructed. The checker establishes record integrity,
recomputes the receipt-carried USD 200 limit convention, and reports stated
evidence grounding. It does not establish that funds moved, that the named
principal had live authority, that the forum remains reachable, or that the
supplied action record is complete.

`vectors/signed-authorized.json` exercises the optional v0.3 identity rung. It
does not change the normative v0.2 wire format. If PyNaCl is unavailable, the
checker reports the identity rung as skipped instead of claiming it ran.

`MANIFEST.json` makes the extracted contents self-checking. It does not prove
who published the archive. Authenticate the archive with the detached
`action-receipt-v0.2-verification-kit.zip.sha256` or a signed Bulla release
receipt obtained through the release channel.
