#!/usr/bin/env python3
"""
VC Lead Finder Dashboard Server
Flask web server that exposes API endpoints to run the pipeline, retrieve leads,
display real-time logs, and fetch high-level metrics.
"""

import os
import csv
import json
import gzip
import re
import threading

import requests
from datetime import date, datetime, timezone
from flask import Flask, jsonify, request, send_from_directory, render_template_string
from pipeline import clean_firm_name, extract_related_name, is_entity_identity, reassess_saved_lead, run_pipeline
from build_research_backlog import build as build_research_backlog, build_rows as build_research_backlog_rows
from lead_signals import AdvIndex, early_signal, sec_people
from pipeline import normalize_phone

app = Flask(__name__, static_folder="templates")

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LEADS_FILE = os.path.join(SCRIPT_DIR, "ALL_VC_LEADS.csv")
BACKLOG_FILE = os.path.join(SCRIPT_DIR, "ALAMAT_RESEARCH_BACKLOG.csv")
ADV_FILE = os.path.join(SCRIPT_DIR, "ALAMAT_ADV_SIGNALS.csv")
LOGS_FILE = os.path.join(SCRIPT_DIR, "pipeline_run.log")
# Vercel serverless functions stop after each response and discard written files,
# so the hosted dashboard starts the "Refresh SEC Leads" GitHub Action instead of
# running the pipeline itself. The Action commits new data, which redeploys the site.
HOSTED = bool(os.environ.get("VERCEL"))
GITHUB_TOKEN = os.environ.get("GITHUB_DISPATCH_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO") or (
    f"{os.environ['VERCEL_GIT_REPO_OWNER']}/{os.environ['VERCEL_GIT_REPO_SLUG']}"
    if os.environ.get("VERCEL_GIT_REPO_OWNER") and os.environ.get("VERCEL_GIT_REPO_SLUG")
    else "alohaaki7/vc_finder"
)
GITHUB_REF = os.environ.get("GITHUB_DISPATCH_REF", "main")
REFRESH_WORKFLOW = "refresh-sec-leads.yml"
RUN_PASSWORD = os.environ.get("RUN_PASSWORD", "")
RUNS_ENABLED = not HOSTED or bool(GITHUB_TOKEN)
ACTIVE_RUN_STATES = {"queued", "in_progress", "waiting", "pending", "requested"}

# Lock and state for running pipeline
pipeline_lock = threading.Lock()
pipeline_status = {
    "running": False,
    "current_type": "",
    "current_days": 30,
    "progress": "",
    "error": "",
    "can_run": RUNS_ENABLED
}

# Standard template directory configuration
TEMPLATE_DIR = os.path.join(SCRIPT_DIR, "templates")
if not os.path.exists(TEMPLATE_DIR):
    os.makedirs(TEMPLATE_DIR)


def prepare_lead_for_display(row):
    """Use a parent manager as the display label for legacy SEC series rows."""
    display_row = reassess_saved_lead(dict(row))
    issuer_name = str(display_row.get("name") or "")
    if "series of" in issuer_name.casefold():
        manager_name = clean_firm_name(issuer_name)
        if manager_name:
            display_row["sec_vehicle_name"] = display_row.get("firm_name") or issuer_name
            display_row["firm_name"] = manager_name
    display_row["linkedin_search_firm"] = linkedin_search_firm(display_row.get("firm_name"))
    display_row["linkedin_search_person"] = linkedin_search_person(display_row)
    display_row["linkedin_manager_candidate"] = linkedin_manager_candidate(display_row)
    display_row["sec_people"] = "; ".join(sec_people(display_row))
    (display_row["early_signal"], display_row["early_signal_label"],
     display_row["early_rank"]) = early_signal(display_row)
    return display_row


_leads_cache = {"key": None, "leads": []}


def file_version(path):
    return os.path.getmtime(path) if os.path.exists(path) else None


def load_display_leads():
    """Return VC leads prepared for the dashboard, cached until either data file changes."""
    key = (file_version(LEADS_FILE), file_version(ADV_FILE))
    if _leads_cache["key"] == key:
        return _leads_cache["leads"]

    with open(LEADS_FILE, "r", encoding="utf-8") as f:
        leads = [prepare_lead_for_display(row) for row in csv.DictReader(f)]
    leads = [lead for lead in leads if lead.get("manager_status_code") != "not_vc"]

    adv_index = AdvIndex.from_csv(ADV_FILE)
    phone_counts = {}
    for lead in leads:
        phone = normalize_phone(lead.get("phone"))
        phone_counts[phone] = phone_counts.get(phone, 0) + 1
    for lead in leads:
        lead.update(adv_index.match(lead, lead["linkedin_search_firm"], phone_counts) or {})

    _leads_cache.update(key=key, leads=leads)
    return leads


def prepare_backlog_for_display(row, index=0):
    """Add stable browser-only metadata to a research-backlog row.

    Backlog rows are deliberately still unverified.  The URLs in this view are
    search routes, not assertions that a person or company identity is correct.
    """
    display_row = dict(row)
    display_row["backlog_id"] = f"backlog-{index}"
    display_row["linkedin_search_firm"] = linkedin_search_firm(
        display_row.get("firm_name") or display_row.get("name")
    )
    display_row["linkedin_search_person"] = linkedin_search_person(display_row)
    return display_row


def linkedin_search_firm(value):
    """Reduce an SEC fund vehicle label to the operating brand used in public search."""
    name = clean_firm_name(value)
    name = re.sub(
        r"\s*(?:-|,)?\s*(?:fund|feeder|series|spv)\s*(?:[ivx]+|\d+|one|two)?\b.*$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(r",?\s*(?:l\.?p\.?|l\.?l\.?c\.?|inc\.?|ltd\.?)\s*$", "", name, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", name).strip(" ,-.") or clean_firm_name(value)


def linkedin_search_person(row):
    """Choose a human SEC-associated person instead of a GP or management entity."""
    people = sec_people(row, limit=1)
    return people[0] if people else ""


MANAGER_ROLE_PATTERN = re.compile(
    r"\b(founder|co-?founder|founding partner|managing partner|general partner|"
    r"chief investment officer|investment partner|venture partner|fund manager|"
    r"managing director|partner)\b",
    flags=re.IGNORECASE,
)


def linkedin_manager_candidate(row):
    """Return a person only when the stored evidence identifies an investment decision-maker."""
    person = linkedin_search_person(row)
    verification = str(row.get("contact_verification_status") or "").strip().lower()
    if person and verification in {"verified", "verified_public"}:
        return person

    candidates = [(row.get("contact_name", ""), row.get("contact_title", ""))]
    for raw in str(row.get("all_contacts") or "").split(";"):
        role_match = re.search(r"\(([^()]*)\)\s*$", raw)
        candidates.append((extract_related_name(raw), role_match.group(1) if role_match else ""))

    for raw_name, role in candidates:
        name = extract_related_name(raw_name)
        name = re.sub(r"^(?:n/?a|general partner|management company)\s+", "", name, flags=re.IGNORECASE)
        if (
            name
            and not is_entity_identity(name)
            and len(name.split()) >= 2
            and MANAGER_ROLE_PATTERN.search(str(role or ""))
        ):
            return name.title() if name.isupper() else name
    return ""


def log_writer(msg):
    """Write log message to the log file and update progress state."""
    print(msg)
    try:
        with open(LOGS_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
        pipeline_status["progress"] = msg
    except Exception as e:
        print(f"Error writing to log file: {e}")


def run_pipeline_thread(days, lead_type, min_size):
    """Runs the pipeline in a background thread."""
    global pipeline_status
    with pipeline_lock:
        pipeline_status["running"] = True
        pipeline_status["current_type"] = lead_type
        pipeline_status["current_days"] = days
        pipeline_status["error"] = ""

    # Clear logs file
    try:
        with open(LOGS_FILE, "w", encoding="utf-8") as f:
            f.write(f"--- Pipeline started at {threading.current_thread().name} ---\n")
    except Exception:
        pass

    try:
        run_pipeline(days=days, lead_type=lead_type, min_size=min_size, output_file=LEADS_FILE, logger=log_writer)
        build_research_backlog(LEADS_FILE, BACKLOG_FILE, date.today())
        log_writer("Inclusive VC research universe refreshed for the dashboard.")
        log_writer("\n🎉 PIPELINE SUCCESSFUL! Ready to review.")
    except Exception as e:
        log_writer(f"\n❌ PIPELINE ERROR: {e}")
        pipeline_status["error"] = str(e)
    finally:
        with pipeline_lock:
            pipeline_status["running"] = False


@app.route("/")
def index():
    """Serve the single-page dashboard HTML."""
    try:
        with open(os.path.join(TEMPLATE_DIR, "index.html"), "r", encoding="utf-8") as f:
            content = f.read()
        return render_template_string(content)
    except Exception as e:
        return f"Error loading index.html. Ensure it exists in templates/index.html. Details: {e}", 500


@app.route("/api/leads", methods=["GET"])
def get_leads():
    """Read the master CSV file and return leads as JSON."""
    if not os.path.exists(LEADS_FILE):
        return jsonify([])

    try:
        leads = load_display_leads()
    except Exception as e:
        return jsonify({"error": f"Failed to read CSV: {e}"}), 500

    response = jsonify(leads)
    if 'gzip' in request.headers.get('Accept-Encoding', ''):
        response.set_data(gzip.compress(response.get_data()))
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Vary'] = 'Accept-Encoding'
    return response


@app.route('/adv')
def adv_inbox():
    return send_from_directory(TEMPLATE_DIR, 'adv.html')


@app.route('/api/adv')
def adv_data():
    path = os.path.join(SCRIPT_DIR, 'ALAMAT_ADV_SIGNALS.csv')
    if not os.path.exists(path):
        return jsonify({'rows': [], 'metadata': {}, 'error': 'ADV data has not been imported yet.'})
    try:
        with open(path, encoding='utf-8', newline='') as f:
            rows = list(csv.DictReader(f))
        with open(os.path.join(SCRIPT_DIR, 'ALAMAT_ADV_SIGNALS.json'), encoding='utf-8') as f:
            metadata = json.load(f)
        review_path = os.path.join(SCRIPT_DIR, 'ADV_REVIEWS.json')
        reviews = {}
        if os.path.exists(review_path):
            with open(review_path, encoding='utf-8') as f:
                reviews = json.load(f)
        for row in rows:
            row['review'] = reviews.get(row['crd_number'], {})
            for key in ('record_type', 'adv_id', 'source_url', 'feed_date', 'phone', 'new_to_snapshot', 'verification_status', 'freshness_bucket'):
                row.pop(key, None)
        rows.sort(key=lambda row: row.get('registration_date', ''), reverse=True)
        response = jsonify({'rows': rows, 'metadata': metadata})
        if 'gzip' in request.headers.get('Accept-Encoding', ''):
            response.set_data(gzip.compress(response.get_data()))
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Vary'] = 'Accept-Encoding'
        return response
    except (OSError, ValueError) as error:
        return jsonify({'error': str(error)}), 500


@app.route("/api/backlog", methods=["GET"])
def get_research_backlog():
    """Build the VC universe from the latest SEC master data and return it."""
    source = "live_sec_master"
    try:
        if os.path.exists(LEADS_FILE):
            candidate_rows, unresolved_source_rows = build_research_backlog_rows(
                LEADS_FILE,
                date.today(),
            )
        elif os.path.exists(BACKLOG_FILE):
            source = "saved_backlog_fallback"
            with open(BACKLOG_FILE, "r", encoding="utf-8") as f:
                saved_rows = list(csv.DictReader(f))
            candidate_rows = [row for row in saved_rows if row.get("record_type") != "unresolved_vc_filing"]
            unresolved_source_rows = [row for row in saved_rows if row.get("record_type") == "unresolved_vc_filing"]
        else:
            return jsonify({
                "rows": [],
                "total": 0,
                "counts": {},
                "unresolved_rows": [],
                "unresolved_total": 0,
                "source": "empty",
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:
        return jsonify({"error": f"Failed to rebuild research backlog: {e}"}), 500

    rows = [prepare_backlog_for_display(row, index) for index, row in enumerate(candidate_rows)]
    unresolved_rows = [
        prepare_backlog_for_display(row, len(rows) + index)
        for index, row in enumerate(unresolved_source_rows)
    ]
    counts = {}
    for prepared in rows:
        bucket = str(prepared.get("backlog_bucket") or "unknown")
        counts[bucket] = counts.get(bucket, 0) + 1

    return jsonify({
        "rows": rows,
        "total": len(rows),
        "counts": counts,
        "unresolved_rows": unresolved_rows,
        "unresolved_total": len(unresolved_rows),
        "source": source,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Compute high-level lead dashboard stats from the CSV file."""
    if not os.path.exists(LEADS_FILE):
        return jsonify({
            "total_leads": 0,
            "new_since_last_run": 0,
            "likely_new_firms": 0,
            "existing_managers": 0,
            "needs_review": 0,
            "not_checked": 0
        })

    total = 0
    new_since_last_run = 0
    likely_new_firms = 0
    existing_managers = 0
    needs_review = 0
    not_checked = 0

    try:
        with open(LEADS_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                manager_status = reassess_saved_lead(row).get("manager_status_code") or "not_checked"
                if manager_status == "not_vc":
                    continue
                total += 1
                if str(row.get("is_new_since_last_run", "")).lower() == "yes":
                    new_since_last_run += 1

                if manager_status == "likely_new":
                    likely_new_firms += 1
                elif manager_status == "existing_manager":
                    existing_managers += 1
                elif manager_status == "needs_review":
                    needs_review += 1
                else:
                    not_checked += 1

    except Exception as e:
        return jsonify({"error": f"Error gathering stats: {e}"}), 500

    return jsonify({
        "total_leads": total,
        "new_since_last_run": new_since_last_run,
        "likely_new_firms": likely_new_firms,
        "existing_managers": existing_managers,
        "needs_review": needs_review,
        "not_checked": not_checked
    })


def github_api(method, path, **kwargs):
    return requests.request(
        method,
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{REFRESH_WORKFLOW}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=15,
        **kwargs,
    )


def latest_github_run():
    """Return the newest run of the refresh workflow, or None."""
    response = github_api("GET", "/runs", params={"per_page": 1})
    response.raise_for_status()
    runs = response.json().get("workflow_runs") or []
    return runs[0] if runs else None


def github_run_status(since=""):
    """Describe the refresh workflow run started at or after `since` (ISO time)."""
    run = latest_github_run()
    if not run or (since and run.get("created_at", "") < since):
        return {"running": bool(since), "logs": "Waiting for GitHub to start the run...", "run_url": ""}
    running = run.get("status") in ACTIVE_RUN_STATES
    state = run.get("status") if running else (run.get("conclusion") or run.get("status"))
    lines = [
        f"GitHub Actions run #{run.get('run_number')}: {state}",
        f"Started: {run.get('created_at', '')}",
        f"Details: {run.get('html_url', '')}",
    ]
    if running:
        lines.append("The SEC search usually takes several minutes.")
    elif run.get("conclusion") == "success":
        lines.append("Done. New data appears here once Vercel finishes redeploying the commit.")
    return {"running": running, "logs": "\n".join(lines), "run_url": run.get("html_url", "")}


@app.route("/api/run", methods=["POST"])
def run_pipeline_api():
    """Trigger the pipeline script, or the GitHub Action when hosted."""
    global pipeline_status
    if not RUNS_ENABLED:
        return jsonify({
            "status": "error",
            "message": "Runs are not set up on the hosted site. Add GITHUB_DISPATCH_TOKEN in Vercel."
        }), 403
    if RUN_PASSWORD and request.headers.get("X-Run-Password") != RUN_PASSWORD:
        return jsonify({"status": "error", "message": "Password required.", "needs_password": True}), 401

    data = request.get_json() or {}
    try:
        days = int(data.get("days", 30))
        min_size = int(data.get("min_size", 5000000))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Days and minimum size must be numbers."}), 400
    lead_type = str(data.get("type", "vc")).strip().lower()

    if lead_type not in ["vc", "pe", "fund2"]:
        return jsonify({"status": "error", "message": "Invalid type. Must be vc, pe, or fund2"}), 400

    if HOSTED:
        try:
            run = latest_github_run()
            if run and run.get("status") in ACTIVE_RUN_STATES:
                return jsonify({"status": "error", "message": "A refresh is already running on GitHub."}), 400
            started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            response = github_api("POST", "/dispatches", json={
                "ref": GITHUB_REF,
                "inputs": {"lead_type": lead_type, "days": str(days), "min_size": str(min_size)},
            })
        except requests.RequestException as e:
            return jsonify({"status": "error", "message": f"Could not reach GitHub: {e}"}), 502
        if response.status_code != 204:
            return jsonify({
                "status": "error",
                "message": f"GitHub refused the run ({response.status_code}): {response.text[:200]}"
            }), 502
        return jsonify({"status": "success", "message": "Refresh started on GitHub.", "started_at": started_at})

    if pipeline_status["running"]:
        return jsonify({"status": "error", "message": "Pipeline is already running."}), 400

    # Start runner thread
    t = threading.Thread(target=run_pipeline_thread, args=(days, lead_type, min_size), name="LeadFinderThread")
    t.daemon = True
    t.start()

    return jsonify({"status": "success", "message": "Pipeline triggered successfully."})


@app.route("/api/status", methods=["GET"])
def get_pipeline_status():
    """Retrieve current background runner status."""
    if HOSTED and GITHUB_TOKEN:
        try:
            github = github_run_status()
        except requests.RequestException:
            github = {"running": False}
        return jsonify({**pipeline_status, "running": github["running"], "hosted": True})
    return jsonify(pipeline_status)


@app.route("/api/logs", methods=["GET"])
def get_pipeline_logs():
    """Read the live run logs file, or the GitHub run summary when hosted."""
    if HOSTED and GITHUB_TOKEN:
        try:
            return jsonify(github_run_status(request.args.get("since", "")))
        except requests.RequestException as e:
            return jsonify({"logs": f"Could not reach GitHub: {e}", "running": False})

    if not os.path.exists(LOGS_FILE):
        return jsonify({"logs": "No logs recorded yet."})

    try:
        with open(LOGS_FILE, "r", encoding="utf-8") as f:
            logs = f.read()
    except Exception as e:
        return jsonify({"logs": f"Error reading logs: {e}"})

    return jsonify({"logs": logs, "running": pipeline_status["running"]})


if __name__ == "__main__":
    print("=" * 60)
    print("VC Lead Finder Dashboard server running on http://localhost:5001")
    print("=" * 60)
    app.run(host="localhost", port=5001, debug=True)
