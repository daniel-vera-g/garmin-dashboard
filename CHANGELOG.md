# Changelog

All notable changes, newest first. Versions follow [semantic versioning](https://semver.org):
**major** = a change that needs action (config format, removed feature), **minor** = new
features, **patch** = fixes. The running version is shown in the dashboard header.
How to release: [docs/releasing.md](docs/releasing.md).

## [1.2.0] - 2026-10-17

### Added
- **Three-level bands (green / amber / red)** - amber counts as done, red doesn't:
  - rep caps per rep length (`cap_tolerance_long_bpm`, `cap_tolerance_short_bpm`): reps
    up to cap + tolerance are amber and count as threshold km; only red reps are over cap;
  - weekly run km and threshold km: green ± `*_tolerance`, amber down to
    `-*_amber_under` / up to `+*_amber_over`, red beyond;
  - easy runs: time above the cap amber from `share_green`, red from `max_share_above`;
  - sleep: amber nights (`amber_score`, `amber_hours`) and `ok_nights_target`.
- **Pre-quality gate**: both nights at least amber pass; one red night passes when
  readiness is at least `red_night_ok_readiness`.
- **Speed sessions judged on pace**: a `[[session]]` can carry `pace = "m:ss-m:ss"`;
  reps outside the band (± `speed.pace_tolerance_s`) are amber / red, with an alert.
- The coach prompt lists **every** session of the last two weeks and the **exact key
  names** for all bands.

### Changed
- A long run over its HR cap is red (was amber).
- "Cap breached" alerts name only the red reps; reps inside the tolerance are listed
  separately.

### Fixed
- **Reps merged by the workout steps**: an extra rep run inside one workout step was
  read as one long rep. Structured runs now also fetch the watch's laps, and a step
  holding several laps is split into reps and recoveries by pace. Run a sync to fetch
  the laps for older runs.

## [1.1.0] - 2026-10-16

### Added
- **Mark alerts as viewed**: a ✓ on each alert moves it below the others and out of the
  counts. Remembered per browser; an alert whose wording changes comes back as new.
- **Session verdicts from the coach**: `[[session]]` entries in the config (or a coach
  answer) reclassify a session (e.g. a planned fast session → `speed`) and/or accept its
  verdict, with a note shown in the session list. A reclassified or accepted hard session
  still starts the 48 h rest window.
- **Tolerances**: `threshold.cap_tolerance_bpm` (reps just over the cap count as within,
  shown amber with a "near the cap" alert) and `runs.threshold_tolerance` (± around the
  threshold-km band).
- **Coach prompt asks for everything**: weekly targets, the dashboard's reading of the
  last two weeks' sessions (with the reason for each verdict), all caps, rules and
  tolerances with current values, races ahead, and anything else relevant.
- **Coach answers update more**: `[[session]]`, `[[race]]` and any coach-set setting
  (caps, tolerances, sleep, HRV band, alert texts …), each type- and range-checked. The
  coach's notes are shown before applying and archived with the answer.
- Version shown in the header; this changelog.

### Fixed
- A JavaScript syntax error in a page script now fails the tests (node check).

## [1.0.0] - 2026-10-16

First public release: incremental whitelisted Garmin sync; training metrics (threshold
km within the HR cap, hard-session gate, weekly targets, easy-run discipline, bike rules,
sleep, load); home, season and health pages on localhost; Coach popup for plan values;
one gitignored config file for every personal value.
