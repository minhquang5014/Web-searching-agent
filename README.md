# Web Search Bridge

A small, self-contained service that gives an **air-gapped LLM agent access to
real-time web search** — by running on a second machine that *does* have internet.

> **This repo is Phase 1: a search-only test.** Right now it only answers one
> question — *can a local SearXNG instance return 10 / 20 / 50 distinct sources
> for a query?* Once that's proven, we grow it into the real bridge service
> (scraping, an authenticated HTTP API, and wiring into the agent). See
> [Roadmap](#roadmap).

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

## Phase 1 scope (this repo, right now)

**Search only.** No scraping, no API server, no LLM, no agent wiring yet. We just
prove the search backend can deliver enough *distinct* sources, fast, fully locally.

> Can a local SearXNG instance return **10 / 20 / 50 distinct sources** for a query?

### How it works

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

## Tuning for more sources

If 50 is hard to reach, edit [`searxng/settings.yml`](searxng/settings.yml):
- enable more engines (already broadened: google, bing, ddg, brave, startpage,
  qwant, mojeek, wikipedia)
- some engines get rate-limited from a datacenter IP — a residential/office line on
  Mac #2 usually does better
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

## Roadmap

Testing is the first phase. Once search quality is proven, we build the real system:

- [x] **Phase 1 — Search test** (this repo). Prove SearXNG returns 10/20/50 distinct
      sources locally, measure latency and engine diversity.
- [ ] **Phase 2 — Scraping.** Add page-content extraction (BeautifulSoup; later
      Playwright for JS-heavy sites).
- [ ] **Phase 3 — HTTP API.** Wrap `search()` and scrape in a small FastAPI service
      (`/search`, `/scrape`) with **API-key auth**, bound to the private bridge
      interface only.
- [ ] **Phase 4 — Agent wiring.** On Mac #1, route the agent's `web_search` /
      `fetch_url` tools to this bridge over the Thunderbolt link.

### Security rules for later phases (must hold)

1. **One-way only:** Mac #1 → Mac #2. Mac #2 never initiates connections to `10.52`.
2. **API key** on every request, checked by the bridge server.
3. **Firewall on Mac #2:** only accept connections from `192.168.100.1` on the API port.
4. Bridge server binds to the private bridge interface, **not** the internet-facing side.
