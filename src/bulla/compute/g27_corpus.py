"""G27 corpus builder using LiveSession + perturbations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any

from bulla.compute.perturbations import perturb_tools_with_stats
from bulla.live import LiveSession


@dataclass(frozen=True)
class CorpusRow:
    composition_id: str
    servers: tuple[str, ...]
    perturbation_seed: str
    coherence_fee: int
    n_tools: int
    n_edges: int
    tools_perturbed: int
    schema_properties_before: int
    schema_properties_after: int
    schema_properties_hidden: int
    required_fields_before: int
    required_fields_after: int
    required_fields_hidden: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "composition_id": self.composition_id,
            "servers": list(self.servers),
            "perturbation_seed": self.perturbation_seed,
            "coherence_fee": self.coherence_fee,
            "n_tools": self.n_tools,
            "n_edges": self.n_edges,
            "tools_perturbed": self.tools_perturbed,
            "schema_properties_before": self.schema_properties_before,
            "schema_properties_after": self.schema_properties_after,
            "schema_properties_hidden": self.schema_properties_hidden,
            "required_fields_before": self.required_fields_before,
            "required_fields_after": self.required_fields_after,
            "required_fields_hidden": self.required_fields_hidden,
            "source": self.source,
            "r_proxy": self.coherence_fee,
        }


@dataclass(frozen=True)
class CorpusBuildMetadata:
    manifests_dir: str
    manifest_files: tuple[str, ...]
    manifest_bundle_sha256: str
    target_size: int
    seeds_per_combo: int
    combo_sizes: tuple[int, ...]
    total_rows: int
    high_r_count: int
    min_high_r_count: int
    fast_fail_triggered: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifests_dir": self.manifests_dir,
            "manifest_files": list(self.manifest_files),
            "manifest_bundle_sha256": self.manifest_bundle_sha256,
            "target_size": self.target_size,
            "seeds_per_combo": self.seeds_per_combo,
            "combo_sizes": list(self.combo_sizes),
            "total_rows": self.total_rows,
            "high_r_count": self.high_r_count,
            "min_high_r_count": self.min_high_r_count,
            "fast_fail_triggered": self.fast_fail_triggered,
        }


def _load_manifest_tools(manifests_dir: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for file in sorted(manifests_dir.glob("*.json")):
        if file.name.startswith("."):
            continue
        try:
            payload = json.loads(file.read_text())
        except json.JSONDecodeError:
            continue
        tools = payload.get("tools")
        if isinstance(tools, list) and tools:
            out[file.stem] = tools
    return out


def _manifest_bundle_sha256(manifests_dir: Path) -> tuple[str, tuple[str, ...]]:
    digest = hashlib.sha256()
    included: list[str] = []
    for file in sorted(manifests_dir.glob("*.json")):
        if file.name.startswith("."):
            continue
        included.append(file.name)
        digest.update(file.name.encode())
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), tuple(included)


def evaluate_fast_fail_gate(
    rows: list[CorpusRow],
    *,
    min_high_r_count: int = 30,
) -> dict[str, Any]:
    high_r_count = sum(1 for row in rows if row.coherence_fee >= 2)
    passed = high_r_count >= min_high_r_count
    return {
        "min_high_r_count": min_high_r_count,
        "high_r_count": high_r_count,
        "pass": passed,
        "status": "ready_for_sweep" if passed else "deferred_underpowered",
        "defer_tags": [] if passed else ["deferred", "underpowered", "insufficient_high_obstruction"],
    }


def build_corpus_with_metadata(
    manifests_dir: Path,
    *,
    target_size: int = 120,
    seeds_per_combo: int = 4,
    combo_sizes: tuple[int, ...] = (2, 3, 4),
    min_high_r_count: int = 30,
) -> tuple[list[CorpusRow], CorpusBuildMetadata]:
    """Build a corpus by composing server subsets with deterministic perturbations."""
    server_tools = _load_manifest_tools(manifests_dir)
    servers = sorted(server_tools.keys())
    if len(servers) < 2:
        raise ValueError(
            f"Need >=2 manifests with tools in {manifests_dir}; got {len(servers)}"
        )

    manifest_hash, manifest_files = _manifest_bundle_sha256(manifests_dir)
    rows: list[CorpusRow] = []
    for k in combo_sizes:
        for combo in itertools.combinations(servers, min(k, len(servers))):
            for seed_idx in range(seeds_per_combo):
                seed = f"g27-{k}-{seed_idx}"
                live = LiveSession(name=f"g27_{'_'.join(combo)}_{seed_idx}")
                total_source_tools = 0
                aggregate_stats = {
                    "tools_perturbed": 0,
                    "schema_properties_before": 0,
                    "schema_properties_after": 0,
                    "schema_properties_hidden": 0,
                    "required_fields_before": 0,
                    "required_fields_after": 0,
                    "required_fields_hidden": 0,
                }
                for srv in combo:
                    tools, stats = perturb_tools_with_stats(server_tools[srv], seed=f"{seed}:{srv}")
                    total_source_tools += len(tools)
                    aggregate_stats["tools_perturbed"] += stats.tools_perturbed
                    aggregate_stats["schema_properties_before"] += stats.properties_before
                    aggregate_stats["schema_properties_after"] += stats.properties_after
                    aggregate_stats["schema_properties_hidden"] += stats.properties_hidden
                    aggregate_stats["required_fields_before"] += stats.required_before
                    aggregate_stats["required_fields_after"] += stats.required_after
                    aggregate_stats["required_fields_hidden"] += stats.required_hidden
                    live.add_server(srv, tools)

                comp = live.composition
                cid = f"{comp.canonical_hash()[:16]}-{seed}"
                rows.append(
                    CorpusRow(
                        composition_id=cid,
                        servers=tuple(combo),
                        perturbation_seed=seed,
                        coherence_fee=live.fee,
                        n_tools=len(comp.tools) if comp is not None else total_source_tools,
                        n_edges=len(comp.edges) if comp is not None else 0,
                        tools_perturbed=aggregate_stats["tools_perturbed"],
                        schema_properties_before=aggregate_stats["schema_properties_before"],
                        schema_properties_after=aggregate_stats["schema_properties_after"],
                        schema_properties_hidden=aggregate_stats["schema_properties_hidden"],
                        required_fields_before=aggregate_stats["required_fields_before"],
                        required_fields_after=aggregate_stats["required_fields_after"],
                        required_fields_hidden=aggregate_stats["required_fields_hidden"],
                        source="real_mcp_manifests",
                    )
                )
                if len(rows) >= target_size:
                    gate = evaluate_fast_fail_gate(rows, min_high_r_count=min_high_r_count)
                    metadata = CorpusBuildMetadata(
                        manifests_dir=str(manifests_dir),
                        manifest_files=manifest_files,
                        manifest_bundle_sha256=manifest_hash,
                        target_size=target_size,
                        seeds_per_combo=seeds_per_combo,
                        combo_sizes=combo_sizes,
                        total_rows=len(rows),
                        high_r_count=int(gate["high_r_count"]),
                        min_high_r_count=min_high_r_count,
                        fast_fail_triggered=not bool(gate["pass"]),
                    )
                    return rows, metadata
    gate = evaluate_fast_fail_gate(rows, min_high_r_count=min_high_r_count)
    metadata = CorpusBuildMetadata(
        manifests_dir=str(manifests_dir),
        manifest_files=manifest_files,
        manifest_bundle_sha256=manifest_hash,
        target_size=target_size,
        seeds_per_combo=seeds_per_combo,
        combo_sizes=combo_sizes,
        total_rows=len(rows),
        high_r_count=int(gate["high_r_count"]),
        min_high_r_count=min_high_r_count,
        fast_fail_triggered=not bool(gate["pass"]),
    )
    return rows, metadata


def build_corpus(
    manifests_dir: Path,
    *,
    target_size: int = 120,
    seeds_per_combo: int = 4,
    combo_sizes: tuple[int, ...] = (2, 3, 4),
) -> list[CorpusRow]:
    """Backward-compatible corpus build API returning rows only."""
    rows, _ = build_corpus_with_metadata(
        manifests_dir,
        target_size=target_size,
        seeds_per_combo=seeds_per_combo,
        combo_sizes=combo_sizes,
    )
    return rows


def write_corpus(rows: list[CorpusRow], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [row.to_dict() for row in rows]
    out_path.write_text(json.dumps(payload, indent=2) + "\n")


def write_corpus_bundle(
    rows: list[CorpusRow],
    metadata: CorpusBuildMetadata,
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "metadata": metadata.to_dict(),
        "fast_fail_gate": evaluate_fast_fail_gate(rows, min_high_r_count=metadata.min_high_r_count),
        "rows": [row.to_dict() for row in rows],
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n")

