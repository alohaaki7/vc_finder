#!/usr/bin/env python3
"""
VC Lead Finder Dashboard Server
Flask web server that exposes API endpoints to run the pipeline, retrieve leads,
display real-time logs, and fetch high-level metrics.
"""

import os
import csv
import json
import threading
from flask import Flask, jsonify, request, send_from_directory, render_template_string
from pipeline import run_pipeline

app = Flask(__name__, static_folder="templates")

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LEADS_FILE = os.path.join(SCRIPT_DIR, "ALL_VC_LEADS.csv")
LOGS_FILE = os.path.join(SCRIPT_DIR, "pipeline_run.log")

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
                leads.append(row)
    except Exception as e:
        return jsonify({"error": f"Failed to read CSV: {e}"}), 500

    return jsonify(leads)


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
