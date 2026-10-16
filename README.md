# Training dashboard

A personal training dashboard built from Garmin Connect data. Three static pages,
rendered by a small Python pipeline and served locally:

- **Home** - is a hard session OK today, did the quality land, where am I drifting
- **Season** - race timeline with countdowns, running km planned vs actual by phase
- **Health** - resting HR, sleep stages, HRV against Garmin's own baseline

Day to day use and how to read the numbers: **[HOWTO.md](HOWTO.md)**. This file is the reference.

## Setup

```bash
./run.sh login
```
Sign in with your Garmin account in your own terminal (email, password, MFA code).
Only the resulting OAuth tokens are kept, in `~/.garminconnect`.

```bash
mkdir -p private && cp config.example.toml private/config.toml
```
Fill in your own values. `private/` is gitignored. Without the file the dashboard runs
on the neutral placeholder values from the template.

```bash
./run.sh install
```
Runs the dashboard as a background service that starts at every login, at
**http://127.0.0.1:8765**. The first sync backfills 13 weeks (about 250 API calls,
3-4 minutes); after that a sync takes seconds.

## Commands

| Command | What it does |
|---|---|
| `./run.sh install` | Run in the background from now on, starting at login |
| `./run.sh status` | Is it installed and responding? |
| `./run.sh uninstall` | Stop it and remove the login item |
| `./run.sh` | Run the server in this terminal instead (Ctrl+C to stop) |
| `./run.sh sync` | Sync and rebuild once, without the server |
| `./run.sh build` | Rebuild from cached data only, no API calls |
| `./run.sh test` | Run the test suite |
| `./run.sh login` | Re-authenticate with Garmin |

## Configuration

One file, **`private/config.toml`**, holds every personal value: the app settings
(port, auto-sync), every threshold the metrics use (HR caps, bike limits, sleep
targets, HRV band, ACWR band, hard-session timings, gym targets, FTP), the alert
texts, and the season plan (week numbering, races, phases, weekly targets).
`config.example.toml` documents each key; [docs/configuration.md](docs/configuration.md)
gives the overview. Weekly targets and the FTP can also be updated from the
dashboard's **Coach** popup.

## Data and privacy

- Everything runs on your Mac. The server binds to `127.0.0.1` and serves only the
  three pages; `data/` is never reachable over HTTP.
- `garmin_sync.py` writes **only whitelisted fields** to `data/cache/`. Garmin's
  training-status label, VO2 max, race predictor, body battery, performance
  condition, LT heart rate and FTP never reach disk.
- `tests/test_guard.py` builds every page from a cache seeded with all of those and
  fails if any label or value becomes visible, tooltips included.
- `data/`, the generated `*.html` pages and `private/` (config, notes, backups) are
  gitignored. The repo itself holds no personal values; a test checks tracked files
  against the local config.

## Garmin quirks handled

| Quirk | Handling |
|---|---|
| Lactate-threshold speed arrives as m/s ÷ 10 | multiplied by 10 in `model.py` |
| Station weather is Fahrenheit with no unit field | converted; flagged if the dew point exceeds the temperature |
| Watch "Intervals" runs label every lap `INTERVAL` | reps read from typed splits, not lap DTOs |
| Lap-button artefacts (a 27 m "rep") | ACTIVE laps under 100 m or 30 s ignored |
| Lap timestamps are GMT | activity dates come from local start time |
| Races are rarely tagged as races | a race date from the config also counts |

## Layout

```
garmin_sync.py       incremental, whitelisted pull -> data/cache/
model.py             cache -> typed objects; unit quirks fixed here
metrics.py           every spec rule as a pure function
charts.py            inline-SVG chart primitives
build_dashboard.py   renders dashboard.html, season.html, health.html
settings.py          reads private/config.toml (defaults = config.example.toml)
serve.py             localhost server for the pages, the Sync button and the coach update
coach.py             what the plan is missing, the coach prompt, merging the answer
run.sh               all commands, including the launchd login agent
VERSION, CHANGELOG.md  release version and per-release changes (docs/releasing.md)
tests/               unittest suite on synthetic data
```

## Disclaimer and license

Not affiliated with or endorsed by Garmin. Data comes through
[`garminconnect`](https://github.com/cyberjunky/python-garminconnect), an unofficial
client for Garmin Connect; use it with your own account and at your own risk. The
dashboard is a training aid, not medical advice. MIT licensed, see [LICENSE](LICENSE).

