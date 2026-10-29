#!/usr/bin/env python3
"""Run a few Google searches through treg and print the results.

Used to try a search idea (for example public LinkedIn pages of new funds)
before adding it to the daily job. Each result is marked when its fund already
appears in the SEC leads, so only new names stand out.

Queries come from the QUERIES environment variable (one per line) or the
command line. Needs TREG_TOKEN.
"""

from __future__ import annotations

import os
import re
import sys

import requests

import daily_research

MAX_QUERIES = 20


def known_brands() -> list[str]:
    import server  # prepared leads, with the same verdict rules as the dashboard

    brands = set()
    for lead in server.load_display_leads():
        words = re.findall(r"[a-z0-9]+", daily_research.brand_of(lead.get("name") or "").lower())
        distinctive = [w for w in words if w not in daily_research.GENERIC_WORDS]
        if distinctive and (len(words) >= 2 or len(words[0]) >= 5):
            brands.add(" ".join(words))
    return sorted(brands)


def search(token: str, query: str) -> tuple[int, list[dict], int]:
    response = requests.post(
        daily_research.TREG_URL + "treg.google.serp.organic",
        headers={"X-Treg-Token": token, "X-Treg-Route-Max-Cost": daily_research.MAX_COST_PER_CALL},
        json={"q": query, "limit": 10},
        timeout=120,
    )
    cost = int(response.headers.get("x-treg-cost-micro") or 0)
    if response.status_code != 200:
        return response.status_code, [], cost
    return 200, ((response.json().get("output") or {}).get("results") or []), cost


def main() -> int:
    token = os.environ.get("TREG_TOKEN", "").strip()
    if not token:
        print("TREG_TOKEN is not set.")
        return 1
    queries = [q.strip() for q in (sys.argv[1:] or os.environ.get("QUERIES", "").splitlines()) if q.strip()]
    brands = known_brands()
    spent = new = total = 0
    for query in queries[:MAX_QUERIES]:
        status, results, cost = search(token, query)
        spent += cost
        print(f"\n## {query}\n   status {status}, {len(results)} results")
        for result in results:
            text = " ".join(re.findall(r"[a-z0-9]+", f"{result.get('title', '')} {result.get('snippet', '')}".lower()))
            known = next((b for b in brands if f" {b} " in f" {text} "), "")
            total += 1
            new += not known
            print(f" {'  known' if known else '* NEW  '} {result.get('title', '')[:90]}")
            print(f"          {result.get('url', '')}")
            print(f"          {(result.get('snippet') or '')[:200]}")
    print(f"\n{total} results, {new} not matched to an SEC lead; treg spent ${spent / 1e6:.4f}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
