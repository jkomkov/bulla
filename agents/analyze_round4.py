"""Apply the pre-registered Round 4 ship/kill criteria to the panel data.

Loads ``uptake_results_round4.jsonl``, aggregates per (model, condition)
cell, and prints the decision tree result based on the criteria committed
in ``UPTAKE-PROTOCOL.md`` BEFORE the trial run.

Run::

    python bulla/agents/analyze_round4.py
"""

from __future__ import annotations

import json
from pathlib import Path


RESULTS = Path(__file__).resolve().parent / "uptake_results_round4.jsonl"


def _pct(values: list[bool]) -> float:
    return 100.0 * sum(values) / max(1, len(values))


def main() -> None:
    recs = [
        json.loads(line)
        for line in RESULTS.read_text().splitlines() if line
    ]
    by_cell: dict[tuple[str, str], list[dict]] = {}
    for r in recs:
        by_cell.setdefault((r["model"], r["condition"]), []).append(r["metrics"])

    print(f"\nLoaded {len(recs)} trials across {len(by_cell)} cells.\n")
    header_cols = [
        "Model", "Cond", "n",
        "consult", "refrain", "bridge", "surface",
        "read_ad", "act_no_c", "refr_blind",
    ]
    fmt = (
        "{:<30} {:<16} {:<3} "
        "{:>7} {:>7} {:>6} {:>7} "
        "{:>7} {:>9} {:>10}"
    )
    print(fmt.format(*header_cols))
    print("─" * 110)

    cell_summary: dict[tuple[str, str], dict[str, float]] = {}
    for (model, cond), ms in sorted(by_cell.items()):
        consult = sum(m["consultation_rate"] for m in ms) / len(ms)
        refrain = _pct([m["verdict_adherence_refrain"] for m in ms])
        bridge = _pct([m["verdict_adherence_bridge_called"] for m in ms])
        surface = _pct([m["verdict_adherence_surfaced"] for m in ms])
        read_ad = _pct([m.get("read_advisory", False) for m in ms])
        act_no_c = _pct(
            [m.get("acted_on_advisory_without_consult", False) for m in ms]
        )
        refr_blind = _pct(
            [m.get("refrained_from_blind_cross_server_call", True) for m in ms]
        )
        print(fmt.format(
            model, cond, len(ms),
            f"{consult:.2f}",
            f"{refrain:.0f}%", f"{bridge:.0f}%", f"{surface:.0f}%",
            f"{read_ad:.0f}%", f"{act_no_c:.0f}%", f"{refr_blind:.0f}%",
        ))
        cell_summary[(model, cond)] = {
            "consult": consult,
            "refrain_xs": refr_blind,
            "full_loop": (
                consult * 100 + refrain + bridge + surface
            ) / 4.0,
            "read_advisory": read_ad,
            "acted_without_consult": act_no_c,
        }

    # ── Apply pre-registered criteria ────────────────────────────
    def get(model: str, cond: str, key: str) -> float:
        return cell_summary.get((model, cond), {}).get(key, 0.0)

    print("\n" + "═" * 60)
    print("APPLYING PRE-REGISTERED KILL / SHIP CRITERIA")
    print("═" * 60)

    claude = "anthropic/claude-sonnet-4.5"
    gpt = "openai/gpt-4o"

    claude_ann_refrain = get(claude, "annotation_only", "refrain_xs")
    gpt_ann_refrain = get(gpt, "annotation_only", "refrain_xs")
    claude_ann_consult = get(claude, "annotation_only", "consult")
    claude_control_refrain = get(claude, "control", "refrain_xs")
    combined_full = get(claude, "combined", "full_loop")
    prompt_full = get(claude, "prompt_only", "full_loop")
    ann_full = get(claude, "annotation_only", "full_loop")

    print(f"\nKey cell metrics (Claude):")
    print(f"  control refrain_blind          : {claude_control_refrain:.0f}%")
    print(f"  annotation_only refrain_blind  : {claude_ann_refrain:.0f}%")
    print(f"  prompt_only full_loop          : {prompt_full:.0f}")
    print(f"  annotation_only full_loop      : {ann_full:.0f}")
    print(f"  combined full_loop             : {combined_full:.0f}")
    print(f"\nGPT-4o:")
    print(f"  annotation_only refrain_blind  : {gpt_ann_refrain:.0f}%")

    # Decision tree
    ann_refrain_delta = claude_ann_refrain - claude_control_refrain
    combined_dominance = combined_full - max(prompt_full, ann_full)

    print("\n" + "─" * 60)
    print("DECISION:")
    print("─" * 60)

    decision = None

    if claude_ann_refrain >= 80 and gpt_ann_refrain >= 50:
        decision = (
            "SHIP producer-annotation as the deployment default.\n"
            f"  Claude annotation_only refrain={claude_ann_refrain:.0f}% (≥80%), "
            f"GPT={gpt_ann_refrain:.0f}% (≥50%)."
        )

    elif combined_dominance >= 15:
        decision = (
            "SHIP annotation + v1.1 prompt as the deployment default.\n"
            f"  combined full_loop ({combined_full:.0f}) exceeds either alone "
            f"by ≥15 (Δ={combined_dominance:.0f})."
        )

    elif ann_refrain_delta < 20:
        # Detect the strict-equivalence case: prompt and combined are
        # within 1 point AND annotation_only ≈ control. That is "prompt
        # is necessary AND sufficient." It's the same kill branch on
        # the producer-annotation thesis but a *positive* finding on
        # the prompt-only deployment thesis.
        if (
            abs(combined_full - prompt_full) <= 5
            and ann_full <= claude_control_refrain + 30
        ):
            decision = (
                "DO NOT ship producer-annotation — pivot KILLED.\n"
                f"  Claude annotation_only ({claude_ann_refrain:.0f}%) did "
                f"not beat control ({claude_control_refrain:.0f}%) by "
                f"≥20 points (Δ={ann_refrain_delta:.0f}).\n"
                f"  Furthermore, combined ({combined_full:.0f}) ≈ "
                f"prompt_only ({prompt_full:.0f}); annotation adds NO "
                f"marginal effect when prompt is on.\n"
                f"  POSITIVE FINDING: v1.1 system prompt is necessary "
                f"AND sufficient on this task. Deploy prompt-only."
            )
        else:
            decision = (
                "DO NOT ship producer-annotation — pivot KILLED.\n"
                f"  Claude annotation_only ({claude_ann_refrain:.0f}%) did not beat "
                f"control ({claude_control_refrain:.0f}%) by ≥20 points "
                f"(Δ={ann_refrain_delta:.0f})."
            )

    elif (
        abs(combined_full - prompt_full) <= 5
        and ann_full <= claude_control_refrain + 10
    ):
        decision = (
            "RECOMMEND prompt-only deployment — defer annotation refactor.\n"
            f"  combined ({combined_full:.0f}) and prompt_only ({prompt_full:.0f}) "
            f"within 5; annotation_only ({ann_full:.0f}) ≤ control+10."
        )

    else:
        decision = (
            "AMBIGUOUS — none of the pre-registered branches triggered.\n"
            f"  Read the cells manually. The data isn't conclusive on "
            f"which channel to ship as default.\n"
            f"  Suggested next: broaden model coverage (gpt-5, claude-opus-4-7, "
            f"gemini), n=5 per cell."
        )

    print(decision)

    # Methodology-note framing
    print("\n" + "─" * 60)
    print("METHODOLOGY-NOTE SENTENCE:")
    print("─" * 60)
    if "SHIP producer-annotation as the deployment default" in decision:
        print('"Bulla prevents cross-server failures even when the agent has '
              'no idea the proxy exists."')
    elif "SHIP annotation + v1.1 prompt" in decision:
        print('"Bulla works under a documented deployment recipe: a '
              '3-sentence policy prompt + a transparent annotation '
              'channel."')
    elif "RECOMMEND prompt-only" in decision or "POSITIVE FINDING" in decision:
        print('"The v1.1 system prompt is the active ingredient; the proxy '
              'is the enforcement substrate. Producer-side annotation is '
              'empirically insufficient — agent attention to runtime '
              'advisories does not translate into action without an '
              'instruction-loaded prompt."')
    else:
        print("(no headline sentence — revisit with more data)")


if __name__ == "__main__":
    main()
