#!/usr/bin/env python3
"""Render dashboard.html (home), season.html and health.html from data/cache/.

Every metric shows actual, target and direction; amber and red always come with
a line saying what to do; missing data is shown as missing. Never rendered
anywhere: VO2 max, race predictor, training status, endurance/hill score,
performance condition, body battery, LT heart rate, Garmin FTP.
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path

import charts as C
import coach
import metrics as M
import settings as S
from model import Model, load_model

ROOT = Path(__file__).parent


def version() -> str:
    try:
        return (ROOT / "VERSION").read_text().strip()
    except OSError:
        return "dev"
DASH = "–"
NAV = [("home", "Home", "dashboard.html"), ("season", "Season", "season.html"),
       ("health", "Health", "health.html")]


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------
def mmss(sec: float | None) -> str:
    if sec is None:
        return DASH
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}"


def hmin(sec: float | None) -> str:
    if not sec:
        return DASH
    m = int(round(sec / 60))
    return f"{m // 60}:{m % 60:02d}"


def clock(dt: datetime | None) -> str:
    return f"{dt:%H:%M}" if dt else DASH


def clock_noon(minutes_after_noon: float | None) -> str:
    if minutes_after_noon is None:
        return DASH
    m = int(round(minutes_after_noon + 720)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def when(dt: datetime | None, now: datetime, morning: bool = False) -> str:
    if dt is None:
        return DASH
    days = (dt.date() - now.date()).days
    day = "today" if days == 0 else "tomorrow" if days == 1 else f"{dt:%a}"
    return f"{day} morning" if morning else f"{day} {dt:%H:%M}"


def countdown(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days < 14:
        return f"{days} days"
    return f"{round(days / 7)} wks"


def arrow(cur: float | None, prev: float | None) -> str:
    return M._direction(cur, prev)


def week_label(season: S.Season, wk: date) -> str:
    n = season.week_number(wk)
    return f"W{n}" if n else f"{wk:%d.%m}"


# --------------------------------------------------------------------------
# building blocks
# --------------------------------------------------------------------------
STATUS_LABEL = {"good": "on target", "warn": "drifting", "act": "act now", "none": "no data"}
shown = M.shown


def cell(label: str, value: str, target: str = "", status: str = "neutral",
         direction: str = "", sub: str = "", progress: tuple[float, float | None] | None = None,
         split: bool = False, key: str = "") -> str:
    """One metric. The dot is its status: green on target, amber drifting, red act
    now, grey no data. A metric with nothing to judge yet has no dot; a count that
    is still in progress shows a bar instead, coloured once it's decided."""
    dot = f'<i class="dot" title="{STATUS_LABEL[status]}"></i>' if status in STATUS_LABEL else ""
    words = {"\u2191": "up on last week", "\u2193": "down on last week", "\u2192": "same as last week"}
    d = (f'<span class="m-d" title="{words.get(direction, "")}" aria-label="{words.get(direction, "")}">'
         f'{direction}</span>' if direction else "")
    t = f'<span class="m-t">{target}</span>' if target else ""
    s = f'<div class="m-s">{sub}</div>' if sub else ""
    bar = ""
    if progress is not None and progress[1]:
        pct = min(progress[0] / progress[1] * 100, 100)
        bar = f'<div class="mprog"><i style="width:{pct:.0f}%"></i></div>'
    # split: the value holds several judged parts, each coloured on its own
    v = f' data-v="{key}"' if key else ""            # the verdict key its alert carries
    return (f'<div class="m st-{status}"{v}><div class="m-l">{dot}{label}</div>'
            f'<div class="m-v{" split" if split else ""}">{value}{t}{d}</div>{bar}{s}</div>')


def group(title: str, body: str, cls: str = "") -> str:
    return f'<div class="grp {cls}"><div class="grp-h">{title}</div>{body}</div>'


# --------------------------------------------------------------------------
# home
# --------------------------------------------------------------------------
def today_card(m: Model, cfg: dict, hs: M.HardSession, ts: M.Today, now: datetime) -> str:
    today = now.date()
    pill = {"open": ("open", "OPEN"), "closed": ("closed", "CLOSED"),
            "nodata": ("nodata", "NO DATA")}[hs.state]
    fails = [c for c in hs.checks if c.state == "fail"]
    if hs.state == "closed":
        top = max((c for c in fails if c.opens), key=lambda c: c.opens, default=None)
        morning = bool(top and top.label in ("Readiness", "Two solid nights"))
        reason = " · ".join(escape(c.detail) for c in fails)
        reason = reason[:1].upper() + reason[1:]
        reason += f" — opens {when(hs.opens, now, morning)} at the earliest" if hs.opens else ""
    elif hs.state == "nodata":
        reason = " · ".join(escape(c.detail) for c in hs.checks if c.state == "nodata")
    else:
        reason = "All checks pass."
    checks = "".join(
        f'<li class="ck-{c.state}"><span>{"✓" if c.state == "ok" else "✕" if c.state == "fail" else "?"}</span>'
        f'{escape(c.label)} <span class="dim">· {escape(c.detail)}</span></li>' for c in hs.checks)

    r = ts.reading                           # latest reading: readiness and recovery move after every workout
    last_r = max([*m.readiness, *m.readiness_now], default=None)
    rc, max_rec = cfg["readiness"], cfg["hard_session"]["max_recovery_hours"]
    since = f"no data since {last_r:%a %d %b}" if last_r else "no data"
    rec_m = ts.recovery
    if rec_m.value is not None:
        # hours left now, counted down from the reading (yesterday's, before the
        # first reading of the day) - the same numbers the check and the alert use
        if ts.recovery_opens:
            sub = f"under {max_rec} h {when(ts.recovery_opens, now)}"
        elif rec_m.value <= 0:
            sub = "fully recovered"
        else:
            sub = f"fully recovered {when(ts.recovered_at, now)}"
        rec = cell("Recovery", f"{shown(rec_m.value, max_rec)} h", f"/ ≤ {max_rec} h", rec_m.status, key="recovery",
                   sub=f"{sub} · read {escape(rec_m.note)}")
    else:
        rec = cell("Recovery", DASH, f"/ ≤ {max_rec} h", "none", sub=since if not r else "")
    if r:
        rd = cell("Readiness", f"{r.score:.0f}" if r.score is not None else DASH, "", ts.readiness.status, key="readiness",
                  sub=escape(ts.readiness.note) + (f" · {clock(r.at)}" if r.at else ""))
        sh = ts.sleep_history
        hist = cell("Sleep history", f"{sh.value:.0f} %" if sh.value is not None else DASH,
                    "", sh.status, key="sleep_history", sub=f"Garmin: {escape(sh.note)}" if sh.note else "readiness component")
    else:
        rd = cell("Readiness", DASH, "", "none", sub=since)
        hist = cell("Sleep history", DASH, "", "none", sub=since)
    hv = ts.hrv
    hrv = cell("HRV 7-day", shown(hv.value, rc["hrv_low"] if hv.value < rc["hrv_low"] else rc["hrv_high"])
               if hv.value else DASH, f"/ {rc['hrv_low']}–{rc['hrv_high']}", hv.status,
               arrow(hv.value, hv.prev), sub=hv.note, key="hrv")

    n = m.nights.get(today)
    if n and n.hours:
        solid = M.is_solid(n, cfg)
        score = f"{n.score:.0f}" if n.score is not None else DASH
        solid_text = "solid" if solid else ("not solid" if solid is False else "score unavailable")
        solid_class = "t-good" if solid else "t-warn"
        night = (f'Last night <b>{clock(n.bedtime)} → {clock(n.wake)}</b> · {n.hours:.1f} h · '
                 f'score {score} · <span class="{solid_class}">{solid_text}</span>')
    else:
        last_n = max((d for d, x in m.nights.items() if x.hours), default=None)
        night = f'Last night <span class="dim">no sleep data{f" since {last_n:%a}" if last_n else ""}</span>'

    return f"""<section class="card today">
  <div class="pill-row"><div class="pill {pill[0]}">HARD SESSION <b>{pill[1]}</b></div></div>
  <div class="reason">{reason}</div>
  <div class="mgrid four">{rd}{rec}{hrv}{hist}</div>
  <div class="night">{night}</div>
  <div class="checks"><ul>{checks}</ul></div>
</section>"""


def week_card(w: M.Week, cfg: dict, m: Model, ts: M.Today, now: datetime) -> str:
    thr, oc = w.threshold, w.over_cap
    band = thr.target
    tgt = f"/ {M.band_text(*band)}" if band else "/ no target"
    scale = max([band[1] if band else 0, thr.value + oc.value, 1]) * 1.12
    band_div = (f'<div class="hb-band" style="left:{band[0] / scale * 100:.1f}%;'
                f'width:{max((band[1] - band[0]) / scale * 100, 0.8):.1f}%"></div>' if band else "")   # a single figure stays visible
    hero_bar = (f'<div class="herobar">{band_div}'
                f'<div class="hb-in" style="width:{thr.value / scale * 100:.1f}%"></div>'
                f'<div class="hb-over" style="left:{thr.value / scale * 100:.1f}%;width:{oc.value / scale * 100:.1f}%"></div></div>')
    over = (f'<span class="t-act st-act" data-v="over_cap">over cap <b>{oc.value:.1f}</b> km</span>' if oc.value > 0
            else '<span class="dim">over cap 0.0 km</span>')
    progress = "" if w.complete else '<span class="tag">week in progress</span>'

    rk = w.run_km
    rk_dot = f'<i class="dot" title="{STATUS_LABEL[rk.status]}"></i>' if rk.status in STATUS_LABEL else ""
    rk_pct = min(rk.value / rk.target * 100, 100) if rk.target else 0
    run_bar = (f'<div class="runrow st-{rk.status}" data-v="run_km"><div class="m-l">{rk_dot}Run km</div>'
               f'<div class="m-v">{rk.value:.1f}<span class="m-t">/ {f"{rk.target:g}" if rk.target else "no target"}</span>'
               f'<span class="m-d">{arrow(rk.value, rk.prev)}</span></div>'
               f'<div class="prog"><div style="width:{rk_pct:.1f}%"></div></div></div>')

    ec, bc, sc = cfg["easy"], cfg["bike"], cfg["sleep"]
    lr = w.long_run
    # The coloured number is always the one being judged: the long run is judged on
    # its average HR, not its distance.
    lr_cap = cfg["long_run"]["hr_cap_bpm"]
    if lr and lr.act.avg_hr:
        long_cell = cell("Long run · avg HR", shown(lr.act.avg_hr, lr_cap), f"/ ≤ {lr_cap}", lr.status, key="long_run",
                         sub=f"{lr.act.km:.1f} km · {hmin(lr.act.duration_s)}")
    elif lr:
        long_cell = cell("Long run · avg HR", DASH, f"/ ≤ {lr_cap}", "none",
                         sub=f"{lr.act.km:.1f} km · no HR")
    else:
        long_cell = cell("Long run · avg HR", DASH, f"/ ≤ {lr_cap}", sub="none yet")

    # Easy runs have two rules - average HR and share of time above the cap. Show
    # both, each marked pass/fail, so the red lands on the one that failed.
    cap, max_share = ec["hr_cap_bpm"], ec["max_share_above"]
    if w.easy_avg_hr.value is not None:
        avg, share = w.easy_avg_hr.value, w.easy_share.value
        avg_ok = avg <= cap
        share_ok = None if share is None else share < max_share

        def part(text: str, ok: bool | None) -> str:
            cls = "ok" if ok else ("bad" if ok is False else "unk")
            mark = {"ok": "✓", "bad": "✗", "unk": "?"}[cls]
            return f'<span class="pt {cls}">{text}<i>{mark}</i></span>'

        value = (part(f'{shown(avg, cap)}<span class="m-t">avg</span>', avg_ok)
                 + part(f'{shown(share, max_share, pct=True)}<span class="m-t">&gt;{cap}</span>' if share is not None
                        else f'{DASH}<span class="m-t">&gt;{cap}</span>', share_ok))
        # the verdict is per run: one run over the line fails the week even when
        # the pooled numbers pass, and gets its own marked part
        n_easy = sum(1 for s in w.sessions if s.kind == "easy")
        if w.easy_failed:
            value += part(f'{len(w.easy_failed)}/{n_easy}<span class="m-t">runs over</span>', False)
        easy_cell = cell("Easy-run HR", value, "", w.easy.status, key="easy",
                         sub=f"targets: avg ≤ {cap} · under {max_share:.0%} of the time above {cap}",
                         split=True)
    else:
        easy_cell = cell("Easy-run HR", DASH, f"/ ≤ {cap}", sub="no easy runs yet")
    share_v = w.run_share.value
    ld = m.load.get(now.date()) or (m.load[max(m.load)] if m.load else None)
    ld_prev = m.load.get(now.date() - timedelta(days=7))
    lo, hi = cfg["load"]["acwr_low"], cfg["load"]["acwr_high"]
    ratio = ts.acwr.value
    latest_ratio = ld.ratio if ld else None
    mb = ts.bedtime.value
    disc = w.discipline

    running = group("Running", f"""
    <div class="hero st-{thr.status}" data-v="threshold">
      <div class="k">Threshold km <span class="dim">· within cap</span></div>
      <div class="hero-v">{thr.value:.1f}<span class="m-t">{tgt}</span><span class="m-d">{arrow(thr.value, thr.prev)}</span></div>
      {hero_bar}
      <div class="hero-s">{over}</div>
    </div>
    {run_bar}
    <div class="mgrid three">
      {cell("Run days", f"{w.run_days.value:.0f}", f"/ {cfg['runs']['run_days_target']}", w.run_days.status,
            arrow(w.run_days.value, w.run_days.prev), progress=(w.run_days.value, cfg['runs']['run_days_target']),
            key="run_days")}
      {long_cell}{easy_cell}
    </div>""", "run")
    bike = group("Bike", f"""<div class="mgrid two">
      {cell("Hours", f"{shown(w.bike_hours.value, bc['max_hours'], 1)} h", f"/ ≤ {bc['max_hours']:g} h", w.bike_hours.status,
            arrow(w.bike_hours.value, w.bike_hours.prev), key="bike_hours")}
      {cell("Run share", shown(share_v, bc['min_run_share'], pct=True) if share_v is not None else DASH, f"/ ≥ {bc['min_run_share']:.0%}",
            w.run_share.status, arrow(share_v, w.run_share.prev), key="run_share", sub=w.run_share.note or "running vs bike volume")}
      {cell("Discipline", str(len(disc)), "/ 0", w.discipline_m.status, key="discipline",
            sub=escape(disc[0][1]) + (f" +{len(disc) - 1} more" if len(disc) > 1 else "") if disc else "no issues")}
      {cell("Threshold min", f"{w.bike_thr_min.value:.0f} min" if w.bike_thr_min.value is not None else DASH,
            "", w.bike_thr_min.status, sub=w.bike_thr_min.note or f"≥ {bc['threshold_pct']:.0%} FTP")}
    </div>""")
    sleep = group("Sleep", f"""<div class="mgrid one">
      {cell("Solid nights", f"{w.solid_nights.value:.0f}", f"/ ≥ {sc['solid_nights_target']}", w.solid_nights.status, key="solid_nights",
            progress=(w.solid_nights.value, sc['solid_nights_target']))}
      {cell("Bedtime 7-day", clock_noon(mb), f"/ ≤ {sc['bedtime_target']}",
            ts.bedtime.status, arrow(mb, ts.bedtime.prev), key="bedtime")}
    </div>""")
    load = group("Load &amp; gym", f"""<div class="mgrid one">
      {cell("ACWR", shown(ratio, hi if ratio > hi else lo, 2) if ratio is not None else DASH, f"/ {lo:g}–{hi:g}", ts.acwr.status,
            arrow(latest_ratio, ld_prev.ratio if ld_prev else None), sub=ts.acwr.note, key="acwr")}
      {cell("Gym", f"{w.gym.value:.0f}", f"/ {w.gym.target}" if w.gym.target else "", w.gym.status,
            arrow(w.gym.value, w.gym.prev), progress=(w.gym.value, w.gym.target), key="gym")}
    </div>""")
    legend = ('<div class="st-legend"><span><i class="dot g"></i>on target</span><span><i class="dot w"></i>drifting</span>'
              '<span><i class="dot a"></i>act now</span><span><i class="dot n"></i>no data</span>'
              '<span><i class="bar"></i>in progress</span></div>')
    return f"""<section class="card week">
  <div class="wk-head"><h2>This week</h2>{progress}</div>
  {running}
  <div class="wk-groups">{bike}{sleep}{load}</div>
  {legend}
</section>"""


def alert_id(a: M.Alert) -> str:
    """Stable while the alert says the same thing; a changed alert is a new one."""
    import hashlib
    return hashlib.sha1(f"{a.key}|{a.what}".encode()).hexdigest()[:12]


def alerts_card(alerts: list[M.Alert]) -> str:
    if not alerts:
        return ""
    rows = "".join(f'<li class="al-{a.level}" data-k="{a.key}" data-id="{alert_id(a)}"><i class="dot"></i>'
                   f'<div class="al-b"><div class="al-w">{escape(a.what)}</div>'
                   f'<div class="al-a">→ {escape(a.action)}</div></div>'
                   f'<button class="al-v" type="button" title="Mark as viewed" aria-pressed="false">✓</button></li>'
                   for a in alerts)
    n_act = sum(1 for a in alerts if a.level == "act")
    n_warn = sum(1 for a in alerts if a.level == "warn")
    counts = "".join(f'<span class="al-n al-{lvl}" data-lvl="{lvl}"><i class="dot"></i><b>{n}</b> {txt}</span>'
                     for lvl, n, txt in (("act", n_act, "act now"), ("warn", n_warn, "drifting")) if n)
    counts += '<span class="al-n al-seen" hidden><b>0</b> viewed</span>'
    # collapsible; the summary keeps the counts visible while closed
    return (f'<details class="card alerts" id="alerts"><summary><h2>Alerts</h2>{counts}'
            f'<span class="chev" aria-hidden="true"></span></summary><ul>{rows}</ul></details>')


def session_detail(s: M.Session, cfg: dict) -> tuple[str, str]:
    """Key figure for the row, and the tap-open detail."""
    a = s.act
    bits: list[str] = []
    if s.reps:
        rows = []
        for i, r in enumerate(s.reps, 1):
            if r.kind == "subt":
                cls = ("t-warn" if r.near else "t-good" if r.within
                       else ("dim" if s.accepted else "t-act") if r.within is False else "dim")
                hr = f'<span class="{cls}">{r.avg_hr:.0f}</span> <span class="dim">/ {r.cap}</span>' if r.avg_hr else DASH
            else:
                hr = f'<span class="dim">{r.avg_hr:.0f} · {r.kind}</span>' if r.avg_hr else r.kind
            rows.append(f"<tr><td>{i}</td><td class=num>{r.distance_m:.0f} m</td><td class=num>{mmss(r.pace)}</td>"
                        f"<td class=num>{hr}</td></tr>")
        bits.append(f'<table class="reps"><tr><th>Rep</th><th class=num>Dist</th><th class=num>Pace /km</th>'
                    f'<th class=num>HR / cap</th></tr>{"".join(rows)}</table>')
        sub = [r for r in s.reps if r.kind == "subt"]
        fig = "reps " + "·".join(f"{r.avg_hr:.0f}" for r in (sub or s.reps) if r.avg_hr)
    elif s.kind == "easy":
        fig = f"HR {a.avg_hr:.0f}" if a.avg_hr else ""
        if s.hr_above:
            ab, tot = s.hr_above
            fig += f" · {ab / tot:.0%} &gt;{cfg['easy']['hr_cap_bpm']}"
            bits.append(f"{ab / 60:.0f} of {tot / 60:.0f} min above {cfg['easy']['hr_cap_bpm']} bpm ({ab / tot:.0%}).")
    elif s.kind in ("long", "race", "test"):
        fig = f"{hmin(a.duration_s)}" + (f" @ {a.avg_hr:.0f}" if a.avg_hr else "")
        if a.pace and a.sport == "run":
            bits.append(f"Pace {mmss(a.pace)}/km.")
    elif s.kind == "ride":
        fig = f"{a.hours:.1f} h" + (f" · HR {a.avg_hr:.0f}" if a.avg_hr else "")
        bits += [f'<span class="t-warn">{escape(f)}</span>' for f in s.flags]
        if s.thr_min is not None:
            bits.append(f"{s.thr_min:.0f} min at threshold power.")
    else:
        fig = hmin(a.duration_s)
    if a.sport == "run" and not a.indoor:
        if a.temp_c is not None:
            bits.append(f"Weather {a.temp_c:.0f} °C" + (" (reading looks wrong)" if a.temp_suspect else "") + ".")
        else:
            bits.append('<span class="dim">No weather data.</span>')
    for note in s.notes:
        bits.append(f'<span class="dim">{escape(note)}.</span>')
    if s.coach:
        verdict = " Verdict accepted." if s.accepted else ""
        bits.insert(0, f'<b>Coach:</b> {escape(s.coach)}.{verdict}')
    bits.append(f'<a href="https://connect.garmin.com/modern/activity/{escape(a.id)}" '
                f'target="_blank" rel="noopener">Open in Garmin Connect</a>')
    return fig, "".join(f"<p>{b}</p>" if not b.startswith("<table") else b for b in bits)


def sessions_card(w: M.Week, cfg: dict) -> str:
    rows = []
    for s in w.sessions:
        fig, detail = session_detail(s, cfg)
        st = s.status if s.kind not in ("race", "other") else "neutral"
        dot = (f'<i class="dot st-{st}" title="{STATUS_LABEL[st]}"></i>' if st in STATUS_LABEL
               else '<i class="nodot"></i>')
        rows.append(f'<details class="sess"><summary><span class="sd">{s.act.start:%a}</span>'
                    f'<span class="stt">{escape(s.title)}{" ✓" if s.accepted else ""}</span><span class="sf">{fig}</span>'
                    f'{dot}</summary><div class="sdet">{detail}</div></details>')
    body = "".join(rows) or '<p class="dim">No sessions yet this week.</p>'
    return f'<section class="card"><h2>Sessions · Mon–Sun</h2>{body}</section>'


def last_weeks(m: Model, cfg: dict, season: S.Season, now: datetime, n: int = 12) -> list[M.Week]:
    """The last n weeks, oldest first; the last one is the current week."""
    cur = M.monday(now.date())
    return [M.week_summary(cur - timedelta(weeks=n - 1 - i), m, cfg, season, now) for i in range(n)]


def trends(m: Model, cfg: dict, season: S.Season, now: datetime, summ: list[M.Week]) -> str:
    today = now.date()
    weeks = [s.start for s in summ]
    labels = [week_label(season, wk) for wk in weeks]
    start, n_days = weeks[0], 84
    days = [start + timedelta(days=i) for i in range(n_days)]
    xl = [(7 * i + 3, labels[i]) for i in range(12) if (11 - i) % 2 == 0]
    hl = (7 * 11, min(7 * 11 + 6, n_days - 1))

    # 1 threshold km + over-cap
    t1 = C.week_stack(labels, [s.threshold.value for s in summ], [s.over_cap.value for s in summ],
                      [s.threshold.target for s in summ], 11,
                      [f"{labels[i]}: {s.threshold.value:.1f} km within cap, {s.over_cap.value:.1f} over"
                       + (f" · band {M.band_text(*s.threshold.target)}" if s.threshold.target else "")
                       for i, s in enumerate(summ)])
    # 2 run km vs target
    t2 = C.week_bars(labels, [s.run_km.value for s in summ], [s.run_km.target for s in summ], 11,
                     [f"{labels[i]}: {s.run_km.value:.1f} km" + (f" / {s.run_km.target:g}" if s.run_km.target else "")
                      for i, s in enumerate(summ)])
    # 3 pace at fixed HR
    pc = cfg["pace_at_hr"]
    quality_ends = [se.act.end for s in summ for se in s.sessions
                    if se.kind in ("subt", "speed", "race", "test")]
    pts = []
    for s in summ:
        for se in s.sessions:
            if se.kind in ("easy", "long") and (p := M.pace_at_hr(se.act, cfg, quality_ends)):
                pts.append(p)
    xs = [(p.day - start).days for p in pts]
    valid = [p.valid for p in pts]
    fit = M.linear_fit([x for x, ok in zip(xs, valid) if ok], [p.pace for p in pts if p.valid])
    tips3 = [f"{p.day:%a %d %b}: {mmss(p.pace)}/km over {p.minutes:.0f} min"
             + ("" if p.valid else f" · excluded: {', '.join(p.reasons)}") for p in pts]
    t3 = (C.scatter_trend(xs, [p.pace for p in pts], valid, tips3, n_days, fit, xl, hl, mmss, invert=True)
          if pts else '<p class="dim empty">No easy runs with enough time at this HR yet.</p>')
    n_valid = sum(valid)
    trend_txt = ""
    if fit and n_valid >= 3:
        per_wk = fit[0] * 7
        trend_txt = (f"{abs(per_wk):.1f} s/km per week {'faster' if per_wk < 0 else 'slower'}"
                     f" · {n_valid} valid runs")
    # 4 LT pace (step line)
    lt = [m.lt_pace_on(d) if d <= today and m.lt_pace and d >= m.lt_pace[0][0] else None for d in days]
    lt_tips = [f"{d:%a %d %b}: LT {mmss(v)}/km" if v else "" for d, v in zip(days, lt)]
    t4 = C.day_line(n_days, [("var(--s1)", [-v if v else None for v in lt], "line")], None, xl, hl,
                    fmt=lambda v: mmss(-v), tips=lt_tips, steps=(5, 10, 15, 30, 60)) if any(lt) else '<p class="dim empty">No LT data.</p>'
    lt_now = next((v for v in reversed(lt) if v), None)
    pw_now = m.lt_power[-1][1] if m.lt_power else None
    # 5 bedtime + score
    bt = [M.bedtime_minutes(n.bedtime) if (n := m.nights.get(d)) and n.bedtime else None for d in days]
    sc = [n.score if (n := m.nights.get(d)) else None for d in days]
    tgt = (S.bedtime_target_minutes(cfg) - 720) % 1440
    bt_tips = [f"{d:%a %d %b}: bed {clock_noon(v)}" if v is not None else "" for d, v in zip(days, bt)]
    t5a = C.day_dots(n_days, bt, tgt, (540, 900), xl, hl, clock_noon, bt_tips, height=120,
                     yticks=[540, 660, 780, 900])
    t5b = C.day_line(n_days, [("var(--ink2)", sc, "faint")], None, xl, hl, height=80, y_range=(40, 100),
                     nticks=2, tips=[f"{d:%a %d %b}: score {v:.0f}" if v else "" for d, v in zip(days, sc)])
    # 6 HRV + ACWR
    rc, lc = cfg["readiness"], cfg["load"]
    hv = [m.hrv[d].weekly_avg if d in m.hrv else None for d in days]
    ac = [m.load[d].ratio if d in m.load else None for d in days]
    t6a = C.day_line(n_days, [("var(--s1)", hv, "line")], (rc["hrv_low"], rc["hrv_high"]), xl, hl, height=110,
                     tips=[f"{d:%a %d %b}: HRV 7-day {v:.0f}" if v else "" for d, v in zip(days, hv)])
    t6b = C.day_line(n_days, [("var(--s1)", ac, "line")], (lc["acwr_low"], lc["acwr_high"]), xl, hl, height=110,
                     fmt=lambda v: f"{v:g}",
                     tips=[f"{d:%a %d %b}: ACWR {v:.2f}" if v else "" for d, v in zip(days, ac)])

    def panel(title: str, note: str, body: str, legend: str = "") -> str:
        return (f'<div class="card panel"><h3>{title}</h3>'
                f'{f"<div class=legend>{legend}</div>" if legend else ""}{body}'
                f'{f"<div class=note>{note}</div>" if note else ""}</div>')

    sw = lambda cls, txt: f'<span class="lg"><i class="sw {cls}"></i>{txt}</span>'
    return f"""<h2 class="sec">Trends · 12 weeks</h2>
<div class="tgrid">
{panel("Threshold km", "Shaded: the week's band. This week highlighted.", t1,
       sw("s1", "within cap") + sw("sact", "over cap") + sw("band", "band"))}
{panel("Run km vs target", "", t2, sw("s1", "run km") + sw("target", "target"))}
{panel(f"Pace at HR {pc['hr_low']}\u2013{pc['hr_high']}", trend_txt or f"Flat, \u2264 {pc['max_temp_c']:g} \u00b0C easy runs; first {pc['skip_first_min']} min ignored. Faster is up.", t3,
       sw("s1", "counts") + sw("grey", "excluded (warm, hilly, indoor, day after quality)") + sw("fit", "trend"))}
{panel("LT pace", f"Now {mmss(lt_now)}/km" + (f" · running power {pw_now:.0f} W" if pw_now else "") + ". Faster is up.", t4)}
{panel("Bedtime + sleep score", "", t5a + t5b, sw("s1", "bedtime") + sw("warn", f"after {cfg['sleep']['bedtime_target']}") + sw("faint", "score"))}
{panel("HRV + ACWR", "", t6a + t6b, sw("s1", "HRV 7-day / ACWR") + sw("band", "target band"))}
</div>"""


def home(m: Model, cfg: dict, season: S.Season, now: datetime) -> str:
    # computed once and shared: the week summaries feed the cards, alerts and trends
    summ = last_weeks(m, cfg, season, now)
    w, previous = summ[-1], summ[-2]
    ts = M.today_state(m, cfg, now)
    hs = M.hard_session(m, cfg, season, now, ts)
    alert_rows = M.alerts(w, m, cfg, now, ts)
    if now.date().weekday() < cfg["app"]["carryover_days"]:
        # last week's missed targets stay visible for the first days of the new week
        alert_rows += M.carryover_alerts(previous, cfg, f"Last week ({week_label(season, previous.start)})")
    return (today_card(m, cfg, hs, ts, now) + week_card(w, cfg, m, ts, now) + alerts_card(alert_rows)
            + sessions_card(w, cfg) + trends(m, cfg, season, now, summ))


# --------------------------------------------------------------------------
# season
# --------------------------------------------------------------------------
def season_page(m: Model, cfg: dict, season: S.Season, now: datetime) -> str:
    today = now.date()
    if season.missing or not (season.races or season.phases):
        return ('<section class="card"><h2>No season plan yet</h2><p class="dim">Copy '
                '<code>config.example.toml</code> to <code>private/config.toml</code> and fill in '
                '<code>[season]</code>, races, phases and weekly targets.</p></section>')
    nxt = season.next_race(today)
    hero = ""
    if nxt:
        hero = (f'<div class="hero"><div class="k">Next race</div><div class="race-name">{escape(nxt.name)}</div>'
                f'<div class="hero-s">{nxt.date:%a %d %b} · in {countdown((nxt.date - today).days)}</div></div>')
    results: dict[date, M.Session] = {}
    for r in season.races:
        if r.date <= today:
            wk = M.monday(r.date)
            for s in M.classify(m.in_range(wk, wk + timedelta(days=7)), m, cfg, season):
                if s.kind == "race" and s.act.day == r.date:
                    results[r.date] = s
    rows = []
    for r in season.races:
        past = r.date < today
        res = results.get(r.date)
        if res:
            a = res.act
            detail = (f"{hmin(a.duration_s)} · {mmss(a.pace)}/km" + (f" · HR {a.avg_hr:.0f}" if a.avg_hr else ""))
        elif past:
            detail = '<span class="dim">no activity found</span>'
        else:
            detail = f"in {countdown((r.date - today).days)}"
        rows.append(f'<li class="race{" past" if past else ""}{" a" if r.priority == "A" else ""}">'
                    f'<span class="rd">{r.date:%d %b}</span><span class="rn">{escape(r.name)}</span>'
                    f'<span class="rx">{detail}</span></li>')

    first = season.week1_start or M.monday(today) - timedelta(weeks=16)
    # the season's own span; a finished season doesn't stretch out to today
    last_day = max([r.date for r in season.races] + [p.end for p in season.phases], default=today)
    weeks = []
    wk = M.monday(first)
    while wk <= last_day:
        weeks.append(wk)
        wk += timedelta(days=7)
    actual = [sum(a.km for a in m.in_range(w, w + timedelta(days=7)) if a.sport == "run") if w <= today else None
              for w in weeks]
    targets = [season.targets(w)["run_km"] for w in weeks]
    labels = [week_label(season, w) for w in weeks]
    cur = next((i for i, w in enumerate(weeks) if w <= today < w + timedelta(days=7)), None)
    phases = []
    for p in season.phases:
        idx = [i for i, w in enumerate(weeks) if p.start <= w + timedelta(days=3) <= p.end]
        if idx:
            phases.append((p.name, idx[0], idx[-1],
                           f"{p.name} · {p.start:%d %b} – {p.end:%d %b}"
                           + (" · recovery: running-share rule off" if p.recovery else "")))
    tips = []
    for i, (w, a, t) in enumerate(zip(weeks, actual, targets)):
        ph = season.targets(w)["phase"]
        bits = [f"{labels[i]} · {w:%d %b}"]
        if a is not None:
            bits.append(f"{a:.0f} km run")
        if t:
            bits.append(f"plan {t:g} km")
        if ph:
            bits.append(ph)
        tips.append(" · ".join(bits))
    chart = C.season_bars(labels, actual, targets, cur, tips, phases, short=season.phase_short)
    no_plan = ("" if any(targets) else
               '<p class="note">No weekly targets yet — use the Coach button, or add phases or weeks to the config.</p>')
    return f"""<section class="card">{hero}<ul class="races">{"".join(rows)}</ul></section>
<section class="card"><h2>Running km across the season</h2>
<div class="legend"><span class="lg"><i class="sw s1"></i>actual</span><span class="lg"><i class="sw plan"></i>plan</span><span class="lg"><i class="sw rec"></i>recovery phase</span></div>
<div class="scrollx">{chart}</div>{no_plan}</section>"""


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------
def health_page(m: Model, cfg: dict, now: datetime) -> str:
    today = now.date()
    d90 = [today - timedelta(days=89 - i) for i in range(90)]
    xl90 = [(i, f"{d:%d.%m}") for i, d in enumerate(d90) if i % 14 == 0]

    rhr = [m.nights[d].rhr if d in m.nights else None for d in d90]
    rhr_now = next((v for v in reversed(rhr) if v), None)
    rhr_3 = [v for v in rhr[-3:] if v]
    rhr_30 = [v for v in rhr[-30:] if v]
    rhr_avg = statistics.fmean(rhr_30) if rhr_30 else None
    rhr_chart = C.day_line(90, [("var(--s4)", rhr, "line")], None, xl90, None, width=720, height=150,
                           tips=[f"{d:%a %d %b}: {v:.0f} bpm" if v else "" for d, v in zip(d90, rhr)])

    d30 = d90[-30:]
    keys = [("deep", "deep_s"), ("light", "light_s"), ("rem", "rem_s"), ("awake", "awake_s")]
    stacks = [(cls, [((getattr(m.nights[d], k) or 0) / 3600 if d in m.nights and m.nights[d].hours else 0)
                     for d in d30]) for cls, k in keys]
    missing = [not (d in m.nights and m.nights[d].hours) for d in d30]
    tips = [(f"{d:%a %d %b}: {m.nights[d].hours:.1f} h · score {m.nights[d].score or 0:.0f}"
             if not miss else f"{d:%a %d %b}: no sleep data") for d, miss in zip(d30, missing)]
    stages = C.stacked_days([f"{d:%d.%m}" for d in d30], stacks, tips, missing, width=720)
    hrs = [m.nights[d].hours for d in d30 if d in m.nights and m.nights[d].hours]
    needs = [m.nights[d].need_min for d in d30 if d in m.nights and m.nights[d].need_min]
    need = statistics.fmean(needs) if needs else None      # average, to compare with the average
    beds = [M.bedtime_minutes(m.nights[d].bedtime) for d in d30 if d in m.nights and m.nights[d].bedtime]
    wakes = [n.wake.hour * 60 + n.wake.minute for d in d30 if (n := m.nights.get(d)) and n.wake]
    cons = statistics.fmean([statistics.pstdev(beds), statistics.pstdev(wakes)]) if len(beds) > 1 and len(wakes) > 1 else None
    hc = cfg["health"]
    cons_st = ("none" if cons is None else "good" if cons < hc["consistency_good_min"]
               else "warn" if cons < hc["consistency_warn_min"] else "act")
    gaps = sum(missing)

    if not rhr_3:
        rhr_cell = cell("Last 3 nights", DASH, "", "none",
                        sub=f"last reading {rhr_now:.0f} bpm" if rhr_now else "")
    else:
        # the judged number is the 3-night mean, so that is the one shown
        mean3 = statistics.fmean(rhr_3)
        rise = mean3 - rhr_avg if rhr_avg else 0.0
        last = f"last night {rhr[-1]:.0f} bpm" if rhr[-1] else "no reading last night"
        rhr_cell = cell("Last 3 nights", f"{mean3:.0f} bpm", "", "warn" if rise >= hc["rhr_rise_bpm"] else "good",
                        sub=(f"{rise:+.0f} bpm vs normal \u2014 fatigue or illness? · {last}" if rise >= hc["rhr_rise_bpm"]
                             else f"within your normal · {last}"))
    avg_h = statistics.fmean(hrs) if hrs else None
    if avg_h is None:
        sleep_cell = cell("30-night average", DASH, "", "none")
    elif need:
        short = need / 60 - avg_h
        sleep_cell = cell("30-night average", f"{avg_h:.1f} h", f"/ {need / 60:.1f} h", "warn" if short > hc["sleep_short_h"] else "good",
                          sub=f"{short:.1f} h under your need" if short > hc["sleep_short_h"] else "meets your need")
    else:
        sleep_cell = cell("30-night average", f"{avg_h:.1f} h")
    bed_mean = statistics.fmean(beds) if beds else None
    wake_txt = (f"{int(statistics.fmean(wakes)) // 60:02d}:{int(statistics.fmean(wakes)) % 60:02d}" if wakes else DASH)
    bed_target = (S.bedtime_target_minutes(cfg) - 720) % 1440
    bed_cell = cell("Typical bed / wake", f"{clock_noon(bed_mean)} \u2013 {wake_txt}", "",
                    "none" if bed_mean is None else ("warn" if bed_mean > bed_target else "good"),
                    sub=f"bedtime target \u2264 {cfg['sleep']['bedtime_target']}")

    hv = [m.hrv[d] if d in m.hrv else None for d in d90]
    base = next(((h.base_low, h.base_high) for h in reversed(hv) if h and h.base_low and h.base_high), None)
    hrv_chart = C.day_line(90, [("var(--s2)", [h.last_night if h else None for h in hv], "line"),
                                ("var(--s1)", [h.weekly_avg if h else None for h in hv], "line")],
                           base, xl90, None, width=720, height=170,
                           tips=[f"{d:%a %d %b}: night {h.last_night or 0:.0f} · 7-day {h.weekly_avg or 0:.0f}"
                                 if h else "" for d, h in zip(d90, hv)])
    last_hrv = next((h for h in reversed(hv) if h), None)
    return f"""<section class="card"><h2>Resting heart rate</h2>
<div class="mgrid four">{rhr_cell}{cell("30-day average", f"{rhr_avg:.1f} bpm" if rhr_avg else DASH, sub="your normal")}</div>
<div class="scrollx end">{rhr_chart}</div><div class="note">From overnight sleep data. A sustained rise of 5–7 bpm usually means fatigue or illness.</div></section>
<section class="card"><h2>Sleep stages · 30 nights</h2>
<div class="legend"><span class="lg"><i class="sw deep"></i>Deep</span><span class="lg"><i class="sw light"></i>Light</span><span class="lg"><i class="sw rem"></i>REM</span><span class="lg"><i class="sw awake"></i>Awake</span></div>
<div class="scrollx end">{stages}</div>
<div class="mgrid four">
{sleep_cell}
{cell("Sleep need", f"{need / 60:.1f} h" if need else DASH, sub="Garmin's estimate, 30-night average")}
{bed_cell}
{cell("Consistency", f"±{cons:.0f} min" if cons is not None else DASH, "", cons_st, sub="drift of bed and wake times")}
</div>{f'<div class="note">{gaps} of the last 30 nights have no sleep recording and are left out of these averages.</div>' if gaps else ""}</section>
<section class="card"><h2>HRV against Garmin's baseline · 90 days</h2>
<div class="legend"><span class="lg"><i class="sw s2"></i>overnight</span><span class="lg"><i class="sw s1"></i>7-day average</span><span class="lg"><i class="sw band"></i>Garmin balanced range</span></div>
<div class="scrollx end">{hrv_chart}</div>
<div class="note">{f"Status {escape((last_hrv.status or '').title())} · range {base[0]:.0f}–{base[1]:.0f} ms. " if last_hrv and base else ""}Garmin's status follows the 7-day average, so one low night can sit under the band while the status still reads balanced. The home page judges HRV against your own target band ({cfg["readiness"]["hrv_low"]}–{cfg["readiness"]["hrv_high"]}) instead.</div></section>"""


# --------------------------------------------------------------------------
# page shell
# --------------------------------------------------------------------------
CSS = """
:root{color-scheme:light dark;
 --plane:#f6f6f4;--surface:#fdfdfc;--ink:#121211;--ink2:#52514e;--muted:#87867f;--grid:#e8e7e1;--axis:#c9c8c0;
 --line:rgba(0,0,0,.09);--hl:rgba(0,0,0,.04);
 --s1:#2a78d6;--s2:#eb6834;--s4:#c98500;--good:#0ca30c;--warn:#fab219;--act:#d03b3b;--none:#a9a8a1;
 --t-good:#006300;--t-warn:#8a6100;--t-act:#c22f2f;
 --band:rgba(42,120,214,.13);--deep:#184f95;--light:#2a78d6;--rem:#86b6ef;--awake:#c9c8c0;
 --ph-a:#ecebe6;--ph-b:#e2e1da;--ph-rec:#dce8f6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
 --plane:#0d0d0d;--surface:#1a1a19;--ink:#f3f3f1;--ink2:#c3c2b7;--muted:#8f8e88;--grid:#2b2b29;--axis:#3c3c39;
 --line:rgba(255,255,255,.09);--hl:rgba(255,255,255,.05);
 --s1:#3987e5;--s2:#d95926;--s4:#c98500;--t-good:#33c233;--t-warn:#fab219;--t-act:#f07474;
 --band:rgba(57,135,229,.2);--deep:#1c5cab;--light:#3987e5;--rem:#86b6ef;--awake:#55544f;
 --ph-a:#2a2a28;--ph-b:#343431;--ph-rec:#1d2b3d}}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
 -webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:12px 12px 56px}
a{color:var(--s1)}
.dim{color:var(--muted)}
.t-good{color:var(--t-good)}.t-warn{color:var(--t-warn)}.t-act{color:var(--t-act)}
h2{font-size:13px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}
h2.sec{margin:26px 4px 10px}
h3{font-size:14px;font-weight:620;margin:0 0 6px}
.top{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;padding:6px 4px 12px}
.top .hl{font-size:15px;font-weight:650;flex:1 1 100%}
nav{display:flex;gap:4px}
nav a{color:var(--ink2);text-decoration:none;font-size:13px;padding:5px 10px;border-radius:7px;border:1px solid var(--line)}
nav a.on{background:var(--ink);color:var(--plane);border-color:var(--ink)}
.syncbox{display:flex;align-items:center;gap:8px;margin-left:auto;font-size:12px}
.btn{font:inherit;font-size:12px;font-weight:600;color:var(--ink);background:var(--surface);border:1px solid var(--line);
 border-radius:7px;padding:5px 11px;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.btn:disabled{opacity:.6}
.spin{width:10px;height:10px;border:2px solid var(--axis);border-top-color:var(--ink);border-radius:50%;animation:sp .7s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:10px}
.pill-row{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px}
.pill{font-size:14px;font-weight:600;letter-spacing:.04em;padding:9px 14px;border-radius:10px}
.pill b{font-size:20px;margin-left:6px;letter-spacing:.02em}
.pill.open{background:rgba(12,163,12,.14);color:var(--t-good)}
.pill.closed{background:rgba(208,59,59,.13);color:var(--t-act)}
.pill.nodata{background:var(--grid);color:var(--ink2)}
.reason{font-size:14px;color:var(--ink2);margin:8px 0 12px}
.night{font-size:14px;margin-top:10px;color:var(--ink2)}
.checks{margin-top:8px;font-size:13px}
.checks ul{list-style:none;padding:8px 0 0;margin:8px 0 0;border-top:1px solid var(--line)}
.checks li{padding:3px 0}
.checks li span:first-child{display:inline-block;width:20px;font-weight:700}
.ck-ok span:first-child{color:var(--t-good)}.ck-fail span:first-child{color:var(--t-act)}.ck-nodata span:first-child{color:var(--muted)}
.mgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.m{border:1px solid var(--line);border-radius:10px;padding:9px 10px;min-width:0}
.m-l{font-size:11.5px;color:var(--muted);display:flex;align-items:center;gap:6px}
.m-v{font-size:21px;font-weight:650;letter-spacing:-.01em;margin-top:2px;font-variant-numeric:tabular-nums}
.m-t{font-size:13px;font-weight:500;color:var(--muted);margin-left:5px}
.m-d{font-size:13px;color:var(--muted);margin-left:5px}
.m-s{font-size:11.5px;color:var(--ink2);margin-top:2px}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none;background:var(--axis)}
.st-good .dot,.dot.st-good{background:var(--good)}.st-warn .dot,.dot.st-warn{background:var(--warn)}
.st-act .dot,.dot.st-act{background:var(--act)}.st-none .dot,.dot.st-none{background:var(--none)}
.st-warn>.m-v,.st-warn .hero-v{color:var(--t-warn)}.st-act>.m-v,.st-act .hero-v{color:var(--t-act)}
.st-none>.m-v{color:var(--muted)}
.m .m-v.split{color:var(--ink);display:flex;flex-wrap:wrap;gap:2px 14px}
.pt{display:inline-flex;align-items:baseline}
.pt i{font-style:normal;font-size:13px;font-weight:700;margin-left:5px}
.pt.ok i{color:var(--t-good)}
.pt.bad{color:var(--t-act)}.pt.bad .m-t{color:var(--t-act);opacity:.8}
.pt.unk{color:var(--muted)}
.wk-head{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.wk-head h2{margin:0}
.grp{margin-top:12px}
.grp-h{font-size:11.5px;font-weight:650;letter-spacing:.07em;text-transform:uppercase;color:var(--ink2);
 margin:0 0 7px;padding-bottom:5px;border-bottom:1px solid var(--line)}
.wk-groups{display:grid;grid-template-columns:minmax(0,1fr);gap:0 14px}
.mprog{height:5px;border-radius:3px;background:var(--grid);margin-top:6px;overflow:hidden}
.mprog i{display:block;height:100%;border-radius:3px;background:var(--s1)}
.st-good .mprog i{background:var(--good)}.st-warn .mprog i{background:var(--warn)}.st-act .mprog i{background:var(--act)}
.st-legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:11.5px;color:var(--muted);margin-top:12px}
.st-legend span{display:inline-flex;align-items:center;gap:5px}
.st-legend .g{background:var(--good)}.st-legend .w{background:var(--warn)}.st-legend .a{background:var(--act)}.st-legend .n{background:var(--none)}
.st-legend .bar{width:14px;height:5px;border-radius:3px;background:var(--s1);display:inline-block}
.nodot{width:8px;height:8px;display:inline-block}
.hero{margin-bottom:12px}
.hero .k{font-size:12px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;color:var(--ink2)}
.hero-v{font-size:46px;font-weight:700;letter-spacing:-.02em;line-height:1.05;margin:4px 0 6px;font-variant-numeric:tabular-nums}
.hero-v .m-t{font-size:20px}.hero-v .m-d{font-size:20px}
.hero-s{font-size:14px;margin-top:6px}
.tag{font-size:11px;font-weight:500;letter-spacing:0;text-transform:none;color:var(--muted);
 border:1px solid var(--line);border-radius:6px;padding:1px 6px;margin-left:6px}
.herobar{position:relative;height:12px;border-radius:6px;background:var(--grid);overflow:hidden}
.hb-band{position:absolute;top:0;bottom:0;background:var(--band);box-shadow:inset 0 0 0 1px var(--s1)}
.hb-in{position:absolute;left:0;top:0;bottom:0;background:var(--s1)}
.hb-over{position:absolute;top:0;bottom:0;background:var(--act)}
.runrow{margin-bottom:10px}
.runrow .m-v{font-size:24px}
.prog{height:8px;border-radius:4px;background:var(--grid);overflow:hidden;margin-top:5px}
.prog div{height:100%;background:var(--s1);border-radius:4px}
.alerts summary{display:flex;align-items:center;gap:6px 14px;flex-wrap:wrap;cursor:pointer;list-style:none}
.alerts summary::-webkit-details-marker{display:none}
.alerts summary h2{margin:0}
.al-n{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;color:var(--ink2)}
.al-n .dot{margin:0}
.chev{margin-left:auto;width:9px;height:9px;border-right:2px solid var(--muted);border-bottom:2px solid var(--muted);
 transform:rotate(-45deg);transition:transform .15s}
.alerts[open] .chev{transform:rotate(45deg)}
.alerts[open] summary{margin-bottom:6px}
.alerts ul{list-style:none;margin:0;padding:0}
.alerts li{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-top:1px solid var(--line)}
.alerts li:first-child{border-top:0}
.alerts .dot{margin-top:7px}
.al-act .dot{background:var(--act)}.al-warn .dot{background:var(--warn)}
.al-w{font-weight:600;font-size:14px}.al-a{font-size:13px;color:var(--ink2)}
.al-b{flex:1;min-width:0}
.al-v{flex:none;width:28px;height:28px;border-radius:7px;border:1px solid var(--line);background:var(--surface);
 color:var(--muted);font:inherit;font-size:13px;font-weight:700;cursor:pointer}
.al-v:hover{color:var(--ink)}
.alerts li.seen{opacity:.45}.alerts li.seen .al-v{background:var(--good);border-color:var(--good);color:#fff}
.al-seen{color:var(--muted);font-weight:500}
.cnotes{white-space:pre-wrap;font-size:12px;background:var(--plane);border:1px solid var(--line);border-radius:8px;padding:8px;max-height:220px;overflow:auto}
.sess{border-top:1px solid var(--line)}
.sess:first-of-type{border-top:0}
.sess summary{display:grid;grid-template-columns:36px minmax(0,1fr) auto 14px;gap:8px;align-items:center;
 padding:10px 0;cursor:pointer;list-style:none;font-size:14px}
.sess summary::-webkit-details-marker{display:none}
.sd{color:var(--muted);font-size:13px}.stt{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sf{color:var(--ink2);font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}
.sdet{padding:0 0 10px 44px;font-size:13px;color:var(--ink2)}
.sdet p{margin:4px 0}
table.reps{border-collapse:collapse;margin:4px 0 6px;font-size:13px}
table.reps th{font-weight:600;color:var(--muted);font-size:11px;text-align:left;padding:2px 12px 4px 0}
table.reps td{padding:3px 12px 3px 0;border-top:1px solid var(--line)}
.num{text-align:right;font-variant-numeric:tabular-nums}
.tgrid{display:grid;grid-template-columns:minmax(0,1fr);gap:10px}
.panel{margin:0}
.legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:11.5px;color:var(--ink2);margin:0 0 6px}
.lg{display:inline-flex;align-items:center;gap:5px}
.sw{width:10px;height:10px;border-radius:3px;display:inline-block}
.sw.s1{background:var(--s1)}.sw.s2{background:var(--s2)}.sw.sact{background:var(--act)}.sw.band{background:var(--band)}
.sw.grey{background:var(--none);border-radius:50%}.sw.warn{background:var(--warn);border-radius:50%}
.sw.fit{height:2px;background:var(--ink2)}.sw.target{height:2px;background:var(--ink2)}.sw.faint{height:2px;background:var(--ink2);opacity:.55}
.sw.deep{background:var(--deep)}.sw.light{background:var(--light)}.sw.rem{background:var(--rem)}.sw.awake{background:var(--awake)}
.note{font-size:12px;color:var(--muted);margin-top:6px}
.empty{font-size:13px;padding:24px 0}
.chart{width:100%;height:auto;display:block;overflow:visible}
.grid{stroke:var(--grid);stroke-width:1}.axis{stroke:var(--axis);stroke-width:1}
.tick{fill:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}.end{text-anchor:end}.mid{text-anchor:middle}
.band{fill:var(--band)}.hl{fill:var(--hl)}
.target{stroke:var(--ink2);stroke-width:2;stroke-dasharray:3 3}
.bar.s1{fill:var(--s1)}.bar.sact{fill:var(--act)}
.bar.deep{fill:var(--deep)}.bar.light{fill:var(--light)}.bar.rem{fill:var(--rem)}.bar.awake{fill:var(--awake)}
.nodata{fill:var(--axis)}
.line{fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.faint{fill:none;stroke-width:1.5;opacity:.55}
.enddot{stroke:var(--surface);stroke-width:2}
.dot.dok{fill:var(--s1)}.dot.dgrey{fill:var(--none)}.dot.dwarn{fill:var(--warn)}
.fit{stroke:var(--ink2);stroke-width:1.5;stroke-dasharray:5 4}
.ph-a{fill:var(--ph-a)}.ph-b{fill:var(--ph-b)}.ph-rec{fill:var(--ph-rec)}
.ph-l{fill:var(--ink2);font-size:11px;text-anchor:middle;pointer-events:none}
.ph-div{stroke:var(--axis);stroke-width:1;stroke-dasharray:2 3}
.planbar{fill:none;stroke:var(--ink2);stroke-width:1.5}
.sw.plan{background:transparent;box-shadow:inset 0 0 0 1.5px var(--ink2)}.sw.rec{background:var(--ph-rec)}
.scrollx{overflow-x:auto;-webkit-overflow-scrolling:touch}
.badge{background:var(--warn);color:#121211;border-radius:9px;font-size:11px;padding:0 6px;line-height:16px}
dialog.coach{border:1px solid var(--line);border-radius:14px;background:var(--surface);color:var(--ink);
 padding:16px 18px;width:min(640px,calc(100vw - 32px));max-height:calc(100vh - 48px);overflow:auto}
dialog.coach::backdrop{background:rgba(0,0,0,.45)}
.coach h2{margin:0 0 4px}.coach h3{margin:14px 0 6px}
.coach p{font-size:13px;color:var(--ink2);margin:4px 0 8px}
.coach textarea{width:100%;font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink);
 background:var(--plane);border:1px solid var(--line);border-radius:8px;padding:8px;resize:vertical}
.coach .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:8px}
.coach ul{margin:6px 0;padding-left:18px;font-size:13px}
.coach .x{position:sticky;top:0;float:right}
.coach .needs{font-size:13px;margin:6px 0}
.coach .needs li.miss{color:var(--t-warn)}
.race-name{font-size:24px;font-weight:700;letter-spacing:-.01em;margin:4px 0 2px}
.races{list-style:none;margin:10px 0 0;padding:0}
.race{display:grid;grid-template-columns:62px minmax(0,1fr);gap:2px 10px;padding:8px 0;border-top:1px solid var(--line);font-size:14px}
.race .rx{grid-column:2;font-size:13px;color:var(--ink2)}
.race.a .rn{font-weight:700}.race.past{opacity:.7}
.rd{color:var(--muted);font-variant-numeric:tabular-nums}
code{font-size:13px}
#tip{position:fixed;z-index:50;pointer-events:none;opacity:0;transition:opacity .1s;background:var(--ink);color:var(--plane);
 padding:6px 9px;border-radius:7px;font-size:12px;max-width:260px}
@media (max-width:759px){.scrollx svg{min-width:600px}}
@media (min-width:760px){
 .wrap{padding:20px 20px 64px}
 .top .hl{flex:0 1 auto;font-size:16px}
 .mgrid{grid-template-columns:repeat(4,minmax(0,1fr))}
 .mgrid.three{grid-template-columns:repeat(3,minmax(0,1fr))}
 .mgrid.two{grid-template-columns:repeat(2,minmax(0,1fr))}
 .mgrid.one{grid-template-columns:minmax(0,1fr)}
 .wk-groups{grid-template-columns:2fr 1fr 1fr}
 .tgrid{grid-template-columns:repeat(2,minmax(0,1fr))}
 .hero-v{font-size:56px}
 .card{padding:16px 18px}
}
"""

JS = """
(function(){
  var tip=document.getElementById('tip');
  function show(t,x,y){tip.innerHTML=t;tip.style.opacity='1';
    var w=tip.offsetWidth,h=tip.offsetHeight;
    tip.style.left=Math.max(6,Math.min(x+12,window.innerWidth-w-6))+'px';
    tip.style.top=(y-h-12<6?y+16:y-h-12)+'px';}
  document.addEventListener('mousemove',function(e){var t=e.target.closest&&e.target.closest('[data-tip]');
    if(t)show(t.getAttribute('data-tip'),e.clientX,e.clientY);else tip.style.opacity='0';});
  document.addEventListener('click',function(e){var t=e.target.closest&&e.target.closest('[data-tip]');
    if(t)show(t.getAttribute('data-tip'),e.clientX,e.clientY);else tip.style.opacity='0';});
})();
(function(){
  var dlg=document.getElementById('coachdlg'),btn=document.getElementById('coachbtn');
  if(!dlg||!btn)return;
  var data=JSON.parse(document.getElementById('coachdata').textContent);
  var served=location.protocol==='http:'||location.protocol==='https:';
  var ans=document.getElementById('coachans'),out=document.getElementById('coachout'),
      chk=document.getElementById('coachchk'),app=document.getElementById('coachapply');
  document.getElementById('coachprompt').value=data.prompt;
  if(!served)document.getElementById('coachpaste').hidden=true;
  function open(){ if(typeof dlg.showModal==='function')dlg.showModal(); else dlg.setAttribute('open',''); }
  btn.addEventListener('click',open);
  document.getElementById('coachclose').addEventListener('click',function(){dlg.close();});
  // pop up by itself once a day while this or next week is missing a target
  var key='coachSeen',stamp=data.today+'|'+data.signature;
  try{ if(data.urgent&&localStorage.getItem(key)!==stamp){ open(); localStorage.setItem(key,stamp); } }catch(e){}
  document.getElementById('coachcopy').addEventListener('click',function(){
    var t=document.getElementById('coachprompt'),b=this;
    function done(){b.textContent='Copied';setTimeout(function(){b.textContent='Copy prompt';},1500);}
    if(navigator.clipboard){navigator.clipboard.writeText(t.value).then(done,function(){t.select();document.execCommand('copy');done();});}
    else{t.select();document.execCommand('copy');done();}
  });
  function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
  function show(r,applied){
    var h='';
    if(!r.ok)h+='<p class="t-act">'+esc(r.error||'failed')+'</p>';
    else if(!r.changes.length)h+='<p>Nothing to change: the dashboard already has these values.</p>';
    else h+='<p>'+(applied?'Saved:':'This will change:')+'</p><ul>'+r.changes.map(function(c){return '<li>'+esc(c)+'</li>';}).join('')+'</ul>';
    if(r.warnings&&r.warnings.length)h+='<p class="t-warn">Not taken over:</p><ul>'+r.warnings.map(function(c){return '<li>'+esc(c)+'</li>';}).join('')+'</ul>';
    if(r.notes)h+='<p>Notes from the coach'+(applied?' (saved to private/notes/coach/)':'')+':</p><pre class="cnotes">'+esc(r.notes)+'</pre>';
    out.innerHTML=h;
  }
  function post(mode){
    return fetch('/api/coach/'+mode,{method:'POST',headers:{'X-Sync':'1','Content-Type':'application/json'},
      body:JSON.stringify({text:ans.value})}).then(function(r){if(!r.ok)throw new Error(r.status);return r.json();});
  }
  ans.addEventListener('input',function(){app.disabled=true;});
  chk.addEventListener('click',function(){
    chk.disabled=true;out.innerHTML='<p>Checking\u2026</p>';
    post('preview').then(function(r){show(r,false);app.disabled=!(r.ok&&r.changes.length);})
      .catch(function(e){out.innerHTML='<p class="t-act">Could not reach serve.py ('+esc(String(e.message))+')</p>';})
      .then(function(){chk.disabled=false;});
  });
  app.addEventListener('click',function(){
    app.disabled=true;chk.disabled=true;out.innerHTML='<p>Saving and rebuilding\u2026</p>';
    post('apply').then(function(r){show(r,true);if(r.ok&&r.changes.length){setTimeout(function(){location.reload();},1800);}})
      .catch(function(e){out.innerHTML='<p class="t-act">Could not reach serve.py ('+esc(String(e.message))+')</p>';})
      .then(function(){chk.disabled=false;});
  });
})();
document.querySelectorAll('.scrollx.end').forEach(function(e){e.scrollLeft=e.scrollWidth;});  // newest data in view
(function(){
  // alerts marked as viewed: kept per browser, keyed by what the alert says
  var d=document.getElementById('alerts'); if(!d)return;
  var KEY='alertsViewed',seen={};
  try{seen=JSON.parse(localStorage.getItem(KEY)||'{}')||{};}catch(e){}
  var list=d.querySelector('ul'),items=[].slice.call(d.querySelectorAll('li[data-id]'));
  var live={};items.forEach(function(li){live[li.getAttribute('data-id')]=1;});
  Object.keys(seen).forEach(function(k){if(!live[k])delete seen[k];});     // forget alerts that are gone
  function store(){try{localStorage.setItem(KEY,JSON.stringify(seen));}catch(e){}}
  function render(){
    var n={act:0,warn:0},v=0;
    items.forEach(function(li){
      var on=!!seen[li.getAttribute('data-id')];
      li.classList.toggle('seen',on);
      li.querySelector('.al-v').setAttribute('aria-pressed',on?'true':'false');
      li.querySelector('.al-v').title=on?'Viewed - click to mark as new':'Mark as viewed';
      if(on)v++;else n[li.classList.contains('al-act')?'act':'warn']++;
    });
    // unviewed first, viewed below, each in the original order
    function isSeen(li){return !!seen[li.getAttribute('data-id')];}
    items.filter(function(li){return !isSeen(li);}).concat(items.filter(isSeen))
      .forEach(function(li){list.appendChild(li);});
    d.querySelectorAll('.al-n[data-lvl]').forEach(function(c){var k=c.getAttribute('data-lvl');c.querySelector('b').textContent=n[k];c.hidden=!n[k];});
    var s=d.querySelector('.al-seen');s.querySelector('b').textContent=v;s.hidden=!v;
  }
  items.forEach(function(li){li.querySelector('.al-v').addEventListener('click',function(e){
    e.preventDefault();var id=li.getAttribute('data-id');
    if(seen[id])delete seen[id];else seen[id]=new Date().toISOString().slice(0,10);
    store();render();});});
  store();render();
  try{ if(localStorage.getItem('alertsOpen')==='1') d.open=true; }catch(e){}
  function save(){ try{ localStorage.setItem('alertsOpen', d.open?'1':'0'); }catch(e){} }
  d.addEventListener('toggle',save);
  window.addEventListener('pagehide',save);   // the page reloads itself after every sync
})();
(function(){
  if(location.protocol!=='http:'&&location.protocol!=='https:')return;
  var AUTO=parseFloat(document.body.getAttribute('data-auto-sync')||'0');
  var btn=document.getElementById('syncbtn'),at=document.getElementById('syncedat');
  btn.hidden=false;
  function busy(on){btn.disabled=on;btn.innerHTML=on?'<span class="spin"></span>Syncing\\u2026':'Sync';}
  function fail(m){busy(false);at.className='t-act';at.textContent='Sync failed: '+m;}
  function poll(){fetch('/api/status').then(function(r){return r.json();}).then(function(s){
    if(s.running){setTimeout(poll,1500);return;} if(s.error){fail(s.error);return;} location.reload();
  }).catch(function(){fail('lost contact with serve.py');});}
  function sync(){busy(true);fetch('/api/sync',{method:'POST',headers:{'X-Sync':'1'}})
    .then(function(r){if(!r.ok)throw new Error(r.status);setTimeout(poll,1500);})
    .catch(function(){fail('could not reach serve.py');});}
  btn.addEventListener('click',sync);
  fetch('/api/status').then(function(r){return r.json();}).then(function(s){
    if(s.running){busy(true);setTimeout(poll,1500);}
    else if(AUTO>0&&s.age_seconds!==null&&s.age_seconds>AUTO*3600){sync();}
  }).catch(function(){});
})();
"""


def coach_dialog(nd: coach.Needs, prompt_text: str) -> tuple[str, str]:
    """The Coach button and its popup: the prompt to send, and a box for the answer."""
    n = nd.open_items
    badge = f' <span class="badge" title="{n} values missing">{n}</span>' if n else ""
    button = f'<button id="coachbtn" class="btn" title="Update the plan from your coach">Coach{badge}</button>'
    rows = "".join(
        f'<li class="{"miss" if w.missing else ""}">Wk{w.n} · {w.start:%d %b}: '
        + (f"missing {' and '.join(w.missing)}" if w.missing else "complete") + "</li>"
        for w in nd.weeks)
    if nd.ftp_missing:
        rows += '<li class="miss">Bike FTP: missing</li>'
    signature = ";".join(f"{w.n}:{','.join(w.missing)}" for w in nd.weeks) + (";ftp" if nd.ftp_missing else "")
    data = json.dumps({"prompt": prompt_text, "urgent": nd.urgent, "today": nd.today.isoformat(),
                       "signature": signature}).replace("</", "<\\/")
    dialog = f"""<dialog id="coachdlg" class="coach">
<button class="btn x" id="coachclose" aria-label="Close">Close</button>
<h2>Coach update</h2>
<p>Garmin can't tell the dashboard your plan. These targets come from your coach:</p>
<ul class="needs">{rows}</ul>
<h3>1 · Send this to your coach</h3>
<textarea id="coachprompt" rows="9" readonly></textarea>
<div class="row"><button class="btn" id="coachcopy">Copy prompt</button></div>
<div id="coachpaste">
<h3>2 · Paste the coach's answer</h3>
<p>Paste the whole reply. The TOML block updates weekly targets, session verdicts, caps, tolerances, races and the FTP; the coach's notes around it are shown and saved with the update.</p>
<textarea id="coachans" rows="8" placeholder="Paste the reply here"></textarea>
<div class="row"><button class="btn" id="coachchk">Check</button><button class="btn" id="coachapply" disabled>Apply</button></div>
<div id="coachout"></div>
</div>
</dialog><script type="application/json" id="coachdata">{data}</script>"""
    return button, dialog


def shell(title: str, active: str, header: str, updated: str, body: str, auto_sync: float,
          coach_ui: tuple[str, str] = ("", "")) -> str:
    nav = "".join(f'<a href="{href}" class="{"on" if key == active else ""}">{name}</a>'
                  for key, name, href in NAV)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head>
<body data-auto-sync="{auto_sync:g}"><div class="wrap">
<header class="top"><div class="hl">{header}</div><nav>{nav}</nav>
<div class="syncbox"><span class="dim ver" title="Dashboard version - see CHANGELOG.md">v{version()}</span>
<span id="syncedat" class="dim">updated {updated}</span>
{coach_ui[0]}<button id="syncbtn" class="btn" hidden>Sync</button></div></header>
{body}
</div>{coach_ui[1]}<div id="tip"></div><script>{JS}</script></body></html>
"""


def header_line(season: S.Season, now: datetime) -> str:
    today = now.date()
    wk = M.monday(today)
    n = season.week_number(wk)
    parts = [f"Wk{n}" if n else f"Week of {wk:%d %b}"]
    phase = season.targets(wk)["phase"]
    if phase:
        parts.append(f"{escape(phase)} phase")
    race = season.next_race(today, a_only=True) or season.next_race(today)
    if race:
        parts.append(f"{escape(race.short)} in {countdown((race.date - today).days)}")
    return " · ".join(parts)


def build(cache: Path, out: Path, config: Path | None = None,
          now: datetime | None = None) -> list[Path]:
    now = now or datetime.now()
    cfg = S.load_athlete(config)
    season = S.load_season(config)
    m = load_model(cache)
    auto = float(cfg["app"]["auto_sync_hours"])
    if m.synced_at:
        updated = f"{m.synced_at:%H:%M}" if m.synced_at.date() == now.date() else f"{m.synced_at:%d %b %H:%M}"
    else:
        updated = "never"
    head = header_line(season, now)
    cu = coach_dialog(coach.needs(season, cfg, now.date(), cfg["coach"]["ahead_weeks"]),
                      coach.prompt(season, cfg, now.date(), m))
    pages = {
        "dashboard.html": shell("Training", "home", head, updated, home(m, cfg, season, now), auto, cu),
        "season.html": shell("Season", "season", head, updated, season_page(m, cfg, season, now), auto, cu),
        "health.html": shell("Health", "health", head, updated, health_page(m, cfg, now), auto, cu),
    }
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, html in pages.items():
        (out / name).write_text(html)
        written.append(out / name)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the dashboard pages.")
    ap.add_argument("--cache", type=Path, default=ROOT / "data" / "cache")
    ap.add_argument("--out", type=Path, default=ROOT)
    args = ap.parse_args()
    for p in build(args.cache, args.out):
        print(f"Built {p.name:16s} {p.stat().st_size / 1024:5.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
