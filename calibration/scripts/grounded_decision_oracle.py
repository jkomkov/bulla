"""Grounded-decision oracle — Stage 1b/2 labels. EXECUTION_INDEPENDENT, import-isolated.

Produces the pre-registered PRIMARY label per the **2026-06-18 amendment** to
`papers/coherence-cliff/holonomy_pre_registration.md` (§10): **grounded decision
dispersion vs gold**. For a concept-loop, every model on the loop emits its
*committed decision* on the concept; the loop's label is the fraction of member
models whose decision disagrees with the held-out **gold** answer. This is
set-based and gold-anchored — order-independent, with **no loop-closure structure**
— so it shares no construct with the holonomy predictor it will be scored against.

NON-CIRCULARITY (load-bearing). This module imports NOTHING from
`bulla.adapters.holonomy` or `bulla.adapters.restriction_maps`, and never reads an
alignment map, a holonomy, or an embedding distance. The guarantee is enforced
structurally by `bulla/tests/test_grounded_decision_oracle.py`
(`test_oracle_import_graph_is_isolated`, an AST scan of this file). The decision is
read from the model's *greedy completion* via a deterministic verbalizer — a channel
the SAE backend does not expose (it yields only which features fire).

SPLIT. `read_decision` is MODEL-GATED (needs `model.generate` on Colab/GPU). Every
other function — verbalization and the dispersion-vs-gold aggregation — is pure
Python and is what the unit tests exercise locally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

PROVENANCE = "EXECUTION_INDEPENDENT"


@dataclass(frozen=True)
class ProbeConcept:
    """A decision predicate with a gold answer. The prompt must force a choice from
    `choices`; `gold` must be one of `choices`. Concepts are the loop vertices'
    shared meaning under test."""

    concept_id: str
    prompt: str
    gold: str
    choices: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.gold not in self.choices:
            raise ValueError(f"gold {self.gold!r} not in choices {self.choices} for {self.concept_id}")
        if len(self.choices) < 2:
            raise ValueError(f"{self.concept_id}: need >=2 choices to make a decision")


@dataclass(frozen=True)
class Decision:
    """A model's committed decision on a concept, with grounded correctness."""

    model_id: str
    concept_id: str
    answer: str
    correct: bool


def verbalize(completion: str, choices: Sequence[str]) -> str:
    """Map a free-text greedy completion to the committed decision: the choice whose
    surface form appears EARLIEST in the (lowercased) completion. Deterministic; ties
    broken by `choices` order. Returns "" (an abstain, scored incorrect) if no choice
    is mentioned. Pure — the locally tested core of the decision readout.
    """
    text = completion.lower()
    best_choice = ""
    best_pos = len(text) + 1
    for c in choices:
        pos = text.find(c.lower())
        if pos != -1 and pos < best_pos:
            best_pos, best_choice = pos, c
    return best_choice


def read_decision(
    *,
    model,
    tokenizer,
    concept: ProbeConcept,
    max_new_tokens: int = 8,
    device: str = "cpu",
) -> Decision:
    """MODEL-GATED (Colab/GPU). Greedy-decode the model on `concept.prompt`, verbalize
    the completion to a committed decision, and score it against gold. `torch` is
    imported lazily so this module stays import-light and locally importable.
    """
    try:
        import torch  # noqa: F401  (lazy; only needed for the forward pass)
    except ImportError as e:  # pragma: no cover - environment-gated
        raise ImportError(
            "read_decision needs torch + a generate-capable model (Colab/GPU). "
            "Install with: pip install 'bulla[g23-a3]'"
        ) from e
    inputs = tokenizer(concept.prompt, return_tensors="pt").to(device)
    out = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,  # greedy = a single COMMITTED decision, no sampling noise
        num_beams=1,
        pad_token_id=getattr(tokenizer, "eos_token_id", None),
    )
    completion = tokenizer.decode(
        out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
    )
    answer = verbalize(completion, concept.choices)
    return Decision(
        model_id=getattr(model, "name_or_path", "model"),
        concept_id=concept.concept_id,
        answer=answer,
        correct=(answer == concept.gold),
    )


def read_decision_loglik(*, model, tokenizer, concept: ProbeConcept, device: str = "cpu") -> Decision:
    """MODEL-GATED, **base-model-appropriate** readout (the registered Llama/Mistral/Gemma
    SAEs all target BASE models, which do not reliably follow "answer with one word").

    Scores each choice as a continuation of the prompt by its **length-normalized token
    log-probability** and commits to the argmax — the standard lm-eval-harness MCQ method,
    far more robust on base models than greedy-generate-then-verbalize. Tokens for context
    and continuation are concatenated at the id level (never re-tokenizing the joined
    string) so a tokenizer boundary-merge cannot misalign the scored span.
    """
    try:
        import torch
    except ImportError as e:  # pragma: no cover - environment-gated
        raise ImportError(
            "read_decision_loglik needs torch + a logits-returning model (Colab/GPU). "
            "Install with: pip install 'bulla[g23-a3]'"
        ) from e
    dev = next(model.parameters()).device            # follow the model's device, not the arg
    ctx_ids = tokenizer(concept.prompt, return_tensors="pt")["input_ids"][0]
    best_choice, best_score = concept.choices[0], -float("inf")
    for choice in concept.choices:
        cont_ids = tokenizer(" " + choice, return_tensors="pt", add_special_tokens=False)["input_ids"][0]
        if len(cont_ids) == 0:
            continue
        ids = torch.cat([ctx_ids, cont_ids]).unsqueeze(0).to(dev)
        with torch.no_grad():
            logits = model(ids).logits[0].float()    # (T, V); float32 for stable log-softmax (model may be bf16)
        logprobs = torch.log_softmax(logits[:-1], dim=-1)   # predict token t+1 from t
        targets = ids[0, 1:]                                # tokens 1..T-1
        n = len(cont_ids)
        cont_lp = logprobs[-n:].gather(1, targets[-n:].unsqueeze(1)).squeeze(1)
        score = float(cont_lp.mean())                       # length-normalized
        if score > best_score:
            best_score, best_choice = score, choice
    return Decision(
        model_id=getattr(model, "name_or_path", "model"),
        concept_id=concept.concept_id,
        answer=best_choice,
        correct=(best_choice == concept.gold),
    )


def loop_dispersion_label(
    loop_model_ids: Sequence[str],
    concept: ProbeConcept,
    decisions: Mapping[tuple[str, str], Decision],
) -> float:
    """The amendment's PRIMARY label, pure & local: the fraction of the loop's models
    whose committed decision on `concept` disagrees with gold (= 1 − accuracy over the
    loop's vertices). Order-independent, set-based, gold-anchored — no loop-closure.

    `decisions` is keyed by (model_id, concept_id). A model with no recorded decision
    counts as an abstain (incorrect), so a missing read never silently lowers
    dispersion.
    """
    if len(loop_model_ids) == 0:
        raise ValueError("empty loop")
    wrong = 0
    for m in loop_model_ids:
        d = decisions.get((m, concept.concept_id))
        if d is None or not d.correct:
            wrong += 1
    return wrong / len(loop_model_ids)


def loop_label_binary(dispersion: float, threshold: float) -> int:
    """Binarize the dispersion label for the AUC primary. `threshold` is the
    base-rate-calibrated cut from pre-reg §6 (the run is void unless the resulting
    base rate ∈ [0.2, 0.8])."""
    return 1 if dispersion > threshold else 0


def base_rate(labels: Sequence[int]) -> float:
    return sum(labels) / len(labels) if labels else 0.0


# A minimal worked probe set so the structure is concrete and the verbalizer is
# exercised on real predicates. The Stage-1b/2 run loads a larger curated set
# (see the Colab runbook); these are not the experiment's corpus.
EXAMPLE_CONCEPTS: tuple[ProbeConcept, ...] = (
    ProbeConcept(
        "water_boiling",
        "For water at sea level, is 100 degrees Celsius closer to freezing or boiling? "
        "Answer with one word: freezing or boiling.",
        gold="boiling",
        choices=("freezing", "boiling"),
    ),
    ProbeConcept(
        "currency_minor_unit",
        "An amount is stored as the integer 250 in minor units (cents) of US dollars. "
        "Is that closer to 2 dollars or 250 dollars? Answer: 2 or 250.",
        gold="2",
        choices=("2", "250"),
    ),
    ProbeConcept(
        "utc_vs_local",
        "A timestamp says 00:30 UTC. In New York (UTC-5), is the local time in the "
        "evening or the morning? Answer: evening or morning.",
        gold="evening",
        choices=("evening", "morning"),
    ),
)


if __name__ == "__main__":
    # Smoke (no model): show the verbalizer + label aggregation on fabricated decisions.
    concept = EXAMPLE_CONCEPTS[0]
    fake = {
        ("A", concept.concept_id): Decision("A", concept.concept_id, "boiling", True),
        ("B", concept.concept_id): Decision("B", concept.concept_id, "freezing", False),
        ("C", concept.concept_id): Decision("C", concept.concept_id, "boiling", True),
    }
    disp = loop_dispersion_label(["A", "B", "C"], concept, fake)
    print(f"verbalize('I think boiling.', choices) = {verbalize('I think boiling.', concept.choices)!r}")
    print(f"loop dispersion vs gold (1/3 wrong)     = {disp:.4f}")
