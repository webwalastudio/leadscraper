# WebWala Lead Scraper

Finds local businesses in Gurugram with no website (or only a weak one —
just a Facebook/Instagram/Linktree/WhatsApp page), using the Google Places
API (New). Built for WebWala Studio's outbound pitching.

## Setup

### 1. Get a Google Places API key

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and
   create (or select) a project.
2. Enable billing on the project — the Places API requires a billing
   account, even though there's a monthly free credit.
3. Enable the **Places API (New)**: APIs & Services → Library → search
   "Places API (New)" → Enable.
4. Create an API key: APIs & Services → Credentials → Create Credentials →
   API key. Restrict it to the Places API (New) for safety.

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and paste your key:

```
GOOGLE_PLACESEY=your_actual_api_key_here
```

### 3. Set up a virtual environment and install dependencies

Everything installs into a local venv — nothing is installed globally.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

(On future runs, just `source venv/bin/activate` again before using the
script.)

### 4. Run it

There are two ways to run a scrape: the command-line script (fixed
category/area lists edited in the file) or the web dashboard (pick
categories/areas yourself, see results live in a browser).

#### Option A — CLI

Estimate cost first:

```bash
python webwala_lead_scraper.py --dry-run
```

This prints how many API calls a full run would make (categories x areas
text searches, plus an estimated details call per result) so you can check
it against the Places API free monthly credit before spending real calls.

Then run it for real:

```bash
python webwala_lead_scraper.py
```

Progress prints to the terminal as it searches each category+area
combination. Leads are appended to `webwala_leads.csv` as they're found,
with columns: `name, address, phone, rating, review_count,
current_website, category, area`.

Edit the `CATEGORIES` and `AREAS` lists at the top of
`webwala_lead_scraper.py` to change what business types and Gurugram
localities are searched.

#### Option B — Web dashboard

```bash
python app.py
```

Then open http://127.0.0.1:5001 in a browser. You can:

- Add your own categories and areas as tags (click a suggestion chip, or
  type your own and press Enter) — nothing is hardcoded.
- Set the delay between API requests (useful if you're on a trial/demo key
  with a low rate limit — try 5-10 seconds if you're getting rate limited).
- Click **Estimate cost (dry run)** to see the projected API call count
  before running for real.
- Click **Start Scrape** to run it — progress, the live log, and the leads
  table update as it goes. **Stop** ends the run early (in-progress
  business finishes first).
- Leads still get written to `webwala_leads.csv` the whole time — the
  dashboard is a view on top of the same pipeline, not a separate one. Use
  the **Download webwala_leads.csv** link to grab the file.

## Resuming after a crash

Both the CLI and the dashboard checkpoint progress to
`webwala_scraper_checkpoint.json` after each category+area combo finishes
(and track every `place_id` already seen, so results aren't duplicated
across searches, CLI runs, or dashboard runs). If a run stops partway
through — crash, rate limit, Ctrl+C, clicking Stop — just run it again
(same CLI command, or click Start again in the dashboard with the same or
overlapping categories/areas).

Combos already completed are skipped and previously-seen businesses aren't
re-fetched. To start completely fresh, delete
`webwala_scraper_checkpoint.json` and `webwala_leads.csv`.

## What counts as a "lead"

A business is written to the CSV if its Google Places listing has:

- no website at all, or
- a website that's just a link to `facebook.com`, `instagram.com`,
  `linktr.ee`, or `wa.me` (WhatsApp) — not a real site of its own.
