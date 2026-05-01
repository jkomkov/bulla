# Local Interface Descriptions Do Not Compose

## One-Sentence Claim

Compositional hiddenness is a non-local predicate that current frontier LLMs cannot stably access; instead, they rely on prompt-conditioned lexical heuristics whose activation requires both relational framing and convention-specific task language, with the lexical intervention effect (direction→path = +58%, p = 0.008) disappearing entirely under flat prompt formats.

## The Thesis

There is a latent interface ecology that lives between tool schemas. The important facts for safe composition — which fields have hidden conventions, which conventions silently couple calls — are not inside any single schema. They are relational properties of the composition graph. Current models do not infer this ecology; they substitute lexical proxies where structural inference is required. Bulla computes the missing global object algebraically.

## Theorem Targets (see THEOREM-TARGETS.md)

1. **Non-locality of hiddenness.** Whether a field is a blind spot depends on the composition partner, not the field alone.
2. **Local equivalence, global divergence.** Same tool schema, different partner → different fee, different blind spots.
3. **Diagnostic sufficiency.** Once the hidden set is externally identified, specification is algebraically trivial.

## Experimental Tableau

| Experiment | Result | What it shows |
|---|---|---|
| Vocabulary probe (N=103) | OR = 34.5, p < 2.4×10⁻⁶ | Identification clusters on lexically canonical names |
| Lexical intervention (N=12) | +58pp, p = 0.008 | Name change causes identification change (causal) |
| Cross-model (GPT-4o vs Claude) | 0% vs 88% on "direction" | Identification is model-specific, not structure-specific |
| Reverse intervention (N=10) | -30% (location), 0% (target) | Directional but underpowered |
| **Context ablation (N=12)** | **See below** | **Relational framing + task language gate the effect** |
| **Synthetic ecology (N=8×3)** | **(pending)** | **Can models detect non-local hiddenness?** |

## Context Ablation (NEW — decisive for mechanism)

What in the structured prompt activates the vocabulary phenomenon?

| Condition | `path` ID rate | Δ |
|---|---|---|
| **Full** (two named servers, "hidden conventions") | **100%** | — |
| **Anonymous** (server_A/B, tool_1/2, no descriptions) | **92%** | -8% |
| **No grouping** (flat list, named, "hidden conventions") | **67%** | -33% |
| **Neutral task** (structured, named, "integration issues") | **67%** | -33% |

**Finding:** Two factors gate the effect, each contributing ~33pp:
1. **Relational framing** (two-server grouping vs flat list)
2. **Task language** ("hidden conventions" vs "integration issues")

Names and descriptions barely matter (anonymous → 92%). The model finds `path` from schema shape alone, *when the relational frame is active*.

**Interpretation:** The model does not have a stable internal representation of hiddenness. It has a prompt-conditionable "convention audit mode" that, once activated by relational framing + task language, relies on lexical priors to select which fields to flag.

## The Killer Numbers

```
Field          Hidden instances    Identified    Rate
─────────────────────────────────────────────────────
path           41                  35            85%
paths          36                  10            28%
filePath       1                   1             100%
direction      5                   0             0%
state          5                   0             0%
after          5                   0             0%
sort.timestamp 5                   0             0%
page           5                   1             4%  ← noise
```

Fisher's exact: χ² = 20.9, p < 2.4×10⁻⁶, OR = 34.5

## Lines of Evidence

### 1. Vocabulary Phenomenon (descriptive, Claude Sonnet 4, N=103)

Path-family fields: 59% identification. Non-path fields: 4%. OR = 34.5, p < 2.4×10⁻⁶.

Within path-family: `path` (85%) >> `paths` (28%). Same concept, morphological variant. Surface-form gradient, not reasoning gradient.

### 2. Lexical Intervention (causal, Claude Sonnet 4, N=12)

Same field, same structural role, same composition graph. Only the name changes.

| Condition | Obscure field ID rate | |
|---|---|---|
| Baseline ("direction") | 0/12 = 0% | |
| Renamed to "path" | 7/12 = 58% | +58% |

McNemar exact binomial: p = 0.0078, 7/7 discordant pairs in predicted direction.

**Prompt sensitivity:** This effect appears under the structured two-server prompt. Under a flat prompt, the effect disappears (0% for both). The effect is task-frame contingent.

### 3. Cross-Model Divergence (GPT-4o vs Claude, N=8+12)

| Field | Claude Sonnet 4 | GPT-4o |
|---|---|---|
| `path` | 42% | 25% |
| `direction` | **0%** | **88%** |

Model-specific identification on identical compositions. Function of training distribution, not algebraic status.

### 4. Context Ablation (mechanism, Claude Sonnet 4, N=12)

Progressively strips the structured prompt to isolate the trigger:
- **Anonymous schemas** (no names, no descriptions): 92% → names/descriptions carry ~8% of the signal
- **Flat list** (no server grouping): 67% → relational framing carries ~33%
- **Neutral task** ("integration issues"): 67% → "hidden conventions" language carries ~33%

The vocabulary phenomenon is gated by relational framing × task language, not by contextual cues from server/tool names.

### 5. Reverse Intervention (directional, Claude Sonnet 4, N=10)

| Condition | ID Rate | |
|---|---|---|
| baseline ("path") | 80% | |
| renamed "location" | 50% | -30% |
| renamed "target" | 80% | 0% (also canonical) |

Directional but underpowered (p = 0.19). "Target" doesn't drop because it is itself convention-canonical. This is consistent with a gradient broader than "path" alone.

## The Paper's Logical Structure

```
Theorem 1: Hiddenness is non-local (depends on composition graph).
  ↓
Theorem 2: Local equivalence, global divergence (constructive proof).
  ↓
Experiment: Models use prompt-conditioned lexical proxies.
  Evidence: vocabulary phenomenon + intervention + cross-model
  ↓
Mechanism: Relational framing + task language → convention audit mode
  → lexical priors steer identification.
  Evidence: context ablation (names don't matter, framing does)
  ↓
The proxy behavior is mode-dependent, not a stable competence.
  ↓
Theorem 3: Once the non-local object is externally supplied,
           specification collapses to trivial.
  ↓
Construction: Bulla computes the non-local object algebraically.
```

## Honest Framing

**What we show**: Hiddenness is a non-local predicate that current frontier LLMs access through prompt-conditioned heuristics, not through stable structural inference. Structured prompts with relational framing and convention-specific task language activate a "convention audit mode" in which lexical priors dominate identification. Different models produce different identification patterns on identical compositions (model-specific, not structure-specific). Once hidden fields are externally supplied, specification is algebraically trivial.

**What we do NOT show**: That no future model or prompting strategy can derive hiddenness from schema structure. Our result characterizes current frontier models under direct prompting. The non-locality theorem constrains any approach that works from bilateral schema inspection alone, but does not rule out approaches with access to runtime behavior, extended context, or explicit composition-graph reasoning.

**What is a hypothesis, not a result**: The "two-channel model" (lexical prior channel × contextual redundancy channel) is an interpretive framework consistent with the data, not an experimentally isolated mechanism.

## Reproduction

```bash
cd bulla

# Vocabulary phenomenon (60 compositions)
python -m calibration.harness.run_familiarity_probe --full --api-key $KEY

# Lexical intervention (12 minimal pairs × 3 conditions)
python -m calibration.harness.run_lexical_intervention --api-key $KEY --max-cases 12

# Cross-model replication
python -m calibration.harness.run_lexical_intervention --api-key $KEY --model openai/gpt-4o --max-cases 8

# Context ablation (4 prompt conditions × 12 compositions)
python -m calibration.harness.run_context_ablation --api-key $KEY --max-cases 12

# Synthetic ecology (8 compositions × 3 conditions × 3 repeats)
python -m calibration.harness.run_synthetic_ecology --api-key $KEY --repeats 3
```

Data directory: `calibration/data/agent_confusion/`
Theorem targets: `calibration/THEOREM-TARGETS.md`
