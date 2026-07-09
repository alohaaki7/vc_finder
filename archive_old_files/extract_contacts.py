#!/usr/bin/env python3
"""
SEC Form D Contact Extractor
Fetches the actual Form D XML filing for each lead and extracts:
- Phone number
- Contact person name + title
- Address
- Year of incorporation
- Offering amount (fund size)
"""

import requests
import csv
import time
import re
import sys
from xml.etree import ElementTree

HEADERS = {"User-Agent": "VCLeadFinder contact@example.com", "Accept": "application/json"}


def get_filing_xml(cik):
    """Get the Form D XML content for a CIK."""
    # Get filing info
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()

        recent = data.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        if not accessions:
            return None

        acc = accessions[0].replace("-", "")
        idx_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/"

        r2 = requests.get(idx_url + "index.json", headers=HEADERS, timeout=15)
        if r2.status_code != 200:
            return None

        idx = r2.json()
        files = idx.get("directory", {}).get("item", [])
        for f_item in files:
            name = f_item.get("name", "")
            if name.endswith(".xml") and "primary" in name.lower():
                xml_url = f"{idx_url}{name}"
                r3 = requests.get(xml_url, headers=HEADERS, timeout=15)
                if r3.status_code == 200:
                    return r3.text
            elif name == "primary_doc.xml":
                xml_url = f"{idx_url}{name}"
                r3 = requests.get(xml_url, headers=HEADERS, timeout=15)
                if r3.status_code == 200:
                    return r3.text

        # Try any XML file
        for f_item in files:
            name = f_item.get("name", "")
            if name.endswith(".xml"):
                xml_url = f"{idx_url}{name}"
                r3 = requests.get(xml_url, headers=HEADERS, timeout=15)
                if r3.status_code == 200:
                    return r3.text

    except Exception as e:
        pass

    return None


def parse_form_d(xml_text):
    """Parse Form D XML and extract contact info."""
    info = {
        "phone": "",
        "contact_first": "",
        "contact_last": "",
        "contact_title": "",
        "street": "",
        "city": "",
        "state": "",
        "zip": "",
        "year_inc": "",
        "offering_amount": "",
        "amount_sold": "",
        "industry": "",
        "all_persons": [],
    }

    try:
        # Use regex since XML namespaces can be tricky
        phone = re.search(r"<issuerPhoneNumber>([^<]+)</issuerPhoneNumber>", xml_text)
        if phone:
            info["phone"] = phone.group(1).strip()

        # Year of incorporation
        year = re.search(r"<yearOfInc>.*?<value>(\d{4})</value>.*?</yearOfInc>", xml_text, re.DOTALL)
        if year:
            info["year_inc"] = year.group(1)

        # Offering amount
        amount = re.search(r"<totalOfferingAmount>(\d+)</totalOfferingAmount>", xml_text)
        if amount:
            val = int(amount.group(1))
            if val >= 1_000_000_000:
                info["offering_amount"] = f"${val/1_000_000_000:.1f}B"
            elif val >= 1_000_000:
                info["offering_amount"] = f"${val/1_000_000:.0f}M"
            elif val > 0:
                info["offering_amount"] = f"${val:,}"

        sold = re.search(r"<totalAmountSold>(\d+)</totalAmountSold>", xml_text)
        if sold:
            val = int(sold.group(1))
            if val >= 1_000_000:
                info["amount_sold"] = f"${val/1_000_000:.0f}M"
            elif val > 0:
                info["amount_sold"] = f"${val:,}"

        # Industry
        ind = re.search(r"<industryGroupType>([^<]+)</industryGroupType>", xml_text)
        if ind:
            info["industry"] = ind.group(1).strip()

        # Issuer address
        addr_block = re.search(r"<issuerAddress>(.*?)</issuerAddress>", xml_text, re.DOTALL)
        if addr_block:
            block = addr_block.group(1)
            s = re.search(r"<street1>([^<]+)</street1>", block)
            c = re.search(r"<city>([^<]+)</city>", block)
            st = re.search(r"<stateOrCountry>([^<]+)</stateOrCountry>", block)
            z = re.search(r"<zipCode>([^<]+)</zipCode>", block)
            if s: info["street"] = s.group(1)
            if c: info["city"] = c.group(1)
            if st: info["state"] = st.group(1)
            if z: info["zip"] = z.group(1)

        # Related persons (partners/directors)
        persons = re.findall(
            r"<relatedPersonInfo>(.*?)</relatedPersonInfo>", xml_text, re.DOTALL
        )
        for p in persons:
            first = re.search(r"<firstName>([^<]+)</firstName>", p)
            last = re.search(r"<lastName>([^<]+)</lastName>", p)
            rels = re.findall(r"<relationship>([^<]+)</relationship>", p)

            if first and last:
                name = f"{first.group(1).strip()} {last.group(1).strip()}"
                role = ", ".join(rels) if rels else ""
                info["all_persons"].append({"name": name, "role": role})

                # Use the first person as primary contact
                if not info["contact_first"]:
                    info["contact_first"] = first.group(1).strip()
                    info["contact_last"] = last.group(1).strip()

        # Signer info (fallback)
        signer = re.search(r"<nameOfSigner>([^<]+)</nameOfSigner>", xml_text)
        title = re.search(r"<signatureTitle>([^<]+)</signatureTitle>", xml_text)
        if title:
            info["contact_title"] = title.group(1).strip()
        if signer and not info["contact_first"]:
            parts = signer.group(1).strip().split()
            if len(parts) >= 2:
                info["contact_first"] = parts[0]
                info["contact_last"] = " ".join(parts[1:])

    except Exception as e:
        pass

    return info


def format_phone(phone):
    """Format phone number nicely."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return phone


def main():
    input_file = sys.argv[1] if len(sys.argv) > 1 else "vc_leads_december.csv"
    output_file = input_file.replace(".csv", "_contacts.csv")

    print("=" * 70)
    print(f"SEC Form D Contact Extractor")
    print(f"Input: {input_file}")
    print("=" * 70)

    with open(input_file, "r") as f:
        leads = list(csv.DictReader(f))

    print(f"Loaded {len(leads)} leads\n")

    results = []
    phones_found = 0
    people_found = 0

    for i, lead in enumerate(leads, 1):
        cik = lead.get("cik", "")
        firm = lead.get("firm_name", "")

        xml = get_filing_xml(cik)
        if xml:
            info = parse_form_d(xml)
        else:
            info = {"phone": "", "contact_first": "", "contact_last": "",
                    "contact_title": "", "street": "", "city": "", "state": "",
                    "zip": "", "year_inc": "", "offering_amount": "",
                    "amount_sold": "", "industry": "", "all_persons": []}

        # Merge
        lead["phone"] = format_phone(info["phone"]) if info["phone"] else ""
        lead["contact_name"] = f"{info['contact_first']} {info['contact_last']}".strip()
        lead["contact_title"] = info["contact_title"]
        lead["address"] = f"{info['street']}, {info['city']}, {info['state']} {info['zip']}".strip(", ")
        lead["year_incorporated"] = info["year_inc"]
        lead["fund_size"] = info["offering_amount"]
        lead["amount_sold"] = info["amount_sold"]
        lead["industry"] = info["industry"]

        # All persons
        persons_str = "; ".join([f"{p['name']} ({p['role']})" for p in info["all_persons"]])
        lead["all_contacts"] = persons_str

        results.append(lead)

        if info["phone"]:
            phones_found += 1
        if info["contact_first"]:
            people_found += 1

        icon = "📞" if info["phone"] else "  "
        print(f"{icon} {i}/{len(leads)} | {firm} | {lead['contact_name']} | {lead['phone']} | {lead['fund_size']}")

        time.sleep(0.3)  # Rate limiting

    # Save
    fieldnames = [
        "checked", "firm_name", "name", "contact_name", "contact_title",
        "phone", "address", "fund_size", "amount_sold", "year_incorporated",
        "industry", "all_contacts", "fund_stage", "filing_date", "cik", "filing_url"
    ]

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)

    print(f"\n{'='*70}")
    print(f"✓ RESULTS:")
    print(f"  📞 Phone numbers found: {phones_found}/{len(leads)}")
    print(f"  👤 Contact names found: {people_found}/{len(leads)}")
    print(f"  ✓ Saved to: {output_file}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
