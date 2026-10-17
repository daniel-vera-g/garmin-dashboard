"""All configuration, from one TOML file.

private/config.toml (gitignored, like everything in private/) holds every personal
value: HR caps and thresholds, FTP, the season plan (races, phases, weekly
targets), app settings and the alert texts. config.example.toml is the committed
template with neutral placeholders. DASHBOARD_CONFIG=<path> points elsewhere.

Every number and text the dashboard judges with comes from here, so the code
holds no personal values and a coach changing a cap edits the config, not code.
"""
from __future__ import annotations

import copy
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
PRIVATE = ROOT / "private"                  # gitignored: config, notes, backups
SEASON_KEYS = ("season", "race", "phase", "week", "session")
SESSION_KINDS = ("speed", "subt", "race", "test")       # what a [[session]] override may reclassify to


def config_path() -> Path:
    return Path(os.environ.get("DASHBOARD_CONFIG") or PRIVATE / "config.toml")


# Defaults mirror config.example.toml. A missing key in the config falls back to
# these; a missing file means the dashboard runs entirely on defaults.
DEFAULTS: dict[str, Any] = {
    "app": {
        "host": "127.0.0.1",
        "port": 8765,
        "auto_sync_hours": 6,       # sync on page open when the data is older; 0 = never
        "carryover_days": 2,        # Mon(+Tue): last week's missed targets stay in the alerts
    },
    "sync": {
        "tokenstore": "~/.garminconnect",
        "detail_days": 91,          # per-activity data and load history
        "wellness_days": 120,       # sleep/HRV history (health page needs 90)
        "recent_activity_days": 21,
        "recent_wellness_days": 10,
    },
    "coach": {"ahead_weeks": 4},    # Coach popup: the current week plus this many
    "health": {
        "rhr_rise_bpm": 5,          # 3-night mean this far above the 30-day mean -> amber
        "sleep_short_h": 0.5,       # 30-night average this far under Garmin's need -> amber
        "consistency_good_min": 45, # bed/wake drift (std dev) under this -> green
        "consistency_warn_min": 75, # under this -> amber, above -> red
    },
    # The "-> what to do" line under each alert, by alert key.
    "alerts": {
        "over_cap": "Ease off inside the rep. Next hard day +48 h",
        "near_cap": "Close to the cap - ease off a little inside the rep",
        "pace": "Run the reps inside the pace band",
        "easy_drift": "Easy means easy - keep the HR down",
        "easy": "Easy profile, no pace field",
        "run_share": "Running floor first",
        "bike_hours": "No more riding this week",
        "long_run": "Slower long run — HR under the cap",
        "run_km": "Back to the weekly target",
        "run_days": "Spread the running over more days",
        "threshold_low": "Protect the sub-T sessions next week",
        "threshold_high": "No more sub-T this week",
        "solid_nights": "Prioritize sleep before the next quality session",
        "gym": "Plan the remaining gym sessions",
        "readiness_poor": "No hard session today",
        "readiness_low": "Keep today's session easy",
        "recovery": "No hard session before then",
        "hrv_low": "Keep it easy until HRV is back in range",
        "hrv_high": "Note it in the weekly check-in",
        "sleep_history": "Prioritize an earlier, longer night",
        "night": "Prioritize an earlier, longer night",
        "bedtime": "Two good nights before quality",
        "acwr": "Hold this level two weeks",
        "discipline_hard_run": "Keep hard-run days run-only",
        "discipline_long_run": "Rest the legs before the long run",
        "discipline_easy_ride": "Ride easier — HR under {easy_hr_cap_bpm}",
        "discipline_distance": "Keep rides under {max_km:.0f} km",
    },
    "threshold": {
        "cap_long_bpm": 165,       # reps >= long_rep_m
        "cap_short_bpm": 170,      # shorter reps
        "cap_tolerance_bpm": 0,    # a rep this close above its cap counts as within, flagged amber
        "cap_tolerance_long_bpm": 0,   # the same for long / short reps only (the larger value wins)
        "cap_tolerance_short_bpm": 0,
        "long_rep_m": 1200,
        "subt_max_slower_s": 30,   # rep slower than LT pace + this is aerobic, not a rep
        "speed_margin_s": 8,       # session's median rep this much faster than LT = speed session
        "min_rep_m": 100,          # shorter ACTIVE laps are lap-button artefacts
        "min_rep_s": 30,
    },
    # max_share_above: this share of the time above the cap or more is red;
    # share_green > 0 adds an amber zone from share_green up to max_share_above
    "easy": {"hr_cap_bpm": 140, "max_share_above": 0.10, "share_green": 0.0},
    "speed": {"pace_tolerance_s": 3},   # s/km outside a [[session]] pace band that is still amber
    "long_run": {"hr_cap_bpm": 140, "min_minutes": 60},
    # Weekly targets get three levels: inside the tolerance green, inside the amber
    # zone amber, beyond it red. Amber zones of 0 (not set) keep everything outside
    # the tolerance amber.
    "runs": {"run_days_target": 6, "run_days_min": 5,
             "run_km_tolerance": 0.10,          # green: +/- around the run-km target
             "run_km_amber_under": 0.0, "run_km_amber_over": 0.0,
             # green +/- around the threshold-km band; 0 = a range is judged as
             # written (a single planned figure then uses run_km_tolerance)
             "threshold_tolerance": 0.0,
             "threshold_amber_under": 0.0, "threshold_amber_over": 0.0},
    "bike": {
        "max_hours": 3.0,
        "easy_hr_cap_bpm": 125,
        "max_km": 60.0,
        "threshold_pct": 0.88,
        "hilly_m_per_km": 10.0,
        "run_equiv_flat": 3.5,
        "run_equiv_hilly": 3.0,
        "min_run_share": 0.65,
        "min_volume_for_share_km": 25.0,
        # "ftp_w": absent until the test value exists; never taken from Garmin
    },
    "sleep": {
        "bedtime_target": "23:00",
        "solid_score": 70,
        "solid_hours": 6.5,
        "solid_nights_target": 5,
        # an amber night: below solid, at least these (0 = not set: below solid is red)
        "amber_score": 0, "amber_hours": 0.0,
        "ok_nights_target": 0,      # solid-or-amber nights per week that still read amber
    },
    "readiness": {"hrv_low": 55, "hrv_high": 65},
    "load": {"acwr_low": 0.8, "acwr_high": 1.3},
    "pace_at_hr": {
        "hr_low": 135,
        "hr_high": 140,
        "skip_first_min": 10,
        "max_temp_c": 20.0,
        "max_elev_m_per_km": 5.0,
        "min_minutes_in_band": 5.0,
        "hours_after_quality": 24,   # runs this soon after a quality session are excluded
    },
    "hard_session": {
        "hours_after_subt": 24,
        "hours_after_overcap_race_test": 48,
        "max_recovery_hours": 24,
        # one red night before quality is fine if readiness is at least this level
        # (e.g. "MODERATE"); "" = any red night closes the hard session
        "red_night_ok_readiness": "",
    },
    "gym": {"targets": [{"per_week": 2}]},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_raw(path: Path | None = None) -> dict[str, Any] | None:
    path = path or config_path()
    if not path.exists():
        return None
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_athlete(path: Path | None = None) -> dict[str, Any]:
    """The settings: defaults overlaid with the config file (season plan left out)."""
    raw = load_raw(path)
    if raw is None:
        cfg = copy.deepcopy(DEFAULTS)
        cfg["_missing"] = True
        return cfg
    return _merge(DEFAULTS, {k: v for k, v in raw.items() if k not in SEASON_KEYS})


def action(cfg: dict[str, Any], key: str) -> str:
    """The configured action line for an alert key, with settings filled in."""
    text = cfg.get("alerts", {}).get(key) or DEFAULTS["alerts"].get(key, "")
    try:
        return text.format(**cfg["bike"], **cfg["sleep"])
    except (KeyError, IndexError, ValueError):
        return text


def gym_target(cfg: dict[str, Any], on: date) -> int | None:
    """Targets are listed in order; the first whose `until` has not passed wins."""
    for t in cfg.get("gym", {}).get("targets", []):
        until = t.get("until")
        if until is None or on <= _as_date(until):
            return int(t["per_week"])
    return None


def bedtime_target_minutes(cfg: dict[str, Any]) -> int:
    hh, mm = str(cfg["sleep"]["bedtime_target"]).split(":")
    return int(hh) * 60 + int(mm)


def pace_band(v: Any) -> tuple[float, float] | None:
    """'3:35-3:42' (min:sec per km) -> (215.0, 222.0); None if absent or malformed."""
    m = re.fullmatch(r"\s*(\d+):(\d{2})\s*[-–]\s*(\d+):(\d{2})\s*", str(v or ""))
    if not m:
        return None
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    return (float(min(a, b)), float(max(a, b)))


def _as_date(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


# --------------------------------------------------------------------------
# season plan
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Race:
    date: date
    name: str
    short: str
    priority: str = "B"          # A races drive the header countdown
    sport: str = "run"


@dataclass(frozen=True)
class Phase:
    name: str
    start: date
    end: date
    run_km: float | None = None
    threshold: tuple[float, float] | None = None
    recovery: bool = False


@dataclass(frozen=True)
class SessionOverride:
    """The coach's word on one session: reclassify it (a planned change of plan,
    e.g. a fast session instead of sub-T) and/or accept its verdict."""
    date: date
    kind: str | None = None          # one of SESSION_KINDS
    accept: bool = False             # the verdict stands corrected: no red, no alert
    note: str = ""
    pace: tuple[float, float] | None = None   # planned rep pace band, s/km (fast, slow)
    name: str = ""                   # optional: part of the activity name, when a day has several


@dataclass
class Season:
    week1_start: date | None = None
    races: list[Race] = field(default_factory=list)
    phases: list[Phase] = field(default_factory=list)
    weeks: dict[int, dict[str, Any]] = field(default_factory=dict)
    missing: bool = False
    phase_short: dict[str, str] = field(default_factory=dict)   # labels for narrow chart segments
    sessions: list[SessionOverride] = field(default_factory=list)

    def week_number(self, d: date) -> int | None:
        """Season week of day d; None before week 1, so pre-season weeks are
        labelled by date rather than all reading "W1"."""
        if not self.week1_start or d < self.week1_start:
            return None
        return (d - self.week1_start).days // 7 + 1

    def phase_on(self, d: date) -> Phase | None:
        active = [p for p in self.phases if p.start <= d <= p.end]
        return max(active, key=lambda p: p.start, default=None)

    def targets(self, week_start: date) -> dict[str, Any]:
        """Run-km target and threshold band for the week starting `week_start`.
        Per-week entries override the phase defaults."""
        phase = self.phase_on(week_start + timedelta(days=3))  # mid-week decides
        out: dict[str, Any] = {
            "phase": phase.name if phase else None,
            "recovery": bool(phase and phase.recovery),
            "run_km": phase.run_km if phase else None,
            "threshold": phase.threshold if phase else None,
        }
        n = self.week_number(week_start)
        wk = self.weeks.get(n, {}) if n is not None else {}
        if "run_km" in wk:
            out["run_km"] = float(wk["run_km"])
        if "threshold" in wk:
            lo, hi = wk["threshold"]
            out["threshold"] = (float(lo), float(hi))
        if "recovery" in wk:
            out["recovery"] = bool(wk["recovery"])
        if "phase" in wk:
            out["phase"] = wk["phase"]
        return out

    def race_on(self, d: date) -> Race | None:
        return next((r for r in self.races if r.date == d), None)

    def next_race(self, today: date, a_only: bool = False) -> Race | None:
        pool = [r for r in self.races if r.date >= today and (not a_only or r.priority == "A")]
        return min(pool, key=lambda r: r.date) if pool else None


def load_season(path: Path | None = None) -> Season:
    raw = load_raw(path)
    if raw is None:
        return Season(missing=True)
    meta = raw.get("season", {})
    races = [Race(date=_as_date(r["date"]), name=r["name"], short=r.get("short", r["name"]),
                  priority=r.get("priority", "B"), sport=r.get("sport", "run"))
             for r in raw.get("race", [])]
    phases = []
    for p in raw.get("phase", []):
        thr = p.get("threshold")
        phases.append(Phase(name=p["name"], start=_as_date(p["start"]), end=_as_date(p["end"]),
                            run_km=float(p["run_km"]) if "run_km" in p else None,
                            threshold=(float(thr[0]), float(thr[1])) if thr else None,
                            recovery=bool(p.get("recovery", False))))
    weeks = {int(w["n"]): w for w in raw.get("week", [])}
    overrides = [SessionOverride(date=_as_date(o["date"]),
                                 kind=o.get("kind") if o.get("kind") in SESSION_KINDS else None,
                                 accept=bool(o.get("accept", False)), note=str(o.get("note", "")),
                                 name=str(o.get("name", "")), pace=pace_band(o.get("pace")))
                 for o in raw.get("session", []) if o.get("date")]
    w1 = meta.get("week1_start")
    return Season(week1_start=_as_date(w1) if w1 else None,
                  races=sorted(races, key=lambda r: r.date),
                  phases=sorted(phases, key=lambda p: p.start), weeks=weeks, sessions=overrides,
                  phase_short={str(k).lower(): str(v) for k, v in meta.get("phase_short", {}).items()})
