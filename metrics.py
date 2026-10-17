"""Metric definitions from spec v4.0. Pure functions: model + settings in, results out.

Status vocabulary (maps to the spec's colours):
  good    green  on target
  warn    amber  drifting
  act     red    act now
  none    grey   no data
  neutral        in progress, or no target to judge against

Weekly totals are judged fairly while the week is running: a cap that is already
exceeded flags at once, but falling short of a target only flags once the week is
over, or once the target can no longer be reached.
"""
from __future__ import annotations

import statistics
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import settings as S
from model import Activity, Model, Night, Readiness


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


# --------------------------------------------------------------------------
# reps and sessions
# --------------------------------------------------------------------------
@dataclass
class Rep:
    lap_n: int
    distance_m: float
    duration_s: float
    pace: float | None          # s/km
    avg_hr: float | None
    cap: int
    kind: str                   # subt | speed | aerobic
    within: bool | None         # sub-T reps only; None when HR is missing
    near: bool = False          # above the cap but inside cap_tolerance_bpm: counts as within
    pace_verdict: str | None = None   # speed reps with a planned pace band: good | warn | act


def reps_for(act: Activity, lt_pace: float | None, cfg: dict) -> list[Rep]:
    """ACTIVE intervals of a structured run, junk laps removed.

    The session is classified by its median rep pace against the LT pace in
    force that day: more than speed_margin_s faster means a speed session, and
    none of its reps count. Otherwise every rep is a sub-T rep judged against
    the cap - including one run too fast, because catching that rep is the
    point of the cap. Reps far slower than LT (structured easy steps) are
    aerobic and don't count either way."""
    th = cfg["threshold"]
    laps = [l for l in act.laps or [] if l.intensity == "ACTIVE"
            and (l.distance_m or 0) >= th["min_rep_m"] and (l.duration_s or 0) >= th["min_rep_s"]]
    paces = sorted(l.pace for l in laps if l.pace)
    median = paces[len(paces) // 2] if paces else None
    speed = median is not None and lt_pace is not None and median < lt_pace - th["speed_margin_s"]
    out = []
    for lap in laps:
        pace = lap.pace
        cap = th["cap_long_bpm"] if lap.distance_m >= th["long_rep_m"] else th["cap_short_bpm"]
        if speed:
            kind = "speed"
        elif pace is not None and lt_pace is not None and pace > lt_pace + th["subt_max_slower_s"]:
            kind = "aerobic"
        else:
            kind = "subt"
        within, near = None, False
        if kind == "subt" and lap.avg_hr is not None:
            # "under the cap" = at or below it; within the tolerance counts too, flagged
            long_rep = lap.distance_m >= th["long_rep_m"]
            tol = max(th.get("cap_tolerance_bpm", 0),
                      th.get("cap_tolerance_long_bpm" if long_rep else "cap_tolerance_short_bpm", 0))
            within = lap.avg_hr <= cap + tol
            near = within and lap.avg_hr > cap
        out.append(Rep(lap.n, lap.distance_m, lap.duration_s, pace, lap.avg_hr, cap, kind, within, near))
    return out


def time_above(series: dict[str, list] | None, cap: float) -> tuple[float, float] | None:
    """Seconds above `cap` and total timer seconds, from the HR samples."""
    if not series or not series.get("hr") or not series.get("t"):
        return None
    t, hr = series["t"], series["hr"]
    above = total = 0.0
    for i in range(1, len(t)):
        if t[i] is None or t[i - 1] is None or hr[i] is None:
            continue
        dt = t[i] - t[i - 1]
        if not 0 < dt <= 30:                              # pauses / gaps
            continue
        total += dt
        if hr[i] > cap:
            above += dt
    return (above, total) if total > 0 else None


def threshold_minutes(series: dict[str, list] | None, ftp: float, pct: float) -> float | None:
    """Minutes with 30-s rolling mean power at or above pct x FTP."""
    if not series or not series.get("p") or not series.get("t"):
        return None
    t, p = series["t"], series["p"]
    pts = [(t[i], p[i] or 0.0) for i in range(len(t)) if t[i] is not None]
    if len(pts) < 2:
        return None
    thr, j, acc, secs = pct * ftp, 0, 0.0, 0.0
    for i in range(len(pts)):
        acc += pts[i][1]
        while pts[i][0] - pts[j][0] > 30:
            acc -= pts[j][1]
            j += 1
        if i == 0:
            continue
        dt = pts[i][0] - pts[i - 1][0]
        if 0 < dt <= 30 and acc / (i - j + 1) >= thr:
            secs += dt
    return secs / 60.0


@dataclass
class Session:
    act: Activity
    kind: str          # subt | speed | quality | easy | long | race | test | ride | gym | other
    title: str
    reps: list[Rep] = field(default_factory=list)
    within_km: float = 0.0
    over_km: float = 0.0
    status: str = "neutral"
    notes: list[str] = field(default_factory=list)
    hr_above: tuple[float, float] | None = None      # easy runs: (s above, total s)
    flags: list[str] = field(default_factory=list)   # rides
    thr_min: float | None = None                      # rides
    coach: str = ""                                   # the coach's note from a [[session]] override
    accepted: bool = False                            # verdict accepted by the coach
    breached: bool = False                            # reps over the cap before an accept


def _rep_title(reps: list[Rep], label: str) -> str:
    """'Sub-T 4×1000' when the reps share a length, 'Sub-T 2 reps · 6.0 km' when they don't."""
    if not reps:
        return label
    dists = sorted(r.distance_m for r in reps)
    typical = dists[len(dists) // 2]
    if all(abs(d - typical) <= 0.15 * typical for d in dists):
        rounded = int(round(typical / 100.0) * 100) or int(typical)
        return f"{label} {len(reps)}×{rounded}"
    return f"{label} {len(reps)} reps · {sum(dists) / 1000:.1f} km"


def _apply_override(sessions: list[Session], o: S.SessionOverride, pace_tol: float = 3) -> None:
    """The coach's word on a session. Picks the day's run with reps (or the day's
    only session), optionally by part of its name, then reclassifies and/or
    accepts it. The reps and HRs stay visible; only the judgement changes."""
    day = [s for s in sessions if s.act.day == o.date
           and (not o.name or o.name.lower() in (s.act.name or "").lower())]
    target = next((s for s in day if s.reps), day[0] if len(day) == 1 else None)
    if target is None:
        return
    s = target
    s.coach = o.note or "coach override"
    if o.kind and o.kind != s.kind:
        if o.kind == "speed":
            for r in s.reps:
                if r.kind == "subt":
                    r.kind, r.within, r.near = "speed", None, False
            s.title = _rep_title([r for r in s.reps if r.kind == "speed"], "Speed")
        elif o.kind == "subt":
            pass                                   # sub-T needs the cap check; keep the reps as read
        else:
            s.title = f"{o.kind.title()} {s.act.km:.1f} km"
        if o.kind != "subt":
            s.kind, s.within_km, s.over_km, s.status = o.kind, 0.0, 0.0, "good"
    if o.pace and s.kind == "speed":
        # speed reps are judged on pace against the planned band, not on HR
        fast, slow = o.pace
        for r in s.reps:
            if r.kind != "speed" or r.pace is None:
                continue
            off = max(fast - r.pace, r.pace - slow, 0.0)
            r.pace_verdict = "good" if off == 0 else ("warn" if off <= pace_tol else "act")
        verdicts = [r.pace_verdict for r in s.reps if r.pace_verdict]
        s.status = worst(*verdicts) if verdicts else s.status
    if o.accept:
        s.breached = s.over_km > 0
        s.accepted, s.over_km = True, 0.0
        s.status = "good"


def classify(acts: list[Activity], model: Model, cfg: dict, season: S.Season) -> list[Session]:
    """Sessions of one week. The long run is the longest unstructured run of at
    least long_run.min_minutes; races come from the season plan or Garmin's
    event type, tests from the activity name."""
    out: list[Session] = []
    race_ids: set[str] = set()
    by_day: dict[date, list[Activity]] = {}
    for a in acts:
        by_day.setdefault(a.day, []).append(a)
    for d, day_acts in by_day.items():
        race = season.race_on(d)
        if race:
            cands = [a for a in day_acts if a.sport == race.sport]
            if cands:
                race_ids.add(max(cands, key=lambda a: a.distance_m or 0).id)
    for a in acts:
        if a.event == "race":
            race_ids.add(a.id)

    min_long = cfg["long_run"]["min_minutes"]
    long_cands = [a for a in acts if a.sport == "run" and a.id not in race_ids
                  and not a.structured and not re.search(r"\btest\b", a.name, re.I)
                  and (a.duration_s or 0) >= min_long * 60]
    long_id = max(long_cands, key=lambda a: a.duration_s or 0).id if long_cands else None

    ec = cfg["easy"]
    for a in acts:
        if a.id in race_ids:
            out.append(Session(a, "race", f"Race {a.km:.1f} km"))
            continue
        if re.search(r"\btest\b", a.name, re.I):
            out.append(Session(a, "test", f"Test · {a.name}"))
            continue
        if a.sport == "run":
            if a.structured and a.laps:
                reps = reps_for(a, model.lt_pace_on(a.day), cfg)
                sub = [r for r in reps if r.kind == "subt"]
                spd = [r for r in reps if r.kind == "speed"]
                if sub or spd:
                    s = Session(a, "subt" if sub else "speed",
                                _rep_title(sub, "Sub-T") if sub else _rep_title(spd, "Speed"), reps)
                    s.within_km = sum(r.distance_m for r in sub if r.within) / 1000.0
                    s.over_km = sum(r.distance_m for r in sub if r.within is False) / 1000.0
                    if any(r.within is False for r in sub):
                        s.status = "act"
                    elif sub and all(r.within is None for r in sub):
                        s.status = "none"
                    elif any(r.near for r in sub):
                        s.status = "warn"                 # within the tolerance, not under the cap
                    else:
                        s.status = "good"
                    out.append(s)
                    continue
            if a.id == long_id:
                s = Session(a, "long", f"Long {a.km:.1f} km")
                cap = cfg["long_run"]["hr_cap_bpm"]
                s.status = "none" if a.avg_hr is None else ("act" if a.avg_hr > cap else "good")
                out.append(s)
                continue
            s = Session(a, "easy", f"{'Treadmill' if a.indoor else 'Easy'} {a.km:.1f} km")
            s.hr_above = time_above(a.series, ec["hr_cap_bpm"])
            share = s.hr_above[0] / s.hr_above[1] if s.hr_above else None
            if a.avg_hr is None:
                s.status = "none"
            elif a.avg_hr > ec["hr_cap_bpm"] or (share is not None and share >= ec["max_share_above"]):
                s.status = "act"
            elif share is not None and ec.get("share_green") and share >= ec["share_green"]:
                s.status = "warn"                 # inside the amber zone of time above the cap
            else:
                s.status = "good" if share is not None else "none"   # share unknown: no data, not "fine"
                if share is None:
                    s.notes.append("no HR detail for time above cap")
            out.append(s)
        elif a.sport == "bike":
            out.append(Session(a, "ride", f"{'Indoor ride' if a.indoor else 'Ride'} {a.km:.0f} km"))
        elif a.sport == "strength":
            out.append(Session(a, "gym", "Gym", status="good"))
        else:
            out.append(Session(a, "other", a.name or a.type.replace("_", " ").title()))

    for o in season.sessions:
        _apply_override(out, o, cfg.get("speed", {}).get("pace_tolerance_s", 3))

    # bike discipline needs the whole week's sessions
    bc = cfg["bike"]
    hard_days = {s.act.day for s in out if s.kind in ("subt", "speed", "race")
                 or (s.kind == "test" and s.act.sport == "run")}
    long_day = next((s.act.day for s in out if s.kind == "long"), None)
    for s in out:
        if s.kind != "ride":
            continue
        a = s.act
        if a.day in hard_days:
            s.flags.append(f"ride on a hard-run day ({a.day:%a})")
        if long_day and a.day == long_day - timedelta(days=1):
            s.flags.append("ride the day before the long run")
        if not a.structured and a.avg_hr is not None and a.avg_hr > bc["easy_hr_cap_bpm"]:
            s.flags.append(f"easy ride avg HR {a.avg_hr:.0f} > {bc['easy_hr_cap_bpm']}")
        if a.km > bc["max_km"]:
            s.flags.append(f"ride over {bc['max_km']:.0f} km ({a.km:.0f} km)")
        if bc.get("ftp_w"):
            s.thr_min = threshold_minutes(a.series, bc["ftp_w"], bc["threshold_pct"])
        s.status = "warn" if s.flags else "good"
    out.sort(key=lambda s: s.act.start)
    return out


# --------------------------------------------------------------------------
# weekly summary
# --------------------------------------------------------------------------
@dataclass
class Metric:
    value: float | None
    target: Any = None              # number, (lo, hi), or None
    status: str = "neutral"
    prev: float | None = None       # same point last week, for the direction arrow
    note: str = ""


def _direction(cur: float | None, prev: float | None) -> str:
    if cur is None or prev is None:
        return ""
    if prev == 0:
        return "↑" if cur > 0 else "→"
    ch = (cur - prev) / abs(prev)
    return "↑" if ch > 0.05 else ("↓" if ch < -0.05 else "→")


def run_equiv_km(a: Activity, cfg: dict) -> float:
    bc = cfg["bike"]
    epk = a.elev_per_km or 0.0
    return a.km / (bc["run_equiv_hilly"] if epk >= bc["hilly_m_per_km"] else bc["run_equiv_flat"])


@dataclass
class Week:
    start: date
    number: int | None
    phase: str | None
    recovery: bool
    complete: bool
    days_left: int
    sessions: list[Session]
    threshold: Metric
    over_cap: Metric
    run_km: Metric
    run_days: Metric
    long_run: Session | None
    easy_avg_hr: Metric             # pooled over the week's easy runs
    easy_share: Metric
    easy_failed: list[Session]      # every easy run is judged on its own
    easy_drift: list[Session]       # easy runs in the amber zone
    easy: Metric                    # the cell's verdict: worst of pooled and per-run
    discipline_m: Metric            # count of discipline flags, 0 is the target
    bike_hours: Metric
    run_share: Metric
    bike_thr_min: Metric
    solid_nights: Metric
    gym: Metric
    discipline: list[tuple[Session, str]]


def band_verdict(v: float, lo: float, hi: float, green: float, under: float, over: float,
                 complete: bool) -> str:
    """Three levels around a weekly target [lo, hi]: within +/- green -> good;
    within the amber zone (-under, +over) -> warn; beyond -> act. With no amber
    zone set (0), everything outside green is warn, as before. Going over flags at
    once, like a cap; falling short only once the week is over."""
    zone = under > 0 or over > 0
    eps = 1e-9                     # 3 x 1.6 km must sit on a -25 % edge of 6.4, not below it
    if v > hi * (1 + green) + eps:
        return "act" if zone and v > hi * (1 + max(over, green)) + eps else "warn"
    if v >= lo * (1 - green) - eps:
        return "good"
    if not complete:
        return "neutral"
    return "act" if zone and v < lo * (1 - max(under, green)) - eps else "warn"


def worst(*statuses: str) -> str:
    order = ["act", "warn", "none", "good", "neutral"]
    present = [s for s in statuses if s in order]
    return min(present, key=order.index) if present else "neutral"


def _goal_status(have: float, target: float | None, chances_left: int) -> str:
    """A count to reach by Sunday: green once reached, amber once it can no longer
    be reached (or the week ended short), otherwise still in progress."""
    if not target:
        return "neutral"
    if have >= target:
        return "good"
    if have + chances_left < target:
        return "warn"
    return "neutral"


def _wtd(model: Model, week_start: date, until: datetime) -> list[Activity]:
    return [a for a in model.in_range(week_start, week_start + timedelta(days=7)) if a.start <= until]


def week_summary(week_start: date, model: Model, cfg: dict, season: S.Season,
                 now: datetime) -> Week:
    end = week_start + timedelta(days=7)
    complete = now.date() >= end
    until = datetime.combine(end, time.min) if complete else now
    days_left = 0 if complete else (end - now.date()).days - 1   # days after today
    acts = _wtd(model, week_start, until)
    # last week up to the same point, for direction arrows
    prev_until = until - timedelta(days=7)
    prev_acts = _wtd(model, week_start - timedelta(days=7), prev_until)
    sessions = classify(acts, model, cfg, season)
    prev_sessions = classify(prev_acts, model, cfg, season)
    tg = season.targets(week_start)

    within = sum(s.within_km for s in sessions)
    over = sum(s.over_km for s in sessions)
    band = tg["threshold"]
    # A range is judged as written. A single planned figure ([10, 10]) gets the
    # run-km tolerance, or 0.1 km short would fail the week.
    tol = cfg["runs"].get("threshold_tolerance", 0.0) or (
        cfg["runs"]["run_km_tolerance"] if band and band[0] == band[1] else 0.0)
    rc_ = cfg["runs"]
    thr_status = "neutral" if band is None else band_verdict(
        within, band[0], band[1], tol, rc_.get("threshold_amber_under", 0.0),
        rc_.get("threshold_amber_over", 0.0), complete)
    threshold = Metric(within, band, thr_status, sum(s.within_km for s in prev_sessions))
    over_cap = Metric(over, 0, "act" if over > 0 else "good", sum(s.over_km for s in prev_sessions))

    runs = [s for s in sessions if s.act.sport == "run"]
    run_km_v = sum(s.act.km for s in runs)
    rk_t, tol = tg["run_km"], cfg["runs"]["run_km_tolerance"]
    # no target: nothing to judge, not "no data"
    rk_status = "neutral" if rk_t is None else band_verdict(
        run_km_v, rk_t, rk_t, tol, cfg["runs"].get("run_km_amber_under", 0.0),
        cfg["runs"].get("run_km_amber_over", 0.0), complete)
    run_km = Metric(run_km_v, rk_t, rk_status,
                    sum(a.km for a in prev_acts if a.sport == "run"))

    run_dates = {s.act.day for s in runs}
    rd = len(run_dates)
    rd_min, rd_t = cfg["runs"]["run_days_min"], cfg["runs"]["run_days_target"]
    # days on which a run could still happen: the rest of the week, plus today if not run yet
    still_open = 0 if complete else days_left + (0 if now.date() in run_dates else 1)
    if rd + still_open < rd_min:
        rd_status = "warn"
    else:
        rd_status = "good" if rd >= rd_min else "neutral"
    run_days = Metric(rd, rd_t, rd_status, len({a.day for a in prev_acts if a.sport == "run"}))

    long_run = next((s for s in sessions if s.kind == "long"), None)

    easy = [s for s in sessions if s.kind == "easy"]
    secs = sum(s.act.duration_s or 0 for s in easy if s.act.avg_hr)
    easy_hr = (sum((s.act.avg_hr or 0) * (s.act.duration_s or 0) for s in easy if s.act.avg_hr) / secs) if secs else None
    above = [s.hr_above for s in easy if s.hr_above]
    share = (sum(a for a, _ in above) / sum(t for _, t in above)) if above else None
    ec = cfg["easy"]
    # The pooled numbers are shown and marked on their own; the week's verdict is
    # "every easy run passed", which the failed list carries. A pooled failure
    # always implies a failed run, so the two can't disagree in the red direction.
    easy_failed = [s for s in easy if s.status == "act"]
    easy_drift = [s for s in easy if s.status == "warn"]
    easy_avg_hr = Metric(easy_hr, ec["hr_cap_bpm"],
                         "none" if easy_hr is None and easy else
                         ("act" if easy_hr is not None and easy_hr > ec["hr_cap_bpm"]
                          else ("good" if easy else "neutral")))
    easy_share = Metric(share, ec["max_share_above"],
                        "none" if share is None and easy else
                        ("act" if share is not None and share >= ec["max_share_above"]
                         else "warn" if share is not None and ec.get("share_green") and share >= ec["share_green"]
                         else ("good" if easy else "neutral")))

    rides = [s for s in sessions if s.act.sport == "bike"]
    bh = sum(s.act.hours for s in rides)
    bc = cfg["bike"]
    bike_hours = Metric(bh, bc["max_hours"], "warn" if bh > bc["max_hours"] else "good",
                        sum(a.hours for a in prev_acts if a.sport == "bike"))

    equiv = sum(run_equiv_km(s.act, cfg) for s in rides)
    share_v = run_km_v / (run_km_v + equiv) if (run_km_v + equiv) > 0 else None
    if tg["recovery"]:
        sh_status, sh_note = "neutral", "off in recovery weeks"
    elif share_v is None or (run_km_v + equiv) < bc["min_volume_for_share_km"]:
        sh_status, sh_note = "neutral", "too little volume yet"
    else:
        sh_status, sh_note = ("act" if share_v < bc["min_run_share"] else "good"), ""
    prev_eq = sum(run_equiv_km(a, cfg) for a in prev_acts if a.sport == "bike")
    prev_run = sum(a.km for a in prev_acts if a.sport == "run")
    run_share = Metric(share_v, bc["min_run_share"], sh_status,
                       prev_run / (prev_run + prev_eq) if (prev_run + prev_eq) else None, sh_note)

    if bc.get("ftp_w"):
        tm = [s.thr_min for s in rides if s.thr_min is not None]
        bike_thr = Metric(sum(tm) if tm else (0.0 if not rides else None), None, "neutral")
    else:
        bike_thr = Metric(None, None, "none", note="no FTP set")

    sc = cfg["sleep"]
    nights = [model.nights.get(week_start + timedelta(days=i)) for i in range(7)]
    nights = [n for n in nights if n and n.day <= now.date()]
    levels = [night_level(n, cfg) for n in nights]
    solid = levels.count("good")
    ok = solid + levels.count("warn")
    # nights still to come this week: the rest of the week, plus last night if not synced yet
    nights_left = 0 if complete else days_left + (0 if now.date() in {n.day for n in nights} else 1)
    target, ok_target = sc["solid_nights_target"], sc.get("ok_nights_target", 0)
    if solid >= target:
        sn_status = "good"
    elif ok_target and ok >= ok_target and solid + nights_left < target:
        sn_status = "warn"           # short of solid nights, but enough solid-or-amber ones
    elif solid + nights_left < target and (not ok_target or ok + nights_left < ok_target):
        sn_status = "act" if ok_target else "warn"
    else:
        sn_status = "neutral"
    solid_nights = Metric(solid, target, sn_status, note=f"{ok} solid or amber" if ok_target else "")

    gt = S.gym_target(cfg, week_start)
    g = sum(1 for s in sessions if s.kind == "gym")
    gym_today = any(s.kind == "gym" and s.act.day == now.date() for s in sessions)
    gym = Metric(g, gt, _goal_status(g, gt, 0 if complete else days_left + (0 if gym_today else 1)),
                 sum(1 for a in prev_acts if a.sport == "strength"))

    discipline = [(s, f) for s in rides for f in s.flags]
    return Week(week_start, season.week_number(week_start), tg["phase"], tg["recovery"], complete,
                days_left, sessions, threshold, over_cap, run_km, run_days, long_run,
                easy_avg_hr, easy_share, easy_failed, easy_drift,
                Metric(len(easy_failed), 0, worst(easy_avg_hr.status, easy_share.status,
                                                  "act" if easy_failed else "warn" if easy_drift else "good")
                       if easy else "neutral"),
                Metric(len(discipline), 0, "warn" if discipline else "good"), bike_hours, run_share, bike_thr, solid_nights, gym,
                discipline)


# --------------------------------------------------------------------------
# sleep, load, readiness
# --------------------------------------------------------------------------
def night_level(n: Night | None, cfg: dict) -> str | None:
    """good = solid; warn = amber (if an amber night is configured); act = below."""
    s = is_solid(n, cfg)
    if s is None:
        return None
    if s:
        return "good"
    sc = cfg["sleep"]
    if sc.get("amber_score") and n.score >= sc["amber_score"] and n.hours >= sc.get("amber_hours", 0):
        return "warn"
    return "act"


READINESS_RANK = {"POOR": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3, "PRIME": 4}


def is_solid(n: Night | None, cfg: dict) -> bool | None:
    if n is None or n.score is None or n.hours is None:
        return None
    sc = cfg["sleep"]
    return n.score >= sc["solid_score"] and n.hours >= sc["solid_hours"]


def bedtime_minutes(dt: datetime) -> int:
    """Minutes after noon, so 23:40 and 00:20 sit 40 minutes apart, not 23 hours."""
    return (dt.hour * 60 + dt.minute - 720) % 1440


def mean_bedtime(model: Model, end_day: date, days: int = 7) -> float | None:
    vals = [bedtime_minutes(n.bedtime) for i in range(days)
            if (n := model.nights.get(end_day - timedelta(days=i))) and n.bedtime]
    return statistics.fmean(vals) if vals else None


def bedtime_late(model: Model, cfg: dict, end_day: date) -> tuple[bool, float | None]:
    mb = mean_bedtime(model, end_day)
    target = (S.bedtime_target_minutes(cfg) - 720) % 1440
    return (mb is not None and mb > target), mb


def acwr_alert(model: Model, cfg: dict, today: date) -> tuple[bool, float | None, float | None]:
    """Spec: above the band for two weeks. Mean of the last 7 days and of the 7
    days before both above it."""
    hi = cfg["load"]["acwr_high"]

    def mean(days: range) -> float | None:
        vals = [r for i in days if (ld := model.load.get(today - timedelta(days=i))) and (r := ld.ratio)]
        return statistics.fmean(vals) if len(vals) >= 4 else None

    this, prev = mean(range(0, 7)), mean(range(7, 14))
    return (this is not None and prev is not None and this > hi and prev > hi), this, prev


READINESS_STATUS = {"PRIME": "good", "HIGH": "good", "MODERATE": "good", "LOW": "warn", "POOR": "act"}
SLEEP_HISTORY_STATUS = {"EXCELLENT": "good", "VERY_GOOD": "good", "GOOD": "good", "MODERATE": "warn",
                        "FAIR": "warn", "POOR": "act", "VERY_POOR": "act"}


@dataclass
class Today:
    """Today's readiness-side verdicts, computed once. The today card, the
    hard-session checks and the alerts all read these, so they can't disagree."""
    reading: Readiness | None
    readiness: Metric
    recovery: Metric              # value: hours of recovery left now
    recovery_opens: datetime | None   # when recovery drops under the limit (None: already under)
    recovered_at: datetime | None     # when recovery reaches zero
    hrv: Metric
    sleep_history: Metric
    acwr: Metric                  # 7-day mean of Garmin's acute/chronic ratio
    bedtime: Metric               # 7-day mean, minutes after noon


def today_state(model: Model, cfg: dict, now: datetime) -> Today:
    today = now.date()
    r = model.current_readiness(today)
    max_rec = cfg["hard_session"]["max_recovery_hours"]
    rc = cfg["readiness"]

    if r is None or r.score is None:
        readiness = Metric(None, None, "none")
    else:
        readiness = Metric(r.score, None, READINESS_STATUS.get((r.level or "").upper(), "neutral"),
                           note=(r.level or "").title())

    opens = recovered = None
    # Before the day's first reading, yesterday's last recovery time is still
    # counting down - "no data" would wrongly read as nothing to wait for.
    rr = r if r is not None and r.recovery_h is not None else model.current_readiness(today - timedelta(days=1))
    if rr is None or rr.recovery_h is None:
        recovery = Metric(None, max_rec, "none")
    else:
        r_day = rr
        # Garmin's recovery time counts down from the moment of the reading. Judge
        # what is left now, not the number as it was read hours ago.
        at = r_day.at or datetime.combine(r_day.day, time(7, 0))
        recovered = at + timedelta(hours=r_day.recovery_h)
        left = max(0.0, (recovered - now).total_seconds() / 3600)
        if r_day.recovery_h > max_rec:
            opens = at + timedelta(hours=r_day.recovery_h - max_rec)
            if opens <= now:
                opens = None
        day = "" if at.date() == today else f"{at:%a} "
        recovery = Metric(left, max_rec, "warn" if left > max_rec else "good",
                          note=f"{r_day.recovery_h:.0f} h at {day}{at:%H:%M}")

    hrv_now = (r.hrv_weekly if r and r.hrv_weekly else
               (model.hrv[today].weekly_avg if today in model.hrv else None))
    hrv_prev = model.hrv[today - timedelta(days=7)].weekly_avg if (today - timedelta(days=7)) in model.hrv else None
    if not hrv_now:
        hrv = Metric(None, (rc["hrv_low"], rc["hrv_high"]), "none")
    else:
        in_band = rc["hrv_low"] <= hrv_now <= rc["hrv_high"]
        hrv = Metric(hrv_now, (rc["hrv_low"], rc["hrv_high"]), "good" if in_band else "warn", hrv_prev,
                     "" if in_band else ("below your range" if hrv_now < rc["hrv_low"] else "above your range"))

    fb = ((r.sleep_history_fb if r else None) or "").upper()
    if r is None:
        sleep_history = Metric(None, None, "none")
    else:
        sleep_history = Metric(r.sleep_history_pct, None, SLEEP_HISTORY_STATUS.get(fb, "neutral"),
                               note=fb.replace("_", " ").title())
    lc = cfg["load"]
    ramp, mean7, _ = acwr_alert(model, cfg, today)
    if mean7 is None:
        acwr = Metric(None, (lc["acwr_low"], lc["acwr_high"]), "none", note="no load data")
    elif ramp:
        acwr = Metric(mean7, (lc["acwr_low"], lc["acwr_high"]), "warn", note="above the band two weeks running")
    elif lc["acwr_low"] <= mean7 <= lc["acwr_high"]:
        acwr = Metric(mean7, (lc["acwr_low"], lc["acwr_high"]), "good", note="Garmin acute ÷ chronic load")
    else:   # one-off excursions are not flagged - the spec's rule is two weeks
        acwr = Metric(mean7, (lc["acwr_low"], lc["acwr_high"]), "neutral",
                      note="above the band" if mean7 > lc["acwr_high"] else "below the band")
    late, mb = bedtime_late(model, cfg, today)
    bedtime = Metric(mb, cfg["sleep"]["bedtime_target"],
                     "none" if mb is None else ("warn" if late else "good"),
                     prev=mean_bedtime(model, today - timedelta(days=7)))
    return Today(r, readiness, recovery, opens, recovered, hrv, sleep_history, acwr, bedtime)


@dataclass
class Check:
    label: str
    state: str                  # ok | fail | nodata
    detail: str
    opens: datetime | None = None


@dataclass
class HardSession:
    state: str                  # open | closed | nodata
    checks: list[Check]
    opens: datetime | None


def hard_session(model: Model, cfg: dict, season: S.Season, now: datetime,
                 ts: Today | None = None) -> HardSession:
    today = now.date()
    hc = cfg["hard_session"]
    checks: list[Check] = []
    tomorrow = datetime.combine(today + timedelta(days=1), time(7, 0))

    ts = ts or today_state(model, cfg, now)
    r = ts.reading
    if r is None or r.level is None:
        checks.append(Check("Readiness", "nodata", "no readiness reading yet today"))
    elif r.level.upper() == "POOR":
        checks.append(Check("Readiness", "fail", "readiness POOR", tomorrow))
    else:
        checks.append(Check("Readiness", "ok", f"{r.level.title()} {r.score:.0f} \u2014 only POOR blocks"))

    n1, n2 = model.nights.get(today), model.nights.get(today - timedelta(days=1))
    s1, s2 = is_solid(n1, cfg), is_solid(n2, cfg)
    l1, l2 = night_level(n1, cfg), night_level(n2, cfg)
    reds = [l1, l2].count("act")
    need_level = hc.get("red_night_ok_readiness", "")
    ready = READINESS_RANK.get(((r.level if r else None) or "").upper(), -1)
    if s1 is None or s2 is None:
        checks.append(Check("Two solid nights", "nodata", "sleep missing for a night"))
    elif s1 and s2:
        checks.append(Check("Two solid nights", "ok", "last two nights solid"))
    elif reds == 0:
        checks.append(Check("Two solid nights", "ok", "both nights at least amber"))
    elif reds == 1 and need_level and ready >= READINESS_RANK.get(need_level.upper(), 99):
        checks.append(Check("Two solid nights", "ok",
                            f"one red night, readiness {r.level.title()} (≥ {need_level.title()} allows it)"))
    else:
        # name the night by its evening and say what missed, so "not solid" can't
        # be read as contradicting "last night ... solid" right below it
        bad = n1 if not s1 else n2
        sc = cfg["sleep"]
        why = [f"{bad.hours:.2f} h < {sc['solid_hours']:.2f} h"] if bad.hours < sc["solid_hours"] else []
        if bad.score is not None and bad.score < sc["solid_score"]:
            why.append(f"score {bad.score:.0f} < {sc['solid_score']}")
        night = "last night" if not s1 else f"{bad.day - timedelta(days=1):%a}→{bad.day:%a} night"
        need = 2 if not s1 else 1
        checks.append(Check("Two solid nights", "fail", f"{night} not solid ({', '.join(why)})",
                            datetime.combine(today + timedelta(days=need), time(7, 0))))

    # time since the last sub-T within cap / over-cap, race or test
    history = model.in_range(today - timedelta(days=10), today + timedelta(days=1))
    sess = []
    for wk in sorted({monday(a.day) for a in history}):
        sess += classify(model.in_range(wk, wk + timedelta(days=7)), model, cfg, season)
    sess = [s for s in sess if s.act.end <= now]
    # Every sub-T session starts the 24 h window, including one recorded without
    # heart rate: its cap can't be checked, but it was still a hard session.
    subt_ok = [s for s in sess if s.kind == "subt" and s.over_km == 0 and not s.breached]
    heavy = [s for s in sess if s.kind in ("speed", "race", "test")
             or (s.kind == "subt" and (s.over_km > 0 or s.breached))]   # accepted or not, it was hard
    if subt_ok:
        last = max(subt_ok, key=lambda s: s.act.end)
        opens = last.act.end + timedelta(hours=hc["hours_after_subt"])
        no_hr = " (no HR, cap unchecked)" if last.status == "none" else ""
        checks.append(Check(f"{hc['hours_after_subt']} h since sub-T",
                            "fail" if opens > now else "ok",
                            f"last sub-T {last.act.day:%a %d %b} {last.act.end:%H:%M}{no_hr}",
                            opens if opens > now else None))
    else:
        checks.append(Check(f"{hc['hours_after_subt']} h since sub-T", "ok",
                            "no sub-T within the cap recently"
                            if any(s.kind == "subt" for s in heavy) else "no recent sub-T"))
    if heavy:
        last = max(heavy, key=lambda s: s.act.end)
        opens = last.act.end + timedelta(hours=hc["hours_after_overcap_race_test"])
        what = {"race": "race", "test": "test", "speed": "speed session"}.get(
            last.kind, "over-cap session")
        checks.append(Check(f"{hc['hours_after_overcap_race_test']} h since over-cap/race/test",
                            "fail" if opens > now else "ok",
                            f"{what} {last.act.day:%a %d %b} {last.act.end:%H:%M}", opens if opens > now else None))
    else:
        checks.append(Check(f"{hc['hours_after_overcap_race_test']} h since over-cap/race/test",
                            "ok", "none recently"))

    rec = ts.recovery
    if rec.value is None:
        checks.append(Check("Recovery time", "nodata", "no recovery time yet"))
    else:
        detail = f"{shown(rec.value, hc['max_recovery_hours'])} h left (max {hc['max_recovery_hours']} h) · read {rec.note}"
        checks.append(Check("Recovery time", "fail" if ts.recovery_opens else "ok", detail,
                            ts.recovery_opens))

    fails = [c for c in checks if c.state == "fail"]
    if fails:
        opens = max((c.opens for c in fails if c.opens), default=None)
        return HardSession("closed", checks, opens)
    if any(c.state == "nodata" for c in checks):
        return HardSession("nodata", checks, None)
    return HardSession("open", checks, None)


# --------------------------------------------------------------------------
# fitness trends
# --------------------------------------------------------------------------
@dataclass
class PacePoint:
    day: date
    pace: float                 # s/km at HR in band
    minutes: float
    valid: bool
    reasons: list[str]
    act: Activity


def pace_at_hr(act: Activity, cfg: dict,
               quality_ends: list[datetime] | None = None) -> PacePoint | None:
    """Average pace while HR is in the band, after the first N minutes. Points
    from warm, hilly, indoor or weatherless runs come back with valid=False, as do
    runs within hours_after_quality of a quality session (sub-T, speed, race, test):
    tired legs read as lost fitness and drag the trend."""
    pc = cfg["pace_at_hr"]
    s = act.series
    if not s or not s.get("hr") or not s.get("v") or not s.get("t"):
        return None
    t, hr, v = s["t"], s["hr"], s["v"]
    skip = pc["skip_first_min"] * 60
    secs = dist = 0.0
    for i in range(1, len(t)):
        if None in (t[i], t[i - 1], hr[i], v[i]):
            continue
        dt = t[i] - t[i - 1]
        if t[i] < skip or not 0 < dt <= 30 or v[i] < 1.5:
            continue
        if pc["hr_low"] <= hr[i] <= pc["hr_high"]:
            secs += dt
            dist += v[i] * dt
    if secs / 60.0 < pc["min_minutes_in_band"] or dist <= 0:
        return None
    reasons = []
    gap = pc.get("hours_after_quality", 24)
    if any(timedelta(0) <= act.start - end < timedelta(hours=gap) for end in quality_ends or []):
        reasons.append(f"< {gap} h after quality")
    if act.indoor:
        reasons.append("indoor")
    if act.temp_c is None or act.temp_suspect:
        reasons.append("no reliable weather")
    elif act.temp_c > pc["max_temp_c"]:
        reasons.append(f"warm {act.temp_c:.0f} °C")
    epk = act.elev_per_km
    if epk is None or epk > pc["max_elev_m_per_km"]:
        reasons.append(f"hilly {epk:.0f} m/km" if epk is not None else "no elevation")
    return PacePoint(act.day, secs / (dist / 1000.0), secs / 60.0, not reasons, reasons, act)


def linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    if len(xs) < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return slope, my - slope * mx


# --------------------------------------------------------------------------
# alerts - each one says what happened and what to do
# --------------------------------------------------------------------------
@dataclass
class Alert:
    level: str          # act | warn
    what: str
    action: str
    key: str = ""       # the verdict it explains (see verdicts())


def week_alerts(week: Week, cfg: dict) -> list[Alert]:
    """Alerts for the week's own verdicts: sessions and weekly totals."""
    out: list[Alert] = []
    for s in week.sessions:
        if s.kind == "subt" and s.over_km > 0:
            red = [r for r in s.reps if r.within is False]
            near = [r for r in s.reps if r.near]
            caps = sorted({r.cap for r in red})
            what = (f"Cap breached {s.act.day:%a} — rep{'s' if len(red) > 1 else ''} "
                    + "·".join(f"{r.avg_hr:.0f}" for r in red) + f" vs cap {'/'.join(map(str, caps))}")
            if near:
                what += " (" + "·".join(f"{r.avg_hr:.0f}" for r in near) + " inside the tolerance)"
            out.append(Alert("act", what, S.action(cfg, "over_cap"), "over_cap"))
    for s in week.sessions:
        if s.kind == "subt" and s.status == "warn":
            hrs = "·".join(f"{r.avg_hr:.0f}" for r in s.reps if r.near)
            caps = sorted({r.cap for r in s.reps if r.near})
            out.append(Alert("warn", f"Near the cap {s.act.day:%a} — reps {hrs} vs cap {'/'.join(map(str, caps))}"
                                     " (inside the tolerance)", S.action(cfg, "near_cap"), "near_cap"))
    for s in week.sessions:
        bad = [r for r in s.reps if r.pace_verdict in ("warn", "act")]
        if s.kind == "speed" and bad and not s.accepted:
            out.append(Alert(worst(*(r.pace_verdict for r in bad)),
                             f"Off pace {s.act.day:%a} — reps " + "·".join(_mmss(r.pace) for r in bad),
                             S.action(cfg, "pace"), "pace"))
    for s in week.easy_drift:
        share = s.hr_above[0] / s.hr_above[1] if s.hr_above else None
        out.append(Alert("warn", f"Easy run drifting {s.act.day:%a} — "
                                 + (f"{shown(share, cfg['easy']['share_green'], pct=True)} above {cfg['easy']['hr_cap_bpm']}"
                                    if share is not None else "HR near the cap"),
                         S.action(cfg, "easy_drift"), "easy"))
    for s in week.easy_failed:
        bits = [f"avg {shown(s.act.avg_hr, cfg['easy']['hr_cap_bpm'])}"] if s.act.avg_hr else []
        if s.hr_above:
            share = s.hr_above[0] / s.hr_above[1]
            bits.append(f"{shown(share, cfg['easy']['max_share_above'], pct=True)} above {cfg['easy']['hr_cap_bpm']}")
        out.append(Alert("act", f"Easy-run HR leak {s.act.day:%a} — {', '.join(bits)}",
                         S.action(cfg, "easy"), "easy"))
    if week.run_share.status == "act":
        out.append(Alert("act", f"Bike is displacing running — run share "
                                f"{shown(week.run_share.value, cfg['bike']['min_run_share'], pct=True)}",
                         S.action(cfg, "run_share"), "run_share"))
    if week.bike_hours.status == "warn":
        out.append(Alert("warn", f"Bike over cap — {shown(week.bike_hours.value, cfg['bike']['max_hours'], 1)}"
                                 f" / {cfg['bike']['max_hours']:g} h",
                         S.action(cfg, "bike_hours"), "bike_hours"))
    actions = {"hard-run": S.action(cfg, "discipline_hard_run"),
               "before the long run": S.action(cfg, "discipline_long_run"),
               "easy ride": S.action(cfg, "discipline_easy_ride"),
               "over": S.action(cfg, "discipline_distance")}
    for s, flag in week.discipline:
        act = next((v for k, v in actions.items() if k in flag), "")
        out.append(Alert("warn", f"Bike discipline {s.act.day:%a} — {flag}", act, "discipline"))
    if week.long_run and week.long_run.status in ("warn", "act"):
        out.append(Alert(week.long_run.status, f"Long run avg HR {shown(week.long_run.act.avg_hr, cfg['long_run']['hr_cap_bpm'])}"
                                 f" > {cfg['long_run']['hr_cap_bpm']}",
                         S.action(cfg, "long_run"), "long_run"))
    out += target_alerts(week, cfg)
    return out


def _mmss(sec: float | None) -> str:
    if sec is None:
        return "–"
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}"


def target_alerts(week: Week, cfg: dict) -> list[Alert]:
    """Shortfalls against the week's targets - the part of a week's verdict that
    is still worth knowing after the week has ended."""
    out: list[Alert] = []
    if week.run_km.status in ("warn", "act") and week.run_km.target:
        diff = (week.run_km.value - week.run_km.target) / week.run_km.target
        out.append(Alert(week.run_km.status, f"Run km {week.run_km.value:.0f} / {week.run_km.target:.0f} ({diff:+.0%})",
                         S.action(cfg, "run_km"), "run_km"))
    if week.run_days.status == "warn":
        out.append(Alert("warn", f"Run days {week.run_days.value:.0f} / {cfg['runs']['run_days_target']}",
                         S.action(cfg, "run_days"), "run_days"))
    if week.threshold.status in ("warn", "act") and week.threshold.target is not None:
        lo, hi = week.threshold.target
        lvl = week.threshold.status
        if week.threshold.value > hi:
            what = "no sub-T planned" if hi == 0 else f"above band — {week.threshold.value:.1f} / {band_text(lo, hi)}"
            out.append(Alert(lvl, f"Threshold km {what}" + (f" ({week.threshold.value:.1f} km)" if hi == 0 else ""),
                             S.action(cfg, "threshold_high"), "threshold"))
        else:
            out.append(Alert(lvl, f"Threshold km below band — {week.threshold.value:.1f} / {band_text(lo, hi)}",
                             S.action(cfg, "threshold_low"), "threshold"))
    if week.solid_nights.status in ("warn", "act"):
        out.append(Alert(week.solid_nights.status, f"Solid nights {week.solid_nights.value:.0f} / {week.solid_nights.target}"
                                 + ("" if week.complete else " — target out of reach this week"),
                         S.action(cfg, "solid_nights"), "solid_nights"))
    if week.gym.status == "warn":
        out.append(Alert("warn", f"Gym {week.gym.value:.0f} / {week.gym.target}"
                                 + ("" if week.complete else " — target out of reach this week"),
                         S.action(cfg, "gym"), "gym"))
    return out


def carryover_alerts(last_week: Week, cfg: dict, label: str) -> list[Alert]:
    """Last week's missed targets, labelled, for the start of the new week.
    Session and day alerts are not carried over: they were about last week's
    days and would duplicate today's own."""
    return [Alert(a.level, f"{label}: {a.what}", a.action, "last_week")
            for a in target_alerts(last_week, cfg)]


def day_alerts(ts: Today, model: Model, cfg: dict, now: datetime) -> list[Alert]:
    """Alerts for today's readiness-side verdicts, sleep and load."""
    out: list[Alert] = []
    level = (ts.reading.level or "").upper() if ts.reading else ""
    if level == "POOR":
        out.append(Alert("act", "Readiness POOR", S.action(cfg, "readiness_poor"), "readiness"))
    elif level == "LOW":
        out.append(Alert("warn", "Readiness LOW", S.action(cfg, "readiness_low"), "readiness"))
    if ts.recovery.status == "warn" and ts.recovery_opens:
        out.append(Alert("warn", f"Recovery {shown(ts.recovery.value, cfg['hard_session']['max_recovery_hours'])} h left — under "
                                 f"{cfg['hard_session']['max_recovery_hours']} h at {ts.recovery_opens:%a %H:%M}",
                         S.action(cfg, "recovery"), "recovery"))
    if ts.hrv.status == "warn":
        lo, hi = ts.hrv.target
        out.append(Alert("warn", f"HRV 7-day {shown(ts.hrv.value, lo if ts.hrv.value < lo else hi)} "
                                 f"{ts.hrv.note} ({lo}–{hi})",
                         S.action(cfg, "hrv_low" if ts.hrv.value < lo else "hrv_high"), "hrv"))
    if ts.sleep_history.status in ("warn", "act"):
        out.append(Alert(ts.sleep_history.status, f"Sleep history {ts.sleep_history.note.lower()} (Garmin)",
                         S.action(cfg, "sleep_history"), "sleep_history"))
    for n in (model.nights.get(now.date()), model.nights.get(now.date() - timedelta(days=1))):
        if n and n.hours is not None and n.score is not None and night_level(n, cfg) == "act":
            out.append(Alert("warn", f"Sleep {n.day:%a} was not solid",
                             S.action(cfg, "night"), "night"))
            break
    if ts.bedtime.status == "warn":
        hh, mm = divmod(int(round(ts.bedtime.value + 720)) % 1440, 60)
        out.append(Alert("warn", f"Bedtime is the lever — 7-day mean {hh:02d}:{mm:02d}",
                         S.action(cfg, "bedtime"), "bedtime"))
    if ts.acwr.status == "warn":
        out.append(Alert("warn", f"Ramp outrunning the base — ACWR {ts.acwr.value:.2f} two weeks running",
                         S.action(cfg, "acwr"), "acwr"))
    return out


def alerts(week: Week, model: Model, cfg: dict, now: datetime, ts: Today | None = None) -> list[Alert]:
    return week_alerts(week, cfg) + day_alerts(ts or today_state(model, cfg, now), model, cfg, now)


def verdicts(week: Week, ts: Today) -> dict[str, str]:
    """Every judged cell on the home page and its status - the same Metric objects
    the cells render. Each amber or red one must have an alert with the same key;
    the tests hold the page to that, and a page test checks no cell is missing here."""
    v = {"over_cap": week.over_cap.status, "threshold": week.threshold.status,
         "run_km": week.run_km.status, "run_days": week.run_days.status,
         "easy": week.easy.status,
         "long_run": week.long_run.status if week.long_run else "neutral",
         "bike_hours": week.bike_hours.status, "run_share": week.run_share.status,
         "discipline": week.discipline_m.status,
         "solid_nights": week.solid_nights.status, "gym": week.gym.status,
         "readiness": ts.readiness.status, "recovery": ts.recovery.status,
         "hrv": ts.hrv.status, "sleep_history": ts.sleep_history.status,
         "acwr": ts.acwr.status, "bedtime": ts.bedtime.status}
    return v


def band_text(lo: float, hi: float) -> str:
    """'12–16', or just '10.5' when the plan gives a single figure."""
    return f"{lo:g}" if lo == hi else f"{lo:g}–{hi:g}"


def shown(v: float, limit: float, digits: int = 0, pct: bool = False) -> str:
    """v formatted so rounding can't make it look equal to the limit it is judged
    against: 140.4 against a cap of 140 reads "140.4", not "140"."""
    k = 100 if pct else 1
    d = digits
    while d < digits + 2 and v != limit and round(v * k, d) == round(limit * k, d):
        d += 1
    return f"{v * k:.{d}f}" + ("%" if pct else "")
