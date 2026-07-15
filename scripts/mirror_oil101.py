"""Mirror Morgan Downey's Oil 101 site into a single index.md + images/ dir.

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

from book_markdown import build_book_markdown, html_fragment_to_markdown, write_book_markdown

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
    "appendices": [
        "forward-markets-mechanics",
        "conversion-factors",
    ]
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


def generate_wti_negative_svg() -> str:
    """Compile a beautiful, premium, offline-friendly SVG for the WTI Negative Price event."""
    data = [
        {"day": "Apr 13", "val": 22.41},
        {"day": "Apr 14", "val": 20.11},
        {"day": "Apr 15", "val": 19.87},
        {"day": "Apr 16", "val": 19.87},
        {"day": "Apr 17", "val": 18.27},
        {"day": "Apr 20", "val": -37.63, "is_neg": True},
        {"day": "Apr 21", "val": 10.01},
        {"day": "Apr 22", "val": 13.78},
    ]

    width = 800
    height = 360
    padding_left = 60
    padding_right = 40
    padding_top = 40
    padding_bottom = 40

    chart_width = width - padding_left - padding_right
    chart_height = height - padding_top - padding_bottom

    def x_scale(i):
        return padding_left + i * (chart_width / (len(data) - 1))

    y_min = -50.0
    y_max = 30.0
    y_range = y_max - y_min

    def y_scale(v):
        return padding_top + (y_max - v) * (chart_height / y_range)

    # 1. Zero line
    zero_y = y_scale(0)
    zero_line = f'<line class="zero-line" x1="{padding_left}" y1="{zero_y}" x2="{width - padding_right}" y2="{zero_y}" />'

    # 2. Main Price Line
    pts = [f"{x_scale(i)},{y_scale(d['val'])}" for i, d in enumerate(data)]
    path_d = "M " + " L ".join(pts)

    # 3. Gridlines
    grid_lines = []
    for val in range(-40, 31, 20):
        y = y_scale(val)
        grid_lines.append(f'<line class="grid-line" x1="{padding_left}" y1="{y}" x2="{width - padding_right}" y2="{y}" />')
        grid_lines.append(f'<text class="axis-text" x="{padding_left - 12}" y="{y + 4}" text-anchor="end">${val}</text>')

    # 4. Data points and labels
    dots = []
    for i, d in enumerate(data):
        x = x_scale(i)
        y = y_scale(d["val"])
        fill = "#ef4444" if d.get("is_neg") else "#6366f1"
        dots.append(f'<circle cx="{x}" cy="{y}" r="4" fill="{fill}" />')
        if d.get("is_neg"):
            dots.append(f'<text class="callout-text" x="{x}" y="{y + 20}" text-anchor="middle" fill="#ef4444" font-weight="bold">-${abs(d["val"])}</text>')

        # X labels
        dots.append(f'<text class="axis-text" x="{x}" y="{height - padding_bottom + 20}" text-anchor="middle">{d["day"]}</text>')

    svg = f"""<svg viewBox="0 0 {width} {height}" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" class="wti-negative-svg">
  <style>
    .wti-negative-svg {{ background: transparent; font-family: Inter, sans-serif; }}
    .grid-line {{ stroke: var(--rule, #e5e5e5); stroke-width: 1; stroke-opacity: 0.5; }}
    .zero-line {{ stroke: #64748b; stroke-width: 1.5; stroke-dasharray: 4 4; }}
    .axis-text {{ fill: var(--muted, #888888); font-size: 11px; }}
    .price-line {{ stroke: #6366f1; stroke-width: 3; fill: none; stroke-linejoin: round; stroke-linecap: round; }}
    .callout-text {{ font-size: 12px; }}
    @media (prefers-color-scheme: dark) {{
      .grid-line {{ stroke: #333333; }}
      .axis-text {{ fill: #9ca3af; }}
      .price-line {{ stroke: #818cf8; }}
    }}
  </style>
  {"".join(grid_lines)}
  {zero_line}
  <path class="price-line" d="{path_d}" />
  {"".join(dots)}
</svg>"""
    return svg


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

    chapter_sections = []
    toc_entries = []

    # Combine chapters and appendices
    all_pages = [(slug, f"{base}/chapters/{slug}") for slug in cfg["chapters"]]
    all_pages += [(slug, f"{base}/appendices/{slug}") for slug in cfg["appendices"]]

    for i, (slug, url) in enumerate(all_pages, 1):
        print(f"[{i}/{len(all_pages)}] {url}")
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

        for source in main.find_all("source"):
            source.decompose()

        # Internal links
        for a in main.find_all("a", href=True):
            href = a["href"]
            for s in cfg["chapters"] + cfg["appendices"]:
                if f"/chapters/{s}" in href or f"/appendices/{s}" in href:
                    a["href"] = f"#chapter-{s}"

        chapter_id = f"chapter-{slug}"
        toc_entries.append((i, title, chapter_id))

        body_content = main.decode_contents()

        # SVG Chart Injection
        if slug == "negative-prices":
            target_regex = r'<div class="recharts-responsive-container"[^>]*></div>'
            if re.search(target_regex, body_content):
                svg_chart = generate_wti_negative_svg()
                injected_html = f"""<div class="offline-chart-container" style="width:100%; border: 1px solid var(--rule); border-radius: 8px; padding: 1.5rem 1rem 1rem 0.5rem; margin: 2rem 0; background: rgba(0,0,0,0.02);">
{svg_chart}
</div>"""
                body_content = re.sub(target_regex, injected_html, body_content, count=1)
                print("  -> Injected WTI Negative Price SVG chart.")

        kicker_label = "Appendix" if slug in cfg["appendices"] else "Chapter"
        body_md = html_fragment_to_markdown(body_content)
        body_md = f"## {title}\n\n{body_md}".strip()
        chapter_sections.append(
            {
                "id": chapter_id,
                "class": "chapter",
                "kicker": f"{kicker_label} {i}",
                "title": title,
                "body_md": body_md,
            }
        )

    toc_lines = ["## Contents", ""]
    for n, t, cid in toc_entries:
        toc_lines.append(f"{n}. [{t}](#{cid})")
    toc_md = "\n".join(toc_lines)
    preamble = (
        f"# {cfg['title']}\n\n"
        f"*Morgan Downey · offline mirror of [{urlparse(base).netloc}]({base})*\n\n"
        f"{toc_md}"
    )
    page = build_book_markdown(
        title=cfg["title"],
        author="Morgan Downey",
        source=f"offline mirror of {urlparse(base).netloc}",
        preamble_md=preamble,
        sections=chapter_sections,
    )
    write_book_markdown(out_dir / "index.md", page)
    print(f"\nWrote {out_dir / 'index.md'} ({len(chapter_sections)} chapters, {len(image_cache)} images)")

def main():
    root = Path(__file__).resolve().parent.parent
    process_site(root / "books" / "oil101")


if __name__ == "__main__":
    main()
