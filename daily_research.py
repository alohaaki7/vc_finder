#!/usr/bin/env python3
"""Research the next batch of VC leads every day and record pitch points.

Leads come from three places, best first, one per manager, not already in
RESEARCH_LEDGER.csv:

- funds added by hand or found on X (MANUAL_LEADS.csv, see lead_inbox.py);
- SEC Form D: first funds that raised $1-20M, other new first funds, then
  managers raising Fund II;
- SEC adviser registrations (Form ADV, venture capital exemption): firms
  registered in the last 120 days, then firms registered since 2023 whose
  registration lists no website or only a social profile.

For each lead:

1. Google the brand (treg): find the fund's own site and what an LP sees when
   searching the name (own site rank, databases in the top results).
2. Keywords the site ranks for in Google (treg).
3. Read the site (no treg cost) and sort the lead into one of three groups:
   "No site", "Broken or empty" or "Has a site". The tool cannot judge design,
   so a site that exists is left for a person to look at.
4. Optional, with AI_OVERVIEW_CHECK=true: ask Google's AI Overview "What is
   <brand>?" (treg): does it know the fund, and which sources does it cite?

Spending is capped per call and per run (TREG_DAILY_BUDGET_USD). When the search
cannot run (no TREG_TOKEN, empty balance, rejected token), a lead is recorded
only if a record already says what its website is: an adviser registration, or a
site given with a hand-added lead. Form D leads wait, because "No site" is only
true after a search.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

import grade_websites
import lead_inbox

LEDGER_FILE = "RESEARCH_LEDGER.csv"
ADV_FILE = "ALAMAT_ADV_SIGNALS.csv"
ADV_REVIEWS_FILE = "ADV_REVIEWS.json"
# Advisers registered before this are no longer young firms
ADV_BACKLOG_SINCE = "2023-01-01"
FRESH_DAYS = 120
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
# Names that are a deal vehicle, a side entity or not a venture fund, so not worth a research slot
VEHICLE_HINT = re.compile(
    r"continuation|access|leasing|\bspv\b|series|co-?invest"
    # "Purdue Alumni Fund I - Roll Tack", "Mentors Fund II, LP - F2": one deal or one share class
    r"|\s-\s(?!.*\bfund\b)|-\s?(ii|2)\b|\bproject\s+\w+|\bdirect\b|\bqp\b|\binvestors\b"
    # Vintage-year vehicles, GP entities, insiders' funds, trusts and India GIFT City feeders
    r"|\b20\d{2}\b|\bgp\b|founders['’]\s+fund|\btrust\b|\bgift\b"
    r"|public\s+markets|financing|collectibles|infrastructure|real\s+estate|\bcredit\b|\blending\b",
    re.IGNORECASE,
)
FIRM_WORDS = {"ventures", "venture", "capital", "partners", "vc"}
# "First Rays Venture Partners Management Company" registers the firm "First Rays Venture Partners"
ADVISER_SUFFIX = re.compile(
    r"[,\s]+((investment\s+)?(management|advisory)(\s+company)?|managers?|advis[eo]rs?|general\s+partner|gp|company|"
    r"&\s*co|co|llc|l\.l\.c\.?|lp|l\.p\.?|ltd\.?|limited|inc\.?|corp\.?|m)\.?$",
    re.IGNORECASE,
)
KEEP_UPPER = {"LLC", "LP", "VC", "II", "III", "IV", "GP", "US", "USA", "AI", "SF", "NYC", "UK"}
# A site with fewer words than this across its pages says nothing about the fund
EMPTY_SITE_WORDS = 100
NO_SITE, EMPTY_SITE, HAS_SITE, UNCONFIRMED_SITE = "No site", "Broken or empty", "Has a site", "Check by hand"
LEDGER_FIELDS = [
    "researched_on", "priority", "fund", "brand", "raised", "first_sale", "filed", "location", "people",
    "website", "website_source", "site_status", "grade", "missing", "builder", "site_flags",
    "source", "searched", "phone", "emails", "linkedin", "note",
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
        self.rejected = False

    @property
    def available(self) -> bool:
        return (bool(self.token) and not self.out_of_money and not self.rejected
                and self.spent_micro < self.budget_micro)

    @property
    def why_unavailable(self) -> str:
        if not self.token:
            return "TREG_TOKEN is not set"
        if self.out_of_money:
            return "the treg balance is empty"
        if self.rejected:
            return "treg rejected the token"
        return "the treg budget for this run is used up" if not self.available else ""

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
        if response.status_code in (401, 403):
            self.rejected = True
            return None
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def google(self, query: str) -> list[dict] | None:
        """Google results, or None when the search itself did not run."""
        data = self.call("treg.google.serp.organic", json_body={"q": query, "limit": 10})
        if data is None:
            return None
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


def manager_key(fund_name: str) -> str:
    """The manager behind a fund name, so its vehicles count once.

    'Voltage Ventures Opportunity I LP' and 'Voltage Ventures Fund I LP' -> 'voltage ventures'.
    """
    tokens = [t for t in re.findall(r"[a-z0-9]+", brand_of(fund_name).lower()) if len(t) > 1]
    for index, token in enumerate(tokens[1:], 1):
        if token in FIRM_WORDS:
            return " ".join(tokens[: index + 1])
    return " ".join(tokens[:2])


def tidy_name(name: str) -> str:
    """'FIELD VENTURES M, LP' -> 'Field Ventures M, LP'; names with lower case are left alone."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not name.isupper():
        return name

    def fix(match):
        word = match.group(0)
        keep = word in KEEP_UPPER or any(c.isdigit() for c in word) or not re.search(r"[AEIOUY]", word)
        return word if keep else word.capitalize()
    return re.sub(r"[A-Z0-9]+(?:'[A-Z]+)?", fix, name)


def adviser_brand(name: str) -> str:
    """The firm an adviser entity belongs to: 'Mar Across Management LLC' -> 'Mar Across'."""
    brand = tidy_name(name)
    while True:
        shorter = ADVISER_SUFFIX.sub("", brand).strip(" ,&")
        if not shorter or shorter == brand:
            return brand
        brand = shorter


def adviser_leads(path: str = ADV_FILE, reviews_path: str = ADV_REVIEWS_FILE) -> list[dict]:
    """Venture-exempt advisers from the SEC feed, shaped like Form D leads."""
    if not os.path.exists(path):
        return []
    reviews = {}
    if os.path.exists(reviews_path):
        with open(reviews_path, encoding="utf-8") as f:
            reviews = json.load(f)
    from pipeline import classify_fund_stage

    leads = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = row.get("business_name") or row.get("legal_adviser_name") or ""
            if row.get("vc_signal_strength") != "explicit_venture_exemption" or not name:
                continue
            if row.get("registration_date", "") < ADV_BACKLOG_SINCE:
                continue
            if (reviews.get(row.get("crd_number", "")) or {}).get("established"):
                continue
            # "WXYZ II, LLC" or "BSF III GP" is a later fund's entity, so the firm is not new
            if classify_fund_stage(name) in {"Fund II", "Later Fund"}:
                continue
            site = row.get("reported_website", "").strip()
            filed = row.get("last_adv_filing_date") or row.get("registration_date", "")
            leads.append({
                "name": tidy_name(name), "brand": adviser_brand(name), "source": "SEC adviser registration",
                "sec_number": f"adv-{row.get('crd_number', '')}", "crd": row.get("crd_number", ""),
                "filing_date": row.get("registration_date", ""), "filing_url": row.get("iapd_url", ""),
                "city": row.get("city", ""), "state": row.get("state", ""), "country": row.get("country", ""),
                "phone": row.get("phone", ""), "sec_people": "",
                "record_site": site, "record_name": "SEC adviser record", "record_complete": True,
                "no_site_fact": f"Lists no website on its SEC adviser registration (last filed {filed})",
            })
    return leads


def inbox_leads(rows: list[dict]) -> list[dict]:
    """Hand-added and X-found funds from MANUAL_LEADS.csv, shaped like Form D leads."""
    leads = []
    for row in rows:
        fund = (row.get("fund") or "").strip()
        if not fund:
            continue
        people = [p.strip() for p in re.split(r"[;,]| and ", row.get("people") or "") if p.strip()]
        leads.append({
            "name": fund, "source": row.get("source") or "Added by you",
            "sec_number": "inbox-" + lead_inbox.name_key(fund).replace(" ", "-"),
            "filing_date": row.get("added_on", ""), "filing_url": row.get("link", ""),
            "sec_people": "; ".join(people), "note": row.get("note", ""),
            "record_site": row.get("website", ""), "record_name": "Given with the lead",
            "record_complete": bool(row.get("website")),
        })
    return leads


def brand_words(brand: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", brand.lower()) if w not in GENERIC_WORDS and len(w) >= 3]


def own_site(results: list[dict], words: list[str]) -> tuple[str, int]:
    for rank, result in enumerate(results, 1):
        host = host_of(result.get("url") or result.get("link"))
        if host and not is_database(host) and words and any(w in host.replace("-", "") for w in words):
            return f"https://{host}/", rank
    return "", 0


def site_confirms(url: str, brand: str, people: list[str]) -> bool:
    """The site names the fund (full brand) or a person from the SEC filing.

    A domain containing one brand word is not enough: "Olympus" also matches a
    medical-devices company and "Upside" a podcast.
    """
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": grade_websites.USER_AGENT})
        text = re.sub(r"\s+", " ", response.text).lower()
    except requests.RequestException:
        return False
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    full_brand = " ".join(re.findall(r"[a-z0-9]+", brand.lower()))
    if len(full_brand.split()) >= 2 and full_brand in re.sub(r"\s+", " ", text):
        return True
    if len(full_brand.split()) == 1 and len(full_brand) >= 5 and full_brand in text:
        return True
    surnames = {p.split()[-1].lower() for p in people if len(p.split()) >= 2 and len(p.split()[-1]) >= 4}
    if any(re.search(rf"\b{re.escape(name)}\b", text) for name in surnames):
        return True
    # The first two words of the name together, e.g. "Fidexus Capital" for
    # "Fidexus Capital Equity Growth" ("Syntropy Capital" does not confirm "Syntropy Ventures")
    words = full_brand.split()
    return len(words) >= 2 and " ".join(words[:2]) in text


def firm_linkedin(links: str, words: list[str]) -> str:
    """Keep the LinkedIn links that reach the firm: its own page first, then its people.

    A fund site also links to its portfolio companies' pages. A company page counts as the
    firm's when its address carries the brand, or when it is the only company page on the site.
    """
    pages = [link for link in str(links or "").split("; ") if link]
    companies = [link for link in pages if "/company/" in link.lower()]
    own = [link for link in companies
           if any(word in link.rstrip("/").rsplit("/", 1)[-1].lower().replace("-", "") for word in words)]
    if not own and len(companies) == 1:
        own = companies
    people = [link for link in pages if link not in companies]
    return "; ".join((own + people)[:5])


def parse_money(value: str) -> float:
    match = re.match(r"\$?\s*([\d.,]+)\s*([MBK]?)", str(value or "").strip(), re.IGNORECASE)
    if not match:
        return 0.0
    number = float(match.group(1).replace(",", ""))
    return number * {"M": 1e6, "B": 1e9, "K": 1e3, "": 1}[match.group(2).upper()]


def lead_key(lead: dict) -> str:
    return lead.get("sec_number") or lead.get("crd") or lead.get("name", "")


def build_queue(leads: list[dict], done: set[str], today: datetime, done_managers: set[str] | None = None,
                advisers: list[dict] | None = None, inbox: list[dict] | None = None) -> list[tuple[str, dict]]:
    """Leads not yet researched, best first, one per manager."""
    cutoff = (today - timedelta(days=FRESH_DAYS)).strftime("%Y-%m-%d")
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

    established = {manager_key(lead.get("name", "")) for lead in leads
                   if lead.get("manager_status_code") == "existing_manager"}
    # An adviser already on file as a Form D manager is either covered by that lead or not new
    on_form_d = established | {manager_key(lead.get("name", "")) for lead in new_firms}
    advisers = [lead for lead in advisers or []
                if lead_key(lead) not in done and manager_key(lead["brand"]) not in on_form_d]

    def no_real_site(lead):
        return not lead["record_site"] or grade_websites.is_social(lead["record_site"])
    new_advisers = sorted((lead for lead in advisers if lead["filing_date"] >= cutoff),
                          key=lambda lead: (no_real_site(lead), lead["filing_date"]), reverse=True)
    young_no_site = sorted((lead for lead in advisers if lead["filing_date"] < cutoff and no_real_site(lead)),
                           key=lambda lead: lead["filing_date"], reverse=True)
    # A fund Grok found on X by a manager with earlier Form D funds is not a new firm; hand-added funds always stay
    added = [lead for lead in inbox or [] if lead_key(lead) not in done
             and not (lead["source"] in {"Found on X", "Grok"} and manager_key(lead["name"]) in established)]

    queue = ([(lead["source"], lead) for lead in added]
             + [("Raised $1-20M", lead) for lead in raised] + [("New VC adviser", lead) for lead in new_advisers]
             + [("New first fund", lead) for lead in others] + [("Raising Fund II", lead) for lead in fund2]
             + [("Young firm, no site on record", lead) for lead in young_no_site])
    seen = set(done_managers or ())
    unique = []
    for priority, lead in queue:
        key = manager_key(lead.get("brand") or lead.get("name", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append((priority, lead))
    return unique


def site_status(website: str, grade: dict) -> str:
    """Sort a site by facts only: missing, broken or nearly empty, or there to be looked at."""
    kind = grade.get("grade", "")
    if not website or kind == "social_only":
        return NO_SITE
    if kind in {"broken", "placeholder"}:
        return EMPTY_SITE
    words = grade.get("words")
    if kind in {"thin_or_incomplete", "adequate"} and words not in ("", None) and int(words) < EMPTY_SITE_WORDS:
        return EMPTY_SITE
    return HAS_SITE


def research(priority: str, lead: dict, treg: Treg, ai_check: bool = False) -> dict | None:
    """Research one lead, or return None when nothing true can be said about its website.

    That is the case when the Google search could not run and no record (an adviser
    registration, a site given with the lead) says what the website is.
    """
    spent_before = treg.spent_micro
    fund = lead.get("name") or lead.get("firm_name", "")
    brand = lead.get("brand") or brand_of(fund)
    words = brand_words(brand)
    people = [p for p in str(lead.get("sec_people") or "").split("; ") if p]
    record_site = str(lead.get("record_site") or "").strip()
    record_name = lead.get("record_name", "")

    results = treg.google(f'"{brand}" venture')
    searched = results is not None
    if not searched and not lead.get("record_complete"):
        return None
    results = results or []
    website, rank = own_site(results, words)
    source = "Google" if website else ""
    if record_site and not grade_websites.is_social(record_site):
        # The site a firm reported itself beats a guess from search results
        website, source = grade_websites.normalize_url(record_site), record_name
        rank = next((position for position, result in enumerate(results, 1)
                     if host_of(result.get("url") or result.get("link")) == host_of(website)), 0)
    if not website and lead.get("adv_website"):
        website, source = lead["adv_website"], "SEC adviser record"
    if not website and people and treg.available:
        website, _ = own_site(treg.google(f'"{people[0]}" "{brand}"') or [], words)
        source = "Google (partner name)" if website else ""
    top_hosts = [host_of(r.get("url") or r.get("link")) for r in results[:5]]
    databases = sorted({h for h in top_hosts if h and is_database(h)})

    overview = treg.ai_overview(f"What is {brand}, the venture capital fund?") if ai_check and treg.available else {}
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
    unconfirmed = ""
    if website and source not in {"SEC adviser record", record_name} and not site_confirms(website, brand, people):
        unconfirmed, website, source = website, "", ""
    if not website and not unconfirmed and record_site:
        website, source = record_site, record_name  # the record lists only a social profile
    cites_own = bool(website) and host_of(website) in citations

    keywords = treg.ranked_keywords(host_of(website)) if website and treg.available else []

    grade = {}
    if website:
        grade = grade_websites.grade_site({
            "crd_number": lead.get("crd", ""), "business_name": brand, "registration_date": "",
            "country": lead.get("country", ""), "reported_website": website,
        })

    status = UNCONFIRMED_SITE if unconfirmed else site_status(website, grade)
    points = []
    if unconfirmed:
        points.append(f"Possible site {host_of(unconfirmed)} does not name the fund or its partners; check by hand.")
    elif not website:
        fact = lead.get("no_site_fact", "")
        if not searched:
            points.append(f"{fact}. Not checked on Google.")
        else:
            points.append(f'No website found for "{brand}".' + (f" {fact}." if fact else ""))
    elif grade.get("grade") == "social_only":
        where = f"on its {record_name}" if source == record_name and record_name else "found"
        points.append(f"The only site {where} is a {host_of(website)} profile."
                      + ("" if searched else " Not checked on Google."))
    elif grade.get("grade") in {"broken", "placeholder"}:
        points.append(f"Website is {grade['grade']}: {grade.get('evidence', '')}")
    elif status == EMPTY_SITE:
        points.append(f"Website is nearly empty: {grade['words']} words on {grade.get('pages_checked', 1)} page(s).")
    established = next((flag.split(": ", 1)[1] for flag in str(grade.get("flags") or "").split("; ")
                        if flag.startswith("established_signal: ")), "")
    if established:
        points.append(f'The site says "{established}": probably not a new firm.')
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

    if unconfirmed:
        offer = "Check by hand"
    elif status in {NO_SITE, EMPTY_SITE}:
        offer = "Website"
    elif databases or (overview and not cites_own) or not rank:
        offer = "Search & AI visibility + brand"
    else:
        offer = "Branding / positioning"

    country = str(lead.get("country", ""))
    city = str(lead.get("city", "")).title()
    country = "" if country in {"", "United States"} else country.split(",")[0].strip()
    place = [city, lead.get("state", ""), "" if country.lower() in city.lower() else country]
    return {
        "researched_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "priority": priority, "fund": fund, "brand": brand,
        "raised": lead.get("amount_sold", ""), "first_sale": lead.get("date_of_first_sale", ""),
        "filed": lead.get("filing_date", ""),
        "location": ", ".join(x for x in place if x),
        "people": ", ".join(people), "website": website, "website_source": source, "site_status": status,
        "grade": grade.get("grade", "no_site" if not website else ""), "missing": grade.get("missing", ""),
        "builder": grade.get("builder", ""), "site_flags": grade.get("flags", ""),
        "source": lead.get("source", "SEC Form D"), "searched": "yes" if searched else "no",
        "phone": lead.get("phone", ""), "emails": grade.get("emails", ""),
        "linkedin": firm_linkedin(grade.get("linkedin", ""), words),
        "note": lead.get("note", ""),
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
    parser.add_argument("--ai-overview", action="store_true",
                        default=os.environ.get("AI_OVERVIEW_CHECK", "").strip().lower() in {"1", "true", "yes"},
                        help="Also ask Google's AI Overview about each fund (more than doubles the cost per lead)")
    args = parser.parse_args()

    import server  # prepared leads, with the same verdict rules as the dashboard

    ledger = load_ledger(args.ledger)
    done = {row["lead_key"] for row in ledger}
    queue = build_queue(server.load_display_leads(), done, datetime.now(),
                        {manager_key(row.get("brand") or row["fund"]) for row in ledger},
                        adviser_leads(), inbox_leads(lead_inbox.load()))
    treg = Treg(os.environ.get("TREG_TOKEN", "").strip(), args.budget)
    print(f"{len(queue)} leads waiting; researching up to {args.limit} (treg budget ${args.budget:.2f}).")

    new_rows, waiting = [], 0
    for priority, lead in queue:
        if len(new_rows) >= args.limit:
            break
        row = research(priority, lead, treg, args.ai_overview)
        if row is None:
            waiting += 1  # needs a Google search; stays in the queue
            continue
        new_rows.append(row)
        print(f"  {row['fund'][:45]:45} | {row['site_status']:15} | ${row['treg_cost_usd']}")
        time.sleep(0.5)
    if waiting:
        print(f"{waiting} leads need a Google search and were left in the queue: {treg.why_unavailable}.")

    with open(args.ledger, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(new_rows + ledger)
    print(f"Researched {len(new_rows)} leads; treg spent ${treg.spent_micro / 1e6:.4f}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
