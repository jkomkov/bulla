#!/usr/bin/env bash
# bulla gate — the 5-minute quickstart.
#
# The recourse gate decides PROCEED / REFUSE on a counterparty's coherence deed, and emits
# a CURABLE refusal when it refuses. It gates on TYPE signals only — authenticity +
# inclusion under a root you trust INDEPENDENTLY of the host; the coherence fee is
# REPORTED by default and gated on only when you opt in (--require-fee N — a disclosure
# demand, not an execution predictor; see FALSIFICATIONS.md). It does NOT verify
# delivery (a coherent liar passes; performance bonding is roadmap).
#
#   prereq:  pip install -e .            # from the bulla/ directory
#   run:     examples/gate-quickstart.sh [your-composition.yaml]   # the arg is optional: BYO
#
# It signs two real shipped compositions, logs them, and gates them:
#   - auth_pipeline.yaml        (coherent, fee=0)     -> PROCEED  (exit 0)
#   - mcp_filesystem_git.yaml   (a real seam, fee>0)  -> REFUSE   (exit 1) + a cure,
#     under the explicit disclosure demand --require-fee 0
set -euo pipefail

BULLA=${BULLA:-bulla}
command -v "$BULLA" >/dev/null 2>&1 || BULLA="python3 -m bulla"
HERE="$(cd "$(dirname "$0")" && pwd)"
COMP="$HERE/../compositions"
D="$(mktemp -d)"; trap 'rm -rf "$D"' EXIT

echo "▸ 1/5  generate an identity (a did:key your agent signs under; it is never minted for you)"
$BULLA key gen -o "$D/key.json" >/dev/null

echo "▸ 2/5  sign two recomputable certificates (the deeds)"
$BULLA certify --sign "$COMP/auth_pipeline.yaml"      --key "$D/key.json" --output "$D/coherent.json" --format json >/dev/null
$BULLA certify --sign "$COMP/mcp_filesystem_git.yaml" --key "$D/key.json" --output "$D/seam.json"     --format json >/dev/null

echo "▸ 3/5  log them in a registry the relying party trusts (here, its own local log)"
$BULLA registry append "$D/coherent.json" --log "$D/log.jsonl" >/dev/null
$BULLA registry append "$D/seam.json"     --log "$D/log.jsonl" >/dev/null

echo "▸ 4/5  GATE a coherent deed  ->  expect PROCEED"
set +e
$BULLA gate --certificate "$D/coherent.json" --registry "$D/log.jsonl"; proceed_rc=$?
echo
echo "▸ 5/5  GATE an incoherent deed (a real filesystem↔git convention seam)  ->  expect REFUSE + a cure"
# --require-fee 0 is the explicit opt-in: demand disclosure (fee=0) before proceeding.
$BULLA gate --certificate "$D/seam.json" --registry "$D/log.jsonl" --require-fee 0 --disclose path_separator; refuse_rc=$?
set -e

if [ "$proceed_rc" -ne 0 ] || [ "$refuse_rc" -ne 1 ]; then
  echo "✗ unexpected exit codes — proceed_rc=$proceed_rc (want 0), refuse_rc=$refuse_rc (want 1)" >&2
  exit 1
fi

# ── BYO — the neutrality step: the gate is yours, not a replay of ours ───────────────
# Pass YOUR composition as the first arg and it runs through the same gate. This is the
# whole point: a relying party points the gate at its own composition + registry; nothing
# above is hardcoded to us.
BYO="${1:-}"
if [ -n "$BYO" ] && [ -f "$BYO" ]; then
  echo
  echo "▸ BYO  gating YOUR composition ($BYO) through the same gate"
  $BULLA certify --sign "$BYO" --key "$D/key.json" --output "$D/byo.json" --format json >/dev/null
  $BULLA registry append "$D/byo.json" --log "$D/log.jsonl" >/dev/null
  set +e; $BULLA gate --certificate "$D/byo.json" --registry "$D/log.jsonl"; set -e
fi

echo
echo "✓ quickstart passed — PROCEED on fee=0 (exit 0), REFUSE-with-cure on fee>0 (exit 1)."
echo
echo "  ▶ Now do it with YOURS — point the gate at your own composition + registry:"
echo "      bulla gate --certificate <your-cert>.json --registry <your-log-or-URL>"
if [ -z "$BYO" ]; then
  echo "      (or re-run with a composition:  $0 path/to/your-composition.yaml)"
fi
echo
echo "  ▶ See the gate catch a LYING host (an equivocated registry root) and prevent a real"
echo "      git breach:  python calibration/recourse_gate_closes_loop_git.py"
