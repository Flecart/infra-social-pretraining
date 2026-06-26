"""Slow, resumable Semantic Scholar collector.

For every query in taxonomy.json it pages through the S2 bulk-search endpoint,
storing papers + authors + seed provenance in SQLite. It is polite by default
(a multi-second delay between requests, exponential backoff on 429/5xx) and
resumable: progress per query is checkpointed, so you can Ctrl-C and re-run.

Usage
-----
  python3 collect.py                       # collect all categories
  python3 collect.py --category ai-security
  python3 collect.py --max-per-query 500   # cap papers pulled per query
  python3 collect.py --delay 3.0           # seconds between requests
  python3 collect.py --reset               # forget progress and start over

Set S2_API_KEY in the environment for higher rate limits (optional).
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import db

BULK_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
FIELDS = ",".join([
    "title", "abstract", "year", "venue", "url", "citationCount",
    "influentialCitationCount", "fieldsOfStudy", "publicationTypes", "authors",
])
PAGE_SIZE = 1000  # bulk endpoint returns up to 1000 per call


def api_get(params, api_key=None, max_retries=6):
    url = BULK_URL + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "researcher-map/1.0 (+local)"}
    if api_key:
        headers["x-api-key"] = api_key
    backoff = 5.0
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = backoff * (2 ** attempt)
                print(f"    HTTP {e.code}; backing off {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            wait = backoff * (2 ** attempt)
            print(f"    network error {e}; retry in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"giving up after {max_retries} retries: {url}")


def collect_query(conn, category_id, query, delay, max_per_query, api_key, min_year=None):
    row = conn.execute("SELECT token,fetched,done FROM fetch_progress WHERE query=?", (query,)).fetchone()
    if row and row["done"]:
        print(f"  [done] {query}  ({row['fetched']} papers)")
        return 0
    token = row["token"] if row else None
    fetched = row["fetched"] if row else 0

    pulled = 0
    while True:
        if max_per_query and fetched >= max_per_query:
            break
        params = {"query": query, "fields": FIELDS}
        if min_year:
            params["year"] = f"{min_year}-"
        if token:
            params["token"] = token
        data = api_get(params, api_key=api_key)
        total = data.get("total", 0)
        papers = data.get("data") or []
        token = data.get("token")

        for p in papers:
            if not p.get("paperId"):
                continue
            db.upsert_paper(conn, p, category_id, query)
        fetched += len(papers)
        pulled += len(papers)

        conn.execute(
            """INSERT INTO fetch_progress(query,category_id,token,fetched,total,done,updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(query) DO UPDATE SET token=excluded.token, fetched=excluded.fetched,
                   total=excluded.total, done=excluded.done, updated_at=excluded.updated_at""",
            (query, category_id, token, fetched, total,
             1 if (not token or not papers) else 0,
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
        conn.commit()
        print(f"  +{len(papers):>4}  total-so-far={fetched:<6} (matches={total}) :: {query}")

        if not token or not papers:
            conn.execute("UPDATE fetch_progress SET done=1 WHERE query=?", (query,))
            conn.commit()
            break
        time.sleep(delay)
    return pulled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", help="only collect this category id")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between requests (S2 allows ~1 req/sec)")
    ap.add_argument("--max-per-query", type=int, default=2000,
                    help="cap papers per query (0 = unlimited)")
    ap.add_argument("--reset", action="store_true", help="clear fetch progress first")
    ap.add_argument("--min-year", type=int, help="only papers from this year onward (focuses on active researchers)")
    args = ap.parse_args()

    import os
    api_key = os.environ.get("S2_API_KEY")

    conn = db.get_conn()
    db.init_db(conn)
    taxonomy = db.load_taxonomy()
    db.sync_categories(conn, taxonomy)

    if args.reset:
        conn.execute("DELETE FROM fetch_progress")
        conn.commit()
        print("fetch progress reset.")

    grand = 0
    for cat in taxonomy["categories"]:
        if args.category and cat["id"] != args.category:
            continue
        print(f"\n=== {cat['label']} ({cat['id']}) ===")
        for q in cat["queries"]:
            grand += collect_query(conn, cat["id"], q, args.delay,
                                   args.max_per_query or None, api_key, args.min_year)
    print(f"\nDone. Pulled {grand} paper-rows this run. DB: {db.DB_PATH}")
    conn.close()


if __name__ == "__main__":
    main()
