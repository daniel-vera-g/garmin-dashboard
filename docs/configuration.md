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
| `[threshold]` | HR caps for sub-threshold reps by rep length; `cap_tolerance_long_bpm` / `cap_tolerance_short_bpm` (and `cap_tolerance_bpm` for both): reps up to cap + tolerance are amber and count as threshold km, above it red; how reps are classified against LT pace |
| `[easy]`, `[long_run]` | Easy-run and long-run HR caps (a long run over its cap is red); time above the easy cap: `share_green` starts amber, `max_share_above` starts red |
| `[speed]` | `pace_tolerance_s`: s/km outside a session's planned pace band that is still amber |
| `[runs]` | Run-day targets. Weekly run km and threshold km have three levels: within `*_tolerance` green, down to `-*_amber_under` / up to `+*_amber_over` amber, beyond red (amber 0 = not set: outside green is amber). `threshold_tolerance` 0 = a range as written; a single-figure band then uses `run_km_tolerance` |
| `[bike]` | Bike-hour cap, easy-ride HR cap, ride distance cap, `ftp_w` (your own test only), run-equivalence and running-share rules |
| `[sleep]` | Bedtime target; solid night (`solid_score`, `solid_hours`) and amber night (`amber_score`, `amber_hours`); `solid_nights_target` solid nights = green, `ok_nights_target` solid-or-amber nights = amber |
| `[readiness]` | Your HRV 7-day target band |
| `[load]` | ACWR band |
| `[pace_at_hr]` | HR band and conditions for the pace-at-HR fitness trend |
| `[hard_session]` | Rest windows after sub-T / over-cap / race / test; recovery-time limit; `red_night_ok_readiness`: one red night before quality is fine at this readiness or better (both nights at least amber always pass) |
| `[gym]` | Gym sessions per week, optionally changing at dates |
| `[alerts]` | The "what to do" line under each alert, by alert key; `{easy_hr_cap_bpm}`-style placeholders are filled in from the settings |
| `[season]` | `week1_start` (Monday of week 1); `phase_short` labels for narrow phase segments |
| `[[race]]` | date, name, short name, priority A/B/C (A drives the header countdown), sport |
| `[[phase]]` | name, start, end, default `run_km` and `threshold` band, `recovery` |
| `[[week]]` | per-week overrides by week number `n`: `run_km`, `threshold`, `recovery` |
| `[[session]]` | the coach's word on one session by `date` (optional `name`): `kind` = speed / subt / race / test to reclassify, `accept = true` to accept its verdict, `pace = "m:ss-m:ss"` (speed reps judged on pace), `note`. A reclassified or accepted hard session still starts the 48 h rest window |

Everything the coach decides - weekly targets, session verdicts, races, caps, tolerances,
sleep and HRV targets, alert texts, the FTP - can be updated from the dashboard's
**Coach** popup, which edits this file as text (comments survive), keeps a backup in
`private/backups/` and archives the answer in `private/notes/coach/`.

Changes show up on the next sync, or at once with `./run.sh build`. A change to
`[app] host`/`port` needs `./run.sh install`.
