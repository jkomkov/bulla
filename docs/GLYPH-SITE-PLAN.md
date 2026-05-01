# Glyph Standard — Site Plan

*Revised 2026-04-06. Incorporates three rounds of editorial review + logo integration.*

---

## The argument for building now

The project has a published PyPI package (v0.33.0), a calibration study with real results across 50 MCP servers, a public benchmark (BABEL, 932 instances), and zero web presence beyond a GitHub README. The README is good but it's not where technical evaluators form opinions.

Three audiences need a home:

1. **Developers** who want to audit their MCP config — they need `pip install bulla && bulla audit` and a 2-minute quickstart.
2. **Technical evaluators** (CTOs, platform engineers, agent framework authors) who want to know whether Bulla is real — they need the calibration data, the BABEL benchmark, and a clear explanation of the math.
3. **Intellectually serious readers** who encounter the Res Agentica thesis — they already have resagentica.com.

The mistake would be a sprawling marketing site. The right move is 7 pages that earn their place. Everything else waits for the first 10 users to tell you what they actually need.

## Domain

**glyphstandard.com** — separate from resagentica.com.

"Standard" signals permanence, institutional weight, and verification infrastructure. The `.com` communicates seriousness. The name reads like a standards body, not a startup.

- **glyphstandard.com** → corporate identity + Bulla docs + research
- **resagentica.com** → the trilogy + papers (unchanged)
- Cross-links where the audiences overlap (the About page, not a dedicated bridge page)

## Design principles

### Governing aesthetic

**Palantir's institutional gravity as primary.** Vercel's developer ergonomics for docs. Anthropic's typographic clarity for prose. The domain "glyphstandard" demands more weight than a playful dev-tool site — this is verification infrastructure, and the visual language should communicate that a standards body built it, not a weekend project.

### What this means in practice

- **Don't explain, don't persuade, just show.** The people who get it are your users. That self-selection is a feature, not a bug — it should permeate every design decision.
- **Static terminal rendering, not a live demo.** A live demo says "let me prove this works." A static screenshot says "this is what it does." The distinction is intellectual posture: confidence vs. persuasion.
- **Typography-driven.** The words carry the weight. No images except diagrams. No stock photography. No illustrations. No decorative gradients. No animated hero sections.
- **Code blocks are first-class citizens.** They're the primary content, not supplementary to prose.

### What to avoid

Marketing fluff, "trusted by" logos (zero customers), feature comparison tables against competitors that don't exist, exclamation marks, "revolutionary," "the future of." Just the terminal output, the calibration data, and the math.

## Visual identity

### The mark: Sa-Ra

The logo is the Egyptian hieroglyphic compound Sa-Ra (sꜣ-Rꜥ): the duck ("son") and the sun disk ("Ra"). One of the five royal titulary names — the one that established legitimacy through witnessed divine lineage. For a verification infrastructure company called "Glyph Standard," this is not decoration. It's a statement of origin.

**Visual properties:**
- Heavy calligraphic strokes — a seal impression, not a tech logo
- Organic + geometric tension: the duck flows, the circle is pure geometry. Mirrors the product: human judgment meets mathematical precision.
- The white eye in the duck's head is the detail that makes the mark alive. Preserve at all costs.
- Stroke weight is substantial — holds at favicon size.

**Rendering rules:**
- Black (`#1A1A1A`) on light backgrounds. White (`#E8E8E8`) on dark backgrounds.
- Never colorized. Never bronze. The accent sits *beside* the mark, not on it.
- The mark's calligraphic weight provides visual mass. Typography must counterbalance with lightness, not compete.

**Scales:**

| Context | Mark | Notes |
|---------|------|-------|
| Full (nav, landing, ~80px) | Duck + circle | Complete Sa-Ra compound |
| Reduced (favicon, 32px) | Circle alone (Ra) | Sun disk reads cleanly at small sizes |
| Social/OG image | Full mark centered | White on `#0A0A0A` field |

**Source file:** `sa-ra.png` (need SVG for production).

### Color system: Patinated bronze

The accent color is muted bronze — the color of aged authentication stamps, museum brass, the tarnished surface of a seal matrix. Not gold (too crypto), not amber (too warm/friendly), but the specific desaturated metallic brown of an institutional object that has been used and trusted for decades.

No other developer tool uses this color. It reads as quiet authority. It doesn't fight for attention — it anchors.

**Design rationale:** The accent color matches the diagnostic's "uncertain" zone — the space between safe (fee=0) and unsafe (fee≥4) where Bulla's judgment is most valuable. The brand lives in the space where the answer isn't obvious.

All values verified WCAG AA (≥4.5:1 contrast ratio):

| Token | Dark mode | Light mode | Usage |
|-------|-----------|------------|-------|
| `--bg` | `#0A0A0A` | `#FAFAF8` | Page background |
| `--surface` | `#141414` | `#FFFFFF` | Card/terminal block backgrounds |
| `--text-primary` | `#E8E8E8` | `#1A1A1A` | Body text |
| `--text-secondary` | `#888888` | `#666666` | Muted text, labels |
| `--text-tertiary` | `#555555` | `#999999` | Metadata, timestamps |
| `--accent` | `#B09070` | `#7F6A4C` | Interactive elements, logo, highlights |
| `--accent-hover` | `#C8A882` | `#5C4D35` | Hover state |
| `--border` | `#222222` | `#E5E5E3` | Subtle borders |
| `--code-bg` | `#111111` | `#F5F5F3` | Code blocks |
| `--zone-safe` | `#4A9A6A` | `#2D7A4A` | Fee zone: safe (fee = 0) |
| `--zone-uncertain` | `#B09070` | `#7F6A4C` | Fee zone: uncertain (= accent) |
| `--zone-unsafe` | `#C85A4A` | `#B5453A` | Fee zone: unsafe (fee ≥ 4) |

Note: `--zone-uncertain` intentionally equals `--accent`. The brand IS the uncertain zone.

### Typography

| Role | Font | Weight | Rationale |
|------|------|--------|-----------|
| Headings & body | Geist Sans | 400, 500, 600 | Clean, geometric — counterbalances the mark's organic calligraphic weight |
| Wordmark | Geist Sans | 400–500, uppercase, tracked | Lightweight and geometric beside the heavy mark |
| Code & terminal | JetBrains Mono | 400 | Industry standard for developer tooling |

No system fonts. A single well-chosen sans-serif is worth the 50KB for a site projecting institutional authority.

The mark provides visual mass. The type provides intellectual clarity. They don't compete. The wordmark renders as:

```
G L Y P H   S T A N D A R D
```

Uppercase, generous letter-spacing. The tracking creates the institutional register that "glyphstandard" as a URL already implies.

### Layout

| Parameter | Value |
|-----------|-------|
| Max prose width | 720px |
| Max content width (with sidebar) | 960px |
| Default mode | Dark |
| Navigation | Top bar only (7 pages doesn't need a sidebar) |
| Code block background | `#111111` (dark, terminal-like — the product IS terminal output) |
| Light mode background | `#FAFAF8` (warm off-white, not clinical pure white) |

## MVP site structure (7 pages)

```
glyphstandard.com/
├── /                              # Landing — terminal hero, one screen
├── /bulla/quickstart              # 2 min to first audit
├── /bulla/concepts                # What the terms mean
├── /bulla/cli                     # Every command, documented
├── /research/calibration          # The step function that proves it works
├── /research/babel                # Public benchmark: 932 instances, leaderboard
└── /about                         # Two paragraphs: who + license
```

Product docs live under `/bulla/*` (not `/docs/*`) so the namespace is stable when Babel Gauge ships later at `/gauge/*`.

**Total: 7 pages. ~9 hours of focused work.**

### Future pages (build only when users ask)

- `/bulla/sdk` — Python SDK reference
- `/bulla/packs` — Convention pack authoring
- `/bulla/ci` — CI/CD integration (GitHub Actions, SARIF)
- `/bulla/mcp-server` — Bulla as MCP server
- `/research/seam-paper` — Hosted PDF + theorem statement
- `/research/witness-contract` — Normative spec rendered
- `/index` — Coherence Index dashboard (deferred: only 2 dimensions differentiate servers today)

## Page designs

### `/` — Landing

One screen. No scroll required. Four elements: wordmark, tagline, terminal, navigation.

```
              [Sa-Ra mark, ~80px]

         G L Y P H   S T A N D A R D


    Your MCP servers silently disagree on conventions.
    Bulla tells you where.


    ┌──────────────────────────────────────────────────┐
    │ $ bulla audit                                    │
    │                                                  │
    │ filesystem → github                              │
    │   path_convention: absolute_local ↔ relative_repo│
    │   read_file.path ↔ create_file.path              │
    │                                                  │
    │ Coherence fee: 2    Zone: UNSAFE                 │
    │ Blind spots: 3                                   │
    │ Disposition: proceed_with_bridge                 │
    └──────────────────────────────────────────────────┘

    pip install bulla


    Docs →    Research →    GitHub →
```

**Key decisions:**
- The mark sits above the wordmark. No explanation of what it is. A glyph on a site called Glyph Standard. Those who recognize Sa-Ra will know what it means. Those who don't will register it as an authoritative, ancient-feeling mark. Both reactions are correct.
- The blind spot detail is the **first thing** in the terminal, not the summary numbers. The specific `absolute_local ↔ relative_repo` mismatch is the moment of recognition — "oh, I have that exact setup."
- Zone classification (`UNSAFE`) makes the output self-interpreting. A developer doesn't need to know what fee=2 means; they know what "unsafe" means.
- `pip install bulla` is a standalone line below the terminal, not inside it. It's the call to action, not part of the output.
- Static rendering. Not a live demo. Confidence, not persuasion.

**Below the fold** (appears on scroll):

> Fee = 0 guarantees safety. Fee > 0 predicts failure with 95–100% accuracy across 1,225 real-world compositions. The math is exact — no probabilistic inference, no model weights.
>
> [Read the calibration study →](/research/calibration)    [Read the BABEL benchmark →](/research/babel)

### `/bulla/quickstart` — 2 minutes to first audit

```markdown
# Quickstart

## Install

    pip install bulla

## Audit your MCP setup

    bulla audit

Bulla auto-detects your Cursor or Claude Desktop config and scans
all configured MCP servers.

## Read the output

    filesystem → github
      path_convention: absolute_local ↔ relative_repo
      read_file.path ↔ create_file.path

    Coherence fee: 2    Zone: UNSAFE
    Blind spots: 3

The coherence fee counts semantic dimensions where your servers
disagree on conventions that bilateral verification cannot detect.

Fee = 0 means safe. Fee > 0 means blind spots exist.

## What to do about it

    bulla audit -v           # see all blind spots
    bulla audit --format json # machine-readable for CI
    bulla bridge comp.yaml   # auto-generate patches

## Next: understand the concepts →
```

### `/bulla/concepts` — What the terms mean

Glossary-meets-tutorial. Each concept is a short section with one definition and one example. No prose. No narrative.

- **Coherence fee** — what it is, how it's computed (one paragraph + the rank formula)
- **Blind spot** — a convention that bilateral checks can't see
- **Dimension** — a named convention (path format, ID offset, date format...)
- **Pack** — a YAML file defining dimensions (base pack ships with 11)
- **Bridge** — a patch that makes a hidden convention observable
- **Receipt** — a tamper-evident record of what was diagnosed
- **Boundary fee** — the cost of cross-server delegation
- **Zone** — safe (fee=0), uncertain (fee 1–3), unsafe (fee≥4)

### `/bulla/cli` — CLI reference

Every command. Description, usage, flags, example output.

| Command | Purpose |
|---------|---------|
| `bulla audit` | Scan MCP config, diagnose cross-server coherence |
| `bulla gauge` | Diagnose single server/manifest |
| `bulla diagnose` | Full diagnostic from composition YAML |
| `bulla check` | CI gate with fee/blind-spot thresholds |
| `bulla scan` | Zero-config live server scanning |
| `bulla witness` | Diagnose + emit tamper-evident receipt |
| `bulla bridge` | Auto-patch and emit patched composition |
| `bulla serve` | MCP stdio server (expose bulla as MCP tool) |
| `bulla discover` | LLM-powered convention inference |
| `bulla manifest` | Generate manifest from server metadata |
| `bulla init` | Initialize composition YAML |
| `bulla infer` | Classify dimension values from tool schemas |

### `/research/calibration` — The data

**Critical framing:** The calibration data is a step function, not a smooth curve. Present it honestly:

> Fee = 0 guarantees safety (0% failure across 1,128 compositions). Fee > 0 predicts failure with 95–100% accuracy. There is no gradual degradation — the transition is binary.

The table:

| Fee | Compositions | With mismatch | P(failure) |
|-----|-------------|---------------|------------|
| 0 | 1,128 | 0 | 0.00% |
| 11 | 48 | 46 | 95.83% |
| 18 | 48 | 48 | 100.00% |
| 30 | 1 | 1 | 100.00% |

Methodology section: 50 MCP servers, 1,225 pairwise compositions, Bulla v0.28.0+, base convention pack (11 dimensions), blind spots annotated via live execution + LLM classification.

Dimension landscape: `id_offset_match` (7,497 occurrences), `path_convention_match` (3,307 occurrences). Two dimensions dominate because the vocabulary is young — as the community pack grows, the landscape will diversify.

Link to the SEAM paper PDF. Link to WITNESS-CONTRACT on GitHub. Link to calibration data on GitHub.

### `/research/babel` — The public benchmark

This is the highest-authority page on the site for technical evaluators. It's also the page most likely to earn inbound links from the agent/ML research community.

Content:
- **932 instances** across 7 families, 3 tracks
- **Deterministic oracle** (mean holonomy as ground truth — no human annotation, no model-as-judge)
- **10 registered baselines** including the reference sheaf-cohomological diagnostic
- **Key result:** GPT-4o scores 0.82. Standard evals score 0.17. The structural method scores 0.99.
- Download link for dev split
- Evaluation instructions: `python -m coherence_gym evaluate --split dev`
- Link to `benchmark/coherence-gym/` on GitHub
- Link to `COMMERCIAL_BOUNDARY.md` (public/proprietary separation)

### `/about` — About Glyph Standard

Two paragraphs. No more.

**Paragraph 1:** "Glyph Standard builds verification infrastructure for tool-composition coordination. Bulla is our first product — a witness kernel that detects semantic convention mismatches in MCP server compositions before execution. The people who get it are our users."

**Paragraph 2:** "Bulla is licensed under the Business Source License 1.1. It converts to Apache 2.0 on 2030-04-01. The BSL means you can use Bulla freely for internal tooling, evaluation, and non-production purposes. Production use by organizations with >$10M revenue requires a commercial license." (Adjust terms to match actual LICENSE.)

Links: GitHub, PyPI, resagentica.com (one sentence: "Bulla operationalizes the compositional coherence theory developed in the Res Agentica research program.").

## Technical implementation

**Framework:** Next.js 15 (App Router) — consistent with the existing monorepo stack.

**Content:** MDX for docs pages. CLI reference generated from `bulla --help` output where possible.

**Deployment:** Vercel. Separate project, separate domain (glyphstandard.com).

**Design system:** Minimal `@glyph/design-system` or inline CSS variables. Tailwind for layout. No component library beyond what's needed.

**Search:** Not in MVP. 7 pages doesn't need search. Add when page count exceeds ~15.

**Analytics:** Vercel Analytics (built-in). No third-party tracking.

**Monorepo structure:**
```
res-agentica/
├── glyph/                     # New: glyphstandard.com
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx       # Landing
│   │   │   ├── bulla/         # Product docs
│   │   │   ├── research/      # Research pages
│   │   │   └── about/         # About
│   │   ├── components/        # Minimal: Terminal, CodeBlock, Nav
│   │   └── styles/
│   │       └── tokens.css     # The color system above
│   ├── content/               # MDX docs content
│   ├── next.config.ts
│   ├── tailwind.config.ts
│   ├── package.json
│   └── vercel.json
├── app/                       # Existing: resagentica.com
├── bulla/                     # Existing: Python tool
└── ...
```

## Build order

| # | Page | Estimate | Notes |
|---|------|----------|-------|
| 1 | `/` (landing) | 1.5h | Terminal component + responsive layout |
| 2 | `/bulla/quickstart` | 1h | MDX content + code blocks |
| 3 | `/bulla/concepts` | 1.5h | Glossary layout + formula rendering |
| 4 | `/bulla/cli` | 2h | 12 commands, flags, examples |
| 5 | `/research/calibration` | 1.5h | Table + methodology + SVG step chart |
| 6 | `/research/babel` | 1h | Benchmark description + download links |
| 7 | `/about` | 15min | Two paragraphs + links |

**Total: ~9 hours.**

## What NOT to build

- Blog (no content; a blog with one post looks worse than no blog)
- Pricing page (BSL is in the LICENSE; no SaaS to price yet)
- Status page (no service to monitor)
- Community page (no community yet)
- Changelog on the site (CHANGELOG.md on GitHub is sufficient)
- API reference auto-generation (70+ symbols is too many for zero users)
- Interactive playground (the CLI is the playground)
- Newsletter signup (no audience to email)
- Coherence Index dashboard (only 2 dimensions differentiate servers; data is flat)
- `/research/res-agentica` as a standalone page (the cross-link belongs in About)
- Live terminal demo (static rendering is more confident)
- Client-side search (7 pages doesn't need it)

## The voice

Clean. Quiet. Confident. The site says: "This is a serious tool backed by serious math. Here's how to use it. Here's the proof it works. That's all."

The people who get it are your users. Don't explain, don't persuade, just show.
