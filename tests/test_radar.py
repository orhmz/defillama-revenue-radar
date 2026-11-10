import unittest
import json
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from radar.core import DataError, analyze
from radar.notify import digest
from radar import checkpoint, notify


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
    def test_momentum_uses_complete_windows_and_keeps_one_event(self):
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
        next_signal = next(x for x in next_report["signals"] if x["type"] == "momentum")
        self.assertEqual(next_signal["event_id"], momentum[0]["event_id"])
        self.assertIsNone(digest(next_report, {momentum[0]["event_id"]}))

    def test_momentum_keeps_one_event_through_short_dips_and_rearms_after_a_week(self):
        # Two separated revenue rises remain one event until seven complete
        # daily comparisons have fallen below the push threshold.
        for first_spike, rearmed in ((-12, False), (-14, False), (-17, True)):
            with self.subTest(first_spike=first_spike):
                data = fixture(days=60)
                chart = data["totalDataChartBreakdown"]
                for index in (first_spike, first_spike + 1):
                    chart[index][1]["Surge"] = 650
                for index in (-2, -1):
                    chart[index][1]["Surge"] = 1300
                report = analyze(data, today=TODAY)
                signal = next(x for x in report["signals"] if x["type"] == "momentum")
                first_start = (END + timedelta(days=first_spike + 2)).isoformat()
                prior_id = f"momentum:2:{first_start}"
                if rearmed:
                    self.assertEqual(signal["episode_start"], (END - timedelta(days=1)).isoformat())
                    self.assertIsNotNone(digest(report, {prior_id}))
                else:
                    self.assertEqual(signal["event_id"], prior_id)
                    self.assertIsNone(digest(report, {prior_id}))
                self.assertEqual(signal["confidence"], "medium")

    def test_small_dollar_increase_stays_in_the_report(self):
        data = fixture()
        for row in data["totalDataChartBreakdown"][-4:]:
            row[1]["Surge"] = 200
        report = analyze(data, today=TODAY)
        signal = next(x for x in report["signals"] if x["type"] == "momentum")
        self.assertEqual(signal["change_usd"], 400)
        self.assertEqual(signal["confidence"], "low")
        self.assertIsNone(digest(report, set()))

    def test_checkpoint_catches_a_missed_day_and_current_checkpoint_stays_quiet(self):
        data = fixture()
        data["totalDataChartBreakdown"][-8][1]["Surge"] = 1100
        report = analyze(data, today=TODAY, after=END - timedelta(days=2))
        self.assertEqual(report["processed_days"], ["2026-11-05", "2026-11-06"])
        signal = next(x for x in report["signals"] if x["type"] == "momentum")
        self.assertEqual(signal["as_of"], "2026-11-05")
        self.assertIsNotNone(digest(report, set()))
        self.assertIsNone(digest(report, {signal["event_id"]}))
        current = analyze(data, today=TODAY, after=END)
        self.assertEqual(current["processed_days"], [])
        self.assertIsNone(digest(current, set()))

    def test_checkpoint_rejects_gaps_and_regressions(self):
        data = fixture()
        data["totalDataChartBreakdown"].pop(-25)
        with self.assertRaises(DataError):
            analyze(data, today=TODAY, after=END - timedelta(days=5))
        with self.assertRaises(DataError):
            analyze(fixture(), today=TODAY, after=TODAY)
        with self.assertRaises(DataError):
            analyze(fixture(), today=TODAY + timedelta(days=1), require_latest=True)

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
        self.assertIsNone(digest(analyze(data, today=TODAY), set()))

    def test_first_revenue_needs_two_positive_days_and_one_hundred_dollars(self):
        for values, should_alert in (((30, 60), False), ((0, 120), False), ((50, 50), True)):
            with self.subTest(values=values):
                data = fixture()
                data["protocols"].append({"id": "3", "name": "New", "slug": "new",
                                          "protocolType": "protocol"})
                chart = data["totalDataChartBreakdown"]
                for row, value in zip(chart[-3:], (0, *values)):
                    row[1]["New"] = value
                report = analyze(data, today=TODAY)
                self.assertEqual(digest(report, set()) is not None, should_alert)
                self.assertIsNone(digest(report, {"first:3:2026-11-04"}))

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
        signal = report["signals"][0]
        report["signals"] = [signal] * 21
        title, body = digest(report, set())
        self.assertIn("1 new signal", title)
        self.assertEqual(body.count("radar-event:"), 1)
        report["signals"] = [
            {**signal, "event_id": f"first:{i}:2026-11-04"} for i in range(21)
        ]
        title, body = digest(report, set())
        self.assertIn("20 new signal(s)", title)
        self.assertEqual(body.count("radar-event:"), 20)

    def test_legacy_marker_suppresses_the_same_momentum_episode(self):
        data = fixture()
        for row in data["totalDataChartBreakdown"][-5:]:
            row[1]["Surge"] = 800
        report = analyze(data, today=TODAY)
        signal = next(x for x in report["signals"] if x["type"] == "momentum")
        self.assertLess(signal["episode_start"], report["as_of"])
        self.assertIsNone(digest(report, {f"momentum:2:{report['as_of']}"}))
        self.assertIsNotNone(digest(report, {"momentum:2:2026-10-20"}))

    def test_notification_assigns_repository_owner(self):
        report = {"as_of": "2026-11-06", "source": "https://example.test",
                  "signals": [{"event_id": "first:3:2026-11-04", "type": "first_observed_revenue",
                               "name": "New", "source_url": "https://defillama.com/fees/new",
                               "first_positive_day": "2026-11-04", "observed_since_first_usd": 150,
                               "as_of": "2026-11-06"}]}
        sent = []

        def record(url, token, *, payload=None):
            sent.append(payload)
            return {"html_url": "https://github.com/orhmz/defillama-revenue-radar/issues/1"}

        with (patch.dict("os.environ", {"GITHUB_TOKEN": "test-token",
                                        "GITHUB_REPOSITORY": "orhmz/defillama-revenue-radar"}),
              patch("pathlib.Path.read_text", return_value=json.dumps(report)),
              patch.object(notify, "known_events", return_value=set()),
              patch.object(notify, "_request", side_effect=record),
              patch("builtins.print")):
            self.assertEqual(notify.main(), 0)
        self.assertEqual(sent[0]["assignees"], ["orhmz"])


class CheckpointTests(unittest.TestCase):
    def test_restore_a_legacy_report_and_skip_unpublished_previews(self):
        content = BytesIO()
        with ZipFile(content, "w") as archive:
            archive.writestr("report.json", json.dumps({"as_of": "2026-11-05"}))
        replies = [
            {"workflow_runs": [{"id": 30, "event": "workflow_dispatch"},
                               {"id": 29, "event": "schedule"}]},
            {"jobs": [{"name": "notify", "conclusion": "skipped"}]},
            {"jobs": [{"name": "notify", "conclusion": "success"}]},
            {"artifacts": [{"id": 50, "name": "revenue-radar-report", "expired": False}]},
        ]
        with (patch.object(checkpoint, "_request", side_effect=replies),
              patch.object(checkpoint.urllib.request, "urlopen",
                           return_value=BytesIO(content.getvalue())) as opened):
            day = checkpoint.last_published_day("owner/repo", "token", today=TODAY)
        self.assertEqual(day, date(2026, 11, 5))
        request = opened.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/actions/artifacts/50/zip"))
        self.assertEqual(request.unredirected_hdrs["Authorization"], "Bearer token")


if __name__ == "__main__":
    unittest.main()
