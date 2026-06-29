# Web Search Bridge

A small, self-contained service that gives an **air-gapped LLM agent access to
real-time web search** — by running on a second machine that *does* have internet.

> **Phases 1–3 are done.** The repo now has the proven search backend (Phase 1),
> a content scraper (Phase 2), and an **authenticated FastAPI bridge** that Mac #1
> calls to get search-and-scrape results ready for LLM context (Phase 3). The
> remaining work is **Phase 4** — wiring the agent's tools on Mac #1 to this bridge
> over the Thunderbolt link. See [Roadmap](#roadmap).

---

## Background — why this exists

There is a separate project, **Local LLM Agent**: a ReAct agent (Qwen3-9B, mlx-lm
on Apple Silicon) built for manufacturing engineers. It does SFIS database queries,
persistent vector memory, file tools, and web search.

The catch: that agent runs on a machine **inside the corporate network
(`10.52.x.x`)**, and that network **cannot reach the public internet**. So anything
internal (SFIS, files, memory) works great, but `web_search` / `fetch_url` — anything
needing the open web — fails. The engineers still want to ask the agent real-time
questions ("latest spec for component X", "recall notice for vendor Y", etc.).

**The solution: two Mac minis.**

```
   Internet  ──WiFi/Ethernet──►  Mac #2  (this repo — the "web bridge")
                                   │
                                   │  Thunderbolt Bridge (direct USB-C cable)
                                   │  private link 192.168.100.0/24, one-way
                                   ▼
   Corporate 10.52  ──Ethernet──►  Mac #1  (Agent 9B + Web UI + SFIS)
                                   ▲
                                   │
                            Company users (browser) → http://10.52.x.x:8088
```

- **Mac #1** (internal): runs the agent + web UI. Users talk to it. It has SFIS and
  the corporate LAN, but no internet.
- **Mac #2** (this repo): on the outside internet. It does the web search/scrape and
  hands results back to Mac #1. It has **no route into `10.52`** — the link between
  the two Macs is a private, isolated Thunderbolt connection (so a compromise of the
  internet-facing Mac can't reach the corporate network).

Both machines are Mac mini M4 24 GB. Mac #2 doesn't even need to run an LLM — it's a
dumb, fast search/scrape proxy.

### Networking note (for when we connect the two Macs)

The Mac mini M4 has only **one** built-in Ethernet port, so we don't put both on a
switch. Instead use **Thunderbolt Bridge**: connect the two Macs directly with a
USB-C / Thunderbolt cable, macOS auto-creates a `Thunderbolt Bridge` interface, and
we assign static IPs (Mac #1 = `192.168.100.1`, Mac #2 = `192.168.100.2`). Mac #1
then calls `http://192.168.100.2:<port>`. This keeps the internet path and the
corporate LAN physically separate. *(Not needed for Phase 1 — the search test runs
entirely on Mac #2.)*

---

## What's built (Phases 1–3)

- **Phase 1 — Search.** Prove a local SearXNG instance can deliver enough *distinct*
  sources, fast, fully locally:

  > Can a local SearXNG instance return **10 / 20 / 50 distinct sources** for a query?

- **Phase 2 — Scrape.** `scrape.py` fetches each result URL and extracts clean
  readable text (BeautifulSoup, boilerplate stripped), with the search snippet as
  fallback when a page is JS-only or blocks scrapers.
- **Phase 3 — Bridge API.** `bridge_api.py` is a FastAPI service exposing
  `POST /search` (search → scrape → trim → `combined_text` ready for LLM context)
  and `GET /health`, guarded by an `X-API-Key` header. `pipeline.py` ties search and
  parallel scraping together.

### How the search test works

`search.py` queries SearXNG's JSON API and **paginates + de-duplicates by URL**.
A single SearXNG page returns only ~10–20 merged results, so to reach 50 distinct
sources the algorithm walks `pageno=1,2,3,…`, collecting unique URLs until it hits
the target or runs out of new results. Each result records which underlying engine
(Google, Bing, DDG, Brave, …) returned it, so you can see source diversity.

Backend priority: **SearXNG** (local, no quotas) → Brave API → DuckDuckGo. The
fallbacks only kick in if SearXNG is unreachable.

---

## Setup (on Mac #2)

### Option A — Offline (OrbStack .dmg + searxng-image.tar already on the Mac)

Use this when the Mac has no/limited internet and you copied the assets over (USB, etc.).

```bash
# 1. Install OrbStack from the .dmg (double-click → drag to Applications),
#    launch it once so the Docker engine starts.

# 2. Load the pre-saved SearXNG image (no internet needed)
docker load -i /path/to/searxng-image.tar
docker images | grep searxng            # confirm the image is present

# 3. Start SearXNG from this folder's compose file
docker compose up -d                    # → http://localhost:8080

# 4. Python deps (requests only)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 5. Verify SearXNG answers JSON
curl "http://localhost:8080/search?q=test&format=json" | head -c 200
```

> To (re)create `searxng-image.tar` on a machine WITH internet:
> ```bash
> docker pull ghcr.io/searxng/searxng:latest
> docker save ghcr.io/searxng/searxng:latest -o searxng-image.tar
> ```

### Option B — Online (pull the image directly)

```bash
docker compose up -d                    # pulls ghcr.io/searxng/searxng:latest → :8080
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
curl "http://localhost:8080/search?q=test&format=json" | head -c 200
```

> If `curl` returns `403 Forbidden`, wait ~10s and retry — the container may still be
> starting. The limiter is already disabled in `searxng/settings.yml`.

### Windows note (testing on a PC)

The production target is a Mac mini, but everything runs the same on Windows with
**Docker Desktop** — the `docker-compose.yml` is OS-agnostic. Two differences:

```powershell
# Activate the venv the Windows way
python -m venv .venv ; .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Start SearXNG (Docker Desktop must be running)
docker compose up -d                    # → http://localhost:8080

# Verify (PowerShell — curl is an alias for Invoke-WebRequest)
curl.exe "http://localhost:8080/search?q=test&format=json"
```

If the `./searxng` bind-mount shows as empty in the container, enable file sharing
for the drive in **Docker Desktop → Settings → Resources → File Sharing**.

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

For each target it prints backend, returned vs requested count, unique domains, pages
fetched, latency, and a per-engine contribution breakdown — then a final scoreboard:

```
  SUMMARY — distinct sources retrieved per target
   requested   returned    domains    time(s)
          10         10         10       0.41  ✅
          20         20         19       0.83  ✅
          50         48         45       2.10  ⚠️
```

`⚠️` on the largest target means SearXNG ran out of fresh results before reaching it
— usually fixed by enabling more engines in `searxng/settings.yml`.

---

## Run the scrape + pipeline (Phase 2)

```bash
python run_scrape_test.py            # scrape a sample set of URLs, report quality
python run_pipeline.py "AI news"     # full search → parallel-scrape → combined text
```

## Run the bridge API (Phase 3)

```bash
# Set the shared secret Mac #1 will send on every request
export BRIDGE_API_KEY=choose-a-long-random-string      # PowerShell: $env:BRIDGE_API_KEY="..."

# Dev / test — listen on all interfaces
uvicorn bridge_api:app --host 0.0.0.0 --port 8000

# Production on Mac #2 — bind to the Thunderbolt bridge interface only
uvicorn bridge_api:app --host 192.168.100.2 --port 8000
```

Call it the way Mac #1 will:

```bash
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/search \
  -H "X-API-Key: $BRIDGE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "latest AI news", "num_results": 10}'
```

The response includes `combined_text` — trimmed, source-attributed content ready to
drop straight into the LLM prompt on Mac #1.

---

## Tuning for more sources

If 50 is hard to reach, edit [`searxng/settings.yml`](searxng/settings.yml):
- enable more engines (already broadened: google, bing, ddg, brave, startpage,
  qwant, mojeek, wikipedia)
- some engines get rate-limited from a datacenter IP — a residential/office line on
  Mac #2 usually does better
- restart after changes: `docker compose restart`

---

## Phase 3.5 — Relevance filtering (RAG retrieval)

> **Status:** implemented in [`retrieval.py`](retrieval.py) and wired into
> [`run_pipeline.py`](run_pipeline.py) via `--rag` for testing. Wiring it into the
> bridge API (`POST /search`) is the remaining step.

**The problem.** Today the bridge ([`bridge_api.py`](bridge_api.py) `_trim()`) keeps the
**first N words** of each scraped page. But the fact the agent needs is often buried
mid-article, and the first words are usually nav/intro cruft. We waste the token
budget on irrelevant text.

**The fix.** Filter the scraped text against the *user's question* before sending it
to Qwen-9B — classic retrieval (the "R" in RAG), done in-memory per request:

1. **Chunk** each page into ~220-word passages (with ~40-word overlap).
2. **Embed** the query and every chunk with a small multilingual embedding model.
3. **Score** each chunk by cosine similarity to the query (a dot product — no vector DB).
4. **Select** the top-k chunks across *all* sources up to the word budget, return them
   verbatim with `[rank] title / URL` attribution.

This keeps Mac #2 a **dumb, fast proxy** — an embedding model is not a generative LLM,
so the architecture rule ("Mac #2 doesn't run an LLM") still holds. It also keeps the
text **verbatim** (no summary-model hallucination), so the agent can cite exact figures.

### Model choice (downloaded from HuggingFace, runs on the M4)

| Model | Download | RAM | Multilingual (Vietnamese)? |
|---|---|---|---|
| **`intfloat/multilingual-e5-small`** ✅ | **~470 MB** | ~1 GB | ✅ yes — recommended |
| `intfloat/multilingual-e5-base` | ~1.1 GB | ~2 GB | ✅ yes — higher quality |
| `sentence-transformers/all-MiniLM-L6-v2` | ~90 MB | <0.5 GB | ❌ English only |

`multilingual-e5-small` is the pick: small, supports both English and Vietnamese
queries, embeds ~20 pages in well under a second on CPU (MPS-accelerated via torch on
Apple Silicon). Downloaded once, then cached locally — no internet needed at runtime.

> **e5 gotcha:** the model **requires** prefixes — encode queries as `"query: …"` and
> chunks as `"passage: …"`. Omitting them measurably hurts relevance.

### Install

```bash
pip install sentence-transformers            # reliable, MPS on M4 (pulls torch)
# or, lighter deps (ONNX, no torch):  pip install fastembed
```

The model lives in a local folder, resolved in this order (so it works the same on
the Windows test PC and the Mac mini):

1. `$RAG_MODEL_PATH`  *(explicit override — set this in production)*
2. `./models/multilingual-e5-small`  *(repo-local, portable)*
3. `~/models/multilingual-e5-small`  *(Mac mini default)*
4. `{D,E,F}:/models/multilingual-e5-small`  *(Windows USB default)*
5. `intfloat/multilingual-e5-small`  *(last resort: download from HuggingFace)*

### Try it

```bash
# Filter live search results to the chunks relevant to the query
python run_pipeline.py "Apple M4 Max GPU benchmark score" --rag --budget 1500

# Filter a local text/log file against a query (chunks by lines)
python run_rag_test.py "I2C bus timeout station 7" --file fail.log --lines-per-chunk 5
```

### When to use it — relevance, not just "make it shorter"

RAG is a **relevance filter**, not a summarizer. The real knob is `--rag` **plus the
word budget** (`--budget` here, `max_total_words` in the API) — *not* the cosmetic
`--raw` flag, which only toggles the stats header.

| Query type | Setting | Why |
|---|---|---|
| **Specific / factual** ("benchmark score", "recall notice for vendor Y") | `--rag` + small budget (1000–2000) | RAG is strongest here — filters straight to the passage holding the answer. Best for concise synthesis. |
| **Broad / exploratory** ("everything about X", "this week's AI news") | `--rag` + large budget (4000–6000), or no RAG | Over-filtering drops relevant-but-diverse info; breadth matters more than a single focus. |

Even for **detailed** answers, keep RAG **on** — it strips nav/footer/"about author"
cruft regardless. So in production, RAG is essentially always on; **`max_total_words`
controls detailed vs. concise**, not an on/off switch:

```jsonc
{ "query": "...", "rag": true, "max_total_words": 1500 }   // concise
{ "query": "...", "rag": true, "max_total_words": 5000 }   // detailed
```

**Caveat for structured logs.** On natural-language web text, relevance scores separate
cleanly (~0.90+). On a structured fail log they bunch up (~0.83–0.88) and exact tokens
(station IDs, error codes) are better matched by `grep`. For a 120k-line log, prefilter
with keyword/regex first, then embed the survivors (hybrid) — don't embed everything.

### Note: do summarization on Mac #1, not here

If even the top relevant chunks overflow Qwen's context, summarize **on Mac #1 with
Qwen itself** — don't add a second generative model to the bridge. Mac #2 only retrieves.

---

## Files

| File | Purpose |
|---|---|
| `search.py` | Search algorithm: SearXNG pagination + dedup, fallbacks (Tavily/Brave/DDG), structured results |
| `scrape.py` | Page-content scraper: fetch + boilerplate-strip → clean text (Phase 2) |
| `pipeline.py` | `search_and_scrape()` — search then parallel-scrape, returns LLM-ready combined text |
| `bridge_api.py` | FastAPI bridge: `POST /search` + `GET /health`, `X-API-Key` auth (Phase 3) |
| `retrieval.py` | Query-aware chunk selection: embed + rank, keep relevant passages (Phase 3.5) |
| `run_search_test.py` | CLI harness — tests 10/20/50 targets, prints diversity report |
| `run_scrape_test.py` | CLI harness — scrapes a set of URLs and reports extraction quality |
| `run_pipeline.py` | CLI harness — full search→scrape pipeline; `--rag` to relevance-filter |
| `run_rag_test.py` | CLI harness — RAG filter over live search or a local text/log file |
| `docker-compose.yml` | Brings up SearXNG locally |
| `searxng/settings.yml` | SearXNG config (JSON API on, limiter off, broad engine set) |
| `requirements.txt` | `requests`, `beautifulsoup4`, `lxml`, `fastapi`, `uvicorn` (ddgs optional) |

---

## Roadmap

Testing is the first phase. Once search quality is proven, we build the real system:

- [x] **Phase 1 — Search test.** Prove SearXNG returns 10/20/50 distinct
      sources locally, measure latency and engine diversity. (`search.py`,
      `run_search_test.py`)
- [x] **Phase 2 — Scraping.** Page-content extraction (BeautifulSoup, boilerplate
      stripping; Playwright for JS-heavy sites is still a later option). (`scrape.py`,
      `pipeline.py`)
- [x] **Phase 3 — HTTP API.** `search()` + scrape wrapped in a FastAPI service
      (`POST /search`, `GET /health`) with **API-key auth** via `X-API-Key`, bindable
      to the private bridge interface only. (`bridge_api.py`)
- [ ] **Phase 3.5 — Relevance filtering (RAG retrieval).** Replace the naive
      first-N-words trim with query-aware chunk selection: chunk each page, embed with
      a small multilingual model, keep the top-k most relevant passages. See
      [Phase 3.5](#phase-35--relevance-filtering-rag-retrieval-planned).
- [ ] **Phase 4 — Agent wiring.** On Mac #1, route the agent's `web_search` /
      `fetch_url` tools to this bridge over the Thunderbolt link.

### Security rules for later phases (must hold)

1. **One-way only:** Mac #1 → Mac #2. Mac #2 never initiates connections to `10.52`.
2. **API key** on every request, checked by the bridge server.
3. **Firewall on Mac #2:** only accept connections from `192.168.100.1` on the API port.
4. Bridge server binds to the private bridge interface, **not** the internet-facing side.
