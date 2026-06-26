"""Aggregate the DB into webapp/data.js (a single inlined JS file so the webapp
works from file:// with no server).

For every category we compute two views:
  - "reviewed"  : papers Claude has explicitly assigned to the category
  - "candidate" : papers a seed query surfaced (provisional, available instantly)
For each view: paper count, distinct-researcher count, and a leaderboard of the
top authors (ranked by #papers in the category, then total citations).

Usage: python3 build_site.py
"""

import json
import os
import time

import db

TOP_AUTHORS = 150
TOP_PAPERS = 120


def paper_ids_for(conn, category_id, view):
    if view == "reviewed":
        sql = "SELECT paper_id FROM paper_categories WHERE category_id=?"
    else:
        sql = "SELECT DISTINCT paper_id FROM seed_hits WHERE category_id=?"
    return [r["paper_id"] for r in conn.execute(sql, (category_id,))]


def summarize(conn, paper_ids):
    if not paper_ids:
        return {"paper_count": 0, "researcher_count": 0, "authors": [], "papers": []}
    qmarks = ",".join("?" * len(paper_ids))

    authors = {}
    for r in conn.execute(
        f"""SELECT a.author_id, a.name, p.citation_count, p.year
            FROM paper_authors pa
            JOIN authors a ON a.author_id=pa.author_id
            JOIN papers p ON p.paper_id=pa.paper_id
            WHERE pa.paper_id IN ({qmarks})""",
        paper_ids,
    ):
        d = authors.setdefault(r["author_id"], {
            "author_id": r["author_id"], "name": r["name"],
            "papers": 0, "citations": 0, "year_min": None, "year_max": None,
        })
        d["papers"] += 1
        d["citations"] += r["citation_count"] or 0
        y = r["year"]
        if y:
            d["year_min"] = y if d["year_min"] is None else min(d["year_min"], y)
            d["year_max"] = y if d["year_max"] is None else max(d["year_max"], y)

    ranked = sorted(authors.values(), key=lambda d: (d["papers"], d["citations"]), reverse=True)

    papers = []
    for r in conn.execute(
        f"""SELECT p.paper_id, p.title, p.year, p.venue, p.citation_count, p.url
            FROM papers p WHERE p.paper_id IN ({qmarks})
            ORDER BY p.citation_count DESC LIMIT {TOP_PAPERS}""",
        paper_ids,
    ):
        papers.append({
            "paper_id": r["paper_id"], "title": r["title"], "year": r["year"],
            "venue": r["venue"], "citations": r["citation_count"], "url": r["url"],
        })

    return {
        "paper_count": len(paper_ids),
        "researcher_count": len(authors),
        "authors": ranked[:TOP_AUTHORS],
        "papers": papers,
    }


def main():
    conn = db.get_conn()
    db.init_db(conn)
    taxonomy = db.load_taxonomy()

    cats = []
    for c in taxonomy["categories"]:
        reviewed = summarize(conn, paper_ids_for(conn, c["id"], "reviewed"))
        candidate = summarize(conn, paper_ids_for(conn, c["id"], "candidate"))
        cats.append({
            "id": c["id"], "label": c["label"], "description": c["description"],
            "reviewed": reviewed, "candidate": candidate,
        })

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "total_papers": conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"],
        "total_authors": conn.execute("SELECT COUNT(*) c FROM authors").fetchone()["c"],
        "total_reviewed": conn.execute(
            "SELECT COUNT(*) c FROM review_status WHERE status='reviewed'").fetchone()["c"],
        "categories": cats,
    }

    webdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")
    os.makedirs(webdir, exist_ok=True)
    with open(os.path.join(webdir, "data.js"), "w") as f:
        f.write("window.DATA = ")
        json.dump(out, f, ensure_ascii=False)
        f.write(";\n")
    print(f"wrote {webdir}/data.js  "
          f"({out['total_papers']} papers, {out['total_authors']} authors, "
          f"{out['total_reviewed']} reviewed)")
    conn.close()


if __name__ == "__main__":
    main()
