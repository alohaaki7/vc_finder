#!/usr/bin/env python3
"""Select a capped daily batch for Alamat agent research without using an LLM."""

import argparse
import csv
import hashlib
import os
from datetime import date
from pathlib import Path

from build_monthly_prospects import MONTHLY_OUTPUT_FIELDS, normalize_identity


AGENT_CONTROL_FIELDS = [
    "agent_batch_date",
    "agent_task_id",
    "record_key",
    "agent_review_status",
    "agent_instruction",
]
AGENT_QUEUE_FIELDS = AGENT_CONTROL_FIELDS + MONTHLY_OUTPUT_FIELDS
FINAL_VERDICTS = {"good_lead", "needs_review", "reject"}


def record_key(row):
    accession = str(row.get("sec_number") or "").strip()
    if accession:
        return f"sec:{accession}"
    firm = normalize_identity(row.get("firm_name") or row.get("name") or "")
    signal_date = str(row.get("signal_date") or row.get("filing_date") or "").strip()
    return f"firm:{firm}:{signal_date}"


def task_id_for(row):
    return hashlib.sha256(record_key(row).encode("utf-8")).hexdigest()[:16]


def load_completed_keys(ledger_path):
    path = Path(ledger_path)
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return {
            str(row.get("record_key") or "").strip()
            for row in csv.DictReader(handle)
            if str(row.get("verdict") or "").strip() in FINAL_VERDICTS
            and str(row.get("record_key") or "").strip()
        }


def priority_key(row):
    depth_priority = {"deep": 3, "light": 2}.get(str(row.get("research_depth") or ""), 0)
    tier_priority = {"A": 3, "B": 2, "C": 1}.get(str(row.get("prospect_tier") or ""), 0)
    status_priority = {
        "linkedin_lookup": 4,
        "profile_found": 3,
        "profile_verified": 2,
        "identity_lookup": 1,
    }.get(str(row.get("volume_status") or ""), 0)
    try:
        score = int(row.get("prospect_score") or 0)
    except (TypeError, ValueError):
        score = 0
    try:
        rank = int(row.get("monthly_rank") or 999999)
    except (TypeError, ValueError):
        rank = 999999
    return depth_priority, tier_priority, status_priority, score, -rank


def instruction_for(row):
    firm = row.get("firm_name") or row.get("name") or "this candidate"
    return (
        f"Verify {firm} as an active emerging VC firm; confirm the operating brand, founder or GP, "
        "exact current LinkedIn profile, official website condition, company-page activity, and factual "
        "Alamat offer route. Return good_lead, needs_review, or reject with direct evidence URLs. "
        "Do not save, follow, connect, message, email, or contact anyone."
    )


def build_daily_queue(source, ledger, destination, limit=5, batch_date=None):
    batch_date = batch_date or date.today().isoformat()
    with Path(source).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    completed = load_completed_keys(ledger)

    eligible = []
    for original in rows:
        key = record_key(original)
        if not key or key in completed:
            continue
        if str(original.get("research_depth") or "") == "complete":
            continue
        if str(original.get("volume_status") or "") == "approval_queue":
            continue
        if str(original.get("lead_status") or "") in {"approved", "contacted", "do_not_contact", "duplicate"}:
            continue
        row = dict(original)
        row.update({
            "agent_batch_date": batch_date,
            "agent_task_id": task_id_for(row),
            "record_key": key,
            "agent_review_status": "pending",
            "agent_instruction": instruction_for(row),
        })
        eligible.append(row)

    eligible.sort(key=priority_key, reverse=True)
    selected = eligible[:limit]

    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination_path.with_name(f".{destination_path.name}.tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=AGENT_QUEUE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(selected)
    os.replace(temp_path, destination_path)

    print(f"Selected {len(selected)} candidates for agent review from {len(eligible)} eligible survivors")
    print(f"Saved daily agent queue to {destination}")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Monthly prospect CSV")
    parser.add_argument("destination", help="Daily agent queue CSV")
    parser.add_argument("--ledger", default="ALAMAT_AGENT_REVIEWS.csv", help="Durable completed-review ledger")
    parser.add_argument("--limit", type=int, default=5, help="Maximum candidates sent to agents")
    parser.add_argument("--date", help="Batch date in YYYY-MM-DD format")
    args = parser.parse_args()
    if not 1 <= args.limit <= 20:
        parser.error("--limit must be between 1 and 20")
    build_daily_queue(args.source, args.ledger, args.destination, limit=args.limit, batch_date=args.date)


if __name__ == "__main__":
    main()
