#!/usr/bin/env python3
"""Pull training data from Garmin Connect into data/cache/, incrementally.

Everything Garmin returns that cannot change later (a finished activity's laps,
samples and weather; a past day's load) is fetched once and kept. Each sync only
re-pulls today and a short recent window, so it takes seconds after the first run.

Cache layout (data/cache/):
  activities.json              {activityId: summary}     whole season once a day, else last 21 days
  sleep.json                   {date: night}             last 10 days + dates not yet covered
  hrv.json                     {date: summary}           last 10 days + dates not yet covered
  coverage.json                which dates are final     see below
  readiness/<date>.json        morning readiness         today + missing recent days
  load/<date>.json             acute + chronic load      re-pulled until fetched 2+ days after the day
                                                         (fetched_on; a no-data day gets a marker)

A date counts as covered once a successful request returned it after the
re-pull window had passed. Failed requests leave dates uncovered, so a later
sync fills the gap; covered dates are not asked for again.
  lactate.json                 LT speed + running power  re-pulled (one call)
  activity/<id>/intervals.json typed interval splits     once, structured runs
  activity/<id>/laps.json      lap-button laps           once, structured runs (finer than the steps)
  activity/<id>/series.json    HR / speed / power        once, unstructured runs and rides
  activity/<id>/weather.json   station weather (raw °F)  once, outdoor runs

Only whitelisted fields are written. Training-status labels, VO2 max, body
battery, performance condition, LT heart rate and Garmin FTP never reach disk,
so they cannot leak into a page.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from garminconnect import Garmin

import settings
from model import BIKE_TYPES, INDOOR_RUN_TYPES, INTERVAL_TYPES, RUN_TYPES

ROOT = Path(__file__).parent
CACHE = ROOT / "data" / "cache"
_SYNC = settings.load_athlete()["sync"]
TOKENSTORE = str(Path(_SYNC["tokenstore"]).expanduser())

DETAIL_DAYS = _SYNC["detail_days"]                  # per-activity data and load history
WELLNESS_DAYS = _SYNC["wellness_days"]              # sleep/HRV history (health page needs 90)
RECENT_ACTIVITY_DAYS = _SYNC["recent_activity_days"]
RECENT_WELLNESS_DAYS = _SYNC["recent_wellness_days"]
CHUNK = 28                  # Garmin caps several range endpoints

_calls = 0
_failures = 0
_successes = 0


# --------------------------------------------------------------------------
# whitelists - the only fields that are ever stored
# --------------------------------------------------------------------------
def slim_activity(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": a.get("activityId"),
        "name": a.get("activityName") or "",
        "type": (a.get("activityType") or {}).get("typeKey"),
        "event": (a.get("eventType") or {}).get("typeKey"),
        "start_local": a.get("startTimeLocal"),
        "start_gmt": a.get("startTimeGMT"),
        "distance_m": a.get("distance"),
        "duration_s": a.get("duration"),
        "elapsed_s": a.get("elapsedDuration"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "elev_gain_m": a.get("elevationGain"),
        "avg_power": a.get("avgPower"),
        "intervals": sorted({s.get("splitType") for s in (a.get("splitSummaries") or [])
                             if str(s.get("splitType", "")).startswith("INTERVAL_")}),
    }


def slim_night(v: dict[str, Any]) -> dict[str, Any]:
    keys = ("totalSleepTimeInSeconds", "sleepScore", "sleepNeed", "restingHeartRate",
            "deepTime", "lightTime", "remTime", "awakeTime",
            "localSleepStartTimeInMillis", "localSleepEndTimeInMillis",
            "avgOvernightHrv", "hrv7dAverage")
    return {k: v.get(k) for k in keys}


def slim_hrv(s: dict[str, Any]) -> dict[str, Any]:
    b = s.get("baseline") or {}
    return {"last_night": s.get("lastNightAvg"), "weekly_avg": s.get("weeklyAvg"),
            "status": s.get("status"), "base_low": b.get("balancedLow"),
            "base_high": b.get("balancedUpper")}


def slim_readiness(r: dict[str, Any]) -> dict[str, Any]:
    keys = ("calendarDate", "timestampLocal", "level", "score", "recoveryTime",
            "sleepHistoryFactorPercent", "sleepHistoryFactorFeedback", "hrvWeeklyAverage",
            "inputContext")
    return {k: r.get(k) for k in keys}


def slim_load(ts: dict[str, Any]) -> dict[str, Any] | None:
    """Only acute and chronic load. The status label, VO2 max and load-balance
    blocks in the same response are deliberately dropped."""
    data = ((ts or {}).get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
    for dev in data.values():
        dto = (dev or {}).get("acuteTrainingLoadDTO") or {}
        if dto.get("dailyTrainingLoadAcute") is not None:
            return {"date": dev.get("calendarDate"),
                    "acute": dto.get("dailyTrainingLoadAcute"),
                    "chronic": dto.get("dailyTrainingLoadChronic")}
    return None


def slim_lactate(rng: dict[str, Any], latest: dict[str, Any]) -> dict[str, Any]:
    """Raw LT speed is stored as Garmin sends it; model.py applies the x10 fix.
    LT heart rate is not stored."""
    speed = [{"date": p.get("from"), "value": p.get("value")}
             for p in (rng or {}).get("speed") or [] if p.get("series") == "running"]
    power = [{"date": p.get("from"), "value": p.get("value")}
             for p in (rng or {}).get("power") or [] if p.get("series") == "running"]
    sh = (latest or {}).get("speed_and_heart_rate") or {}
    pw = (latest or {}).get("power") or {}
    if sh.get("speed") is not None:
        speed.append({"date": str(sh.get("calendarDate", ""))[:10], "value": sh.get("speed")})
    if pw.get("functionalThresholdPower") is not None and pw.get("sport") == "RUNNING":
        power.append({"date": str(pw.get("calendarDate", ""))[:10],
                      "value": pw.get("functionalThresholdPower")})
    dedupe = lambda xs: sorted({(x["date"], x["value"]) for x in xs if x["date"]})
    return {"speed_raw": [{"date": d, "value": v} for d, v in dedupe(speed)],
            "power_w": [{"date": d, "value": v} for d, v in dedupe(power)]}


def slim_intervals(ts: dict[str, Any]) -> list[dict[str, Any]]:
    """Typed splits, INTERVAL_* entries only. Lap DTOs aren't enough: runs done
    with the watch's Intervals feature label every lap plain "INTERVAL", so
    reps and recoveries can only be told apart here. The RWD_* run/walk
    detection splits in the same response are dropped."""
    rows = [s for s in (ts or {}).get("splits") or []
            if str(s.get("type", "")).startswith("INTERVAL_")]
    rows.sort(key=lambda s: (str(s.get("startTimeGMT")), s.get("messageIndex") or 0))
    return [{"n": i + 1, "start_gmt": s.get("startTimeGMT"), "distance_m": s.get("distance"),
             "duration_s": s.get("duration"), "avg_hr": s.get("averageHR"),
             "max_hr": s.get("maxHR"), "avg_speed": s.get("averageSpeed"),
             "intensity": str(s["type"]).removeprefix("INTERVAL_")}
            for i, s in enumerate(rows)]


def slim_laps(sp: dict[str, Any]) -> list[dict[str, Any]]:
    """The watch's own laps. A workout step can cover several of them - e.g. an
    extra rep run inside one step - so model.py uses them to split such steps."""
    return [{"n": i + 1, "start_gmt": l.get("startTimeGMT"), "distance_m": l.get("distance"),
             "duration_s": l.get("duration"), "avg_hr": l.get("averageHR"),
             "max_hr": l.get("maxHR"), "avg_speed": l.get("averageSpeed")}
            for i, l in enumerate((sp or {}).get("lapDTOs") or [])]


SERIES_KEYS = {"sumDuration": "t", "sumDistance": "d", "directSpeed": "v",
               "directHeartRate": "hr", "directPower": "p", "directElevation": "elev"}


def slim_series(det: dict[str, Any]) -> dict[str, list] | None:
    """Columnar samples, by metric key (Garmin's index order varies per activity).
    Body battery and performance condition samples are not copied."""
    idx = {m["key"]: m["metricsIndex"] for m in (det or {}).get("metricDescriptors") or []}
    rows = (det or {}).get("activityDetailMetrics") or []
    if not rows or "sumDuration" not in idx:
        return None
    out: dict[str, list] = {}
    for key, short in SERIES_KEYS.items():
        if key in idx:
            i = idx[key]
            out[short] = [(r.get("metrics") or [None] * (i + 1))[i] for r in rows]
    return out


def slim_weather(w: dict[str, Any]) -> dict[str, Any] | None:
    if not w or w.get("temp") is None:
        return None
    # Garmin's station weather is Fahrenheit with no unit field, whatever the
    # account's units. Stored raw; model.py converts and sanity-checks.
    return {"temp_raw": w.get("temp"), "dew_point_raw": w.get("dewPoint"),
            "humidity": w.get("relativeHumidity"), "issued": w.get("issueDate")}


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------
def fetch(name: str, fn: Callable[..., Any], *args: Any) -> Any:
    global _calls, _failures, _successes
    _calls += 1
    try:
        res = fn(*args)
        _successes += 1
        time.sleep(0.3)
        return res
    except Exception as exc:
        _failures += 1
        print(f"    ! {name}: {type(exc).__name__}: {str(exc)[:160]}")
        return None


def read_json(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), default=str))
    tmp.replace(p)


def windows(start: date, end: date, size: int):
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=size - 1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


# --------------------------------------------------------------------------
# sync steps
# --------------------------------------------------------------------------
def load_coverage() -> dict[str, Any]:
    return read_json(CACHE / "coverage.json", {})


def save_coverage(cov: dict[str, Any]) -> None:
    write_json(CACHE / "coverage.json", cov)


def sync_activities(c: Garmin, today: date, cov: dict[str, Any]) -> dict[str, dict]:
    path = CACHE / "activities.json"
    cached: dict[str, dict] = read_json(path, {})
    season = settings.load_season()
    first = today - timedelta(days=WELLNESS_DAYS)
    if season.week1_start and season.week1_start < first:
        first = season.week1_start
    # The whole season once a day, so edits made in Garmin to older activities
    # (a race tagged as race, a rename, a deletion) arrive; later syncs that day
    # only re-pull the recent window. Still one call either way.
    full = cov.get("activities_full_on") != today.isoformat() or not cached
    start = first if full else today - timedelta(days=RECENT_ACTIVITY_DAYS)
    fresh = fetch("activities", c.get_activities_by_date, start.isoformat(), today.isoformat())
    if fresh is None:
        return cached
    # replace the whole window, so activities deleted or edited in Garmin follow
    cutoff = start.isoformat()
    kept = {k: v for k, v in cached.items() if (v.get("start_local") or "")[:10] < cutoff}
    for a in fresh:
        s = slim_activity(a)
        kept[str(s["id"])] = s
    write_json(path, kept)
    if full:
        cov["activities_full_on"] = today.isoformat()
    print(f"  activities       {len(fresh):4d} fetched since {start}, {len(kept)} cached")
    return kept


def runs_of(days: list[date], size: int):
    """Contiguous stretches of `days`, each cut to at most `size` days."""
    run: list[date] = []
    for d in sorted(days):
        if run and (d - run[-1]).days != 1:
            yield from windows(run[0], run[-1], size)
            run = []
        run.append(d)
    if run:
        yield from windows(run[0], run[-1], size)


def sync_by_date(c: Garmin, name: str, fn: Callable[..., Any], today: date,
                 extract: Callable[[Any], list[tuple[str, dict]]], cov: dict[str, Any]) -> None:
    path = CACHE / f"{name}.json"
    cached: dict[str, dict] = read_json(path, {})
    start = today - timedelta(days=WELLNESS_DAYS)
    final = today - timedelta(days=RECENT_WELLNESS_DAYS)
    covered = {d for d in cov.get(name, []) if d >= start.isoformat()} if cached else set()
    wanted = [start + timedelta(days=i) for i in range((today - start).days + 1)]
    wanted = [d for d in wanted if d > final or d.isoformat() not in covered]
    n = 0
    for a, b in runs_of(wanted, CHUNK):
        res = fetch(name, fn, a.isoformat(), b.isoformat())
        if res is None:
            continue                        # stays uncovered: the next sync asks again
        for d, row in extract(res):
            cached[d] = row
            n += 1
        covered |= {(a + timedelta(days=i)).isoformat() for i in range((min(b, final) - a).days + 1)}
    cov[name] = sorted(covered)
    if n or not cached:
        write_json(path, cached)
    print(f"  {name:<16} {n:4d} days updated, {len(cached)} cached")


def _sleep_rows(res: Any) -> list[tuple[str, dict]]:
    return [(r["calendarDate"], slim_night(r.get("values") or {}))
            for r in res or [] if r.get("calendarDate")]


def _hrv_rows(res: Any) -> list[tuple[str, dict]]:
    return [(s["calendarDate"], slim_hrv(s))
            for s in (res or {}).get("hrvSummaries") or [] if s.get("calendarDate")]


def sync_readiness(c: Garmin, today: date) -> None:
    n = 0
    for i in range(7):
        d = today - timedelta(days=i)
        p = CACHE / "readiness" / f"{d.isoformat()}.json"
        if p.exists() and i > 0:
            continue
        r = fetch("readiness", c.get_morning_training_readiness, d.isoformat())
        if r and r.get("score") is not None:
            write_json(p, slim_readiness(r))
            n += 1
    # The app shows the latest reading, which drops after exercise. Keep it too,
    # so the dashboard can show both instead of looking wrong next to the app.
    rows = fetch("readiness", c.get_training_readiness, today.isoformat())
    if isinstance(rows, list) and rows:
        latest = max(rows, key=lambda r: str(r.get("timestampLocal") or ""))
        if latest.get("score") is not None:
            write_json(CACHE / "readiness_now" / f"{today.isoformat()}.json", slim_readiness(latest))
    print(f"  readiness        {n:4d} days updated")


def load_is_final(p: Path, d: date) -> bool:
    """A day's load is final once it was fetched two or more days after the day:
    a snapshot taken during the day (or before a late watch upload) is not. The
    fetch date is stored in the file; files from before that field fall back to
    their mtime."""
    row = read_json(p, None)
    if not isinstance(row, dict):
        return False
    try:
        fetched = (date.fromisoformat(row["fetched_on"]) if row.get("fetched_on")
                   else date.fromtimestamp(p.stat().st_mtime))
    except (OSError, ValueError):
        return False
    return fetched >= d + timedelta(days=2)


def sync_load(c: Garmin, today: date) -> None:
    n = 0
    for i in range(DETAIL_DAYS):
        d = today - timedelta(days=i)
        p = CACHE / "load" / f"{d.isoformat()}.json"
        if load_is_final(p, d):
            continue
        res = fetch("load", c.get_training_status, d.isoformat())
        if res is None:
            continue                             # failed: ask again next sync
        # A day without load data gets a marker too, so it isn't asked for again
        # on every sync once it is final.
        row = slim_load(res) or {"date": d.isoformat(), "acute": None, "chronic": None}
        row["fetched_on"] = today.isoformat()
        write_json(p, row)
        n += 1
    print(f"  load             {n:4d} days updated")


def sync_lactate(c: Garmin, today: date) -> None:
    rng = fetch("lactate", lambda: c.get_lactate_threshold(
        latest=False, start_date=(today - timedelta(days=WELLNESS_DAYS)).isoformat(),
        end_date=today.isoformat()))
    latest = fetch("lactate", lambda: c.get_lactate_threshold(latest=True))
    if rng is None and latest is None:
        return
    # Merge into what is cached: the range call only covers the last 120 days, so
    # replacing the file would drop older LT points the season's early weeks need.
    previous = read_json(CACHE / "lactate.json", {})
    fresh = slim_lactate(rng or {}, latest or {})
    lt = {}
    for key in ("speed_raw", "power_w"):
        by_date = {x["date"]: x["value"] for x in previous.get(key, []) if x.get("date")}
        by_date.update({x["date"]: x["value"] for x in fresh[key]})   # fresh wins for a date
        lt[key] = [{"date": d, "value": v} for d, v in sorted(by_date.items())]
    write_json(CACHE / "lactate.json", lt)
    print(f"  lactate          {len(lt['speed_raw']):4d} pace points, {len(lt['power_w'])} power points")


def sync_activity_detail(c: Garmin, acts: dict[str, dict], today: date) -> None:
    since = (today - timedelta(days=DETAIL_DAYS)).isoformat()
    got = {"intervals": 0, "laps": 0, "series": 0, "weather": 0}
    for a in acts.values():
        if (a.get("start_local") or "")[:10] < since:
            continue
        aid, t = str(a["id"]), a.get("type")
        folder = CACHE / "activity" / aid
        structured = "INTERVAL_ACTIVE" in a.get("intervals", []) and \
            bool(INTERVAL_TYPES & set(a.get("intervals", [])))
        want = []
        if t in RUN_TYPES:
            want += ["intervals", "laps"] if structured else ["series"]
            if t not in INDOOR_RUN_TYPES:
                want.append("weather")
        elif t in BIKE_TYPES:
            want.append("series")
        for kind in want:
            p = folder / f"{kind}.json"
            if p.exists():
                continue
            if kind == "intervals":
                raw = fetch("intervals", c.get_activity_typed_splits, aid)
                if raw is None:
                    continue
                obj = slim_intervals(raw)
            elif kind == "laps":
                raw = fetch("laps", c.get_activity_splits, aid)
                if raw is None:
                    continue
                obj = slim_laps(raw)
            elif kind == "series":
                raw = fetch("series", c.get_activity_details, aid)
                if raw is None:
                    continue
                obj = slim_series(raw)
            else:
                raw = fetch("weather", c.get_activity_weather, aid)
                if raw is None:
                    continue
                obj = slim_weather(raw)
            write_json(p, obj)
            got[kind] += 1
    print(f"  per-activity     intervals {got['intervals']}, laps {got['laps']}, series {got['series']}, "
          f"weather {got['weather']}")


def main() -> int:
    today = date.today()
    print(f"Syncing Garmin data up to {today}\n")
    c = Garmin()
    try:
        c.login(tokenstore=TOKENSTORE)
    except Exception as exc:
        print(f"Not logged in ({type(exc).__name__}). Run:  ./run.sh login")
        return 1

    t0 = time.time()
    cov = load_coverage()
    acts = sync_activities(c, today, cov)
    sync_by_date(c, "sleep", c.get_sleep_daily, today, _sleep_rows, cov)
    sync_by_date(c, "hrv", c.get_hrv_data_range, today, _hrv_rows, cov)
    save_coverage(cov)
    sync_readiness(c, today)
    sync_load(c, today)
    sync_lactate(c, today)
    sync_activity_detail(c, acts, today)
    if _successes == 0:
        print("\nSync failed: no Garmin data was retrieved.")
        return 1
    write_json(CACHE / "meta.json", {"synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                     "failed_calls": _failures})
    print(f"\nDone. {_calls} API calls in {time.time() - t0:.0f}s -> {CACHE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
