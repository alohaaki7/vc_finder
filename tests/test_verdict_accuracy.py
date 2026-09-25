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


def write_codes(path, codes):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["crd", "name", "manager_status_code", "is_new_since_last_run", "issues"])
        writer.writeheader()
        for index, (code, issues) in enumerate(codes):
            writer.writerow({
                "crd": str(index), "name": f"Firm {index} Fund I, LP", "manager_status_code": code,
                "is_new_since_last_run": "no", "issues": issues,
            })


VC = "Pooled Investment Fund - Venture Capital Fund"


class ServerStatsTests(unittest.TestCase):
    def test_non_vc_rows_are_hidden_and_unchecked_rows_counted_separately(self):
        codes = [("likely_new", VC), ("needs_review", VC), ("not_checked", VC), ("not_checked", VC),
                 ("likely_new", "Oil and Gas - Unknown"), ("existing_manager", VC)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "leads.csv")
            write_codes(path, codes)
            with patch.object(server, "LEADS_FILE", path):
                client = server.app.test_client()
                stats = client.get("/api/stats").get_json()
                leads = client.get("/api/leads").get_json()

        self.assertEqual(stats["total_leads"], 5)
        self.assertEqual(stats["needs_review"], 1)
        self.assertEqual(stats["not_checked"], 2)
        self.assertEqual(stats["likely_new_firms"], 1)
        self.assertNotIn("not_vc", {lead["manager_status_code"] for lead in leads})

    def test_runs_are_refused_when_disabled(self):
        with patch.object(server, "RUNS_ENABLED", False):
            response = server.app.test_client().post("/api/run", json={"type": "vc"})
        self.assertEqual(response.status_code, 403)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class HostedRunTests(unittest.TestCase):
    def hosted(self, **extra):
        values = {"HOSTED": True, "GITHUB_TOKEN": "token", "RUNS_ENABLED": True, "RUN_PASSWORD": ""}
        values.update(extra)
        return patch.multiple(server, **values)

    def test_hosted_run_dispatches_github_workflow(self):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs.get("json")))
            if method == "GET":
                return FakeResponse(payload={"workflow_runs": [{"status": "completed"}]})
            return FakeResponse(status_code=204)

        with self.hosted(), patch("server.requests.request", side_effect=fake_request):
            response = server.app.test_client().post(
                "/api/run", json={"type": "vc", "days": "90", "min_size": "0"}
            )

        self.assertEqual(response.status_code, 200)
        method, url, body = calls[-1]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/refresh-sec-leads.yml/dispatches"))
        self.assertEqual(body["inputs"], {"lead_type": "vc", "days": "90", "min_size": "0"})

    def test_hosted_run_refuses_while_github_run_is_active(self):
        active = FakeResponse(payload={"workflow_runs": [{"status": "in_progress"}]})
        with self.hosted(), patch("server.requests.request", return_value=active):
            response = server.app.test_client().post("/api/run", json={"type": "vc"})
        self.assertEqual(response.status_code, 400)

    def test_hosted_run_requires_password_when_configured(self):
        with self.hosted(RUN_PASSWORD="secret"), patch("server.requests.request") as request_mock:
            response = server.app.test_client().post("/api/run", json={"type": "vc"})
        self.assertEqual(response.status_code, 401)
        request_mock.assert_not_called()

    def test_logs_wait_for_a_run_newer_than_the_dispatch(self):
        old_run = FakeResponse(payload={"workflow_runs": [
            {"status": "completed", "conclusion": "success", "created_at": "2026-09-25T10:00:00Z"}
        ]})
        with self.hosted(), patch("server.requests.request", return_value=old_run):
            data = server.app.test_client().get("/api/logs?since=2026-09-25T12:00:00Z").get_json()
        self.assertTrue(data["running"])


class PipelineMergeTests(unittest.TestCase):
    @patch("pipeline.search_form_d_filings", return_value=[])
    def test_saved_non_vc_rows_are_dropped_on_next_run(self, _search_mock):
        from pipeline import run_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "leads.csv")
            write_codes(path, [("likely_new", VC), ("likely_new", "Residential - Unknown")])
            run_pipeline(days=30, output_file=path, logger=lambda _msg: None)
            with open(path, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual([row["crd"] for row in rows], ["0"])


if __name__ == "__main__":
    unittest.main()
