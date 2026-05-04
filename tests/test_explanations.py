"""Coverage gate for the dimension explanation registry.

Every dimension that ``Diagnostic.blind_spots[i].dimension`` can carry
must have an entry in ``bulla.explanations.EXPLANATIONS``. The
narrative scan formatter reads this registry; missing entries silently
fall back to a generic explanation, which dilutes the output.

The tests here lock the registry to two universes:

  1. Every dimension declared in ``src/bulla/packs/{seed,community}/*.yaml``.
  2. Every hardcoded pattern in ``bulla.infer.classifier`` (the
     built-in convention dimensions like ``path_convention``,
     ``temporal_format``, ``id_offset``).

Adding a new dimension to a pack without adding an entry here will
fail this test before the PR can merge.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import yaml

from bulla.explanations import EXPLANATIONS, DimensionExplanation, explain


# ── Hardcoded list mirrors ``bulla.infer.classifier._CORE_NAME_PATTERNS`` ──
#
# Kept in sync with the patterns block in classifier.py. If a new
# pattern is added there, this list must grow too.
HARDCODED_DIMENSIONS = {
    "amount_unit",
    "date_format",
    "encoding",
    "id_offset",
    "line_ending",
    "null_handling",
    "path_convention",
    "precision",
    "rate_scale",
    "score_range",
    "timezone",
}


def _packs_dir() -> Path:
    pkg = importlib.resources.files("bulla")
    return Path(str(pkg / "packs"))


def _all_pack_dimensions() -> set[str]:
    """Walk every pack YAML under ``src/bulla/packs/`` (seed + community
    + base) and collect every dimension key declared at the top level."""
    out: set[str] = set()
    for path in _packs_dir().rglob("*.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        dims = data.get("dimensions") or {}
        if isinstance(dims, dict):
            for name in dims.keys():
                if isinstance(name, str):
                    out.add(name)
    return out


# ── 1. Every pack-declared dimension has an entry ──────────────────


def test_every_pack_dimension_has_an_entry():
    """The seed-pack + community-pack dimension union must be a
    subset of EXPLANATIONS."""
    pack_dims = _all_pack_dimensions()
    missing = sorted(pack_dims - set(EXPLANATIONS.keys()))
    assert not missing, (
        f"the following pack-declared dimensions have no entry in "
        f"bulla/src/bulla/explanations.py: {missing}"
    )


# ── 2. Every hardcoded built-in dimension has an entry ─────────────


def test_every_hardcoded_dimension_has_an_entry():
    missing = sorted(HARDCODED_DIMENSIONS - set(EXPLANATIONS.keys()))
    assert not missing, (
        f"the following classifier-hardcoded dimensions have no entry "
        f"in bulla/src/bulla/explanations.py: {missing}"
    )


# ── 3. Every entry is well-formed ──────────────────────────────────


def test_every_entry_has_non_empty_strings():
    for name, entry in EXPLANATIONS.items():
        assert isinstance(entry, DimensionExplanation), (
            f"{name}: entry is not a DimensionExplanation"
        )
        assert entry.name == name, (
            f"{name}: entry.name {entry.name!r} doesn't match key {name!r}"
        )
        assert entry.human_label.strip(), (
            f"{name}: human_label is empty"
        )
        assert entry.explanation.strip(), (
            f"{name}: explanation is empty"
        )
        assert entry.failure_mode.strip(), (
            f"{name}: failure_mode is empty"
        )


# ── 4. Fallback is reachable for unknown dimensions ────────────────


def test_explain_fallback_on_unknown():
    """``explain('nonexistent')`` must return a valid
    DimensionExplanation rather than raising."""
    entry = explain("definitely_not_a_real_dimension_xyz_123")
    assert isinstance(entry, DimensionExplanation)
    # The fallback's name field is the queried name (so callers can
    # still produce useful output).
    assert entry.name == "definitely_not_a_real_dimension_xyz_123"
    # And the fallback fields are non-empty.
    assert entry.human_label.strip()
    assert entry.explanation.strip()
    assert entry.failure_mode.strip()


# ── 5. Lookup by key returns the registered entry ─────────────────


def test_explain_returns_registered_entry_for_known_dimension():
    entry = explain("path_convention")
    assert entry.name == "path_convention"
    assert entry.human_label == "path format"
    # The explanation should mention paths concretely.
    assert "/" in entry.explanation or "path" in entry.explanation.lower()


# ── 6. No duplicate human_labels (visual collision check) ──────────


def test_no_duplicate_human_labels_within_a_tier():
    """Two entries sharing the same human_label produce ambiguous
    narrative output. This isn't a hard correctness issue but it
    flags lazy labeling. Allow exact matches only when the dimensions
    are obviously variants (date_format vs temporal_format)."""
    seen: dict[str, list[str]] = {}
    for name, entry in EXPLANATIONS.items():
        seen.setdefault(entry.human_label, []).append(name)
    # We tolerate 'date convention' vs 'timestamp format' even though
    # they cover related ground; the test catches accidental
    # copy-paste of the same label across unrelated dimensions.
    duplicates = {label: dims for label, dims in seen.items() if len(dims) > 1}
    assert not duplicates, (
        f"these dimensions share a human_label and would render "
        f"ambiguously in scan output: {duplicates}"
    )
