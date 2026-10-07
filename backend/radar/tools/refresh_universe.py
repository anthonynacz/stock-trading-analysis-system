"""Rebuild radar/data/universe.csv: S&P 500 + Nasdaq-100 + the reference ETFs.

    python -m radar.tools.refresh_universe            # fetch, print the diff, write the CSV
    python -m radar.tools.refresh_universe --check    # fetch and print the diff only

Sources (radar/docs/data-sources.md section 3): the datasets/s-and-p-500-companies CSV for the S&P 500
with GICS sectors, api.nasdaq.com for the Nasdaq-100 list, and the Yahoo quote endpoint for the sector
and industry of Nasdaq-100 names outside the S&P 500 (sector mapped onto its GICS name). Nothing is
written when either list looks truncated.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

from curl_cffi import requests as creq

from radar.fetch import HttpTransport, from_nasdaq
from radar.universe import COLUMNS, UNIVERSE_CSV, gics_sector, load_universe

SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
NDX_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
TIMEOUT_S = 30.0
SP500_COUNT = (495, 510)
NDX_COUNT = (98, 104)
ETFS = {
    "SPY": "State Street SPDR S&P 500 ETF Trust",
    "QQQ": "Invesco QQQ Trust",
    "IWM": "iShares Russell 2000 ETF",
    "DIA": "State Street SPDR Dow Jones Industrial Average ETF Trust",
    "XLK": "State Street Technology Select Sector SPDR ETF",
    "XLF": "State Street Financial Select Sector SPDR ETF",
    "XLE": "State Street Energy Select Sector SPDR ETF",
    "XLV": "State Street Health Care Select Sector SPDR ETF",
    "XLI": "State Street Industrial Select Sector SPDR ETF",
    "XLY": "State Street Consumer Discretionary Select Sector SPDR ETF",
    "XLP": "State Street Consumer Staples Select Sector SPDR ETF",
    "XLU": "State Street Utilities Select Sector SPDR ETF",
    "XLB": "State Street Materials Select Sector SPDR ETF",
    "XLRE": "State Street Real Estate Select Sector SPDR ETF",
    "XLC": "State Street Communication Services Select Sector SPDR ETF",
    "SMH": "VanEck Semiconductor ETF",
    "TLT": "iShares 20+ Year Treasury Bond ETF",
    "GLD": "SPDR Gold Shares",
    "SLV": "iShares Silver Trust",
    "USO": "United States Oil Fund, LP",
    "HYG": "iShares iBoxx $ High Yield Corporate Bond ETF",
    "ARKK": "ARK Innovation ETF",
    "KRE": "State Street SPDR S&P Regional Banking ETF",
    "XBI": "State Street SPDR S&P Biotech ETF",
}


def parse_sp500(text: str) -> list[dict]:
    return [{"symbol": from_nasdaq(r["Symbol"]), "name": r["Security"].strip() or None,
             "sector": r["GICS Sector"].strip() or None, "industry": r["GICS Sub-Industry"].strip() or None}
            for r in csv.DictReader(io.StringIO(text))]


def parse_ndx(body: dict) -> list[dict]:
    rows = ((body.get("data") or {}).get("data") or {}).get("rows") or []
    return [{"symbol": from_nasdaq(r["symbol"]), "name": r.get("companyName")} for r in rows]


def build_rows(sp500: list[dict], ndx: list[dict], profiles: dict[str, dict]) -> list[dict]:
    """Merge the lists; `profiles` holds Yahoo quote rows for Nasdaq-100 names outside the S&P 500."""
    rows = {r["symbol"]: {**r, "in_sp500": True, "in_ndx": False, "is_etf": False} for r in sp500}
    for r in ndx:
        if r["symbol"] in rows:
            rows[r["symbol"]]["in_ndx"] = True
            continue
        p = profiles.get(r["symbol"], {})
        rows[r["symbol"]] = {"symbol": r["symbol"], "name": p.get("longName") or p.get("shortName") or r["name"],
                             "sector": gics_sector(p.get("sector")), "industry": p.get("industry"),
                             "in_sp500": False, "in_ndx": True, "is_etf": False}
    for symbol, name in ETFS.items():
        rows[symbol] = {"symbol": symbol, "name": name, "sector": "ETF", "industry": "ETF",
                        "in_sp500": False, "in_ndx": False, "is_etf": True}
    return sorted(rows.values(), key=lambda r: r["symbol"])


def check_counts(rows: list[dict]) -> None:
    sp500 = sum(r["in_sp500"] for r in rows)
    ndx = sum(r["in_ndx"] for r in rows)
    if not (SP500_COUNT[0] <= sp500 <= SP500_COUNT[1] and NDX_COUNT[0] <= ndx <= NDX_COUNT[1]):
        raise ValueError(f"refusing to write: {sp500} S&P 500 and {ndx} Nasdaq-100 rows look truncated")


def diff(old: list[dict], new: list[dict]) -> list[str]:
    before = {r["symbol"]: r for r in old}
    after = {r["symbol"]: r for r in new}
    lines = [f"+ {s} {after[s]['name']}" for s in sorted(after.keys() - before.keys())]
    lines += [f"- {s} {before[s]['name']}" for s in sorted(before.keys() - after.keys())]
    for s in sorted(before.keys() & after.keys()):
        changed = [f"{k}: {before[s][k]!r} -> {after[s][k]!r}" for k in COLUMNS[1:] if before[s][k] != after[s][k]]
        if changed:
            lines.append(f"~ {s} " + "; ".join(changed))
    return lines


def write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({k: "" if r[k] is None else r[k] for k in COLUMNS} for r in rows)


def fetch_inputs() -> tuple[list[dict], list[dict], dict[str, dict]]:
    r = creq.get(SP500_URL, impersonate="chrome", timeout=TIMEOUT_S)
    r.raise_for_status()
    sp500 = parse_sp500(r.text)
    transport = HttpTransport()
    status, body = transport.nasdaq(NDX_URL, {}, TIMEOUT_S)
    if status != 200:
        raise RuntimeError(f"Nasdaq-100 list: HTTP {status}")
    ndx = parse_ndx(body)
    extra = sorted({r["symbol"] for r in ndx} - {r["symbol"] for r in sp500})
    if not extra:
        return sp500, ndx, {}
    params = {"symbols": ",".join(extra), "formatted": "false", "fields": "longName,shortName,sector,industry"}
    status, body = transport.yahoo_quote(params, TIMEOUT_S)
    if status != 200:
        raise RuntimeError(f"Yahoo profiles for {len(extra)} Nasdaq-100 names: HTTP {status}")
    return sp500, ndx, {row["symbol"]: row for row in body["quoteResponse"]["result"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="print the diff without writing")
    ap.add_argument("--out", type=Path, default=UNIVERSE_CSV)
    args = ap.parse_args(argv)
    rows = build_rows(*fetch_inputs())
    try:
        check_counts(rows)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    changes = diff(load_universe(args.out) if args.out.exists() else [], rows)
    print("\n".join(changes) or "no changes")
    print(f"{len(rows)} rows: {sum(r['in_sp500'] for r in rows)} S&P 500, "
          f"{sum(r['in_ndx'] for r in rows)} Nasdaq-100, {len(ETFS)} ETFs")
    if changes and not args.check:
        write_csv(rows, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
