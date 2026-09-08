import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class ReviewStateTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_bucket_decision_is_persisted_and_returned(self):
        row = {"sec_number": "021-123", "firm_name": "Fresh Ventures"}
        backlog_id = f"backlog-{server.backlog_key(row)}"
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "review-state.json"
            with patch.object(server, "REVIEW_STATE_FILE", str(state_path)):
                response = self.client.post(
                    f"/api/backlog/{backlog_id}/bucket",
                    json={"workflow_bucket": "watchlist"},
                )
                self.assertEqual(response.status_code, 200)
                saved = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(saved[server.backlog_key(row)]["workflow_bucket"], "watchlist")

    def test_unknown_bucket_is_rejected(self):
        response = self.client.post(
            "/api/backlog/aaaaaaaaaaaaaaaaaaaa/bucket",
            json={"workflow_bucket": "contacted"},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
