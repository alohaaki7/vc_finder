import csv
import os
import tempfile
import unittest
from unittest.mock import patch

import server
from pipeline import assess_manager_novelty, classify_fund_stage, reassess_saved_lead


CLEAN_HISTORY = {
    "checked": True,
    "found": False,
    "weak_match": False,
    "count": 0,
    "reason": "No earlier Form D fund filing matched.",
}


def saved_likely_new(name, issues="Pooled Investment Fund - Venture Capital Fund"):
    return {
        "name": name,
        "firm_name": name,
        "manager_status_code": "likely_new",
        "manager_status": "Likely new firm",
        "issues": issues,
    }


class FundStageTests(unittest.TestCase):
    def test_trailing_roman_numeral_sets_stage(self):
        self.assertEqual(classify_fund_stage("Noar Ventures II, LP"), "Fund II")
        self.assertEqual(classify_fund_stage("MW VC Prometheus-II, LLC"), "Fund II")
        self.assertEqual(classify_fund_stage("Grove Ventures IV, L.P."), "Later Fund")
        self.assertEqual(classify_fund_stage("Lightcone Venture Capital I LP"), "Fund I")
        self.assertEqual(classify_fund_stage("Eval Ventures Alpha 1, LP"), "Fund I")

    def test_names_without_a_sequence_stay_emerging(self):
        self.assertEqual(classify_fund_stage("Woodstork 22 Ventures LLC"), "Emerging Fund")
        self.assertEqual(classify_fund_stage("Agentic Builders Capital LLC"), "Emerging Fund")
        self.assertEqual(classify_fund_stage("Axel Ventures Fund LLC - Series 4"), "Emerging Fund")


class NewRunVerdictTests(unittest.TestCase):
    def test_real_estate_filing_is_not_vc(self):
        info = {"year_inc": "2026", "industry_group": "Residential", "investment_fund_type": "Unknown"}
        assessment = assess_manager_novelty(
            {"name": "LLJ Multifamily Ventures 21, LLC"}, info, "Emerging Fund", CLEAN_HISTORY
        )
        self.assertEqual(assessment["manager_status_code"], "not_vc")

    def test_later_fund_is_existing_manager(self):
        info = {"year_inc": "2026", "industry_group": "Pooled Investment Fund",
                "investment_fund_type": "Venture Capital Fund"}
        assessment = assess_manager_novelty(
            {"name": "Craft Ventures V, LP"}, info, "Later Fund", CLEAN_HISTORY
        )
        self.assertEqual(assessment["manager_status_code"], "existing_manager")

    def test_numbered_series_is_not_a_new_firm(self):
        info = {"year_inc": "2026", "industry_group": "Pooled Investment Fund",
                "investment_fund_type": "Venture Capital Fund"}
        assessment = assess_manager_novelty(
            {"name": "Axel Ventures Fund LLC - Series 4"}, info, "Emerging Fund", CLEAN_HISTORY
        )
        self.assertEqual(assessment["manager_status_code"], "needs_review")

    def test_clean_vc_fund_i_is_still_likely_new(self):
        info = {"year_inc": "2026", "industry_group": "Pooled Investment Fund",
                "investment_fund_type": "Venture Capital Fund"}
        assessment = assess_manager_novelty(
            {"name": "Atomus Fund I, L.P."}, info, "Fund I", CLEAN_HISTORY
        )
        self.assertEqual(assessment["manager_status_code"], "likely_new")


class SavedRowReassessmentTests(unittest.TestCase):
    def test_saved_follow_on_fund_is_downgraded(self):
        row = reassess_saved_lead(saved_likely_new("Noar Ventures II, LP"))
        self.assertEqual(row["manager_status_code"], "existing_manager")
        self.assertEqual(row["fund_stage"], "Fund II")

    def test_saved_oil_and_gas_deal_is_not_vc(self):
        row = reassess_saved_lead(saved_likely_new("TSO PDP #1 Joint Venture", "Oil and Gas - Unknown"))
        self.assertEqual(row["manager_status_code"], "not_vc")

    def test_vague_industry_goes_to_review_not_exclusion(self):
        row = reassess_saved_lead(saved_likely_new("Ardenwood Ventures LLC Fund 1", "Investing - Unknown"))
        self.assertEqual(row["manager_status_code"], "needs_review")

    def test_saved_clean_fund_i_is_unchanged(self):
        row = reassess_saved_lead(saved_likely_new("Vivace Longevity Fund I LP"))
        self.assertEqual(row["manager_status_code"], "likely_new")

    def test_existing_manager_is_never_upgraded(self):
        row = saved_likely_new("Some Ventures Fund I, LP")
        row["manager_status_code"] = "existing_manager"
        self.assertEqual(reassess_saved_lead(row)["manager_status_code"], "existing_manager")


class ServerStatsTests(unittest.TestCase):
    def test_unchecked_and_non_vc_rows_are_not_counted_as_needs_review(self):
        codes = ["likely_new", "needs_review", "not_checked", "not_checked", "not_vc", "existing_manager"]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "leads.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["manager_status_code", "is_new_since_last_run"])
                writer.writeheader()
                for code in codes:
                    writer.writerow({"manager_status_code": code, "is_new_since_last_run": "no"})
            with patch.object(server, "LEADS_FILE", path):
                stats = server.app.test_client().get("/api/stats").get_json()

        self.assertEqual(stats["needs_review"], 1)
        self.assertEqual(stats["not_checked"], 2)
        self.assertEqual(stats["not_vc"], 1)
        self.assertEqual(stats["likely_new_firms"], 1)

    def test_runs_are_refused_when_disabled(self):
        with patch.object(server, "RUNS_ENABLED", False):
            response = server.app.test_client().post("/api/run", json={"type": "vc"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
