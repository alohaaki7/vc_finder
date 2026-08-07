import csv
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from pipeline import (
    assess_manager_novelty,
    build_manager_search_identities,
    clean_firm_name,
    find_manager_history,
    run_pipeline,
)


PRIOR_SUNCOAST_XML = """<?xml version="1.0"?>
<edgarSubmission>
  <primaryIssuer>
    <issuerAddress>
      <street1>348 OLIVE STREET</street1>
      <city>SAN DIEGO</city>
      <stateOrCountry>CA</stateOrCountry>
      <zipCode>92103</zipCode>
    </issuerAddress>
    <issuerPhoneNumber>5039012555</issuerPhoneNumber>
    <yearOfInc><value>2021</value></yearOfInc>
  </primaryIssuer>
  <relatedPersonsList>
    <relatedPersonInfo>
      <relatedPersonName>
        <firstName>-</firstName>
        <lastName>Suncoast Ventures Management, LLC</lastName>
      </relatedPersonName>
      <relatedPersonRelationshipList>
        <relationship>Executive Officer</relationship>
      </relatedPersonRelationshipList>
    </relatedPersonInfo>
  </relatedPersonsList>
  <offeringData>
    <industryGroup>
      <industryGroupType>Pooled Investment Fund</industryGroupType>
      <investmentFundInfo>
        <investmentFundType>Venture Capital Fund</investmentFundType>
      </investmentFundInfo>
    </industryGroup>
  </offeringData>
</edgarSubmission>
"""


class ManagerHistoryTests(unittest.TestCase):
    def setUp(self):
        self.current_filing = {
            "name": "Suncoast Ventures Fund II, LP",
            "cik": "0002138723",
            "adsh": "0002138723-26-000001",
            "filing_date": "2026-06-18",
        }
        self.current_info = {
            "related_people": [
                "Suncoast Ventures GP II, LLC (Executive Officer)",
                "Suncoast Ventures Management, LLC (Executive Officer)",
                "Genevieve Vaschetto (Executive Officer, Director)",
            ],
            "phone": "5039012555",
            "street": "1212 BROADWAY PLAZA SUITE 2100",
            "zip": "94596",
            "year_inc": "2026",
        }

    def test_builds_manager_name_before_supporting_identifiers(self):
        identities = build_manager_search_identities(
            self.current_filing["name"],
            clean_firm_name(self.current_filing["name"]),
            self.current_info,
        )

        self.assertEqual(identities[0], {"kind": "manager_name", "value": "Suncoast Ventures"})
        self.assertIn(
            {"kind": "manager_entity", "value": "Suncoast Ventures Management, LLC"},
            identities,
        )

    @patch("pipeline.fetch_form_d_xml", return_value=PRIOR_SUNCOAST_XML)
    @patch("pipeline.search_prior_form_d_filings")
    def test_finds_prior_fund_from_manager_identity(self, search_mock, _fetch_mock):
        search_mock.return_value = ([{
            "_id": "0001901959-22-000001:primary_doc.xml",
            "_source": {
                "ciks": ["0001901959"],
                "display_names": ["Suncoast Ventures Fund I, LP (CIK 0001901959)"],
                "file_date": "2022-01-06",
                "form": "D",
                "adsh": "0001901959-22-000001",
            },
        }], True)

        history = find_manager_history(
            self.current_filing,
            clean_firm_name(self.current_filing["name"]),
            self.current_info,
            logger=lambda _message: None,
        )
        assessment = assess_manager_novelty(
            self.current_filing,
            self.current_info,
            "Fund II",
            history,
        )

        self.assertTrue(history["found"])
        self.assertEqual(history["first_filing_date"], "2022-01-06")
        self.assertEqual(assessment["manager_status_code"], "existing_manager")
        self.assertEqual(assessment["manager_confidence"], "High")

    def test_clean_fund_i_with_no_history_is_likely_new(self):
        history = {
            "checked": True,
            "found": False,
            "weak_match": False,
            "count": 0,
            "reason": "No earlier Form D fund filing matched.",
        }
        filing = {"name": "Blank Space Ventures Fund I, LP"}
        info = {"year_inc": "2026"}

        assessment = assess_manager_novelty(filing, info, "Fund I", history)

        self.assertEqual(assessment["manager_status_code"], "likely_new")
        self.assertEqual(assessment["manager_novelty_score"], 95)

    def test_incomplete_history_check_stays_in_review(self):
        history = {
            "checked": False,
            "found": False,
            "weak_match": False,
            "count": 0,
            "reason": "Manager history search was incomplete; keep this lead in review.",
        }
        filing = {"name": "Blank Space Ventures Fund I, LP"}
        info = {"year_inc": "2026"}

        assessment = assess_manager_novelty(filing, info, "Fund I", history)

        self.assertEqual(assessment["manager_status_code"], "needs_review")

    def test_series_vehicle_is_not_promoted_to_new_firm(self):
        history = {
            "checked": True,
            "found": False,
            "weak_match": False,
            "count": 0,
            "reason": "No earlier Form D fund filing matched.",
        }
        filing = {
            "name": "BU-0421 Fund I, a series of Freedom Fund Venture Capital, LP"
        }
        info = {"year_inc": "2026"}

        assessment = assess_manager_novelty(filing, info, "Fund I", history)

        self.assertEqual(assessment["manager_status_code"], "needs_review")
        self.assertIn("Series/SPV", assessment["manager_history_reason"])

    @patch("pipeline.find_manager_history")
    @patch("pipeline.fetch_form_d_xml")
    @patch("pipeline.search_form_d_filings")
    def test_pipeline_writes_manager_verdict_and_preserves_seen_state(
        self,
        search_mock,
        fetch_mock,
        history_mock,
    ):
        filing_date = datetime.now().strftime("%Y-%m-%d")
        search_mock.return_value = [{
            "name": "Blank Space Ventures Fund I, LP",
            "cik": "0002106284",
            "adsh": "0002106284-26-000003",
            "xml_filename": "primary_doc.xml",
            "filing_date": filing_date,
            "form_type": "D",
            "biz_locations": ["San Francisco, CA"],
        }]
        fetch_mock.return_value = f"""<?xml version="1.0"?>
<edgarSubmission>
  <primaryIssuer>
    <issuerAddress>
      <street1>2931 Scott Street</street1>
      <city>San Francisco</city>
      <stateOrCountry>CA</stateOrCountry>
      <zipCode>94123</zipCode>
    </issuerAddress>
    <issuerPhoneNumber>4153223392</issuerPhoneNumber>
    <yearOfInc><value>{datetime.now().year}</value></yearOfInc>
  </primaryIssuer>
  <relatedPersonsList>
    <relatedPersonInfo>
      <relatedPersonName><firstName>Michael</firstName><lastName>Fanfant</lastName></relatedPersonName>
      <relatedPersonRelationshipList><relationship>Director</relationship></relatedPersonRelationshipList>
    </relatedPersonInfo>
  </relatedPersonsList>
  <offeringData>
    <industryGroup>
      <industryGroupType>Pooled Investment Fund</industryGroupType>
      <investmentFundInfo><investmentFundType>Venture Capital Fund</investmentFundType></investmentFundInfo>
    </industryGroup>
    <typeOfFiling><dateOfFirstSale><yetToOccur>true</yetToOccur></dateOfFirstSale></typeOfFiling>
    <offeringSalesAmounts><totalOfferingAmount>100000000</totalOfferingAmount><totalAmountSold>0</totalAmountSold></offeringSalesAmounts>
  </offeringData>
</edgarSubmission>"""
        history_mock.return_value = {
            "checked": True,
            "found": False,
            "weak_match": False,
            "count": 0,
            "reason": "No earlier Form D fund filing matched.",
            "first_filing_date": "",
            "filing_name": "",
            "filing_url": "",
            "matched_identity": "",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = os.path.join(temp_dir, "leads.csv")
            run_pipeline(output_file=output_file, logger=lambda _message: None)
            with open(output_file, newline="", encoding="utf-8") as handle:
                first_row = next(csv.DictReader(handle))

            run_pipeline(output_file=output_file, logger=lambda _message: None)
            with open(output_file, newline="", encoding="utf-8") as handle:
                second_row = next(csv.DictReader(handle))

        self.assertEqual(first_row["manager_status_code"], "likely_new")
        self.assertEqual(first_row["is_new_since_last_run"], "yes")
        self.assertEqual(second_row["manager_status_code"], "likely_new")
        self.assertEqual(second_row["is_new_since_last_run"], "no")
        self.assertEqual(second_row["first_seen_at"], first_row["first_seen_at"])


if __name__ == "__main__":
    unittest.main()
