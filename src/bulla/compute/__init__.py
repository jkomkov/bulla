"""bulla.compute — heavy compute / I/O helpers gated behind [g23-a3] extras.

Modules under this package handle heavy operations (HF Hub downloads,
sentence-transformers embeddings, Modal job dispatch). Each module
lazy-imports the heavy dependencies inside function bodies so the
package itself imports without [g23-a3] extras installed; calling a
function that requires the heavy deps raises
``bulla.adapters.sae_lens_backend.SAEBackendImportError`` with a clear
install hint.

Modules:
  * ``cocycle_pairs``: controlled pair generation + rank-delta primitive
    shared by EvalGap (G25) and Semantic SemVer (G26).
  * ``eval_gap``: evaluator leaderboard against witness-capacity floor.
  * ``semver``: semantic update classification via witness-rank deltas.
  * ``g23_a3_pairing``: §3b′ Mirage-disciplined Neuronpedia feature
    pairing pipeline. Produces the locked artifacts referenced in
    ``papers/composition-doctrine/sprint_g23_a3_pre_registration.md``
    §4.
  * ``g23_a3_calibration``: §3a calibration spot-check on probe pairs
    P1, P2. Produces ``g23_a3_calibration_spotcheck.jsonl``.
"""
