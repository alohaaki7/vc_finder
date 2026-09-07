#!/usr/bin/env python3
"""Collect free, early VC-manager signals from the public IAPD Form ADV feed.

This module intentionally emits *signals*, not qualified Alamat leads.  A legal
adviser name still has to be resolved to an operating firm, a current decision
maker, and a weak public presence before it belongs in the lead pipeline.
"""

from __future__ import annotations

import argparse
import csv
import json
import gzip
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Iterable
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import requests


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = SCRIPT_DIR / "ALAMAT_ADV_SIGNALS.csv"
DEFAULT_SNAPSHOT = SCRIPT_DIR / "ALAMAT_ADV_SNAPSHOT.csv"
FEED_TEMPLATE = (
    "https://reports.adviserinfo.sec.gov/reports/CompilationReports/"
    "IA_FIRM_SEC_Feed_{stamp}.xml.gz"
)
USER_AGENT = "AlamatStudio-ADV-Research/1.0 (public regulatory data)"

OUTPUT_FIELDS = [
    "record_type",
    "adv_id",
    "legal_adviser_name",
    "business_name",
    "crd_number",
    "sec_number",
    "firm_type",
    "registration_status",
    "registration_date",
    "last_adv_filing_date",
    "city",
    "state",
    "country",
    "phone",
    "reported_website",
    "vc_signal_strength",
    "vc_signal_reason",
    "new_to_snapshot",
    "feed_date",
    "source_url",
    "iapd_url",
    "linkedin_company_search_url",
    "linkedin_people_search_url",
    "website_search_url",
    "verification_status",
    "freshness_bucket",
]

VENTURE_NAME_RE = re.compile(r"\b(?:venture|ventures|venture\s+capital|vc)\b", re.I)


def _tag_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    for candidate in element:
        if _tag_name(candidate) == name:
            return candidate
    return None


def _descendant(element: ET.Element, name: str) -> ET.Element | None:
    for candidate in element.iter():
        if _tag_name(candidate) == name:
            return candidate
    return None


def _iso_date(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def _reported_website(firm: ET.Element) -> str:
    web_addresses = _descendant(firm, "WebAddrs")
    if web_addresses is None:
        return ""
    for candidate in web_addresses:
        if _tag_name(candidate) == "WebAddr" and (candidate.text or "").strip():
            return (candidate.text or "").strip()
    return ""


def parse_adv_xml(source: str | os.PathLike | BinaryIO) -> tuple[list[dict], str]:
    """Stream the official IAPD compilation XML into a compact firm list."""
    firms: list[dict] = []
    feed_date = ""
    for event, element in ET.iterparse(source, events=("start", "end")):
        tag = _tag_name(element)
        if event == "start" and tag == "IAPDFirmSECReport":
            feed_date = _iso_date(element.attrib.get("GenOn", ""))
            continue
        if event != "end" or tag != "Firm":
            continue

        info = _child(element, "Info")
        address = _child(element, "MainAddr")
        registration = _child(element, "Rgstn")
        filing = _child(element, "Filing")
        item2b = _descendant(element, "Item2B")
        if info is None:
            element.clear()
            continue

        firms.append({
            "business_name": info.attrib.get("BusNm", "").strip(),
            "legal_adviser_name": info.attrib.get("LegalNm", "").strip(),
            "crd_number": info.attrib.get("FirmCrdNb", "").strip(),
            "sec_number": info.attrib.get("SECNb", "").strip(),
            "firm_type": registration.attrib.get("FirmType", "").strip() if registration is not None else "",
            "registration_status": registration.attrib.get("St", "").strip() if registration is not None else "",
            "registration_date": _iso_date(registration.attrib.get("Dt", "")) if registration is not None else "",
            "last_adv_filing_date": _iso_date(filing.attrib.get("Dt", "")) if filing is not None else "",
            "city": address.attrib.get("City", "").strip() if address is not None else "",
            "state": address.attrib.get("State", "").strip() if address is not None else "",
            "country": address.attrib.get("Cntry", "").strip() if address is not None else "",
            "phone": address.attrib.get("PhNb", "").strip() if address is not None else "",
            "reported_website": _reported_website(element),
            "venture_exemption": (item2b.attrib.get("Q2B1", "") if item2b is not None else "").upper() == "Y",
            "private_fund_exemption": (item2b.attrib.get("Q2B2", "") if item2b is not None else "").upper() == "Y",
        })
        element.clear()
    return firms, feed_date


def classify_adv_signal(firm: dict) -> tuple[str, str] | tuple[None, None]:
    """Return an evidence label without asserting that the adviser is a lead."""
    if firm.get("venture_exemption"):
        return (
            "explicit_venture_exemption",
            "Form ADV Item 2.B.(1) says the adviser relies on the venture-capital-fund adviser exemption.",
        )

    names = " ".join([
        str(firm.get("business_name") or ""),
        str(firm.get("legal_adviser_name") or ""),
    ])
    if VENTURE_NAME_RE.search(names) and firm.get("private_fund_exemption"):
        return (
            "possible_venture_manager",
            "The adviser reports the private-fund exemption and its filed name contains an explicit venture/VC term.",
        )
    if VENTURE_NAME_RE.search(names):
        return (
            "possible_venture_manager",
            "The filed adviser name contains an explicit venture/VC term; strategy still needs verification.",
        )
    if firm.get("firm_type", "").upper() == "ERA" or firm.get("private_fund_exemption"):
        return "strategy_unresolved", "Private-fund adviser; VC strategy has not been established. Retained for strategy review."
    return None, None


def _date_on_or_after(value: str, cutoff: date) -> bool:
    try:
        return date.fromisoformat(value) >= cutoff
    except (TypeError, ValueError):
        return False


def load_snapshot(path: str | os.PathLike) -> set[str]:
    snapshot_path = Path(path)
    if not snapshot_path.exists():
        return set()
    with snapshot_path.open("r", encoding="utf-8", newline="") as handle:
        return {str(row.get("crd_number") or "").strip() for row in csv.DictReader(handle)} - {""}


def build_signal_rows(
    firms: Iterable[dict],
    *,
    feed_date: str,
    source_url: str,
    previous_crds: set[str] | None = None,
    as_of: date | None = None,
    days: int = 180,
) -> list[dict]:
    """Keep recent or newly-seen ADV records with explicit VC evidence."""
    previous_crds = previous_crds or set()
    has_baseline = bool(previous_crds)
    as_of = as_of or date.today()
    cutoff = as_of - timedelta(days=max(days, 0))
    rows: list[dict] = []

    for firm in firms:
        strength, reason = classify_adv_signal(firm)
        if not strength:
            continue
        crd = str(firm.get("crd_number") or "").strip()
        new_to_snapshot = has_baseline and bool(crd) and crd not in previous_crds
        recent_registration = _date_on_or_after(firm.get("registration_date", ""), cutoff)
        recent_filing_without_registration = (
            not firm.get("registration_date")
            and _date_on_or_after(firm.get("last_adv_filing_date", ""), cutoff)
        )
        freshness_bucket = "recent_registration" if recent_registration else (
            "date_unknown" if not firm.get("registration_date") else "older_registration")

        business_name = str(firm.get("business_name") or firm.get("legal_adviser_name") or "").strip()
        legal_name = str(firm.get("legal_adviser_name") or business_name).strip()
        firm_query = business_name or legal_name
        iapd_url = f"https://adviserinfo.sec.gov/firm/summary/{quote_plus(crd)}" if crd else "https://adviserinfo.sec.gov/firm/index.html"
        linkedin_query = quote_plus(firm_query)
        people_query = quote_plus(f'{firm_query} founder "general partner" "managing partner"')
        rows.append({
            "record_type": "adv_signal",
            "adv_id": f"adv-{crd or len(rows)}",
            "legal_adviser_name": legal_name,
            "business_name": business_name,
            "crd_number": crd,
            "sec_number": firm.get("sec_number", ""),
            "firm_type": firm.get("firm_type", ""),
            "registration_status": firm.get("registration_status", ""),
            "registration_date": firm.get("registration_date", ""),
            "last_adv_filing_date": firm.get("last_adv_filing_date", ""),
            "city": firm.get("city", ""),
            "state": firm.get("state", ""),
            "country": firm.get("country", ""),
            "phone": firm.get("phone", ""),
            "reported_website": firm.get("reported_website", ""),
            "vc_signal_strength": strength,
            "vc_signal_reason": reason,
            "new_to_snapshot": "yes" if new_to_snapshot else "no",
            "feed_date": feed_date,
            "source_url": source_url,
            "iapd_url": iapd_url,
            "linkedin_company_search_url": f"https://www.linkedin.com/search/results/companies/?keywords={linkedin_query}",
            "linkedin_people_search_url": f"https://www.linkedin.com/search/results/people/?keywords={people_query}",
            "website_search_url": f"https://www.google.com/search?q={quote_plus(firm_query + ' venture capital')}",
            "verification_status": "unresolved",
            "freshness_bucket": freshness_bucket,
        })

    rows.sort(key=lambda row: (
        row.get("new_to_snapshot") == "yes",
        row.get("registration_date", ""),
        row.get("last_adv_filing_date", ""),
    ), reverse=True)
    return rows


def _atomic_write_csv(path: str | os.PathLike, rows: Iterable[dict], fields: list[str]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=output_path.name, suffix=".tmp", dir=output_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_name, output_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def download_latest_feed(days_back: int = 12) -> tuple[Path, str]:
    """Download the newest available dated public IAPD feed."""
    errors: list[str] = []
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/gzip, application/xml"})
    for offset in range(max(days_back, 1)):
        candidate_date = date.today() - timedelta(days=offset)
        url = FEED_TEMPLATE.format(stamp=candidate_date.strftime("%m_%d_%Y"))
        try:
            response = session.get(url, timeout=(10, 120), allow_redirects=False)
            if response.status_code != 200:
                location = response.headers.get("Location", "")
                errors.append(f"{candidate_date.isoformat()}: HTTP {response.status_code}{' -> ' + location if location else ''}")
                if response.status_code in (401, 403) or "waf" in location.lower():
                    raise RuntimeError("Official IAPD access blocked: " + errors[-1])
                continue
            if not response.content.startswith(b"\x1f\x8b"):
                errors.append(f"{candidate_date.isoformat()}: response was not gzip data")
                continue
            fd, temp_name = tempfile.mkstemp(prefix="alamat-adv-", suffix=".xml.gz")
            with os.fdopen(fd, "wb") as handle:
                handle.write(response.content)
            return Path(temp_name), url
        except requests.RequestException as exc:
            errors.append(f"{candidate_date.isoformat()}: {exc}")
    raise RuntimeError("Official IAPD feed unavailable. " + " | ".join(errors[-3:]))


def collect_adv_signals(
    *,
    source: str | os.PathLike | None = None,
    output: str | os.PathLike = DEFAULT_OUTPUT,
    snapshot: str | os.PathLike = DEFAULT_SNAPSHOT,
    days: int = 180,
) -> dict:
    """Fetch/parse ADV data, write candidate signals, then update the CRD baseline."""
    downloaded_path: Path | None = None
    if source:
        feed_path = Path(source)
        source_url = str(feed_path)
    else:
        downloaded_path, source_url = download_latest_feed()
        feed_path = downloaded_path

    try:
        opener = gzip.open if str(feed_path).endswith(".gz") else open
        with opener(feed_path, "rb") as handle:
            firms, feed_date = parse_adv_xml(handle)
        if not firms or not feed_date or any(not firm.get('crd_number') for firm in firms):
            raise ValueError("Invalid or empty ADV feed; existing data preserved")
        previous_crds = load_snapshot(snapshot)
        rows = build_signal_rows(
            firms,
            feed_date=feed_date,
            source_url=source_url,
            previous_crds=previous_crds,
            days=days,
        )
        _atomic_write_csv(output, rows, OUTPUT_FIELDS)
        _atomic_write_csv(
            snapshot,
            ({"crd_number": firm.get("crd_number", "")} for firm in firms if firm.get("crd_number")),
            ["crd_number"],
        )
        metadata = {"total_firms_scanned": len(firms), "signals_written": len(rows),
                    "feed_date": feed_date, "source_url": source_url,
                    "counts": {key: sum(r['vc_signal_strength'] == key for r in rows)
                               for key in ('explicit_venture_exemption', 'possible_venture_manager', 'strategy_unresolved')}}
        Path(output).with_suffix('.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        return {
            "total_firms_scanned": len(firms),
            "signals_written": len(rows),
            "feed_date": feed_date,
            "source_url": source_url,
            "output": str(output),
        }
    finally:
        if downloaded_path:
            downloaded_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="Optional local IAPD XML or XML.GZ file")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    parser.add_argument("--days", type=int, default=180, help="First-run registration lookback")
    args = parser.parse_args()
    result = collect_adv_signals(
        source=args.source,
        output=args.output,
        snapshot=args.snapshot,
        days=args.days,
    )
    print(
        f"ADV scan complete: {result['signals_written']} signals from "
        f"{result['total_firms_scanned']} firms (feed {result['feed_date'] or 'unknown'})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
