#!/usr/bin/env python3
"""Wire the SPA shell + on-device chatbot into the reading library.

The library is now a single-page app: the root index.html hosts the SPA
shell (spa.js + spa.css) and the chatbot singleton (chatbot.js +
chatbot.css). Book pages are *content sources* fetched by the SPA at
runtime, so they must NOT carry their own chatbot <link>/<script> —
otherwise two chatbot instances would compete. This script:

    * ensures the root index.html loads spa.css, chatbot.css, spa.js, and
      chatbot.js (idempotent);
    * strips any chatbot <link>/<script> from every books/<slug>/index.html
      (legacy HTML books only) so they remain clean content sources for the
      SPA's fetch+inject path.

Run after adding/converting books or after updating web_assets/. The
web_assets/ copies are the source of truth; pair this with a sync of
web_assets/ → assets/ (see copy step in the docs).

Usage: uv run python scripts/wire_chatbot.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Asset filenames loaded by the SPA shell at the repo root.
ROOT_ASSETS = [
    ("css", "assets/spa.css"),
    ("css", "assets/chatbot.css"),
    ("js", "assets/spa.js"),
    ("js", "assets/chatbot.js"),
]

# Patterns matching chatbot/spa asset references to strip from book pages.
STRIP_PATTERNS = [
    re.compile(r'<link[^>]*href="[^"]*chatbot\.css"[^>]*>\s*\n?', re.I),
    re.compile(r'<link[^>]*href="[^"]*spa\.css"[^>]*>\s*\n?', re.I),
    re.compile(r'<script[^>]*src="[^"]*chatbot\.js"[^>]*>\s*</script>\s*\n?', re.I),
    re.compile(r'<script[^>]*src="[^"]*spa\.js"[^>]*>\s*</script>\s*\n?', re.I),
]


def is_root_wired(html: str) -> bool:
    return all(name in html for _kind, name in ROOT_ASSETS)


def wire_root(html_path: Path) -> str:
    html = html_path.read_text(encoding="utf-8")
    if is_root_wired(html):
        return "skip"
    # Insert any missing <link> before </head> and <script> before </body>.
    head_inserts = ""
    body_inserts = ""
    for kind, name in ROOT_ASSETS:
        if name in html:
            continue
        if kind == "css":
            head_inserts += f'  <link rel="stylesheet" href="{name}">\n'
        else:
            body_inserts += f'  <script type="module" src="{name}"></script>\n'
    new = html
    if head_inserts:
        new = re.sub(r"</head>", f"{head_inserts}</head>", new, count=1)
    if body_inserts:
        new = re.sub(r"</body>", f"{body_inserts}</body>", new, count=1)
    html_path.write_text(new, encoding="utf-8")
    return "wired"


def strip_book_assets(html_path: Path) -> str:
    html = html_path.read_text(encoding="utf-8")
    new = html
    for pat in STRIP_PATTERNS:
        new = pat.sub("", new)
    if new != html:
        html_path.write_text(new, encoding="utf-8")
        return "stripped"
    return "clean"


def main() -> int:
    root = PROJECT_ROOT / "index.html"
    if not root.exists():
        print("  miss: index.html (root)")
        return 1
    result = wire_root(root)
    print(f"  root: {result}")

    stripped = 0
    clean = 0
    for path in sorted((PROJECT_ROOT / "books").glob("*/index.html")):
        r = strip_book_assets(path)
        if r == "stripped":
            stripped += 1
            print(f"  strip: {path.relative_to(PROJECT_ROOT)}")
        else:
            clean += 1
    print(f"\nroot {result}; books: {stripped} stripped, {clean} already clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
