#!/usr/bin/env python3
"""Export plausible raw SEC candidates that strict discovery previously sidelined."""

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

from build_monthly_prospects import (
    extract_series_manager_name,
    has_positive_manager_signal,
    normalize_identity,
    volume_eligibility,
)


BACKLOG_FIELDS = [
    "backlog_priority",
    "backlog_bucket",
    "strict_drop_reason",
    "research_recommendation",
    "firm_name",
    "name",
    "contact_name",
    "contact_title",
    "all_contacts",
    "manager_status_code",
    "manager_status",
    "fund_stage",
    "filer_status",
    "year_inc",
    "fund_size",
    "amount_sold",
    "filing_date",
    "issues",
    "city",
    "state",
    "sec_number",
    "filing_url",
    "linkedin_search_url",
    "linkedin_company_search_url",
    "website_search_url",
]


# The backlog is a VC research queue, not a dump of every pooled SEC vehicle.
# A generic Fund I label or a first filer is not enough to put a row here.
VC_NAME_SIGNAL_PATTERN = re.compile(
    r"\b(venture|ventures|vc|seed|pre[- ]seed|startup|startups)\b",
    re.IGNORECASE,
)
VEHICLE_PATTERN = re.compile(
    r"\b(?:spv|spvs|series|feeder|syndicate|co-?invest(?:ment)?|"
    r"continuation|project)\b",
    re.IGNORECASE,
)
NON_VC_PATTERN = re.compile(
    r"\b(private\s+equity|real\s+estate|reits?|eb[- ]?5|mortgage|"
    r"credit|debt|income|lending|oil|gas|restaurants?|agriculture|"
    r"health\s+care|banking|financial\s+services)\b",
    re.IGNORECASE,
)


def search_url(base, query):
    return f"{base}{quote_plus(query.strip())}" if query.strip() else ""


def bucket(row, reason):
    if reason == "no explicit VC category or strong pooled-fund VC name signal":
        return "ambiguous_vc_signal"
    if reason == "existing manager":
        return "established_manager_watchlist"
    if reason == "follow-on fund":
        return "follow_on_watchlist"
    if reason == "issuer formed outside the broad emerging-manager window":
        return "older_issuer_review"
    return "other_review"


def priority(row, reason):
    if reason == "no explicit VC category or strong pooled-fund VC name signal":
        if row.get("fund_stage") == "Fund I" and row.get("filer_status") == "first_filer":
            return 1
        if row.get("fund_stage") == "Fund I" or row.get("filer_status") == "first_filer":
            return 2
        return 3
    if reason == "issuer formed outside the broad emerging-manager window":
        return 3
    if reason == "existing manager":
        return 4
    if reason == "follow-on fund":
        return 5
    return 6


def recommendation(row, reason):
    if reason == "existing manager":
        return "Verify as established manager; keep only for branding/SMM or positioning opportunity."
    if reason == "follow-on fund":
        return "Verify parent manager; do not count as a new-firm lead."
    if reason == "issuer formed outside the broad emerging-manager window":
        return "Check whether a newer operating brand exists despite older legal issuer."
    return "Research operating firm, decision-maker, launch evidence, and public presence before rejecting."


def is_vc_backlog_candidate(row, reason):
    """Keep only rows with an actual VC signal and no obvious vehicle/non-VC marker."""
    issuer_text = f"{row.get('firm_name', '')} {row.get('name', '')}"
    evidence_text = f"{issuer_text} {row.get('issues', '')}"
    if VEHICLE_PATTERN.search(issuer_text) or NON_VC_PATTERN.search(evidence_text):
        return False

    issues = str(row.get("issues") or "").casefold()
    explicit_vc = "venture capital fund" in issues
    if reason == "no explicit VC category or strong pooled-fund VC name signal":
        return bool(VC_NAME_SIGNAL_PATTERN.search(issuer_text))
    if reason in {
        "existing manager",
        "issuer formed outside the broad emerging-manager window",
    }:
        return explicit_vc
    return explicit_vc or bool(VC_NAME_SIGNAL_PATTERN.search(issuer_text))


def build(source, destination, today):
    with Path(source).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    seen = set()
    backlog = []
    for row in rows:
        strict_ok, strict_reason = volume_eligibility(row, today=today, include_watchlist=False)
        broad_ok, _ = volume_eligibility(row, today=today, include_watchlist=True)
        if strict_ok or not broad_ok or not is_vc_backlog_candidate(row, strict_reason):
            continue
        firm = extract_series_manager_name(row.get("name")) or row.get("firm_name") or row.get("name") or ""
        key = (normalize_identity(firm), normalize_identity(f"{row.get('city', '')} {row.get('state', '')}"))
        # Keep separate locations for collision review, but do not repeat the same filing.
        filing_key = row.get("sec_number") or key
        if filing_key in seen:
            continue
        seen.add(filing_key)
        query_person = f"{row.get('contact_name', '')} {firm}".strip()
        backlog.append({
            "backlog_priority": priority(row, strict_reason),
            "backlog_bucket": bucket(row, strict_reason),
            "strict_drop_reason": strict_reason,
            "research_recommendation": recommendation(row, strict_reason),
            "firm_name": firm,
            "name": row.get("name", ""),
            "contact_name": row.get("contact_name", ""),
            "contact_title": row.get("contact_title", ""),
            "all_contacts": row.get("all_contacts", ""),
            "manager_status_code": row.get("manager_status_code", ""),
            "manager_status": row.get("manager_status", ""),
            "fund_stage": row.get("fund_stage", ""),
            "filer_status": row.get("filer_status", ""),
            "year_inc": row.get("year_inc", ""),
            "fund_size": row.get("fund_size", ""),
            "amount_sold": row.get("amount_sold", ""),
            "filing_date": row.get("filing_date", ""),
            "issues": row.get("issues", ""),
            "city": row.get("city", ""),
            "state": row.get("state", ""),
            "sec_number": row.get("sec_number", ""),
            "filing_url": row.get("filing_url", ""),
            "linkedin_search_url": search_url("https://www.linkedin.com/search/results/people/?keywords=", query_person),
            "linkedin_company_search_url": search_url("https://www.linkedin.com/search/results/companies/?keywords=", firm),
            "website_search_url": search_url("https://www.google.com/search?q=", f'"{firm}" venture capital'),
        })
    backlog.sort(key=lambda row: (int(row["backlog_priority"]), row.get("filing_date", "")),)
    with Path(destination).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=BACKLOG_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(backlog)
    print(f"Exported {len(backlog)} sidelined candidates to {destination}")
    return backlog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="ALL_VC_LEADS.csv")
    parser.add_argument("--destination", default="ALAMAT_RESEARCH_BACKLOG.csv")
    parser.add_argument("--date", default="2026-08-21")
    args = parser.parse_args()
    build(args.source, args.destination, datetime.strptime(args.date, "%Y-%m-%d").date())


if __name__ == "__main__":
    main()
