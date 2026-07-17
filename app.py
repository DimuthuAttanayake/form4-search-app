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


# quick filters a user can click instead of typing (also the dashboard chips)
FILTERS = {
    "buys":      ("t.code = 'P'", "Buys"),
    "sells":     ("t.code = 'S'", "Sells"),
    "grants":    ("t.code = 'A'", "Grants"),
    "exercises": ("t.code IN ('M', 'X')", "Option exercises"),
    "directors": ("f.relationship LIKE '%Director%'", "Directors"),
}


# handling the search query
def execute_search_query(query, flt="", limit=None, offset=None):
    # one helper so the page and the CSV export always search the same way
    conn = get_db_connection()
    search_term = f"%{query}%"

    # the columns a user can search across
    searchable_columns = ["f.insider_name", "f.company", "f.ticker", "f.relationship"]

    # with no search term we match everything, so the page shows the most recent trades
    conditions, params = [], []
    if query:
        conditions.append("(" + " OR ".join([f"{col} LIKE ?" for col in searchable_columns]) + ")")
        params = [search_term] * len(searchable_columns)
    if flt in FILTERS:
        conditions.append(FILTERS[flt][0])
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

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

    # box 1: the ten biggest open-market cash buys (insiders spending their own money)
    buy_rows = conn.execute("""
        SELECT f.ticker || ' · ' || f.insider_name AS label, f.company,
               t.transaction_date, t.shares, t.price, t.value AS n
        FROM trades t JOIN filings f ON t.accession = f.accession
        WHERE t.code = 'P' AND t.value IS NOT NULL
        ORDER BY t.value DESC LIMIT 10
    """).fetchall()
    buys = to_bars(buy_rows)

    # box 2: the ten biggest paper profits vs that day's close, summed per insider
    profit_rows = conn.execute("""
        SELECT f.ticker || ' · ' || f.insider_name AS label,
               ROUND(SUM((t.market_close - t.price) * t.shares), 0) AS n
        FROM trades t JOIN filings f ON t.accession = f.accession
        WHERE t.acquired_disposed = 'Acquired' AND t.market_close IS NOT NULL
          AND t.price IS NOT NULL AND t.shares IS NOT NULL
        GROUP BY label HAVING n > 0
        ORDER BY n DESC LIMIT 10
    """).fetchall()
    profits = to_bars(profit_rows)

    conn.close()
    return {"buys": buys, "profits": profits, "filters": FILTERS}


def to_bars(rows, total=None):
    # attach a width percent to each row so the template can draw a simple bar
    top = max([r["n"] for r in rows], default=1)
    bars = []
    for r in rows:
        b = dict(r)
        b["pct"] = round(r["n"] / top * 100, 1)
        if total:
            b["share"] = round(r["n"] / total * 100)
        bars.append(b)
    return bars


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", **dashboard_data())


@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("query", "").strip()
    flt = request.args.get("flt", "").strip()
    page = request.args.get("page", 1, type=int)
    offset = (page - 1) * PER_PAGE
    results, total_count = execute_search_query(query, flt, limit=PER_PAGE, offset=offset)
    total_pages = (total_count + PER_PAGE - 1) // PER_PAGE
    return render_template("search.html", results=results, query=query, flt=flt,
                           filters=FILTERS, page=page, total_pages=total_pages,
                           total_count=total_count)


@app.route("/export", methods=["GET"])
def export_csv():
    query = request.args.get("query", "").strip()
    flt = request.args.get("flt", "").strip()
    results, _ = execute_search_query(query, flt)  # all matching rows, no page limit

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

@app.template_filter("compact")
def compact(v):
    # short money labels for chart bars: $1.2B / $45M / $980K
    try:
        v = float(v)
    except (ValueError, TypeError):
        return ""
    for cut, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= cut:
            return f"${v / cut:.1f}{suffix}".replace(".0", "")
    return f"${v:,.0f}"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() in ("true", "1")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
