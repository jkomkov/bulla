#!/usr/bin/env python3
"""Generate the golden vectors + expected.json.

The vectors are a valid receipt (bulla's own real 0.40.0 release reconstruction)
plus two adversarial mutations. ``expected.json`` records bulla's ground-truth
verdict for each; ``independent_check.py`` (which imports no bulla) must
reproduce those verdicts from the spec alone.

    python bulla/spec/vectors/generate.py
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from bulla.action_receipt import verify_receipt

_HERE = Path(__file__).resolve().parent
_CORPUS = _HERE.parents[1] / "releases" / "0.40.0.json"


def main() -> int:
    valid = json.loads(_CORPUS.read_text())  # a real, unsigned reconstruction

    # (1) valid, untouched
    vectors: dict[str, dict] = {"valid-release.json": valid}

    # (2) tampered evidence, hashes NOT recomputed -> content hash mismatch
    tampered = copy.deepcopy(valid)
    tampered["evidence_refs"][0]["hash"] = "sha256:" + "0" * 64
    vectors["tampered-evidence.json"] = tampered

    # (3) a remedy whose anchor was blanked -> modality-law violation
    #     (process theater: a remedy that executes against nothing stateful)
    blanked = copy.deepcopy(valid)
    blanked["remedy"]["remedies"][0]["anchor"] = ""
    vectors["blank-remedy-anchor.json"] = blanked

    expected: dict[str, dict] = {}
    for name, doc in vectors.items():
        (_HERE / name).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        v = verify_receipt(doc)
        expected[name] = {"ok": v.ok, "verified_to": v.verified_to}
        print(f"wrote {name:26s} bulla: ok={v.ok} verified_to={v.verified_to}")

    (_HERE / "expected.json").write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote expected.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
