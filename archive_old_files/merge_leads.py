#!/usr/bin/env python3
"""
Merge all VC lead CSVs into one master file and clean up duplicates.
"""
import csv
import os

DIR = os.path.dirname(os.path.abspath(__file__))

# Use the BEST version of each month (contacts > raw)
CONTACT_FILES = [
    "vc_leads_october_contacts.csv",
    "vc_leads_november_contacts.csv",
    "vc_leads_december_contacts.csv",
]

# Also include the weekly run
WEEKLY_FILES = [f for f in os.listdir(DIR) if f.startswith("weekly_leads_") and f.endswith(".csv")]

all_leads = {}  # keyed by CIK to dedupe

for filename in CONTACT_FILES + WEEKLY_FILES:
    path = os.path.join(DIR, filename)
    if not os.path.exists(path):
        print(f"  ⚠️  Skipping {filename} (not found)")
        continue

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            cik = row.get("cik", "")
            if cik and cik not in all_leads:
                row["source_file"] = filename
                all_leads[cik] = row
                count += 1
    print(f"  ✓ {filename}: {count} new leads added")

# Merge emails if the email file exists
email_file = os.path.join(DIR, "vc_leads_december_emails_emails.csv")
if os.path.exists(email_file):
    with open(email_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        email_count = 0
        for row in reader:
            cik = row.get("cik", "")
            email = row.get("primary_email", "")
            if cik and email and cik in all_leads:
                all_leads[cik]["primary_email"] = email
                all_leads[cik]["emails_found"] = row.get("emails_found", "")
                all_leads[cik]["domain"] = row.get("domain", "")
                email_count += 1
    print(f"  ✓ Merged {email_count} emails from email finder")

# Define clean fieldnames
fieldnames = [
    "firm_name", "name", "contact_name", "contact_title",
    "phone", "primary_email", "domain",
    "address", "fund_size", "year_inc",
    "fund_stage", "filer_status", "total_filings",
    "all_contacts", "filing_date", "cik", "filing_url",
    "source_file"
]

# Sort: first filers first, then by filing date (newest first)
leads_list = list(all_leads.values())
status_order = {"first_filer": 0, "new_filer": 1, "unknown": 2, "established": 3}
leads_list.sort(key=lambda x: (
    status_order.get(x.get("filer_status", ""), 4),
    -int(x.get("filing_date", "0").replace("-", "") or "0")
))

# Save master file
output = os.path.join(DIR, "ALL_VC_LEADS.csv")
with open(output, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for lead in leads_list:
        w.writerow(lead)

# Stats
first = sum(1 for l in leads_list if l.get("filer_status") == "first_filer")
phones = sum(1 for l in leads_list if l.get("phone"))
emails = sum(1 for l in leads_list if l.get("primary_email"))

print(f"\n{'='*60}")
print(f"✅ MASTER FILE: ALL_VC_LEADS.csv")
print(f"  Total unique leads: {len(leads_list)}")
print(f"  🔥 First-time filers: {first}")
print(f"  📞 With phone numbers: {phones}")
print(f"  📧 With emails: {emails}")
print(f"{'='*60}")

# List files that are now redundant
print(f"\n📁 You can now DELETE these intermediate files:")
redundant = [
    "vc_leads_october.csv",
    "vc_leads_november.csv", 
    "vc_leads_december.csv",
    "vc_leads_december_emails_emails.csv",
    "vc_leads_with_domains.csv",
    "vc_news_leads.csv",
    "vc_curated_leads.csv",
]
for f in redundant:
    if os.path.exists(os.path.join(DIR, f)):
        print(f"  rm \"{f}\"")

print(f"\n📁 KEEP these files:")
print(f"  ALL_VC_LEADS.csv          ← your master list")
print(f"  vc_best_leads.csv         ← your hand-picked 40 leads")
print(f"  vc_fresh_leads.csv        ← original search results")
print(f"  weekly_leads_*.csv        ← weekly auto-runs")
