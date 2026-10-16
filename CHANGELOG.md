# Changelog

All notable changes, newest first. Versions follow [semantic versioning](https://semver.org):
**major** = a change that needs action (config format, removed feature), **minor** = new
features, **patch** = fixes. The running version is shown in the dashboard header.
How to release: [docs/releasing.md](docs/releasing.md).

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
