#!/usr/bin/env python3
"""
Fresh Fund II VC Finder
Searches SEC EDGAR for VERY RECENT (last 30-60 days) Venture Capital "Fund II" filings.
Extracts contacts and saves to FRESH_FUND_II_VCS.csv.
"""

import requests
import csv
import time
import re
import os
import sys
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_URL = "https://efts.sec.gov/LATEST/search-index"
HEADERS = {"User-Agent": "VCFund2Finder contact@example.com", "Accept": "application/json"}
MIN_CIK = 1800000  # allow slightly older CIKs since they are on fund 2

SEARCH_QUERIES = [
    "\"fund II\"", "\"fund 2\"", "\"fund two\"",
    "venture capital fund II", "venture fund II"
]

# SPV / junk filters
SPV_NOISE = re.compile(
    r"series of|series [a-z]?\d|roll up|angellist|allocations|exitfund|platform funds|"
    r"spv|co-invest|co.invest|sidecar|holdings.*series|series.*holdings", re.IGNORECASE
)

# Exclude non-VC types
NON_VC = re.compile(
    r"real estate|realty|mortgage|housing|reit|apartment|residential|"
    r"oil\s|gas\s|energy fund|mining|petroleum|mineral|insurance|annuity|"
    r"offshore fund|master fund|feeder fund|"
    r"hedge fund|macro fund|scsp|scsps|opportunity zone|qualified opportunity|"
    r"private equity|buyout|litigation|settlement fund|film|movie|entertainment fund|"
    r"warehouse|industrial fund|debt fund|credit fund|"
    r"roth|401k|ira\s|annuit|pension",
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


def get_contact_info(cik):
    info = {"phone": "", "contact_name": "", "contact_title": "",
            "address": "", "fund_size": "", "year_inc": ""}
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return info
        data = r.json()
        recent = data.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        if not accessions:
            return info
        acc = accessions[0].replace("-", "")
        idx_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/"
        r2 = requests.get(idx_url + "index.json", headers=HEADERS, timeout=15)
        if r2.status_code != 200:
            return info
        idx = r2.json()
        xml_text = None
        for f_item in idx.get("directory", {}).get("item", []):
            name = f_item.get("name", "")
            if name.endswith(".xml"):
                r3 = requests.get(f"{idx_url}{name}", headers=HEADERS, timeout=15)
                if r3.status_code == 200:
                    xml_text = r3.text
                    break
        if not xml_text:
            return info

        phone = re.search(r"<issuerPhoneNumber>([^<]+)</issuerPhoneNumber>", xml_text)
        if phone:
            digits = re.sub(r"\D", "", phone.group(1))
            if len(digits) == 10:
                info["phone"] = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
            else:
                info["phone"] = phone.group(1).strip()

        amount = re.search(r"<totalOfferingAmount>(\d+)</totalOfferingAmount>", xml_text)
        if amount:
            val = int(amount.group(1))
            if val >= 1_000_000_000:
                info["fund_size"] = f"${val / 1_000_000_000:.1f}B"
            elif val >= 1_000_000:
                info["fund_size"] = f"${val / 1_000_000:.0f}M"
            elif val > 0:
                info["fund_size"] = f"${val:,}"

        persons = re.findall(r"<relatedPersonInfo>(.*?)</relatedPersonInfo>", xml_text, re.DOTALL)
        for p in persons:
            first = re.search(r"<firstName>([^<]+)</firstName>", p)
            last = re.search(r"<lastName>([^<]+)</lastName>", p)
            if first and last:
                n = f"{first.group(1).strip()} {last.group(1).strip()}"
                if not info["contact_name"]:
                    info["contact_name"] = n
                    break

        if not info["contact_name"]:
            signer = re.search(r"<nameOfSigner>([^<]+)</nameOfSigner>", xml_text)
            if signer:
                info["contact_name"] = signer.group(1).strip()

    except:
        pass
    return info

def clean_firm_name(name):
    firm = name
    firm = re.sub(r",?\s*fund\s*(I+|[0-9]+|one|two).*$", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r",?\s*L\.P\.\s*$", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r",?\s*LLC\s*$", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r",?\s*Inc\.?\s*$", "", firm, flags=re.IGNORECASE)
    return firm.strip(" ,\"")

def main():
    # Date range: last 60 days
    today = datetime.now()
    two_months_ago = today - timedelta(days=60)
    start_date = two_months_ago.strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    print("=" * 60)
    print("FRESH FUND II VC LEAD FINDER")
    print(f"Searching: {start_date} to {end_date} (Last 60 days)")
    print("=" * 60)

    # Step 1: Search
    all_leads = {}
    for query in SEARCH_QUERIES:
        print(f"\nSearching: \"{query}\"...")
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
            if SPV_NOISE.search(name):
                continue
            if NON_VC.search(name):
                continue
            if cik:
                filing["firm_name"] = clean_firm_name(name)
                all_leads[cik] = filing
                count += 1
        print(f"  → {count} new leads")
        time.sleep(0.3)

    print(f"\nTotal after filters: {len(all_leads)}")

    # Step 2: Extract contacts
    print("\nExtracting contacts...")
    results = []
    for i, (cik, lead) in enumerate(all_leads.items(), 1):
        contact = get_contact_info(cik)
        lead.update(contact)
        lead["filing_url"] = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=D"
        results.append(lead)

        icon = "📞" if contact["phone"] else "  "
        print(f"  {icon} {i}/{len(all_leads)} | {lead['filing_date']} | {lead['firm_name']} | {contact['phone']} | {contact.get('fund_size', '?')}")
        time.sleep(0.3)

    # Sort newest first
    results.sort(key=lambda x: x.get("filing_date", ""), reverse=True)

    # Save
    output = os.path.join(SCRIPT_DIR, "FRESH_FUND_II_VCS.csv")
    fieldnames = [
        "filing_date", "firm_name", "name", "contact_name", 
        "phone", "fund_size", "cik", "filing_url"
    ]

    with open(output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)

    phones = sum(1 for r in results if r.get("phone"))

    print(f"\n{'='*60}")
    print(f"✅ FRESH FUND II RESULTS")
    print(f"  Total leads: {len(results)}")
    print(f"  📞 With phone numbers: {phones}/{len(results)}")
    print(f"  ✓ Saved to: FRESH_FUND_II_VCS.csv")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
