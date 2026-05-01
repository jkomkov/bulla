"""Phase 1: Corpus collection for the Coherence Index.

Collects MCP server manifests from three sources:
  1. Official MCP registry API (registry.modelcontextprotocol.io)
  2. Pre-collected schema repositories (oslook/mcp-servers-schemas)
  3. Local scanning of installable servers (npm/pip)

All manifests are stored in bulla provenance-tagged JSON format,
content-addressed for deduplication.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"
SCHEMAS_REPO_TARBALL = (
    "https://github.com/oslook/mcp-servers-schemas/archive/refs/heads/main.tar.gz"
)

# Tier 1: official reference servers that ship with Cursor / Claude Desktop
TIER_1_SERVERS: list[dict[str, str]] = [
    {"name": "filesystem", "package": "@modelcontextprotocol/server-filesystem",
     "command": "npx -y @modelcontextprotocol/server-filesystem /tmp"},
    {"name": "github", "package": "@modelcontextprotocol/server-github",
     "command": "npx -y @modelcontextprotocol/server-github"},
    {"name": "memory", "package": "@modelcontextprotocol/server-memory",
     "command": "npx -y @modelcontextprotocol/server-memory"},
    {"name": "fetch", "package": "@modelcontextprotocol/server-fetch",
     "command": "npx -y @modelcontextprotocol/server-fetch"},
    {"name": "puppeteer", "package": "@modelcontextprotocol/server-puppeteer",
     "command": "npx -y @modelcontextprotocol/server-puppeteer"},
    {"name": "git", "package": "@modelcontextprotocol/server-git",
     "command": "npx -y @modelcontextprotocol/server-git"},
    {"name": "postgres", "package": "@modelcontextprotocol/server-postgres",
     "command": "npx -y @modelcontextprotocol/server-postgres postgresql://localhost/test"},
    {"name": "sqlite", "package": "@modelcontextprotocol/server-sqlite",
     "command": "npx -y @modelcontextprotocol/server-sqlite /tmp/test.db"},
    {"name": "brave-search", "package": "@modelcontextprotocol/server-brave-search",
     "command": "npx -y @modelcontextprotocol/server-brave-search"},
    {"name": "google-maps", "package": "@modelcontextprotocol/server-google-maps",
     "command": "npx -y @modelcontextprotocol/server-google-maps"},
    {"name": "slack", "package": "@modelcontextprotocol/server-slack",
     "command": "npx -y @modelcontextprotocol/server-slack"},
    {"name": "sequential-thinking", "package": "@modelcontextprotocol/server-sequential-thinking",
     "command": "npx -y @modelcontextprotocol/server-sequential-thinking"},
]


def _content_hash(tools: list[dict[str, Any]]) -> str:
    """SHA-256 of the canonical JSON representation of a tools array."""
    canonical = json.dumps(tools, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ManifestEntry:
    """Metadata for a collected manifest."""

    name: str
    package: str
    content_hash: str
    n_tools: int
    captured_via: str
    capture_date: str
    category: str = ""
    popularity_rank: int = 0


@dataclass
class ManifestStore:
    """Content-addressed manifest storage.

    Each server produces one ``{name}.json`` file in ``manifests_dir``.
    An ``index.json`` tracks metadata for all collected servers.
    """

    data_dir: Path
    _index: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.manifests_dir = self.data_dir / "manifests"
        self.manifests_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.data_dir / "index.json"
        if self._index_path.exists():
            self._index = json.loads(self._index_path.read_text())

    def add(
        self,
        name: str,
        tools: list[dict[str, Any]],
        *,
        package: str = "",
        captured_via: str = "",
        category: str = "",
        popularity_rank: int = 0,
    ) -> ManifestEntry:
        """Add a manifest to the store. Idempotent (deduplicates by content hash)."""
        chash = _content_hash(tools)

        manifest = {
            "_bulla_provenance": {
                "captured_via": captured_via,
                "server_package": package,
                "capture_date": _now_iso(),
                "bulla_version": _get_bulla_version(),
                "content_hash": f"sha256:{chash}",
                "category": category,
                "popularity_rank": popularity_rank,
            },
            "tools": tools,
        }

        path = self.manifests_dir / f"{name}.json"
        path.write_text(json.dumps(manifest, indent=2))

        entry = ManifestEntry(
            name=name,
            package=package,
            content_hash=chash,
            n_tools=len(tools),
            captured_via=captured_via,
            capture_date=manifest["_bulla_provenance"]["capture_date"],
            category=category,
            popularity_rank=popularity_rank,
        )
        self._index[name] = {
            "package": package,
            "content_hash": chash,
            "n_tools": len(tools),
            "captured_via": captured_via,
            "capture_date": entry.capture_date,
            "category": category,
            "popularity_rank": popularity_rank,
        }
        self._save_index()
        return entry

    def get_tools(self, name: str) -> list[dict[str, Any]]:
        """Load the tools array for a server."""
        path = self.manifests_dir / f"{name}.json"
        data = json.loads(path.read_text())
        return data["tools"]

    def list_servers(self) -> list[str]:
        return sorted(self._index.keys())

    def get_metadata(self, name: str) -> dict[str, Any]:
        """Return the index metadata dict for a server (or empty dict if unknown)."""
        return dict(self._index.get(name, {}))

    def stats(self) -> dict[str, int]:
        total_tools = sum(e["n_tools"] for e in self._index.values())
        return {
            "servers": len(self._index),
            "total_tools": total_tools,
        }

    def _save_index(self) -> None:
        self._index_path.write_text(json.dumps(self._index, indent=2, sort_keys=True))


def _get_bulla_version() -> str:
    try:
        from bulla import __version__
        return __version__
    except ImportError:
        return "unknown"


# ── Source 1: Registry API ───────────────────────────────────────────


@dataclass
class RegistryEntry:
    """A server entry from the MCP registry."""

    name: str
    package_name: str
    description: str
    repository: str
    install_command: str
    category: str = ""


def crawl_registry(
    *,
    max_pages: int = 50,
    page_size: int = 100,
) -> list[RegistryEntry]:
    """Crawl the official MCP registry and return server entries."""
    entries: list[RegistryEntry] = []
    cursor: str | None = None

    for page in range(max_pages):
        url = f"{REGISTRY_URL}?limit={page_size}"
        if cursor:
            url += f"&cursor={cursor}"

        logger.info("Registry page %d: %s", page + 1, url)
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", f"bulla-calibration/{_get_bulla_version()}")
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            logger.warning("Registry crawl stopped at page %d: %s", page + 1, e)
            break

        servers = data.get("servers", data.get("items", []))
        if not servers:
            break

        for s in servers:
            name = s.get("name", s.get("id", ""))
            if not name:
                continue
            entries.append(RegistryEntry(
                name=_sanitize_name(name),
                package_name=s.get("package_name", s.get("npm_package", name)),
                description=s.get("description", ""),
                repository=s.get("repository", s.get("repo", "")),
                install_command=_infer_install_command(s),
                category=s.get("category", ""),
            ))

        cursor = data.get("next_cursor", data.get("cursor"))
        if not cursor:
            break

    logger.info("Registry crawl complete: %d entries", len(entries))
    return entries


def _sanitize_name(name: str) -> str:
    """Convert a package/server name to a safe filesystem name."""
    return name.replace("/", "__").replace("@", "").replace(" ", "-").lower()


def _infer_install_command(entry: dict[str, Any]) -> str:
    """Best-effort install command from registry metadata."""
    if "install_command" in entry:
        return entry["install_command"]
    pkg = entry.get("package_name", entry.get("npm_package", ""))
    if pkg:
        return f"npx -y {pkg}"
    return ""


# ── Schema normalization ─────────────────────────────────────────────


def _normalize_tool_schemas(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize tool schema format for consistency.

    Handles:
    - input_schema (underscore) → inputSchema (camelCase)
    - JSON string schemas → parsed dicts
    """
    normalized = []
    for t in tools:
        t = dict(t)  # shallow copy
        # Normalize key name
        if "input_schema" in t and "inputSchema" not in t:
            schema = t.pop("input_schema")
            # Handle string-encoded schemas
            if isinstance(schema, str):
                try:
                    schema = json.loads(schema)
                except (json.JSONDecodeError, ValueError):
                    schema = {}
            t["inputSchema"] = schema
        normalized.append(t)
    return normalized


# ── Source 2: Schema repository ──────────────────────────────────────


def import_from_schemas_repo(
    store: ManifestStore,
    *,
    tarball_url: str = SCHEMAS_REPO_TARBALL,
) -> int:
    """Download and import manifests from oslook/mcp-servers-schemas."""
    count = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        tarball_path = Path(tmpdir) / "schemas.tar.gz"
        logger.info("Downloading schemas repo: %s", tarball_url)
        try:
            urllib.request.urlretrieve(tarball_url, tarball_path)
        except Exception as e:
            logger.warning("Failed to download schemas repo: %s", e)
            return 0

        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(tmpdir, filter="data")

        extracted = Path(tmpdir)
        for json_file in sorted(extracted.rglob("*.json")):
            try:
                data = json.loads(json_file.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            tools: list[dict[str, Any]] | None = None
            if isinstance(data, list) and data and "name" in data[0]:
                tools = data
            elif isinstance(data, dict) and "tools" in data:
                tools = data["tools"]

            if not tools:
                continue

            # Normalize input_schema → inputSchema (schemas repo uses underscore)
            tools = _normalize_tool_schemas(tools)

            name = _sanitize_name(json_file.stem)
            store.add(
                name,
                tools,
                package=json_file.stem,
                captured_via=f"schemas_repo:{tarball_url}",
            )
            count += 1
            logger.debug("Imported %s (%d tools)", name, len(tools))

    logger.info("Imported %d manifests from schemas repo", count)
    return count


# ── Source 3: Local scanning ─────────────────────────────────────────


def scan_tier1(store: ManifestStore) -> list[ManifestEntry]:
    """Scan Tier 1 official servers locally and add to store."""
    from bulla.scan import ScanError, scan_mcp_server

    entries: list[ManifestEntry] = []
    for server in TIER_1_SERVERS:
        name = server["name"]
        command = server["command"]
        logger.info("Scanning %s: %s", name, command)
        try:
            tools = scan_mcp_server(command, timeout=15.0)
        except ScanError as e:
            logger.warning("Failed to scan %s: %s", name, e)
            continue

        if not tools:
            logger.warning("No tools returned from %s", name)
            continue

        entry = store.add(
            name,
            tools,
            package=server["package"],
            captured_via=f"local_scan:{command}",
            category="official",
        )
        entries.append(entry)
        logger.info("  %s: %d tools (hash: %s)", name, len(tools), entry.content_hash[:12])

    return entries


def scan_from_registry(
    store: ManifestStore,
    registry_entries: list[RegistryEntry],
    *,
    max_servers: int = 200,
    timeout: float = 15.0,
) -> list[ManifestEntry]:
    """Scan registry-discovered servers locally and add to store."""
    from bulla.scan import ScanError, scan_mcp_server

    entries: list[ManifestEntry] = []
    attempted = 0

    for reg_entry in registry_entries:
        if attempted >= max_servers:
            break
        if not reg_entry.install_command:
            continue
        if reg_entry.name in store.list_servers():
            continue

        attempted += 1
        logger.info("Scanning [%d/%d] %s", attempted, max_servers, reg_entry.name)
        try:
            tools = scan_mcp_server(reg_entry.install_command, timeout=timeout)
        except ScanError as e:
            logger.debug("Failed: %s — %s", reg_entry.name, e)
            continue
        except Exception as e:
            logger.debug("Unexpected error: %s — %s", reg_entry.name, e)
            continue

        if not tools:
            continue

        entry = store.add(
            reg_entry.name,
            tools,
            package=reg_entry.package_name,
            captured_via=f"registry_scan:{reg_entry.install_command}",
            category=reg_entry.category,
            popularity_rank=attempted,
        )
        entries.append(entry)
        logger.info("  %s: %d tools", reg_entry.name, len(tools))

    return entries


# ── Pre-captured manifests import ────────────────────────────────────


def import_from_directory(
    store: ManifestStore,
    manifests_dir: Path,
) -> int:
    """Import pre-captured manifest JSON files (e.g., from examples/real_world_audit)."""
    count = 0
    for json_file in sorted(manifests_dir.glob("*.json")):
        try:
            data = json.loads(json_file.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        if isinstance(data, dict) and "tools" in data:
            tools = data["tools"]
            prov = data.get("_bulla_provenance", {})
        elif isinstance(data, list):
            tools = data
            prov = {}
        else:
            continue

        if not tools:
            continue

        tools = _normalize_tool_schemas(tools)

        name = _sanitize_name(json_file.stem)
        store.add(
            name,
            tools,
            package=prov.get("server_package", json_file.stem),
            captured_via=f"import:{json_file}",
            category=prov.get("category", ""),
        )
        count += 1

    logger.info("Imported %d manifests from %s", count, manifests_dir)
    return count


# ── Top-level collection entrypoint ──────────────────────────────────


def collect(
    *,
    tier: int = 1,
    output_dir: str | Path = "calibration/data",
    scan_local: bool = True,
    import_existing: Path | None = None,
) -> ManifestStore:
    """Run the corpus collection pipeline.

    Args:
        tier: 1 = official servers only (~12), 2 = + popular (~200),
              3 = full registry crawl (~500+)
        output_dir: Where to store manifests and index.
        scan_local: If True, actually start servers locally to capture
                    tools/list. If False, only use pre-collected sources.
        import_existing: Optional path to a directory of pre-captured
                         manifest JSON files to import.
    """
    store = ManifestStore(data_dir=Path(output_dir))

    if import_existing:
        import_from_directory(store, import_existing)

    if scan_local and tier >= 1:
        logger.info("=== Tier 1: Official servers ===")
        scan_tier1(store)

    if tier >= 2:
        logger.info("=== Tier 2: Popular servers (registry + schemas repo) ===")
        import_from_schemas_repo(store)
        if scan_local:
            registry_entries = crawl_registry(max_pages=5)
            scan_from_registry(store, registry_entries, max_servers=200)

    if tier >= 3:
        logger.info("=== Tier 3: Full registry crawl ===")
        if scan_local:
            registry_entries = crawl_registry(max_pages=50)
            scan_from_registry(store, registry_entries, max_servers=2000)

    stats = store.stats()
    logger.info(
        "Corpus complete: %d servers, %d total tools",
        stats["servers"],
        stats["total_tools"],
    )
    return store
