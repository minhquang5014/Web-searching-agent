"""
Search + scrape pipeline for the web-search bridge.

search_and_scrape(query, num_results=20) is the single entry point:
  1. Calls search() to get up to `num_results` URLs (SearXNG → Tavily → Brave → DDG).
  2. Scrapes each URL in parallel (ThreadPoolExecutor).
  3. Returns a PipelineResult whose .combined_text() gives one large text block
     ready to be handed to an LLM.

Tavily results already carry a content snippet — used as fallback text when
the full scrape of that page fails (e.g. JS-only, 403).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from scrape import scrape, ScrapeResult
from search import search, SearchReport, SearchResult

_DEFAULT_RESULTS = 20
_DEFAULT_WORKERS = 5      # parallel HTTP fetches — polite but fast


@dataclass
class SourceContent:
    rank: int
    title: str
    url: str
    domain: str
    search_snippet: str   # snippet already returned by the search engine
    scraped_text: str     # full text from scraper (empty if scrape failed)
    word_count: int
    scrape_ok: bool
    scrape_error: str = ""
    elapsed_s: float = 0.0

    @property
    def best_text(self) -> str:
        """Full scraped text if available, else fall back to search snippet."""
        return self.scraped_text if self.scraped_text else self.search_snippet


@dataclass
class PipelineResult:
    query: str
    search_backend: str
    sources: list[SourceContent]
    search_elapsed_s: float
    scrape_elapsed_s: float
    total_elapsed_s: float

    @property
    def ok_sources(self) -> list[SourceContent]:
        return [s for s in self.sources if s.best_text]

    @property
    def total_words(self) -> int:
        return sum(s.word_count for s in self.ok_sources)

    def combined_text(self, separator: str = "\n\n") -> str:
        """
        One large text block — each source prefixed with its rank, title, and URL.
        This is what gets sent to the LLM on Mac #1.
        """
        parts: list[str] = []
        for s in self.ok_sources:
            header = (
                f"=== [{s.rank}] {s.title or s.domain} ===\n"
                f"URL: {s.url}\n"
            )
            parts.append(header + s.best_text)
        return separator.join(parts)


def _scrape_one(rank: int, sr: SearchResult) -> SourceContent:
    result = scrape(sr.url)
    return SourceContent(
        rank=rank,
        title=sr.title or result.title,
        url=sr.url,
        domain=sr.domain,
        search_snippet=sr.content,
        scraped_text=result.text,
        word_count=result.word_count if result.ok else len((sr.content or "").split()),
        scrape_ok=result.ok,
        scrape_error=result.error,
        elapsed_s=result.elapsed_s,
    )


def search_and_scrape(
    query: str,
    num_results: int = _DEFAULT_RESULTS,
    max_workers: int = _DEFAULT_WORKERS,
) -> PipelineResult:
    """Search then scrape in parallel. Always returns a PipelineResult."""
    pipeline_start = time.time()

    # ── 1. Search ──────────────────────────────────────────────────────
    search_report: SearchReport = search(query, num_results)
    search_elapsed = search_report.elapsed_s

    # ── 2. Scrape in parallel ──────────────────────────────────────────
    scrape_start = time.time()
    sources: list[SourceContent] = [None] * len(search_report.results)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_scrape_one, i + 1, sr): i
            for i, sr in enumerate(search_report.results)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                sources[idx] = future.result()
            except Exception as e:
                sr = search_report.results[idx]
                sources[idx] = SourceContent(
                    rank=idx + 1,
                    title=sr.title,
                    url=sr.url,
                    domain=sr.domain,
                    search_snippet=sr.content,
                    scraped_text="",
                    word_count=0,
                    scrape_ok=False,
                    scrape_error=str(e),
                )

    scrape_elapsed = time.time() - scrape_start

    return PipelineResult(
        query=query,
        search_backend=search_report.backend,
        sources=[s for s in sources if s is not None],
        search_elapsed_s=search_elapsed,
        scrape_elapsed_s=scrape_elapsed,
        total_elapsed_s=time.time() - pipeline_start,
    )
