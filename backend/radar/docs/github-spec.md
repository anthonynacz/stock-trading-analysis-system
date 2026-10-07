# Momentum Radar: binding specification (v1)

This document is the contract between the modules. Where it differs from the design docs in
`radar/docs/` (signal-model.md, data-sources.md, storage.md, runtime.md), **this document wins**.
Everything the radar produces is educational analysis, not financial advice. Nothing here places trades.

## 1. What it is

**Momentum Radar** is a list of US stocks that are racing up or down right now. It works like this:
- A scanner runs every 5 minutes during the regular session (09:30-16:00 ET).
- It adds a stock when a time-sensitive signal set fires and a confirming bar follows.
- It refreshes every member on every scan.
- It drops a member when its exit rules say the move is over.

Vocabulary:
- A member is "on the radar". It "enters" and "drops off".
- The nav label is `📡 Radar`. The page is `radar.html`. The page title is "Momentum Radar".

The signals are separate from, and faster than, the hourly "Stock Reaction Alerts" routine (4% move or news). They are:
- index-relative thrust z-scores over 15 and 30 minutes;
- the "in play" day move;
- time-of-day relative volume;
- position against VWAP;
- new extremes;
- trend efficiency.

## 2. Architecture

```
GitHub Actions (public repo: free minutes)
  radar-scan.yml   long-running loop job (<=330 min) + watchdog crons, concurrency group radar-scanner
      radar.loop  --every 5 min-->  subprocess `python -m radar.tick` (270 s timeout)
                                          fetch (Yahoo; Nasdaq fallback) -> baselines -> engine
                                          writes a DELTA dir (appends + replacements)
      radar.loop applies the delta to the `data` worktree, commits, pushes (writer contract, section 7)
  radar-housekeeping.yml  nightly 03:30 UTC (+ manual, + rate-limited scanner request)
      measure tables -> archive to monthly backups when degraded -> 3-month retention -> squash data branch
  radar-ci.yml     pytest on pushes touching radar/** or .github/workflows/**

Branches
  main  (GitHub Pages site + code)   radar/ package, radar.html, .github/workflows/*, alerts/log.json
  data  (orphan, never a Pages source) radar/*.json(l), ops/*, backups/*

Browser (radar.html)
  GET api.github.com/repos/{repo}/git/ref/heads/data  (ETag)  ->  sha
  GET raw.githubusercontent.com/{repo}/{sha}/radar/state.json   (fresh, immutable)
```

## 3. File layout and ownership (main branch)

| Path | Owner (implementation agent) |
|---|---|
| `radar/types.py`, `radar/config.py`, `radar/calendar_nyse.py`, `radar/SPEC.md`, `radar/docs/*` | pinned (orchestrator). Additive changes only, and only when unavoidable |
| `radar/fetch.py`, `radar/universe.py`, `radar/data/universe.csv`, `radar/tools/refresh_universe.py`, `radar/tests/test_fetch.py`, `radar/tests/test_universe.py` | DATA |
| `radar/features.py`, `radar/baselines.py`, `radar/engine.py`, `radar/replay.py`, `radar/tools/replay_cli.py`, `radar/tests/test_features.py`, `radar/tests/test_engine.py`, `radar/tests/test_baselines.py`, `radar/tests/test_replay.py` | ENGINE |
| `radar/tick.py`, `radar/delta.py`, `radar/loop.py`, `radar/gitsync.py`, `radar/gate.py`, `radar/requirements.in`, `radar/requirements.lock`, `.github/workflows/radar-scan.yml`, `.github/workflows/radar-ci.yml`, `radar/tests/test_calendar.py`, `radar/tests/test_gate.py`, `radar/tests/test_tick.py`, `radar/tests/test_delta.py`, `radar/tests/test_loop.py`, `radar/tests/test_gitsync.py`, `radar/tests/test_crons.py` | RUNTIME |
| `radar/storage/*`, `.github/workflows/radar-housekeeping.yml`, `radar/tests/test_housekeeping.py`, `radar/tests/test_storage_git.py`, `radar/tests/test_soak.py`, `radar/tests/test_alerts_store.py` | STORAGE |
| `radar.html`, `radar/mock/state.json`, `template/jsx_to_html.py` (pill only), `reports/**/*.html` + `template/MarketMatrix.html` (regenerated), `alerts.html` (link only) | FRONTEND |

Conventions:
- Python 3.12, stdlib plus the pinned lock.
- Tests: `python -m pytest radar/tests -q`, run from the repo root.
- Tests never touch the network, except those marked `@pytest.mark.live`, which are skipped unless `RADAR_LIVE=1`.
- Every timestamp written to disk is UTC `YYYY-MM-DDTHH:MM:SSZ`.

## 4. Module interfaces (pinned)

### 4.1 `radar.calendar_nyse` (pinned, stdlib)
- `session_for(date) -> Session | None`
- `phase_at(datetime) -> (phase, Session|None)`
- `previous_sessions(before: date, n) -> list[Session]` (oldest first)
- `next_sessions(start, n)`
- `Session` methods and attributes: `.open_epoch`, `.close_epoch`, `.n_slots` (78, or 42 on a half day), `.slot_start(j)`, `.slot_of(ts) -> int|None`, `.to_json()`.
- Holidays are hard-coded through 2028.

### 4.2 `radar.fetch` (DATA)
```python
class Fetcher:
    def __init__(self, *, health: dict | None = None, workers: int = 16, timeout_s: float = 12.0): ...
    def quotes(self, symbols: list[str]) -> tuple[dict[str, Quote], FetchReport]      # v7 batch (<=250/req), crumb via yfinance YfData
    def bars_5m(self, symbols: list[str], *, range_: str = "1d", include_prepost: bool = False) -> tuple[dict[str, Bars], FetchReport]
    def daily(self, symbols: list[str], *, range_: str = "3mo") -> tuple[dict[str, DailyBars], FetchReport]
    def movers(self) -> tuple[list[Quote], FetchReport]    # day_gainers + day_losers + most_actives, US exchanges only, deduped
    def health_state(self) -> dict                          # JSON-able; passed back as `health=` next tick
```

**Transport**
- Yahoo v8 chart runs through `curl_cffi` (`impersonate="chrome"`), one thread-local session per worker. Never use plain `requests` against Yahoo: the default User-Agent gets 429.
- Parsing is numpy-first: build no per-symbol pandas frame.
- Off-grid rows (`ts % 300 != 0`) are dropped from the arrays and reported as `last_trade_*`.
- `range_` values used: `"1d"` for ticks, `"1mo"` for baselines.
- `yf.download` must never be used (45-53 s for 542 symbols; UTC index).

**Resilience**
- Retry: up to 3 tries on 429, 5xx or network errors, with exponential backoff and jitter. Other 4xx responses are not retried.
- Degraded mode: when a call degrades, it reduces workers.
- Circuit breaker: after 3 degraded calls in a row, `quotes` and `movers` switch to the Nasdaq screener and `bars_5m` to Nasdaq 1-minute `charttype=rs` resampled to 5 minutes (members only is fine).
- Yahoo is probed again every 3rd call, and the breaker closes after 2 good probes.
- The breaker state lives in `health_state()`.

**Symbols**
- Yahoo style (`BRK-B`). Nasdaq uses `BRK.B` or `BRK/B`, so map both ways.

### 4.3 `radar.universe` (DATA)
- `load_universe() -> list[dict]` reads `radar/data/universe.csv` (542 rows: S&P 500 + Nasdaq-100 + 24 ETFs). Columns: `symbol,name,sector,industry,in_sp500,in_ndx,is_etf`.
- `scan_symbols() -> list[str]` returns the non-ETF universe plus `REFERENCE_SYMBOLS`.
- `dynamic_candidates(movers: list[Quote], known: set[str], params) -> list[Quote]` returns US-exchange EQUITY names not already known, with market cap ≥ $2B, price ≥ $5 and |change| ≥ 3%.

### 4.4 `radar.baselines` (ENGINE)
```python
@dataclass
class SymbolBaseline:
    symbol: str; name: str | None; sector: str | None
    sigma5: float; beta: float; vm: np.ndarray; cvm: np.ndarray      # vm/cvm length = 78 (normal-day slots)
    sigma_d: float; adv20_usd: float; medbar_usd: float; missing_share: float; n_base: int
    prev_close: float | None; eligible: bool; ineligible_reason: str | None
@dataclass
class BaselinePack:
    asof: str; params_version: str; tod_m: np.ndarray; sigma5_spy: float
    symbols: dict[str, SymbolBaseline]
    def to_json(self) -> dict: ...
    @classmethod
    def from_json(cls, d: dict) -> "BaselinePack": ...
def build_pack(session: Session, bars5: dict[str, Bars], daily: dict[str, DailyBars],
               meta: dict[str, dict], params: dict) -> BaselinePack
def extend_pack(pack: BaselinePack, session: Session, bars5: dict[str, Bars],
                daily: dict[str, DailyBars], meta: dict[str, dict], params: dict) -> BaselinePack
```
- The pack is built from the 20 complete sessions **before** `session` (half-days excluded), per signal-model.md §1.3. There is no look-ahead.
- `meta[sym]` = `{"name":..., "sector":...}`.
- Half-day sessions use slots `0..41` of the same `vm`/`tod_m` arrays.

### 4.5 `radar.engine` (ENGINE)
```python
@dataclass
class TickOutput:
    processed_slots: list[int]
    events: list[dict]          # events.jsonl rows (section 6)
    member_rows: list[dict]     # member_ticks.jsonl rows (section 6), one per member/heating symbol per processed slot
    snapshot: dict              # keys: members, heating, recent_exits, market, sector_banners, counts (state.json shapes, section 6)
class Engine:
    def __init__(self, params: dict, pack: BaselinePack, session: Session, state: dict | None = None): ...
    def stage_a(self, quotes: dict[str, Quote], now_epoch: int) -> list[str]   # symbols needing bars this tick
    def step(self, bars: dict[str, Bars], now_epoch: int, *, halted: set[str] = frozenset()) -> TickOutput
    def state_dict(self) -> dict                                                # JSON-able, small (<200 KiB)
```
- **step** processes, in order, every regular-session slot `j` with `slot_start(j) + 300 + bar_final_grace_s <= now_epoch` that is not yet processed (catch-up is allowed; entries made more than 2 bars behind real time get `late: true`).
- **stage_a** returns the Stage-A passers (signal-model.md §1.4), all members, all HEATING names and `REFERENCE_SYMBOLS`. If a quote is missing for a member, the member is still returned.
- **state** from another session date is ignored: fresh session state.
- **Determinism:** the same inputs must give the same outputs. `replay.py` drives the same `Engine`.
- **Missing data never evicts a member.** It counts toward `DATA_STALE` only when the symbol has no fresh bars and the source is otherwise healthy (signal-model.md §6.1).

### 4.6 `radar.replay` (ENGINE)
```python
run(bars_by_session: dict[str, dict[str, Bars]], daily: dict[str, DailyBars], params: dict, sessions: list[str]) -> dict
```
- It returns a scorecard dict per signal-model.md §8.2 (entries/day, prec@15/30/60, hold30, flap rate, dwell, concurrency, post-exit continuation), plus the two baselines.
- CLI: `python -m radar.tools.replay_cli --bars <csv.gz> --daily <csv.gz> [--sessions N]`.

### 4.7 `radar.delta` (RUNTIME)
A delta is a directory. It is binary-safe and idempotent to re-apply on a fresh checkout:
```
<delta>/meta.json                 {"tick_id": "...Z", "created_at": "...Z", "kind": "tick|warmup"}
<delta>/append/<relpath>          bytes appended to data:<relpath> (JSONL rows, each ending in \n)
<delta>/replace/<relpath>         full replacement of data:<relpath>
```
- API: `write_delta(root, tick_id, appends: dict[str, list[dict]], replaces: dict[str, bytes|dict])`, `apply_delta(delta_dir, data_worktree)`, `list_pending(pending_root) -> list[Path]` (tick order).
- JSONL rows use compact separators and `ensure_ascii=False`.
- JSON replacements use sorted keys and are small.

### 4.8 `radar.tick` (RUNTIME)
`python -m radar.tick --data-dir <data worktree> --pending-dir <dir> --tick-id <ISO boundary Z> [--warmup] [--final] [--ignore-calendar]`

The tick reads only from the data worktree, which already holds every applied-but-unpushed delta. Steps:
1. Read `radar/engine.json`, a JSON object with these top-level keys: `{"schema":1,"session":..., "engine": Engine.state_dict(), "source_health": Fetcher.health_state(), "dynamic_adds": [...], "ops": {"hk_dispatched_at": ts|null}, "loop": {...}}`.
2. Load the baseline pack from `radar/baselines.json.gz` (+ `baselines_extra.json.gz`) when its `asof` equals today's session. Otherwise build it: fetch `1mo` 5m bars and `3mo` daily bars for `scan_symbols()` and write it (this is the `--warmup` job, which also runs on demand).
3. `quotes(scan_symbols + dynamic_adds)` and `movers()`. New dynamic candidates get baselines via `extend_pack`, up to `max_adds_per_day`.
4. `Engine.stage_a` → `bars_5m(stage_b, range_="1d")` → `Engine.step(now)`.
5. Write one delta. It appends to member_ticks, events and scan_log, and replaces `radar/state.json` and `radar/engine.json` (and a baselines file when it changed).
6. Print one last stdout line of JSON: `{"status":"ok|degraded|no_data|closed|error","message":str,"members":int,"entered":[..],"exited":[..],"processed_slots":[..],"source":{...},"ms":{...},"hot_bytes":{...}}`. The exit code is 0 unless there is a bug.

Outside the regular session (and without `--ignore-calendar`) it writes a heartbeat `state.json` with `status:"closed"`, the previous `recent_exits`, and no engine step.

### 4.9 `radar.loop`, `radar.gitsync`, `radar.gate` (RUNTIME)
- **Session:** regular session only. Warmup runs from `RUNTIME.warmup_et` (09:10 ET).
- **Tick times:**
  - Ticks run at every 5-minute boundary + `tick_offset_s` (50 s) from 09:35 through close.
  - The last tick is at close + 50 s, flagged `--final`. It processes slot 76/77 and SESSION_END.
  - Half day: the close is 13:00 ET.
- **Tick execution:** each tick is a subprocess with `tick_timeout_s`.
- **Overruns:** after an overrun, the next boundary is computed from now, and skipped boundaries are counted. The engine catch-up keeps results identical.
- **Retire:** the loop retires after `loop_retire_min`.
- **Signals and flush:** SIGTERM is honoured. An `always()` step flushes the pending deltas.
- **The loop owns git.** After each tick it applies the delta to the worktree, commits, and pushes per section 7.
- **scan_log timing:** the tick's scan_log row cannot contain its own push timing, so the tick records the previous tick's git timings (read from `engine.json.loop`, which the loop updates) as `git_prev`.
- **Token scrubbing:** `GITHUB_TOKEN`, `GH_TOKEN` and `ACTIONS_*` are removed from the tick subprocess environment.
- **Gate:** `python3 -m radar.gate` (stdlib) writes `run=true|false`, `reason`, `window_start` and `window_end` to `$GITHUB_OUTPUT`. `run=true` when `window_start - 120 min <= now < window_end`, where the window is [09:10 ET, close + 5 min].

## 5. Signal model

This is **signal-model.md sections 1-10 exactly, with `radar.config.PARAMS`**, plus these amendments:

1. **Grace and tick offset:** `bar_final_grace_s` = 45 and the tick offset is 50 s, so each tick processes the bar that just closed. Verify on the first live session: if Yahoo bars are not final at +45 s, raise both together.
2. **Dynamic universe adds:** US-exchange equities from Yahoo movers screeners that are not in `universe.csv`, with market cap ≥ $2B, price ≥ $5 and |day change| ≥ 3%.
   - They receive baselines on first sight and then follow the same eligibility rules (§3 of signal-model.md).
   - They are capped at `dynamic.max_adds_per_day`, and they persist for the session.
3. **Sector map:** `universe.csv` sector for universe names, the Yahoo quote/`info` sector for dynamic adds, and `"Unknown"` otherwise. Only the sector cap and the banner use it.
4. **Halt evidence:** use the missing-bar heuristic (b) only. The Nasdaq halt RSS is out of scope for v1.
5. **Pre-market gap watch:** out of scope for v1. The engine never runs outside 09:30-16:00.
6. **Intensity (0-100):** this is the display name of the composite "score". Store it as `intensity`.

## 6. Tables and schemas (data branch unless noted)

| Table | Path | Kind | PK | ts field | Browser reads |
|---|---|---|---|---|---|
| state | `radar/state.json` | snapshot | – | `tick_id` | **yes (only this)** |
| engine | `radar/engine.json` | snapshot | – | – | no |
| baselines | `radar/baselines.json.gz`, `radar/baselines_extra.json.gz` | snapshot | – | – | no |
| member_ticks | `radar/member_ticks.jsonl` | append | (`tick`,`ticker`) | `tick` | no |
| events | `radar/events.jsonl` | append | `id` | `ts` | no |
| scan_log | `radar/scan_log.jsonl` | append | (`tick`,`run_id`) | `tick` | no |
| table_metrics | `ops/table_metrics.jsonl` | append | (`run_id`,`table`) | `measured_at` | no |
| housekeeping_runs | `ops/housekeeping_runs.jsonl` | append | `run_id` | `started_at` | no |
| health | `ops/health.json` | snapshot | – | `as_of` | optional |
| manifest | `backups/manifest.json` | index | – | – | no |
| backups | `backups/<table>/<YYYY-MM>.jsonl.gz` | partitions | table pk | table ts | no |
| alerts_log | **main**:`alerts/log.json` | `{"alerts":[...]}`, newest first | (`ts`,`ticker`) | `ts` | alerts.html |

**`radar/state.json`** (≤ 256 KiB; typical < 30 KiB)
```json
{"schema":1,"generated_at":"Z","tick_id":"Z","last_bar":"Z|null","status":"ok|degraded|no_data|closed|error","message":"",
 "session":{"date":"YYYY-MM-DD","phase":"pre|regular|post|closed","open":"Z","close":"Z","half_day":false},
 "next_tick_at":"Z|null","params_version":"radar-sm-1",
 "source":{"name":"yahoo|nasdaq","status":"ok|degraded|down","consecutive_failures":0,"last_ok_at":"Z|null"},
 "market":{"mode":"normal|market","dir":"up|down|null","spy_chg_day_pct":0.0,"spy_z30":0.0,"breadth30":0.0},
 "counts":{"universe":0,"stage_b":0,"members":0,"heating":0,"entered_today":0,"exited_today":0},
 "members":[{"ticker":"","name":"","sector":"","direction":"up|down","state":"racing|cooling|halted","late":false,
   "entered_at":"Z","entry_price":0.0,"last_price":0.0,"last_bar_at":"Z","minutes_on_radar":0,
   "move_since_entry_pct":0.0,"peak_since_entry_pct":0.0,"chg_5m_pct":0.0,"chg_15m_pct":0.0,"chg_30m_pct":0.0,
   "chg_day_pct":0.0,"rvol":0.0,"rvol_day":0.0,"vwap_dist_pct":0.0,"z15":0.0,"z30":0.0,"zday":0.0,
   "intensity":0,"reasons":["short plain-English strings"],"soft_fails":0,"episode":1,
   "spark":{"t0":"Z","step_s":300,"entry_i":0,"p":[0.0]}}],
 "heating":[{"ticker":"","name":"","direction":"up|down","since":"Z","price":0.0,"chg_day_pct":0.0,"intensity":0,"reasons":[]}],
 "recent_exits":[{"ticker":"","name":"","direction":"up|down","entered_at":"Z","exited_at":"Z","minutes_on_radar":0,
   "move_since_entry_pct":0.0,"exit_reason":"FADE|DRY|STALL|REVERSAL|GIVEBACK|VWAP_CROSS|SESSION_END|HALT_LONG|DATA_STALE|DISPLACED","exit_detail":""}],
 "sector_banners":[{"sector":"","direction":"up|down","count":0,"tickers":[""]}],
 "health":{"ticks_today":0,"ticks_skipped":0,"last_tick_ms":0,"loop_run_id":"","loop_started_at":"Z|null","push_backlog":0},
 "disclaimer":"Educational analysis of what is moving now, not a forecast and not financial advice."}
```
- `recent_exits` holds the current session, newest first, at most 30. After the close it keeps that session's exits until the next session starts.
- `spark.p` holds the closes from up to 6 slots before entry through now, at most 80 points. `entry_i` is the index of the entry point.
- `reasons` are 1-3 plain strings built from the features, e.g. `"+2.9σ vs market in 15 min"`, `"volume 3.4× normal for this time"`, `"new high of day, above VWAP"`.

**`radar/member_ticks.jsonl`**: one row per member or heating symbol per processed slot:
```json
{"v":1,"tick":"<bar close = slot_start+300, Z>","session":"YYYY-MM-DD","slot":0,"ticker":"","role":"member|heating",
 "dir":"up|down","state":"racing|cooling|halted|heating","price":0.0,"chg_day_pct":0.0,"chg_5m_pct":0.0,
 "move_since_entry_pct":null,"vol_5m":0,"intensity":0.0,
 "signals":{"z3":0.0,"z6":0.0,"zday":0.0,"rvol3":0.0,"rvolc":0.0,"dvwap":0.0,"er6":0.0,"acc":0.0}}
```

**`radar/events.jsonl`**:
```json
{"v":1,"id":"<YYYYMMDDTHHMMZ of ts>-<TICKER>-<ENTER|EXIT>-<episode>","ts":"<bar close Z>","session":"YYYY-MM-DD","slot":0,
 "ticker":"","type":"ENTER|EXIT","dir":"up|down","price":0.0,"intensity":0.0,"reason":"ENTRY|<exit code>","detail":"<=160 chars",
 "episode":1,"held_min":null,"move_since_entry_pct":null,"late":false,"signals":{...same keys...},"params_version":"radar-sm-1"}
```

**`radar/scan_log.jsonl`**: one row per tick run:
```json
{"v":1,"tick":"<tick_id Z>","run_id":"gha-<run_id>-<attempt>|local","written_at":"Z","session":"YYYY-MM-DD|null","phase":"",
 "status":"ok|degraded|no_data|closed|error","lag_s":0,"universe":0,"stage_b":0,"quotes_ok":0,"quotes_err":0,"bars_ok":0,"bars_err":0,
 "members":0,"heating":0,"entered":[],"exited":[],"processed_slots":[],"source":"yahoo|nasdaq",
 "ms":{"fetch_quotes":0,"fetch_movers":0,"fetch_bars":0,"baselines":0,"compute":0,"write":0,"total":0},
 "git_prev":{"stage":0,"commit":0,"push":0,"attempts":0,"status":"ok|resynced|failed|none"},
 "hot_bytes":{"member_ticks":0,"events":0,"scan_log":0,"state":0},"errors":[]}
```

The ops, manifest and backup schemas are **exactly storage.md §1.4**.

## 7. Git writer contract (everyone who writes `data`)

This is storage.md §4.4, and it replaces runtime.md's rebase approach. On each attempt:
1. `git fetch --no-tags origin +refs/heads/data:refs/remotes/origin/data`
2. `git checkout -f -B data origin/data && git reset --hard origin/data && git clean -fdx`, excluding nothing in the worktree. The pending deltas live **outside** the worktree.
3. Re-apply every pending delta in tick order (`delta.apply_delta`).
4. Commit with parent = the fetched head, as `github-actions[bot]`, message `radar <HH:MM>Z <status> · <n> on radar (+a/-b)`.
5. `git push origin HEAD:refs/heads/data`. **Never force.**
6. On rejection, go back to step 1: at most 5 attempts within the push budget, with backoff `min(30, 2^n)·U(0.5,1.5)` s.

Rules:
- Once a push lands, delete the delta dirs it contained.
- Never merge or rebase.
- Only housekeeping may force-push, with `--force-with-lease=refs/heads/data:<sha read>`, when it squashes.
- The data worktree is checked out with `fetch-depth: 1`.
- No workflow triggers on pushes to `data`.

## 8. Housekeeping and backups (storage.md, with these decisions)

**Degradation**
- A table is DEGRADED per storage.md §2.3, with the thresholds in the storage.md §2.3 table.
- The snapshots (`state.json` 256 KiB, `engine.json` 256 KiB, `baselines*.json.gz` 2 MiB, `health.json` 64 KiB, `manifest.json` 256 KiB) get guards only.

**Archive and retention**
- When DEGRADED, rows older than `hot_days` go to `backups/<table>/<YYYY-MM>.jsonl.gz`. Each partition is verified before the hot table is trimmed, and trimming continues down to `target_ratio`.
- **Retention is 3 calendar months**, row level, UTC (storage.md §3.4). It applies on every run to hot tables, backups, the manifest, ops tables and `alerts/log.json`.

**Schedule**
- Daily cron `30 3 * * *` UTC.
- Manual `workflow_dispatch` with inputs `mode` (apply|dry-run), `squash` (auto|force|skip), `force_archive`, `reason`.
- **On-demand request from the scanner:** the tick sets `engine.json.ops.hk_request` when any per-tick table exceeds 1.25 × `max_bytes`, or when p95(`git_prev.stage + commit`) exceeds 1,500 ms. The loop dispatches `radar-housekeeping.yml` via the REST API with `{mode: apply, squash: skip}`, at most every 6 h. The scan job needs `actions: write` for this.

**Squash**
- The data branch is squashed nightly (auto window 02:00-07:00 UTC, or weekends), with the lease.
- Guards before touching data:
  - (a) the calendar says no scan window is within 70 minutes;
  - (b) no `radar-scan.yml` run is `in_progress` (API; `actions: read`).
- If either guard fails, skip the squash but still archive.

**Alerts log**
- `alerts/log.json` stays on main and is trimmed through the contents API (compare-and-swap on `sha`) only after the data-branch backup push is confirmed.
- `max_rows` is 180, below the routine's 200 cap.

**Tooling**
- `python -m radar.storage.housekeeping run|query|restore|verify ...`
- The restore procedure is in storage.md §6.

## 9. Workflows

**`radar-scan.yml`**
- Crons: starter `33 12 * * 1-5`; watchdogs `7,37 13-21 * * 1-5`.
- `concurrency: {group: radar-scanner, cancel-in-progress: false}`.
- `timeout-minutes: 350`.
- Permissions: `contents: write` and `actions: write` (the housekeeping request only).
- Checkouts: main (sparse `radar`, `persist-credentials: false`) and data (`path: _data`, `fetch-depth: 1`).
- The gate runs before Python setup.
- Install with `pip install --require-hashes -r radar/requirements.lock`.
- `workflow_dispatch` inputs: `mode` = `loop|once|probe` (probe = fetch-only diagnostics that write nothing: timings, 429 counts, bar finality) and `push` (bool; watch the null-vs-false caveat in runtime.md).
- Pin every action to a full commit SHA (with a comment naming the tag).

**`radar-housekeeping.yml`**
- Permissions: `contents: write`, `actions: read`.
- Concurrency group `radar-housekeeping`.
- `timeout-minutes: 20`.

**`radar-ci.yml`**
- Runs on push and pull_request touching `radar/**` or `.github/workflows/**`.
- Ubuntu, Python 3.12, installs from the lock, runs `pytest radar/tests -q`.

## 10. Page (`radar.html`)

The design is runtime.md §3 adapted to `radar/state.json`. It is dependency-free, uses the site's dark theme, is mobile-first, and has 16 px gutters. It shows:
- members (direction arrow, ticker, name, state, time on radar, move since entry, 5/15/30-minute and day change, relative volume, VWAP distance, intensity bar labelled "intensity" with no probability wording, reasons, sparkline with the entry marker);
- "warming up" (heating) names in a muted style;
- recently dropped off, with plain-English exit reasons;
- sector banners;
- market-mode banner;
- a freshness and status bar that goes stale after `stale_warning_min` without a scan while the session is open;
- the session state (pre-open "Radar starts at 09:35 ET", closed, holiday);
- disclaimer.

**Data loading**
- Resolve the `data` head SHA via `GET /repos/{repo}/git/ref/heads/data` (with `If-None-Match`), then fetch `raw.githubusercontent.com/{repo}/{sha}/radar/state.json`.
- Anonymous 304s still cost quota (measured), so without a token poll once per expected tick (`next_tick_at` + 40 s) plus on visibility/focus, and at most every 15 min when closed.
- With the token from localStorage key `mm_alerts_gh_token`, poll every 30 s.
- On rate-limit or error, fall back to the branch raw URL (≤ 5 min stale) and say so.
- `?mock=1` loads `radar/mock/state.json` from the site for local testing.

**Optional opt-in** in-page Notification when a ticker enters while the page is open. No email.

**Links**
- `template/jsx_to_html.py` adds a `📡 Radar` pill next to `🔔 Alerts`, and every report HTML is regenerated with the converter. Embedded report content must stay byte-identical, as before.
- `alerts.html` links to `radar.html` and back.

## 11. Definition of done (per agent)
- Own files only.
- Unit tests pass with no network.
- Code follows the interfaces above exactly.
- Report any interface gap instead of silently diverging.
- The ENGINE replay on the design dataset (`scratchpad/radar_design/replay/`) must reproduce signal-model.md REC within tolerance: entries/day 3-12 on the 542 universe, prec@30 ≥ 53%, flap rate ≤ 6%.

## 12. Review amendments (binding, 2026-10-06)

These come from the adversarial review (findings in brackets). Where they change earlier sections, they win.

### 12.1 Fetcher (DATA-1, DATA-2, E2E-1, DATA-7)
- `Fetcher.__init__` gains keyword-only `deadline: float | None` (an absolute `time.monotonic()`-style value on the fetcher's own clock) and `on_health: Callable[[dict], None] | None`.
- **Deadline:** past the deadline, no new request is started and no retry is made. The call returns what it has, graded `down` if nothing came back, so the tick always reaches its own `_finish`.
- **Fail fast:** a Yahoo call stops issuing requests once 20 consecutive replies are 429 or network errors with no 200 in between, and is graded `down`.
- **Crumb timeout:** the cookie and crumb fetch must honour `timeout_s`.
- **Breaker per endpoint family:**
  - The families are `chart` (bars_5m, daily) and `crumb` (quotes, movers). Each has its own consecutive_failures, breaker and probe counter.
  - `health_state()` keeps top-level `name`, `status`, `consecutive_failures` and `last_ok_at`, derived as the worst of the two families (`name` is `nasdaq` if either breaker is open). It adds `families: {chart: {...}, crumb: {...}}`.
  - Restoring the old flat format must work.
- **on_health:** called with `health_state()` after every public call. The tick uses it to write `<pending_dir>/source_health.json` atomically.
- **Nasdaq replies:** an HTTP 200 with `data: null` and a `bCodeMessage` is retryable (treat as 503). `BRK-B` has no Nasdaq chart and is skipped in fallback.
- **Provisional bars (DATA-4):** `Bars.provisional_last` is True when the newest grid row starts at t and the last trade is still inside it (`last_trade_ts < t + 300`). Yahoo closes a row only when the first trade after its end arrives (it folds that trade into the row, and later trades open the next row), so until then the row can still change. This was measured live on thin names; the earlier wording (`>= t + 300`) was backwards. Without a last-trade row the flag is False.

### 12.2 Engine (ENG-1, ENG-2, ENG-3, E2E-1, E2E-2, DATA-4, DATA-6)
- `baselines.build_pack` raises `ValueError` when SPY has no prev_close.
- **DATA_STALE** counts consecutive bars with no fresh print (`present and v > 0` for slot k), not 3-bar windows. The 3-bar `fresh` window stays for entry and confirmation.
- **Name and sector** are stored in each member and heating dict at entry, and read from there.
- **Missing baselines:** symbols in state but missing from the pack never raise. Heating entries are dropped. Members stay frozen and exit at SESSION_END.
- **`Engine.stage_a(quotes, now_epoch, *, quotes_ok: bool = True)`:** when `quotes_ok` is False, return only members, heating names and REFERENCE_SYMBOLS. Never widen to the whole universe during an outage.
- **`Engine.step(bars, now_epoch, *, halted=frozenset(), degraded_volume: bool = False)`:** while `degraded_volume` is True (bars came from the Nasdaq fallback), make no new HEATING or entries and suspend DRY soft-fails. Members are still refreshed and can exit on price-based rules.
- **Provisional bars:** for a symbol whose slot-k bar is provisional (`provisional_last` and the newest row is slot k), make no new HEATING, confirmation or entry, and count no DRY/STALL soft-fail at that slot.
- **Sector banners** list only names that are not members. `count` is the number of names held back beyond the cap.

### 12.3 Tick, loop and workflows (RT-1, RT-2, ENG-1, ENG-3, DATA-3, DATA-4, DATA-5, E2E-4)
- **Deadline:** the tick passes `deadline = start + tick_timeout_s - 40 s` to the Fetcher, and `on_health` writes `<pending_dir>/source_health.json`.
- **Failure handling:** when a tick fails or times out, `Loop._write_failure` merges `source_health.json` (if present) into `engine.json.source_health`, then deletes it. On `timeout` it also raises the worst family's `consecutive_failures` by 1, and opens that breaker once it reaches the trip count.
- **Stage A quotes:** the tick passes `quotes_ok = (quotes report status != "down")` to `stage_a`.
- **Fallback bars:** it passes `degraded_volume = (bars report source == "nasdaq")` to `step`.
- **Pack acceptance:** the pack is accepted only if SPY has 5m and daily bars, 5m coverage ≥ 80% and daily coverage ≥ 80%. Otherwise nothing is written and the next tick retries.
- **Pack gaps:**
  - Universe names missing from an accepted pack, or ineligible for `no prior close` / `no daily history`, are retried on later ticks, at most 3 times per day and only while the source is ok. They are stored in `baselines_extra` without using the dynamic budget.
  - Names still missing are listed in scan_log `errors`.
- **Dynamic adds:**
  - They need both 5m and daily bars.
  - Their sector comes from one batched `quotes()` call (Yahoo screens carry no sector), not `"Unknown"`.
  - When `dynamic_adds` is non-empty but `baselines_extra` is missing or rejected, the tick re-extends those symbols before building the Engine.
- **`state.json.health.push_backlog` is replaced by `published_late`:** the number of earlier scans this commit delivered after failed pushes. The page labels it "Published late: N earlier scans".
- **Cancel handling:** `radar-scan.yml` runs `exec python -m radar.loop ...`. The tick runs via Popen and is polled every second so a stop request terminates it, then kills it after 3 s. `gitsync.publish` holds an exclusive `fcntl.flock` (no-op on Windows) on `<pending_dir>/.publish.lock`.
- **Probe:** the probe samples SPY, QQQ, AAPL plus about 10 thin eligible names (lowest `medbar_usd` from the stored pack, else a fixed thin list) at offsets up to 300 s. It reports per-name stability and whether a fold was in progress.
- **CI:** `radar-ci.yml` runs `pytest radar/tests -m "not soak"` on pushes and PRs. The soak tests carry `@pytest.mark.soak` and run in a separate job, on `workflow_dispatch` and weekly on Sunday.

### 12.4 Storage (STO-1, STO-2, STO-3, STO-4)
- **JSONL splitting:** JSONL is split on `"\n"` only, never `str.splitlines()`.
- **Alerts backup:** every apply run merges all current alerts_log rows into its monthly backup partitions (backup only). Rows are removed from main only when the table is DEGRADED, by heal, or by retention.
- **Daytime runs:** main is written (Phase B) only by the scheduled run, or by any run outside weekdays 13:00-21:30 UTC. Otherwise the main trim is `deferred` and done by the next nightly heal.
- **Query matching:** `query --where k=v` matches both the Python and the JSON spelling of the value (`false`, `null`).

### 12.5 Page (E2E-2, E2E-3, E2E-4)
- **Exit rows** show `exit_detail` alone when present, else the plain-English label for `exit_reason`.
- **Status banners** show `message` as is for degraded, no_data and error.
- **Sector banners** read "+N more held back (tickers)" with a singular form for N = 1.
- **`next_tick_at`** is the expected scan start (5-minute boundary + 50 s).

### 12.6 Integration-rehearsal amendments (binding, 2026-10-07)
- **No cross-session carry (INT-1):**
  - An in-session state (any status other than `closed`) may carry `recent_exits`, `counts.entered_today` / `exited_today` and `last_bar` from the previous `state.json` only when that state was a scan of the same session: same `session.date`, `session.phase` of `regular` or `post` (the final tick runs after the close) and status not `closed`. A closed heartbeat that is already labelled with the session it waits for keeps carrying the previous session's exits, counts and last_bar.
  - Otherwise these start empty, zero and null.
  - This applies to `tick._carry` / `_prev_same` and to `loop._write_failure`.
- **Alerts heal on main (INT-2):**
  - Backup-only copies of alerts_log rows never cause a heal.
  - A row is removed from main only when the table is DEGRADED, by retention, or when it is recorded as a pending main trim, i.e. rows archived by a run whose Phase B was deferred or failed.
  - Housekeeping keeps that pending list in `ops/pending_main_trim.json` and clears it once the trim lands.
  - Main therefore keeps alerts until 180 rows or 3 months, as SPEC 8 intended.
- **Timeout bump without evidence (INT-4):** when a killed tick left no `source_health.json`, `Loop._write_failure` bumps every family whose breaker is still closed (one count each).
- **No baseline downloads while the chart breaker is open (INT-5):** dynamic-add extensions and pack-gap retries are skipped, so the 1mo/3mo calls never act as unscheduled probes. They resume once the chart family is ok again.
- **Probe (INT-3):** the probe's "fold in progress" column uses the same rule as `fetch._parse_bars` (section 12.1). It reports, per sampled name, whether bar k was provisional at the probe offset and whether it changed later.
