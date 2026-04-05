# The Seam Problem

An agent copies a file from a local filesystem server to a GitHub repository. The file lands in the wrong location. No error is thrown.

Schema validation, model evals, and runtime observability all pass. The composition still silently fails.

The filesystem server uses absolute paths (`/Users/me/repo/src/main.py`). The GitHub server uses repo-relative paths (`src/main.py`). Neither server declares this convention. No existing tool can detect the mismatch.

Bulla catches this.

## Output

```
$ python run_canonical_demo.py

  ════════════════════════════════════════════════════════════
    The Seam Problem — Bulla v0.29.0
  ════════════════════════════════════════════════════════════

    Servers: filesystem (14 tools), github (26 tools)

    Coherence fee: 30
    Cross-server boundary fee: 1
    Obligation: path_convention_match at filesystem ↔ github

    Guided discovery (3 round(s), 5 confirmed):
      filesystem: absolute_local
      github: relative_repo

    Receipt: 45b2b3f0... (VALID)
    Discovered conventions: path_convention_match ['absolute_local', 'relative_repo']

  ════════════════════════════════════════════════════════════
```

## Verify

```
python -c "import json; from bulla import verify_receipt_integrity; \
  print(verify_receipt_integrity(json.load(open('receipts/audit_receipt.json'))))"
```

```
True
```

## Reproduce

```
pip install bulla
cd examples/canonical-demo
python run_canonical_demo.py
```
