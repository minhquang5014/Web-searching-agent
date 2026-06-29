"""
Test harness for the local search algorithm.

Runs a query at several target sizes (10 / 20 / 50) and reports, for each:
  - how many DISTINCT results actually came back
  - how many unique domains (= different sources)
  - which underlying engines contributed (SearXNG aggregates many)
  - how many pages were fetched and how long it took

Usage:
    python run_search_test.py
    python run_search_test.py "apple silicon m4 benchmark"
    python run_search_test.py "tin tức công nghệ" --counts 10 25 50
    SEARXNG_BASE_URL=http://10.x.x.x:8080 python run_search_test.py "query"

Make sure SearXNG is running first:  docker compose up -d
"""

from __future__ import annotations

import argparse
import sys

from search import search, SearchReport


def _bar(n: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return ""
    filled = round(width * n / total)
    return "█" * filled + "·" * (width - filled)


def print_report(report: SearchReport) -> None:
    print("\n" + "═" * 64)
    print(f"  Query     : {report.query!r}")
    print(f"  Backend   : {report.backend}")
    print(f"  Requested : {report.requested}   "
          f"Returned: {report.returned}   "
          f"Unique domains: {len(report.unique_domains)}")
    print(f"  Pages     : {report.pages_fetched}   "
          f"Time: {report.elapsed_s:.2f}s")
    if report.error:
        print(f"  ⚠️  {report.error}")
    print("─" * 64)

    if report.results:
        print("  Engine contribution:")
        for engine, count in report.engine_breakdown.items():
            print(f"    {engine:<18} {count:>3}  {_bar(count, report.returned)}")
        print("─" * 64)
        print("  Top results:")
        for i, r in enumerate(report.results[:10], 1):
            eng = ",".join(r.engines) if r.engines else "?"
            pageno = f"p{r.pageno} · " if r.pageno else ""
            print(f"   {i:>2}. {r.title[:60]}")
            print(f"       {r.domain}   [{pageno}{eng} · score={r.score:.1f}]")
            if r.content:
                print(f"       ↳ {r.content[:120]}…")
        if report.returned > 10:
            print(f"   … and {report.returned - 10} more")
    print("═" * 64)


def main() -> int:
    ap = argparse.ArgumentParser(description="Test the local SearXNG search algorithm.")
    ap.add_argument("query", nargs="?", default="latest AI news",
                    help="search query (default: 'latest AI news')")
    ap.add_argument("--counts", nargs="+", type=int, default=[10, 20, 50],
                    help="target result counts to test (default: 10 20 50)")
    args = ap.parse_args()

    print(f"\nTesting search for: {args.query!r}")
    print(f"Target counts: {args.counts}")

    summary: list[tuple[int, int, int, float]] = []
    for count in args.counts:
        report = search(args.query, num_results=count)
        print_report(report)
        summary.append((count, report.returned, len(report.unique_domains),
                        report.elapsed_s))

    # Final scoreboard — the answer to "can it return 10/20/50 sources?"
    print("\n" + "#" * 64)
    print("  SUMMARY — distinct sources retrieved per target")
    print("#" * 64)
    print(f"  {'requested':>10} {'returned':>10} {'domains':>10} {'time(s)':>10}")
    for req, ret, dom, t in summary:
        flag = "✅" if ret >= req else "⚠️ "
        print(f"  {req:>10} {ret:>10} {dom:>10} {t:>10.2f}  {flag}")
    print("#" * 64 + "\n")

    # Non-zero exit if the largest target couldn't be met (handy for CI).
    if summary and summary[-1][1] < summary[-1][0]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
