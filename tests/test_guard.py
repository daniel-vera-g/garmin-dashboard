"""The never-shown list from the spec, enforced.

A synthetic cache is written with every banned metric planted in it - in the
places Garmin really puts them (training-status response, activity summary,
sample stream, lactate response, weather). All three pages are built from it,
and none of the banned labels or planted sentinel values may appear.

The sync whitelists fields before writing the cache, so in real use these
values never even reach disk; this test also proves the build would not show
them if they did.
"""
import json
import re
import tempfile
from html.parser import HTMLParser
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import build_dashboard as B
import garmin_sync as G

TODAY = date(2030, 3, 13)          # a Wednesday
NOW = datetime(2030, 3, 13, 9, 0)

# Sentinel values chosen so they can't occur by accident anywhere else.
VO2 = 77.7
LT_HR = 197
CYCLING_FTP = 433
BODY_BATTERY = 91
PERF_COND = -17
ENDURANCE = 9876
RACE_5K_S = 1111                    # 18:31

BANNED_LABELS = re.compile(
    r"vo2|vo₂|vo&#8322;|race predict|training status|endurance score|hill score|"
    r"performance condition|body battery|lactate threshold heart|lt heart|lthr|"
    r"productive|maintaining|unproductive|overreaching|detraining|peaking", re.I)


class _Visible(HTMLParser):
    """Collects what a person can see: text nodes plus tooltip and aria text.
    SVG coordinates and CSS are not 'shown', so they don't count."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip += 1
        for k, v in attrs:
            if k in ("data-tip", "aria-label", "title") and v:
                self.parts.append(v)

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def visible(html: str) -> str:
    v = _Visible()
    v.feed(html)
    return "\n".join(v.parts)


def write(root: Path, rel: str, obj) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, default=str))


class NeverShown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cache, out = root / "cache", root / "out"

        # raw Garmin-shaped responses with the banned data planted in them
        run = {"activityId": 1, "activityName": "Morning Run", "activityType": {"typeKey": "running"},
               "eventType": {"typeKey": "uncategorized"}, "startTimeLocal": f"{TODAY} 07:00:00",
               "startTimeGMT": f"{TODAY} 06:00:00", "distance": 10000.0, "duration": 3000.0,
               "elapsedDuration": 3100.0, "averageHR": 140.0, "maxHR": 150.0, "elevationGain": 10.0,
               "vO2MaxValue": VO2, "differenceBodyBattery": BODY_BATTERY, "splitSummaries": []}
        status = {"mostRecentVO2Max": {"generic": {"vo2MaxPreciseValue": VO2}},
                  "mostRecentTrainingStatus": {"latestTrainingStatusData": {"1": {
                      "calendarDate": str(TODAY), "trainingStatus": 7,
                      "trainingStatusFeedbackPhrase": "PRODUCTIVE_3",
                      "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 900, "dailyTrainingLoadChronic": 1000,
                                               "acwrStatus": "OPTIMAL", "dailyAcuteChronicWorkloadRatio": 0.9}}}},
                  "mostRecentTrainingLoadBalance": {"x": {"trainingBalanceFeedbackPhrase": "AEROBIC_LOW_FOCUS"}}}
        details = {"metricDescriptors": [{"metricsIndex": 0, "key": "sumDuration"},
                                         {"metricsIndex": 1, "key": "directHeartRate"},
                                         {"metricsIndex": 2, "key": "directSpeed"},
                                         {"metricsIndex": 3, "key": "directBodyBattery"},
                                         {"metricsIndex": 4, "key": "directPerformanceCondition"}],
                   "activityDetailMetrics": [{"metrics": [i * 2.0, 138.0, 3.4, BODY_BATTERY, PERF_COND]}
                                             for i in range(1500)]}
        lactate_latest = {"speed_and_heart_rate": {"calendarDate": f"{TODAY}T06:00:00", "speed": 0.42,
                                                   "heartRate": LT_HR, "heartRateCycling": LT_HR - 10},
                          "power": {"calendarDate": f"{TODAY}T06:00:00", "sport": "CYCLING",
                                    "functionalThresholdPower": CYCLING_FTP}}
        lactate_range = {"speed": [{"from": str(TODAY), "series": "running", "value": 0.42}],
                         "heart_rate": [{"from": str(TODAY), "series": "running", "value": LT_HR}],
                         "power": []}

        # exactly what the sync would write - through its whitelists
        write(cache, "activities.json", {"1": G.slim_activity(run)})
        write(cache, "activity/1/series.json", G.slim_series(details))
        write(cache, "activity/1/weather.json", G.slim_weather({"temp": 50, "dewPoint": 40}))
        write(cache, f"load/{TODAY}.json", G.slim_load(status))
        write(cache, "lactate.json", G.slim_lactate(lactate_range, lactate_latest))
        write(cache, "sleep.json", {str(TODAY): G.slim_night(
            {"totalSleepTimeInSeconds": 27000, "sleepScore": 80, "bodyBatteryChange": BODY_BATTERY,
             "restingHeartRate": 48, "localSleepStartTimeInMillis": 1, "localSleepEndTimeInMillis": 2})})
        write(cache, f"readiness/{TODAY}.json", G.slim_readiness(
            {"calendarDate": str(TODAY), "level": "HIGH", "score": 80, "recoveryTime": 300}))
        # and a stray file as if an old version had left banned data behind
        write(cache, "race_predictions.json", {"time5K": RACE_5K_S, "vo2": VO2})
        write(cache, "endurance.json", {"overallScore": ENDURANCE})

        # the committed example configs, never the personal ones
        cls.pages = {p.name: p.read_text() for p in B.build(
            cache, out, config=B.ROOT / "config.example.toml", now=NOW)}
        cls.cache_text = "".join(p.read_text() for p in cache.rglob("*.json")
                                 if p.name not in ("race_predictions.json", "endurance.json"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_page_scripts_parse(self):
        """A broken quote kills every script on the page (sync, alerts, coach)."""
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        for name, html in self.pages.items():
            js = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
            p = Path(self.tmp.name) / f"{name}.js"
            p.write_text(js)
            res = subprocess.run([node, "--check", str(p)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"{name}: {res.stderr}")

    def test_three_pages_built(self):
        self.assertEqual(set(self.pages), {"dashboard.html", "season.html", "health.html"})

    def test_no_banned_labels_on_any_page(self):
        for name, html in self.pages.items():
            m = BANNED_LABELS.search(visible(html))
            self.assertIsNone(m, f"{name} shows banned label {m and m.group(0)!r}")

    def test_no_planted_values_on_any_page(self):
        for name, html in self.pages.items():
            text = visible(html)
            for label, v in [("VO2", "77.7"), ("LT HR", str(LT_HR)), ("cycling FTP", str(CYCLING_FTP)),
                             ("endurance", str(ENDURANCE)), ("race 5k", "18:31"),
                             ("performance condition", str(PERF_COND))]:
                hit = re.search(rf"(?<![\d.]){re.escape(v)}(?![\d.])", text)
                self.assertIsNone(hit, f"{name} shows {label} value {v}: ...{text[max(0, (hit.start() if hit else 0) - 60):(hit.end() if hit else 0) + 20]!r}")

    def test_whitelists_keep_banned_data_off_disk(self):
        for v in ("77.7", str(LT_HR), str(CYCLING_FTP), str(PERF_COND)):
            self.assertIsNone(re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", self.cache_text),
                              f"cache contains {v}")
        for word in ("PRODUCTIVE", "OPTIMAL", "AEROBIC_LOW", "BodyBattery", "PerformanceCondition", "vO2"):
            self.assertNotIn(word, self.cache_text, f"cache contains {word}")


if __name__ == "__main__":
    unittest.main()
