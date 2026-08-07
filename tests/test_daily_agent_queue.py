import csv
import os
import tempfile
import unittest

from build_daily_agent_queue import build_daily_queue


class DailyAgentQueueTests(unittest.TestCase):
    def row(self, index, **overrides):
        row = {
            "monthly_rank": str(index),
            "prospect_score": str(90 - index),
            "prospect_tier": "A" if index < 4 else "B",
            "research_depth": "deep" if index < 7 else "light",
            "volume_status": "linkedin_lookup",
            "firm_name": f"Firm {index}",
            "name": f"Firm {index} Fund I",
            "sec_number": f"sec-{index}",
            "lead_status": "discovered",
        }
        row.update(overrides)
        return row

    def write_csv(self, path, rows):
        fields = sorted({field for row in rows for field in row})
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_caps_daily_queue_and_skips_completed_or_previously_reviewed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "monthly.csv")
            ledger = os.path.join(temp_dir, "ledger.csv")
            destination = os.path.join(temp_dir, "daily.csv")
            rows = [self.row(index) for index in range(1, 10)]
            rows[0]["research_depth"] = "complete"
            self.write_csv(source, rows)
            self.write_csv(ledger, [{"record_key": "sec:sec-2", "verdict": "reject"}])

            selected = build_daily_queue(source, ledger, destination, limit=5, batch_date="2026-07-24")

            self.assertEqual(len(selected), 5)
            self.assertNotIn("Firm 1", {row["firm_name"] for row in selected})
            self.assertNotIn("Firm 2", {row["firm_name"] for row in selected})
            self.assertTrue(all(row["agent_batch_date"] == "2026-07-24" for row in selected))
            self.assertTrue(all(row["agent_review_status"] == "pending" for row in selected))
            self.assertTrue(all("Do not save" in row["agent_instruction"] for row in selected))

    def test_prioritizes_deep_rows_over_higher_scored_light_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "monthly.csv")
            destination = os.path.join(temp_dir, "daily.csv")
            rows = [
                self.row(1, firm_name="Light High", research_depth="light", prospect_score="99"),
                self.row(8, firm_name="Deep Lower", research_depth="deep", prospect_score="60"),
            ]
            self.write_csv(source, rows)

            selected = build_daily_queue(source, os.path.join(temp_dir, "missing.csv"), destination, limit=1)

            self.assertEqual(selected[0]["firm_name"], "Deep Lower")


if __name__ == "__main__":
    unittest.main()
