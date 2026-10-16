# Reference

Working knowledge that isn't obvious from the code.

## Garmin data sources (library `garminconnect` 0.3.x)

| Data | Call | Stored as | Notes |
|---|---|---|---|
| Activities | `get_activities_by_date` | `activities.json` | whitelisted summary fields only |
| Sleep | `get_sleep_daily` (28-day chunks) | `sleep.json` | contains `restingHeartRate` → used for RHR |
| HRV | `get_hrv_data_range` (28-day chunks) | `hrv.json` | Garmin baseline band; status follows the 7-day average |
| Morning readiness | `get_morning_training_readiness` | `readiness/` | |
| Latest readiness | `get_training_readiness` (latest of day) | `readiness_now/` | drops after every workout |
| Acute/chronic load | `get_training_status` per day | `load/` | only the two load numbers are stored |
| LT pace/power | `get_lactate_threshold` (range + latest) | `lactate.json` | speed ×10; LT heart rate never stored |
| Reps | `get_activity_typed_splits` | `activity/<id>/intervals.json` | lap DTOs aren't enough (watch "Intervals" label every lap `INTERVAL`) |
| HR/speed/power samples | `get_activity_details` | `activity/<id>/series.json` | ~1.8 MB raw → slim columns; body battery / performance condition dropped |
| Weather | `get_activity_weather` | `activity/<id>/weather.json` | °F with no unit field |

- Not used (banned or unneeded): VO2max/max metrics, race predictions, body battery,
  endurance/hill score, body composition, cycling FTP.
- Call budget: first backfill ≈ 250 calls / 3–4 min. Normal sync: **~9 calls / ~6 s**
  thanks to `data/cache/coverage.json`; deleting it forces one full re-fetch (~17 calls).
- `get_sleep_summary`, `get_hrv_trend`, `get_training_load_trend` are Garmin **MCP
  server** tools, not library methods; the MCP's load trend is itself built on the
  training-status endpoint. An MCP server is handy for interactive checks; the
  dashboard pipeline uses the library.

## Verification baselines (repeat these in reviews)

- Weekly run/bike km and session counts vs Garmin's own aggregate
  (`get_progress_summary_between_dates`, `distance` sum is in **centimetres**):
  should match exactly.
- ACWR = Garmin acute ÷ chronic must equal Garmin's trend values to 2 decimals. A local
  EWMA drifted up to 0.14 — don't reintroduce it.
- LT pace = raw speed × 10 → pace; sanity-check against a known recent value.
- Every "fixed" claim gets a reproduction (synthetic fixture or stub client), not just a
  passing test.

## Dev workflow

- Larger changes: work on a branch in a worktree (`.worktrees/<name>`, excluded via
  `.git/info/exclude`, `.venv` symlinked) so the live service on :8765 keeps running.
- Preview a worktree build on another port: `serve.py --no-browser --port 8766`.
- Before every commit: `./run.sh test` and a grep of staged files for personal values.
- After changing `serve.py`: nothing to do — the login agent
  (`DASHBOARD_RESTART_ON_CHANGE=1`) exits within ~5 s and launchd starts the new code.
  Changing `run.sh`'s plist still needs `./run.sh install`.
- Build profile: full build ~200 ms, ~75 % is reading the cache.

## Coach prompts

Plan values come from a coaching session. Routine updates go through the dashboard's
Coach popup ([coach-updates.md](coach-updates.md)). For a first season import, ask the
coach for exactly one `tomllib`-parseable block with `[season] week1_start`, `[[race]]`
(date, name, short, priority A/B/C, sport), `[[phase]]` (name, start, end, optional
run_km / threshold [lo, hi], recovery) and `[[week]]` overrides (n, run_km, threshold),
plus FLAGS and MISSING lists. Rules: never invent values; a range → midpoint with the
range in a comment; a single threshold number → `[n, n]` + flag; `recovery = true` only
where the plan says so.
