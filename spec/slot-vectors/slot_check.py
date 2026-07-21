"""Stdlib-only decision procedure for commitment-slot vectors (DRAFT).

Imports nothing from bulla. Decides the OBJECTIVE slot-level properties from
`commitment-slot-v0.1-draft.md`: closure uniqueness under a named ordering,
deadline arithmetic against a FROZEN checkpoint, omission vs undetermined
(censorship), and rejection of an authority-forged close (inherited I3).

Cryptographic receipt, parent-chain, and map-proof verification is an UPSTREAM
precondition. This checker requires those verdicts explicitly; missing verdicts
fail closed. It never treats a proof-shaped JSON object as a verified proof.

This is the executable form of the adversarial vectors — the artifact an
external reviewer attacks: "break closure uniqueness, defeat the omission
predicate." It is a draft illustration of the spec, not a conformance suite.

    python bulla/spec/slot-vectors/slot_check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


_CLOSE_TYPES = {"delivery", "cancellation", "refusal", "timeout"}
_ORDERING_REGIMES = {"local-host", "quorum", "rail-cas"}


def _seq(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validated_event(event: dict, slot_id: str) -> bool:
    return bool(
        event.get("slot_id") == slot_id
        and event.get("receipt_validated") is True
        and event.get("receipt_authority") == "verified"
        and _seq(event.get("seq"))
    )


def _validated_proof_at(
    proof: object, *, slot_id: str, checkpoint_ref: str, checkpoint: int
) -> bool:
    """Consume an upstream proof-verifier verdict, not arbitrary proof bytes."""
    if not isinstance(proof, dict):
        return False
    return bool(
        proof.get("verified") is True
        and proof.get("slot_id") == slot_id
        and proof.get("checkpoint_ref") == checkpoint_ref
        and proof.get("checkpoint") == checkpoint
        and isinstance(proof.get("root_ref"), str)
        and proof["root_ref"].startswith("sha256:")
    )


def _validated_state_proof(proof: object, case: dict) -> bool:
    checkpoint = case["evaluation_checkpoint"]
    return _validated_proof_at(
        proof,
        slot_id=case["slot_id"],
        checkpoint_ref=checkpoint["checkpoint_ref"],
        checkpoint=checkpoint["value"],
    )


def _classify_winner(c: dict, deadline: int) -> str:
    t = c["close_type"]
    seq = c["seq"]
    if t == "timeout":
        return "TIMEOUT"
    if t == "delivery":
        if seq > deadline:
            return "LATE_DELIVERY"
        return "CLOSED_DELIVERY_CONFORMING" if c.get("conforming") else "CLOSED_DELIVERY_NONCONFORMING"
    if t == "cancellation":
        return "CLOSED_CANCELLATION"
    if t == "refusal":
        return "CLOSED_REFUSAL"
    return "UNKNOWN_CLOSE"


def evaluate_slot_detail(case: dict) -> dict:
    """Return ``{finding, faults}`` under explicit, validated preconditions."""
    faults: list[str] = []
    events = case.get("events", [])
    slot_id = case.get("slot_id")
    if not isinstance(slot_id, str) or not slot_id:
        return {"finding": "INVALID_SLOT", "faults": ["MISSING_SLOT_ID"]}
    if case.get("ordering") not in _ORDERING_REGIMES:
        return {"finding": "INVALID_SLOT", "faults": ["UNKNOWN_ORDERING_REGIME"]}

    orders = [e for e in events if e.get("type") == "order"]
    countersigns = [e for e in events if e.get("type") == "countersign"]
    if len(orders) != 1 or len(countersigns) != 1:
        return {"finding": "INVALID_SLOT", "faults": ["ORDER_COUNTERSIGN_CARDINALITY"]}
    order, countersign = orders[0], countersigns[0]
    if not _validated_event(order, slot_id) or not _validated_event(countersign, slot_id):
        return {"finding": "INVALID_SLOT", "faults": ["UNVERIFIED_OPEN"]}
    if not order["seq"] < countersign["seq"] or countersign.get("parent_seq") != order["seq"]:
        return {"finding": "INVALID_SLOT", "faults": ["BROKEN_OPEN_CHAIN"]}

    deadline_obj = case.get("deadline") or {}
    eval_obj = case.get("evaluation_checkpoint") or {}
    deadline = deadline_obj.get("value")
    evalcp = eval_obj.get("value")
    if not _seq(deadline) or not _seq(evalcp):
        return {"finding": "INVALID_SLOT", "faults": ["INVALID_CHECKPOINT"]}
    if deadline_obj.get("checkpoint_ref") != eval_obj.get("checkpoint_ref"):
        return {"finding": "UNDETERMINED_CLOSURE", "faults": ["INCOMPARABLE_CHECKPOINTS"]}
    closes = [e for e in events if e.get("type") == "close"]

    # A close is admissible only after upstream receipt, authority, and
    # parent-chain verification. Defaults are deliberately not permissive.
    structurally_valid = [
        c for c in closes
        if _validated_event(c, slot_id)
        and c.get("close_type") in _CLOSE_TYPES
        and c.get("countersigned_valid") is True
        and c.get("parent_chain_valid") is True
        and c.get("parent_seq") == countersign["seq"]
        and isinstance(c.get("receipt_hash"), str)
        and c["receipt_hash"].startswith("sha256:")
    ]

    valid: list[dict] = []
    for close in structurally_valid:
        if close["close_type"] != "timeout":
            valid.append(close)
            continue
        if _validated_proof_at(
            close.get("timeout_basis"),
            slot_id=slot_id,
            checkpoint_ref=deadline_obj["checkpoint_ref"],
            checkpoint=close["seq"],
        ):
            valid.append(close)
        else:
            faults.append("UNVERIFIED_TIMEOUT")

    # A timeout before the deadline is an invalid assertion, not a terminal
    # event that may mask a subsequent valid delivery.
    premature_timeouts = [c for c in valid if c["close_type"] == "timeout" and c["seq"] < deadline]
    if premature_timeouts:
        faults.append("FALSE_TIMEOUT")
    valid = [c for c in valid if c not in premature_timeouts]

    if closes and not valid:
        if any(c.get("receipt_authority") == "forged" for c in closes):
            return {"finding": "REJECTED_FORGED_CLOSE", "faults": faults}
        if premature_timeouts and len(premature_timeouts) == len(closes):
            return {"finding": "FALSE_TIMEOUT", "faults": faults}
        return {"finding": "REJECTED_UNVERIFIED_CLOSE", "faults": faults}

    if len(valid) >= 2:
        winner = min(valid, key=lambda c: c["seq"])
        same_position = [c for c in valid if c["seq"] == winner["seq"]]
        # Any two distinct terminal receipts at the same winning position are
        # equivocation, including same-type deliveries of different artifacts.
        if len({c["receipt_hash"] for c in same_position}) > 1:
            return {"finding": "EQUIVOCATED_CLOSURE", "faults": faults}
        for later in (c for c in valid if c["seq"] > winner["seq"]):
            faults.append(
                "SUPERSEDED_TIMEOUT" if later["close_type"] == "timeout" else "POST_CLOSE_TERMINAL"
            )
        return {"finding": _classify_winner(winner, deadline), "faults": sorted(set(faults))}
    if len(valid) == 1:
        return {"finding": _classify_winner(valid[0], deadline), "faults": faults}

    # no valid close
    if evalcp <= deadline:
        return {"finding": "ACTIVE", "faults": faults}
    if _validated_state_proof(case.get("seller_inclusion_proof"), case):
        return {"finding": "UNDETERMINED_CLOSURE", "faults": [*faults, "POSSIBLE_HOST_CENSORSHIP"]}
    if _validated_state_proof(case.get("non_membership_proof"), case):
        # This is a statement about the pinned record, never proof that the
        # seller did not perform or attempt submission.
        return {"finding": "RECORD_OMISSION", "faults": faults}
    if case.get("non_membership_proof") is not None:
        faults.append("INVALID_NON_MEMBERSHIP_PROOF")
    return {"finding": "UNDETERMINED_CLOSURE", "faults": faults}


def evaluate_slot(case: dict) -> str:
    """Backward-compatible convenience returning only the primary finding."""
    return evaluate_slot_detail(case)["finding"]


def main() -> int:
    here = Path(__file__).resolve().parent
    failures = 0
    vectors = sorted(p for p in here.glob("*.json"))
    for path in vectors:
        case = json.loads(path.read_text())
        want = case.get("expected_finding")
        detail = evaluate_slot_detail(case)
        got = detail["finding"]
        want_faults = sorted(case.get("expected_faults") or [])
        got_faults = sorted(detail["faults"])
        ok = got == want and got_faults == want_faults
        suffix = "" if ok else f"  (expected {want}; faults={want_faults})"
        print(f"  {'✓' if ok else '✗'} {path.name:32s} finding={got} faults={got_faults}{suffix}")
        if case.get("note"):
            print(f"        {case['note']}")
        if not ok:
            failures += 1
    print(f"\n{'OK' if not failures else 'FAIL'}: {len(vectors) - failures}/{len(vectors)} "
          "slot findings reproduced with zero bulla imports")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
