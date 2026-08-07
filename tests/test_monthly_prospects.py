import csv
import os
import tempfile
import unittest
from datetime import date

from build_monthly_prospects import (
    build_monthly_queue,
    calculate_prospect_score,
    offer_route_for,
    volume_eligibility,
)


class MonthlyProspectTests(unittest.TestCase):
    def base_row(self, **overrides):
        row = {
            "firm_name": "Fresh Ventures",
            "name": "Fresh Ventures Fund I, LP",
            "vehicle_type": "fund_vehicle",
            "lead_status": "discovered",
            "manager_status_code": "not_checked",
            "fund_stage": "Fund I",
            "filer_status": "first_filer",
            "year_inc": "2026",
            "amount_sold": "$1,000,000",
            "fund_size": "$10,000,000",
            "filing_date": "2026-07-10",
            "contact_name": "Avery Morgan",
            "contact_verification_status": "unverified_sec",
            "website_status": "unknown",
            "issues": "Pooled Investment Fund - Venture Capital Fund",
            "city": "Austin",
            "state": "TX",
            "sec_number": "one",
            "filing_url": "https://www.sec.gov/example",
        }
        row.update(overrides)
        return row

    def test_adequate_website_is_an_offer_route_not_a_rejection(self):
        row = self.base_row(
            lead_status="rejected",
            checked="yes",
            website_status="adequate",
            qualification_reason="Adequate website plus coherent public presence.",
        )

        eligible, _reason = volume_eligibility(row, today=date(2026, 7, 23))

        self.assertTrue(eligible)
        self.assertEqual(offer_route_for(row), "smm_branding")

    def test_true_icp_hard_rejection_remains_excluded(self):
        row = self.base_row(
            lead_status="rejected",
            qualification_reason="Established manager launching another vehicle.",
        )

        eligible, reason = volume_eligibility(row, today=date(2026, 7, 23))

        self.assertFalse(eligible)
        self.assertIn("hard gate", reason)

    def test_prior_fund_management_language_remains_a_hard_rejection(self):
        row = self.base_row(
            lead_status="rejected",
            qualification_reason="Adequate website; the founder previously co-founded a $200M investment fund.",
        )

        eligible, reason = volume_eligibility(row, today=date(2026, 7, 23))

        self.assertFalse(eligible)
        self.assertIn("hard gate", reason)

    def test_legacy_vc_suffix_number_is_rejected_as_follow_on(self):
        row = self.base_row(
            firm_name="KC VC 7",
            name="KC VC 7 LP",
            fund_stage="Emerging Fund",
        )

        eligible, reason = volume_eligibility(row, today=date(2026, 7, 24))

        self.assertFalse(eligible)
        self.assertIn("follow-on sequence", reason)

    def test_amendment_is_retained_but_scores_below_original(self):
        original = self.base_row(filer_status="first_filer")
        amendment = self.base_row(filer_status="new_filer")

        self.assertTrue(volume_eligibility(amendment, today=date(2026, 7, 23))[0])
        self.assertGreater(
            calculate_prospect_score(original, today=date(2026, 7, 23)),
            calculate_prospect_score(amendment, today=date(2026, 7, 23)),
        )

    def test_builds_100_rows_and_limits_deep_research_to_20(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "queue.csv")
            destination = os.path.join(temp_dir, "monthly.csv")
            weekly_dir = os.path.join(temp_dir, "weeks")
            rows = []
            for index in range(120):
                rows.append(self.base_row(
                    firm_name=f"Fresh Ventures {index}",
                    name=f"Fresh Ventures {index} Fund I, LP",
                    contact_name=f"Avery Morgan {index}",
                    sec_number=f"sec-{index}",
                ))
            fields = sorted({field for row in rows for field in row})
            with open(source, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

            selected = build_monthly_queue(
                source,
                destination,
                limit=100,
                deep_limit=20,
                today=date(2026, 7, 23),
                month="2026-07",
                weekly_dir=weekly_dir,
            )

            self.assertEqual(len(selected), 100)
            self.assertEqual(sum(row["research_depth"] == "deep" for row in selected), 20)
            self.assertEqual(sum(row["research_depth"] == "light" for row in selected), 80)
            self.assertEqual(selected[0]["monthly_rank"], "1")
            self.assertIn("linkedin.com/search/results/people", selected[0]["linkedin_search_url"])
            self.assertEqual(
                sorted(os.listdir(weekly_dir)),
                ["ALAMAT_WEEK_1.csv", "ALAMAT_WEEK_2.csv", "ALAMAT_WEEK_3.csv", "ALAMAT_WEEK_4.csv"],
            )

    def test_recovered_adequate_site_gets_a_new_offer_instead_of_old_exclusion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "queue.csv")
            destination = os.path.join(temp_dir, "monthly.csv")
            row = self.base_row(
                lead_status="rejected",
                checked="yes",
                website_status="adequate",
                service_opportunity="Exclude.",
                qualification_reason="Adequate website plus coherent public presence.",
            )
            with open(source, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)

            selected = build_monthly_queue(
                source,
                destination,
                limit=1,
                deep_limit=0,
                today=date(2026, 7, 23),
            )

            self.assertEqual(selected[0]["offer_route"], "smm_branding")
            self.assertNotIn("exclude", selected[0]["service_opportunity"].casefold())

    def test_combines_normalized_public_launch_signals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "queue.csv")
            external = os.path.join(temp_dir, "launches.csv")
            destination = os.path.join(temp_dir, "monthly.csv")
            sec_row = self.base_row()
            launch_row = {
                "signal_type": "launch_news",
                "firm_name": "Northstar Ventures",
                "name": "Northstar Ventures",
                "vehicle_type": "public_signal",
                "lead_status": "discovered",
                "filing_date": "2026-07-20",
                "qualification_reason": "Northstar Ventures closes debut fund",
                "website_status": "unknown",
            }
            for path, row in ((source, sec_row), (external, launch_row)):
                with open(path, "w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(row))
                    writer.writeheader()
                    writer.writerow(row)

            selected = build_monthly_queue(
                source,
                destination,
                limit=10,
                deep_limit=2,
                today=date(2026, 7, 23),
                external_paths=[external],
            )

            self.assertEqual({row["firm_name"] for row in selected}, {"Fresh Ventures", "Northstar Ventures"})
            launch = next(row for row in selected if row["firm_name"] == "Northstar Ventures")
            self.assertEqual(launch["source_confidence"], "public_launch_signal")

    def test_completed_research_does_not_consume_deep_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "queue.csv")
            destination = os.path.join(temp_dir, "monthly.csv")
            rows = [
                self.base_row(firm_name="Already Audited", checked="yes", linkedin_person="https://linkedin.com/in/avery"),
                self.base_row(firm_name="Fresh Lookup", sec_number="two"),
            ]
            fields = sorted({field for row in rows for field in row})
            with open(source, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

            selected = build_monthly_queue(
                source,
                destination,
                limit=2,
                deep_limit=1,
                today=date(2026, 7, 23),
            )

            depths = {row["firm_name"]: row["research_depth"] for row in selected}
            self.assertEqual(depths["Already Audited"], "complete")
            self.assertEqual(depths["Fresh Lookup"], "deep")


if __name__ == "__main__":
    unittest.main()
