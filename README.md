# Researcher Map

Information software to see **who the players are** in CS subfields
(multi-agent interpretability, AI policy/governance, AI security, legal AI,
interpretability, AI safety/alignment — edit `taxonomy.json` to change).

It builds a local database by slowly querying Semantic Scholar, lets Claude read
abstracts and assign authoritative categories, and renders a static webapp that
maps researcher counts and ranks the top researchers per field.

## Pipeline

```
taxonomy.json ──collect.py──▶ data/researchers.db ──build_site.py──▶ webapp/data.js
                                     ▲
                                review.py  (Claude reads abstracts → categories)
```

### 1. Collect (slow, resumable)
```bash
python3 collect.py                      # all categories, ~1 req/sec (S2 limit)
python3 collect.py --category ai-security --delay 1
```
Stop with Ctrl-C anytime; re-run to resume. Set `S2_API_KEY` env var for higher
rate limits (optional). `--max-per-query` caps how many papers each query pulls
(default 2000).

### 2. Review abstracts (Claude assigns categories)
```bash
python3 review.py status
python3 review.py export --n 25 --out batch.json   # pending papers + abstracts
# Claude reads batch.json, fills each paper's "assign":[...] list
python3 review.py import batch.json
```
The seed query gives every paper a *candidate* category instantly; review turns
those into *reviewed* (authoritative) assignments. A paper assigned `[]` is a
reviewed true-negative.

### 3. Build & view
```bash
python3 build_site.py
# then open webapp/index.html in a browser (no server needed)
```
Toggle **Candidates** (instant, from seed queries) vs **Reviewed**
(Claude-curated). Click a field for its researcher leaderboard; search a name to
see every field they appear in.

## Notes
- Stdlib only (urllib + sqlite3); no `pip install` required.
- "Researchers in a subfield" = distinct authors of that field's papers.
  "Players" = authors ranked by #papers in the field, then total citations.
- Re-run `collect.py` periodically to refresh; re-run `build_site.py` to rebuild.
