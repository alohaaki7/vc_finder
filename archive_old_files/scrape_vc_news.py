#!/usr/bin/env python3
"""
VC News Scraper
Finds new VC fund launches from news articles.
Uses Google News RSS feeds (free, no API key needed).
"""

import requests
import csv
import re
import time
from datetime import datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

# Search queries that indicate NEW fund launches
NEWS_QUERIES = [
    '"debut fund" venture',
    '"first fund" venture capital',
    '"launches fund" venture',
    '"new fund" venture capital 2026',
    '"emerging manager" venture',
    '"raises first" venture fund',
    '"fund I" venture capital launch',
    '"inaugural fund" venture',
    '"closes fund" emerging venture 2026',
    '"new venture firm"',
    '"just raised" venture fund',
]


def search_google_news(query, num_results=100):
    """Search Google News RSS for articles matching query."""
    encoded = quote_plus(query)
    # Google News RSS feed
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        
        root = ElementTree.fromstring(r.content)
        articles = []
        
        for item in root.findall('.//item'):
            title = item.find('title')
            link = item.find('link')
            pub_date = item.find('pubDate')
            source = item.find('source')
            
            articles.append({
                'title': title.text if title is not None else '',
                'url': link.text if link is not None else '',
                'pub_date': pub_date.text if pub_date is not None else '',
                'source': source.text if source is not None else '',
            })
        
        return articles
    except Exception as e:
        print(f"  Error: {e}")
        return []


def extract_vc_info(title):
    """Try to extract VC firm name and fund size from article title."""
    firm_name = ""
    fund_size = ""
    
    # Try to find dollar amounts
    size_match = re.search(r'\$[\d,\.]+\s*(million|billion|M|B|m|b)', title, re.IGNORECASE)
    if size_match:
        fund_size = size_match.group(0)
    
    return fund_size


def parse_date(date_str):
    """Parse RSS date format."""
    try:
        # Format: "Mon, 03 Feb 2026 12:00:00 GMT"
        dt = datetime.strptime(date_str.strip(), "%a, %d %b %Y %H:%M:%S %Z")
        return dt.strftime("%Y-%m-%d")
    except:
        try:
            dt = datetime.strptime(date_str.strip()[:25], "%a, %d %b %Y %H:%M:%S")
            return dt.strftime("%Y-%m-%d")
        except:
            return date_str


def main():
    print("=" * 70)
    print("VC News Scraper - Finding New Fund Launches")
    print("=" * 70)
    
    all_articles = {}
    
    for query in NEWS_QUERIES:
        print(f'\nSearching: {query}')
        articles = search_google_news(query)
        
        new_count = 0
        for article in articles:
            title = article['title']
            url = article['url']
            
            # Skip if already seen (by URL)
            if url in all_articles:
                continue
            
            # Must mention venture/VC related terms
            if not re.search(r'venture|vc|fund|capital|startup', title, re.IGNORECASE):
                continue
            
            # Skip non-VC articles
            if re.search(r'stock|etf|mutual fund|retirement|401k|sovereign', title, re.IGNORECASE):
                continue
            
            fund_size = extract_vc_info(title)
            
            all_articles[url] = {
                'title': title,
                'source': article['source'],
                'pub_date': parse_date(article['pub_date']),
                'fund_size': fund_size,
                'url': url,
            }
            new_count += 1
        
        print(f"  → {new_count} new articles")
        time.sleep(1)  # Be nice to Google
    
    # Sort by date (newest first)
    articles = sorted(all_articles.values(), key=lambda x: x.get('pub_date', ''), reverse=True)
    
    # Save
    output = "vc_news_leads.csv"
    with open(output, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["checked", "title", "source", "pub_date", "fund_size", "url"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for a in articles:
            a["checked"] = ""
            w.writerow(a)
    
    print("\n" + "=" * 70)
    print(f"✓ Found {len(articles)} news articles about new VC funds")
    print(f"✓ Saved to: {output}")
    print("=" * 70)
    
    print(f"\nTop 20 articles:")
    print("-" * 70)
    for i, a in enumerate(articles[:20], 1):
        print(f"{i}. [{a['pub_date']}] {a['title']}")
        if a['fund_size']:
            print(f"   💰 {a['fund_size']}")
        print(f"   📰 {a['source']}")
        print()


if __name__ == "__main__":
    main()
