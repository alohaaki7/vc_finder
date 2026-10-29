#!/usr/bin/env python3
"""Research the next batch of VC leads every day and record pitch points.

For each lead, in priority order (first funds that raised $1-20M, other new first
funds, then managers raising Fund II), not already in RESEARCH_LEDGER.csv:

1. Google the brand (treg): find the fund's own site and what an LP sees when
   searching the name (own site rank, databases in the top results).
2. Ask Google's AI Overview "What is <brand>?" (treg): does it know the fund,
   and does it cite the fund's own site or third-party databases?
3. Keywords the site ranks for in Google (treg).
4. Grade the site with grade_websites.py (no treg cost).

Spending is capped per call and per run (TREG_DAILY_BUDGET_USD). Without a
TREG_TOKEN the treg steps are skipped and only known websites are graded.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

import grade_websites

LEDGER_FILE = "RESEARCH_LEDGER.csv"
TREG_URL = "https://treg.to/call/"
MAX_COST_PER_CALL = "0.005"
DATABASE_HOSTS = (
    "pitchbook.com", "crunchbase.com", "linkedin.com", "sec.gov", "globaldealflow.com", "aum13f.com",
    "nomas.fyi", "privateequityinternational.com", "pehub.com", "cbinsights.com", "tracxn.com", "nfx.com",
    "bloomberg.com", "zoominfo.com", "dnb.com", "opencorporates.com", "formds.com", "secdatabase.com",
    "fintel.io", "wellfound.com", "bizapedia.com", "privatefunddata.com", "x.com", "twitter.com",
    "facebook.com", "instagram.com", "youtube.com", "medium.com", "vcbacked.co", "startupfundraising.com",
    "theventurecodex.com", "yahoo.com", "wsj.com", "signal.nfx.com", "dealroom.co", "preqin.com",
)
GENERIC_WORDS = {
    "capital", "ventures", "venture", "partners", "management", "fund", "funds", "group", "holdings", "lp",
    "llc", "the", "and", "vc", "global", "investments", "investment", "advisors", "one", "i", "ii", "1", "2",
}
VEHICLE_HINT = re.compile(r"continuation|access|leasing|\bspv\b|series|co-?invest", re.IGNORECASE)
LEDGER_FIELDS = [
    "researched_on", "priority", "fund", "brand", "raised", "first_sale", "filed", "location", "people",
    "website", "website_source", "grade", "missing", "builder", "site_flags",
    "google_own_site_rank", "google_top_results", "google_databases_top5",
    "ai_overview_knows_fund", "ai_overview_cites_own_site", "ai_overview_sources", "ai_overview_summary",
    "ranked_keywords", "offer", "pitch_points", "sec_filing", "lead_key", "treg_cost_usd",
]


class Treg:
    """treg calls with a per-call cap and a running budget."""

    def __init__(self, token: str, budget_usd: float):
        self.token = token
        self.budget_micro = int(budget_usd * 1_000_000)
        self.spent_micro = 0
        self.out_of_money = False

    @property
    def available(self) -> bool:
        return bool(self.token) and not self.out_of_money and self.spent_micro < self.budget_micro

    def call(self, endpoint: str, *, json_body=None, params=None, method="POST"):
        if not self.available:
            return None
        headers = {"X-Treg-Token": self.token, "X-Treg-Route-Max-Cost": MAX_COST_PER_CALL}
        try:
            response = requests.request(
                method, TREG_URL + endpoint, headers=headers, json=json_body, params=params, timeout=120,
            )
        except requests.RequestException:
            return None
        self.spent_micro += int(response.headers.get("x-treg-cost-micro") or 0)
        if response.status_code == 402:
            self.out_of_money = True
            return None
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def google(self, query: str) -> list[dict]:
        data = self.call("treg.google.serp.organic", json_body={"q": query, "limit": 10}) or {}
        return (data.get("output") or {}).get("results") or []

    def ai_overview(self, prompt: str) -> dict:
        data = self.call("anyapi.google.serp.ai_overview", json_body={"prompt": prompt}) or {}
        return ((data.get("output") or {}).get("data")) or {}

    def ranked_keywords(self, domain: str) -> list[dict]:
        data = self.call("serpstat.google.domain.ranked_keywords", json_body={
            "id": "1", "method": "SerpstatDomainProcedure.getDomainKeywords",
            "params": {"domain": domain, "se": "g_us", "size": 5},
        }) or {}
        result = ((data.get("output") or data).get("result")) or {}
        return result.get("data") or []


def host_of(url: str) -> str:
    return urllib.parse.urlparse(url or "").netloc.lower().removeprefix("www.")


def is_database(host: str) -> bool:
    return any(host == db or host.endswith("." + db) for db in DATABASE_HOSTS)


def brand_of(fund_name: str) -> str:
    """'Syntropy Ventures Fund I, LP' -> 'Syntropy Ventures'."""
    name = re.sub(r"[,\s]+(l\.?\s?p\.?|llc|l\.l\.c\.?|ltd\.?|inc\.?)\s*$", "", fund_name.strip(), flags=re.IGNORECASE)
    name = re.sub(r"\s+(fund\s+)?(i|1|one)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+fund$", "", name, flags=re.IGNORECASE)
    return name.strip(" ,-") or fund_name


def brand_words(brand: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", brand.lower()) if w not in GENERIC_WORDS and len(w) >= 3]


def own_site(results: list[dict], words: list[str]) -> tuple[str, int]:
    for rank, result in enumerate(results, 1):
        host = host_of(result.get("url") or result.get("link"))
        if host and not is_database(host) and words and any(w in host.replace("-", "") for w in words):
            return f"https://{host}/", rank
    return "", 0


def parse_money(value: str) -> float:
    match = re.match(r"\$?\s*([\d.,]+)\s*([MBK]?)", str(value or "").strip(), re.IGNORECASE)
    if not match:
        return 0.0
    number = float(match.group(1).replace(",", ""))
    return number * {"M": 1e6, "B": 1e9, "K": 1e3, "": 1}[match.group(2).upper()]


def lead_key(lead: dict) -> str:
    return lead.get("sec_number") or lead.get("crd") or lead.get("name", "")


def build_queue(leads: list[dict], done: set[str], today: datetime) -> list[tuple[str, dict]]:
    """Leads not yet researched, best first."""
    cutoff = (today - timedelta(days=120)).strftime("%Y-%m-%d")
    fresh = [
        lead for lead in leads
        if lead.get("filing_date", "") >= cutoff and lead_key(lead) not in done
        and not VEHICLE_HINT.search(lead.get("name", ""))
    ]
    new_firms = [lead for lead in fresh if lead.get("manager_status_code") == "likely_new"]
    raised = sorted(
        (lead for lead in new_firms if 1e6 <= parse_money(lead.get("amount_sold")) < 20e6),
        key=lambda lead: parse_money(lead.get("amount_sold")), reverse=True,
    )
    raised_keys = {lead_key(lead) for lead in raised}
    others = sorted((lead for lead in new_firms if lead_key(lead) not in raised_keys),
                    key=lambda lead: lead.get("filing_date", ""), reverse=True)
    fund2 = sorted((lead for lead in fresh if lead.get("fund2_raise")),
                   key=lambda lead: (parse_money(lead.get("amount_sold")) > 0, lead.get("filing_date", "")), reverse=True)
    return ([("Raised $1-20M", lead) for lead in raised] + [("New first fund", lead) for lead in others]
            + [("Raising Fund II", lead) for lead in fund2])


def research(priority: str, lead: dict, treg: Treg) -> dict:
    spent_before = treg.spent_micro
    fund = lead.get("name") or lead.get("firm_name", "")
    brand = brand_of(fund)
    words = brand_words(brand)
    people = [p for p in str(lead.get("sec_people") or "").split("; ") if p]

    results = treg.google(f'"{brand}" venture') if treg.available else []
    website, rank = own_site(results, words)
    source = "Google" if website else ""
    if not website and lead.get("adv_website"):
        website, source = lead["adv_website"], "SEC adviser record"
    if not website and people and treg.available:
        website, _ = own_site(treg.google(f'"{people[0]}" "{brand}"'), words)
        source = "Google (partner name)" if website else ""
    top_hosts = [host_of(r.get("url") or r.get("link")) for r in results[:5]]
    databases = sorted({h for h in top_hosts if h and is_database(h)})

    overview = treg.ai_overview(f"What is {brand}, the venture capital fund?") if treg.available else {}
    answer = str(overview.get("answer") or "")
    citations = [host_of(c.get("url")) for c in overview.get("citations") or [] if c.get("url")]
    knows = bool(answer) and bool(words) and any(w in answer.lower() for w in words) \
        and not re.search(r"(couldn't|could not|unable to) find|no (specific )?information", answer, re.IGNORECASE)
    if not website:
        # The AI sometimes cites the fund's own site when the Google results miss it
        cited_own = next((h for h in citations if not is_database(h) and words
                          and any(w in h.replace("-", "") for w in words)), "")
        if cited_own:
            website, source = f"https://{cited_own}/", "Google AI Overview source"
    cites_own = bool(website) and host_of(website) in citations

    keywords = treg.ranked_keywords(host_of(website)) if website and treg.available else []

    grade = {}
    if website:
        grade = grade_websites.grade_site({
            "crd_number": lead.get("crd", ""), "business_name": brand, "registration_date": "",
            "country": lead.get("country", ""), "reported_website": website,
        })

    points = []
    if not website:
        points.append(f'No website found for "{brand}".')
    elif grade.get("grade") in {"broken", "placeholder", "thin_or_incomplete", "social_only"}:
        points.append(f"Website is {grade['grade'].replace('_', ' ')}: {grade.get('evidence', '')}")
    if results and not rank:
        points.append(f'Googling "{brand}" does not show the fund\'s own site in the top 10.')
    elif rank > 3:
        points.append(f'Googling "{brand}" shows the fund\'s own site only at position {rank}.')
    if databases:
        points.append(f"An LP searching the name sees {', '.join(databases)} in the top 5.")
    if overview and not knows:
        points.append("Google's AI Overview does not know the fund.")
    elif knows and citations and not cites_own:
        points.append(f"Google's AI describes the fund from {', '.join(sorted(set(citations))[:3])}, not its own site.")

    if not website or grade.get("grade") in {"broken", "placeholder", "thin_or_incomplete", "social_only"}:
        offer = "Website"
    elif databases or (overview and not cites_own) or not rank:
        offer = "Search & AI visibility + brand"
    else:
        offer = "Branding / positioning"

    return {
        "researched_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "priority": priority, "fund": fund, "brand": brand,
        "raised": lead.get("amount_sold", ""), "first_sale": lead.get("date_of_first_sale", ""),
        "filed": lead.get("filing_date", ""),
        "location": ", ".join(x for x in [str(lead.get("city", "")).title(), lead.get("state", "")] if x),
        "people": ", ".join(people), "website": website, "website_source": source,
        "grade": grade.get("grade", "no_site" if not website else ""), "missing": grade.get("missing", ""),
        "builder": grade.get("builder", ""), "site_flags": grade.get("flags", ""),
        "google_own_site_rank": rank or "", "google_top_results": " | ".join(h for h in top_hosts if h),
        "google_databases_top5": ", ".join(databases),
        "ai_overview_knows_fund": ("yes" if knows else "no") if overview else "",
        "ai_overview_cites_own_site": ("yes" if cites_own else "no") if overview else "",
        "ai_overview_sources": ", ".join(sorted(set(citations))[:5]),
        "ai_overview_summary": re.sub(r"\s+", " ", answer)[:400],
        "ranked_keywords": "; ".join(str(k.get("keyword", "")) for k in keywords[:5]),
        "offer": offer, "pitch_points": " ".join(points), "sec_filing": lead.get("filing_url", ""),
        "lead_key": lead_key(lead), "treg_cost_usd": f"{(treg.spent_micro - spent_before) / 1e6:.5f}",
    }


def load_ledger(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=20, help="Leads to research this run")
    parser.add_argument("--ledger", default=LEDGER_FILE)
    parser.add_argument("--budget", type=float, default=float(os.environ.get("TREG_DAILY_BUDGET_USD") or 0.10),
                        help="Maximum treg spend for this run, in USD")
    args = parser.parse_args()

    import server  # prepared leads, with the same verdict rules as the dashboard

    ledger = load_ledger(args.ledger)
    done = {row["lead_key"] for row in ledger}
    queue = build_queue(server.load_display_leads(), done, datetime.now())
    treg = Treg(os.environ.get("TREG_TOKEN", "").strip(), args.budget)
    if not treg.token:
        print("TREG_TOKEN is not set: Google and AI checks are skipped.")
    print(f"{len(queue)} leads waiting; researching up to {args.limit} (treg budget ${args.budget:.2f}).")

    new_rows = []
    for priority, lead in queue[: args.limit]:
        if treg.token and not treg.available:
            print("Stopping: treg budget reached or balance empty.")
            break
        row = research(priority, lead, treg)
        new_rows.append(row)
        print(f"  {row['fund'][:45]:45} | {row['offer']:28} | ${row['treg_cost_usd']}")
        time.sleep(0.5)

    with open(args.ledger, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(new_rows + ledger)
    print(f"Researched {len(new_rows)} leads; treg spent ${treg.spent_micro / 1e6:.4f}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
