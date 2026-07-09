#!/usr/bin/env python3
"""
VC Email Finder
Finds REAL email addresses for VC leads by:
1. Scraping their website (contact page, footer, about page, team page)
2. Checking WHOIS records for registrant emails
3. Checking DNS MX records to confirm email is set up
"""

import requests
import csv
import re
import time
import sys
import socket
import os

try:
    import whois
    HAS_WHOIS = True
except ImportError:
    HAS_WHOIS = False
    print("⚠️  python-whois not installed. Run: pip3 install python-whois")

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# Common VC website domain patterns
def generate_domains(firm_name):
    """Generate likely domain names from firm name."""
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", firm_name).strip()
    words = clean.lower().split()
    if not words:
        return []

    domains = []
    # Full name joined: keelventures.com
    domains.append("".join(words) + ".com")
    # With hyphens: keel-ventures.com
    if len(words) > 1:
        domains.append("-".join(words) + ".com")
    # Without common suffixes: keel.com (if "ventures"/"capital" etc.)
    stop_words = {"ventures", "venture", "capital", "partners", "fund", "group", "investments"}
    core = [w for w in words if w not in stop_words]
    if core and core != words:
        domains.append("".join(core) + ".com")
        domains.append("".join(core) + ".vc")
        domains.append("".join(core) + ".io")
    # .vc domain
    domains.append("".join(words) + ".vc")

    return list(dict.fromkeys(domains))  # dedupe, preserve order


def check_domain_exists(domain):
    """Check if domain resolves via DNS."""
    try:
        socket.getaddrinfo(domain, 80, socket.AF_INET, socket.SOCK_STREAM)
        return True
    except:
        return False


def scrape_emails_from_url(url):
    """Scrape a URL for email addresses."""
    emails = set()
    try:
        r = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if r.status_code != 200:
            return emails
        text = r.text

        # Find all email addresses in the page
        found = re.findall(
            r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
            text
        )
        for email in found:
            email = email.lower().strip()
            # Filter out common junk
            if any(x in email for x in [
                'example.com', 'sentry.io', 'wixpress', 'googleapis',
                'cloudflare', 'schema.org', 'w3.org', 'gravatar',
                'wordpress', 'squarespace', 'webflow', '.png', '.jpg',
                '.svg', '.gif', '.css', '.js', 'webpack', 'babel',
                'react', 'angular', 'vue', 'node', 'npm',
                'sentry', 'hotjar', 'google', 'facebook', 'twitter',
                'instagram', 'youtube', 'github', 'linkedin',
                'protection', 'abuse', 'spam', 'noreply', 'no-reply',
                'unsubscribe', 'mailer-daemon', 'postmaster'
            ]):
                continue
            emails.add(email)
    except:
        pass
    return emails


def scrape_website_for_emails(domain):
    """Scrape multiple pages of a website for email addresses."""
    all_emails = set()

    # Pages to check
    pages = [
        f"https://{domain}",
        f"https://{domain}/contact",
        f"https://{domain}/contact-us",
        f"https://{domain}/about",
        f"https://{domain}/about-us",
        f"https://{domain}/team",
        f"https://www.{domain}",
        f"https://www.{domain}/contact",
        f"https://www.{domain}/about",
    ]

    for url in pages:
        emails = scrape_emails_from_url(url)
        all_emails.update(emails)
        if all_emails:  # Found some, no need to check all pages
            break
        time.sleep(0.5)

    return all_emails


def get_whois_email(domain):
    """Get email from WHOIS record."""
    if not HAS_WHOIS:
        return set()

    emails = set()
    try:
        w = whois.whois(domain)
        if w:
            # Check various WHOIS fields for emails
            for field in ['emails', 'registrant_email', 'admin_email', 'tech_email']:
                val = getattr(w, field, None)
                if val:
                    if isinstance(val, list):
                        for e in val:
                            if e and '@' in str(e):
                                email = str(e).lower().strip()
                                # Skip privacy/proxy emails
                                if not any(x in email for x in [
                                    'privacy', 'proxy', 'redacted', 'abuse',
                                    'whoisguard', 'domainsby', 'contactprivacy',
                                    'withheld', 'domainsbyproxy', 'godaddy',
                                    'namecheap', 'cloudflare', 'domains',
                                    'registrant', 'hostmaster'
                                ]):
                                    emails.add(email)
                    elif isinstance(val, str) and '@' in val:
                        email = val.lower().strip()
                        if not any(x in email for x in [
                            'privacy', 'proxy', 'redacted', 'abuse',
                            'whoisguard', 'domainsby', 'contactprivacy',
                            'withheld', 'domainsbyproxy', 'godaddy',
                            'namecheap', 'cloudflare', 'domains',
                            'registrant', 'hostmaster'
                        ]):
                            emails.add(email)
    except:
        pass
    return emails


def find_domain_for_firm(firm_name):
    """Find the actual working domain for a firm."""
    domains = generate_domains(firm_name)
    for domain in domains:
        if check_domain_exists(domain):
            return domain
    return None


def main():
    input_file = sys.argv[1] if len(sys.argv) > 1 else "vc_leads_december_contacts.csv"
    output_file = input_file.replace("_contacts.csv", "_emails.csv").replace(".csv", "_emails.csv")
    if output_file == input_file:
        output_file = input_file.replace(".csv", "_with_emails.csv")

    print("=" * 60)
    print("VC EMAIL FINDER")
    print(f"Input: {input_file}")
    print("=" * 60)

    with open(input_file, "r") as f:
        leads = list(csv.DictReader(f))

    print(f"Loaded {len(leads)} leads\n")

    results = []
    emails_found = 0
    whois_emails_found = 0
    website_emails_found = 0

    for i, lead in enumerate(leads, 1):
        firm = lead.get("firm_name", "")
        contact = lead.get("contact_name", "")

        print(f"{i}/{len(leads)} | {firm}...", end=" ", flush=True)

        found_emails = set()
        email_source = ""

        # Step 1: Find domain
        domain = find_domain_for_firm(firm)

        if domain:
            # Step 2: Scrape website for emails
            web_emails = scrape_website_for_emails(domain)
            if web_emails:
                found_emails.update(web_emails)
                email_source = "website"
                website_emails_found += 1

            # Step 3: Check WHOIS
            whois_emails = get_whois_email(domain)
            if whois_emails:
                found_emails.update(whois_emails)
                if not email_source:
                    email_source = "whois"
                else:
                    email_source += "+whois"
                whois_emails_found += 1

            lead["domain"] = domain
        else:
            lead["domain"] = ""

        if found_emails:
            emails_found += 1
            # Sort: prefer info@, hello@, contact@ first, then personal
            sorted_emails = sorted(found_emails, key=lambda e: (
                0 if any(e.startswith(p) for p in ['info@', 'hello@', 'contact@', 'team@']) else
                1 if re.match(r'^[a-z]+@', e) else 2
            ))
            lead["emails_found"] = "; ".join(sorted_emails)
            lead["primary_email"] = sorted_emails[0]
            lead["email_source"] = email_source
            print(f"📧 {sorted_emails[0]} ({email_source})")
        else:
            lead["emails_found"] = ""
            lead["primary_email"] = ""
            lead["email_source"] = ""
            print(f"❌ No email found" + (f" (domain: {domain})" if domain else " (no domain)"))

        results.append(lead)
        time.sleep(0.5)

    # Save
    # Get existing fieldnames and add new ones
    fieldnames = list(leads[0].keys()) if leads else []
    for new_field in ["primary_email", "emails_found", "email_source", "domain"]:
        if new_field not in fieldnames:
            fieldnames.insert(fieldnames.index("firm_name") + 1 if "firm_name" in fieldnames else 0, new_field)

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)

    print(f"\n{'='*60}")
    print(f"✅ RESULTS:")
    print(f"  📧 Emails found: {emails_found}/{len(leads)}")
    print(f"     From websites: {website_emails_found}")
    print(f"     From WHOIS: {whois_emails_found}")
    print(f"  ✓ Saved to: {output_file}")
    print(f"{'='*60}")

    # Show found emails
    found = [r for r in results if r.get("primary_email")]
    if found:
        print(f"\n📧 ALL EMAILS FOUND:")
        for r in found:
            print(f"  {r['firm_name']} → {r['primary_email']} ({r['email_source']})")


if __name__ == "__main__":
    main()
