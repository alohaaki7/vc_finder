import unittest

from lead_signals import AdvIndex, early_signal, same_brand, sec_people


def adv(crd, name, phone="", state="CA", registered="2026-08-21"):
    return {
        "crd_number": crd, "business_name": name, "legal_adviser_name": name, "phone": phone,
        "state": state, "registration_date": registered, "firm_type": "ERA",
        "reported_website": "", "iapd_url": f"https://adviserinfo.sec.gov/firm/summary/{crd}",
    }


class EarlySignalTests(unittest.TestCase):
    def test_no_first_sale_is_earliest(self):
        self.assertEqual(early_signal({"date_of_first_sale": "Yet to Occur"})[0], "not_raised")

    def test_recent_first_sale_is_just_raised(self):
        row = {"date_of_first_sale": "2026-09-08", "filing_date": "2026-09-22"}
        self.assertEqual(early_signal(row)[0], "just_raised")

    def test_old_first_sale_has_no_early_signal(self):
        row = {"date_of_first_sale": "2025-06-24", "filing_date": "2026-08-18"}
        self.assertEqual(early_signal(row), ("", "", 0))


class SecPeopleTests(unittest.TestCase):
    def test_skips_entities_and_placeholders(self):
        row = {
            "contact_name": "N/A Vivace Longevity Fund I GP LLC",
            "all_contacts": "N/A Vivace Longevity Fund I GP LLC (Executive Officer); "
                            "Dylan Venezia Livingston (Executive Officer); MATTHEW KAEBERLEIN (Executive Officer)",
        }
        self.assertEqual(sec_people(row), ["Dylan Venezia Livingston", "Matthew Kaeberlein"])

    def test_escaped_company_name_is_not_a_person(self):
        row = {"all_contacts": "N/A ABG &amp; CO INVESTMENT MANAGEMENT, LLC (Promoter); JACKSON EISENPRESSER (Executive Officer)"}
        self.assertEqual(sec_people(row), ["Jackson Eisenpresser"])


class AdvMatchTests(unittest.TestCase):
    def test_brand_match_through_gp_entity(self):
        index = AdvIndex([adv("1", "SEMIANALYSIS CAPITAL MANAGEMENT LLC")])
        lead = {"state": "CA", "all_contacts": "N/A SemiAnalysis Capital Fund I GP, LLC (Director); Dylan Patel (Director)"}
        match = index.match(lead, "SemiAnalysis Capital")
        self.assertEqual(match["adv_crd"], "1")
        self.assertEqual(match["adv_match_basis"], "name")

    def test_shared_generic_word_is_not_a_match(self):
        index = AdvIndex([adv("1", "GENERAL GLOBAL CAPITAL LLC")])
        lead = {"state": "CA", "all_contacts": ""}
        self.assertIsNone(index.match(lead, "Global Horizons Holdings Ventures"))

    def test_name_match_in_another_state_is_rejected(self):
        index = AdvIndex([adv("1", "LIGHTCONE VENTURES", state="NY")])
        self.assertIsNone(index.match({"state": "MD", "all_contacts": ""}, "Lightcone"))

    def test_unique_phone_matches_and_shared_phone_does_not(self):
        index = AdvIndex([adv("1", "JSI MANAGER LLC", phone="(415) 555-0100")])
        lead = {"state": "CA", "phone": "415-555-0100", "all_contacts": ""}
        self.assertEqual(index.match(lead, "Firehunter")["adv_match_basis"], "phone")
        self.assertIsNone(index.match(lead, "Firehunter", {"4155550100": 5}))

    def test_same_brand(self):
        self.assertTrue(same_brand("Lightcone Venture Capital I GP LLC", "LIGHTCONE VENTURES"))
        self.assertFalse(same_brand("Serendipity Capital Global Quantum Technologies", "Quantum Capital Investment Group"))


if __name__ == "__main__":
    unittest.main()
