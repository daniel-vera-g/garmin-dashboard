"""Sync bookkeeping with a fake Garmin client: gaps refill, covered dates are
not asked for again, failed requests are retried."""
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import garmin_sync as G

TODAY = date(2030, 3, 13)


class FakeSleep:
    def __init__(self, fail: set[date] | None = None):
        self.calls: list[tuple[date, date]] = []
        self.fail = fail or set()

    def __call__(self, a: str, b: str):
        a, b = date.fromisoformat(a), date.fromisoformat(b)
        self.calls.append((a, b))
        if any(a <= d <= b for d in self.fail):
            raise RuntimeError("429")
        return [{"calendarDate": (a + timedelta(days=i)).isoformat(),
                 "values": {"sleepScore": 80}} for i in range((b - a).days + 1)]


class SyncCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patches = [mock.patch.object(G, "CACHE", Path(self.tmp.name)),
                   mock.patch.object(G.time, "sleep", lambda s: None),
                   mock.patch("builtins.print", lambda *a, **k: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def sync(self, fn, today, cov):
        G.sync_by_date(None, "sleep", fn, today, G._sleep_rows, cov)

    def test_second_sync_only_asks_for_the_recent_window(self):
        cov, fn = {}, FakeSleep()
        self.sync(fn, TODAY, cov)
        self.assertEqual(len(fn.calls), 5)                       # 121 days in 28-day chunks
        fn.calls.clear()
        self.sync(fn, TODAY + timedelta(days=1), cov)
        self.assertEqual(len(fn.calls), 1)
        a, b = fn.calls[0]
        self.assertEqual(b, TODAY + timedelta(days=1))
        self.assertLessEqual((b - a).days, G.RECENT_WELLNESS_DAYS + 1)

    def test_failed_window_is_fetched_again_next_time(self):
        cov = {}
        hole = TODAY - timedelta(days=60)
        self.sync(FakeSleep(fail={hole}), TODAY, cov)
        self.assertNotIn(hole.isoformat(), cov["sleep"])
        fn = FakeSleep()
        self.sync(fn, TODAY, cov)
        self.assertTrue(any(a <= hole <= b for a, b in fn.calls))
        self.assertIn(hole.isoformat(), cov["sleep"])
        self.assertIn(hole.isoformat(), G.read_json(G.CACHE / "sleep.json", {}))

    def test_load_snapshot_taken_during_the_day_is_not_final(self):
        d = TODAY - timedelta(days=5)
        p = G.CACHE / "load" / f"{d}.json"
        G.write_json(p, {"acute": 1})                        # an old file without fetched_on
        same_day = datetime.combine(d, datetime.min.time()).timestamp() + 20 * 3600
        os.utime(p, (same_day, same_day))
        self.assertFalse(G.load_is_final(p, d))
        later = same_day + 3 * 86400
        os.utime(p, (later, later))
        self.assertTrue(G.load_is_final(p, d))


    def test_activity_list_is_pulled_whole_once_a_day(self):
        calls = []

        class C:
            def get_activities_by_date(self, a, b):
                calls.append(a)
                return []
        G.write_json(G.CACHE / "activities.json", {"1": {"id": 1, "start_local": "2029-01-01 07:00:00"}})
        cov = {}
        with mock.patch.object(G.settings, "load_season", lambda: G.settings.Season()):
            G.sync_activities(C(), TODAY, cov)
            G.sync_activities(C(), TODAY, cov)
            G.sync_activities(C(), TODAY + timedelta(days=1), cov)
        first = (TODAY - timedelta(days=G.WELLNESS_DAYS)).isoformat()
        self.assertEqual(calls, [first, (TODAY - timedelta(days=G.RECENT_ACTIVITY_DAYS)).isoformat(),
                                 (TODAY + timedelta(days=1) - timedelta(days=G.WELLNESS_DAYS)).isoformat()])

    def test_lactate_history_survives_a_successful_sync(self):
        G.write_json(G.CACHE / "lactate.json", {"speed_raw": [{"date": "2029-01-01", "value": 0.40}],
                                                "power_w": []})

        class C:
            def get_lactate_threshold(self, latest, start_date=None, end_date=None):
                if latest:
                    return {"speed_and_heart_rate": {"calendarDate": "2030-03-10T06:00:00", "speed": 0.44}}
                return {"speed": [{"from": "2030-03-01", "series": "running", "value": 0.43}], "power": []}
        G.sync_lactate(C(), TODAY)
        dates = [p["date"] for p in G.read_json(G.CACHE / "lactate.json", {})["speed_raw"]]
        self.assertEqual(dates, ["2029-01-01", "2030-03-01", "2030-03-10"])


    def test_day_without_load_data_gets_a_final_marker(self):
        calls = []

        class C:
            def get_training_status(self, d):
                calls.append(d)
                return {}                                    # Garmin has nothing for the day
        with mock.patch.object(G, "DETAIL_DAYS", 5):
            G.sync_load(C(), TODAY)
            self.assertEqual(len(calls), 5)
            calls.clear()
            G.sync_load(C(), TODAY)                          # same day: the last 2 days stay open
            self.assertEqual(len(calls), 2)
        row = G.read_json(G.CACHE / "load" / f"{TODAY - timedelta(days=4)}.json", {})
        self.assertEqual((row["acute"], row["fetched_on"]), (None, TODAY.isoformat()))


if __name__ == "__main__":
    unittest.main()
