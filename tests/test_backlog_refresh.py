import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import app


class BacklogRefreshTests(unittest.TestCase):
    def test_api_rebuilds_backlog_from_latest_sec_master(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ALL_VC_LEADS.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "firm_name",
                        "name",
                        "issues",
                        "fund_stage",
                        "filer_status",
                        "filing_date",
                        "sec_number",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "firm_name": "Current Ventures",
                        "name": "Current Ventures Fund I, LP",
                        "issues": "Pooled Investment Fund - Venture Capital Fund",
                        "fund_stage": "Fund I",
                        "filer_status": "first_filer",
                        "filing_date": "2026-08-22",
                        "sec_number": "0000000001-26-000001",
                    }
                )

            missing_saved_backlog = Path(directory) / "missing.csv"
            with (
                patch("server.LEADS_FILE", str(source)),
                patch("server.BACKLOG_FILE", str(missing_saved_backlog)),
            ):
                response = app.test_client().get("/api/backlog")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["source"], "live_sec_master")
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["rows"][0]["firm_name"], "Current Ventures")
        self.assertIn("refreshed_at", payload)

    def test_dashboard_shows_refresh_progress_and_result(self):
        template = (Path(__file__).parents[1] / "templates" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("function refreshBacklog()", template)
        self.assertIn("Refreshing latest SEC data...", template)
        self.assertIn("Updated ${allBacklog.length.toLocaleString()} VC firms", template)
        self.assertIn('id="backlog-refresh-status" aria-live="polite"', template)


if __name__ == "__main__":
    unittest.main()
