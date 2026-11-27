import unittest

import fund_momentum


def item(name, amount, currency, size, kind="first", reported="2026-11-20", stage="pre-seed", slug=None):
    return {
        "url": f"https://fundmomentum.vc/blog/{name}",
        "date_published": reported + "T10:00:00Z",
        "_fund_momentum": {
            "fund": {"name": name, "slug": slug or name.lower().replace(" ", "-"), "stage": stage},
            "close": {"type": kind, "amount_m": amount, "currency": currency, "size_display": size,
                      "reported_date": reported},
            "sources": {"fund_momentum_post": f"https://fundmomentum.vc/blog/{name}-post"},
        },
    }


class FundMomentumTests(unittest.TestCase):
    def test_small_first_and_second_funds_are_kept_with_a_note(self):
        feed = {"items": [
            item("Ascension Capital", 30, "CAD", "C$30M", reported="2026-11-27"),
            item("Mudita Venture Fund II", 120, "USD", "$120M", kind="final", stage="seed"),
            item("Saviu Fund II", 40, "EUR", "€40M", kind="final", stage="growth"),
            item("Vine Ventures Fund III", 236, "USD", "$236M"),
            item("Plural Fund", 1000, "EUR", "€1B"),
            item("Redalpine Fund VIII", 250, "USD", "$250M"),
            item("Old News Fund I", 10, "USD", "$10M", reported="2026-09-01"),
            item("Known Fund I", 10, "USD", "$10M"),
            item("Odd Currency Fund I", 900, "XYZ", "900 XYZ"),
        ]}
        rows = fund_momentum.rows_from_feed(feed, "2026-10-28", {"known fund i"})
        self.assertEqual([row["fund"] for row in rows],
                         ["Ascension Capital", "Mudita Venture Fund II", "Odd Currency Fund I"])
        self.assertEqual(rows[0]["note"], "Flagged by Fund Momentum: first close of C$30M, pre-seed, reported 2026-11-27.")
        self.assertEqual(rows[1]["note"], "Flagged by Fund Momentum: final close of $120M, seed, reported 2026-11-20.")
        self.assertEqual((rows[0]["source"], rows[0]["link"]),
                         ("Fund Momentum", "https://fundmomentum.vc/blog/Ascension Capital-post"))

    def test_profile_fills_site_and_linkedin(self):
        row = {"fund": "Ascension Capital", "people": "", "website": "", "linkedin": ""}
        fund_momentum.add_profile(row, {"website": "https://ascensioncapital.vc/", "fundManager": None,
                                        "linkedin": "https://www.linkedin.com/company/ascensioncapitalvc/"})
        self.assertEqual((row["website"], row["linkedin"], row["people"]),
                         ("https://ascensioncapital.vc/", "https://www.linkedin.com/company/ascensioncapitalvc/", ""))

    def test_known_leads_are_matched_to_flagged_funds_only_by_a_distinctive_name(self):
        import os, tempfile
        feed = {"items": [item("Zacua Ventures Fund II", 135, "USD", "$135M", stage="pre-seed"),
                          item("U&I Ventures Fund I", 32, "USD", "$32M")]}
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "flagged.csv")
            fund_momentum.record_funds(feed, path)
            index = fund_momentum.flag_index(path)
        self.assertEqual(fund_momentum.flag_for("Zacua Ventures Fund II, L.P.", index)["note"],
                         "Flagged by Fund Momentum: first close of $135M, pre-seed, reported 2026-11-20.")
        self.assertIsNotNone(fund_momentum.flag_for("Zacua Ventures", index))
        # "U&I Ventures" leaves only "ventures" to match on, which would match any fund
        self.assertIsNone(fund_momentum.flag_for("J Ventures", index))
        self.assertIsNone(fund_momentum.flag_for("Other Ventures Fund I", index))

    def test_a_number_in_the_name_tells_firms_apart(self):
        self.assertEqual(fund_momentum.match_key("Stage 2 Capital Venture Fund IV and Catalyst IV"), "stage 2 capital")
        self.assertEqual(fund_momentum.match_key("STAGE 2 CAPITAL ADVISORS"), "stage 2 capital")
        self.assertEqual(fund_momentum.match_key("STAGE CAPITAL MANAGEMENT, LLC"), "stage capital")
        self.assertEqual(fund_momentum.match_key("Proximal Ventures ET a Series of CGF2021 LLC"), "proximal ventures")


if __name__ == "__main__":
    unittest.main()
