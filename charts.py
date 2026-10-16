"""Inline-SVG chart primitives. No library, no dependencies.

Conventions: recessive gridlines, muted tick labels, one y-axis per chart (two
measures get two stacked panels, never a second axis), 2 px lines, rounded bar
tops on the baseline, hover tooltips through data-tip. Colours come from CSS
custom properties so light and dark mode are handled by the stylesheet.
"""
from __future__ import annotations

import math
from html import escape
from typing import Callable, Sequence

W = 480        # small-multiple width: labels stay legible when a phone scales it down
PAD_L, PAD_R, PAD_T, PAD_B = 40, 6, 10, 20


class Plot:
    """Maps data to pixels for one panel. x is either slots (weekly bars) or a
    continuous range (dates as day numbers)."""

    def __init__(self, height: int, y0: float, y1: float,
                 slots: int | None = None, x0: float = 0.0, x1: float = 1.0, width: int = W,
                 pad_t: int = PAD_T):
        self.h = height
        self.w = width
        self.pt = pad_t
        self.pw = width - PAD_L - PAD_R
        self.ph = height - pad_t - PAD_B
        self.y0, self.y1 = y0, (y1 if y1 != y0 else y0 + 1)
        self.slots = slots
        self.x0, self.x1 = x0, (x1 if x1 != x0 else x0 + 1)

    def y(self, v: float) -> float:
        return self.pt + self.ph - (v - self.y0) / (self.y1 - self.y0) * self.ph

    def slot(self, i: int) -> tuple[float, float]:
        """Centre and width of slot i."""
        w = self.pw / (self.slots or 1)
        return PAD_L + w * i + w / 2, w

    def x(self, v: float) -> float:
        return PAD_L + (v - self.x0) / (self.x1 - self.x0) * self.pw


def nice_max(hi: float, n: int = 4, integer: bool = False) -> tuple[float, float]:
    if hi <= 0:
        return (float(n), 1.0) if integer else (1.0, 1.0 / n)
    raw = hi / n
    base = 10.0 ** math.floor(math.log10(raw))
    step = next((base * m for m in (1, 2, 2.5, 5, 10) if base * m >= raw - 1e-12), base * 10)
    if integer:
        step = max(1.0, math.ceil(step))
    return step * n, step


def nice_ticks(lo: float, hi: float, n: int = 3,
               steps: Sequence[float] | None = None) -> tuple[float, float, list[float]]:
    """Round tick positions covering [lo, hi]: returns widened lo, hi and the ticks.
    `steps` lists acceptable step sizes (e.g. 5/10/15/30 s for paces)."""
    span = max(hi - lo, 1e-9)
    if steps is None:
        base = 10.0 ** math.floor(math.log10(span / n))
        steps = [base * m for m in (1, 2, 2.5, 5, 10)]
    step = next((st for st in sorted(steps) if span / st <= n + 0.5), max(steps))
    lo2 = math.floor(lo / step) * step
    hi2 = math.ceil(hi / step) * step
    ticks, v = [], lo2
    while v <= hi2 + 1e-9:
        ticks.append(round(v, 6))
        v += step
    return lo2, hi2, ticks


def frame(p: Plot, body: str, yticks: Sequence[tuple[float, str]],
          xlabels: Sequence[tuple[float, str]], highlight: tuple[float, float] | None = None,
          label: str = "") -> str:
    hl = (f'<rect x="{highlight[0]:.1f}" y="{p.pt}" width="{highlight[1] - highlight[0]:.1f}" '
          f'height="{p.ph}" class="hl"/>' if highlight else "")
    grid = "".join(f'<line x1="{PAD_L}" y1="{p.y(v):.1f}" x2="{p.w - PAD_R}" y2="{p.y(v):.1f}" class="grid"/>'
                   f'<text x="{PAD_L - 6}" y="{p.y(v) + 3.5:.1f}" class="tick end">{escape(t)}</text>'
                   for v, t in yticks)
    xs = "".join(f'<text x="{x:.1f}" y="{p.h - 6}" class="tick mid">{escape(t)}</text>' for x, t in xlabels)
    base = f'<line x1="{PAD_L}" y1="{p.pt + p.ph}" x2="{p.w - PAD_R}" y2="{p.pt + p.ph}" class="axis"/>'
    aria = f' aria-label="{escape(label)}"' if label else ""
    return (f'<svg viewBox="0 0 {p.w} {p.h}" class="chart" role="img"{aria}>'
            f'{hl}{grid}{body}{base}{xs}</svg>')


def _every(n: int, room: int = 12) -> int:
    return max(1, math.ceil(n / room))


def _segments(pts: Sequence[tuple[float, float] | None]) -> list[list[tuple[float, float]]]:
    """Split a line on missing values so a gap stays a gap."""
    segs, cur = [], []
    for pt in pts:
        if pt is None:
            if len(cur) > 1:
                segs.append(cur)
            cur = []
        else:
            cur.append(pt)
    if len(cur) > 1:
        segs.append(cur)
    return segs


def _poly(seg: list[tuple[float, float]], cls: str, color: str) -> str:
    return (f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in seg)}" class="{cls}" '
            f'style="stroke:{color}"/>')


# --------------------------------------------------------------------------
# weekly charts
# --------------------------------------------------------------------------
def week_stack(labels: list[str], within: list[float], over: list[float],
               bands: list[tuple[float, float] | None], current: int | None,
               tips: list[str], height: int = 170) -> str:
    """Threshold km within cap with over-cap stacked on top; each week's band
    shaded behind its own bar."""
    tops = [w + o for w, o in zip(within, over)] + [b[1] for b in bands if b]
    top, step = nice_max(max(tops) if tops else 1)
    p = Plot(height, 0, top, slots=len(labels))
    body = []
    for i, b in enumerate(bands):
        if not b:
            continue
        cx, w = p.slot(i)
        if b[0] == b[1]:          # a single planned figure: a target tick, not a zero-height band
            body.append(f'<line x1="{cx - w * 0.42:.1f}" y1="{p.y(b[0]):.1f}" x2="{cx + w * 0.42:.1f}" '
                        f'y2="{p.y(b[0]):.1f}" class="target"/>')
            continue
        body.append(f'<rect x="{cx - w / 2 + 1:.1f}" y="{p.y(b[1]):.1f}" width="{w - 2:.1f}" '
                    f'height="{p.y(b[0]) - p.y(b[1]):.1f}" class="band"/>')
    for i, (wi, ov) in enumerate(zip(within, over)):
        cx, w = p.slot(i)
        bw = min(w * 0.55, 30)
        if wi > 0:
            body.append(f'<rect x="{cx - bw / 2:.1f}" y="{p.y(wi):.1f}" width="{bw:.1f}" '
                        f'height="{p.y(0) - p.y(wi):.1f}" rx="3" class="bar s1" data-tip="{escape(tips[i])}"/>')
        if ov > 0:
            y_top = p.y(wi + ov)
            h = p.y(wi) - y_top - (2 if wi > 0 else 0)
            body.append(f'<rect x="{cx - bw / 2:.1f}" y="{y_top:.1f}" width="{bw:.1f}" '
                        f'height="{max(h, 1):.1f}" rx="3" class="bar sact" data-tip="{escape(tips[i])}"/>')
        if wi == 0 and ov == 0:
            body.append(f'<rect x="{cx - w / 2:.1f}" y="{PAD_T}" width="{w:.1f}" height="{p.ph}" '
                        f'fill="transparent" data-tip="{escape(tips[i])}"/>')
    yt = [(step * k, f"{step * k:g}") for k in range(5)]
    hl = None
    if current is not None:
        cx, w = p.slot(current)
        hl = (cx - w / 2, cx + w / 2)
    every = _every(len(labels))
    xl = [(p.slot(i)[0], l) for i, l in enumerate(labels) if (len(labels) - 1 - i) % every == 0]
    return frame(p, "".join(body), yt, xl, hl, "Threshold km per week")


def week_bars(labels: list[str], values: list[float | None], targets: list[float | None],
              current: int | None, tips: list[str], integer: bool = False,
              height: int = 170, cls: str = "s1", width: int = W) -> str:
    """Weekly bars with a target tick per week."""
    vals = [v for v in values if v] + [t for t in targets if t]
    top, step = nice_max(max(vals) if vals else 1, integer=integer)
    p = Plot(height, 0, top, slots=len(labels), width=width)
    body = []
    for i, v in enumerate(values):
        cx, w = p.slot(i)
        bw = min(w * 0.55, 30)
        if v:
            body.append(f'<rect x="{cx - bw / 2:.1f}" y="{p.y(v):.1f}" width="{bw:.1f}" '
                        f'height="{p.y(0) - p.y(v):.1f}" rx="3" class="bar {cls}" data-tip="{escape(tips[i])}"/>')
        else:
            body.append(f'<rect x="{cx - w / 2:.1f}" y="{PAD_T}" width="{w:.1f}" height="{p.ph}" '
                        f'fill="transparent" data-tip="{escape(tips[i])}"/>')
        t = targets[i]
        if t:
            body.append(f'<line x1="{cx - w * 0.42:.1f}" y1="{p.y(t):.1f}" x2="{cx + w * 0.42:.1f}" '
                        f'y2="{p.y(t):.1f}" class="target"/>')
    yt = [(step * k, f"{step * k:g}") for k in range(5)]
    hl = None
    if current is not None:
        cx, w = p.slot(current)
        hl = (cx - w / 2, cx + w / 2)
    every = _every(len(labels))
    xl = [(p.slot(i)[0], l) for i, l in enumerate(labels) if (len(labels) - 1 - i) % every == 0]
    return frame(p, "".join(body), yt, xl, hl)


# --------------------------------------------------------------------------
# daily charts (x = day index)
# --------------------------------------------------------------------------
def day_line(n_days: int, series: list[tuple[str, list[float | None], str]],
             band: tuple[float, float] | None, xlabels: list[tuple[int, str]],
             highlight: tuple[int, int] | None, height: int = 130,
             fmt: Callable[[float], str] = lambda v: f"{v:g}",
             y_range: tuple[float, float] | None = None, tips: list[str] | None = None,
             nticks: int = 3, width: int = W, steps: Sequence[float] | None = None) -> str:
    """One or more daily lines, a shaded target band, gaps kept as gaps."""
    vals = [v for _c, vs, _n in series for v in vs if v is not None]
    if band:
        vals += list(band)
    ticks = None
    if y_range:
        lo, hi = y_range
    elif vals:
        lo, hi, ticks = nice_ticks(min(vals), max(vals), nticks, steps)
    else:
        lo, hi = 0, 1
    p = Plot(height, lo, hi, x0=0, x1=max(n_days - 1, 1), width=width)
    body = []
    if band:
        body.append(f'<rect x="{PAD_L}" y="{p.y(band[1]):.1f}" width="{p.pw}" '
                    f'height="{p.y(band[0]) - p.y(band[1]):.1f}" class="band"/>')
    for color, vs, cls in series:
        pts = [(p.x(i), p.y(v)) if v is not None else None for i, v in enumerate(vs)]
        for seg in _segments(pts):
            body.append(_poly(seg, cls, color))
        last = next((pt for pt in reversed(pts) if pt), None)
        if last and cls == "line":
            body.append(f'<circle cx="{last[0]:.1f}" cy="{last[1]:.1f}" r="3.2" class="enddot" style="fill:{color}"/>')
    if tips:
        step = p.pw / max(n_days, 1)
        body += [f'<rect x="{p.x(i) - step / 2:.1f}" y="{PAD_T}" width="{step:.1f}" height="{p.ph}" '
                 f'fill="transparent" data-tip="{escape(t)}"/>' for i, t in enumerate(tips) if t]
    tv = ticks or [lo + (hi - lo) * k / nticks for k in range(nticks + 1)]
    yt = [(v, fmt(v)) for v in tv]
    hl = (p.x(highlight[0]) - 3, p.x(highlight[1]) + 3) if highlight else None
    return frame(p, "".join(body), yt, [(p.x(i), t) for i, t in xlabels], hl)


def day_dots(n_days: int, values: list[float | None], target: float | None,
             y_range: tuple[float, float], xlabels: list[tuple[int, str]],
             highlight: tuple[int, int] | None, fmt: Callable[[float], str],
             tips: list[str], late_is_bad: bool = True, height: int = 130,
             yticks: list[float] | None = None) -> str:
    """One dot per day on a fixed axis (bedtime), with a target line."""
    lo, hi = y_range
    p = Plot(height, lo, hi, x0=0, x1=max(n_days - 1, 1))
    body = []
    if target is not None:
        body.append(f'<line x1="{PAD_L}" y1="{p.y(target):.1f}" x2="{p.w - PAD_R}" y2="{p.y(target):.1f}" class="target"/>')
    for i, v in enumerate(values):
        if v is None:
            continue
        vv = min(max(v, lo), hi)
        late = target is not None and ((v > target) if late_is_bad else (v < target))
        body.append(f'<circle cx="{p.x(i):.1f}" cy="{p.y(vv):.1f}" r="4" class="dot {"dwarn" if late else "dok"}" '
                    f'data-tip="{escape(tips[i])}"/>')
    ticks = yticks or [lo + (hi - lo) * k / 3 for k in range(4)]
    hl = (p.x(highlight[0]) - 4, p.x(highlight[1]) + 4) if highlight else None
    return frame(p, "".join(body), [(t, fmt(t)) for t in ticks], [(p.x(i), t) for i, t in xlabels], hl)


def scatter_trend(x_days: list[float], ys: list[float], valid: list[bool], tips: list[str],
                  n_days: int, fit: tuple[float, float] | None, xlabels: list[tuple[int, str]],
                  highlight: tuple[int, int] | None, fmt: Callable[[float], str],
                  invert: bool = False, height: int = 150) -> str:
    """Dots per run, trend line over the valid ones; invalid runs drawn grey.
    invert=True puts smaller values (faster pace) at the top."""
    if ys:
        lo, hi, ticks = nice_ticks(min(ys) - 3, max(ys) + 3, 3, (5, 10, 15, 20, 30, 60, 120))
    else:
        lo, hi, ticks = 0, 1, [0, 1]
    sgn = -1 if invert else 1
    p = Plot(height, sgn * (hi if invert else lo), sgn * (lo if invert else hi), x0=0, x1=max(n_days - 1, 1))
    body = []
    if fit and len(x_days) > 1:
        a, b = fit
        xa, xb = min(x for x, ok in zip(x_days, valid) if ok), max(x for x, ok in zip(x_days, valid) if ok)
        body.append(f'<line x1="{p.x(xa):.1f}" y1="{p.y(sgn * (a * xa + b)):.1f}" x2="{p.x(xb):.1f}" '
                    f'y2="{p.y(sgn * (a * xb + b)):.1f}" class="fit"/>')
    for x, yv, ok, t in zip(x_days, ys, valid, tips):
        body.append(f'<circle cx="{p.x(x):.1f}" cy="{p.y(sgn * yv):.1f}" r="4.2" '
                    f'class="dot {"dok" if ok else "dgrey"}" data-tip="{escape(t)}"/>')
    yt = [(sgn * v, fmt(v)) for v in ticks]
    hl = (p.x(highlight[0]) - 4, p.x(highlight[1]) + 4) if highlight else None
    return frame(p, "".join(body), yt, [(p.x(i), t) for i, t in xlabels], hl)


def stacked_days(labels: list[str], series: list[tuple[str, list[float]]], tips: list[str],
                 missing: list[bool], height: int = 170, width: int = W) -> str:
    """Stacked daily bars (sleep stages). Missing nights get an explicit marker."""
    totals = [sum(vs[i] for _c, vs in series) for i in range(len(labels))]
    top, step = nice_max(max(totals) if totals else 1)
    p = Plot(height, 0, top, slots=len(labels), width=width)
    body = []
    for i in range(len(labels)):
        cx, w = p.slot(i)
        bw = max(min(w - 3, 22), 3)
        if missing[i]:
            body.append(f'<rect x="{cx - bw / 2:.1f}" y="{p.y(0) - 3:.1f}" width="{bw:.1f}" height="3" '
                        f'rx="1.5" class="nodata" data-tip="{escape(tips[i])}"/>')
            continue
        acc = 0.0
        for cls, vs in series:
            v = vs[i]
            if not v:
                continue
            y = p.y(acc + v)
            body.append(f'<rect x="{cx - bw / 2:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                        f'height="{max(p.y(acc) - y - 2, 0.8):.1f}" rx="2" class="bar {cls}" '
                        f'data-tip="{escape(tips[i])}"/>')
            acc += v
    yt = [(step * k, f"{step * k:g}") for k in range(5)]
    every = _every(len(labels))
    xl = [(p.slot(i)[0], l) for i, l in enumerate(labels) if (len(labels) - 1 - i) % every == 0]
    return frame(p, "".join(body), yt, xl)


def spark(values: list[float | None], color: str, height: int = 30) -> str:
    xs = [v for v in values if v is not None]
    if len(xs) < 2:
        return f'<svg class="spark" viewBox="0 0 108 {height}" preserveAspectRatio="none" aria-hidden="true"></svg>'
    lo, hi = min(xs), max(xs)
    if hi == lo:
        lo, hi = lo - 1, hi + 1
    n = len(values) - 1
    pts = [((i / n) * 108, 3 + (height - 6) * (1 - (v - lo) / (hi - lo))) if v is not None else None
           for i, v in enumerate(values)]
    body = "".join(_poly(seg, "line", color) for seg in _segments(pts))
    return (f'<svg class="spark" viewBox="0 0 108 {height}" preserveAspectRatio="none" '
            f'aria-hidden="true">{body}</svg>')


# --------------------------------------------------------------------------
# season overview
# --------------------------------------------------------------------------
def _fit(name: str, width: float, short: dict[str, str] | None = None, char_px: float = 6.4) -> str:
    """Longest of name / short form / initial that fits the segment. Short forms
    come from the config ([season] phase_short), else the first four letters."""
    for cand in (name, (short or {}).get(name.lower(), name[:4]), name[:1]):
        if len(cand) * char_px + 8 <= width:
            return cand
    return ""


def season_bars(labels: list[str], actual: list[float | None], plan: list[float | None],
                current: int | None, tips: list[str],
                phases: list[tuple[str, int, int, str]], width: int = 720, height: int = 240,
                short: dict[str, str] | None = None) -> str:
    """Plan as outlined bars, actual as solid bars, and the phases in their own
    labelled strip above the plot - a label never overlaps its neighbour; one too
    long for its phase is shortened, and the full name is in the tooltip."""
    strip_h, gap = 18, 8
    vals = [v for v in actual if v] + [v for v in plan if v]
    top, step = nice_max(max(vals) if vals else 1)
    p = Plot(height, 0, top, slots=len(labels), width=width, pad_t=PAD_T + strip_h + gap)
    body = []
    for k, (name, i0, i1, tip) in enumerate(phases):
        x0 = p.slot(i0)[0] - p.slot(i0)[1] / 2 + 1
        x1 = p.slot(i1)[0] + p.slot(i1)[1] / 2 - 1
        y = PAD_T
        cls = "ph-rec" if name.lower().startswith("recovery") else ("ph-a" if k % 2 == 0 else "ph-b")
        body.append(f'<rect x="{x0:.1f}" y="{y}" width="{x1 - x0:.1f}" height="{strip_h}" rx="4" '
                    f'class="{cls}" data-tip="{escape(tip)}"/>')
        text = _fit(name, x1 - x0, short)
        if text:
            body.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{y + 12.5}" class="ph-l">{escape(text)}</text>')
        if k:                                          # faint boundary line through the plot
            body.append(f'<line x1="{x0 - 1:.1f}" y1="{p.pt}" x2="{x0 - 1:.1f}" y2="{p.pt + p.ph}" class="ph-div"/>')
    for i in range(len(labels)):
        cx, w = p.slot(i)
        bw = min(w * 0.62, 26)
        a, pl = actual[i], plan[i]
        if a:
            body.append(f'<rect x="{cx - bw / 2:.1f}" y="{p.y(a):.1f}" width="{bw:.1f}" '
                        f'height="{p.y(0) - p.y(a):.1f}" rx="3" class="bar s1"/>')
        if pl:
            body.append(f'<rect x="{cx - bw / 2:.1f}" y="{p.y(pl):.1f}" width="{bw:.1f}" '
                        f'height="{p.y(0) - p.y(pl):.1f}" rx="3" class="planbar"/>')
        body.append(f'<rect x="{cx - w / 2:.1f}" y="{p.pt}" width="{w:.1f}" height="{p.ph}" '
                    f'fill="transparent" data-tip="{escape(tips[i])}"/>')
    yt = [(step * k, f"{step * k:g}") for k in range(5)]
    hl = None
    if current is not None:
        cx, w = p.slot(current)
        hl = (cx - w / 2, cx + w / 2)
    every = _every(len(labels), room=12)
    # keep a label only if it fits inside the chart, so nothing overflows the edge
    xl = [(p.slot(i)[0], l) for i, l in enumerate(labels)
          if i % every == 0 and p.slot(i)[0] + 4 * len(l) <= p.w - PAD_R]
    return frame(p, "".join(body), yt, xl, hl, "Running km per week across the season, plan and actual")
