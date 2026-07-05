"""Claims-integrity gate for the public README (WS12).

Makes the honesty discipline structural rather than a matter of authorial
care: a public claim that over-states what the program proved becomes a CI
failure. Mirrors the demo-layer gate (``test_demo.py`` on the bulla-demo
branch) and the site-layer gate (``glyph/scripts/check-claims.mjs``). This
module is the canonical home of the forbidden-phrase list; the glyph gate
mirrors it and ``test_forbidden_lists_in_sync_with_glyph_gate`` fails the build
if the two drift, so the "one shared semantics" claim is enforced, not assumed.

The recurring error this prevents: presenting the ρ ≈ 0.996 calibration —
which is fee-vs-*annotation-derived* labels on a schema corpus — as if it were
execution-derived real-traffic prediction, or stating construct-validity as a
"guarantee". Real-traffic predictive validation is open; every surface must say
so or stay silent, never overclaim.
"""

from __future__ import annotations

import re
from pathlib import Path

_README = Path(__file__).resolve().parent.parent / "README.md"

# A correlation number (0.99 / 0.996) is only allowed near an explicit
# label-basis qualifier. Within this many characters of the number, one of the
# qualifier terms must appear.
_QUALIFIER = re.compile(
    r"annotation|not execution|schema-derived|execution-derived|labelled corpus|"
    r"annotation-labelled|annotated",
    re.IGNORECASE,
)
_CORRELATION = re.compile(r"0\.99\d?")
_QUALIFIER_WINDOW = 240

# Phrases that overclaim regardless of context (claims-discipline list). This
# is the canonical source; the glyph site gate (glyph/scripts/check-claims.mjs)
# mirrors it and test_forbidden_lists_in_sync_with_glyph_gate (below) fails if
# the two ever drift, so "shared semantics" is enforced, not just asserted.
_FORBIDDEN = [
    "guarantees no convention mismatch",
    "guarantees real mismatches",
    "the right one",            # uniqueness-given-axioms is not proof-of-validity
    "no other tool",            # banned "we are the only ones" framing
    "only tool in this space",
    "proves the metric is correct",
]


def _scan(text: str) -> list[str]:
    """Return a list of violation messages for the given text."""
    violations: list[str] = []
    lower = text.lower()
    for phrase in _FORBIDDEN:
        if phrase in lower:
            violations.append(f"forbidden phrase: {phrase!r}")
    for m in _CORRELATION.finditer(text):
        window = text[
            max(0, m.start() - _QUALIFIER_WINDOW) : m.start() + _QUALIFIER_WINDOW
        ]
        if not _QUALIFIER.search(window):
            ctx = text[max(0, m.start() - 50) : m.start() + 50].replace("\n", " ")
            violations.append(
                f"unqualified correlation {m.group(0)!r} near: …{ctx}…"
            )
    return violations


def test_readme_makes_no_unqualified_or_forbidden_claims() -> None:
    text = _README.read_text(encoding="utf-8")
    violations = _scan(text)
    assert not violations, (
        "README claims-integrity violations (every correlation number must "
        "carry an annotation/not-execution-derived qualifier; no overclaim "
        "phrases):\n  - " + "\n  - ".join(violations)
    )


def test_gate_bites_on_planted_violations() -> None:
    """The guard must actually fire — assert it catches both a bare
    correlation and a forbidden phrase, so a future weakening of the regex is
    itself a test failure."""
    bare = "The structural method correlates at 0.996, deterministic and exact."
    assert _scan(bare), "gate failed to catch an unqualified 0.996"

    guaranteed = "fee=0 guarantees no convention mismatch."
    assert _scan(guaranteed), "gate failed to catch a 'guarantees' overclaim"

    # And the corrected forms must pass cleanly.
    qualified = (
        "Spearman 0.996 against annotation-derived labels (not execution-derived)."
    )
    assert not _scan(qualified), "gate false-positived on a properly qualified claim"


_GLYPH_GATE = (
    Path(__file__).resolve().parent.parent.parent
    / "glyph"
    / "scripts"
    / "check-claims.mjs"
)


def _glyph_forbidden_phrases() -> set[str]:
    """Extract the FORBIDDEN array literal from the glyph site gate."""
    text = _GLYPH_GATE.read_text(encoding="utf-8")
    block = re.search(r"const FORBIDDEN = \[(.*?)\]", text, re.DOTALL)
    assert block, "could not locate FORBIDDEN array in check-claims.mjs"
    return set(re.findall(r"'([^']+)'", block.group(1)))


def test_forbidden_lists_in_sync_with_glyph_gate() -> None:
    """The README gate (here) and the glyph site gate must enforce the *same*
    overclaim list. This makes the cross-surface 'shared semantics' a structural
    guarantee: adding a banned phrase to one gate but not the other fails CI."""
    if not _GLYPH_GATE.exists():
        import pytest

        pytest.skip(f"glyph gate not present at {_GLYPH_GATE}")
    py = set(_FORBIDDEN)
    js = _glyph_forbidden_phrases()
    assert py == js, (
        "forbidden-phrase lists have drifted between the README gate and the "
        f"glyph gate.\n  only in Python: {sorted(py - js)}\n  only in JS:     "
        f"{sorted(js - py)}"
    )
