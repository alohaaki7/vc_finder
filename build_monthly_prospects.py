#!/usr/bin/env python3
"""Build a 100-prospect Alamat queue without per-firm LLM research."""

import argparse
import csv
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote_plus


OUTPUT_FIELDS = [
    "checked", "signal_type", "firm_name", "name", "vehicle_type", "lead_status",
    "discovery_score", "discovery_reason", "contact_name", "contact_title",
    "contact_verification_status", "is_new_since_last_run", "first_seen_at", "last_seen_at",
    "manager_status_code", "manager_status", "manager_novelty_score", "manager_confidence",
    "manager_history_count", "manager_history_reason", "manager_first_filing_date",
    "manager_history_name", "manager_history_url", "manager_matched_identity", "phone",
    "primary_email", "domain", "website_status", "website_status_reason", "website_checked_at",
    "address", "fund_size", "amount_sold", "year_inc", "date_of_first_sale", "fund_stage",
    "filer_status", "total_filings", "platform", "site_score", "vc_score", "freshness_score",
    "freshness_reason", "issues", "linkedin_company", "linkedin_person", "linkedin_status",
    "service_opportunity", "qualification_score", "qualification_tier", "qualification_reason",
    "evidence_sources", "all_contacts", "filing_date", "crd", "sec_number", "filing_url",
    "city", "state", "country",
]
EXPLICIT_NON_VC_METADATA_PATTERN = re.compile(
    r"\b(private\s+equity\s+fund|hedge\s+fund|real\s+estate|commercial|residential|"
    r"oil\s+and\s+gas|oil|gas|mortgage|credit\s+fund|debt\s+fund)\b",
    re.IGNORECASE,
)


def normalize_identity(value):
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def has_explicit_non_vc_metadata(*values):
    return bool(EXPLICIT_NON_VC_METADATA_PATTERN.search(" ".join(str(value or "") for value in values)))


def parse_amount(value):
    text = str(value or "").strip().upper().replace(",", "").replace("$", "")
    if not text or text in {"N/A", "NONE", "INDEFINITE"}:
        return 0
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMB]?)", text)
    if not match:
        return 0
    amount = float(match.group(1))
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[match.group(2)]
    return int(amount * multiplier)


MONTHLY_FIELDS = [
    "monthly_rank",
    "monthly_batch",
    "prospect_score",
    "prospect_tier",
    "volume_status",
    "offer_route",
    "research_depth",
    "source_confidence",
    "volume_reason",
    "linkedin_search_url",
    "website_search_url",
    "signal_title",
    "signal_source",
    "signal_date",
]
MONTHLY_OUTPUT_FIELDS = MONTHLY_FIELDS + OUTPUT_FIELDS

EXCLUDED_WORKFLOW_STATUSES = {
    "approved",
    "contacted",
    "do_not_contact",
    "duplicate",
}

HARD_REJECTION_PATTERNS = (
    r"\bestablished manager\b",
    r"\bexisting manager\b",
    r"\bestablished multi[- ]fund\b",
    r"\blarge established\b",
    r"\bprior fund[- ]management experience\b",
    r"\bpreviously (?:co-)?founded .{0,50}\binvestment fund\b",
    r"\bbrand originated in \d{4}\b",
    r"\bmanager operating since \d{4}\b",
    r"\brelaunched independently\b",
    r"\bnew vehicle rather than (?:a )?new manager\b",
    r"\bnot a new or unknown manager\b",
    r"\bnot a standalone\b",
    r"\bregional sleeve\b",
    r"\bspecial[- ]purpose vehicle\b",
    r"\bdeal[- ]specific vehicle\b",
    r"\bspv\b",
    r"\bseries vehicle\b",
    r"\bwrong (?:asset class|category)\b",
    r"\boutside vc\b",
    r"\bnot vc\b",
    r"\bprivate[- ]credit\b",
    r"\bprivate equity\b",
    r"\breal estate\b",
    r"\bcredit/hedge\b",
    r"\blending business\b",
    r"\bnewness hard gate fails\b",
)

STRONG_VC_NAME_PATTERN = re.compile(
    r"\b(venture|ventures|vc|seed|pre[- ]seed|startup|startups|technology|tech|innovation)\b",
    re.IGNORECASE,
)
PUBLIC_LAUNCH_SIGNAL_TYPES = {"launch_news", "founder_launch", "emerging_manager_program", "linkedin_launch"}
FOLLOW_ON_NAME_PATTERN = re.compile(
    r"\b(?:fund|vc)\s*(?:ii|iii|iv|v|vi|[2-9]|[1-9]\d+)\b",
    re.IGNORECASE,
)


def normalized_text(*values):
    return " ".join(str(value or "") for value in values).casefold()


def hard_rejection_reason(row):
    """Return a deterministic hard-gate reason, or an empty string."""
    vehicle_type = str(row.get("vehicle_type") or "")
    if vehicle_type in {"possible_spv_or_series", "possible_non_vc"}:
        return vehicle_type
    if has_explicit_non_vc_metadata(row.get("issues", "")):
        return "explicit non-VC SEC category"
    if FOLLOW_ON_NAME_PATTERN.search(str(row.get("name") or row.get("firm_name") or "")):
        return "follow-on sequence in issuer name"
    if row.get("fund_stage") in {"Fund II", "Later Fund"}:
        return "follow-on fund"
    if row.get("manager_status_code") == "existing_manager":
        return "existing manager"
    if row.get("lead_status") in EXCLUDED_WORKFLOW_STATUSES:
        return f"workflow status {row.get('lead_status')}"

    if row.get("lead_status") == "rejected":
        evidence = normalized_text(
            row.get("qualification_reason"),
            row.get("manager_history_reason"),
            row.get("issues"),
        )
        for pattern in HARD_REJECTION_PATTERNS:
            if re.search(pattern, evidence):
                return f"audited hard gate: {pattern}"
    return ""


def source_confidence_for(row):
    if row.get("signal_type") in PUBLIC_LAUNCH_SIGNAL_TYPES:
        return "public_launch_signal"
    issues = normalized_text(row.get("issues"))
    if "venture capital fund" in issues:
        return "explicit_vc"
    if "pooled investment fund" in issues:
        return "pooled_fund_name_signal"
    return "name_signal_only"


def has_positive_manager_signal(row):
    name_text = f"{row.get('firm_name', '')} {row.get('name', '')}"
    return bool(STRONG_VC_NAME_PATTERN.search(name_text))


def parse_year(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def filing_age_days(row, today):
    try:
        filed = datetime.strptime(str(row.get("filing_date") or ""), "%Y-%m-%d").date()
    except ValueError:
        return None
    return (today - filed).days


def volume_eligibility(row, today=None):
    """Apply only true ICP hard gates; leave website quality as an offer signal."""
    today = today or date.today()
    hard_reason = hard_rejection_reason(row)
    if hard_reason:
        return False, hard_reason

    if row.get("signal_type") in PUBLIC_LAUNCH_SIGNAL_TYPES:
        signal_text = normalized_text(row.get("firm_name"), row.get("name"), row.get("qualification_reason"))
        if re.search(r"\b(fund\s*(?:ii|iii|iv|v|2|3|4|5)|second fund|third fund|private equity|real estate|credit fund)\b", signal_text):
            return False, "public signal indicates a follow-on or non-VC fund"
        return True, "public new-manager launch signal; identity verification required"

    issues = normalized_text(row.get("issues"))
    explicit_vc = "venture capital fund" in issues
    pooled_other = "pooled investment fund - other investment fund" in issues
    if not explicit_vc and not (pooled_other and has_positive_manager_signal(row)):
        return False, "no explicit VC category or strong pooled-fund VC name signal"

    year_inc = parse_year(row.get("year_inc"))
    if year_inc and year_inc < today.year - 3:
        return False, "issuer formed outside the broad emerging-manager window"

    reasons = []
    reasons.append("explicit VC filing" if explicit_vc else "VC/manager name signal")
    if row.get("fund_stage") == "Fund I":
        reasons.append("Fund I")
    else:
        reasons.append("unsequenced emerging fund")
    if row.get("filer_status") == "first_filer":
        reasons.append("original Form D")
    elif row.get("filer_status"):
        reasons.append("amendment retained for volume review")
    if row.get("lead_status") == "rejected":
        reasons.append("previous soft rejection recovered")
    return True, "; ".join(reasons)


def opportunity_points(row):
    status = str(row.get("website_status") or "unknown")
    return {
        "not_found_after_search": 15,
        "broken": 15,
        "placeholder": 15,
        "thin_or_incomplete": 13,
        "adequate": 8,
        "official_domain_verified": 6,
        "unknown": 5,
    }.get(status, 5)


def calculate_prospect_score(row, today=None):
    """Score monthly prospect priority, not final sales qualification."""
    today = today or date.today()

    newness = 14 if row.get("fund_stage") == "Fund I" else 8
    year_inc = parse_year(row.get("year_inc"))
    if year_inc == today.year:
        newness += 10
    elif year_inc == today.year - 1:
        newness += 8
    elif year_inc == today.year - 2:
        newness += 5
    elif year_inc:
        newness += 2
    else:
        newness += 1
    newness += 6 if row.get("filer_status") == "first_filer" else 3
    newness = min(newness, 30)

    amount_sold = parse_amount(row.get("amount_sold"))
    offering_amount = parse_amount(row.get("fund_size"))
    capital = 15 if amount_sold > 0 else (5 if offering_amount > 0 else 0)

    contact_status = str(row.get("contact_verification_status") or "")
    has_contact = bool(str(row.get("contact_name") or "").strip())
    has_linkedin = bool(str(row.get("linkedin_person") or "").strip())
    if has_linkedin and contact_status in {"verified", "verified_public"}:
        identity = 20
    elif has_linkedin and has_contact:
        identity = 16
    elif has_contact:
        identity = 12
    else:
        identity = 0

    age = filing_age_days(row, today)
    if age is not None and 0 <= age <= 30:
        recency = 15
    elif age is not None and age <= 60:
        recency = 12
    elif age is not None and age <= 90:
        recency = 9
    elif age is not None and age <= 180:
        recency = 5
    else:
        recency = 0

    source_confidence = source_confidence_for(row)
    category = 5 if source_confidence == "explicit_vc" else (4 if source_confidence == "public_launch_signal" else 2)
    return min(newness + capital + identity + recency + opportunity_points(row) + category, 100)


def prospect_tier(score):
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    return "C"


def volume_status_for(row):
    if row.get("lead_status") == "awaiting_approval":
        return "approval_queue"
    has_linkedin = bool(str(row.get("linkedin_person") or "").strip())
    contact_status = str(row.get("contact_verification_status") or "")
    if has_linkedin and contact_status in {"verified", "verified_public"}:
        return "profile_verified"
    if has_linkedin:
        return "profile_found"
    if str(row.get("contact_name") or "").strip():
        return "linkedin_lookup"
    return "identity_lookup"


def offer_route_for(row):
    status = str(row.get("website_status") or "unknown")
    if status in {"not_found_after_search", "broken", "placeholder", "thin_or_incomplete"}:
        return "website"
    if status == "adequate":
        if not row.get("linkedin_company") or row.get("linkedin_status") in {"not_checked", "not_found"}:
            return "smm_branding"
        return "branding_positioning"
    return "presence_check"


def service_opportunity_for(row):
    """Translate the current presence gap into an Alamat offer without contacting anyone."""
    if row.get("volume_status") == "approval_queue":
        return "Already verified; wait for the user's exact approval before any outreach."
    return {
        "website": "Verify the current website gap, then prepare a concise website audit.",
        "smm_branding": "Verify company content cadence and route to SMM or branding.",
        "branding_positioning": "Verify positioning and proof gaps before a branding-led approach.",
        "presence_check": "Perform a lightweight website and LinkedIn presence check.",
    }[row["offer_route"]]


def search_url(base, query):
    return f"{base}{quote_plus(query.strip())}" if query.strip() else ""


def dedupe_key(row):
    firm = normalize_identity(row.get("firm_name") or row.get("name") or "")
    firm = re.sub(r"\s+fund(?:\s+(?:i|1))?$", "", firm).strip()
    location = normalize_identity(f"{row.get('city', '')} {row.get('state', '')}")
    return firm, location


def write_weekly_batches(rows, weekly_dir, batch_size=25):
    directory = Path(weekly_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for legacy_path in directory.glob("ALAMAT_WEEK_*_*.csv"):
        if re.fullmatch(r"ALAMAT_WEEK_\d+_\d+\.csv", legacy_path.name):
            legacy_path.unlink()
    batch_count = max(4, (len(rows) + batch_size - 1) // batch_size)
    for week_number in range(1, batch_count + 1):
        offset = (week_number - 1) * batch_size
        path = directory / f"ALAMAT_WEEK_{week_number}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=MONTHLY_OUTPUT_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows[offset:offset + batch_size])


def build_monthly_queue(
    source,
    destination,
    limit=100,
    deep_limit=20,
    today=None,
    month=None,
    external_paths=None,
    weekly_dir=None,
):
    today = today or date.today()
    month = month or today.strftime("%Y-%m")
    with Path(source).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    for external_path in external_paths or []:
        with Path(external_path).open(newline="", encoding="utf-8-sig") as handle:
            rows.extend(csv.DictReader(handle))

    candidates = []
    for original in rows:
        eligible, reason = volume_eligibility(original, today=today)
        if not eligible:
            continue
        row = dict(original)
        score = calculate_prospect_score(row, today=today)
        row.update({
            "prospect_score": str(score),
            "prospect_tier": prospect_tier(score),
            "volume_status": volume_status_for(row),
            "offer_route": offer_route_for(row),
            "source_confidence": source_confidence_for(row),
            "volume_reason": reason,
            "linkedin_search_url": search_url(
                "https://www.linkedin.com/search/results/people/?keywords=",
                f"{row.get('contact_name', '')} {row.get('firm_name', '')}",
            ),
            "website_search_url": search_url(
                "https://www.google.com/search?q=",
                f'"{row.get("firm_name", "")}" venture capital',
            ),
            "signal_title": row.get("news_title", ""),
            "signal_source": row.get("news_source", ""),
            "signal_date": row.get("news_date", "") or row.get("filing_date", ""),
        })
        if not row.get("evidence_sources") and row.get("filing_url"):
            row["evidence_sources"] = row["filing_url"]
        candidates.append(row)

    candidates.sort(
        key=lambda row: (
            int(row.get("prospect_score") or 0),
            bool(row.get("contact_name")),
            row.get("filing_date", ""),
        ),
        reverse=True,
    )

    selected = []
    seen = set()
    for row in candidates:
        key = dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
        if len(selected) >= limit:
            break

    deep_used = 0
    for rank, row in enumerate(selected, 1):
        row["monthly_rank"] = str(rank)
        row["monthly_batch"] = month
        already_researched = str(row.get("checked") or "").strip().casefold() == "yes"
        ready_for_deep = row.get("volume_status") not in {"identity_lookup", "approval_queue"}
        if already_researched:
            row["research_depth"] = "complete"
        elif ready_for_deep and deep_used < deep_limit:
            row["research_depth"] = "deep"
            deep_used += 1
        else:
            row["research_depth"] = "light"
        row["service_opportunity"] = service_opportunity_for(row)

    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with destination_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MONTHLY_OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(selected)
    if weekly_dir:
        write_weekly_batches(selected, weekly_dir)

    print(f"Selected {len(selected)} monthly prospects from {len(rows)} discovery rows")
    completed = sum(row["research_depth"] == "complete" for row in selected)
    light = sum(row["research_depth"] == "light" for row in selected)
    print(f"Deep research: {deep_used}; light verification: {light}; already researched: {completed}")
    if len(selected) < limit:
        print(f"Warning: target was {limit}; add non-SEC discovery sources to fill the remaining {limit - len(selected)} rows.")
    print(f"Saved monthly queue to {destination}")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Durable Alamat queue CSV")
    parser.add_argument("destination", help="Monthly prospect CSV")
    parser.add_argument("--limit", type=int, default=100, help="Monthly prospect target")
    parser.add_argument("--deep-limit", type=int, default=20, help="Rows reserved for deep research")
    parser.add_argument("--month", help="Batch label in YYYY-MM format")
    parser.add_argument(
        "--external",
        action="append",
        default=[],
        help="Additional normalized public-signal CSV; may be repeated",
    )
    parser.add_argument("--weekly-dir", help="Optional directory for 25-row weekly CSV batches")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.deep_limit < 0 or args.deep_limit > args.limit:
        parser.error("--deep-limit must be between 0 and --limit")
    build_monthly_queue(
        args.source,
        args.destination,
        limit=args.limit,
        deep_limit=args.deep_limit,
        month=args.month,
        external_paths=args.external,
        weekly_dir=args.weekly_dir,
    )


if __name__ == "__main__":
    main()
