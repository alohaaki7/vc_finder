#!/usr/bin/env python3
"""Funds found outside the SEC data: by hand, in Sales Navigator, or by Grok on X.

They are kept in MANUAL_LEADS.csv and go to the front of the daily research
queue, where they are checked against the SEC leads and get a site status like
every other lead.

Add funds one per line, with optional parts separated by "|":

    Fund name | partner names | website or link to the post | note

    python lead_inbox.py add "Black Prism Capital Partners | Steven Miller | https://x.com/i/status/1"

The lines can also come from the LEADS environment variable (used by the
"Add leads" workflow).
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime, timezone

INBOX_FILE = "MANUAL_LEADS.csv"
FIELDS = ["added_on", "fund", "people", "website", "link", "source", "note", "linkedin", "slug", "profile_checked"]
URL_PATTERN = re.compile(r"^(https?://\S+|[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?)$", re.IGNORECASE)
# A link to a post or profile is where the fund was seen, not the fund's own site
POST_HOSTS = ("x.com", "twitter.com", "linkedin.com", "substack.com", "medium.com", "techcrunch.com")


def name_key(name: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(name or "").lower()))


def parse_lines(text: str, source: str, today: str) -> list[dict]:
    """Turn pasted lines into inbox rows; blank lines and a header line are skipped."""
    rows = []
    for line in str(text or "").replace(";;", "\n").splitlines():
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        fund = parts[0].lstrip("-*•0123456789. ").strip() if parts else ""
        if not fund or name_key(fund) in {"fund", "fund name", "name", "none"} or set(fund) <= set("-: "):
            continue
        row = {"added_on": today, "fund": fund, "people": "", "website": "", "link": "", "source": source, "note": ""}
        for part in parts[1:]:
            if not part or set(part) <= set("-: "):
                continue
            if URL_PATTERN.match(part):
                url = part if part.lower().startswith("http") else "https://" + part
                host = re.sub(r"^https?://(www\.)?", "", url.lower()).split("/")[0]
                is_post = any(host == h or host.endswith("." + h) for h in POST_HOSTS)
                row["link" if is_post else "website"] = row["link" if is_post else "website"] or url
            elif not row["people"]:
                row["people"] = part
            else:
                row["note"] = f"{row['note']} {part}".strip()
        rows.append(row)
    return rows


def load(path: str = INBOX_FILE) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def save(rows: list[dict], path: str = INBOX_FILE) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def add(new_rows: list[dict], path: str = INBOX_FILE) -> int:
    """Append rows whose fund is not in the inbox yet; return how many were added."""
    rows = load(path)
    known = {name_key(row["fund"]) for row in rows}
    added = 0
    for row in new_rows:
        key = name_key(row["fund"])
        if key and key not in known:
            known.add(key)
            rows.append(row)
            added += 1
    save(rows, path)
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["add"])
    parser.add_argument("lines", nargs="*", help="One fund per argument; default: the LEADS environment variable")
    parser.add_argument("--source", default="Added by you")
    parser.add_argument("--inbox", default=INBOX_FILE)
    args = parser.parse_args()
    text = "\n".join(args.lines) or os.environ.get("LEADS", "") or ("" if sys.stdin.isatty() else sys.stdin.read())
    rows = parse_lines(text, args.source, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    added = add(rows, args.inbox)
    print(f"Read {len(rows)} funds; added {added} new to {args.inbox}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
