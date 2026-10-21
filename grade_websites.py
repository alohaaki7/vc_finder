#!/usr/bin/env python3
"""Grade VC managers' websites with the Alamat playbook's website taxonomy.

Each site is visited politely (its homepage plus at most three linked pages such
as /team or /portfolio, one page per second, robots.txt respected) and gets one
factual status:

- broken: the site does not load, has a certificate error, or returns an error page
- placeholder: parked or for-sale domain, "coming soon", or a hosting default page
- thin_or_incomplete: missing at least two of team, investment focus, portfolio,
  contact path (or almost no text)
- adequate: presents the firm, team, focus or activity, and a contact path
- social_only: the reported "website" is a LinkedIn/X/Facebook profile
- blocked: the site refused automated visits (bot protection); check by hand

The grade is evidence for a human decision, not a verdict on design quality:
an adequate but plain site can still be a branding or positioning lead.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import time
import urllib.parse
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

CONTACT = os.environ.get("VC_FINDER_SEC_USER_AGENT", "").strip()
USER_AGENT = f"AlamatStudioSiteCheck/1.0 ({CONTACT or 'public website review'})"
TIMEOUT = 15
MAX_BYTES = 3_000_000
MAX_EXTRA_PAGES = 3
PAGE_PAUSE_SECONDS = 1.0

SOCIAL_HOSTS = (
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "wellfound.com", "angel.co", "crunchbase.com",
)
PARKED_PATTERN = re.compile(
    r"domain (?:name )?(?:is|may be) for sale|buy this domain|this domain is parked|"
    r"parked (?:free|domain)|sedoparking|hugedomains|afternic|dan\.com/|parkingcrew|bodis\.com|"
    r"godaddy\.com/forsale|domain has expired|renew (?:this|your) domain",
    re.IGNORECASE,
)
PLACEHOLDER_PATTERN = re.compile(
    r"coming soon|under construction|launching soon|stay tuned|site is under|website is under|"
    r"future home of|welcome to nginx|apache2 (?:ubuntu|debian) default page|default web site page|"
    r"just another wordpress site|index of /|it works!",
    re.IGNORECASE,
)
BOT_WALL_PATTERN = re.compile(
    r"attention required|just a moment\.\.\.|cf-browser-verification|checking your browser|"
    r"access denied|request blocked|captcha",
    re.IGNORECASE,
)

# What a visitor (or an LP) needs to find on a VC site, per the playbook
ELEMENT_PATTERNS = {
    "team": re.compile(
        r"\b(our team|the team|team|people|partners|founders?|general partner|managing partner|"
        r"founding partner|who we are|about us)\b",
        re.IGNORECASE,
    ),
    "focus": re.compile(
        r"\b(thesis|we invest|invests? in|investment focus|our focus|we back|we partner with|"
        r"pre-seed|seed[- ]stage|early[- ]stage|series a|check size|sectors?|strategy|approach)\b",
        re.IGNORECASE,
    ),
    "portfolio": re.compile(
        r"\b(portfolio|our companies|investments|companies we|early believers|backed|we've invested|invested in)\b",
        re.IGNORECASE,
    ),
    "contact": re.compile(r"\b(contact|get in touch|reach us|email us|pitch us|submit)\b", re.IGNORECASE),
}
LINK_HINTS = re.compile(r"team|people|about|who-we-are|portfolio|companies|invest|thesis|approach|contact", re.IGNORECASE)

BUILDERS = [
    ("Carrd", re.compile(r"carrd\.co|made with carrd", re.IGNORECASE)),
    ("Notion", re.compile(r"notion\.site|notion-static|super\.so", re.IGNORECASE)),
    ("Wix", re.compile(r"wix\.com|_wixcssimports|wixstatic", re.IGNORECASE)),
    ("Squarespace", re.compile(r"squarespace\.com|static1\.squarespace", re.IGNORECASE)),
    ("Webflow", re.compile(r"webflow", re.IGNORECASE)),
    ("Framer", re.compile(r"framer\.com|framerusercontent", re.IGNORECASE)),
    ("GoDaddy builder", re.compile(r"img1\.wsimg\.com|godaddy website builder", re.IGNORECASE)),
    ("WordPress", re.compile(r"wp-content|wp-includes", re.IGNORECASE)),
]
ESTABLISHED_PATTERN = re.compile(
    r"\b(?:since|founded in|established in|est\.?)\s+((?:19|20)\d{2})\b"
    # "over 25 years" of the firm, not "over 10 years of experience"
    r"|\bover\s+(\d{1,3})\s+years\b(?!\s+(?:of\s+)?(?:combined\s+)?(?:experience|investing experience))",
    re.IGNORECASE,
)
GENERIC_NAME_WORDS = {
    "capital", "ventures", "venture", "partners", "management", "fund", "funds", "group", "holdings",
    "advisors", "advisers", "advisory", "investment", "investments", "global", "limited", "llc",
    "l.p", "lp", "ltd", "inc", "the", "and", "gp", "company", "managers", "s.a.r.l", "lux",
}
SOCIAL_LINK_HOSTS = SOCIAL_HOSTS + ("youtube.com", "medium.com", "substack.com", "github.com", "google.com")

OUTPUT_FIELDS = [
    "crd_number", "firm", "registration_date", "country", "website_reported", "final_url",
    "http_status", "grade", "missing", "builder", "flags", "evidence", "pages_checked", "checked_at",
]


def normalize_url(raw: str) -> str:
    url = (raw or "").strip().split()[0] if (raw or "").strip() else ""
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", "", ""))


def host_of(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def fetch(session: requests.Session, url: str):
    """Return (response, text) or (None, error message)."""
    try:
        response = session.get(url, timeout=TIMEOUT, allow_redirects=True, stream=True)
        content = response.raw.read(MAX_BYTES, decode_content=True) if response.raw else b""
        response.close()
        encoding = response.encoding or "utf-8"
        return response, content.decode(encoding, errors="replace")
    except requests.exceptions.SSLError:
        return None, "certificate error"
    except requests.exceptions.ConnectionError:
        return None, "does not resolve or refuses connections"
    except requests.exceptions.Timeout:
        return None, f"no response within {TIMEOUT}s"
    except requests.RequestException as error:
        return None, f"request failed ({type(error).__name__})"


def robots_for(session: requests.Session, base_url: str):
    parser = urllib.robotparser.RobotFileParser()
    try:
        response = session.get(urllib.parse.urljoin(base_url, "/robots.txt"), timeout=TIMEOUT)
        parser.parse(response.text.splitlines() if response.status_code == 200 else [])
    except requests.RequestException:
        parser.parse([])
    return parser


def visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def candidate_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    base_host = host_of(base_url)
    links = []
    for anchor in soup.find_all("a", href=True):
        href = urllib.parse.urljoin(base_url, anchor["href"].split("#")[0])
        label = f"{anchor.get_text(' ')} {href}"
        if host_of(href) == base_host and LINK_HINTS.search(label) and href.rstrip("/") != base_url.rstrip("/"):
            if href not in links:
                links.append(href)
    return links[:MAX_EXTRA_PAGES]


def grade_site(row: dict) -> dict:
    reported = row.get("reported_website", "")
    result = {
        "crd_number": row.get("crd_number", ""),
        "firm": row.get("business_name") or row.get("legal_adviser_name", ""),
        "registration_date": row.get("registration_date", ""),
        "country": row.get("country", ""),
        "website_reported": reported,
        "final_url": "", "http_status": "", "grade": "", "missing": "", "builder": "",
        "flags": "", "evidence": "", "pages_checked": 0,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    url = normalize_url(reported)
    if not url:
        result.update(grade="none_reported", evidence="No website on the SEC adviser registration.")
        return result
    if any(host_of(url) == social or host_of(url).endswith("." + social) for social in SOCIAL_HOSTS):
        result.update(grade="social_only", evidence=f"Reported website is a {host_of(url)} profile, not a firm site.")
        return result

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    response, text = fetch(session, url)
    if response is None and url.startswith("https://"):
        response, text = fetch(session, "http://" + url[len("https://"):])
    if response is None:
        result.update(grade="broken", evidence=f"Site {text}.")
        return result

    result["final_url"] = response.url
    result["http_status"] = response.status_code
    result["pages_checked"] = 1
    if response.status_code in (401, 403, 429, 503) and BOT_WALL_PATTERN.search(text or ""):
        result.update(grade="blocked", evidence=f"HTTP {response.status_code} bot protection; check by hand.")
        return result
    if response.status_code >= 400:
        result.update(grade="broken", evidence=f"Homepage returns HTTP {response.status_code}.")
        return result
    if host_of(response.url) != host_of(url) and PARKED_PATTERN.search(response.url):
        result.update(grade="placeholder", evidence=f"Redirects to a domain marketplace ({host_of(response.url)}).")
        return result

    soup = BeautifulSoup(text, "html.parser")
    title = (soup.title.get_text(" ").strip() if soup.title else "")[:80]
    links = candidate_links(soup, response.url)
    html_all = text
    home_text = visible_text(soup)
    texts = [home_text]

    if PARKED_PATTERN.search(home_text) or PARKED_PATTERN.search(text[:20000]):
        result.update(grade="placeholder", evidence="Parked or for-sale domain page.")
        return result
    words = len(home_text.split())
    if PLACEHOLDER_PATTERN.search(home_text) and words < 250:
        match = PLACEHOLDER_PATTERN.search(home_text).group(0)
        result.update(grade="placeholder", evidence=f'Homepage says "{match}" ({words} words).')
        return result

    robots = robots_for(session, response.url)
    for link in links:
        if not robots.can_fetch(USER_AGENT, link):
            continue
        time.sleep(PAGE_PAUSE_SECONDS)
        page, page_text = fetch(session, link)
        if page is None or page.status_code >= 400:
            continue
        result["pages_checked"] += 1
        html_all += page_text
        texts.append(visible_text(BeautifulSoup(page_text, "html.parser")))

    combined = " ".join(texts)
    link_labels = " ".join(f"{a.get_text(' ')} {a.get('href', '')}" for a in soup.find_all("a", href=True))
    present = {}
    for element, pattern in ELEMENT_PATTERNS.items():
        present[element] = bool(pattern.search(combined) or pattern.search(link_labels))
    if re.search(r"mailto:|tel:|/cdn-cgi/l/email-protection|type=[\"']email[\"']", html_all):
        present["contact"] = True
    # Several links out to different companies is a portfolio even without the word
    external_hosts = {
        host_of(urllib.parse.urljoin(response.url, a["href"])) for a in soup.find_all("a", href=True)
        if a["href"].startswith("http")
    }
    external_hosts = {
        h for h in external_hosts
        if h and h != host_of(response.url) and not any(h == s or h.endswith("." + s) for s in SOCIAL_LINK_HOSTS)
    }
    if len(external_hosts) >= 3:
        present["portfolio"] = True
    missing = [element for element, found in present.items() if not found]
    total_words = len(combined.split())

    flags = []
    if not response.url.startswith("https://"):
        flags.append("no_https")
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", html_all, re.IGNORECASE)]
    if years and max(years) < datetime.now().year - 1:
        flags.append(f"stale_copyright_{max(years)}")
    if result["pages_checked"] == 1 and not links:
        flags.append("single_page")
    for match in ESTABLISHED_PATTERN.finditer(combined):
        year, years_over = match.group(1), match.group(2)
        if (year and int(year) <= datetime.now().year - 5) or (years_over and int(years_over) >= 15):
            flags.append(f"established_signal: {match.group(0)}")
            break

    brand_words = [
        word for word in re.findall(r"[a-z0-9]+", result["firm"].lower())
        if len(word) >= 3 and word not in GENERIC_NAME_WORDS
    ]
    site_identity = f"{host_of(response.url)} {title} {combined[:5000]}".lower()
    if brand_words and not any(word in site_identity for word in brand_words):
        flags.append("site_may_belong_to_someone_else")

    builder = next((name for name, pattern in BUILDERS if pattern.search(html_all)), "")
    if len(missing) >= 2 or total_words < 120:
        grade = "thin_or_incomplete"
        reason = f"Missing {', '.join(missing)}" if missing else "Very little content"
        evidence = f"{reason}; {total_words} words across {result['pages_checked']} page(s)."
    else:
        grade = "adequate"
        evidence = f"Has {', '.join(e for e, f in present.items() if f)}; {total_words} words across {result['pages_checked']} page(s)."
    if title:
        evidence += f' Title: "{title}".'

    result.update(grade=grade, missing="; ".join(missing), builder=builder, flags="; ".join(flags), evidence=evidence)
    return result


def select_advisers(path: str, since: str, limit: int) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = [
            row for row in csv.DictReader(f)
            if row.get("vc_signal_strength") == "explicit_venture_exemption"
            and row.get("registration_date", "") >= since
        ]
    rows.sort(key=lambda row: row.get("registration_date", ""), reverse=True)
    return rows[:limit] if limit else rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adv", default="ALAMAT_ADV_SIGNALS.csv")
    parser.add_argument("--output", default="ADV_WEBSITE_GRADES.csv")
    parser.add_argument("--since", default="2025-01-01", help="Earliest SEC adviser registration date")
    parser.add_argument("--limit", type=int, default=0, help="Newest N advisers only (0 = all)")
    parser.add_argument("--workers", type=int, default=6, help="Sites graded in parallel (one page at a time per site)")
    args = parser.parse_args()

    advisers = select_advisers(args.adv, args.since, args.limit)
    print(f"Grading {len(advisers)} venture-exempt advisers registered since {args.since}...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(grade_site, advisers))

    with open(args.output, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(results)

    counts = {}
    for result in results:
        counts[result["grade"]] = counts.get(result["grade"], 0) + 1
    print(f"Saved {len(results)} grades to {args.output}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
