"""
RAG retrieval test harness — Phase 3.5.

Two modes:

  1. Live web mode — search + scrape, then keep only the chunks relevant to the query:
        python run_rag_test.py "latest apple m4 benchmark"
        python run_rag_test.py "query" --results 15 --budget 3000

  2. File mode — filter a local text/log file against the query (e.g. an industrial
     fail log). Chunks the file by lines, scores each block, prints the most relevant:
        python run_rag_test.py "power supply overcurrent" --file fail.log
        python run_rag_test.py "I2C timeout station 7" --file fail.log --lines-per-chunk 8 --top 30

Run `docker compose up -d` first if you use live web mode.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

# Windows consoles default to cp1252, which can't encode the glyphs below.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from retrieval import select_relevant, combined_text


@dataclass
class _TextSource:
    """Minimal duck-typed source for file mode (matches what select_relevant reads)."""
    rank: int
    title: str
    url: str
    domain: str
    best_text: str


def _sources_from_file(path: str, lines_per_chunk: int) -> list[_TextSource]:
    """Split a text file into blocks of `lines_per_chunk` lines → pseudo-sources."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = [ln.rstrip("\n") for ln in f]
    blocks = []
    for i in range(0, len(lines), lines_per_chunk):
        block = "\n".join(lines[i:i + lines_per_chunk]).strip()
        if block:
            blocks.append(_TextSource(
                rank=i + 1,                       # first line number of the block
                title=f"lines {i + 1}-{i + lines_per_chunk}",
                url=path,
                domain=path,
                best_text=block,
            ))
    return blocks, len(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG relevance-filter test.")
    ap.add_argument("query", help="the question / what you're looking for")
    ap.add_argument("--file", help="filter this local text/log file instead of the web")
    ap.add_argument("--results", type=int, default=10, help="web mode: sources to fetch")
    ap.add_argument("--budget", type=int, default=4000, help="max words to keep")
    ap.add_argument("--top", type=int, default=None, help="cap on number of chunks kept")
    ap.add_argument("--lines-per-chunk", type=int, default=10,
                    help="file mode: lines per block (default 10)")
    args = ap.parse_args()

    # ── Build the source list ──────────────────────────────────────────
    if args.file:
        print(f"\nReading {args.file} …")
        sources, n_lines = _sources_from_file(args.file, args.lines_per_chunk)
        print(f"  {n_lines:,} lines → {len(sources):,} blocks "
              f"({args.lines_per_chunk} lines each)")
    else:
        from pipeline import search_and_scrape
        print(f"\nSearching + scraping for {args.query!r} …")
        result = search_and_scrape(args.query, args.results)
        sources = result.ok_sources
        print(f"  backend={result.search_backend}  sources={len(sources)}  "
              f"raw_words=~{result.total_words:,}")

    if not sources:
        print("No content to filter.")
        return 1

    # ── Filter by relevance ────────────────────────────────────────────
    print(f"Embedding + ranking against the query …  (budget {args.budget:,} words)\n")
    chunks = select_relevant(args.query, sources,
                             max_total_words=args.budget, max_chunks=args.top)

    kept_words = sum(c.word_count for c in chunks)
    print("=" * 64)
    print(f"  Query : {args.query!r}")
    print(f"  Kept  : {len(chunks)} chunks  /  {kept_words:,} words "
          f"(budget {args.budget:,})")
    if chunks:
        print(f"  Score : {chunks[0].score:.3f} (best) … {chunks[-1].score:.3f} (worst kept)")
    print("=" * 64 + "\n")

    print(combined_text(chunks))
    return 0 if chunks else 1


if __name__ == "__main__":
    sys.exit(main())
