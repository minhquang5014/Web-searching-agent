"""
Search + scrape CLI — Phase 2 combined pipeline.

Searches for a query, scrapes the top N pages in parallel, and prints
one large text block with the content from every source.

Usage:
    python run_pipeline.py "AI news today"
    python run_pipeline.py "apple M4 benchmark" --results 10
    python run_pipeline.py "query" --results 20 --workers 8
    python run_pipeline.py "query" --raw          # dump raw text only, no headers
"""

from __future__ import annotations

import argparse
import sys

from pipeline import search_and_scrape


def main() -> int:
    ap = argparse.ArgumentParser(description="Search + scrape pipeline.")
    ap.add_argument("query", help="search query")
    ap.add_argument("--results",  type=int, default=20,
                    help="number of sources to fetch (default: 20)")
    ap.add_argument("--workers",  type=int, default=5,
                    help="parallel scrape threads (default: 5)")
    ap.add_argument("--raw", action="store_true",
                    help="print raw combined text only — no stats header")
    args = ap.parse_args()

    if not args.raw:
        print(f"\nQuery   : {args.query!r}")
        print(f"Sources : up to {args.results}  |  Workers: {args.workers}")
        print("Searching and scraping …\n")

    result = search_and_scrape(args.query, args.results, args.workers)

    if not args.raw:
        # ── Stats header ──────────────────────────────────────────────
        ok      = [s for s in result.sources if s.scrape_ok]
        snippet = [s for s in result.sources if not s.scrape_ok and s.search_snippet]
        failed  = [s for s in result.sources if not s.best_text]

        print("=" * 64)
        print(f"  Search  : {result.search_backend}  ({result.search_elapsed_s:.1f}s)")
        print(f"  Scrape  : {result.scrape_elapsed_s:.1f}s parallel")
        print(f"  Total   : {result.total_elapsed_s:.1f}s")
        print(f"  Sources : {len(result.sources)} found")
        print(f"    ✅ Full text   : {len(ok)}")
        print(f"    📄 Snippet only: {len(snippet)}  (JS-heavy / blocked — using search snippet)")
        print(f"    ❌ No content  : {len(failed)}")
        print(f"  Words   : ~{result.total_words:,} total across all sources")
        print("=" * 64)

        if failed:
            print("\nFailed sources:")
            for s in failed:
                print(f"  [{s.rank}] {s.domain} — {s.scrape_error}")

        print("\n" + "─" * 64)
        print("  COMBINED TEXT")
        print("─" * 64 + "\n")

    print(result.combined_text())

    if not args.raw:
        print(f"\n{'─'*64}")
        print(f"  End of results — {result.total_words:,} words from "
              f"{len(result.ok_sources)} sources.")

    return 0 if result.ok_sources else 1


if __name__ == "__main__":
    sys.exit(main())
