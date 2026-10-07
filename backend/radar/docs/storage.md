# AMENDMENTS
This design predates the review and rehearsal amendments. Where the text below disagrees with `radar/SPEC.md` sections 12.4 and 12.6, the SPEC wins. In particular:
- **The routine's 200-row cap no longer drops rows before they are backed up** (corrects RISKS, item 2). Every apply run copies all current alerts_log rows into their monthly backup partitions (backup only, SPEC 12.4). The cap can therefore only drop rows a backup already holds, unless more than 200 alerts arrive between two housekeeping runs.
- **Main is not touched only at 03:30 UTC** (corrects section 1.3). Manual and scanner-requested runs exist. Main is written (phase B) by the scheduled run, or by any run outside weekdays 13:00-21:30 UTC (SPEC 12.4). Inside that window the main trim is `deferred`, and the next run that writes main removes the rows. Every write stays a compare-and-swap on the file `sha`.
- **Heal on main** (amends sections 3.1 and 3.2, SPEC 12.6): the backup-only copies never take a row off main. An alerts_log row leaves main only by archive (DEGRADED or forced), by retention, or when it is listed in `ops/pending_main_trim.json` on the data branch, i.e. it was archived by a run whose phase B was deferred or failed. The list is written with the data commit, ahead of phase B. A later run drops the rows main no longer has. Main therefore keeps alerts until 180 rows or 3 months.

# SUMMARY
Storage and housekeeping design for Momentum Radar, with a working prototype. 27 pytest tests pass: 23 on the table engine and 4 on git races against a local bare remote.
- Tables: 5 append-only JSON Lines hot tables plus 2 snapshots on the `data` branch (radar/member_ticks, radar/events, radar/scan_log, ops/table_metrics, ops/housekeeping_runs; radar/state.json and ops/health.json). alerts/log.json stays on main, keeps its current format and is handled by housekeeping there.
- Degradation is measurable and recorded in ops/table_metrics.jsonl on every run. A table is DEGRADED when its size, its row count, its measured lookup latency (median of 3 cold parse+filter runs), the scanner's per-tick write p95, or a client ceiling passes its limit. Limits were calibrated from benchmarks: member_ticks at 8 MiB / 25k rows, because the git write path stays flat to about 16 MiB and parse cost grows linearly.
- Housekeeping moves rows older than the table's hot window into canonical monthly partitions (backups/<table>/<YYYY-MM>.jsonl.gz): sorted, de-duplicated by primary key, deterministic gzip, plus a manifest. Each partition is verified from disk before the hot table is trimmed. If the table is still too big it trims further, down to 40-55% of the limit (hysteresis). Re-runs change nothing, appending twice to a month creates no duplicates, and a crash at any of the 5 steps recovers on the next run (all tested).
- Retention is 3 calendar months, checked row by row at the UTC day boundary (for example 2026-09-27 keeps rows from 2026-06-27 onward). It applies to hot tables, backups, the alerts log and the data-branch history.
- Git: all data-branch writers follow one rule: rebuild the change on the current head, then push only if the head has not moved. The scanner pushes fast-forward only and never force-pushes. Housekeeping squashes the data branch into one parentless commit every night (02:00-07:00 UTC window) using `--force-with-lease` pinned to the head it read. There is no shared Actions concurrency group, because pending runs would be cancelled by the watchdog.
- Schedule: daily at 03:30 UTC, plus manual dispatch (apply or dry-run, squash auto/force/skip, force-archive) and a rate-limited request from the scanner. Mid-session runs never squash.

# RISKS
- Absolute latency numbers were measured on a laptop whose CPU was at 100% from parallel agents; the GitHub runner is likely 2-4x faster. Size and row limits (8 MiB / 25k rows for member_ticks) are the main triggers; max_lookup_ms should be re-tuned from ops/table_metrics after the first week on the runner.
- The Stock Reaction Alerts routine caps alerts/log.json at the newest 200 entries. If more than about 20 alerts arrive between two daily housekeeping runs, or housekeeping is down, the routine drops rows before they are backed up. Housekeeping triggers at 180 rows to stay ahead; raising the routine cap to 500 would remove the gap but means editing the routine prompt.
- The nightly squash relies on force pushes (with a lease) being allowed on the data branch. If the user later adds a ruleset that blocks them, the squash fails: the job falls back to a normal commit and flags WARN, but history grows and retention is no longer enforced in history.
- Retention in git is only best effort. GitHub keeps unreachable force-pushed objects for an unspecified time (fetchable by SHA), forks keep them, and main's history keeps every old version of alerts/log.json because main (the Pages branch) is never rewritten. The data is market data and not sensitive, so this is acceptable.
- Growth estimates assume member_ticks rows of about 335 B and at most 10 logged candidates per tick; the row contents belong to the signal-design agent. Bigger rows just make the byte limit fire earlier (by design), but archiving becomes more frequent.
- Adding .github/workflows/radar-housekeeping.yml by git push needs a token with Workflows write permission; the fine-grained PAT embedded in the routines may not have it (and it is still due for rotation).
- Gzip output can change if the runner's zlib changes. Equality checks use the uncompressed content hash, so the worst case is one extra partition rewrite and a manifest sha change, never duplicates, but any external tool that compares .gz hashes would see a change.
- The optional pre-squash git bundle is uploaded as a workflow artifact (7-day retention). This assumes artifact storage is free for public repos; if it is not wanted it can be dropped, since the squash is already checked for tree drift.
- Scheduled Actions runs can be delayed or occasionally dropped; a skipped night delays retention by a day. health.json exposes min_ts, but no alert fires on retention lag yet (proposed: WARN when min_ts < cutoff - 2 days).
- An on-demand housekeeping run during market hours competes with scanner pushes. The head checks keep it correct, but the scanner may lose one tick push to a retry (about 1-3 s) per housekeeping commit. The scanner must implement the fetch / reset / re-apply loop exactly as specified; a merge or rebase approach would conflict on JSONL appends.

# Momentum Radar: tables, housekeeping and backups (storage design)

Prototype, tests and benchmarks (scratch only; repo and data worktree untouched):
`<design-scratchpad>/radar_design/storage/`
- `tables.py`: table registry and policy (paths, primary key, timestamp field, thresholds)
- `housekeeping.py`: measure → classify → plan → archive/verify/trim → retention → manifest → ops rows; CLI `run | query | restore | verify`
- `gitops.py`: data-branch write protocol (sync, publish with a head check, squash with lease, retry)
- `synth.py`: synthetic volume generator; `bench_lookup.py`, `bench_git.py`: calibration
- `test_housekeeping.py` (23 tests), `test_gitops.py` (4 tests against a local bare remote). **All 27 pass** (`python -m pytest -q test_gitops.py test_housekeeping.py`, about 2 minutes)
- `radar-housekeeping.yml`: workflow draft (target `.github/workflows/radar-housekeeping.yml` on main)

Two real bugs the tests caught and I fixed:
- A corrupted gzip raises `zlib.error`, which was not treated as corruption.
- `sync` must `checkout -f` so that a rejected attempt's leftover edits do not block the retry.

---

## 1. Table inventory

### 1.1 Conventions (all tables)
- **Timestamps:** UTC, format `YYYY-MM-DDTHH:MM:SSZ` (seconds, `Z`). Text order equals time order, and lookups compare strings.
- **Row version:** every row carries `"v": 1`. Partitions may mix versions.
- **Hot tables:** one file per table. Tables that grow are JSON Lines, append-only, one compact JSON object per line (`separators=(",",":")`), LF endings. Snapshots are a single JSON document that is overwritten.
- **Why JSON Lines, not CSV or one JSON array:** appending costs O(1) with no read. Git line diffs let each push send only a small delta. Readers can stream it. Nested `signals` maps survive. CSV loses types and nesting.
- **Partitions:** `backups/<table>/<YYYY-MM>.jsonl.gz` on `data`, one per table and UTC month of the row timestamp. Content is canonical:
  - `json.dumps(row, sort_keys=True, separators=(",",":"), ensure_ascii=False)` per line;
  - sorted by (ts, pk), unique by pk;
  - gzip level 6, `mtime=0`, `filename=""`.
- **Data branch** gets a `.gitattributes` containing `* -text`, so Windows clones never convert line endings. Remove `probe.json` when implementing.
- **No workflow may trigger on `push` to `data`** (it would burn minutes about 160 times a day).

### 1.2 Tables

| Table | Branch:path | Format | PK | ts field | Writer / cadence | Read by |
|---|---|---|---|---|---|---|
| state (snapshot) | data:`radar/state.json` | JSON doc, overwritten | n/a | `tick` | scanner, every tick | browser (SHA-pinned raw URL), scanner warm start |
| member_ticks | data:`radar/member_ticks.jsonl` | JSONL append | (`tick`,`ticker`) | `tick` | scanner, every tick | scanner warm start, calibration/replay, housekeeping; **not the browser** |
| events | data:`radar/events.jsonl` | JSONL append | `id` | `ts` | scanner, on enter/exit | browser (recent activity), scanner cooldown rules |
| scan_log | data:`radar/scan_log.jsonl` | JSONL append | (`tick`,`run_id`) | `tick` | scanner, every tick | watchdog / ops, housekeeping (write-path metric) |
| table_metrics | data:`ops/table_metrics.jsonl` | JSONL append | (`run_id`,`table`) | `measured_at` | housekeeping, 1 row per table per run | ops, trend of degradation metrics |
| housekeeping_runs | data:`ops/housekeeping_runs.jsonl` | JSONL append | `run_id` | `started_at` | housekeeping, 1 row per run | ops |
| health (snapshot) | data:`ops/health.json` | JSON doc | n/a | `as_of` | housekeeping | browser "data health" chip (optional) |
| manifest (index) | data:`backups/manifest.json` | JSON doc | `table/YYYY-MM` | n/a | housekeeping | restore / query tools |
| alerts_log | **main**:`alerts/log.json` | `{"alerts":[...]}`, newest first (unchanged) | (`ts`,`ticker`) | `ts` | Claude routine (hourly, plain git) | alerts.html via contents API, the routine itself |

### 1.3 Decision: keep `alerts/log.json` on main
- The routine (LLM, plain git, hourly), alerts.html (`contents/…?ref=main`) and its dedupe logic all point there. Nothing needs to change.
- Moving it would put an LLM writer on the same branch as 5-minute scanner pushes and a nightly force-push. A non-fast-forward rejection could tempt the LLM into a plain `--force`, which would wipe radar data. On main that risk does not exist.
- Housekeeping touches main at most once a day (about 1 Pages build). It uses a contents API `PUT` with the file `sha`, which is a built-in compare-and-swap. It runs at 03:30 UTC, while the routine runs at `:45` 13-20 UTC on weekdays, so they never overlap.
- Its backups go to the data branch like every other table: `backups/alerts_log/YYYY-MM.jsonl.gz`.
- Limits: `max_rows` is 180, below the routine's current 200-row cap, so housekeeping archives rows before the routine can drop them. **No routine prompt change is required.** Optionally raise the routine cap to 500 later as an emergency brake.
- 1 MB contents API limit: 180 rows × ~350 B ≈ 63 KB, about 16× headroom. A hard guard fires at 900 KiB.
- LLM context: 180 rows ≈ 16k tokens; after a trim ≈ 100 rows.

### 1.4 Row schemas

Fields the storage layer needs are fixed here. The contents of `signals` belong to the signal-design agent; storage treats it as an opaque `{str: number}` map (at most about 8 keys, 3 decimals).

**radar/state.json**
```
{ "schema_version":1, "generated_at":ts, "tick":ts, "session":"pre|regular|post|closed",
  "scanner":{"run_id":str, "started_at":ts, "version":str, "source":"yfinance|nasdaq", "ticks_this_run":int},
  "members":[{"ticker":str, "dir":"up|down", "entered_at":ts, "entry_price":float, "last_price":float,
              "chg_day_pct":float, "chg_since_entry_pct":float, "score":float, "reason":str(<=160),
              "signals":{str:float}, "trail":[[ts_epoch_min:int, price:float, score:float], ...<=78]}],
  "recent_exits":[{"ticker","dir","exited_at":ts,"reason","held_min":int,"chg_since_entry_pct":float}] (<=20),
  "ops":{"hk_dispatched_at":ts|null} }
```
- Size is at most about 60 KB (25 members × ~2 KB). Guard limit 256 KiB.
- The browser reads only this file for live data (trails are embedded), so it never downloads member_ticks.

**radar/member_ticks.jsonl** (measured ≈ 335 B per row with 6 signals)
```
{"v":1,"tick":ts,"ticker":str,"role":"member|candidate","dir":"up|down","price":float(4dp),
 "chg_day_pct":float(2dp),"chg_5m_pct":float,"chg_since_entry_pct":float|null,"vol_5m":int,"rvol":float,
 "score":float(3dp),"state":"enter|hold|exit_watch|exit","signals":{str:float}}
```
- `tick` is the UTC start of the 5-minute slot the scan belongs to.
- `role:"candidate"` rows (top-K near misses, K ≤ 10) are optional and exist for calibration.

**radar/events.jsonl** (≈ 310 B)
```
{"v":1,"id":"20260925T1955Z-NVDA-ENTER","ts":ts,"ticker":str,"type":"ENTER|EXIT|FLIP","dir":"up|down",
 "price":float,"score":float,"reason":str(<=160),"held_min":int|null,"chg_since_entry_pct":float|null,"signals":{...}}
```

**radar/scan_log.jsonl** (≈ 450 B)
```
{"v":1,"tick":ts,"run_id":"gha-<run_id>-<attempt>","written_at":ts,"session":str,"lag_s":int,"universe":int,
 "quotes_ok":int,"quotes_err":int,"candidates":int,"members":int,"entered":[str],"exited":[str],"source":str,
 "ms":{"fetch":int,"compute":int,"write":int,"stage":int,"commit":int,"push":int,"total":int},
 "push_attempts":int,"hot_bytes":{"member_ticks":int,"events":int,"scan_log":int,"state":int},"errors":[str(<=200)] (<=5)}
```
- `ms.stage` (git add, i.e. hashing and compressing) and `ms.commit` are local costs that grow with volume. They feed the write-path metric.
- `ms.push` is network time; it is recorded but not used as a trigger.

**ops/table_metrics.jsonl**
```
{"v":1,"run_id":str,"measured_at":ts,"table":str,"location":"data:radar/member_ticks.jsonl","status":"OK|WARN|DEGRADED|ERROR",
 "reasons":[str],"bytes":int,"rows":int,"min_ts":ts|null,"max_ts":ts|null,"lookups_ms":{name:float},"lookup_max_ms":float,
 "io_p95_ms":float|null,"action":"none|archive|purge|heal|archive+purge…","archived_rows":int,"purged_rows":int,
 "healed_rows":int,"rows_after":int,"bytes_after":int}
```

**ops/housekeeping_runs.jsonl**
```
{"v":1,"run_id":"hk-<gha_run_id>-<attempt>","started_at":ts,"trigger":"schedule|manual|scanner_request","mode":"apply",
 "retention_cutoff":ts,"status_by_table":{table:status},"rows_archived":int,"rows_purged_hot":int,
 "partitions_written":[key],"partitions_deleted":[key],"partitions_filtered":[key],"manifest_repairs":[str],"partition_errors":{table:str}}
```
- The squash outcome goes in the commit message ("squashed data branch; previous head <sha>") and the job summary. A commit cannot contain its own SHA.

**backups/manifest.json** (an index that can always be rebuilt from the files)
```
{"schema_version":1,"updated_at":ts,"last_run_id":str,"retention":{"policy":"3 calendar months, row-level, UTC","cutoff":ts},
 "partitions":{"member_ticks/2026-09":{"table","month","path","rows":int,"bytes":int,"raw_bytes":int,
   "sha256":"<of .gz>","content_sha256":"<of canonical uncompressed>","min_ts","max_ts","pk":[..],"ts_field",
   "format","created_at","updated_at","writes":int,"last_run_id"}}}
```

**ops/health.json**: per table `{status, bytes, rows, lookup_max_ms, backup_months[], backup_rows}`, plus snapshot guards and `backups_total_bytes`.

### 1.5 Expected growth (5-25 members, 80-160 ticks/day)

| Table | Rows/day | Bytes/day | First DEGRADED after | Afterwards |
|---|---|---|---|---|
| member_ticks, low (5×80) | 400 | 134 KB | ~62 trading days | about equal to retention, so it may never archive (retention purge only) |
| member_ticks, typical (12×120) | 1,440 | 480 KB | ~17 trading days | archive every ~12 trading days |
| member_ticks, high (25×160, +10 candidates) | 4,000-5,600 | 1.34-1.88 MB | ~4.5 days | every ~2.6 days (trim to 3.2 MiB, refill) |
| events | 10-120 | 3-37 KB | 14-170 days (512 KiB client ceiling) | high turnover about monthly, else retention only |
| scan_log | 80-160 | 36-72 KB | 25-50 trading days (4,000 rows) | about monthly |
| table_metrics | ~6 | ~3 KB | never (5k rows = 800+ days) | retention only |
| housekeeping_runs | 1 | ~0.7 KB | never | retention only |
| alerts_log | 0-35 | 0-12 KB | 180 rows (~5-36 days) | trim to ≤ 99 rows / 30 days |
| state.json | overwritten | ≤ 60 KB | n/a | guard only |

- **Backup volume at the high end over a 3-month window:** member_ticks ≈ 118 MB raw → ≈ 21 MB gz (measured compression ratio 5.7 on synthetic random data; real data compresses better).
  - Per month: ≤ ~41 MB raw / ~7 MB gz. GitHub warns at 50 MB and rejects files over 100 MB.
  - scan_log ≈ 0.8 MB gz per quarter.
- **Whole data branch:** about 8 MB typical, 35 MB or less at the high end, with about one day of history after squashing.

## 2. "Lookup performance degraded": definition and measurement

### 2.1 Standard lookups
Each is a cold query (full parse plus filter), the way a consumer actually runs it.

| Table | Lookups (consumer) |
|---|---|
| member_ticks | `latest_by_ticker` (scanner warm start); `series_ticker_day` (trail rebuild / exit logic); `rows_on_day` (end-of-day calibration / replay) |
| events | `open_membership` (last ENTER with no later EXIT: state reconciliation); `rows_last_24h` (browser activity feed); `latest_by_ticker` (re-entry cooldown) |
| scan_log | `last_row` (watchdog / gap detection); `rows_on_day` (daily coverage %) |
| table_metrics / housekeeping_runs | `last_row`, `rows_last_24h` |
| alerts_log | `newest_25` (alerts.html); `rows_last_24h` (routine dedupe) |

- Parameters come from the data itself: the ticker and day of the newest row, and the 24 hours before it.
- Each lookup is timed with `perf_counter`, median of 3 runs (1 run if the table has more than 4× `max_rows`).

### 2.2 Calibration (measured on this machine; CPU was at 100% from parallel agents, so absolute numbers are pessimistic; an idle 4-vCPU runner should be roughly 2-4× faster)

Lookup cost (parse + filter, ms, median of 3):

| member_ticks rows | MiB | parse | latest_by_ticker | series | rows_on_day |
|---|---|---|---|---|---|
| 1,000 | 0.32 | 50 | 52 | 68 | 58 |
| 10,000 | 3.2 | 379 | 353 | 476 | 403 |
| 25,000 | 8.0 | 1,045 | 1,326 | 1,298 | 1,220 |
| 50,000 | 16 | 2,588 | 1,897 | 2,027 | 2,549 |
| 100,000 | 32 | 3,490 | 4,703 | 4,545 | 4,646 |
| 200,000 | 64 | 8,558 | 8,338 | 10,916 | 10,153 |

Cost grows linearly with rows (about 25-45 µs per row here).

Per-tick write path (append 35 rows + git add + commit + push to a local bare remote, ms, median of 5):

| MiB | add | commit | push | total |
|---|---|---|---|---|
| 1 | 189 | 319 | 1,001 | 1,601 |
| 8 | 322 | 301 | 975 | 1,612 |
| 16 | 460 | 272 | 1,160 | 1,799 |
| 32 | 919 | 230 | 2,070 | 3,397 |
| 64 | 1,773 | 230 | 3,369 | 5,584 |

The write path is flat up to about 16 MiB (the knee), then roughly doubles each time the file size doubles. The 8 MiB limit keeps member_ticks at half the knee.

In the real-volume test, one latency sample on the contended machine spiked to 6.2 s. That noise is why size and row count are the main triggers, and latency is a median-of-3 backstop that only counts when the table is non-trivial.

### 2.3 Rules

**A table is DEGRADED if any of these holds:**
- a. `bytes > max_bytes`
- b. `rows > max_rows`
- c. `lookup_max_ms > max_lookup_ms`, counted only when `rows ≥ 20% of max_rows` (noise guard)
- d. per-tick tables only: `write_p95_ms > 750` **and** `bytes ≥ 50% of max_bytes`
  - `write_p95_ms` is the p95 of `ms.stage + ms.commit` over the last 50 scan_log rows, with at least 10 samples.
  - The 50% condition assigns the shared write cost to the tables that are actually big.
- e. `bytes > external_max_bytes`: the browser ceiling for events (512 KiB) or the contents API guard for alerts_log (900 KiB)

**WARN:** any metric at 80% or more of its limit, or unparseable lines / rows without a timestamp, or duplicate primary keys. No action is taken.

**ERROR:** the table's backups are corrupt, or the manifest lists a live month whose file is missing. The table is frozen (no archive, trim or purge) and the job exits with code 2. A failed scheduled run makes GitHub email the owner by default.

**Snapshot guards** (`state.json` 256 KiB, `health.json` 64 KiB, `manifest.json` 256 KiB) are reported in health.json only. They indicate a scanner bug, so housekeeping takes no action.

**Default thresholds** (`tables.py`):

| Table | max_bytes | max_rows | max_lookup_ms | hot_days | min_hot_hours | target_ratio | external |
|---|---|---|---|---|---|---|---|
| member_ticks | 8 MiB | 25,000 | 750 | 5 | 20 | 0.40 | none |
| events | 1 MiB | 3,000 | 250 | 30 | 72 | 0.50 | 512 KiB (browser) |
| scan_log | 2 MiB | 4,000 | 250 | 10 | 20 | 0.50 | none |
| table_metrics | 1 MiB | 5,000 | 250 | 90 | 24 | 0.50 | none |
| housekeeping_runs | 512 KiB | 1,000 | 250 | 90 | 24 | 0.50 | none |
| alerts_log | 256 KiB | 180 | 100 | 30 | 48 | 0.55 | 900 KiB (contents API) |

**Where metrics are recorded:**
- Per tick: the scanner writes `hot_bytes` and `ms.*` into scan_log (costs one `os.stat` per file).
- Per run: housekeeping appends one `ops/table_metrics.jsonl` row per table and overwrites `ops/health.json`.
- Plan: re-tune `max_lookup_ms` after the first week of runner data. Byte and row limits stay.

## 3. Housekeeping algorithm

### 3.1 Plan (pure; row positions in the file keep row identity)
Here `now` is the run time (UTC) and `cutoff = retention_cutoff(now.date())`.
1. **purge** = rows with `ts < cutoff`. They are deleted without being archived, because they are past retention anyway.
2. If DEGRADED, or the table is listed in `force_archive`:
   - **archive** = live rows with `ts < now − hot_days`.
   - **Squeeze:** if the remaining rows are still above `target_ratio × max_bytes` or `target_ratio × max_rows`, keep archiving the oldest remaining rows until within target. Never archive rows with `ts ≥ now − min_hot_hours` (this protects the current session and the scanner's warm start). This gives hysteresis, so the table does not trigger again the next day.
3. **heal** = rows older than `hot_days` that already sit, identical, in a partition (left there by an interrupted earlier run). A cheap min/max range check against the manifest comes first. Heal never shrinks the hot window below policy.
4. **keep** = everything else, in the original file order. Unparseable lines and rows without a timestamp or key are kept verbatim forever and reported.

### 3.2 Apply: order of operations (phase A on the data working tree, then publish, then phase B on main)

**Step 0.** Delete leftover `*.tmp-hk` files. Reconcile the manifest against the files; the files are the source of truth:
- sha mismatch but the file is valid → rebuild the entry (and count the unrecorded write);
- file not in the manifest → add it;
- listed month's file is missing but the month is past retention → drop the entry (an interrupted purge already deleted it);
- otherwise → ERROR for that table.

**Step 1: write partitions.** For each (table, month) with archive rows:
- Merge `existing ∪ archive` by pk, last write wins in chronological order.
- Sort by (ts, pk), serialize canonically.
- If `content_sha256` equals the manifest value and the gz sha matches, skip the write (this is the idempotency point).
- Otherwise:
  - write the deterministic gzip to `path.tmp-hk`, then `fsync`;
  - **re-read it from disk and validate:** decompresses cleanly, every line canonical, strictly increasing (ts, pk) (so sorted and unique), row count matches, every archived row present and equal to the winning version;
  - `os.replace` the temp file into place.
- `CHECKPOINT partitions_written`.

**Step 2: backup retention.** Delete months whose end is at or before `cutoff`. Filter the boundary month row by row (same verified rewrite path); delete it if it becomes empty. `CHECKPOINT backups_purged`.

**Step 3.** Write the manifest atomically (temp file, fsync, replace). `CHECKPOINT manifest_written`.

**Step 4.** Rewrite each data-branch hot table with `keep` rows only (atomic replace; original line text preserved). `CHECKPOINT hot_trimmed`.

**Step 5: post-condition asserts.** Per table, `|keep| + |archive| + |purge| + |heal| = |original|`. Every archived row's month is in the manifest. Re-reading each hot table gives exactly `keep`.

**Step 6.** Append the table_metrics and housekeeping_runs rows and write `ops/health.json`. `CHECKPOINT ops_written`.

**Step 7: publish the data branch** (section 4).

**Phase B, main table (only if step 7 succeeded):** trim `alerts/log.json` through the contents API.
- GET (content + sha); drop rows whose pk is in the archive/purge/heal set **and** whose content is identical; PUT `{content, sha, branch:"main"}`.
- On 409/422, re-GET and retry (up to 5 times). New alerts the routine appended in between are preserved (tested).
- If phase B never happens, the next run heals: the rows are already in the backups.

### 3.3 Crash safety and idempotency
- **In production,** a crash anywhere before step 7 changes nothing on the remote. Everything is one atomic commit, and the runner's local tree is thrown away.
- **For local reruns on the same tree,** every point a crash can stop at is one of three states:
  - (a) the original state;
  - (b) rows present in both the backups and the hot table (the overlap is healed or re-archived identically on the next run);
  - (c) the final state.
- A row is never missing from both places. The hot table is trimmed only after the partition holding those rows has been verified on disk.
- The cross-branch order (data backup pushed and checked with `ls-remote` before main is trimmed) gives the same guarantee across the two branches.
- **Tested:** a crash injected at each of the 5 checkpoints, followed by a rerun, gives a tree byte-identical to a clean run, and no unexpired row is ever missing.
- **No duplicates:** a partition's content is a function of the set of rows it holds, so archiving the same rows again yields the same bytes. Tested: a rerun changes no files (ops rows excluded); a second archive into the same month with overlapping rows produces unique primary keys.
- **zlib differences:** if the runner's zlib changes gz bytes, `content_sha256` (uncompressed) still decides equality. The worst case is one extra rewrite, never duplicates.

### 3.4 Retention: exact definition
- **Rule:** keep a row if and only if `ts ≥ cutoff`, where `cutoff = 00:00:00Z` of the same calendar day 3 months before the run's UTC date, with the day clamped to the end of the month.
  - 2026-09-27 → 2026-06-27
  - 2026-05-31 → 2026-02-28
  - 2024-05-31 → 2024-02-29
  - 2026-11-30 → 2026-08-30
- **Why calendar months, not 90 days:** it matches "3 months" as the user said it and matches the monthly partitions. 90 days would drift (Sep 27 − 90 days = Jun 29).
- **Where it is applied, on every run** (whether degraded or not): hot tables, backup partitions (row by row at the boundary month), manifest entries, ops tables, alerts_log on main, and the data-branch history (through the squash).
- **Effective lifetime** is between 3 months and 3 months + 1 day while the daily run is healthy. Missed runs add lag. health.json shows `min_ts` per table; alert if `min_ts < cutoff − 2 days`.
- **Caveats:**
  - main's git history keeps old versions of `alerts/log.json`. Main is not rewritten because it hosts Pages. The data is non-sensitive.
  - GitHub keeps unreachable force-pushed objects for an unspecified time, and they stay fetchable by SHA until its GC runs.

## 4. Git-level design

### 4.1 Why squash
- Without squashing, `data` receives 80-160 commits a day. Each rewrites state.json and member_ticks (up to 8 MiB).
- Delta chains are capped (depth about 50), so full base copies recur. Estimated packed growth is 2-7 MB/day, i.e. 0.2-0.6 GB per quarter, and it never stops growing.
- Purged rows would live on in history, which violates retention. Fresh clones, which the scanner makes at every watchdog restart, would keep getting slower.

### 4.2 Recommendation: nightly squash inside the housekeeping commit
- When `squash_due` is true (policy `auto` means 02:00-07:00 UTC, i.e. 22:00-03:00 EDT or 21:00-02:00 EST, or any time on a weekend; `force`; `skip`), the housekeeping commit is **parentless**: its tree is the head's tree plus the housekeeping changes.
- It is pushed with `git push --force-with-lease=refs/heads/data:<head read at sync> origin <new>:refs/heads/data`.
- **Guards:**
  - `git diff --name-only <head> <new>` must equal exactly the set of staged paths, otherwise the push is refused (tree drift);
  - after the push, `ls-remote` must equal the new sha;
  - the job first saves `git bundle --all` of `data` as a workflow artifact with `retention-days: 7`. This is insurance outside the repo; drop it if artifact storage is unwanted.
- Result: the data branch always holds at most about one trading day of history. Tested: the squash leaves 1 commit with the expected tree and a valid manifest.
- A daytime (on-demand) run never squashes; it adds a normal commit (tested).
- If a ruleset ever blocks force pushes on `data`, the squash push fails with a "denied" error, not a lease error. The job should then retry once without squashing and flag WARN.

### 4.3 Concurrency: compare-and-swap on the branch, not a shared Actions concurrency group or a lock file
- **Rejected: a shared concurrency group.**
  - The scanner is a long-running job (up to 6 h), so housekeeping would sit blocked for hours.
  - GitHub keeps only one pending run per group and cancels the older pending run when a newer one queues. The 5-10 minute watchdog cron would silently cancel a pending housekeeping run.
- **Rejected: a lock file in git.** It is itself racy and needs stale-lock timeouts.
- **Chosen:** Git's own compare-and-swap:
  - normal pushes are fast-forward only, so they are rejected if the remote moved;
  - the squash is protected by the lease;
  - housekeeping has its own concurrency group only to prevent two housekeeping runs overlapping.
- Tested races:
  - (1) the scanner pushes between housekeeping's sync and publish → the lease rejects → housekeeping redoes the whole run on the new head → the scanner's row survives and history is still 1 commit;
  - (2) housekeeping squashes between the scanner's sync and push → the scanner's fast-forward push is rejected → it re-applies on the squashed head → exactly one copy of the row, and history is 2 commits.

### 4.4 Writer contract, binding for the scanner design
Every data-branch writer does this on each attempt:
1. `git fetch --no-tags origin +refs/heads/data:refs/remotes/origin/data`
2. `git checkout -f -B data origin/data`, then `git reset --hard origin/data`, then `git clean -fd`
3. Re-apply its delta from memory (append the tick's rows; overwrite state.json).
4. Commit with parent = the head read in step 1.
5. `git push origin <sha>:refs/heads/data` (never force).
6. On rejection, go back to step 1: at most 5 attempts, backoff `min(30, 2^n) × U(0.5, 1.5)` seconds.

Rules:
- Never merge or rebase (appends to the same JSONL would conflict).
- Keep the pending tick delta in memory until the push lands.
- A shallow clone (`--depth 1 --single-branch -b data`) is fine for the scanner.
- The scanner records `ms.stage`, `ms.commit`, `ms.push` and `hot_bytes` in each scan_log row.

## 5. Schedule, triggers and reports
- **Schedule:** `cron: "30 3 * * *"` (03:30 UTC daily = 23:30 EDT / 22:30 EST). The scanner is idle and the time is inside the squash window. Weekend runs still apply retention and squash.
- **Manual `workflow_dispatch` inputs:** `mode` (apply | dry-run), `squash` (auto | force | skip), `force_archive` (table names), `reason`.
- **On-demand request from the scanner:**
  - The scanner calls `POST /repos/{r}/actions/workflows/radar-housekeeping.yml/dispatches` with `{ref:"main", inputs:{mode:"apply", squash:"skip", reason:"scanner: <why>"}}`. This needs `actions: write` on the scanner job; dispatches made with GITHUB_TOKEN do start workflows.
  - It triggers when any per-tick table goes over `1.25 × max_bytes`, or `write_p95 > 1,500 ms`.
  - Rate limit: at most once every 6 hours, tracked in `state.json.ops.hk_dispatched_at`.
  - Mid-session runs archive only (no squash). The head check keeps them correct; at worst the scanner makes one retry.
- **Job settings:** `permissions: contents: write`; `concurrency: {group: radar-housekeeping, cancel-in-progress: false}`; `timeout-minutes: 20`. Checkouts: main (code; depth 1) and data (`fetch-depth: 0`, which is short after squashing). Draft: `radar-housekeeping.yml`.
- **Dry-run** writes nothing to either branch (tested byte-identical). It prints a markdown table to `$GITHUB_STEP_SUMMARY`: status, rows, size, lookup max ms, archive/purge/heal counts, rows after, reasons. It also lists the partitions it would write, delete or filter.
- **Outputs of an apply run:**
  - the same summary;
  - JSON report artifact (7 days);
  - `ops/table_metrics.jsonl`, `ops/housekeeping_runs.jsonl`, `ops/health.json`;
  - commit message `Housekeeping <run_id>: archived N rows, purged M (squashed data branch; previous head <sha12>)`.
- **Exit code 2** on partition errors, so the job goes red and the owner gets an email.
- **Implementation wrapper** `radar/storage/housekeeping_job.py`: `gitops.housekeeping_job(...)` around `housekeeping.run(...)`. Its main-branch store is a contents API adapter with the same `read → (bytes, sha)` / `write_cas(bytes, sha) → bool` shape as the prototype's `DirStore`.

## 6. Restore and query procedure
1. **Find the partition:** `backups/manifest.json` (or `ops/health.json → backup_months`).
2. **Download and verify:**
   ```
   curl -sL https://raw.githubusercontent.com/anthonynacz/daily-market-analysis/data/backups/member_ticks/2026-08.jsonl.gz -o p.gz
   sha256sum p.gz
   ```
   The hash must equal the manifest's `sha256`. The branch URL may be up to 5 minutes stale; use a commit-SHA URL for the exact version.
3. **Ad-hoc query:** `gunzip -c p.gz | jq -c 'select(.ticker=="NVDA")'`, or `pandas.read_json("p.gz", lines=True)`.
4. **Tooling** (merges hot + backups, de-duplicated by primary key, hot copy wins):
   - query: `python radar/storage/housekeeping.py query --data-root data --table member_ticks --from 2026-08-01T00:00:00Z --to 2026-09-01T00:00:00Z --where ticker=NVDA`
   - restore: `python … restore --data-root data --table member_ticks --month 2026-08 --out /tmp/mt-2026-08.jsonl` (checks sha and validates rows)
   - verify: `python … verify --data-root data` (re-hashes and re-validates every partition against the manifest)
5. **Do not push restored rows back into a hot table.** It would immediately re-trigger degradation. Point the analysis or replay at the restored file instead.
6. **Disaster recovery** (a hot table or partition damaged by a bug) within 7 days: download the `housekeeping-<run_id>` artifact, `git clone data-pre.bundle`, then `git show HEAD:<path>`.
7. Data older than 3 months is gone by design.

## 7. Test plan
**Implemented and passing:**
- retention cutoff, including clamping and a leap year (5 cases);
- small tables untouched;
- volume trigger → archive + trim:
  - hot size within target;
  - hot and backups disjoint;
  - hot ∪ backups = original;
  - nothing newer than the `min_hot_hours` floor archived;
  - manifest row counts equal the file row counts;
- real thresholds, no scaling: 33,600 rows / 10.7 MiB → DEGRADED on bytes, rows and latency → trimmed to exactly 10,000 rows / 3.2 MiB;
- rerun makes no changes;
- same month appended twice → no duplicates;
- retention purges hot + backups, the boundary month is filtered row by row, and every unexpired row survives;
- crash at each of the 5 checkpoints → no loss → rerun converges byte-identically;
- a corrupt partition freezes only its own table;
- manifest rebuilt from the files;
- alerts_log two-phase: main untouched if the data publish fails; newest-first order and extra keys preserved; hot ∪ backup = all rows;
- alerts trim retries when the file changes between read and write (a concurrent routine append is preserved);
- dry-run writes nothing;
- unparseable lines kept verbatim;
- query/restore round trip;
- git: squash leaves a single root with the expected tree; lease rejection → retry keeps the concurrent scanner row; scanner re-apply after a squash leaves no duplicates; a daytime run does not squash.

**To add during implementation:**
- a contents API adapter against a mocked 409/422;
- a squash fallback test for when force push is denied by a ruleset;
- a scanner dispatch rate-limit test;
- a shallow-clone variant of the writer protocol;
- a **120-day soak simulation**: `synth.py` produces daily volume at low, typical and high profiles, housekeeping runs each simulated night, and after every day these invariants are asserted:
  - no row older than the cutoff anywhere;
  - hot tables within their limits;
  - backups ∪ hot = every generated row not older than the cutoff;
  - every partition under 50 MB;
  - `verify` clean;
  - commit count ≤ 1 + ticks since the last squash;
- a CI workflow running pytest on pushes that touch `radar/**`;
- a first-week fire drill: dispatch `mode=dry-run` and then `force_archive=scan_log` on the live data.

## 8. Interfaces for the other agents
- **Scanner:** follow the writer contract (4.4); the schemas in 1.4; the scan_log `ms.*` and `hot_bytes` fields; the on-demand dispatch rule (5). Never force-push. Never write under `backups/` or `ops/`.
- **Frontend:**
  - read only `radar/state.json`, and optionally `radar/events.jsonl` (kept under 512 KiB) and `ops/health.json`;
  - resolve the head SHA with `GET /repos/{r}/git/ref/heads/data` + If-None-Match (304 responses are free), then fetch `raw.githubusercontent.com/{r}/{sha}/...`;
  - expect the SHA to change after nightly squashes;
  - never read member_ticks or the backups.
- **Alerts routine:** no change required. Optional: raise its 200-row cap to 500 as an emergency brake.