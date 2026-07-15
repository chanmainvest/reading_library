#!/usr/bin/env python3
"""Build the chatbot RAG chunk index for the reading library.

Walks every published book in books/catalog.json, extracts the readable
text from its single-page index.md **section by section** (stripping
presentational kickers and image references), splits each section into
~1200-character overlapping chunks, and writes a single
assets/chatbot_chunks.json containing records of the form::

    {"id": int, "lang": "en", "url": "books/<slug>/index.md",
     "title": "<book title>", "author": "<author>",
     "sectionId": "<section id or ''>", "sectionTitle": "<heading or ''>",
     "text": "<chunk>"}

Usage: uv run python scripts/build_chatbot_index.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from book_markdown import SECTION_MARKER_RE, parse_sections_from_markdown

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = PROJECT_ROOT / "books" / "catalog.json"
OUT_PATH = PROJECT_ROOT / "assets" / "chatbot_chunks.json"

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
LANG = "en"


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
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


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def markdown_to_plain(md: str) -> str:
    text = md
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"[*_~>|]", "", text)
    return _clean_text(text)


def collect_sections(markdown: str) -> list[dict]:
    body = markdown
    fm_match = re.match(r"^---\r?\n[\s\S]*?\r?\n---\r?\n", markdown)
    if fm_match:
        body = markdown[fm_match.end():]

    out: list[dict] = []
    parts = SECTION_MARKER_RE.split(body)
    preamble = parts[0] if parts else ""
    preamble_text = markdown_to_plain(preamble)
    if preamble_text:
        out.append({"sectionId": "", "sectionTitle": "", "text": preamble_text})

    idx = 1
    while idx + 1 < len(parts):
        attr_str = parts[idx]
        section_md = parts[idx + 1]
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', attr_str))
        text = markdown_to_plain(section_md)
        if text:
            out.append(
                {
                    "sectionId": attrs.get("id", ""),
                    "sectionTitle": attrs.get("title", ""),
                    "text": text,
                }
            )
        idx += 2
    return out


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
        md_path = PROJECT_ROOT / href
        if not md_path.exists() and href.endswith(".html"):
            md_path = md_path.with_suffix(".md")
        if not md_path.exists():
            print(f"  warn: missing {href}, skipping", file=sys.stderr)
            continue
        href = str(md_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        title = book.get("title", slug)
        author = book.get("author", "")
        try:
            sections = collect_sections(md_path.read_text(encoding="utf-8"))
        except Exception as err:
            print(f"  warn: failed to parse {href}: {err}", file=sys.stderr)
            continue
        book_chunks = 0
        for sec in sections:
            for chunk in chunk_text(sec["text"]):
                records.append({
                    "id": next_id,
                    "lang": LANG,
                    "url": href,
                    "title": title,
                    "author": author,
                    "sectionId": sec["sectionId"],
                    "sectionTitle": sec["sectionTitle"],
                    "text": chunk,
                })
                next_id += 1
                book_chunks += 1
        print(f"  {slug}: {len(sections)} sections -> {book_chunks} chunks")

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
