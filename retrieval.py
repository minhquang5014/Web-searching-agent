"""
Relevance filtering via embedding retrieval — Phase 3.5.

Replaces the naive "first-N-words" trim with query-aware chunk selection:
chunk every scraped page, embed the query + chunks with a small multilingual
model (intfloat/multilingual-e5-small), keep the top chunks by cosine
similarity until the word budget is full. Text stays VERBATIM (no summary
model, no hallucination), so the agent on Mac #1 can still cite exact figures.

Cross-platform: the model is loaded from a local folder (e.g. the USB drive
on Windows, or ~/models on the Mac mini). Resolution order:
    1. $RAG_MODEL_PATH                      (explicit override — set this in prod)
    2. ./models/multilingual-e5-small       (repo-local, portable)
    3. ~/models/multilingual-e5-small       (Mac mini default)
    4. {D,E,F}:/models/multilingual-e5-small (Windows USB default)
    5. "intfloat/multilingual-e5-small"     (last resort: download from HF)

The model is loaded once and reused across requests.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

# sentence-transformers + numpy are Phase-3.5 deps (see requirements.txt).
import numpy as np
from sentence_transformers import SentenceTransformer

_MODEL_ID = "intfloat/multilingual-e5-small"
_DIR_NAME = "multilingual-e5-small"

# e5 REQUIRES these prefixes — omitting them measurably hurts relevance.
_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "

# Chunking defaults (words). ~220 words ≈ a few paragraphs; overlap keeps a
# sentence from being split across a boundary and lost.
_CHUNK_WORDS = 220
_CHUNK_OVERLAP = 40


# ── Model loading ──────────────────────────────────────────────────────
def _resolve_model_source() -> str:
    """Return a local model dir if we can find one, else the HF model id."""
    env = os.getenv("RAG_MODEL_PATH")
    if env and Path(env).expanduser().exists():
        return str(Path(env).expanduser())

    candidates = [
        Path("models") / _DIR_NAME,                 # repo-local (portable)
        Path.home() / "models" / _DIR_NAME,         # Mac mini default
    ]
    if platform.system() == "Windows":
        for drive in ("D:", "E:", "F:", "G:"):      # USB drives
            candidates.append(Path(f"{drive}/models/{_DIR_NAME}"))

    for c in candidates:
        if c.exists():
            return str(c)
    return _MODEL_ID  # not found locally — sentence-transformers will download it


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """Load the embedding model once. Uses MPS on Apple Silicon if available."""
    source = _resolve_model_source()
    # device=None lets sentence-transformers auto-pick (CUDA → MPS → CPU).
    return SentenceTransformer(source, device=None)


# ── Chunking ───────────────────────────────────────────────────────────
def chunk_text(text: str,
               size: int = _CHUNK_WORDS,
               overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping word windows. Empty/blank text → []."""
    words = text.split()
    if not words:
        return []
    step = max(1, size - overlap)
    return [" ".join(words[i:i + size]) for i in range(0, len(words), step)]


# ── Retrieval ──────────────────────────────────────────────────────────
@dataclass
class SelectedChunk:
    rank: int            # rank of the source it came from
    title: str
    url: str
    domain: str
    text: str            # verbatim chunk
    score: float         # cosine similarity to the query
    word_count: int


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def select_relevant(query: str,
                    sources: list,
                    max_total_words: int = 4000,
                    max_chunks: int | None = None,
                    min_score: float = 0.0,
                    batch_size: int = 64) -> list[SelectedChunk]:
    """
    Rank every chunk of every source against `query`, then greedily keep the
    most relevant ones until the word budget (or chunk cap) is reached.

    `sources` is any list of objects exposing: best_text, rank, title, url, domain.
    (e.g. PipelineResult.ok_sources). Returns chunks ordered best-first.
    """
    chunks: list[str] = []
    meta: list[object] = []
    for s in sources:
        text = getattr(s, "best_text", "") or ""
        for c in chunk_text(text):
            chunks.append(_PASSAGE_PREFIX + c)
            meta.append(s)

    if not chunks:
        return []

    model = get_model()
    q_emb = model.encode([_QUERY_PREFIX + query], normalize_embeddings=True)[0]
    c_emb = model.encode(chunks, normalize_embeddings=True,
                         batch_size=batch_size, show_progress_bar=False)
    scores = c_emb @ q_emb                      # cosine (vectors are normalized)
    order = np.argsort(-scores)                 # best first

    selected: list[SelectedChunk] = []
    total_words = 0
    for idx in order:
        score = float(scores[idx])
        if score < min_score:
            break
        if max_chunks is not None and len(selected) >= max_chunks:
            break
        verbatim = chunks[idx][len(_PASSAGE_PREFIX):]   # strip the e5 prefix
        wc = len(verbatim.split())
        if total_words + wc > max_total_words:
            if total_words >= max_total_words:
                break
            verbatim = " ".join(verbatim.split()[:max_total_words - total_words])
            wc = len(verbatim.split())
        s = meta[idx]
        selected.append(SelectedChunk(
            rank=getattr(s, "rank", 0),
            title=getattr(s, "title", "") or "",
            url=getattr(s, "url", "") or "",
            domain=getattr(s, "domain", "") or _domain(getattr(s, "url", "")),
            text=verbatim,
            score=round(score, 4),
            word_count=wc,
        ))
        total_words += wc
        if total_words >= max_total_words:
            break

    return selected


def combined_text(chunks: list[SelectedChunk], separator: str = "\n\n") -> str:
    """Format selected chunks into one LLM-ready block with source attribution."""
    parts = []
    for c in chunks:
        header = f"=== [{c.rank}] {c.title or c.domain}  (relevance {c.score:.2f}) ===\nURL: {c.url}\n"
        parts.append(header + c.text)
    return separator.join(parts)
