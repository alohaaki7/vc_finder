import csv
import os
import tempfile
import unittest

from apply_agent_reviews import REVIEW_FIELDS, apply_reviews


class AgentReviewTests(unittest.TestCase):
    def write_csv(self, path, rows, fields=None):
        fields = fields or sorted({field for row in rows for field in row})
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def valid_review(self, **overrides):
        row = {
            "agent_task_id": "task-1",
            "record_key": "sec:one",
            "reviewed_at": "2026-07-24",
            "firm_name": "Northstar Ventures",
            "verdict": "good_lead",
            "firm_verified": "yes",
            "manager_verified": "yes",
            "decision_maker": "Avery Morgan",
            "contact_title": "Founding Partner",
            "linkedin_person": "https://linkedin.com/in/avery",
            "linkedin_company": "https://linkedin.com/company/northstar",
            "website_url": "https://northstar.example",
            "website_status": "placeholder",
            "offer_route": "website",
            "verified_facts": "New Fund I and a coming-soon website.",
            "rejection_reason": "",
            "next_action": "Wait for user approval.",
            "evidence_sources": "https://sec.example/one | https://linkedin.com/in/avery | https://northstar.example",
            "external_action_taken": "no",
        }
        row.update(overrides)
        return row

    def run_apply(self, temp_dir, review):
        queue = os.path.join(temp_dir, "queue.csv")
        reviews = os.path.join(temp_dir, "reviews.csv")
        ledger = os.path.join(temp_dir, "ledger.csv")
        report = os.path.join(temp_dir, "report.md")
        self.write_csv(queue, [{"agent_task_id": "task-1", "record_key": "sec:one", "firm_name": "Northstar Ventures"}])
        self.write_csv(reviews, [review], fields=REVIEW_FIELDS)
        result = apply_reviews(queue, reviews, ledger, report, report_date="2026-07-24")
        return result, ledger, report

    def test_accepts_verified_good_lead_and_writes_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result, ledger, report = self.run_apply(temp_dir, self.valid_review())

            self.assertEqual(result[0]["verdict"], "good_lead")
            with open(report, encoding="utf-8") as handle:
                self.assertIn("Northstar Ventures", handle.read())
            with open(ledger, encoding="utf-8") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)

    def test_good_lead_requires_exact_linkedin_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "linkedin_person"):
                self.run_apply(temp_dir, self.valid_review(linkedin_person=""))

    def test_rejects_any_external_action(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "external_action_taken"):
                self.run_apply(temp_dir, self.valid_review(external_action_taken="yes"))


if __name__ == "__main__":
    unittest.main()
