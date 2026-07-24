#!/usr/bin/env python3
"""Validate Alamat agent verdicts, update the durable ledger, and write a daily report."""

import argparse
import csv
import os
from collections import Counter
from datetime import date
from pathlib import Path


VERDICTS = {"good_lead", "needs_review", "reject"}
WEBSITE_STATUSES = {
    "unknown",
    "official_domain_verified",
    "not_found_after_search",
    "broken",
    "placeholder",
    "thin_or_incomplete",
    "adequate",
}
OFFER_ROUTES = {"website", "smm_branding", "branding_positioning", "presence_check"}
REVIEW_FIELDS = [
    "agent_task_id",
    "record_key",
    "reviewed_at",
    "firm_name",
    "verdict",
    "firm_verified",
    "manager_verified",
    "decision_maker",
    "contact_title",
    "linkedin_person",
    "linkedin_company",
    "website_url",
    "website_status",
    "offer_route",
    "verified_facts",
    "rejection_reason",
    "next_action",
    "evidence_sources",
    "external_action_taken",
]


def validate_review(review, queue_by_task):
    task_id = str(review.get("agent_task_id") or "").strip()
    if task_id not in queue_by_task:
        raise ValueError(f"Unknown agent_task_id: {task_id or '<blank>'}")
    queue_row = queue_by_task[task_id]
    expected_key = str(queue_row.get("record_key") or "").strip()
    if str(review.get("record_key") or "").strip() != expected_key:
        raise ValueError(f"record_key mismatch for {task_id}")

    verdict = str(review.get("verdict") or "").strip()
    if verdict not in VERDICTS:
        raise ValueError(f"Invalid verdict for {task_id}: {verdict or '<blank>'}")
    website_status = str(review.get("website_status") or "").strip()
    if website_status not in WEBSITE_STATUSES:
        raise ValueError(f"Invalid website_status for {task_id}: {website_status or '<blank>'}")
    offer_route = str(review.get("offer_route") or "").strip()
    if offer_route not in OFFER_ROUTES:
        raise ValueError(f"Invalid offer_route for {task_id}: {offer_route or '<blank>'}")
    if str(review.get("external_action_taken") or "").strip().casefold() != "no":
        raise ValueError(f"external_action_taken must be 'no' for {task_id}")
    if "http" not in str(review.get("evidence_sources") or ""):
        raise ValueError(f"At least one direct evidence URL is required for {task_id}")

    if verdict == "good_lead":
        required = {
            "firm_verified": "yes",
            "manager_verified": "yes",
        }
        for field, expected in required.items():
            if str(review.get(field) or "").strip().casefold() != expected:
                raise ValueError(f"good_lead requires {field}={expected} for {task_id}")
        for field in ("decision_maker", "linkedin_person", "verified_facts"):
            if not str(review.get(field) or "").strip():
                raise ValueError(f"good_lead requires {field} for {task_id}")
        if website_status in {"unknown", "official_domain_verified"}:
            raise ValueError(f"good_lead requires a completed website audit for {task_id}")
        if offer_route == "presence_check":
            raise ValueError(f"good_lead requires a resolved offer route for {task_id}")
        if str(review.get("evidence_sources") or "").count("http") < 2:
            raise ValueError(f"good_lead requires at least two evidence URLs for {task_id}")
    elif verdict == "reject":
        if not str(review.get("rejection_reason") or "").strip():
            raise ValueError(f"reject requires rejection_reason for {task_id}")
    elif not str(review.get("next_action") or "").strip():
        raise ValueError(f"needs_review requires next_action for {task_id}")


def write_report(rows, destination, report_date=None):
    report_date = report_date or date.today().isoformat()
    counts = Counter(row["verdict"] for row in rows)
    lines = [
        f"# Alamat daily lead report — {report_date}",
        "",
        f"- Agent reviewed: {len(rows)}",
        f"- Good leads: {counts['good_lead']}",
        f"- Needs review: {counts['needs_review']}",
        f"- Rejected: {counts['reject']}",
        "- External actions taken: 0",
        "",
    ]
    good_leads = [row for row in rows if row["verdict"] == "good_lead"]
    if good_leads:
        lines.append("## Good leads")
        lines.append("")
        for row in good_leads:
            lines.extend([
                f"### {row['firm_name']}",
                "",
                f"- Decision-maker: {row['decision_maker']} — {row.get('contact_title') or 'title not recorded'}",
                f"- Website: {row['website_status']} — {row.get('website_url') or 'no official URL found'}",
                f"- Offer route: {row['offer_route']}",
                f"- Verified facts: {row['verified_facts']}",
                f"- Evidence: {row['evidence_sources']}",
                f"- Next action: {row.get('next_action') or 'Wait for user approval.'}",
                "",
            ])
    else:
        lines.extend(["## Good leads", "", "None verified in this batch.", ""])
    Path(destination).write_text("\n".join(lines), encoding="utf-8")


def apply_reviews(queue_path, reviews_path, ledger_path, report_path, report_date=None):
    with Path(queue_path).open(newline="", encoding="utf-8-sig") as handle:
        queue_rows = list(csv.DictReader(handle))
    queue_by_task = {str(row.get("agent_task_id") or "").strip(): row for row in queue_rows}
    with Path(reviews_path).open(newline="", encoding="utf-8-sig") as handle:
        reviews = list(csv.DictReader(handle))
    if not reviews:
        raise ValueError("Agent review file is empty")

    seen = set()
    normalized_reviews = []
    for original in reviews:
        review = {field: str(original.get(field) or "").strip() for field in REVIEW_FIELDS}
        task_id = review["agent_task_id"]
        if task_id in seen:
            raise ValueError(f"Duplicate agent_task_id in review file: {task_id}")
        seen.add(task_id)
        review["reviewed_at"] = review["reviewed_at"] or (report_date or date.today().isoformat())
        validate_review(review, queue_by_task)
        normalized_reviews.append(review)

    existing = []
    ledger = Path(ledger_path)
    if ledger.exists():
        with ledger.open(newline="", encoding="utf-8-sig") as handle:
            existing = list(csv.DictReader(handle))
    merged = {str(row.get("agent_task_id") or "").strip(): row for row in existing}
    for row in normalized_reviews:
        merged[row["agent_task_id"]] = row

    ledger.parent.mkdir(parents=True, exist_ok=True)
    temp_path = ledger.with_name(f".{ledger.name}.tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged.values())
    os.replace(temp_path, ledger)
    write_report(normalized_reviews, report_path, report_date=report_date)
    print(f"Merged {len(normalized_reviews)} agent reviews into {ledger_path}")
    print(f"Saved daily report to {report_path}")
    return normalized_reviews


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue", help="Daily agent queue CSV")
    parser.add_argument("reviews", help="Completed agent review CSV")
    parser.add_argument("ledger", help="Durable review ledger CSV")
    parser.add_argument("report", help="Daily Markdown report")
    parser.add_argument("--date", help="Report date in YYYY-MM-DD format")
    args = parser.parse_args()
    apply_reviews(args.queue, args.reviews, args.ledger, args.report, report_date=args.date)


if __name__ == "__main__":
    main()
