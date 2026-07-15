#!/usr/bin/env python3
"""Convert legacy books/<slug>/index.html files to index.md.

Usage: uv run python scripts/convert_html_books_to_markdown.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from book_markdown import convert_html_book_to_markdown, write_book_markdown

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOOKS_ROOT = PROJECT_ROOT / "books"


def main() -> int:
    converted = 0
    skipped = 0
    for html_path in sorted(BOOKS_ROOT.glob("*/index.html")):
        md_path = html_path.with_suffix(".md")
        if md_path.exists():
            print(f"  skip: {md_path.relative_to(PROJECT_ROOT)} (already exists)")
            skipped += 1
            continue
        print(f"  convert: {html_path.relative_to(PROJECT_ROOT)}")
        md = convert_html_book_to_markdown(html_path.read_text(encoding="utf-8"))
        write_book_markdown(md_path, md)
        html_path.unlink()
        converted += 1
    print(f"\nConverted {converted} book(s); skipped {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
