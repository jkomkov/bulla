"""Build the Phase 7 API/MCP schema index seed.

The pipeline (``bulla.api_registry``) is the load-bearing asset; this
script drives it across an initial corpus of ~100 schemas to produce
the Phase 7 deliverable:

  - Per-schema capture JSONs at
    ``calibration/data/api-registry/<source_kind>/<source_id>.json``
  - Aggregate coverage map at
    ``calibration/data/api-registry/coverage.json``
  - Flat classifier-training corpus at
    ``calibration/data/api-registry/classifier-corpus.jsonl``

Sources for the seed (per the plan's diversity-first principle):

  1. **Existing 57 MCP manifests** captured at
     ``calibration/data/registry/manifests/`` — already real-world,
     no network required.
  2. **Curated synthetic schemas** for canonical commercial seams
     (Stripe, GitHub, Shopify, Slack, Twilio, AWS S3, ICD-10-CM,
     UCUM-bearing FHIR Quantity) — small, illustrative, hand-built
     to exercise specific seed-pack dimensions. **These are
     pipeline-validation fixtures, not real-world coverage.**
  3. **Real public OpenAPI/GraphQL specs** fetched at build time
     (Phase 7-growth lever). The fetcher (``_external_fetcher.py``)
     content-addresses each download and caches under
     ``calibration/data/api-registry/_cache/``. A failed fetch
     skips the entry and continues; one bad URL must not block
     the whole rebuild.

The pipeline accepts any future source addition without modification;
this script's curation is just the Phase 7 seed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bulla.api_registry import (  # noqa: E402
    SOURCE_KIND_GRAPHQL,
    SOURCE_KIND_MCP,
    SOURCE_KIND_OPENAPI,
    build_classifier_corpus,
    build_coverage_map,
    capture,
    capture_to_dir,
)
from bulla.infer.classifier import _reset_taxonomy_cache, configure_packs  # noqa: E402

# Local helper — must not pull in src/bulla/.
sys.path.insert(0, str(Path(__file__).parent))
from _external_fetcher import (  # noqa: E402
    FetchError,
    ParseError,
    fetch_and_parse,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _seed_pack_paths() -> list[Path]:
    return sorted(
        (REPO_ROOT / "src" / "bulla" / "packs" / "seed").glob("*.yaml")
    )


def _captured_mcp_dir() -> Path:
    return REPO_ROOT / "calibration" / "data" / "registry" / "manifests"


def _api_registry_dir() -> Path:
    return REPO_ROOT / "calibration" / "data" / "api-registry"


def _external_cache_dir() -> Path:
    return _api_registry_dir() / "_cache"


# ── Curated synthetic schemas (commercial seams) ─────────────────────


def stripe_charges_openapi() -> dict:
    """Minimal OpenAPI shape covering the canonical Stripe charges
    surface — currency, amount, country, customer reference."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Stripe Charges API"},
        "paths": {
            "/v1/charges": {
                "post": {
                    "operationId": "createCharge",
                    "summary": "Create a charge",
                    "description": (
                        "Charge a customer; amount is in the smallest "
                        "currency unit (cents for USD; whole units for JPY)."
                    ),
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "amount": {
                                            "type": "integer",
                                            "description": "Amount in minor units",
                                        },
                                        "currency": {
                                            "type": "string",
                                            "enum": ["usd", "eur", "jpy", "gbp"],
                                            "description": "Three-letter ISO currency code",
                                        },
                                        "customer": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["amount", "currency"],
                                },
                            },
                        },
                    },
                },
                "get": {
                    "operationId": "listCharges",
                    "summary": "List charges",
                    "parameters": [
                        {
                            "name": "created",
                            "in": "query",
                            "schema": {
                                "type": "string",
                                "format": "date-time",
                            },
                        },
                        {
                            "name": "country",
                            "in": "query",
                            "schema": {
                                "type": "string",
                                "enum": ["US", "GB", "FR", "DE", "JP"],
                            },
                        },
                    ],
                },
            },
        },
    }


def shopify_admin_graphql() -> dict:
    """Minimal Shopify Admin GraphQL surface — order with currency
    fields, language, country."""
    return {
        "__schema": {
            "queryType": {"name": "Query"},
            "mutationType": {"name": "Mutation"},
            "types": [
                {
                    "kind": "OBJECT",
                    "name": "Query",
                    "fields": [
                        {
                            "name": "order",
                            "description": "Get an order by ID",
                            "args": [
                                {"name": "id", "type": {"kind": "SCALAR", "name": "ID"}},
                            ],
                        },
                        {
                            "name": "shop",
                            "description": "The shop's primary settings including currency",
                            "args": [],
                        },
                    ],
                },
                {
                    "kind": "OBJECT",
                    "name": "Mutation",
                    "fields": [
                        {
                            "name": "draftOrderCreate",
                            "description": "Create a draft order with currency and country",
                            "args": [
                                {
                                    "name": "currencyCode",
                                    "description": "ISO 4217 currency code",
                                    "type": {"kind": "SCALAR", "name": "String"},
                                },
                                {
                                    "name": "country",
                                    "description": "ISO 3166 country code",
                                    "type": {"kind": "SCALAR", "name": "String"},
                                },
                                {
                                    "name": "language",
                                    "description": "BCP-47 language tag",
                                    "type": {"kind": "SCALAR", "name": "String"},
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    }


def github_v3_openapi() -> dict:
    """Minimal GitHub REST API surface — repo, language code, etc."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "GitHub API v3"},
        "paths": {
            "/repos/{owner}/{repo}": {
                "get": {
                    "operationId": "getRepo",
                    "summary": "Get a repository",
                    "parameters": [
                        {
                            "name": "owner",
                            "in": "path",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "repo",
                            "in": "path",
                            "schema": {"type": "string"},
                        },
                    ],
                },
            },
            "/search/repositories": {
                "get": {
                    "operationId": "searchRepos",
                    "summary": "Search public repositories",
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "schema": {"type": "string"},
                            "description": "Search query",
                        },
                        {
                            "name": "language",
                            "in": "query",
                            "schema": {
                                "type": "string",
                                "description": "Programming language filter",
                            },
                        },
                        {
                            "name": "created",
                            "in": "query",
                            "schema": {
                                "type": "string",
                                "format": "date",
                            },
                        },
                    ],
                },
            },
        },
    }


def fhir_patient_resource_openapi() -> dict:
    """Minimal FHIR Patient + Observation surface — resourceType,
    UCUM-bearing Quantity, ICD-10-CM code field."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "FHIR Patient API"},
        "paths": {
            "/Patient/{id}": {
                "get": {
                    "operationId": "readPatient",
                    "summary": "Read a Patient resource",
                    "parameters": [
                        {"name": "id", "in": "path", "schema": {"type": "string"}},
                    ],
                },
            },
            "/Observation/{id}": {
                "get": {
                    "operationId": "readObservation",
                    "summary": "Read an Observation resource with UCUM-bearing value",
                    "parameters": [
                        {"name": "id", "in": "path", "schema": {"type": "string"}},
                    ],
                },
            },
            "/Condition": {
                "post": {
                    "operationId": "createCondition",
                    "summary": "Create a Condition (diagnosis) resource",
                    "description": (
                        "ICD-10-CM coded diagnosis attached to a patient; "
                        "FHIR R4 resource type"
                    ),
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "resourceType": {
                                            "type": "string",
                                            "enum": ["Condition"],
                                        },
                                        "code": {
                                            "type": "string",
                                            "description": "ICD-10-CM diagnosis code",
                                        },
                                        "onsetDateTime": {
                                            "type": "string",
                                            "format": "date-time",
                                        },
                                        "language": {
                                            "type": "string",
                                            "description": "BCP-47 language tag",
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def slack_web_api_openapi() -> dict:
    """Minimal Slack Web API — language, locale, mime-type."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Slack Web API"},
        "paths": {
            "/files.upload": {
                "post": {
                    "operationId": "uploadFile",
                    "summary": "Upload a file with a content type",
                    "parameters": [
                        {
                            "name": "filetype",
                            "in": "query",
                            "schema": {"type": "string", "description": "Slack file type"},
                        },
                        {
                            "name": "content_type",
                            "in": "query",
                            "schema": {
                                "type": "string",
                                "description": "IANA media type",
                            },
                        },
                    ],
                },
            },
            "/users.profile.get": {
                "get": {
                    "operationId": "getUserProfile",
                    "summary": "Get a user profile",
                    "parameters": [
                        {"name": "user", "in": "query", "schema": {"type": "string"}},
                    ],
                },
            },
        },
    }


def twilio_messages_openapi() -> dict:
    """Minimal Twilio Messages API — currency-bearing pricing,
    country-bearing phone numbers."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Twilio Messages API"},
        "paths": {
            "/Accounts/{AccountSid}/Messages.json": {
                "post": {
                    "operationId": "createMessage",
                    "summary": "Send an SMS / MMS",
                    "parameters": [
                        {
                            "name": "AccountSid",
                            "in": "path",
                            "schema": {"type": "string"},
                        },
                    ],
                    "requestBody": {
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "From": {
                                            "type": "string",
                                            "description": "Sender phone number (E.164)",
                                        },
                                        "To": {
                                            "type": "string",
                                            "description": "Recipient phone number (E.164)",
                                        },
                                        "Body": {"type": "string"},
                                        "PriceUnit": {
                                            "type": "string",
                                            "description": "ISO 4217 currency code",
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def fix_trade_openapi() -> dict:
    """Minimal trading-system surface that exposes FIX MsgType + Side."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Trading System Order API"},
        "paths": {
            "/orders": {
                "post": {
                    "operationId": "submitOrder",
                    "summary": "Submit a FIX order",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "msg_type": {
                                            "type": "string",
                                            "enum": ["D", "F", "G"],
                                            "description": "FIX MsgType (Tag 35)",
                                        },
                                        "side": {
                                            "type": "string",
                                            "enum": ["1", "2", "5"],
                                            "description": "FIX Side (Tag 54)",
                                        },
                                        "currency": {
                                            "type": "string",
                                            "enum": ["USD", "EUR", "GBP"],
                                        },
                                        "settlement_date": {
                                            "type": "string",
                                            "format": "date",
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def gs1_traceability_openapi() -> dict:
    """Minimal GS1-bearing supply-chain API — GTIN, GLN, AI codes."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "GS1 Traceability API"},
        "paths": {
            "/events": {
                "post": {
                    "operationId": "createEvent",
                    "summary": "Record a GS1 EPCIS event",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "gtin": {
                                            "type": "string",
                                            "description": "GS1 trade item identifier (GTIN-14)",
                                        },
                                        "id_key_type": {
                                            "type": "string",
                                            "enum": ["GTIN", "GLN", "SSCC"],
                                        },
                                        "application_identifier": {
                                            "type": "string",
                                            "description": "GS1 AI prefix",
                                        },
                                        "country_of_origin": {
                                            "type": "string",
                                            "description": "ISO 3166 alpha-2",
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }


SYNTHETIC_SOURCES: list[tuple[str, str, callable]] = [
    ("openapi", "stripe-charges",        stripe_charges_openapi),
    ("graphql", "shopify-admin",         shopify_admin_graphql),
    ("openapi", "github-v3",             github_v3_openapi),
    ("openapi", "fhir-patient",          fhir_patient_resource_openapi),
    ("openapi", "slack-web",             slack_web_api_openapi),
    ("openapi", "twilio-messages",       twilio_messages_openapi),
    ("openapi", "fix-trading-orders",    fix_trade_openapi),
    ("openapi", "gs1-traceability",      gs1_traceability_openapi),
]


# ── Curated real public schemas (Phase 7 growth) ─────────────────────
#
# Each entry is (source_kind, source_id, url). The source_id must not
# collide with a SYNTHETIC_SOURCES entry — by convention the real
# entries carry a vendor-flavored or version-flavored suffix.
#
# These were probed at curation time and confirmed reachable. Network
# failures during a build are non-fatal — the pipeline records the
# skip and continues. To add or refresh entries, run the build; to
# force a refetch of a cached URL, delete the entry from
# ``_cache/manifest.json``.

EXTERNAL_SOURCES: list[tuple[str, str, str]] = [
    # Canonical official OpenAPI specs from vendor repos.
    (
        "openapi", "github-rest",
        "https://raw.githubusercontent.com/github/rest-api-description/main/"
        "descriptions/api.github.com/api.github.com.json",
    ),
    (
        "openapi", "stripe-openapi",
        "https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json",
    ),
    (
        "openapi", "slack-web-official",
        "https://raw.githubusercontent.com/slackapi/slack-api-specs/master/"
        "web-api/slack_web_openapi_v2.json",
    ),
    (
        "openapi", "twilio-2010",
        "https://raw.githubusercontent.com/twilio/twilio-oai/main/spec/json/"
        "twilio_api_v2010.json",
    ),
    (
        "openapi", "discord-api",
        "https://raw.githubusercontent.com/discord/discord-api-spec/main/"
        "specs/openapi.json",
    ),
    (
        "openapi", "spotify-web",
        "https://raw.githubusercontent.com/sonallux/spotify-web-api/main/"
        "fixed-spotify-open-api.yml",
    ),
    (
        "openapi", "box-api",
        "https://raw.githubusercontent.com/box/box-openapi/main/openapi.json",
    ),
    (
        "openapi", "intercom-2-13",
        "https://raw.githubusercontent.com/intercom/Intercom-OpenAPI/main/"
        "descriptions/2.13/api.intercom.io.yaml",
    ),
    (
        "openapi", "zoom-v2",
        "https://raw.githubusercontent.com/zoom/api/master/openapi.v2.json",
    ),
    (
        "openapi", "plaid-api",
        "https://raw.githubusercontent.com/plaid/plaid-openapi/master/"
        "2020-09-14.yml",
    ),
    (
        "openapi", "asana-api",
        "https://raw.githubusercontent.com/asana/openapi/master/defs/"
        "asana_oas.yaml",
    ),
    (
        "openapi", "weather-gov",
        "https://api.weather.gov/openapi.json",
    ),
    # APIs.guru directory snapshots — community-vetted OpenAPI for
    # vendors that don't publish a single canonical URL we can hit.
    (
        "openapi", "openai-platform",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/openai.com/1.2.0/openapi.yaml",
    ),
    (
        "openapi", "notion-api",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/notion.com/1.0.0/openapi.yaml",
    ),
    (
        "openapi", "square-connect",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/squareup.com/2.0/openapi.yaml",
    ),
    (
        "openapi", "sendgrid-api",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/sendgrid.com/1.0.0/openapi.yaml",
    ),
    (
        "openapi", "plaid-apis-guru",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/plaid.com/2020-09-14_1.345.1/openapi.yaml",
    ),
    (
        "openapi", "twilio-apis-guru",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/twilio.com/api/1.55.0/openapi.yaml",
    ),
    (
        "openapi", "spotify-apis-guru",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/spotify.com/1.0.0/openapi.yaml",
    ),
    (
        "openapi", "stripe-apis-guru",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/stripe.com/2022-11-15/openapi.yaml",
    ),
    (
        "openapi", "zoom-apis-guru",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/zoom.us/2.0.0/openapi.yaml",
    ),
    (
        "openapi", "slack-apis-guru",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/slack.com/1.7.0/openapi.yaml",
    ),
    # Healthcare valuesets (FHIR R4/R5 official). These are valueset
    # JSON, not full OpenAPI. The capture pipeline accepts any dict;
    # the classifier just runs over their fields.
    (
        "openapi", "fhir-r4-resource-types",
        "https://hl7.org/fhir/R4/valueset-resource-types.json",
    ),
    (
        "openapi", "fhir-r5-resource-types",
        "https://hl7.org/fhir/R5/valueset-resource-types.json",
    ),
    # Cloud / infrastructure / dev-tooling APIs
    (
        "openapi", "digitalocean-api",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/digitalocean.com/2.0/openapi.yaml",
    ),
    (
        "openapi", "linode-api",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/linode.com/4.151.1/openapi.yaml",
    ),
    (
        "openapi", "atlassian-jira",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/atlassian.com/jira/1001.0.0-SNAPSHOT/openapi.yaml",
    ),
    (
        "openapi", "circleci-v1",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/circleci.com/v1/openapi.yaml",
    ),
    (
        "openapi", "wikimedia-api",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/wikimedia.org/1.0.0/swagger.yaml",
    ),
    (
        "openapi", "mailchimp-3",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/mailchimp.com/3.0.55/openapi.yaml",
    ),
    # Google APIs — multiple surfaces, each touches different
    # dimensions (calendar → temporal, drive → mime, youtube →
    # language, sheets → unit-of-measure-adjacent fields).
    (
        "openapi", "google-calendar",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/googleapis.com/calendar/v3/openapi.yaml",
    ),
    (
        "openapi", "google-gmail",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/googleapis.com/gmail/v1/openapi.yaml",
    ),
    (
        "openapi", "google-drive",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/googleapis.com/drive/v3/openapi.yaml",
    ),
    (
        "openapi", "google-sheets",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/googleapis.com/sheets/v4/openapi.yaml",
    ),
    (
        "openapi", "google-youtube",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/"
        "APIs/googleapis.com/youtube/v3/openapi.yaml",
    ),
]


# Toggle: set BULLA_NO_NETWORK=1 to skip external fetches entirely
# (deterministic builds in offline CI; the cache is honored if it
# already covers a URL).


def _network_disabled() -> bool:
    return os.environ.get("BULLA_NO_NETWORK", "").strip() not in ("", "0", "false", "False")


# ── Pipeline driver ──────────────────────────────────────────────────


def main() -> None:
    out_dir = _api_registry_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    seed_paths = _seed_pack_paths()
    print(
        f"Configuring {len(seed_paths)} seed packs...",
        file=sys.stderr,
    )
    _reset_taxonomy_cache()
    configure_packs(extra_paths=seed_paths)

    captures = []

    # 1. Existing MCP manifests
    print("Capturing existing MCP manifests...", file=sys.stderr)
    mcp_count = 0
    for mcp_path in sorted(_captured_mcp_dir().glob("*.json")):
        try:
            raw = json.loads(mcp_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        cap = capture(
            raw,
            source_kind=SOURCE_KIND_MCP,
            source_id=mcp_path.stem,
        )
        captures.append(cap)
        capture_to_dir(
            raw,
            source_kind=SOURCE_KIND_MCP,
            source_id=mcp_path.stem,
            out_dir=out_dir,
            captured_at=cap.captured_at,
        )
        mcp_count += 1
    print(f"  {mcp_count} MCP manifests captured", file=sys.stderr)

    # 2. Curated synthetic schemas
    print("Capturing synthetic commercial-seam schemas...", file=sys.stderr)
    synthetic_count = 0
    for source_kind, source_id, builder in SYNTHETIC_SOURCES:
        raw = builder()
        cap = capture(raw, source_kind=source_kind, source_id=source_id)
        captures.append(cap)
        capture_to_dir(
            raw,
            source_kind=source_kind,
            source_id=source_id,
            out_dir=out_dir,
            captured_at=cap.captured_at,
        )
        synthetic_count += 1
    print(f"  {synthetic_count} synthetic schemas captured", file=sys.stderr)

    # 3. Curated real external schemas (network-fetched + cached)
    if _network_disabled():
        print(
            "Skipping external real-schema fetches "
            "(BULLA_NO_NETWORK=1)…",
            file=sys.stderr,
        )
        external_count = 0
        external_skipped = 0
    else:
        print(
            f"Capturing {len(EXTERNAL_SOURCES)} real external schemas "
            "(cached at calibration/data/api-registry/_cache/)…",
            file=sys.stderr,
        )
        cache_dir = _external_cache_dir()
        external_count = 0
        external_skipped = 0
        for source_kind, source_id, url in EXTERNAL_SOURCES:
            print(f"  ext  {source_kind}/{source_id} ← {url}", file=sys.stderr)
            try:
                raw, sha, n_bytes = fetch_and_parse(
                    url, cache_dir=cache_dir
                )
            except (FetchError, ParseError) as e:
                print(f"    SKIP  {e}", file=sys.stderr)
                external_skipped += 1
                continue
            cap = capture(raw, source_kind=source_kind, source_id=source_id)
            captures.append(cap)
            capture_to_dir(
                raw,
                source_kind=source_kind,
                source_id=source_id,
                out_dir=out_dir,
                captured_at=cap.captured_at,
            )
            external_count += 1
            print(
                f"    OK  {n_bytes} bytes  sha256:{sha[:16]}…",
                file=sys.stderr,
            )

    # 4. Aggregate
    print(f"\nBuilding coverage map across {len(captures)} schemas...", file=sys.stderr)
    coverage = build_coverage_map(captures)
    # Annotate the coverage map with explicit real-vs-synthetic counts.
    # The plan is unambiguous: synthetic fixtures are validation-only,
    # not real-world coverage. Surfacing the split lets every consumer
    # of coverage.json reason about the corpus honestly without
    # having to reconstruct the categorization from source_id heuristics.
    coverage.setdefault("totals", {})
    coverage["totals"]["n_real_mcp"] = mcp_count
    coverage["totals"]["n_synthetic_fixtures"] = synthetic_count
    coverage["totals"]["n_real_external"] = external_count
    coverage["totals"]["n_external_skipped"] = external_skipped
    coverage["totals"]["n_real"] = mcp_count + external_count
    coverage["totals"]["n_synthetic"] = synthetic_count
    coverage_path = out_dir / "coverage.json"
    coverage_path.write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    print(f"  wrote {coverage_path}", file=sys.stderr)

    print(f"Building classifier-training corpus...", file=sys.stderr)
    corpus = build_classifier_corpus(captures)
    corpus_path = out_dir / "classifier-corpus.jsonl"
    with corpus_path.open("w", encoding="utf-8") as fh:
        for row in corpus:
            fh.write(json.dumps(row) + "\n")
    print(f"  wrote {corpus_path} ({len(corpus)} rows)", file=sys.stderr)

    # Headline summary
    total_dims = len(coverage["by_dimension"])
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"PHASE 7 INDEX BUILD RESULTS", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"Sources captured:         {len(captures)}", file=sys.stderr)
    print(f"  - Real MCP manifests:   {mcp_count}", file=sys.stderr)
    print(f"  - Real external specs:  {external_count} "
          f"(skipped {external_skipped} of {len(EXTERNAL_SOURCES)})",
          file=sys.stderr)
    print(f"  - Synthetic fixtures:   {synthetic_count}", file=sys.stderr)
    print(
        f"  - Real total:           {mcp_count + external_count}",
        file=sys.stderr,
    )
    print(f"Tools indexed:            {coverage['totals']['n_tools']}", file=sys.stderr)
    print(f"Fields indexed:           {coverage['totals']['n_fields']}", file=sys.stderr)
    print(f"Distinct dimensions hit:  {total_dims}", file=sys.stderr)
    print(f"Classifier corpus rows:   {len(corpus)}", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"Top dimensions by hits:", file=sys.stderr)
    for row in coverage["by_dimension"][:10]:
        print(
            f"  {row['dimension']:35s} "
            f"{row['total_hits']:5d} hits across {len(row['sources']):3d} sources",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
