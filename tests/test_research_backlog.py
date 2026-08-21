import unittest

from build_research_backlog import (
    bucket_for,
    is_vc_backlog_candidate,
    operating_firm_candidate,
)


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

    def test_series_parent_vc_manager_is_recovered_instead_of_skipped(self):
        row = {
            "firm_name": "DI-0702",
            "name": "DI-0702 Fund I, a series of Syntax Ventures, LP",
            "issues": "Pooled Investment Fund - Venture Capital Fund",
        }

        self.assertEqual(operating_firm_candidate(row), "Syntax Ventures")
        self.assertTrue(is_vc_backlog_candidate(row))

    def test_explicit_non_vc_category_beats_a_vague_venture_name(self):
        row = {
            "firm_name": "Harbor Real Estate Ventures",
            "name": "Harbor Real Estate Ventures Fund I, LP",
            "issues": "Pooled Investment Fund - Other Real Estate Fund",
        }

        self.assertFalse(is_vc_backlog_candidate(row))

    def test_follow_on_name_stays_visible_but_moves_to_watchlist(self):
        row = {
            "firm_name": "Craft Ventures V",
            "name": "Craft Ventures V, LP",
            "issues": "Pooled Investment Fund - Venture Capital Fund",
            "fund_stage": "Emerging Fund",
            "manager_status_code": "likely_new",
        }

        self.assertEqual(bucket_for(row, "explicit_sec_vc"), "established_manager_watchlist")


if __name__ == "__main__":
    unittest.main()
