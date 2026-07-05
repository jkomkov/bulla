# Bulla Uptake Panel — Open Replication Invitation

**Date posted**: 2026-05-17
**Budget needed**: ~$3 OpenRouter (or equivalent)
**Time**: 30 minutes including setup

We're inviting independent parties to replicate the Round 4 uptake
panel. The panel falsified the producer-annotation thesis under
pre-registered criteria (see `UPTAKE-RESULTS.md` and
`UPTAKE-PROTOCOL.md`). Until at least one external lab or
independent developer reproduces the result, the kill verdict is
single-lab pre-registration — interesting but not citable.

We commit to publishing your replication result alongside ours in
the methodology note **whether it confirms or contradicts our
verdict**, with attribution and a link to your run.

## What you need

- An OpenRouter API key (or equivalent OpenAI-compatible endpoint
  that routes to anthropic/claude-sonnet-4.5 and openai/gpt-4o).
  ~$3 covers the 24-trial 2×2 plus a couple of dry runs.
- A Python 3.11+ environment.
- ~20 minutes of wallclock for the 24 trials.

## How to reproduce

```bash
# 1. Clone and install
git clone https://github.com/jkomkov/res-agentica.git
cd res-agentica
pip install -e bulla/

# 2. Read the pre-registration
$EDITOR bulla/agents/UPTAKE-PROTOCOL.md
# §"Round 4" defines the 2×2, the new metrics, and the
# ship/kill criteria committed BEFORE the original trial run.

# 3. Run the panel against your key
export OPENROUTER_API_KEY=your-key-here
python bulla/agents/uptake_test.py \
    --trials 3 \
    --models anthropic/claude-sonnet-4.5 openai/gpt-4o \
    --output uptake_results_replication.jsonl

# 4. Apply the pre-registered criteria
python bulla/agents/analyze_round4.py
# (Edit the script's RESULTS path to point at your jsonl, or
#  copy your file to uptake_results_round4.jsonl first.)

# 5. Compare against the canonical result
diff <(python bulla/agents/analyze_round4.py) \
     bulla/agents/UPTAKE-RESULTS.md  # not literal diff; structural
```

## Expected canonical result

Per `UPTAKE-RESULTS.md` Round 4 §"Decision":

> DO NOT ship producer-annotation — pivot KILLED.
> Claude annotation_only (0%) did not beat control (0%) on
> refrain_from_blind_cross_server_call by ≥20 points (Δ=0).
> Furthermore, combined (100) ≈ prompt_only (100); annotation adds
> NO marginal effect when prompt is on.
> POSITIVE FINDING: v1.1 system prompt is necessary AND sufficient
> on this task. Deploy prompt-only.

## What would change the verdict

Per the pre-registered decision tree, your replication could:

- **Confirm**: same kill verdict → strengthens the canonical result.
- **Partially confirm with model variance**: e.g., Claude annotation
  shows marginal lift at n=10 that wasn't visible at n=3 → tightens
  the model-asymmetry hedge.
- **Falsify**: annotation cell consistently produces refrain ≥80% on
  Claude → invalidates the kill and we learn the panel was sensitive
  to model version, session context, or some hidden environment
  variable.

Any of those outcomes is publishable. The honest one is whatever
the data shows.

## Known caveats (disclosed in `UPTAKE-RESULTS.md` §"Verification
and caveats")

These bound the kill verdict to the format we tested:

1. **The advisory's "Recommended next action" had a tied-cluster bug**:
   under 12 downstream pairs all tied at 1 obstruction, `max()`
   resolved to `filesystem__read_media_file` instead of the agent's
   actual target `github__create_or_update_file`. A smarter ranking
   might change Claude's response. Doesn't invalidate the kill
   (Claude `read_advisory=0%` — it didn't engage with any part of
   the advisory) but bounds the verdict.
2. **The format collapse may matter**: we tested inline-prepended
   text advisory mixed into the file content. We did NOT test a
   structured `_bulla_advisory` JSON envelope key in a separate
   response field, advisory-as-notification, or advisory-between-turns.
   Replications exploring those formats would be a different
   experiment, not a contradiction of ours.
3. **n=3 per cell is preliminary**: the model-asymmetry observation
   (Claude annotation-blind / GPT reads-but-doesn't-refrain) needs
   replication at n≥10 before it becomes a settled finding.

If your replication explores any of (1), (2), or (3) above, we'd
especially welcome it. Mark the variation clearly in your report.

## How to report

Open an issue on the repo titled
`replication: <your-affiliation>, <date>`
with:
- Your `uptake_results_replication.jsonl` attached or linked.
- The output of `analyze_round4.py` against your data.
- Any model-version metadata you can capture (OpenRouter's
  `model_dump_json()` on the response object is useful).
- Optional: any variations you applied to the protocol, and why.

We will:
- Acknowledge within 48 hours.
- Re-run the analyzer on your data ourselves to confirm
  reproducibility of the scoring.
- Cite your replication in the methodology note's results section
  whether it confirms or contradicts our verdict, with attribution.

## Why we're inviting this

The methodology note's load-bearing claim is *"pre-registered
falsification killed two design directions before they absorbed
engineering effort."* By empirical-paper standards, that claim is
exploratory until someone other than us reproduces it. We'd rather
discover early that the test is model-version-sensitive or has a
hidden confounder than discover it from a referee.

— John Komkov, jkomkov@gvt.ai
