#!/usr/bin/env python3
"""Two-instance receipt-witness pilot demo.

Runs two team-controlled witness instances over the project's own
contemporaneous release receipts, proves inclusion and checkpoint extension,
then injects a same-size split view and shows the equivocation evidence. The
output states the control-domain boundary exactly: two instances, one control
domain, independent witness count zero.

    PYTHONPATH=src python examples/receipt-witness/run_demo.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from bulla.experimental.checkpoint import verify_checkpoint, verify_checkpoint_extension
from bulla.experimental.receipt_witness import ReceiptWitness
from bulla.identity import LocalEd25519Signer
from bulla.registry import classify_root_trust, verify_inclusion_record

_REPO = Path(__file__).resolve().parents[2]
_OUT = Path(__file__).resolve().parent / "demo-output.json"


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="receipt-witness-demo-"))
    witnesses = {
        name: ReceiptWitness(
            operator_signer=LocalEd25519Signer(seed=name.encode().ljust(32, b"\0")),
            log_id=f"receipt-witness-{name}",
            log_path=workdir / name / "log.jsonl",
            store_path=workdir / name / "store.jsonl",
        )
        for name in ("alpha", "beta")
    }
    output: dict = {"profile": "bulla.receipt-witness/0.1-experimental", "stages": []}

    # 1 — first cargo: the project's own contemporaneous release receipts.
    cargo = ["0.44.0", "0.44.1"]
    intakes = {}
    for version in cargo:
        raw = (_REPO / "releases" / f"{version}.json").read_bytes()
        for name, witness in witnesses.items():
            intakes[(name, version)] = witness.intake(raw)
    alpha, beta = witnesses["alpha"], witnesses["beta"]
    assert alpha.root() == beta.root()
    output["stages"].append({
        "stage": "intake",
        "cargo": cargo,
        "verified_to": intakes[("alpha", "0.44.0")]["verified_to"],
        "received_at_alpha": intakes[("alpha", "0.44.0")]["received_at"],
        "roots_agree": True,
        "root": alpha.root(),
    })

    # 2 — inclusion proof, checked against the locally computed root.
    attestation = intakes[("alpha", "0.44.1")]["attestation_hash"]
    record = alpha.inclusion(attestation)
    assert record is not None and verify_inclusion_record(record, trusted_root=alpha.root())
    output["stages"].append({
        "stage": "inclusion",
        "attestation_hash": attestation,
        "received_at": record["received_at"],
        "proof_ok": True,
    })

    # 3 — signed heads and append-only extension.
    head1 = alpha.checkpoint()
    raw = (_REPO / "spec" / "vectors" / "signed-authorized.json").read_bytes()
    alpha.intake(raw)
    head2 = alpha.checkpoint()
    extension = verify_checkpoint_extension(head1, head2, alpha.consistency(head1.tree_size))
    assert verify_checkpoint(head2).ok and extension.ok
    output["stages"].append({
        "stage": "checkpoint-extension",
        "from_size": head1.tree_size,
        "to_size": head2.tree_size,
        "extension_ok": True,
    })

    # 4 — injected split view: beta appends a DIFFERENT receipt at the same size.
    beta.intake((_REPO / "spec" / "vectors" / "reliance-rely.json").read_bytes())
    assert len(alpha) == len(beta) and alpha.root() != beta.root()
    label, trusted = classify_root_trust(True, beta.root(), alpha.root(), None)
    head_beta = beta.checkpoint()
    forked = verify_checkpoint_extension(head1, head_beta, beta.consistency(head1.tree_size))
    output["stages"].append({
        "stage": "split-view-drill",
        "same_size": len(alpha),
        "roots_differ": True,
        "root_trust_label": label,
        "root_trusted": trusted,
        "forked_extension_ok": forked.ok,
        "evidence": "same-size conflicting signed heads from one control domain",
    })
    assert label == "mismatch" and not trusted and not forked.ok

    # 5 — the boundary, stated exactly.
    output["boundary"] = {
        "instances": 2,
        "control_domains": 1,
        "independent_witnesses": 0,
        "statement": "Operational team-controlled witness pilot. Two instances, one "
                     "control domain. Independent witness count remains zero.",
    }
    _OUT.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))
    print(f"\nwrote {_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
