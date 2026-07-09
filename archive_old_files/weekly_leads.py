#!/usr/bin/env python3
"""
Weekly VC Lead Finder
Searches SEC EDGAR for new VC filings from the past 7 days,
extracts contact info, and saves to a date-stamped CSV.

Designed to run via cron every Monday at 9 AM.
"""

import requests
import csv
import time
import re
import os
import subprocess
from datetime import datetime, timedelta
from urllib.parse import quote_plus, unquote

# ===== CONFIG =====
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_URL = "https://efts.sec.gov/LATEST/search-index"
HEADERS = {"User-Agent": "VCLeadFinder contact@example.com", "Accept": "application/json"}
MIN_CIK = 1950000

SEARCH_QUERIES = [
    "ventures", "venture capital", "venture partners",
    "venture fund", "seed fund", "vc fund",
]

# ===== EXCLUSION PATTERNS =====
FUND_3_PLUS = re.compile(
    r"fund\s*(III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX"
    r"|[3-9]|[1-9][0-9])\b", re.IGNORECASE
)
ROMAN_3_PLUS = re.compile(
    r"\b(III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX)\s*[,\s]",
    re.IGNORECASE
)
SPV_NOISE = re.compile(
    r"series of|series [a-z]?\d|roll up|angellist|allocations|exitfund|platform funds|"
    r"cgf2021|cgf2022|cgf2023|cgf2024|cgf2025|spv|co-invest|co.invest|sidecar|"
    r"holdings.*series|series.*holdings", re.IGNORECASE
)
NON_VC = re.compile(
    r"real estate|realty|mortgage|housing|reit|apartment|residential|"
    r"oil|gas|energy fund|mining|petroleum|mineral|insurance|annuity|"
    r"offshore fund|master fund|feeder fund|credit fund|credit partner|debt fund|clo |"
    r"hedge fund|macro fund|scsp|scsps|opportunity zone|qualified opportunity|"
    r"litigation|settlement fund|film|movie|entertainment fund|warehouse|industrial fund",
    re.IGNORECASE
)
JUNK_FIRM = re.compile(
    r"^AVSF\b|^OurCrowd\b|^WAGMI\b|^8VC\b|^StepStone\b|^Insight Partners|"
    r"^Thrive Capital|^Anduril|^Menlo Ventures|^Menlo AI|^Sound Ventures|"
    r"^Inflection Ventures|^AUGUREY|^American Ventures|^Varia Ventures|"
    r"^Collective Global|^Fifth Era|LP\s*-\s*[A-Z]\d|Rolling Fund|"
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
    r"^Overture\s|^Moderne\s|^Photon\b", re.IGNORECASE
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


def check_first_filer(cik):
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return "unknown", 0
        data = r.json()
        recent = data.get("filings", {}).get("recent", {})
        total = len(recent.get("accessionNumber", []))
        if total <= 2:
            return "first_filer", total
        elif total <= 5:
            return "new_filer", total
        else:
            return "established", total
    except:
        return "unknown", 0


def get_contact_info(cik):
    """Fetch Form D XML and extract contact details."""
    info = {"phone": "", "contact_name": "", "contact_title": "",
            "address": "", "fund_size": "", "year_inc": "", "all_contacts": ""}
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

        # Parse XML
        phone = re.search(r"<issuerPhoneNumber>([^<]+)</issuerPhoneNumber>", xml_text)
        if phone:
            digits = re.sub(r"\D", "", phone.group(1))
            if len(digits) == 10:
                info["phone"] = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
            else:
                info["phone"] = phone.group(1).strip()

        year = re.search(r"<yearOfInc>.*?<value>(\d{4})</value>.*?</yearOfInc>", xml_text, re.DOTALL)
        if year:
            info["year_inc"] = year.group(1)

        amount = re.search(r"<totalOfferingAmount>(\d+)</totalOfferingAmount>", xml_text)
        if amount:
            val = int(amount.group(1))
            if val >= 1_000_000_000:
                info["fund_size"] = f"${val / 1_000_000_000:.1f}B"
            elif val >= 1_000_000:
                info["fund_size"] = f"${val / 1_000_000:.0f}M"
            elif val > 0:
                info["fund_size"] = f"${val:,}"

        # Address
        addr = re.search(r"<issuerAddress>(.*?)</issuerAddress>", xml_text, re.DOTALL)
        if addr:
            block = addr.group(1)
            parts = []
            for tag in ["street1", "city", "stateOrCountry", "zipCode"]:
                m = re.search(f"<{tag}>([^<]+)</{tag}>", block)
                if m:
                    parts.append(m.group(1))
            info["address"] = ", ".join(parts)

        # Persons
        persons = re.findall(r"<relatedPersonInfo>(.*?)</relatedPersonInfo>", xml_text, re.DOTALL)
        all_names = []
        for p in persons:
            first = re.search(r"<firstName>([^<]+)</firstName>", p)
            last = re.search(r"<lastName>([^<]+)</lastName>", p)
            rels = re.findall(r"<relationship>([^<]+)</relationship>", p)
            if first and last:
                n = f"{first.group(1).strip()} {last.group(1).strip()}"
                role = ", ".join(rels)
                all_names.append(f"{n} ({role})" if role else n)
                if not info["contact_name"]:
                    info["contact_name"] = n

        # Signer fallback
        if not info["contact_name"]:
            signer = re.search(r"<nameOfSigner>([^<]+)</nameOfSigner>", xml_text)
            if signer:
                info["contact_name"] = signer.group(1).strip()

        title = re.search(r"<signatureTitle>([^<]+)</signatureTitle>", xml_text)
        if title:
            info["contact_title"] = title.group(1).strip()

        info["all_contacts"] = "; ".join(all_names)

    except:
        pass
    return info


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


def find_linkedin_url(firm_name, contact_name=""):
    """Generate LinkedIn search URLs for the firm and contact person."""
    result = {"linkedin_company": "", "linkedin_person": ""}

    # Company search URL — one click to find their LinkedIn page
    if firm_name:
        q = quote_plus(firm_name)
        result["linkedin_company"] = f"https://www.linkedin.com/search/results/companies/?keywords={q}"

    # Person search URL
    if contact_name and contact_name not in ("N/A", "n/a", "None", ""):
        clean_name = re.sub(r'\b(LLC|GP|LP|Inc|N/A|n/a|General Partner|None|NA)\b', '', contact_name).strip()
        clean_name = re.sub(r'[,.]', '', clean_name).strip()
        if clean_name and len(clean_name) > 3 and ' ' in clean_name:
            q = quote_plus(f"{clean_name} {firm_name}")
            result["linkedin_person"] = f"https://www.linkedin.com/search/results/people/?keywords={q}"

    return result


def send_mac_notification(title, message):
    """Send a macOS notification."""
    try:
        script = f'display notification "{message}" with title "{title}"'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except:
        pass


def main():
    today = datetime.now()
    week_ago = today - timedelta(days=7)
    start_date = week_ago.strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    date_stamp = today.strftime("%Y-%m-%d")

    output_file = os.path.join(SCRIPT_DIR, f"weekly_leads_{date_stamp}.csv")
    log_file = os.path.join(SCRIPT_DIR, f"weekly_log_{date_stamp}.txt")

    # Log
    log = open(log_file, "w")
    def log_print(msg):
        print(msg)
        log.write(msg + "\n")

    log_print("=" * 60)
    log_print(f"WEEKLY VC LEAD FINDER — {date_stamp}")
    log_print(f"Searching: {start_date} to {end_date}")
    log_print("=" * 60)

    # Step 1: Search
    all_leads = {}
    for query in SEARCH_QUERIES:
        log_print(f"\nSearching: \"{query}\"...")
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
            if FUND_3_PLUS.search(name) or ROMAN_3_PLUS.search(name):
                continue
            if SPV_NOISE.search(name) or NON_VC.search(name):
                continue
            if cik:
                filing["fund_stage"] = classify_fund(name)
                filing["firm_name"] = clean_firm_name(name)
                if JUNK_FIRM.search(filing["firm_name"]):
                    continue
                all_leads[cik] = filing
                count += 1
        log_print(f"  → {count} new leads")
        time.sleep(0.3)

    log_print(f"\nTotal after filters: {len(all_leads)}")

    # Step 2: Check filing history + extract contacts
    log_print("\nChecking filing history & extracting contacts...")
    results = []
    for i, (cik, lead) in enumerate(all_leads.items(), 1):
        status, num = check_first_filer(cik)
        if status == "established":
            continue
        lead["filer_status"] = status
        lead["total_filings"] = num

        # Get contact info
        contact = get_contact_info(cik)
        lead.update(contact)

        # Find LinkedIn URLs
        linkedin = find_linkedin_url(lead["firm_name"], contact.get("contact_name", ""))
        lead.update(linkedin)

        lead["filing_url"] = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=D"
        results.append(lead)

        li_icon = "🔗" if linkedin["linkedin_company"] else "  "
        icon = "📞" if contact["phone"] else "  "
        log_print(f"  {icon}{li_icon} {i}/{len(all_leads)} | {lead['firm_name']} | {contact['contact_name']} | {contact['phone']} | {linkedin['linkedin_company']}")
        time.sleep(0.3)

    # Sort by fund size (largest first)
    def parse_fund_size(s):
        if not s:
            return 0
        s = s.replace("$", "").replace(",", "")
        if "B" in s:
            return float(s.replace("B", "")) * 1_000_000_000
        if "M" in s:
            return float(s.replace("M", "")) * 1_000_000
        try:
            return float(s)
        except:
            return 0

    results.sort(key=lambda x: parse_fund_size(x.get("fund_size", "")), reverse=True)

    # Step 3: Save
    fieldnames = [
        "checked", "firm_name", "name", "contact_name", "contact_title",
        "phone", "linkedin_company", "linkedin_person",
        "address", "fund_size", "year_inc", "fund_stage",
        "filer_status", "total_filings", "all_contacts",
        "filing_date", "cik", "filing_url"
    ]

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in results:
            r["checked"] = ""
            w.writerow(r)

    phones = sum(1 for r in results if r.get("phone"))
    log_print(f"\n{'=' * 60}")
    log_print(f"✅ WEEKLY RESULTS — {date_stamp}")
    log_print(f"  New leads: {len(results)}")
    log_print(f"  Phone numbers: {phones}/{len(results)}")
    log_print(f"  Saved to: {output_file}")
    log_print(f"{'=' * 60}")

    if results:
        log_print(f"\n🔝 TOP 10 LEADS (by fund size):")
        for i, r in enumerate(results[:10], 1):
            log_print(f"  {i}. {r['firm_name']} | {r.get('fund_size', '?')} | {r.get('contact_name', '')} | {r.get('phone', '')}")

    log.close()

    # macOS notification
    send_mac_notification(
        "VC Lead Finder",
        f"Found {len(results)} new VC leads this week! {phones} with phone numbers."
    )


if __name__ == "__main__":
    main()
