import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from model import f_to_c, load_model


def write(root: Path, rel: str, obj) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


class Quirks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lt_speed_is_multiplied_by_ten(self):
        write(self.root, "lactate.json", {"speed_raw": [{"date": "2030-01-01", "value": 0.4}], "power_w": []})
        m = load_model(self.root)
        self.assertAlmostEqual(m.lt_pace[0][1], 250.0)          # 4.0 m/s -> 4:10/km

    def test_weather_converted_from_fahrenheit(self):
        write(self.root, "activities.json", {"1": {"id": 1, "type": "running", "start_local": "2030-01-01 08:00:00",
                                                   "distance_m": 10000, "duration_s": 3000}})
        write(self.root, "activity/1/weather.json", {"temp_raw": 59, "dew_point_raw": 50})
        a = load_model(self.root).activities[0]
        self.assertAlmostEqual(a.temp_c, 15.0)
        self.assertFalse(a.temp_suspect)

    def test_dew_point_above_temperature_is_suspect(self):
        write(self.root, "activities.json", {"1": {"id": 1, "type": "running", "start_local": "2030-01-01 08:00:00"}})
        write(self.root, "activity/1/weather.json", {"temp_raw": 12, "dew_point_raw": 40})
        self.assertTrue(load_model(self.root).activities[0].temp_suspect)

    def test_sleep_local_millis_are_wall_clock(self):
        ms = int(datetime(2030, 1, 1, 23, 45).timestamp() * 1000)
        # Garmin's "local" millis are wall-clock time encoded as if it were UTC
        utc_wall = int((datetime(2030, 1, 1, 23, 45) - datetime(1970, 1, 1)).total_seconds() * 1000)
        write(self.root, "sleep.json", {"2030-01-02": {"localSleepStartTimeInMillis": utc_wall,
                                                       "totalSleepTimeInSeconds": 27000, "sleepScore": 80}})
        n = load_model(self.root).nights[date(2030, 1, 2)]
        self.assertEqual((n.bedtime.hour, n.bedtime.minute), (23, 45))
        self.assertEqual(n.hours, 7.5)

    def test_missing_and_zero_values_are_none(self):
        write(self.root, "sleep.json", {"2030-01-02": {"sleepScore": None, "totalSleepTimeInSeconds": 0}})
        n = load_model(self.root).nights[date(2030, 1, 2)]
        self.assertIsNone(n.score)
        self.assertIsNone(n.hours)

    def test_f_to_c(self):
        self.assertAlmostEqual(f_to_c(57), 13.9, places=1)


if __name__ == "__main__":
    unittest.main()
