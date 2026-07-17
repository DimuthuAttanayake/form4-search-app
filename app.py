import csv
import io
import os
import sqlite3
from flask import Flask, make_response, render_template, request

app = Flask(__name__)
PER_PAGE = 20  # number of results per page

# dynamic path so it works both locally and on Render
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "form4.db")

# connecting to the database via sqlite3
def get_db_connection():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)  # read-only
    conn.row_factory = sqlite3.Row
    return conn


# handling the search query
def execute_search_query(query, limit=None, offset=None):
    # one helper so the page and the CSV export always search the same way
    conn = get_db_connection()
    search_term = f"%{query}%"

    # the columns a user can search across
    searchable_columns = ["f.insider_name", "f.company", "f.ticker", "f.relationship"]

    # with no search term we match everything, so the page shows the most recent trades
    if query:
        where_clause = "WHERE " + " OR ".join([f"{col} LIKE ?" for col in searchable_columns])
        params = [search_term] * len(searchable_columns)
    else:
        where_clause = ""
        params = []

    # each trade joined to its filing, newest first
    sql_base = f"""
        FROM trades t
        JOIN filings f ON t.accession = f.accession
        {where_clause}
        ORDER BY f.filing_date DESC, t.transaction_date DESC
    """

    total_count = conn.execute(f"SELECT COUNT(*) {sql_base}", params).fetchone()[0]

    select_sql = f"""
        SELECT
            f.insider_name, f.company, f.ticker, f.relationship,
            t.transaction_date, t.code, t.code_meaning, t.acquired_disposed,
            t.shares, t.price, t.value, t.market_close, t.pct_vs_market, f.sec_url
        {sql_base}
    """
    if limit is not None and offset is not None:
        select_sql += " LIMIT ? OFFSET ?"
        results = conn.execute(select_sql, params + [limit, offset]).fetchall()
    else:
        results = conn.execute(select_sql, params).fetchall()

    conn.close()
    return results, total_count


# building the numbers and chart data for the dashboard
def dashboard_data():
    conn = get_db_connection()

    # headline counts (stat tiles)
    stats = {
        "filings": conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0],
        "trades": conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
        "companies": conn.execute("SELECT COUNT(DISTINCT company) FROM filings WHERE company != ''").fetchone()[0],
        "insiders": conn.execute("SELECT COUNT(DISTINCT insider_name) FROM filings WHERE insider_name != ''").fetchone()[0],
    }

    # chart: how many trades of each type (single-series magnitude)
    type_rows = conn.execute("""
        SELECT code_meaning AS label, COUNT(*) AS n FROM trades
        WHERE code_meaning != '' GROUP BY code_meaning ORDER BY n DESC
    """).fetchall()
    types = to_bars(type_rows)

    # chart: most active companies by number of trades
    company_rows = conn.execute("""
        SELECT f.company AS label, COUNT(*) AS n
        FROM trades t JOIN filings f ON t.accession = f.accession
        WHERE f.company != '' GROUP BY f.company ORDER BY n DESC LIMIT 8
    """).fetchall()
    companies = to_bars(company_rows)

    # chart: most active insiders by number of trades
    insider_rows = conn.execute("""
        SELECT f.insider_name AS label, COUNT(*) AS n
        FROM trades t JOIN filings f ON t.accession = f.accession
        WHERE f.insider_name != '' GROUP BY f.insider_name ORDER BY n DESC LIMIT 8
    """).fetchall()
    top_insiders = to_bars(insider_rows)

    # chart: acquired vs disposed (the overall buy/sell balance)
    direction_rows = conn.execute("""
        SELECT acquired_disposed AS label, COUNT(*) AS n FROM trades
        WHERE acquired_disposed != '' GROUP BY acquired_disposed ORDER BY n DESC
    """).fetchall()
    direction = to_bars(direction_rows)

    # donut: how insiders got their shares (cash purchase vs grant vs option exercise)
    acq_rows = conn.execute("""
        SELECT code_meaning AS label, COUNT(*) AS n FROM trades
        WHERE acquired_disposed = 'Acquired' AND code_meaning != ''
        GROUP BY code_meaning ORDER BY n DESC
    """).fetchall()
    acquired = to_slices(acq_rows)

    # chart: how each trade's price compared to that day's market close
    vm = conn.execute("""
        SELECT
            SUM(CASE WHEN pct_vs_market < -1 THEN 1 ELSE 0 END) AS below,
            SUM(CASE WHEN pct_vs_market >= -1 AND pct_vs_market <= 1 THEN 1 ELSE 0 END) AS near,
            SUM(CASE WHEN pct_vs_market > 1 THEN 1 ELSE 0 END) AS above
        FROM trades WHERE pct_vs_market IS NOT NULL
    """).fetchone()
    vs_market = to_bars([
        {"label": "Below market", "n": vm["below"] or 0},
        {"label": "Within 1% of market", "n": vm["near"] or 0},
        {"label": "Above market", "n": vm["above"] or 0},
    ])

    # highlight 1: rare open-market buys (the high-signal event)
    buys = conn.execute("""
        SELECT f.insider_name, f.company, f.ticker, f.relationship,
               t.transaction_date, t.shares, t.price, t.value, t.pct_vs_market, f.sec_url
        FROM trades t JOIN filings f ON t.accession = f.accession
        WHERE t.code = 'P' ORDER BY t.value DESC
    """).fetchall()

    # highlight 2: biggest trades by dollar value
    biggest = conn.execute("""
        SELECT f.insider_name, f.company, f.ticker, t.code_meaning, t.acquired_disposed,
               t.transaction_date, t.shares, t.price, t.value, f.sec_url
        FROM trades t JOIN filings f ON t.accession = f.accession
        WHERE t.value IS NOT NULL ORDER BY t.value DESC LIMIT 10
    """).fetchall()

    # highlight 3: biggest price deviations, each labelled with the likely reason
    outlier_rows = conn.execute("""
        SELECT f.insider_name, f.company, f.ticker, t.code, t.code_meaning,
               t.price, t.market_close, t.pct_vs_market, f.sec_url
        FROM trades t JOIN filings f ON t.accession = f.accession
        WHERE t.pct_vs_market IS NOT NULL
        ORDER BY ABS(t.pct_vs_market) DESC LIMIT 10
    """).fetchall()
    outliers = [dict(r, reason=outlier_reason(r["code"], r["pct_vs_market"])) for r in outlier_rows]

    conn.close()
    return {"stats": stats, "types": types, "companies": companies,
            "top_insiders": top_insiders, "direction": direction,
            "acquired": acquired, "vs_market": vs_market,
            "buys": buys, "biggest": biggest, "outliers": outliers}


def to_bars(rows):
    # attach a width percent to each row so the template can draw a simple bar
    top = max([r["n"] for r in rows], default=1)
    return [{"label": r["label"], "n": r["n"], "pct": round(r["n"] / top * 100, 1)} for r in rows]


def to_slices(rows):
    # build donut slices (share of total) using tints of the one teal accent
    tints = ["#2f6f6a", "#4f938c", "#77b3ad", "#a3ccc8", "#cfe3e1"]
    total = sum(r["n"] for r in rows) or 1
    slices, cum = [], 0.0
    for i, r in enumerate(rows):
        pct = r["n"] / total * 100
        slices.append({"label": r["label"], "n": r["n"], "pct": round(pct),
                       "start": round(cum, 2), "end": round(cum + pct, 2),
                       "color": tints[i % len(tints)]})
        cum += pct
    gradient = ", ".join(f'{s["color"]} {s["start"]}% {s["end"]}%' for s in slices)
    return {"slices": slices, "gradient": gradient}


def outlier_reason(code, pct):
    # explain WHY a trade is far from market, so we never imply wrongdoing
    if code in ("M", "X", "C"):
        return "Option or derivative exercised at its set strike price"
    if abs(pct) > 200:
        return "Likely a data mismatch (foreign share or currency)"
    return "Open-market trade priced away from the daily close"


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", **dashboard_data())


@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("query", "").strip()
    page = request.args.get("page", 1, type=int)
    offset = (page - 1) * PER_PAGE
    results, total_count = execute_search_query(query, limit=PER_PAGE, offset=offset)
    total_pages = (total_count + PER_PAGE - 1) // PER_PAGE
    return render_template("search.html", results=results, query=query,
                           page=page, total_pages=total_pages, total_count=total_count)


@app.route("/export", methods=["GET"])
def export_csv():
    query = request.args.get("query", "").strip()
    results, _ = execute_search_query(query)  # all matching rows, no page limit

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["Insider", "Company", "Ticker", "Role", "Date", "Type",
                 "Shares", "Price", "Value", "Market Close", "Pct vs Market", "SEC URL"])
    for row in results:
        cw.writerow([row["insider_name"], row["company"], row["ticker"], row["relationship"],
                     row["transaction_date"], row["code_meaning"], row["shares"], row["price"],
                     row["value"], row["market_close"], row["pct_vs_market"], row["sec_url"]])

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=insider_trades_export.csv"
    output.headers["Content-type"] = "text/csv"
    return output


@app.route("/methodology", methods=["GET"])
def methodology():
    return render_template("methodology.html")


# jinja filters for clean number formatting in the templates
@app.template_filter("commas")
def commas(v):
    try:
        return "{:,.0f}".format(float(v))
    except (ValueError, TypeError):
        return ""

@app.template_filter("money")
def money(v):
    try:
        return "${:,.2f}".format(float(v))
    except (ValueError, TypeError):
        return ""

@app.template_filter("money0")
def money0(v):
    try:
        return "${:,.0f}".format(float(v))
    except (ValueError, TypeError):
        return ""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() in ("true", "1")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
