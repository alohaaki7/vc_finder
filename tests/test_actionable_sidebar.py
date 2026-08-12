import unittest
from pathlib import Path


class ActionableSidebarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (Path(__file__).parents[1] / "templates" / "index.html").read_text(
            encoding="utf-8"
        )

    def test_sidebar_prioritizes_people_capital_and_public_links(self):
        for expected in (
            "Capital signal",
            "SEC-associated people and entities",
            "Open manager LinkedIn",
            "Open company LinkedIn",
            "Open website",
            "Why it is in the pipeline",
        ):
            self.assertIn(expected, self.template)

    def test_sec_plumbing_is_removed_from_primary_sidebar(self):
        for removed in (
            "CIK (SEC ID)",
            "Accession Number",
            "First Seen in App",
            "Last Seen in Scan",
        ):
            self.assertNotIn(removed, self.template)

    def test_unverified_linkedin_uses_search_instead_of_guessing(self):
        self.assertIn("linkedinSearchUrl('people'", self.template)
        self.assertIn("linkedinSearchUrl('companies'", self.template)
        self.assertIn("personExact || personSearch", self.template)
        self.assertIn("companyExact || companySearch", self.template)


if __name__ == "__main__":
    unittest.main()
