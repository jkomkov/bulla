"""CANON_VERSION-2 drift guard — byte-exact vectors for both hash layers.

The one canonicalization rule (``bulla._canonical.canonical_json``) is what
makes every bulla hash recomputable by a stranger. This file pins it three
ways so it can never silently drift again:

  1. **Byte-exact serialization vectors** — exact output strings for the
     canonical (v2, compact) and legacy (v1, spaced) forms.
  2. **Deed layer byte-unchanged** — the layer was already compact in v0.1;
     the checked-in golden vectors and release receipts must verify
     identically after the single-sourcing.
  3. **Witness layer moved to v2, legacy accepted** — new receipts stamp
     ``canon_version: 2`` and hash compact; a pre-v2 (spaced, unstamped)
     receipt still verifies, reported as canon 1 — a format change is a
     version difference, not tampering.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from bulla._canonical import CANON_VERSION, canonical_json, legacy_json_v1
from bulla.action_receipt import verify_receipt
from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.witness import (
    receipt_integrity_report,
    verify_receipt_integrity,
    witness,
)

_REPO = Path(__file__).resolve().parents[1]

SAMPLE_COMPOSITION = Composition(
    name="canon-guard",
    tools=(
        ToolSpec("a", ("x", "y"), ("x",)),
        ToolSpec("b", ("x", "z"), ("x",)),
    ),
    edges=(Edge("a", "b", (SemanticDimension("d", "y", "z"),)),),
)


# ── 1. the serialization rule itself, byte-exact ─────────────────────


class TestCanonicalJson:
    # A fixture exercising key sorting, nesting, unicode escaping, and
    # every JSON scalar kind.
    OBJ = {"b": [1, 2, {"y": None, "x": True}], "a": "é", "c": 1}

    def test_canon_version_is_2(self):
        assert CANON_VERSION == 2

    def test_v2_byte_exact(self):
        assert (
            canonical_json(self.OBJ)
            == '{"a":"\\u00e9","b":[1,2,{"x":true,"y":null}],"c":1}'
        )

    def test_v1_byte_exact(self):
        assert (
            legacy_json_v1(self.OBJ)
            == '{"a": "\\u00e9", "b": [1, 2, {"x": true, "y": null}], "c": 1}'
        )

    def test_v2_hash_pinned(self):
        # The full pipeline pinned to a hex constant: any change to the
        # rule (separators, escaping, sorting) breaks this line.
        h = hashlib.sha256(canonical_json(self.OBJ).encode("utf-8")).hexdigest()
        assert h == hashlib.sha256(
            b'{"a":"\\u00e9","b":[1,2,{"x":true,"y":null}],"c":1}'
        ).hexdigest()


# ── 2. deed layer: byte-unchanged by the single-sourcing ─────────────


class TestDeedLayerUnchanged:
    def test_spec_vectors_verify_identically(self):
        """The checked-in golden vectors' expected verdicts must reproduce
        exactly under the v2 code (action receipts were always compact; the
        witness vectors pin canon-2 and the legacy fallback)."""
        vectors = _REPO / "spec" / "vectors"
        expected = json.loads((vectors / "expected.json").read_text())
        assert expected, "expected.json is empty — the gate would be vacuous"
        for name, want in expected.items():
            doc = json.loads((vectors / name).read_text())
            if want.get("kind") == "witness_receipt":
                rep = receipt_integrity_report(doc)
                assert rep["ok"] == want["ok"], name
                assert rep["canon"] == want["canon"], name
            else:
                v = verify_receipt(doc)
                # Signed vectors split the verdict: top-level `ok`/`verified_to`
                # are the stdlib (digest) rung; `identity` holds bulla's full
                # crypto verdict (bulla verifies signatures when identity is
                # installed, so it reaches the identity rung here).
                target = want.get("identity", want)
                assert v.ok == target["ok"], f"{name}: ok {v.ok} != {target['ok']}"
                assert v.verified_to == target["verified_to"], name

    def test_release_receipts_verify(self):
        for path in sorted((_REPO / "releases").glob("0.*.json")):
            doc = json.loads(path.read_text())
            v = verify_receipt(doc)
            assert v.ok, f"{path.name}: {v.reasons}"

    def test_release_candidate_receipt_signed(self):
        """The 0.43.0 candidate receipt (minted by scripts/mint_release_receipt.py)
        carries a non-null signature and verifies — to attestation when
        bulla[identity] is present, digest otherwise."""
        path = _REPO / "releases" / "candidates" / "0.43.0-rc.json"
        doc = json.loads(path.read_text())
        assert doc["signature"] is not None
        v = verify_receipt(doc)
        assert v.ok, v.reasons
        assert v.verified_to in ("digest", "attestation")
        assert v.effective_grounding == "third_party_anchored"

    def test_attestation_preimage_byte_exact(self):
        """The deed-layer attestation preimage, pinned byte-for-byte."""
        preimage = {
            "certificate_content_hash": "sha256:" + "0" * 64,
            "signature": None,
        }
        assert canonical_json(preimage) == (
            '{"certificate_content_hash":"sha256:'
            + "0" * 64
            + '","signature":null}'
        )


# ── 3. witness layer: v2 minted, v1 accepted ─────────────────────────


def _fresh_receipt_dict() -> dict:
    diag = diagnose(SAMPLE_COMPOSITION)
    return witness(diag, SAMPLE_COMPOSITION).to_dict()


class TestWitnessLayerV2:
    def test_new_receipts_stamp_canon_version(self):
        d = _fresh_receipt_dict()
        assert d["canon_version"] == 2

    def test_receipt_hash_is_compact_canonical(self):
        """Byte-exactness of the minting rule: the stored hash equals a
        from-scratch recomputation over canonical_json — the exact
        recipe the spec hands a stranger."""
        d = _fresh_receipt_dict()
        obj = {k: v for k, v in d.items() if k not in ("receipt_hash", "anchor_ref")}
        assert (
            hashlib.sha256(canonical_json(obj).encode()).hexdigest()
            == d["receipt_hash"]
        )
        # ...and does NOT equal the legacy spaced form.
        assert (
            hashlib.sha256(legacy_json_v1(obj).encode()).hexdigest()
            != d["receipt_hash"]
        )

    def test_v2_receipt_verifies_as_canon_2(self):
        d = _fresh_receipt_dict()
        assert verify_receipt_integrity(d)
        assert receipt_integrity_report(d) == {"ok": True, "canon": 2}

    def test_legacy_v1_receipt_still_verifies(self):
        """A pre-v2 receipt: no canon_version stamp, hash minted with the
        spaced form. Accepted, and reported as canon 1."""
        d = _fresh_receipt_dict()
        del d["canon_version"]
        obj = {k: v for k, v in d.items() if k not in ("receipt_hash", "anchor_ref")}
        d["receipt_hash"] = hashlib.sha256(legacy_json_v1(obj).encode()).hexdigest()
        assert verify_receipt_integrity(d)
        assert receipt_integrity_report(d) == {"ok": True, "canon": 1}

    def test_tampered_receipt_fails_both_forms(self):
        d = _fresh_receipt_dict()
        tampered = copy.deepcopy(d)
        tampered["fee"] = tampered["fee"] + 1
        assert not verify_receipt_integrity(tampered)
        assert receipt_integrity_report(tampered) == {"ok": False, "canon": None}

    def test_missing_hash_fails(self):
        d = _fresh_receipt_dict()
        del d["receipt_hash"]
        assert not verify_receipt_integrity(d)
