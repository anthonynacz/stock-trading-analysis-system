# SUMMARY
Name it Momentum Radar: stocks are "on the radar" or "dropped off", it covers moves both up and down, and the nav label is "📡 Radar". The design is built and tested as runnable prototypes in scratch; the repo and data worktree were not touched.

- **Runtime:** a single long-running GitHub Actions job scans every 5 minutes, 25 s after each wall-clock boundary, from 07:00 to 20:00 ET (17:00 on half days). Watchdog crons run every 30 min at minutes :07 and :37, plus a starter cron, all under one concurrency group with `cancel-in-progress: false`. While a loop runs, one successor run always waits in the queue and starts within seconds when the loop retires (after 330 min) or crashes.
- **Tick isolation:** each scan runs as a separate process with a 270 s hard timeout, and each push has a 60 s budget, so a hung data source or a GitHub outage never stalls the 5-minute cadence.
- **Git:** per-tick commits go to the `data` branch only, which avoids the Pages build limit on `main`. A rejected push is fetched and rebased with the scanner's version winning conflicts; if housekeeping has squashed the history, the scanner resyncs instead of crashing.
- **Calendar:** NYSE holidays and early closes are hard-coded through 2028 and match exchange_calendars 4.13.2 exactly. A cheap stdlib gate step turns off-hours and holiday triggers into roughly 15 s no-ops.
- **Housekeeping:** runs nightly at 06:23 UTC in its own concurrency group. It checks the calendar and confirms no scanner run is active before touching anything, backs up and trims tables only when thresholds are hit, applies 92-day retention, and squashes the data history on Saturdays.
- **Measured finding that changes the page design:** anonymous GitHub API requests answered 304 still cost quota (remaining went 51, 50, 49). So a page without a token calls the API once per scan, timed to when that scan's commit usually lands. With the Alerts-page token it checks every 30 s, since answered-304 requests are free with auth per GitHub's docs (not tested without a token). Near the budget floor or when rate-limited, it falls back to the delayed CDN copy of the branch. The page fetches a single `feed.json` per change.
- **Page:** `radar.html` is dependency-free and mobile-first. Features:
  - live members table with a price path since entry
  - recently dropped-off list with exit reasons
  - market session and freshness bar, and a stale warning after 12 min without a scan while the scanner should be running
  - optional in-page notifications
- **Linking:** an idempotent retrofit adds a "📡 Radar" pill beside "🔔 Alerts" on all 147 report pages that have the Alerts pill, and on the template.
- **Tests:** 10 automated tests pass: calendar vs exchange_calendars, session gate, cron coverage for all 569 sessions through 2028 across DST, git push/rebase/squash cases on a local bare repo, and live loop runs including slow and crashing ticks. The page was checked in the browser at 320, 375, 860 and 960 px.

# RISKS
- GitHub Actions terms of use: the Actions terms rule out work unrelated to building, testing or publishing the repo's software. A 13-hour-a-day scraping loop is heavier than usual scheduled data scraping and could be throttled or disabled. Mitigation: keep the 07:00-20:00 ET window rather than 04:00, and make every off-window run a 15-second no-op.
- Anonymous API requests answered 304 still used the 60/hour quota (measured: 51, 50, 49). The page is designed around this, but several devices behind one IP (home network, office NAT) share that budget. Heavy use therefore drops readers to the delayed CDN copy, up to about 5 minutes behind, unless they save a token on the Alerts page. That the requests are free with a token comes from GitHub's docs; it was not tested, because no token was used.
- raw.githubusercontent.com has unpublished limits for unauthenticated requests, which were tightened in 2025. The design needs only one raw fetch per client per change, and SHA-pinned files can be cached by the browser, but this was not load-tested.
- The watchdog design relies on GitHub's documented concurrency behaviour: one pending run per group, and a newer pending run cancels the older one. If GitHub changes this, or drops triggers for more than 30 minutes, handover gaps grow. The page then shows its stale warning. Scheduled-trigger delays longer than 27 minutes would make the EDT pre-market start late.
- Housekeeping pushes to the archive through a git worktree, relying on actions/checkout v4 storing credentials in the shared repo config. Newer checkout versions may store credentials in a way worktrees don't pick up. Pin v4, or verify on the first housekeeping run.
- GITHUB_TOKEN with contents: write can push to main as well as data, so a compromised dependency could deface the Pages site. Mitigations: hash-pinned lock with --require-hashes, actions pinned to SHAs, token variables removed from the tick process (defence in depth only), and an optional main-branch ruleset that bypasses only the Admin role.
- Yahoo data from shared GitHub runner IP addresses may be throttled more often, or arrive later than the 25-second scan offset assumes. The runtime marks such scans degraded and never evicts on missing data, but coverage should be measured in the first live week. That is the signals agent's call, along with any fallback source.
- The data branch gets about 157 commits per trading day. The weekly squash force-push rewrites history, and GitHub's cleanup of unreachable objects is not immediate. SHA-pinned URLs to squashed-away commits may stop working; the page only uses the current tip.
- The NYSE calendar is hard-coded only through 2028, and unscheduled closures (such as a national day of mourning) need a manual edit. Beyond 2028 it falls back to treating weekdays as sessions, and housekeeping fails its run 45 days before coverage ends so GitHub's failure email serves as the reminder.
- The housekeeping guard sees only in-progress scanner runs, not queued ones, so it relies mainly on the calendar check. A scanner run started manually by dispatch in the middle of the night could collide with a squash. force-with-lease blocks the push in that case, and the scanner resyncs rather than crashing.
- Scheduled workflows in public repos are disabled after 60 days without repository activity. This is covered today by the daily report commits to main.
- Browser notifications only work while the page is open. Android Chrome needs a service worker, so it falls back to the in-page toast, and iOS supports notifications only for installed web apps. Radar entries deliberately send no email.
- Interface dependencies on other agents: the tick must follow the command-line and last-line JSON contract, write latest.json with the field names and exit-reason codes above, and never write feed.json. The data-model agent owns the table layout, the backup format and thresholds, and radar.housekeeping. The page shows any unknown exit reason through exit_detail.

# Momentum Radar: runtime (GitHub Actions) and page (radar.html)

Role: platform and frontend. This covers the runtime and the page. It is a design only: nothing was changed in the repo or the data worktree. Every artifact is a runnable prototype under
`<design-scratchpad>/radar_design/runtime/` (called `RT/` below).

---

## 0. Findings that change the shared assumptions

1. **Anonymous 304s are NOT free (measured 2026-09-27, from the browser, on this repo).**
   - Three calls to `GET /repos/anthonynacz/daily-market-analysis/git/ref/heads/data`: the first returned 200; the next two sent `If-None-Match` and got 304.
   - `x-ratelimit-remaining` still went 51, 50, 49.
   - GitHub's docs exempt 304s only for requests with a valid Authorization header.
   - Consequence: a page without a token that polls every 60 s would spend all 60 requests/hour per IP. The page design in §3.2 is built around this.
   - Also confirmed working from the browser: CORS on the API with a readable `ETag`; the ETag is weak (`W/"…"`) and must be sent back as-is. A SHA-pinned raw URL returned 200 with the fresh content.
2. **NYSE calendar:** a hard-coded table for 2026-2028 is identical to exchange_calendars 4.13.2 (XNYS) for every day. Early closes: 2026-11-27, 2026-12-24, 2027-11-26, 2028-07-03, 2028-11-24.
3. **Cron coverage:** checked by simulation for all 569 sessions from 2026-09-28 to 2028-12-29, in both EDT and EST.
   - A starter trigger lands 27-87 min before every scan window.
   - Watchdog triggers are never more than 35 min apart inside the window.
   - The housekeeping cron never falls inside a (padded) scan window.

---

## 1. Name

| Candidate | Up and down? | Clear to non-traders | Natural add/remove verbs | Clash with existing | Verdict |
|---|---|---|---|---|---|
| Fast Track (user's working name) | neutral | no, sounds like a queue or process | "fast-tracked"? | none | no |
| **Momentum Radar** | yes | yes | "on the radar" / "dropped off the radar" | none; icon 📡 vs 🔔 Alerts | **recommended** |
| Stocks in Play | yes | trader jargon, implies a catalyst | "in play" / "out of play" | none | runner-up |
| Movers / Fast Movers | yes | yes | weak | "day's biggest movers" already names the alert universe | no |
| Hot List | leans up | vague | weak | none | no |
| Breakout Board | up only | yes | weak | none | no (misses downside) |
| Velocity Watch | yes | abstract | weak | none | no |

Vocabulary used everywhere:
- A **member** is "on the radar"; it **enters** and **drops off**.
- The nav label is "📡 Radar"; the page title is "Momentum Radar".
- File and branch paths use `radar/`.

---

## 2. Runtime

### 2.1 Loop job, not a cron job per tick

| | Cron job per tick (`*/5`) | **Long loop job + watchdog cron (chosen)** |
|---|---|---|
| Cadence | GitHub delays scheduled events 5-15+ min under load and sometimes drops them, so a steady 5-minute cadence is impossible | wall-clock aligned to within a second (scan at every :x0:25 / :x5:25) |
| Setup overhead | about 30-60 s per tick (checkout, Python, pip) | once per 5.5 h |
| State | re-read every tick | same (read from files), plus warm process counters |
| Cost | free (public repo) | free (public repo); job cap 6 h, so it hands over every 5.5 h |

### 2.2 Triggers and concurrency: "always one successor queued"

```
concurrency: { group: radar-scanner, cancel-in-progress: false }
```
GitHub rules this relies on:
- A trigger that arrives while a run is in progress becomes **pending** (no runner allocated, costs nothing).
- A newer pending run cancels the older pending run.

So while a loop runs, exactly one successor always waits. When the loop retires or dies, that successor starts within seconds. The cron triggers are only a watchdog and never interrupt a running loop.

| Cron (UTC) | Purpose | EDT (UTC-4) | EST (UTC-5) |
|---|---|---|---|
| `33 10 * * 1-5` | starter | 06:33, sleeps 27 min until 07:00 | 05:33, sleeps 87 min (allowance 90) |
| `7,37 11-23 * * 1-5` | watchdog / queue refill | 07:07 … 19:37 | 06:07 (sleeps) … 18:37 |
| `7,37 0 * * 2-6` | watchdog, EST evenings | 20:07/20:37: gate no-op | 19:07/19:37 post-market |

- Minutes :07, :33 and :37 avoid GitHub's top-of-hour peak.
- About 29 triggers per trading day: about 25 end as "cancelled pending" runs and 2-3 as ~15 s gate no-ops. Housekeeping prunes these after 3 days.
- On holidays every trigger is a no-op.
- The default window is 07:00-20:00 ET (config `scan_start_et`). For the full 04:00 pre-market, set `"04:00"` and move the starter to `33 6 * * 1-5` and the watchdog hours to `7-23`. Not recommended: ToS risk, and liquidity is thin that early.

Typical EDT day:
```
06:33 ET  starter -> run A: gate "pre-start" -> loop sleeps
07:00:25  first scan (tick 07:00), then every 5 min
07:07…    each watchdog trigger queues run B (the newest replaces the older)
~12:01    A retires after the 12:00 tick (330 min after it started); B starts at once, first scan 12:05:25
~17:31    B retires; C runs to the final tick 20:00:25 (covers the 19:55-20:00 bar) and exits
20:07/20:37  gate: closed -> no-op
```
**On a crash**, the queued successor starts immediately. If a crash lands in the few minutes after a handover, before the next watchdog trigger, recovery takes at most 30 min plus GitHub's delay, and the page shows its stale warning.

### 2.3 The loop (`RT/radar/loop.py`, stdlib only)
- **Scan times:** each scan runs at the next 5-minute boundary + `tick_offset_seconds` (25), so Yahoo has published the closed bar. Tick ids are the boundary times within [window_start, window_end]; the first is 07:00 and the last is 20:00, flagged `final`.
- **Isolated ticks:** each tick runs `python -m radar.tick` as a **subprocess** with `timeout=270 s`. A hang or crash becomes status `timeout` or `error`; members stay unchanged and the loop carries on. Memory is released every tick.
- **No overlap, no catch-up burst:** after a slow tick the next boundary is computed from the current time, and missed boundaries are counted in `ticks_skipped`.
- **Catch-up final tick:** if an overrun jumps past the window end, the final tick still runs immediately, so session-end processing always happens (tested).
- **Retire:** the loop exits when the next scan would start after `started + 330 min`; the job timeout is 350.
- **Cancel:** SIGTERM/SIGINT sets a stop flag (sleeps are 5 s naps); an `always()` step flushes unpushed commits.
- **Token scrubbing:** the tick subprocess environment has `GITHUB_TOKEN`, `GH_TOKEN` and `ACTIONS_*` removed. This is defence in depth, not isolation.
- **Files:** after each tick the loop writes **`radar/feed.json` = {schema, health, latest}**, the single file the page reads, then commits and pushes.

### 2.4 Tick contract (for the signals and data-model agents)
- **CLI:** `python -m radar.tick --data-dir <_data> --tick-id <ISO Z boundary> --phase pre|regular|post [--final]`.
- **Time:** must finish in under 270 s; use per-request timeouts of 20 s or less.
- **Writes:** only under `<data-dir>/radar/`, atomically (write a temp file, then `os.replace`). It writes `radar/latest.json` (the snapshot below) plus its own tables. It never writes `feed.json` or anything under `_meta`.
- **Result line:** the last stdout line is JSON: `{"status":"ok|degraded|no_data|error","message":"","universe":318,"coverage":0.99,"members":6,"entered":["HALX"],"exited":[],"source":{"name":"yahoo","status":"ok","last_ok_at":"…","consecutive_failures":0},"tables":{"<table>":{"rows":…,"bytes":…,"load_ms":…}}}`. Exit code 0 unless there is a bug.
- **Missing data never evicts a member.** It sets `stale_ticks` and state `paused` (a possible halt). Eviction with reason `data_gap` happens only after a long gap while the source is otherwise healthy (for example 12 ticks).
- **Snapshot fields the page uses** (`latest.json`):
```json
{"schema":1,"generated_at":"…Z","tick_id":"2026-09-28T14:35:00Z","phase":"regular",
 "members":[{"ticker":"NOVX","name":"…","direction":"up|down","state":"new|racing|cooling|paused",
   "entered_at":"…Z","entry_price":12.4,"last_price":13.12,"last_bar_at":"…Z",
   "move_since_entry_pct":5.81,"peak_since_entry_pct":6.94,"chg_5m_pct":1.1,"chg_15m_pct":2.8,"chg_30m_pct":4.0,
   "chg_day_pct":5.9,"rvol":6.8,"vwap_dist_pct":4.9,"score":91,"reasons":["…"],"stale_ticks":0,
   "spark":{"t0":"…Z","step_s":300,"entry_i":5,"p":[…]}}],
 "recent_exits":[{"ticker":"PXLR","direction":"down","entered_at":"…","exited_at":"…","minutes_on_radar":45,
   "move_since_entry_pct":-3.1,"exit_reason":"momentum_faded|reversal|stalled|vwap_lost|data_gap|session_end|max_duration|capacity","exit_detail":"…"}]}
```
- **Sparkline data:** `spark.p` holds tick closes from up to 6 ticks before entry through now, capped at 60 points; `entry_i` marks the entry point.
- **Exits:** keep the last about 2 h, or the current session. The page shows up to 30.
- **Size:** 6 members come to about 4.9 KB, and `feed.json` to about 7 KB.

### 2.5 Git on `data` (`RT/radar/gitsync.py`)
- **Checkout:** `actions/checkout@v4` with `ref: data`, `path: _data`, `fetch-depth: 1`. The code checkout (main) uses `persist-credentials: false`.
- **Identity and commit:** github-actions[bot]; one commit per tick, message like `radar 14:35Z ok · 6 on radar (+1/-0)`. Pushes from GITHUB_TOKEN trigger no workflows, and `data` is not the Pages source, so there are no Pages builds.
- **Push (60 s budget per tick, 150 s for the final flush):**
  - OK: done.
  - Rejected: fetch, then `rebase -X theirs` (the scanner's commits win on conflicts), then retry.
  - No merge base, meaning housekeeping squashed the branch: hard-reset to the remote. That tick's commit is dropped and the next tick rewrites everything.
  - Network or server error: back off; commits stay local (`push.backlog`) and are retried next tick.
- **During the window the loop is the only writer**, so rejections should essentially never happen.
- **Volume:** about 157 commits per trading day. A weekly squash (§2.8) keeps the branch to about 800 commits.

### 2.6 Python, caching, supply chain
- **Pinned lock:** `radar/requirements.lock` is generated with `uv pip compile requirements.in --generate-hashes --python-version 3.12 --python-platform x86_64-manylinux_2_28`. Verified: it resolves 23 packages (yfinance 1.2.0, pandas 3.0.1, numpy 1.26.4, curl-cffi 0.13.0, …).
- **Install:** `pip install --require-hashes`, cached by `actions/setup-python@v5` (`cache: pip`, keyed on the lock).
- **Actions only from GitHub** (checkout, setup-python); pin them to full commit SHAs when implementing.
- **Runner:** `ubuntu-24.04` is pinned so the gate's system `python3` is 3.12 with system tzdata for `zoneinfo`.

### 2.7 Calendar and session detection (`RT/radar/calendar_nyse.py`, stdlib)
- **Holidays:** a hard-coded `HOLIDAYS`, `EARLY_CLOSES` and `EXTRA_CLOSURES` (hand-edited for unscheduled closures), covering through 2028-12-31. exchange_calendars is not a runtime dependency: the gate must run before pip.
- **Times:** built with `datetime.combine(day, time, tzinfo=ZoneInfo("America/New_York"))` and converted to UTC, so DST is correct.
  - Normal day: pre-market 04:00, regular 09:30-16:00, after-hours to 20:00.
  - Half day: close 13:00, after-hours to 17:00.
- **Phase:** `phase_at()` returns pre / regular / post / closed.
- **Past the covered date** the calendar fails open (weekdays count as sessions). `radar.calendar_check` fails the housekeeping run 45 days before coverage ends, so GitHub's failed-run email reminds the owner.
- **Gate (`RT/radar/gate.py`):**
  - It writes `run`, `reason`, `window_start`, `window_end` and `mode` to `$GITHUB_OUTPUT`.
  - The scanner runs when `window_start - 90 min <= now < window_end`.
  - With `--housekeeping` it reports `ok=false` inside any scan window padded by 70 min before and 10 min after.

### 2.8 Housekeeping coordination
- **Concurrency:** its own group, `radar-housekeeping`. Sharing the scanner's group would let housekeeping and the queued scanner successor cancel each other.
- **Safety instead of shared concurrency:**
  1. The calendar guard (it runs daily at 06:23 UTC = 02:23 EDT / 01:23 EST, outside every window; verified through 2028).
  2. An API check that no `radar-scan.yml` run is `in_progress` (needs `actions: read`).
  3. Squash uses `push --force-with-lease=refs/heads/data:<sha it read>`.
  4. The scanner resyncs if it ever meets a rewritten history (tested).
- **Performance-triggered backups:**
  - Each tick reports `tables.{rows, bytes, load_ms}`.
  - Housekeeping re-measures and backs up and trims only when a threshold is hit, or on `force_backup`.
  - Proposed defaults, owned by the data-model agent: hot file > 2 MB, or > 20k rows, or p50 load+lookup > 150 ms on the runner.
- **Order of operations:** write the backup, verify it by read-back and checksum, then trim the hot table.
- **Backup store (recommendation; the data-model agent owns the format):** an orphan branch `data-archive` with the single writer being housekeeping, one file per month and table (`archive/<table>/<YYYY-MM>.jsonl.gz`), readable through raw URLs with CORS.
  - Retention: delete months that ended more than 92 days ago, then rewrite `data-archive` as one orphan commit so deleted data actually leaves the history.
  - Alternative: release assets per month, which give true deletion without history but add tags and releases to the repo page.
- **Squash of `data`:** Saturdays (`--squash auto`) or on demand. Page links are SHA-pinned only to the current tip, so they are unaffected.
- **Run pruning:** a separate job with only `actions: write` deletes cancelled or no-op scanner runs older than 3 days.

### 2.9 Failure handling

| Failure | Behaviour | What the page shows |
|---|---|---|
| Yahoo slow or down | tick → `degraded` / `no_data`; members held and marked `paused` per symbol; no evictions | amber: "Last scan: data came back incomplete (62% …). Stocks already on the radar are held, not dropped" |
| A stock stops printing (halt) | state `paused`; `data_gap` exit only after a long gap | "NO DATA" pill; details show "No fresh bars for N scans (possible halt)" |
| Tick hangs or crashes | subprocess killed at 270 s → `timeout` / `error`; previous snapshot republished with new health | amber banner; health box shows the failure count |
| Tick overruns 5 min | next boundary computed from now; `ticks_skipped`++; final tick guaranteed | "Next ≈ …" and freshness still correct |
| Push fails | 60 s budget, rebase/resync, backlog kept, `always()` flush | stale after 12 min |
| Loop or runner dies | queued successor starts at once (or next watchdog ≤ 30 min) | red: "No new scan for N min while the market is open…" |
| Scheduled triggers delayed | starter 27-87 min early; the queue masks watchdog delays | "Waiting for today's first scan (from 07:00 ET)" for the first 15 min |
| Unscheduled closure | calendar says open, no bars → `no_data`, no evictions | amber banner |
| Calendar running out | housekeeping fails 45 days before (email) | none |

### 2.10 `feed.json` (the page contract; written by the loop)
```json
{"schema":1,
 "health":{"schema":1,"updated_at":"…Z","tick_id":"…Z","status":"ok|degraded|no_data|timeout|error","message":"",
   "phase":"regular","final":false,"next_tick_at":"…Z (next scan start, null after final)",
   "tick":{"duration_s":33.4,"universe":318,"coverage":0.99,"members":6,"entered":["HALX"],"exited":[]},
   "source":{"name":"yahoo","status":"ok","last_ok_at":"…","consecutive_failures":0},
   "loop":{"ticks_ok":43,"ticks_failed":0,"ticks_skipped":1,"consecutive_failures":0,"run_id":"…","run_attempt":"1",
           "code_sha":"abc1234","started_at":"…","retire_at":"…","window_start":"…","window_end":"…"},
   "push":{"status":"ok|resynced|failed|none","backlog":0,"detail":"","last_ok_at":"…"},
   "calendar":[{"date":"2026-09-28","pre_open":"…Z","open":"…Z","close":"…Z","post_close":"…Z","early_close":false,"scan_start":"…Z"} /* today + next 5 sessions */],
   "calendar_known_until":"2028-12-31"},
 "latest": { /* the tick's latest.json snapshot, or null */ }}
```
`push` is the outcome of the previous push, since a commit cannot report its own push.

### 2.11 Workflow YAML (full; `RT/workflows/`)

**`.github/workflows/radar-scan.yml`**
```yaml
name: Momentum Radar scanner

# One long-running job scans every 5 minutes (wall-clock aligned) through the scan window.
# Cron triggers are only a watchdog: thanks to the concurrency group, while a loop runs the
# newest trigger waits as its successor and starts the moment the loop retires or dies.
on:
  schedule:
    # All times UTC. Scan window = 07:00-20:00 America/New_York (17:00 on NYSE half days)
    #   EDT: 11:00-00:00 UTC    EST: 12:00-01:00 UTC
    # The ranges below cover both DST states; the gate turns every off-window, weekend-in-ET
    # or holiday trigger into a ~15 s no-op. Minutes 7/33/37 avoid GitHub's top-of-hour peak.
    - cron: '33 10 * * 1-5'       # starter: 06:33 EDT / 05:33 EST; the loop sleeps until 07:00 ET
    - cron: '7,37 11-23 * * 1-5'  # watchdog: keeps one successor queued behind the running loop
    - cron: '7,37 0 * * 2-6'      # watchdog for EST evenings (19:07 / 19:37 EST); no-op in EDT
  workflow_dispatch:
    inputs:
      mode:
        description: 'loop = normal scan window; once = one tick now, even if the market is closed'
        type: choice
        options: [loop, once]
        default: loop
      push:
        description: 'Push results to the data branch'
        type: boolean
        default: true

permissions:
  contents: write              # push to the 'data' branch; nothing else

concurrency:
  group: radar-scanner
  cancel-in-progress: false    # never interrupt the running loop; the newest trigger queues as successor

defaults:
  run:
    shell: bash

jobs:
  scan:
    runs-on: ubuntu-24.04
    timeout-minutes: 350       # hard stop (GitHub max is 360); the loop retires by itself at 330 min
    env:
      PYTHONUNBUFFERED: '1'
      PYTHONDONTWRITEBYTECODE: '1'
      DATA_DIR: ${{ github.workspace }}/_data
      NO_PUSH: ${{ github.event_name == 'workflow_dispatch' && !inputs.push }}
      MODE: ${{ inputs.mode || 'loop' }}
    steps:
      - name: Check out radar code (main)
        uses: actions/checkout@v4          # pin to a full commit SHA when implementing
        with:
          ref: main                        # newest code even for a run that waited in the queue
          persist-credentials: false       # this checkout never pushes
          sparse-checkout: radar

      - name: Session gate (system python, stdlib only)
        id: gate
        run: |
          args=()
          [ "$MODE" = "once" ] && args+=(--ignore-calendar)
          python3 -m radar.gate "${args[@]}" | tee -a "$GITHUB_OUTPUT"
          echo "code_sha=$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"

      - name: Check out data branch
        if: steps.gate.outputs.run == 'true'
        uses: actions/checkout@v4
        with:
          ref: data
          path: _data
          fetch-depth: 1                   # the loop only appends commits on top

      - name: Set up Python
        if: steps.gate.outputs.run == 'true'
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip
          cache-dependency-path: radar/requirements.lock

      - name: Install pinned dependencies
        if: steps.gate.outputs.run == 'true'
        run: python -m pip install --disable-pip-version-check --require-hashes -r radar/requirements.lock

      - name: Scan loop
        if: steps.gate.outputs.run == 'true'
        env:
          RADAR_CODE_SHA: ${{ steps.gate.outputs.code_sha }}
        run: |
          args=(--data-dir "$DATA_DIR")
          [ "$MODE" = "once" ] && args+=(--once)
          [ "$NO_PUSH" = "true" ] && args+=(--no-push)
          python -m radar.loop "${args[@]}"

      - name: Flush unpushed ticks
        if: always() && steps.gate.outputs.run == 'true' && env.NO_PUSH != 'true'
        run: python3 -m radar.gitsync --data-dir "$DATA_DIR" --final
```
Note on expressions: `inputs.push != false` would be wrong for scheduled runs. There `inputs.push` is null, and GitHub's loose comparison coerces null and false both to 0, so scheduled runs would never push. The `NO_PUSH` expression above avoids that.

**`.github/workflows/radar-housekeeping.yml`**
```yaml
name: Momentum Radar housekeeping

# Nightly, outside every scan window: measure the tables; when lookups got slow or files
# got big, back up the old rows to the 'data-archive' branch, verify, then trim the hot
# tables; purge backups older than the 3-month retention; on Saturdays squash the data
# branch history. Never runs while a scanner loop is active.
on:
  schedule:
    - cron: '23 6 * * *'        # 02:23 EDT / 01:23 EST, every day (weekend runs catch up on Friday's volume)
  workflow_dispatch:
    inputs:
      force_backup:
        description: 'Back up and trim now even if no threshold is reached'
        type: boolean
        default: false
      squash_history:
        description: 'Rewrite the data branch as a single commit (normally Saturdays only)'
        type: boolean
        default: false
      dry_run:
        description: 'Measure and report only; change nothing'
        type: boolean
        default: false

permissions: {}                  # granted per job below

concurrency:
  group: radar-housekeeping      # NOT the scanner's group: sharing it would let housekeeping and the
  cancel-in-progress: false      # scanner's queued successor cancel each other. Guarded by calendar + API instead.

defaults:
  run:
    shell: bash

jobs:
  housekeeping:
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    permissions:
      contents: write            # push trimmed tables to 'data' and backups to 'data-archive'
      actions: read              # confirm no scanner run is active
    env:
      PYTHONUNBUFFERED: '1'
      DATA_DIR: ${{ github.workspace }}/_data
      ARCHIVE_DIR: ${{ github.workspace }}/_archive
      FORCE: ${{ inputs.force_backup == true }}
      SQUASH: ${{ inputs.squash_history == true }}
      DRY_RUN: ${{ inputs.dry_run == true }}
    steps:
      - name: Check out radar code (main)
        uses: actions/checkout@v4
        with:
          ref: main
          persist-credentials: false
          sparse-checkout: radar

      - name: Make sure the scanner is idle
        id: guard
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          python3 -m radar.gate --housekeeping | tee -a "$GITHUB_OUTPUT"
          active=$(gh api "repos/$GITHUB_REPOSITORY/actions/workflows/radar-scan.yml/runs?status=in_progress&per_page=1" --jq '.total_count')
          echo "scanner_active=$active" | tee -a "$GITHUB_OUTPUT"
          if [ "$active" != "0" ]; then echo "::notice::Scanner run in progress; housekeeping skipped."; fi

      - name: Check out data branch
        if: steps.guard.outputs.ok == 'true' && steps.guard.outputs.scanner_active == '0'
        uses: actions/checkout@v4
        with:
          ref: data
          path: _data
          fetch-depth: 1

      - name: Attach archive branch as a worktree (created on first use)
        if: steps.guard.outputs.ok == 'true' && steps.guard.outputs.scanner_active == '0'
        run: |
          cd "$DATA_DIR"
          git config user.name  'github-actions[bot]'
          git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
          if git fetch -q --depth 1 origin data-archive; then
            git worktree add -q -B data-archive "$ARCHIVE_DIR" FETCH_HEAD
          else
            git worktree add -q --orphan -b data-archive "$ARCHIVE_DIR"   # git >= 2.42
          fi

      - name: Set up Python
        if: steps.guard.outputs.ok == 'true' && steps.guard.outputs.scanner_active == '0'
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip
          cache-dependency-path: radar/requirements.lock

      - name: Install pinned dependencies
        if: steps.guard.outputs.ok == 'true' && steps.guard.outputs.scanner_active == '0'
        run: python -m pip install --disable-pip-version-check --require-hashes -r radar/requirements.lock

      - name: Measure, back up, trim, purge, squash
        if: steps.guard.outputs.ok == 'true' && steps.guard.outputs.scanner_active == '0'
        run: |
          args=(--data-dir "$DATA_DIR" --archive-dir "$ARCHIVE_DIR" --retention-days 92 --squash auto)
          [ "$FORCE" = "true" ]   && args+=(--force-backup)
          [ "$SQUASH" = "true" ]  && args+=(--squash now)
          [ "$DRY_RUN" = "true" ] && args+=(--dry-run)
          python -m radar.housekeeping "${args[@]}"

      - name: Calendar coverage check (fails 45 days before the NYSE table runs out)
        if: always()
        run: python3 -m radar.calendar_check --warn-days 45

  prune-runs:
    # Keeps the Actions tab readable: the watchdog pattern leaves ~25 cancelled or
    # 15-second no-op scanner runs per day. Real loop runs are kept (logs follow repo retention).
    needs: housekeeping
    if: always()
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      actions: write
    steps:
      - name: Delete cancelled / no-op scanner runs older than 3 days
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          cutoff=$(date -u -d '3 days ago' +%Y-%m-%dT%H:%M:%SZ)
          gh api --paginate "repos/$GITHUB_REPOSITORY/actions/workflows/radar-scan.yml/runs?created=%3C$cutoff&per_page=100" \
            --jq '.workflow_runs[]
                  | select(.status == "completed")
                  | select(.conclusion == "cancelled" or .conclusion == "skipped"
                           or ((.updated_at | fromdate) - (.run_started_at | fromdate) < 120))
                  | .id' \
            | head -n 400 \
            | while read -r id; do gh api -X DELETE "repos/$GITHUB_REPOSITORY/actions/runs/$id" >/dev/null || true; done
```
`radar.housekeeping` (measure, backup, trim, purge, squash) is the data-model agent's module. The runtime gives it both worktrees, the git credentials that checkout stored (worktrees share the repo config), `gitsync.push`, and a guarantee that the scanner is idle.

---

## 3. Page: `radar.html` (prototype `RT/radar.html`, about 40 KB, no dependencies)

### 3.1 Content and layout
- **Theme:** the existing tokens (bg #0b0f1a, cards #151c2c, borders #1e293b, accent #818cf8, up #34d399, down #f87171, warn #fbbf24). 16 px body padding; plain JS; all text set with `textContent`.
- **Header:** "← Latest report" and "🔔 Alerts" links; title "📡 Momentum Radar"; a one-line plain explanation.
- **Status bar (`aria-live`):**
  - a session pill: PRE-MARKET, MARKET OPEN, AFTER HOURS or MARKET CLOSED
  - context: "Closes 16:00 ET (half day)", "Radar runs until 20:00 ET", or "Radar resumes Mon 07:00 ET"
  - freshness: a green / amber / red dot with "Updated 2 min ago · scan of 10:35 ET"
  - "Next ≈ 10:40 ET"
- **Banners (one at a time, in priority order):** load error; stale (red, over 12 min without a scan inside the scan window); "Waiting for today's first scan" (info, first 15 min); rate-limit / saving-budget (info); degraded tick (amber); refresh error; token rejected.
- **On the radar (N):**
  - Controls: filter chips All / ▲ Up / ▼ Down; sort by Strongest, Newest, Longest or Biggest move (remembered per viewer in localStorage, wrapped in try/catch); "🔔 Notify me" toggle.
  - Row contents: ▲/▼ glyph (direction is never shown by colour alone), ticker, state pill (NEW, RACING, COOLING, NO DATA), company name, time on radar, move since entry, 5, 15 and 30 min moves, RVol ×, distance from VWAP, a score meter from 0 to 100, and an inline SVG path since entry.
  - The path (104×32) shows the pre-entry part in grey and the move since entry in the direction colour, 2 px wide, with a dashed entry-price line and a ringed last-point dot.
  - Layout: cards on phones; a single-row grid table from 940 px (`display: contents` on the metric groups).
  - Tap or Enter expands a row: why it entered, entry time and price, last price and time, best move since entry, today's change, a halt hint, and a Yahoo chart link (ticker validated by regex).
- **Dropped off:** direction, ticker, move since entry, time on radar, plain-language exit reason plus detail, and when it left.
- **Collapsed sections:** "How the radar works" (glossary) and "Scanner health" (last scan and status, duration, coverage, source, scans ok / failed / missed, run handover time, publishing backlog, how the page is reading and requests left, code version).
- **Footer:** educational-use and data-delay disclaimer; times are shown in ET.
- **Checked in the browser:**
  - 320, 375, 860 and 960 px, with no horizontal scroll (a first attempt overflowed at 860; fixed by the 940 px breakpoint and tighter columns).
  - Scenarios via `?mock=…&now=…`: open, stale, rate-limited, first scan pending, weekend closed, overnight closed.
  - The real-API path against this repo: it resolves the ref, finds no `feed.json` yet, and shows "The radar has not published any data yet."

### 3.2 Data loading (budget-aware; the skeleton is as implemented)
```js
var OWNER="anthonynacz", REPO="daily-market-analysis", BRANCH="data", PREFIX="radar/";
var TOKEN_KEY="mm_alerts_gh_token", NOTIFY_KEY="mm_radar_notify";
var POLL_TOKEN=30e3, POLL_CLOSED=15*60e3, POLL_HIDDEN=3*60e3, POLL_RAW=2*60e3;
var TICK_MS=5*60e3, MISS_RETRY=40e3, MAX_MISSES=3, BUDGET_FLOOR=15;
var API_REF="https://api.github.com/repos/"+OWNER+"/"+REPO+"/git/ref/heads/"+BRANCH;
function rawUrl(ref,f){ return "https://raw.githubusercontent.com/"+OWNER+"/"+REPO+"/"+ref+"/"+PREFIX+f; }
function hasToken(){ return S.useToken && !!read(TOKEN_KEY); }
function lowBudget(){ return !hasToken() && S.rl.remaining!=null && S.rl.remaining<=BUDGET_FLOOR && S.rl.reset>now(); }

async function checkRef(){                         // 1 API call; ETag reused; token only to api.github.com
  var h={Accept:"application/vnd.github+json"}; if(S.etag) h["If-None-Match"]=S.etag;
  var tok=S.useToken?read(TOKEN_KEY):null; if(tok) h.Authorization="Bearer "+tok;
  var r=await fetchT(API_REF,{headers:h,cache:"no-store"},10000);   // manual conditional => browser passes 304 through
  record(r.headers x-ratelimit-remaining / -reset);
  if(r.status===304) return S.sha;
  if(r.status===200){ S.etag=r.headers.get("ETag"); return (await r.json()).object.sha; }
  if(r.status===401&&tok){ S.useToken=false; S.etag=null; S.tokenNote="…"; return checkRef(); }
  if((r.status===403||r.status===429)&&(S.rl.remaining===0||retryAfter)) throw {rateLimited:true, until:reset|retryAfter};
  if(r.status===404) throw new Error("The radar has not published any data yet.");
  throw new Error("GitHub answered "+r.status);
}
async function loadFeed(ref,pinned){               // ONE file per change
  // pinned (SHA): fresh + immutable -> cache:"force-cache";  branch fallback: CDN ≤5 min -> "no-store"
  // 404 right after a new commit: retry twice after 3 s
  return {latest:f.latest, health:f.health};
}
async function poll(){
  if(S.limitedUntil<=now() && !lowBudget()) try{ sha=await checkRef(); }catch(e){ if(!e.rateLimited) throw e; S.limitedUntil=e.until; }
  if(S.limitedUntil>now() || lowBudget()){ S.via="raw"; snap=await loadFeed(BRANCH,false); /* skip if same health.updated_at */ }
  else if(sha && (sha!==S.sha || !S.latest)){ snap=await loadFeed(sha,true); S.sha=sha; }
  if(snap){ apply(snap); S.misses=0; } else S.misses++;
  render(); schedule();                             // lists re-render only when a new snapshot arrived
}
function nextAnonymousPoll(h){                      // learn when this scanner's commits land
  var tick=Date.parse(h.tick_id), lag=clamp(Date.parse(h.updated_at)-tick, 30e3, 240e3)+15e3;
  var t=tick+lag; while(t<=now()+5e3) t+=TICK_MS; return t-now();
}
function schedule(){
  if(document.hidden && !notifyOn) return;                          // paused; visibilitychange -> poll()
  d = errors      ? min(15s*2^(n-1), 5 min)
    : !active     ? 15 min                                          // outside scan window
    : limited/low ? 2 min  (delayed CDN copy)
    : token       ? (hidden ? 3 min : 30 s)                         // authorised 304s are free
    : !health     ? 60 s
    : missed && misses<=3 && data older than 60 s ? 40 s            // commit late: short retries
    : nextAnonymousPoll(health);                                    // one call per scan
  if(document.hidden) d=max(d,3 min);
  setTimeout(poll,d);
}
```
**Budget per device on one network (60 requests/hour anonymous):**
- About 12-15 API calls/hour while the radar is running: one per scan, plus rare retries.
- 4/hour when closed; 0 when the tab is hidden and notifications are off.
- One raw fetch per change.
- A token from the Alerts page moves the page to 30 s checks, where 304s cost nothing.
- Below 15 remaining requests the page switches to the delayed CDN copy and says so. This leaves room for alerts.html and a second device.

**Market clock:**
- Taken from `health.calendar` (today plus the next 5 sessions, including `scan_start`).
- `active` means inside [scan_start, post_close + 5 min].
- If there is no feed yet, the page falls back to the ET wall clock via `Intl.DateTimeFormat(…timeZone:"America/New_York")` on weekdays (holidays unknown).
- Stale means `active` and `now - health.updated_at > 12 min`; amber from 7 min.

### 3.3 Notifications (opt-in)
- New entries are found by diffing snapshots on `ticker|entered_at`. The first load never announces.
- The page always shows an in-page toast. When the tab is hidden, the title shows a count ("(2) Momentum Radar").
- If the viewer opted in: `navigator.vibrate`, plus `new Notification()` inside try/catch. Android Chrome requires a service worker, so there it falls back to the in-page toast; iOS supports notifications only for installed web apps.
- The preference is stored in `localStorage["mm_radar_notify"]`. With notifications on, a hidden tab keeps polling at 3 min or slower.

### 3.4 Security and privacy
- The Alerts token is only ever sent to api.github.com, never to raw.githubusercontent.com (public reads need no auth).
- No `innerHTML` with data. External links use `rel="noopener noreferrer"`.
- Mock mode (`?mock`, `window.__radar`) is prototype-only: remove it before shipping.

---

## 4. Linking
- **Report pages:**
  - `template/jsx_to_html.py` WRAPPER: replace the single pill with `<nav class="float-links"><a class="alerts-link" href="__RADAR__">📡 Radar</a><a class="alerts-link" href="__ALERTS__">🔔 Alerts</a></nav>`.
  - Add the CSS `.float-links{position:fixed;right:14px;bottom:14px;z-index:50;display:flex;gap:8px}.float-links .alerts-link{position:static}`.
  - Compute `__RADAR__` with the same `relpath` logic as `alerts_href`.
  - Existing pages: `RT/add_radar_link.py <repo>` does an idempotent regex retrofit. On copies it changed 2 pages, then 0 on a second run, and skipped the legacy `2026-03-24/DailyMarketAnalysis.html`. In the real repo it would change 147 report pages plus the template.
  - Rendered on a 375 px phone: the two pills sit bottom-right without covering content.
- **alerts.html:** add a nav line `<a href="index.html">← Latest report</a> · <a href="radar.html">📡 Momentum Radar</a>`, and one sentence in the intro: "For stocks racing right now, every 5 minutes, see the Momentum Radar."
- **index.html:** a redirect page, so no visible link is possible.
  - Add the radar link to its "No report published yet" fallback.
  - Optionally accept `index.html#radar` and redirect to `radar.html`.
  - A `radar/index.html` alias for a short URL is optional.
- **radar.html** links back to the latest report and to Alerts.
- **Optional:** the hourly alert routine could read `feed.json` from the data branch and add "also on the Momentum Radar" to its emails. Radar entries themselves send no email, since a 5-minute cadence would be spam.

---

## 5. One-time implementation steps
1. Add `radar/` (code, `config.json`, `requirements.lock`) and both workflows to main in **one commit** (one Pages build).
2. Pin action SHAs.
3. No secrets are needed.
4. Run "Run workflow" with mode=once, push=false (dry run), then mode=once, push=true. The first real push also removes `probe.json` from `data`.
5. Run `add_radar_link.py` and commit (one Pages build).
6. Optional hardening: a repository ruleset on `main` that restricts updates, with a bypass for the Admin role.
   - Why: GITHUB_TOKEN with contents: write can push to any branch.
   - Effect: a compromised dependency could not rewrite the Pages site, while the user's routines, which push with the user's token, still work.

## 6. Verification performed
- `RT/tests/test_runtime.py`: 10 passed.
  - calendar identical to exchange_calendars for 2026-2028
  - phases across DST and the half day
  - 7 scanner-gate and 4 housekeeping-guard cases
  - cron coverage for all 569 sessions (starter lead min 27 / max 87 min; watchdog gaps ≤ 35 min; quick-exit triggers in Q4 2026 = 120)
  - housekeeping cron always outside the window
  - git: plain push; rebase over another writer; conflict where the scanner wins; squash followed by resync; failure with backlog (local bare repo)
  - loop end-to-end with a fake tick: aligned ticks, final tick, health; survives slow (timeout) and crashing ticks; catch-up final tick after an overrun
- Both YAML files parse with PyYAML.
- The lock resolves with hashes for manylinux x86_64.
- The page was checked in the browser (widths and scenarios listed in §3.1). Live API test: 200/304 with the ETag readable, rate-limit headers readable, SHA-pinned raw returns 200.

## 7. Files
- `RT/radar/calendar_nyse.py`, `RT/radar/gate.py`, `RT/radar/loop.py`, `RT/radar/gitsync.py`, `RT/radar/runtime_config.py`, `RT/radar/config.json`, `RT/radar/calendar_check.py`, `RT/radar/fake_tick.py`, `RT/radar/requirements.in`, `RT/radar/requirements.lock`
- `RT/workflows/radar-scan.yml`, `RT/workflows/radar-housekeeping.yml`
- `RT/radar.html`, `RT/mock/radar/{feed,latest}.json`, `RT/make_mock.py`
- `RT/add_radar_link.py`, `RT/tests/test_runtime.py`
- `RT/_vendor/` holds exchange_calendars, used only by the calendar cross-check test and installed with `--target`; the user's venv is unchanged.