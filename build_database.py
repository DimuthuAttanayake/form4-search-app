# BUILD THE DATABASE
# Turns the raw scraped Form 4 JSON into the SQLite file the Flask app reads.
# Steps: read JSON -> dedupe -> flatten -> merge Yahoo prices -> write two tables.

import json
import time
import sqlite3
import datetime
import urllib.request
import pandas as pd

DATA_FILE = "data/sec_form4.json"
DB_FILE = "form4.db"

# plain-English meaning for each SEC transaction code
CODE_MEANING = {
    "P": "Open-market purchase", "S": "Open-market sale", "A": "Grant or award",
    "M": "Option exercise", "F": "Tax withholding", "G": "Gift",
    "C": "Conversion", "D": "Disposition to issuer", "X": "Option exercise", "J": "Other",
}

def relationship(rel):
    # turn the 1/0 flags into a readable role like "Director, CFO"
    if not rel:
        return ""
    parts = []
    if rel.get("isDirector") == "1": parts.append("Director")
    if rel.get("isOfficer") == "1": parts.append(rel.get("officerTitle") or "Officer")
    if rel.get("isTenPercentOwner") == "1": parts.append("10% owner")
    if rel.get("isOther") == "1": parts.append("Other")
    return ", ".join(parts)

def clean_date(v):
    # some dates come with a timezone tail like 2024-12-27-05:00, keep just YYYY-MM-DD
    return (v or "")[:10]

def to_float(v):
    # some fields are blank or text, so guard the conversion
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


# STEP ONE: read the raw scrape
with open(DATA_FILE) as fh:
    data = json.load(fh)
print(f"read {len(data)} raw entries")

# STEP TWO: dedupe by accession number (each filing is listed twice)
seen = {}
for r in data:
    acc = r.get("accession_number")
    if acc and acc not in seen:
        seen[acc] = r
filings_raw = list(seen.values())
print(f"{len(filings_raw)} unique filings after dedupe")

# STEP THREE: build the two tables in memory
filing_rows = []
trade_rows = []
for r in filings_raw:
    acc = r.get("accession_number")
    # one row per filing
    filing_rows.append({
        "accession": acc,
        "insider_name": r.get("reporting_owner_name", ""),
        "insider_cik": r.get("reporting_owner_cik", ""),
        "company": (r.get("issuer_name", "") or "").title(),
        "ticker": (r.get("issuer_trading_symbol") or "").strip(),
        "relationship": relationship(r.get("relationship")),
        "filing_date": r.get("filing_date", ""),
        "footnotes": " | ".join(r.get("footnotes", [])),
        "sec_url": r.get("url", ""),
    })
    # one row per transaction inside the filing
    for t in r.get("non_derivative_transactions", []) + r.get("derivative_transactions", []):
        shares = to_float(t.get("shares"))
        price = to_float(t.get("price_per_share"))
        ad = t.get("acquired_disposed", "")
        trade_rows.append({
            "accession": acc,
            "ticker": (r.get("issuer_trading_symbol") or "").strip(),
            "transaction_date": clean_date(t.get("transaction_date")),
            "code": t.get("transaction_code", ""),
            "code_meaning": CODE_MEANING.get(t.get("transaction_code", ""), ""),
            "acquired_disposed": "Acquired" if ad == "A" else ("Disposed" if ad == "D" else ad),
            "security": t.get("security_title", ""),
            "shares": shares,
            "price": price,
            "value": round(shares * price, 2) if (shares and price) else None,
            "shares_after": to_float(t.get("shares_owned_following")),
            "ownership": "Direct" if t.get("direct_or_indirect") == "D" else ("Indirect" if t.get("direct_or_indirect") == "I" else ""),
        })
print(f"flattened into {len(trade_rows)} trades")

# STEP FOUR: fetch Yahoo prices and merge on ticker + date
def get_prices(tickers, start_date, end_date):
    # one API call per ticker gets its daily closes across the range
    start_unix = int(time.mktime(datetime.date.fromisoformat(start_date).timetuple())) - 86400 * 4
    end_unix = int(time.mktime(datetime.date.fromisoformat(end_date).timetuple())) + 86400 * 2
    head = {"User-Agent": "Mozilla/5.0"}
    prices = {}
    for ticker in tickers:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
               f"?period1={start_unix}&period2={end_unix}&interval=1d")
        try:
            req = urllib.request.Request(url, headers=head)
            js = json.loads(urllib.request.urlopen(req, timeout=30).read())
            result = js["chart"]["result"][0]
            stamps = result.get("timestamp", [])
            closes = result["indicators"]["quote"][0]["close"]
            for i, s in enumerate(stamps):
                if closes[i] is not None:
                    prices[(ticker, datetime.date.fromtimestamp(s).isoformat())] = round(closes[i], 2)
        except Exception as e:
            print(f"{ticker}: no price data ({e})")
        time.sleep(0.2)
    return prices

all_tickers = sorted({t["ticker"] for t in trade_rows if t["ticker"]})
all_dates = [t["transaction_date"] for t in trade_rows if t["transaction_date"]]
prices = get_prices(all_tickers, min(all_dates), max(all_dates))

matched = 0
for t in trade_rows:
    close = prices.get((t["ticker"], t["transaction_date"]))
    t["market_close"] = close
    if close and t["price"]:
        t["pct_vs_market"] = round((t["price"] - close) / close * 100, 1)
        matched += 1
    else:
        t["pct_vs_market"] = None
print(f"matched {matched}/{len(trade_rows)} trades to a market price")

# STEP FIVE: load into pandas, then write to SQLite
df_filings = pd.DataFrame(filing_rows)
df_trades = pd.DataFrame(trade_rows)

conn = sqlite3.connect(DB_FILE)
df_filings.to_sql("filings", conn, if_exists="replace", index=False)
df_trades.to_sql("trades", conn, if_exists="replace", index=False)
# indexes so the app's searches stay fast as the data grows
conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_accession ON trades(accession)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker)")
conn.commit()
conn.close()

print(f"wrote {DB_FILE}: {len(df_filings)} filings, {len(df_trades)} trades")
