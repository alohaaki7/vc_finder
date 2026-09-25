"""Display-time signals that help spot new VC managers early.

- `early_signal`: whether the fund has taken money yet, from the Form D first-sale date.
- `sec_people`: the human names listed on a Form D, skipping GP and management entities.
- `AdvIndex`: links a Form D lead to an SEC adviser (Form ADV) registration.

These are research aids. A match is a pointer to check, not a verified identity.
"""

import csv
import html
import os
import re
from datetime import datetime

from pipeline import (
    extract_related_name,
    is_entity_identity,
    manager_brand_tokens,
    normalize_phone,
)


JUST_RAISED_DAYS = 30


def early_signal(row):
    """Return (code, label, rank) for how early the fund is in raising money."""
    first_sale = str(row.get("date_of_first_sale") or "").strip()
    if not first_sale or first_sale.lower().startswith("yet"):
        return "not_raised", "Not raised yet", 2
    try:
        sale = datetime.strptime(first_sale[:10], "%Y-%m-%d")
        filed = datetime.strptime(str(row.get("filing_date") or "")[:10], "%Y-%m-%d")
    except ValueError:
        return "", "", 0
    if 0 <= (filed - sale).days <= JUST_RAISED_DAYS:
        return "just_raised", "Just raised", 1
    return "", "", 0


def clean_person_name(raw):
    name = html.unescape(extract_related_name(raw))
    name = re.sub(r"^(?:n/?a|none(?:\s+none)?|general partner(?: of the general partner)?|management company)\s+",
                  "", name, flags=re.IGNORECASE)
    if not name or "&" in name or is_entity_identity(name) or len(name.split()) < 2:
        return ""
    return name.title() if name.isupper() else name


def sec_people(row, limit=4):
    """Human names from the Form D contact and related-person list, in filing order."""
    people = []
    for raw in [row.get("contact_name", "")] + str(row.get("all_contacts") or "").split(";"):
        name = clean_person_name(raw)
        if name and name.lower() not in {p.lower() for p in people}:
            people.append(name)
    return people[:limit]


def manager_names(row, brand):
    """Names that may identify the manager: the brand plus GP/management entities."""
    names = [brand] if brand else []
    for raw in str(row.get("all_contacts") or "").split(";"):
        name = html.unescape(extract_related_name(raw))
        if name and is_entity_identity(name):
            names.append(name)
    return names


class AdvIndex:
    """Lookup of SEC adviser registrations by phone and by brand token."""

    def __init__(self, rows):
        self.rows = rows
        self.by_phone = {}
        self.by_token = {}
        for row in rows:
            phone = normalize_phone(row.get("phone"))
            if len(phone) == 10:
                self.by_phone.setdefault(phone, []).append(row)
            for token in set(manager_brand_tokens(adv_name(row))):
                if len(token) >= 4:
                    self.by_token.setdefault(token, []).append(row)

    @classmethod
    def from_csv(cls, path):
        if not os.path.exists(path):
            return cls([])
        with open(path, encoding="utf-8", newline="") as f:
            return cls(list(csv.DictReader(f)))

    def match(self, lead, brand, phone_counts=None):
        """Return the best adviser match for a Form D lead, or None.

        A phone shared by several advisers or several Form D filings usually belongs
        to a fund administrator or law firm, so it only counts when it is unique.
        """
        names = manager_names(lead, brand)
        phone = normalize_phone(lead.get("phone"))
        phone_rows = self.by_phone.get(phone, []) if len(phone) == 10 else []
        phone_unique = len(phone_rows) == 1 and (phone_counts or {}).get(phone, 1) <= 2

        candidates = {}
        for name in names:
            for token in set(manager_brand_tokens(name)):
                for row in self.by_token.get(token, []):
                    candidates[row.get("crd_number")] = row
        if phone_unique:
            candidates[phone_rows[0].get("crd_number")] = phone_rows[0]

        best = None
        for row in candidates.values():
            name_match = any(same_brand(name, adv_name(row)) for name in names)
            phone_match = phone_unique and normalize_phone(row.get("phone")) == phone
            if not (name_match or phone_match):
                continue
            basis = "name and phone" if name_match and phone_match else ("name" if name_match else "phone")
            if name_match and not phone_match and not same_state(lead, row):
                continue
            rank = (name_match and phone_match, row.get("registration_date", ""))
            if best is None or rank > best[0]:
                best = (rank, row, basis)
        if not best:
            return None
        row, basis = best[1], best[2]
        return {
            "adv_name": adv_name(row),
            "adv_crd": row.get("crd_number", ""),
            "adv_registration_date": row.get("registration_date", ""),
            "adv_firm_type": row.get("firm_type", ""),
            "adv_website": row.get("reported_website", ""),
            "adv_url": row.get("iapd_url", ""),
            "adv_match_basis": basis,
        }


def adv_name(row):
    return row.get("business_name") or row.get("legal_adviser_name") or ""


def same_state(lead, adv_row):
    lead_state = str(lead.get("state") or "").strip().upper()
    adv_state = str(adv_row.get("state") or "").strip().upper()
    return not lead_state or not adv_state or lead_state == adv_state


def same_brand(first, second):
    """True when both names reduce to the same distinctive words, e.g.
    "WYO VC Frontier Fund I GP" and "WYO VC Management I LLC" do not, but
    "Lightcone Venture Capital I GP LLC" and "Lightcone Ventures" do."""
    tokens = set(manager_brand_tokens(first))
    return bool(tokens) and any(len(t) >= 3 for t in tokens) and tokens == set(manager_brand_tokens(second))
