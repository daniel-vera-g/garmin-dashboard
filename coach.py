#!/usr/bin/env python3
"""The coach loop: what the dashboard needs from the coach, and how the answer
gets back in.

Garmin can't tell the dashboard the plan - weekly run-km targets, threshold-km
bands, the bike FTP from a test. This module finds what is missing for the
coming weeks, writes a prompt for the coaching session, and merges the coach's
answer into the gitignored private/config.toml.

The answer is parsed strictly: one TOML block, only known keys, values range-
checked. Anything else is reported back, never guessed. Files are edited as
text, so comments and layout survive, and a backup is written before each
change.

    coach.py prompt            print the prompt
    coach.py preview < answer  what would change (JSON)
    coach.py apply   < answer  change the files (JSON)
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
AHEAD_WEEKS = S.DEFAULTS["coach"]["ahead_weeks"]    # the current week plus this many
MAX_ANSWER = 64 * 1024

WEEK_KEYS = {"n", "run_km", "threshold", "recovery"}


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


def _fmt_band(b: tuple[float, float] | None) -> str:
    if b is None:
        return "MISSING"
    return f"{b[0]:g}" if b[0] == b[1] else f"{b[0]:g}–{b[1]:g}"


def prompt(season: S.Season, cfg: dict, today: date) -> str:
    nd = needs(season, cfg, today, cfg["coach"]["ahead_weeks"])
    if not season.week1_start or not nd.weeks:
        return ("I need the season plan for my training dashboard. Give me week1_start (the Monday "
                "of Wk1) and, per week, the run-km target and the threshold-km band, as one ```toml "
                "block of [[week]] entries with n, run_km and threshold = [low, high].")
    rows = "\n".join(
        f"Wk{w.n} · Mon {w.start.day} {w.start:%b} · run km {w.run_km:g} · threshold km {_fmt_band(w.threshold)}"
        if w.run_km is not None else
        f"Wk{w.n} · Mon {w.start.day} {w.start:%b} · run km MISSING · threshold km {_fmt_band(w.threshold)}"
        for w in nd.weeks)
    ftp = cfg["bike"].get("ftp_w")
    first, last = nd.weeks[0].n, nd.weeks[-1].n
    return f"""Update for my training dashboard. Today is {today:%a %d %b %Y}, Wk{nd.current}.
Week numbering: Wk1 = the week starting Monday {season.week1_start:%d %b %Y}.

These are the dashboard's targets for Wk{first}–Wk{last}. Confirm, correct or fill them in from the current plan, including any changes we made since. Where the plan doesn't decide a value, write MISSING. Don't invent one.

{rows}
Bike FTP: {f"{ftp} W" if ftp else "MISSING"} (my own test result only, never Garmin's estimate)

Threshold km = sub-threshold rep distance only, at or under my HR cap. Give it as [low, high], as [n, n] for a single planned figure, or [0, 0] for a week without sub-T.

Answer with exactly one ```toml block, one [[week]] entry per week, in this format:

[[week]]
n = {first}
run_km = 60
threshold = [12, 16]

[bike]
ftp_w = 250

Leave out any key you would write MISSING, and leave out [bike] if the FTP is unknown. After the block, list FLAGS (uncertain or inferred values) and MISSING."""


# --------------------------------------------------------------------------
# reading the answer
# --------------------------------------------------------------------------
@dataclass
class Update:
    weeks: dict[int, dict[str, Any]] = field(default_factory=dict)
    ftp_w: int | None = None
    warnings: list[str] = field(default_factory=list)


def _number(v: Any, lo: float, hi: float) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if lo <= v <= hi else None


def parse(text: str) -> Update:
    """The coach's answer -> validated changes. Raises ValueError when there is
    nothing usable in it."""
    if len(text) > MAX_ANSWER:
        raise ValueError("answer is too long")
    blocks = re.findall(r"```(?:toml)?[ \t]*\n(.*?)```", text, re.S)
    if len(blocks) > 1:
        raise ValueError("found more than one ``` block - paste one answer at a time")
    body = blocks[0] if blocks else text
    try:
        raw = tomllib.loads(body)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"the TOML block doesn't parse: {exc}") from None

    up = Update()
    for key in sorted(set(raw) - {"week", "bike"}):
        up.warnings.append(f"ignored '{key}': the dashboard only takes [[week]] and [bike] ftp_w here")
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
    bike = raw.get("bike")
    if isinstance(bike, dict):
        for key in sorted(set(bike) - {"ftp_w"}):
            up.warnings.append(f"[bike]: ignored '{key}'")
        if "ftp_w" in bike:
            v = _number(bike["ftp_w"], 50, 600)
            if v is None:
                up.warnings.append(f"ftp_w {bike['ftp_w']!r} is not a wattage between 50 and 600")
            else:
                up.ftp_w = int(round(v))
    if not up.weeks and up.ftp_w is None:
        raise ValueError("no usable values found - the answer needs a ```toml block with [[week]] "
                         "entries (n plus run_km and/or threshold) or [bike] ftp_w"
                         + (f" ({'; '.join(up.warnings)})" if up.warnings else ""))
    return up


# --------------------------------------------------------------------------
# writing it back
# --------------------------------------------------------------------------
def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, tuple):
        return f"[{v[0]:g}, {v[1]:g}]"
    return f"{v:g}" if isinstance(v, float) else str(v)


_BLOCK = re.compile(r"^\[\[week\]\][^\n]*\n(?:(?!\[)[^\n]*\n?)*", re.M)


def _set_key(block: str, key: str, line: str) -> str:
    pat = re.compile(rf"^{key}\s*=.*$", re.M)
    if pat.search(block):
        return pat.sub(line, block, count=1)
    m = re.search(r"^n\s*=.*\n?", block, re.M)
    end = m.end() if m else len(block)
    sep = "" if block[:end].endswith("\n") else "\n"
    return block[:end] + sep + line + "\n" + block[end:]


def merge_season(text: str, weeks: dict[int, dict[str, Any]], week1: date | None,
                 stamp: str) -> tuple[str, list[str]]:
    """Set the given keys in the [[week]] blocks of the config (as text), adding
    blocks for weeks that don't have one. Returns the new text and the changes."""
    changes: list[str] = []
    blocks = {int(m.group(1)): b for b in _BLOCK.finditer(text)
              if (m := re.search(r"^n\s*=\s*(\d+)", b.group(0), re.M))}
    for n in sorted(weeks):
        row = weeks[n]
        if n in blocks:
            span = blocks[n]
            block = span.group(0)
            current = tomllib.loads(block).get("week", [{}])[0]
            new = block
            for key, v in row.items():
                old = current.get(key)
                old_cmp = tuple(old) if isinstance(old, list) else old
                if old_cmp == v or (isinstance(old, (int, float)) and not isinstance(old, bool)
                                    and isinstance(v, float) and float(old) == v):
                    continue
                new = _set_key(new, key, f"{key} = {_toml_value(v)}  # coach {stamp}")
                changes.append(f"Wk{n} {key}: {_toml_value(old_cmp) if old is not None else '—'} → {_toml_value(v)}")
            if new != block:
                text = text.replace(block, new, 1)
                blocks = {int(m.group(1)): b for b in _BLOCK.finditer(text)
                          if (m := re.search(r"^n\s*=\s*(\d+)", b.group(0), re.M))}
        else:
            ws = week1 + timedelta(weeks=n - 1) if week1 else None
            label = f"  # Mon {ws.day} {ws:%b}" if ws else ""
            lines = [f"[[week]]{label}", f"n = {n}"]
            lines += [f"{k} = {_toml_value(v)}  # coach {stamp}" for k, v in row.items()]
            new_block = "\n".join(lines) + "\n"
            before = [k for k in blocks if k < n]
            if before:
                anchor = blocks[max(before)]
                at = anchor.end()
                prefix = "" if text[:at].endswith("\n\n") else ("\n" if text[:at].endswith("\n") else "\n\n")
                text = text[:at] + prefix + new_block + ("\n" if not text[at:].startswith("\n") else "") + text[at:]
            else:
                text = text.rstrip("\n") + "\n\n" + new_block
            changes += [f"Wk{n} {k}: — → {_toml_value(v)}" for k, v in row.items()]
            blocks = {int(m.group(1)): b for b in _BLOCK.finditer(text)
                      if (m := re.search(r"^n\s*=\s*(\d+)", b.group(0), re.M))}
    return text, changes


def merge_athlete(text: str, ftp_w: int, stamp: str) -> tuple[str, list[str]]:
    current = tomllib.loads(text).get("bike", {}).get("ftp_w") if text.strip() else None
    if current == ftp_w:
        return text, []
    line = f"ftp_w = {ftp_w}  # coach {stamp}"
    sec = re.search(r"^\[bike\][^\n]*\n(?:(?!\[)[^\n]*\n?)*", text, re.M)
    if sec:
        block = sec.group(0)
        pat = re.compile(r"^ftp_w\s*=.*$", re.M)
        new = pat.sub(line, block, count=1) if pat.search(block) else \
            block[:block.index("\n") + 1] + line + "\n" + block[block.index("\n") + 1:]
        text = text[:sec.start()] + new + text[sec.end():]
    else:
        text = text.rstrip("\n") + f"\n\n[bike]\n{line}\n"
    return text, [f"bike FTP: {current if current else '—'} → {ftp_w} W"]


def plan(up: Update, config: Path, stamp: str) -> tuple[dict[Path, str], list[str]]:
    """New file contents and the list of changes, without writing anything."""
    if not config.exists():
        raise ValueError(f"{config.name} doesn't exist yet - copy config.example.toml to private/config.toml first")
    old = text = config.read_text()
    changes: list[str] = []
    if up.weeks:
        text, ch = merge_season(text, up.weeks, S.load_season(config).week1_start, stamp)
        changes += ch
    if up.ftp_w is not None:
        text, ch = merge_athlete(text, up.ftp_w, stamp)
        changes += ch
    tomllib.loads(text)                          # never write a file that doesn't parse
    return ({config: text} if text != old else {}), changes


def apply(up: Update, config: Path, now: datetime | None = None,
          backups: Path = BACKUPS) -> list[str]:
    now = now or datetime.now()
    files, changes = plan(up, config, f"{now:%d %b %Y}")
    for p, text in files.items():
        if p.exists():
            backups.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, backups / f"{p.stem}-{now:%Y%m%d-%H%M%S}{p.suffix}")
        tmp = p.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(p)
    return changes


# --------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "prompt"
    config = S.config_path()
    if mode == "prompt":
        print(prompt(S.load_season(config), S.load_athlete(config), date.today()))
        return 0
    if mode not in ("preview", "apply"):
        print(__doc__)
        return 2
    try:
        up = parse(sys.stdin.read())
        if mode == "preview":
            _, changes = plan(up, config, f"{datetime.now():%d %b %Y}")
        else:
            changes = apply(up, config)
        print(json.dumps({"ok": True, "changes": changes, "warnings": up.warnings}))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "changes": [], "warnings": []}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
