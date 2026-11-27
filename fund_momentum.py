#!/usr/bin/env python3
"""Use Fund Momentum's closings feed to find new funds and to mark funds we already have.

Fund Momentum (fundmomentum.vc) writes up VC fund closes, often within a day of
the news, including funds outside the US that never file with the SEC. Its
closings feed is free and needs no key. Each run:

1. Records every fund in the feed in FUND_MOMENTUM_FUNDS.csv. The leads pages
   mark any lead whose manager is in that file as flagged by Fund Momentum.
2. Adds early-stage Fund I or Fund II closes under MAX_SIZE_M million, reported
   in the last --days days, to MANUAL_LEADS.csv as "Fund Momentum", with a note
   saying Fund Momentum flagged them. daily_research.py researches them.
3. Fills their website and LinkedIn page from the fund's free profile, up to
   PROFILE_LIMIT funds a run (without a key Fund Momentum allows 10 profile calls
   a day); funds not reached are tried on the next run.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from datetime import datetime, timedelta, timezone

import requests

import lead_inbox
from pipeline import classify_fund_stage

FEED_URL = "https://fundmomentum.vc/_api/closings/feed"
PROFILE_URL = "https://fundmomentum.vc/_api/v1/funds/detail"
FUNDS_FILE = "FUND_MOMENTUM_FUNDS.csv"
FUNDS_FIELDS = ["slug", "fund", "stage", "close_type", "size", "reported_date", "link", "note"]
SOURCE = "Fund Momentum"
MAX_SIZE_M = 150
PROFILE_LIMIT = 8
LATE_STAGES = {"growth", "late_stage", "late-stage", "series_b", "series-b", "series_c", "series-c"}
# Rough US dollar value of one unit, only to apply the size limit
USD_PER = {"USD": 1, "EUR": 1.1, "GBP": 1.3, "CHF": 1.15, "CAD": 0.72, "AUD": 0.66, "SGD": 0.75,
           "NZD": 0.6, "SEK": 0.095, "NOK": 0.093, "DKK": 0.15, "ILS": 0.27, "INR": 0.012, "JPY": 0.0067}
STAGE_WORDS = {"pre_seed": "pre-seed", "series_a": "Series A", "series_b": "Series B", "series_c": "Series C",
               "late_stage": "late stage"}
# Words that say what a firm is, not which firm it is
COMMON_WORDS = {
    "capital", "ventures", "venture", "partners", "management", "fund", "funds", "group", "holdings", "global",
    "investments", "investment", "advisors", "vc", "the", "and", "lp", "llc", "one", "growth", "equity", "seed",
}


def size_usd_m(close: dict) -> float | None:
    """The close in millions of US dollars, or None when the amount or currency is unknown."""
    amount, rate = close.get("amount_m"), USD_PER.get(str(close.get("currency") or "").upper())
    if amount is None or rate is None:
        return None
    return float(amount) * rate


def is_small_new_fund(fund: dict, close: dict) -> bool:
    if classify_fund_stage(fund.get("name", "")) == "Later Fund":
        return False
    if str(fund.get("stage") or "").lower() in LATE_STAGES:
        return False
    size = size_usd_m(close)
    return size is None or size < MAX_SIZE_M


def flag_note(fund: dict, close: dict) -> str:
    """'Flagged by Fund Momentum: first close of C$30M, pre-seed, reported 2026-11-27.'"""
    kind = str(close.get("type") or "").replace("_", " ")
    kind = "close" if kind in {"", "closed unspecified"} else f"{kind} close"
    size = f" of {close['size_display']}" if close.get("size_display") else ""
    stage = STAGE_WORDS.get(str(fund.get("stage") or "").replace("-", "_"), str(fund.get("stage") or ""))
    reported = f"reported {close['reported_date']}" if close.get("reported_date") else ""
    return "Flagged by Fund Momentum: " + ", ".join(p for p in [f"{kind}{size}", stage, reported] if p) + "."


def feed_entries(feed: dict) -> list[tuple[dict, dict, str]]:
    """(fund, close, link to the Fund Momentum write-up) for each item in the feed."""
    entries = []
    for item in feed.get("items") or []:
        info = item.get("_fund_momentum") or {}
        fund, close = dict(info.get("fund") or {}), dict(info.get("close") or {})
        if not str(fund.get("name") or "").strip():
            continue
        close.setdefault("reported_date", str(item.get("date_published", ""))[:10])
        link = (info.get("sources") or {}).get("fund_momentum_post") or item.get("url", "")
        entries.append((fund, close, link))
    return entries


def rows_from_feed(feed: dict, since: str, known: set[str]) -> list[dict]:
    """Inbox rows for funds in the feed that pass the filters and are not in the inbox yet."""
    rows = []
    for fund, close, link in feed_entries(feed):
        name = fund["name"].strip()
        if lead_inbox.name_key(name) in known or str(close.get("reported_date") or "") < since:
            continue
        if not is_small_new_fund(fund, close):
            continue
        known.add(lead_inbox.name_key(name))
        rows.append({
            "added_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "fund": name, "people": "",
            "website": "", "linkedin": "", "slug": fund.get("slug", ""), "profile_checked": "",
            "link": link, "source": SOURCE, "note": flag_note(fund, close),
        })
    return rows


def record_funds(feed: dict, path: str = FUNDS_FILE) -> list[dict]:
    """Add every fund in the feed to the file of funds Fund Momentum has flagged; return all of them."""
    funds = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            funds = {row["slug"] or row["fund"]: row for row in csv.DictReader(f)}
    for fund, close, link in feed_entries(feed):
        funds[fund.get("slug") or fund["name"]] = {
            "slug": fund.get("slug", ""), "fund": fund["name"].strip(), "stage": fund.get("stage", ""),
            "close_type": close.get("type", ""), "size": close.get("size_display", ""),
            "reported_date": close.get("reported_date", ""), "link": link, "note": flag_note(fund, close),
        }
    rows = sorted(funds.values(), key=lambda row: row["reported_date"], reverse=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FUNDS_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return rows


def match_key(name: str) -> str:
    """The manager behind a fund name, or "" when the name has no distinctive word to match on.

    Like daily_research.manager_key, but numbers stay in the name, so "Stage 2 Capital" and
    "Stage Capital" are different firms.
    """
    from daily_research import FIRM_WORDS, brand_of

    tokens = re.findall(r"[a-z0-9]+", brand_of(name).lower())
    for index, token in enumerate(tokens[1:], 1):
        if token in FIRM_WORDS:
            tokens = tokens[: index + 1]
            break
    else:
        tokens = tokens[:2]
    distinctive = [word for word in tokens if word not in COMMON_WORDS and len(word) >= 3]
    return " ".join(tokens) if distinctive else ""


def flag_index(path: str = FUNDS_FILE) -> dict[str, dict]:
    """Funds Fund Momentum has flagged, by manager, newest close first."""
    if not os.path.exists(path):
        return {}
    index = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = match_key(row["fund"])
            if key and key not in index:
                index[key] = row
    return index


def flag_for(name: str, index: dict[str, dict]) -> dict | None:
    key = match_key(name)
    return index.get(key) if key else None


def add_profile(row: dict, profile: dict) -> None:
    """Fill the website, LinkedIn page and manager from a Fund Momentum fund profile."""
    row["website"] = row.get("website") or str(profile.get("website") or "")
    row["linkedin"] = row.get("linkedin") or str(profile.get("linkedin") or "")
    if isinstance(profile.get("fundManager"), str) and not row.get("people"):
        row["people"] = profile["fundManager"]


def fill_profiles(rows: list[dict], today: str) -> int:
    """Look up profiles for Fund Momentum rows not checked yet, newest first; return how many were filled."""
    waiting = [row for row in rows if row.get("source") == SOURCE and row.get("slug") and not row.get("profile_checked")]
    filled = 0
    for row in waiting[:PROFILE_LIMIT]:
        try:
            response = requests.get(PROFILE_URL, params={"slug": row["slug"]}, timeout=60)
        except requests.RequestException:
            continue
        if response.status_code in (402, 429):
            print("Fund Momentum's daily limit for profiles is reached; the rest are tried on the next run.")
            break
        if response.status_code == 200:
            add_profile(row, response.json())
            filled += 1
        row["profile_checked"] = today
    return filled


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=30, help="Only add closes reported in this many days")
    parser.add_argument("--inbox", default=lead_inbox.INBOX_FILE)
    args = parser.parse_args()

    try:
        response = requests.get(FEED_URL, timeout=60)
        response.raise_for_status()
        feed = response.json()
    except (requests.RequestException, ValueError) as error:
        print(f"Could not read the Fund Momentum feed: {error}")
        return 0  # the daily research still runs
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    flagged = record_funds(feed)
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")
    inbox = lead_inbox.load(args.inbox)
    new_rows = rows_from_feed(feed, since, {lead_inbox.name_key(row["fund"]) for row in inbox})
    inbox += new_rows
    filled = fill_profiles(list(reversed(inbox)), today)
    lead_inbox.save(inbox, args.inbox)
    print(f"{len(feed.get('items') or [])} closes in the feed ({len(flagged)} funds on file); "
          f"{len(new_rows)} new early first or second funds added; {filled} profiles filled.")
    for row in new_rows:
        print(f"  {row['fund'][:45]:45} | {row['website'] or 'site not known yet':35} | {row['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
