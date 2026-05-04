"""Sprint 13 — single figure: regime-lattice predicate rates per corpus.

Reuses the Sprint 11 lattice-audit data (no new computation) to render a
one-figure summary suitable for the certification suite report.

Outputs:
  papers/composition-doctrine/sprint13_certification_suite_figure.svg
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AUDIT_JSON = (
    REPO.parent / "papers" / "composition-doctrine" / "sprint11_lattice_audit.json"
)
OUT_SVG = (
    REPO.parent / "papers" / "composition-doctrine"
    / "sprint13_certification_suite_figure.svg"
)


# Predicate columns to render, in regime-lattice order (weakest → strongest)
PREDICATES = [
    ("has_projective_observables", "projective"),
    ("is_well_formed_for_fee", "well-formed"),
    ("has_dfd_conservative", "DFD-conservative"),
    ("has_chp_conservative", "CHP-conservative"),
    ("is_exact_regime_conservative", "exact-conservative"),
]


def render_svg(rows: list[dict]) -> str:
    """Render a horizontal bar chart in pure SVG (no matplotlib dep).
    Each row of the chart is a corpus; each bar shows the rate of one
    predicate. Light grey ⇒ low rate; dark blue ⇒ 100%."""
    n_corpora = len(rows)
    n_preds = len(PREDICATES)

    # Layout
    margin_left = 220
    margin_right = 30
    margin_top = 60
    margin_bottom = 30
    bar_h = 14
    cell_h = bar_h + 4  # 4px padding between predicate rows in same corpus
    corpus_h = (n_preds * cell_h) + 16  # extra padding between corpora
    chart_w = 540
    width = margin_left + chart_w + margin_right
    height = margin_top + (n_corpora * corpus_h) + margin_bottom

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" '
        f'font-family="-apple-system, system-ui, sans-serif" font-size="10">'
    )
    # Background
    parts.append(f'<rect width="{width}" height="{height}" fill="#fafafa"/>')

    # Title
    parts.append(
        f'<text x="{margin_left}" y="22" font-size="13" font-weight="600" '
        f'fill="#222">Sprint 13 — Regime Predicate Rates by Corpus</text>'
    )
    parts.append(
        f'<text x="{margin_left}" y="40" font-size="10" fill="#666">'
        f'Higher = stronger regime guarantees. Source: '
        f'sprint11_lattice_audit.json (Sprint 11).</text>'
    )

    # x-axis grid (0%, 50%, 100%)
    for pct in (0, 25, 50, 75, 100):
        x = margin_left + (chart_w * pct / 100)
        parts.append(
            f'<line x1="{x}" y1="{margin_top}" '
            f'x2="{x}" y2="{height - margin_bottom}" '
            f'stroke="#e0e0e0" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x}" y="{margin_top - 6}" '
            f'text-anchor="middle" fill="#888">{pct}%</text>'
        )

    # Per-corpus block
    y = margin_top + 4
    for row in rows:
        n = row.get("n", 0)
        # Corpus label
        parts.append(
            f'<text x="{margin_left - 12}" y="{y + 12}" '
            f'text-anchor="end" font-weight="600" fill="#222">'
            f'{row["name"]}</text>'
        )
        parts.append(
            f'<text x="{margin_left - 12}" y="{y + 26}" '
            f'text-anchor="end" font-size="9" fill="#888">n = {n}</text>'
        )

        # Per-predicate bar rows
        for j, (key, label) in enumerate(PREDICATES):
            count = row.get(key, 0)
            rate = (count / n) if n > 0 else 0
            bar_y = y + (j * cell_h) + (n_preds * 0)  # offset accumulates
            bar_y = y + (j * cell_h)
            # bar background (rule)
            parts.append(
                f'<rect x="{margin_left}" y="{bar_y}" '
                f'width="{chart_w}" height="{bar_h}" '
                f'fill="#fff" stroke="#e8e8e8" stroke-width="0.5"/>'
            )
            # filled bar
            fill_w = chart_w * rate
            color = (
                "#1f4ea1" if rate >= 0.99 else
                "#3f6fb5" if rate >= 0.5 else
                "#a3b6d1" if rate >= 0.1 else
                "#dbe2ec"
            )
            parts.append(
                f'<rect x="{margin_left}" y="{bar_y}" '
                f'width="{fill_w}" height="{bar_h}" '
                f'fill="{color}" rx="1"/>'
            )
            # predicate label
            parts.append(
                f'<text x="{margin_left + 6}" y="{bar_y + 10}" '
                f'fill="{"#fff" if rate >= 0.5 else "#222"}">'
                f'{label}</text>'
            )
            # rate text
            parts.append(
                f'<text x="{margin_left + chart_w + 4}" y="{bar_y + 10}" '
                f'fill="#222">{100 * rate:.1f}%</text>'
            )

        y += corpus_h

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    if not AUDIT_JSON.exists():
        print(f"Error: Sprint 11 audit JSON missing: {AUDIT_JSON}",
              file=sys.stderr)
        return 1
    rows = json.loads(AUDIT_JSON.read_text())
    svg = render_svg(rows)
    OUT_SVG.write_text(svg)
    print(f"Wrote {OUT_SVG.relative_to(REPO.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
