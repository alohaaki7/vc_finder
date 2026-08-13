import unittest

from server import linkedin_search_firm, linkedin_search_person, prepare_lead_for_display


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


if __name__ == "__main__":
    unittest.main()
