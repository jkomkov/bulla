"""Thresholded convention-filtration of H^1 for Bulla compositions (Sprint G19).

Four filtration backends:
  - "lcp": longest-common-prefix similarity on dimension names (PRIMARY under Path B)
  - "jaccard": Jaccard token-set similarity on dimension names (SECONDARY)
  - "pack_tag": pack-membership on underlying fields (REFERENCE; binary at eps in {0, 1})
  - "alignment": per-edge restriction-map quality scores (G23 cross-model; see
    compute_alignment_h1 / compute_alignment_barcode)

For each filtration and threshold eps, the module:
  1. Determines eps-equivalence classes of dimension names.
  2. Derives field-equivalence classes (dimensions sharing an equivalence class
     also identify their referenced fields, since "merged convention" means
     "merged underlying field").
  3. Constructs a mutated Composition with canonical merged names of the form
     `_merged_<sha8>` (8 hex chars of sha256 over the sorted member tuple) -
     guaranteed-fresh, deterministic, replayable.
  4. Calls existing bulla.diagnostic.diagnose() on the mutation; extracts H^1.

The empirical question: does merging dimensions / fields collapse H^1
generators in predictable ways? G19 is a prototype, not a final tool.

Reuses bulla.parser, bulla.diagnostic, bulla.coboundary, bulla.model
without modification.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Callable, Literal

import yaml

from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec


FiltrationName = Literal["lcp", "jaccard", "pack_tag"]


# ── Result dataclass ────────────────────────────────────────────────


@dataclass(frozen=True)
class ThresholdedH1Result:
    """Result of computing H^1 at threshold eps under one filtration."""

    eps: float
    filtration: FiltrationName
    h1_obs: int
    h1_full: int
    coherence_fee: int
    n_distinct_dimensions: int  # distinct dim names after merging
    n_distinct_fields: int      # distinct field names after merging (across all tools)
    equivalence_classes: tuple[tuple[str, ...], ...]  # non-trivial dim classes (size > 1)
    canonical_names: dict[str, str]  # member dim name -> canonical merged name


# ── Similarity functions ────────────────────────────────────────────


def lcp_similarity(a: str, b: str) -> float:
    """Longest-common-prefix similarity = |LCP(a,b)| / max(len(a), len(b))."""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    common = 0
    while common < n and a[common] == b[common]:
        common += 1
    return common / max(len(a), len(b))


def jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity on token sets (split on '_')."""
    ta = set(a.split("_"))
    tb = set(b.split("_"))
    union = ta | tb
    if not union:
        return 1.0
    return len(ta & tb) / len(union)


# ── Pack-tag ontology loader ────────────────────────────────────────


@dataclass(frozen=True)
class PackTagOntology:
    """Loaded pack ontology mapping field-pattern -> 'pack_name.dim_name'.

    Pack files live at bulla/src/bulla/packs/*.yaml. Each pack defines
    dimensions with `field_patterns` (e.g. '*_encoding', 'page'). A field
    matches a pack-dimension if its name matches any of those patterns.
    Lookup is exact-name first, then suffix wildcard (*_xxx).
    """

    field_patterns: tuple[tuple[str, str], ...]  # ((pattern, "pack.dim"), ...)

    @classmethod
    def load(cls, pack_root: Path | None = None) -> "PackTagOntology":
        if pack_root is None:
            pack_root = Path(__file__).parent / "packs"
        out: list[tuple[str, str]] = []
        for pack_file in sorted(pack_root.glob("*.yaml")):
            with open(pack_file) as f:
                data = yaml.safe_load(f) or {}
            pack_name = data.get("pack_name", pack_file.stem)
            for dim_name, dim_spec in (data.get("dimensions") or {}).items():
                tag = f"{pack_name}.{dim_name}"
                for pattern in dim_spec.get("field_patterns") or []:
                    out.append((pattern, tag))
        return cls(field_patterns=tuple(out))

    def lookup(self, field_name: str) -> str | None:
        """Find the pack-dimension matching a field name, or None if app-specific.

        Exact match wins over wildcard. Wildcards are *_xxx suffix patterns
        (matching anything ending in _xxx). Returns 'pack.dim' or None.
        """
        for pattern, tag in self.field_patterns:
            if pattern == field_name:
                return tag
        for pattern, tag in self.field_patterns:
            if pattern.startswith("*_") and field_name.endswith(pattern[1:]):
                return tag
            if pattern.startswith("*") and not pattern.startswith("*_"):
                if field_name.endswith(pattern[1:]):
                    return tag
        return None


# ── Canonical naming (collision-safe) ───────────────────────────────


def canonical_merged_name(members: tuple[str, ...]) -> str:
    """Generate `_merged_<sha8>` for an equivalence class of members.

    Deterministic across runs; collision-resistant; the leading underscore
    plus 'merged_' prefix avoids clashing with any plausible original
    dimension or field name in Bulla compositions.
    """
    sorted_members = tuple(sorted(members))
    digest = hashlib.sha256(repr(sorted_members).encode()).hexdigest()[:8]
    return f"_merged_{digest}"


# ── Equivalence-class computation (transitive closure via union-find) ─


def _equivalence_classes(
    items: list[str], similarity: Callable[[str, str], float], eps: float
) -> list[frozenset[str]]:
    """Group items into eps-equivalence classes.

    Convention: eps is a DISSIMILARITY threshold (matching standard persistent
    homology). Two items are eps-equivalent if (1 - similarity) <= eps,
    equivalently similarity >= (1 - eps). This means:
      - eps = 0: only identical pairs (similarity = 1) merge -> no merging
      - eps = 1: any positive similarity merges -> maximum merging
      - Generators are BORN at eps = 0 (each dim distinct) and DIE as eps
        increases (merging happens at progressively looser tolerances).

    Uses transitive closure (union-find): if A~B and B~C, then A~C.
    """
    parent = {x: x for x in items}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    threshold = 1.0 - eps
    for a, b in combinations(items, 2):
        # Strict inequality: pairs with similarity = 0 (truly distinct names)
        # never merge regardless of eps. Pairs with similarity > 1 - eps merge.
        if similarity(a, b) > threshold:
            union(a, b)

    classes: dict[str, set[str]] = {}
    for x in items:
        classes.setdefault(find(x), set()).add(x)
    return [frozenset(c) for c in classes.values()]


# ── Composition mutation ────────────────────────────────────────────


def _build_field_rename(field_classes: list[frozenset[str]]) -> dict[str, str]:
    """Map each field in a non-trivial class to its canonical merged name."""
    out: dict[str, str] = {}
    for cls in field_classes:
        if len(cls) > 1:
            merged = canonical_merged_name(tuple(cls))
            for f in cls:
                out[f] = merged
    return out


def _build_dim_rename(dim_classes: list[frozenset[str]]) -> dict[str, str]:
    """Map each dim name in a non-trivial class to its canonical merged name."""
    out: dict[str, str] = {}
    for cls in dim_classes:
        if len(cls) > 1:
            merged = canonical_merged_name(tuple(cls))
            for d in cls:
                out[d] = merged
    return out


def _mutate_composition(
    comp: Composition,
    dim_rename: dict[str, str],
    field_rename: dict[str, str],
) -> Composition:
    """Construct a mutated Composition with merged dim names and merged fields.

    Tools: rename fields in internal_state and observable_schema, then dedupe.
    Edges: rename dimension names AND from_field/to_field, then dedupe duplicate
    dimensions on the same edge (multiple dims with same name post-merging
    collapse into one).
    """
    new_tools: list[ToolSpec] = []
    for t in comp.tools:
        new_internal: list[str] = []
        seen_i: set[str] = set()
        for f in t.internal_state:
            new_f = field_rename.get(f, f)
            if new_f not in seen_i:
                seen_i.add(new_f)
                new_internal.append(new_f)
        new_obs: list[str] = []
        seen_o: set[str] = set()
        for f in t.observable_schema:
            new_f = field_rename.get(f, f)
            if new_f not in seen_o:
                seen_o.add(new_f)
                new_obs.append(new_f)
        new_tools.append(
            ToolSpec(
                name=t.name,
                internal_state=tuple(new_internal),
                observable_schema=tuple(new_obs),
            )
        )

    new_edges: list[Edge] = []
    for e in comp.edges:
        new_dims: list[SemanticDimension] = []
        seen_dim: set[str] = set()
        for d in e.dimensions:
            new_name = dim_rename.get(d.name, d.name)
            if new_name in seen_dim:
                continue
            seen_dim.add(new_name)
            new_from = field_rename.get(d.from_field, d.from_field) if d.from_field else None
            new_to = field_rename.get(d.to_field, d.to_field) if d.to_field else None
            new_dims.append(
                SemanticDimension(
                    name=new_name, from_field=new_from, to_field=new_to
                )
            )
        new_edges.append(
            Edge(
                from_tool=e.from_tool,
                to_tool=e.to_tool,
                dimensions=tuple(new_dims),
            )
        )
    return Composition(
        name=comp.name, tools=tuple(new_tools), edges=tuple(new_edges)
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _all_dimension_names(comp: Composition) -> list[str]:
    seen: dict[str, None] = {}
    for e in comp.edges:
        for d in e.dimensions:
            if d.name not in seen:
                seen[d.name] = None
    return list(seen.keys())


def _all_fields(comp: Composition) -> list[str]:
    seen: dict[str, None] = {}
    for t in comp.tools:
        for f in t.internal_state:
            if f not in seen:
                seen[f] = None
        for f in t.observable_schema:
            if f not in seen:
                seen[f] = None
    return list(seen.keys())


def _dim_to_fields(comp: Composition) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for e in comp.edges:
        for d in e.dimensions:
            s = out.setdefault(d.name, set())
            if d.from_field:
                s.add(d.from_field)
            if d.to_field:
                s.add(d.to_field)
    return out


def _field_classes_from_dim_classes(
    dim_classes: list[frozenset[str]],
    dim_to_fields: dict[str, set[str]],
    all_fields: list[str],
) -> list[frozenset[str]]:
    """Derive field equivalence classes from dim equivalence classes.

    Dimensions in the same class identify their referenced fields. Fields not
    touched by any non-trivial dim class remain singleton classes.
    """
    field_to_class: dict[str, frozenset[str]] = {}
    classes: list[frozenset[str]] = []
    for dcls in dim_classes:
        if len(dcls) <= 1:
            continue
        members: set[str] = set()
        for d in dcls:
            members.update(dim_to_fields.get(d, set()))
        if len(members) > 1:
            cls = frozenset(members)
            classes.append(cls)
            for f in members:
                field_to_class[f] = cls
    for f in all_fields:
        if f not in field_to_class:
            classes.append(frozenset([f]))
    return classes


# ── Public API ──────────────────────────────────────────────────────


def compute_thresholded_h1(
    composition: Composition,
    eps: float,
    *,
    filtration: FiltrationName = "lcp",
    pack_ontology: PackTagOntology | None = None,
) -> ThresholdedH1Result:
    """Compute H^1 at threshold eps under the given filtration.

    Strategy: determine equivalence classes (dim names for lcp/jaccard,
    fields for pack_tag), rewrite the composition with canonical merged
    names, run existing diagnose() on the mutation.
    """
    all_dims = _all_dimension_names(composition)
    all_fields = _all_fields(composition)
    d2f = _dim_to_fields(composition)

    if filtration == "lcp":
        dim_classes = _equivalence_classes(all_dims, lcp_similarity, eps)
        field_classes = _field_classes_from_dim_classes(dim_classes, d2f, all_fields)
    elif filtration == "jaccard":
        dim_classes = _equivalence_classes(all_dims, jaccard_similarity, eps)
        field_classes = _field_classes_from_dim_classes(dim_classes, d2f, all_fields)
    elif filtration == "pack_tag":
        if pack_ontology is None:
            pack_ontology = PackTagOntology.load()
        # Pack-tag is binary in Path B: at eps >= 1.0, fields with the same
        # pack-dim merge; at eps < 1.0, no merging at all.
        if eps >= 1.0:
            tag_to_fields: dict[str, set[str]] = {}
            for f in all_fields:
                tag = pack_ontology.lookup(f)
                if tag is not None:
                    tag_to_fields.setdefault(tag, set()).add(f)
            field_classes = []
            clustered: set[str] = set()
            for tag, fs in tag_to_fields.items():
                if len(fs) > 1:
                    field_classes.append(frozenset(fs))
                    clustered.update(fs)
            for f in all_fields:
                if f not in clustered:
                    field_classes.append(frozenset([f]))
            # Dim classes: derive from CHANGES in merged-field-sets caused by pack-merging.
            # If a dim's merged-field-set differs from its original-field-set, it was affected
            # by pack-merging. Group affected dims by their new merged-field-set.
            field_rename_for_grouping = _build_field_rename(field_classes)
            from collections import defaultdict
            groups: dict[frozenset[str], set[str]] = defaultdict(set)
            unchanged: list[str] = []
            for d, fs in d2f.items():
                merged_fs = frozenset(field_rename_for_grouping.get(f, f) for f in fs)
                if merged_fs == frozenset(fs):
                    unchanged.append(d)  # not affected by pack-merging
                else:
                    groups[merged_fs].add(d)
            dim_classes = [frozenset(ds) for ds in groups.values() if len(ds) > 1]
            # Singletons: unchanged dims plus changed dims whose group is alone
            for ds in groups.values():
                if len(ds) == 1:
                    dim_classes.append(frozenset(ds))
            for d in unchanged:
                dim_classes.append(frozenset([d]))
            # Defensive: ensure all dims are accounted for
            seen_dims: set[str] = set()
            for c in dim_classes:
                seen_dims.update(c)
            for d in all_dims:
                if d not in seen_dims:
                    dim_classes.append(frozenset([d]))
        else:
            # eps < 1.0: no merging
            field_classes = [frozenset([f]) for f in all_fields]
            dim_classes = [frozenset([d]) for d in all_dims]
    else:
        raise ValueError(f"Unknown filtration: {filtration}")

    dim_rename = _build_dim_rename(dim_classes)
    field_rename = _build_field_rename(field_classes)
    mutated = _mutate_composition(composition, dim_rename, field_rename)
    diag = diagnose(mutated)

    n_distinct_dims_after = len({d.name for e in mutated.edges for d in e.dimensions})
    n_distinct_fields_after = len(
        {f for t in mutated.tools for f in t.internal_state}
        | {f for t in mutated.tools for f in t.observable_schema}
    )

    nontrivial_classes = tuple(
        tuple(sorted(c)) for c in dim_classes if len(c) > 1
    )
    canonical = {
        m: dim_rename[m] for m in dim_rename
    }

    return ThresholdedH1Result(
        eps=eps,
        filtration=filtration,
        h1_obs=diag.h1_obs,
        h1_full=diag.h1_full,
        coherence_fee=diag.coherence_fee,
        n_distinct_dimensions=n_distinct_dims_after,
        n_distinct_fields=n_distinct_fields_after,
        equivalence_classes=nontrivial_classes,
        canonical_names=canonical,
    )


# ── Barcode construction (G19.2) ────────────────────────────────────


@dataclass(frozen=True)
class Bar:
    """A single barcode interval: a dimension's lifespan in epsilon.

    multiplicity is the number of (edge, dim) pairs in C^1 that share this
    dim name. For seeds where a single dim name appears on many edges
    (e.g., filesystem+github has 99 edges all carrying path_convention_match),
    multiplicity is large and the bar represents the aggregate contribution
    of all those edge-dim pairs. total_persistence sums multiplicity *
    (death - birth) to approximate the true H^1-weighted persistence.
    """

    generator_id: str  # canonical merged name OR original dim name (if unmerged)
    birth_eps: float
    death_eps: float  # 1.0 = end-of-sweep (still alive at last step)
    contributing_dims: tuple[str, ...]  # original dim names that comprise this generator
    multiplicity: int = 1  # number of (edge, dim) pairs sharing this dim name


def _eps_grid(step: float = 0.05) -> list[float]:
    """Return [0.00, 0.05, 0.10, ..., 1.00], 21 points by default."""
    n = round(1.0 / step) + 1
    return [round(i * step, 4) for i in range(n)]


def compute_barcode(
    composition: Composition,
    *,
    filtration: FiltrationName = "lcp",
    eps_step: float = 0.05,
    pack_ontology: PackTagOntology | None = None,
) -> list[Bar]:
    """Sweep eps from 0 to 1 and track each generator's birth-death interval.

    Generator identity: at eps=0 each dimension is its own generator. As eps
    increases and equivalence classes grow, the generator with the smaller
    (alphabetically) original-dim member name is considered the "survivor"
    and the absorbed members get death_eps = current_eps. The survivor's
    canonical_id is the canonical merged name once it's part of a non-trivial
    class; before that, it's the original dim name.

    Returns a list of Bar tuples sorted by (birth_eps, generator_id).
    """
    grid = _eps_grid(eps_step)
    if filtration == "pack_tag" and pack_ontology is None:
        pack_ontology = PackTagOntology.load()

    # At eps=0 every dim is its own generator (alive)
    all_dims = _all_dimension_names(composition)
    # Map original dim -> (birth_eps, alive flag, current_canonical_id, current_class_members)
    alive: dict[str, dict] = {
        d: {
            "birth_eps": 0.0,
            "death_eps": None,  # None = still alive at end of sweep
            "canonical_id": d,
            "class": frozenset({d}),
            "absorbed_into": None,
        }
        for d in all_dims
    }

    prev_class_of: dict[str, frozenset[str]] = {d: frozenset({d}) for d in all_dims}

    for eps in grid:
        result = compute_thresholded_h1(
            composition, eps, filtration=filtration, pack_ontology=pack_ontology
        )
        # Build dim -> equivalence_class map for this eps
        class_of: dict[str, frozenset[str]] = {d: frozenset({d}) for d in all_dims}
        for cls in result.equivalence_classes:
            cls_set = frozenset(cls)
            for d in cls:
                class_of[d] = cls_set

        # Detect newly-merged classes: classes that grew from previous step
        for cls in result.equivalence_classes:
            cls_set = frozenset(cls)
            members_alive = [m for m in cls if alive[m]["death_eps"] is None]
            if len(members_alive) <= 1:
                continue
            # Survivor: alphabetically smallest dim name still alive in the class
            survivor = sorted(members_alive)[0]
            absorbed = [m for m in members_alive if m != survivor]
            for m in absorbed:
                # Only record death if this is a NEW absorption (was not in same class before)
                if survivor not in prev_class_of[m]:
                    alive[m]["death_eps"] = eps
                    alive[m]["absorbed_into"] = survivor
            # Survivor takes on canonical_id from the merged-name map
            canonical = result.canonical_names.get(survivor, survivor)
            alive[survivor]["canonical_id"] = canonical
            alive[survivor]["class"] = cls_set

        prev_class_of = class_of

    # Compute multiplicity (count of edge-dim pairs per dim name) once
    multiplicity: dict[str, int] = {d: 0 for d in all_dims}
    for e in composition.edges:
        for dim in e.dimensions:
            multiplicity[dim.name] = multiplicity.get(dim.name, 0) + 1

    # Build Bars: one per ORIGINAL dim, with appropriate (birth, death) and multiplicity
    bars: list[Bar] = []
    for d in all_dims:
        info = alive[d]
        death = 1.0 if info["death_eps"] is None else info["death_eps"]
        bars.append(
            Bar(
                generator_id=info["canonical_id"],
                birth_eps=info["birth_eps"],
                death_eps=death,
                contributing_dims=(d,),
                multiplicity=multiplicity.get(d, 1),
            )
        )
    bars.sort(key=lambda b: (b.birth_eps, b.death_eps, b.generator_id))
    return bars


def total_persistence(bars: list[Bar]) -> float:
    """Sum of multiplicity * (death - birth) across all bars (criterion 4-prime input).

    Multiplicity weighting approximates the H^1-generator-weighted total
    persistence when a single dim name represents many (edge, dim) cocycles.
    """
    return sum(b.multiplicity * (b.death_eps - b.birth_eps) for b in bars)


# ── Bottleneck distance (Hopcroft-Karp matching, hand-coded G19.3) ──


def _bar_endpoint(b: Bar) -> tuple[float, float]:
    """The (birth, death) point for a barcode interval in the persistence plane."""
    return (b.birth_eps, b.death_eps)


def _linf_distance(p: tuple[float, float], q: tuple[float, float]) -> float:
    """L-infinity distance between two persistence-plane points."""
    return max(abs(p[0] - q[0]), abs(p[1] - q[1]))


def _diagonal_distance(p: tuple[float, float]) -> float:
    """L-infinity distance from a point to the diagonal y=x."""
    return abs(p[0] - p[1]) / 2.0


def bottleneck_distance(bars_a: list[Bar], bars_b: list[Bar]) -> float:
    """Bottleneck distance between two persistence diagrams.

    Standard definition: minimum over all matchings (including matchings to
    the diagonal) of the maximum L-infinity distance. We compute via binary
    search on the threshold delta + a feasibility check using bipartite
    matching (Hopcroft-Karp).

    For tiny diagrams (<=22 generators per side), this is well under a
    millisecond per call.
    """
    pts_a = [_bar_endpoint(b) for b in bars_a]
    pts_b = [_bar_endpoint(b) for b in bars_b]

    # Generate candidate thresholds: pairwise distances between points + diagonal distances
    candidates: set[float] = {0.0}
    for p in pts_a:
        candidates.add(_diagonal_distance(p))
        for q in pts_b:
            candidates.add(_linf_distance(p, q))
    for q in pts_b:
        candidates.add(_diagonal_distance(q))

    sorted_candidates = sorted(candidates)

    # Binary search for smallest threshold that admits a feasible matching
    lo, hi = 0, len(sorted_candidates) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if _is_feasible(pts_a, pts_b, sorted_candidates[mid]):
            hi = mid
        else:
            lo = mid + 1
    return sorted_candidates[lo]


def _is_feasible(
    pts_a: list[tuple[float, float]],
    pts_b: list[tuple[float, float]],
    delta: float,
) -> bool:
    """Check whether a delta-bottleneck matching exists.

    Each point in A must match either to a point in B (within delta) or to
    its diagonal projection (if diagonal_distance <= delta). Same for B.
    """
    # Build bipartite graph with augmented nodes for diagonals
    n_a = len(pts_a)
    n_b = len(pts_b)
    # Adjacency: a node in A can connect to b nodes in B (via _linf <= delta)
    # plus its own "diagonal slot" (if diagonal_distance(p_a) <= delta).
    # B-side likewise has diagonal slots.
    # Total left nodes: n_a (real A points) + n_b (diagonal copies of B points,
    # which can absorb B's that don't match to a real A).
    # Total right nodes: n_b (real B points) + n_a (diagonal copies of A points).
    L = n_a + n_b
    R = n_b + n_a
    adj: list[list[int]] = [[] for _ in range(L)]
    for i, pa in enumerate(pts_a):
        for j, pb in enumerate(pts_b):
            if _linf_distance(pa, pb) <= delta:
                adj[i].append(j)
        # A's diagonal slot is at right index n_b + i
        if _diagonal_distance(pa) <= delta:
            adj[i].append(n_b + i)
    for j, pb in enumerate(pts_b):
        # Diagonal copy of B (left index n_a + j) connects only to real B[j]
        # if pb's diagonal distance <= delta (so b can match its own diagonal)
        if _diagonal_distance(pb) <= delta:
            adj[n_a + j].append(j)
        # Diagonal copy of B can also "absorb" any A's diagonal slot (free match)
        # because both are diagonal points; no constraint needed.
        for i in range(n_a):
            adj[n_a + j].append(n_b + i)

    return _max_bipartite_matching(adj, L, R) == L


def _max_bipartite_matching(
    adj: list[list[int]], n_left: int, n_right: int
) -> int:
    """Hopcroft-Karp maximum bipartite matching. Returns matching size."""
    INF = float("inf")
    pair_u = [-1] * n_left
    pair_v = [-1] * n_right
    dist = [0.0] * n_left

    def bfs() -> bool:
        from collections import deque
        queue: "deque[int]" = deque()
        nonlocal_dist_nil = INF
        for u in range(n_left):
            if pair_u[u] == -1:
                dist[u] = 0
                queue.append(u)
            else:
                dist[u] = INF
        nonlocal_dist_nil = INF
        found = False
        while queue:
            u = queue.popleft()
            if dist[u] < nonlocal_dist_nil:
                for v in adj[u]:
                    pu = pair_v[v]
                    if pu == -1:
                        nonlocal_dist_nil = dist[u] + 1
                        found = True
                    elif dist[pu] == INF:
                        dist[pu] = dist[u] + 1
                        queue.append(pu)
        return found

    def dfs(u: int) -> bool:
        for v in adj[u]:
            pu = pair_v[v]
            if pu == -1 or (dist[pu] == dist[u] + 1 and dfs(pu)):
                pair_u[u] = v
                pair_v[v] = u
                return True
        dist[u] = INF
        return False

    result = 0
    while bfs():
        for u in range(n_left):
            if pair_u[u] == -1 and dfs(u):
                result += 1
    return result


# ── Alignment-quality filtration (G23 cross-model) ──────────────────


@dataclass(frozen=True)
class AlignmentH1Result:
    """Result of computing coherence_fee at threshold eps under alignment filtration.

    Unlike ThresholdedH1Result (which merges dimension names), this
    filtration REMOVES edges whose alignment quality exceeds the
    threshold. Edges represent cross-model feature pairings; removing
    a well-aligned edge says "this pairing is resolved."
    """

    eps: float
    fee: int  # coherence_fee at this threshold
    h1_obs: int
    h1_full: int
    n_edges_total: int
    n_edges_remaining: int


def compute_alignment_h1(
    composition: Composition,
    edge_qualities: dict[int, float],
    eps: float,
) -> AlignmentH1Result:
    """Compute coherence_fee at threshold eps under alignment-quality filtration.

    Convention matches _equivalence_classes: eps is a DISSIMILARITY threshold.
    An edge is removed when its alignment quality > (1 - eps), i.e., when its
    dissimilarity (1 - quality) < eps.

    At eps=0: quality must be > 1.0 (impossible) → no edges removed → max fee.
    At eps=1: quality must be > 0.0 → all non-zero-quality edges removed.

    For hidden-field cross-model compositions (rank_obs = 0), fee = rank_full
    and is monotonically non-increasing as eps increases (removing rows from
    the coboundary matrix can only decrease or maintain rank).

    Args:
        composition: the cross-model composition with hidden-field edges.
        edge_qualities: mapping from edge index (0-based, matching
            composition.edges order) to alignment quality in [0, 1].
            Missing edges default to quality 0.0 (never removed).
        eps: dissimilarity threshold in [0, 1].
    """
    threshold = 1.0 - eps
    new_edges = []
    for i, edge in enumerate(composition.edges):
        quality = edge_qualities.get(i, 0.0)
        if not (quality > threshold):  # strict inequality, matching convention
            new_edges.append(edge)

    filtered = Composition(
        name=composition.name,
        tools=composition.tools,
        edges=tuple(new_edges),
    )
    diag = diagnose(filtered)

    return AlignmentH1Result(
        eps=eps,
        fee=diag.coherence_fee,
        h1_obs=diag.h1_obs,
        h1_full=diag.h1_full,
        n_edges_total=len(composition.edges),
        n_edges_remaining=len(new_edges),
    )


def compute_alignment_barcode(
    composition: Composition,
    edge_qualities: dict[int, float],
    *,
    eps_step: float = 0.05,
) -> list[Bar]:
    """Sweep eps from 0→1 under alignment-quality filtration; produce barcode.

    All generators are born at eps=0 (no edges removed, max fee). A generator
    dies at the eps where fee decreases. The barcode encodes the fee trajectory:
    fee(eps) = number of alive bars at eps.

    For hidden-field compositions, fee is monotonically non-increasing, so
    all bars have birth=0 and death >= birth. Bars dying at eps=1.0 represent
    obstruction that survives even the loosest alignment threshold (persistent
    obstruction).

    The resulting bars are comparable via bottleneck_distance() to bars from
    a different map's alignment-quality filtration on the same composition
    topology. Non-trivial bottleneck distance ↔ feature-sensitive measurement.
    """
    grid = _eps_grid(eps_step)

    fee_trajectory: list[tuple[float, int]] = []
    for eps in grid:
        result = compute_alignment_h1(composition, edge_qualities, eps)
        fee_trajectory.append((eps, result.fee))

    bars: list[Bar] = []
    if not fee_trajectory:
        return bars

    prev_fee = fee_trajectory[0][1]
    bar_idx = 0

    for eps, fee in fee_trajectory:
        if fee < prev_fee:
            for _ in range(prev_fee - fee):
                bars.append(Bar(
                    generator_id=f"align_gen_{bar_idx}",
                    birth_eps=0.0,
                    death_eps=eps,
                    contributing_dims=(),
                    multiplicity=1,
                ))
                bar_idx += 1
        prev_fee = fee

    # Generators still alive at end of sweep (fee > 0 at eps=1.0)
    for _ in range(prev_fee):
        bars.append(Bar(
            generator_id=f"align_gen_{bar_idx}",
            birth_eps=0.0,
            death_eps=1.0,
            contributing_dims=(),
            multiplicity=1,
        ))
        bar_idx += 1

    bars.sort(key=lambda b: (b.birth_eps, b.death_eps, b.generator_id))
    return bars


# ── Stability check (G19.3) ─────────────────────────────────────────


def perturb_type1_rename(comp: Composition, dim_to_rename: str | None = None) -> Composition:
    """Type-1 perturbation: rename one convention to a similar-but-different name.

    Picks the first dimension name in lexicographic order if dim_to_rename is
    None. Appends '_renamed' to the chosen name (creates new distinct name).
    Returns a new Composition with the renamed dimension; does NOT mutate fields.
    """
    if dim_to_rename is None:
        dim_to_rename = sorted(_all_dimension_names(comp))[0]
    new_name = f"{dim_to_rename}_renamed"
    new_edges: list[Edge] = []
    for e in comp.edges:
        new_dims: list[SemanticDimension] = []
        for d in e.dimensions:
            if d.name == dim_to_rename:
                new_dims.append(SemanticDimension(
                    name=new_name, from_field=d.from_field, to_field=d.to_field,
                ))
            else:
                new_dims.append(d)
        new_edges.append(Edge(
            from_tool=e.from_tool, to_tool=e.to_tool, dimensions=tuple(new_dims),
        ))
    return Composition(name=comp.name, tools=comp.tools, edges=tuple(new_edges))


def perturb_type2_drop_edge(comp: Composition, edge_index: int | None = None) -> Composition:
    """Type-2 perturbation: drop one edge.

    Picks the last edge if edge_index is None (heuristic: lowest-leverage
    edges are typically last in a topologically-sorted edge list, but this
    is a coarse approximation; a real lowest-leverage selection would use
    bulla.witness_geometry leverage scores).

    Returns a new Composition with the dropped edge; does NOT modify tools
    or other edges.
    """
    if not comp.edges:
        return comp
    if edge_index is None:
        edge_index = len(comp.edges) - 1
    new_edges = tuple(e for i, e in enumerate(comp.edges) if i != edge_index)
    return Composition(name=comp.name, tools=comp.tools, edges=new_edges)


@dataclass(frozen=True)
class StabilityResult:
    """Result of stability check on a composition + filtration."""

    composition_name: str
    filtration: FiltrationName
    type1_bottleneck: float  # bottleneck distance for rename perturbation
    type2_bottleneck: float  # bottleneck distance for edge-drop perturbation
    type1_within_one_step: bool  # type1_bottleneck <= 1 eps-step (=eps_step)
    type2_within_one_step: bool


def stability_check(
    composition: Composition,
    *,
    filtration: FiltrationName = "lcp",
    eps_step: float = 0.05,
    pack_ontology: PackTagOntology | None = None,
) -> StabilityResult:
    """Compute stability via type-1 (rename) and type-2 (drop edge) perturbations.

    Promotion target: type-1 bottleneck <= 1 eps-step in >=7/9 well-formed
    compositions; type-2 leverage-bounded similarly.
    """
    bars_orig = compute_barcode(
        composition, filtration=filtration, eps_step=eps_step, pack_ontology=pack_ontology
    )
    perturbed_t1 = perturb_type1_rename(composition)
    bars_t1 = compute_barcode(
        perturbed_t1, filtration=filtration, eps_step=eps_step, pack_ontology=pack_ontology
    )
    perturbed_t2 = perturb_type2_drop_edge(composition)
    bars_t2 = compute_barcode(
        perturbed_t2, filtration=filtration, eps_step=eps_step, pack_ontology=pack_ontology
    )
    bn_t1 = bottleneck_distance(bars_orig, bars_t1)
    bn_t2 = bottleneck_distance(bars_orig, bars_t2)
    return StabilityResult(
        composition_name=composition.name,
        filtration=filtration,
        type1_bottleneck=bn_t1,
        type2_bottleneck=bn_t2,
        type1_within_one_step=bn_t1 <= eps_step + 1e-9,
        type2_within_one_step=bn_t2 <= eps_step + 1e-9,
    )
