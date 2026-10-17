"""Turn data/cache/ into typed objects for the metrics.

Garmin's unit quirks are fixed here, in one place:
  - lactate-threshold speed arrives as m/s divided by 10 (0.4417 is 4.417 m/s)
  - station weather arrives in Fahrenheit with no unit field; converted to C and
    flagged suspect when the dew point is above the temperature (impossible)
  - sleep "local millis" are epoch ms already shifted to local wall-clock time
  - lap timestamps are GMT; activity dates come from startTimeLocal

A missing value is None, never 0, so it can't drag an average down.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

RUN_TYPES = {"running", "trail_running", "treadmill_running", "track_running",
             "ultra_run", "virtual_run", "indoor_running", "obstacle_run"}
INDOOR_RUN_TYPES = {"treadmill_running", "indoor_running", "virtual_run"}
BIKE_TYPES = {"cycling", "road_biking", "mountain_biking", "gravel_cycling",
              "indoor_cycling", "virtual_ride", "cyclocross", "e_bike_fitness",
              "downhill_biking", "recumbent_cycling"}
STRENGTH_TYPES = {"strength_training", "functional_strength_training", "weight_training"}
INTERVAL_TYPES = {"INTERVAL_WARMUP", "INTERVAL_COOLDOWN", "INTERVAL_RECOVERY", "INTERVAL_REST"}


def num(v: Any, allow_zero: bool = False) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f < 0 or (f == 0 and not allow_zero):
        return None
    return f


REP_PACE_SPREAD = 1.15      # a lap this much slower than the step's fastest is a recovery


def split_steps(steps: list[dict] | None, laps: list[dict] | None) -> list[dict] | None:
    """Workout steps (typed splits) refined by the watch's laps.

    A step normally is one lap. When a run doesn't follow the programmed
    workout - an extra rep, the lap button pressed inside a step - one ACTIVE
    step covers several laps: reps and the jogs between them. Those laps are
    taken instead, the ones near the step's fastest pace as reps and the
    slower ones as recoveries."""
    if not steps or not laps:
        return steps
    starts = [_dt(s.get("start_gmt")) for s in steps]
    lap_starts = [(_dt(l.get("start_gmt")), l) for l in laps]
    out: list[dict] = []
    for i, step in enumerate(steps):
        lo, hi = starts[i], starts[i + 1] if i + 1 < len(steps) else None
        inside = [l for t, l in lap_starts if lo and t and lo <= t and (hi is None or t < hi)]
        if step.get("intensity") != "ACTIVE" or len(inside) < 2:
            out.append(step)
            continue
        speeds = [num(l.get("avg_speed")) for l in inside]
        fastest = max((v for v in speeds if v), default=None)
        for l, v in zip(inside, speeds):
            rep = bool(fastest and v and fastest / v <= REP_PACE_SPREAD)
            out.append({**l, "intensity": "ACTIVE" if rep else "RECOVERY"})
    return [{**row, "n": i + 1} for i, row in enumerate(out)]


def _dt(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace(" ", "T")[:19])
    except ValueError:
        return None


def _wall(ms: Any) -> datetime | None:
    m = num(ms)
    return datetime.fromtimestamp(m / 1000, tz=timezone.utc).replace(tzinfo=None) if m else None


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def pace_s_per_km(speed_mps: float | None) -> float | None:
    return 1000.0 / speed_mps if speed_mps and speed_mps > 0.3 else None


# --------------------------------------------------------------------------
@dataclass
class Lap:
    n: int
    distance_m: float | None
    duration_s: float | None
    avg_hr: float | None
    avg_speed: float | None
    intensity: str | None

    @property
    def pace(self) -> float | None:
        if self.distance_m and self.duration_s:
            return self.duration_s / (self.distance_m / 1000.0)
        return pace_s_per_km(self.avg_speed)


@dataclass
class Activity:
    id: str
    name: str
    type: str
    event: str | None
    start: datetime
    distance_m: float | None
    duration_s: float | None
    elapsed_s: float | None
    avg_hr: float | None
    max_hr: float | None
    elev_gain_m: float | None
    avg_power: float | None
    intervals: list[str]
    laps: list[Lap] | None = None
    series: dict[str, list] | None = None
    temp_c: float | None = None
    temp_suspect: bool = False

    @property
    def sport(self) -> str:
        if self.type in RUN_TYPES:
            return "run"
        if self.type in BIKE_TYPES:
            return "bike"
        if self.type in STRENGTH_TYPES:
            return "strength"
        return "other"

    @property
    def day(self) -> date:
        return self.start.date()

    @property
    def km(self) -> float:
        return (self.distance_m or 0.0) / 1000.0

    @property
    def hours(self) -> float:
        return (self.duration_s or 0.0) / 3600.0

    @property
    def end(self) -> datetime:
        return self.start + timedelta(seconds=self.elapsed_s or self.duration_s or 0)

    @property
    def elev_per_km(self) -> float | None:
        if self.elev_gain_m is None or self.km < 0.5:
            return None
        return self.elev_gain_m / self.km

    @property
    def structured(self) -> bool:
        return "INTERVAL_ACTIVE" in self.intervals and bool(INTERVAL_TYPES & set(self.intervals))

    @property
    def indoor(self) -> bool:
        return self.type in INDOOR_RUN_TYPES or self.type in {"indoor_cycling", "virtual_ride"}

    @property
    def pace(self) -> float | None:
        if self.km > 0.2 and self.duration_s:
            return self.duration_s / self.km
        return None


@dataclass
class Night:
    day: date                   # the morning you woke up
    bedtime: datetime | None
    wake: datetime | None
    hours: float | None
    score: float | None
    rhr: float | None
    deep_s: float | None
    light_s: float | None
    rem_s: float | None
    awake_s: float | None
    need_min: float | None
    hrv: float | None


@dataclass
class HrvDay:
    day: date
    last_night: float | None
    weekly_avg: float | None
    status: str | None
    base_low: float | None
    base_high: float | None


@dataclass
class Readiness:
    day: date
    at: datetime | None
    score: float | None
    level: str | None
    recovery_h: float | None
    sleep_history_pct: float | None
    hrv_weekly: float | None
    context: str | None = None
    sleep_history_fb: str | None = None     # Garmin's own verdict: GOOD / MODERATE / POOR ...


@dataclass
class LoadDay:
    day: date
    acute: float
    chronic: float

    @property
    def ratio(self) -> float | None:
        return self.acute / self.chronic if self.chronic else None


@dataclass
class Model:
    activities: list[Activity] = field(default_factory=list)
    nights: dict[date, Night] = field(default_factory=dict)
    hrv: dict[date, HrvDay] = field(default_factory=dict)
    readiness: dict[date, Readiness] = field(default_factory=dict)        # morning reading
    readiness_now: dict[date, Readiness] = field(default_factory=dict)    # latest reading
    load: dict[date, LoadDay] = field(default_factory=dict)
    lt_pace: list[tuple[date, float]] = field(default_factory=list)    # s/km
    lt_power: list[tuple[date, float]] = field(default_factory=list)   # W, running
    synced_at: datetime | None = None

    def current_readiness(self, d: date) -> Readiness | None:
        """The latest reading of the day. Readiness and recovery time drop after
        every workout, so the newest value is the one that counts; the overnight
        parts (sleep history, HRV) are identical in every reading of a day."""
        cands = [r for r in (self.readiness.get(d), self.readiness_now.get(d)) if r]
        return max(cands, key=lambda r: r.at or datetime.min) if cands else None

    def lt_pace_on(self, d: date) -> float | None:
        """LT pace in force on day d: the latest value on or before it. Sessions
        older than the first known value use that first value."""
        if not self.lt_pace:
            return None
        before = [p for day, p in self.lt_pace if day <= d]
        return before[-1] if before else self.lt_pace[0][1]

    def in_range(self, start: date, end_exclusive: date) -> list[Activity]:
        return [a for a in self.activities if start <= a.day < end_exclusive]


# --------------------------------------------------------------------------
def _read(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def load_model(cache: Path) -> Model:
    m = Model()

    for a in _read(cache / "activities.json", {}).values():
        start = _dt(a.get("start_local"))
        if not start:
            continue
        act = Activity(
            id=str(a.get("id")), name=a.get("name") or "", type=a.get("type") or "",
            event=a.get("event"), start=start,
            distance_m=num(a.get("distance_m")), duration_s=num(a.get("duration_s")),
            elapsed_s=num(a.get("elapsed_s")), avg_hr=num(a.get("avg_hr")),
            max_hr=num(a.get("max_hr")), elev_gain_m=num(a.get("elev_gain_m"), allow_zero=True),
            avg_power=num(a.get("avg_power")), intervals=list(a.get("intervals") or []))
        folder = cache / "activity" / act.id
        laps = split_steps(_read(folder / "intervals.json", None), _read(folder / "laps.json", None))
        if laps:
            act.laps = [Lap(n=l.get("n") or i + 1, distance_m=num(l.get("distance_m")),
                            duration_s=num(l.get("duration_s")), avg_hr=num(l.get("avg_hr")),
                            avg_speed=num(l.get("avg_speed")), intensity=l.get("intensity"))
                        for i, l in enumerate(laps)]
        series = _read(folder / "series.json", None)
        if series and series.get("t"):
            act.series = series
        w = _read(folder / "weather.json", None)
        if w and w.get("temp_raw") is not None:
            t = f_to_c(float(w["temp_raw"]))
            dp = w.get("dew_point_raw")
            act.temp_c = round(t, 1)
            act.temp_suspect = (dp is not None and float(dp) > float(w["temp_raw"]) + 1) or not -40 < t < 50
        m.activities.append(act)
    m.activities.sort(key=lambda a: a.start)

    for ds, v in _read(cache / "sleep.json", {}).items():
        d = date.fromisoformat(ds)
        total = num(v.get("totalSleepTimeInSeconds"))
        m.nights[d] = Night(
            day=d, bedtime=_wall(v.get("localSleepStartTimeInMillis")),
            wake=_wall(v.get("localSleepEndTimeInMillis")),
            hours=total / 3600.0 if total else None, score=num(v.get("sleepScore")),
            rhr=num(v.get("restingHeartRate")), deep_s=num(v.get("deepTime"), True),
            light_s=num(v.get("lightTime"), True), rem_s=num(v.get("remTime"), True),
            awake_s=num(v.get("awakeTime"), True), need_min=num(v.get("sleepNeed")),
            hrv=num(v.get("avgOvernightHrv")))

    for ds, v in _read(cache / "hrv.json", {}).items():
        d = date.fromisoformat(ds)
        m.hrv[d] = HrvDay(day=d, last_night=num(v.get("last_night")),
                          weekly_avg=num(v.get("weekly_avg")), status=v.get("status"),
                          base_low=num(v.get("base_low")), base_high=num(v.get("base_high")))

    for folder, target in (("readiness", m.readiness), ("readiness_now", m.readiness_now)):
        for p in sorted((cache / folder).glob("*.json")):
            r = _read(p, {})
            if r.get("score") is None:
                continue
            d = date.fromisoformat(r.get("calendarDate") or p.stem)
            rec = num(r.get("recoveryTime"), allow_zero=True)
            target[d] = Readiness(
                day=d, at=_dt(r.get("timestampLocal")), score=num(r.get("score"), True),
                level=r.get("level"), recovery_h=rec / 60.0 if rec is not None else None,
                sleep_history_pct=num(r.get("sleepHistoryFactorPercent"), True),
                hrv_weekly=num(r.get("hrvWeeklyAverage")), context=r.get("inputContext"),
                sleep_history_fb=r.get("sleepHistoryFactorFeedback"))

    for p in sorted((cache / "load").glob("*.json")):
        r = _read(p, {})
        a, c = num(r.get("acute"), True), num(r.get("chronic"))
        if a is not None and c:
            d = date.fromisoformat(p.stem)
            m.load[d] = LoadDay(day=d, acute=a, chronic=c)

    lt = _read(cache / "lactate.json", {})
    for row in lt.get("speed_raw") or []:
        raw = num(row.get("value"))
        if raw and row.get("date"):
            mps = raw * 10.0                      # Garmin quirk: m/s / 10
            m.lt_pace.append((date.fromisoformat(row["date"][:10]), 1000.0 / mps))
    for row in lt.get("power_w") or []:
        w = num(row.get("value"))
        if w and row.get("date"):
            m.lt_power.append((date.fromisoformat(row["date"][:10]), w))
    m.lt_pace.sort()
    m.lt_power.sort()

    meta = _read(cache / "meta.json", {})
    m.synced_at = _dt(meta.get("synced_at"))
    return m
