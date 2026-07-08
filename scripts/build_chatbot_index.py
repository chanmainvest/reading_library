#!/usr/bin/env python3
"""Build the chatbot RAG chunk index for the reading library.

Walks every published book in books/catalog.json, extracts the readable
text from its single-page index.html (stripping nav, styles, scripts,
image/svg wrappers, and the per-section kicker labels), splits it into
~600-character overlapping chunks, and writes a single
assets/chatbot_chunks.json containing records of the form::

    {"id": int, "lang": "en", "url": "books/<slug>/index.html",
     "title": "<book title>", "author": "<author>", "text": "<chunk>"}

The browser-side chatbot fetches this file, embeds chunks via an in-page
embedding model (transformers.js), caches the embeddings in IndexedDB,
and uses cosine similarity to retrieve the top-K most relevant chunks
for each user question. Embedding is *not* done here so that this build
step has no Python ML dependencies — pure stdlib + bs4/lxml (already
required by the EPUB converter).

The reading library is English-only, so every chunk is tagged lang="en".
The companion scripts/build_chatbot_embeddings.mjs uses a single "en"
language bucket.

Usage: py scripts/build_chatbot_index.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import warnings
from pathlib import Path

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = PROJECT_ROOT / "books" / "catalog.json"
OUT_PATH = PROJECT_ROOT / "assets" / "chatbot_chunks.json"

# Books are far longer than the tutorial's lessons, so we use larger chunks
# than the tutorial's 600/100. ~1200 chars keeps each chunk a coherent
# passage (a few paragraphs) while roughly halving the chunk count versus
# the tutorial setting — important because every chunk is embedded and
# shipped in the prebuilt binary.
CHUNK_SIZE = 1200      # characters per chunk
CHUNK_OVERLAP = 200    # overlap between consecutive chunks
LANG = "en"            # the reading library is English-only

# Tags whose subtrees contribute no retrievable prose.
STRIP_TAGS = [
    "script", "style", "noscript", "nav", "form", "input", "button",
    "svg", "img", "figure", "figcaption",
]
# Classes that are presentational chrome, not content.
STRIP_CLASS_SUBSTRINGS = ("section-kicker", "chapter-number", "book-sub", "toc")


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping windows.

    Tries to break on sentence / paragraph boundaries when possible to
    avoid mid-word cuts; falls back to hard cuts when no boundary is
    nearby. Mirrors scripts/build_chatbot_index.py from the tutorial so
    chunk boundaries stay consistent across the project.
    """
    if len(text) <= size:
        return [text] if text.strip() else []

    chunks: list[str] = []
    step = max(1, size - overlap)
    i = 0
    while i < len(text):
        end = min(i + size, len(text))
        if end < len(text):
            window = text[end:end + 80]
            for boundary in ("\n\n", ". ", "! ", "? ", "\n"):
                idx = window.find(boundary)
                if 0 <= idx < 80:
                    end = end + idx + len(boundary)
                    break
        chunk = text[i:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        i = max(i + step, end - overlap)
    return chunks


def extract_text(html: str) -> str:
    """Pull readable prose out of a book's single-page HTML.

    Handles both layouts in the library:
      * EPUB conversions: <article> wrapping <section class="epub-section">
      * Web mirrors (oil101/natgas101): <section class="chapter"> with a
        <nav class="toc"> and per-chapter titles.
    Strips presentational chrome so embeddings are dense natural language.
    """
    doc = BeautifulSoup(html, "lxml")

    # Drop subtrees that aren't prose. Collect first, then decompose, so the
    # tree mutation doesn't invalidate live find_all() results mid-iteration.
    to_remove = []
    for tag_name in STRIP_TAGS:
        to_remove.extend(doc.find_all(tag_name))
    for node in doc.find_all(class_=True):
        classes = node.get("class", []) or []
        if any(any(sub in c for sub in STRIP_CLASS_SUBSTRINGS) for c in classes):
            # Keep section/article/div wrappers (they structure content);
            # only decompose presentational chrome nodes.
            if node.name not in ("section", "article", "div"):
                to_remove.append(node)
    for node in to_remove:
        node.decompose()

    # Prefer the main content container if present, else fall back to body.
    root = doc.find("article") or doc.find("main") or doc.body or doc
    text = root.get_text(separator="\n", strip=True)
    # Collapse whitespace runs but keep paragraph breaks for chunk boundary hints.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def collect_chunks() -> list[dict]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    records: list[dict] = []
    next_id = 0

    for book in catalog.get("publicBooks", []):
        slug = book.get("slug")
        href = book.get("href")
        status = book.get("status")
        if not slug or not href or status not in ("public_mirror", "epub_conversion"):
            continue
        html_path = PROJECT_ROOT / href
        if not html_path.exists():
            print(f"  warn: missing {href}, skipping", file=sys.stderr)
            continue
        title = book.get("title", slug)
        author = book.get("author", "")
        try:
            text = extract_text(html_path.read_text(encoding="utf-8"))
        except Exception as err:
            print(f"  warn: failed to parse {href}: {err}", file=sys.stderr)
            continue
        book_chunks = 0
        for chunk in chunk_text(text):
            records.append({
                "id": next_id,
                "lang": LANG,
                "url": href,                # e.g. books/the-big-short/index.html
                "title": title,
                "author": author,
                "text": chunk,
            })
            next_id += 1
            book_chunks += 1
        print(f"  {slug}: {len(text):,} chars -> {book_chunks} chunks")

    return records


def main() -> int:
    print(f"Building chatbot RAG index at {OUT_PATH}")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    records = collect_chunks()
    OUT_PATH.write_text(
        json.dumps(records, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_mb = OUT_PATH.stat().st_size / 1024 / 1024
    print(f"\nWrote {len(records)} chunks from {LANG} ({size_mb:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
