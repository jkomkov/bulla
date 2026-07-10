# The Seam Problem

An agent copies a file from a local filesystem server to a GitHub repository. The file lands in the wrong location. No error is thrown.

Schema validation, model evals, and runtime observability all pass. The composition can still silently produce the wrong result.

The filesystem server uses absolute paths (`/Users/me/repo/src/main.py`). The GitHub server uses repo-relative paths (`src/main.py`). Neither server declares this convention. No existing tool surfaces the undisclosed mismatch.

Bulla surfaces this undisclosed convention — from the schemas alone. (It flags the disclosure gap, not a prediction that the write *will* land wrong; on execution-derived labels the fee does not predict failure — see [FALSIFICATIONS.md](https://github.com/jkomkov/bulla/blob/main/FALSIFICATIONS.md).)

## Output

```
$ python run_canonical_demo.py

  ════════════════════════════════════════════════════════════
    The Seam Problem — Bulla v0.30.0
  ════════════════════════════════════════════════════════════

    Servers: filesystem (14 tools), github (26 tools)

    Coherence fee: 53
    Cross-server boundary fee: 1
    Obligation: path_convention_match at filesystem ↔ github

    Guided discovery (3 round(s), 5 confirmed):
      filesystem: absolute_local
      github: relative_repo

    Contradictions: 1
      path_convention_match: absolute_local vs relative_repo (MISMATCH)

    Receipt: a73a8395... (VALID)
    Discovered conventions: path_convention_match ['absolute_local', 'relative_repo']

  ════════════════════════════════════════════════════════════
```

## Verify

```
python -c "import json; from bulla import verify_receipt_integrity; \
  print(verify_receipt_integrity(json.load(open('receipts/audit_receipt_v030.json'))))"
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
