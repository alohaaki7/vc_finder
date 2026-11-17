import unittest

import x_signals


def reply(text, citations=None, annotations=None):
    return {
        "output": [
            {"type": "custom_tool_call", "call_id": "xs_1"},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": text, "annotations": annotations or []}]},
        ],
        "citations": citations or [],
        "usage": {"server_side_tool_usage_details": {"x_posts_fetched": 44, "x_users_fetched": 3}},
    }


class XSignalTests(unittest.TestCase):
    def test_only_funds_with_a_post_grok_cited_are_kept(self):
        text = """Black Prism Capital Partners | Steven Miller | https://x.com/stevenm/status/111 | $40M Fund I, first close
Made Up Ventures | Nobody Real | https://x.com/nobody/status/999 | $10M Fund I
No Link Capital | Ada Lovelace | | Fund I"""
        rows, dropped = x_signals.funds_from(reply(text, citations=["https://x.com/i/status/111"]), "2026-11-17")
        self.assertEqual([row["fund"] for row in rows], ["Black Prism Capital Partners"])
        self.assertEqual((rows[0]["people"], rows[0]["source"], rows[0]["note"]),
                         ("Steven Miller", "Found on X", "$40M Fund I, first close"))
        self.assertEqual(len(dropped), 2)

    def test_sources_in_annotations_count_and_none_gives_nothing(self):
        notes = [{"type": "url_citation", "url": "https://x.com/i/status/222"}]
        rows, _ = x_signals.funds_from(
            reply("Quiet Fund | Alan Turing | https://twitter.com/alan/status/222 | Fund I", annotations=notes), "2026-11-17")
        self.assertEqual([row["fund"] for row in rows], ["Quiet Fund"])
        self.assertEqual(x_signals.funds_from(reply("NONE"), "2026-11-17"), ([], []))


if __name__ == "__main__":
    unittest.main()
