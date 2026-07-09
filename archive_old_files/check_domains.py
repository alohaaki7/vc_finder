#!/usr/bin/env python3
"""
Fast Domain Checker for VC Leads
Uses 1-second DNS timeout + quick HTTP check.
"""

import csv
import re
import time
import socket
import urllib.request
import ssl


def name_to_domains(firm_name):
    """Generate likely domain names from a firm name."""
    name = firm_name.strip()
    name = re.sub(r',?\s*fund\s*(I+|[0-9]+|one|two).*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r',?\s*(L\.?P\.?|LLC|Inc\.?)\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[,\.\'\"\-]', '', name).strip()
    
    words = name.lower().split()
    words = [w for w in words if w not in ('the', 'a', 'an', 'and', '&', '-', 'fund')]
    
    if not words:
        return []
    
    joined = ''.join(words)
    domains = [f"{joined}.com"]
    if len(words) >= 2:
        domains.append(f"{words[0]}{words[1]}.com")
    domains.append(f"{joined}.vc")
    if len(words[0]) >= 5:
        domains.append(f"{words[0]}.com")
    
    return list(dict.fromkeys(domains))


def check_domain(domain):
    """Quick DNS check with 1 second timeout."""
    try:
        socket.setdefaulttimeout(1)
        socket.gethostbyname(domain)
        return True
    except:
        return False


def check_site(domain):
    """Quick HTTP check - does it return real content?"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    for proto in ['https', 'http']:
        try:
            req = urllib.request.Request(f"{proto}://{domain}", headers={
                'User-Agent': 'Mozilla/5.0'
            })
            with urllib.request.urlopen(req, timeout=3, context=ctx) as r:
                content = r.read(3000).decode('utf-8', errors='ignore').lower()
                parked = ['domain for sale', 'buy this domain', 'parked',
                          'coming soon', 'under construction', 'is available']
                for p in parked:
                    if p in content:
                        return "basic_site"
                return "has_website" if len(content) > 300 else "basic_site"
        except:
            continue
    return "no_website"


def main():
    print("=" * 70)
    print("Fast Domain Checker")
    print("=" * 70)
    
    with open("vc_fresh_leads.csv", "r") as f:
        leads = list(csv.DictReader(f))
    
    print(f"Checking {len(leads)} leads...\n")
    
    results = []
    stats = {"no_website": 0, "basic_site": 0, "has_website": 0}
    
    for i, lead in enumerate(leads, 1):
        firm = lead.get("firm_name", lead.get("name", ""))
        domains = name_to_domains(firm)
        
        status = "no_website"
        found = ""
        
        for d in domains[:3]:
            if check_domain(d):
                status = check_site(d)
                found = d
                break
        
        lead["domain_status"] = status
        lead["domain_found"] = found
        results.append(lead)
        stats[status] += 1
        
        icon = {"no_website": "🔥", "basic_site": "⭐", "has_website": "⬜"}[status]
        print(f"{icon} {i}/{len(leads)} | {firm} | {status} | {found}")
    
    # Sort: no_website first
    order = {"no_website": 0, "basic_site": 1, "has_website": 2}
    results.sort(key=lambda x: order.get(x["domain_status"], 3))
    
    output = "vc_leads_with_domains.csv"
    fieldnames = ["checked", "firm_name", "name", "domain_status", "domain_found",
                  "fund_stage", "filer_status", "filing_date", "cik", "filing_url"]
    
    with open(output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)
    
    print(f"\n{'='*70}")
    print(f"🔥 No website: {stats['no_website']} (BEST - they need one!)")
    print(f"⭐ Basic/parked: {stats['basic_site']} (need redesign)")
    print(f"⬜ Has website: {stats['has_website']} (lower priority)")
    print(f"\n✓ Saved to: {output}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
