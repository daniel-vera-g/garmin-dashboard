# Configuration

Everything personal is in one file, `private/config.toml` (gitignored). Start from the
template:

```bash
mkdir -p private && cp config.example.toml private/config.toml
```

`DASHBOARD_CONFIG=/path/to/file.toml` points the dashboard elsewhere. A missing key
falls back to the default in `settings.DEFAULTS` (the same values as the template), and
a missing file runs the whole dashboard on defaults. Every value in the template is a
neutral placeholder, not a recommendation.

| Section | What it controls |
|---|---|
| `[app]` | `host`, `port` of the local server; `auto_sync_hours` (0 = never); `carryover_days` (how long last week's missed targets stay in the alerts) |
| `[sync]` | Garmin token folder; how many days of history are kept and re-pulled |
| `[coach]` | `ahead_weeks`: how far ahead the Coach popup looks for missing targets |
| `[health]` | Thresholds on the Health page: resting-HR rise, sleep shortfall, bed/wake consistency |
| `[threshold]` | HR caps for sub-threshold reps by rep length; how reps are classified against LT pace |
| `[easy]`, `[long_run]` | Easy-run and long-run HR caps; share of time allowed above the cap |
| `[runs]` | Run-day targets; tolerance around the weekly run-km target (also for single-figure threshold bands) |
| `[bike]` | Bike-hour cap, easy-ride HR cap, ride distance cap, `ftp_w` (your own test only), run-equivalence and running-share rules |
| `[sleep]` | Bedtime target, what counts as a solid night, solid nights per week |
| `[readiness]` | Your HRV 7-day target band |
| `[load]` | ACWR band |
| `[pace_at_hr]` | HR band and conditions for the pace-at-HR fitness trend |
| `[hard_session]` | Rest windows after sub-T / over-cap / race / test; recovery-time limit |
| `[gym]` | Gym sessions per week, optionally changing at dates |
| `[alerts]` | The "what to do" line under each alert, by alert key; `{easy_hr_cap_bpm}`-style placeholders are filled in from the settings |
| `[season]` | `week1_start` (Monday of week 1); `phase_short` labels for narrow phase segments |
| `[[race]]` | date, name, short name, priority A/B/C (A drives the header countdown), sport |
| `[[phase]]` | name, start, end, default `run_km` and `threshold` band, `recovery` |
| `[[week]]` | per-week overrides by week number `n`: `run_km`, `threshold`, `recovery` |

Weekly targets and the FTP can be updated from the dashboard's **Coach** popup, which
edits this file as text (comments survive) and keeps a backup in `private/backups/`.

Changes show up on the next sync, or at once with `./run.sh build`. A change to
`[app] host`/`port` needs `./run.sh install`.
