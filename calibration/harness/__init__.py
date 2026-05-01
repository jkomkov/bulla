"""Compositional hiddenness identification harness.

Experiments testing how frontier LLMs access the non-local hiddenness
predicate in tool compositions.  Core finding: model access to the
predicate is representation-conditioned — it requires relational framing
and convention-specific task language, and otherwise reverts to lexical
heuristics.

Experiments (flagship):
    synthetic_ecology        — Synthetic benchmark: local equivalence,
                               global divergence (Theorems 1–2)
    run_synthetic_ecology    — Runner (8 compositions × 3 conditions × N repeats)
    run_context_ablation     — Prompt-mode decomposition (4 conditions)

Experiments (supporting):
    familiarity              — GitHub-stars proxy, field-level scoring
    lexical_intervention     — Causal rename experiment (forward intervention)
    run_familiarity_probe    — Runner for vocabulary phenomenon (60+ compositions)
    run_lexical_intervention — Runner for forward/swap/mask intervention
    run_reverse_intervention — Runner for canonical→neutral rename

Legacy (specification-gap foundation):
    specification_gap        — Original 3-condition specification-gap experiment
    run_specification_gap    — Runner for specification-gap
    experiment, judge, analysis, arms, tasks — Tool-selection experiment scoring
"""
