"""G27 scaling sweep over corpus with witness vs local/combined models.

This module supports two evidence classes:
- operational proxy outcomes derived from perturbation/corpus telemetry;
- externally supplied observed outcomes keyed by composition_id.

It also enforces the G27 mid-cycle fast-fail gate before any sweep work:
if fewer than the minimum number of high-obstruction (`r >= 2`) compositions
exist in the corpus, the run is deferred and tagged underpowered.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RegressionSummary:
    status: str
    n_rows: int
    high_r_count: int
    min_high_r_count: int
    r2_witness: float
    r2_local_audit: float
    r2_combined: float
    corr_witness_loss: float
    corr_local_loss: float
    artifact_label: str
    evidence_class: str
    outcome_source: str
    tail_precision_top20: dict[str, float]
    scale_bin_stats: dict[str, dict[str, float]]
    rank_bin_stats: dict[str, dict[str, float]]
    corpus_metadata: dict[str, Any]
    defer_tags: list[str]
    simulated: bool
    warning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "n_rows": self.n_rows,
            "high_r_count": self.high_r_count,
            "min_high_r_count": self.min_high_r_count,
            "r2_witness": self.r2_witness,
            "r2_local_audit": self.r2_local_audit,
            "r2_combined": self.r2_combined,
            "corr_witness_loss": self.corr_witness_loss,
            "corr_local_loss": self.corr_local_loss,
            "artifact_label": self.artifact_label,
            "evidence_class": self.evidence_class,
            "outcome_source": self.outcome_source,
            "tail_precision_top20": self.tail_precision_top20,
            "scale_bin_stats": self.scale_bin_stats,
            "rank_bin_stats": self.rank_bin_stats,
            "corpus_metadata": self.corpus_metadata,
            "defer_tags": self.defer_tags,
            "simulated": self.simulated,
            "warning": self.warning,
        }


def _synthetic_loss_control(row: dict[str, Any]) -> float:
    fee = float(row["coherence_fee"])
    edges = float(row["n_edges"])
    seed = str(row["perturbation_seed"])
    # deterministic bounded jitter in [-0.5, 0.5]
    jitter = (int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF) - 0.5
    return 2.0 * fee + 0.15 * edges + jitter


def _operational_proxy_loss(row: dict[str, Any]) -> float:
    hidden_ratio = float(row.get("schema_properties_hidden", 0)) / max(1.0, float(row.get("schema_properties_before", 0)))
    required_ratio = float(row.get("required_fields_hidden", 0)) / max(1.0, float(row.get("required_fields_before", 0)))
    local_score = float(row["n_edges"]) / max(1.0, float(row["n_tools"]))
    server_span = float(len(row.get("servers", [])))
    seed = str(row.get("perturbation_seed", ""))
    jitter = ((int(hashlib.sha256(f"operational:{seed}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF) - 0.5) * 0.1
    return 1.3 * hidden_ratio + 0.9 * required_ratio + 0.2 * local_score + 0.05 * server_span + jitter


def _corr(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n == 0:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = sum((x - mx) ** 2 for x in xs)
    deny = sum((y - my) ** 2 for y in ys)
    if denx <= 0 or deny <= 0:
        return 0.0
    return num / (denx ** 0.5 * deny ** 0.5)


def _fit_linear_r2(xs: list[float], ys: list[float]) -> tuple[list[float], float]:
    n = len(xs)
    if n == 0:
        return [], 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        preds = [my for _ in xs]
    else:
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        intercept = my - slope * mx
        preds = [intercept + slope * x for x in xs]
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, preds))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 0.0 if ss_tot == 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return preds, r2


def _fit_two_feature_linear(
    x1: list[float],
    x2: list[float],
    ys: list[float],
) -> tuple[list[float], float]:
    n = len(ys)
    if n == 0:
        return [], 0.0

    s1 = float(n)
    sx1 = sum(x1)
    sx2 = sum(x2)
    sx1x1 = sum(v * v for v in x1)
    sx2x2 = sum(v * v for v in x2)
    sx1x2 = sum(a * b for a, b in zip(x1, x2))
    sy = sum(ys)
    sx1y = sum(a * y for a, y in zip(x1, ys))
    sx2y = sum(b * y for b, y in zip(x2, ys))

    # Solve 3x3 normal equation via explicit elimination.
    a = [
        [s1, sx1, sx2],
        [sx1, sx1x1, sx1x2],
        [sx2, sx1x2, sx2x2],
    ]
    b = [sy, sx1y, sx2y]

    for col in range(3):
        pivot = col
        for row in range(col, 3):
            if abs(a[row][col]) > abs(a[pivot][col]):
                pivot = row
        if abs(a[pivot][col]) < 1e-12:
            mean_y = sy / n
            preds = [mean_y for _ in ys]
            ss_res = sum((y - p) ** 2 for y, p in zip(ys, preds))
            ss_tot = sum((y - mean_y) ** 2 for y in ys)
            r2 = 0.0 if ss_tot == 0 else max(0.0, 1.0 - ss_res / ss_tot)
            return preds, r2
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
            b[col], b[pivot] = b[pivot], b[col]

        scale = a[col][col]
        a[col] = [v / scale for v in a[col]]
        b[col] = b[col] / scale
        for row in range(3):
            if row == col:
                continue
            factor = a[row][col]
            a[row] = [a[row][j] - factor * a[col][j] for j in range(3)]
            b[row] = b[row] - factor * b[col]

    intercept, w1, w2 = b
    preds = [intercept + w1 * a + w2 * b for a, b in zip(x1, x2)]
    mean_y = sy / n
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, preds))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r2 = 0.0 if ss_tot == 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return preds, r2


def _tail_precision_topk(
    truth: list[float],
    preds: list[float],
    *,
    frac: float = 0.2,
) -> float:
    n = len(truth)
    if n == 0:
        return 0.0
    k = max(1, int(n * frac))
    true_ix = set(sorted(range(n), key=lambda i: truth[i], reverse=True)[:k])
    pred_ix = set(sorted(range(n), key=lambda i: preds[i], reverse=True)[:k])
    return len(true_ix & pred_ix) / k


def _bin_stats(
    rows: list[dict[str, Any]],
    vals: list[float],
    witness: list[float],
    local: list[float],
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    scale_bins: dict[str, list[int]] = {"small": [], "medium": [], "large": []}
    rank_bins: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        n_tools = int(row["n_tools"])
        if n_tools <= 8:
            scale_bins["small"].append(i)
        elif n_tools <= 16:
            scale_bins["medium"].append(i)
        else:
            scale_bins["large"].append(i)
        rk = str(int(row["coherence_fee"]))
        rank_bins.setdefault(rk, []).append(i)

    def summarize(indices: list[int]) -> dict[str, float]:
        if not indices:
            return {"n": 0.0, "corr_witness": 0.0, "corr_local": 0.0}
        ws = [witness[i] for i in indices]
        ls = [local[i] for i in indices]
        ys = [vals[i] for i in indices]
        return {"n": float(len(indices)), "corr_witness": _corr(ws, ys), "corr_local": _corr(ls, ys)}

    scale_summary = {k: summarize(v) for k, v in scale_bins.items()}
    rank_summary = {k: summarize(v) for k, v in rank_bins.items()}
    return scale_summary, rank_summary


def _load_corpus(corpus_json_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(corpus_json_path.read_text())
    if isinstance(payload, list):
        return payload, {}
    if isinstance(payload, dict):
        rows = payload.get("rows")
        if isinstance(rows, list):
            metadata = payload.get("metadata", {})
            return rows, metadata if isinstance(metadata, dict) else {}
    raise ValueError(f"Corpus at {corpus_json_path} must be a JSON list or bundle object with rows")


def _resolve_outcomes(
    rows: list[dict[str, Any]],
    outcomes_json_path: Path | None,
) -> tuple[list[float], str, str]:
    if outcomes_json_path is not None:
        payload = json.loads(outcomes_json_path.read_text())
        id_to_loss: dict[str, float] = {}
        if isinstance(payload, dict):
            for k, v in payload.items():
                if isinstance(v, (int, float)):
                    id_to_loss[str(k)] = float(v)
        elif isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                cid = item.get("composition_id")
                loss = item.get("realized_loss")
                if isinstance(cid, str) and isinstance(loss, (int, float)):
                    id_to_loss[cid] = float(loss)
        losses: list[float] = []
        for row in rows:
            cid = str(row["composition_id"])
            if cid not in id_to_loss:
                raise ValueError(f"Missing realized_loss for composition_id={cid} in {outcomes_json_path}")
            losses.append(id_to_loss[cid])
        return losses, "REAL_OUTCOME_V1", "real_observed"

    losses = [_operational_proxy_loss(r) for r in rows]
    return losses, "OPERATIONAL_PROXY_V1", "operational_proxy"


def run_scaling_sweep(
    corpus_json_path: Path,
    *,
    csv_out: Path,
    summary_out: Path,
    outcomes_json_path: Path | None = None,
    min_high_r_count: int = 30,
    strict_fast_fail: bool = False,
) -> RegressionSummary:
    rows, corpus_metadata = _load_corpus(corpus_json_path)
    high_r_count = sum(1 for r in rows if float(r.get("coherence_fee", 0)) >= 2.0)
    if high_r_count < min_high_r_count:
        summary = RegressionSummary(
            status="deferred_underpowered",
            n_rows=len(rows),
            high_r_count=high_r_count,
            min_high_r_count=min_high_r_count,
            r2_witness=0.0,
            r2_local_audit=0.0,
            r2_combined=0.0,
            corr_witness_loss=0.0,
            corr_local_loss=0.0,
            artifact_label="DEFER_UNDERPOWERED",
            evidence_class="deferred",
            outcome_source="none",
            tail_precision_top20={"witness": 0.0, "local": 0.0, "combined": 0.0},
            scale_bin_stats={},
            rank_bin_stats={},
            corpus_metadata=corpus_metadata,
            defer_tags=["deferred", "underpowered", "insufficient_high_obstruction"],
            simulated=False,
            warning=(
                "G27 fast-fail gate triggered before sweep: insufficient compositions "
                f"with r>=2 ({high_r_count} < {min_high_r_count})."
            ),
        )
        summary_out.parent.mkdir(parents=True, exist_ok=True)
        summary_out.write_text(json.dumps(summary.to_dict(), indent=2) + "\n")
        csv_out.parent.mkdir(parents=True, exist_ok=True)
        with csv_out.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "composition_id",
                    "artifact_label",
                    "outcome_source",
                    "outcome_loss",
                    "coherence_fee",
                    "local_audit_score",
                    "pred_witness",
                    "pred_local",
                    "pred_combined",
                    "synthetic_control_loss",
                ]
            )
        if strict_fast_fail:
            raise ValueError(summary.warning)
        return summary

    outcomes, artifact_label, outcome_source = _resolve_outcomes(rows, outcomes_json_path)
    witness = [float(r["coherence_fee"]) for r in rows]
    local = [float(r["n_edges"]) / max(1.0, float(r["n_tools"])) for r in rows]
    synthetic_control = [_synthetic_loss_control(r) for r in rows]

    witness_pred, r2_w = _fit_linear_r2(witness, outcomes)
    local_pred, r2_l = _fit_linear_r2(local, outcomes)
    combined_pred, r2_c = _fit_two_feature_linear(witness, local, outcomes)
    corr_w = _corr(witness, outcomes)
    corr_l = _corr(local, outcomes)
    scale_stats, rank_stats = _bin_stats(rows, outcomes, witness, local)
    tail_precision = {
        "witness": _tail_precision_topk(outcomes, witness_pred, frac=0.2),
        "local": _tail_precision_topk(outcomes, local_pred, frac=0.2),
        "combined": _tail_precision_topk(outcomes, combined_pred, frac=0.2),
    }

    csv_out.parent.mkdir(parents=True, exist_ok=True)
    with csv_out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "composition_id",
                "artifact_label",
                "outcome_source",
                "coherence_fee",
                "local_audit_score",
                "outcome_loss",
                "pred_witness",
                "pred_local",
                "pred_combined",
                "synthetic_control_loss",
            ]
        )
        for i, r in enumerate(rows):
            writer.writerow(
                [
                    r["composition_id"],
                    artifact_label,
                    outcome_source,
                    witness[i],
                    local[i],
                    outcomes[i],
                    witness_pred[i],
                    local_pred[i],
                    combined_pred[i],
                    synthetic_control[i],
                ]
            )

    summary = RegressionSummary(
        status="completed",
        n_rows=len(rows),
        high_r_count=high_r_count,
        min_high_r_count=min_high_r_count,
        r2_witness=r2_w,
        r2_local_audit=r2_l,
        r2_combined=r2_c,
        corr_witness_loss=corr_w,
        corr_local_loss=corr_l,
        artifact_label=artifact_label,
        evidence_class="empirical" if outcome_source == "real_observed" else "operational_proxy",
        outcome_source=outcome_source,
        tail_precision_top20=tail_precision,
        scale_bin_stats=scale_stats,
        rank_bin_stats=rank_stats,
        corpus_metadata=corpus_metadata,
        defer_tags=[],
        simulated=False,
        warning=""
        if outcome_source == "real_observed"
        else (
            "Operational proxy outcomes are used in place of external realized-loss traces; "
            "treat as intermediate evidence class pending full production replay outcomes."
        ),
    )
    summary_out.parent.mkdir(parents=True, exist_ok=True)
    summary_out.write_text(json.dumps(summary.to_dict(), indent=2) + "\n")
    return summary

