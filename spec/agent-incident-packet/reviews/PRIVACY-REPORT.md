# Agent Incident Packet v0.1 privacy report

**Assessment:** synthetic public fixtures only. Disclosure safety is not
computed.

The HTTP and MCP packets contain fixed synthetic identifiers, abstract
operations, public verification keys, signed receipts, denominator records,
coverage reports, a sanitized trace, a redaction record, and witness evidence.
They contain no customer records, production credentials, exploit material,
arbitrary host paths, or private model reasoning.

The source trace is represented as a `controlled` artifact. Its digest, byte
length, retention class, and access condition are public; its bytes are absent
from the public packet. The released trace is a separate committed artifact.
The redaction record binds the source commitment, released bytes, tool version,
rules hash, and reviewer statement. Those bindings establish which bytes and
claims were published. They do not establish that the release is safe.

Privacy controls:

- Public packets use fixed synthetic content and deterministic identities.
- Runtime pilots accept only fixed benign operations and write to a newly
  created output directory.
- The MCP subprocess receives a minimal environment. Its ephemeral signing
  seed is transferred over bootstrap stdin rather than process arguments.
- Exact retained evidence is hashed under the named adapter; raw credentials
  and request bodies are not included in the public fixtures.
- Verification is offline.
- Public-fixture tests recursively inspect JSON and JSONL for nested
  credential-like keys and values, exception and traceback leakage,
  user-specific host paths, personal-data sentinels, and digests of a fixed
  low-entropy secret dictionary.

Residual limitations:

- Secret and personal-data scanning is heuristic. It can miss novel encodings,
  high-entropy credentials, context-dependent personal data, and secrets
  outside its fixed low-entropy dictionary. It is a regression aid, not a
  disclosure-safety determination.
- Content hashes may reveal equality or permit guessing of low-entropy source
  material. The profile does not add salting, encryption, or zero-knowledge
  disclosure.
- A controlled artifact reference is an access declaration, not access-control
  enforcement.
- Team review is not external privacy review.
- The verifier intentionally returns `disclosure_safety=NOT_COMPUTED`.
