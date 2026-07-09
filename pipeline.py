#!/usr/bin/env python3
"""
Unified VC/PE Lead Generation Pipeline (IAPD Edition)
Crawls the SEC Investment Adviser Public Disclosure (IAPD) database for:
- Active Exempt Reporting Advisers (ERAs) with '802-' registration prefix
- Filters out operational startups
- Discovers website domains via DuckDuckGo search
- Scans website quality and detects platform (Webflow, Wix, Squarespace, etc.)
- Scrapes contact details and WHOIS emails
"""

import os
import re
import csv
import sys
import time
import json
import socket
import ssl
import random
import urllib.parse
import urllib.request
import argparse
from datetime import datetime, timedelta
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# Keep whois import safe
try:
    import whois
    HAS_WHOIS = True
except ImportError:
    HAS_WHOIS = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# Global configuration
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*"
}

IAPD_URL = "https://api.adviserinfo.sec.gov/search/firm"


def requests_get(url, params=None, headers=None, timeout=10):
    """Request helper with default headers and simple retries."""
    h = headers or HEADERS
    for attempt in range(2):
        try:
            import requests
            r = requests.get(url, params=params, headers=h, timeout=timeout)
            return r
        except Exception:
            time.sleep(1)
    return None


# 1. SEC EDGAR Form D Scraper & XML Parser
POSITIVE_KEYWORDS = re.compile(
    r"\b(venture|ventures|capital|partners|seed|pre-seed|emerging|opportunity\s+fund|ai\s+fund|technology\s+fund|vc)\b|"
    r"\bfund\s*(i|ii|1|2|one|two)\b",
    re.IGNORECASE
)

NEGATIVE_KEYWORDS = re.compile(
    r"\b(real\s+estate|realty|apartment|residential|housing|mortgage|reit|"
    r"oil|gas|mining|energy\s+fund|petroleum|mineral|coal|utilities|"
    r"restaurant|restaurants|film|movie|entertainment\s+fund|cinema|"
    r"biotech|biotechnology|pharma|pharmaceuticals|therapeutics|sciences|"
    r"crypto|token|coin|blockchain|digital\s+asset|web3\s+token|"
    r"credit\s+fund|credit\s+partner|debt\s+fund|debt\s+partner|clo|cdo|bond|yield|"
    r"warehouse|industrial\s+fund|logistics\s+fund|storage\s+fund)\b",
    re.IGNORECASE
)


def search_form_d_filings(days, logger=print):
    """Search SEC EDGAR EFTS index for Form D filings in the last `days` days."""
    today = datetime.now()
    start_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    
    logger(f"Searching SEC EDGAR for Form D filings from {start_date} to {end_date}...")
    
    base_url = "https://efts.sec.gov/LATEST/search-index"
    headers = {
        "User-Agent": "LeadFinderTeam contact@emergingvcscout.com",
        "Accept": "application/json"
    }
    
    filings = []
    start_from = 0
    size = 100
    
    while True:
        params = {
            "q": "",
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
            "forms": "D",
            "from": start_from,
            "size": size,
        }
        
        data = None
        for attempt in range(3):
            try:
                time.sleep(0.2)  # Rate limiting compliance
                r = requests.get(base_url, params=params, headers=headers, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    break
                else:
                    logger(f"    ⚠️ EFTS search returned {r.status_code}. Retrying in 1.5s...")
                    time.sleep(1.5)
            except Exception as e:
                logger(f"    ⚠️ EFTS search exception: {e}. Retrying in 1.5s...")
                time.sleep(1.5)
        else:
            logger(f"    ❌ EFTS search failed after 3 attempts.")
            break
            
        if not data:
            break
            
        hits = data.get("hits", {}).get("hits", [])
        total = data.get("hits", {}).get("total", {}).get("value", 0)
        
        if not hits:
            break
            
        for hit in hits:
            src = hit.get("_source", {})
            _id = hit.get("_id", "")
            
            # Extract adsh and xml filename
            if ":" in _id:
                adsh, xml_filename = _id.split(":", 1)
            else:
                adsh = src.get("adsh", "")
                xml_filename = "primary_doc.xml"
                
            ciks = src.get("ciks", [])
            cik = ciks[0] if ciks else ""
            display_name = src.get("display_names", [""])[0] if src.get("display_names") else src.get("entity", "")
            display_name = re.sub(r"\s*\(CIK\s*\d+\)\s*$", "", display_name).strip()
            
            filings.append({
                "name": display_name,
                "cik": cik,
                "adsh": adsh,
                "xml_filename": xml_filename,
                "filing_date": src.get("file_date", ""),
                "form_type": src.get("form", ""),  # D or D/A
                "biz_locations": src.get("biz_locations", [])
            })
            
        start_from += size
        if start_from >= total or start_from >= 3000:
            break
            
    logger(f"  → Found {len(filings)} raw Form D filings in the range.")
    return filings


def filter_filings_by_name(filings, logger=print):
    """Filter filings locally by positive and negative keywords in their name."""
    candidates = []
    for f in filings:
        name = f["name"]
        
        # Exclude obvious bad fits
        if NEGATIVE_KEYWORDS.search(name):
            continue
            
        # Keep positive keywords
        if POSITIVE_KEYWORDS.search(name):
            candidates.append(f)
            
    logger(f"  → Filtered name candidates: {len(candidates)} / {len(filings)}")
    return candidates


def fetch_form_d_xml(cik, adsh, xml_filename, logger=print):
    """Fetch the Form D XML file content from SEC EDGAR Archives."""
    if not cik or not adsh or not xml_filename:
        return None
        
    cik_numeric = str(int(cik))
    adsh_no_dashes = adsh.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_numeric}/{adsh_no_dashes}/{xml_filename}"
    
    headers = {
        "User-Agent": "LeadFinderTeam contact@emergingvcscout.com",
        "Accept": "application/xml"
    }
    
    try:
        # Respect SEC limit: 10 requests per second
        time.sleep(0.15)
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.text
        else:
            # Fallback
            fallback_url = f"https://www.sec.gov/Archives/edgar/data/{cik_numeric}/{adsh_no_dashes}/primary_doc.xml"
            time.sleep(0.15)
            r = requests.get(fallback_url, headers=headers, timeout=10)
            if r.status_code == 200:
                return r.text
    except Exception as e:
        logger(f"Error fetching XML for CIK {cik}: {e}")
        
    return None


def parse_form_d_xml(xml_text):
    """Parse Form D XML and extract key metadata fields."""
    info = {
        "phone": "N/A",
        "date_of_first_sale": "Yet to Occur",
        "offering_amount": 0,
        "amount_sold": 0,
        "industry_group": "Unknown",
        "investment_fund_type": "Unknown",
        "related_people": [],
        "street": "",
        "city": "",
        "state": "",
        "country": "United States",
        "zip": "",
        "year_inc": "N/A"
    }
    
    if not xml_text:
        return info
        
    try:
        # Phone
        phone_match = re.search(r"<issuerPhoneNumber>([^<]+)</issuerPhoneNumber>", xml_text)
        if phone_match:
            info["phone"] = phone_match.group(1).strip()
            
        # Year of incorporation
        year_match = re.search(r"<yearOfInc>.*?<value>(\d{4})</value>.*?</yearOfInc>", xml_text, re.DOTALL)
        if year_match:
            info["year_inc"] = year_match.group(1)
            
        # Date of First Sale
        sale_block = re.search(r"<dateOfFirstSale>(.*?)</dateOfFirstSale>", xml_text, re.DOTALL)
        if sale_block:
            block_content = sale_block.group(1)
            val_match = re.search(r"<value>([^<]+)</value>", block_content)
            if val_match:
                info["date_of_first_sale"] = val_match.group(1).strip()
            elif "yetToOccur" in block_content:
                info["date_of_first_sale"] = "Yet to Occur"
                
        # Offering amounts
        amount_match = re.search(r"<totalOfferingAmount>(\d+)</totalOfferingAmount>", xml_text)
        if amount_match:
            info["offering_amount"] = int(amount_match.group(1))
        elif "<totalOfferingAmount" in xml_text and 'indefinite="true"' in xml_text:
            info["offering_amount"] = "Indefinite"
            
        sold_match = re.search(r"<totalAmountSold>(\d+)</totalAmountSold>", xml_text)
        if sold_match:
            info["amount_sold"] = int(sold_match.group(1))
            
        # Industry Group
        ind_group = re.search(r"<industryGroupType>([^<]+)</industryGroupType>", xml_text)
        if ind_group:
            info["industry_group"] = ind_group.group(1).strip()
            
        fund_type = re.search(r"<investmentFundType>([^<]+)</investmentFundType>", xml_text)
        if fund_type:
            info["investment_fund_type"] = fund_type.group(1).strip()
            
        # Related Persons
        person_blocks = re.findall(r"<relatedPersonInfo>(.*?)</relatedPersonInfo>", xml_text, re.DOTALL)
        for p_block in person_blocks:
            first_match = re.search(r"<firstName>([^<]+)</firstName>", p_block)
            last_match = re.search(r"<lastName>([^<]+)</lastName>", p_block)
            rel_matches = re.findall(r"<relationship>([^<]+)</relationship>", p_block)
            
            if first_match and last_match:
                first = first_match.group(1).strip()
                last = last_match.group(1).strip()
                name = f"{first} {last}"
                if first == "-":
                    name = last
                role = ", ".join(rel_matches) if rel_matches else "Related Person"
                info["related_people"].append(f"{name} ({role})")
                
        # Signature Block Fallback
        if not info["related_people"]:
            sig_blocks = re.findall(r"<signature>(.*?)</signature>", xml_text, re.DOTALL)
            for s_block in sig_blocks:
                name_match = re.search(r"<nameOfSigner>([^<]+)</nameOfSigner>", s_block)
                title_match = re.search(r"<signatureTitle>([^<]+)</signatureTitle>", s_block)
                if name_match:
                    name = name_match.group(1).strip()
                    title = title_match.group(1).strip() if title_match else "Signer"
                    info["related_people"].append(f"{name} ({title})")
                    
        # Issuer Address
        addr_block = re.search(r"<issuerAddress>(.*?)</issuerAddress>", xml_text, re.DOTALL)
        if addr_block:
            block = addr_block.group(1)
            s1 = re.search(r"<street1>([^<]+)</street1>", block)
            c = re.search(r"<city>([^<]+)</city>", block)
            st = re.search(r"<stateOrCountry>([^<]+)</stateOrCountry>", block)
            z = re.search(r"<zipCode>([^<]+)</zipCode>", block)
            
            if s1: info["street"] = s1.group(1).strip()
            if c: info["city"] = c.group(1).strip()
            if st: info["state"] = st.group(1).strip()
            if z: info["zip"] = z.group(1).strip()
            
    except Exception:
        pass
        
    return info


def check_related_people_roles(related_people):
    """Check if any of the related people list matches GP/partner/manager roles."""
    keywords = ["partner", "manager", "managing member", "general partner", "managing director", "founder", "gp"]
    for p in related_people:
        p_low = p.lower()
        if any(k in p_low for k in keywords):
            return True
    return False


def calculate_vc_score(f, xml_info, domain_found):
    """Calculate Likely VC Score (-7 to +13)."""
    score = 0
    name = f["name"].lower()
    
    # 1. Name keywords
    if re.search(r"\bfund\s*(i|ii|1|2|one|two)\b", name):
        score += 3
        
    if any(term in name for term in ["venture", "ventures", "seed"]):
        score += 3
        
    # 2. Original filing
    if f["form_type"] == "D":
        score += 2
        
    # 3. Filed in last 30 days
    try:
        f_date = datetime.strptime(f["filing_date"], "%Y-%m-%d")
        if (datetime.now() - f_date).days <= 30:
            score += 2
    except Exception:
        pass
        
    # 4. Offering amount is $1M - $100M
    off_amt = xml_info["offering_amount"]
    if isinstance(off_amt, (int, float)):
        if 1_000_000 <= off_amt <= 100_000_000:
            score += 2
            
    # 5. Related GP/partner/manager
    if check_related_people_roles(xml_info["related_people"]):
        score += 2
        
    # 6. Industry penalties
    industry_grp = xml_info["industry_group"].lower()
    fund_type = xml_info["investment_fund_type"].lower()
    
    is_fund = "pooled investment fund" in industry_grp or "pooled investment fund" in fund_type
    
    bad_industry = False
    for term in ["real estate", "oil", "gas", "energy", "biotech", "biotechnology", "hedge fund", "credit", "debt", "private equity"]:
        if term in industry_grp or term in fund_type or term in name:
            bad_industry = True
            break
            
    if bad_industry or not is_fund:
        score -= 4
        
    # 7. No website found (Disabled to prioritize fast Form D scouting)
    # if not domain_found:
    #     score -= 3
        
    return score


# 2. DuckDuckGo Search-based Domain Discovery
def generate_guess_domains(firm_name):
    """Fall back domains in case search fails."""
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", firm_name).strip()
    words = clean.lower().split()
    if not words:
        return []
    domains = ["".join(words) + ".com"]
    if len(words) > 1:
        domains.append("-".join(words) + ".com")
    stop = {"ventures", "venture", "capital", "partners", "fund", "group", "management"}
    core = [w for w in words if w not in stop]
    if core and core != words:
        domains.append("".join(core) + ".com")
        domains.append("".join(core) + ".vc")
        domains.append("".join(core) + ".io")
    domains.append("".join(words) + ".vc")
    return list(dict.fromkeys(domains))


def search_domain_via_ddg(firm_name, logger=print):
    """Use DuckDuckGo search to locate the VC/PE firm domain."""
    query = f'"{firm_name}" venture capital website'
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        # Respectful rate limit
        time.sleep(random.uniform(1.0, 2.0))
        r = requests_get(url, headers=headers, timeout=10)
        if not r or r.status_code != 200:
            return None

        if HAS_BS4:
            soup = BeautifulSoup(r.text, 'html.parser')
            links = soup.find_all('a', class_='result__url')
            for link in links:
                href = link.get('href', '').strip()
                if "duckduckgo.com/y.js" in href:
                    continue
                parsed = urllib.parse.urlparse(href)
                domain = parsed.netloc or href.split('/')[0]
                domain = domain.replace('www.', '').lower().strip()

                bad_domains = [
                    'sec.gov', 'linkedin.com', 'crunchbase.com', 'pitchbook.com',
                    'twitter.com', 'facebook.com', 'youtube.com', 'google.com',
                    'wikipedia.org', 'cbinsights.com', 'insider.com', 'techcrunch.com',
                    'f6s.com', 'zoominfo.com', 'apollo.io', 'pitchbook.com', 'ycombinator.com',
                    'adviserinfo.sec.gov', 'crd.finra.org', 'sec.report'
                ]
                if domain and not any(bad in domain for bad in bad_domains):
                    return domain
        else:
            links = re.findall(r'href="([^"]+)" class="result__url"', r.text)
            for href in links:
                parsed = urllib.parse.urlparse(href)
                domain = parsed.netloc or href.split('/')[0]
                domain = domain.replace('www.', '').lower().strip()
                bad_domains = ['sec.gov', 'linkedin.com', 'crunchbase.com', 'pitchbook.com', 'twitter.com']
                if domain and not any(bad in domain for bad in bad_domains):
                    return domain
    except Exception as e:
        logger(f"DDG Search error for {firm_name}: {e}")

    return None


def discover_domain(firm_name, logger=print):
    """Finds the domain by search, falls back to guesses (Disabled for speed/reliability)."""
    return None


def verify_domain_match(firm_name, domain, html):
    """
    Verifies that the discovered website domain/html actually belongs to the firm name.
    This prevents false positives (e.g. DDG returning standard.com for Standard Capital Ventures).
    """
    if not html:
        return False
        
    # Clean the firm name: remove punctuation and legal suffixes
    clean_firm = firm_name.lower()
    clean_firm = re.sub(r"\b(llc|lp|l\.p\.|inc|incorporated|corp|corporation|co|company)\b", "", clean_firm)
    clean_firm = re.sub(r"\s+", " ", clean_firm).strip()
    
    domain_lower = domain.lower().replace("www.", "")
    domain_name = domain_lower.split(".")[0]
    
    # Clean the HTML content (remove tags) to get plain text
    text_content = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    
    # Core words from the firm name (longer than 2 letters)
    words = [w.strip() for w in re.split(r"[^a-zA-Z0-9]", clean_firm) if len(w.strip()) > 2]
    
    if not words:
        return True # Fallback if name is very short
        
    # Case 1: High confidence domain name match
    # E.g. firm "Bloom Venture Partners" -> domain "bloomvp" or "bloomventures"
    if len(words) >= 2:
        # Check if first word is in domain, and at least one other core word is in the domain name
        if words[0] in domain_name and any(w in domain_name for w in words[1:]):
            return True
            
    # Case 2: Website text contains the core phrase
    # E.g. "bloom venture" or "standard capital" or "bcf venture"
    if len(words) >= 2:
        phrase = f"{words[0]} {words[1]}"
        if phrase in text_content:
            return True
            
    # Case 3: Domain name matches the first word, and website text mentions the first two words
    if words[0] in domain_name:
        match_count = sum(1 for w in words if w in text_content)
        if match_count >= 2:
            return True
            
    # Fallback for single word firms
    if len(words) == 1 and words[0] in domain_name and words[0] in text_content:
        return True
        
    # If it's a parked domain, let's keep it if the domain itself contains the core name
    if "parked" in html or "domain for sale" in html or "buy this domain" in html:
        if words[0] in domain_name:
            return True
            
    return False


# 3. Website Quality Analyzer
def analyze_website_quality(domain, firm_name=None):
    """Fetches homepage and detects platform, verifying domain owner."""
    result = {"platform": "Custom", "score": 50, "issues": [], "page_size_kb": 0}
    if not domain:
        return {"platform": "No website", "score": 0, "issues": ["No website"], "page_size_kb": 0}

    has_ssl = False
    html = ""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # Try HTTPS first
        try:
            url = f"https://{domain}"
            req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                html = r.read(150000).decode('utf-8', errors='ignore').lower()
                has_ssl = True
                result["page_size_kb"] = len(html) // 1024
        except Exception:
            url = f"http://{domain}"
            req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=5) as r:
                html = r.read(150000).decode('utf-8', errors='ignore').lower()
                result["page_size_kb"] = len(html) // 1024

        if not html:
            return {"platform": "No website", "score": 0, "issues": [], "page_size_kb": 0}

        # Verify domain match to ensure high findings quality
        if firm_name and not verify_domain_match(firm_name, domain, html):
            return {"platform": "No website", "score": 0, "issues": [], "page_size_kb": 0}

        parked_phrases = ['domain for sale', 'buy this domain', 'parked', 'coming soon',
                          'under construction', 'is available', 'register domain', 'hosting provider']
        for p in parked_phrases:
            if p in html:
                return {"platform": "Parked Domain", "score": 0, "issues": [], "page_size_kb": result["page_size_kb"]}

        # Detect platform
        platforms = {
            "squarespace": "Squarespace",
            "wix.com": "Wix",
            "wixstatic": "Wix",
            "wp-content": "WordPress",
            "wordpress": "WordPress",
            "webflow": "Webflow",
            "website-files.com": "Webflow",
            "framer": "Framer",
            "cargo.site": "Cargo",
            "hubspot": "HubSpot",
            "godaddy": "GoDaddy",
            "weebly": "Weebly",
            "carrd.co": "Carrd",
            "notion.site": "Notion",
            "super.so": "Notion",
        }
        for keyword, name in platforms.items():
            if keyword in html:
                result["platform"] = name
                break
        result["score"] = 0
        result["issues"] = []
    except Exception:
        result["platform"] = "No website"
        result["score"] = 0
        result["issues"] = []

    return result


# 4. Email Scraping & WHOIS
def scrape_emails_from_site(domain):
    """Scrape website pages for email addresses."""
    emails = set()
    if not domain:
        return emails

    pages = [
        f"https://{domain}",
        f"https://{domain}/contact",
        f"https://{domain}/about",
    ]
    for url in pages:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=4) as r:
                text = r.read(100000).decode('utf-8', errors='ignore')
                found = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', text)
                for email in found:
                    email_low = email.lower().strip()
                    if not any(x in email_low for x in [
                        'sentry', 'wixpress', 'googleapis', 'cloudflare', 'schema.org', 'w3.org',
                        'gravatar', 'wordpress', 'squarespace', 'webflow', '.png', '.jpg', '.svg', '.gif',
                        'example.com', 'noreply', 'no-reply', 'spam', 'protection', 'abuse', 'postmaster'
                    ]):
                        emails.add(email_low)
                if emails:
                    break
        except Exception:
            continue
    return emails


def get_whois_emails(domain):
    """Retrieve non-private registration email from WHOIS."""
    if not HAS_WHOIS or not domain:
        return set()
    emails = set()
    try:
        w = whois.whois(domain)
        if w:
            for field in ['emails', 'registrant_email', 'admin_email', 'tech_email']:
                val = getattr(w, field, None)
                if val:
                    val_list = val if isinstance(val, list) else [val]
                    for e in val_list:
                        if e and '@' in str(e):
                            email_low = str(e).lower().strip()
                            if not any(x in email_low for x in [
                                'privacy', 'proxy', 'redacted', 'abuse', 'whoisguard',
                                'godaddy', 'namecheap', 'cloudflare', 'domainsbyproxy'
                            ]):
                                emails.add(email_low)
    except Exception:
        pass
    return emails


# 5. LinkedIn Search Link Generator
def get_linkedin_urls(firm_name):
    """Constructs LinkedIn company search URL and team search URL targeting decision-makers."""
    company_url = ""
    team_url = ""
    if firm_name:
        q = urllib.parse.quote_plus(firm_name)
        company_url = f"https://www.linkedin.com/search/results/companies/?keywords={q}"
        team_url = f"https://www.linkedin.com/search/results/people/?keywords={q}+AND+(%22general+partner%22+OR+%22managing+director%22+OR+%22founder%22+OR+%22partner%22)"
    return company_url, team_url# Lead Enricher and Execution Engine
def run_pipeline(days=30, lead_type="vc", min_size=0, output_file="ALL_VC_LEADS.csv", logger=print):
    """Executes the lead finder pipeline using SEC EDGAR Form D search and scoring."""
    logger(f"🚀 Starting Form D Lead Pipeline | Days: {days} | Target: {lead_type.upper()}")
    
    # 1. Search EFTS index for Form D filings in the range
    raw_filings = search_form_d_filings(days, logger=logger)
    
    # 2. Filter locally by name keywords
    candidates = filter_filings_by_name(raw_filings, logger=logger)
    
    logger(f"🔍 Found {len(candidates)} candidate filings. Fetching Form D details...")
    
    enriched_leads = []
    target_count = len(candidates)
    
    for done, c in enumerate(candidates, 1):
        cik = c["cik"]
        adsh = c["adsh"]
        xml_file = c["xml_filename"]
        firm_name = c["name"]
        
        logger(f"  [{done}/{target_count}] Processing: {firm_name} (CIK: {cik})...")
        
        try:
            xml_text = fetch_form_d_xml(cik, adsh, xml_file, logger=logger)
            if not xml_text:
                logger(f"    ⚠️ Could not retrieve XML filing.")
                continue
                
            xml_info = parse_form_d_xml(xml_text)
            
            # Format clean firm name
            clean_name = firm_name
            clean_name = re.sub(r",?\s*fund\s*(I+|[0-9]+|one|two).*$", "", clean_name, flags=re.IGNORECASE)
            clean_name = re.sub(r",?\s*L\.?P\.?\s*$", "", clean_name, flags=re.IGNORECASE)
            clean_name = re.sub(r",?\s*LLC\s*$", "", clean_name, flags=re.IGNORECASE)
            clean_name = re.sub(r",?\s*Inc\.?\s*$", "", clean_name, flags=re.IGNORECASE)
            clean_name = clean_name.strip(" ,\"")
            
            # Resolve domain only for candidate leads to keep it fast
            domain = discover_domain(clean_name, logger=logger) or ""
            domain_found = bool(domain)
            
            # Calculate VC score
            score = calculate_vc_score(c, xml_info, domain_found)
            
            # Format amounts nicely
            offering_amt = xml_info["offering_amount"]
            if isinstance(offering_amt, (int, float)):
                if offering_amt >= 1_000_000_000:
                    off_amt_str = f"${offering_amt/1_000_000_000:.1f}B"
                elif offering_amt >= 1_000_000:
                    off_amt_str = f"${offering_amt/1_000_000:.0f}M"
                else:
                    off_amt_str = f"${offering_amt:,}"
            else:
                off_amt_str = str(offering_amt)
                
            amt_sold = xml_info["amount_sold"]
            if isinstance(amt_sold, (int, float)):
                if amt_sold >= 1_000_000:
                    amt_sold_str = f"${amt_sold/1_000_000:.0f}M"
                else:
                    amt_sold_str = f"${amt_sold:,}"
            else:
                amt_sold_str = str(amt_sold)
                
            # Classify fund stage
            if re.search(r"fund\s*(I|1|one)\b", firm_name, re.IGNORECASE):
                fund_stage = "Fund I"
            elif re.search(r"fund\s*(II|2|two)\b", firm_name, re.IGNORECASE):
                fund_stage = "Fund II"
            else:
                fund_stage = "Emerging Fund"
                
            # Parse contact name/title from related persons
            contact_name = "View Team on SEC/LinkedIn"
            contact_title = "General Partner"
            if xml_info["related_people"]:
                first_person = xml_info["related_people"][0]
                if " (" in first_person:
                    contact_name, contact_title = first_person.split(" (", 1)
                    contact_title = contact_title.rstrip(")")
                else:
                    contact_name = first_person
                    
            lead = {
                "checked": "",
                "firm_name": clean_name,
                "name": firm_name,
                "contact_name": contact_name,
                "contact_title": contact_title,
                "phone": xml_info["phone"],
                "primary_email": "",
                "domain": domain,
                "address": f"{xml_info['street']}, {xml_info['city']}, {xml_info['state']} {xml_info['zip']}".strip(" ,"),
                "fund_size": off_amt_str,
                "amount_sold": amt_sold_str,
                "year_inc": xml_info["year_inc"],
                "fund_stage": fund_stage,
                "filer_status": "first_filer" if c["form_type"] == "D" else "new_filer",
                "total_filings": "1",
                "platform": "Has website" if domain_found else "No website",
                "site_score": score,  # for dashboard backwards compatibility
                "vc_score": score,
                "issues": f"{xml_info['industry_group']} - {xml_info['investment_fund_type']}",
                "linkedin_company": "",
                "linkedin_person": "",
                "all_contacts": "; ".join(xml_info["related_people"]),
                "filing_date": c["filing_date"],
                "crd": cik,  # mapped to CRD for server/frontend integration
                "sec_number": adsh, # mapped to SEC number for server/frontend integration
                "filing_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}/{adsh}-index.htm",
                "city": xml_info["city"],
                "state": xml_info["state"],
                "country": xml_info["country"]
            }
            
            enriched_leads.append(lead)
            logger(f"    → Success! Scored: {score} | Location: {lead['city']}, {lead['state']} | Site: {lead['domain'] or 'None'}")
            
        except Exception as ex:
            logger(f"    ❌ Error processing lead: {ex}")
            
    # Load and merge existing leads if file exists
    all_leads_dict = {}
    if os.path.exists(output_file):
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("crd"): # CRD contains CIK here
                        all_leads_dict[row["crd"]] = row
        except Exception:
            pass
            
    # Merge new leads
    merged_count = 0
    for lead in enriched_leads:
        cik = lead["crd"]
        if cik not in all_leads_dict:
            merged_count += 1
        all_leads_dict[cik] = lead
        
    # Sort master list by vc_score descending (best leads first)
    final_list = list(all_leads_dict.values())
    def get_score_val(x):
        try:
            return int(x.get("vc_score") or x.get("site_score") or 0)
        except (ValueError, TypeError):
            return 0
    final_list.sort(key=get_score_val, reverse=True)
    
    # Save CSV
    fieldnames = [
        "checked", "firm_name", "name", "contact_name", "contact_title",
        "phone", "primary_email", "domain", "address", "fund_size",
        "amount_sold", "year_inc", "fund_stage", "filer_status",
        "total_filings", "platform", "site_score", "vc_score", "issues",
        "linkedin_company", "linkedin_person", "all_contacts",
        "filing_date", "crd", "sec_number", "filing_url", "city", "state", "country"
    ]
    
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for lead in final_list:
            w.writerow(lead)
            
    logger(f"✅ Pipeline Completed! Saved {len(final_list)} unique leads (Added {merged_count} new filings in this run) to {output_file}")
    return len(final_list)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEC EDGAR Form D VC Lead Pipeline")
    parser.add_argument("--type", type=str, choices=["vc", "pe"], default="vc", help="Target firm type")
    parser.add_argument("--output", type=str, default="ALL_VC_LEADS.csv", help="Master output CSV file")
    parser.add_argument("--days", type=int, default=30, help="Days range")
    args = parser.parse_args()

    run_pipeline(days=args.days, lead_type=args.type, output_file=args.output)
