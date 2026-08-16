# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Tools for WebWala Studio (a web design agency in Gurugram, India) that find local businesses with no website, or only a weak one, to pitch web design services to. They query the Google Places API (New): Text Search per category+area combo, then Place Details per result to check the website field. There are two front ends over the same scraping engine:

- `webwala_lead_scraper.py` — CLI, fixed `CATEGORIES`/`AREAS` lists edited in the file.
- `app.py` — Flask web dashboard at `http://127.0.0.1:5001`, lets the user pick categories/areas at runtime and watch leads populate live in a table, in addition to the CSV.

Both call into `scraper_core.py`, which owns the actual search/details/dedupe/checkpoint/retry loop (`run_scrape()`) so the two front ends can't drift out of sync — the CLI drives it with print-based callbacks, the dashboard drives it with callbacks that update an in-memory job state polled by the browser.

## Commands

```bash
# Setup (venv only — never install packages globally)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# CLI: estimate API call volume/cost before a real run
python webwala_lead_scraper.py --dry-run

# CLI: full run against the hardcoded CATEGORIES/AREAS (writes leads to webwala_leads.csv as it goes)
python webwala_lead_scraper.py

# Web dashboard: pick categories/areas in the browser, watch leads live
python app.py   # then open http://127.0.0.1:5001
```

There is no test suite, linter, or build step in this repo.

Requires a `.env` file (copy from `.env.example`) with `GOOGLE_PLACESEY` set to a Google Places API (New) key that has billing enabled — a trial/demo key's low rate limit will trigger frequent 429s (see retry/backoff note below).

## Architecture

- **`scraper_core.py`** — the shared engine. `run_scrape(categories, areas, delay_seconds, on_combo_start, on_lead, on_error, on_retry, should_stop)` runs the full category x area double loop and is the only place that double loop exists; both front ends pass callbacks into it rather than reimplementing it.
  - **Two-call pipeline per result**: `search_places()` (Text Search, cheap field mask: id/name/address only) finds candidate place IDs for a category+area combo, then `get_place_details()` (Details endpoint, fuller field mask: phone/rating/reviews/website) is called per unique place ID. This split keeps the search call on a cheaper Places API SKU tier and avoids paying for detail fields on results that turn out to be duplicates.
  - **Retry/backoff**: `_request_with_retry()` retries 429/5xx responses with exponential backoff (`RETRY_BASE_DELAY_SECONDS`, `MAX_RETRIES`) before giving up on a single request.
  - **Global dedupe**: place IDs are tracked in a `seen_place_ids` set (persisted in the checkpoint file), shared across CLI and dashboard runs, so the same business found via multiple category/area searches — or a previous run — is only detail-fetched and written once. A place ID is only added to `seen_place_ids` on a *successful* details fetch; a failed lookup is deliberately left off so it gets retried rather than silently and permanently skipped.
  - **Checkpoint/resume**: `webwala_scraper_checkpoint.json` records which category+area combos are fully done (`completed_combos`) and which place IDs have been seen. A combo is only marked complete if none of its lookups failed — if any did, the combo is left off the list so the next run re-searches it and retries just the unresolved place IDs (already-successful ones are skipped via `seen_place_ids`). Saved after every combo, so a crash mid-run only loses progress within the in-flight combo.
  - **Incremental CSV output**: `webwala_leads.csv` (`OUTPUT_CSV`) is appended to as leads are found (not buffered in memory), columns `name, address, phone, rating, review_count, current_website, category, area, maps_url`. Only businesses matching the "no/weak website" rule (`is_weak_or_missing_website()`, domains in `WEAK_WEBSITE_DOMAINS`) are written — this file is a pitch list, not a full scrape dump. `ensure_csv_header()` migrates an older CSV in place (rewriting the header and backfilling blanks) if its columns don't match `CSV_FIELDS`, so adding a field never misaligns existing rows.
  - `estimate_calls()` computes the dry-run cost projection used by both front ends.
- **`webwala_lead_scraper.py`** — CLI entry point. Owns its own `CATEGORIES`/`AREAS`/`AVG_RESULTS_PER_SEARCH` config block; wires `scraper_core.run_scrape()` callbacks to `print()`.
- **`app.py`** — Flask app. Holds one global `STATE` dict (guarded by `STATE_LOCK`) representing the single currently-running (or last) job — there's no multi-job queue, starting a scrape while one is running returns 409. `_scrape_worker()` runs `scraper_core.run_scrape()` in a background thread, writing progress into `STATE` via callbacks; `/api/scrape/status` is polled by the browser (every ~1.2s) to read it. Routes: `/api/dry-run`, `/api/scrape/start`, `/api/scrape/stop` (sets `stop_requested`, checked by `should_stop` between combos and between businesses within a combo — finishes only the in-flight request's retry attempt, not the whole combo), `/api/scrape/status`, `/api/leads` (reads current `webwala_leads.csv` off disk), `/download/csv`.
- **`templates/dashboard.html`** — single self-contained page (inline CSS/JS, no build step) rendered by Flask's `render_template`. Category/area tag inputs are free-form (plus clickable suggestion chips seeded from the old CLI defaults, purely as convenience — nothing is preselected). Polls `/api/scrape/status` while a run is active to update the progress bar, log panel, and leads table.
