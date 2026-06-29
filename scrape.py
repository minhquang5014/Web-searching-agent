"""
Page-content scraper for the web-search bridge (Phase 2).

scrape(url) fetches a URL, strips boilerplate (nav / header / footer /
scripts / ads), and returns clean readable text plus metadata.

Uses requests + BeautifulSoup for static HTML. JS-heavy pages (SPAs) will
return thin text — that's a Playwright concern for a later phase.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Comment

_TIMEOUT = 12.0
_MAX_TEXT_CHARS = 8_000   # ~1,500–2,000 words — enough for an LLM context chunk

# Tags whose entire subtree we discard before extracting text.
_STRIP_TAGS = {
    "script", "style", "noscript",
    "header", "footer", "nav", "aside",
    "form", "button", "input", "select", "textarea",
    "figure", "figcaption",
    "iframe", "embed", "object",
    "svg", "canvas",
    "advertisement", "ads",
}

# ARIA roles that indicate chrome rather than content.
_STRIP_ROLES = {"navigation", "banner", "complementary", "contentinfo", "search"}

# Class/id substrings that strongly suggest non-content blocks.
_NOISE_HINTS = {
    "nav", "menu", "sidebar", "footer", "header", "cookie",
    "popup", "modal", "overlay", "banner", "ad-", "-ad",
    "advert", "promo", "newsletter", "subscribe", "signup",
    "share", "social", "comment", "related", "recommend",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class ScrapeResult:
    url: str
    title: str = ""
    text: str = ""          # cleaned body text, truncated to _MAX_TEXT_CHARS
    word_count: int = 0     # words in the full extracted text before truncation
    elapsed_s: float = 0.0
    status_code: int = 0
    error: str = ""
    redirected_to: str = ""

    @property
    def domain(self) -> str:
        try:
            return urlparse(self.url).netloc.lower().removeprefix("www.")
        except Exception:
            return ""

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.error


def _is_noise(tag) -> bool:
    """Return True if a tag looks like chrome/boilerplate based on role/class/id."""
    try:
        role = (tag.get("role") or "").lower()
        if role in _STRIP_ROLES:
            return True
        for attr in ("class", "id"):
            val = tag.get(attr) or []
            if isinstance(val, str):
                val = [val]
            joined = " ".join(val).lower()
            if any(hint in joined for hint in _NOISE_HINTS):
                return True
    except Exception:
        pass
    return False


def _find_content_root(soup: BeautifulSoup):
    """
    Return the most specific content container without touching the tree yet.
    Ordered from most to least specific so we grab the tightest real-content node.
    """
    candidates = [
        soup.find("article"),
        soup.find("main"),
        soup.find(id="mw-content-text"),       # Wikipedia
        soup.find(id="main-content"),
        soup.find(id="content"),
        soup.find(id="bodyContent"),
        soup.find(attrs={"role": "main"}),
        soup.find(class_="post-content"),
        soup.find(class_="article-body"),
        soup.find(class_="entry-content"),
        soup.find(class_="story-body"),
        soup.find("body"),
        soup,
    ]
    return next((c for c in candidates if c is not None), soup)


def _extract_text(soup: BeautifulSoup) -> tuple[str, int]:
    """
    Locate the tightest content container, strip boilerplate within it,
    and return (truncated_text, full_word_count).
    """
    # Remove HTML comments globally.
    for node in soup.find_all(string=lambda s: isinstance(s, Comment)):
        node.extract()

    # Find content root BEFORE stripping so we don't accidentally destroy it.
    root = _find_content_root(soup)

    # Strip boilerplate tags within the content root only.
    for tag in root.find_all(_STRIP_TAGS):
        tag.decompose()

    # Collect noise tags first, then decompose (avoid mutating while iterating).
    noise = [t for t in root.find_all(True) if _is_noise(t)]
    for tag in noise:
        tag.decompose()

    raw = root.get_text(separator=" ", strip=True)
    words = raw.split()
    full_word_count = len(words)
    truncated = " ".join(words)[:_MAX_TEXT_CHARS]
    return truncated, full_word_count


def scrape(url: str, timeout: float = _TIMEOUT) -> ScrapeResult:
    """
    Fetch *url*, extract clean text, and return a ScrapeResult.
    Never raises — errors are captured in result.error.
    """
    start = time.time()
    result = ScrapeResult(url=url)

    try:
        resp = requests.get(
            url,
            headers=_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        result.status_code = resp.status_code
        if resp.url != url:
            result.redirected_to = resp.url

        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            result.error = f"Non-HTML content-type: {content_type.split(';')[0].strip()}"
            result.elapsed_s = time.time() - start
            return result

        if resp.status_code in (401, 403):
            result.error = f"{resp.status_code} Forbidden — site blocks scrapers"
            result.elapsed_s = time.time() - start
            return result

        if resp.status_code == 429:
            result.error = "429 Too Many Requests"
            result.elapsed_s = time.time() - start
            return result

        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        title_tag = soup.find("title")
        result.title = title_tag.get_text(strip=True) if title_tag else ""

        result.text, result.word_count = _extract_text(soup)

        if not result.text.strip():
            result.error = "No text extracted (page may be JS-rendered)"

    except requests.exceptions.Timeout:
        result.error = f"Timeout after {timeout}s"
    except requests.exceptions.TooManyRedirects:
        result.error = "Too many redirects"
    except requests.exceptions.ConnectionError as e:
        result.error = f"Connection error: {e}"
    except Exception as e:
        result.error = f"Unexpected error: {e}"

    result.elapsed_s = time.time() - start
    return result
