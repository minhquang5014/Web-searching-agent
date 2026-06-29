"""
Scrape test harness — Phase 2.

Searches for a query, then scrapes the top N result URLs and prints a
content excerpt + stats for each. Shows a final success/fail scoreboard.

Usage:
    python run_scrape_test.py
    python run_scrape_test.py "AI news today"
    python run_scrape_test.py "apple M4 benchmark" --top 5
    python run_scrape_test.py "query" --top 10 --excerpt 300
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from search import search
from scrape import scrape, ScrapeResult


def _status_icon(r: ScrapeResult) -> str:
    if r.ok:
        return "✅"
    if r.status_code in (403, 429):
        return "🚫"
    if "Timeout" in r.error:
        return "⏱️ "
    return "⚠️ "


def print_scrape_result(i: int, r: ScrapeResult, excerpt_chars: int) -> None:
    icon = _status_icon(r)
    print(f"\n{'─'*64}")
    print(f"  {icon} [{i}] {r.domain}  (HTTP {r.status_code or '—'}, {r.elapsed_s:.2f}s)")
    print(f"       {r.url[:80]}")
    if r.title:
        print(f"       Title : {r.title[:70]}")
    if r.error:
        print(f"       Error : {r.error}")
    if r.text:
        excerpt = r.text[:excerpt_chars].rstrip()
        wrapped = textwrap.fill(excerpt, width=60,
                                initial_indent="       ",
                                subsequent_indent="       ")
        print(f"       Words : {r.word_count}")
        print(f"       ──── excerpt ────")
        print(wrapped)
        if len(r.text) > excerpt_chars:
            print("       […]")


def main() -> int:
    ap = argparse.ArgumentParser(description="Search then scrape top results.")
    ap.add_argument("query", nargs="?", default="latest AI news")
    ap.add_argument("--top", type=int, default=5,
                    help="number of search results to scrape (default: 5)")
    ap.add_argument("--excerpt", type=int, default=400,
                    help="characters of body text to display (default: 400)")
    args = ap.parse_args()

    print(f"\nQuery   : {args.query!r}")
    print(f"Scraping top {args.top} results …")

    report = search(args.query, num_results=args.top)

    if not report.results:
        print(f"\n⚠️  Search returned no results. Error: {report.error}")
        return 1

    print(f"Search  : {report.returned} results in {report.elapsed_s:.2f}s "
          f"via {report.backend}")

    results: list[ScrapeResult] = []
    for i, sr in enumerate(report.results, 1):
        print(f"  scraping [{i}/{report.returned}] {sr.domain} …", end="\r", flush=True)
        scraped = scrape(sr.url)
        results.append(scraped)
        print_scrape_result(i, scraped, args.excerpt)

    # Final scoreboard.
    ok = [r for r in results if r.ok]
    blocked = [r for r in results if r.status_code in (403, 429)]
    timeout = [r for r in results if "Timeout" in r.error]
    other_err = [r for r in results if not r.ok and r not in blocked and r not in timeout]

    print(f"\n{'#'*64}")
    print(f"  SCRAPE SUMMARY  ({len(results)} URLs attempted)")
    print(f"{'#'*64}")
    print(f"  ✅ Success   : {len(ok)}")
    print(f"  🚫 Blocked   : {len(blocked)}")
    print(f"  ⏱️  Timeout   : {len(timeout)}")
    print(f"  ⚠️  Other err : {len(other_err)}")
    if ok:
        avg_words = sum(r.word_count for r in ok) / len(ok)
        print(f"  Avg words   : {avg_words:.0f}  (from pages that succeeded)")
    print(f"{'#'*64}\n")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
