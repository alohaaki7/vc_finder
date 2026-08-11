import unittest

from server import prepare_lead_for_display


class ServerDisplayTests(unittest.TestCase):
    def test_legacy_series_code_displays_parent_manager(self):
        row = {
            "firm_name": "DI-0702",
            "name": "DI-0702 Fund I, a series of Syntax Ventures, LP",
        }

        displayed = prepare_lead_for_display(row)

        self.assertEqual(displayed["firm_name"], "Syntax Ventures")
        self.assertEqual(displayed["sec_vehicle_name"], "DI-0702")
        self.assertEqual(row["firm_name"], "DI-0702")

    def test_normal_firm_name_is_unchanged(self):
        row = {
            "firm_name": "1616 Ventures",
            "name": "1616 Ventures Fund I, LP",
        }

        self.assertEqual(prepare_lead_for_display(row), row)


if __name__ == "__main__":
    unittest.main()
