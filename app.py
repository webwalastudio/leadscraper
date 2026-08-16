#!/usr/bin/env python3
"""
WebWala Lead Scraper — web dashboard.

Lets you pick categories and areas yourself (instead of editing a config
list) and watch leads show up live as the scrape runs, in addition to the
usual webwala_leads.csv output.

Usage:
    python app.py
    (then open http://127.0.0.1:5001 in a browser)
"""

import threading

from flask import Flask, jsonify, render_template, request, send_file

import scraper_core as core

app = Flask(__name__)

STATE_LOCK = threading.Lock()
STATE = {
    "running": False,
    "stop_requested": False,
    "current_combo": None,
    "combo_index": 0,
    "total_combos": 0,
    "leads": [],
    "log": [],
    "error": None,
    "categories": [],
    "areas": [],
}

MAX_LOG_LINES = 300


def _log(message: str) -> None:
    with STATE_LOCK:
        STATE["log"].append(message)
        if len(STATE["log"]) > MAX_LOG_LINES:
            STATE["log"] = STATE["log"][-MAX_LOG_LINES:]


def _reset_state(categories, areas) -> None:
    with STATE_LOCK:
        STATE.update(
            {
                "running": True,
                "stop_requested": False,
                "current_combo": None,
                "combo_index": 0,
                "total_combos": len(categories) * len(areas),
                "leads": [],
                "log": [],
                "error": None,
                "categories": categories,
                "areas": areas,
            }
        )


def _scrape_worker(categories, areas, delay_seconds) -> None:
    def on_combo_start(index, total, category, area, already_done):
        with STATE_LOCK:
            STATE["combo_index"] = index
            STATE["total_combos"] = total
            STATE["current_combo"] = f"{category} in {area}"
        _log(("Skipping (already done): " if already_done else "Searching: ") + f"{category} in {area}")

    def on_lead(lead):
        with STATE_LOCK:
            STATE["leads"].append(lead)
        _log(f"LEAD: {lead['name']} (website: {lead['current_website'] or 'none'})")

    def on_error(context, exc):
        _log(f"ERROR {context}: {exc}")

    def on_retry(status_code, wait_seconds, attempt, max_attempts):
        _log(f"Rate limited ({status_code}), retrying in {wait_seconds:.0f}s "
             f"(attempt {attempt}/{max_attempts})...")

    def should_stop():
        with STATE_LOCK:
            return STATE["stop_requested"]

    try:
        core.run_scrape(
            categories,
            areas,
            delay_seconds=delay_seconds,
            on_combo_start=on_combo_start,
            on_lead=on_lead,
            on_error=on_error,
            on_retry=on_retry,
            should_stop=should_stop,
        )
        _log("Done.")
    except core.ConfigError as exc:
        with STATE_LOCK:
            STATE["error"] = str(exc)
        _log(f"FATAL: {exc}")
    finally:
        with STATE_LOCK:
            STATE["running"] = False


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/dry-run", methods=["POST"])
def api_dry_run():
    payload = request.get_json(force=True) or {}
    categories = payload.get("categories", [])
    areas = payload.get("areas", [])
    avg_results = int(payload.get("avg_results", 10))
    return jsonify(core.estimate_calls(len(categories), len(areas), avg_results))


@app.route("/api/scrape/start", methods=["POST"])
def api_start():
    payload = request.get_json(force=True) or {}
    categories = [c.strip() for c in payload.get("categories", []) if c.strip()]
    areas = [a.strip() for a in payload.get("areas", []) if a.strip()]
    try:
        delay_seconds = float(payload.get("delay_seconds", core.DEFAULT_REQUEST_DELAY_SECONDS))
    except (TypeError, ValueError):
        delay_seconds = core.DEFAULT_REQUEST_DELAY_SECONDS

    if not categories or not areas:
        return jsonify({"error": "Pick at least one category and one area."}), 400

    with STATE_LOCK:
        if STATE["running"]:
            return jsonify({"error": "A scrape is already running."}), 409

    _reset_state(categories, areas)
    thread = threading.Thread(target=_scrape_worker, args=(categories, areas, delay_seconds), daemon=True)
    thread.start()
    return jsonify({"started": True})


@app.route("/api/scrape/stop", methods=["POST"])
def api_stop():
    with STATE_LOCK:
        STATE["stop_requested"] = True
    return jsonify({"stopping": True})


@app.route("/api/scrape/status")
def api_status():
    with STATE_LOCK:
        return jsonify(dict(STATE))


@app.route("/api/leads")
def api_leads():
    return jsonify({"leads": core.read_existing_leads()})


@app.route("/download/csv")
def download_csv():
    return send_file(core.OUTPUT_CSV, as_attachment=True, download_name=core.OUTPUT_CSV)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
