"""
Web Bridge HTTP API — Phase 3.

Mac #1 (LLM agent, internal network) calls this service on Mac #2 (internet-facing).
Mac #2 searches the web, scrapes pages, trims content, and returns a clean
text block the LLM can use directly as context.

Endpoints:
    POST /search   — search + scrape + trim → JSON + combined_text
    GET  /health   — liveness check (no auth required)

Auth: every /search request must include header  X-API-Key: <BRIDGE_API_KEY>

Binding:
    Dev / test  →  0.0.0.0:8000   (all interfaces)
    Production  →  192.168.100.2:8000  (Thunderbolt bridge interface only)

Start:
    uvicorn bridge_api:app --host 0.0.0.0 --port 8000
    uvicorn bridge_api:app --host 192.168.100.2 --port 8000   # production
"""

from __future__ import annotations

import os
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field

from pipeline import search_and_scrape, PipelineResult

# ── Config ────────────────────────────────────────────────────────────
# Set BRIDGE_API_KEY in .env (same file as TAVILY_API_KEY).
# Mac #1 must send this in every request header: X-API-Key: <value>
BRIDGE_API_KEY = os.getenv("BRIDGE_API_KEY", "")

_MAX_WORDS_PER_SOURCE = int(os.getenv("MAX_WORDS_PER_SOURCE", "400"))
_MAX_TOTAL_WORDS      = int(os.getenv("MAX_TOTAL_WORDS", "4000"))
_DEFAULT_RESULTS      = int(os.getenv("DEFAULT_RESULTS", "10"))

# ── Auth ──────────────────────────────────────────────────────────────
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def _require_key(key: str | None = Security(_api_key_header)) -> str:
    if not BRIDGE_API_KEY:
        raise HTTPException(503, "BRIDGE_API_KEY not configured on bridge server.")
    if key != BRIDGE_API_KEY:
        raise HTTPException(401, "Invalid or missing X-API-Key header.")
    return key

# ── Models ────────────────────────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    num_results: int = Field(default=_DEFAULT_RESULTS, ge=1, le=20)
    words_per_source: int = Field(default=_MAX_WORDS_PER_SOURCE, ge=50, le=2000)
    max_total_words: int = Field(default=_MAX_TOTAL_WORDS, ge=200, le=20000)

class SourceItem(BaseModel):
    rank: int
    title: str
    url: str
    domain: str
    word_count: int
    scrape_ok: bool

class SearchResponse(BaseModel):
    query: str
    backend: str
    sources_found: int
    sources_with_content: int
    total_words: int
    elapsed_s: float
    sources: list[SourceItem]
    combined_text: str          # ready to inject into LLM context


# ── Helpers ───────────────────────────────────────────────────────────
def _trim(result: PipelineResult,
          words_per_source: int,
          max_total_words: int) -> tuple[list[SourceItem], str]:
    """
    Trim each source to `words_per_source` words, then cut the combined
    text at `max_total_words` total — so Mac #1 never gets an oversized payload.
    """
    items: list[SourceItem] = []
    parts: list[str] = []
    total = 0

    for s in result.ok_sources:
        if total >= max_total_words:
            break

        words = s.best_text.split()
        trimmed = " ".join(words[:words_per_source])
        chunk_words = len(trimmed.split())

        # Hard-stop if adding this source would exceed total budget.
        if total + chunk_words > max_total_words:
            remaining = max_total_words - total
            trimmed = " ".join(words[:remaining])
            chunk_words = remaining

        header = f"=== [{s.rank}] {s.title or s.domain} ===\nURL: {s.url}\n"
        parts.append(header + trimmed)
        total += chunk_words

        items.append(SourceItem(
            rank=s.rank,
            title=s.title,
            url=s.url,
            domain=s.domain,
            word_count=chunk_words,
            scrape_ok=s.scrape_ok,
        ))

    return items, "\n\n".join(parts)


# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Web Bridge API",
    description="Search + scrape proxy for air-gapped LLM agent on Mac #1.",
    version="0.3.0",
)


@app.get("/health")
def health():
    """Liveness check — no auth required. Mac #1 can ping this to confirm the bridge is up."""
    return {"status": "ok", "bridge": "web-search-bridge v0.3"}


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest, _key: str = Security(_require_key)):
    """
    Search the web and return trimmed content ready for LLM context.

    Mac #1 calls:
        POST http://192.168.100.2:8000/search
        Headers: X-API-Key: <BRIDGE_API_KEY>
        Body:    {"query": "AI news today", "num_results": 10}

    Returns combined_text — inject this directly into the LLM prompt as context.
    """
    t0 = time.time()
    result = search_and_scrape(req.query, req.num_results)
    items, combined = _trim(result, req.words_per_source, req.max_total_words)

    return SearchResponse(
        query=req.query,
        backend=result.search_backend,
        sources_found=len(result.sources),
        sources_with_content=len(items),
        total_words=sum(i.word_count for i in items),
        elapsed_s=round(time.time() - t0, 2),
        sources=items,
        combined_text=combined,
    )
