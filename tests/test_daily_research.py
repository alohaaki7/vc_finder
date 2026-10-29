import unittest
from datetime import datetime
from unittest.mock import patch

import daily_research


class FakeTreg(daily_research.Treg):
    def __init__(self, google=None, overview=None):
        super().__init__("token", 1.0)
        self._google = google or []
        self._overview = overview or {}

    def google(self, query):
        return self._google

    def ai_overview(self, prompt):
        return self._overview

    def ranked_keywords(self, domain):
        return []


def lead(name, sold="$5M", code="likely_new", filed="2026-10-20", **extra):
    return {"name": name, "amount_sold": sold, "manager_status_code": code, "filing_date": filed,
            "sec_number": name, "sec_people": "Ada Lovelace", **extra}


class QueueTests(unittest.TestCase):
    def test_raised_first_then_new_then_fund2_and_skip_done(self):
        leads = [
            lead("Small Fund I, LP", sold="$0"),
            lead("Raised Fund I, LP", sold="$8M"),
            lead("Big Raise Fund I, LP", sold="$15M"),
            lead("Second Fund II, LP", code="existing_manager", fund2_raise=True),
            lead("AI Continuation Vehicle I, LLC", sold="$4M"),
            lead("Done Fund I, LP", sold="$9M"),
        ]
        queue = daily_research.build_queue(leads, {"Done Fund I, LP"}, datetime(2026, 10, 29))
        self.assertEqual([item[1]["name"] for item in queue], [
            "Big Raise Fund I, LP", "Raised Fund I, LP", "Small Fund I, LP", "Second Fund II, LP",
        ])

    def test_brand_drops_legal_suffix_and_fund_number(self):
        self.assertEqual(daily_research.brand_of("Syntropy Ventures Fund I, LP"), "Syntropy Ventures")
        self.assertEqual(daily_research.brand_of("NeXeption Capital Fund I, L.P."), "NeXeption Capital")


class ResearchTests(unittest.TestCase):
    def test_no_site_and_database_results_become_pitch_points(self):
        treg = FakeTreg(
            google=[{"url": "https://pitchbook.com/x"}, {"url": "https://crunchbase.com/y"}],
            overview={"answer": "Atomus VC is a venture firm.", "citations": [{"url": "https://pitchbook.com/x"}]},
        )
        row = daily_research.research("Raised $1-20M", lead("Atomus Fund I, L.P."), treg)
        self.assertEqual(row["offer"], "Website")
        self.assertIn("No website found", row["pitch_points"])
        self.assertIn("pitchbook.com", row["pitch_points"])

    def test_site_cited_by_ai_is_used_when_google_misses_it(self):
        treg = FakeTreg(google=[], overview={
            "answer": "Cork & Vines is a hospitality fund.", "citations": [{"url": "https://corknvines.com/about"}]})
        with patch.object(daily_research.grade_websites, "grade_site", return_value={"grade": "adequate"}):
            row = daily_research.research("Raised $1-20M", lead("Cork & Vines Fund I, LP"), treg)
        self.assertEqual(row["website"], "https://corknvines.com/")
        self.assertEqual(row["website_source"], "Google AI Overview source")

    def test_budget_stops_calls(self):
        treg = daily_research.Treg("token", 0.0)
        self.assertFalse(treg.available)
        self.assertIsNone(treg.call("anything"))


if __name__ == "__main__":
    unittest.main()
