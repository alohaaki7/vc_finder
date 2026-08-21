import unittest

from build_research_backlog import is_vc_backlog_candidate


class ResearchBacklogTests(unittest.TestCase):
    def test_generic_other_pooled_fund_is_not_a_vc_backlog_candidate(self):
        row = {
            "firm_name": "FH Structured Solutions",
            "name": "FH Structured Solutions Fund I, L.P.",
            "issues": "Pooled Investment Fund - Other Investment Fund",
        }

        self.assertFalse(
            is_vc_backlog_candidate(
                row,
                "no explicit VC category or strong pooled-fund VC name signal",
            )
        )

    def test_venture_name_can_stay_for_identity_review(self):
        row = {
            "firm_name": "Ardenwood Ventures",
            "name": "Ardenwood Ventures Fund I LP",
            "issues": "Investing - Unknown",
        }

        self.assertTrue(
            is_vc_backlog_candidate(
                row,
                "no explicit VC category or strong pooled-fund VC name signal",
            )
        )

    def test_explicit_vc_watchlist_row_is_kept(self):
        row = {
            "firm_name": "Syntax Ventures",
            "name": "Syntax Ventures Fund I, LP",
            "issues": "Pooled Investment Fund - Venture Capital Fund",
        }

        self.assertTrue(is_vc_backlog_candidate(row, "existing manager"))

    def test_series_vehicle_is_not_presented_as_an_operating_firm(self):
        row = {
            "firm_name": "Capital Factory SPVs",
            "name": "Home Factory, a series of Capital Factory SPVs, LP",
            "issues": "Pooled Investment Fund - Venture Capital Fund",
        }

        self.assertFalse(is_vc_backlog_candidate(row, "existing manager"))


if __name__ == "__main__":
    unittest.main()
