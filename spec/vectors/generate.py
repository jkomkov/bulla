#!/usr/bin/env python3
"""Generate the golden vectors + expected.json.

Two families, one contract — the *spec* (not this repo's source) reproduces
every verdict:

ActionReceipt vectors
  * ``valid-release.json``        — bulla's real 0.40.0 release reconstruction (v0.1)
  * ``tampered-evidence.json``    — evidence mutated, hashes not recomputed
  * ``blank-remedy-anchor.json``  — a modality-law violation (process theater)
  * ``convention-receipt.json``   — a v0.2 receipt coining one executable and one
                                    semantic convention at a payment seam
  * ``tampered-convention.json``  — the convention relaxed after the fact (pin
                                    recomputed, so only the content hash catches it)

WitnessReceipt vectors (CANON_VERSION 2)
  * ``witness-canon2.json``       — a fresh v2 receipt (compact, canon stamped)
  * ``witness-legacy-v1.json``    — the same content minted the pre-v2 way
                                    (spaced, unstamped): MUST verify, as canon 1

``expected.json`` records bulla's ground-truth verdict for each;
``independent_check.py`` (which imports no bulla) must reproduce those
verdicts from the spec alone.

    python bulla/spec/vectors/generate.py
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from bulla._canonical import legacy_json_v1
from bulla.action_receipt import (
    build_tool_call_receipt,
    convention_definition_hash,
    verify_receipt,
)
from bulla.diagnostic import diagnose
from bulla.envelope import (
    Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy,
)
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.witness import receipt_integrity_report, witness

_HERE = Path(__file__).resolve().parent
_CORPUS = _HERE.parents[1] / "releases" / "0.40.0.json"

_FIXED_TS = "2026-07-13T00:00:00+00:00"

# The composition behind the witness vectors — small enough to recompute by
# hand, mirrored in tests/test_canonicalization.py.
_COMPOSITION = Composition(
    name="canon-guard",
    tools=(
        ToolSpec("a", ("x", "y"), ("x",)),
        ToolSpec("b", ("x", "z"), ("x",)),
    ),
    edges=(Edge("a", "b", (SemanticDimension("d", "y", "z"),)),),
)


def _envelope() -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(principal="did:key:zSpecVector", policy="policy://payments@sha256:aa"),
        bounds=Bounds(scope="payments.charge amount<=100000"),
        recourse=Recourse(
            challenge_window="P7D",
            forum=Forum(log_endpoint="https://log.example", trusted_root_ref="ots:root"),
            remedies=(
                Remedy(rung="recompute", verifier="bulla receipt verify", anchor="hashes.content"),
                Remedy(rung="escalate", verifier="maintainer review", anchor="did:key:zSpecVector"),
            ),
        ),
        retention_class="operational",
        disclosure_class="party",
    )


def _convention_receipt_dict() -> dict:
    executable = {
        "name": "amount-in-usd-cents",
        "scope": "seam:caller->payments.charge",
        "kind": "executable",
        "definition": {
            "form": "jsonschema+quantum/1",
            "schema": {
                "type": "object",
                "required": ["amount", "currency"],
                "properties": {
                    "amount": {"type": "integer", "minimum": 0},
                    "currency": {"const": "USD"},
                },
            },
            "quantum": {"amount": {"unit": "USD_cents", "multipleOf": 1}},
        },
    }
    semantic = {
        "name": "refund-honored-in-full",
        "scope": "seam:caller->payments.charge",
        "kind": "semantic",
        "definition": "A refund request within the challenge window is honored in full.",
        "forum": {"log_endpoint": "https://log.example", "trusted_root_ref": "ots:root"},
    }
    r = build_tool_call_receipt(
        tool="payments.charge",
        call_subject={"amount": 1250, "currency": "USD", "merchant": "acme"},
        diagnostic_ref={"status": "reference", "ref": "sha256:" + "e" * 64},
        envelope=_envelope(),
        result_hash="sha256:" + "f" * 64,
        conventions=(executable, semantic),
        timestamp=_FIXED_TS,
        producer={"bulla_version": "0.43.0"},
    )
    return r.to_dict()


def _witness_dicts() -> tuple[dict, dict]:
    """(canon2, legacy-v1) — same semantic content, two minting rules."""
    diag = diagnose(_COMPOSITION)
    r = witness(diag, _COMPOSITION)
    r = replace(r, timestamp=_FIXED_TS)  # deterministic vector
    v2 = r.to_dict()

    legacy = copy.deepcopy(v2)
    del legacy["canon_version"]  # pre-v2 receipts carried no stamp
    obj = {k: v for k, v in legacy.items() if k not in ("receipt_hash", "anchor_ref")}
    legacy["receipt_hash"] = hashlib.sha256(legacy_json_v1(obj).encode()).hexdigest()
    return v2, legacy


def main() -> int:
    valid = json.loads(_CORPUS.read_text())  # a real, unsigned reconstruction

    vectors: dict[str, dict] = {"valid-release.json": valid}

    tampered = copy.deepcopy(valid)
    tampered["evidence_refs"][0]["hash"] = "sha256:" + "0" * 64
    vectors["tampered-evidence.json"] = tampered

    blanked = copy.deepcopy(valid)
    blanked["remedy"]["remedies"][0]["anchor"] = ""
    vectors["blank-remedy-anchor.json"] = blanked

    conv = _convention_receipt_dict()
    vectors["convention-receipt.json"] = conv

    # The convention forgery: relax the quantum after the fact and recompute
    # the entry pin, so ONLY the content hash catches the edit.
    forged = copy.deepcopy(conv)
    forged["conventions"][0]["definition"]["quantum"]["amount"]["multipleOf"] = 100
    forged["conventions"][0]["definition_hash"] = convention_definition_hash(
        forged["conventions"][0]["definition"]
    )
    vectors["tampered-convention.json"] = forged

    expected: dict[str, dict] = {}
    for name, doc in vectors.items():
        (_HERE / name).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        v = verify_receipt(doc)
        expected[name] = {"kind": "action_receipt", "ok": v.ok, "verified_to": v.verified_to}
        if v.conventions:
            expected[name]["conventions"] = v.conventions
        if v.effective_grounding:
            expected[name]["effective_grounding"] = v.effective_grounding
        print(f"wrote {name:26s} bulla: ok={v.ok} verified_to={v.verified_to}")

    w2, w1 = _witness_dicts()
    for name, doc in (("witness-canon2.json", w2), ("witness-legacy-v1.json", w1)):
        (_HERE / name).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        rep = receipt_integrity_report(doc)
        expected[name] = {
            "kind": "witness_receipt", "ok": rep["ok"],
            "verified_to": "digest" if rep["ok"] else "none",
            "canon": rep["canon"],
        }
        print(f"wrote {name:26s} bulla: ok={rep['ok']} canon={rep['canon']}")

    (_HERE / "expected.json").write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote expected.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
