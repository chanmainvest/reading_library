"""Refresh coverImage paths in books.json from each book's index.md."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOKS_JSON = ROOT / "books.json"
INDEX_HTML = ROOT / "index.html"
BOOKS_ROOT = ROOT / "books"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
COVER_PATH_RE = re.compile(r"(?:cover|calibre_cover|cover_image|cvi)", re.I)
SECTION_MARKER_RE = re.compile(r"<!--\s*rl-section\b")
SVG_IMAGE_RE = re.compile(
    r'<image[^>]+xlink:href=["\']((?:assets|images)/[^"\']+)["\']',
    re.I,
)
MD_IMAGE_WITH_ALT_RE = re.compile(
    r"!\[([^\]]*)\]\(((?:assets|images)/[^\s)\"']+\.(?:jpe?g|png|webp|gif))"
    r'(?:\s+["\'][^"\']*["\'])?\)',
    re.I,
)


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def _resolve_cover_rel(book_dir: Path, rel: str) -> str | None:
    rel = rel.replace("\\", "/")
    resolved = (book_dir / rel).resolve()
    if not resolved.is_file():
        return None
    try:
        resolved.relative_to(book_dir.resolve())
    except ValueError:
        return None
    return rel


def _first_section_text(text: str) -> str:
    match = SECTION_MARKER_RE.search(text)
    if not match:
        return text[:30000]
    end = SECTION_MARKER_RE.search(text, match.end())
    if end:
        return text[match.start() : end.start()]
    return text[match.start() : match.start() + 30000]


def _glob_cover_asset(folder: Path, prefix: str) -> str | None:
    if not folder.is_dir():
        return None
    for pattern in (
        "cover-official.*",
        "calibre_cover*",
        "Cover*",
        "MSRCover*",
        "cover*",
        "*_cvi_*",
        "*cover*",
    ):
        for path in sorted(folder.glob(pattern)):
            if _is_image(path):
                return f"{prefix}/{path.name}"
    return None


def _cover_markdown_match(match: re.Match[str], book_dir: Path) -> str | None:
    alt, rel = match.group(1), match.group(2)
    if COVER_PATH_RE.search(rel) or re.search(r"\bcover\b", alt, re.I):
        return _resolve_cover_rel(book_dir, rel)
    return None


def find_cover_path(book_dir: Path) -> str | None:
    slug = book_dir.name

    for sub in ("assets", "images"):
        for ext in IMAGE_EXTS:
            dedicated = book_dir / sub / f"cover-{slug}{ext}"
            if dedicated.is_file():
                return f"{sub}/{dedicated.name}"

    for name in ("index.md", "index.html"):
        index_path = book_dir / name
        if not index_path.exists():
            continue
        text = index_path.read_text(encoding="utf-8", errors="replace")
        section_one = _first_section_text(text)

        for match in SVG_IMAGE_RE.finditer(section_one):
            rel = _resolve_cover_rel(book_dir, match.group(1))
            if rel:
                return rel

        for match in MD_IMAGE_WITH_ALT_RE.finditer(section_one):
            rel = _cover_markdown_match(match, book_dir)
            if rel:
                return rel

        for match in MD_IMAGE_WITH_ALT_RE.finditer(section_one):
            rel = _resolve_cover_rel(book_dir, match.group(2))
            if rel:
                return rel

    for sub in ("assets", "images"):
        official = book_dir / sub / "cover-official.jpg"
        if official.is_file():
            return f"{sub}/cover-official.jpg"

    for sub in ("assets", "images"):
        rel = _glob_cover_asset(book_dir / sub, sub)
        if rel:
            return rel

    return None


def sync_hrefs(catalog: list[dict]) -> int:
    updated = 0
    for entry in catalog:
        href = entry.get("href", "")
        if href.endswith("/index.html"):
            entry["href"] = href.replace("/index.html", "/index.md")
            updated += 1
    return updated


def sync_cover_images(catalog: list[dict]) -> int:
    updated = 0
    for entry in catalog:
        slug = entry.get("slug")
        if not slug:
            continue
        book_dir = BOOKS_ROOT / slug
        cover = find_cover_path(book_dir)
        if cover:
            if entry.get("coverImage") != cover:
                entry["coverImage"] = cover
                updated += 1
        elif "coverImage" in entry:
            del entry["coverImage"]
            updated += 1
    return updated


def sync_fallback_books(catalog: list[dict]) -> bool:
    if not INDEX_HTML.exists():
        return False

    text = INDEX_HTML.read_text(encoding="utf-8")
    start_marker = "    const FALLBACK_BOOKS = "
    end_marker = "    ];\n\n    let booksData = [];"
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        raise RuntimeError("Could not locate FALLBACK_BOOKS block in index.html")

    payload = json.dumps(catalog, indent=2)
    indented = "\n".join("    " + line for line in payload.splitlines())
    replacement = f"    const FALLBACK_BOOKS = {indented[4:]};\n"
    new_text = text[:start] + replacement + text[end + len("    ];\n") :]
    if new_text != text:
        INDEX_HTML.write_text(new_text, encoding="utf-8", newline="\n")
        return True
    return False


def main() -> None:
    catalog = json.loads(BOOKS_JSON.read_text(encoding="utf-8"))
    href_updates = sync_hrefs(catalog)
    cover_updates = sync_cover_images(catalog)
    BOOKS_JSON.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    fallback_updated = sync_fallback_books(catalog)
    print(f"Updated {href_updates} href field(s) in books.json")
    print(f"Updated {cover_updates} coverImage field(s) in books.json")
    print(f"Fallback embed in index.html: {'updated' if fallback_updated else 'unchanged'}")


if __name__ == "__main__":
    main()
