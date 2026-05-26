"""Mirror Morgan Downey's NatGas 101 site into a single index.html + images/ dir.
Fixes the interactive chart rendering issue by programmatically compiling an offline-friendly SVG.

Usage:
    py scripts/mirror_natgas101.py
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
    "base": "https://natgas101.morgandowney.com",
    "title": "NatGas 101",
    "chapters": [
        "why-gas-is-different",
        "history-of-natgas",
        "the-players",
        "geology-and-origins",
        "chemistry-and-specifications",
        "exploration-and-drilling",
        "hydraulic-fracturing",
        "production-profiles",
        "gathering-and-processing",
        "pipelines-and-compression",
        "storage-mechanics",
        "power-generation",
        "industrial-and-residential-demand",
        "lng-export",
        "pricing-hubs-and-basis",
        "transportation-and-capacity",
        "regulation-and-environment",
        "ngl-physical",
        "ngl-markets",
        "seasonality",
        "switching",
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


def generate_duck_curve_svg() -> str:
    """Compile a beautiful, premium, offline-friendly SVG Duck Curve chart."""
    # Raw hourly dataset extracted from the next.js bundle
    fq = [
        {"hour": 0, "total": 24, "solar": 0, "net": 24},
        {"hour": 1, "total": 22, "solar": 0, "net": 22},
        {"hour": 2, "total": 21, "solar": 0, "net": 21},
        {"hour": 3, "total": 20, "solar": 0, "net": 20},
        {"hour": 4, "total": 21, "solar": 0, "net": 21},
        {"hour": 5, "total": 23, "solar": 0, "net": 23},
        {"hour": 6, "total": 26, "solar": 1, "net": 25},
        {"hour": 7, "total": 28, "solar": 4, "net": 24},
        {"hour": 8, "total": 29, "solar": 8, "net": 21},
        {"hour": 9, "total": 30, "solar": 12, "net": 18},
        {"hour": 10, "total": 30, "solar": 16, "net": 14},
        {"hour": 11, "total": 30, "solar": 18, "net": 12},
        {"hour": 12, "total": 30, "solar": 19, "net": 11},
        {"hour": 13, "total": 30, "solar": 19, "net": 11},
        {"hour": 14, "total": 30, "solar": 17, "net": 13},
        {"hour": 15, "total": 30, "solar": 14, "net": 16},
        {"hour": 16, "total": 31, "solar": 10, "net": 21},
        {"hour": 17, "total": 33, "solar": 5, "net": 28},
        {"hour": 18, "total": 34, "solar": 1, "net": 33},
        {"hour": 19, "total": 33, "solar": 0, "net": 33},
        {"hour": 20, "total": 31, "solar": 0, "net": 31},
        {"hour": 21, "total": 29, "solar": 0, "net": 29},
        {"hour": 22, "total": 27, "solar": 0, "net": 27},
        {"hour": 23, "total": 25, "solar": 0, "net": 25}
    ]

    width = 800
    height = 360
    padding_left = 60
    padding_right = 40
    padding_top = 50
    padding_bottom = 50

    chart_width = width - padding_left - padding_right
    chart_height = height - padding_top - padding_bottom

    # Scale helpers
    def x_scale(h):
        return padding_left + h * (chart_width / 23.0)

    max_val = 40.0
    def y_scale(v):
        return padding_top + (max_val - v) * (chart_height / max_val)

    # 1. Total System Demand Curve (Slate Dashed Line)
    total_pts = [f"{x_scale(d['hour'])},{y_scale(d['total'])}" for d in fq]
    total_path = "M " + " L ".join(total_pts)

    # 2. Solar Generation Curve (Warm Gold Area and Line)
    solar_fill_pts = [f"{x_scale(0)},{y_scale(0)}"]
    solar_line_pts = []
    for d in fq:
        x = x_scale(d["hour"])
        y = y_scale(d["solar"])
        solar_fill_pts.append(f"{x},{y}")
        solar_line_pts.append(f"{x},{y}")
    solar_fill_pts.append(f"{x_scale(23)},{y_scale(0)}")
    solar_fill_path = "M " + " L ".join(solar_fill_pts) + " Z"
    solar_line_path = "M " + " L ".join(solar_line_pts)

    # 3. Residual Net Load Curve (Vibrant Royal/Light Blue Line - The Duck Curve)
    net_pts = [f"{x_scale(d['hour'])},{y_scale(d['net'])}" for d in fq]
    net_path = "M " + " L ".join(net_pts)

    # Generate Gridlines & Y-Axis labels (every 10 GW from 0 to 40)
    grid_lines = []
    for val in range(0, 41, 10):
        y = y_scale(val)
        grid_lines.append(f'<line class="grid-line" x1="{padding_left}" y1="{y}" x2="{width - padding_right}" y2="{y}" />')
        grid_lines.append(f'<text class="axis-text" x="{padding_left - 12}" y="{y + 4}" text-anchor="end">{val} GW</text>')

    # Generate X-Axis ticks and labels (every 2 hours)
    x_labels = []
    for h in range(0, 24, 2):
        x = x_scale(h)
        if h == 0:
            lbl = "12 AM"
        elif h == 12:
            lbl = "12 PM"
        elif h > 12:
            lbl = f"{h - 12} PM"
        else:
            lbl = f"{h} AM"
        x_labels.append(f'<line class="axis-tick" x1="{x}" y1="{height - padding_bottom}" x2="{x}" y2="{height - padding_bottom + 5}" stroke="var(--rule, #e5e5e5)" stroke-width="1" />')
        x_labels.append(f'<text class="axis-text" x="{x}" y="{height - padding_bottom + 20}" text-anchor="middle">{lbl}</text>')

    svg = f"""<svg viewBox="0 0 {width} {height}" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" class="duck-curve-svg">
  <style>
    .duck-curve-svg {{
      background: transparent;
      font-family: Inter, system-ui, -apple-system, sans-serif;
    }}
    .grid-line {{
      stroke: var(--rule, #e5e5e5);
      stroke-width: 1;
      stroke-opacity: 0.5;
    }}
    .axis-tick {{
      stroke: var(--rule, #e5e5e5);
      stroke-width: 1;
    }}
    .axis-text {{
      fill: var(--muted, #888888);
      font-size: 11px;
      font-weight: 500;
    }}
    .line-total {{
      stroke: #6b7280;
      stroke-width: 2.5;
      stroke-dasharray: 6 4;
      stroke-linecap: round;
      fill: none;
    }}
    .line-solar-border {{
      stroke: #f59e0b;
      stroke-width: 2;
      stroke-linecap: round;
      fill: none;
    }}
    .solar-area {{
      fill: url(#solar-gradient-natgas);
    }}
    .line-net {{
      stroke: #0284c7;
      stroke-width: 3.5;
      stroke-linecap: round;
      fill: none;
    }}
    .legend-text {{
      font-size: 12px;
      font-weight: 500;
      fill: var(--fg, #1a1a1a);
    }}
    @media (prefers-color-scheme: dark) {{
      .grid-line {{ stroke: #333333; }}
      .axis-tick {{ stroke: #333333; }}
      .axis-text {{ fill: #9ca3af; }}
      .legend-text {{ fill: #e5e7eb; }}
      .line-net {{ stroke: #38bdf8; }}
      .line-total {{ stroke: #9ca3af; }}
    }}
  </style>
  
  <defs>
    <linearGradient id="solar-gradient-natgas" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#f59e0b" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="#f59e0b" stop-opacity="0.0"/>
    </linearGradient>
  </defs>

  <!-- Y-Axis Gridlines and Labels -->
  {"".join(grid_lines)}
  
  <!-- X-Axis Line -->
  <line class="grid-line" x1="{padding_left}" y1="{height - padding_bottom}" x2="{width - padding_right}" y2="{height - padding_bottom}" stroke-dasharray="0" stroke-width="1.5" />
  
  <!-- X-Axis ticks and labels -->
  {"".join(x_labels)}

  <!-- Areas and Curves -->
  <!-- Solar Area -->
  <path class="solar-area" d="{solar_fill_path}" />
  <path class="line-solar-border" d="{solar_line_path}" />
  
  <!-- Total Demand Line -->
  <path class="line-total" d="{total_path}" />
  
  <!-- Net Load Line (The Duck Curve) -->
  <path class="line-net" d="{net_path}" />
  
  <!-- Legends -->
  <g transform="translate({padding_left + 10}, 20)">
    <!-- Total Demand -->
    <g transform="translate(0, 0)">
      <line x1="0" y1="5" x2="20" y2="5" class="line-total" stroke-width="2" />
      <text x="28" y="9" class="legend-text">Total System Demand</text>
    </g>
    <!-- Solar Generation -->
    <g transform="translate(210, 0)">
      <rect x="0" y="0" width="16" height="10" rx="2" fill="#f59e0b" fill-opacity="0.25" stroke="#f59e0b" stroke-width="1.5" />
      <text x="24" y="9" class="legend-text">Solar Generation</text>
    </g>
    <!-- Net Load -->
    <g transform="translate(380, 0)">
      <line x1="0" y1="5" x2="20" y2="5" class="line-net" stroke-width="2.5" />
      <text x="28" y="9" class="legend-text">Residual Net Load (Duck Curve)</text>
    </g>
  </g>
</svg>
"""
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

        # Extract contents as string
        body_content = main.decode_contents()

        # If this is the power-generation chapter, inject our compiled SVG chart
        if slug == "power-generation":
            recharts_placeholder = '<div class="recharts-responsive-container" style="width:100%;height:320px;min-width:0"></div>'
            if recharts_placeholder in body_content:
                svg_chart = generate_duck_curve_svg()
                # Inject SVG directly inside the responsive wrapper with beautiful borders/shadows
                injected_html = f"""<div class="offline-chart-container" style="width:100%; border: 1px solid var(--rule); border-radius: 8px; padding: 1.5rem 1rem 1rem 0.5rem; margin: 2rem 0; background: rgba(0,0,0,0.02); box-shadow: inset 0 1px 2px rgba(0,0,0,0.05);">
{svg_chart}
</div>"""
                body_content = body_content.replace(recharts_placeholder, injected_html)
                print("  -> Injected offline-friendly SVG Duck Curve chart into Power Generation chapter.")
            else:
                # Fallback replacement if container spacing/styling varies slightly on live site
                target_regex = r'<div class="recharts-responsive-container"[^>]*></div>'
                if re.search(target_regex, body_content):
                    svg_chart = generate_duck_curve_svg()
                    injected_html = f"""<div class="offline-chart-container" style="width:100%; border: 1px solid var(--rule); border-radius: 8px; padding: 1.5rem 1rem 1rem 0.5rem; margin: 2rem 0; background: rgba(0,0,0,0.02); box-shadow: inset 0 1px 2px rgba(0,0,0,0.05);">
{svg_chart}
</div>"""
                    body_content = re.sub(target_regex, injected_html, body_content)
                    print("  -> Injected offline-friendly SVG Duck Curve chart via regex.")
                else:
                    print("  ! Recharts container placeholder not found in chapter body HTML!")

        # Wrap chapter content
        chapter_html = (
            f'<section class="chapter" id="{chapter_id}">'
            f'<div class="chapter-number">Chapter {i}</div>'
            f'<h1 class="chapter-title">{title}</h1>'
            f'<div class="chapter-body">{body_content}</div>'
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
      .offline-chart-container { background: rgba(255,255,255,0.01) !important; }
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
    process_site(root / "natgas101")


if __name__ == "__main__":
    main()
