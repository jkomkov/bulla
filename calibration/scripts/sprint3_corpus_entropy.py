"""Sprint 3: Corpus repair-entropy profile.

Computes β (repair multiplicity) and H_repair (repair entropy) for all
240 nonzero-fee corpus compositions using the corrected formula:

    β(G) = ∏_{components C of K} |C|
    H_repair(G) = log β(G) = Σ log|C|

Also computes normalized flexibility:

    F(G) = (H_repair − log(φ+1)) / (φ·log2 − log(φ+1))  ∈ [0, 1]

where φ = fee(G), and the bounds are:
  - Lower: β = φ+1 (single component of size φ+1)
  - Upper: β = 2^φ (φ components of size 2)
"""

import json
import math
import sys
from collections import defaultdict
from functools import reduce
from operator import mul
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from bulla.witness_geometry import (
    _connected_components_of_gram,
    compute_profile,
)

# ── Corpus loading (shared with sprint3_separation_probe.py) ──

MANIFESTS_DIR = REPO / "calibration" / "data" / "registry" / "manifests"
PAIRS_PATH = REPO / "calibration" / "data" / "registry" / "report" / "schema_structure_pairs.jsonl"


def load_manifests():
    manifests = {}
    for f in sorted(MANIFESTS_DIR.glob("*.json")):
        if f.name == "coherence.db" or f.stem.startswith("."):
            continue
        try:
            data = json.loads(f.read_text())
            tools = data.get("tools", [])
            if tools:
                manifests[f.stem] = tools
        except (json.JSONDecodeError, KeyError):
            continue
    return manifests


def build_composition_from_manifests(server_a, tools_a, server_b, tools_b):
    from bulla.guard import BullaGuard
    prefixed = []
    for t in tools_a:
        p = dict(t)
        p["name"] = f"{server_a}__{t['name']}"
        prefixed.append(p)
    for t in tools_b:
        p = dict(t)
        p["name"] = f"{server_b}__{t['name']}"
        prefixed.append(p)
    return BullaGuard.from_tools_list(prefixed, name=f"{server_a}+{server_b}").composition


# ── Repair entropy computation ──

def compute_repair_entropy(profile):
    """Compute β, H_repair, and normalized flexibility F from a WitnessProfile.

    Returns dict with: component_sizes, beta, H_repair, F, fee
    """
    K = profile.K
    fee = profile.fee
    if fee == 0:
        return {
            "component_sizes": [],
            "beta": 1,
            "H_repair": 0.0,
            "F": 0.0,
            "fee": 0,
        }

    components = _connected_components_of_gram(K)
    component_sizes = sorted([len(c) for c in components], reverse=True)

    # β = ∏ |C| (singletons contribute 1)
    beta = 1
    for s in component_sizes:
        beta *= s

    # H_repair = log β
    H_repair = math.log(beta) if beta > 0 else 0.0

    # Normalized flexibility F ∈ [0, 1]
    # Lower bound: log(φ+1), upper bound: φ·log(2)
    H_min = math.log(fee + 1)
    H_max = fee * math.log(2)
    if H_max > H_min:
        F = (H_repair - H_min) / (H_max - H_min)
    else:
        F = 0.0  # fee=1: both bounds equal log(2), F undefined → 0

    return {
        "component_sizes": component_sizes,
        "beta": beta,
        "H_repair": H_repair,
        "F": F,
        "fee": fee,
    }


# ── Main ──

print("=" * 70)
print("CORPUS REPAIR-ENTROPY PROFILE")
print("=" * 70)

manifests = load_manifests()
pairs = []
with open(PAIRS_PATH) as f:
    for line in f:
        line = line.strip()
        if line:
            pairs.append(json.loads(line))

nonzero = [p for p in pairs if p["fee"] > 0]
print(f"  {len(nonzero)} nonzero-fee compositions to process")

results = []
errors = 0
for i, p in enumerate(nonzero):
    name = p["pair_name"]
    left, right = p["left_server"], p["right_server"]
    if left not in manifests or right not in manifests:
        continue

    if (i + 1) % 40 == 0 or i == 0:
        print(f"  [{i+1}/{len(nonzero)}] {name}...")

    try:
        comp = build_composition_from_manifests(left, manifests[left], right, manifests[right])
        profile = compute_profile(list(comp.tools), list(comp.edges))
        if profile.fee == 0 or profile.fee != p["fee"]:
            continue

        entropy = compute_repair_entropy(profile)
        entropy["composition"] = name
        entropy["n_hidden"] = len(profile.hidden_basis)
        entropy["n_coloops"] = len(profile.coloops)
        entropy["n_loops"] = len(profile.loops)
        results.append(entropy)
    except Exception as e:
        errors += 1
        continue

print(f"\n  Processed: {len(results)} compositions ({errors} errors)")

# ── Summary statistics ──

print("\n" + "=" * 70)
print("FEE-GROUPED ENTROPY SUMMARY")
print("=" * 70)

by_fee = defaultdict(list)
for r in results:
    by_fee[r["fee"]].append(r)

print(f"\n  {'Fee':>5} {'N':>5} {'β range':>20} {'H range':>20} {'F range':>15} {'Components':>15}")
print(f"  {'-'*85}")

for fee in sorted(by_fee):
    group = by_fee[fee]
    n = len(group)
    b_min = min(r["beta"] for r in group)
    b_max = max(r["beta"] for r in group)
    h_min = min(r["H_repair"] for r in group)
    h_max = max(r["H_repair"] for r in group)
    f_min = min(r["F"] for r in group)
    f_max = max(r["F"] for r in group)
    # Component structure summary
    all_sizes = [tuple(r["component_sizes"]) for r in group]
    unique_patterns = len(set(all_sizes))
    print(f"  {fee:>5} {n:>5} {b_min:>8}–{b_max:<8} {h_min:>8.3f}–{h_max:<8.3f} {f_min:>6.3f}–{f_max:<6.3f} {unique_patterns:>5} patterns")


# ── Flexibility distribution ──

print("\n" + "=" * 70)
print("FLEXIBILITY DISTRIBUTION")
print("=" * 70)

all_F = [r["F"] for r in results]
if all_F:
    bins = [(0.0, 0.2, "rigid"), (0.2, 0.4, "low-flex"), (0.4, 0.6, "mid-flex"),
            (0.6, 0.8, "high-flex"), (0.8, 1.001, "max-flex")]
    print(f"\n  {'Bin':>15} {'Count':>8} {'Pct':>8}")
    print(f"  {'-'*35}")
    for lo, hi, label in bins:
        count = sum(1 for f in all_F if lo <= f < hi)
        pct = 100 * count / len(all_F) if all_F else 0
        print(f"  {label:>15} {count:>8} {pct:>7.1f}%")
    print(f"\n  Mean F = {sum(all_F)/len(all_F):.3f}")
    print(f"  Median F = {sorted(all_F)[len(all_F)//2]:.3f}")

# ── Component-size motif atlas ──

print("\n" + "=" * 70)
print("MOTIF ATLAS (component-size signatures)")
print("=" * 70)

motif_counts = defaultdict(list)
for r in results:
    # Filter out size-1 components (loops, contribute nothing to β)
    nontrivial = tuple(s for s in r["component_sizes"] if s > 1)
    motif_counts[nontrivial].append(r["composition"])

print(f"\n  {len(motif_counts)} distinct motif patterns across {len(results)} compositions")
print(f"\n  {'Motif':>25} {'Count':>6} {'β':>8} {'Fee':>5} {'Class':>15}")
print(f"  {'-'*65}")

# Classify motifs
def classify_motif(sizes):
    if not sizes:
        return "trivial"
    if all(s == 2 for s in sizes):
        return "binary-flex"
    if len(sizes) == 1:
        return "rigid" if sizes[0] <= 3 else "single-block"
    if max(sizes) > 3:
        return "component-heavy"
    return "mixed"

for motif in sorted(motif_counts, key=lambda m: (-len(motif_counts[m]), m)):
    count = len(motif_counts[motif])
    beta = 1
    for s in motif:
        beta *= s
    fee = sum(s - 1 for s in motif)
    cls = classify_motif(motif)
    motif_str = str(motif) if motif else "()"
    if count >= 2 or len(motif_counts) <= 30:
        print(f"  {motif_str:>25} {count:>6} {beta:>8} {fee:>5} {cls:>15}")

# Top 10 most flexible
print("\n  Top 10 most flexible (highest F):")
top_flex = sorted(results, key=lambda r: -r["F"])[:10]
for r in top_flex:
    nontrivial = tuple(s for s in r["component_sizes"] if s > 1)
    print(f"    F={r['F']:.3f}  β={r['beta']:>6}  fee={r['fee']}  motif={nontrivial}  {r['composition']}")

# Top 10 most rigid (lowest F, excluding fee=1)
print("\n  Top 10 most rigid (lowest F, fee≥2):")
rigid = [r for r in results if r["fee"] >= 2]
top_rigid = sorted(rigid, key=lambda r: r["F"])[:10]
for r in top_rigid:
    nontrivial = tuple(s for s in r["component_sizes"] if s > 1)
    print(f"    F={r['F']:.3f}  β={r['beta']:>6}  fee={r['fee']}  motif={nontrivial}  {r['composition']}")


# ── Save results ──

OUTPUT_PATH = REPO / "calibration" / "results" / "sprint3_corpus_entropy.json"
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

output = {
    "summary": {
        "n_compositions": len(results),
        "n_errors": errors,
        "mean_F": sum(all_F) / len(all_F) if all_F else None,
        "median_F": sorted(all_F)[len(all_F) // 2] if all_F else None,
    },
    "fee_groups": {
        str(fee): {
            "n": len(group),
            "beta_range": [min(r["beta"] for r in group), max(r["beta"] for r in group)],
            "H_range": [min(r["H_repair"] for r in group), max(r["H_repair"] for r in group)],
            "F_range": [min(r["F"] for r in group), max(r["F"] for r in group)],
        }
        for fee, group in by_fee.items()
    },
    "motif_atlas": {
        str(motif): {
            "count": len(comps),
            "beta": 1 if not motif else reduce(mul, motif),
            "fee": sum(s - 1 for s in motif),
            "class": classify_motif(motif),
        }
        for motif, comps in motif_counts.items()
    },
    "per_composition": [
        {
            "composition": r["composition"],
            "fee": r["fee"],
            "beta": r["beta"],
            "H_repair": round(r["H_repair"], 6),
            "F": round(r["F"], 6),
            "component_sizes": r["component_sizes"],
            "n_hidden": r["n_hidden"],
            "n_coloops": r["n_coloops"],
            "n_loops": r["n_loops"],
        }
        for r in results
    ],
}
with open(OUTPUT_PATH, "w") as f:
    json.dump(output, f, indent=2)
print(f"\n  Saved to {OUTPUT_PATH}")
