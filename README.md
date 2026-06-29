# Web Search Bridge — local search test (SearXNG)

Standalone test for the **real-time web search** side of the dual-Mac setup.
This folder is self-contained — copy it into its own GitHub repo and run it on
**Mac #2** (the machine with internet access).

**Scope for now: search only.** No scraping, no API server, no LLM. We just want
to answer one question:

> Can a local SearXNG instance return **10 / 20 / 50 distinct sources** for a query?

Scraping, the `/search` HTTP endpoint, and the agent wiring come later (see the
PLAN section in the parent project's `README.md`).

---

## How it works

`search.py` queries SearXNG's JSON API and **paginates + de-duplicates by URL**.
A single SearXNG page only returns ~10–20 merged results, so to reach 50 distinct
sources the algorithm walks `pageno=1,2,3,…`, collecting unique URLs until it hits
the target or runs out of new results. Each result records which underlying engine
(Google, Bing, DDG, Brave, …) returned it, so you can see the source diversity.

Backend priority: **SearXNG** → Brave API → DuckDuckGo (fallbacks only kick in if
SearXNG is unreachable).

---

## Setup (on Mac #2)

```bash
# 1. Start SearXNG (needs Docker / OrbStack)
docker compose up -d                 # → http://localhost:8080

# 2. Python deps
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Verify SearXNG answers JSON
curl "http://localhost:8080/search?q=test&format=json" | head -c 200
```

---

## Run the test

```bash
# Default: query "latest AI news" at targets 10, 20, 50
python run_search_test.py

# Custom query
python run_search_test.py "apple silicon m4 benchmark"

# Custom targets
python run_search_test.py "tin tức công nghệ" --counts 10 25 50

# Point at a non-default SearXNG host
SEARXNG_BASE_URL=http://127.0.0.1:8080 python run_search_test.py "query"
```

### What you'll see

For each target it prints the backend, returned vs requested count, unique domains,
pages fetched, latency, and a per-engine contribution breakdown — then a final
scoreboard:

```
  SUMMARY — distinct sources retrieved per target
   requested   returned    domains    time(s)
          10         10         10       0.41  ✅
          20         20         19       0.83  ✅
          50         48         45       2.10  ⚠️
```

`⚠️` on the largest target means SearXNG ran out of fresh results before reaching
it — usually fixed by enabling more engines in `searxng/settings.yml`.

---

## Tuning for more sources

If 50 is hard to reach, edit [`searxng/settings.yml`](searxng/settings.yml):
- enable more engines (already broadened: google, bing, ddg, brave, startpage,
  qwant, mojeek, wikipedia)
- some engines get rate-limited from a datacenter IP — a residential/office line
  on Mac #2 usually does better
- restart after changes: `docker compose restart`

---

## Files

| File | Purpose |
|---|---|
| `search.py` | Search algorithm: SearXNG pagination + dedup, fallbacks, structured results |
| `run_search_test.py` | CLI harness — tests 10/20/50 targets, prints diversity report |
| `docker-compose.yml` | Brings up SearXNG locally |
| `searxng/settings.yml` | SearXNG config (JSON API on, limiter off, broad engine set) |
| `requirements.txt` | Just `requests` (ddgs optional) |

---

## Next steps (later, not now)

1. Wrap `search()` in a small FastAPI `/search` endpoint with API-key auth.
2. Add a `/scrape` endpoint (BeautifulSoup, later Playwright).
3. On Mac #1, route the agent's `web_search` / `fetch_url` tools to this bridge.
