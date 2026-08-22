#!/usr/bin/env python3
"""Build an inclusive VC-only research universe from the SEC master file."""

import argparse
import csv
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote_plus

from build_monthly_prospects import (
    FOLLOW_ON_NAME_PATTERN,
    PUBLIC_LAUNCH_SIGNAL_TYPES,
    extract_series_manager_name,
    has_explicit_non_vc_metadata,
    normalize_identity,
)


BACKLOG_FIELDS = [
    "record_type",
    "backlog_priority",
    "backlog_bucket",
    "vc_signal_strength",
    "vc_signal_reason",
    "strict_drop_reason",
    "research_recommendation",
    "source_filing_count",
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


# These are inclusive discovery signals, not final qualification claims.
VC_NAME_SIGNAL_PATTERN = re.compile(
    r"\b(venture|ventures|vc|seed|pre[- ]seed|startup|startups)\b",
    re.IGNORECASE,
)
VEHICLE_PATTERN = re.compile(
    r"\b(?:spvs?(?:[-_ ]?\d+)?|series|feeder|syndicate|co-?invest(?:ment)?|"
    r"continuation|project)\b",
    re.IGNORECASE,
)
OPERATING_MANAGER_NAME_PATTERN = re.compile(
    r"\b(venture|ventures|vc|capital|partners?|management)\b",
    re.IGNORECASE,
)


def search_url(base, query):
    return f"{base}{quote_plus(query.strip())}" if query.strip() else ""


def vc_signal_for(row):
    """Return a VC discovery signal or None; uncertainty remains visible."""
    issues = str(row.get("issues") or "")
    if "venture capital fund" in issues.casefold():
        return "explicit_sec_vc", "SEC Form D category: Venture Capital Fund"

    # An explicit non-VC SEC category wins over a vague name signal.
    if has_explicit_non_vc_metadata(issues):
        return None

    identity_text = f"{row.get('firm_name', '')} {row.get('name', '')}"
    if VC_NAME_SIGNAL_PATTERN.search(identity_text):
        return "vc_name_signal", "VC term in issuer or operating-firm candidate; confirmation required"

    if row.get("signal_type") in PUBLIC_LAUNCH_SIGNAL_TYPES:
        signal_text = f"{identity_text} {row.get('qualification_reason', '')}"
        if VC_NAME_SIGNAL_PATTERN.search(signal_text):
            return "public_vc_signal", "Public VC launch signal; operating identity requires confirmation"
    return None


def operating_firm_candidate(row):
    """Resolve a series parent when possible without claiming it is verified."""
    legal_name = str(row.get("name") or "").strip()
    return (
        extract_series_manager_name(legal_name)
        or str(row.get("firm_name") or "").strip()
        or legal_name
    )


def needs_identity_resolution(row, firm):
    """Keep vehicle-only VC filings out of the firm list without deleting them."""
    legal_name = str(row.get("name") or row.get("firm_name") or "")
    series_parent = extract_series_manager_name(legal_name)
    if series_parent:
        return bool(
            VEHICLE_PATTERN.search(series_parent)
            or not OPERATING_MANAGER_NAME_PATTERN.search(series_parent)
        )
    return bool(VEHICLE_PATTERN.search(firm) or VEHICLE_PATTERN.search(legal_name))


def is_established_or_follow_on(row):
    issuer_text = f"{row.get('firm_name', '')} {row.get('name', '')}"
    return (
        row.get("manager_status_code") == "existing_manager"
        or row.get("fund_stage") in {"Fund II", "Later Fund"}
        or bool(FOLLOW_ON_NAME_PATTERN.search(issuer_text))
    )


def bucket_for(row, signal_strength):
    if is_established_or_follow_on(row):
        return "established_manager_watchlist"
    if signal_strength == "explicit_sec_vc":
        return "explicit_vc_candidate"
    return "vc_name_signal_review"


def priority_for(row, signal_strength):
    if is_established_or_follow_on(row):
        return 4
    if row.get("manager_status_code") == "likely_new":
        return 1
    if signal_strength == "explicit_sec_vc" and row.get("fund_stage") == "Fund I" and row.get("filer_status") == "first_filer":
        return 1
    if signal_strength == "explicit_sec_vc":
        return 2
    return 3


def recommendation_for(row, signal_strength):
    if is_established_or_follow_on(row):
        return "Established-manager watchlist. Do not count as a new-firm lead without contrary evidence."
    if signal_strength == "explicit_sec_vc":
        return "Verify the operating firm, manager history, decision-maker, LinkedIn presence, and website."
    return "Confirm this is an operating VC firm before researching its decision-maker and public presence."


def is_vc_backlog_candidate(row, reason=None):
    """Compatibility helper used by tests and other scripts."""
    signal = vc_signal_for(row)
    if not signal:
        return False
    firm = operating_firm_candidate(row)
    return bool(normalize_identity(firm)) and not needs_identity_resolution(row, firm)


def representative_score(row, signal_strength):
    return (
        3 if signal_strength == "explicit_sec_vc" else 2 if signal_strength == "public_vc_signal" else 1,
        1 if not is_established_or_follow_on(row) else 0,
        2 if row.get("manager_status_code") == "likely_new" else 1 if row.get("manager_status_code") != "existing_manager" else 0,
        2 if row.get("fund_stage") == "Fund I" else 1 if row.get("fund_stage") == "Emerging Fund" else 0,
        str(row.get("filing_date") or ""),
    )


def export_row(row, firm, signal_strength, signal_reason, source_filing_count=1, unresolved=False):
    contact = str(row.get("contact_name") or "").strip()
    search_firm = firm if not unresolved else str(row.get("name") or firm)
    query_person = f"{contact} {search_firm}".strip()
    bucket = "identity_resolution" if unresolved else bucket_for(row, signal_strength)
    recommendation = (
        "Resolve the operating VC firm from SEC related parties and public sources; this filing was retained, not rejected."
        if unresolved
        else recommendation_for(row, signal_strength)
    )
    return {
        "record_type": "unresolved_vc_filing" if unresolved else "vc_firm_candidate",
        "backlog_priority": 5 if unresolved else priority_for(row, signal_strength),
        "backlog_bucket": bucket,
        "vc_signal_strength": signal_strength,
        "vc_signal_reason": signal_reason,
        "strict_drop_reason": signal_reason,
        "research_recommendation": recommendation,
        "source_filing_count": source_filing_count,
        "firm_name": "" if unresolved else firm,
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
        "linkedin_company_search_url": search_url("https://www.linkedin.com/search/results/companies/?keywords=", search_firm),
        "website_search_url": search_url("https://www.google.com/search?q=", f'"{search_firm}" venture capital'),
    }


def build_rows(source, today):
    """Return current VC candidates and unresolved filings from the SEC master file."""
    with Path(source).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    candidates = {}
    unresolved = []
    seen_unresolved = set()
    for row in rows:
        signal = vc_signal_for(row)
        if not signal:
            continue
        signal_strength, signal_reason = signal
        firm = operating_firm_candidate(row)
        if not normalize_identity(firm) or needs_identity_resolution(row, firm):
            filing_key = row.get("sec_number") or normalize_identity(row.get("name"))
            if filing_key and filing_key not in seen_unresolved:
                seen_unresolved.add(filing_key)
                unresolved.append(export_row(row, firm, signal_strength, signal_reason, unresolved=True))
            continue

        key = normalize_identity(firm)
        score = representative_score(row, signal_strength)
        existing = candidates.get(key)
        if existing is None:
            candidates[key] = {
                "score": score,
                "row": row,
                "firm": firm,
                "signal_strength": signal_strength,
                "signal_reason": signal_reason,
                "count": 1,
            }
        else:
            existing["count"] += 1
            if score > existing["score"]:
                existing.update({
                    "score": score,
                    "row": row,
                    "firm": firm,
                    "signal_strength": signal_strength,
                    "signal_reason": signal_reason,
                })

    backlog = [
        export_row(
            item["row"],
            item["firm"],
            item["signal_strength"],
            item["signal_reason"],
            source_filing_count=item["count"],
        )
        for item in candidates.values()
    ]
    backlog.sort(key=lambda row: row.get("filing_date", ""), reverse=True)
    backlog.sort(key=lambda row: int(row["backlog_priority"]))
    unresolved.sort(key=lambda row: row.get("filing_date", ""), reverse=True)

    return backlog, unresolved


def build(source, destination, today):
    """Write deduplicated VC firms plus a retained unresolved-filing appendix."""
    backlog, unresolved = build_rows(source, today)

    combined = backlog + unresolved
    with Path(destination).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=BACKLOG_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(combined)
    print(
        f"Exported {len(backlog)} VC firm candidates and "
        f"{len(unresolved)} unresolved VC filings to {destination}"
    )
    return backlog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="ALL_VC_LEADS.csv")
    parser.add_argument("--destination", default="ALAMAT_RESEARCH_BACKLOG.csv")
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    build(args.source, args.destination, datetime.strptime(args.date, "%Y-%m-%d").date())


if __name__ == "__main__":
    main()
