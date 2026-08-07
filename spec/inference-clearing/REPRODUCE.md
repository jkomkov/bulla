# Reproduce the recheckable-inference result

The kit contains three deterministic transaction bundles and their separately
supplied verification contexts. No provider process or network connection is
needed to verify them.

```bash
python3 -m pip install pynacl
python3 -I check.py vectors/recheckable --context contexts/recheckable.json
node check.mjs vectors/recheckable --context contexts/recheckable.json
```

Expected bounded results:

| Bundle | Model binding | Relation | Historical execution | Buyer decision | Payment | Authorization | Settlement |
|---|---|---|---|---|---|---|---|
| `opaque` | `UNAVAILABLE` | `UNAVAILABLE` | `NOT_ESTABLISHED` | `REFUSE` | `INELIGIBLE` | `NOT_ISSUED` | `NOT_ATTEMPTED` |
| `recheckable` | `TERM_BOUND` | `REPRODUCED` | `NOT_ESTABLISHED` | `RELY` | `ELIGIBLE` | `NOT_ISSUED` | `NOT_ATTEMPTED` |
| `bypass` | `TERM_BOUND` | `REPRODUCED` | `NOT_ESTABLISHED` | `REFUSE` | `INELIGIBLE` | `NOT_ISSUED` | `NOT_ATTEMPTED` |

The Python checker uses the exact minimal Bulla source closure included in the
kit. The standalone Node checker uses Node 22 built-ins and does not import
Bulla. Both are project-authored implementations over synthetic evidence;
agreement increments no external counter.
