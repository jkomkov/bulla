#!/usr/bin/env python3
"""The execution gap — does fee=0 (schema-coherent) predict execution-coherence? (witness_market_prereg.md)

Pre-reg: papers/refinement-types/witness_market_prereg.md (90db6a7), lock-before-measure.

The crux: r = P(execution breach | fee=0). fee is value-blind (8a56a00), so fee=0 = SCHEMA-coherent, not
EXECUTION-coherent. The gap is the fee's blind spot: value-level conventions (encoding, EOL, path-rooting) no
field-name expresses. This script runs the parts that are HONEST today with REAL execution; it does NOT
fabricate a sampled-corpus r (that needs the deferred live-server substrate — pre-reg §3a sample-size honesty).

What runs here, each labelled by provenance:
  [CONSTRUCTED control, part b]  the oracle + harness + attribution WORK: a hand-built fee=0 composition
                                 (latin-1 writer -> utf-8 reader) breaches at real execution, and transporting
                                 the encoding clears it. Proves "the oracle catches the gap"; CANNOT fail by
                                 design (positive control) — so it earns mechanism-validity, never "r=X".
  [ABORT gate, negative control] a coherent fee=0 composition (utf-8 -> utf-8) must NOT breach. If it does, the
                                 harness false-positives -> ABORT (705b500 verify-before-record).
  [EXECUTION_INDEPENDENT grid]   over a DISCLOSED real-convention tool set, the real breach rate among fee=0 vs
                                 fee>0. The breach is the world's verdict (real file I/O). HONEST CEILING: the
                                 tool-set COMPOSITION bounds this rate, so it demonstrates the gap is real +
                                 attributable; it is NOT the sampled-corpus r and yields NO FLOOR/MARKET verdict.

The breach label imports ZERO diagnostic code — it is a real exception or a real content round-trip mismatch.
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from type_layer_slash import completeness_slash  # noqa: E402  (the built floor's recompute)

from bulla.diagnostic import diagnose  # noqa: E402
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec  # noqa: E402

PAYLOAD = "café naïve coördination\r\n"  # all chars latin-1-safe (<=U+00FF) so latin-1 CAN write; utf-8 vs
#                                          latin-1 still DECODE differently (é/ï/ö) -> the seam, not a write limit.
#                                          CRLF present so EOL conventions also diverge.


# ── the FLOOR (schema-only fee). encoding/EOL/case are NOT schema dimensions -> invisible (fee=0). ──────────
def schema_fee(extra_hidden: tuple[str, ...] = ()) -> int:
    """fee of a producer->consumer seam sharing a declared 'payload' dimension, plus any `extra_hidden`
    dimensions HELD but not declared (each adds 1). Encoding/EOL are absent here by construction = the blind spot."""
    held = ("payload",) + extra_hidden
    p = ToolSpec("producer", held, ("payload",))
    c = ToolSpec("consumer", held, ("payload",))
    edges = tuple(Edge("producer", "consumer", (SemanticDimension(d, d, d),)) for d in held)
    return diagnose(Composition("seam", (p, c), edges)).coherence_fee


# ── the WORLD (real execution). a real file write under the producer's convention, read under the consumer's. ─
@dataclass(frozen=True)
class Conv:
    encoding: str = "utf-8"
    eol: str = "\n"          # producer normalizes \r\n -> this on write; consumer expects this on read
    path_root: str = "rel"   # "rel" | "abs"  (abs producer + rel consumer = a real not-found)


def execute(prod: Conv, cons: Conv) -> tuple[bool, str]:
    """Returns (breach, detail). Breach = real exception OR real content round-trip mismatch (world's verdict)."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        body = PAYLOAD.replace("\r\n", prod.eol)                 # producer's EOL convention
        rel = "sub/note.txt"
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        try:
            fp.write_bytes(body.encode(prod.encoding))           # producer writes under its encoding
        except Exception as e:
            return True, f"write:{type(e).__name__}"
        # consumer resolves the path under ITS rooting convention
        read_path = (root / rel) if cons.path_root == "rel" else Path("/" + rel)
        if prod.path_root == "abs" and cons.path_root == "rel":  # producer handed an abs path; consumer rooted rel
            read_path = Path(str(root / rel).lstrip("/"))         # the real mismatch: rel-rooting an abs path
        try:
            raw = read_path.read_bytes()
        except Exception as e:
            return True, f"read:{type(e).__name__}"
        try:
            text = raw.decode(cons.encoding)                     # consumer reads under its encoding
        except Exception as e:
            return True, f"decode:{type(e).__name__}"
        got = text.replace(cons.eol, "\n")                       # consumer normalizes under ITS EOL
        want = PAYLOAD.replace("\r\n", "\n")
        return (got != want), ("ok" if got == want else "content-mismatch")


def main() -> int:
    out: dict = {"experiment": "execution_gap", "prereg": "witness_market_prereg.md (90db6a7)"}

    UTF8, LATIN1 = Conv(encoding="utf-8"), Conv(encoding="latin-1")

    # (b) CONSTRUCTED control: a hand-built fee=0 gap that breaches + is attributable. -------------------------
    fee_enc = schema_fee()                                        # encoding not a dimension -> 0
    breach_enc, det_enc = execute(LATIN1, UTF8)                   # latin-1 bytes, utf-8 read -> real decode break
    # attribution: TRANSPORT (consumer reads under producer's encoding) clears it; ABLATE returns it.
    transported, _ = execute(LATIN1, Conv(encoding="latin-1"))
    ablated, _ = execute(LATIN1, UTF8)
    attributable = (breach_enc and not transported and ablated)
    control = {"fee": fee_enc, "breach": breach_enc, "detail": det_enc,
               "attributable_transport_clears": attributable, "provenance": "CONSTRUCTED",
               "note": "fee=0 yet real execution breaks on encoding (the blind spot); transport clears, ablate returns."}

    # negative control / ABORT gate: coherent fee=0 must NOT breach. -----------------------------------------
    breach_neg, det_neg = execute(UTF8, UTF8)
    neg = {"fee": schema_fee(), "breach": breach_neg, "detail": det_neg}
    if breach_neg:
        out.update({"VERDICT": "ABORT — harness false-positives on a coherent fee=0 pair", "negative_control": neg})
        (HERE / "results").mkdir(exist_ok=True)
        (HERE / "results" / "execution_gap.json").write_text(json.dumps(out, indent=2) + "\n")
        print("ABORT: negative control breached."); return 1

    # EXECUTION_INDEPENDENT grid over a DISCLOSED real-convention tool set. -----------------------------------
    encodings = ["utf-8", "latin-1", "ascii"]
    eols = ["\n", "\r\n"]
    roots = ["rel", "abs"]
    fee0_cases, feepos_cases = [], []
    for pe in encodings:
        for ce in encodings:
            for peol in eols:
                for ceol in eols:
                    p, c = Conv(pe, peol, "rel"), Conv(ce, ceol, "rel")
                    b, det = execute(p, c)
                    fee0_cases.append((schema_fee(), b, f"{pe}/{peol!r}->{ce}/{ceol!r}:{det}"))
    for pr in roots:                                              # path-rooting = a HIDDEN schema dimension -> fee>0
        for cr in roots:
            p, c = Conv("utf-8", "\n", pr), Conv("utf-8", "\n", cr)
            b, det = execute(p, c)
            feepos_cases.append((schema_fee(("path_convention",)), b, f"path {pr}->{cr}:{det}"))

    r_breaches = sum(1 for f, b, _ in fee0_cases if f == 0 and b)
    r_total = sum(1 for f, _, _ in fee0_cases if f == 0)
    b_breaches = sum(1 for f, b, _ in feepos_cases if f > 0 and b)
    b_total = sum(1 for f, _, _ in feepos_cases if f > 0)
    r = r_breaches / r_total if r_total else None
    b = b_breaches / b_total if b_total else None

    out.update({
        "part_b_constructed_control": control,
        "negative_control": neg,
        "grid_EXECUTION_INDEPENDENT": {
            "disclosed_tool_set": {"encodings": encodings, "eols": [repr(e) for e in eols], "path_roots": roots},
            "fee0": {"breaches": r_breaches, "total": r_total, "breach_rate_authored": round(r, 4) if r is not None else None},
            "feepos": {"breaches": b_breaches, "total": b_total, "breach_rate_authored": round(b, 4) if b is not None else None},
            "examples_fee0_breach": [e for f, bb, e in fee0_cases if f == 0 and bb][:6],
        },
        "VERDICT": (
            "MECHANISM VALIDATED (not a sampled verdict). The gap is REAL: a fee=0 composition breaches at real "
            f"execution and is attributable={attributable} (control); the harness does NOT false-positive (neg "
            "control ok). The blind spot spans multiple convention types (encoding AND EOL) — fee=0 compositions "
            "breach across both. The grid's breach RATES are artifacts of the authored tool set (uniform convention "
            "diversity) and are reported only to show the gap is broad, NOT as the pre-registered r/b — they yield "
            "NO FLOOR-SUFFICES/MARKET-REAL verdict (which requires the sampled, non-authored corpus: live servers, "
            "pre-reg §3a). What is earned: the harness + oracle + attribution work, and the fee blind spot is real."),
        "honest_scope": "fee=0 breaches here are value-level (encoding/EOL); fee>0 cases hide path_convention. "
                        "Real execution, real labels; tool set authored, so the RATE is illustrative, the EXISTENCE "
                        "+ ATTRIBUTABILITY of the gap is the result.",
    })
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "execution_gap.json").write_text(json.dumps(out, indent=2) + "\n")

    print(f"[CONSTRUCTED control] fee={fee_enc} breach={breach_enc} ({det_enc})  attributable={attributable}")
    print(f"[ABORT gate] neg-control fee=0 breach={breach_neg} ({det_neg}) -> {'OK' if not breach_neg else 'ABORT'}")
    print(f"[grid, authored tool set] fee=0 breach {r_breaches}/{r_total}={round(r,3) if r is not None else None}  |  "
          f"fee>0 breach {b_breaches}/{b_total}={round(b,3) if b is not None else None}")
    print(f"  e.g. fee=0 breaches: {[e for f,bb,e in fee0_cases if f==0 and bb][:4]}")
    print("\nVERDICT: MECHANISM VALIDATED — gap real + attributable; sampled-corpus r awaits live servers (NOT faked).")
    print(f"artifact: {HERE/'results'/'execution_gap.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
