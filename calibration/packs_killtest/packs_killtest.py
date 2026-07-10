#!/usr/bin/env python3
"""Packs execution kill-test — frozen harness (see PRE-REGISTRATION.md).

Three predictors, computed BLIND to the label:
  * bulla      — bulla's FULL detection on the real product path:
                 fee = BullaGuard.from_tools_list([producer, consumer]).diagnose()
                 .coherence_fee (the hidden-convention / disclosure channel), OR the
                 StructuralDiagnostic contradiction channel (the visible-but-incompatible
                 channel). "bulla fires" = fee>0 OR contradiction_score>0. Both are
                 recorded separately. (The pre-reg's "infers a convention AND dimension
                 differs OR value violates the pack value-set" — the structural channel
                 is bulla's implementation of the latter; fee alone is strictly weaker.)
  * dumb       — ~20-line baseline, no pack taxonomy: shared field name in both schemas
                 AND (type OR enum OR format OR pattern) differs for that field.
  * jsonschema — validate the producer's REAL emitted value against the consumer's field
                 schema with a strict validator; fires iff validation fails.

FAIRNESS (post-diagnosis corrections, all recorded):
  1. Each tool carries an extra neutral observable field ("note") so a convention field
     can actually be HIDDEN — else guard.py:429's empty-observable fallback forces the
     lone convention field observable and the fee can never fire (an artifact, not bulla).
  2. In-pack conventions use descriptions that hit bulla's actual base-pack KEYWORDS
     (date_format / amount_unit / encoding) so inference fairly fires. currency-code,
     physical length, and geo-order are NOT in the base pack -> the trap arm (silent-FN).

The LABEL is a real Python round-trip (strptime / arithmetic / encode-decode / lookup);
no model, no LLM, no Bernoulli. See PRE-REGISTRATION.md §3.

Run: PYTHONPATH=<repo>/bulla/src python3 packs_killtest.py  (deterministic)
"""
from __future__ import annotations

import json
import math
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "bulla" / "src"))
from bulla.guard import BullaGuard  # noqa: E402

try:
    import jsonschema as _js  # noqa: E402

    def _validate_fails(value: Any, schema: dict) -> bool:
        try:
            _js.validate(value, schema)
            return False
        except _js.ValidationError:
            return True
    _JS_IMPL = "jsonschema-lib"
except Exception:  # pragma: no cover
    def _validate_fails(value: Any, schema: dict) -> bool:
        import re
        t = schema.get("type")
        ok = True
        if t == "string":
            ok = isinstance(value, str)
        elif t == "integer":
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif t == "number":
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif t == "array":
            ok = isinstance(value, list)
        if ok and "enum" in schema:
            ok = value in schema["enum"]
        if ok and "pattern" in schema and isinstance(value, str):
            ok = re.search(schema["pattern"], value) is not None
        return not ok
    _JS_IMPL = "fallback-strict"


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


# ── Oracles: real execution. Return True iff the round-trip BREAKS. ──────────
_DATE_FMT = {"iso": "%Y-%m-%d", "us": "%m/%d/%Y", "eu": "%d/%m/%Y", "compact": "%Y%m%d"}


def oracle_date(a: str, b: str, y: int, m: int, d: int) -> bool:
    emitted = datetime(y, m, d).strftime(_DATE_FMT[a])
    try:
        p = datetime.strptime(emitted, _DATE_FMT[b])
    except ValueError:
        return True
    return (p.year, p.month, p.day) != (y, m, d)


def oracle_amount(a: str, b: str, magnitude: float) -> bool:
    base = {"dollars": 1.0, "cents": 0.01}  # money
    return abs(magnitude * base[a] - magnitude * base[b]) > 1e-9


def oracle_encoding(a: str, b: str, text: str) -> bool:
    codec = {"utf-8": "utf-8", "latin-1": "latin-1", "ascii": "ascii"}
    try:
        raw = text.encode(codec[a])
    except UnicodeEncodeError:
        return True  # producer cannot even emit it under a
    try:
        got = raw.decode(codec[b])
    except UnicodeDecodeError:
        return True
    return got != text  # mojibake = silent corruption


def oracle_length(a: str, b: str, magnitude: float) -> bool:  # TRAP (out-of-pack)
    base = {"meters": 1.0, "feet": 0.3048}
    return abs(magnitude * base[a] - magnitude * base[b]) > 1e-9


_ISO4217 = [("USD", "840"), ("EUR", "978"), ("JPY", "392"), ("GBP", "826"),
            ("CHF", "756"), ("CAD", "124"), ("AUD", "036"), ("CNY", "156")]
_A2N = {a: n for a, n in _ISO4217}
_N2A = {n: a for a, n in _ISO4217}


def oracle_currency(a: str, b: str, alpha: str, numeric: str) -> bool:  # TRAP
    emitted = alpha if a == "alpha" else numeric
    try:
        if b == "alpha":
            return emitted not in _A2N
        n = int(emitted)
        return _N2A.get(str(n).zfill(3)) != alpha
    except (ValueError, KeyError):
        return True


def oracle_geo(a: str, b: str, lat: float, lng: float) -> bool:  # TRAP
    emitted = (lat, lng) if a == "latlng" else (lng, lat)
    if b == "latlng":
        c_lat, c_lng = emitted
    else:
        c_lng, c_lat = emitted
    return math.hypot(c_lat - lat, c_lng - lng) > 1e-9


# ── Field schema per convention / stratum / encoding ─────────────────────────
# rich: description hits bulla keywords + a distinguishing schema signal.
# medium: description hits bulla keywords, no schema-type distinction.
# bare: field name only, generic type, no description.
_SPECS: dict[str, dict] = {
    "date_format": {"name": "date", "type_default": "string", "in_pack": True,
        "rich": {
            "iso": ("string", "event date in ISO 8601 format, yyyy-mm-dd", {"pattern": r"^\d{4}-\d{2}-\d{2}$"}),
            "us": ("string", "event date in US date format mm/dd/yyyy", {"pattern": r"^\d{2}/\d{2}/\d{4}$"}),
            "eu": ("string", "event date in EU date format dd/mm/yyyy", {"pattern": r"^\d{2}/\d{2}/\d{4}$"}),
            "compact": ("string", "event date, compact datetime format yyyymmdd", {"pattern": r"^\d{8}$"}),
        },
        "medium": ("string", "the event date format", {}),
    },
    "amount_unit": {"name": "amount", "type_default": "number", "in_pack": True,
        "rich": {
            "dollars": ("number", "amount in dollars, the major unit", {}),
            "cents": ("integer", "amount in cents, the minor unit", {}),
        },
        "medium": ("number", "monetary amount", {}),
    },
    "encoding": {"name": "text", "type_default": "string", "in_pack": True,
        "rich": {
            "utf-8": ("string", "text encoded as utf-8 unicode", {}),
            "latin-1": ("string", "text encoded as latin-1 (iso-8859)", {}),
            "ascii": ("string", "text encoded as ascii character encoding", {}),
        },
        "medium": ("string", "the character encoding of the text", {}),
    },
    # ── traps (out-of-pack) ──
    "currency_code": {"name": "currency", "type_default": "string", "in_pack": False,
        "rich": {
            "alpha": ("string", "currency (three letters)", {"pattern": r"^[A-Z]{3}$"}),
            "numeric": ("string", "currency (three digits)", {"pattern": r"^[0-9]{3}$"}),
        },
        "medium": ("string", "the currency", {}),
    },
    "length_unit": {"name": "distance", "type_default": "number", "in_pack": False,
        "rich": {
            "meters": ("number", "distance value (metric)", {}),
            "feet": ("number", "distance value (imperial)", {}),
        },
        "medium": ("number", "the distance", {}),
    },
    "geo_order": {"name": "coordinates", "type_default": "array", "in_pack": False,
        "rich": {
            "latlng": ("array", "coordinates as [lat, lng]", {}),
            "lnglat": ("array", "coordinates as [lng, lat]", {}),
        },
        "medium": ("array", "the coordinates", {}),
    },
}

_TOOL_NAMES = {
    "date_format": ("fetch_event", "schedule_task"),
    "amount_unit": ("get_invoice", "record_payment"),
    "encoding": ("read_document", "index_document"),
    "currency_code": ("get_quote", "place_order"),
    "length_unit": ("measure_route", "store_route"),
    "geo_order": ("locate", "store_location"),
}


def _schema_for(conv: str, stratum: str, enc: str) -> tuple[str, dict, str]:
    spec = _SPECS[conv]
    name = spec["name"]
    if stratum == "rich":
        t, desc, extra = spec["rich"][enc]
        sch = {"type": t, "description": desc, **extra}
    elif stratum == "medium":
        t, desc, extra = spec["medium"]
        sch = {"type": t, "description": desc, **extra}
    else:  # bare
        sch = {"type": spec["type_default"]}
        desc = ""
    return name, sch, desc


def _tool(role: str, conv: str, field: str, schema: dict) -> dict:
    # neutral extra observable field so the convention field can be hidden (fairness #1)
    props = {field: schema, "note": {"type": "string", "description": "free-text note"}}
    p, c = _TOOL_NAMES[conv]
    if role == "producer":
        return {"name": p, "description": f"Produce a {conv} value.",
                "outputSchema": {"type": "object", "properties": props}}
    return {"name": c, "description": f"Consume a {conv} value.",
            "inputSchema": {"type": "object", "properties": props}}


@dataclass
class Case:
    conv: str
    in_pack: bool
    stratum: str
    enc_a: str
    enc_b: str
    match: bool
    value: Any
    producer: dict
    consumer: dict
    field: str
    consumer_field_schema: dict
    label: bool


def build_cases() -> list[Case]:
    cases: list[Case] = []
    dates = [(2024, 1, 2), (2024, 3, 5), (2024, 11, 12), (2024, 6, 7),
             (2024, 5, 13), (2024, 12, 25), (2023, 2, 28), (2024, 10, 3)]
    encs = {
        "date_format": ["iso", "us", "eu", "compact"],
        "amount_unit": ["dollars", "cents"],
        "encoding": ["utf-8", "latin-1", "ascii"],
        "currency_code": ["alpha", "numeric"],
        "length_unit": ["meters", "feet"],
        "geo_order": ["latlng", "lnglat"],
    }
    texts = ["café", "résumé", "naïve", "Zürich", "plain-ascii"]
    amts = [1050.0, 999.0, 10.5, 250.0]
    dists = [100.0, 50.0, 26.2, 1000.0]
    geos = [(40.0, -74.0), (51.5, -0.12), (-33.8, 151.2), (35.6, 139.7)]

    def add(conv, stratum, ea, eb, value, label):
        fa, sa, _ = _schema_for(conv, stratum, ea)
        fb, sb, _ = _schema_for(conv, stratum, eb)
        cases.append(Case(conv, _SPECS[conv]["in_pack"], stratum, ea, eb, ea == eb,
                          value, _tool("producer", conv, fa, sa),
                          _tool("consumer", conv, fb, sb), fa, sb, label))

    for stratum in ("rich", "medium", "bare"):
        for ea in encs["date_format"]:
            for eb in encs["date_format"]:
                for (y, m, d) in dates:
                    add("date_format", stratum, ea, eb,
                        datetime(y, m, d).strftime(_DATE_FMT[ea]), oracle_date(ea, eb, y, m, d))
        for ea in encs["amount_unit"]:
            for eb in encs["amount_unit"]:
                for mag in amts:
                    add("amount_unit", stratum, ea, eb, mag, oracle_amount(ea, eb, mag))
        for ea in encs["encoding"]:
            for eb in encs["encoding"]:
                for txt in texts:
                    add("encoding", stratum, ea, eb, txt, oracle_encoding(ea, eb, txt))
        # traps
        for ea in encs["currency_code"]:
            for eb in encs["currency_code"]:
                for (al, nu) in _ISO4217:
                    add("currency_code", stratum, ea, eb, al if ea == "alpha" else nu,
                        oracle_currency(ea, eb, al, nu))
        for ea in encs["length_unit"]:
            for eb in encs["length_unit"]:
                for mag in dists:
                    add("length_unit", stratum, ea, eb, mag, oracle_length(ea, eb, mag))
        for ea in encs["geo_order"]:
            for eb in encs["geo_order"]:
                for (lat, lng) in geos:
                    add("geo_order", stratum, ea, eb, [lat, lng], oracle_geo(ea, eb, lat, lng))
    return cases


# ── Predictors (blind to label) ──────────────────────────────────────────────
def predict_bulla(case: Case) -> tuple[bool, int, int]:
    g = BullaGuard.from_tools_list([case.producer, case.consumer], name="kt")
    fee = g.diagnose().coherence_fee
    sd = getattr(g, "structural_diagnostic", None)
    contra = getattr(sd, "contradiction_score", 0) if sd is not None else 0
    fires = (fee > 0) or (contra > 0)
    return fires, fee, contra


def _field_of(tool: dict, field: str) -> dict | None:
    for key in ("outputSchema", "inputSchema"):
        props = tool.get(key, {}).get("properties", {})
        if field in props:
            return props[field]
    return None


def predict_dumb(case: Case) -> bool:
    p = _field_of(case.producer, case.field)
    c = case.consumer_field_schema
    if p is None or c is None:
        return False
    return any(p.get(k) != c.get(k) for k in ("type", "enum", "format", "pattern"))


def predict_jsonschema(case: Case) -> bool:
    return _validate_fails(case.value, case.consumer_field_schema)


# ── Scoring ──────────────────────────────────────────────────────────────────
def _prf(rows: list[dict], key: str) -> dict:
    tp = sum(1 for r in rows if r[key] and r["label"])
    fp = sum(1 for r in rows if r[key] and not r["label"])
    fn = sum(1 for r in rows if not r[key] and r["label"])
    tn = sum(1 for r in rows if not r[key] and not r["label"])
    prec = tp / (tp + fp) if (tp + fp) else float("nan")
    rec = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * prec * rec / (prec + rec)
          if (tp + fp) and (tp + fn) and (prec + rec) else float("nan"))
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": prec, "recall": rec, "f1": f1, "n": len(rows)}


def main() -> None:
    cases = build_cases()
    rows: list[dict] = []
    for c in cases:
        fires, fee, contra = predict_bulla(c)
        rows.append({
            "conv": c.conv, "in_pack": c.in_pack, "stratum": c.stratum,
            "enc_a": c.enc_a, "enc_b": c.enc_b, "match": c.match, "label": c.label,
            "bulla": fires, "bulla_fee": fee > 0, "bulla_contra": contra > 0, "fee": fee,
            "dumb": predict_dumb(c), "jsonschema": predict_jsonschema(c),
        })

    def sub(pred, **f):
        return _prf([r for r in rows if all(r[k] == v for k, v in f.items())], pred)

    in_pack = [r for r in rows if r["in_pack"]]
    traps = [r for r in rows if not r["in_pack"]]
    undeclared = [r for r in in_pack if r["stratum"] in ("medium", "bare")]

    def fire_rate(rs, key, want_match):
        d = [r for r in rs if r["match"] == want_match]
        return (sum(1 for r in d if r[key]) / len(d)) if d else float("nan")

    report = {
        "harness": "packs_killtest.py", "jsonschema_impl": _JS_IMPL,
        "n_cases": len(rows), "n_in_pack": len(in_pack), "n_traps": len(traps),
        "break_prevalence_in_pack": sum(1 for r in in_pack if r["label"]) / len(in_pack),
        "overall_in_pack": {p: _prf(in_pack, p) for p in ("bulla", "dumb", "jsonschema")},
        "undeclared_only": {p: _prf(undeclared, p) for p in ("bulla", "dumb", "jsonschema")},
        "by_stratum": {s: {p: sub(p, stratum=s, in_pack=True)
                           for p in ("bulla", "dumb", "jsonschema")}
                       for s in ("rich", "medium", "bare")},
        "by_conv": {cv: {p: sub(p, conv=cv) for p in ("bulla", "dumb", "jsonschema")}
                    for cv in _SPECS},
        "bulla_channels_in_pack": {"fee": _prf(in_pack, "bulla_fee"),
                                   "contradiction": _prf(in_pack, "bulla_contra")},
        "bulla_presence_not_agreement": {
            "fee_fire_rate_MATCH": fire_rate(in_pack, "bulla_fee", True),
            "fee_fire_rate_MISMATCH": fire_rate(in_pack, "bulla_fee", False),
            "contra_fire_rate_MATCH": fire_rate(in_pack, "bulla_contra", True),
            "contra_fire_rate_MISMATCH": fire_rate(in_pack, "bulla_contra", False),
        },
        "trap_silent_fn": {"n_trap_breaks": sum(1 for r in traps if r["label"]),
                           "bulla_missed": sum(1 for r in traps if r["label"] and not r["bulla"])},
        "rows": rows,
    }
    (HERE / "result.json").write_text(json.dumps(report, indent=2))

    def f(d):
        return (f"P={d['precision']:.3f} R={d['recall']:.3f} F1={d['f1']:.3f} "
                f"(tp{d['tp']} fp{d['fp']} fn{d['fn']} tn{d['tn']} n{d['n']})")

    print(f"cases={len(rows)} in_pack={len(in_pack)} traps={len(traps)} "
          f"break_prevalence={report['break_prevalence_in_pack']:.3f} js={_JS_IMPL}")
    print("\n== OVERALL (in-pack: date_format, amount_unit, encoding) ==")
    for p in ("bulla", "dumb", "jsonschema"):
        print(f"  {p:11s} {f(report['overall_in_pack'][p])}")
    print("\n== UNDECLARED (medium+bare) — the honest hard case ==")
    for p in ("bulla", "dumb", "jsonschema"):
        print(f"  {p:11s} {f(report['undeclared_only'][p])}")
    print("\n== bulla channels (in-pack) ==")
    print(f"  fee           {f(report['bulla_channels_in_pack']['fee'])}")
    print(f"  contradiction {f(report['bulla_channels_in_pack']['contradiction'])}")
    print("\n== is bulla a PRESENCE flag or a MISMATCH detector? (fire rate) ==")
    b = report["bulla_presence_not_agreement"]
    print(f"  fee:          MATCH={b['fee_fire_rate_MATCH']:.3f}  MISMATCH={b['fee_fire_rate_MISMATCH']:.3f}")
    print(f"  contradiction MATCH={b['contra_fire_rate_MATCH']:.3f}  MISMATCH={b['contra_fire_rate_MISMATCH']:.3f}")
    print("  (equal MATCH/MISMATCH => presence flag, blind to agreement)")
    print("\n== BY STRATUM (bulla) ==")
    for s in ("rich", "medium", "bare"):
        print(f"  {s:7s} {f(report['by_stratum'][s]['bulla'])}")
    print("\n== TRAP (out-of-pack) silent false-negatives ==")
    t = report["trap_silent_fn"]
    print(f"  bulla missed {t['bulla_missed']}/{t['n_trap_breaks']} real breaks")
    print("\nwrote result.json")


if __name__ == "__main__":
    main()
