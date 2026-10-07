# SUMMARY
I measured every source on Sunday 2026-09-27 from this machine's home connection, with the market closed. Values reflect Friday 2026-09-25.
- **Yahoo is fast enough to scan the whole universe every 5 minutes. Don't use `yf.download` in the loop.** It took 45-53 s for 542 symbols (5m bars, 1d or 5d), and adding threads (up to 32) didn't help. The overhead is in yfinance itself. Its multi-ticker index also comes back in UTC, not America/New_York.
- **Calling Yahoo's chart API directly is much quicker.** v8/finance/chart through curl_cffi Chrome impersonation, with 32 worker threads, fetched all 542 symbols in 3.0-3.6 s (7 s with 16, 12 s with 8). Each request took about 0.17 s (p95 up to 0.45 s). v7/finance/quote returns all 542 symbols in one request (0.4-0.9 s; it needs a cookie and crumb). Predefined screeners take 0.13-1.1 s each and return at most 250 rows.
- **One simulated full tick took about 4.5 s, and I saw no throttling.** A tick was 1 batch quote, 3 screeners and 542 chart requests. About 9,000 Yahoo requests in roughly 50 minutes (bursts near 180 per second) got zero 429s. The one throttle I found: plain python-requests with its default User-Agent gets 429 "Edge: Too Many Requests" immediately.
- **Bar details that matter:** Yahoo's 5m extended-hours bars always show volume 0. Summed regular-session bar volume is only about 77% of the daily volume (median). Raw chart responses end with an extra "last trade" row whose timestamp isn't on the 5-minute grid.
- **Universe:** 542 symbols (503 S&P 500 + 15 Nasdaq-100-only names + 24 ETFs). Wikipedia, a maintained GitHub list and Nasdaq's official API agree 100%.
- **Nasdaq fallback works without a key** (browser-like headers needed). One 2.2 MB request (1.5-3.4 s) covers about 7,000 stocks. A per-symbol 1-minute chart includes per-minute share volume for pre- and post-market. Its multi-symbol quote endpoint stops at 20 symbols without an error.
- **Replay dataset saved:** 60 sessions of 5m bars with pre/post-market data (2026-07-02 to 2026-09-25; 4,255,825 rows; 52 MB csv.gz) plus 1 year of daily bars (135,497 rows).
- **Recommendation per tick:** 1 batch quote for the whole universe plus 3 screeners to find candidates, then 5m bars only for current list members and the top ~60 candidates. That's about 65 requests and 2-4 s per tick. Do a full-universe bar sweep only when the engine starts or after a gap. Use backoff, a circuit breaker and the Nasdaq fallback.

# RISKS
- GitHub Actions runners use cloud (datacenter) IPs. All my measurements came from this machine's home connection, where about 9,000 Yahoo requests got zero 429s. Yahoo has throttled or blocked cloud IPs before. Run a probe workflow (workflow_dispatch) from Actions with the same tick simulation before committing to the design.
- Nothing was observed during a live session, because today is Sunday. Still to confirm on Monday 2026-09-28: the partial last bar and off-grid last-trade row, how long after the boundary a finished bar is final, the pre-market quote fields (preMarketPrice and friends), whether screeners update in pre-market, and how fresh the Nasdaq screener data is.
- Yahoo 5m bars show zero volume in pre- and post-market, and the regular-session bars leave out the opening and closing auctions (bar volume is about 77% of daily volume; JPM only 49%). Relative-volume baselines must come from the same 5m bars, not averageDailyVolume3Month. Pre-market volume signals would need Nasdaq's per-symbol chart=rs endpoint, one request per symbol.
- All of these endpoints are unofficial (Yahoo v7 quote, v8 chart, screener; api.nasdaq.com) and can change without notice. Examples already seen: v7 quote needs a cookie and crumb (401 without); spark caps at 20 symbols; Nasdaq's watchlist silently truncates at 20; plain requests with the default User-Agent gets 429 immediately. Pin yfinance and curl_cffi versions and keep the fallback path working.
- Yahoo screener gotchas: at most 250 rows per call; predefined screeners ignore offset; custom queries need size, not count; predefined and custom totals differ (116 vs 128), so predefined results look cached; custom results include OTC tickers (PNK/OID) that must be filtered out.
- Index membership changes. Wikipedia's Nasdaq-100 table moved to a new page name. Refresh the universe weekly and cross-check against the GitHub dataset and the Nasdaq API; all three agreed today.
- Per-symbol pandas parsing costs 8.6 ms per symbol, which makes a full sweep take 8 s instead of 3 s. The engine should parse with numpy and build one frame per tick. yf.download is not suitable for the loop: 45-53 s for 542 symbols, and it returns a UTC index.
- The replay dataset exists only in the local scratch folder (52 MB csv.gz, 60 sessions, Yahoo's 5m maximum) and is not in the repo. The base-rate statistics are rough: the relative-volume baseline used all 60 sessions, so it includes future data.
- Nasdaq chart timestamps are the ET wall clock written as UTC milliseconds, and its date labels were a day off (Sep 24 labels on Sep 25 data). Decode carefully, or fallback data will be shifted by 4-5 hours.
- Terms-of-use and politeness: these are free, keyless, unofficial feeds and the repo is public. Keep request volume low (about 65 requests per tick recommended rather than about 550) and present all output as educational, not advice.

# Momentum Radar: data sources, measured

All measurements were taken on 2026-09-27 (Sunday, market closed; values are Friday 2026-09-25) on this Windows machine's home connection, with Python 3.12, yfinance 1.2.0, curl_cffi 0.13.0 and pandas 3.0.1.

Scratch folder: `<design-scratchpad>/radar_design/` (called `RD/` below).
- Scripts: `RD/t1_screeners.py`, `t2_download.py`, `t2b_semantics.py`, `t2c_direct.py`, `t3_universe.py`, `t4_nasdaq.py`, `t5_replay.py`, `t6_tick_sim.py`, `t7_ws.py`, `t8_replay_stats.py`.
- Raw results: `RD/out/*.json`.
- Tested fetch layer: `RD/radar_fetch.py`.

---

## 1. yfinance screeners (`yf.screen`)

**Access:** none of this needs an API key. yfinance fetches the cookie and crumb itself.

| Screener | Latency (s) | Total matches | Rows with default count | Rows with count=250 |
|---|---|---|---|---|
| day_gainers (up more than 3%, cap ≥ $2B, price ≥ $5) | 1.11 first call, then 0.13-0.6 | 116 | 25 | 116 |
| day_losers (down more than 2.5%) | 0.37-0.6 | 151 | 25 | 151 |
| most_actives (day volume > 5M, cap ≥ $2B) | 0.39-0.76 | 338 | 25 | **250 (cap)** |
| small_cap_gainers | 0.37-0.49 | 43 | 25 | 43 |
| aggressive_small_caps | 0.37-0.78 | 478 | 25 | 250 |
| most_shorted_stocks | 0.67-0.74 | 3948 | 25 | 250 |
| custom EquityQuery, up more than 2% (cap ≥ $2B, price ≥ $5, volume > 100k) | 1.02 | 253 | – | 250 (+3 via offset=250) |
| custom, down more than 2% | 0.82 | 210 | – | 210 |

Other predefined screeners (growth_technology_stocks, undervalued_*, and the fund screeners) aren't useful for finding racing stocks.

**Gotchas**
- **Row limit:** the maximum is 250 rows per call. The predefined endpoint ignores `offset`.
- **Paging:** passing `offset` switches yfinance to the custom POST endpoint. That endpoint reads **`size`, not `count`**. `yf.screen('day_gainers', offset=0, count=250)` returned only 25 rows. To page, use a custom `EquityQuery` with `size=250, offset=k`.
- **Predefined vs custom totals differ:** predefined day_gainers reported total=116, but the same query sent as a custom POST reported 128. The predefined results look cached or snapshotted.
- **Exchanges:** predefined results cover ASE, NCM, NGM, NMS, NYQ, BTS and a few `OID`. Custom queries also return OTC `PNK`. Filter to `{NMS, NGM, NCM, NYQ, ASE, PCX, BTS}`.
- **Fields (93-96 per quote):**
  - Price and volume: symbol, quoteType (EQUITY), exchange, fullExchangeName, marketState, regularMarketPrice, regularMarketChange, regularMarketChangePercent, regularMarketVolume, regularMarketOpen, regularMarketDayHigh, regularMarketDayLow, regularMarketPreviousClose, regularMarketTime (epoch).
  - Averages and size: averageDailyVolume3Month, averageDailyVolume10Day, marketCap, sharesOutstanding, fiftyDayAverage, twoHundredDayAverage, fiftyTwoWeekHigh, fiftyTwoWeekLow.
  - Other: earningsTimestamp and its start/end, bid, ask, sizes, hasPrePostMarketData, postMarketPrice, postMarketChange, postMarketChangePercent, postMarketTime, fulldayPrice, fulldayChangePercent, exchangeDataDelayedBy (0), quoteSourceName ("Delayed Quote" or "Nasdaq Real Time Price"), sourceInterval (15).
- **Pre-market fields weren't returned today (marketState=CLOSED).** Yahoo's schema has preMarketPrice, preMarketChangePercent and preMarketTime while marketState=PRE. **Check Monday pre-market.** Also check whether the screeners' `percentchange` updates during pre-market; it probably covers the regular session only.

```python
import yfinance as yf
from yfinance import EquityQuery as EQ
g = yf.screen("day_gainers", count=250)["quotes"]          # predefined: count<=250, offset ignored
up = EQ("and", [EQ("gt", ["percentchange", 2]), EQ("eq", ["region", "us"]),
                EQ("gte", ["intradaymarketcap", 2_000_000_000]), EQ("gte", ["intradayprice", 5]),
                EQ("gt", ["dayvolume", 100_000])])
r = yf.screen(up, size=250, offset=0, sortField="percentchange", sortAsc=False)  # custom: use size (+offset)
```

---

## 2. 5-minute bars

### 2a. `yf.download` (too slow for the 5-minute loop)

The call was `yf.download(syms, interval='5m', group_by='ticker', auto_adjust=False, progress=False)`.

| Symbols | Period | prepost | Wall time (s) | Rows | Failures |
|---|---|---|---|---|---|
| 50 | 1d | True | 5.71 | 192 | 0 |
| 50 | 1d | False | 4.82 | 78 | 0 |
| 200 | 5d | True | 20.69 | 963 | 0 |
| 542 | 1d | True | 52.17 / 48.31 (2 runs) | 197 | 0 |
| 542 | 5d | True | 51.33 | 965 | 0 |
| 542 | 1d | True, threads=32 | 53.43 / 45.15 (immediate repeat) | 197 | 0 |

- **Speed:** about 0.09 s per symbol no matter how many threads. It is limited by yfinance's per-ticker pandas work and a shared-session lock, not by the network.
- **Errors:** none. No missing symbols, no all-NaN columns, no `YFRateLimitError`, no 429 in 5 full-universe runs.
- **Timezone:** the multi-ticker `yf.download` index is **UTC**, because it calls `pd.to_datetime(..., utc=True)`. `Ticker.history()` returns America/New_York. Convert explicitly.
- **Bars per day with prepost=True:** 192 (66 pre-market 04:00-09:25, 78 regular 09:30-15:55, 48 post-market 16:00-19:55). With prepost=False: 78.
- **Stray rows:** a few symbols carry an extra off-grid row (for example 19:57), which is why 197 rows appear instead of 192.

### 2b. Direct Yahoo chart API (recommended)

The endpoint is `https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=5m&includePrePost=true`. It needs no cookie or crumb, but it does need a browser TLS fingerprint or User-Agent.

| Variant | Wall time (s) | HTTP |
|---|---|---|
| 542 symbols, 1d, 8 workers | 12.19 | 542 × 200 |
| 542 symbols, 1d, 16 workers | 6.6-7.0 | 542 × 200 |
| 542 symbols, 1d, 32 workers | 2.96-3.64 | 542 × 200 |
| 542 symbols, 5d, 16 workers | 6.69 | 542 × 200 |

- **Per-request latency:** p50 0.17 s, p95 0.25-0.45 s, max 0.96 s.
- **Parsing cost:** building a pandas frame per symbol costs **8.6 ms per symbol**, about 4.7 s for 542 under the GIL. `radar_fetch.bars_5m` takes 8 s end to end because of this. Parse to numpy and build one frame at the end instead.
- **Plain `requests` with the default User-Agent** gets **429 "Edge: Too Many Requests" on the first call**. A browser User-Agent gets 200. Always use `curl_cffi` with `impersonate="chrome"`.
- **Chart `meta` is a snapshot too:** regularMarketPrice, regularMarketVolume, regularMarketChangePercent, previousClose, chartPreviousClose, regularMarketDayHigh, regularMarketDayLow, fulldayPrice, exchangeTimezoneName, gmtoffset, currentTradingPeriod (pre/regular/post epochs), hasPrePostMarketData.
- **History depth:**
  - 5m: 60 sessions (range=60d returned 2026-07-02 to 2026-09-25, 11,521 rows, 704 kB per symbol). `period1` older than 60 days gets 422.
  - 1m: at most 8 days (422 otherwise).
  - 15m: 60 sessions.
  - Daily: 1y takes 0.19 s per symbol.
- **Spark endpoint** (`v7/finance/spark`): at most 20 symbols per request (400 above that) and close prices only. Not useful.

```python
import threading
from concurrent.futures import ThreadPoolExecutor
from curl_cffi import requests as creq
_tls = threading.local()
def sess():
    if getattr(_tls, "s", None) is None:
        _tls.s = creq.Session(impersonate="chrome")
    return _tls.s
def chart(sym, rng="1d"):
    r = sess().get(f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}",
                   params={"range": rng, "interval": "5m", "includePrePost": "true"}, timeout=15)
    return sym, r.status_code, (r.json()["chart"]["result"][0] if r.status_code == 200 else None)
with ThreadPoolExecutor(32) as ex:
    results = list(ex.map(chart, symbols))   # 542 symbols: ~3 s
```

### 2c. Batch quote: the whole universe in one request

The endpoint is `https://query1.finance.yahoo.com/v7/finance/quote?symbols=A,B,...`.
- **Needs a cookie and crumb:** 401 "Unauthorized" without them. yfinance's `YfData` handles this. Manually: GET `https://fc.yahoo.com` (returns 404 but sets the cookie), then `/v1/test/getcrumb` (about 1.0 s once), then pass `crumb=`.
- **Timing by batch size:** 542 symbols in 1 request took 0.89 s (1.49 MB). 3 × 250 took 1.39 s. 6 × 100 took 2.88 s. With `fields=` limiting the output: **0.42 s, 475 kB**.
- **Quality:** all 542 returned. exchangeDataDelayedBy=0 for every exchange. quoteSourceName was mixed ("Nasdaq Real Time Price" for 195, "Delayed Quote" for 347 on the weekend). marketCap is null for the 24 ETFs.

```python
from yfinance.data import YfData
yd = YfData()   # singleton; fetches cookie + crumb
FIELDS = ("regularMarketPrice,regularMarketChangePercent,regularMarketVolume,regularMarketTime,averageDailyVolume3Month,"
          "marketCap,marketState,preMarketPrice,preMarketChangePercent,postMarketPrice,postMarketChangePercent")
q = yd.get_raw_json("https://query1.finance.yahoo.com/v7/finance/quote",
                    params={"symbols": ",".join(U), "formatted": "false", "fields": FIELDS})["quoteResponse"]["result"]
```

### 2d. Bar details (measured on the 60-session replay and on Friday's data)

- **Timestamps:** the raw chart API gives epoch seconds (UTC) at each bar's **start**. Convert with `tz_convert('America/New_York')`. DST switches on 2026-11-01, so keep UTC epochs as the storage key.
- **Extended-hours volume is always 0 on Yahoo 5m bars.** 0.0% of pre-market bars have volume > 0, and 0.47% of post-market bars do. Those few are only the 16:00 and 16:05 bars, and they hold the **NYSE closing auction** for some names: ACN's 16:00 bar had 620,202 shares; JPM's didn't. Pre- and post-market bars carry price only.
- **Bar volume undercounts the day.** Summed regular-session 5m volume divided by daily volume: median **0.766**, p10 0.608, p90 0.884. Examples: AAPL 0.756, NVDA 0.794, JPM 0.487. Opening and closing auctions (and some late prints) are missing. **So never compare cumulative bar volume with `averageDailyVolume3Month`.** Build time-of-day volume baselines from the same 5m bars.
- **Completeness:**
  - Regular session: every one of the 60 sessions has a median of 78 bars. At least 94.6% of symbols have 77 or more regular bars in every session. Zero-volume regular bars: 0.01%.
  - Pre/post: bars are sparse. Only 44% of possible pre-market bars and 50% of possible post-market bars exist, because Yahoo leaves out empty bars.
  - No duplicates. Holidays 07-03 and 09-07 are absent. No early closes in the window.
- **The in-progress bar:** raw chart responses end with an **extra row off the 5-minute grid** carrying the last trade's timestamp. Friday's was 19:59:58 ET, with O=H=L=C=last and volume 0.
  - yfinance's `fix_Yahoo_returning_live_separate` merges that row into the current bar.
  - During a live session, the newest grid bar (start ≤ now < start+5m) is **partial**: its close is the latest trade and its volume is still growing.
  - Rule used in `radar_fetch.bars_5m`: drop rows where `ts % 300 != 0` (keep them as `last_trade`), and treat a bar as complete only when `start + 5min + settle_s (20 s) <= now`.
  - I could not watch this live on a weekend. **Check it Monday.**
- **Time to publish a bar:** couldn't be measured on a weekend. Yahoo serves real-time Nasdaq Last Sale prices and updates the partial bar continuously. Ticking at boundary + about 30 s (hh:m0:30, hh:m5:30) should see finished bars. **Verify Monday.**

### 2e. Rate limits

- **Totals:** about 9,000 Yahoo chart requests plus about 30 quote and screener calls over roughly 50 minutes. Peak burst was 542 requests in 3 s. There were **zero 429s and zero `YFRateLimitError`**.
- **Simulated ticks:** three back-to-back ticks (1 quote + 3 screeners + 542 charts at 32 workers) each finished in 4.4-4.8 s, all HTTP 200.
- **The only throttle seen** was the default-User-Agent 429 from §2b.
- **Not measured: GitHub Actions runners use cloud (datacenter) IPs.** Yahoo has throttled cloud IPs before. This is the biggest open risk (see open risks).

### 2f. Yahoo streaming WebSocket (optional)

`yf.AsyncWebSocket()` connects to `wss://streamer.finance.yahoo.com/?version=2` with no key. In 20 s it delivered 8 decoded messages for BTC-USD and ETH-USD. Fields: id, price, time (ms), change_percent, day_volume, day_high, day_low, open_price, market_hours. AAPL and SPY were silent because the market was closed. It could push live prices for list members between 5-minute ticks inside the long-running job. It's optional, and equity streaming hasn't been verified.

---

## 3. Universe: `RD/universe.csv` (542 rows)

Columns: `symbol,name,sector,industry,in_sp500,in_ndx,is_etf`. Tickers are in Yahoo style ("." becomes "-"): **BRK-B** and **BF-B**.

| Source | Latency (s) | Rows | Agreement |
|---|---|---|---|
| Wikipedia "List of S&P 500 companies", table 0 (`requests` with browser User-Agent, then `pd.read_html(StringIO)`; lxml 6.0.2 and html5lib 1.1 were already installed) | 1.2 | 503 | – |
| GitHub `datasets/s-and-p-500-companies` constituents.csv | 0.5 | 503 | identical to Wikipedia |
| Wikipedia "List of NASDAQ-100 companies", table 0 | 0.75 | 101 | – |
| `api.nasdaq.com/api/quote/list-type/nasdaq100` (official) | 1.7-2.6 | 101 | identical to Wikipedia |

- **Composition:** 542 = 503 S&P 500 + 15 Nasdaq-100 names not in the S&P 500 + 24 ETFs.
  - The 15: ALAB, ALNY, ARM, ASML, CCEP, CRWV, FER, MELI, MSTR, NBIS, PDD, RKLB, SHOP, SPCX, TRI.
  - The 24 ETFs: SPY, QQQ, IWM, DIA, the 11 XL* sector funds, SMH, TLT, GLD, SLV, USO, HYG, ARKK, KRE, XBI.
- **Wikipedia page name:** the Nasdaq-100 list moved off the `Nasdaq-100` article. That article no longer has a constituents table.
- **Refresh:** weekly, using the GitHub CSV plus the Nasdaq API as a cross-check.

---

## 4. Nasdaq fallback (`api.nasdaq.com`)

Plain `requests` works with browser-like headers: `User-Agent: Chrome`, `Accept: application/json, text/plain, */*`, `Origin: https://www.nasdaq.com`, `Referer: https://www.nasdaq.com/`. Every endpoint below returned 200 with no key.

| Endpoint | Latency (s) | Payload | Notes |
|---|---|---|---|
| `/api/marketmovers?assetclass=stocks` | 3.6 | 9 kB | Five lists of 10 rows: MostActiveByShareVolume, MostAdvanced, MostDeclined, MostActiveByDollarVolume, Nasdaq100Movers. Nasdaq-listed only. Full of penny stocks (CTNT at $0.03). Stamped "Data as of Sep 25, 2026 4:15 PM ET"; Nasdaq100Movers was stamped Sep 24. **Weak for our use.** |
| `/api/screener/stocks?tableonly=true&download=true` | **1.5-3.4** | 2.2 MB, **7,017 rows** | Fields: symbol, name, lastsale "$172.84", netchange, pctchange "4.549%", volume "3053449", marketCap, country, ipoyear, industry, sector. **Covers all 518 universe stocks** (no ETFs). `asOf` is null; freshness during a live session is unknown. **Best fallback for finding candidates.** |
| `/api/screener/etf?tableonly=true&download=true` | 5.1 | 1.16 MB, 5,252 rows | Fields: symbol, lastSalePrice, netChange, percentageChange. |
| `/api/quote/{SYM}/chart?assetclass=stocks` | 1.4-1.7 | 152 kB | 958 points at 1-minute resolution, 04:00-19:59 ET, price only. |
| `/api/quote/{SYM}/chart?assetclass=stocks&charttype=rs` | 1.4 | 130 kB | 958 points of 1-minute price **plus shares per minute in `w`, including pre- and post-market.** AAPL pre-market sum was 311,076, consistent with the extended-trading endpoint's 315,694. **This is the only keyless source of extended-hours volume found.** |
| `/api/quote/{SYM}/info?assetclass=stocks` | 1.35 | 1 kB | lastSalePrice, percentageChange, volume, marketStatus, isRealTime=false. |
| `/api/quote/{SYM}/extended-trading?markettype=pre` | 1.75 | 1 kB | Pre-market volume, high and low. |
| `/api/quote/watchlist?symbol=aapl%7cstocks&symbol=spy%7cetf...` | 1.5-3.8 | ~0.4 kB per symbol | Multi-symbol snapshot, but **silently capped at 20 symbols**: asking for 50 or 100 returned 20. |

**Format gotchas**
- **Numbers are strings** with `$`, `,`, `%` and `+`. Strip them with the regex `[$,%+]` and treat "N/A"/"NA" as missing.
- **Chart `x` isn't a real UTC time.** It's the ET wall clock written as if it were UTC, in milliseconds: 1790308800000 is labelled "4:00 AM ET". Decode with `pd.to_datetime(x, unit='ms').tz_localize('America/New_York')`.
- **Date labels are off by a day:** they said "Sep 24, 2026" while the values were Friday Sep 25 (they match Yahoo's close).
- **Symbols:** Nasdaq writes class shares with "." or "/" (BRK.B), while Yahoo uses "-".

---

## 5. Replay dataset (for calibration): `RD/replay/`

| File | Size | Rows | Coverage | Columns |
|---|---|---|---|---|
| `bars_5m_prepost.csv.gz` | 52.0 MB | 4,255,825 (2,528,577 regular, 952,530 pre, 774,718 post) | 542 symbols × **60 sessions, 2026-07-02 to 2026-09-25** (Yahoo's 5m maximum). VMRK has only 29 sessions (trading since 2026-08-17). | `symbol, ts` (epoch s UTC, bar start), `open, high, low, close` (rounded to 4 dp), `volume` (int) |
| `bars_1d_1y.csv.gz` | 3.48 MB | 135,497 | 542 symbols, 2025-09-26 to 2026-09-25 | `symbol, date, open, high, low, close, adjclose, volume` |
| `meta.csv` | 31 kB | 542 | Chart meta per symbol: exchangeName, instrumentType, regularMarketPrice, chartPreviousClose, previousClose, regularMarketVolume, fiftyTwoWeekHigh, fiftyTwoWeekLow | |
| `sessions_5m.txt` | – | 60 | Session dates | |

- **Download time:** 70 s for the 5m set (8 workers, batches of 100 with 4 s pauses, 0 errors) and 21 s for the daily set.
- **Load time:** `pd.read_csv` takes 13 s.
- **Cleaning:** off-grid "last trade" rows were dropped, and rows with a null close were dropped.
- **Format:** pyarrow isn't installed, so the files are CSV.gz.

Rough base rates from `t8_replay_stats.py`, for the people designing the signals. The regular session after 10:00 was used. The relative-volume baseline is the median for the same symbol and time of day across all 60 sessions, so it includes the future.

| Measure | p50 | p90 | p99 | p99.9 |
|---|---|---|---|---|
| Size of 5-minute move | 0.08% | 0.27% | 0.64% | 1.22% |
| Size of 30-minute move | 0.20% | 0.67% | 1.67% | 3.36% |
| Relative volume per 5m bar (vs same-time-of-day median) | 1.0 | 2.27 | 6.11 | 16.6 |

- **Candidate rate for a naive trigger** (30-minute move of at least 2% and relative volume ≥ 3):
  - 0.98 hits per tick on average, p95 4, maximum 28 across 4,320 ticks.
  - 1,848 ticks had at least one hit.
  - About 23.5 distinct symbols per session.
- **Time-of-day volume skew:** the first 30 minutes hold 16.2% of regular-session bar volume and the last 30 minutes hold 18.3%.

---

## 6. Recommendation

### Per-tick fetch strategy: snapshot and screeners, then bars for a small watch set

Scanning the whole universe every tick is affordable (about 5 s) but wasteful: it would mean about 6,500 chart requests an hour from a cloud IP. Recommended tick, aligned to bar boundary + about 30 s:

1. **Snapshot (1 request, 0.4-0.9 s).** Batch v7 quote for all 542 symbols with `fields=`. Diffing it against the previous tick gives, for every symbol, the 5-minute price change, day %, pace of cumulative volume vs its own baseline, and pre/post-market %. That is the cheap universe-wide pre-filter.
2. **Discovery (3 requests, 0.4-1.9 s).** `day_gainers`, `day_losers`, `most_actives` with count=250, filtered to US-listed exchanges. This catches names at or above $2B outside the universe (optional, since it depends on the scope the designers pick).
3. **Bars (≤ 80 requests, about 1 s at 16 workers).** v8 chart range=1d, 5m, includePrePost, fetched only for **current list members plus the top K (about 60) snapshot and screener candidates**. Signals should run on completed bars only.
4. **Full sweep:** fetch 5m bars for all 542 symbols (3-7 s) only at engine start, after a missed tick or data gap, and optionally every 30-60 minutes to keep bar history complete.
5. **Once a day, pre-market:** 1y daily bars (about 21 s at 8 workers) and 5m history for the time-of-day volume profile (range=1mo or 60d, or keep a rolling profile from our own saved data).
6. **Pre-market volume signals**, if wanted: Nasdaq `charttype=rs` for members only (1 request per symbol, about 1.5 s each; run 4-8 in parallel).

**Expected tick duration:** 2-4 s typical, 5-8 s with a full sweep, with about 65 requests per tick (about 780 per hour). The rest of the 5-minute window is idle, so a long-running loop job has plenty of slack.

**Parsing:** never parse with a pandas frame per symbol (8.6 ms each). Collect numpy arrays and build one long frame per tick. Avoid `yf.download` in the loop (45-53 s for 542 symbols, UTC index). Keep yfinance only for `YfData` (crumb) and `yf.screen`.

### Retry, backoff and fallback policy

- **Per request:** timeout 10-15 s. Retry up to 3 times on 429, 5xx or a network error, sleeping `1 s × 2^k` plus 0-0.5 s of random jitter. Don't retry other 4xx (404 delisted, 422 range); mark the symbol as failed for that tick. On a v7 401, rebuild the session and crumb once.
- **Per tick (degraded mode):** if the quote call fails after retries, **or** more than 20% of chart calls fail, **or** 429s appear:
  - cut concurrency 32 → 16 → 8;
  - skip discovery and the full sweep;
  - fetch bars for members only;
  - write `source_status: "degraded"` into the published JSON.
- **Circuit breaker:** after 3 degraded ticks in a row, switch to Nasdaq:
  - `screener/stocks` (1 request, about 7,000 rows) as the snapshot and discovery source;
  - `quote/{sym}/chart?charttype=rs` for members (1-minute data, resampled to 5m);
  - `quote/watchlist` in groups of ≤ 20 for quick member snapshots.
  - Probe Yahoo again every 3rd tick with one quote request; close the breaker after 2 good probes.
- **Stale-data guard:** during the regular session, if SPY's `regularMarketTime` is more than 10 minutes old, or the newest completed bar is more than 2 bars old, mark the tick "stale". Don't add or remove list members on stale data; carry the list forward.
- **Politeness:**
  - use a browser fingerprint (`curl_cffi` `impersonate="chrome"`);
  - keep concurrency at or below 16 on Actions, except for the start-up sweep;
  - never re-download the same range more than once per tick;
  - space ticks at least 5 minutes apart even after catch-up;
  - cap the full sweep at once per 30 minutes.

### Tested code

`RD/radar_fetch.py` has the working helpers: `quotes()`, `bars_5m()` with the completed/partial bar split, `screeners()`, `nasdaq_snapshot()` and `nasdaq_intraday_1m()`. Smoke-test output:
- quotes: 542 rows in 2.0 s including crumb setup, then 0.63 s;
- bars_5m: 542 symbols, 0 failures, 8.1-8.5 s (network 3-3.6 s, the rest is pandas);
- screeners: 516 rows in 1.95 s;
- nasdaq_snapshot: 7,017 rows in 3.4 s;
- nasdaq 1m AAPL: 958 points with pre-market shares 311,076.

Key excerpt:

```python
on_grid = (df.index.second == 0) & (df.index.minute % 5 == 0)   # drop Yahoo's appended last-trade row
last_trade = df[~on_grid].tail(1); df = df[on_grid]
done = df.index + pd.Timedelta(minutes=5) + pd.Timedelta(seconds=20) <= now   # completed bars only
bars, live_bar = df[done], (df[~done].tail(1) if (~done).any() else None)
```

Nasdaq 1-minute data with volume (note the local-time decoding):

```python
j = requests.get(f"https://api.nasdaq.com/api/quote/{sym}/chart?assetclass=stocks&charttype=rs", headers=NQ_H, timeout=20).json()
pts = j["data"]["chart"]
idx = pd.to_datetime([p["x"] for p in pts], unit="ms").tz_localize("America/New_York")
df = pd.DataFrame({"price": [p["y"] for p in pts], "shares": [p.get("w") for p in pts]}, index=idx)
```

---

## 7. Check on Monday 2026-09-28 (couldn't be seen with the market closed)

1. From 04:00 ET, v7 quote returns `preMarketPrice`, `preMarketChangePercent` and `preMarketTime`, and `marketState` cycles PREPRE → PRE → REGULAR → POST → POSTPOST.
2. Partial-bar behaviour: at hh:m2 the newest 5m bar is in progress and an off-grid last-trade row is appended. Also measure how long after the boundary a finished bar is final (its close and volume stop changing).
3. How often the screeners refresh intraday and in pre-market.
4. How fresh the Nasdaq `screener/stocks` data is during a live session (compare its `lastsale` with Yahoo `regularMarketPrice`).
5. **Run the same measurements from a GitHub Actions runner** (a workflow_dispatch probe workflow) to check for datacenter-IP throttling, before building the engine.
