"""Options chain analysis and contract selection for EdgeFlow."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OptionsSnapshot, SuggestedOption
from utils.data_sources import DataSourceClient
from utils.scoring import calculate_iv_rank

logger = logging.getLogger(__name__)


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

            # Collect IV values and volumes across all contracts
            all_ivs: list[float] = []
            total_call_volume = 0
            total_put_volume = 0
            unusual_contracts: list[dict] = []

            for exp, sides in chains.items():
                for contract in sides.get("calls", []):
                    iv = contract.get("impliedVolatility")
                    if iv is not None and iv > 0:
                        all_ivs.append(iv)
                    vol = contract.get("volume") or 0
                    oi = contract.get("openInterest") or 0
                    total_call_volume += vol
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
                    iv = contract.get("impliedVolatility")
                    if iv is not None and iv > 0:
                        all_ivs.append(iv)
                    vol = contract.get("volume") or 0
                    oi = contract.get("openInterest") or 0
                    total_put_volume += vol
                    if vol > 0 and oi > 0 and vol > 1.5 * oi:
                        unusual_contracts.append({
                            "type": "PUT",
                            "strike": contract.get("strike"),
                            "expiry": exp,
                            "volume": vol,
                            "open_interest": oi,
                            "ratio": round(vol / oi, 2),
                        })

            # IV rank: use ATM IV as current, max/min across all contracts as proxy
            iv_rank_value: float | None = None
            if all_ivs and stock_price:
                atm_iv = self._find_atm_iv(chains, stock_price)
                if atm_iv is not None:
                    iv_high = max(all_ivs)
                    iv_low = min(all_ivs)
                    iv_rank_value = calculate_iv_rank(atm_iv, iv_high, iv_low)

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

            snapshot = OptionsSnapshot(
                ticker=ticker,
                stock_price=Decimal(str(stock_price)) if stock_price else None,
                iv_rank=Decimal(str(round(iv_rank_value, 2))) if iv_rank_value is not None else None,
                iv_percentile=None,
                put_call_ratio=(
                    Decimal(str(round(put_call_ratio, 3)))
                    if put_call_ratio is not None
                    else None
                ),
                total_call_volume=total_call_volume,
                total_put_volume=total_put_volume,
                avg_call_volume=avg_call_volume,
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

                    # Delta estimate
                    abs_pct = abs(pct_from_atm)
                    if abs_pct <= 0.005:
                        delta_est = 0.50
                    elif abs_pct <= 0.01:
                        delta_est = 0.45
                    elif abs_pct <= 0.02:
                        delta_est = 0.40
                    else:
                        delta_est = 0.35

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
                        "delta_estimate": Decimal(str(delta_est)),
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
