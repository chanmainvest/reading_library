#!/usr/bin/env python3
"""Wire the on-device chatbot into every page of the reading library.

Injects, into each index.html (the portal at the repo root and every
books/<slug>/index.html), two asset references with the correct relative
depth:

    * before </head>:  <link rel="stylesheet" href="{prefix}assets/chatbot.css">
    * before </body>:  <script type="module" src="{prefix}assets/chatbot.js" defer></script>

The portal (root index.html) uses prefix "" (assets/...); book pages at
books/<slug>/index.html use prefix "../" (../assets/...). Idempotent: a
page already carrying the chatbot link is left untouched.

Run this after adding or converting books, or after updating
web_assets/chatbot.{css,js}, so every page references the assets. The
web_assets/ copies are the source of truth; this script does NOT copy
them to assets/ — run `copy_web_assets.py` (or copy manually) for that.

Usage: py scripts/wire_chatbot.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSS_NAME = "chatbot.css"
JS_NAME = "chatbot.js"
# Marker tokens that prove a page is already wired.
CSS_MARKER = f"assets/{CSS_NAME}"
JS_MARKER = f"assets/{JS_NAME}"


def relative_prefix(html_path: Path) -> str:
    """Return the relative prefix from an html page to the repo root.

    Root index.html -> "" (so assets resolve as "assets/...").
    books/<slug>/index.html -> "../" (so assets resolve as "../assets/...").
    """
    rel = html_path.relative_to(PROJECT_ROOT).as_posix()
    depth = rel.count("/")  # index.html -> 0; books/x/index.html -> 2
    # depth 0 = root page; depth >=1 means at least one dir below root.
    if depth == 0:
        return ""
    return "../" * (depth - 1) if depth > 1 else "../"


def is_wired(html: str) -> bool:
    return CSS_MARKER in html and JS_MARKER in html


def wire_file(html_path: Path) -> str:
    html = html_path.read_text(encoding="utf-8")
    if is_wired(html):
        return "skip"
    prefix = relative_prefix(html_path)
    css_link = f'<link rel="stylesheet" href="{prefix}assets/{CSS_NAME}">'
    js_link = f'<script type="module" src="{prefix}assets/{JS_NAME}" defer></script>'

    # Insert the stylesheet just before </head>. If no </head>, append to end.
    new = re.sub(r"</head>", f"  {css_link}\n</head>", html, count=1)
    if new == html:
        new = html + f"\n{css_link}\n"
    # Insert the script just before </body>.
    new2 = re.sub(r"</body>", f"  {js_link}\n</body>", new, count=1)
    if new2 == new:
        new2 = new + f"\n{js_link}\n"
    html_path.write_text(new2, encoding="utf-8")
    return "wired"


def main() -> int:
    # Discover every index.html: the portal plus each book.
    targets = [PROJECT_ROOT / "index.html"]
    targets.extend(sorted((PROJECT_ROOT / "books").glob("*/index.html")))
    wired = 0
    skipped = 0
    missing = 0
    for path in targets:
        if not path.exists():
            print(f"  miss: {path.relative_to(PROJECT_ROOT)}")
            missing += 1
            continue
        result = wire_file(path)
        if result == "wired":
            wired += 1
            print(f"  wire: {path.relative_to(PROJECT_ROOT)}")
        else:
            skipped += 1
            print(f"  skip: {path.relative_to(PROJECT_ROOT)} (already wired)")
    print(f"\n{wired} wired, {skipped} skipped, {missing} missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
