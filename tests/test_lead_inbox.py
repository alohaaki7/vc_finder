import os
import tempfile
import unittest

import lead_inbox


class InboxTests(unittest.TestCase):
    def test_lines_are_split_into_fund_people_site_and_post(self):
        text = """
        Fund name | people | link
        1. Black Prism Capital Partners | Steven Miller | https://x.com/i/status/123 | first close in 6 months
        - Project Ventures | | projectventures.co
        Quiet Fund
        """
        rows = lead_inbox.parse_lines(text, "Found on X", "2026-11-17")
        self.assertEqual([row["fund"] for row in rows], ["Black Prism Capital Partners", "Project Ventures", "Quiet Fund"])
        self.assertEqual((rows[0]["people"], rows[0]["link"], rows[0]["website"], rows[0]["note"]),
                         ("Steven Miller", "https://x.com/i/status/123", "", "first close in 6 months"))
        self.assertEqual((rows[1]["website"], rows[1]["link"]), ("https://projectventures.co", ""))
        self.assertEqual(rows[2]["source"], "Found on X")

    def test_one_pasted_line_from_a_grok_task(self):
        text = ("Black Prism Capital Partners | Steven Miller | https://x.com/stevenm/status/111 | $40M Fund I ;; "
                "Project Ventures | Ada Lovelace, Alan Turing | https://x.com/pv/status/222 | pre-seed ;; NONE")
        rows = lead_inbox.parse_lines(text, "Grok", "2026-11-17")
        self.assertEqual([(row["fund"], row["people"]) for row in rows], [
            ("Black Prism Capital Partners", "Steven Miller"), ("Project Ventures", "Ada Lovelace, Alan Turing")])
        self.assertEqual(rows[1]["link"], "https://x.com/pv/status/222")

    def test_a_fund_is_added_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "inbox.csv")
            rows = lead_inbox.parse_lines("Quiet Fund\nLoud Fund", "Added by you", "2026-11-17")
            self.assertEqual(lead_inbox.add(rows, path), 2)
            again = lead_inbox.parse_lines("quiet fund\nThird Fund", "Added by you", "2026-11-18")
            self.assertEqual(lead_inbox.add(again, path), 1)
            self.assertEqual([row["fund"] for row in lead_inbox.load(path)], ["Quiet Fund", "Loud Fund", "Third Fund"])


if __name__ == "__main__":
    unittest.main()
