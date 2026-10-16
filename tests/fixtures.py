"""Synthetic fixtures. Shaped like real Garmin data, but every value is made up -
no personal data belongs in the repo."""
from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta
from itertools import count

import settings as S
from model import Activity, Lap, LoadDay, Model, Night, Readiness

MON = date(2030, 3, 4)              # a Monday
LT_PACE = 240.0                     # 4:00/km
_ids = count(1000)


def cfg(**sections) -> dict:
    c = copy.deepcopy(S.DEFAULTS)
    c["threshold"].update(cap_long_bpm=165, cap_short_bpm=170)
    for k, v in sections.items():
        c[k].update(v)
    return c


def lap(n: int, dist: float, secs: float, hr: float | None, intensity: str = "ACTIVE") -> Lap:
    return Lap(n, dist, secs, hr, dist / secs if secs else None, intensity)


def series(*segments: tuple[float, float, float, float | None], step: float = 2.0) -> dict:
    """segments of (minutes, hr, speed m/s, power W or None)."""
    t, hr, v, p = [], [], [], []
    now = 0.0
    for minutes, h, sp, pw in segments:
        for _ in range(int(minutes * 60 / step)):
            now += step
            t.append(now); hr.append(h); v.append(sp); p.append(pw)
    out = {"t": t, "hr": hr, "v": v}
    if any(x is not None for x in p):
        out["p"] = p
    return out


def act(day: int = 0, hour: int = 8, type: str = "running", km: float = 10.0,
        minutes: float = 50.0, avg_hr: float | None = 140, elev: float | None = 0.0,
        name: str = "Run", event: str | None = None, structured: bool = False,
        laps: list[Lap] | None = None, ser: dict | None = None,
        temp_c: float | None = 12.0) -> Activity:
    a = Activity(
        id=str(next(_ids)), name=name, type=type, event=event,
        start=datetime.combine(MON + timedelta(days=day), time(hour)),
        distance_m=km * 1000, duration_s=minutes * 60, elapsed_s=minutes * 60,
        avg_hr=avg_hr, max_hr=None, elev_gain_m=elev, avg_power=None,
        intervals=["INTERVAL_ACTIVE", "INTERVAL_WARMUP", "INTERVAL_COOLDOWN"] if structured else ["INTERVAL_ACTIVE"],
        laps=laps, series=ser, temp_c=temp_c)
    return a


def subt_session(day: int, rep_hrs: list[float], rep_m: float = 1600, rep_pace: float = 250.0,
                 hour: int = 18) -> Activity:
    laps = [lap(1, 3000, 1000, 135, "WARMUP")]
    for i, h in enumerate(rep_hrs):
        laps.append(lap(len(laps) + 1, rep_m, rep_m / 1000 * rep_pace, h))
        laps.append(lap(len(laps) + 1, 200, 60, 150, "REST"))
    laps.append(lap(len(laps) + 1, 2000, 700, 130, "COOLDOWN"))
    km = sum(l.distance_m for l in laps) / 1000
    return act(day=day, hour=hour, km=km, minutes=sum(l.duration_s for l in laps) / 60,
               avg_hr=150, structured=True, laps=laps)


def night(day: date, bed: str = "23:00", hours: float = 7.5, score: float = 80) -> Night:
    hh, mm = map(int, bed.split(":"))
    bedtime = datetime.combine(day - timedelta(days=1 if hh >= 12 else 0), time(hh, mm))
    return Night(day, bedtime, bedtime + timedelta(hours=hours), hours, score,
                 50, None, None, None, None, None, None)


def model(*acts: Activity, nights=(), readiness=None, readiness_now=None, load=None) -> Model:
    m = Model(activities=sorted(acts, key=lambda a: a.start))
    m.lt_pace = [(MON - timedelta(days=60), LT_PACE)]
    for n in nights:
        m.nights[n.day] = n
    if readiness:
        m.readiness[readiness.day] = readiness
    if readiness_now:
        m.readiness_now[readiness_now.day] = readiness_now
    for d, a, c in load or []:
        m.load[d] = LoadDay(d, a, c)
    return m


def readiness(day: date, level: str = "HIGH", score: float = 80, recovery_h: float = 8,
              at_hour: int = 7) -> Readiness:
    return Readiness(day, datetime.combine(day, time(at_hour)), score, level, recovery_h, 80, 60)


def season(**kw) -> S.Season:
    return S.Season(week1_start=MON - timedelta(weeks=10), **kw)
