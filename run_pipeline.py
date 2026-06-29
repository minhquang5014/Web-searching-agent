"""
Search + scrape CLI — Phase 2 combined pipeline.

Searches for a query, scrapes the top N pages in parallel, and prints
one large text block with the content from every source.

Usage:
    python run_pipeline.py "AI news today"
    python run_pipeline.py "apple M4 benchmark" --results 10
    python run_pipeline.py "query" --results 20 --workers 8
    python run_pipeline.py "query" --raw          # dump raw text only, no headers
    python run_pipeline.py "query" --rag          # filter to query-relevant chunks (Phase 3.5)
    python run_pipeline.py "query" --rag --budget 2000
"""

from __future__ import annotations

import argparse
import sys

# Windows consoles default to cp1252, which can't encode the ✅/📄/❌ glyphs below.
# Force UTF-8 so the report prints on every platform.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
    ap.add_argument("--rag", action="store_true",
                    help="filter scraped text to query-relevant chunks (Phase 3.5)")
    ap.add_argument("--budget", type=int, default=4000,
                    help="--rag: max words to keep after filtering (default: 4000)")
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

        label = "RAG-FILTERED TEXT (query-relevant chunks)" if args.rag else "COMBINED TEXT"
        print("\n" + "─" * 64)
        print(f"  {label}")
        print("─" * 64 + "\n")

    if args.rag:
        # Lazy import: only load the embedding model (torch) when --rag is used.
        from retrieval import select_relevant, combined_text
        chunks = select_relevant(args.query, result.ok_sources,
                                 max_total_words=args.budget)
        print(combined_text(chunks))
        if not args.raw:
            kept = sum(c.word_count for c in chunks)
            print(f"\n{'─'*64}")
            print(f"  RAG kept {len(chunks)} chunks / {kept:,} words "
                  f"(from ~{result.total_words:,} scraped) "
                  f"across {len({c.url for c in chunks})} sources.")
        return 0 if chunks else 1

    print(result.combined_text())

    if not args.raw:
        print(f"\n{'─'*64}")
        print(f"  End of results — {result.total_words:,} words from "
              f"{len(result.ok_sources)} sources.")

    return 0 if result.ok_sources else 1


if __name__ == "__main__":
    sys.exit(main())
