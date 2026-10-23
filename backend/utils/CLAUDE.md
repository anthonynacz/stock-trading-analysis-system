# Utils — Data Sources, Sentiment, yfinance Gotchas

## Sentiment Analysis (FinBERT)

News sentiment uses `ProsusAI/finbert`, a BERT model fine-tuned on financial text. The wrapper is in `utils/finbert.py`:
- `score_sentiment(text)` — single text, returns float in [-1.0, 1.0]
- `score_batch(texts)` — batch scoring, used by news_scanner for efficiency
- Score = P(positive) - P(negative) from softmax output
- Model lazy-loads on first call (~500MB, cached by HuggingFace)
- Runs on CPU (torch CPU-only installed via `--index-url https://download.pytorch.org/whl/cpu`)

## Data Sources

Unified in `utils/data_sources.py` with rate limiting and fallback chains:
- **Analyst ratings**: FMP (primary) → Finnhub → yfinance
- **Earnings calendar**: FMP (primary, provides EPS estimates) → yfinance
- **Insider trades**: FMP (primary) → Finnhub
- **Prices/fundamentals/options**: yfinance (primary, no key needed)
- **News**: Finnhub → FMP → NewsAPI. Per-ticker Finnhub uses a **3-day rolling window** (`from=today-3,to=today`) so Monday's premarket scan catches Sat/Sun articles and Tuesday-after-holiday catches the holiday's news. URL + content-hash dedup in `scan_news` absorbs the overlap with prior runs at no cost.

## yfinance Data Quality

yfinance returns inconsistent types that cause asyncpg crashes if not sanitized:
- **NaN for integers**: `volume`, `openInterest` can be `float('nan')`. Use `_safe_int()` helper in `options_analyzer.py`.
- **NaN/Inf for Decimals**: `stock_price`, `iv_rank`, `put_call_ratio` need NaN/Inf guards. Use `_safe_decimal()`.
- **Float for VARCHAR fields**: `earnings_time` and `fiscal_quarter` may be float or large numbers. Sanitized in `earnings_calendar.py` via `_sanitize_earnings_time()` (keeps BMO/AMC, drops numeric garbage) and `_sanitize_string_field()` (drops large numeric strings like revenue estimates).
- **Fields widened**: `earnings_time` is `String(50)` (was 20), `fiscal_quarter` is `String(50)` (was 10).

## Geopolitical Detection

`utils/geopolitical.py` detects keyword events (MILITARY_CONFLICT, TRADE_WAR, SANCTIONS, DIPLOMATIC_BREAKTHROUGH, OIL_DISRUPTION, REGULATION) and maps each event to per-sector point impacts via `_SECTOR_IMPACT` (8 sectors × 6 event types). Used by both ticker recommendation engine and `industry_analyzer.py`. Net geopolitical contribution is capped at ±20 per ticker, ±25 per industry per run.

## Greeks

`utils/greeks.py` computes Black-Scholes delta/gamma/theta/vega/rho using yfinance IV per contract. Used by strike recommender (`options_analyzer.py`), deep options analyzer, and Greek signals in `recommendation_engine.py`. `RISK_FREE_RATE` (0.045) is the one rate for every pricer; `radar/options.py` imports it too.

## IV measurement and the IV cost score

Design: `docs/iv-score-design.md`.
- **`utils/iv_measure.py`** is the one IV definition. IV30 is the constant-maturity 30-day ATM IV: the call/put average at the strike nearest spot, interpolated in total variance. Front IV is the first expiry ≥ 7 DTE. HV20/HV60 are close-to-close realised vols.
- **Quality gate:** only two-sided quotes count (spread ≤ 25% of mid, IV 5–300%). After-hours chains give **no reading**, never a junk number. The old front-week `atm_iv` history mixed 0.00 and 5.88 readings, so it is no longer used.
- **`utils/iv_score.py`** turns IV30, HV20 and the clean history into the 0–100 IV cost score:
  - Parts: a size-damped percentile in the history, with a regime window and a spike note, plus IV/HV.
  - Weights: 50/50 once 120+ clean days exist.
  - Labels and confidence: cheap/fair/pricey/expensive at 35/60/80; confidence high/medium/low.
  - Events: an earnings date or a front IV ≥ 1.2× IV30 floors the label at pricey.
  
  It is pure and tested in `tests/test_iv_score.py`.
- **Rank and percentile** now come only from the `iv_daily` history (`history.rank` / `history.percentile` in the score).

## Maintaining this file

Update this file when you add/swap a data source, change a fallback chain, discover a new yfinance sanitization gotcha, change the FinBERT model or scoring contract, or extend the geopolitical event/sector map. Full rules in the root `CLAUDE.md` § Maintaining these files. Surface what you changed in your reply.
