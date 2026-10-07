"""
Parser tests against a saved copy of the DGHS page (07-Oct-2026).

Run with:  python3 -m unittest discover -s tests
"""

import csv
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scraper"))

import scrape  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "dghs-2026-10-07.html.gz")


def load_fixture():
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as fh:
        return fh.read()


class HelperTests(unittest.TestCase):
    def test_to_number(self):
        self.assertEqual(scrape.to_number("7,446"), 7446)
        self.assertEqual(scrape.to_number("1.5"), 1.5)
        self.assertIsNone(scrape.to_number(""))
        self.assertIsNone(scrape.to_number("-"))
        self.assertIsNone(scrape.to_number(None))

    def test_parse_site_date(self):
        self.assertEqual(scrape.parse_site_date("05-Sep-2026"), "2026-09-05")
        self.assertEqual(scrape.parse_site_date("5-Sep-26"), "2026-09-05")
        self.assertIsNone(scrape.parse_site_date("W35"))
        self.assertIsNone(scrape.parse_site_date(None))

    def test_strip_tags_decodes_entities(self):
        self.assertEqual(scrape.strip_tags("<b>A&nbsp;&amp;&#39;B</b>"), "A &'B")

    def test_categories_by_variable_reference(self):
        pre = '<script>var categories = ["0-5","6-10"];\n'
        block = "{ xAxis: [{categories: categories}], series: [] }"
        self.assertEqual(scrape.parse_categories(block, pre), ["0-5", "6-10"])

    def test_series_without_name_does_not_crash_summary(self):
        charts = {"dengue_discharged_total_and_24_hours": {
            "series": [{"name": None, "data": [5]}]}}
        self.assertEqual(scrape.extract_summary("", charts)["discharged_last24"], 5)


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = scrape.build(load_fixture())

    def test_passes_sanity_check(self):
        self.assertEqual(scrape.sanity_check(self.doc), [])

    def test_meta(self):
        self.assertEqual(self.doc["meta"]["last_updated"], "2026-10-07")
        self.assertEqual(self.doc["meta"]["year"], 2026)

    def test_summary(self):
        s = self.doc["summary"]
        self.assertEqual(s["epi_week"], "W40")
        self.assertEqual(s["ytd_cases"], 92216)
        self.assertEqual(s["ytd_deaths"], 289)
        self.assertEqual(s["last24_cases"], 1860)
        self.assertEqual(s["discharged_ytd"], 86152)

    def test_chart_count(self):
        self.assertEqual(len(self.doc["charts"]), 27)

    def test_age_group_charts_have_labels(self):
        for cid in ("dengue_affected_by_age_group", "dengue_death_by_age_group",
                    "percentage_of_dengue_affected_by_age_group",
                    "percentage_of_dengue_death_by_age_group"):
            chart = self.doc["charts"][cid]
            self.assertTrue(chart["categories"], cid)
            for series in chart["series"]:
                self.assertEqual(len(series["data"]), len(chart["categories"]), cid)

    def test_tables(self):
        self.assertEqual(len(self.doc["tables"]), 8)
        self.assertEqual(self.doc["tables"][0]["headers"], ["Age Group", "Male", "Female", "Total"])

    def test_daily_series(self):
        rows = scrape.daily_series(self.doc)
        self.assertEqual(rows[0]["date"], "2026-01-01")
        self.assertEqual(rows[-1]["date"], "2026-10-07")
        self.assertEqual(sum(r["deaths"] for r in rows), self.doc["summary"]["ytd_deaths"])


class EndToEndTests(unittest.TestCase):
    def test_main_writes_feed(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        html_path = os.path.join(tmp, "page.html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(load_fixture())
        env = dict(os.environ, DENGUE_HTML_FILE=html_path, DENGUE_DATA_DIR=os.path.join(tmp, "data"))
        script = os.path.join(ROOT, "scraper", "scrape.py")

        subprocess.run([sys.executable, script], env=env, check=True, capture_output=True)
        data = os.path.join(tmp, "data")
        for name in ("latest.json", "summary.json", "timeseries.json", "timeseries.csv",
                     "daily.csv", "history/index.json", "history/2026-10-07.json"):
            self.assertTrue(os.path.exists(os.path.join(data, name)), name)

        with open(os.path.join(data, "timeseries.json"), encoding="utf-8") as fh:
            ts = json.load(fh)
        self.assertEqual(ts["rows"][0]["date"], "2026-10-07")
        self.assertAlmostEqual(ts["rows"][0]["cfr_pct"], 0.313, places=3)
        with open(os.path.join(data, "timeseries.csv"), encoding="utf-8") as fh:
            self.assertEqual(next(csv.reader(fh))[0], "date")

        # A second run with identical figures must not touch any file.
        before = {n: os.path.getmtime(os.path.join(data, n)) for n in ("latest.json", "summary.json")}
        out = subprocess.run([sys.executable, script], env=env, check=True,
                             capture_output=True, text=True).stdout
        self.assertIn("no change", out)
        after = {n: os.path.getmtime(os.path.join(data, n)) for n in before}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
