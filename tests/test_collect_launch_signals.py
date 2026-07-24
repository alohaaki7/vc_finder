import unittest
from datetime import date

from collect_launch_signals import article_to_signal, extract_firm_name


class LaunchSignalTests(unittest.TestCase):
    def test_extracts_firm_before_launch_action(self):
        self.assertEqual(
            extract_firm_name("Northstar Ventures closes $40M debut fund"),
            "Northstar Ventures",
        )

    def test_converts_recent_debut_fund_article_to_unverified_signal(self):
        signal = article_to_signal({
            "title": "Northstar Ventures closes $40M debut fund - Example News",
            "source": "Example News",
            "url": "https://example.com/northstar",
            "pub_date": "Wed, 22 Jul 2026 10:00:00 GMT",
        }, today=date(2026, 7, 23), days=180)

        self.assertIsNotNone(signal)
        self.assertEqual(signal["firm_name"], "Northstar Ventures")
        self.assertEqual(signal["signal_type"], "launch_news")
        self.assertEqual(signal["contact_verification_status"], "not_identified")
        self.assertEqual(signal["website_status"], "unknown")

    def test_rejects_follow_on_and_non_vc_articles(self):
        for title in (
            "Northstar Ventures closes Fund II",
            "Northstar Capital launches new real estate fund",
            "Paradigm launches $1.2 billion AI venture fund",
            "Boreal Ventures reaches first close of its second seed fund",
        ):
            signal = article_to_signal({
                "title": title,
                "source": "Example News",
                "url": "https://example.com/story",
                "pub_date": "Wed, 22 Jul 2026 10:00:00 GMT",
            }, today=date(2026, 7, 23), days=180)
            self.assertIsNone(signal, title)

    def test_extracts_named_firm_launched_by_a_person(self):
        self.assertEqual(
            extract_firm_name("Ex-a16z Partner Michelle Volz Launches Pax Ventures with $50 Million First Fund"),
            "Pax Ventures",
        )

    def test_cleans_headline_descriptors_to_the_operating_brand(self):
        examples = {
            "Africa-focussed 3IF Ventures marks first close of debut fund": "3IF Ventures",
            "Kalos Ventures eyes workforce tech investments amid AI upheaval, closes debut fund": "Kalos Ventures",
            "Blitzer's Bolt Ventures backs 154 Partners as debut fund closes at hard cap": "154 Partners",
            "Italian life sciences firm XGEN Venture closes debut fund": "XGEN Venture",
            "Superorganism, First Venture Capital Firm Dedicated to Biodiversity, Closes Debut Fund": "Superorganism",
            "IndiGo Ventures makes first close of debut fund": "IndiGo Ventures",
        }
        for title, expected in examples.items():
            self.assertEqual(extract_firm_name(title), expected, title)

    def test_rejects_generic_headline_fragments_as_firms(self):
        for title in (
            "Ashton Kutcher leaving Sound Ventures to launch new VC firm with Morgan Beller",
            "Deals in brief: Gobi Partners backs Funding Societies and Vertex Ventures Japan makes debut fund close",
        ):
            self.assertEqual(extract_firm_name(title), "", title)


if __name__ == "__main__":
    unittest.main()
