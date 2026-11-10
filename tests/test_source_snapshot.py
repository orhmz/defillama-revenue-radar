import unittest
from datetime import date

from radar.core import DataError
from radar.source_snapshot import FEEDS, collect, snapshot
from tests.test_radar import TODAY, fixture


class SourceSnapshotTests(unittest.TestCase):
    def test_full_feed_separates_closed_days_from_rolling_values(self):
        data = fixture(days=35)
        data["protocols"][0]["total24h"] = 999
        result = snapshot(data, source="https://example.test", today=TODAY,
                          fetched_at="2026-11-07T12:00:00+00:00")
        self.assertEqual(result["last_completed_utc_day"], "2026-11-06")
        self.assertEqual(result["coverage"]["product_protocols"], 2)
        self.assertEqual(result["coverage"]["incomplete_14d_protocols"], 0)
        steady = result["protocols"][0]
        self.assertEqual(steady["current_7d_usd"], 700)
        self.assertEqual(steady["previous_7d_usd"], 700)
        self.assertEqual(steady["rolling_at_fetch"]["total24h"], 999)
        self.assertTrue(steady["complete_14d"])

    def test_missing_protocol_day_remains_unknown(self):
        data = fixture(days=35)
        del data["totalDataChartBreakdown"][-3][1]["Surge"]
        result = snapshot(data, source="https://example.test", today=TODAY,
                          fetched_at="2026-11-07T12:00:00+00:00")
        surge = next(row for row in result["protocols"] if row["name"] == "Surge")
        self.assertIsNone(surge["current_7d_usd"])
        self.assertFalse(surge["complete_14d"])
        self.assertEqual(result["coverage"]["complete_14d_protocols"], 1)

    def test_unknown_chart_identity_and_missing_day_fail_closed(self):
        data = fixture(days=35)
        data["totalDataChartBreakdown"][-1][1]["Unknown"] = 50
        with self.assertRaisesRegex(DataError, "no protocol ID"):
            snapshot(data, source="https://example.test", today=TODAY,
                     fetched_at="2026-11-07T12:00:00+00:00")
        data = fixture(days=35)
        data["totalDataChartBreakdown"].pop(-3)
        with self.assertRaisesRegex(DataError, "missing usable data"):
            snapshot(data, source="https://example.test", today=TODAY,
                     fetched_at="2026-11-07T12:00:00+00:00")

    def test_any_feed_failure_marks_entire_capture_incomplete(self):
        def fetcher(url):
            if url == FEEDS["holders_global"]:
                raise OSError("source unavailable")
            return fixture(days=35)

        result = collect(today=TODAY, fetched_at="2026-11-07T12:00:00+00:00",
                         fetcher=fetcher)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(len(result["feeds"]), 3)
        self.assertEqual(result["errors"], {"holders_global": "source unavailable"})

    def test_validated_capture_does_not_claim_candidate_or_alert_coverage(self):
        result = collect(today=TODAY, fetched_at="2026-11-07T12:00:00+00:00",
                         fetcher=lambda url: fixture(days=35))
        self.assertEqual(result["status"], "source_validated")
        self.assertFalse(result["candidate_scan_performed"])
        self.assertFalse(result["alerts_enabled"])


if __name__ == "__main__":
    unittest.main()
