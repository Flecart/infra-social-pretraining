"""SQLite layer for the researcher-map project.

Tables
------
papers          : one row per Semantic Scholar paper we have pulled
authors         : one row per distinct author
paper_authors   : author <-> paper join (with author order)
seed_hits       : provenance — which taxonomy query/category surfaced a paper
                  (this is the *candidate* category, available immediately)
paper_categories: authoritative category assignments from Claude's abstract review
review_status   : per-paper review state (pending / reviewed / skipped)
fetch_progress  : per-query resumable collection progress
categories      : copy of taxonomy categories for reference / labels
"""

import json
import os
import sqlite3
import time

DB_PATH = os.environ.get(
    "S2MAP_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "researchers.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    paper_id                TEXT PRIMARY KEY,
    title                   TEXT,
    abstract                TEXT,
    year                    INTEGER,
    venue                   TEXT,
    url                     TEXT,
    citation_count          INTEGER,
    influential_citation_count INTEGER,
    fields_of_study         TEXT,   -- json array
    publication_types       TEXT,   -- json array
    fetched_at              TEXT
);

CREATE TABLE IF NOT EXISTS authors (
    author_id   TEXT PRIMARY KEY,
    name        TEXT
);

CREATE TABLE IF NOT EXISTS paper_authors (
    paper_id    TEXT NOT NULL,
    author_id   TEXT NOT NULL,
    position    INTEGER,
    PRIMARY KEY (paper_id, author_id)
);

CREATE TABLE IF NOT EXISTS seed_hits (
    paper_id    TEXT NOT NULL,
    category_id TEXT NOT NULL,
    query       TEXT NOT NULL,
    PRIMARY KEY (paper_id, category_id, query)
);

CREATE TABLE IF NOT EXISTS paper_categories (
    paper_id    TEXT NOT NULL,
    category_id TEXT NOT NULL,
    method      TEXT DEFAULT 'claude',
    confidence  REAL,
    notes       TEXT,
    reviewed_at TEXT,
    PRIMARY KEY (paper_id, category_id)
);

CREATE TABLE IF NOT EXISTS review_status (
    paper_id    TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | reviewed | skipped
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS fetch_progress (
    query       TEXT PRIMARY KEY,
    category_id TEXT,
    token       TEXT,
    fetched     INTEGER DEFAULT 0,
    total       INTEGER,
    done        INTEGER DEFAULT 0,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS categories (
    category_id TEXT PRIMARY KEY,
    label       TEXT,
    description TEXT
);

CREATE INDEX IF NOT EXISTS idx_pa_author ON paper_authors(author_id);
CREATE INDEX IF NOT EXISTS idx_pa_paper  ON paper_authors(paper_id);
CREATE INDEX IF NOT EXISTS idx_seed_cat  ON seed_hits(category_id);
CREATE INDEX IF NOT EXISTS idx_pc_cat    ON paper_categories(category_id);
"""


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(conn=None):
    own = conn is None
    conn = conn or get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    if own:
        conn.close()


def load_taxonomy(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "taxonomy.json")
    with open(path) as f:
        return json.load(f)


def sync_categories(conn, taxonomy):
    for c in taxonomy["categories"]:
        conn.execute(
            "INSERT INTO categories(category_id,label,description) VALUES(?,?,?) "
            "ON CONFLICT(category_id) DO UPDATE SET label=excluded.label, description=excluded.description",
            (c["id"], c["label"], c["description"]),
        )
    conn.commit()


def upsert_paper(conn, p, category_id, query):
    """Insert/refresh a paper, its authors, and record the seed provenance."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        """INSERT INTO papers(paper_id,title,abstract,year,venue,url,citation_count,
                              influential_citation_count,fields_of_study,publication_types,fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(paper_id) DO UPDATE SET
               title=excluded.title, abstract=excluded.abstract, year=excluded.year,
               venue=excluded.venue, url=excluded.url, citation_count=excluded.citation_count,
               influential_citation_count=excluded.influential_citation_count,
               fields_of_study=excluded.fields_of_study,
               publication_types=excluded.publication_types, fetched_at=excluded.fetched_at""",
        (
            p["paperId"],
            p.get("title"),
            p.get("abstract"),
            p.get("year"),
            (p.get("venue") or None),
            p.get("url"),
            p.get("citationCount"),
            p.get("influentialCitationCount"),
            json.dumps(p.get("fieldsOfStudy") or []),
            json.dumps(p.get("publicationTypes") or []),
            now,
        ),
    )

    for i, a in enumerate(p.get("authors") or []):
        aid = a.get("authorId")
        if not aid:
            continue
        conn.execute(
            "INSERT INTO authors(author_id,name) VALUES(?,?) "
            "ON CONFLICT(author_id) DO UPDATE SET name=excluded.name",
            (aid, a.get("name")),
        )
        conn.execute(
            "INSERT OR IGNORE INTO paper_authors(paper_id,author_id,position) VALUES(?,?,?)",
            (p["paperId"], aid, i),
        )

    conn.execute(
        "INSERT OR IGNORE INTO seed_hits(paper_id,category_id,query) VALUES(?,?,?)",
        (p["paperId"], category_id, query),
    )
    conn.execute(
        "INSERT OR IGNORE INTO review_status(paper_id,status) VALUES(?, 'pending')",
        (p["paperId"],),
    )
