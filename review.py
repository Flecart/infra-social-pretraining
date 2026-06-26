"""Abstract-review workflow — this is where Claude reads abstracts and assigns
authoritative categories (overriding/confirming the provisional seed category).

Loop
----
  python3 review.py status                      # how much is left
  python3 review.py export --n 25 --out batch.json [--category X]
        -> writes a batch of pending papers (title + abstract + candidate cats)
  ... Claude reads batch.json and fills each paper's "assign" list ...
  python3 review.py import batch.json           # writes assignments back

Each paper in an exported batch looks like:
  {
    "paper_id": "...",
    "title": "...",
    "abstract": "...",
    "candidate_categories": ["ai-security"],
    "assign": [],          <- Claude fills: list of category ids (may be empty = none fit)
    "note": ""             <- optional free text
  }
A paper assigned [] is recorded as reviewed with no category (a true negative).

You can also assign directly without a file:
  python3 review.py set <paper_id> ai-security,ai-policy-governance --note "..."
"""

import argparse
import json
import sys
import time

import db


def valid_categories(conn):
    return {r["category_id"] for r in conn.execute("SELECT category_id FROM categories")}


def cmd_status(conn, args):
    total = conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"]
    reviewed = conn.execute("SELECT COUNT(*) c FROM review_status WHERE status='reviewed'").fetchone()["c"]
    pending = conn.execute("SELECT COUNT(*) c FROM review_status WHERE status='pending'").fetchone()["c"]
    print(f"papers: {total}   reviewed: {reviewed}   pending: {pending}")
    print("\nseed (candidate) hits per category:")
    for r in conn.execute(
        "SELECT category_id, COUNT(DISTINCT paper_id) n FROM seed_hits GROUP BY category_id ORDER BY n DESC"
    ):
        print(f"  {r['category_id']:<28} {r['n']}")
    print("\nreviewed assignments per category:")
    rows = list(conn.execute(
        "SELECT category_id, COUNT(*) n FROM paper_categories GROUP BY category_id ORDER BY n DESC"
    ))
    if not rows:
        print("  (none yet — run export, review, import)")
    for r in rows:
        print(f"  {r['category_id']:<28} {r['n']}")


def cmd_export(conn, args):
    where = "rs.status='pending'"
    params = []
    if args.category:
        where += " AND sh.category_id=?"
        params.append(args.category)
    # prefer papers with an abstract and higher citations first (more worth reviewing)
    sql = f"""
        SELECT p.paper_id, p.title, p.abstract, p.year, p.venue, p.citation_count,
               GROUP_CONCAT(DISTINCT sh.category_id) cands
        FROM papers p
        JOIN review_status rs ON rs.paper_id=p.paper_id
        JOIN seed_hits sh ON sh.paper_id=p.paper_id
        WHERE {where}
        GROUP BY p.paper_id
        ORDER BY (p.abstract IS NOT NULL) DESC, p.citation_count DESC
        LIMIT ?
    """
    params.append(args.n)
    batch = []
    for r in conn.execute(sql, params):
        batch.append({
            "paper_id": r["paper_id"],
            "title": r["title"],
            "year": r["year"],
            "venue": r["venue"],
            "citation_count": r["citation_count"],
            "abstract": r["abstract"],
            "candidate_categories": (r["cands"] or "").split(",") if r["cands"] else [],
            "assign": [],
            "note": "",
        })
    with open(args.out, "w") as f:
        json.dump({"papers": batch}, f, indent=2, ensure_ascii=False)
    print(f"wrote {len(batch)} papers -> {args.out}")
    if batch:
        print("Now read the file, fill each paper's \"assign\" list, then: "
              f"python3 review.py import {args.out}")


def _record(conn, paper_id, cats, note, valid):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for c in cats:
        if c not in valid:
            print(f"  !! unknown category '{c}' for {paper_id} — skipped", file=sys.stderr)
            continue
        conn.execute(
            """INSERT INTO paper_categories(paper_id,category_id,method,notes,reviewed_at)
               VALUES(?,?, 'claude', ?, ?)
               ON CONFLICT(paper_id,category_id) DO UPDATE SET notes=excluded.notes,
                   reviewed_at=excluded.reviewed_at""",
            (paper_id, c, note or None, now),
        )
    conn.execute(
        "INSERT INTO review_status(paper_id,status,reviewed_at) VALUES(?, 'reviewed', ?) "
        "ON CONFLICT(paper_id) DO UPDATE SET status='reviewed', reviewed_at=excluded.reviewed_at",
        (paper_id, now),
    )


def cmd_import(conn, args):
    valid = valid_categories(conn)
    with open(args.file) as f:
        data = json.load(f)
    papers = data["papers"] if isinstance(data, dict) else data
    n = 0
    for p in papers:
        _record(conn, p["paper_id"], p.get("assign") or [], p.get("note"), valid)
        n += 1
    conn.commit()
    print(f"imported {n} reviewed papers from {args.file}")


def cmd_set(conn, args):
    valid = valid_categories(conn)
    cats = [c.strip() for c in args.categories.split(",") if c.strip()]
    _record(conn, args.paper_id, cats, args.note, valid)
    conn.commit()
    print(f"set {args.paper_id} -> {cats}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    e = sub.add_parser("export")
    e.add_argument("--n", type=int, default=25)
    e.add_argument("--category")
    e.add_argument("--out", default="batch.json")

    i = sub.add_parser("import")
    i.add_argument("file")

    s = sub.add_parser("set")
    s.add_argument("paper_id")
    s.add_argument("categories", help="comma-separated category ids")
    s.add_argument("--note")

    args = ap.parse_args()
    conn = db.get_conn()
    db.init_db(conn)
    {"status": cmd_status, "export": cmd_export, "import": cmd_import, "set": cmd_set}[args.cmd](conn, args)
    conn.close()


if __name__ == "__main__":
    main()
