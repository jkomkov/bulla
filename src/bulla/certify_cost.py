"""`bulla certify-cost` — the Coherence Cost Certificate, v0 skeleton.

Separates, for one composition, the *irreducible cost of coherence* from any
*unexplained intermediary premium*:

  - **coherence floor** — the fee: the number of independent convention
    declarations without which the composition cannot be made coherent
    (exact regime; recomputable by any party from the composition alone);
  - **witness fields** — the minimum disclosure set: WHICH declarations,
    named as (tool, field) pairs (the same set the repair loop cures on);
  - **unexplained premium** — when `--observed-cost` is supplied: whatever
    the intermediary charges above the cost of satisfying that transcript
    is *not required by coherence itself*. The certificate prices the floor;
    it deliberately does not name what the excess is — that is economic
    analysis, outside the certificate.

v0 SKELETON: the instrument half only. The floor's theorem statement (minimum
sound certification transcript = fee, exact regime, with the retirement memo's
master formula as its proof) lands with the merged measurement paper; until
then the certificate cites the fee's existing anchors and stays at instrument
altitude.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def build_certificate(comp, observed_cost: float | None = None) -> dict:
    from bulla.diagnostic import diagnose, minimum_disclosure_set

    diag = diagnose(comp)
    fields = [{"tool": t, "field": f} for (t, f) in minimum_disclosure_set(comp)]
    body: dict = {
        "certificate_type": "coherence-cost/v0",
        "composition_hash": comp.canonical_hash(),
        "coherence_floor": diag.coherence_fee,
        "witness_fields": fields,
        "semantics": (
            "exact regime; the floor is the number of independent convention "
            "declarations required for coherence, recomputable from the "
            "composition alone"
        ),
    }
    if observed_cost is not None:
        body["observed_cost"] = observed_cost
        body["note"] = (
            "the portion of observed_cost above the cost of supplying the "
            f"{diag.coherence_fee} witness field(s) is not required by "
            "coherence itself"
        )
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    body["certificate_hash"] = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    return body


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="bulla certify-cost",
        description="Emit a Coherence Cost Certificate (v0 skeleton).",
    )
    ap.add_argument("composition", type=Path, help="composition JSON file")
    ap.add_argument("--observed-cost", type=float, default=None,
                    help="the intermediary's observed charge, in your unit")
    args = ap.parse_args(argv)

    from bulla.parser import load_composition

    comp = load_composition(args.composition)
    print(json.dumps(build_certificate(comp, args.observed_cost), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
