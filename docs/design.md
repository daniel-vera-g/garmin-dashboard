# Design decisions

Why the dashboard works the way it does. "Owner" = decided by the person the dashboard
was built for; "Default" = chosen during the build and stated. Personal values (caps,
targets, dates) are never part of a decision here - they live in the config.

## Hosting, privacy, operation

| Decision | By | Why |
|---|---|---|
| Local only — data never leaves the machine; the server binds to loopback and refuses anything else | Owner | Health data is sensitive |
| Manual refresh at first; later a Sync button + auto-sync after 6 h | Owner | Simplest option first |
| Background service at login (launchd), localhost-only | Owner | "Very small process, autostart" |
| No personal information in git; every personal value is configuration in the gitignored `private/config.toml` | Owner | The repo is public |
| Develop v4 in a worktree; live dashboard stays on v1 until merge | Default | Avoid breaking the daily dashboard |
| Delete the old v1 cache at merge | Default (stated, no objection) | Contained banned metrics; v1 can re-sync |

## Scope and content (spec v4.0)

| Decision | By | Why |
|---|---|---|
| A written training-dashboard spec (v4.0; not published) is the source of truth | Owner | — |
| Never-shown list enforced (VO2max, race predictor, training status, endurance/hill score, performance condition, body battery, LT HR, Garmin FTP) incl. tooltips; banned fields never stored | Spec + default | "A metric on screen gets optimised" |
| Keep resting HR, sleep stages, HRV-vs-Garmin-baseline on a separate Health page | Owner | Didn't want to lose them |
| Bike km card, "Other" card, load-by-sport chart, monthly load balance dropped | Default | Owner was asked whether to keep any; **[uncertain]** no answer recorded |
| Season plan values come from the coaching session (in the config) | Owner | Not available from Garmin |
| Execution rate not built (needs workouts on the Garmin calendar; none existed) | Default per spec's own condition | — |

## Metric definitions

| Decision | By | Why |
|---|---|---|
| Sub-T vs speed reps by pace against Garmin LT pace | Owner | Automatic |
| Refined to **session-level** (median rep > 8 s/km faster than LT = speed session) | Default, explained | Per-rep rule hid cap breaches |
| Reps from typed splits; ACTIVE laps < 100 m or < 30 s ignored | Default | Real-data quirks |
| "Under the cap" = at or below | Default | Spec ambiguous |
| Hilly ride = ≥ 10 m/km; flat run for pace-at-HR = ≤ 5 m/km | Default | Spec silent |
| Long run = longest unstructured run ≥ 60 min | Default | Spec silent |
| ACWR from Garmin's own acute/chronic load, not a local EWMA | Default | Local EWMA drifted up to 0.14 |
| Readiness/recovery use the **latest** reading of the day, not the morning one | Owner (overrode spec) | Dashboard contradicted the Garmin app |
| Weekly shortfalls judged at week end / when unreachable; exceeded caps flag at once | Default | Fair mid-week judgement |
| Pace-at-HR excludes runs within 24 h of a quality session | Default (from spec v1.0), flagged to owner | One fatigued run flipped the trend |
| Run share needs ≥ 25 km week volume before it is judged | Default | Noise early in the week |

## Display

| Decision | By |
|---|---|
| Dot = verdict: green on target, amber drifting, red act now, grey **no data only**; no dot when nothing to judge; progress bar for counts in progress | Owner feedback |
| Weekly card grouped Running · Bike · Sleep · Load & gym | Owner feedback |
| The coloured number must be the judged number (easy-run HR shows both rules ✓/✗) | Owner feedback |
| Five hard-session checks always visible; no secondary "now" line | Owner |
| Alerts collapsible, closed by default, state remembered | Owner |
| Season chart: plan as outline bars, phases in their own strip | Owner feedback |

## Review fixes

| Decision | Why |
|---|---|
| A sub-T session without HR starts the 24 h window (not 48 h) | Hard either way; cap can't be checked. Asked the coach |
| Recovery is judged on hours **left now** (reading counted down) | Cell, alert and check contradicted each other |
| Easy runs: the week fails if **any** run fails; pooled numbers shown alongside | One verdict source for cell and alerts. Asked the coach |
| Last week's missed targets shown Mon–Tue, labelled, targets only | Monday duplicates; session/day alerts are stale by then |
| Pre-season weeks have no number | All read "W1" |
| New alerts: HRV out of range, Garmin sleep history moderate/poor, solid nights or gym out of reach | Every amber/red cell needs an explanation. **Action lines are placeholders** — asked the coach |
| Sleep/HRV/activity dates are "covered" once fetched after the re-pull window; load final once fetched 2+ days after the day | Gap repair without re-fetching everything |
| Login agent restarts itself when `serve.py` changes (`DASHBOARD_RESTART_ON_CHANGE=1`) | Old code stayed in memory for days |

## Review fixes and the Coach popup

| Decision | By | Why |
|---|---|---|
| Band tolerance only for single-figure bands; ranges judged as written | Default (review #2) | A green number outside its band broke the "coloured number is judged" rule |
| Activity list re-pulled whole once a day, recent window otherwise | Default (review #4) | Garmin edits to older activities must arrive; still 1 call |
| Restart-on-change switched by env var, not flag | Default (review #1) | A flag crash-loops older versions after rollback |
| In-app **Coach** popup: generated prompt, paste answer, preview, apply | Owner request | Re-prompting the coach is routine and the plan isn't in Garmin |
| Popup only takes weekly run km / threshold / recovery and FTP; races, phases, caps, rules stay manual | Default | They change rarely and need judgement |
| Popup opens itself once a day while this or next week lacks a target; FTP alone doesn't open it | Default | Nudge without nagging |
| Coach answers merged as text with `# coach <date>` and a backup per change | Default | Keep the file readable and reversible |

## Coach verdicts, tolerances, releases

| Decision | By | Why |
|---|---|---|
| The coach can reclassify a session or accept its verdict (`[[session]]`) | Owner | Planned changes (a fast session instead of sub-T) shouldn't read as failures |
| A reclassified or accepted hard session still starts the 48 h window | Default | It was still a hard session for the body |
| Cap tolerance: reps within `cap_tolerance_bpm` count as within cap, shown amber with their own alert | Owner | A rep 2–3 bpm over the cap isn't the same as 10 over |
| `threshold_tolerance` widens the band both ways; 0 keeps "judged as written" | Owner | Same idea for weekly targets |
| One prompt asks for everything; the answer may set any coach-set key, type-checked | Owner | The coach knows more than the dashboard asks for |
| Alerts can be marked viewed, per browser, keyed by their text | Owner | Acknowledge without hiding a new problem |
| Semantic-versioned releases with a changelog; version in the header | Owner | Track what changed and check features |

## Three-level bands (v1.2.0)

| Decision | By | Why |
|---|---|---|
| Green / amber / red around caps, weekly targets, easy runs and sleep; amber counts as executed, red doesn't | Owner (coach's model) | Close misses aren't failures |
| A session's colour is its worst rep; threshold km counts green and amber reps, over-cap km only red ones | Coach | |
| Amber zones default to "not set", which keeps the old behaviour (outside green = amber) | Default | Existing configs keep working |
| Speed sessions with a planned pace band are judged on pace, not HR | Coach | Track reps are pace work |
| A long run over its cap is red | Coach | |
| Pre-quality gate: both nights at least amber pass; one red night passes at readiness ≥ a configured level | Coach | |
| Structured runs also fetch the watch's laps; an ACTIVE step holding several laps is split into reps and recoveries by pace | Default | An extra rep inside one workout step merged two reps and the jog |
| The coach prompt lists every session of the last two weeks and the exact key names | Default | The coach thought sessions were missing and invented key names |

