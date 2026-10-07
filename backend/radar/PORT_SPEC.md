# Momentum Radar in Vela: port specification (binding)

The Momentum Radar was built and battle-tested in `anthonynacz/daily-market-analysis`, where it runs on GitHub Actions and stores its tables in a git branch. This port moves it into Vela (EdgeFlow) on the Hetzner VPS. The **signal engine, data layer and calendar are reused as is**. Only the runtime, storage, housekeeping, API, alerts and UI are re-platformed.

- **Source of the code being ported:** `C:/Users/antho/AppData/Local/Temp/claude/C--Users-antho-Documents-Docs-Personal-claudemisc-misc-financial-marketanalysis/56584ef7-bc4e-44cf-adb0-c08f86caa6db/scratchpad/deploy/radar/` (git main `896380a`). It is called `SRC/` below.
- **Reference docs:** `backend/radar/docs/github-spec.md` (the binding spec of the GitHub version; its sections 5, 6 and 12 describe the engine, the state schema and every review fix) and `signal-model.md`, `data-sources.md`, `storage.md`, `runtime.md`.
- **Where this document differs from them, this document wins.**
- Everything the radar produces is educational analysis, not financial advice. Nothing places trades.

## 1. Architecture in Vela

```
docker-compose service `radar` (same image as backend; command: python -m radar.worker)
  radar.worker   long-running scheduler, Postgres advisory lock (single instance)
     -> every 5-min boundary + 50 s in the regular session (warmup 09:10 ET, final tick close + 50 s):
        subprocess `python -m radar.tick ...` (timeout 270 s)  -> reads/writes Postgres via radar.store
     -> after each tick: Discord alerts for new radar entries (services.alerts.dispatch_radar_entries)
     -> nightly 03:30 ET (and on demand): radar.housekeeping (measure -> archive to backups -> 3-month retention)
backend (FastAPI)  GET /api/radar ...  (reads radar tables)      frontend  /radar page (+ Dashboard strip, Settings toggle)
Postgres           radar_* tables (section 4)     volume `radar_backups` mounted at /backups/radar (monthly gz backups)
```

**Why a separate container, not a job inside the backend's APScheduler:**
- The backend runs one replica with FinBERT loaded (about 1.3 GB).
- A tick does blocking HTTP and numpy work every 5 minutes; inside the backend it would stall the API event loop.
- A crash or hang must not take the API down.
- The worker reuses the backend image, so there is no new build or dependency set.

## 2. Ownership (one agent per group; only edit your files)

| Group | Files |
|---|---|
| CORE | `backend/radar/{__init__,types,config,calendar_nyse,fetch,universe,features,baselines,engine,replay}.py`, `backend/radar/data/universe.csv`, `backend/radar/tools/{__init__,replay_cli,refresh_universe}.py`, `backend/tests/radar/test_{fetch,universe,features,baselines,engine,replay,calendar}.py`, `backend/tests/radar/fixtures/**`, `backend/tests/radar/__init__.py` |
| STORE | `backend/db/models.py` (append the radar models only), `backend/alembic/versions/<new>_radar_tables.py`, `backend/radar/store.py`, `backend/tests/radar/test_store.py`, `backend/tests/radar/conftest.py` |
| RUNTIME | `backend/radar/tick.py`, `backend/radar/worker.py`, `docker-compose.yml` (add the `radar` service + `radar_backups` volume only), `backend/tests/radar/test_tick.py`, `backend/tests/radar/test_worker.py` |
| HOUSEKEEPING | `backend/radar/housekeeping.py`, `backend/tests/radar/test_housekeeping.py` |
| API+ALERTS | `backend/api/routes.py` (radar endpoints only), `backend/services/alerts.py` (`radar_entry` only), `backend/services/preferences.py` (`radar_entry` alert key only), `backend/tests/radar/test_api_alerts.py` |
| FRONTEND | `frontend/src/pages/RadarPage.tsx`, `frontend/src/components/radar/**`, `frontend/src/App.tsx` (route), `frontend/src/components/AppNav.tsx` (nav item), `frontend/src/types/index.ts` (radar types), `frontend/src/hooks/useEdgeFlow.ts` (radar hook), `frontend/src/pages/Dashboard.tsx` (one compact "On the radar" strip), Settings Alerts pane (`radar_entry` toggle row only), `frontend/src/pages/knowledge/RadarTab.tsx` + its registration |

Docs (`CLAUDE.md` files) are updated by the orchestrator at the end. Test conventions:
- Tests go under `backend/tests/radar/` and use the existing `backend/tests/conftest.py`.
- No test hits the network or needs a live Postgres. Store and housekeeping tests run on SQLite through SQLAlchemy; Postgres-only statements are guarded.
- Run tests with `cd backend && python -m pytest tests/radar -q -p no:cacheprovider`.
- **Local Python:** `C:/Users/antho/.venv/Scripts/python.exe`. Install packages only with `C:/Users/antho/.local/bin/uv pip install --python <that python> <pkg>`.
- **Production libraries:** production runs numpy 2.5.3, pandas 3.0.5, yfinance 1.7.0 and curl_cffi 0.16.3, while the local venv has numpy 1.26 and yfinance 1.2. For a compatibility run, install the production versions into a throwaway target dir and put it first on PYTHONPATH: `uv pip install --target <scratch>/prodlibs numpy==2.5.3 pandas==3.0.5 yfinance==1.7.0 curl_cffi==0.16.3` (never into the venv). Both must pass.

## 3. Ported modules (CORE)

- **Copy unchanged:** `SRC/{types,calendar_nyse,fetch,universe,features,baselines,engine,replay}.py`, `SRC/data/universe.csv` and `SRC/tools/{replay_cli,refresh_universe}.py`, plus their tests and fixtures. Keep their behaviour byte-for-byte except:
  - imports become `radar.*` (package root = `backend/`);
  - numpy 2 / pandas 3 / yfinance 1.7 compatibility fixes, if any are needed.
- **`config.py`:** keep `PARAMS`, `BUSY_OVERRIDES`, `REFERENCE_SYMBOLS` and `US_EXCHANGES` unchanged. Keep `RUNTIME` but drop GitHub-only keys (`push_budget_s`, `final_push_budget_s`, `hk_dispatch_min_interval_h`). Remove `REPO`, `DATA_BRANCH` and the file `PATHS`. Add:
```python
BACKUP_DIR = os.environ.get("RADAR_BACKUP_DIR", "/backups/radar")
HOUSEKEEPING = {"schedule_et": "03:30", "retention_months": 3}
```
- **The engine's state, snapshot, event and member-row shapes are unchanged.** They are github-spec.md section 6 and section 12, and the API and UI rely on them.

## 4. Postgres schema (STORE): SQLAlchemy models + one Alembic migration

All DateTime columns use `DateTime(timezone=True)`. JSON columns use `sqlalchemy.JSON` (portable to SQLite for tests). The migration is idempotent (`IF NOT EXISTS`), as in `b8d3e1f57c92`, with `down_revision` = the current head (run `alembic heads` to find it).

| Table | Columns | Keys / indexes |
|---|---|---|
| `radar_snapshot` | `id` int PK (always 1), `state` JSON (the github-spec section 6 state.json document), `updated_at` | singleton |
| `radar_runtime` | `id` int PK (always 1), `engine` JSON (the engine.json document: schema, session, engine, source_health, dynamic_adds, pack_gaps, ops, loop), `source_health_live` JSON (written by the Fetcher's on_health during a tick; replaces the GitHub side file), `alert_cursor` text (last event id dispatched to alerts), `updated_at` | singleton |
| `radar_baselines` | `id` PK, `session_date` date, `kind` str (`main`/`extra`), `params_version` str, `pack_gz` LargeBinary (gzip of `BaselinePack.to_json()`), `created_at` | unique (`session_date`, `kind`) |
| `radar_member_ticks` | `id` bigint PK, `tick` timestamptz, `session_date` date, `slot` int, `ticker` str, `role`, `dir`, `state` str, `price`, `chg_day_pct`, `chg_5m_pct`, `move_since_entry_pct` float nullable, `vol_5m` bigint, `intensity` float, `signals` JSON | unique (`tick`, `ticker`); index (`ticker`, `tick`); index (`tick`) |
| `radar_events` | `id` str PK (github-spec event id), `ts` timestamptz, `session_date`, `slot`, `ticker`, `type` (`ENTER`/`EXIT`), `dir`, `price`, `intensity`, `reason`, `detail`, `episode`, `held_min` nullable, `move_since_entry_pct` nullable, `late` bool, `signals` JSON, `params_version` | index (`ts`); index (`ticker`, `ts`) |
| `radar_scan_log` | `id` bigint PK, `tick` timestamptz, `run_id` str, `written_at`, `session_date` nullable, `phase`, `status`, `row` JSON (the full github-spec scan_log row) | unique (`tick`, `run_id`); index (`tick`) |
| `radar_backup_manifest` | `id` PK, `table_name`, `month` (`YYYY-MM`), `path`, `rows`, `bytes`, `raw_bytes`, `sha256`, `content_sha256`, `min_ts`, `max_ts`, `created_at`, `updated_at`, `writes` int, `last_run_id` | unique (`table_name`, `month`) |
| `radar_housekeeping_runs` | `id` PK, `run_id` str unique, `started_at`, `finished_at`, `trigger` (`schedule`/`manual`/`worker_request`), `mode` (`apply`/`dry-run`), `status`, `report` JSON | — |
| `radar_table_metrics` | `id` PK, `run_id`, `measured_at`, `table_name`, `status` (`OK`/`WARN`/`DEGRADED`/`ERROR`), `reasons` JSON, `rows` bigint, `bytes` bigint, `lookup_ms` JSON, `lookup_max_ms` float, `write_p95_ms` float nullable, `action`, `archived_rows`, `purged_rows`, `rows_after` | index (`table_name`, `measured_at`) |

`radar/store.py` (STORE) is the only module that talks to these tables. It uses **sync** SQLAlchemy (psycopg2 in production: `settings.DATABASE_URL` with `+asyncpg` replaced by `+psycopg2`; any SQLAlchemy URL in tests). Pinned API:

```python
class RadarStore:
    def __init__(self, url: str | None = None) -> None: ...            # None -> derived from config.settings.DATABASE_URL
    # reads
    def load_state(self) -> dict: ...                                   # {} if none
    def load_engine_doc(self) -> dict: ...                              # {} if none
    def load_live_health(self) -> dict | None: ...
    def load_pack(self, session_date: str, kind: str) -> tuple[str | None, bytes | None]: ...   # (params_version, gz)
    def recent_events(self, *, since: datetime | None = None, ticker: str | None = None, limit: int = 500) -> list[dict]: ...
    def member_ticks(self, ticker: str, session_date: str) -> list[dict]: ...
    def scan_log_tail(self, n: int = 50) -> list[dict]: ...
    # writes (one transaction per call)
    def commit_tick(self, *, state: dict, engine_doc: dict, member_rows: list[dict], events: list[dict],
                    scan_row: dict, packs: dict[str, tuple[str, str, bytes]] | None = None) -> None: ...
        # upsert snapshot+runtime.engine; insert rows (ON CONFLICT DO NOTHING on the unique keys so a re-run tick is idempotent);
        # packs: {kind: (session_date, params_version, gz)} upserted; clears source_health_live
    def write_live_health(self, health: dict) -> None: ...              # own short transaction; used by Fetcher.on_health
    def write_failure(self, *, state: dict, engine_doc: dict, scan_row: dict) -> None: ...
    def get_alert_cursor(self) -> str | None: ...
    def set_alert_cursor(self, event_id: str) -> None: ...
    def try_advisory_lock(self, key: int) -> bool: ...                  # Postgres pg_try_advisory_lock on a dedicated connection; True on SQLite
    def ping(self) -> bool: ...
```

## 5. Tick and worker (RUNTIME)

- **`radar/tick.py`** is a port of `SRC/tick.py` with the delta/git layer replaced by `RadarStore`. Everything else in its logic stays, including every github-spec section 12 fix:
  - deadline;
  - on_health → `store.write_live_health`;
  - quotes_ok / degraded_volume wiring;
  - pack acceptance and gap retries (max 3/day, only while the source is ok, and never while the chart breaker is open);
  - dynamic adds need 5m + daily bars and take their sector from a batched quotes() call;
  - re-extending dyn adds;
  - same_session_scan with the regular/post rule;
  - the closed heartbeat carry rule;
  - published_late (always 0 here; keep the field);
  - probe mode.
- **Packs** are stored in `radar_baselines` (`main` / `extra`) instead of `.json.gz` files.
- **CLI:** `python -m radar.tick --tick-id <ISO Z> [--warmup] [--final] [--ignore-calendar] [--probe]`. Its last stdout line is the same JSON result as before.
- **`radar/worker.py`** is a port of `SRC/loop.py` scheduling, minus git, retire and handover; the container restarts on its own.
- **Single instance:** at start the worker takes the Postgres advisory lock `radar.worker` (key `771234`). If the lock is not free, it logs and sleeps-retries every 60 s.
- **Waiting for the schema:** it waits until the radar tables exist, polling every 10 s; the backend container runs the Alembic migrations.
- **Ticks:**
  - Each trading day, the warmup runs at `RUNTIME.warmup_et`. Ticks run at every 5-minute boundary + `tick_offset_s` from open + 5 min through close, and the final tick at close + 50 s (half days included).
  - Each tick runs as a subprocess with `tick_timeout_s`.
  - Overruns skip boundaries; the engine catches up.
  - On failure or timeout, the worker writes the failure state via `store.write_failure`, porting `Loop._write_failure`: merge `source_health_live`, bump the closed families on timeout (all of them when there is no live health), and no cross-session carry.
- **After each successful tick:** call `asyncio.run(services.alerts.dispatch_radar_entries(new_enter_events))`. The new ENTER events are those after `alert_cursor`; then advance the cursor. Wrap this in try/except: alert failures never stop the radar.
- **Nightly:** at `HOUSEKEEPING.schedule_et` ET, every day including weekends, run `radar.housekeeping.run(mode="apply", trigger="schedule")`.
- **On request:** also run housekeeping when the tick result has `hk_request` set (at most every 6 h).
- **Signals:** SIGTERM / SIGINT stop the loop within about 5 s and terminate a running tick subprocess (terminate, then kill after 3 s).
- **Logging:** the worker logs to stdout and also writes a `pipeline_run_log` row per tick (phase `radar_tick`) and per housekeeping run (phase `radar_housekeeping`) via `services.run_log`, so the existing /schedule page shows them. Logging must never break the loop.
- **docker-compose:**
```yaml
  radar:
    restart: unless-stopped
    build: ./backend
    command: ["python", "-m", "radar.worker"]
    environment:
      DATABASE_URL: <same as backend>
      RADAR_BACKUP_DIR: /backups/radar
    env_file: [.env]
    depends_on: { db: { condition: service_healthy } }
    volumes: ["./backend:/app", "radar_backups:/backups/radar"]
    mem_limit: 700m
```
  Add `radar_backups:` under `volumes:`.

## 6. Housekeeping and backups (HOUSEKEEPING)

The user's requirement is to back up tables as housekeeping when lookup performance degrades due to volume, with 3-month retention. Implement `radar/housekeeping.py`:

```python
def run(*, mode: str = "apply", trigger: str = "manual", force_tables: list[str] | None = None,
        now: datetime | None = None, store: RadarStore | None = None) -> dict   # returns the report (also stored)
```

**Measure**, per hot table (`radar_member_ticks`, `radar_events`, `radar_scan_log`), recording a row in `radar_table_metrics`:
- row count;
- total bytes (`pg_total_relation_size`; on SQLite an estimate or None);
- lookup latency: median of 3 cold runs of each standard lookup (latest rows for a ticker, a ticker's session series, rows of a day, the last 24 h);
- write p95: the p95 of the tick's DB write time from `radar_scan_log.row.ms.write` over the last 50 rows.

**Classify:**
- DEGRADED when any threshold is crossed.
- WARN at 80% of a threshold.
- ERROR when a backup partition is corrupt or missing; that table is frozen.

Defaults:

| Table | max_rows | max_bytes | max_lookup_ms | hot_days |
|---|---|---|---|---|
| member_ticks | 2,000,000 | 1 GiB | 150 | 14 |
| events | 200,000 | 256 MiB | 100 | 45 |
| scan_log | 300,000 | 512 MiB | 100 | 14 |

The latency rule counts only at 20% or more of max_rows.

**Archive (only when DEGRADED or forced):**
- Rows older than `hot_days` are written to `BACKUP_DIR/<table>/<YYYY-MM>.jsonl.gz`. Port `SRC/storage/housekeeping.py`'s canonical partition logic: sorted, de-duplicated by key, deterministic gzip, written to a temp file, fsynced, re-read and verified, then renamed.
- Only after that are the rows deleted from Postgres, in one transaction, then `radar_backup_manifest` is upserted.
- Re-runs are idempotent: merging the same rows into a month gives the same bytes.

**Retention (every run, degraded or not):** 3 calendar months, row level, UTC, with month-end clamping (port `retention_cutoff`). It applies to:
- hot rows past the cutoff (deleted);
- backup partitions (whole months past the cutoff deleted; the boundary month filtered row by row);
- manifest entries.

**Other behaviour:**
- `dry-run` writes nothing and returns the plan.
- After deleting many rows, run `VACUUM (ANALYZE)` on that table (Postgres only, outside the transaction).
- Record `radar_housekeeping_runs` and a `pipeline_run_log` row.
- Provide a CLI: `python -m radar.housekeeping run|query|restore|verify [...]`. `query` and `restore` read backups plus hot rows, de-duplicated by key, hot copy wins; `verify` re-hashes every partition against the manifest.

## 7. API and alerts (API+ALERTS)

Add these to `backend/api/routes.py` (prefix `/api`, `user = Depends(get_current_user)` like the neighbouring routes; radar data is shared and visible to every user):
- `GET /api/radar` returns the snapshot state (github-spec section 6 state.json) plus `{"stale": bool}`. `stale` is true when the session is open and there has been no scan for more than `RUNTIME.stale_warning_min`. If nothing has been written yet, it returns `{"status": "no_data", ...}` with a 200.
- `GET /api/radar/events?days=1&ticker=` returns events, newest first, limit 500.
- `GET /api/radar/ticker/{ticker}?session=YYYY-MM-DD` returns that ticker's member_ticks for the session (for a detail chart).
- `GET /api/radar/health` returns the scan_log tail (50 rows), the latest housekeeping run and table metrics, and the backup manifest.
- `POST /api/radar/housekeeping?mode=dry-run|apply` is admin only (use the same admin check other admin routes use). It runs in a BackgroundTask and returns 202 with the run id.

**Alerts:** add the alert key `radar_entry`:
- label "Momentum Radar entry", description "A stock enters the 5-minute Momentum Radar (racing up or down)", `tier_min` the same as `news_spike`, default False;
- it must appear wherever the other keys are enumerated (preferences validation set, ALERT_KEYS).
- Add `async def dispatch_radar_entries(events: list[dict]) -> int` in `services/alerts.py`. It mirrors `dispatch_breaking_news`:
  - for each user with `radar_entry` enabled and the tier met, `_dispatch_alert(..., "radar_entry", ticker, key=f"radar_entry:{event_id}", payload)`;
  - the Discord line is `📡 **TICKER** racing ↑/↓ — <detail> (intensity N)`, plus a deep link `PUBLIC_APP_URL/radar` when set;
  - it returns the number of alerts sent.
- The periodic `run_alerts_scan` does **not** scan radar entries; they are pushed by the worker only.

## 8. Frontend (FRONTEND)

**`/radar` page** (lazy `RadarPage.tsx`, nav item "Radar" next to the Scanner):
- Port the content of the GitHub `radar.html` into Vela's design system: Tailwind, `ui/` feedback primitives, `utils/format.ts` formatters, `utils/theme.ts` colours, `usePolling`, and `getApiErrorMessage`.
- Polling: `/api/radar` every 30 s while the tab is visible (usePolling already handles visibility), every 5 min when the session is closed.
- Sections:
  - a status and freshness bar (session state, last scan, next scan, stale and degraded banners);
  - a market-mode banner and sector banners ("+N more held back");
  - members as cards on mobile and a table on desktop: direction, ticker (links `/?ticker=` like other pages), name, state chip, late/back badges, time on radar, move since entry, 5/15/30-minute and day change, RVol, VWAP distance, an intensity bar (never call it probability or confidence), reasons, and a sparkline from `spark`;
  - "Warming up" (heating);
  - "Recently dropped off" with plain-English exit reasons (use `exit_detail` when present);
  - a "How the radar works" collapsible and the disclaimer.
- **Dashboard:** one compact "On the radar now" strip: up to 6 tickers with direction and intensity, linking to `/radar`; hidden when empty.
- **Settings → Alerts:** the `radar_entry` toggle row comes from the backend ALERT_KEYS, the same way the existing rows render. Only add code if the pane hard-codes keys.
- **Knowledge:** a "Radar" tab (`?tab=radar`) explaining the signals in plain language, from signal-model.md.
- **Checks:** `npx tsc --noEmit` and `npm run build` must pass. Check the page with `npm run dev:prodapi` only if the production API already has radar endpoints; otherwise use a local mock via a temporary dev-only fixture, and remove it before finishing.
