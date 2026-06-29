"""
Local web-search algorithm for the external Mac (#2) "web bridge".

Goal: given a query and a target number of results (10 / 20 / 50), return that
many DISTINCT sources by aggregating SearXNG across multiple engines and
paginating + de-duplicating by URL.

Backend priority (search only — no scraping here):
    1. SearXNG   — fully local, aggregates Google/Bing/DDG/Brave/… (preferred)
    2. Brave API — fallback if SEARXNG is down and BRAVE_SEARCH_API_KEY is set
    3. DuckDuckGo (ddgs) — last-resort fallback, may be rate-limited

Why pagination matters: one SearXNG JSON page returns only ~10–20 merged results.
To reliably reach 50 distinct sources we walk pages (pageno=1,2,3,…), collecting
unique URLs until we hit the target or run out of new results.

This module has no dependency on the parent agent — it is meant to be copied into
its own GitHub repo and run standalone on Mac #2.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

SEARXNG_BASE_URL = os.getenv("SEARXNG_BASE_URL", "http://localhost:8080")
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")

# Safety caps so a runaway target can't paginate forever.
_MAX_PAGES = 12
_PER_REQUEST_TIMEOUT = 12.0


@dataclass
class SearchResult:
    title: str
    url: str
    content: str = ""
    engines: list[str] = field(default_factory=list)   # which engines returned it
    score: float = 0.0
    pageno: int = 0                                     # which page it came from

    @property
    def domain(self) -> str:
        try:
            return urlparse(self.url).netloc.lower().removeprefix("www.")
        except Exception:
            return ""


@dataclass
class SearchReport:
    """Everything the test harness needs to judge how well the search did."""
    query: str
    backend: str
    requested: int
    results: list[SearchResult]
    pages_fetched: int = 0
    elapsed_s: float = 0.0
    error: str = ""

    @property
    def returned(self) -> int:
        return len(self.results)

    @property
    def unique_domains(self) -> list[str]:
        seen: list[str] = []
        for r in self.results:
            if r.domain and r.domain not in seen:
                seen.append(r.domain)
        return seen

    @property
    def engine_breakdown(self) -> dict[str, int]:
        """How many results each underlying engine contributed."""
        counts: dict[str, int] = {}
        for r in self.results:
            for e in (r.engines or ["unknown"]):
                counts[e] = counts.get(e, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


# ──────────────────────────────────────────────────────────────────────
# SearXNG (primary)
# ──────────────────────────────────────────────────────────────────────

def _searxng_page(query: str, pageno: int, base_url: str) -> list[dict] | None:
    """Fetch one SearXNG JSON page. Returns raw result dicts, or None on failure."""
    try:
        resp = requests.get(
            f"{base_url}/search",
            params={
                "q": query,
                "format": "json",
                "pageno": pageno,
                "categories": "general",
                "language": "en",
                "safesearch": 0,
            },
            headers={"User-Agent": "web-bridge-search/1.0"},
            timeout=_PER_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception:
        return None


def searxng_search(query: str, num_results: int,
                   base_url: str = SEARXNG_BASE_URL) -> SearchReport:
    """
    Paginate SearXNG until we collect `num_results` distinct URLs (or run out).
    """
    start = time.time()
    report = SearchReport(query=query, backend="SearXNG",
                          requested=num_results, results=[])

    # Probe page 1 first — distinguishes "SearXNG down" from "no results".
    first = _searxng_page(query, 1, base_url)
    if first is None:
        report.error = (
            f"SearXNG not reachable at {base_url}. "
            "Start it with `docker compose up -d` and retry."
        )
        report.elapsed_s = time.time() - start
        return report

    seen_urls: set[str] = set()
    pages = [(1, first)]

    pageno = 2
    while len(seen_urls) < num_results and pageno <= _MAX_PAGES:
        page = _searxng_page(query, pageno, base_url)
        if not page:
            break                      # no more results / engine exhausted
        # Stop early if a page yields nothing new (some engines repeat).
        before = len(seen_urls)
        for item in page:
            url = (item.get("url") or "").strip()
            if url:
                seen_urls.add(url)
        pages.append((pageno, page))
        if len(seen_urls) == before:
            break
        pageno += 1

    # Build de-duplicated, ordered result list across all fetched pages.
    collected: dict[str, SearchResult] = {}
    for pno, page in pages:
        for item in page:
            url = (item.get("url") or "").strip()
            if not url or url in collected:
                continue
            collected[url] = SearchResult(
                title=(item.get("title") or "").strip(),
                url=url,
                content=(item.get("content") or "").strip(),
                engines=item.get("engines") or ([item["engine"]] if item.get("engine") else []),
                score=float(item.get("score") or 0.0),
                pageno=pno,
            )
            if len(collected) >= num_results:
                break
        if len(collected) >= num_results:
            break

    report.results = list(collected.values())[:num_results]
    report.pages_fetched = len(pages)
    report.elapsed_s = time.time() - start
    return report


# ──────────────────────────────────────────────────────────────────────
# Brave API (fallback)
# ──────────────────────────────────────────────────────────────────────

def brave_search(query: str, num_results: int) -> SearchReport:
    start = time.time()
    report = SearchReport(query=query, backend="Brave Search",
                          requested=num_results, results=[])
    if not BRAVE_SEARCH_API_KEY:
        report.error = "BRAVE_SEARCH_API_KEY not set."
        return report
    try:
        # Brave caps `count` at 20 per call; paginate via `offset`.
        collected: dict[str, SearchResult] = {}
        offset = 0
        while len(collected) < num_results and offset < 10:
            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": min(20, num_results), "offset": offset},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
                },
                timeout=_PER_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            items = resp.json().get("web", {}).get("results", [])
            if not items:
                break
            for it in items:
                url = (it.get("url") or "").strip()
                if url and url not in collected:
                    collected[url] = SearchResult(
                        title=(it.get("title") or "").strip(),
                        url=url,
                        content=(it.get("description") or "").strip(),
                        engines=["brave-api"],
                    )
            offset += 1
        report.results = list(collected.values())[:num_results]
    except Exception as e:
        report.error = f"Brave API error: {e}"
    report.elapsed_s = time.time() - start
    return report


# ──────────────────────────────────────────────────────────────────────
# DuckDuckGo (last resort)
# ──────────────────────────────────────────────────────────────────────

def ddg_search(query: str, num_results: int) -> SearchReport:
    start = time.time()
    report = SearchReport(query=query, backend="DuckDuckGo",
                          requested=num_results, results=[])
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # older package name
        collected: dict[str, SearchResult] = {}
        with DDGS() as ddgs:
            for it in ddgs.text(query, max_results=num_results):
                url = (it.get("href") or it.get("link") or "").strip()
                if url and url not in collected:
                    collected[url] = SearchResult(
                        title=(it.get("title") or "").strip(),
                        url=url,
                        content=(it.get("body") or "").strip(),
                        engines=["duckduckgo"],
                    )
        report.results = list(collected.values())[:num_results]
    except Exception as e:
        report.error = f"DuckDuckGo error: {e}  (try: pip install ddgs)"
    report.elapsed_s = time.time() - start
    return report


# ──────────────────────────────────────────────────────────────────────
# Public entry point — SearXNG first, then fallbacks
# ──────────────────────────────────────────────────────────────────────

def search(query: str, num_results: int = 10,
           base_url: str = SEARXNG_BASE_URL) -> SearchReport:
    """Try SearXNG; fall back to Brave then DDG only if SearXNG is unreachable."""
    report = searxng_search(query, num_results, base_url)
    if report.results:
        return report
    # SearXNG returned nothing usable — try fallbacks, keep the first that works.
    for fallback in (brave_search, ddg_search):
        fb = fallback(query, num_results)
        if fb.results:
            fb.error = f"(SearXNG unavailable: {report.error}) "
            return fb
    return report  # nothing worked — return the SearXNG report with its error
