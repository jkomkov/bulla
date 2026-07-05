"""Cross-boundary bonded transaction — the thesis, end to end.

Org X hires **agent B, a stateless runtime agent from another org**, to execute a
financial settlement. B has no persistent identity and nothing to lose: it can
commit, act, and terminate before any consequence attaches. Reputation,
disbarment, liability — every enforcement mechanism civilization uses presupposes
a persistent entity that values its future. B has none. So X requires a **bond**:
something at stake, pre-funded, that persists when B does not.

The receipt is what makes the bond *slashable*. B acts; bulla mints a signed
`ToolCallReceipt` whose `diagnostic_ref` is a **recomputable verdict** on the
settlement. B then vanishes. A bystander — X, an auditor, anyone — recomputes the
verdict from the receipt's pinned inputs and, finding the settlement carried
undisclosed convention deficits, **slashes the bond with no oracle and no
arbitrator**. You cannot jail a fork; you can slash its bond.

What this demo does NOT claim: the recomputable verdict is an **objective trigger
and a cap** (the coherence fee = the disclosures owed). It does **not** price
severity or harm — that is the junior tranche, and it needs an adjudicator (a
carrier). The senior tranche shown here slashes on the objective trigger only.

    python bulla/examples/cross-boundary-bond/demo.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from bulla.action_receipt import build_tool_call_receipt, verify_receipt
from bulla.diagnostic import diagnose
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.parser import load_composition
from bulla.witness import witness

_HERE = Path(__file__).resolve().parent
_COMPOSITION = _HERE / "settlement_pipeline.yaml"

ORG_X = "did:key:zOrgX-buyer"          # the surviving principal (persists)
ESCROW = "escrow://settlement-market"  # where the stake is held


# ── the bond (a stub — the mechanism, not the rail; x402/AP2 escrow come later) ──

@dataclass
class Bond:
    amount: int
    currency: str
    posted_by: str
    beneficiary: str            # who is made whole on a slash
    slash_condition: str
    held_by: str = ESCROW
    status: str = "held"
    resolution: dict = field(default_factory=dict)

    def slash(self, reason: str) -> dict:
        self.status = "slashed"
        self.resolution = {"paid_to": self.beneficiary, "amount": self.amount,
                           "currency": self.currency, "reason": reason}
        return self.resolution


# ── the recomputable verdict (bulla's core diagnostic) ───────────────────────

def settlement_verdict(comp_path: Path):
    """The deterministic verdict on a composition: fee (disclosures owed) and the
    diagnostic content hash. Recomputable by anyone from the pinned inputs."""
    comp = load_composition(path=comp_path)
    receipt = witness(diagnose(comp), comp)
    return comp, receipt  # receipt.fee, receipt.diagnostic_hash, receipt.composition_hash are deterministic


# ── B acts and receipts (then vanishes) ──────────────────────────────────────

def agent_b_settles(signer) -> dict:
    """B executes the settlement and signs a ToolCallReceipt binding the mandate
    and the recomputable verdict. B's ephemeral did:key is the signature issuer;
    the surviving principal is Org X."""
    comp, wr = settlement_verdict(_COMPOSITION)

    envelope = RecourseEnvelope(
        authority=Authority(
            principal=ORG_X,                      # surviving principal — the escalate anchor
            policy="policy://settlement-market/mandate@sha256:m",
            delegation=("marketplace:settlement",),
        ),
        bounds=Bounds(scope="settle: one transaction, coherent conventions required"),
        recourse=Recourse(
            challenge_window="P14D",
            forum=Forum(log_endpoint="https://log.settlement-market", trusted_root_ref="ots:sha256:market-root"),
            remedies=(
                Remedy(rung="recompute", verifier="bulla: re-diagnose the pinned composition",
                       anchor=f"composition@{wr.composition_hash}"),
                Remedy(rung="slash", verifier="fee>0 recomputed by any party",
                       anchor="the posted bond"),
                Remedy(rung="escalate", verifier="marketplace review", anchor=ORG_X),
            ),
        ),
        retention_class="authority-permanent",
        disclosure_class="party",
    )

    subject = {
        "composition": comp.name if hasattr(comp, "name") else "settlement",
        "composition_hash": wr.composition_hash,
        "pinned_file": _COMPOSITION.name,
        "executed_by": signer.issuer_block().get("id"),   # B's ephemeral did:key
    }
    # the verdict rides as a recomputable reference: the deterministic diagnostic hash
    diagnostic_ref = {"status": "reference", "ref": wr.diagnostic_hash}

    unsigned = build_tool_call_receipt(
        tool="settlement.execute", call_subject=subject, diagnostic_ref=diagnostic_ref,
        envelope=envelope, result_hash=wr.composition_hash,
        anchor_ref={"kind": "composition", "ref": wr.composition_hash},
        timestamp="2026-07-04T12:00:00+00:00", producer={"bulla_version": "0.41.0"},
    )
    proof = signer.sign(unsigned.content_hash)
    signed = build_tool_call_receipt(
        tool="settlement.execute", call_subject=subject, diagnostic_ref=diagnostic_ref,
        envelope=envelope, result_hash=wr.composition_hash,
        anchor_ref={"kind": "composition", "ref": wr.composition_hash},
        signature=proof, timestamp="2026-07-04T12:00:00+00:00", producer={"bulla_version": "0.41.0"},
    )
    return signed.to_dict()


# ── a bystander recomputes and (maybe) slashes — no oracle ───────────────────

def bystander_audit_and_slash(receipt: dict, bond: Bond) -> dict:
    """Anyone can run this. It (1) verifies B's signed commitment, (2) recomputes
    the verdict from the pinned composition, and (3) slashes iff the settlement
    carried undisclosed convention deficits. No arbitrator is consulted."""
    result: dict = {"steps": []}

    v = verify_receipt(receipt)
    result["steps"].append(f"receipt verifies to '{v.verified_to}' (B committed: signature={v.checks.get('signature')})")
    if not v.ok:
        result["verdict"] = "receipt invalid — nothing to enforce"
        return result

    pinned = receipt["anchor_ref"]["ref"]
    comp, wr = settlement_verdict(_COMPOSITION)
    same_composition = (comp.canonical_hash() == pinned)
    same_verdict = (wr.diagnostic_hash == receipt["diagnostic_ref"]["ref"])
    result["steps"].append(
        f"recomputed the pinned composition: composition_hash matches={same_composition}, "
        f"diagnostic matches={same_verdict}, fee={wr.fee}"
    )
    if not (same_composition and same_verdict):
        result["verdict"] = "receipt does not bind the composition it claims — refuse"
        return result

    # the slash condition — objective, recomputable, oracle-free
    if wr.fee > 0:
        reason = (f"settlement delivered with {wr.fee} undisclosed convention deficit(s) "
                  f"(disposition {wr.disposition.name}); breach recomputed by a third party")
        payout = bond.slash(reason)
        result["steps"].append(f"SLASHED: {payout['amount']} {payout['currency']} → {payout['paid_to']}")
        result["verdict"] = "slashed"
        result["cap"] = wr.fee
        result["payout"] = payout
    else:
        result["steps"].append("fee=0 — coherent settlement, bond released")
        result["verdict"] = "released"
    return result


def main() -> int:
    from bulla.identity import LocalEd25519Signer

    print(__doc__.split("\n\n")[0] + "\n")
    print("=" * 72)

    # 1. B posts a bond (it has nothing else to lose)
    bond = Bond(amount=50_000, currency="USD", posted_by="agent-B (stateless)",
                beneficiary=ORG_X, slash_condition="undisclosed convention deficit (fee>0), recomputable")
    print(f"1. Agent B posts a bond: {bond.amount} {bond.currency} held at {bond.held_by}")
    print(f"   slash condition: {bond.slash_condition}")

    # 2. B (ephemeral) executes + signs, then its process is gone
    b = LocalEd25519Signer.generate()
    print(f"\n2. Agent B (did:key {b.issuer_block().get('id', '?')[:24]}…) executes the settlement, signs a receipt, terminates.")
    receipt = agent_b_settles(b)
    print(f"   receipt.action.type = {receipt['action']['type']}   verdict ref = {receipt['diagnostic_ref']['ref'][:23]}…")
    print("   (agent B no longer exists — nothing to jail, no one to answer)")

    # 3. a bystander recomputes and slashes
    print("\n3. A bystander (Org X / an auditor / anyone) audits the receipt:")
    out = bystander_audit_and_slash(receipt, bond)
    for s in out["steps"]:
        print(f"     · {s}")

    print("\n" + "=" * 72)
    print(f"RESULT: bond {out['verdict']}.", end=" ")
    if out["verdict"] == "slashed":
        print(f"Trigger = a recomputable verdict (fee={out['cap']}). Cap = {out['cap']} (the disclosures owed).")
        print("HONEST BOUND: the fee is the objective trigger and the cap — NOT a severity price.")
        print("Pricing the harm is the junior tranche; it needs an adjudicator (a carrier). Not shown here.")
    return 0 if out["verdict"] in ("slashed", "released") else 1


if __name__ == "__main__":
    raise SystemExit(main())
