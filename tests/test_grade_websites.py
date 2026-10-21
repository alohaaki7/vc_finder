import unittest
from unittest.mock import patch

import grade_websites


class FakeResponse:
    def __init__(self, url, status=200):
        self.url = url
        self.status_code = status


ADEQUATE = """<html><head><title>Atomus Ventures</title></head><body>
<a href="/team">Team</a><a href="/portfolio">Portfolio</a><a href="mailto:hi@atomus.vc">Contact</a>
<p>We invest in pre-seed founders building industrial software. {filler}</p></body></html>"""


def adviser(website, name="ATOMUS VENTURES LLC"):
    return {"crd_number": "1", "business_name": name, "registration_date": "2026-09-01",
            "country": "United States", "reported_website": website}


def fake_fetch(pages):
    def fetch(_session, url):
        html = pages.get(url.rstrip("/"))
        if html is None:
            return None, "does not resolve or refuses connections"
        return FakeResponse(url), html
    return fetch


class GradeTests(unittest.TestCase):
    def grade(self, website, pages, name="ATOMUS VENTURES LLC"):
        with patch.object(grade_websites, "fetch", side_effect=fake_fetch(pages)), \
                patch.object(grade_websites, "robots_for") as robots, \
                patch.object(grade_websites.time, "sleep"):
            robots.return_value.can_fetch.return_value = True
            return grade_websites.grade_site(adviser(website, name))

    def test_social_profile_is_not_a_site(self):
        self.assertEqual(self.grade("https://www.linkedin.com/company/atomus", {})["grade"], "social_only")

    def test_medium_blog_is_not_a_site(self):
        self.assertEqual(self.grade("https://medium.com/@atomus", {})["grade"], "social_only")

    def test_script_only_page_needs_a_human(self):
        shell = "<html><body><div id='root'></div>" + "<script src='a.js'></script>" * 6 + "</body></html>"
        self.assertEqual(self.grade("https://atomus.vc", {"https://atomus.vc": shell})["grade"], "blocked")

    def test_missing_website(self):
        self.assertEqual(self.grade("", {})["grade"], "none_reported")

    def test_unreachable_site_is_broken(self):
        self.assertEqual(self.grade("https://atomus.vc", {})["grade"], "broken")

    def test_coming_soon_is_placeholder(self):
        pages = {"https://atomus.vc": "<html><body><h1>Coming soon</h1><p>Atomus Ventures</p></body></html>"}
        self.assertEqual(self.grade("atomus.vc", pages)["grade"], "placeholder")

    def test_one_line_site_is_thin(self):
        pages = {"https://atomus.vc": "<html><body><h1>Atomus</h1><a href='mailto:a@atomus.vc'>email</a></body></html>"}
        result = self.grade("https://atomus.vc", pages)
        self.assertEqual(result["grade"], "thin_or_incomplete")
        self.assertIn("team", result["missing"])

    def test_full_site_is_adequate(self):
        pages = {"https://atomus.vc": ADEQUATE.format(filler="Our thesis and portfolio. " * 40)}
        self.assertEqual(self.grade("https://atomus.vc", pages)["grade"], "adequate")

    def test_someone_elses_site_is_flagged(self):
        pages = {"https://morrislegal-pc.com": ADEQUATE.format(filler="Law firm. " * 80).replace("Atomus", "Morris")}
        result = self.grade("https://morrislegal-pc.com", pages, name="ENGLISH RIVER LLC")
        self.assertIn("site_may_belong_to_someone_else", result["flags"])


if __name__ == "__main__":
    unittest.main()
