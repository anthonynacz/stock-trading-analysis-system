"""Expert-level single-ticker options analysis for EdgeFlow.

Generates a structured report covering greeks, volatility structure,
dealer positioning, liquidity, a recommended strategy, and a list of
hidden risks that a vanilla "buy a call" recommendation would miss.
"""

from __future__ import annotations

import asyncio
import logging
import math
import statistics
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from utils.data_sources import DataSourceClient
from utils.greeks import RISK_FREE_RATE, compute_greeks, compute_greeks_extended
from utils.iv_history import compute_iv_rank_and_percentile

logger = logging.getLogger(__name__)


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _safe_int(val: Any) -> int:
    f = _safe_float(val)
    return int(f) if f is not None else 0


def _nearest(items: list[dict], key: str, target: float) -> dict | None:
    """Return the item whose `key` value is nearest `target`."""
    best = None
    best_dist = float("inf")
    for it in items:
        v = _safe_float(it.get(key))
        if v is None:
            continue
        d = abs(v - target)
        if d < best_dist:
            best_dist = d
            best = it
    return best


class DeepOptionsAnalyzer:
    """Produces an expert-level options report for a single ticker."""

    def __init__(self, session: AsyncSession, data_client: DataSourceClient) -> None:
        self._session = session
        self._data_client = data_client

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def analyze(
        self,
        ticker: str,
        *,
        directional_bias: str = "NEUTRAL",
        conviction_score: float = 0.0,
        earnings_date: date | None = None,
    ) -> dict[str, Any]:
        """Run the full deep-options analysis and return a structured report."""
        loop = asyncio.get_event_loop()
        chain_data = await loop.run_in_executor(
            None,
            lambda: self._data_client.get_options_chain(ticker, mode="spread"),
        )
        chains: dict[str, dict[str, list[dict]]] = chain_data.get("chains", {}) or {}
        spot = _safe_float(chain_data.get("current_price"))

        if not chains or spot is None or spot <= 0:
            return {
                "error": "no_chain_data",
                "ticker": ticker,
                "stock_price": spot,
                "greeks_detail": {"expirations": []},
                "vol_structure": {},
                "positioning": {},
                "liquidity": {},
                "strategy": {"verdict": "NO_TRADE", "reason": "No options chain available."},
                "hidden_risks": [],
                "rationale": "Chain data unavailable from yfinance.",
            }

        today = date.today()

        # Sort expirations chronologically
        expiries: list[tuple[str, date, int]] = []
        for exp_str in chains.keys():
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            dte = (exp_date - today).days
            if dte <= 0:
                continue
            expiries.append((exp_str, exp_date, dte))
        expiries.sort(key=lambda x: x[2])

        if not expiries:
            return {
                "error": "no_valid_expiries",
                "ticker": ticker,
                "stock_price": spot,
                "greeks_detail": {"expirations": []},
                "vol_structure": {},
                "positioning": {},
                "liquidity": {},
                "strategy": {"verdict": "NO_TRADE", "reason": "All expirations already passed."},
                "hidden_risks": [],
                "rationale": "No future-dated expirations.",
            }

        # Phase 1 — build per-expiry metrics
        per_exp = [self._analyze_expiry(exp_str, exp_date, dte, sides, spot)
                   for exp_str, exp_date, dte in expiries
                   for sides in [chains[exp_str]]]
        per_exp = [p for p in per_exp if p is not None]

        # Phase 2 — global aggregates
        all_atm_ivs = [p["atm_iv"] for p in per_exp if p.get("atm_iv") is not None]
        current_atm_iv = all_atm_ivs[0] if all_atm_ivs else None
        iv_rank, iv_pct, _iv_source = await compute_iv_rank_and_percentile(
            self._session,
            ticker,
            current_atm_iv,
            chains=chains,
            spot=spot,
        )

        # Phase 3 — vol structure
        vol_structure = self._build_vol_structure(per_exp, spot)

        # Phase 4 — positioning
        positioning = self._build_positioning(per_exp, chains, spot, today)

        # Phase 5 — liquidity
        liquidity = self._build_liquidity(per_exp)

        # Phase 6 — greeks detail table (per expiry ATM)
        greeks_detail = {
            "risk_free_rate": RISK_FREE_RATE,
            "spot": spot,
            "expirations": [
                {
                    "expiry": p["expiry"],
                    "dte": p["dte"],
                    "atm_strike": p["atm_strike"],
                    "atm_iv": p["atm_iv"],
                    "atm_call": p.get("atm_call_greeks"),
                    "atm_put": p.get("atm_put_greeks"),
                    "straddle_price": p.get("straddle_price"),
                    "expected_move_pct": p.get("expected_move_pct"),
                }
                for p in per_exp
            ],
        }

        # Phase 7 — strategy selection
        earnings_dte = None
        if earnings_date is not None:
            earnings_dte = (earnings_date - today).days
        strategy = self._recommend_strategy(
            bias=directional_bias.upper(),
            conviction=conviction_score,
            iv_rank=iv_rank,
            per_exp=per_exp,
            spot=spot,
            earnings_dte=earnings_dte,
            positioning=positioning,
            liquidity=liquidity,
        )

        # Phase 8 — hidden risks
        hidden_risks = self._hidden_risks(
            iv_rank=iv_rank,
            per_exp=per_exp,
            positioning=positioning,
            liquidity=liquidity,
            vol_structure=vol_structure,
            earnings_dte=earnings_dte,
            strategy=strategy,
        )

        rationale = self._build_rationale(
            bias=directional_bias,
            iv_rank=iv_rank,
            iv_pct=iv_pct,
            vol_structure=vol_structure,
            positioning=positioning,
            strategy=strategy,
        )

        return {
            "ticker": ticker,
            "stock_price": spot,
            "iv_rank": iv_rank,
            "iv_percentile": iv_pct,
            "directional_bias": directional_bias.upper(),
            "conviction_score": conviction_score,
            "verdict": strategy.get("verdict"),
            "greeks_detail": greeks_detail,
            "vol_structure": vol_structure,
            "positioning": positioning,
            "liquidity": liquidity,
            "strategy": strategy,
            "hidden_risks": hidden_risks,
            "rationale": rationale,
        }

    # ------------------------------------------------------------------
    # Per-expiry analysis
    # ------------------------------------------------------------------

    def _analyze_expiry(
        self,
        exp_str: str,
        exp_date: date,
        dte: int,
        sides: dict[str, list[dict]],
        spot: float,
    ) -> dict | None:
        calls = sides.get("calls", []) or []
        puts = sides.get("puts", []) or []
        if not calls and not puts:
            return None

        # ATM strike (nearest to spot)
        atm_call = _nearest(calls, "strike", spot)
        atm_put = _nearest(puts, "strike", spot)
        atm_strike = _safe_float((atm_call or atm_put or {}).get("strike"))
        if atm_strike is None:
            return None

        # ATM IV: average of direct-ATM call+put IV. yfinance often returns
        # ~1e-5 (i.e., zero) for stale ATM quotes (off-hours), so fall back to
        # the median of valid IVs from near-ATM contracts (within 5% of spot).
        c_iv = _safe_float(atm_call.get("impliedVolatility")) if atm_call else None
        p_iv = _safe_float(atm_put.get("impliedVolatility")) if atm_put else None
        ivs = [iv for iv in (c_iv, p_iv) if iv is not None and 0.05 <= iv <= 3.0]
        atm_iv = sum(ivs) / len(ivs) if ivs else None

        if atm_iv is None:
            band = spot * 0.05
            nearby: list[float] = []
            for c in calls + puts:
                k = _safe_float(c.get("strike"))
                iv = _safe_float(c.get("impliedVolatility"))
                if k is None or iv is None or abs(k - spot) > band:
                    continue
                if 0.05 <= iv <= 3.0:
                    nearby.append(iv)
            if nearby:
                nearby.sort()
                atm_iv = nearby[len(nearby) // 2]  # median

        # ATM premiums (mid of bid/ask, else lastPrice)
        def _mid(c: dict | None) -> float | None:
            if not c:
                return None
            bid = _safe_float(c.get("bid")) or 0
            ask = _safe_float(c.get("ask")) or 0
            last = _safe_float(c.get("lastPrice"))
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
            if last and last > 0:
                return last
            return None

        c_premium = _mid(atm_call)
        p_premium = _mid(atm_put)
        straddle_price = (c_premium or 0) + (p_premium or 0) if (c_premium or p_premium) else None

        # Expected move — 1-sigma move implied by straddle
        expected_move_pct = None
        expected_move_abs = None
        if straddle_price and spot > 0:
            expected_move_pct = round(straddle_price / spot * 100, 2)
            expected_move_abs = round(straddle_price, 2)

        # ATM greeks
        T = dte / 365.0
        atm_call_greeks = None
        atm_put_greeks = None
        if atm_iv is not None and atm_iv > 0:
            atm_call_greeks = compute_greeks_extended(
                S=spot, K=atm_strike, T=T, r=RISK_FREE_RATE, sigma=atm_iv, is_call=True,
            )
            atm_put_greeks = compute_greeks_extended(
                S=spot, K=atm_strike, T=T, r=RISK_FREE_RATE, sigma=atm_iv, is_call=False,
            )
            # Attach premium for % conversions downstream
            if c_premium:
                atm_call_greeks["premium"] = round(c_premium, 2)
                atm_call_greeks["theta_pct"] = (
                    round(abs(atm_call_greeks["theta"]) / c_premium * 100, 2)
                    if atm_call_greeks.get("theta") and c_premium > 0 else None
                )
                atm_call_greeks["vega_pct"] = (
                    round(abs(atm_call_greeks["vega"]) / c_premium * 100, 2)
                    if atm_call_greeks.get("vega") and c_premium > 0 else None
                )
            if p_premium:
                atm_put_greeks["premium"] = round(p_premium, 2)
                atm_put_greeks["theta_pct"] = (
                    round(abs(atm_put_greeks["theta"]) / p_premium * 100, 2)
                    if atm_put_greeks.get("theta") and p_premium > 0 else None
                )
                atm_put_greeks["vega_pct"] = (
                    round(abs(atm_put_greeks["vega"]) / p_premium * 100, 2)
                    if atm_put_greeks.get("vega") and p_premium > 0 else None
                )

        # 25-delta skew: find call with delta ~+0.25, put with delta ~-0.25
        call_25d_iv = self._delta_target_iv(calls, spot, T, is_call=True, target=0.25)
        put_25d_iv = self._delta_target_iv(puts, spot, T, is_call=False, target=-0.25)
        skew_25d = None
        if call_25d_iv is not None and put_25d_iv is not None:
            skew_25d = round((put_25d_iv - call_25d_iv) * 100, 2)  # in IV-pts

        # OI sums and spread stats
        call_oi = sum(_safe_int(c.get("openInterest")) for c in calls)
        put_oi = sum(_safe_int(p.get("openInterest")) for p in puts)
        call_vol = sum(_safe_int(c.get("volume")) for c in calls)
        put_vol = sum(_safe_int(p.get("volume")) for p in puts)

        spreads_pct: list[float] = []
        for c in calls + puts:
            b = _safe_float(c.get("bid"))
            a = _safe_float(c.get("ask"))
            if b and a and b > 0 and a > 0 and a >= b:
                mid = (a + b) / 2.0
                if mid > 0.05:  # ignore pennies (noise)
                    spreads_pct.append((a - b) / mid * 100)
        median_spread_pct = round(statistics.median(spreads_pct), 2) if spreads_pct else None

        # Gamma exposure for this expiry (signed: calls +, puts -).
        # Use contract's own IV when valid, else fall back to ATM IV.
        gex = 0.0
        if atm_iv is not None and atm_iv > 0:
            for c in calls:
                k = _safe_float(c.get("strike"))
                oi = _safe_int(c.get("openInterest"))
                raw_iv = _safe_float(c.get("impliedVolatility"))
                iv = raw_iv if raw_iv is not None and 0.05 <= raw_iv <= 3.0 else atm_iv
                if k is None or oi <= 0:
                    continue
                g = compute_greeks(S=spot, K=k, T=T, r=RISK_FREE_RATE, sigma=iv, is_call=True)
                if g["gamma"] is not None:
                    gex += g["gamma"] * oi * 100
            for p in puts:
                k = _safe_float(p.get("strike"))
                oi = _safe_int(p.get("openInterest"))
                raw_iv = _safe_float(p.get("impliedVolatility"))
                iv = raw_iv if raw_iv is not None and 0.05 <= raw_iv <= 3.0 else atm_iv
                if k is None or oi <= 0:
                    continue
                g = compute_greeks(S=spot, K=k, T=T, r=RISK_FREE_RATE, sigma=iv, is_call=False)
                if g["gamma"] is not None:
                    gex -= g["gamma"] * oi * 100

        # Lightweight contract list kept on the per-expiry dict so _build_legs
        # can snap target strikes to the nearest liquid contract. Internal-only;
        # not persisted to greeks_detail (filtered out before serialization).
        compact_calls = [
            {
                "strike": _safe_float(c.get("strike")),
                "openInterest": c.get("openInterest") or 0,
                "volume": c.get("volume") or 0,
                "bid": _safe_float(c.get("bid")),
                "ask": _safe_float(c.get("ask")),
            }
            for c in calls
            if _safe_float(c.get("strike")) is not None
        ]
        compact_puts = [
            {
                "strike": _safe_float(p.get("strike")),
                "openInterest": p.get("openInterest") or 0,
                "volume": p.get("volume") or 0,
                "bid": _safe_float(p.get("bid")),
                "ask": _safe_float(p.get("ask")),
            }
            for p in puts
            if _safe_float(p.get("strike")) is not None
        ]

        return {
            "expiry": exp_str,
            "dte": dte,
            "atm_strike": atm_strike,
            "atm_iv": round(atm_iv, 4) if atm_iv is not None else None,
            "atm_call_premium": c_premium,
            "atm_put_premium": p_premium,
            "atm_call_greeks": atm_call_greeks,
            "atm_put_greeks": atm_put_greeks,
            "straddle_price": round(straddle_price, 2) if straddle_price else None,
            "expected_move_pct": expected_move_pct,
            "expected_move_abs": expected_move_abs,
            "call_25d_iv": round(call_25d_iv, 4) if call_25d_iv else None,
            "put_25d_iv": round(put_25d_iv, 4) if put_25d_iv else None,
            "skew_25d_iv_pts": skew_25d,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "call_vol": call_vol,
            "put_vol": put_vol,
            "pc_oi_ratio": round(put_oi / call_oi, 3) if call_oi > 0 else None,
            "median_spread_pct": median_spread_pct,
            "gex": round(gex, 0),
            "_calls": compact_calls,  # internal — for liquidity-aware strike snapping
            "_puts": compact_puts,
        }

    def _delta_target_iv(
        self,
        contracts: list[dict],
        spot: float,
        T: float,
        *,
        is_call: bool,
        target: float,
    ) -> float | None:
        """Find the IV of the contract whose BS delta is closest to `target`."""
        best_iv: float | None = None
        best_dist = float("inf")
        for c in contracts:
            k = _safe_float(c.get("strike"))
            iv = _safe_float(c.get("impliedVolatility"))
            if k is None or iv is None or not (0.05 <= iv <= 3.0):
                continue
            g = compute_greeks(S=spot, K=k, T=T, r=RISK_FREE_RATE, sigma=iv, is_call=is_call)
            d = g["delta"]
            if d is None:
                continue
            dist = abs(d - target)
            if dist < best_dist:
                best_dist = dist
                best_iv = iv
        return best_iv

    # ------------------------------------------------------------------
    # Global aggregates
    # ------------------------------------------------------------------

    def _build_vol_structure(self, per_exp: list[dict], spot: float) -> dict:
        """Term structure (contango/backwardation), skew summary, expected moves."""
        term = [
            {"expiry": p["expiry"], "dte": p["dte"], "atm_iv": p["atm_iv"]}
            for p in per_exp if p.get("atm_iv") is not None
        ]
        shape = "UNKNOWN"
        front_back_pts = None
        if len(term) >= 2:
            front = term[0]["atm_iv"]
            back = term[-1]["atm_iv"]
            front_back_pts = round((front - back) * 100, 2)
            if front > back + 0.02:
                shape = "BACKWARDATION"  # event-driven vol priced in the front
            elif back > front + 0.02:
                shape = "CONTANGO"  # normal — back longer-dated higher IV
            else:
                shape = "FLAT"

        skew = [
            {"expiry": p["expiry"], "dte": p["dte"], "skew_25d_iv_pts": p["skew_25d_iv_pts"]}
            for p in per_exp if p.get("skew_25d_iv_pts") is not None
        ]
        avg_skew = (
            round(sum(s["skew_25d_iv_pts"] for s in skew) / len(skew), 2)
            if skew else None
        )

        moves = [
            {
                "expiry": p["expiry"],
                "dte": p["dte"],
                "expected_move_pct": p["expected_move_pct"],
                "expected_move_abs": p["expected_move_abs"],
                "straddle_price": p["straddle_price"],
            }
            for p in per_exp if p.get("expected_move_pct") is not None
        ]

        return {
            "term_structure": term,
            "term_shape": shape,
            "front_minus_back_iv_pts": front_back_pts,
            "skew_25d_by_expiry": skew,
            "avg_skew_25d_iv_pts": avg_skew,
            "expected_moves": moves,
        }

    def _build_positioning(
        self,
        per_exp: list[dict],
        chains: dict,
        spot: float,
        today: date,
    ) -> dict:
        """Max pain, gamma exposure, pin risk, P/C OI by expiry."""
        # GEX total across all sampled expiries
        gex_total = round(sum(p.get("gex", 0) or 0 for p in per_exp), 0)
        gex_regime = "POSITIVE" if gex_total > 0 else ("NEGATIVE" if gex_total < 0 else "NEUTRAL")

        # Max pain per expiry
        max_pain_by_exp: list[dict] = []
        pin_magnets: list[dict] = []
        for p in per_exp:
            exp_str = p["expiry"]
            sides = chains.get(exp_str, {})
            calls = sides.get("calls", []) or []
            puts = sides.get("puts", []) or []
            mp = self._max_pain(calls, puts)
            max_pain_by_exp.append({
                "expiry": exp_str,
                "dte": p["dte"],
                "max_pain_strike": mp["strike"],
                "max_pain_distance_pct": (
                    round((mp["strike"] - spot) / spot * 100, 2)
                    if mp["strike"] and spot > 0 else None
                ),
            })
            # Pin magnets — within 14d and OI cluster > 10% of total near spot
            if p["dte"] <= 14:
                magnets = self._pin_magnets(calls, puts, spot)
                for m in magnets:
                    m["expiry"] = exp_str
                    m["dte"] = p["dte"]
                    pin_magnets.append(m)

        pc_oi = [
            {"expiry": p["expiry"], "dte": p["dte"], "pc_oi_ratio": p["pc_oi_ratio"]}
            for p in per_exp if p.get("pc_oi_ratio") is not None
        ]

        return {
            "gex_total": gex_total,
            "gex_regime": gex_regime,
            "max_pain_by_expiry": max_pain_by_exp,
            "pin_magnets": pin_magnets,
            "pc_oi_by_expiry": pc_oi,
        }

    def _max_pain(self, calls: list[dict], puts: list[dict]) -> dict:
        """Find strike that minimizes option-writer payout at expiry."""
        strikes: set[float] = set()
        for c in calls + puts:
            k = _safe_float(c.get("strike"))
            if k is not None:
                strikes.add(k)
        if not strikes:
            return {"strike": None, "pain": None}

        best_strike = None
        best_pain = float("inf")
        for test_K in sorted(strikes):
            pain = 0.0
            for c in calls:
                k = _safe_float(c.get("strike"))
                oi = _safe_int(c.get("openInterest"))
                if k is None or oi <= 0:
                    continue
                pain += oi * max(0.0, test_K - k)
            for p in puts:
                k = _safe_float(p.get("strike"))
                oi = _safe_int(p.get("openInterest"))
                if k is None or oi <= 0:
                    continue
                pain += oi * max(0.0, k - test_K)
            if pain < best_pain:
                best_pain = pain
                best_strike = test_K
        return {"strike": best_strike, "pain": round(best_pain, 0)}

    def _pin_magnets(
        self, calls: list[dict], puts: list[dict], spot: float
    ) -> list[dict]:
        """Strikes within 2% of spot with OI cluster > 10% of total near-spot OI."""
        band = 0.02 * spot
        near: dict[float, dict] = {}
        total_oi = 0
        for c in calls:
            k = _safe_float(c.get("strike"))
            oi = _safe_int(c.get("openInterest"))
            if k is None or oi <= 0 or abs(k - spot) > band:
                continue
            near.setdefault(k, {"strike": k, "call_oi": 0, "put_oi": 0})
            near[k]["call_oi"] += oi
            total_oi += oi
        for p in puts:
            k = _safe_float(p.get("strike"))
            oi = _safe_int(p.get("openInterest"))
            if k is None or oi <= 0 or abs(k - spot) > band:
                continue
            near.setdefault(k, {"strike": k, "call_oi": 0, "put_oi": 0})
            near[k]["put_oi"] += oi
            total_oi += oi

        if total_oi == 0:
            return []

        magnets: list[dict] = []
        for k, entry in near.items():
            combined = entry["call_oi"] + entry["put_oi"]
            share = combined / total_oi
            if share >= 0.10:
                magnets.append({
                    "strike": k,
                    "call_oi": entry["call_oi"],
                    "put_oi": entry["put_oi"],
                    "oi_share_of_near_spot": round(share * 100, 2),
                    "distance_from_spot_pct": round((k - spot) / spot * 100, 2),
                })
        magnets.sort(key=lambda m: -m["oi_share_of_near_spot"])
        return magnets

    def _build_liquidity(self, per_exp: list[dict]) -> dict:
        """Overall liquidity rating and per-expiry spread/OI stats."""
        rows = []
        for p in per_exp:
            total_oi = (p.get("call_oi") or 0) + (p.get("put_oi") or 0)
            thin = total_oi < 1000
            wide = (p.get("median_spread_pct") or 0) > 15
            rows.append({
                "expiry": p["expiry"],
                "dte": p["dte"],
                "total_oi": total_oi,
                "median_spread_pct": p.get("median_spread_pct"),
                "thin_oi": thin,
                "wide_spreads": wide,
            })

        valid_spreads = [r["median_spread_pct"] for r in rows if r.get("median_spread_pct") is not None]
        avg_spread = round(sum(valid_spreads) / len(valid_spreads), 2) if valid_spreads else None
        if avg_spread is None:
            rating = "UNKNOWN"
        elif avg_spread < 5:
            rating = "TIGHT"
        elif avg_spread < 15:
            rating = "MODERATE"
        else:
            rating = "WIDE"

        return {
            "rating": rating,
            "avg_spread_pct": avg_spread,
            "by_expiry": rows,
        }

    # ------------------------------------------------------------------
    # Strategy recommender
    # ------------------------------------------------------------------

    def _recommend_strategy(
        self,
        *,
        bias: str,
        conviction: float,
        iv_rank: float | None,
        per_exp: list[dict],
        spot: float,
        earnings_dte: int | None,
        positioning: dict,
        liquidity: dict,
    ) -> dict:
        """Select one of 8 strategies and pick concrete strikes/expiries."""
        iv_bucket = "MID"
        if iv_rank is not None:
            if iv_rank >= 70:
                iv_bucket = "HIGH"
            elif iv_rank < 30:
                iv_bucket = "LOW"

        near_earnings = earnings_dte is not None and 0 <= earnings_dte <= 14

        # Pick a target expiry: 21-35 DTE normally, after earnings if imminent.
        target_dte_lo, target_dte_hi = 21, 45
        if near_earnings and earnings_dte is not None:
            target_dte_lo = max(earnings_dte + 7, 21)
            target_dte_hi = earnings_dte + 35

        # Strategy rules
        verdict = "NO_TRADE"
        strategy_name = "WAIT"
        notes: list[str] = []

        if bias == "BULLISH":
            if iv_bucket == "LOW":
                verdict = "BUY_CALL"
                strategy_name = "LONG_CALL"
                notes.append("Low IV — buying premium is cheap; full directional upside.")
            elif iv_bucket == "MID" and not near_earnings:
                verdict = "BUY_CALL"
                strategy_name = "LONG_CALL"
                notes.append("IV moderate — long call remains reasonable; consider spreading if conviction is mid.")
            elif iv_bucket == "MID" and near_earnings:
                verdict = "BUY_CALL_SPREAD"
                strategy_name = "BULL_CALL_SPREAD"
                notes.append("Earnings within 14d — use debit spread to cap IV-crush exposure.")
            elif iv_bucket == "HIGH" and not near_earnings:
                verdict = "SELL_PUT_SPREAD"
                strategy_name = "BULL_PUT_SPREAD"
                notes.append("High IV — sell premium via defined-risk credit spread.")
            else:  # HIGH + near_earnings
                verdict = "SELL_PUT_SPREAD"
                strategy_name = "BULL_PUT_SPREAD"
                notes.append("High IV into earnings — long premium would be punished by IV crush; credit spread benefits from the crush.")
        elif bias == "BEARISH":
            if iv_bucket == "LOW":
                verdict = "BUY_PUT"
                strategy_name = "LONG_PUT"
                notes.append("Low IV — long puts are cheap.")
            elif iv_bucket == "MID":
                verdict = "BUY_PUT_SPREAD" if near_earnings else "BUY_PUT"
                strategy_name = "BEAR_PUT_SPREAD" if near_earnings else "LONG_PUT"
                if near_earnings:
                    notes.append("Earnings — debit put spread caps IV crush.")
            else:  # HIGH
                verdict = "SELL_CALL_SPREAD"
                strategy_name = "BEAR_CALL_SPREAD"
                notes.append("High IV — bearish credit spread.")
        else:  # NEUTRAL
            if iv_bucket == "HIGH":
                verdict = "SELL_IRON_CONDOR"
                strategy_name = "IRON_CONDOR"
                notes.append("No directional edge + high IV — harvest premium via iron condor.")
            elif iv_bucket == "LOW" and near_earnings:
                verdict = "BUY_STRADDLE"
                strategy_name = "LONG_STRADDLE"
                notes.append("Low IV into an event — long straddle bets on expansion.")
            else:
                verdict = "NO_TRADE"
                strategy_name = "WAIT"
                notes.append("No edge: neutral direction with moderate IV. Skip.")

        # Pick the target expiry
        candidates = [p for p in per_exp if target_dte_lo <= p["dte"] <= target_dte_hi]
        if not candidates and per_exp:
            # Fall back: nearest expiry that is at least target_dte_lo
            candidates = [p for p in per_exp if p["dte"] >= target_dte_lo] or per_exp[-1:]
        target_exp = candidates[0] if candidates else None

        # Build legs (with liquidity-aware strike snapping)
        legs: list[dict] = []
        legs_dropped_for_liquidity = False
        if target_exp is not None and verdict != "NO_TRADE":
            legs = self._build_legs(strategy_name, target_exp, spot)
            # If the math-derived strikes can't find a tradeable contract in
            # the chain, downgrade to NO_TRADE rather than ship an OI=6 fill.
            if not legs:
                legs_dropped_for_liquidity = True
                min_oi = self._min_oi_for_dte(target_exp.get("dte") or 30)
                notes.append(
                    f"⚠ Liquidity gate: no strike near the target met OI ≥ {min_oi} "
                    f"at {target_exp['dte']}d expiry. Original strategy was {strategy_name} — "
                    f"downgraded to NO_TRADE to avoid an untradeable fill."
                )
                verdict = "NO_TRADE"
                strategy_name = "WAIT"

        # Liquidity warnings
        if liquidity.get("rating") == "WIDE":
            notes.append("⚠ Wide bid-ask spreads — expect meaningful slippage; use limit orders.")

        # Positioning nudges
        if positioning.get("gex_regime") == "NEGATIVE" and verdict.startswith("SELL"):
            notes.append("⚠ Negative-gamma regime = amplified moves; short-premium strategies face larger swings.")

        return {
            "verdict": verdict,
            "strategy": strategy_name,
            "target_expiry": target_exp["expiry"] if target_exp else None,
            "target_dte": target_exp["dte"] if target_exp else None,
            "legs": legs,
            "notes": notes,
            "iv_bucket": iv_bucket,
            "near_earnings": near_earnings,
            "earnings_dte": earnings_dte,
            "legs_dropped_for_liquidity": legs_dropped_for_liquidity,
        }

    # Minimum open-interest floor by DTE bucket. A strike below the floor at
    # the requested DTE is treated as untradeable — caller downgrades to
    # NO_TRADE. Tuned for retail-trader execution: tight enough to avoid
    # OI=6 fills, loose enough to not reject every mid-cap chain.
    @staticmethod
    def _min_oi_for_dte(dte: int) -> int:
        if dte <= 7:
            return 100
        if dte <= 14:
            return 50
        if dte <= 30:
            return 25
        if dte <= 60:
            return 15
        return 10

    @staticmethod
    def _snap_strike(
        target: float,
        contracts: list[dict],
        min_oi: int,
        tolerance_pct: float = 0.05,
    ) -> tuple[float | None, dict | None]:
        """Snap a math-derived target strike to the nearest *liquid* contract.

        Scans contracts within ±tolerance_pct of target; among those meeting
        ``min_oi``, returns the one with the highest liquidity score
        (OI + 5 × daily_vol). Returns (None, None) if nothing qualifies — the
        caller should treat this as a NO_TRADE / LIQUIDITY signal rather than
        ship a recommendation that can't be filled.
        """
        if not contracts or target is None:
            return None, None
        lo = target * (1 - tolerance_pct)
        hi = target * (1 + tolerance_pct)
        candidates: list[tuple[float, dict]] = []
        for c in contracts:
            k = c.get("strike")
            if k is None or not (lo <= k <= hi):
                continue
            oi = int(c.get("openInterest") or 0)
            if oi < min_oi:
                continue
            vol = int(c.get("volume") or 0)
            score = oi + 5 * vol
            # Tie-breaker: prefer strikes closer to the math target
            distance_penalty = abs(k - target) / max(target, 1)
            score = score - distance_penalty
            candidates.append((score, c))
        if not candidates:
            return None, None
        candidates.sort(key=lambda t: t[0], reverse=True)
        best = candidates[0][1]
        return best["strike"], best

    def _build_legs(self, strategy_name: str, exp: dict, spot: float) -> list[dict]:
        """Construct the concrete legs for a given strategy.

        Each math-derived target strike is snapped to the nearest liquid
        contract in the chain (OI floor scaled by DTE). If any required leg
        can't find a tradeable strike, returns ``[]`` — the caller will
        downgrade the verdict to NO_TRADE with a LIQUIDITY hidden risk.
        """
        atm = exp["atm_strike"]
        if atm is None:
            return []

        exp_str = exp["expiry"]
        dte = exp.get("dte") or 30
        em_abs = exp.get("expected_move_abs") or spot * 0.05  # fallback 5% if no straddle
        calls = exp.get("_calls", []) or []
        puts = exp.get("_puts", []) or []
        min_oi = self._min_oi_for_dte(dte)

        def snap(target: float, side: str) -> tuple[float | None, dict | None]:
            return self._snap_strike(target, calls if side == "CALL" else puts, min_oi)

        def liq_note(contract: dict | None) -> str:
            if not contract:
                return ""
            oi = int(contract.get("openInterest") or 0)
            vol = int(contract.get("volume") or 0)
            return f" [OI {oi}, vol {vol}]"

        # Each strategy below builds its target strikes mathematically, then
        # snaps each to the most liquid contract within ±5%. If any leg fails
        # to find a tradeable strike, the whole plan is dropped (return [])
        # so the caller can downgrade to NO_TRADE.

        if strategy_name == "LONG_CALL":
            k, c = snap(atm, "CALL")
            if k is None:
                return []
            return [{
                "action": "BUY", "side": "CALL", "strike": round(k, 2),
                "expiry": exp_str, "qty": 1,
                "rationale": f"ATM call — balanced delta/gamma, clean directional exposure.{liq_note(c)}",
            }]
        if strategy_name == "LONG_PUT":
            k, c = snap(atm, "PUT")
            if k is None:
                return []
            return [{
                "action": "BUY", "side": "PUT", "strike": round(k, 2),
                "expiry": exp_str, "qty": 1,
                "rationale": f"ATM put — balanced delta/gamma, clean directional exposure.{liq_note(c)}",
            }]
        if strategy_name == "BULL_CALL_SPREAD":
            kl, cl = snap(atm, "CALL")
            ks, cs = snap(atm + em_abs, "CALL")
            if kl is None or ks is None or kl >= ks:
                return []
            return [
                {"action": "BUY", "side": "CALL", "strike": round(kl, 2), "expiry": exp_str, "qty": 1,
                 "rationale": f"ATM long leg — bulk of directional P&L.{liq_note(cl)}"},
                {"action": "SELL", "side": "CALL", "strike": round(ks, 2), "expiry": exp_str, "qty": 1,
                 "rationale": f"Short at ~+1 expected-move (${em_abs:.2f}) to finance the debit and cap vega.{liq_note(cs)}"},
            ]
        if strategy_name == "BEAR_PUT_SPREAD":
            kl, cl = snap(atm, "PUT")
            ks, cs = snap(atm - em_abs, "PUT")
            if kl is None or ks is None or kl <= ks:
                return []
            return [
                {"action": "BUY", "side": "PUT", "strike": round(kl, 2), "expiry": exp_str, "qty": 1,
                 "rationale": f"ATM long put — directional downside.{liq_note(cl)}"},
                {"action": "SELL", "side": "PUT", "strike": round(ks, 2), "expiry": exp_str, "qty": 1,
                 "rationale": f"Short at ~-1 expected-move (${em_abs:.2f}) to finance debit and cap vega.{liq_note(cs)}"},
            ]
        if strategy_name == "BULL_PUT_SPREAD":
            ks, cs = snap(atm - em_abs * 0.5, "PUT")
            kl, cl = snap(atm - em_abs * 1.5, "PUT")
            if ks is None or kl is None or kl >= ks:
                return []
            return [
                {"action": "SELL", "side": "PUT", "strike": round(ks, 2), "expiry": exp_str, "qty": 1,
                 "rationale": f"Short put below spot — collects premium; profitable if price stays above strike.{liq_note(cs)}"},
                {"action": "BUY", "side": "PUT", "strike": round(kl, 2), "expiry": exp_str, "qty": 1,
                 "rationale": f"Long put further OTM — caps tail risk at a defined loss.{liq_note(cl)}"},
            ]
        if strategy_name == "BEAR_CALL_SPREAD":
            ks, cs = snap(atm + em_abs * 0.5, "CALL")
            kl, cl = snap(atm + em_abs * 1.5, "CALL")
            if ks is None or kl is None or kl <= ks:
                return []
            return [
                {"action": "SELL", "side": "CALL", "strike": round(ks, 2), "expiry": exp_str, "qty": 1,
                 "rationale": f"Short call above spot — collects premium.{liq_note(cs)}"},
                {"action": "BUY", "side": "CALL", "strike": round(kl, 2), "expiry": exp_str, "qty": 1,
                 "rationale": f"Long call further OTM — caps tail risk.{liq_note(cl)}"},
            ]
        if strategy_name == "IRON_CONDOR":
            sp, csp = snap(atm - em_abs, "PUT")
            lp, clp = snap(atm - em_abs * 2, "PUT")
            sc, csc = snap(atm + em_abs, "CALL")
            lc, clc = snap(atm + em_abs * 2, "CALL")
            if any(x is None for x in (sp, lp, sc, lc)) or lp >= sp or lc <= sc:
                return []
            return [
                {"action": "SELL", "side": "PUT", "strike": round(sp, 2),
                 "expiry": exp_str, "qty": 1,
                 "rationale": f"Short put at -1 expected-move.{liq_note(csp)}"},
                {"action": "BUY", "side": "PUT", "strike": round(lp, 2),
                 "expiry": exp_str, "qty": 1,
                 "rationale": f"Long put wing — caps downside tail.{liq_note(clp)}"},
                {"action": "SELL", "side": "CALL", "strike": round(sc, 2),
                 "expiry": exp_str, "qty": 1,
                 "rationale": f"Short call at +1 expected-move.{liq_note(csc)}"},
                {"action": "BUY", "side": "CALL", "strike": round(lc, 2),
                 "expiry": exp_str, "qty": 1,
                 "rationale": f"Long call wing — caps upside tail.{liq_note(clc)}"},
            ]
        if strategy_name == "LONG_STRADDLE":
            kc, cc_ = snap(atm, "CALL")
            kp, cp_ = snap(atm, "PUT")
            if kc is None or kp is None:
                return []
            return [
                {"action": "BUY", "side": "CALL", "strike": round(kc, 2), "expiry": exp_str, "qty": 1,
                 "rationale": f"ATM call half of the straddle.{liq_note(cc_)}"},
                {"action": "BUY", "side": "PUT", "strike": round(kp, 2), "expiry": exp_str, "qty": 1,
                 "rationale": f"ATM put half — profits on large move either direction.{liq_note(cp_)}"},
            ]
        return []

    # ------------------------------------------------------------------
    # Hidden risks
    # ------------------------------------------------------------------

    def _hidden_risks(
        self,
        *,
        iv_rank: float | None,
        per_exp: list[dict],
        positioning: dict,
        liquidity: dict,
        vol_structure: dict,
        earnings_dte: int | None,
        strategy: dict,
    ) -> list[dict]:
        risks: list[dict] = []

        near_earnings = earnings_dte is not None and 0 <= earnings_dte <= 14
        is_long_premium = strategy.get("verdict", "").startswith("BUY")
        is_short_premium = strategy.get("verdict", "").startswith("SELL")

        # IV CRUSH — long premium into earnings with elevated IV
        if near_earnings and iv_rank is not None and iv_rank > 60 and is_long_premium:
            risks.append({
                "severity": "HIGH",
                "code": "IV_CRUSH",
                "title": "IV Crush after earnings",
                "detail": (
                    f"Earnings in {earnings_dte}d with IV rank {iv_rank:.0f}. "
                    f"Post-event IV typically collapses 30-50%. "
                    f"Long premium positions can lose 15-30% even if the directional thesis plays out."
                ),
            })

        # Only flag greek risks for sides the strategy is actually long.
        long_sides = {
            leg.get("side")
            for leg in (strategy.get("legs") or [])
            if leg.get("action") == "BUY"
        }
        relevant_greek_keys: list[str] = []
        if "CALL" in long_sides:
            relevant_greek_keys.append("atm_call_greeks")
        if "PUT" in long_sides:
            relevant_greek_keys.append("atm_put_greeks")

        # THETA BURN — long premium with heavy theta
        nearest = per_exp[0] if per_exp else None
        if nearest and is_long_premium:
            for leg in relevant_greek_keys:
                g = nearest.get(leg) or {}
                t_pct = g.get("theta_pct")
                if t_pct is not None and t_pct > 2:
                    risks.append({
                        "severity": "HIGH" if t_pct > 3 else "MEDIUM",
                        "code": "THETA_BURN",
                        "title": f"Heavy theta decay ({t_pct:.1f}%/day)",
                        "detail": (
                            f"The near-term {'call' if leg.startswith('atm_call') else 'put'} "
                            f"loses {t_pct:.1f}% of premium per calendar day. "
                            f"Need a fast directional move or vol expansion to overcome decay."
                        ),
                    })

        # VEGA EXPOSURE — long premium with high vega
        if nearest and is_long_premium:
            for leg in relevant_greek_keys:
                g = nearest.get(leg) or {}
                v_pct = g.get("vega_pct")
                if v_pct is not None and v_pct > 10:
                    risks.append({
                        "severity": "MEDIUM",
                        "code": "VEGA_EXPOSURE",
                        "title": f"High vega sensitivity ({v_pct:.1f}% per 1% IV)",
                        "detail": (
                            f"A 5% drop in IV would shave ~{v_pct * 5:.0f}% off the premium regardless of direction. "
                            f"Meaningful for ATM long options near vol-crush events."
                        ),
                    })

        # PIN RISK — near-dated OI magnet near spot
        magnets = positioning.get("pin_magnets", []) or []
        if magnets:
            top = magnets[0]
            risks.append({
                "severity": "MEDIUM",
                "code": "PIN_RISK",
                "title": f"Pin magnet at ${top['strike']:.0f} ({top['dte']}d)",
                "detail": (
                    f"Strike ${top['strike']:.0f} carries {top['oi_share_of_near_spot']:.0f}% "
                    f"of near-spot OI expiring in {top['dte']}d. "
                    f"Price may gravitate there into expiration — positions near this strike can trade sideways through theta."
                ),
            })

        # LIQUIDITY — wide spreads
        if liquidity.get("rating") == "WIDE":
            risks.append({
                "severity": "MEDIUM",
                "code": "LIQUIDITY",
                "title": f"Wide bid-ask spreads (avg {liquidity.get('avg_spread_pct'):.1f}%)",
                "detail": (
                    "Average spread > 15% of premium across expiries. "
                    "Round-trip slippage can eat 5-10% of the trade. Use limit orders; expect partial fills."
                ),
            })

        # LIQUIDITY — strategy downgrade because no strike near the math
        # target met the OI floor for the chosen DTE. High-severity because
        # this means the chain is too thin to act on at the expiry the
        # analyzer wanted.
        if strategy.get("legs_dropped_for_liquidity"):
            risks.append({
                "severity": "HIGH",
                "code": "LIQUIDITY",
                "title": "Strategy downgraded — target strikes too thin to trade",
                "detail": (
                    "No strike near the math-derived target met the open-interest floor "
                    "for the target expiry. The chain at this DTE doesn't support a clean "
                    "fill. Consider a different DTE bucket, or a different ticker."
                ),
            })

        # GAMMA REGIME — negative GEX = vol expansion bias
        gex_regime = positioning.get("gex_regime")
        gex_total = positioning.get("gex_total") or 0
        if gex_regime == "NEGATIVE":
            risks.append({
                "severity": "MEDIUM" if is_short_premium else "LOW",
                "code": "NEGATIVE_GEX",
                "title": "Negative dealer gamma — moves amplify",
                "detail": (
                    f"Net dealer gamma {gex_total:+,.0f}. Dealers hedge with-the-trend, "
                    f"expanding realized vol. Short-premium positions face fatter tails."
                ),
            })

        # BACKWARDATION — event priced in
        if vol_structure.get("term_shape") == "BACKWARDATION":
            risks.append({
                "severity": "LOW",
                "code": "BACKWARDATION",
                "title": "Front-month IV above back-month (backwardation)",
                "detail": (
                    f"Front-back IV spread {vol_structure.get('front_minus_back_iv_pts'):+.1f} pts. "
                    f"Near-term vol priced for a specific event. "
                    f"Expect IV to normalize after the event — harmful to front-month long premium."
                ),
            })

        # SKEW — extreme put skew
        avg_skew = vol_structure.get("avg_skew_25d_iv_pts")
        if avg_skew is not None and avg_skew > 8:
            risks.append({
                "severity": "LOW",
                "code": "PUT_SKEW",
                "title": f"Elevated put skew ({avg_skew:+.1f} IV pts)",
                "detail": (
                    "25d puts significantly more expensive than 25d calls. "
                    "Market pricing tail-risk hedges — downside protection costly, "
                    "and short puts carry larger-than-usual left-tail risk."
                ),
            })

        # SHORT-LEG ASSIGNMENT RISK
        has_short_leg = any(leg.get("action") == "SELL" for leg in (strategy.get("legs") or []))
        if has_short_leg:
            risks.append({
                "severity": "LOW",
                "code": "ASSIGNMENT_RISK",
                "title": "Short leg carries early-assignment risk",
                "detail": (
                    "American options can be assigned at any time — especially short ITM calls "
                    "before a dividend or deep-ITM short puts late in life. Monitor intrinsic vs extrinsic."
                ),
            })

        # EXPECTED MOVE vs actual — data to compute later; stub a generic note
        # (we don't have historical realized moves here)

        return risks

    # ------------------------------------------------------------------
    # Rationale text
    # ------------------------------------------------------------------

    def _build_rationale(
        self,
        *,
        bias: str,
        iv_rank: float | None,
        iv_pct: float | None,
        vol_structure: dict,
        positioning: dict,
        strategy: dict,
    ) -> str:
        parts = []
        iv_str = f"IV rank {iv_rank:.0f}" if iv_rank is not None else "IV rank unavailable"
        if iv_pct is not None:
            iv_str += f" (percentile {iv_pct:.0f})"
        parts.append(f"Bias: {bias}. {iv_str}.")

        shape = vol_structure.get("term_shape")
        if shape and shape != "UNKNOWN":
            parts.append(f"Term structure: {shape}.")

        regime = positioning.get("gex_regime")
        if regime:
            parts.append(f"Dealer gamma regime: {regime}.")

        verdict = strategy.get("verdict")
        target = strategy.get("target_expiry")
        if verdict and verdict != "NO_TRADE" and target:
            parts.append(f"Verdict: {verdict} ({strategy.get('strategy')}) targeting {target}.")
        elif verdict == "NO_TRADE":
            parts.append("Verdict: stand aside — no asymmetric edge right now.")

        return " ".join(parts)
