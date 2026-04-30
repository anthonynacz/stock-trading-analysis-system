"""Options chain analysis and contract selection for EdgeFlow."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any


def _safe_int(val: Any) -> int:
    """Convert a value to int, treating None/NaN as 0."""
    if val is None:
        return 0
    try:
        if isinstance(val, float) and math.isnan(val):
            return 0
        return int(val)
    except (TypeError, ValueError):
        return 0

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OptionsSnapshot, SuggestedOption
from utils.data_sources import DataSourceClient
from utils.greeks import RISK_FREE_RATE, compute_greeks
from utils.iv_history import compute_iv_rank_and_percentile

logger = logging.getLogger(__name__)

RISK_PROFILES = {
    "conservative": {
        "call_delta": (0.50, 0.80),
        "put_delta": (-0.80, -0.50),
        "dte": (7, 30),
        "label": "Conservative",
        "desc": "ITM/ATM strikes, shorter DTE. High probability of profit, lower leverage.",
    },
    "moderate": {
        "call_delta": (0.30, 0.55),
        "put_delta": (-0.55, -0.30),
        "dte": (14, 50),
        "label": "Moderate",
        "desc": "ATM to slightly OTM strikes, medium DTE. Balanced cost vs probability.",
    },
    "aggressive": {
        "call_delta": (0.10, 0.35),
        "put_delta": (-0.35, -0.10),
        "dte": (21, 75),
        "label": "Aggressive",
        "desc": "OTM strikes, longer DTE. Maximum leverage, lower probability.",
    },
}


class OptionsAnalyzer:
    """Analyzes options chains and selects optimal contracts."""

    def __init__(self, session: AsyncSession, data_client: DataSourceClient) -> None:
        self._session = session
        self._data_client = data_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze_ticker(self, ticker: str) -> OptionsSnapshot | None:
        """Pull full options chain, compute metrics, persist and return snapshot."""
        try:
            loop = asyncio.get_event_loop()
            chain_data = await loop.run_in_executor(
                None, self._data_client.get_options_chain, ticker
            )

            chains: dict[str, dict[str, list[dict]]] = chain_data.get("chains", {})
            if not chains:
                logger.warning("No chain data returned for %s", ticker)
                return None

            stock_price = chain_data.get("current_price")
            expirations = list(chains.keys())
            num_expirations = len(expirations)

            # Collect volumes and open interest across all contracts
            total_call_volume = 0
            total_put_volume = 0
            total_call_oi = 0
            total_put_oi = 0
            unusual_contracts: list[dict] = []

            for exp, sides in chains.items():
                for contract in sides.get("calls", []):
                    vol = _safe_int(contract.get("volume"))
                    oi = _safe_int(contract.get("openInterest"))
                    total_call_volume += vol
                    total_call_oi += oi
                    if vol > 0 and oi > 0 and vol > 1.5 * oi:
                        unusual_contracts.append({
                            "type": "CALL",
                            "strike": contract.get("strike"),
                            "expiry": exp,
                            "volume": vol,
                            "open_interest": oi,
                            "ratio": round(vol / oi, 2),
                        })

                for contract in sides.get("puts", []):
                    vol = _safe_int(contract.get("volume"))
                    oi = _safe_int(contract.get("openInterest"))
                    total_put_volume += vol
                    total_put_oi += oi
                    if vol > 0 and oi > 0 and vol > 1.5 * oi:
                        unusual_contracts.append({
                            "type": "PUT",
                            "strike": contract.get("strike"),
                            "expiry": exp,
                            "volume": vol,
                            "open_interest": oi,
                            "ratio": round(vol / oi, 2),
                        })

            # IV rank: prefer historical ATM IV over last year of snapshots;
            # fall back to a stabilized cross-sectional proxy when history is thin.
            iv_rank_value: float | None = None
            iv_pct_value: float | None = None
            atm_iv_value: float | None = None
            if stock_price:
                atm_iv_value = self._find_atm_iv(chains, stock_price)
                if atm_iv_value is not None:
                    iv_rank_value, iv_pct_value, _src = await compute_iv_rank_and_percentile(
                        self._session,
                        ticker,
                        atm_iv_value,
                        chains=chains,
                        spot=stock_price,
                    )

            # Put/call ratio
            put_call_ratio: float | None = None
            if total_call_volume > 0:
                put_call_ratio = total_put_volume / total_call_volume
            elif total_put_volume > 0:
                put_call_ratio = 999.999  # all puts, no calls
            else:
                put_call_ratio = None

            # Average call volume per expiration
            avg_call_volume = (
                total_call_volume // num_expirations if num_expirations > 0 else 0
            )

            # Unusual activity detection
            total_volume = total_call_volume + total_put_volume
            avg_total_volume = total_volume / num_expirations if num_expirations > 0 else 0
            has_unusual = bool(unusual_contracts) or (
                avg_total_volume > 0 and total_volume > 2 * avg_total_volume
            )

            unusual_detail: str | None = None
            if unusual_contracts:
                unusual_detail = json.dumps(unusual_contracts)

            def _safe_decimal(val: Any) -> Decimal | None:
                if val is None:
                    return None
                if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                    return None
                return Decimal(str(val))

            snapshot = OptionsSnapshot(
                ticker=ticker,
                stock_price=_safe_decimal(stock_price),
                atm_iv=_safe_decimal(round(atm_iv_value, 4)) if atm_iv_value is not None else None,
                iv_rank=_safe_decimal(round(iv_rank_value, 2)) if iv_rank_value is not None else None,
                iv_percentile=_safe_decimal(round(iv_pct_value, 2)) if iv_pct_value is not None else None,
                put_call_ratio=(
                    _safe_decimal(round(put_call_ratio, 3))
                    if put_call_ratio is not None
                    else None
                ),
                total_call_volume=total_call_volume,
                total_put_volume=total_put_volume,
                avg_call_volume=avg_call_volume,
                total_call_oi=total_call_oi,
                total_put_oi=total_put_oi,
                unusual_activity=has_unusual,
                unusual_activity_detail=unusual_detail,
            )

            self._session.add(snapshot)
            await self._session.commit()
            await self._session.refresh(snapshot)

            logger.info(
                "Options snapshot created for %s — IV rank %.1f, P/C %.3f, unusual=%s",
                ticker,
                iv_rank_value or 0,
                put_call_ratio or 0,
                has_unusual,
            )
            return snapshot

        except Exception:
            logger.exception("analyze_ticker failed for %s", ticker)
            await self._session.rollback()
            return None

    async def analyze_all(self, tickers: list[str]) -> list[OptionsSnapshot]:
        """Analyze all tickers, skipping failures."""
        results: list[OptionsSnapshot] = []
        for ticker in tickers:
            snapshot = await self.analyze_ticker(ticker)
            if snapshot is not None:
                results.append(snapshot)
        return results

    async def detect_unusual_activity(self, ticker: str) -> list[dict]:
        """Find contracts with unusual volume relative to open interest."""
        try:
            loop = asyncio.get_event_loop()
            chain_data = await loop.run_in_executor(
                None, self._data_client.get_options_chain, ticker
            )

            chains: dict[str, dict[str, list[dict]]] = chain_data.get("chains", {})
            unusual: list[dict] = []

            for exp, sides in chains.items():
                for contract_type, label in [("calls", "CALL"), ("puts", "PUT")]:
                    for contract in sides.get(contract_type, []):
                        vol = contract.get("volume") or 0
                        oi = contract.get("openInterest") or 0
                        if vol <= 0 or oi <= 0:
                            continue
                        ratio = vol / oi
                        if ratio > 1.5 and vol > 500:
                            unusual.append({
                                "contract_type": label,
                                "strike": contract.get("strike"),
                                "expiry": exp,
                                "volume": vol,
                                "open_interest": oi,
                                "ratio": round(ratio, 2),
                                "direction_signal": "BULLISH" if label == "CALL" else "BEARISH",
                            })

            unusual.sort(key=lambda x: x["ratio"], reverse=True)
            logger.info("Found %d unusual-activity contracts for %s", len(unusual), ticker)
            return unusual

        except Exception:
            logger.exception("detect_unusual_activity failed for %s", ticker)
            return []

    async def select_optimal_contracts(
        self,
        ticker: str,
        direction: str,
        iv_rank: float,
        current_price: float,
        catalyst_type: str | None = None,
        earnings_date: date | str | None = None,
    ) -> list[dict]:
        """Select best contracts based on direction, IV, and catalyst playbook."""
        try:
            loop = asyncio.get_event_loop()
            chain_data = await loop.run_in_executor(
                None, self._data_client.get_options_chain, ticker
            )

            chains: dict[str, dict[str, list[dict]]] = chain_data.get("chains", {})
            if not chains:
                return []

            # Determine which side to look at
            side_key = "calls" if direction.upper() == "BULLISH" else "puts"
            is_call = side_key == "calls"

            # DTE target range based on catalyst
            dte_min, dte_max = self._dte_range(catalyst_type, earnings_date)

            # Use spread strategy when IV is elevated
            if iv_rank > 70:
                strategy = "CALL_DEBIT_SPREAD" if is_call else "PUT_DEBIT_SPREAD"
            else:
                strategy = "NAKED_CALL" if is_call else "NAKED_PUT"

            today = date.today()
            candidates: list[dict] = []

            for exp_str, sides in chains.items():
                try:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                except ValueError:
                    continue

                dte = (exp_date - today).days
                if dte < dte_min or dte > dte_max:
                    continue

                for contract in sides.get(side_key, []):
                    strike = contract.get("strike")
                    if strike is None:
                        continue

                    bid = contract.get("bid") or 0
                    ask = contract.get("ask") or 0
                    premium = contract.get("lastPrice") or ((bid + ask) / 2 if bid and ask else 0)
                    oi = contract.get("openInterest") or 0

                    # Strike filter: ATM (within 2%) or 1-3% OTM
                    pct_from_atm = (strike - current_price) / current_price
                    if is_call:
                        # Calls: ATM or slightly OTM (0% to +3%)
                        if pct_from_atm < -0.02 or pct_from_atm > 0.03:
                            continue
                    else:
                        # Puts: ATM or slightly OTM (0% to -3%)
                        if pct_from_atm > 0.02 or pct_from_atm < -0.03:
                            continue

                    # Filter: premium < $10, OI > 500, reasonable bid-ask
                    if premium > 10:
                        continue
                    if oi < 500:
                        continue
                    if bid > 0 and ask > 0 and (ask / bid) > 1.15:
                        continue

                    # Compute greeks via Black-Scholes
                    iv = contract.get("impliedVolatility") or 0.30
                    greeks = compute_greeks(
                        S=current_price, K=strike, T=dte / 365.0,
                        r=RISK_FREE_RATE, sigma=iv, is_call=is_call,
                    )
                    delta_est = greeks["delta"] if greeks["delta"] is not None else 0.50

                    # Breakeven
                    if is_call:
                        breakeven = strike + premium
                    else:
                        breakeven = strike - premium

                    strategy_rationale = self._build_rationale(
                        strategy, iv_rank, catalyst_type, dte, direction
                    )

                    candidates.append({
                        "contract_type": "CALL" if is_call else "PUT",
                        "strike": Decimal(str(strike)),
                        "expiry": exp_date,
                        "premium_estimate": Decimal(str(round(premium, 2))),
                        "delta_estimate": Decimal(str(round(abs(delta_est), 4))),
                        "gamma_estimate": Decimal(str(round(greeks["gamma"], 6))) if greeks["gamma"] else None,
                        "theta_estimate": Decimal(str(round(greeks["theta"], 4))) if greeks["theta"] else None,
                        "vega_estimate": Decimal(str(round(greeks["vega"], 4))) if greeks["vega"] else None,
                        "strategy": strategy,
                        "strategy_rationale": strategy_rationale,
                        "days_to_expiry": dte,
                        "breakeven_price": Decimal(str(round(breakeven, 2))),
                    })

            # Sort by delta (prefer higher delta / closer to ATM), then by OI implicitly
            candidates.sort(key=lambda c: float(c["delta_estimate"]), reverse=True)
            selected = candidates[:3]

            logger.info(
                "Selected %d optimal contracts for %s (%s, IV rank %.1f)",
                len(selected), ticker, direction, iv_rank,
            )
            return selected

        except Exception:
            logger.exception("select_optimal_contracts failed for %s", ticker)
            return []

    async def get_latest_snapshot(self, ticker: str) -> OptionsSnapshot | None:
        """Query DB for the most recent options snapshot."""
        try:
            stmt = (
                select(OptionsSnapshot)
                .where(OptionsSnapshot.ticker == ticker)
                .order_by(OptionsSnapshot.snapshot_time.desc())
                .limit(1)
            )
            result = await self._session.execute(stmt)
            return result.scalar_one_or_none()
        except Exception:
            logger.exception("get_latest_snapshot failed for %s", ticker)
            return None

    # ------------------------------------------------------------------
    # Strike recommender
    # ------------------------------------------------------------------

    async def recommend_strikes(
        self,
        ticker: str,
        risk_level: str = "moderate",
        max_budget: float | None = None,
    ) -> dict:
        """Recommend best call and put strikes for a given risk/budget profile."""
        profile = RISK_PROFILES.get(risk_level)
        if not profile:
            raise ValueError(f"Invalid risk level: {risk_level}")

        loop = asyncio.get_event_loop()
        chain_data = await loop.run_in_executor(
            None, self._data_client.get_options_chain, ticker
        )
        chains = chain_data.get("chains", {})
        current_price = chain_data.get("current_price")
        if not chains or not current_price:
            return {
                "ticker": ticker,
                "current_price": current_price,
                "risk_level": risk_level,
                "max_budget": max_budget,
                "recommended_call": None,
                "recommended_put": None,
            }

        dte_min, dte_max = profile["dte"]
        today = date.today()

        def _scan(side_key: str, delta_range: tuple, is_call: bool) -> dict | None:
            lo, hi = delta_range
            best = None
            best_score = -1.0

            for exp_str, sides in chains.items():
                try:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                dte = (exp_date - today).days
                if dte < dte_min or dte > dte_max:
                    continue

                for c in sides.get(side_key, []):
                    strike = c.get("strike")
                    if strike is None:
                        continue

                    bid = c.get("bid") or 0
                    ask = c.get("ask") or 0
                    premium = c.get("lastPrice") or ((bid + ask) / 2 if bid and ask else 0)
                    oi = c.get("openInterest") or 0

                    if oi < 10 or premium <= 0:
                        continue
                    if bid > 0 and ask > 0 and (ask / bid) > 1.50:
                        continue
                    if max_budget is not None and premium * 100 > max_budget:
                        continue

                    # Compute greeks via Black-Scholes
                    iv = c.get("impliedVolatility") or 0.30
                    greeks = compute_greeks(
                        S=current_price, K=strike, T=dte / 365.0,
                        r=RISK_FREE_RATE, sigma=iv, is_call=is_call,
                    )
                    if is_call:
                        delta = greeks["delta"] if greeks["delta"] is not None else 0.50
                    else:
                        delta = greeks["delta"] if greeks["delta"] is not None else -0.50

                    if delta < lo or delta > hi:
                        continue

                    # Score: prefer delta near midpoint of target range, then higher OI
                    mid = (lo + hi) / 2
                    score = 1.0 - abs(delta - mid) / max(abs(hi - lo), 0.01)
                    score += min(oi / 10000, 0.3)  # OI bonus

                    # Theta penalty: heavy theta contracts ranked lower
                    if greeks["theta"] is not None and premium > 0:
                        theta_pct = abs(greeks["theta"]) / premium
                        if theta_pct > 0.03:
                            score -= 0.15
                        elif theta_pct > 0.015:
                            score -= 0.05

                    if score > best_score:
                        breakeven = (strike + premium) if is_call else (strike - premium)
                        risk_label = profile["label"]
                        contract_label = "call" if is_call else "put"
                        expl = (
                            f"{risk_label} {contract_label}: ${strike:.0f} strike, {dte} DTE, "
                            f"delta {abs(delta):.2f}. "
                            f"Premium ${premium:.2f} (${premium * 100:.0f}/contract). "
                            f"Breakeven at ${breakeven:.2f}. "
                            f"{profile['desc']}"
                        )

                        # Greek warnings
                        warnings = []
                        if greeks["theta"] is not None and premium > 0:
                            t_pct = abs(greeks["theta"]) / premium * 100
                            if t_pct > 3:
                                warnings.append(f"HIGH THETA: losing {t_pct:.1f}% premium/day")
                            elif t_pct > 1.5:
                                warnings.append(f"Theta: {t_pct:.1f}% daily decay")
                        if greeks["vega"] is not None and premium > 0:
                            v_pct = abs(greeks["vega"]) / premium * 100
                            if v_pct > 10:
                                warnings.append(f"HIGH VEGA: {v_pct:.1f}% per 1% IV move")
                        if warnings:
                            expl += " | " + "; ".join(warnings)

                        best = {
                            "strike": round(strike, 2),
                            "expiry": exp_date.isoformat(),
                            "premium_estimate": round(premium, 2),
                            "delta_estimate": round(abs(delta), 4),
                            "gamma_estimate": round(greeks["gamma"], 6) if greeks["gamma"] else None,
                            "theta_estimate": round(greeks["theta"], 4) if greeks["theta"] else None,
                            "vega_estimate": round(greeks["vega"], 4) if greeks["vega"] else None,
                            "breakeven": round(breakeven, 2),
                            "days_to_expiry": dte,
                            "open_interest": oi,
                            "explanation": expl,
                        }
                        best_score = score

            return best

        call_result = _scan("calls", profile["call_delta"], is_call=True)
        put_result = _scan("puts", profile["put_delta"], is_call=False)

        return {
            "ticker": ticker,
            "current_price": round(current_price, 2),
            "risk_level": risk_level,
            "max_budget": max_budget,
            "recommended_call": call_result,
            "recommended_put": put_result,
        }

    async def recommend_strikes_all(
        self,
        ticker: str,
        max_budget: float | None = None,
    ) -> dict:
        """Recommend strikes for all three risk levels in a single chain fetch."""
        loop = asyncio.get_event_loop()
        chain_data = await loop.run_in_executor(
            None, self._data_client.get_options_chain, ticker
        )
        chains = chain_data.get("chains", {})
        current_price = chain_data.get("current_price")
        if not chains or not current_price:
            return {
                "ticker": ticker,
                "current_price": current_price,
                "max_budget": max_budget,
                "conservative": {"recommended_call": None, "recommended_put": None},
                "moderate": {"recommended_call": None, "recommended_put": None},
                "aggressive": {"recommended_call": None, "recommended_put": None},
            }

        results: dict = {
            "ticker": ticker,
            "current_price": round(current_price, 2),
            "max_budget": max_budget,
        }
        for level, profile in RISK_PROFILES.items():
            call_result = self._scan_strikes(
                chains, current_price, profile, "calls", True, max_budget
            )
            put_result = self._scan_strikes(
                chains, current_price, profile, "puts", False, max_budget
            )
            results[level] = {
                "recommended_call": call_result,
                "recommended_put": put_result,
            }
        return results

    def _scan_strikes(
        self,
        chains: dict,
        current_price: float,
        profile: dict,
        side_key: str,
        is_call: bool,
        max_budget: float | None,
    ) -> dict | None:
        """Scan a single side (calls/puts) for a given risk profile."""
        delta_range = profile["call_delta"] if is_call else profile["put_delta"]
        lo, hi = delta_range
        dte_min, dte_max = profile["dte"]
        today = date.today()
        best = None
        best_score = -1.0

        for exp_str, sides in chains.items():
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            dte = (exp_date - today).days
            if dte < dte_min or dte > dte_max:
                continue

            for c in sides.get(side_key, []):
                strike = c.get("strike")
                if strike is None:
                    continue

                bid = c.get("bid") or 0
                ask = c.get("ask") or 0
                premium = c.get("lastPrice") or ((bid + ask) / 2 if bid and ask else 0)
                oi = c.get("openInterest") or 0

                if oi < 10 or premium <= 0:
                    continue
                if bid > 0 and ask > 0 and (ask / bid) > 1.50:
                    continue
                if max_budget is not None and premium * 100 > max_budget:
                    continue

                # Compute greeks via Black-Scholes
                iv = c.get("impliedVolatility") or 0.30
                greeks = compute_greeks(
                    S=current_price, K=strike, T=dte / 365.0,
                    r=RISK_FREE_RATE, sigma=iv, is_call=is_call,
                )
                if is_call:
                    delta = greeks["delta"] if greeks["delta"] is not None else 0.50
                else:
                    delta = greeks["delta"] if greeks["delta"] is not None else -0.50

                if delta < lo or delta > hi:
                    continue

                mid = (lo + hi) / 2
                score = 1.0 - abs(delta - mid) / max(abs(hi - lo), 0.01)
                score += min(oi / 10000, 0.3)

                # Theta penalty: heavy theta contracts ranked lower
                if greeks["theta"] is not None and premium > 0:
                    theta_pct = abs(greeks["theta"]) / premium
                    if theta_pct > 0.03:
                        score -= 0.15
                    elif theta_pct > 0.015:
                        score -= 0.05

                if score > best_score:
                    breakeven = (strike + premium) if is_call else (strike - premium)
                    risk_label = profile["label"]
                    contract_label = "call" if is_call else "put"
                    expl = (
                        f"{risk_label} {contract_label}: ${strike:.0f} strike, {dte} DTE, "
                        f"delta {abs(delta):.2f}. "
                        f"Premium ${premium:.2f} (${premium * 100:.0f}/contract). "
                        f"Breakeven at ${breakeven:.2f}. "
                        f"{profile['desc']}"
                    )

                    # Greek warnings
                    warnings = []
                    if greeks["theta"] is not None and premium > 0:
                        t_pct = abs(greeks["theta"]) / premium * 100
                        if t_pct > 3:
                            warnings.append(f"HIGH THETA: losing {t_pct:.1f}% premium/day")
                        elif t_pct > 1.5:
                            warnings.append(f"Theta: {t_pct:.1f}% daily decay")
                    if greeks["vega"] is not None and premium > 0:
                        v_pct = abs(greeks["vega"]) / premium * 100
                        if v_pct > 10:
                            warnings.append(f"HIGH VEGA: {v_pct:.1f}% per 1% IV move")
                    if warnings:
                        expl += " | " + "; ".join(warnings)

                    best = {
                        "strike": round(strike, 2),
                        "expiry": exp_date.isoformat(),
                        "premium_estimate": round(premium, 2),
                        "delta_estimate": round(abs(delta), 4),
                        "gamma_estimate": round(greeks["gamma"], 6) if greeks["gamma"] else None,
                        "theta_estimate": round(greeks["theta"], 4) if greeks["theta"] else None,
                        "vega_estimate": round(greeks["vega"], 4) if greeks["vega"] else None,
                        "breakeven": round(breakeven, 2),
                        "days_to_expiry": dte,
                        "open_interest": oi,
                        "explanation": expl,
                    }
                    best_score = score

        return best

    # ------------------------------------------------------------------
    # Chain greeks summary (for recommendation engine signals)
    # ------------------------------------------------------------------

    async def get_chain_greeks_summary(
        self, ticker: str, current_price: float
    ) -> dict | None:
        """Compute aggregate greek metrics from ATM contracts.

        Fetches the options chain and finds ATM contracts for the nearest
        expirations. Returns average theta and vega as percentages of
        premium, used by the recommendation engine for greek signals.
        """
        if not current_price or current_price <= 0:
            return None

        try:
            loop = asyncio.get_event_loop()
            chain_data = await loop.run_in_executor(
                None, self._data_client.get_options_chain, ticker
            )
            chains = chain_data.get("chains", {})
            if not chains:
                return None

            today = date.today()
            samples: list[dict] = []

            # Sample from DTE >= 14 (moderate profile minimum) to reflect
            # contracts traders actually hold. Weeklies (< 14 DTE) have
            # naturally extreme theta that would fire on every ticker.
            eligible_exps = []
            for exp_str in sorted(chains.keys()):
                try:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                dte = (exp_date - today).days
                if dte >= 14:
                    eligible_exps.append((exp_str, dte))
                if len(eligible_exps) >= 2:
                    break

            for exp_str, dte in eligible_exps:

                sides = chains[exp_str]
                # Find ATM call and put (nearest strike to current price)
                for side_key, is_call in [("calls", True), ("puts", False)]:
                    contracts = sides.get(side_key, [])
                    if not contracts:
                        continue

                    # Find contract with strike closest to current price
                    atm = min(
                        contracts,
                        key=lambda c: abs((c.get("strike") or 0) - current_price),
                    )
                    strike = atm.get("strike")
                    if not strike:
                        continue

                    bid = atm.get("bid") or 0
                    ask = atm.get("ask") or 0
                    premium = atm.get("lastPrice") or ((bid + ask) / 2 if bid and ask else 0)
                    iv = atm.get("impliedVolatility") or 0

                    if premium <= 0 or iv <= 0:
                        continue

                    greeks = compute_greeks(
                        S=current_price, K=strike, T=dte / 365.0,
                        r=RISK_FREE_RATE, sigma=iv, is_call=is_call,
                    )
                    if greeks["theta"] is None:
                        continue

                    samples.append({
                        "theta_pct": abs(greeks["theta"]) / premium,
                        "vega_pct": abs(greeks["vega"]) / premium if greeks["vega"] else 0,
                        "dte": dte,
                    })

            if not samples:
                return None

            avg_theta_pct = sum(s["theta_pct"] for s in samples) / len(samples)
            avg_vega_pct = sum(s["vega_pct"] for s in samples) / len(samples)
            near_term_dte = min(s["dte"] for s in samples)

            return {
                "avg_atm_theta_pct": round(avg_theta_pct, 6),
                "avg_atm_vega_pct": round(avg_vega_pct, 6),
                "near_term_dte": near_term_dte,
            }

        except Exception:
            logger.exception("get_chain_greeks_summary failed for %s", ticker)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_atm_iv(
        chains: dict[str, dict[str, list[dict]]], stock_price: float
    ) -> float | None:
        """Find implied volatility of the contract nearest to ATM across all expirations."""
        best_iv: float | None = None
        best_dist = float("inf")

        for _exp, sides in chains.items():
            for contract in sides.get("calls", []):
                strike = contract.get("strike")
                iv = contract.get("impliedVolatility")
                if strike is None or iv is None or iv <= 0:
                    continue
                dist = abs(strike - stock_price)
                if dist < best_dist:
                    best_dist = dist
                    best_iv = iv

        return best_iv

    @staticmethod
    def _dte_range(
        catalyst_type: str | None, earnings_date: date | str | None
    ) -> tuple[int, int]:
        """Return (min_dte, max_dte) based on catalyst type."""
        if catalyst_type == "TIER_CHANGE":
            return 21, 28
        if catalyst_type == "PT_CHANGE":
            return 14, 21
        if catalyst_type == "EARNINGS" and earnings_date is not None:
            if isinstance(earnings_date, str):
                try:
                    earnings_date = datetime.strptime(earnings_date, "%Y-%m-%d").date()
                except ValueError:
                    return 21, 35
            days_to_earnings = (earnings_date - date.today()).days
            # Expiry should be 14-28 days AFTER earnings
            dte_min = days_to_earnings + 14
            dte_max = days_to_earnings + 28
            return max(dte_min, 1), max(dte_max, dte_min + 1)
        return 21, 35

    @staticmethod
    def _build_rationale(
        strategy: str,
        iv_rank: float,
        catalyst_type: str | None,
        dte: int,
        direction: str,
    ) -> str:
        """Build a human-readable strategy rationale."""
        parts: list[str] = []

        if "SPREAD" in strategy:
            parts.append(f"IV rank {iv_rank:.0f} is elevated (>70); using debit spread to reduce vega risk")
        else:
            parts.append(f"IV rank {iv_rank:.0f} is moderate; naked option provides full delta exposure")

        if catalyst_type:
            parts.append(f"catalyst: {catalyst_type}")

        parts.append(f"{dte} DTE, {direction.lower()} bias")
        return ". ".join(parts) + "."
