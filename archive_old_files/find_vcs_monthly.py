#!/usr/bin/env python3
"""
Monthly VC Lead Finder
Runs the smart VC scraper for specific months and saves each to a separate CSV.
"""

import requests
import csv
import time
import re
import sys
from datetime import datetime

BASE_URL = "https://efts.sec.gov/LATEST/search-index"
HEADERS = {"User-Agent": "VCLeadFinder contact@example.com", "Accept": "application/json"}

MIN_CIK = 1950000

SEARCH_QUERIES = [
    "ventures",
    "venture capital",
    "venture partners",
    "venture fund",
    "seed fund",
    "vc fund",
]

FUND_3_PLUS = re.compile(
    r"fund\s*(III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX"
    r"|[3-9]|[1-9][0-9])\b",
    re.IGNORECASE
)

ROMAN_3_PLUS = re.compile(
    r"\b(III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX)\s*[,\s]",
    re.IGNORECASE
)

SPV_NOISE = re.compile(
    r"series of|series [a-z]?\d|"
    r"roll up|angellist|allocations|exitfund|platform funds|"
    r"cgf2021|cgf2022|cgf2023|cgf2024|cgf2025|"
    r"spv|co-invest|co.invest|sidecar|"
    r"holdings.*series|series.*holdings",
    re.IGNORECASE
)

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

# Extra junk patterns for final curation
JUNK_FIRM = re.compile(
    r"^AVSF\b|^OurCrowd\b|^WAGMI\b|^8VC\b|^StepStone\b|^Insight Partners|"
    r"^Thrive Capital|^Anduril|^Menlo Ventures|^Menlo AI|^Sound Ventures|"
    r"^Inflection Ventures|^AUGUREY VENTURES|^American Ventures|^Varia Ventures|"
    r"^ID8 Investments|^Collective Global|^Fifth Era|"
    r"LP\s*-\s*[A-Z]\d|Rolling Fund|"
    r"^HCM\s|^GPIH\b|^LCV\s|^CCV-|^NH-OC\b|^MW\sLSV|^SIF\s|^ISV\s|^QP-|"
    r"^SOSV\b|^BP\sNeuralink|^AH\sAmerican|^CL\sAdvisors|^CLC\sStrategic|"
    r"^TBL-VV|^DTFA\b|^WCM\sPartners|^ESO\sSecondary|^Wingra Capital|"
    r"^MV VC FUND|^InvestX|^KittyHawk|^Brainstorm\s|^ICP\sMica|^776\s|"
    r"^Suttona\b|^HDDC\b|^SHV\b|^PBR\s|^U\.S\.\sHMC|^CC\sFrontier|"
    r"^Prossima\b|^IronArc\b|^PV\sNEO|^TSV\s|^Side\s8|^Brez\b|^Fernbrook\b|"
    r"^House\sHoldings|^Monarch\sCollective|^Parco\sImperial|^Norland\b|"
    r"^Snowpoint\b|^Athos\s|^Mintaka\b|^Riptide\b|^Heraean\b|^NHC\s|"
    r"^SV\sBreakout|^Audacious\sD|^Spacecadet|^BANG\sDENSITY|^AIC\sCarmel|"
    r"^PI\s-\sSB|^TAP\sInvestment|^Redpoint\sOmega|^Unity\sSelect|^Elkstone\b|"
    r"^Overture\s|^Kamal\sRavikant|^Coelius\s|^Masad\s|^Avlok\s|^Balaji\b|"
    r"^Zachary\sGinsburg|^Singh\sCapital|^F\.Inc\s|^Ligature\b|^CapitalX\b|"
    r"^Ravikant\s|^Schox\b|^X-Barzell|^BurklandSaaS|^REFASHIOND|"
    r"^Strata\sSolas|^MACP\b|^TF\sGrowth|^Spice\sOpp|^Hyperlink\b|"
    r"^Greymont\b|^SC\sGrowth|^InnovateHealth|^Friedom\b|^Prior\sLake\b|"
    r"^Minds\sFlag|^Innovation\sOpp|^Fellowship\sof|^KE-\d+|^Injury\sBoard|"
    r"^Venture\sBetches|^Syndicate\sBetches|^AmeriTrust\b|^Talent\s20|"
    r"^Alpha\sAuto|^Shaad\sKhan|^Jude\sGomila|^Moderne\s|^Photon\b",
    re.IGNORECASE
)


def search_filings(query, start_date, end_date):
    start_from = 0
    while True:
        params = {
            "q": query, "dateRange": "custom",
            "startdt": start_date, "enddt": end_date,
            "forms": "D", "from": start_from, "size": 100,
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
                "name": name, "cik": cik,
                "cik_num": int(cik) if cik else 0,
                "filing_date": src.get("file_date", ""),
                "form_type": src.get("form", ""),
            }

        start_from += 100
        if start_from >= total:
            break
        time.sleep(0.12)
        if start_from % 500 == 0:
            print(f"    {start_from}/{total}...")


def check_first_filer(cik):
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
    if re.search(r"fund\s*(I|1|one)\b", name, re.IGNORECASE):
        return "Fund I"
    elif re.search(r"fund\s*(II|2|two)\b", name, re.IGNORECASE):
        return "Fund II"
    return "No fund #"


def clean_firm_name(name):
    firm = name
    firm = re.sub(r",?\s*fund\s*(I+|[0-9]+|one|two).*$", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r",?\s*L\.?P\.?\s*$", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r",?\s*LLC\s*$", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r",?\s*Inc\.?\s*$", "", firm, flags=re.IGNORECASE)
    return firm.strip(" ,\"")


def run_month(month_name, start_date, end_date, output_file):
    print("\n" + "=" * 70)
    print(f"  {month_name.upper()} | {start_date} to {end_date}")
    print("=" * 70)

    all_leads = {}
    for query in SEARCH_QUERIES:
        print(f"\n  Searching: \"{query}\"...")
        count = 0
        for filing in search_filings(query, start_date, end_date):
            cik = filing["cik"]
            name = filing["name"]
            if cik and cik in all_leads:
                continue
            if filing["cik_num"] < MIN_CIK:
                continue
            if filing["form_type"] != "D":
                continue
            if FUND_3_PLUS.search(name):
                continue
            if ROMAN_3_PLUS.search(name):
                continue
            if SPV_NOISE.search(name):
                continue
            if NON_VC.search(name):
                continue
            if cik:
                filing["fund_stage"] = classify_fund(name)
                filing["firm_name"] = clean_firm_name(name)
                # Extra junk filter
                if JUNK_FIRM.search(filing["firm_name"]):
                    continue
                all_leads[cik] = filing
                count += 1
        print(f"  → {count} new leads")
        time.sleep(0.3)

    print(f"\n  Total after filters: {len(all_leads)}")

    # Check filing history
    print(f"  Checking filing history...")
    leads_list = []
    checked = 0
    for cik, lead in all_leads.items():
        status, num = check_first_filer(cik)
        lead["filer_status"] = status
        lead["total_filings"] = num
        # Skip established filers
        if status != "established":
            leads_list.append(lead)
        checked += 1
        if checked % 25 == 0:
            print(f"    Checked {checked}/{len(all_leads)}...")
        time.sleep(0.1)

    # Sort
    status_order = {"first_filer": 0, "new_filer": 1, "unknown": 2}
    leads_list.sort(
        key=lambda x: (status_order.get(x["filer_status"], 4), -int(x["filing_date"].replace("-", "") or "0"))
    )

    # Save
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["checked", "firm_name", "name", "fund_stage", "filer_status",
                     "total_filings", "filing_date", "cik", "filing_url"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for l in leads_list:
            l["checked"] = ""
            l["filing_url"] = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={l['cik']}&type=D"
            w.writerow({k: l.get(k, "") for k in fieldnames})

    first = sum(1 for l in leads_list if l["filer_status"] == "first_filer")
    new = sum(1 for l in leads_list if l["filer_status"] == "new_filer")

    print(f"\n  ✓ {month_name}: {len(leads_list)} leads (🔥 {first} first-filers, ⭐ {new} new)")
    print(f"  ✓ Saved to: {output_file}")

    # Show top leads
    first_filers = [l for l in leads_list if l["filer_status"] == "first_filer"]
    print(f"\n  Top 10 {month_name} leads:")
    for i, l in enumerate(first_filers[:10], 1):
        print(f"    {i}. {l['firm_name']} | {l['fund_stage']} | {l['filing_date']}")

    return len(leads_list)


def main():
    print("=" * 70)
    print("MONTHLY VC LEAD FINDER")
    print("Searching October, November, December 2025")
    print("=" * 70)

    months = [
        ("October 2025", "2025-10-01", "2025-10-31", "vc_leads_october.csv"),
        ("November 2025", "2025-11-01", "2025-11-30", "vc_leads_november.csv"),
        ("December 2025", "2025-12-01", "2025-12-31", "vc_leads_december.csv"),
    ]

    totals = {}
    for month_name, start, end, output in months:
        count = run_month(month_name, start, end, output)
        totals[month_name] = count

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for month, count in totals.items():
        print(f"  {month}: {count} leads")
    print(f"  TOTAL: {sum(totals.values())} leads")
    print("=" * 70)


if __name__ == "__main__":
    main()
