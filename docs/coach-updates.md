# Coach updates (in-app)

Re-prompting the coach for information Garmin can't provide is routine, so the
dashboard generates the prompt and takes the answer back directly.

## What the coach supplies

Garmin has no access to the plan. The coach supplies the following; everything else
comes from Garmin or from `private/config.toml`:

| Value | Stored in | Used for |
|---|---|---|
| Weekly run-km target | `private/config.toml` `[[week]] run_km` | Run km cell, trend, season chart |
| Weekly threshold-km band | `private/config.toml` `[[week]] threshold` | Hero metric, trend |
| Recovery week flag | `private/config.toml` `[[week]] recovery` | Running-share rule off |
| Bike FTP (own test only) | `private/config.toml` `[bike] ftp_w` | Bike threshold minutes |

Races, phases, caps and alert wording are not taken from the popup. They change rarely
and need judgement, so Claude edits them by hand.

## Flow

1. Each build calls `coach.needs()`, which looks at the current week plus the next four
   (`AHEAD_WEEKS`) and lists the ones missing run km or a threshold band. It also flags
   a missing FTP. `coach.prompt()` writes a prompt containing the **current** values
   for those weeks, so the coach can also correct values that already exist.
2. The page shows a **Coach** button with a badge for the number of missing values.
   The popup opens automatically once a day (per set of gaps, remembered in
   `localStorage`) while this week or next is missing something. A missing FTP alone
   doesn't open it.
3. The answer is pasted into the popup. **Check** calls `POST /api/coach/preview`.
   **Apply** calls `POST /api/coach/apply`, which writes the files and runs
   `build_dashboard.py`; the page then reloads.

## Safety rules

- `serve.py` only relays: it runs `coach.py preview|apply` as a subprocess, so the
  long-running server never holds stale code that edits config.
- Same guards as Sync: loopback `Host` only (421 otherwise), `X-Sync: 1` header
  (blocks cross-site requests), body at most 64 KB (413). Error responses close the
  connection.
- Parsing is strict: exactly one TOML block (or the whole text if none is fenced).
  Only `[[week]]` with `n` 1–104, `run_km` 0–250, `threshold` [lo, hi] with
  0 ≤ lo ≤ hi ≤ 60, `recovery` true/false, and `[bike] ftp_w` 50–600 are accepted.
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
