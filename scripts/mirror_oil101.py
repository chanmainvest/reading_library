"""Mirror Morgan Downey's Oil 101 site into a single index.html + images/ dir.

Usage:
    py scripts/mirror_oil101.py
"""
import sys
import os
import re
import hashlib
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
import subprocess
from bs4 import BeautifulSoup

SITE_CFG = {
    "base": "https://oil101.morgandowney.com",
    "title": "Oil 101",
    "chapters": [
        "history",
        "crude-oil-assay",
        "components",
        "chemistry",
        "industry-overview",
        "exploration-production",
        "refining",
        "standards",
        "finished-products",
        "petrochemicals",
        "transporting-oil",
        "storage",
        "seasonality",
        "reserves",
        "environmental",
        "engine-technologies",
        "oil-prices",
        "futures-swaps",
        "options",
        "risk-management",
        "shale-revolution",
        "opec-plus",
        "negative-prices",
        "us-lng",
        "energy-transition",
        "iran-strait",
    ],
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def fetch(url: str) -> bytes:
    # curl.exe uses Windows native cert store; --ssl-no-revoke avoids
    # CRYPT_E_NO_REVOCATION_CHECK on some Windows setups.
    result = subprocess.run(
        [
            "curl.exe",
            "-sSL",
            "--fail",
            "--ssl-no-revoke",
            "-A",
            UA,
            "--max-time",
            "120",
            url,
        ],
        capture_output=True,
        check=True,
    )
    return result.stdout


def safe_image_name(url: str, content: bytes) -> str:
    """Derive a stable, filesystem-safe filename for an image URL."""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    base = os.path.basename(path) or hashlib.md5(url.encode()).hexdigest()[:12]
    # Strip query, sanitize
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    # If no extension, guess from content
    if "." not in base:
        guess = None
        if content.startswith(b"\x89PNG"):
            guess = ".png"
        elif content.startswith(b"\xff\xd8"):
            guess = ".jpg"
        elif content.startswith(b"GIF8"):
            guess = ".gif"
        elif content.startswith(b"RIFF") and b"WEBP" in content[:16]:
            guess = ".webp"
        elif content.lstrip().startswith(b"<svg") or content.lstrip().startswith(b"<?xml"):
            guess = ".svg"
        if guess:
            base += guess
    # Disambiguate by short hash of url to avoid collisions
    h = hashlib.md5(url.encode()).hexdigest()[:6]
    name, ext = os.path.splitext(base)
    return f"{name}-{h}{ext}"


def extract_main(soup: BeautifulSoup) -> BeautifulSoup:
    """Pull the chapter body. Try <main>, then <article>, then biggest <section>."""
    for sel in ["main", "article"]:
        node = soup.find(sel)
        if node:
            return node
    # fallback
    body = soup.find("body")
    return body if body else soup


def strip_chrome(node):
    """Remove nav, header, footer, script, style, and link/cta widgets."""
    for tag in node.find_all(["nav", "header", "footer", "script", "style", "noscript", "form"]):
        tag.decompose()
    # Remove obvious nav/cta classes
    for cls in ["nav", "navbar", "header", "footer", "cta", "newsletter", "subscribe", "site-header", "site-footer", "breadcrumb"]:
        for tag in node.select(f'[class*="{cls}"]'):
            tag.decompose()
    # Remove "Next chapter / Previous chapter" link blocks
    for a in node.find_all("a"):
        text = (a.get_text() or "").strip().lower()
        if text in {"next chapter", "previous chapter", "next", "previous", "back to chapters", "← back"}:
            parent = a.parent
            if parent and len(parent.get_text(strip=True)) < 60:
                parent.decompose()
            else:
                a.decompose()


def process_site(out_dir: Path):
    cfg = SITE_CFG
    base = cfg["base"]
    out_dir.mkdir(parents=True, exist_ok=True)
    img_dir = out_dir / "images"
    img_dir.mkdir(exist_ok=True)

    image_cache: dict[str, str] = {}

    def get_image(src_url: str) -> str | None:
        if src_url in image_cache:
            return image_cache[src_url]
        try:
            data = fetch(src_url)
        except Exception as e:
            print(f"  ! image failed {src_url}: {e}")
            return None
        name = safe_image_name(src_url, data)
        (img_dir / name).write_bytes(data)
        rel = f"images/{name}"
        image_cache[src_url] = rel
        return rel

    chapter_blocks = []
    toc_entries = []

    for i, slug in enumerate(cfg["chapters"], 1):
        url = f"{base}/chapters/{slug}"
        print(f"[{i}/{len(cfg['chapters'])}] {url}")
        try:
            html = fetch(url).decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  ! fetch failed: {e}")
            continue
        soup = BeautifulSoup(html, "lxml")

        # Title
        title_tag = soup.find("h1") or soup.find("title")
        title = (title_tag.get_text(strip=True) if title_tag else slug.replace("-", " ").title())

        main = extract_main(soup)
        strip_chrome(main)

        # Rewrite images
        for img in main.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if not src:
                continue
            abs_src = urljoin(url, src)
            rel = get_image(abs_src)
            if rel:
                img["src"] = rel
                if img.has_attr("srcset"):
                    del img["srcset"]
            for attr in ("loading", "data-src", "data-srcset"):
                if img.has_attr(attr):
                    del img[attr]

        # also handle <source srcset> inside <picture>
        for source in main.find_all("source"):
            source.decompose()

        # Convert internal chapter links to in-page anchors
        for a in main.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/chapters/"):
                target_slug = href.split("/")[-1].split("#")[0]
                a["href"] = f"#chapter-{target_slug}"
            elif href.startswith(base + "/chapters/"):
                target_slug = href[len(base + "/chapters/"):].split("#")[0]
                a["href"] = f"#chapter-{target_slug}"

        chapter_id = f"chapter-{slug}"
        toc_entries.append((i, title, chapter_id))

        # Wrap chapter content
        chapter_html = (
            f'<section class="chapter" id="{chapter_id}">'
            f'<div class="chapter-number">Chapter {i}</div>'
            f'<h1 class="chapter-title">{title}</h1>'
            f'<div class="chapter-body">{main.decode_contents()}</div>'
            f'</section>'
        )
        chapter_blocks.append(chapter_html)

    # Build TOC
    toc_html = '<nav class="toc"><h2>Contents</h2><ol>' + "".join(
        f'<li><a href="#{cid}"><span class="num">{n}.</span> {t}</a></li>'
        for n, t, cid in toc_entries
    ) + "</ol></nav>"

    css = """
    :root { --bg:#fff; --fg:#1a1a1a; --muted:#666; --accent:#0a4f6b; --rule:#e5e5e5; }
    * { box-sizing: border-box; }
    body { font-family: Georgia, 'Times New Roman', serif; max-width: 760px; margin: 0 auto; padding: 2rem 1.25rem 6rem; color: var(--fg); background: var(--bg); line-height: 1.65; font-size: 18px; }
    h1.book-title { font-size: 2.5rem; margin: 0 0 0.25rem; letter-spacing: -0.02em; }
    .book-sub { color: var(--muted); margin: 0 0 2.5rem; font-style: italic; }
    nav.toc { margin: 3rem 0 5rem; padding: 1.5rem 0; border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); }
    nav.toc h2 { font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin: 0 0 1rem; }
    nav.toc ol { list-style: none; padding: 0; margin: 0; }
    nav.toc li { margin: 0.4rem 0; }
    nav.toc a { color: var(--fg); text-decoration: none; display: flex; gap: 0.75rem; }
    nav.toc a:hover { color: var(--accent); }
    nav.toc .num { color: var(--muted); min-width: 2.5rem; font-variant-numeric: tabular-nums; }
    section.chapter { margin: 5rem 0; padding-top: 3rem; border-top: 1px solid var(--rule); }
    section.chapter:first-of-type { border-top: none; }
    .chapter-number { color: var(--muted); text-transform: uppercase; letter-spacing: 0.15em; font-size: 0.8rem; margin-bottom: 0.5rem; }
    .chapter-title { font-size: 2rem; margin: 0 0 2rem; letter-spacing: -0.01em; }
    .chapter-body h2 { font-size: 1.4rem; margin-top: 2.5rem; }
    .chapter-body h3 { font-size: 1.15rem; margin-top: 2rem; }
    .chapter-body p { margin: 1rem 0; }
    .chapter-body img { max-width: 100%; height: auto; display: block; margin: 1.5rem auto; }
    .chapter-body figure { margin: 1.5rem 0; }
    .chapter-body figcaption { font-size: 0.85rem; color: var(--muted); text-align: center; margin-top: 0.5rem; }
    .chapter-body table { border-collapse: collapse; width: 100%; margin: 1.5rem 0; font-size: 0.92rem; }
    .chapter-body th, .chapter-body td { border: 1px solid var(--rule); padding: 0.5rem 0.75rem; text-align: left; }
    .chapter-body th { background: #f7f7f7; }
    .chapter-body blockquote { border-left: 3px solid var(--accent); padding-left: 1rem; color: var(--muted); margin: 1.5rem 0; }
    .chapter-body code { background: #f4f4f4; padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.9em; }
    .chapter-body a { color: var(--accent); }
    @media (prefers-color-scheme: dark) {
      :root { --bg:#111; --fg:#eee; --muted:#888; --rule:#333; --accent:#7ec0d8; }
      .chapter-body th { background: #1c1c1c; }
      .chapter-body code { background: #1c1c1c; }
    }
    """

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{cfg['title']}</title>
<style>{css}</style>
</head>
<body>
<h1 class="book-title">{cfg['title']}</h1>
<p class="book-sub">Morgan Downey &middot; offline mirror of <a href="{base}">{urlparse(base).netloc}</a></p>
{toc_html}
{''.join(chapter_blocks)}
</body>
</html>
"""

    (out_dir / "index.html").write_text(page, encoding="utf-8")
    print(f"\nWrote {out_dir / 'index.html'} ({len(chapter_blocks)} chapters, {len(image_cache)} images)")


def main():
    root = Path(__file__).resolve().parent.parent
    process_site(root / "oil101")


if __name__ == "__main__":
    main()
