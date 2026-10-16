# TODO

Generic open work. Personal open items (plan values still to come from the coach,
decisions about one season) live in the gitignored `private/notes/open-items.md`.
Each item should come with a regression test or a reproducible check before it is
ticked off.

## Features

- [ ] **Execution rate** - share of planned workouts done as planned. Needs the weekly
  workouts on the Garmin calendar; read them with the scheduled-workouts endpoint and
  match by day and type.
- [ ] **Quality sessions with manual laps** - sessions run with the lap button (not a
  structured workout or the watch's Intervals feature) are read as easy runs. Detect
  reps from lap pace against LT pace, or let the user tag them.

## Data quality

- [ ] **Details fetched while Garmin is still processing** - an *empty* (not failed)
  per-activity answer seconds after upload is cached for good. Re-try empty answers
  for activities younger than a day.
- [ ] **Pace-at-HR trend is sparse in warm months** - most summer runs fail the
  ≤ max_temp_c condition. Consider a temperature correction instead of exclusion.

## Done

The full review backlog (two code reviews, Oct 2026) is closed; see
[docs/design.md](docs/design.md) for the resulting rules.
