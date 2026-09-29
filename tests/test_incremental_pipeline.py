import csv
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from pipeline import (
    assess_manager_novelty,
    build_manager_search_identities,
    classify_fund_stage,
    reassess_saved_lead,
    run_pipeline,
    search_form_d_filings,
    shared_contacts,
)


VC_XML = """<?xml version="1.0"?>
<edgarSubmission>
  <primaryIssuer><yearOfInc><value>{year}</value></yearOfInc></primaryIssuer>
  <offeringData>
    <industryGroup>
      <industryGroupType>{industry}</industryGroupType>
      <investmentFundInfo><investmentFundType>{fund_type}</investmentFundType></investmentFundInfo>
    </industryGroup>
    <typeOfFiling><dateOfFirstSale><yetToOccur>true</yetToOccur></dateOfFirstSale></typeOfFiling>
  </offeringData>
</edgarSubmission>"""

NO_HISTORY = {
    "checked": True, "found": False, "weak_match": False, "count": 0,
    "reason": "No earlier Form D fund filing matched.", "first_filing_date": "",
    "filing_name": "", "filing_url": "", "matched_identity": "",
}


def filing(name, adsh, form="D"):
    return {"name": name, "cik": "0000000001", "adsh": adsh, "xml_filename": "primary_doc.xml",
            "filing_date": datetime.now().strftime("%Y-%m-%d"), "form_type": form, "biz_locations": []}


class FakeResponse:
    status_code = 200

    def __init__(self, hits, total):
        self._data = {"hits": {"hits": hits, "total": {"value": total}}}

    def json(self):
        return self._data


class SearchTests(unittest.TestCase):
    @patch("pipeline.time.sleep")
    @patch("pipeline.requests.get")
    def test_each_day_is_queried_separately(self, get_mock, _sleep):
        get_mock.return_value = FakeResponse([], 0)
        search_form_d_filings(2, logger=lambda _m: None)
        days = [call.kwargs["params"]["startdt"] for call in get_mock.call_args_list]
        self.assertEqual(len(days), 3)
        self.assertEqual(len(set(days)), 3)
        for call in get_mock.call_args_list:
            self.assertEqual(call.kwargs["params"]["startdt"], call.kwargs["params"]["enddt"])


class IncrementalRunTests(unittest.TestCase):
    @patch("pipeline.find_manager_history", return_value=NO_HISTORY)
    @patch("pipeline.fetch_form_d_xml")
    @patch("pipeline.search_form_d_filings")
    def test_filings_are_read_once_and_names_do_not_filter(self, search_mock, fetch_mock, _history):
        year = datetime.now().year
        search_mock.return_value = [
            filing("HighPost Galaxy Fund, L.P.", "a-1"),   # no "venture"/"capital" in the name
            filing("Sunny Acres Apartments LLC", "a-2"),   # real estate
            filing("HighPost Galaxy Fund, L.P.", "a-3", form="D/A"),
        ]
        fetch_mock.side_effect = lambda cik, adsh, *_a, **_k: VC_XML.format(
            year=year,
            industry="Pooled Investment Fund" if adsh == "a-1" else "Residential",
            fund_type="Venture Capital Fund" if adsh == "a-1" else "Unknown",
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "leads.csv")
            run_pipeline(output_file=output, logger=lambda _m: None)
            self.assertEqual(fetch_mock.call_count, 2)  # the amendment is never downloaded
            with open(output, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            with open(os.path.join(tmp, "SEC_SEEN_FILINGS.json"), encoding="utf-8") as f:
                seen = json.load(f)

            run_pipeline(output_file=output, logger=lambda _m: None)
            self.assertEqual(fetch_mock.call_count, 2)  # nothing downloaded twice

        self.assertEqual([row["name"] for row in rows], ["HighPost Galaxy Fund, L.P."])
        self.assertIn("a-2", seen)


class SharedContactTests(unittest.TestCase):
    def test_admin_phone_is_not_a_history_identity(self):
        rows = [{"phone": "(360) 340-9337", "address": ""} for _ in range(5)]
        skip = shared_contacts(rows)
        identities = build_manager_search_identities(
            "Athenaeum Fund I LP", "Athenaeum", {"phone": "360-340-9337", "related_people": []}, skip
        )
        self.assertNotIn("phone", [identity["kind"] for identity in identities])


class FounderSpinoutTests(unittest.TestCase):
    def person_history(self, prior_name):
        return {**NO_HISTORY, "found": True, "matched_kind": "person", "matched_identity": "Morgan Beller",
                "filing_name": prior_name, "first_filing_date": "2021-04-14",
                "reason": "Prior fund filing matched person 'Morgan Beller'."}

    def test_partner_from_another_firm_is_a_new_firm(self):
        info = {"year_inc": str(datetime.now().year), "industry_group": "Pooled Investment Fund",
                "investment_fund_type": "Venture Capital Fund"}
        verdict = assess_manager_novelty({"name": "Decimal Capital Fund I LP"}, info, "Fund I",
                                         self.person_history("NFX C2-B Advantage, LP"))
        self.assertEqual(verdict["manager_status_code"], "likely_new")
        self.assertEqual(verdict["manager_status"], "Likely new firm, experienced founder")

    def test_same_brand_stays_existing(self):
        info = {"year_inc": str(datetime.now().year), "industry_group": "Pooled Investment Fund",
                "investment_fund_type": "Venture Capital Fund"}
        verdict = assess_manager_novelty({"name": "Altimeter Catskill Fund I, L.P."}, info, "Fund I",
                                         self.person_history("Altimeter Private SPV I, L.P."))
        self.assertEqual(verdict["manager_status_code"], "existing_manager")

    def test_saved_row_is_reclassified(self):
        row = {
            "name": "Decimal Capital Fund I LP", "manager_status_code": "existing_manager",
            "manager_history_reason": "Prior fund filing matched person 'Morgan Beller': NFX C2-B Advantage, LP (2021-04-14).",
            "manager_history_name": "NFX C2-B Advantage, LP", "manager_matched_identity": "Morgan Beller",
            "issues": "Pooled Investment Fund - Venture Capital Fund", "year_inc": str(datetime.now().year),
        }
        self.assertEqual(reassess_saved_lead(row)["manager_status_code"], "likely_new")


class VehicleNameTests(unittest.TestCase):
    def test_share_classes_and_sub_vehicles_are_not_new_firms(self):
        from pipeline import FUND_VEHICLE_PATTERN
        for name in ["Imagine Access Fund LLC - Series I", "Sense Feeder LP",
                     "ARMRA Capital Partners Growth-A, LP", "Oregon Venture Fund 2027-Q, LLC"]:
            self.assertTrue(FUND_VEHICLE_PATTERN.search(name), name)
        for name in ["Atomus Fund I, L.P.", "Decimal Capital Fund I LP", "BL.vc Deep Tech 1, L.P."]:
            self.assertFalse(FUND_VEHICLE_PATTERN.search(name), name)


class LaterFundTests(unittest.TestCase):
    def test_numerals_in_the_middle_of_the_name(self):
        for name in ["Bessemer Venture Partners XIII L.P.", "Lux Ventures VIII-A, L.P.",
                     "First Round Capital X-F, L.P.", "Sparrow Capital III Trust",
                     "SCP Opportunity CXXXIII LP"]:
            self.assertEqual(classify_fund_stage(name), "Later Fund", name)

    def test_brand_numerals_and_first_funds(self):
        self.assertEqual(classify_fund_stage("XL Ventures Fund I"), "Fund I")
        self.assertEqual(classify_fund_stage("VI Capital Fund"), "Emerging Fund")


if __name__ == "__main__":
    unittest.main()
