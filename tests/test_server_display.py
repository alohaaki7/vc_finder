import unittest

from server import (
    linkedin_manager_candidate,
    linkedin_search_firm,
    linkedin_search_person,
    prepare_backlog_for_display,
    prepare_lead_for_display,
)


class ServerDisplayTests(unittest.TestCase):
    def test_legacy_series_code_displays_parent_manager(self):
        row = {
            "firm_name": "DI-0702",
            "name": "DI-0702 Fund I, a series of Syntax Ventures, LP",
        }

        displayed = prepare_lead_for_display(row)

        self.assertEqual(displayed["firm_name"], "Syntax Ventures")
        self.assertEqual(displayed["sec_vehicle_name"], "DI-0702")
        self.assertEqual(row["firm_name"], "DI-0702")

    def test_normal_firm_name_is_unchanged(self):
        row = {
            "firm_name": "1616 Ventures",
            "name": "1616 Ventures Fund I, LP",
        }

        displayed = prepare_lead_for_display(row)

        self.assertEqual(displayed["firm_name"], "1616 Ventures")
        self.assertEqual(displayed["linkedin_search_firm"], "1616 Ventures")

    def test_backlog_rows_get_stable_browser_id_without_claiming_verification(self):
        row = {
            "firm_name": "FH Structured Solutions",
            "contact_name": "BRIAN MORFITT",
            "linkedin_search_url": "https://www.linkedin.com/search/results/people/?keywords=Brian",
        }

        displayed = prepare_backlog_for_display(row, 7)

        self.assertEqual(displayed["backlog_id"], "backlog-7")
        self.assertEqual(displayed["linkedin_search_person"], "Brian Morfitt")
        self.assertNotIn("linkedin_person", displayed)

    def test_linkedin_firm_search_removes_vehicle_noise(self):
        self.assertEqual(linkedin_search_firm("Axel Ventures Fund LLC - Series 4"), "Axel Ventures")
        self.assertEqual(linkedin_search_firm("Craft Ventures Feeder V"), "Craft Ventures")

    def test_linkedin_person_search_skips_legal_entity(self):
        row = {
            "contact_name": "N/A SemiAnalysis Capital Fund I GP, LLC",
            "all_contacts": (
                "N/A SemiAnalysis Capital Fund I GP, LLC (Director); "
                "Dylan Patel (Director)"
            ),
        }

        self.assertEqual(linkedin_search_person(row), "Dylan Patel")

    def test_linkedin_person_search_normalizes_uppercase_name(self):
        row = {
            "contact_name": "GENERAL PARTNER CRAFT VENTURES GP V, LP",
            "all_contacts": "GENERAL PARTNER CRAFT VENTURES GP V, LP (Promoter); DAVID SACKS (Executive Officer)",
        }

        self.assertEqual(linkedin_search_person(row), "David Sacks")

    def test_generic_sec_officer_is_not_assumed_to_be_manager(self):
        row = {
            "contact_name": "Chris Wood",
            "contact_title": "Executive Officer, Director, Promoter",
            "all_contacts": "Chris Wood (Executive Officer, Director, Promoter)",
        }

        self.assertEqual(linkedin_manager_candidate(row), "")

    def test_explicit_investment_role_is_a_manager_candidate(self):
        row = {
            "contact_name": "Rachel Chalmers",
            "contact_title": "Managing Partner",
            "all_contacts": "Rachel Chalmers (Managing Partner)",
        }

        self.assertEqual(linkedin_manager_candidate(row), "Rachel Chalmers")

    def test_verified_contact_is_a_manager_candidate(self):
        row = {
            "contact_name": "Michelle Yi",
            "contact_title": "Executive Officer",
            "contact_verification_status": "verified_public",
        }

        self.assertEqual(linkedin_manager_candidate(row), "Michelle Yi")


if __name__ == "__main__":
    unittest.main()
