"""Download official book cover images by ISBN for catalog cards."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOKS_JSON = ROOT / "books.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# slug -> (isbn13, extra URL candidates beyond booksense/openlibrary)
COVER_SOURCES: dict[str, tuple[str, list[str]]] = {
    "how-to-make-money-in-stocks": (
        "9780071614139",
        [
            "https://images.booksense.com/images/139/614/9780071614139.jpg",
        ],
    ),
    "liars-poker": (
        "9780393338690",
        [
            "https://images.booksense.com/images/690/338/9780393338690.jpg",
        ],
    ),
    "lords-of-finance": (
        "9780143116806",
        [
            "https://images.booksense.com/images/806/168/9780143116806.jpg",
            "https://blackwells.co.uk/jacket/500x500/9780143116806.jpg",
        ],
    ),
    "material-world": (
        "9780753559154",
        [
            "https://cdn.waterstones.com/bookjackets/large/9780/7535/9780753559178.jpg",
            "https://images.booksense.com/images/154/559/9780753559154.jpg",
        ],
    ),
    "the-ascent-of-money": (
        "9780143116172",
        [
            "https://images.booksense.com/images/172/116/9780143116172.jpg",
            "https://blackwells.co.uk/jacket/500x500/9780143116172.jpg",
        ],
    ),
    "the-intelligent-investor": (
        "9780060555665",
        [
            "https://images.booksense.com/images/665/055/9780060555665.jpg",
            "https://www.timesreads.com/images/default-source/default-album/productimages/9780060555665.jpg",
        ],
    ),
    "the-intelligent-option-investor": (
        "9780071833653",
        [
            "https://pictures.abebooks.com/isbn/9780071833653-uk.jpg",
            "https://images.booksense.com/images/653/833/9780071833653.jpg",
        ],
    ),
}


def booksense_url(isbn13: str) -> str:
    body = isbn13.replace("-", "")
    if body.startswith("978"):
        body = body[3:]
    suffix = body[-3:]
    middle = body[-6:-3]
    return f"https://images.booksense.com/images/{suffix}/{middle}/{isbn13}.jpg"


def openlibrary_url(isbn13: str) -> str:
    return f"https://covers.openlibrary.org/b/isbn/{isbn13}-L.jpg"


def is_image(payload: bytes) -> bool:
    if len(payload) < 5000:
        return False
    if payload.startswith(b"\x89PNG") or payload.startswith(b"\xff\xd8\xff"):
        return True
    if payload.startswith(b"GIF8") or payload.startswith(b"RIFF") and b"WEBP" in payload[:16]:
        return True
    if payload.lstrip().startswith(b"<?xml") or payload.lstrip().startswith(b"<"):
        return False
    return False


def guess_suffix(payload: bytes, url: str) -> str:
    if ".png" in url.lower() or payload.startswith(b"\x89PNG"):
        return ".png"
    if ".gif" in url.lower() or payload.startswith(b"GIF8"):
        return ".gif"
    if ".webp" in url.lower():
        return ".webp"
    return ".jpg"


def fetch_url(url: str) -> bytes | None:
    try:
        result = subprocess.run(
            [
                "curl.exe",
                "-sSL",
                "--fail",
                "--ssl-no-revoke",
                "-A",
                UA,
                "--max-time",
                "60",
                url,
            ],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return None
    payload = result.stdout
    return payload if is_image(payload) else None


def candidate_urls(isbn13: str, extras: list[str]) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for url in [*extras, booksense_url(isbn13), openlibrary_url(isbn13)]:
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def download_cover(slug: str, isbn13: str, extras: list[str]) -> Path | None:
    book_dir = ROOT / "books" / slug / "assets"
    book_dir.mkdir(parents=True, exist_ok=True)
    for old in book_dir.glob("cover-official.*"):
        old.unlink()

    for url in candidate_urls(isbn13, extras):
        payload = fetch_url(url)
        if not payload:
            continue
        dest = book_dir / f"cover-official{guess_suffix(payload, url)}"
        dest.write_bytes(payload)
        print(f"  saved {dest.relative_to(ROOT)} ({len(payload)} bytes) from {url}")
        return dest

    # Reuse embedded McGraw-Hill ci_std cover when present.
    if slug == "how-to-make-money-in-stocks":
        for path in sorted((ROOT / "books" / slug / "assets").glob("*ci_std*.jpg")):
            dest = book_dir / "cover-official.jpg"
            dest.write_bytes(path.read_bytes())
            print(f"  reused {path.name} -> {dest.relative_to(ROOT)}")
            return dest

    print(f"  FAILED: no cover found for {slug}")
    return None


def update_catalog(slug: str, rel_path: str) -> None:
    catalog = json.loads(BOOKS_JSON.read_text(encoding="utf-8"))
    for entry in catalog:
        if entry.get("slug") == slug:
            entry["coverImage"] = rel_path.replace("\\", "/")
            break
    else:
        raise KeyError(slug)
    BOOKS_JSON.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def sync_fallback() -> None:
    import sys

    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from sync_books_catalog import sync_fallback_books

    catalog = json.loads(BOOKS_JSON.read_text(encoding="utf-8"))
    sync_fallback_books(catalog)


def main() -> None:
    for slug, (isbn13, extras) in COVER_SOURCES.items():
        print(slug)
        dest = download_cover(slug, isbn13, extras)
        if dest:
            rel = dest.relative_to(ROOT / "books" / slug).as_posix()
            update_catalog(slug, rel)
    sync_fallback()
    print("Done.")


if __name__ == "__main__":
    main()
