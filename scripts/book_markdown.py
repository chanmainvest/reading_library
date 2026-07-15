"""Shared helpers for reading-library book markdown files.

Book content is stored as ``books/<slug>/index.md`` with YAML front matter,
an optional preamble, and ``<!-- rl-section ... -->`` delimited sections.
The SPA splits on those markers, renders each chunk as markdown, and wraps
the result in ``<section>`` elements for scroll tracking.
"""
from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify as html_to_md

SECTION_MARKER_RE = re.compile(
    r"<!--\s*rl-section\s+([^>]+?)\s*-->",
    re.IGNORECASE,
)

# Block HTML that should survive markdown conversion verbatim.
_PROTECT_RE = re.compile(
    r"(<div[^>]*class=\"[^\"]*offline-chart-container[^\"]*\"[^>]*>[\s\S]*?</div>"
    r"|<svg[\s\S]*?</svg>)",
    re.IGNORECASE,
)


def slugify_section_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "section"


def _protect_html_blocks(html: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    counter = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal counter
        key = f"RLHTMLBLOCK{counter}"
        counter += 1
        placeholders[key] = match.group(0)
        return key

    return _PROTECT_RE.sub(repl, html), placeholders


def _restore_html_blocks(text: str, placeholders: dict[str, str]) -> str:
    for key, block in placeholders.items():
        text = text.replace(key, block)
    return text


def html_fragment_to_markdown(html: str) -> str:
    """Convert an HTML fragment to markdown, preserving offline SVG charts."""
    if not html or not html.strip():
        return ""
    protected, placeholders = _protect_html_blocks(html)
    md = html_to_md(
        protected,
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "noscript"],
    )
    md = _restore_html_blocks(md, placeholders)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def format_section_marker(
    section_id: str,
    section_class: str,
    *,
    kicker: str = "",
    title: str = "",
) -> str:
    attrs = [f'id="{section_id}"', f'class="{section_class}"']
    if kicker:
        attrs.append(f'kicker="{kicker}"')
    if title:
        attrs.append(f'title="{title}"')
    return f"<!-- rl-section {' '.join(attrs)} -->"


def build_book_markdown(
    *,
    title: str,
    author: str = "",
    source: str = "",
    preamble_md: str = "",
    sections: list[dict],
) -> str:
    """Assemble a full book markdown file.

    Each section dict accepts:
      id, class, kicker, title, body_md
    """
    lines = ["---", f"title: {title}"]
    if author:
        lines.append(f"author: {author}")
    if source:
        lines.append(f"source: {source}")
    lines.append("---")
    lines.append("")
    if preamble_md.strip():
        lines.append(preamble_md.strip())
        lines.append("")

    for section in sections:
        lines.append(
            format_section_marker(
                section["id"],
                section.get("class", "epub-section"),
                kicker=section.get("kicker", ""),
                title=section.get("title", ""),
            )
        )
        lines.append("")
        body = section.get("body_md", "").strip()
        if body:
            lines.append(body)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_book_markdown(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _extract_front_matter(doc) -> tuple[str, str, str]:
    title = ""
    author = ""
    source = ""
    h1 = doc.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    sub = doc.find(class_="book-sub")
    if sub:
        sub_text = sub.get_text(" ", strip=True)
        parts = [p.strip() for p in re.split(r"\s*[·•]\s*", sub_text) if p.strip()]
        if parts:
            author = parts[0]
        if len(parts) > 1:
            source = parts[-1]
    if not title and doc.title:
        title = doc.title.string.strip() if doc.title.string else ""
    return title, author, source


def _section_kicker(section) -> str:
    kicker = section.find(class_="section-kicker")
    if kicker and kicker.get_text(strip=True):
        return kicker.get_text(" ", strip=True)
    number = section.find(class_="chapter-number")
    if number and number.get_text(strip=True):
        return number.get_text(" ", strip=True)
    return ""


def _section_title(section) -> str:
    h1ct = section.find("h1", class_="chapter-title")
    if h1ct and h1ct.get_text(strip=True):
        return h1ct.get_text(" ", strip=True)
    h2 = section.find("h2")
    if h2 and h2.get_text(strip=True):
        return h2.get_text(" ", strip=True)
    return ""


def _section_class(section) -> str:
    classes = section.get("class", []) or []
    if "chapter" in classes:
        return "chapter"
    if "epub-section" in classes:
        return "epub-section"
    return classes[0] if classes else "epub-section"


def _toc_to_markdown(nav) -> str:
    if not nav:
        return ""
    items: list[str] = []
    for link in nav.find_all("a", href=True):
        href = link.get("href", "")
        if not href.startswith("#"):
            continue
        label = link.get_text(" ", strip=True)
        if not label:
            continue
        num = link.select_one(".num")
        prefix = f"{num.get_text(strip=True)}. " if num and num.get_text(strip=True) else ""
        if prefix and label.startswith(prefix.rstrip()):
            label = label[len(prefix.rstrip()):].lstrip(". ")
        items.append(f"- [{label.strip()}]({href})")
    if not items:
        return ""
    return "## Contents\n\n" + "\n".join(items)


def convert_html_book_to_markdown(html: str) -> str:
    """Convert a legacy single-page HTML book into the markdown format."""
    doc = BeautifulSoup(html, "lxml")
    for tag in doc.find_all(["script", "style", "link"]):
        tag.decompose()

    title, author, source = _extract_front_matter(doc)
    root = doc.body or doc

    preamble_parts: list[str] = []
    h1 = root.find("h1")
    if h1:
        preamble_parts.append(f"# {h1.get_text(' ', strip=True)}")
    sub = root.find(class_="book-sub")
    if sub:
        preamble_parts.append(f"*{sub.get_text(' ', strip=True)}*")
    toc_md = _toc_to_markdown(root.find("nav", class_="toc"))
    if toc_md:
        preamble_parts.append(toc_md)

    sections_out: list[dict] = []
    for section in root.find_all("section"):
        section_id = section.get("id") or slugify_section_id(_section_title(section))
        section_class = _section_class(section)
        kicker = _section_kicker(section)
        stitle = _section_title(section)

        work = BeautifulSoup(str(section), "lxml").find("section")
        if not work:
            continue
        for chrome in work.find_all(class_=["section-kicker", "chapter-number"]):
            chrome.decompose()
        for heading in work.find_all(["h1", "h2"], class_=["chapter-title"]):
            heading.decompose()
        for heading in work.find_all("h2"):
            if heading.get_text(strip=True) == stitle:
                heading.decompose()
                break

        body_html = ""
        body_div = work.find(class_="chapter-body")
        if body_div:
            body_html = body_div.decode_contents()
        else:
            body_html = work.decode_contents()

        body_md = html_fragment_to_markdown(body_html)
        if stitle and not body_md.lstrip().startswith("#"):
            body_md = f"## {stitle}\n\n{body_md}".strip()

        sections_out.append(
            {
                "id": section_id,
                "class": section_class,
                "kicker": kicker,
                "title": stitle,
                "body_md": body_md,
            }
        )

    return build_book_markdown(
        title=title or "Untitled",
        author=author,
        source=source,
        preamble_md="\n\n".join(preamble_parts),
        sections=sections_out,
    )


def parse_sections_from_markdown(text: str) -> list[dict]:
    """Parse rl-section markers from markdown (for chatbot indexing)."""
    sections: list[dict] = []
    parts = SECTION_MARKER_RE.split(text)
    # parts[0] is preamble/front matter stripped by caller
    idx = 1
    while idx + 1 < len(parts):
        attr_str = parts[idx]
        body = parts[idx + 1]
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', attr_str))
        plain = re.sub(r"<[^>]+>", " ", body)
        plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", plain)
        plain = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "", plain)
        plain = re.sub(r"[#*_>`|]", "", plain)
        plain = re.sub(r"\s+", " ", plain).strip()
        sections.append(
            {
                "sectionId": attrs.get("id", ""),
                "sectionTitle": attrs.get("title", ""),
                "text": plain,
            }
        )
        idx += 2
    return sections
