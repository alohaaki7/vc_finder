#!/usr/bin/env python3
"""
Smart VC Lead Finder v2
Finds truly NEW venture capital firms from SEC Form D filings.

Key filters:
- High CIK numbers (new SEC registrations)
- Original "D" filings only (not amendments)
- Excludes Fund III+ (established firms)
- Excludes SPVs, series, and non-VC funds
- Checks if entity is a first-time filer
"""

import requests
import csv
import time
import re
from datetime import datetime, timedelta

BASE_URL = "https://efts.sec.gov/LATEST/search-index"
HEADERS = {"User-Agent": "VCLeadFinder contact@example.com", "Accept": "application/json"}

# Date range: Last 90 days (3 months)
today = datetime.now()
ninety_days_ago = today - timedelta(days=90)
START_DATE = ninety_days_ago.strftime("%Y-%m-%d")
END_DATE = today.strftime("%Y-%m-%d")

# CIK threshold - entities registered roughly in last ~2 years
# Higher CIK = newer entity. Current new registrations are ~2100000+
MIN_CIK = 1950000

# Broad VC search queries
SEARCH_QUERIES = [
    "ventures",
    "venture capital",
    "venture partners",
    "venture fund",
    "seed fund",
    "vc fund",
]

# ===== EXCLUSION PATTERNS =====

# Fund III+ (roman numerals and arabic numbers 3+)
FUND_3_PLUS = re.compile(
    r"fund\s*(III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX"
    r"|[3-9]|[1-9][0-9])\b",
    re.IGNORECASE
)

# Roman numerals in fund name (standalone, not just "Fund X" but "XYZ III")
ROMAN_3_PLUS = re.compile(
    r"\b(III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX)\s*[,\s]",
    re.IGNORECASE
)

# SPV / Series / Noise
SPV_NOISE = re.compile(
    r"series of|series [a-z]?\d|"
    r"roll up|angellist|allocations|exitfund|platform funds|"
    r"cgf2021|cgf2022|cgf2023|cgf2024|cgf2025|"
    r"spv|co-invest|co.invest|sidecar|"
    r"holdings.*series|series.*holdings",
    re.IGNORECASE
)

# Non-VC fund types
NON_VC = re.compile(
    r"real estate|realty|mortgage|housing|reit|apartment|residential|"
    r"oil|gas|energy fund|mining|petroleum|mineral|"
    r"insurance|annuity|"
    r"offshore fund|master fund|feeder fund|"
    r"credit fund|credit partner|debt fund|clo |"
    r"hedge fund|macro fund|"
    r"scsp|scsps|"
    r"opportunity zone|qualified opportunity|"
    r"litigation|settlement fund|"
    r"film|movie|entertainment fund|"
    r"warehouse|industrial fund",
    re.IGNORECASE
)


def search_filings(query, start_date, end_date):
    """Search SEC EDGAR and yield filings."""
    start_from = 0
    while True:
        params = {
            "q": query,
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
            "forms": "D",
            "from": start_from,
            "size": 100,
        }
        try:
            r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
            data = r.json()
        except Exception as e:
            print(f"  Error: {e}")
            break

        hits = data.get("hits", {}).get("hits", [])
        total = data.get("hits", {}).get("total", {}).get("value", 0)

        if not hits:
            break

        for hit in hits:
            src = hit.get("_source", {})
            name = src.get("display_names", [""])[0] if src.get("display_names") else src.get("entity", "")
            name = re.sub(r"\s*\(CIK\s*\d+\)\s*$", "", name).strip()
            cik = src.get("ciks", [""])[0] if src.get("ciks") else ""

            yield {
                "name": name,
                "cik": cik,
                "cik_num": int(cik) if cik else 0,
                "filing_date": src.get("file_date", ""),
                "form_type": src.get("form", ""),
            }

        start_from += 100
        if start_from >= total:
            break
        time.sleep(0.12)
        if start_from % 500 == 0:
            print(f"  {start_from}/{total}...")

    return total


def check_first_filer(cik):
    """Check if this CIK has only 1-2 filings total (truly new entity)."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return "unknown", 0
        data = r.json()
        recent = data.get("filings", {}).get("recent", {})
        total_filings = len(recent.get("accessionNumber", []))
        if total_filings <= 2:
            return "first_filer", total_filings
        elif total_filings <= 5:
            return "new_filer", total_filings
        else:
            return "established", total_filings
    except:
        return "unknown", 0


def classify_fund(name):
    """Classify the fund stage."""
    if re.search(r"fund\s*(I|1|one)\b", name, re.IGNORECASE):
        return "Fund I"
    elif re.search(r"fund\s*(II|2|two)\b", name, re.IGNORECASE):
        return "Fund II"
    else:
        return "No fund #"


def clean_firm_name(name):
    """Extract clean firm name from filing name."""
    firm = name
    firm = re.sub(r",?\s*fund\s*(I+|[0-9]+|one|two).*$", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r",?\s*L\.?P\.?\s*$", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r",?\s*LLC\s*$", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r",?\s*Inc\.?\s*$", "", firm, flags=re.IGNORECASE)
    firm = firm.strip(" ,\"")
    return firm


def main():
    print("=" * 70)
    print("SMART VC Lead Finder v2")
    print(f"Date: {START_DATE} to {END_DATE}")
    print(f"Min CIK: {MIN_CIK} (new entities only)")
    print("=" * 70)

    # Step 1: Collect all filings
    all_leads = {}

    for query in SEARCH_QUERIES:
        print(f"\nSearching: \"{query}\"...")
        count = 0

        for filing in search_filings(query, START_DATE, END_DATE):
            cik = filing["cik"]
            name = filing["name"]

            # Skip if already collected
            if cik and cik in all_leads:
                continue

            # FILTER 1: CIK must be high (new entity)
            if filing["cik_num"] < MIN_CIK:
                continue

            # FILTER 2: Only original "D" filings, not amendments
            if filing["form_type"] != "D":
                continue

            # FILTER 3: Exclude Fund III+ 
            if FUND_3_PLUS.search(name):
                continue

            # Also catch standalone roman numerals like "Menlo Ventures XVII"
            if ROMAN_3_PLUS.search(name):
                continue

            # FILTER 4: Exclude SPVs and series
            if SPV_NOISE.search(name):
                continue

            # FILTER 5: Exclude non-VC fund types
            if NON_VC.search(name):
                continue

            if cik:
                filing["fund_stage"] = classify_fund(name)
                filing["firm_name"] = clean_firm_name(name)
                all_leads[cik] = filing
                count += 1

        print(f"  → {count} new quality leads")
        time.sleep(0.3)

    print(f"\nTotal after basic filters: {len(all_leads)}")

    # Step 2: Check each lead against SEC to see if they're first-time filers
    print("\nChecking filing history for each lead (this takes a moment)...")
    leads_with_history = []
    checked = 0

    for cik, lead in all_leads.items():
        status, num_filings = check_first_filer(cik)
        lead["filer_status"] = status
        lead["total_filings"] = num_filings
        leads_with_history.append(lead)

        checked += 1
        if checked % 25 == 0:
            print(f"  Checked {checked}/{len(all_leads)}...")
        time.sleep(0.1)  # Rate limiting

    # Step 3: Sort - first filers first, then by date
    status_order = {"first_filer": 0, "new_filer": 1, "unknown": 2, "established": 3}
    leads_with_history.sort(
        key=lambda x: (status_order.get(x["filer_status"], 4), -int(x["filing_date"].replace("-", "") or "0"))
    )

    # Step 4: Save
    output = "vc_fresh_leads.csv"
    with open(output, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "checked", "firm_name", "name", "fund_stage", "filer_status",
            "total_filings", "filing_date", "cik", "filing_url"
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for l in leads_with_history:
            l["checked"] = ""
            l["filing_url"] = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={l['cik']}&type=D"
            w.writerow({k: l.get(k, "") for k in fieldnames})

    # Stats
    first = sum(1 for l in leads_with_history if l["filer_status"] == "first_filer")
    new = sum(1 for l in leads_with_history if l["filer_status"] == "new_filer")
    fund_none = sum(1 for l in leads_with_history if l["fund_stage"] == "No fund #")
    fund1 = sum(1 for l in leads_with_history if l["fund_stage"] == "Fund I")
    fund2 = sum(1 for l in leads_with_history if l["fund_stage"] == "Fund II")

    print("\n" + "=" * 70)
    print(f"✓ TOTAL QUALITY LEADS: {len(leads_with_history)}")
    print(f"\nBy filing history:")
    print(f"  🔥 First-time filers (1-2 filings): {first}")
    print(f"  ⭐ New filers (3-5 filings): {new}")
    print(f"\nBy fund stage:")
    print(f"  No fund #: {fund_none}")
    print(f"  Fund I: {fund1}")
    print(f"  Fund II: {fund2}")
    print(f"\n✓ Saved to: {output}")
    print("=" * 70)

    # Show best leads
    first_filers = [l for l in leads_with_history if l["filer_status"] == "first_filer"]
    print(f"\n🔥 TOP {min(25, len(first_filers))} FIRST-TIME FILERS (best leads!):")
    print("-" * 70)
    for i, l in enumerate(first_filers[:25], 1):
        print(f"{i}. {l['firm_name']}")
        print(f"   Full name: {l['name']}")
        print(f"   Filed: {l['filing_date']} | {l['fund_stage']} | {l['total_filings']} filing(s)")
        print()


if __name__ == "__main__":
    main()
