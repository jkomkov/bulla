"""Phase 2: Batch coherence fee computation for the Coherence Index.

Builds compositions from the manifest corpus, computes coherence fees,
and stores results in an SQLite database for analysis.
"""

from __future__ import annotations

import itertools
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS compositions (
    id TEXT PRIMARY KEY,
    name TEXT,
    servers TEXT,
    n_tools INTEGER,
    n_edges INTEGER,
    strategy TEXT,
    pair_type TEXT,
    categories TEXT
);

CREATE TABLE IF NOT EXISTS diagnostics (
    comp_id TEXT PRIMARY KEY REFERENCES compositions(id),
    coherence_fee INTEGER,
    n_blind_spots INTEGER,
    n_unbridged INTEGER,
    boundary_fee INTEGER,
    rank_obs INTEGER,
    rank_full INTEGER,
    diagnostic_hash TEXT
);

CREATE TABLE IF NOT EXISTS blind_spots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comp_id TEXT REFERENCES compositions(id),
    dimension TEXT,
    from_tool TEXT,
    to_tool TEXT,
    from_field TEXT,
    to_field TEXT,
    from_hidden INTEGER,
    to_hidden INTEGER,
    annotation TEXT,
    annotation_source TEXT,
    validated INTEGER DEFAULT 0,
    validation_result TEXT
);

CREATE INDEX IF NOT EXISTS idx_bs_comp ON blind_spots(comp_id);
CREATE INDEX IF NOT EXISTS idx_bs_dim ON blind_spots(dimension);
CREATE INDEX IF NOT EXISTS idx_diag_fee ON diagnostics(coherence_fee);
"""


class CoherenceDB:
    """SQLite store for coherence computation results."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.execute(
            "INSERT OR REPLACE INTO meta VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self.conn.commit()

    def has_composition(self, comp_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM compositions WHERE id = ?", (comp_id,)
        ).fetchone()
        return row is not None

    def store_result(self, result: "ComputeResult") -> None:
        """Store a composition + diagnostic + blind spots."""
        self.conn.execute(
            "INSERT OR REPLACE INTO compositions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.comp_id,
                result.name,
                json.dumps(result.servers),
                result.n_tools,
                result.n_edges,
                result.strategy,
                result.pair_type,
                json.dumps(result.categories),
            ),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO diagnostics VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.comp_id,
                result.coherence_fee,
                result.n_blind_spots,
                result.n_unbridged,
                result.boundary_fee,
                result.rank_obs,
                result.rank_full,
                result.diagnostic_hash,
            ),
        )
        # Clear existing blind spots for this comp (idempotent re-run)
        self.conn.execute("DELETE FROM blind_spots WHERE comp_id = ?", (result.comp_id,))
        for bs in result.blind_spots:
            self.conn.execute(
                "INSERT INTO blind_spots "
                "(comp_id, dimension, from_tool, to_tool, from_field, to_field, "
                "from_hidden, to_hidden) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result.comp_id,
                    bs["dimension"],
                    bs["from_tool"],
                    bs["to_tool"],
                    bs["from_field"],
                    bs["to_field"],
                    int(bs["from_hidden"]),
                    int(bs["to_hidden"]),
                ),
            )
        self.conn.commit()

    def summary(self) -> dict[str, Any]:
        """Return summary statistics."""
        n_comp = self.conn.execute("SELECT COUNT(*) FROM compositions").fetchone()[0]
        n_diag = self.conn.execute("SELECT COUNT(*) FROM diagnostics").fetchone()[0]
        n_bs = self.conn.execute("SELECT COUNT(*) FROM blind_spots").fetchone()[0]
        fee_dist = self.conn.execute(
            "SELECT coherence_fee, COUNT(*) FROM diagnostics GROUP BY coherence_fee "
            "ORDER BY coherence_fee"
        ).fetchall()
        avg_fee = self.conn.execute(
            "SELECT AVG(coherence_fee) FROM diagnostics"
        ).fetchone()[0]
        n_nonzero = self.conn.execute(
            "SELECT COUNT(*) FROM diagnostics WHERE coherence_fee > 0"
        ).fetchone()[0]
        n_boundary = self.conn.execute(
            "SELECT COUNT(*) FROM diagnostics WHERE boundary_fee > 0"
        ).fetchone()[0]
        return {
            "compositions": n_comp,
            "diagnostics": n_diag,
            "blind_spots": n_bs,
            "fee_distribution": dict(fee_dist),
            "avg_fee": round(avg_fee, 2) if avg_fee is not None else 0,
            "nonzero_fee_count": n_nonzero,
            "nonzero_boundary_count": n_boundary,
            "nonzero_fee_pct": round(100 * n_nonzero / n_comp, 1) if n_comp else 0,
        }

    def close(self) -> None:
        self.conn.close()


@dataclass
class ComputeResult:
    """Result of diagnosing one composition."""

    comp_id: str
    name: str
    servers: list[str]
    n_tools: int
    n_edges: int
    strategy: str
    pair_type: str
    categories: list[str]
    coherence_fee: int
    n_blind_spots: int
    n_unbridged: int
    boundary_fee: int
    rank_obs: int
    rank_full: int
    diagnostic_hash: str
    blind_spots: list[dict[str, Any]]
    # Kernel objects — always populated by diagnose_pair for receipt generation
    kernel_composition: Any = field(default=None, repr=False)
    kernel_diagnostic: Any = field(default=None, repr=False)


def _classify_pair_type(cat_a: str, cat_b: str) -> str:
    if not cat_a or not cat_b:
        return "unknown"
    if cat_a == cat_b:
        return "intra_category"
    return "cross_category"


def diagnose_pair(
    server_a: str,
    tools_a: list[dict[str, Any]],
    server_b: str,
    tools_b: list[dict[str, Any]],
    *,
    category_a: str = "",
    category_b: str = "",
) -> ComputeResult:
    """Diagnose a pairwise composition of two servers."""
    from bulla.diagnostic import decompose_fee, diagnose
    from bulla.guard import BullaGuard

    # Prefix tool names with server name (standard bulla audit convention)
    prefixed_tools: list[dict[str, Any]] = []
    for t in tools_a:
        prefixed = dict(t)
        prefixed["name"] = f"{server_a}__{t['name']}"
        prefixed_tools.append(prefixed)
    for t in tools_b:
        prefixed = dict(t)
        prefixed["name"] = f"{server_b}__{t['name']}"
        prefixed_tools.append(prefixed)

    comp_name = f"{server_a}+{server_b}"
    guard = BullaGuard.from_tools_list(prefixed_tools, name=comp_name)
    comp = guard.composition
    diag = guard.diagnose()

    # Fee decomposition: partition by server
    # BullaGuard normalizes hyphens to underscores in tool names, so we must
    # match the normalized prefix, not the original server name.
    prefix_a = server_a.replace("-", "_") + "__"
    prefix_b = server_b.replace("-", "_") + "__"
    partition = (
        frozenset(t.name for t in comp.tools if t.name.startswith(prefix_a)),
        frozenset(t.name for t in comp.tools if t.name.startswith(prefix_b)),
    )
    partition = tuple(p for p in partition if p)  # remove empty

    boundary_fee = 0
    if len(partition) == 2:
        try:
            decomp = decompose_fee(comp, partition)
            boundary_fee = decomp.boundary_fee
        except Exception:
            pass

    blind_spots_data = []
    for bs in diag.blind_spots:
        blind_spots_data.append({
            "dimension": bs.dimension,
            "from_tool": bs.from_tool,
            "to_tool": bs.to_tool,
            "from_field": bs.from_field,
            "to_field": bs.to_field,
            "from_hidden": bs.from_hidden,
            "to_hidden": bs.to_hidden,
        })

    return ComputeResult(
        comp_id=comp.canonical_hash(),
        name=comp_name,
        servers=[server_a, server_b],
        n_tools=len(comp.tools),
        n_edges=len(comp.edges),
        strategy="pairwise",
        pair_type=_classify_pair_type(category_a, category_b),
        categories=[category_a, category_b],
        coherence_fee=diag.coherence_fee,
        n_blind_spots=len(diag.blind_spots),
        n_unbridged=diag.n_unbridged,
        boundary_fee=boundary_fee,
        rank_obs=diag.rank_obs,
        rank_full=diag.rank_full,
        diagnostic_hash=diag.content_hash(),
        blind_spots=blind_spots_data,
        kernel_composition=comp,
        kernel_diagnostic=diag,
    )


def run_pairwise(
    corpus_dir: str | Path,
    db_path: str | Path,
) -> CoherenceDB:
    """Run pairwise exhaustive computation on the corpus."""
    from calibration.corpus import ManifestStore

    corpus_dir = Path(corpus_dir)
    store = ManifestStore(data_dir=corpus_dir)
    db = CoherenceDB(db_path)

    servers = store.list_servers()
    n_pairs = len(servers) * (len(servers) - 1) // 2
    logger.info("Computing %d pairwise compositions from %d servers", n_pairs, len(servers))

    # Load all tools upfront
    server_tools: dict[str, list[dict[str, Any]]] = {}
    server_categories: dict[str, str] = {}
    for name in servers:
        server_tools[name] = store.get_tools(name)
        meta = store._index.get(name, {})
        server_categories[name] = meta.get("category", "")

    computed = 0
    skipped = 0
    for a, b in itertools.combinations(servers, 2):
        tools_a = server_tools[a]
        tools_b = server_tools[b]

        if not tools_a or not tools_b:
            skipped += 1
            continue

        try:
            result = diagnose_pair(
                a, tools_a, b, tools_b,
                category_a=server_categories[a],
                category_b=server_categories[b],
            )
            db.store_result(result)
            computed += 1

            if computed % 100 == 0:
                logger.info(
                    "  [%d/%d] %s: fee=%d, boundary=%d, blind_spots=%d",
                    computed, n_pairs, result.name,
                    result.coherence_fee, result.boundary_fee, result.n_blind_spots,
                )
        except Exception as e:
            logger.debug("Failed %s+%s: %s", a, b, e)
            skipped += 1

    logger.info(
        "Pairwise complete: %d computed, %d skipped",
        computed, skipped,
    )
    summary = db.summary()
    logger.info(
        "DB summary: %d compositions, avg fee=%.1f, %d%% nonzero fee, %d%% nonzero boundary",
        summary["compositions"],
        summary["avg_fee"],
        summary["nonzero_fee_pct"],
        round(100 * summary["nonzero_boundary_count"] / max(summary["compositions"], 1), 1),
    )
    return db


def run(
    *,
    corpus_dir: str | Path = "calibration/data",
    db_path: str | Path | None = None,
    strategy: str = "pairwise",
) -> CoherenceDB:
    """Run the computation pipeline."""
    corpus_dir = Path(corpus_dir)
    if db_path is None:
        db_path = corpus_dir / "coherence.db"

    if strategy == "pairwise":
        return run_pairwise(corpus_dir, db_path)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
