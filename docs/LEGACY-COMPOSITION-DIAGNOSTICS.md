# Legacy composition diagnostics

Bulla began as a diagnostic for conventions hidden at tool-composition seams.
That implementation remains useful, but its evidentiary scope is narrower than
the early product copy claimed.

## Surviving claim

For a pinned composition and declared observable/full convention model, the
coherence fee is an exact disclosure deficit:

```text
fee = rank(delta_full) - rank(delta_observable)
```

It counts independent convention dimensions represented in the full model but
not exposed through the selected observable seam. The diagnostic localizes
those dimensions and can propose disclosures, bridges, or translations inside
the declared model.

## Withdrawn interpretation

Execution-derived labels did not support using a nonzero fee as a general
predictor of mismatch or runtime failure. Fee-zero cases can still fail for
reasons outside the declared convention model; positive-fee cases need not fail
on a particular run. Therefore:

- the default gate does not block on fee;
- fee thresholds are explicit disclosure policies, not safety rules;
- a fee is not evidence that a worldly event occurred or failed;
- a repair is exact only within its declared finite candidate space;
- `FALSIFICATIONS.md` controls over older calibration prose.

## Available commands

The maintained diagnostic surface includes `audit`, `gauge`, `compose`,
`diagnose`, `check`, `scan`, `witness`, `bridge`, `translate`, `serve`, `proxy`,
convention-pack tools, and the LangGraph/CrewAI adapters. Run `bulla --help` and
the subcommand help for the versioned interface.

Detailed mathematical background remains in `docs/ARCHITECTURE.md` and the Res
Agentica historical corpus. Those materials explain the diagnostic layer; they
do not replace the ActionReceipt trust model.
