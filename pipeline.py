#!/usr/bin/env python3
"""
Unified VC/PE Lead Generation Pipeline
Crawls SEC EDGAR Form D filings for:
- Fresh original fund filings
- Fund I and Fund II signals
- recent or not-yet-occurred first-sale dates
- recently formed issuers
- Venture Capital Fund metadata
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
            middle_match = re.search(r"<middleName>([^<]+)</middleName>", p_block)
            last_match = re.search(r"<lastName>([^<]+)</lastName>", p_block)
            rel_matches = re.findall(r"<relationship>([^<]+)</relationship>", p_block)
            
            if first_match and last_match:
                first = first_match.group(1).strip()
                middle = middle_match.group(1).strip() if middle_match else ""
                last = last_match.group(1).strip()
                name = " ".join(part for part in [first, middle, last] if part)
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


HISTORY_START_DATE = "2001-01-01"
FUND_VEHICLE_PATTERN = re.compile(
    r"\b(a\s+series\s+of|series\s+of|special\s+purpose\s+vehicle|spv|syndicate|co[-\s]?invest(?:ment)?\s+vehicle)\b",
    re.IGNORECASE
)
ENTITY_IDENTITY_PATTERN = re.compile(
    r"\b(llc|l\.l\.c\.?|lp|l\.p\.?|ltd|inc|corp|company|management|manager|"
    r"advisers?|advisors?|partners?|capital|ventures?|fund|gp|group|holdings?)\b",
    re.IGNORECASE
)
MANAGER_STOP_WORDS = {
    "a", "adviser", "advisers", "advisor", "advisors", "and", "capital",
    "co", "company", "corp", "corporation", "fund", "funds", "general",
    "gp", "group", "holding", "holdings", "i", "ii", "iii", "iv", "inc",
    "investment", "investments", "limited", "llc", "lp", "ltd", "management",
    "manager", "managers", "member", "one", "partner", "partners", "the",
    "two", "venture", "ventures"
}


def clean_firm_name(firm_name):
    """Remove fund numbering and legal suffixes from an issuer name."""
    clean_name = str(firm_name or "")
    clean_name = re.sub(
        r",?\s*fund\s*(I+|[0-9]+|one|two).*$",
        "",
        clean_name,
        flags=re.IGNORECASE
    )
    clean_name = re.sub(r",?\s*L\.?P\.?\s*$", "", clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r",?\s*LLC\s*$", "", clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r",?\s*Inc\.?\s*$", "", clean_name, flags=re.IGNORECASE)
    return clean_name.strip(" ,\"")


def extract_related_name(value):
    """Return the name portion of a stored `Name (Role)` related-person value."""
    name = re.sub(r"\s+\([^()]*\)\s*$", "", str(value or "")).strip()
    name = re.sub(r"^(?:n/?a|not\s+applicable)\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+(?:n/?a|not\s+applicable)$", "", name, flags=re.IGNORECASE)
    return name.strip(" ,-")


def normalize_identity(value):
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else digits


def manager_brand_tokens(value):
    return [
        token for token in normalize_identity(value).split()
        if token not in MANAGER_STOP_WORDS and not re.fullmatch(r"[0-9]+", token)
    ]


def is_entity_identity(value):
    return bool(ENTITY_IDENTITY_PATTERN.search(str(value or "")))


def is_useful_manager_name(value):
    normalized = normalize_identity(value)
    if len(normalized) < 4 or normalized in {"n a", "na", "unknown", "fund gp"}:
        return False
    brand_tokens = manager_brand_tokens(value)
    if brand_tokens:
        return any(len(token) >= 3 for token in brand_tokens)
    return bool(re.search(r"\d{3,}", normalized))


def canonical_person_name(value):
    if is_entity_identity(value):
        return ""
    tokens = normalize_identity(value).split()
    if len(tokens) < 2:
        return ""
    return f"{tokens[0]} {tokens[-1]}"


def names_share_manager_identity(current_name, prior_name):
    """Conservatively compare manager/issuer names after structural words are removed."""
    current = normalize_identity(current_name)
    prior = normalize_identity(prior_name)
    if not current or not prior:
        return False
    if len(current) >= 5 and (current in prior or prior in current):
        return True

    current_tokens = set(manager_brand_tokens(current_name))
    prior_tokens = set(manager_brand_tokens(prior_name))
    shared = current_tokens & prior_tokens
    if len(shared) >= 2:
        return True
    if len(shared) == 1:
        token = next(iter(shared))
        return len(token) >= 6 and (current_tokens <= prior_tokens or prior_tokens <= current_tokens)
    return False


def build_manager_search_identities(firm_name, clean_name, xml_info):
    """Build a small, ordered set of strong-to-weak identities for SEC history search."""
    identities = []
    seen = set()

    def add(kind, value):
        value = str(value or "").strip()
        key = (kind, normalize_identity(value))
        if not value or not key[1] or key in seen:
            return
        seen.add(key)
        identities.append({"kind": kind, "value": value})

    if is_useful_manager_name(clean_name):
        add("manager_name", clean_name)

    series_match = re.search(
        r"(?:a\s+)?series\s+of\s+(.+?)(?:,?\s+(?:l\.?p\.?|llc)\b|$)",
        str(firm_name or ""),
        re.IGNORECASE
    )
    if series_match and is_useful_manager_name(series_match.group(1)):
        add("manager_entity", series_match.group(1))

    entity_names = []
    person_names = []
    for related in xml_info.get("related_people", []):
        name = extract_related_name(related)
        if not is_useful_manager_name(name):
            continue
        if is_entity_identity(name):
            entity_names.append(name)
        elif canonical_person_name(name):
            person_names.append(name)

    for name in entity_names[:3]:
        add("manager_entity", name)
    for name in person_names[:2]:
        add("person", name)

    phone = str(xml_info.get("phone", "") or "").strip()
    if len(normalize_phone(phone)) >= 10:
        add("phone", phone)

    street = str(xml_info.get("street", "") or "").strip()
    zip_code = str(xml_info.get("zip", "") or "").strip()
    if street and zip_code:
        add("address", f"{street} {zip_code}")

    return identities[:8]


def search_prior_form_d_filings(query, before_date, logger=print):
    """Search full-text EDGAR for Form D filings older than a candidate filing."""
    filing_date = parse_sec_date(before_date)
    if not filing_date:
        return [], False

    end_date = (filing_date - timedelta(days=1)).strftime("%Y-%m-%d")
    if end_date < HISTORY_START_DATE:
        return [], True

    safe_query = re.sub(r"[\"\r\n]+", " ", str(query or "")).strip()
    if not safe_query:
        return [], True

    params = {
        "q": f'"{safe_query}"',
        "dateRange": "custom",
        "startdt": HISTORY_START_DATE,
        "enddt": end_date,
        "forms": "D",
        "from": 0,
        "size": 100,
    }
    headers = {
        "User-Agent": "LeadFinderTeam contact@emergingvcscout.com",
        "Accept": "application/json"
    }

    for attempt in range(3):
        try:
            time.sleep(0.2)
            response = requests.get(
                "https://efts.sec.gov/LATEST/search-index",
                params=params,
                headers=headers,
                timeout=20
            )
            if response.status_code == 200:
                return response.json().get("hits", {}).get("hits", []), True
        except Exception:
            pass
        if attempt < 2:
            time.sleep(1.0)

    logger(f"    Manager history search failed for: {safe_query}")
    return [], False


def history_hit_to_filing(hit):
    src = hit.get("_source", {})
    hit_id = hit.get("_id", "")
    if ":" in hit_id:
        adsh_from_id, xml_filename = hit_id.split(":", 1)
    else:
        adsh_from_id, xml_filename = "", "primary_doc.xml"
    ciks = src.get("ciks", [])
    display_names = src.get("display_names", [])
    return {
        "name": re.sub(
            r"\s*\(CIK\s*\d+\)\s*$",
            "",
            display_names[0] if display_names else "",
            flags=re.IGNORECASE
        ).strip(),
        "cik": ciks[0] if ciks else "",
        "adsh": src.get("adsh", "") or adsh_from_id,
        "xml_filename": xml_filename or "primary_doc.xml",
        "filing_date": src.get("file_date", ""),
        "form_type": src.get("form", src.get("file_type", "")),
    }


def is_historical_fund(xml_info):
    industry = str(xml_info.get("industry_group", "")).lower()
    fund_type = str(xml_info.get("investment_fund_type", "")).lower()
    return "pooled investment fund" in industry or "fund" in fund_type


def history_identity_matches(identity, prior_filing, prior_info):
    kind = identity["kind"]
    value = identity["value"]
    prior_related = [extract_related_name(p) for p in prior_info.get("related_people", [])]

    if kind in {"manager_name", "manager_entity"}:
        prior_names = [prior_filing.get("name", "")] + prior_related
        return any(names_share_manager_identity(value, name) for name in prior_names)
    if kind == "person":
        person = canonical_person_name(value)
        return bool(person) and any(canonical_person_name(name) == person for name in prior_related)
    if kind == "phone":
        return normalize_phone(value) == normalize_phone(prior_info.get("phone", ""))
    if kind == "address":
        current = normalize_identity(value)
        prior = normalize_identity(f"{prior_info.get('street', '')} {prior_info.get('zip', '')}")
        return bool(current and prior and current == prior)
    return False


def history_filing_url(filing):
    try:
        cik = str(int(filing["cik"]))
    except (ValueError, TypeError, KeyError):
        return ""
    adsh = str(filing.get("adsh", ""))
    if not adsh:
        return ""
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{adsh}-index.htm"


def find_manager_history(filing, clean_name, xml_info, cache=None, logger=print):
    """Find strong or supporting evidence that a manager raised an older fund."""
    cache = cache if cache is not None else {}
    identities = build_manager_search_identities(filing.get("name", ""), clean_name, xml_info)
    result = {
        "checked": False,
        "found": False,
        "weak_match": False,
        "count": 0,
        "queries_checked": 0,
        "queries_total": len(identities),
        "reason": "No usable manager identities were available for history search.",
        "first_filing_date": "",
        "filing_name": "",
        "filing_url": "",
        "matched_identity": "",
    }
    if not identities:
        return result

    strong_matches = []
    weak_matches = []
    all_queries_completed = True

    for identity in identities:
        cache_key = (
            identity["kind"],
            normalize_identity(identity["value"]),
            str(filing.get("filing_date", ""))[:10]
        )
        if cache_key in cache:
            hits, search_ok = cache[cache_key]
        else:
            hits, search_ok = search_prior_form_d_filings(
                identity["value"],
                filing.get("filing_date", ""),
                logger=logger
            )
            cache[cache_key] = (hits, search_ok)

        if not search_ok:
            all_queries_completed = False
            continue
        result["queries_checked"] += 1

        historical_filings = [history_hit_to_filing(hit) for hit in hits]
        historical_filings = [
            item for item in historical_filings
            if item.get("adsh") and item.get("adsh") != filing.get("adsh")
        ]
        historical_filings.sort(
            key=lambda item: (
                0 if item.get("form_type") == "D" else 1,
                item.get("filing_date", "")
            )
        )

        unique_filings = []
        seen_ciks = set()
        for prior in historical_filings:
            history_key = prior.get("cik") or prior.get("adsh")
            if history_key in seen_ciks:
                continue
            seen_ciks.add(history_key)
            unique_filings.append(prior)
            if len(unique_filings) >= 4:
                break

        for prior in unique_filings:
            prior_xml = fetch_form_d_xml(
                prior.get("cik", ""),
                prior.get("adsh", ""),
                prior.get("xml_filename", "primary_doc.xml"),
                logger=logger
            )
            if not prior_xml:
                all_queries_completed = False
                continue
            prior_info = parse_form_d_xml(prior_xml)
            if not is_historical_fund(prior_info):
                continue
            if not history_identity_matches(identity, prior, prior_info):
                continue

            match = {
                "filing": prior,
                "identity": identity,
            }
            if identity["kind"] in {"manager_name", "manager_entity", "person"}:
                strong_matches.append(match)
            else:
                weak_matches.append(match)

        if strong_matches:
            break

    matches = strong_matches or weak_matches
    if matches:
        matches.sort(key=lambda match: match["filing"].get("filing_date", "9999-99-99"))
        first = matches[0]
        unique_history = {
            match["filing"].get("cik") or match["filing"].get("adsh")
            for match in matches
        }
        prior = first["filing"]
        identity = first["identity"]
        result.update({
            "checked": all_queries_completed or bool(strong_matches),
            "found": bool(strong_matches),
            "weak_match": not bool(strong_matches),
            "count": len(unique_history),
            "first_filing_date": prior.get("filing_date", ""),
            "filing_name": prior.get("name", ""),
            "filing_url": history_filing_url(prior),
            "matched_identity": identity["value"],
        })
        if strong_matches:
            result["reason"] = (
                f"Prior fund filing matched {identity['kind'].replace('_', ' ')} "
                f"'{identity['value']}': {prior.get('name', 'older fund')} "
                f"({prior.get('filing_date', 'date unavailable')})."
            )
        else:
            result["reason"] = (
                f"Only a shared {identity['kind']} matched an older fund filing; "
                "manager identity still needs review."
            )
        return result

    result["checked"] = all_queries_completed and result["queries_checked"] == len(identities)
    if result["checked"]:
        result["reason"] = (
            f"No earlier Form D fund filing matched {len(identities)} manager identities "
            f"searched back to {HISTORY_START_DATE[:4]}."
        )
    else:
        result["reason"] = "Manager history search was incomplete; keep this lead in review."
    return result


def assess_manager_novelty(filing, xml_info, fund_stage, history):
    """Turn SEC history evidence into the product-facing manager verdict."""
    firm_name = filing.get("name", "")
    formed_recently = False
    try:
        formed_recently = int(xml_info.get("year_inc")) >= datetime.now().year - 1
    except (TypeError, ValueError):
        pass

    if history.get("found"):
        code = "existing_manager"
        status = "Existing manager"
        score = 5
        confidence = "High"
        reason = history["reason"]
    elif fund_stage == "Fund II":
        code = "existing_manager"
        status = "Existing manager"
        score = 10
        confidence = "High" if history.get("checked") else "Medium"
        reason = history["reason"] if history.get("weak_match") else (
            "Fund II indicates a prior fund even when no matching older SEC filing is available."
        )
    elif FUND_VEHICLE_PATTERN.search(firm_name):
        code = "needs_review"
        status = "Needs review"
        score = 25
        confidence = "High"
        reason = "Series/SPV structure is not treated as a standalone new VC firm."
    elif not history.get("checked") or history.get("weak_match"):
        code = "needs_review"
        status = "Needs review"
        score = 40
        confidence = "Low"
        reason = history["reason"]
    elif fund_stage == "Fund I":
        code = "likely_new"
        status = "Likely new firm"
        score = 95 if formed_recently else 85
        confidence = "High" if formed_recently else "Medium"
        reason = history["reason"]
    elif formed_recently:
        code = "likely_new"
        status = "Likely new firm"
        score = 75
        confidence = "Medium"
        reason = history["reason"]
    else:
        code = "needs_review"
        status = "Needs review"
        score = 50
        confidence = "Low"
        reason = "No prior manager match, but the filing lacks a strong Fund I or recent-formation signal."

    return {
        "manager_status_code": code,
        "manager_status": status,
        "manager_novelty_score": score,
        "manager_confidence": confidence,
        "manager_history_count": history.get("count", 0),
        "manager_history_reason": reason,
        "manager_first_filing_date": history.get("first_filing_date", ""),
        "manager_history_name": history.get("filing_name", ""),
        "manager_history_url": history.get("filing_url", ""),
        "manager_matched_identity": history.get("matched_identity", ""),
    }


FUND_I_PATTERN = re.compile(r"\bfund\s*(i|1|one)\b", re.IGNORECASE)
FUND_II_PATTERN = re.compile(r"\bfund\s*(ii|2|two)\b", re.IGNORECASE)
FOLLOW_ON_FUND_PATTERN = re.compile(
    r"\bfund\s*(iii|iv|v|vi|vii|viii|ix|x|3|4|5|6|7|8|9|10)\b",
    re.IGNORECASE
)


def classify_fund_stage(firm_name):
    """Classify the fund stage from the issuer/fund name."""
    if FUND_II_PATTERN.search(firm_name):
        return "Fund II"
    if FUND_I_PATTERN.search(firm_name):
        return "Fund I"
    if FOLLOW_ON_FUND_PATTERN.search(firm_name):
        return "Later Fund"
    return "Emerging Fund"


def parse_sec_date(value):
    """Parse SEC YYYY-MM-DD strings. Returns None for missing/not-yet dates."""
    if not value or str(value).lower().startswith("yet"):
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except Exception:
        return None


def is_recent_date(value, days):
    """Return True when a date string is within the requested freshness window."""
    parsed = parse_sec_date(value)
    if not parsed:
        return False
    return parsed >= datetime.now() - timedelta(days=days)


def is_not_yet_first_sale(value):
    return str(value or "").strip().lower().startswith("yet")


def amount_passes_minimum(amount, min_size):
    """Respect the user's minimum size filter when the filing has a numeric amount."""
    if not min_size:
        return True
    if isinstance(amount, (int, float)):
        return amount >= min_size
    return True


def looks_like_vc_fund(firm_name, xml_info):
    text = " ".join([
        firm_name,
        xml_info.get("industry_group", ""),
        xml_info.get("investment_fund_type", "")
    ]).lower()
    return (
        "venture capital fund" in text or
        any(term in text for term in ["venture", "ventures", "seed fund", "vc fund"])
    )


def looks_like_pe_fund(firm_name, xml_info):
    text = " ".join([
        firm_name,
        xml_info.get("industry_group", ""),
        xml_info.get("investment_fund_type", "")
    ]).lower()
    return "private equity" in text or "buyout" in text or "growth equity" in text


def calculate_freshness_score(f, xml_info, fund_stage, days):
    """Score only novelty/newness signals, separate from VC fit."""
    score = 0
    reasons = []

    if f["form_type"] == "D":
        score += 4
        reasons.append("original Form D")
    else:
        score -= 3
        reasons.append("amendment")

    filing_date = parse_sec_date(f.get("filing_date", ""))
    if filing_date:
        age_days = (datetime.now() - filing_date).days
        if age_days <= 7:
            score += 3
            reasons.append("filed this week")
        elif age_days <= 30:
            score += 2
            reasons.append("filed this month")
        elif age_days <= days:
            score += 1
            reasons.append(f"filed within {days} days")

    if is_not_yet_first_sale(xml_info["date_of_first_sale"]):
        score += 4
        reasons.append("first sale not yet occurred")
    elif is_recent_date(xml_info["date_of_first_sale"], days):
        score += 3
        reasons.append("recent first sale")

    try:
        year_inc = int(xml_info["year_inc"])
        current_year = datetime.now().year
        if year_inc == current_year:
            score += 2
            reasons.append(f"issuer formed {year_inc}")
        elif year_inc == current_year - 1:
            score += 1
            reasons.append(f"issuer formed {year_inc}")
    except Exception:
        pass

    if fund_stage == "Fund I":
        score += 4
        reasons.append("Fund I")
    elif fund_stage == "Fund II":
        score += 2
        reasons.append("Fund II")
    elif fund_stage == "Later Fund":
        score -= 5
        reasons.append("later fund")

    return score, "; ".join(reasons)


def lead_matches_target(f, xml_info, fund_stage, lead_type, min_size, days):
    """Keep only leads that are truly fresh fund/firm signals."""
    if not amount_passes_minimum(xml_info["offering_amount"], min_size):
        return False

    is_original = f["form_type"] == "D"
    has_fresh_sale = is_not_yet_first_sale(xml_info["date_of_first_sale"]) or is_recent_date(xml_info["date_of_first_sale"], days)
    formed_recently = False
    try:
        formed_recently = int(xml_info["year_inc"]) >= datetime.now().year - 1
    except Exception:
        pass

    # Newness gate: a lead should be an original filing with a current first-sale,
    # recent issuer formation, or a clearly early fund stage.
    if not is_original:
        return False
    if not (has_fresh_sale or formed_recently or fund_stage in ["Fund I", "Fund II"]):
        return False
    if fund_stage == "Later Fund":
        return False

    if lead_type == "fund2" and fund_stage != "Fund II":
        return False
    if lead_type in ["vc", "fund2"] and not looks_like_vc_fund(f["name"], xml_info):
        return False
    if lead_type == "pe" and not looks_like_pe_fund(f["name"], xml_info):
        return False

    return True


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
    run_started_at = datetime.now().isoformat(timespec="seconds")
    logger(f"🚀 Starting Form D Lead Pipeline | Days: {days} | Target: {lead_type.upper()}")
    
    # 1. Search EFTS index for Form D filings in the range
    raw_filings = search_form_d_filings(days, logger=logger)
    
    # 2. Filter locally by name keywords
    candidates = filter_filings_by_name(raw_filings, logger=logger)
    
    logger(f"🔍 Found {len(candidates)} candidate filings. Fetching Form D details...")
    
    enriched_leads = []
    history_cache = {}
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
            clean_name = clean_firm_name(firm_name)

            fund_stage = classify_fund_stage(firm_name)

            if not lead_matches_target(c, xml_info, fund_stage, lead_type, min_size, days):
                logger(f"    ↳ Skipped: not a fresh {lead_type.upper()} target.")
                continue

            history = find_manager_history(
                c,
                clean_name,
                xml_info,
                cache=history_cache,
                logger=logger
            )
            manager_assessment = assess_manager_novelty(c, xml_info, fund_stage, history)
            logger(
                f"    ↳ Manager check: {manager_assessment['manager_status']} "
                f"({manager_assessment['manager_confidence']} confidence)"
            )
            
            # Resolve domain only for candidate leads to keep it fast
            domain = discover_domain(clean_name, logger=logger) or ""
            domain_found = bool(domain)
            
            # Calculate fit + freshness scores. Freshness is the primary product signal.
            freshness_score, freshness_reason = calculate_freshness_score(c, xml_info, fund_stage, days)
            score = calculate_vc_score(c, xml_info, domain_found) + freshness_score
            
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
                "signal_type": manager_assessment["manager_status"],
                "firm_name": clean_name,
                "name": firm_name,
                "contact_name": contact_name,
                "contact_title": contact_title,
                **manager_assessment,
                "phone": xml_info["phone"],
                "primary_email": "",
                "domain": domain,
                "address": f"{xml_info['street']}, {xml_info['city']}, {xml_info['state']} {xml_info['zip']}".strip(" ,"),
                "fund_size": off_amt_str,
                "amount_sold": amt_sold_str,
                "year_inc": xml_info["year_inc"],
                "date_of_first_sale": xml_info["date_of_first_sale"],
                "fund_stage": fund_stage,
                "filer_status": "first_filer" if c["form_type"] == "D" else "new_filer",
                "total_filings": "1",
                "platform": "Has website" if domain_found else "No website",
                "site_score": score,  # for dashboard backwards compatibility
                "vc_score": score,
                "freshness_score": freshness_score,
                "freshness_reason": freshness_reason,
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
            logger(
                f"    → Fresh signal! {manager_assessment['manager_status']} | "
                f"Freshness: {freshness_score} | Location: {lead['city']}, {lead['state']}"
            )
            
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
                        row["is_new_since_last_run"] = "no"
                        row["first_seen_at"] = row.get("first_seen_at") or row.get("filing_date") or ""
                        row["last_seen_at"] = row.get("last_seen_at") or row.get("filing_date") or ""
                        row["manager_status_code"] = row.get("manager_status_code") or "not_checked"
                        row["manager_status"] = row.get("manager_status") or "Not checked"
                        row["manager_novelty_score"] = row.get("manager_novelty_score") or "0"
                        row["manager_confidence"] = row.get("manager_confidence") or "Unknown"
                        row["manager_history_reason"] = row.get("manager_history_reason") or "Run the pipeline again to check SEC manager history."
                        all_leads_dict[row["crd"]] = row
        except Exception:
            pass
            
    # Merge new leads
    merged_count = 0
    for lead in enriched_leads:
        cik = lead["crd"]
        existing = all_leads_dict.get(cik)

        if not existing:
            merged_count += 1
            lead["first_seen_at"] = run_started_at
            lead["last_seen_at"] = run_started_at
            lead["is_new_since_last_run"] = "yes"
        else:
            lead["checked"] = existing.get("checked", lead.get("checked", ""))
            lead["first_seen_at"] = existing.get("first_seen_at") or existing.get("last_seen_at") or existing.get("filing_date") or run_started_at
            lead["last_seen_at"] = run_started_at
            lead["is_new_since_last_run"] = "no"

        all_leads_dict[cik] = lead
        
    # Put validated likely-new managers first, then latest-run and freshness signals.
    final_list = list(all_leads_dict.values())
    def get_score_val(x):
        try:
            return int(x.get("vc_score") or x.get("site_score") or 0)
        except (ValueError, TypeError):
            return 0
    def get_sort_val(x):
        manager_priority = {
            "likely_new": 3,
            "needs_review": 2,
            "not_checked": 1,
            "existing_manager": 0,
        }.get(str(x.get("manager_status_code", "")), 1)
        is_new = 1 if str(x.get("is_new_since_last_run", "")).lower() == "yes" else 0
        try:
            manager_novelty = int(x.get("manager_novelty_score") or 0)
        except (ValueError, TypeError):
            manager_novelty = 0
        try:
            freshness = int(x.get("freshness_score") or 0)
        except (ValueError, TypeError):
            freshness = 0
        return (
            manager_priority,
            is_new,
            manager_novelty,
            freshness,
            get_score_val(x),
            x.get("filing_date", "")
        )
    final_list.sort(key=get_sort_val, reverse=True)
    
    # Save CSV
    fieldnames = [
        "checked", "signal_type", "firm_name", "name", "contact_name", "contact_title",
        "is_new_since_last_run", "first_seen_at", "last_seen_at",
        "manager_status_code", "manager_status", "manager_novelty_score", "manager_confidence",
        "manager_history_count", "manager_history_reason", "manager_first_filing_date",
        "manager_history_name", "manager_history_url", "manager_matched_identity",
        "phone", "primary_email", "domain", "address", "fund_size",
        "amount_sold", "year_inc", "date_of_first_sale", "fund_stage", "filer_status",
        "total_filings", "platform", "site_score", "vc_score", "freshness_score",
        "freshness_reason", "issues",
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
    parser.add_argument("--type", type=str, choices=["vc", "pe", "fund2"], default="vc", help="Target firm type")
    parser.add_argument("--output", type=str, default="ALL_VC_LEADS.csv", help="Master output CSV file")
    parser.add_argument("--days", type=int, default=30, help="Days range")
    parser.add_argument("--min-size", type=int, default=0, help="Minimum offering amount")
    args = parser.parse_args()

    run_pipeline(days=args.days, lead_type=args.type, min_size=args.min_size, output_file=args.output)
