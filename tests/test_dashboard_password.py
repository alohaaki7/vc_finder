import base64
import unittest
from unittest.mock import patch

import server


def basic(password, user="me"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class DashboardPasswordTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_every_route_needs_the_password(self):
        with patch.multiple(server, DASHBOARD_PASSWORD="secret", HOSTED=True):
            for path in ["/", "/api/leads", "/api/stats", "/adv", "/api/adv"]:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401, path)
                self.assertIn("Basic", response.headers["WWW-Authenticate"])
            self.assertEqual(self.client.post("/api/run", json={}).status_code, 401)

    def test_wrong_password_is_refused(self):
        with patch.multiple(server, DASHBOARD_PASSWORD="secret", HOSTED=True):
            self.assertEqual(self.client.get("/api/stats", headers=basic("nope")).status_code, 401)

    def test_right_password_with_any_username_is_allowed(self):
        with patch.multiple(server, DASHBOARD_PASSWORD="secret", HOSTED=True):
            self.assertEqual(self.client.get("/api/stats", headers=basic("secret", "anyone")).status_code, 200)

    def test_hosted_site_without_a_password_serves_nothing(self):
        with patch.multiple(server, DASHBOARD_PASSWORD="", HOSTED=True):
            self.assertEqual(self.client.get("/api/leads").status_code, 503)

    def test_local_server_without_a_password_stays_open(self):
        with patch.multiple(server, DASHBOARD_PASSWORD="", HOSTED=False):
            self.assertEqual(self.client.get("/api/stats").status_code, 200)


if __name__ == "__main__":
    unittest.main()
