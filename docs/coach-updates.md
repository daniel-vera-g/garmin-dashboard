# Coach updates (in-app)

Re-prompting the coach for information Garmin can't provide is routine, so the
dashboard generates the prompt and takes the answer back directly.

## What the coach supplies

Garmin has no access to the plan or the coach's judgement. One prompt asks for all of it,
showing the dashboard's current values so the coach can confirm or correct them:

| Prompt section | Answer as | Updates |
|---|---|---|
| A. Weekly targets, this week + `[coach] ahead_weeks` | `[[week]]` n, run_km, threshold, recovery | the weekly cells, hero, trends |
| B. Every session of the last 14 days with the dashboard's verdict and the reason | `[[session]]` date, kind, accept, pace band, note | reclassify a planned change, accept a verdict, judge speed reps on pace |
| C. Rules, caps and tolerances with current values | `[threshold]`, `[easy]`, `[runs]`, `[bike]` … keys | any coach-set setting, incl. `ftp_w` and `[alerts]` texts |
| D. Races ahead | `[[race]]` date, name, short, priority, sport | season page, race detection |
| E. Everything else | NOTES after the block | shown before applying, archived |

App, sync and display settings (`[app]`, `[sync]`, `[coach]`, `[health]`), phases and gym
targets are not taken from an answer; edit them in the config.

## Flow

1. Each build calls `coach.needs()`, which looks at the current week plus the next four
   (`AHEAD_WEEKS`) and lists the ones missing run km or a threshold band. It also flags
   a missing FTP. `coach.prompt()` writes a prompt containing the **current** values
   for those weeks, so the coach can also correct values that already exist.
2. The page shows a **Coach** button with a badge for the number of missing values.
   The popup opens automatically once a day (per set of gaps, remembered in
   `localStorage`) while this week or next is missing something. A missing FTP alone
   doesn't open it.
3. The answer is pasted into the popup. **Check** calls `POST /api/coach/preview` and
   lists every change, everything not taken over, and the coach's notes. **Apply**
   calls `POST /api/coach/apply`, which writes the config, archives the full answer in
   `private/notes/coach/<timestamp>.md` and runs `build_dashboard.py`; the page reloads.

## Safety rules

- `serve.py` only relays: it runs `coach.py preview|apply` as a subprocess, so the
  long-running server never holds stale code that edits config.
- Same guards as Sync: loopback `Host` only (421 otherwise), `X-Sync: 1` header
  (blocks cross-site requests), body at most 64 KB (413). Error responses close the
  connection.
- Parsing is strict: exactly one TOML block (or the whole text if none is fenced).
  `[[week]]`: `n` 1–104, `run_km` 0–250, `threshold` [lo, hi] with 0 ≤ lo ≤ hi ≤ 60,
  `recovery` true/false. `[[session]]`: a date, `kind` from a fixed list, `accept`,
  `pace` as "m:ss-m:ss", text up to 200 characters. `[[race]]`: date and name, priority A/B/C,
  sport run/bike. Settings: only keys that exist in `settings.DEFAULTS` for a
  coach-set section, with the default's type (numbers ≥ 0; shares and tolerances ≤ 1;
  bedtime as HH:MM; texts up to 160 characters); `ftp_w` 50–600 W.
  Everything else is listed under "Not taken over"; nothing is guessed.
  `# threshold = MISSING` comments are ignored by TOML itself.
- Files are edited **as text**: existing `[[week]]` blocks get the key replaced or
  inserted after `n =`; new weeks are inserted in order with a `# Mon d Mon` label;
  every written line carries `# coach <date>`. Other comments and layout survive.
  The result must parse as TOML before it is written.
- A backup of each changed file goes to `private/backups/<name>-<timestamp>.toml`
  (gitignored with the rest of `private/`).
- Values in git: none. `private/config.toml` is gitignored, so an update changes the
  local config and the rebuilt pages, never a commit.

## Verified

Popup opened on its own (upcoming weeks missing threshold bands, FTP missing). Check on a test
answer listed the change and the ignored key; Apply wrote the file, rebuilt, and the
reloaded prompt showed the new value. The test value was then removed by restoring
the backup. Layout checked at 375 px. Tests: `tests/test_coach.py` (8) plus endpoint
guards in `tests/test_serve.py`.

## Not covered (by design, for now)

- Removing a value (the coach writes MISSING → nothing changes). Ask Claude.
- Races and phases. Ask Claude, or edit `config`.
- Rules (e.g. which readiness level closes a hard session) — see
  [open-items.md](open-items.md).
