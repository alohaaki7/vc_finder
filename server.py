#!/usr/bin/env python3
"""
VC Lead Finder Dashboard Server
Flask web server that exposes API endpoints to run the pipeline, retrieve leads,
display real-time logs, and fetch high-level metrics.
"""

import os
import csv
import hashlib
import json
import re
import tempfile
import threading
from datetime import date
from flask import Flask, jsonify, request, send_from_directory, render_template_string
from pipeline import clean_firm_name, extract_related_name, is_entity_identity, run_pipeline
from build_research_backlog import build as build_research_backlog

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
REVIEW_STATE_FILE = os.path.join(SCRIPT_DIR, "ALAMAT_REVIEW_STATE.json")
LOGS_FILE = os.path.join(SCRIPT_DIR, "pipeline_run.log")
review_state_lock = threading.Lock()

# Lock and state for running pipeline
pipeline_lock = threading.Lock()
pipeline_status = {
    "running": False,
    "current_type": "",
    "current_days": 30,
    "progress": "",
    "error": ""
}

# Standard template directory configuration
TEMPLATE_DIR = os.path.join(SCRIPT_DIR, "templates")
if not os.path.exists(TEMPLATE_DIR):
    os.makedirs(TEMPLATE_DIR)


def prepare_lead_for_display(row):
    """Use a parent manager as the display label for legacy SEC series rows."""
    display_row = dict(row)
    issuer_name = str(display_row.get("name") or "")
    if "series of" in issuer_name.casefold():
        manager_name = clean_firm_name(issuer_name)
        if manager_name:
            display_row["sec_vehicle_name"] = display_row.get("firm_name") or issuer_name
            display_row["firm_name"] = manager_name
    display_row["linkedin_search_firm"] = linkedin_search_firm(display_row.get("firm_name"))
    display_row["linkedin_search_person"] = linkedin_search_person(display_row)
    display_row["linkedin_manager_candidate"] = linkedin_manager_candidate(display_row)
    return display_row


def backlog_key(row):
    """Return a stable identifier that survives backlog rebuilds and reordering."""
    identity = "|".join([
        str(row.get("sec_number") or "").strip().casefold(),
        str(row.get("filing_url") or "").strip().casefold(),
        str(row.get("firm_name") or row.get("name") or "").strip().casefold(),
    ])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def load_review_state():
    if not os.path.exists(REVIEW_STATE_FILE):
        return {}
    try:
        with open(REVIEW_STATE_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_review_state(state):
    directory = os.path.dirname(REVIEW_STATE_FILE)
    fd, temporary_path = tempfile.mkstemp(prefix="alamat-review-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_path, REVIEW_STATE_FILE)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def default_workflow_bucket(row):
    if row.get("record_type") == "unresolved_vc_filing":
        return "needs_identity"
    if row.get("backlog_bucket") == "established_manager_watchlist":
        return "watchlist"
    if row.get("manager_status_code") == "likely_new":
        return "likely_new_vc"
    return "needs_identity"


def prepare_backlog_for_display(row, index=0, review_state=None):
    """Add stable browser-only metadata to a research-backlog row.

    Backlog rows are deliberately still unverified.  The URLs in this view are
    search routes, not assertions that a person or company identity is correct.
    """
    display_row = dict(row)
    stable_key = backlog_key(display_row)
    decision = (review_state or {}).get(stable_key, {})
    display_row["backlog_id"] = f"backlog-{stable_key}"
    display_row["workflow_bucket"] = decision.get("workflow_bucket") or default_workflow_bucket(display_row)
    display_row["workflow_note"] = decision.get("note", "")
    display_row["workflow_updated_at"] = decision.get("updated_at", "")
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
    candidates = [row.get("contact_name", "")]
    candidates.extend(str(row.get("all_contacts") or "").split(";"))

    for candidate in candidates:
        name = extract_related_name(candidate)
        name = re.sub(r"^(?:n/?a|general partner|management company)\s+", "", name, flags=re.IGNORECASE)
        if name and not is_entity_identity(name) and len(name.split()) >= 2:
            return name.title() if name.isupper() else name
    return ""


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

    leads = []
    try:
        with open(LEADS_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                leads.append(prepare_lead_for_display(row))
    except Exception as e:
        return jsonify({"error": f"Failed to read CSV: {e}"}), 500

    return jsonify(leads)


@app.route("/api/backlog", methods=["GET"])
def get_research_backlog():
    """Return deduplicated VC firms and retained unresolved VC filings."""
    if not os.path.exists(BACKLOG_FILE):
        return jsonify({"rows": [], "total": 0, "counts": {}, "unresolved_rows": [], "unresolved_total": 0})

    rows = []
    unresolved_rows = []
    counts = {}
    workflow_counts = {}
    try:
        review_state = load_review_state()
        with open(BACKLOG_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for index, row in enumerate(reader):
                prepared = prepare_backlog_for_display(row, index, review_state)
                workflow_bucket = prepared.get("workflow_bucket") or "needs_identity"
                workflow_counts[workflow_bucket] = workflow_counts.get(workflow_bucket, 0) + 1
                if prepared.get("record_type") == "unresolved_vc_filing":
                    unresolved_rows.append(prepared)
                else:
                    rows.append(prepared)
                    bucket = str(prepared.get("backlog_bucket") or "unknown")
                    counts[bucket] = counts.get(bucket, 0) + 1
    except Exception as e:
        return jsonify({"error": f"Failed to read research backlog: {e}"}), 500

    return jsonify({
        "rows": rows,
        "total": len(rows),
        "counts": counts,
        "workflow_counts": workflow_counts,
        "unresolved_rows": unresolved_rows,
        "unresolved_total": len(unresolved_rows),
    })


@app.route("/api/backlog/<backlog_id>/bucket", methods=["POST"])
def set_backlog_bucket(backlog_id):
    """Persist a user's research decision separately from regenerated source data."""
    stable_key = str(backlog_id or "").removeprefix("backlog-")
    if not re.fullmatch(r"[0-9a-f]{20}", stable_key):
        return jsonify({"error": "Invalid backlog identifier."}), 400

    data = request.get_json(silent=True) or {}
    workflow_bucket = str(data.get("workflow_bucket") or "").strip()
    allowed = {"needs_identity", "likely_new_vc", "watchlist"}
    if workflow_bucket not in allowed:
        return jsonify({"error": "Invalid workflow bucket."}), 400

    with review_state_lock:
        state = load_review_state()
        state[stable_key] = {
            "workflow_bucket": workflow_bucket,
            "note": str(data.get("note") or "").strip()[:500],
            "updated_at": date.today().isoformat(),
        }
        save_review_state(state)

    return jsonify({
        "status": "success",
        "backlog_id": f"backlog-{stable_key}",
        "workflow_bucket": workflow_bucket,
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
            "needs_review": 0
        })

    total = 0
    new_since_last_run = 0
    likely_new_firms = 0
    existing_managers = 0
    needs_review = 0

    try:
        with open(LEADS_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1
                if str(row.get("is_new_since_last_run", "")).lower() == "yes":
                    new_since_last_run += 1

                manager_status = row.get("manager_status_code", "not_checked")
                if manager_status == "likely_new":
                    likely_new_firms += 1
                elif manager_status == "existing_manager":
                    existing_managers += 1
                else:
                    needs_review += 1

    except Exception as e:
        return jsonify({"error": f"Error gathering stats: {e}"}), 500

    return jsonify({
        "total_leads": total,
        "new_since_last_run": new_since_last_run,
        "likely_new_firms": likely_new_firms,
        "existing_managers": existing_managers,
        "needs_review": needs_review
    })


@app.route("/api/run", methods=["POST"])
def run_pipeline_api():
    """Trigger the pipeline script."""
    global pipeline_status
    if pipeline_status["running"]:
        return jsonify({"status": "error", "message": "Pipeline is already running."}), 400

    data = request.get_json() or {}
    days = int(data.get("days", 30))
    lead_type = str(data.get("type", "vc")).strip().lower()
    min_size = int(data.get("min_size", 5000000))

    if lead_type not in ["vc", "pe", "fund2"]:
        return jsonify({"status": "error", "message": "Invalid type. Must be vc, pe, or fund2"}), 400

    # Start runner thread
    t = threading.Thread(target=run_pipeline_thread, args=(days, lead_type, min_size), name="LeadFinderThread")
    t.daemon = True
    t.start()

    return jsonify({"status": "success", "message": "Pipeline triggered successfully."})


@app.route("/api/status", methods=["GET"])
def get_pipeline_status():
    """Retrieve current background runner status."""
    return jsonify(pipeline_status)


@app.route("/api/logs", methods=["GET"])
def get_pipeline_logs():
    """Read the live run logs file."""
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
