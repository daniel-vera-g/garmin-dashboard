# How to use this

## Every day

Open **http://127.0.0.1:8765** (bookmark it). The service starts by itself at login.
If the data is more than 6 hours old the page syncs on its own; otherwise press
**Sync**. A sync takes a few seconds and the page reloads.

## When something's wrong

| What you see | What to do |
|---|---|
| Page won't load | `./run.sh status` - if *not installed*, run `./run.sh install` |
| "Sync failed: ..." in the header | Usually expired tokens: `./run.sh login` |
| No Sync button | You opened the `.html` file directly - use the bookmark |
| "no target" everywhere | the config has no phases or weekly targets - use **Coach** (below) |
| A quality session shows as "Easy" | It wasn't run as a structured workout or with the watch's Intervals feature - see below |
| Want to see what happened | `tail -20 data/serve.log` |

`data/cache/` is only a cache. Delete it and the next sync rebuilds it.

## Updating the plan from your coach

Garmin can't give the dashboard your plan, so the weekly run km, the threshold-km
band and the bike FTP come from your coach. The **Coach** button in the header (the
badge counts missing values) opens a popup:

1. **Copy prompt** and send it to your coaching session. It lists the dashboard's
   current targets for this week and the next four, so the coach can confirm,
   correct or fill them in.
2. Paste the whole reply into the box and press **Check**. You see exactly what would
   change, and what was not taken over.
3. **Apply** writes `private/config.toml` (a backup goes to `private/backups/`),
   rebuilds the pages and reloads.

The prompt asks for everything at once: targets for the coming weeks, the coach's view
on recent sessions the dashboard flagged (a planned fast session can be reclassified,
a verdict that's fine can be accepted), all caps and tolerances, races, and anything
else relevant. The popup opens by itself, once a day, while this week or next is missing
a target. Phases and gym targets are still edited by hand (or ask Claude).

## Alerts

Open **Alerts** to see what needs attention. **✓** marks an alert as viewed: it moves
down, greys out and leaves the counts. Click it again to undo. An alert whose wording
changes (new numbers) shows up as new again. Viewed marks are kept in this browser.

## Version

The header shows the running version (e.g. `v1.1.0`); what changed in each version is in
[CHANGELOG.md](CHANGELOG.md).

## Common changes

All in `private/config.toml` ([docs/configuration.md](docs/configuration.md)):

| You want | Edit |
|---|---|
| Next weeks' run km / threshold band | **Coach** button, or a `[[phase]]` / `[[week]]` override |
| Add a race | a `[[race]]`; `priority = "A"` for the header countdown |
| Change a cap or threshold | its section, e.g. `[easy] hr_cap_bpm` |
| Bike threshold minutes | `ftp_w` under `[bike]` (or the Coach popup) |
| Your coach's wording for an alert | `[alerts]` |
| Never auto-sync | `[app] auto_sync_hours = 0` |

Changes show up on the next sync, or instantly with `./run.sh build`.

---

# Reading the dashboard

**Colours mean action.** The dot next to a metric is its status: green on target,
amber drifting, red act now, grey **no data** (never "neutral"). A metric with nothing
to judge - a baseline like your 30-day resting HR - has no dot. A count still in
progress (run days, solid nights, gym) shows a bar instead, and turns green once
reached or amber once it can't be reached any more. Every amber or red item in
**Alerts** says what to do. Arrows show the change against the same point last week.

Readiness follows Garmin's level: High/Prime/Moderate green, Low amber, Poor red.
Sleep history uses Garmin's own verdict for that component.

**Weekly totals are judged fairly mid-week.** A cap that's already exceeded flags at
once (bike hours, over-cap km, too much running). Falling short only flags when the
week is over, or once the target can no longer be reached (run days).

## Hard session: OPEN / CLOSED

OPEN only when all five checks pass: readiness not POOR; the last two nights solid;
24 h since the last sub-T session within cap; 48 h since an over-cap session, a race
or a test; Garmin's recovery time at or under 24 h. When CLOSED it names what failed
and the **earliest** it can open - a lower bound, since sleep and readiness can't be
predicted.

Readiness and recovery time come from Garmin's **latest** reading, refreshed at
every sync - they drop after every workout, so the dashboard matches the Garmin app.
Values from the night (sleep, HRV, sleep history) don't change during the day.

## Threshold km - the hero number

Rep distance from sub-threshold reps whose average HR stayed at or under the cap.
The cap depends on rep length (long reps get the lower cap). Warm-up, cool-down and
recoveries never count. **Over-cap km** is shown separately in red.

How reps are recognised:
- Run quality sessions as a **structured workout** or with the watch's **Intervals**
  feature - that's what marks reps apart from recoveries. Manually lapped runs are
  treated as easy runs.
- Laps under 100 m or 30 s are lap-button artefacts and ignored.
- A session is **speed work** when its median rep is clearly faster than your Garmin
  LT pace; speed sessions don't count toward threshold km and can't go over cap.
  Everything else is sub-T - so a single rep run too hard is still judged against the
  cap rather than excused as speed.

## The weekly card

Grouped as **Running** (threshold km, run km, run days, long run, easy-run HR),
**Bike** (hours, run share, discipline, threshold minutes), **Sleep** (solid nights,
bedtime) and **Load & gym** (ACWR, gym sessions).

- **Long run** - the week's longest unstructured run of an hour or more, judged on
  its average HR.
- **Easy-run HR** - two rules, both shown with ✓ / ✗: average HR at or under the
  cap, and under 10 % of the time above it. Either failing makes the cell red; the
  failing part is the red number. The share is what catches a run where a walk at
  the end hides HR drifting up.
- **Run share** - running km against bike km converted to running (flat and hilly
  rides convert differently). Off in recovery weeks; not judged until there's enough
  volume in the week to mean something.
- **Bike discipline** - a ride on a hard-run day, the day before the long run, an
  easy ride above the HR cap, or a ride over the distance cap.
- **Bike threshold** - minutes at or above the threshold share of FTP (30-s smoothed
  power). Never added to threshold km. Grey until you set your own FTP.
- **ACWR** - Garmin's own acute and chronic load. Amber when above the band for two
  weeks running.

## Trends (12 weeks, this week highlighted)

- **Pace at fixed HR** - pace while HR sits in the window, first 10 minutes ignored.
  Runs that are warm, hilly, indoor, have no reliable weather, or come within 24 h of
  a quality session are drawn grey and left out of the trend line - tired legs would
  otherwise read as lost fitness. Faster is up.
- **Bedtime + sleep score** and **HRV + ACWR** are each two stacked panels: two
  measures with different units never share one axis.

## Season

Plan is drawn as outlined bars, actual running km as solid bars on top - a solid bar
above its outline is a week over plan. The strip above the chart shows the phases;
hover or tap a segment for its name and dates. On a phone, swipe the chart sideways.

## Missing data

Missing is shown as missing - a dash and "no data", never a zero, and it is left out
of every average.
