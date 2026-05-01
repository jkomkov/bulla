"""Reconstruct ~30 historical mismatch incidents as Bulla composition YAMLs.

Each incident is a real, documented, multi-million-dollar (or fatal)
production failure caused by a convention mismatch across a tool seam.
The reconstructed YAMLs serve as:

1. **Validation fixtures.** Phase 5 metric: ≥80% of incidents must
   produce a non-zero coherence fee under the relevant seed pack.
2. **Demo material.** A single end-to-end demo composition crossing
   ISO-4217 + FHIR + ICD-10 seams is built from the JPY-zero-decimal
   incident plus one healthcare incident.
3. **Anti-marketing-bullshit.** Every incident has a citation in
   ``benchmark/coherence-gym/BABEL_PAPER.md §2`` or in the
   incident's ``description`` field; the framework only earns its
   keep if these are *detectable*.

The output is a directory of YAML files plus a manifest
(``incidents-manifest.json``) listing every incident with metadata
(domain, dimension, primary pack, source citation).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml


# ── Incident specifications ──────────────────────────────────────────


def _composition(
    name: str,
    description: str,
    tools: dict[str, dict],
    edges: list[dict],
) -> dict:
    """Build a composition dict in Bulla's pack-loadable shape."""
    return {
        "name": name,
        "description": description,
        "tools": tools,
        "edges": edges,
    }


def mars_climate_orbiter() -> dict:
    """1999 — $327.6M loss. Lockheed Martin output thruster impulse in
    lbf·s; NASA JPL navigation expected N·s. Factor of 4.45.
    Source: NASA Mishap Investigation Board (1999)."""
    return _composition(
        name="mars_climate_orbiter",
        description=(
            "1999 Mars Climate Orbiter ($327.6M). Lockheed Martin "
            "thruster software output impulse in pound-force·second; "
            "NASA JPL navigation expected newton·second. Factor 4.45 "
            "error accumulated over 9.5 months. UCUM detects: "
            "amount_unit boundary fee on impulse field."
        ),
        tools={
            "lockheed_thruster": {
                "internal_state": [
                    "impulse",
                    "thrust_duration",
                    "burn_id",
                    "force_unit",  # lbf
                ],
                "observable_schema": [
                    "impulse",
                    "thrust_duration",
                    "burn_id",
                    # force_unit is in internal_state but NOT exposed
                ],
            },
            "jpl_nav": {
                "internal_state": [
                    "impulse",
                    "trajectory",
                    "force_unit",  # N
                ],
                "observable_schema": [
                    "impulse",
                    "trajectory",
                ],
            },
        },
        edges=[{
            "from": "lockheed_thruster",
            "to": "jpl_nav",
            "dimensions": [{
                "name": "force_unit_match",
                "from_field": "force_unit",
                "to_field": "force_unit",
            }],
        }],
    )


def drupal_stripe_jpy() -> dict:
    """Recurring incident (5+ libraries). Drupal Commerce + Stripe JPY
    integration multiplies amount by 100 (assumes 2-decimal currency).
    For zero-decimal yen, ¥3,000 → ¥300,000. 100× overcharge.
    Source: Drupal.org issue queue + Stripe forum."""
    return _composition(
        name="drupal_stripe_jpy",
        description=(
            "Drupal Commerce + Stripe JPY 100× overcharge (recurring). "
            "Integration multiplies amount by 100 assuming 2-decimal "
            "currency. JPY is zero-decimal. ISO-4217 detects: "
            "currency_code mismatch on amount field."
        ),
        tools={
            "drupal_commerce": {
                "internal_state": [
                    "amount",
                    "currency",
                    "minor_unit_factor",  # 100 (assumes 2-decimal)
                    "order_id",
                ],
                "observable_schema": [
                    "amount",
                    "currency",
                    "order_id",
                    # minor_unit_factor is hidden — the convention
                    # that drives the mismatch is invisible at the seam
                ],
            },
            "stripe_charge": {
                "internal_state": [
                    "amount_minor",
                    "currency",
                    "charge_id",
                ],
                "observable_schema": [
                    "amount_minor",
                    "currency",
                    "charge_id",
                ],
            },
        },
        edges=[{
            "from": "drupal_commerce",
            "to": "stripe_charge",
            "dimensions": [{
                "name": "minor_unit_convention_match",
                "from_field": "minor_unit_factor",
            }],
        }],
    )


def vancouver_stock_exchange() -> dict:
    """1982-83. Index truncated to 3 dp instead of rounded.
    Recalculated ~3000×/day. Index dragged from 1000 to 524.811 over 22
    months (true value: 1098.892). Source: SEC.gov writeup."""
    return _composition(
        name="vancouver_stock_exchange",
        description=(
            "Vancouver Stock Exchange 1982-83. Index truncated to 3dp "
            "instead of rounded; recalculated ~3000×/day. Self-loop "
            "with β₁=1; even minimal per-step bias compounds. UCUM/ "
            "iso-8601 detects: precision boundary fee on accumulator."
        ),
        tools={
            "trade_aggregator": {
                "internal_state": [
                    "raw_index_value",
                    "rounding_mode",  # truncate
                    "decimal_places",
                ],
                "observable_schema": [
                    "raw_index_value",
                ],
            },
            "index_publisher": {
                "internal_state": [
                    "published_index",
                    "rounding_mode",  # round_half_up assumed
                    "decimal_places",
                ],
                "observable_schema": [
                    "published_index",
                ],
            },
            "trade_aggregator_t1": {  # the same aggregator at next tick
                "internal_state": [
                    "raw_index_value",
                    "rounding_mode",
                    "decimal_places",
                ],
                "observable_schema": [
                    "raw_index_value",
                ],
            },
        },
        edges=[
            {
                "from": "trade_aggregator",
                "to": "index_publisher",
                "dimensions": [{
                    "name": "rounding_match",
                    "from_field": "rounding_mode",
                    "to_field": "rounding_mode",
                }],
            },
            {
                "from": "index_publisher",
                "to": "trade_aggregator_t1",
                "dimensions": [{
                    "name": "feedback_rounding_match",
                    "from_field": "rounding_mode",
                    "to_field": "rounding_mode",
                }],
            },
        ],
    )


def patriot_missile_dhahran() -> dict:
    """1991 — 28 killed. Patriot timing software 24-bit fixed-point
    accumulated drift over 100 hours of operation. Source: GAO IMTEC-92-26."""
    return _composition(
        name="patriot_missile_dhahran",
        description=(
            "1991 Patriot missile Dhahran (28 killed). Timing software "
            "24-bit fixed-point precision drift over 100 hours produced "
            "0.34s tracking error. UCUM detects: precision boundary fee "
            "on time field."
        ),
        tools={
            "radar_tracker": {
                "internal_state": [
                    "tick_count",
                    "tick_precision_bits",  # 24
                    "elapsed_seconds",
                ],
                "observable_schema": [
                    "tick_count",
                    "elapsed_seconds",
                ],
            },
            "intercept_calc": {
                "internal_state": [
                    "elapsed_seconds",
                    "tick_precision_bits",  # 64 expected
                    "intercept_window",
                ],
                "observable_schema": [
                    "elapsed_seconds",
                    "intercept_window",
                ],
            },
        },
        edges=[{
            "from": "radar_tracker",
            "to": "intercept_calc",
            "dimensions": [{
                "name": "precision_match",
                "from_field": "tick_precision_bits",
                "to_field": "tick_precision_bits",
            }],
        }],
    )


def gimli_glider() -> dict:
    """1983. Air Canada 767 ran out of fuel mid-flight: ground crew
    fueled in lbs/liter; aircraft expected kg/liter. Factor 2.2.
    Source: Transport Canada Aviation Safety Analysis."""
    return _composition(
        name="gimli_glider",
        description=(
            "1983 Air Canada Gimli Glider (near-catastrophe). Ground "
            "crew used lbs/liter for fuel density; new metric 767 "
            "expected kg/liter. Aircraft ran out at 41,000 feet. UCUM "
            "detects: amount_unit boundary fee on density field."
        ),
        tools={
            "ground_fueling": {
                "internal_state": [
                    "fuel_mass",
                    "fuel_volume",
                    "density_unit",  # lb/L
                ],
                "observable_schema": [
                    "fuel_mass",
                    "fuel_volume",
                ],
            },
            "flight_planning": {
                "internal_state": [
                    "fuel_mass",
                    "fuel_volume",
                    "density_unit",  # kg/L
                ],
                "observable_schema": [
                    "fuel_mass",
                    "fuel_volume",
                ],
            },
        },
        edges=[{
            "from": "ground_fueling",
            "to": "flight_planning",
            "dimensions": [{
                "name": "density_unit_match",
                "from_field": "density_unit",
                "to_field": "density_unit",
            }],
        }],
    )


def libor_sofr_transition() -> dict:
    """2020. LCH/CME big-bang discounting switch. Pre-conversion swap
    payments calculated at LIBOR; post-conversion at SOFR. Affected
    $154T notional. Source: ARRC progress reports."""
    return _composition(
        name="libor_sofr_transition",
        description=(
            "2020 LIBOR→SOFR big-bang ($154T notional affected). LCH "
            "and CME switched discount curves on different days; cross-"
            "venue netting failed. FIX detects: rate_basis boundary fee."
        ),
        tools={
            "lch_clearing": {
                "internal_state": [
                    "swap_id",
                    "discount_rate",
                    "rate_basis",  # SOFR (post-switch)
                    "valuation_date",
                ],
                "observable_schema": [
                    "swap_id",
                    "discount_rate",
                    "valuation_date",
                ],
            },
            "cme_clearing": {
                "internal_state": [
                    "swap_id",
                    "discount_rate",
                    "rate_basis",  # LIBOR (pre-switch)
                    "valuation_date",
                ],
                "observable_schema": [
                    "swap_id",
                    "discount_rate",
                    "valuation_date",
                ],
            },
            "cross_venue_netting": {
                "internal_state": [
                    "net_amount",
                    "rate_basis",
                ],
                "observable_schema": ["net_amount"],
            },
        },
        edges=[
            {
                "from": "lch_clearing",
                "to": "cross_venue_netting",
                "dimensions": [{
                    "name": "rate_basis_match",
                    "from_field": "rate_basis",
                    "to_field": "rate_basis",
                }],
            },
            {
                "from": "cme_clearing",
                "to": "cross_venue_netting",
                "dimensions": [{
                    "name": "rate_basis_match_b",
                    "from_field": "rate_basis",
                    "to_field": "rate_basis",
                }],
            },
        ],
    )


def t1_settlement_transition() -> dict:
    """2024. US equity settlement moved T+2 → T+1. Cross-border
    counterparties on T+2 had $3.1B aggregate margin impact.
    Source: ICI/SIFMA T+1 implementation reports."""
    return _composition(
        name="t1_settlement_transition",
        description=(
            "2024 US T+1 settlement transition ($3.1B margin impact). "
            "US moved T+2→T+1; non-US counterparties stayed T+2; "
            "cross-border trades had cash-flow gaps. ISO-8601 detects: "
            "settlement_cycle boundary fee."
        ),
        tools={
            "us_dtcc": {
                "internal_state": [
                    "trade_id",
                    "settlement_date",
                    "settlement_cycle",  # T+1
                ],
                "observable_schema": ["trade_id", "settlement_date"],
            },
            "eu_clearer": {
                "internal_state": [
                    "trade_id",
                    "settlement_date",
                    "settlement_cycle",  # T+2
                ],
                "observable_schema": ["trade_id", "settlement_date"],
            },
        },
        edges=[{
            "from": "us_dtcc",
            "to": "eu_clearer",
            "dimensions": [{
                "name": "settlement_cycle_match",
                "from_field": "settlement_cycle",
                "to_field": "settlement_cycle",
            }],
        }],
    )


def levothyroxine_mg_mcg() -> dict:
    """Ongoing. 19% of pediatric tenfold dosing errors involve mg/mcg
    confusion; levothyroxine is one of the most-cited examples.
    Source: ISMP Canada (2017)."""
    return _composition(
        name="levothyroxine_mg_mcg",
        description=(
            "Ongoing levothyroxine mg/mcg dosing errors (19% of "
            "pediatric tenfold incidents). UCUM detects: amount_unit "
            "boundary fee on dose field."
        ),
        tools={
            "ehr_order_entry": {
                "internal_state": [
                    "drug_id",
                    "dose_amount",
                    "dose_unit",  # mg
                    "patient_id",
                ],
                "observable_schema": [
                    "drug_id",
                    "dose_amount",
                    "patient_id",
                ],
            },
            "pharmacy_dispense": {
                "internal_state": [
                    "drug_id",
                    "dose_amount",
                    "dose_unit",  # mcg
                    "patient_id",
                ],
                "observable_schema": [
                    "drug_id",
                    "dose_amount",
                    "patient_id",
                ],
            },
        },
        edges=[{
            "from": "ehr_order_entry",
            "to": "pharmacy_dispense",
            "dimensions": [{
                "name": "dose_unit_match",
                "from_field": "dose_unit",
                "to_field": "dose_unit",
            }],
        }],
    )


def icd_9_to_icd_10_transition() -> dict:
    """2015. US ICD-9 → ICD-10-CM cutover. Claims rejected at scale
    when biller and payer disagreed on which codeset was in force on
    the service date. Source: AHIMA / CMS."""
    return _composition(
        name="icd_9_to_icd_10_transition",
        description=(
            "2015 US ICD-9 → ICD-10-CM cutover. Service date vs. "
            "submission date confusion drove ~10% claim rejection in "
            "first quarter. icd-10-cm detects: code_system boundary "
            "fee."
        ),
        tools={
            "biller": {
                "internal_state": [
                    "claim_id",
                    "diagnosis_code",
                    "code_system",  # ICD-9
                    "service_date",
                ],
                "observable_schema": [
                    "claim_id", "diagnosis_code", "service_date",
                ],
            },
            "payer": {
                "internal_state": [
                    "claim_id",
                    "diagnosis_code",
                    "code_system",  # ICD-10
                    "service_date",
                ],
                "observable_schema": [
                    "claim_id", "diagnosis_code", "service_date",
                ],
            },
        },
        edges=[{
            "from": "biller",
            "to": "payer",
            "dimensions": [{
                "name": "code_system_match",
                "from_field": "code_system",
                "to_field": "code_system",
            }],
        }],
    )


def fhir_r4_to_r5_breaking_changes() -> dict:
    """Recurring 2023+. Health systems running FHIR R4 ↔ R5 partners
    encounter resource-level breaking changes (ImagingManifest renamed
    to ImagingSelection, etc.). Source: HL7 FHIR R5 release notes."""
    return _composition(
        name="fhir_r4_to_r5_breaking_changes",
        description=(
            "FHIR R4 → R5 breaking changes. ImagingManifest (R4) "
            "renamed to ImagingSelection (R5) with field-level "
            "differences. fhir-r4 / fhir-r5 detects: resource_type "
            "boundary fee."
        ),
        tools={
            "r4_pacs": {
                "internal_state": [
                    "study_id",
                    "fhir_version",  # R4
                    "resource_type",  # ImagingManifest
                ],
                "observable_schema": ["study_id", "resource_type"],
            },
            "r5_emr": {
                "internal_state": [
                    "study_id",
                    "fhir_version",  # R5
                    "resource_type",  # ImagingSelection
                ],
                "observable_schema": ["study_id", "resource_type"],
            },
        },
        edges=[{
            "from": "r4_pacs",
            "to": "r5_emr",
            "dimensions": [{
                "name": "fhir_version_match",
                "from_field": "fhir_version",
                "to_field": "fhir_version",
            }],
        }],
    )


def boeing_737_max_faa_oda() -> dict:
    """2018-2019. Boeing 737 MAX FAA Organization Designation
    Authorization self-certification covered 94% of certifications.
    346 deaths in two crashes. Source: NTSB / FAA."""
    return _composition(
        name="boeing_737_max_faa_oda",
        description=(
            "2018-2019 Boeing 737 MAX MCAS (346 deaths). FAA ODA "
            "self-cert covered 94% of certifications; the seam between "
            "design data and certification authority concealed the "
            "single-sensor MCAS dependence. Generic safety-cert pack "
            "would detect: authorization_authority boundary fee."
        ),
        tools={
            "boeing_design": {
                "internal_state": [
                    "system_id",
                    "redundancy_level",  # single sensor
                    "certification_authority",  # ODA
                    "design_review_status",
                ],
                "observable_schema": [
                    "system_id",
                    "design_review_status",
                ],
            },
            "faa_oversight": {
                "internal_state": [
                    "system_id",
                    "redundancy_level",  # assumed dual
                    "certification_authority",  # FAA-direct
                    "approval_status",
                ],
                "observable_schema": [
                    "system_id",
                    "approval_status",
                ],
            },
        },
        edges=[{
            "from": "boeing_design",
            "to": "faa_oversight",
            "dimensions": [{
                "name": "certification_authority_match",
                "from_field": "certification_authority",
                "to_field": "certification_authority",
            }],
        }],
    )


def phe_covid_xls_truncation() -> dict:
    """October 2020. PHE used .XLS export which has 65,536-row limit;
    15,841 COVID-19 cases dropped over 8 days. Source: BBC / PHE statement."""
    return _composition(
        name="phe_covid_xls_truncation",
        description=(
            "October 2020 PHE COVID-19 case truncation (15,841 cases "
            "lost over 8 days). .XLS row limit 65,536 hit; later rows "
            "silently dropped. iana-media-types detects: media_type "
            "boundary fee (xls vs xlsx vs csv)."
        ),
        tools={
            "lab_export": {
                "internal_state": [
                    "case_records",
                    "export_format",  # .xls (legacy)
                    "row_count",
                ],
                "observable_schema": ["case_records", "row_count"],
            },
            "phe_ingestion": {
                "internal_state": [
                    "case_records",
                    "export_format",  # .xlsx assumed
                    "row_count",
                ],
                "observable_schema": ["case_records", "row_count"],
            },
        },
        edges=[{
            "from": "lab_export",
            "to": "phe_ingestion",
            "dimensions": [{
                "name": "export_format_match",
                "from_field": "export_format",
                "to_field": "export_format",
            }],
        }],
    )


def shopify_quickbooks_tax() -> dict:
    """Recurring. Shopify reports gross revenue; QuickBooks expects net
    after tax. Tax categorization mismatched → P&L underreporting.
    Source: vendor support forums."""
    return _composition(
        name="shopify_quickbooks_tax",
        description=(
            "Shopify→QuickBooks gross/net mismatch (recurring). "
            "Shopify reports gross; QuickBooks expects net. iso-4217 "
            "+ amount_unit detects: net_or_gross convention boundary "
            "fee."
        ),
        tools={
            "shopify_orders": {
                "internal_state": [
                    "order_id",
                    "amount",
                    "amount_basis",  # gross
                    "tax_amount",
                ],
                "observable_schema": ["order_id", "amount", "tax_amount"],
            },
            "quickbooks_pl": {
                "internal_state": [
                    "transaction_id",
                    "amount",
                    "amount_basis",  # net
                ],
                "observable_schema": ["transaction_id", "amount"],
            },
        },
        edges=[{
            "from": "shopify_orders",
            "to": "quickbooks_pl",
            "dimensions": [{
                "name": "amount_basis_match",
                "from_field": "amount_basis",
                "to_field": "amount_basis",
            }],
        }],
    )


def stripe_webhook_duplicates() -> dict:
    """Recurring. Stripe webhooks deliver at-least-once; downstream
    consumers must dedupe by event_id. Failure to dedupe → 2-3× charges.
    Source: Stripe Engineering Blog."""
    return _composition(
        name="stripe_webhook_duplicates",
        description=(
            "Stripe webhook duplicate processing (recurring). At-least-"
            "once delivery; downstream must dedupe by event_id. Without "
            "dedupe → 2-3× charges. Generic detects: delivery_semantics "
            "boundary fee."
        ),
        tools={
            "stripe_webhook": {
                "internal_state": [
                    "event_id",
                    "delivery_semantics",  # at-least-once
                    "payload",
                ],
                "observable_schema": ["event_id", "payload"],
            },
            "downstream_consumer": {
                "internal_state": [
                    "event_id",
                    "delivery_semantics",  # exactly-once assumed
                    "processed_payload",
                ],
                "observable_schema": ["event_id", "processed_payload"],
            },
        },
        edges=[{
            "from": "stripe_webhook",
            "to": "downstream_consumer",
            "dimensions": [{
                "name": "delivery_semantics_match",
                "from_field": "delivery_semantics",
                "to_field": "delivery_semantics",
            }],
        }],
    )


def nv_energy_meter_channel() -> dict:
    """2015-2020. NV Energy meter channel mismatch on FERC-regulated
    interconnection points. $685K FERC penalty. Source: FERC ER21-395-000."""
    return _composition(
        name="nv_energy_meter_channel",
        description=(
            "NV Energy meter channel mismatch ($685K FERC penalty). "
            "Channel 1 vs Channel 3 readings reported on FERC-"
            "regulated points. Generic detects: meter_channel boundary "
            "fee (id_offset family)."
        ),
        tools={
            "scada_collector": {
                "internal_state": [
                    "meter_id",
                    "reading_kwh",
                    "channel_index",  # channel 1
                ],
                "observable_schema": ["meter_id", "reading_kwh"],
            },
            "ferc_filer": {
                "internal_state": [
                    "meter_id",
                    "reading_kwh",
                    "channel_index",  # channel 3
                ],
                "observable_schema": ["meter_id", "reading_kwh"],
            },
        },
        edges=[{
            "from": "scada_collector",
            "to": "ferc_filer",
            "dimensions": [{
                "name": "channel_index_match",
                "from_field": "channel_index",
                "to_field": "channel_index",
            }],
        }],
    )


def gcv_ncv_gas_billing() -> dict:
    """Ongoing. UK natgas billing Gross Calorific Value (GCV) vs
    European Net Calorific Value (NCV). 10.8% systematic error in
    cross-border invoicing. Source: Ofgem."""
    return _composition(
        name="gcv_ncv_gas_billing",
        description=(
            "Gross/Net Calorific Value gas billing mismatch (10.8% "
            "systematic error). UK uses GCV; EU uses NCV. UCUM + "
            "energy detects: calorific_basis boundary fee."
        ),
        tools={
            "uk_supplier_meter": {
                "internal_state": [
                    "delivery_id",
                    "energy_mj",
                    "calorific_basis",  # GCV
                ],
                "observable_schema": ["delivery_id", "energy_mj"],
            },
            "eu_buyer_invoice": {
                "internal_state": [
                    "delivery_id",
                    "energy_mj",
                    "calorific_basis",  # NCV
                ],
                "observable_schema": ["delivery_id", "energy_mj"],
            },
        },
        edges=[{
            "from": "uk_supplier_meter",
            "to": "eu_buyer_invoice",
            "dimensions": [{
                "name": "calorific_basis_match",
                "from_field": "calorific_basis",
                "to_field": "calorific_basis",
            }],
        }],
    )


def lng_price_formula_disputes() -> dict:
    """Various. LNG long-term contracts have multiple pricing-formula
    conventions (Henry Hub, JKM, oil-indexed). $4B+ in arbitration
    awards over formula interpretation. Source: GIIGNL annual reports."""
    return _composition(
        name="lng_price_formula_disputes",
        description=(
            "LNG price-formula arbitration disputes ($4B+ aggregate). "
            "Henry Hub vs JKM vs oil-indexed bases mixed in long-term "
            "supply contracts. Generic + iso-4217 detects: "
            "pricing_basis boundary fee."
        ),
        tools={
            "supplier_invoice": {
                "internal_state": [
                    "cargo_id",
                    "price_per_mmbtu",
                    "pricing_basis",  # JKM
                ],
                "observable_schema": ["cargo_id", "price_per_mmbtu"],
            },
            "buyer_payment": {
                "internal_state": [
                    "cargo_id",
                    "price_per_mmbtu",
                    "pricing_basis",  # oil-indexed
                ],
                "observable_schema": ["cargo_id", "price_per_mmbtu"],
            },
        },
        edges=[{
            "from": "supplier_invoice",
            "to": "buyer_payment",
            "dimensions": [{
                "name": "pricing_basis_match",
                "from_field": "pricing_basis",
                "to_field": "pricing_basis",
            }],
        }],
    )


def gtin_check_digit_miscoding() -> dict:
    """Recurring (FDA-recall trigger). GTIN-13 vs GTIN-14 check-digit
    miscoding shifts product identity. FDA UDI compliance failures.
    Source: FDA UDI postmarket surveillance."""
    return _composition(
        name="gtin_check_digit_miscoding",
        description=(
            "GTIN check-digit miscoding (FDA-recall trigger, recurring). "
            "GTIN-13 vs GTIN-14 with leading-zero handling produces "
            "wrong product identity. gs1 detects: gs1_id_key_type "
            "boundary fee."
        ),
        tools={
            "manufacturer_label": {
                "internal_state": [
                    "product_id",
                    "gtin",
                    "gtin_format",  # GTIN-14
                ],
                "observable_schema": ["product_id", "gtin"],
            },
            "fda_udi_db": {
                "internal_state": [
                    "product_id",
                    "gtin",
                    "gtin_format",  # GTIN-13
                ],
                "observable_schema": ["product_id", "gtin"],
            },
        },
        edges=[{
            "from": "manufacturer_label",
            "to": "fda_udi_db",
            "dimensions": [{
                "name": "gtin_format_match",
                "from_field": "gtin_format",
                "to_field": "gtin_format",
            }],
        }],
    )


def edifact_d96a_d21b_drift() -> dict:
    """Recurring. EDIFACT version drift (D.96A → D.21B+) introduces
    new segments and alters semantics; legacy partners reject newer
    envelopes. Source: GS1 EDI implementation surveys."""
    return _composition(
        name="edifact_d96a_d21b_drift",
        description=(
            "EDIFACT D.96A → D.21B+ version drift (recurring). New "
            "segments and altered semantics break legacy partners. "
            "un-edifact detects: edifact_message_type boundary fee."
        ),
        tools={
            "modern_sender": {
                "internal_state": [
                    "envelope_id",
                    "directory_version",  # D.21B
                    "message_type",
                ],
                "observable_schema": ["envelope_id", "message_type"],
            },
            "legacy_receiver": {
                "internal_state": [
                    "envelope_id",
                    "directory_version",  # D.96A
                    "message_type",
                ],
                "observable_schema": ["envelope_id", "message_type"],
            },
        },
        edges=[{
            "from": "modern_sender",
            "to": "legacy_receiver",
            "dimensions": [{
                "name": "directory_version_match",
                "from_field": "directory_version",
                "to_field": "directory_version",
            }],
        }],
    )


def mt_to_iso20022_migration() -> dict:
    """Ongoing 2023-2025. SWIFT MT → ISO 20022 (MX) migration. Banks
    running both formats with imperfect translators produce field-level
    losses. Source: Swift Migration Coordination Group."""
    return _composition(
        name="mt_to_iso20022_migration",
        description=(
            "SWIFT MT → ISO 20022 (MX) migration (ongoing 2023-2025). "
            "Field-level translation losses on amount, party id, "
            "reference. swift-mt-mx + iso-20022 detects: message_format "
            "boundary fee."
        ),
        tools={
            "originating_bank": {
                "internal_state": [
                    "txn_id",
                    "amount",
                    "message_format",  # MT 103
                    "swift_field_70",  # remittance info
                ],
                "observable_schema": ["txn_id", "amount"],
            },
            "intermediary_bank": {
                "internal_state": [
                    "txn_id",
                    "amount",
                    "message_format",  # pacs.008
                    "remittance_info_xml",
                ],
                "observable_schema": ["txn_id", "amount"],
            },
        },
        edges=[{
            "from": "originating_bank",
            "to": "intermediary_bank",
            "dimensions": [{
                "name": "message_format_match",
                "from_field": "message_format",
                "to_field": "message_format",
            }],
        }],
    )


def snomed_icd10_double_coding() -> dict:
    """Recurring. EHR systems double-code (SNOMED + ICD-10) for billing
    + clinical use; cross-vocabulary reconciliation introduces gaps.
    Source: AMIA papers on terminology mapping."""
    return _composition(
        name="snomed_icd10_double_coding",
        description=(
            "SNOMED ↔ ICD-10 double-coding mismatch (recurring). EHRs "
            "code clinical findings in SNOMED; billing in ICD-10; gap "
            "reconciliation depends on UMLS. umls-mappings + icd-10-cm "
            "detects: code_system boundary fee."
        ),
        tools={
            "ehr_clinical": {
                "internal_state": [
                    "encounter_id",
                    "diagnosis_code",
                    "code_system",  # SNOMED
                ],
                "observable_schema": ["encounter_id", "diagnosis_code"],
            },
            "billing_system": {
                "internal_state": [
                    "encounter_id",
                    "diagnosis_code",
                    "code_system",  # ICD-10-CM
                ],
                "observable_schema": ["encounter_id", "diagnosis_code"],
            },
        },
        edges=[{
            "from": "ehr_clinical",
            "to": "billing_system",
            "dimensions": [{
                "name": "code_system_match",
                "from_field": "code_system",
                "to_field": "code_system",
            }],
        }],
    )


def naics_sic_classification_dispute() -> dict:
    """Recurring (US Tax Court). Companies self-classify on SIC; IRS
    re-classifies on NAICS. Tax-credit eligibility depends on
    classification → litigation. Source: US Tax Court records."""
    return _composition(
        name="naics_sic_classification_dispute",
        description=(
            "NAICS vs SIC industry classification dispute (recurring "
            "Tax Court). Self-reported SIC vs IRS-recomputed NAICS "
            "drives tax-credit eligibility differences. naics-2022 "
            "detects: industry_code boundary fee."
        ),
        tools={
            "company_filing": {
                "internal_state": [
                    "ein",
                    "industry_code",
                    "code_system",  # SIC
                ],
                "observable_schema": ["ein", "industry_code"],
            },
            "irs_audit": {
                "internal_state": [
                    "ein",
                    "industry_code",
                    "code_system",  # NAICS
                ],
                "observable_schema": ["ein", "industry_code"],
            },
        },
        edges=[{
            "from": "company_filing",
            "to": "irs_audit",
            "dimensions": [{
                "name": "code_system_match",
                "from_field": "code_system",
                "to_field": "code_system",
            }],
        }],
    )


def country_code_alpha2_alpha3_drift() -> dict:
    """Recurring. Cross-border systems exchange country codes in mixed
    alpha-2 / alpha-3 / numeric forms; lookup mismatches drop records.
    Source: vendor incident reports."""
    return _composition(
        name="country_code_alpha2_alpha3_drift",
        description=(
            "ISO 3166 alpha-2 / alpha-3 / numeric drift (recurring). "
            "Order/shipping/customs systems mix forms; receiver "
            "expecting one form drops records using another. iso-3166 "
            "detects: country_code boundary fee."
        ),
        tools={
            "checkout_form": {
                "internal_state": [
                    "order_id",
                    "country_code",
                    "code_form",  # alpha-2
                ],
                "observable_schema": ["order_id", "country_code"],
            },
            "shipping_label_printer": {
                "internal_state": [
                    "order_id",
                    "country_code",
                    "code_form",  # alpha-3
                ],
                "observable_schema": ["order_id", "country_code"],
            },
        },
        edges=[{
            "from": "checkout_form",
            "to": "shipping_label_printer",
            "dimensions": [{
                "name": "code_form_match",
                "from_field": "code_form",
                "to_field": "code_form",
            }],
        }],
    )


def language_tag_bcp47_drift() -> dict:
    """Recurring. Localization systems mix ISO 639 alpha-2/-3 and
    BCP-47 tags; missing fallback drops translations.
    Source: Mozilla / Google localization team postmortems."""
    return _composition(
        name="language_tag_bcp47_drift",
        description=(
            "Language tag drift (recurring). Localization service "
            "expects BCP-47; CMS sends ISO-639-3 alpha-3 only. iso-639 "
            "detects: language_code boundary fee."
        ),
        tools={
            "cms_publisher": {
                "internal_state": [
                    "content_id",
                    "language",
                    "language_form",  # alpha-3
                ],
                "observable_schema": ["content_id", "language"],
            },
            "translation_service": {
                "internal_state": [
                    "content_id",
                    "language",
                    "language_form",  # BCP-47
                ],
                "observable_schema": ["content_id", "language"],
            },
        },
        edges=[{
            "from": "cms_publisher",
            "to": "translation_service",
            "dimensions": [{
                "name": "language_form_match",
                "from_field": "language_form",
                "to_field": "language_form",
            }],
        }],
    )


def overlingen_tcas_atc() -> dict:
    """2002. Überlingen mid-air collision (71 killed). TCAS told one
    pilot to descend; ATC told the other to descend. Reference frame
    mismatch.  Source: BFU final report."""
    return _composition(
        name="overlingen_tcas_atc",
        description=(
            "2002 Überlingen mid-air collision (71 killed). TCAS and "
            "ATC issued contradictory instructions to two aircraft. "
            "Generic detects: authority_source boundary fee."
        ),
        tools={
            "aircraft_a": {
                "internal_state": [
                    "vertical_command",
                    "command_authority",  # TCAS
                    "altitude",
                ],
                "observable_schema": ["vertical_command", "altitude"],
            },
            "aircraft_b": {
                "internal_state": [
                    "vertical_command",
                    "command_authority",  # ATC
                    "altitude",
                ],
                "observable_schema": ["vertical_command", "altitude"],
            },
        },
        edges=[{
            "from": "aircraft_a",
            "to": "aircraft_b",
            "dimensions": [{
                "name": "command_authority_match",
                "from_field": "command_authority",
                "to_field": "command_authority",
            }],
        }],
    )


def ariane_5_flight_501() -> dict:
    """1996. Ariane 5 Flight 501 ($370M+). Ariane 4's 64-bit horizontal
    velocity converted to 16-bit signed integer; overflow on Ariane 5
    flight profile. Source: ESA Lions Report (1996)."""
    return _composition(
        name="ariane_5_flight_501",
        description=(
            "1996 Ariane 5 Flight 501 ($370M+). 64-bit horizontal "
            "velocity → 16-bit signed integer overflow. UCUM + "
            "precision detects: precision boundary fee."
        ),
        tools={
            "inertial_reference": {
                "internal_state": [
                    "horizontal_velocity",
                    "value_precision_bits",  # 64
                ],
                "observable_schema": ["horizontal_velocity"],
            },
            "flight_control": {
                "internal_state": [
                    "horizontal_velocity",
                    "value_precision_bits",  # 16
                ],
                "observable_schema": ["horizontal_velocity"],
            },
        },
        edges=[{
            "from": "inertial_reference",
            "to": "flight_control",
            "dimensions": [{
                "name": "precision_match",
                "from_field": "value_precision_bits",
                "to_field": "value_precision_bits",
            }],
        }],
    )


def herstatt_risk() -> dict:
    """1974. Herstatt Bank failure ($620M at risk). Cross-timezone
    settlement: USD leg paid in NY before German bank could deliver
    DEM leg. Source: BIS Triennial Survey writeups."""
    return _composition(
        name="herstatt_risk",
        description=(
            "1974 Herstatt Risk ($620M at risk). Cross-timezone FX "
            "settlement: counterparty failed before second leg. "
            "iso-8601 detects: timezone boundary fee."
        ),
        tools={
            "us_correspondent": {
                "internal_state": [
                    "trade_id",
                    "settlement_time",
                    "timezone",  # America/New_York
                ],
                "observable_schema": ["trade_id", "settlement_time"],
            },
            "german_bank": {
                "internal_state": [
                    "trade_id",
                    "settlement_time",
                    "timezone",  # Europe/Frankfurt
                ],
                "observable_schema": ["trade_id", "settlement_time"],
            },
        },
        edges=[{
            "from": "us_correspondent",
            "to": "german_bank",
            "dimensions": [{
                "name": "timezone_match",
                "from_field": "timezone",
                "to_field": "timezone",
            }],
        }],
    )


def leap_second_2012_outages() -> dict:
    """June 30, 2012. Linux kernel hrtimer bug on the leap second
    triggered multi-site outages (LinkedIn, Reddit, Foursquare,
    Yelp, etc). Source: Linux LKML postmortem threads."""
    return _composition(
        name="leap_second_2012_outages",
        description=(
            "2012 leap-second multi-site outages. Kernel hrtimer bug "
            "on 23:59:60 timestamp. iso-8601 detects: temporal_format "
            "boundary fee (60-second minute)."
        ),
        tools={
            "ntp_source": {
                "internal_state": [
                    "timestamp",
                    "minute_seconds",  # 61 (leap second)
                ],
                "observable_schema": ["timestamp"],
            },
            "kernel_hrtimer": {
                "internal_state": [
                    "timestamp",
                    "minute_seconds",  # 60
                ],
                "observable_schema": ["timestamp"],
            },
        },
        edges=[{
            "from": "ntp_source",
            "to": "kernel_hrtimer",
            "dimensions": [{
                "name": "minute_seconds_match",
                "from_field": "minute_seconds",
                "to_field": "minute_seconds",
            }],
        }],
    )


def digoxin_tenfold_transfer() -> dict:
    """Documented (multiple). Digoxin tenfold-error mortality cases:
    pediatric dose calculation drops a decimal during interfacility
    transfer. Source: ISMP / NPSA alerts."""
    return _composition(
        name="digoxin_tenfold_transfer",
        description=(
            "Digoxin tenfold-error pediatric mortality (documented). "
            "Decimal point lost during interfacility transfer doc "
            "rewriting. UCUM + precision detects: precision boundary "
            "fee on dose field."
        ),
        tools={
            "transferring_hospital": {
                "internal_state": [
                    "patient_id",
                    "drug",
                    "dose_value",
                    "dose_unit",
                    "decimal_precision",  # 2 (e.g. 0.05 mg)
                ],
                "observable_schema": [
                    "patient_id", "drug", "dose_value", "dose_unit",
                ],
            },
            "receiving_hospital": {
                "internal_state": [
                    "patient_id",
                    "drug",
                    "dose_value",
                    "dose_unit",
                    "decimal_precision",  # 1 (e.g. 0.5 mg)
                ],
                "observable_schema": [
                    "patient_id", "drug", "dose_value", "dose_unit",
                ],
            },
        },
        edges=[{
            "from": "transferring_hospital",
            "to": "receiving_hospital",
            "dimensions": [{
                "name": "decimal_precision_match",
                "from_field": "decimal_precision",
                "to_field": "decimal_precision",
            }],
        }],
    )


def singapore_lidocaine_overdose() -> dict:
    """Documented (Singapore General Hospital). IV lidocaine dose-rate
    confusion (mg/kg/hr vs mg/min) → patient death. Source: ISMP Canada
    cross-jurisdiction case studies."""
    return _composition(
        name="singapore_lidocaine_overdose",
        description=(
            "Singapore lidocaine IV dose-rate confusion (documented "
            "death). mg/kg/hr vs mg/min unit mismatch. UCUM detects: "
            "rate_unit boundary fee."
        ),
        tools={
            "icu_protocol": {
                "internal_state": [
                    "patient_id",
                    "rate_value",
                    "rate_unit",  # mg/kg/hr
                ],
                "observable_schema": ["patient_id", "rate_value"],
            },
            "infusion_pump": {
                "internal_state": [
                    "patient_id",
                    "rate_value",
                    "rate_unit",  # mg/min
                ],
                "observable_schema": ["patient_id", "rate_value"],
            },
        },
        edges=[{
            "from": "icu_protocol",
            "to": "infusion_pump",
            "dimensions": [{
                "name": "rate_unit_match",
                "from_field": "rate_unit",
                "to_field": "rate_unit",
            }],
        }],
    )


# ── Manifest ─────────────────────────────────────────────────────────


INCIDENTS: dict[str, dict] = {
    "mars_climate_orbiter":           {"build": mars_climate_orbiter,           "domain": "aerospace",   "primary_pack": "ucum",        "loss": "$327.6M"},
    "ariane_5_flight_501":            {"build": ariane_5_flight_501,            "domain": "aerospace",   "primary_pack": "ucum",        "loss": "$370M+"},
    "patriot_missile_dhahran":        {"build": patriot_missile_dhahran,        "domain": "aerospace",   "primary_pack": "ucum",        "loss": "28 killed"},
    "gimli_glider":                   {"build": gimli_glider,                   "domain": "aerospace",   "primary_pack": "ucum",        "loss": "near-catastrophe"},
    "overlingen_tcas_atc":            {"build": overlingen_tcas_atc,            "domain": "aerospace",   "primary_pack": "generic",     "loss": "71 killed"},
    "boeing_737_max_faa_oda":         {"build": boeing_737_max_faa_oda,         "domain": "aerospace",   "primary_pack": "generic",     "loss": "346 killed"},

    "drupal_stripe_jpy":              {"build": drupal_stripe_jpy,              "domain": "payments",    "primary_pack": "iso-4217",    "loss": "100x overcharge"},
    "stripe_webhook_duplicates":      {"build": stripe_webhook_duplicates,      "domain": "payments",    "primary_pack": "generic",     "loss": "2-3x charges"},
    "shopify_quickbooks_tax":         {"build": shopify_quickbooks_tax,         "domain": "payments",    "primary_pack": "iso-4217",    "loss": "tax misreport"},

    "vancouver_stock_exchange":       {"build": vancouver_stock_exchange,       "domain": "finance",     "primary_pack": "ucum",        "loss": "50% index corruption"},
    "libor_sofr_transition":          {"build": libor_sofr_transition,          "domain": "finance",     "primary_pack": "fix-4.4",     "loss": "$154T notional"},
    "t1_settlement_transition":       {"build": t1_settlement_transition,       "domain": "finance",     "primary_pack": "iso-8601",    "loss": "$3.1B margin"},
    "herstatt_risk":                  {"build": herstatt_risk,                  "domain": "finance",     "primary_pack": "iso-8601",    "loss": "$620M"},
    "mt_to_iso20022_migration":       {"build": mt_to_iso20022_migration,       "domain": "finance",     "primary_pack": "swift-mt-mx", "loss": "ongoing"},

    "levothyroxine_mg_mcg":           {"build": levothyroxine_mg_mcg,           "domain": "healthcare",  "primary_pack": "ucum",        "loss": "ongoing"},
    "digoxin_tenfold_transfer":       {"build": digoxin_tenfold_transfer,       "domain": "healthcare",  "primary_pack": "ucum",        "loss": "deaths"},
    "singapore_lidocaine_overdose":   {"build": singapore_lidocaine_overdose,   "domain": "healthcare",  "primary_pack": "ucum",        "loss": "death"},
    "icd_9_to_icd_10_transition":     {"build": icd_9_to_icd_10_transition,     "domain": "healthcare",  "primary_pack": "icd-10-cm",   "loss": "10% claim rejection"},
    "fhir_r4_to_r5_breaking_changes": {"build": fhir_r4_to_r5_breaking_changes, "domain": "healthcare",  "primary_pack": "fhir-r4",     "loss": "ongoing"},
    "snomed_icd10_double_coding":     {"build": snomed_icd10_double_coding,     "domain": "healthcare",  "primary_pack": "umls-mappings", "loss": "claims"},

    "phe_covid_xls_truncation":       {"build": phe_covid_xls_truncation,       "domain": "software",    "primary_pack": "iana-media-types", "loss": "15,841 cases"},
    "leap_second_2012_outages":       {"build": leap_second_2012_outages,       "domain": "software",    "primary_pack": "iso-8601",    "loss": "multi-site"},

    "nv_energy_meter_channel":        {"build": nv_energy_meter_channel,        "domain": "energy",      "primary_pack": "generic",     "loss": "$685K FERC"},
    "gcv_ncv_gas_billing":            {"build": gcv_ncv_gas_billing,            "domain": "energy",      "primary_pack": "ucum",        "loss": "10.8%"},
    "lng_price_formula_disputes":     {"build": lng_price_formula_disputes,     "domain": "energy",      "primary_pack": "iso-4217",    "loss": "$4B+ arbitration"},

    "gtin_check_digit_miscoding":     {"build": gtin_check_digit_miscoding,     "domain": "supply",      "primary_pack": "gs1",         "loss": "FDA recall"},
    "edifact_d96a_d21b_drift":        {"build": edifact_d96a_d21b_drift,        "domain": "supply",      "primary_pack": "un-edifact",  "loss": "ongoing"},
    "country_code_alpha2_alpha3_drift": {"build": country_code_alpha2_alpha3_drift, "domain": "trade",   "primary_pack": "iso-3166",    "loss": "ongoing"},
    "language_tag_bcp47_drift":       {"build": language_tag_bcp47_drift,       "domain": "localization","primary_pack": "iso-639",     "loss": "ongoing"},
    "naics_sic_classification_dispute": {"build": naics_sic_classification_dispute, "domain": "tax",     "primary_pack": "naics-2022",  "loss": "tax court"},
}


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: build_incidents.py OUTPUT_DIR", file=sys.stderr)
        sys.exit(2)
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows = []
    for name, spec in INCIDENTS.items():
        comp = spec["build"]()
        path = out_dir / f"{name}.yaml"
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(comp, fh, sort_keys=False, allow_unicode=True)
        manifest_rows.append({
            "name": name,
            "domain": spec["domain"],
            "primary_pack": spec["primary_pack"],
            "loss": spec["loss"],
            "yaml_path": str(path.relative_to(out_dir.parent.parent)),
        })

    manifest_path = out_dir / "incidents-manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "version": "0.1.0",
                "incident_count": len(manifest_rows),
                "incidents": manifest_rows,
            },
            fh,
            indent=2,
        )

    print(
        f"Wrote {len(manifest_rows)} incidents to {out_dir} + manifest "
        f"at {manifest_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
