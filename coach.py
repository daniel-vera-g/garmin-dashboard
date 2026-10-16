#!/usr/bin/env python3
"""The coach loop: what the dashboard needs from the coach, and how the answer
gets back in.

Garmin can't tell the dashboard the plan or the coach's judgement: weekly
targets, threshold bands, caps and tolerances, the FTP from a test, races, and
whether a session that looks wrong was in fact a planned change. This module
writes one prompt that asks for all of it - showing the dashboard's current
values and its reading of recent sessions - and merges the answer into the
gitignored private/config.toml.

The answer is parsed strictly: one TOML block, only known tables and keys,
values type- and range-checked. Anything else is reported back, never guessed.
The coach's free text around the block is kept as notes. The config is edited
as text, so comments and layout survive; a backup is written before each change
and the full answer is archived in private/notes/coach/.

    coach.py prompt            print the prompt
    coach.py preview < answer  what would change (JSON)
    coach.py apply   < answer  change the config (JSON)
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import settings as S

ROOT = Path(__file__).parent
BACKUPS = S.PRIVATE / "backups"
ARCHIVE = S.PRIVATE / "notes" / "coach"
AHEAD_WEEKS = S.DEFAULTS["coach"]["ahead_weeks"]    # the current week plus this many
MAX_ANSWER = 64 * 1024
RECENT_DAYS = 14                                    # sessions shown to the coach

WEEK_KEYS = {"n", "run_km", "threshold", "recovery"}
SESSION_KEYS = {"date", "kind", "accept", "note", "name"}
RACE_KEYS = {"date", "name", "short", "priority", "sport"}
# Settings the coach decides. App, sync and display settings are not the coach's.
COACH_SECTIONS = ("threshold", "easy", "long_run", "runs", "bike", "sleep", "readiness", "load",
                  "pace_at_hr", "hard_session", "alerts")
# Shown to the coach with their current values, to confirm or correct.
PROMPT_KEYS = [
    ("threshold", "cap_long_bpm"), ("threshold", "cap_short_bpm"), ("threshold", "long_rep_m"),
    ("threshold", "cap_tolerance_bpm"),
    ("easy", "hr_cap_bpm"), ("easy", "max_share_above"), ("long_run", "hr_cap_bpm"),
    ("runs", "run_days_target"), ("runs", "run_days_min"), ("runs", "run_km_tolerance"),
    ("runs", "threshold_tolerance"),
    ("bike", "max_hours"), ("bike", "easy_hr_cap_bpm"), ("bike", "max_km"), ("bike", "ftp_w"),
    ("bike", "min_run_share"),
    ("sleep", "bedtime_target"), ("sleep", "solid_score"), ("sleep", "solid_hours"),
    ("sleep", "solid_nights_target"),
    ("readiness", "hrv_low"), ("readiness", "hrv_high"),
    ("load", "acwr_low"), ("load", "acwr_high"),
    ("hard_session", "hours_after_subt"), ("hard_session", "hours_after_overcap_race_test"),
    ("hard_session", "max_recovery_hours"),
]


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


# --------------------------------------------------------------------------
# what is missing
# --------------------------------------------------------------------------
@dataclass
class WeekNeed:
    n: int
    start: date
    run_km: float | None
    threshold: tuple[float, float] | None

    @property
    def missing(self) -> list[str]:
        return [k for k, v in (("run km", self.run_km), ("threshold km", self.threshold)) if v is None]


@dataclass
class Needs:
    today: date
    current: int | None
    weeks: list[WeekNeed]
    ftp_missing: bool

    @property
    def open_items(self) -> int:
        return sum(len(w.missing) for w in self.weeks) + (1 if self.ftp_missing else 0)

    @property
    def urgent(self) -> bool:
        """Something for this week or next is missing: worth a popup."""
        return any(w.missing for w in self.weeks[:2])


def needs(season: S.Season, cfg: dict, today: date, ahead: int = AHEAD_WEEKS) -> Needs:
    weeks = []
    cur = monday(today)
    for i in range(ahead + 1):
        wk = cur + timedelta(weeks=i)
        n = season.week_number(wk)
        if n is None:
            continue
        t = season.targets(wk)
        weeks.append(WeekNeed(n, wk, t["run_km"], t["threshold"]))
    return Needs(today, season.week_number(cur), weeks, not cfg["bike"].get("ftp_w"))


# --------------------------------------------------------------------------
# the prompt
# --------------------------------------------------------------------------
def _fmt_band(b: tuple[float, float] | None) -> str:
    if b is None:
        return "MISSING"
    return f"{b[0]:g}" if b[0] == b[1] else f"{b[0]:g}–{b[1]:g}"


def _fmt_value(v: Any) -> str:
    if v is None:
        return "MISSING"
    return f"{v:g}" if isinstance(v, float) else str(v)


def recent_sessions(model: Any, cfg: dict, season: S.Season, today: date) -> list[str]:
    """The dashboard's reading of the last two weeks' quality and flagged sessions,
    one line each, so the coach can confirm it or say what was planned."""
    import metrics as M                     # heavy import only when there is a model
    lines = []
    start = today - timedelta(days=RECENT_DAYS)
    for wk in sorted({monday(start + timedelta(days=i)) for i in range(RECENT_DAYS + 1)}):
        for s in M.classify(model.in_range(wk, wk + timedelta(days=7)), model, cfg, season):
            if not start <= s.act.day <= today:
                continue
            if s.kind not in ("subt", "speed", "race", "test", "long") and s.status not in ("act", "warn"):
                continue
            bits = [f"{s.act.day:%a %d %b}", s.title]
            sub = [r for r in s.reps if r.kind == "subt"]
            if sub:
                caps = "/".join(str(c) for c in sorted({r.cap for r in sub}))
                bits.append("reps " + "·".join(f"{r.avg_hr:.0f}" for r in sub if r.avg_hr) + f" vs cap {caps}")
            elif s.act.avg_hr:
                bits.append(f"avg HR {s.act.avg_hr:.0f}")
            if s.kind == "easy" and s.hr_above:
                bits.append(f"{s.hr_above[0] / s.hr_above[1]:.0%} of the time above {cfg['easy']['hr_cap_bpm']}")
            bits += s.flags                                   # rides: which discipline rule
            verdict = {"act": "OVER CAP / FAILED", "warn": "DRIFTING", "good": "OK", "none": "NO HR"}.get(s.status, "")
            if s.accepted:
                verdict = "ACCEPTED BY YOU"
            if verdict:
                bits.append(f"dashboard: {verdict}")
            if s.coach:
                bits.append(f"your note: {s.coach}")
            lines.append(" · ".join(bits))
    return lines


def prompt(season: S.Season, cfg: dict, today: date, model: Any = None) -> str:
    nd = needs(season, cfg, today, cfg["coach"]["ahead_weeks"])
    if not season.week1_start or not nd.weeks:
        return ("I need the season plan for my training dashboard. Give me, as one ```toml block: "
                "[season] week1_start (the Monday of Wk1); every race as [[race]] (date, name, short, "
                "priority A/B/C, sport); and per week a [[week]] entry with n, run_km and "
                "threshold = [low, high]. After the block, list FLAGS, MISSING and NOTES.")
    rows = "\n".join(f"Wk{w.n} · Mon {w.start.day} {w.start:%b} · run km {_fmt_value(w.run_km)}"
                     f" · threshold km {_fmt_band(w.threshold)}" for w in nd.weeks)
    first, last = nd.weeks[0].n, nd.weeks[-1].n
    sessions = recent_sessions(model, cfg, season, today) if model is not None else []
    sess_txt = "\n".join(sessions) if sessions else "(none)"
    rules = "\n".join(f"{sec}.{key} = {_fmt_value(cfg[sec].get(key))}" for sec, key in PROMPT_KEYS)
    races = "\n".join(f"{r.date:%Y-%m-%d} · {r.name} · priority {r.priority} · {r.sport}"
                      for r in season.races if r.date >= today) or "(none)"
    return f"""Update for my training dashboard. Today is {today:%a %d %b %Y}, Wk{nd.current}.
Week numbering: Wk1 = the week starting Monday {season.week1_start:%d %b %Y}.

The dashboard only knows what you tell it. Give me ALL the information below - confirm what is right, correct what is wrong, fill in what is MISSING. Where the plan doesn't decide a value, write MISSING. Don't invent one.

A. WEEKLY TARGETS, Wk{first}–Wk{last} (and any later week that changed)
{rows}
Threshold km = sub-threshold rep distance only, at or under my HR cap. Give it as [low, high], as [n, n] for a single planned figure, or [0, 0] for a week without sub-T.

B. RECENT SESSIONS - the dashboard's reading of the last two weeks
{sess_txt}
If a session was a planned change (e.g. a fast session instead of sub-T), or its verdict is fine as it is, say so with a [[session]] entry: date, kind = "speed" / "subt" / "race" / "test" to reclassify it, and/or accept = true to accept the verdict, plus a short note. Leave out sessions that are read correctly.

C. RULES, CAPS AND TOLERANCES - current values
{rules}
Correct any that are wrong, and give the tolerances: how many bpm over a rep cap are still fine (threshold.cap_tolerance_bpm), and how far under or over the weekly run-km and threshold-km targets is still fine, as a fraction, e.g. 0.1 = ±10 % (runs.run_km_tolerance, runs.threshold_tolerance). bike.ftp_w is my own test result only, never Garmin's estimate.

D. RACES still ahead
{races}
Add, move or correct races as [[race]] entries (date, name, short, priority A/B/C, sport run/bike).

E. EVERYTHING ELSE
Anything relevant for my training or this dashboard that I didn't ask for: plan changes, new rules, things to watch. Put what fits a key above into the block; write the rest as NOTES.

Answer with exactly ONE ```toml block containing only what changes or fills a gap:

[[week]]
n = {first}
run_km = 60
threshold = [12, 16]

[[session]]
date = {today:%Y-%m-%d}
kind = "speed"
accept = true
note = "planned change: fast session"

[threshold]
cap_tolerance_bpm = 2

[runs]
run_km_tolerance = 0.1
threshold_tolerance = 0.1

[bike]
ftp_w = 250

[[race]]
date = {today:%Y-%m-%d}
name = "Race name"
short = "Short"
priority = "B"
sport = "run"

After the block, list FLAGS (uncertain or inferred values), MISSING, and NOTES (everything else relevant)."""


# --------------------------------------------------------------------------
# reading the answer
# --------------------------------------------------------------------------
@dataclass
class Update:
    weeks: dict[int, dict[str, Any]] = field(default_factory=dict)
    settings: dict[tuple[str, str], Any] = field(default_factory=dict)
    sessions: list[dict[str, Any]] = field(default_factory=list)
    races: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""                      # the coach's text around the block
    warnings: list[str] = field(default_factory=list)

    @property
    def ftp_w(self) -> int | None:       # kept for callers that only care about the FTP
        return self.settings.get(("bike", "ftp_w"))

    @property
    def empty(self) -> bool:
        return not (self.weeks or self.settings or self.sessions or self.races)


def _number(v: Any, lo: float, hi: float) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if lo <= v <= hi else None


def _date(v: Any) -> date | None:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


def _setting(sec: str, key: str, v: Any) -> tuple[Any, str | None]:
    """A coach-set value checked against the default's type. (value, error)."""
    if (sec, key) == ("bike", "ftp_w"):
        n = _number(v, 50, 600)
        return (int(round(n)), None) if n is not None else (None, f"bike.ftp_w {v!r} is not watts between 50 and 600")
    if key not in S.DEFAULTS.get(sec, {}):
        return None, f"[{sec}]: ignored unknown key '{key}'"
    default = S.DEFAULTS[sec][key]
    if isinstance(default, str):
        if not isinstance(v, str) or not 0 < len(v) <= 160:
            return None, f"{sec}.{key} must be text (up to 160 characters)"
        if sec == "sleep" and key == "bedtime_target" and not re.fullmatch(r"\d{1,2}:\d{2}", v):
            return None, f"sleep.bedtime_target {v!r} is not HH:MM"
        return v, None
    if isinstance(default, bool) or not isinstance(default, (int, float)):
        return None, f"{sec}.{key} can't be set from an answer"
    hi = 1.0 if isinstance(default, float) and default <= 1.0 and ("share" in key or "tolerance" in key) else 10000
    n = _number(v, 0, hi)
    if n is None:
        return None, f"{sec}.{key} {v!r} is not a number between 0 and {hi:g}"
    return (int(n) if isinstance(default, int) and n == int(n) else n), None


def parse(text: str) -> Update:
    """The coach's answer -> validated changes. Raises ValueError when there is
    nothing usable in it."""
    if len(text) > MAX_ANSWER:
        raise ValueError("answer is too long")
    blocks = list(re.finditer(r"```(?:toml)?[ \t]*\n(.*?)```", text, re.S))
    if len(blocks) > 1:
        raise ValueError("found more than one ``` block - paste one answer at a time")
    body = blocks[0].group(1) if blocks else text
    try:
        raw = tomllib.loads(body)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"the TOML block doesn't parse: {exc}") from None

    up = Update()
    if blocks:
        up.notes = (text[:blocks[0].start()] + "\n" + text[blocks[0].end():]).strip()
    known = {"week", "session", "race", *COACH_SECTIONS}
    for key in sorted(set(raw) - known):
        up.warnings.append(f"ignored '{key}': not something the coach sets")
    for key in ("week", "session", "race"):
        if key in raw and not isinstance(raw[key], list):
            up.warnings.append(f"ignored '{key}': write it as [[{key}]] entries")

    for w in raw.get("week", []) if isinstance(raw.get("week"), list) else []:
        n = w.get("n")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 104:
            up.warnings.append(f"ignored a [[week]] without a valid n ({n!r})")
            continue
        row: dict[str, Any] = {}
        for key in sorted(set(w) - WEEK_KEYS):
            up.warnings.append(f"Wk{n}: ignored '{key}'")
        if "run_km" in w:
            v = _number(w["run_km"], 0, 250)
            if v is None:
                up.warnings.append(f"Wk{n}: run_km {w['run_km']!r} is not a number between 0 and 250")
            else:
                row["run_km"] = v
        if "threshold" in w:
            t = w["threshold"]
            vals = [_number(x, 0, 60) for x in t] if isinstance(t, list) and len(t) == 2 else []
            if len(vals) != 2 or None in vals or vals[0] > vals[1]:
                up.warnings.append(f"Wk{n}: threshold {t!r} is not [low, high] with 0 ≤ low ≤ high ≤ 60")
            else:
                row["threshold"] = (vals[0], vals[1])
        if "recovery" in w:
            if isinstance(w["recovery"], bool):
                row["recovery"] = w["recovery"]
            else:
                up.warnings.append(f"Wk{n}: recovery must be true or false")
        if row:
            if n in up.weeks:
                up.warnings.append(f"Wk{n} appears twice; the later entry wins")
            up.weeks.setdefault(n, {}).update(row)

    for o in raw.get("session", []) if isinstance(raw.get("session"), list) else []:
        d = _date(o.get("date"))
        if d is None:
            up.warnings.append(f"ignored a [[session]] without a valid date ({o.get('date')!r})")
            continue
        for key in sorted(set(o) - SESSION_KEYS):
            up.warnings.append(f"session {d}: ignored '{key}'")
        row = {"date": d}
        if "kind" in o:
            if o["kind"] in S.SESSION_KINDS:
                row["kind"] = o["kind"]
            else:
                up.warnings.append(f"session {d}: kind {o['kind']!r} is not one of {', '.join(S.SESSION_KINDS)}")
        if "accept" in o:
            if isinstance(o["accept"], bool):
                row["accept"] = o["accept"]
            else:
                up.warnings.append(f"session {d}: accept must be true or false")
        for key in ("note", "name"):
            if key in o:
                if isinstance(o[key], str) and len(o[key]) <= 200:
                    row[key] = o[key]
                else:
                    up.warnings.append(f"session {d}: {key} must be text up to 200 characters")
        if "kind" in row or "accept" in row:
            up.sessions.append(row)
        else:
            up.warnings.append(f"session {d}: needs kind and/or accept to change anything")

    for r in raw.get("race", []) if isinstance(raw.get("race"), list) else []:
        d = _date(r.get("date"))
        if d is None or not isinstance(r.get("name"), str) or not r["name"].strip():
            up.warnings.append(f"ignored a [[race]] without a valid date and name ({r.get('date')!r})")
            continue
        for key in sorted(set(r) - RACE_KEYS):
            up.warnings.append(f"race {d}: ignored '{key}'")
        row = {"date": d, "name": r["name"].strip()[:80]}
        if isinstance(r.get("short"), str) and r["short"].strip():
            row["short"] = r["short"].strip()[:20]
        if r.get("priority") in ("A", "B", "C"):
            row["priority"] = r["priority"]
        elif "priority" in r:
            up.warnings.append(f"race {d}: priority must be A, B or C")
        if r.get("sport") in ("run", "bike"):
            row["sport"] = r["sport"]
        elif "sport" in r:
            up.warnings.append(f"race {d}: sport must be run or bike")
        up.races.append(row)

    for sec in COACH_SECTIONS:
        table = raw.get(sec)
        if table is None:
            continue
        if not isinstance(table, dict):
            up.warnings.append(f"ignored '{sec}': expected a [{sec}] table")
            continue
        for key, v in table.items():
            val, err = _setting(sec, key, v)
            if err:
                up.warnings.append(err)
            else:
                up.settings[(sec, key)] = val

    if up.empty:
        raise ValueError("no usable values found - the answer needs a ```toml block with [[week]], "
                         "[[session]], [[race]] entries or settings such as [bike] ftp_w"
                         + (f" ({'; '.join(up.warnings)})" if up.warnings else ""))
    return up


# --------------------------------------------------------------------------
# writing it back (as text)
# --------------------------------------------------------------------------
def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, tuple):
        return f"[{v[0]:g}, {v[1]:g}]"
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    return f"{v:g}" if isinstance(v, float) else str(v)


def _blocks(text: str, table: str) -> list[re.Match]:
    return list(re.finditer(rf"^\[\[{table}\]\][^\n]*\n(?:(?!\[)[^\n]*\n?)*", text, re.M))


def _ident(block: str, table: str) -> Any:
    try:
        row = tomllib.loads(block)[table][0]
    except (tomllib.TOMLDecodeError, KeyError, IndexError):
        return None
    if table == "week":
        return row.get("n")
    if table == "session":
        return (row.get("date"), row.get("name", ""))
    return row.get("date")


def _set_key(block: str, key: str, line: str, after: str) -> str:
    pat = re.compile(rf"^{key}\s*=.*$", re.M)
    if pat.search(block):
        return pat.sub(lambda _: line, block, count=1)
    m = re.search(rf"^{after}\s*=.*\n?", block, re.M)
    end = m.end() if m else len(block)
    sep = "" if block[:end].endswith("\n") else "\n"
    return block[:end] + sep + line + "\n" + block[end:]


def merge_array(text: str, table: str, ident: Any, row: dict[str, Any], stamp: str,
                label: str = "") -> tuple[str, list[str]]:
    """Set row's keys in the [[table]] block with this identity, or add a block
    in order after the last one before it."""
    first_key = {"week": "n"}.get(table, "date")
    what = f"Wk{ident}" if table == "week" else f"{table} {row.get('date')}"
    blocks = _blocks(text, table)
    match = next((b for b in blocks if _ident(b.group(0), table) == ident), None)
    changes: list[str] = []
    if match:
        block = new = match.group(0)
        current = tomllib.loads(block)[table][0]
        for key, v in row.items():
            if key in ("n", "date") or (key == "name" and table == "session"):
                continue
            old = current.get(key)
            old_cmp = tuple(old) if isinstance(old, list) else old
            same = old_cmp == v or (isinstance(old, (int, float)) and not isinstance(old, bool)
                                    and isinstance(v, (int, float)) and float(old) == float(v))
            if same:
                continue
            new = _set_key(new, key, f"{key} = {_toml_value(v)}  # coach {stamp}", first_key)
            changes.append(f"{what} {key}: {_toml_value(old_cmp) if old is not None else '—'} → {_toml_value(v)}")
        if new != block:
            text = text[:match.start()] + new + text[match.end():]
        return text, changes
    lines = [f"[[{table}]]" + (f"  # {label}" if label else "")]
    lines += [f"{k} = {_toml_value(v)}" + ("" if k in ("n", "date") else f"  # coach {stamp}")
              for k, v in sorted(row.items(), key=lambda kv: kv[0] not in ("n", "date"))]
    new_block = "\n".join(lines) + "\n"
    order = (lambda k: k[0]) if table == "session" else (lambda k: k)     # by week number / date
    before = [b for b in blocks
              if (k := _ident(b.group(0), table)) is not None and order(k) is not None
              and order(k) <= order(ident)]
    if before:
        at = before[-1].end()
        prefix = "" if text[:at].endswith("\n\n") else ("\n" if text[:at].endswith("\n") else "\n\n")
        text = text[:at] + prefix + new_block + ("" if text[at:].startswith("\n") else "\n") + text[at:]
    else:
        text = text.rstrip("\n") + "\n\n" + new_block
    changes += [f"{what} {k}: — → {_toml_value(v)}" for k, v in row.items() if k not in ("n", "date")]
    return text, changes


def merge_season(text: str, weeks: dict[int, dict[str, Any]], week1: date | None,
                 stamp: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    for n in sorted(weeks):
        ws = week1 + timedelta(weeks=n - 1) if week1 else None
        text, ch = merge_array(text, "week", n, {"n": n, **weeks[n]}, stamp,
                               f"Mon {ws.day} {ws:%b}" if ws else "")
        changes += ch
    return text, changes


def merge_setting(text: str, sec: str, key: str, value: Any, stamp: str) -> tuple[str, list[str]]:
    try:
        current = tomllib.loads(text).get(sec, {}).get(key) if text.strip() else None
    except tomllib.TOMLDecodeError:
        current = None
    if current == value or (isinstance(current, (int, float)) and not isinstance(current, bool)
                            and isinstance(value, (int, float)) and float(current) == float(value)):
        return text, []
    line = f"{key} = {_toml_value(value)}  # coach {stamp}"
    sect = re.search(rf"^\[{re.escape(sec)}\][^\n]*\n(?:(?!\[)[^\n]*\n?)*", text, re.M)
    if sect:
        block = sect.group(0)
        pat = re.compile(rf"^{re.escape(key)}\s*=.*$", re.M)
        new = pat.sub(lambda _: line, block, count=1) if pat.search(block) else \
            block[:block.index("\n") + 1] + line + "\n" + block[block.index("\n") + 1:]
        text = text[:sect.start()] + new + text[sect.end():]
    else:
        text = text.rstrip("\n") + f"\n\n[{sec}]\n{line}\n"
    label = "bike FTP" if (sec, key) == ("bike", "ftp_w") else f"{sec}.{key}"
    unit = " W" if (sec, key) == ("bike", "ftp_w") else ""
    old = _toml_value(current) if current is not None else "—"
    return text, [f"{label}: {old} → {_toml_value(value)}{unit}"]


def merge_athlete(text: str, ftp_w: int, stamp: str) -> tuple[str, list[str]]:
    return merge_setting(text, "bike", "ftp_w", ftp_w, stamp)


def plan(up: Update, config: Path, stamp: str) -> tuple[dict[Path, str], list[str]]:
    """New file contents and the list of changes, without writing anything."""
    if not config.exists():
        raise ValueError(f"{config.name} doesn't exist yet - copy config.example.toml to private/config.toml first")
    old = text = config.read_text()
    changes: list[str] = []
    if up.weeks:
        text, ch = merge_season(text, up.weeks, S.load_season(config).week1_start, stamp)
        changes += ch
    for o in up.sessions:
        text, ch = merge_array(text, "session", (o["date"], o.get("name", "")), o, stamp)
        changes += ch
    for r in up.races:
        text, ch = merge_array(text, "race", r["date"], r, stamp)
        changes += ch
    for (sec, key), v in up.settings.items():
        text, ch = merge_setting(text, sec, key, v, stamp)
        changes += ch
    tomllib.loads(text)                          # never write a file that doesn't parse
    return ({config: text} if text != old else {}), changes


def apply(up: Update, config: Path, now: datetime | None = None,
          backups: Path = BACKUPS, archive: Path | None = ARCHIVE, answer: str = "") -> list[str]:
    now = now or datetime.now()
    files, changes = plan(up, config, f"{now:%d %b %Y}")
    for p, text in files.items():
        if p.exists():
            backups.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, backups / f"{p.stem}-{now:%Y%m%d-%H%M%S}{p.suffix}")
        tmp = p.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(p)
    if archive is not None and answer:
        # the full answer, notes included, for the record
        archive.mkdir(parents=True, exist_ok=True)
        (archive / f"{now:%Y-%m-%d-%H%M%S}.md").write_text(
            f"# Coach answer, {now:%d %b %Y %H:%M}\n\n## Applied\n\n"
            + ("".join(f"- {c}\n" for c in changes) or "- nothing changed\n")
            + ("\n## Not taken over\n\n" + "".join(f"- {w}\n" for w in up.warnings) if up.warnings else "")
            + f"\n## Full answer\n\n{answer.strip()}\n")
    return changes


# --------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "prompt"
    config = S.config_path()
    if mode == "prompt":
        from pathlib import Path as _P
        from model import load_model
        model = load_model(_P(__file__).parent / "data" / "cache")
        print(prompt(S.load_season(config), S.load_athlete(config), date.today(), model))
        return 0
    if mode not in ("preview", "apply"):
        print(__doc__)
        return 2
    text = sys.stdin.read()
    try:
        up = parse(text)
        if mode == "preview":
            _, changes = plan(up, config, f"{datetime.now():%d %b %Y}")
        else:
            changes = apply(up, config, answer=text)
        print(json.dumps({"ok": True, "changes": changes, "warnings": up.warnings, "notes": up.notes}))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "changes": [], "warnings": [], "notes": ""}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
