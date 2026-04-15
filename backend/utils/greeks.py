"""Black-Scholes greeks computation for EdgeFlow.

Computes delta, gamma, theta, and vega from standard BS inputs.
Uses Abramowitz & Stegun rational approximation for the normal CDF
to avoid a scipy dependency.
"""

from __future__ import annotations

import math

# Risk-free rate — hardcoded, minimal impact on short-dated options greeks.
# The difference between 4% and 5% changes delta by < 0.01 for DTE < 60.
RISK_FREE_RATE = 0.045


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via Abramowitz & Stegun 26.2.17."""
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911

    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2.0)
    return 0.5 * (1.0 + sign * y)


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-x * x / 2.0) / math.sqrt(2.0 * math.pi)


def compute_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    is_call: bool,
) -> dict[str, float | None]:
    """Compute Black-Scholes greeks for a European option.

    Parameters
    ----------
    S : float
        Current stock price.
    K : float
        Strike price.
    T : float
        Time to expiry in years (days / 365.0).
    r : float
        Risk-free rate (annualized, e.g. 0.045).
    sigma : float
        Implied volatility (annualized, e.g. 0.35 for 35%).
    is_call : bool
        True for calls, False for puts.

    Returns
    -------
    dict with keys: delta, gamma, theta, vega.
    All None if inputs are invalid (bad IV, expired, etc.).
    Theta is per-day (negative = decay).
    Vega is per 1% IV change.
    """
    null = {"delta": None, "gamma": None, "theta": None, "vega": None}

    # Guard: reject garbage inputs
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0 or sigma > 5.0:
        return null

    try:
        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r + sigma * sigma / 2.0) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T

        nd1 = _norm_cdf(d1)
        nd2 = _norm_cdf(d2)
        pdf_d1 = _norm_pdf(d1)
        exp_rT = math.exp(-r * T)

        # Delta
        if is_call:
            delta = nd1
        else:
            delta = nd1 - 1.0

        # Gamma (same for calls and puts)
        gamma = pdf_d1 / (S * sigma * sqrt_T)

        # Theta (annualized, then convert to per-day)
        common_theta = -(S * pdf_d1 * sigma) / (2.0 * sqrt_T)
        if is_call:
            theta_annual = common_theta - r * K * exp_rT * nd2
        else:
            theta_annual = common_theta + r * K * exp_rT * _norm_cdf(-d2)
        theta = theta_annual / 365.0  # per day

        # Vega (per 1% IV change)
        vega = S * pdf_d1 * sqrt_T / 100.0

        return {
            "delta": round(delta, 6),
            "gamma": round(gamma, 6),
            "theta": round(theta, 6),
            "vega": round(vega, 6),
        }
    except (ValueError, ZeroDivisionError, OverflowError):
        return null
