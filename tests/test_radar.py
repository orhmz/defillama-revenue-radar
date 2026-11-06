import unittest
from datetime import date, datetime, timedelta, timezone

from radar.core import DataError, analyze
from radar.notify import digest


TODAY = date(2026, 11, 7)
END = TODAY - timedelta(days=1)


def fixture(*, days=30):
    chart = []
    for offset in range(days - 1, -1, -1):
        day = END - timedelta(days=offset)
        timestamp = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
        chart.append([timestamp, {"Steady": 100.0, "Surge": 100.0}])
    protocols = [
        {"id": "1", "name": "Steady", "slug": "steady", "protocolType": "protocol"},
        {"id": "2", "name": "Surge", "slug": "surge", "protocolType": "protocol"},
    ]
    return {"protocols": protocols, "totalDataChartBreakdown": chart}


class RadarTests(unittest.TestCase):
    def test_momentum_uses_two_complete_windows_and_only_crossing_day(self):
        data = fixture()
        for row in data["totalDataChartBreakdown"][-2:]:
            row[1]["Surge"] = 1000.0
        report = analyze(data, today=TODAY)
        momentum = [x for x in report["signals"] if x["type"] == "momentum"]
        self.assertEqual(len(momentum), 1)
        self.assertEqual(momentum[0]["protocol_id"], "2")
        self.assertEqual(momentum[0]["current_7d_usd"], 2500.0)
        self.assertEqual(momentum[0]["previous_7d_usd"], 700.0)
        self.assertEqual(momentum[0]["event_id"], "momentum:2:2026-11-06")
        data["totalDataChartBreakdown"].append([
            int(datetime(2026, 11, 7, tzinfo=timezone.utc).timestamp()),
            {"Steady": 100.0, "Surge": 1000.0},
        ])
        next_report = analyze(data, today=date(2026, 11, 8))
        self.assertFalse([x for x in next_report["signals"] if x["type"] == "momentum"])

    def test_missing_protocol_day_is_unknown_not_zero(self):
        data = fixture()
        for row in data["totalDataChartBreakdown"][-5:]:
            row[1]["Surge"] = 500.0
        del data["totalDataChartBreakdown"][-10][1]["Surge"]
        self.assertFalse([x for x in analyze(data, today=TODAY)["signals"]
                          if x["type"] == "momentum"])

    def test_first_observed_revenue_for_new_protocol(self):
        data = fixture()
        data["protocols"].append({"id": "3", "name": "New", "slug": "new", "protocolType": "protocol"})
        data["totalDataChartBreakdown"][-3][1]["New"] = 40
        data["totalDataChartBreakdown"][-2][1]["New"] = 60
        data["totalDataChartBreakdown"][-1][1]["New"] = 50
        first = [x for x in analyze(data, today=TODAY)["signals"]
                 if x["type"] == "first_observed_revenue"]
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["event_id"], "first:3:2026-11-04")
        self.assertEqual(first[0]["observed_since_first_usd"], 150)
        self.assertEqual(first[0]["prior_observed_days"], 0)

    def test_chain_aggregate_excluded_and_older_positive_prevents_first(self):
        data = fixture()
        data["protocols"].append({"id": "3", "name": "Chain", "protocolType": "chain"})
        for row in data["totalDataChartBreakdown"][-3:]:
            row[1]["Chain"] = 100
        data["totalDataChartBreakdown"][0][1]["Surge"] = 1
        report = analyze(data, today=TODAY)
        self.assertFalse(any(x["protocol_id"] == "3" for x in report["signals"]))

    def test_gaps_duplicates_and_staleness_fail_closed(self):
        data = fixture()
        data["totalDataChartBreakdown"].pop(-5)
        with self.assertRaises(DataError):
            analyze(data, today=TODAY)
        data = fixture()
        data["protocols"].append(data["protocols"][0].copy())
        with self.assertRaises(DataError):
            analyze(data, today=TODAY)
        with self.assertRaises(DataError):
            analyze(fixture(), today=TODAY + timedelta(days=5))

    def test_digest_deduplicates_event_ids_and_caps_volume(self):
        report = {"as_of": "2026-11-06", "source": "https://example.test",
                  "signals": [{"event_id": "first:3:2026-11-04", "type": "first_observed_revenue",
                               "name": "New", "source_url": "https://defillama.com/fees/new",
                               "first_positive_day": "2026-11-04", "observed_since_first_usd": 150,
                               "as_of": "2026-11-06"}]}
        self.assertIsNone(digest(report, {"first:3:2026-11-04"}))
        title, body = digest(report, set())
        self.assertIn("radar-event:first:3:2026-11-04", body)
        self.assertIn("1 new signal", title)
        report["signals"] *= 21
        with self.assertRaises(ValueError):
            digest(report, set())


if __name__ == "__main__":
    unittest.main()
