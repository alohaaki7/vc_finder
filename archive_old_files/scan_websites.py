#!/usr/bin/env python3
"""
VC Website Quality Scanner (FAST - parallel processing)
Finds VC firms with bad/template websites — your best redesign targets.

Scores each site 0-100: lower = worse website = better lead for you.
"""

import requests
import csv
import re
import socket
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def generate_domains(firm_name):
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", firm_name).strip()
    words = clean.lower().split()
    if not words:
        return []
    domains = []
    domains.append("".join(words) + ".com")
    if len(words) > 1:
        domains.append("-".join(words) + ".com")
    stop = {"ventures", "venture", "capital", "partners", "fund", "group",
            "investments", "management", "advisors", "holdings"}
    core = [w for w in words if w not in stop]
    if core and core != words:
        domains.append("".join(core) + ".com")
        domains.append("".join(core) + ".vc")
        domains.append("".join(core) + ".io")
        if len(core) > 1:
            domains.append("-".join(core) + ".com")
        for suffix in ["capital", "ventures", "vc"]:
            d = "".join(core) + suffix + ".com"
            if d not in domains:
                domains.append(d)
    domains.append("".join(words) + ".vc")
    return list(dict.fromkeys(domains))


def find_and_analyze(lead):
    """Find domain and analyze website for one lead. Returns enriched lead dict."""
    firm = lead.get("firm_name", "")
    if not firm:
        return None

    # Find working domain
    domain = None
    for d in generate_domains(firm):
        try:
            socket.setdefaulttimeout(3)
            socket.getaddrinfo(d, 80)
            r = requests.head(f"https://{d}", headers=HEADERS, timeout=4, allow_redirects=True)
            if r.status_code < 500:
                domain = urlparse(r.url).netloc or d
                break
        except:
            try:
                r = requests.head(f"http://{d}", headers=HEADERS, timeout=4, allow_redirects=True)
                if r.status_code < 500:
                    domain = urlparse(r.url).netloc or d
                    break
            except:
                pass

    if not domain:
        return None

    # Analyze the website
    result = {"platform": "unknown", "score": 50, "issues": [], "page_size_kb": 0}
    try:
        r = requests.get(f"https://{domain}", headers=HEADERS, timeout=8, allow_redirects=True)
        html = r.text.lower()
        result["page_size_kb"] = len(r.content) // 1024
        has_ssl = r.url.startswith("https")

        # Detect platform
        if "squarespace" in html or "static1.squarespace" in html:
            result["platform"] = "Squarespace"
        elif "wix.com" in html or "wixstatic" in html:
            result["platform"] = "Wix"
        elif "wp-content" in html or "wordpress" in html:
            result["platform"] = "WordPress"
        elif "webflow" in html or "website-files.com" in html:
            result["platform"] = "Webflow"
        elif "framer" in html or "framerusercontent" in html:
            result["platform"] = "Framer"
        elif "cargo.site" in html or "cargocollective" in html:
            result["platform"] = "Cargo"
        elif "hubspot" in html:
            result["platform"] = "HubSpot"
        elif "godaddy" in html:
            result["platform"] = "GoDaddy"
        elif "weebly" in html:
            result["platform"] = "Weebly"
        elif "carrd.co" in html:
            result["platform"] = "Carrd"
        elif "notion.site" in html or "super.so" in html:
            result["platform"] = "Notion"
        else:
            result["platform"] = "Custom"

        has_viewport = "viewport" in html
        has_favicon = "favicon" in html or 'rel="icon"' in html
        has_og = "og:" in html

        # Score
        scores = {
            "Carrd": 10, "Notion": 10, "GoDaddy": 15, "Weebly": 15,
            "Wix": 20, "Cargo": 25, "Squarespace": 30, "Ghost": 30,
            "WordPress": 35, "HubSpot": 40, "Framer": 50, "Webflow": 55,
            "Custom": 60, "unknown": 40,
        }
        score = scores.get(result["platform"], 40)
        if has_ssl: score += 5
        if has_viewport: score += 5
        if has_favicon: score += 5
        if has_og: score += 5
        if result["page_size_kb"] < 10:
            score -= 15
            result["issues"].append("Tiny page")
        if not has_ssl: result["issues"].append("No HTTPS")
        if not has_viewport: result["issues"].append("Not mobile friendly")
        if not has_favicon: result["issues"].append("No favicon")
        if result["platform"] in ["Wix", "GoDaddy", "Weebly", "Carrd", "Notion"]:
            result["issues"].append(f"Cheap template ({result['platform']})")
        elif result["platform"] in ["Squarespace", "WordPress"]:
            result["issues"].append(f"Template ({result['platform']})")
        result["score"] = max(0, min(100, score))
    except:
        result["score"] = 0
        result["issues"].append("Site error")

    lead["website"] = domain
    lead["platform"] = result["platform"]
    lead["site_score"] = result["score"]
    lead["issues"] = "; ".join(result["issues"])
    lead["page_size_kb"] = result["page_size_kb"]
    return lead


def main():
    input_file = os.path.join(SCRIPT_DIR, "ALL_VC_LEADS.csv")
    output_file = os.path.join(SCRIPT_DIR, "VC_REDESIGN_TARGETS.csv")

    print("=" * 60)
    print("VC WEBSITE QUALITY SCANNER (FAST)")
    print("=" * 60)

    with open(input_file, "r", encoding="utf-8") as f:
        leads = list(csv.DictReader(f))
    print(f"Scanning {len(leads)} VC leads with 10 parallel threads...\n")

    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(find_and_analyze, lead): lead for lead in leads}
        for future in as_completed(futures):
            done += 1
            result = future.result()
            if result:
                results.append(result)
                score = result.get("site_score", "?")
                icon = "🎯" if int(score) <= 40 else ("⚠️ " if int(score) <= 60 else "✓ ")
                print(f"  {icon} {result['firm_name']} | {result['platform']} | {score}/100 | {result['website']}")
            if done % 50 == 0:
                print(f"\n  --- Progress: {done}/{len(leads)} checked, {len(results)} sites found ---\n")

    # Sort worst first
    results.sort(key=lambda x: int(x.get("site_score", 100)))

    fieldnames = [
        "site_score", "platform", "issues", "website",
        "firm_name", "name", "contact_name", "contact_title",
        "phone", "fund_size", "filing_date", "cik", "filing_url",
        "page_size_kb"
    ]

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)

    bad = [r for r in results if int(r.get("site_score", 100)) <= 40]
    mid = [r for r in results if 40 < int(r.get("site_score", 100)) <= 60]

    print(f"\n{'='*60}")
    print(f"✅ RESULTS")
    print(f"  Sites found: {len(results)}/{len(leads)}")
    print(f"  🎯 Bad websites (≤40): {len(bad)} ← PITCH THESE")
    print(f"  ⚠️  Mediocre (41-60): {len(mid)}")
    print(f"  ✓ Saved to: VC_REDESIGN_TARGETS.csv")
    print(f"{'='*60}")

    if bad:
        print(f"\n🎯 YOUR TOP REDESIGN TARGETS:")
        for i, r in enumerate(bad[:25], 1):
            print(f"  {i}. {r['firm_name']} | {r['platform']} | Score: {r['site_score']}/100")
            print(f"     Site: {r['website']} | Phone: {r.get('phone', '')} | Contact: {r.get('contact_name', '')}")
            print(f"     Issues: {r['issues']}")


if __name__ == "__main__":
    main()
