#!/usr/bin/env python3
"""Ask Grok to search X for newly announced venture funds and add them to the lead inbox.

Needs an xAI API key in XAI_API_KEY (console.x.ai; billed separately from a
SuperGrok subscription). X Search is billed per post fetched, so the search is
kept to a date range and a few rounds; the run prints what it fetched and an
estimated cost.

A fund is kept only when Grok gives a link to a post on X, and, when the reply
lists its sources, only when that post is among them. Funds land in
MANUAL_LEADS.csv as "Found on X" and are researched by daily_research.py.
"""

from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timedelta, timezone

import requests

import lead_inbox

API_URL = "https://api.x.ai/v1/responses"
MODEL = "grok-4.7"
SOURCE = "Found on X"
POST_PRICE, PROFILE_PRICE = 0.005, 0.01  # USD per post and per profile fetched
PROMPT = """Search X for posts from {start} to {end} in which a venture capital firm or one of its partners \
announces a new fund: a first or second fund (Fund I or Fund II), a first close or a final close, or a solo GP \
launching a fund.

Keep only funds of under $150M whose firm was founded in the last three years. Leave out established firms, \
Fund III or later, crypto token funds, accelerators, and posts by news outlets or roundup accounts.

Only list a fund whose announcement post you actually found. Never guess a name or a link.

Answer with one line per fund and nothing else, in exactly this form:
Fund name | GP full names separated by commas | link to the announcement post on X | fund size and stage in a few words

If you find none, answer NONE."""
STATUS_ID = re.compile(r"(?:x|twitter)\.com/[^/\s]+/(?:web/)?status/(\d+)", re.IGNORECASE)


def answer_text(data: dict) -> str:
    """The assistant's text from a Responses API reply."""
    parts = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if block.get("type") == "output_text" and block.get("text"):
                parts.append(block["text"])
    return "\n".join(parts) or str(data.get("output_text") or "")


def cited_posts(data: dict) -> set[str]:
    """Status ids of the X posts the reply lists as sources."""
    urls = [str(url) for url in data.get("citations") or []]
    for item in data.get("output") or []:
        for block in item.get("content") or [] if isinstance(item.get("content"), list) else []:
            urls += [str(note.get("url", "")) for note in block.get("annotations") or []]
    return {match.group(1) for url in urls for match in [STATUS_ID.search(url)] if match}


def funds_from(data: dict, today: str) -> tuple[list[dict], list[str]]:
    """Inbox rows for funds backed by a post on X, and the lines that were dropped."""
    text = re.sub(r"\[\[\d+\]\]\([^)]*\)", "", answer_text(data))
    cited = cited_posts(data)
    kept, dropped = [], []
    for row in lead_inbox.parse_lines("\n".join(line for line in text.splitlines() if "|" in line), SOURCE, today):
        post = STATUS_ID.search(row["link"])
        if not post:
            dropped.append(f"{row['fund']}: no link to a post on X")
        elif cited and post.group(1) not in cited:
            dropped.append(f"{row['fund']}: the linked post is not among Grok's sources")
        else:
            kept.append(row)
    return kept, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7, help="How far back to search")
    parser.add_argument("--max-turns", type=int, default=4, help="Rounds of searching Grok may do; fewer costs less")
    parser.add_argument("--inbox", default=lead_inbox.INBOX_FILE)
    args = parser.parse_args()

    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        print("XAI_API_KEY is not set: nothing searched.")
        return 1
    today = datetime.now(timezone.utc)
    start = (today - timedelta(days=args.days)).strftime("%Y-%m-%d")
    end = (today + timedelta(days=1)).strftime("%Y-%m-%d")  # to_date is exclusive
    response = requests.post(API_URL, timeout=600, headers={"Authorization": f"Bearer {key}"}, json={
        "model": MODEL,
        "input": [{"role": "user", "content": PROMPT.format(start=start, end=today.strftime("%Y-%m-%d"))}],
        "tools": [{"type": "x_search", "from_date": start, "to_date": end}],
        "max_turns": args.max_turns,
        "include": ["no_inline_citations"],
    })
    if response.status_code != 200:
        print(f"xAI returned HTTP {response.status_code}: {response.text[:500]}")
        return 1
    data = response.json()
    print("Grok's answer:\n" + answer_text(data) + "\n")

    usage = (data.get("usage") or {}).get("server_side_tool_usage_details") or {}
    posts, profiles = int(usage.get("x_posts_fetched") or 0), int(usage.get("x_users_fetched") or 0)
    print(f"Fetched {posts} posts and {profiles} profiles: about ${posts * POST_PRICE + profiles * PROFILE_PRICE:.2f} "
          "for the search, plus a few cents for the text.")

    rows, dropped = funds_from(data, today.strftime("%Y-%m-%d"))
    for line in dropped:
        print("Dropped: " + line)
    added = lead_inbox.add(rows, args.inbox)
    print(f"{len(rows)} funds with a post on X; {added} new in {args.inbox}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
