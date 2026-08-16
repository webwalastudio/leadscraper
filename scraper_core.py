"""
Shared scraping logic for WebWala's Google Places lead finder.

Both the CLI script (webwala_lead_scraper.py) and the web dashboard (app.py)
call run_scrape() here, so the search/details/dedupe/checkpoint/retry
behavior only lives in one place.
"""

import csv
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# A business is treated as a "lead" (worth pitching) if it has no website at
# all, or its website is just a link to one of these platforms rather than a
# real site of its own.
WEAK_WEBSITE_DOMAINS = ("facebook.com", "instagram.com", "linktr.ee", "wa.me")

DEFAULT_REQUEST_DELAY_SECONDS = 1.0

# Retry behavior for rate-limited (429) and transient server (5xx) errors.
MAX_RETRIES = 5
RETRY_BASE_DELAY_SECONDS = 5.0

OUTPUT_CSV = "webwala_leads.csv"
CHECKPOINT_FILE = "webwala_scraper_checkpoint.json"

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL_TEMPLATE = "https://places.googleapis.com/v1/places/{place_id}"

# Field masks control both what you get back AND what SKU tier you're billed
# at, so keep the search call cheap (just enough to identify candidates) and
# only ask for the expensive fields (rating, website, phone) on Details.
SEARCH_FIELD_MASK = "places.id,places.displayName,places.formattedAddress"
DETAILS_FIELD_MASK = (
    "id,displayName,formattedAddress,internationalPhoneNumber,"
    "rating,userRatingCount,websiteUri,googleMapsUri"
)

CSV_FIELDS = [
    "name",
    "address",
    "phone",
    "rating",
    "review_count",
    "current_website",
    "category",
    "area",
    "maps_url",
]


class ConfigError(Exception):
    """Raised when required setup (like the API key) is missing."""


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def load_api_key() -> str:
    load_dotenv()
    api_key = os.getenv("GOOGLE_PLACESEY")
    if not api_key:
        raise ConfigError(
            "GOOGLE_PLACESEY not set. Copy .env.example to .env and fill in "
            "your Google Places API key."
        )
    return api_key


def combo_key(category: str, area: str) -> str:
    return f"{category}|{area}"


def load_checkpoint() -> dict:
    path = Path(CHECKPOINT_FILE)
    if not path.exists():
        return {"completed_combos": [], "seen_place_ids": []}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("completed_combos", [])
    data.setdefault("seen_place_ids", [])
    return data


def save_checkpoint(checkpoint: dict) -> None:
    tmp_path = Path(CHECKPOINT_FILE + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(checkpoint, f, indent=2)
    tmp_path.replace(CHECKPOINT_FILE)


def ensure_csv_header() -> None:
    path = Path(OUTPUT_CSV)
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
        return

    # If an older run wrote the CSV before a column was added (e.g.
    # maps_url), the file's header won't match CSV_FIELDS. Appending new
    # rows with more fields than the existing header would misalign every
    # column, so rewrite the file with the current header, backfilling
    # missing values as blank for pre-existing rows.
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing_rows = list(reader)
        existing_header = reader.fieldnames

    if list(existing_header or []) == CSV_FIELDS:
        return

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in existing_rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def append_lead(lead: dict) -> None:
    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writerow(lead)


def read_existing_leads() -> list:
    path = Path(OUTPUT_CSV)
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Lead qualification
# ---------------------------------------------------------------------------

def is_weak_or_missing_website(website: str) -> bool:
    if not website:
        return True
    website_lower = website.lower()
    return any(domain in website_lower for domain in WEAK_WEBSITE_DOMAINS)


# ---------------------------------------------------------------------------
# Google Places API (New) calls
# ---------------------------------------------------------------------------

def _request_with_retry(method: str, url: str, on_retry=None, **kwargs) -> requests.Response:
    """Issue a request, retrying with exponential backoff on 429 / 5xx."""
    response = None
    for attempt in range(MAX_RETRIES + 1):
        response = requests.request(method, url, timeout=30, **kwargs)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == MAX_RETRIES:
                response.raise_for_status()
            wait = RETRY_BASE_DELAY_SECONDS * (2 ** attempt)
            if on_retry:
                on_retry(response.status_code, wait, attempt + 1, MAX_RETRIES)
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response
    return response  # unreachable, keeps linters happy


def search_places(api_key: str, category: str, area: str, on_retry=None) -> list:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": SEARCH_FIELD_MASK,
    }
    body = {"textQuery": f"{category} in {area}"}
    response = _request_with_retry("POST", SEARCH_URL, headers=headers, json=body, on_retry=on_retry)
    return response.json().get("places", [])


def get_place_details(api_key: str, place_id: str, on_retry=None) -> dict:
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": DETAILS_FIELD_MASK,
    }
    url = DETAILS_URL_TEMPLATE.format(place_id=place_id)
    response = _request_with_retry("GET", url, headers=headers, on_retry=on_retry)
    return response.json()


# ---------------------------------------------------------------------------
# Dry run: estimate cost before spending real API calls
# ---------------------------------------------------------------------------

def estimate_calls(num_categories: int, num_areas: int, avg_results_per_search: int) -> dict:
    num_combos = num_categories * num_areas
    est_detail_calls = num_combos * avg_results_per_search
    return {
        "combos": num_combos,
        "estimated_detail_calls": est_detail_calls,
        "estimated_total_calls": num_combos + est_detail_calls,
    }


# ---------------------------------------------------------------------------
# Main scrape loop — shared by the CLI and the web dashboard
# ---------------------------------------------------------------------------

def run_scrape(
    categories,
    areas,
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    on_combo_start=None,
    on_lead=None,
    on_error=None,
    on_retry=None,
    should_stop=None,
) -> list:
    """
    Runs the category x area scrape, honoring the on-disk checkpoint for
    dedupe/resume. Callbacks let the CLI print to stdout and the web
    dashboard update its in-memory job state, without duplicating this loop.

    on_combo_start(index, total, category, area, already_done)
    on_lead(lead_dict)
    on_error(context: str, exc: Exception)
    on_retry(status_code, wait_seconds, attempt, max_attempts)
    should_stop() -> bool   # checked between combos, to support a "Stop" button

    Returns the list of lead dicts found during this call.
    """
    api_key = load_api_key()
    checkpoint = load_checkpoint()
    completed_combos = set(checkpoint["completed_combos"])
    seen_place_ids = set(checkpoint["seen_place_ids"])

    ensure_csv_header()

    total_combos = len(categories) * len(areas)
    combo_index = 0
    leads_found = []

    for category in categories:
        for area in areas:
            if should_stop and should_stop():
                return leads_found

            combo_index += 1
            key = combo_key(category, area)
            already_done = key in completed_combos

            if on_combo_start:
                on_combo_start(combo_index, total_combos, category, area, already_done)

            if already_done:
                continue

            try:
                places = search_places(api_key, category, area, on_retry=on_retry)
            except requests.RequestException as exc:
                if on_error:
                    on_error(f"searching {category} in {area}", exc)
                time.sleep(delay_seconds)
                continue

            time.sleep(delay_seconds)

            combo_had_failure = False
            stopped_mid_combo = False

            for place in places:
                if should_stop and should_stop():
                    stopped_mid_combo = True
                    break

                place_id = place.get("id")
                if not place_id or place_id in seen_place_ids:
                    continue

                try:
                    details = get_place_details(api_key, place_id, on_retry=on_retry)
                except requests.RequestException as exc:
                    if on_error:
                        on_error(f"fetching details for {place_id}", exc)
                    combo_had_failure = True
                    time.sleep(delay_seconds)
                    continue

                # Only mark a place as "seen" once its details are actually
                # in hand, so a failed lookup gets retried rather than
                # silently and permanently skipped.
                seen_place_ids.add(place_id)

                time.sleep(delay_seconds)

                website = details.get("websiteUri", "")
                if is_weak_or_missing_website(website):
                    name = details.get("displayName", {}).get("text", "")
                    lead = {
                        "name": name,
                        "address": details.get("formattedAddress", ""),
                        "phone": details.get("internationalPhoneNumber", ""),
                        "rating": details.get("rating", ""),
                        "review_count": details.get("userRatingCount", ""),
                        "current_website": website,
                        "category": category,
                        "area": area,
                        "maps_url": details.get("googleMapsUri", ""),
                    }
                    append_lead(lead)
                    leads_found.append(lead)
                    if on_lead:
                        on_lead(lead)

            # Only mark this combo fully done if every place in it was
            # successfully checked. If anything failed, leave the combo off
            # the completed list so the next run re-searches it and retries
            # just the place_ids that weren't successfully resolved
            # (already-successful ones are skipped via seen_place_ids).
            if not combo_had_failure and not stopped_mid_combo:
                completed_combos.add(key)
                checkpoint["completed_combos"] = sorted(completed_combos)
            checkpoint["seen_place_ids"] = sorted(seen_place_ids)
            save_checkpoint(checkpoint)

            if stopped_mid_combo:
                return leads_found

    return leads_found
