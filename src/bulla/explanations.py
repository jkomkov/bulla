"""Plain-language explanations for dimensions surfaced by ``bulla scan``.

Every dimension that can appear in ``Diagnostic.blind_spots[*].dimension``
gets a short human-readable label, one-sentence explanation, and a
one-sentence failure mode. The narrative output of ``bulla scan``
consumes this registry — it's what turns a JSON receipt into a story
a developer can act on in 10 seconds.

The registry is locked to the dimension universe by
``tests/test_explanations.py``: every name in the union of
``src/bulla/packs/{seed,community}/*.yaml`` dimension keys plus the
hardcoded patterns in ``src/bulla/infer/classifier.py`` must have an
entry here. Adding a dimension elsewhere without an entry fails CI.

Writing discipline (per the awareness-gap sprint):
  - Concrete examples. Real values, not placeholders.
  - One-sentence explanation, one-sentence failure mode.
  - No LLM cadence (no symmetric clauses, no tricolons, no em-dash
    rhetorical contrast).
  - The failure mode should make the consequence visible — what
    actually goes wrong when two tools disagree.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DimensionExplanation:
    """One entry in the registry."""

    name: str
    """Dimension key (e.g. ``"path_convention"``)."""

    human_label: str
    """Plain phrase to show in narrative output (e.g. ``"path format"``)."""

    explanation: str
    """One sentence describing what the dimension covers."""

    failure_mode: str
    """One sentence describing what goes wrong when two tools disagree."""


# ── The 39 entries ──────────────────────────────────────────────────


_FALLBACK = DimensionExplanation(
    name="<unknown>",
    human_label="convention",
    explanation=(
        "This dimension is in the active pack stack but doesn't yet "
        "have a plain-language entry in bulla's explanation registry."
    ),
    failure_mode=(
        "Two tools may interpret the same field differently; check the "
        "pack's description for the specific risk."
    ),
)


EXPLANATIONS: dict[str, DimensionExplanation] = {
    # ── Universal conventions (most likely to fire in the wild) ──
    "path_convention": DimensionExplanation(
        name="path_convention",
        human_label="path format",
        explanation=(
            "Some tools use absolute paths like /Users/alice/proj/file.ts; "
            "others use repository-relative paths like src/file.ts."
        ),
        failure_mode=(
            "An agent that reads /Users/alice/proj/src/main.ts from the "
            "filesystem and passes the same path to a GitHub create_file "
            "call gets 'file not found' or commits the file to the wrong "
            "place in the repo."
        ),
    ),
    "temporal_format": DimensionExplanation(
        name="temporal_format",
        human_label="timestamp format",
        explanation=(
            "Some tools emit timestamps as ISO-8601 strings like "
            "2026-05-03T17:00:00Z; others as Unix epoch integers like "
            "1777248000."
        ),
        failure_mode=(
            "Passing 1777248000 to a tool expecting ISO-8601 gives an "
            "InvalidDate; passing '2026-05-03T17:00:00Z' to one expecting "
            "epoch seconds gives a NaN."
        ),
    ),
    "date_format": DimensionExplanation(
        name="date_format",
        human_label="date convention",
        explanation=(
            "Date fields named 'date', 'expires_at', 'birth_date' may be "
            "ISO-8601 strings, Unix epoch, or the legacy mm/dd/yyyy "
            "depending on the tool."
        ),
        failure_mode=(
            "A tool emits '03/05/2026' meaning 5 March; a downstream "
            "consumer reads it as 3 May. Off-by-two-months silent."
        ),
    ),
    "currency_code": DimensionExplanation(
        name="currency_code",
        human_label="currency code format",
        explanation=(
            "ISO-4217 has three forms: alpha-3 ('USD'), numeric ('840'), "
            "and Stripe's lowercase variant ('usd')."
        ),
        failure_mode=(
            "An agent that reads 'USD' from one tool and passes it to a "
            "Stripe charge call gets 'invalid currency'; passing '840' "
            "to a non-numeric API gets the same."
        ),
    ),
    "country_code": DimensionExplanation(
        name="country_code",
        human_label="country code format",
        explanation=(
            "ISO-3166 has three forms: alpha-2 ('US'), alpha-3 ('USA'), "
            "and numeric ('840')."
        ),
        failure_mode=(
            "A geo-lookup returns 'GB'; the next tool's enum only "
            "accepts 'GBR'. The pipeline silently drops the record."
        ),
    ),
    "language_code": DimensionExplanation(
        name="language_code",
        human_label="language code format",
        explanation=(
            "ISO-639-1 alpha-2 ('en'), ISO-639-3 alpha-3 ('eng'), and "
            "BCP-47 with region ('en-US') all coexist."
        ),
        failure_mode=(
            "Translation pipeline reads 'en-US' and the downstream model "
            "selector expects 'eng'. Falls through to default English."
        ),
    ),
    "media_type": DimensionExplanation(
        name="media_type",
        human_label="MIME type",
        explanation=(
            "IANA media types like 'application/json' identify content "
            "format. Some tools pass them as 'json'; others as the full "
            "RFC 6838 string."
        ),
        failure_mode=(
            "A file upload tool tags content as 'json'; the storage "
            "tool expects 'application/json' and rejects the upload as "
            "an unknown type."
        ),
    ),
    "encoding": DimensionExplanation(
        name="encoding",
        human_label="text encoding",
        explanation=(
            "Fields like 'encoding', 'charset', 'content_encoding' may "
            "be UTF-8, Latin-1, ASCII, or 'gzip' depending on whether "
            "the tool means character set or transfer encoding."
        ),
        failure_mode=(
            "Tool A says encoding='gzip' meaning the bytes are "
            "compressed; Tool B reads encoding='gzip' as a charset and "
            "tries to decode the binary as text."
        ),
    ),
    "id_offset": DimensionExplanation(
        name="id_offset",
        human_label="pagination index base",
        explanation=(
            "Some APIs page from offset=0; others from page=1. Some "
            "treat 'offset' as item count, others as page count."
        ),
        failure_mode=(
            "Agent fetches page=1 from an API that pages from 0 and "
            "silently skips the first batch of results, or fetches the "
            "same first page twice."
        ),
    ),
    "timezone": DimensionExplanation(
        name="timezone",
        human_label="timezone convention",
        explanation=(
            "Some tools store timestamps in UTC; others in local time; "
            "others as a UTC offset like '-05:00'."
        ),
        failure_mode=(
            "A scheduler stores '09:00' meaning UTC; the calendar tool "
            "reads it as local time. Meeting fires five hours late."
        ),
    ),
    # ── Built-in convention dimensions ──
    "amount_unit": DimensionExplanation(
        name="amount_unit",
        human_label="monetary unit",
        explanation=(
            "Stripe and most card APIs use minor units (cents, 100 = "
            "$1.00). Most accounting tools use major units ($1.00 = "
            "1.00)."
        ),
        failure_mode=(
            "Agent reads $50.00 from QuickBooks, passes 50.00 to "
            "Stripe. Stripe charges 50 cents instead of $50."
        ),
    ),
    "rate_scale": DimensionExplanation(
        name="rate_scale",
        human_label="rate scale",
        explanation=(
            "Rates and percentages may be 0–1 (0.05 = 5%) or 0–100 "
            "(5 = 5%) depending on the tool."
        ),
        failure_mode=(
            "Agent reads tax_rate=0.0875 (8.75%) from a config; the "
            "billing tool treats it as 0.0875%. Tax line is 100x too "
            "small."
        ),
    ),
    "score_range": DimensionExplanation(
        name="score_range",
        human_label="score range",
        explanation=(
            "Scores, ratings, priorities can be 0–1, 0–10, 0–100, or "
            "negative-to-positive depending on the tool."
        ),
        failure_mode=(
            "A model returns confidence=0.92 (high); the downstream "
            "filter expects 0–100 and treats 0.92 as nearly-zero "
            "confidence, dropping the result."
        ),
    ),
    "precision": DimensionExplanation(
        name="precision",
        human_label="numeric precision",
        explanation=(
            "Some tools round to 2 decimal places by default; others to "
            "8 (financial) or 0 (integer-only)."
        ),
        failure_mode=(
            "An exchange-rate field is 1.23456789; the downstream "
            "ledger truncates to 1.23 and accumulates rounding error "
            "across thousands of transactions."
        ),
    ),
    "null_handling": DimensionExplanation(
        name="null_handling",
        human_label="null/missing-value strategy",
        explanation=(
            "Some tools treat null as 'unknown'; others as 'no value'; "
            "others as a falsy default."
        ),
        failure_mode=(
            "An optional 'discount' field arrives null; the billing "
            "tool reads null as 0% discount, but the upstream system "
            "meant 'no discount applicable, use the default 10%'."
        ),
    ),
    "line_ending": DimensionExplanation(
        name="line_ending",
        human_label="line-ending convention",
        explanation=(
            "Files written by macOS/Linux tools use LF; files from "
            "Windows tools use CRLF; some text APIs require one or "
            "the other."
        ),
        failure_mode=(
            "An agent writes a CSV via a Windows tool, then a parser "
            "treats CRLF as a record separator and ends up with empty "
            "rows between every line."
        ),
    ),
    # ── Built-in conventions present in the community pack ──
    "owner_convention": DimensionExplanation(
        name="owner_convention",
        human_label="owner reference style",
        explanation=(
            "Resource owners may be referenced as a username "
            "('alice'), a user ID ('123'), or a fully-qualified path "
            "('org/alice')."
        ),
        failure_mode=(
            "Tool A returns owner='alice'; Tool B's lookup expects "
            "owner='org/alice' and returns 'not found' for a user that "
            "exists."
        ),
    ),
    "sort_direction": DimensionExplanation(
        name="sort_direction",
        human_label="sort direction convention",
        explanation=(
            "Sort directions may be 'asc'/'desc', '+'/'-', or 1/-1 "
            "depending on the tool."
        ),
        failure_mode=(
            "An agent passes sort='-created_at' to a tool expecting "
            "'desc'; the API treats '-created_at' as a literal field "
            "name and returns an unknown-field error."
        ),
    ),
    "state_filter": DimensionExplanation(
        name="state_filter",
        human_label="state filter convention",
        explanation=(
            "Issue trackers and CRMs name states differently: 'open' "
            "vs 'opened' vs 'OPEN' vs status_id=1."
        ),
        failure_mode=(
            "An agent filters by state='open' on Linear; passes the "
            "result to a GitHub-flavored tool that expects "
            "state='opened' and returns nothing."
        ),
    ),
    # ── Domain-specific dimensions from seed packs ──
    "industry_code": DimensionExplanation(
        name="industry_code",
        human_label="industry classification",
        explanation=(
            "NAICS codes are 6-digit US-centric ('541110' = Offices of "
            "Lawyers); ISIC and SIC use different code lengths and "
            "different category boundaries."
        ),
        failure_mode=(
            "A CRM stores industry='541110' (NAICS); an enrichment "
            "tool expects ISIC '6910' for the same legal-services "
            "category and tags the record as 'unknown industry'."
        ),
    ),
    "unit_of_measure": DimensionExplanation(
        name="unit_of_measure",
        human_label="unit of measure",
        explanation=(
            "UCUM codes include 'kg', 'lbf.s', 'mm[Hg]'. Some tools "
            "use plain English ('kilogram', 'pound-force-second') or "
            "domain-specific abbreviations."
        ),
        failure_mode=(
            "Mars Climate Orbiter, 1999. Lockheed sent thrust in pound-"
            "force-seconds; NASA's navigation system read newton-"
            "seconds. Mismatch by 4.45x. Spacecraft burned up in the "
            "Martian atmosphere."
        ),
    ),
    # ── Financial messaging (FIX, SWIFT, ISO-20022) ──
    "fix_msg_type": DimensionExplanation(
        name="fix_msg_type",
        human_label="FIX message type",
        explanation=(
            "FIX (Financial Information Exchange) Tag 35 identifies "
            "the message type: 'D' = NewOrderSingle, '8' = "
            "ExecutionReport. Different FIX versions add or rename "
            "types."
        ),
        failure_mode=(
            "A FIX 4.4 trading gateway sends a message with Tag "
            "35='AE' (TradeCaptureReport, added in 4.4); a FIX 4.2 "
            "downstream consumer rejects it as unknown and the trade "
            "isn't booked."
        ),
    ),
    "fix_side": DimensionExplanation(
        name="fix_side",
        human_label="FIX side code",
        explanation=(
            "FIX Tag 54 identifies buy ('1'), sell ('2'), buy-minus "
            "('3'), and sell-plus ('4'). Some adapters translate to "
            "string 'BUY'/'SELL'."
        ),
        failure_mode=(
            "A risk system that expects '1'/'2' receives 'BUY'/'SELL' "
            "from an adapter and silently treats them as 'buy-minus'/"
            "'sell-plus' (the next codes in the enum), rejecting "
            "trades for the wrong reason."
        ),
    ),
    "swift_mt_message_type": DimensionExplanation(
        name="swift_mt_message_type",
        human_label="SWIFT MT message type",
        explanation=(
            "SWIFT MT messages use 3-digit codes: MT103 (single "
            "customer credit transfer), MT202 (general financial "
            "institution transfer), etc. Some integrations pass the "
            "code as 'MT103'; others as '103'."
        ),
        failure_mode=(
            "A payment gateway expects 'MT103'; the upstream tool "
            "sends '103'. The gateway rejects with 'unknown message "
            "type' and the wire transfer doesn't go out."
        ),
    ),
    "swift_mx_message_type": DimensionExplanation(
        name="swift_mx_message_type",
        human_label="SWIFT MX message type",
        explanation=(
            "SWIFT MX messages use ISO-20022 identifiers like "
            "'pacs.008.001.08' (FIToFICustomerCreditTransfer). The "
            "MT-to-MX migration creates parallel naming for the same "
            "business event."
        ),
        failure_mode=(
            "A bank's legacy adapter sends 'MT103' and the modern "
            "core expects 'pacs.008.001.08'. The cutover playbook "
            "isn't applied at this seam, the message is rejected, the "
            "operations team sees 'unknown'."
        ),
    ),
    "iso_20022_message_type": DimensionExplanation(
        name="iso_20022_message_type",
        human_label="ISO 20022 message type",
        explanation=(
            "ISO-20022 message identifiers like 'pacs.008.001.08' "
            "encode business area, message type, variant, and version. "
            "Some integrations strip the version suffix."
        ),
        failure_mode=(
            "An upstream tool sends 'pacs.008.001.08'; the downstream "
            "validator only accepts 'pacs.008.001.09' (the newer "
            "version). The message is rejected even though the "
            "business meaning is identical."
        ),
    ),
    # ── Healthcare ──
    "fhir_resource_type": DimensionExplanation(
        name="fhir_resource_type",
        human_label="FHIR resource type",
        explanation=(
            "FHIR R4 and R5 share most resource names but renamed "
            "some: R4 'ImagingManifest' became R5 'ImagingSelection'. "
            "Resource names are also case-sensitive."
        ),
        failure_mode=(
            "An EHR running R4 sends an 'ImagingManifest' bundle; the "
            "R5 imaging viewer doesn't know that resource type and "
            "drops the entire study from the worklist."
        ),
    ),
    "icd_10_cm_code": DimensionExplanation(
        name="icd_10_cm_code",
        human_label="ICD-10-CM diagnosis code",
        explanation=(
            "ICD-10-CM (US clinical modification) annual releases add "
            "and retire codes. ICD-10-CM 'E11.9' (Type 2 diabetes) "
            "differs from ICD-9 '250.00' for the same diagnosis."
        ),
        failure_mode=(
            "A clinical pipeline reads '250.00' from a legacy chart "
            "and feeds it to a billing tool that expects ICD-10-CM. "
            "The claim is rejected as 'invalid diagnosis code'."
        ),
    ),
    "who_icd_10_code": DimensionExplanation(
        name="who_icd_10_code",
        human_label="WHO ICD-10 code",
        explanation=(
            "WHO ICD-10 (international) and ICD-10-CM (US) share the "
            "first three characters of most codes but diverge on "
            "specificity: WHO 'E11.9' is identical to CM 'E11.9' here, "
            "but other codes differ."
        ),
        failure_mode=(
            "An international research pipeline using WHO ICD-10 "
            "passes a code to a US billing tool expecting ICD-10-CM. "
            "Codes that look identical may carry different clinical "
            "meaning."
        ),
    ),
    "hl7_v2_segment": DimensionExplanation(
        name="hl7_v2_segment",
        human_label="HL7 v2 segment identifier",
        explanation=(
            "HL7 v2 messages are line-delimited segments: PID (patient "
            "ID), OBX (observation), ORC (order). Some adapters strip "
            "the segment header; others expect it."
        ),
        failure_mode=(
            "A lab adapter strips 'OBX|' from observation lines; the "
            "downstream parser expects the segment header and treats "
            "the data as part of the previous segment."
        ),
    ),
    "hl7_v2_message_type": DimensionExplanation(
        name="hl7_v2_message_type",
        human_label="HL7 v2 message type",
        explanation=(
            "HL7 v2 message types are MSH-9 codes like 'ADT^A01' "
            "(admit), 'ORM^O01' (order), 'ORU^R01' (result). Some "
            "integrations pass the prefix only ('ADT')."
        ),
        failure_mode=(
            "An admission feed passes 'ADT' to a downstream router "
            "expecting 'ADT^A01'. The router can't distinguish admit "
            "from discharge and processes both as the same event."
        ),
    ),
    "umls_concept_id": DimensionExplanation(
        name="umls_concept_id",
        human_label="UMLS concept identifier",
        explanation=(
            "UMLS Metathesaurus CUIs are 8-character codes like "
            "'C0011847' (diabetes mellitus). Some tools pass the "
            "code; others a SNOMED, ICD-10, or LOINC equivalent."
        ),
        failure_mode=(
            "A clinical NLP pipeline emits CUI 'C0011847'; the "
            "downstream phenotyper expects SNOMED '73211009'. The "
            "concept is the same, the lookup fails."
        ),
    ),
    # ── Supply chain (GS1, EDIFACT) ──
    "gs1_application_identifier": DimensionExplanation(
        name="gs1_application_identifier",
        human_label="GS1 application identifier",
        explanation=(
            "GS1 AIs are 2-4 digit prefixes that identify what data "
            "follows in a barcode: '01' = GTIN, '17' = expiry date, "
            "'21' = serial number."
        ),
        failure_mode=(
            "A warehouse scanner reads '01' followed by a 14-digit "
            "code as GTIN-14; an inventory system parses the same "
            "stream as 'AI 0' followed by 1-prefixed code and lists "
            "the wrong product."
        ),
    ),
    "gs1_id_key_type": DimensionExplanation(
        name="gs1_id_key_type",
        human_label="GS1 identification key",
        explanation=(
            "GS1 identification keys: GTIN (trade items), GLN "
            "(locations), SSCC (logistic units), GRAI (returnable "
            "assets). Some integrations conflate them."
        ),
        failure_mode=(
            "A traceability event tags the location as a GTIN by "
            "mistake; the downstream EPCIS receiver tries to look up "
            "the warehouse in the product catalog and fails."
        ),
    ),
    "edifact_message_type": DimensionExplanation(
        name="edifact_message_type",
        human_label="UN/EDIFACT message type",
        explanation=(
            "UN/EDIFACT messages are 6-character codes: INVOIC "
            "(invoice), ORDERS (purchase order), DESADV (dispatch "
            "advice). Versions D.21A and D.21B may differ on "
            "individual fields."
        ),
        failure_mode=(
            "A trading partner sends a D.21B INVOIC with a new "
            "segment; the legacy D.21A parser ignores it and the "
            "invoice posts without the new tax breakdown."
        ),
    ),
    # ── Finance edge dimensions present in the community pack ──
    "day_count_convention": DimensionExplanation(
        name="day_count_convention",
        human_label="day-count convention",
        explanation=(
            "Day-count conventions for accrued interest include "
            "30/360, ACT/360, ACT/365, and ACT/ACT. Bond markets "
            "settle on different defaults than money markets."
        ),
        failure_mode=(
            "A bond pricer assumes 30/360; the trading desk's "
            "settlement system uses ACT/365. The same coupon accrues "
            "different amounts and the trade reconciles as a "
            "fractional-cent break."
        ),
    ),
    "fee_basis": DimensionExplanation(
        name="fee_basis",
        human_label="fee calculation basis",
        explanation=(
            "Fees may be a flat amount, a percentage of notional, "
            "basis points, or per-share. The same field name 'fee' "
            "can mean any of them."
        ),
        failure_mode=(
            "An order management system records fee=15 meaning 15 bps; "
            "a clearing tool reads it as $15 flat. Fees are 100x off "
            "until reconciliation catches the gap."
        ),
    ),
    "rounding_mode": DimensionExplanation(
        name="rounding_mode",
        human_label="rounding mode",
        explanation=(
            "Numeric rounding can be HALF_UP (5 rounds up), "
            "HALF_EVEN (banker's rounding, 5 rounds to nearest even), "
            "FLOOR, CEILING, or TRUNCATE."
        ),
        failure_mode=(
            "A payment processor uses HALF_UP; the bank's recon "
            "system uses HALF_EVEN. Many small transactions reconcile "
            "with persistent fractional-cent differences."
        ),
    ),
    "settlement_cycle": DimensionExplanation(
        name="settlement_cycle",
        human_label="settlement cycle",
        explanation=(
            "T+1, T+2, T+3 indicate trade-date plus N business days "
            "for settlement. The US moved most equities to T+1 in "
            "May 2024; some markets remain T+2."
        ),
        failure_mode=(
            "A trade entered against a T+1 expectation but routed to "
            "a T+2 venue settles a day later than the position-keeper "
            "shows. P&L reports that day are off."
        ),
    ),
}


def explain(dimension: str) -> DimensionExplanation:
    """Look up a dimension; return the fallback when not registered.

    bulla's edge-inference layer appends ``_match`` to dimension
    names when emitting BlindSpot rows (e.g. ``path_convention``
    becomes ``path_convention_match``). The lookup tries both the
    raw name and the stripped form so callers don't have to know
    about the suffix.

    The fallback's ``name`` is the queried dimension (so callers can
    still produce useful narrative output even for unknown dimensions
    — they get the dimension name plus a generic explanation).
    """
    entry = EXPLANATIONS.get(dimension)
    if entry is not None:
        return entry
    if dimension.endswith("_match"):
        stripped = dimension[: -len("_match")]
        entry = EXPLANATIONS.get(stripped)
        if entry is not None:
            return entry
    return DimensionExplanation(
        name=dimension,
        human_label=_FALLBACK.human_label,
        explanation=_FALLBACK.explanation,
        failure_mode=_FALLBACK.failure_mode,
    )


__all__ = [
    "DimensionExplanation",
    "EXPLANATIONS",
    "explain",
]
