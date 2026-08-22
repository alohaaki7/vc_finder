import unittest
from pathlib import Path


class MobileDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (Path(__file__).parents[1] / "templates" / "index.html").read_text(
            encoding="utf-8"
        )

    def test_phone_view_uses_cards_instead_of_a_wide_table(self):
        self.assertIn('class="mobile-list" id="mobile-list"', self.template)
        self.assertIn("function renderMobileCards()", self.template)
        self.assertIn(".table-container {\n                display: none;", self.template)
        self.assertNotIn("min-width: 760px", self.template)

    def test_phone_details_open_as_a_closeable_sheet(self):
        self.assertIn('class="detail-close" id="detail-close"', self.template)
        self.assertIn("function closeLeadDetail()", self.template)
        self.assertIn("body.mobile-detail-open", self.template)
        self.assertIn("min-height: 100dvh", self.template)

    def test_mobile_controls_are_collapsible_and_touch_sized(self):
        self.assertIn('id="pipeline-mobile-toggle"', self.template)
        self.assertIn("function togglePipelineControls()", self.template)
        self.assertIn("min-height: 44px", self.template)
        self.assertIn('id="results-count" aria-live="polite"', self.template)


if __name__ == "__main__":
    unittest.main()
