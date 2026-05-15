import yfinance as yf

t = yf.Ticker("QXO")
exps = t.options or ()
print("yfinance expirations (first 8):", exps[:8])
print("Has '2026-12-05'?", "2026-12-05" in exps)

if "2026-12-05" in exps:
    ch = t.option_chain("2026-12-05")
    print(f"calls={len(ch.calls)} puts={len(ch.puts)}")
    print("Puts with strike near $22:")
    for _, row in ch.puts.iterrows():
        s = row.get("strike")
        if s is not None and 20 <= float(s) <= 25:
            print(f"  strike={s} lastPrice={row.get('lastPrice')} bid={row.get('bid')} ask={row.get('ask')}")
