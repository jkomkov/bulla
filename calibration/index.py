"""Coherence Index: continuous scanning and indexing of the MCP ecosystem.

Orchestrates the full pipeline: crawl → scan → discover → compute → report.
Designed for both one-shot runs and scheduled re-indexing.

Each run is incremental: only new or updated servers are scanned, only
new pairwise compositions are computed. Content-addressing ensures
re-running on an unchanged ecosystem is a no-op.

Usage:
    from calibration.index import Indexer

    indexer = Indexer(data_dir="calibration/data/index")
    result = indexer.run()
    print(result.summary())

Or from the command line:
    python calibration/scripts/run_index.py
    python calibration/scripts/run_index.py --discover --provider openrouter
"""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Minimum number of inputSchema fields for a server to be "real-schema"
MIN_SCHEMA_FIELDS = 3


@dataclass
class IndexResult:
    """Result of one indexing run."""

    started_at: str
    finished_at: str = ""
    servers_before: int = 0
    servers_after: int = 0
    servers_scanned: int = 0
    servers_failed: int = 0
    servers_skipped: int = 0
    compositions_computed: int = 0
    dimensions_discovered: int = 0
    real_schema_servers: int = 0
    total_fields: int = 0
    receipts_generated: int = 0
    scope: str = "curated"

    def summary(self) -> str:
        new = self.servers_after - self.servers_before
        lines = [
            f"Coherence Index run: {self.started_at} (scope={self.scope})",
            f"  Servers: {self.servers_after} total ({'+' if new >= 0 else ''}{new} new)",
            f"  Scanned: {self.servers_scanned} OK, {self.servers_failed} failed, {self.servers_skipped} skipped",
            f"  Real-schema servers (≥{MIN_SCHEMA_FIELDS} fields): {self.real_schema_servers}",
            f"  Total fields: {self.total_fields}",
            f"  Compositions computed: {self.compositions_computed}",
        ]
        if self.receipts_generated > 0:
            lines.append(f"  Receipts generated: {self.receipts_generated}")
        if self.dimensions_discovered > 0:
            lines.append(f"  Dimensions discovered: {self.dimensions_discovered}")
        return "\n".join(lines)


@dataclass
class ServerSpec:
    """A server to attempt scanning."""

    name: str
    command: str
    package: str = ""
    category: str = ""
    source: str = ""  # where we learned about this server


# ── Known npm MCP servers ────────────────────────────────────────────
#
# Curated from:
#   - modelcontextprotocol/servers (official reference)
#   - popular community servers on npm
#   - servers discovered via registry crawl
#
# Servers that need API keys or running services are included but will
# fail gracefully — the scanner handles this.

KNOWN_SERVERS: list[ServerSpec] = [
    # Official reference servers
    ServerSpec("filesystem", "npx -y @modelcontextprotocol/server-filesystem /tmp",
               package="@modelcontextprotocol/server-filesystem", category="official"),
    ServerSpec("github", "npx -y @modelcontextprotocol/server-github",
               package="@modelcontextprotocol/server-github", category="official"),
    ServerSpec("memory", "npx -y @modelcontextprotocol/server-memory",
               package="@modelcontextprotocol/server-memory", category="official"),
    ServerSpec("puppeteer", "npx -y @modelcontextprotocol/server-puppeteer",
               package="@modelcontextprotocol/server-puppeteer", category="official"),
    ServerSpec("postgres", "npx -y @modelcontextprotocol/server-postgres postgresql://localhost/test",
               package="@modelcontextprotocol/server-postgres", category="official"),
    ServerSpec("sqlite", "npx -y @modelcontextprotocol/server-sqlite :memory:",
               package="@modelcontextprotocol/server-sqlite", category="official"),
    ServerSpec("sequential-thinking", "npx -y @modelcontextprotocol/server-sequential-thinking",
               package="@modelcontextprotocol/server-sequential-thinking", category="official"),
    ServerSpec("everything", "npx -y @modelcontextprotocol/server-everything",
               package="@modelcontextprotocol/server-everything", category="official"),
    ServerSpec("fetch", "npx -y @modelcontextprotocol/server-fetch",
               package="@modelcontextprotocol/server-fetch", category="official"),
    ServerSpec("brave-search", "npx -y @modelcontextprotocol/server-brave-search",
               package="@modelcontextprotocol/server-brave-search", category="official"),
    ServerSpec("google-maps", "npx -y @modelcontextprotocol/server-google-maps",
               package="@modelcontextprotocol/server-google-maps", category="official"),
    ServerSpec("slack", "npx -y @modelcontextprotocol/server-slack",
               package="@modelcontextprotocol/server-slack", category="official"),
    ServerSpec("git", "npx -y @modelcontextprotocol/server-git",
               package="@modelcontextprotocol/server-git", category="official"),
    # Community servers (known to work without API keys)
    ServerSpec("playwright", "npx -y @executeautomation/playwright-mcp-server",
               package="@executeautomation/playwright-mcp-server", category="community"),
    ServerSpec("youtube-transcript", "npx -y @kimtaeyoon83/mcp-server-youtube-transcript",
               package="@kimtaeyoon83/mcp-server-youtube-transcript", category="community"),
    ServerSpec("tavily", "npx -y tavily-mcp@latest",
               package="tavily-mcp", category="community"),
    ServerSpec("exa", "npx -y exa-mcp-server@latest",
               package="exa-mcp-server", category="community"),
    ServerSpec("notion", "npx -y @notionhq/notion-mcp-server",
               package="@notionhq/notion-mcp-server", category="community"),
    ServerSpec("npm-search", "npx -y npm-search-mcp-server",
               package="npm-search-mcp-server", category="community"),
    ServerSpec("mcp-server-fetch", "npx -y @tokenizin/mcp-npx-fetch",
               package="@tokenizin/mcp-npx-fetch", category="community"),
    # Servers that may need API keys (graceful failure expected)
    ServerSpec("stripe", "npx -y @stripe/mcp",
               package="@stripe/mcp", category="api-key-required"),
    ServerSpec("sentry", "npx -y @sentry/mcp-server-sentry",
               package="@sentry/mcp-server-sentry", category="api-key-required"),
    ServerSpec("linear", "npx -y @linear/linear-mcp-server",
               package="@linear/linear-mcp-server", category="api-key-required"),
    ServerSpec("supabase", "npx -y @supabase/mcp-server-supabase@latest",
               package="@supabase/mcp-server-supabase", category="api-key-required"),
    ServerSpec("cloudflare", "npx -y @cloudflare/mcp-server-cloudflare",
               package="@cloudflare/mcp-server-cloudflare", category="api-key-required"),
    ServerSpec("firecrawl", "npx -y firecrawl-mcp",
               package="firecrawl-mcp", category="api-key-required"),
]


def _field_count(tools: list[dict[str, Any]]) -> int:
    """Count total inputSchema fields across all tools."""
    total = 0
    for t in tools:
        schema = t.get("inputSchema") or t.get("input_schema") or {}
        if isinstance(schema, str):
            import json as _json
            try:
                schema = _json.loads(schema)
            except (ValueError, _json.JSONDecodeError):
                schema = {}
        total += len(schema.get("properties", {}))
    return total


Scope = Literal["curated", "registry", "full"]


class Indexer:
    """Orchestrates the coherence indexing pipeline.

    Designed for incremental, idempotent runs. Each method can be called
    independently or chained via ``run()``.

    Scopes:
      - curated:  KNOWN_SERVERS only (~26 servers)
      - registry: curated + schemas repo + registry crawl (~200)
      - full:     registry + deep registry crawl (~500+)
    """

    def __init__(
        self,
        data_dir: str | Path = "calibration/data/index",
        *,
        scan_timeout: float = 20.0,
        extra_servers: list[ServerSpec] | None = None,
        scope: Scope = "curated",
    ) -> None:
        self.data_dir = Path(data_dir)
        self.scan_timeout = scan_timeout
        self.extra_servers = extra_servers or []
        self.scope: Scope = scope
        # Stores compute results for receipt generation (populated by compute())
        self._compute_results: list[Any] = []

        # Lazy imports to avoid circular dependencies
        from calibration.corpus import ManifestStore
        self.store = ManifestStore(data_dir=self.data_dir)

    def _real_schema_servers(self) -> dict[str, list[dict[str, Any]]]:
        """Return {name: tools} for servers with >= MIN_SCHEMA_FIELDS input fields."""
        return {
            name: tools
            for name in self.store.list_servers()
            if _field_count(tools := self.store.get_tools(name)) >= MIN_SCHEMA_FIELDS
        }

    # ── Phase 0: Collect ─────────────────────────────────────────────

    def collect(self) -> int:
        """Collect manifests from corpus sources based on scope.

        Idempotent: skips servers already in the store.
        Returns count of newly added servers.
        """
        from calibration.corpus import (
            crawl_registry,
            import_from_schemas_repo,
            scan_from_registry,
        )

        before = len(self.store.list_servers())

        # All scopes include the curated scan (Phase 1)
        # — that's handled by scan() below, not here.
        # collect() handles the *additional* corpus sources.

        if self.scope in ("registry", "full"):
            logger.info("=== Collect: schemas repo ===")
            import_from_schemas_repo(self.store)

            logger.info("=== Collect: registry crawl ===")
            max_pages = 5 if self.scope == "registry" else 50
            max_servers = 200 if self.scope == "registry" else 2000
            registry_entries = crawl_registry(max_pages=max_pages)
            scan_from_registry(
                self.store, registry_entries,
                max_servers=max_servers, timeout=self.scan_timeout,
            )

        after = len(self.store.list_servers())
        added = after - before
        logger.info("Collect complete: %d new servers (scope=%s)", added, self.scope)
        return added

    # ── Phase 1: Scan ────────────────────────────────────────────────

    def scan(self) -> tuple[int, int, int]:
        """Scan all known servers. Returns (scanned, failed, skipped)."""
        from bulla.scan import ScanError, scan_mcp_server

        extra_names = {s.name for s in self.extra_servers}
        all_servers = KNOWN_SERVERS + self.extra_servers
        scanned = 0
        failed = 0
        skipped = 0

        for spec in all_servers:
            if spec.name in self.store.list_servers():
                if spec.name in extra_names:
                    logger.warning(
                        "SKIP %s: extra server collides with existing store entry",
                        spec.name,
                    )
                skipped += 1
                continue

            logger.info("SCAN %s: %s", spec.name, spec.command[:60])
            try:
                tools = scan_mcp_server(spec.command, timeout=self.scan_timeout)
                if not tools:
                    logger.debug("  EMPTY: %s returned 0 tools", spec.name)
                    failed += 1
                    continue

                n_fields = _field_count(tools)
                self.store.add(
                    spec.name,
                    tools,
                    package=spec.package,
                    captured_via=f"index_scan:{spec.command}",
                    category=spec.category,
                )
                scanned += 1
                logger.info("  OK: %d tools, %d fields", len(tools), n_fields)

            except ScanError as e:
                logger.debug("  FAIL: %s — %s", spec.name, str(e)[:80])
                failed += 1
            except Exception as e:
                logger.debug("  ERROR: %s — %s", spec.name, str(e)[:80])
                failed += 1

        return scanned, failed, skipped

    # ── Phase 2: Compute ─────────────────────────────────────────────

    def compute(self) -> int:
        """Compute pairwise fees for all real-schema servers. Returns count.

        Stores ComputeResult objects (with kernel_composition and
        kernel_diagnostic) in self._compute_results for receipt generation.
        """
        from calibration.compute import CoherenceDB, diagnose_pair

        db = CoherenceDB(self.data_dir / "coherence.db")
        self._compute_results = []

        real_servers = self._real_schema_servers()

        if len(real_servers) < 2:
            logger.info("Need ≥2 real-schema servers for pairwise computation")
            db.close()
            return 0

        computed = 0
        for a, b in itertools.combinations(sorted(real_servers.keys()), 2):
            try:
                meta_a = self.store.get_metadata(a)
                meta_b = self.store.get_metadata(b)
                result = diagnose_pair(
                    a, real_servers[a], b, real_servers[b],
                    category_a=meta_a.get("category", ""),
                    category_b=meta_b.get("category", ""),
                )
                db.store_result(result)
                self._compute_results.append(result)
                computed += 1
            except Exception as e:
                logger.debug("Failed %s+%s: %s", a, b, e)

        db.close()
        return computed

    # ── Phase 3: Discover (optional, requires LLM) ───────────────────

    def discover(
        self,
        *,
        provider: str = "auto",
        adapter: Any | None = None,
    ) -> int:
        """Run LLM discovery on the corpus. Returns count of new dimensions."""
        from bulla.discover.engine import discover_dimensions

        if adapter is None:
            from bulla.discover.adapter import get_adapter
            adapter = get_adapter(provider=provider)

        all_tools: list[dict[str, Any]] = []
        for name, tools in self._real_schema_servers().items():
            for t in tools:
                prefixed = dict(t)
                prefixed["name"] = f"{name}__{t.get('name', '')}"
                all_tools.append(prefixed)

        if not all_tools:
            logger.info("No real-schema tools to discover from")
            return 0

        logger.info("Running discovery on %d tools", len(all_tools))
        result = discover_dimensions(all_tools, adapter=adapter, existing_packs=[])

        if result.valid and result.n_dimensions > 0:
            import yaml
            output_path = self.data_dir / "discovered_pack.yaml"
            output_path.write_text(
                yaml.dump(result.pack, default_flow_style=False, sort_keys=False)
            )
            logger.info("Discovered %d dimensions → %s", result.n_dimensions, output_path)
            return result.n_dimensions
        else:
            logger.info("Discovery produced no new dimensions")
            return 0

    # ── Phase 4: Report ──────────────────────────────────────────────

    def report(self) -> Path | None:
        """Generate the State of Agent Coherence report. Returns path or None."""
        db_path = self.data_dir / "coherence.db"
        if not db_path.exists():
            logger.info("No coherence database — run compute first")
            return None

        from calibration.analyze import analyze
        from calibration.report import generate

        results = analyze(db_path)
        report_dir = self.data_dir / "report"
        md_path = generate(db_path, results, output_dir=report_dir, format="md")
        generate(db_path, results, output_dir=report_dir, format="json")
        return md_path

    # ── Phase 5: Receipts ──────────────────────────────────────────

    def receipts(self) -> int:
        """Generate WitnessReceipts for all computed compositions.

        Requires compute() to have been called first (populates
        self._compute_results with kernel objects).

        Writes individual receipt JSON files and a receipts/index.json
        that serves as the compatibility database seed.

        Returns count of receipts generated.
        """
        if not self._compute_results:
            logger.info("No compute results — run compute() first")
            return 0

        from bulla.witness import witness

        receipts_dir = self.data_dir / "receipts"
        receipts_dir.mkdir(parents=True, exist_ok=True)

        index_entries: list[dict[str, Any]] = []
        generated = 0

        for result in self._compute_results:
            comp = result.kernel_composition
            diag = result.kernel_diagnostic

            if comp is None or diag is None:
                logger.debug("Skipping %s: missing kernel objects", result.name)
                continue

            try:
                receipt = witness(diag, comp)
                receipt_dict = receipt.to_dict()

                # Write individual receipt
                receipt_path = receipts_dir / f"{result.comp_id[:16]}.json"
                receipt_path.write_text(json.dumps(receipt_dict, indent=2))

                # Build index entry
                index_entries.append({
                    "composition": result.name,
                    "servers": result.servers,
                    "composition_hash": result.comp_id,
                    "diagnostic_hash": result.diagnostic_hash,
                    "receipt_hash": receipt.receipt_hash,
                    "fee": result.coherence_fee,
                    "boundary_fee": result.boundary_fee,
                    "blind_spots": result.n_blind_spots,
                    "disposition": receipt.disposition.value,
                    "pair_type": result.pair_type,
                    "categories": result.categories,
                })
                generated += 1

            except Exception as e:
                logger.debug("Receipt failed for %s: %s", result.name, e)

        # Write the index — the compatibility database seed
        index_path = receipts_dir / "index.json"
        index_data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": self.scope,
            "count": generated,
            "receipts": sorted(
                index_entries,
                key=lambda r: (r["fee"], r["composition"]),
            ),
        }
        index_path.write_text(json.dumps(index_data, indent=2))
        logger.info("Receipts: %d generated → %s", generated, index_path)

        return generated

    # ── Full pipeline ────────────────────────────────────────────────

    def run(
        self,
        *,
        discover: bool = False,
        provider: str = "auto",
        adapter: Any | None = None,
        generate_receipts: bool = True,
    ) -> IndexResult:
        """Run the full indexing pipeline.

        Steps:
          0. Collect from corpus sources (scope-dependent)
          1. Scan all known servers (incremental — skips already-collected)
          2. Compute pairwise fees for real-schema servers
          3. Optionally run LLM discovery
          4. Generate report
          5. Generate witness receipts

        Returns an IndexResult with statistics.
        """
        result = IndexResult(
            started_at=datetime.now(timezone.utc).isoformat(),
            servers_before=len(self.store.list_servers()),
            scope=self.scope,
        )

        # Phase 0: Collect (scope-dependent corpus sources)
        if self.scope != "curated":
            logger.info("=== Phase 0: Collect (scope=%s) ===", self.scope)
            self.collect()

        # Phase 1: Scan
        logger.info("=== Phase 1: Scan ===")
        scanned, failed, skipped = self.scan()
        result.servers_scanned = scanned
        result.servers_failed = failed
        result.servers_skipped = skipped
        result.servers_after = len(self.store.list_servers())

        real = self._real_schema_servers()
        result.real_schema_servers = len(real)
        for name in self.store.list_servers():
            result.total_fields += _field_count(self.store.get_tools(name))

        logger.info("Corpus: %d servers (%d real-schema, %d total fields)",
                     result.servers_after, result.real_schema_servers, result.total_fields)

        # Phase 2: Compute
        logger.info("=== Phase 2: Compute ===")
        result.compositions_computed = self.compute()

        # Phase 3: Discover (optional)
        if discover:
            logger.info("=== Phase 3: Discover ===")
            result.dimensions_discovered = self.discover(
                provider=provider, adapter=adapter,
            )

        # Phase 4: Report
        logger.info("=== Phase 4: Report ===")
        report_path = self.report()
        if report_path:
            logger.info("Report: %s", report_path)

        # Phase 5: Receipts
        if generate_receipts:
            logger.info("=== Phase 5: Receipts ===")
            result.receipts_generated = self.receipts()

        result.finished_at = datetime.now(timezone.utc).isoformat()
        logger.info("\n%s", result.summary())

        # Write run metadata
        self._save_run_metadata(result)

        return result

    def _save_run_metadata(self, result: IndexResult) -> None:
        """Append run metadata to a JSONL log."""
        log_path = self.data_dir / "runs.jsonl"
        entry = {
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "scope": result.scope,
            "servers_before": result.servers_before,
            "servers_after": result.servers_after,
            "servers_scanned": result.servers_scanned,
            "servers_failed": result.servers_failed,
            "real_schema_servers": result.real_schema_servers,
            "total_fields": result.total_fields,
            "compositions_computed": result.compositions_computed,
            "receipts_generated": result.receipts_generated,
            "dimensions_discovered": result.dimensions_discovered,
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
