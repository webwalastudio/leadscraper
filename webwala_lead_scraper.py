#!/usr/bin/env python3
"""
WebWala Studio lead scraper (CLI).

Finds local businesses in Gurugram that have no website, or only a weak one
(Facebook / Instagram / Linktree / WhatsApp link), using the Google Places API
(New): Text Search to find candidates per category+area, then Place Details
to check each one's website field.

For a UI where you pick categories/areas yourself instead of editing this
file, run the web dashboard instead: python app.py

Usage:
    python webwala_lead_scraper.py            # run for real, write leads to CSV
    python webwala_lead_scraper.py --dry-run  # just estimate API call volume
"""

import argparse
import sys

import requests

import scraper_core as core

# ---------------------------------------------------------------------------
# Config — edit these lists to change what gets searched
# ---------------------------------------------------------------------------

CATEGORIES = [
    "dentist",
    "salon",
    "restaurant",
    "school",
    "gym",
    "boutique clothing store",
    "physiotherapist",
    "interior designer",
    "cafe",
    "yoga studio",
]

AREAS = [
    "Sector 14 Gurugram",
    "Sushant Lok Gurugram",
    "DLF Phase 3 Gurugram",
    "Sector 29 Gurugram",
    "Palam Vihar Gurugram",
    "South City 1 Gurugram",
]

# Rough average results per category+area search, used only to size the
# --dry-run cost estimate. Text Search (New) returns up to 20 results per
# call; most local niche searches in a single locality return far fewer.
AVG_RESULTS_PER_SEARCH = 10


def run_dry_run() -> None:
    estimate = core.estimate_calls(len(CATEGORIES), len(AREAS), AVG_RESULTS_PER_SEARCH)

    print("=== WebWala Lead Scraper — dry run estimate ===")
    print(f"Categories: {len(CATEGORIES)}")
    print(f"Areas: {len(AREAS)}")
    print(f"Category x Area combos (Text Search calls): {estimate['combos']}")
    print(f"Assumed avg results per search: {AVG_RESULTS_PER_SEARCH}")
    print(f"Estimated Place Details calls: {estimate['estimated_detail_calls']}")
    print(f"Estimated TOTAL API calls: {estimate['estimated_total_calls']}")
    print()
    print(
        "Note: this assumes one page of results per search (up to 20). "
        "Actual counts will vary by area/category density. Check current "
        "Places API (New) pricing/free-tier credit before a full run."
    )


def run_scrape_cli() -> None:
    def on_combo_start(index, total, category, area, already_done):
        if already_done:
            print(f"[{index}/{total}] Skipping (already done): {category} in {area}")
        else:
            print(f"[{index}/{total}] Searching: {category} in {area}...")

    def on_lead(lead):
        print(f"    LEAD: {lead['name']} (website: {lead['current_website'] or 'none'})")

    def on_error(context, exc):
        print(f"    ERROR {context}: {exc}")
        print("    Not marking this place as done; it will be retried on the next run.")

    def on_retry(status_code, wait_seconds, attempt, max_attempts):
        print(f"    Rate limited ({status_code}), retrying in {wait_seconds:.0f}s "
              f"(attempt {attempt}/{max_attempts})...")

    leads = core.run_scrape(
        CATEGORIES,
        AREAS,
        on_combo_start=on_combo_start,
        on_lead=on_lead,
        on_error=on_error,
        on_retry=on_retry,
    )

    print()
    print(f"Done. {len(leads)} leads written to {core.OUTPUT_CSV}")


def main() -> None:
    parser = argparse.ArgumentParser(description="WebWala Studio Gurugram lead scraper")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Estimate the number of API calls a full run would make, without calling the API.",
    )
    args = parser.parse_args()

    if args.dry_run:
        run_dry_run()
        return

    try:
        run_scrape_cli()
    except core.ConfigError as exc:
        sys.exit(f"ERROR: {exc}")
    except requests.RequestException as exc:
        sys.exit(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
