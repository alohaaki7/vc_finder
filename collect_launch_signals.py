#!/usr/bin/env python3
"""Collect new VC-manager launch signals from free Google News RSS feeds."""

import argparse
import csv
import re
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests

from build_monthly_prospects import OUTPUT_FIELDS, normalize_identity


NEWS_FIELDS = OUTPUT_FIELDS + ["news_title", "news_source", "news_date"]
NEWS_QUERIES = (
    '"debut fund" venture',
    '"debut VC fund"',
    '"debut seed fund"',
    '"first fund" venture capital',
    '"first-time fund" venture',
    '"first-time manager" venture fund',
    '"inaugural fund" venture',
    '"inaugural seed fund"',
    '"new venture firm"',
    '"new VC firm" fund',
    '"emerging manager" venture capital',
    '"first close" venture fund',
    '"first close" "debut fund"',
    '"Fund I" "venture capital" closes',
    '"Fund I" VC launches',
    '"launches with" "debut fund"',
    '"launches" "venture fund"',
)
LAUNCH_PATTERN = re.compile(
    r"\b(debut fund|first fund|inaugural fund|new venture firm|first close|"
    r"launch(?:es|ed|ing)?|unveil(?:s|ed)?|debut(?:s|ed)?|emerging manager)\b",
    re.IGNORECASE,
)
NEW_MANAGER_PATTERN = re.compile(
    r"\b(debut fund|first fund|inaugural fund|new (?:vc|venture capital) firm|"
    r"(?:vc|venture capital) firm launches|launch(?:es|ed|ing)? with .{0,80}\bfund\b)\b",
    re.IGNORECASE,
)
FUND_CONTEXT_PATTERN = re.compile(
    r"\b(ventures?|vc|seed|startup|climate tech|deeptech|medtech|tech)\b.*\bfund\b|"
    r"\bfund\b.*\b(ventures?|vc|seed|startup|climate tech|deeptech|medtech|tech)\b|"
    r"\b(new|emerging)\s+(vc|venture capital)\s+firm\b",
    re.IGNORECASE,
)
FOLLOW_ON_PATTERN = re.compile(
    r"\b(fund\s*(?:ii|iii|iv|v|vi|2|3|4|5|6)|(?:second|third|fourth|fifth).{0,30}\bfund|growth fund)\b",
    re.IGNORECASE,
)
NON_VC_PATTERN = re.compile(
    r"\b(private equity|real estate|property|credit fund|debt fund|hedge fund|mutual fund|etf|"
    r"sports fund|infrastructure fund|sovereign wealth)\b",
    re.IGNORECASE,
)
FIRM_BEFORE_ACTION = re.compile(
    r"^(?P<firm>.+?)\s+(?:raises?|closes?|launches?|unveils?|announces?|secures?|"
    r"seals?|reaches?|hits?|marks?|holds?)\b",
    re.IGNORECASE,
)
NEW_FIRM_PATTERN = re.compile(
    r"\b(?:new|emerging)\s+(?:vc|venture capital)\s+firm\s+(?P<firm>.+?)\s+"
    r"(?:raises?|closes?|launches?|unveils?|debuts?|announces?)\b",
    re.IGNORECASE,
)
DESCRIBED_FIRM_PATTERN = re.compile(
    r"\b(?:VC|venture capital|life sciences)\s+firm\s+(?P<firm>.+?)\s+"
    r"(?:raises?|closes?|launches?|unveils?|announces?|secures?|seals?|reaches?|hits?|marks?)\b",
    re.IGNORECASE,
)
BACKS_DEBUT_PATTERN = re.compile(
    r"\bbacks\s+(?P<firm>.+?)\s+as\s+(?:its\s+)?debut\s+fund\b",
    re.IGNORECASE,
)
FIRST_VC_DESCRIPTION_PATTERN = re.compile(
    r"^(?P<firm>[^,]+),\s*First\s+Venture\s+Capital\s+Firm\b",
    re.IGNORECASE,
)
LAUNCHED_FIRM_PATTERN = re.compile(
    r"\blaunch(?:es|ed|ing)?\s+(?P<firm>[A-Z][A-Za-z0-9&'’.\-]*(?:\s+[A-Z][A-Za-z0-9&'’.\-]*){0,4})\s+with\b",
    re.IGNORECASE,
)
GENERIC_FIRM_PATTERN = re.compile(
    r"\b(company|corporation|airlines?|university|foundation|financial group|sovereign|government|"
    r"former partner|ex-[a-z0-9]+|venture fund|vc fund|seed fund)\b",
    re.IGNORECASE,
)


def parse_news_date(value):
    try:
        parsed = parsedate_to_datetime(str(value or ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.date()
    except (TypeError, ValueError):
        return None


def clean_headline(value, source=""):
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    if source and title.casefold().endswith(f" - {source}".casefold()):
        title = title[: -(len(source) + 3)].strip()
    return re.sub(r"^(exclusive|scoop|breaking)\s*:\s*", "", title, flags=re.IGNORECASE)


def extract_firm_name(title):
    title = clean_headline(title)
    match = (
        FIRST_VC_DESCRIPTION_PATTERN.search(title)
        or BACKS_DEBUT_PATTERN.search(title)
        or LAUNCHED_FIRM_PATTERN.search(title)
        or DESCRIBED_FIRM_PATTERN.search(title)
        or NEW_FIRM_PATTERN.search(title)
        or FIRM_BEFORE_ACTION.search(title)
    )
    if not match:
        return ""
    firm = match.group("firm").strip(" -:,.\"'")
    firm = re.sub(r"^(new|emerging)\s+(vc|venture capital)\s+firm\s+", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r"^.*?\bVC\s+firm\s+", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r"^.*?\bventure capital\s+firm\s+", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r"^.*?-(?:focused|focussed)\s+VC\s+(?:fund\s+)?", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r"^(?:Africa|Asia|Europe|US|UK|LatAm)[ -](?:focused|focussed)\s+", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r"^.+?-led\s+", "", firm, flags=re.IGNORECASE)
    firm = re.sub(r"^(?:US|Israeli|European|Asian|African)\s+(?:venture|VC)\s+fund\s+", "", firm, flags=re.IGNORECASE)
    if "," in firm:
        firm = firm.split(",", 1)[0].strip()
    if ":" in firm:
        firm = firm.split(":", 1)[0].strip()
    firm = re.sub(r"\s+(?:makes|holds)\s+first$", "", firm, flags=re.IGNORECASE)
    brand_match = re.match(r"(?P<brand>.+?\b(?:Ventures?|Capital|Partners|VC))\b", firm, re.IGNORECASE)
    if brand_match and re.search(r"\b(eyes|successfully|amid|backs|announces)\b", firm[brand_match.end():], re.IGNORECASE):
        firm = brand_match.group("brand").strip()
    if not 3 <= len(firm) <= 80:
        return ""
    if re.search(r"\b(how|why|what|who|investors?|startups?|founders?)\b", firm, re.IGNORECASE):
        return ""
    if normalize_identity(firm) in {"first", "exclusive", "fund", "venture fund", "new vc firm", "new venture capital firm"}:
        return ""
    if GENERIC_FIRM_PATTERN.search(firm):
        return ""
    if re.search(r"\b(fund successfully|forged in|first venture capital firm dedicated)\b|[‘’\"]", firm, re.IGNORECASE):
        return ""
    if re.search(r"^(deals? in brief|roundup|weekly roundup)\b", firm, re.IGNORECASE):
        return ""
    if re.search(r"\bfund$", firm, re.IGNORECASE):
        return ""
    return firm


def fetch_rss(query, timeout=20):
    url = (
        "https://news.google.com/rss/search?q="
        f"{quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    response = requests.get(
        url,
        headers={"User-Agent": "AlamatStudioLeadScout/1.0"},
        timeout=timeout,
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    items = []
    for item in root.findall(".//item"):
        source_node = item.find("source")
        items.append({
            "title": item.findtext("title", default=""),
            "url": item.findtext("link", default=""),
            "pub_date": item.findtext("pubDate", default=""),
            "source": source_node.text if source_node is not None else "",
        })
    return items


def article_to_signal(article, today=None, days=180):
    today = today or date.today()
    published = parse_news_date(article.get("pub_date"))
    if not published or not 0 <= (today - published).days <= days:
        return None
    title = clean_headline(article.get("title"), article.get("source"))
    if (
        not LAUNCH_PATTERN.search(title)
        or not NEW_MANAGER_PATTERN.search(title)
        or not FUND_CONTEXT_PATTERN.search(title)
        or FOLLOW_ON_PATTERN.search(title)
        or NON_VC_PATTERN.search(title)
    ):
        return None
    firm = extract_firm_name(title)
    if not firm:
        return None
    source_url = str(article.get("url") or "").strip()
    return {
        "checked": "",
        "signal_type": "launch_news",
        "firm_name": firm,
        "name": firm,
        "vehicle_type": "public_signal",
        "lead_status": "discovered",
        "discovery_reason": "Public launch headline; firm and manager identity require verification.",
        "contact_verification_status": "not_identified",
        "is_new_since_last_run": "yes",
        "first_seen_at": published.isoformat(),
        "last_seen_at": published.isoformat(),
        "manager_status_code": "not_checked",
        "manager_status": "Not checked",
        "manager_novelty_score": "0",
        "manager_confidence": "Unknown",
        "manager_history_reason": "Public launch signal; SEC and manager history not yet checked.",
        "website_status": "unknown",
        "website_status_reason": "Not researched.",
        "fund_stage": "Emerging Fund",
        "filer_status": "public_signal",
        "platform": "Not audited",
        "linkedin_status": "not_checked",
        "service_opportunity": "Perform a lightweight identity, website, and LinkedIn check.",
        "qualification_reason": title,
        "evidence_sources": source_url,
        "filing_date": published.isoformat(),
        "filing_url": source_url,
        "country": "Unknown",
        "news_title": title,
        "news_source": str(article.get("source") or ""),
        "news_date": published.isoformat(),
    }


def collect_launch_signals(destination, queries=None, days=180, today=None, pause=0.2):
    today = today or date.today()
    signals = []
    seen = set()
    for query in queries or NEWS_QUERIES:
        for article in fetch_rss(query):
            signal = article_to_signal(article, today=today, days=days)
            if not signal:
                continue
            key = normalize_identity(signal["firm_name"])
            key = re.sub(r"\s+fund(?:\s+(?:i|1))?$", "", key).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            signals.append(signal)
        if pause:
            time.sleep(pause)

    signals.sort(key=lambda row: row.get("news_date", ""), reverse=True)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=NEWS_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(signals)
    print(f"Saved {len(signals)} public launch signals to {destination}")
    return signals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", help="Output CSV")
    parser.add_argument("--days", type=int, default=180, help="Maximum article age")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be at least 1")
    collect_launch_signals(args.destination, days=args.days)


if __name__ == "__main__":
    main()
