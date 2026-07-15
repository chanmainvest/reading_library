"""Convert a local EPUB into one published markdown book file.

This converter is for EPUB files that the repository owner has rights to
convert and publish in this personal reading-library repository. It does not
bypass DRM.

Usage:
    py scripts/convert_epub.py "E:\\ebook\\Books\\path\\book.epub" --slug book-slug
"""
from __future__ import annotations

import argparse
import hashlib
import posixpath
import re
import sys
import warnings
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from book_markdown import build_book_markdown, html_fragment_to_markdown, write_book_markdown


warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "book"


def namespace(tag: str) -> str:
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def read_text(epub_zip: zipfile.ZipFile, internal_path: str) -> str:
    return epub_zip.read(internal_path).decode("utf-8", errors="replace")


def resolve_path(base_dir: str, href: str) -> str:
    parsed = urlparse(href)
    raw_path = unquote(parsed.path)
    if not raw_path:
        return ""
    return posixpath.normpath(posixpath.join(base_dir, raw_path))


def find_opf_path(epub_zip: zipfile.ZipFile) -> str:
    container_xml = read_text(epub_zip, "META-INF/container.xml")
    root = ET.fromstring(container_xml)
    xml_ns = namespace(root.tag)
    rootfile_path = f".//{{{xml_ns}}}rootfile" if xml_ns else ".//rootfile"
    rootfile = root.find(rootfile_path)
    if rootfile is None or not rootfile.get("full-path"):
        raise ValueError("EPUB container.xml does not declare a package document")
    return rootfile.get("full-path", "")


def parse_package(epub_zip: zipfile.ZipFile, opf_path: str):
    opf_xml = read_text(epub_zip, opf_path)
    root = ET.fromstring(opf_xml)
    opf_ns = namespace(root.tag)
    dc_ns = "http://purl.org/dc/elements/1.1/"

    def find(path: str):
        return root.find(path.format(opf=opf_ns, dc=dc_ns))

    title_node = find(".//{{{dc}}}title")
    creator_node = find(".//{{{dc}}}creator")
    title = title_node.text.strip() if title_node is not None and title_node.text else "Untitled EPUB"
    creator = creator_node.text.strip() if creator_node is not None and creator_node.text else "Unknown author"

    manifest = {}
    manifest_path = f".//{{{opf_ns}}}manifest/{{{opf_ns}}}item" if opf_ns else ".//manifest/item"
    for item in root.findall(manifest_path):
        item_id = item.get("id")
        if not item_id:
            continue
        manifest[item_id] = {
            "href": item.get("href", ""),
            "media_type": item.get("media-type", ""),
        }

    spine_ids = []
    spine_path = f".//{{{opf_ns}}}spine/{{{opf_ns}}}itemref" if opf_ns else ".//spine/itemref"
    for itemref in root.findall(spine_path):
        item_id = itemref.get("idref")
        if item_id:
            spine_ids.append(item_id)

    return title, creator, manifest, spine_ids


def unique_asset_name(internal_path: str, content: bytes, used_names: set[str]) -> str:
    source_name = Path(internal_path).name or hashlib.sha1(internal_path.encode()).hexdigest()[:12]
    clean_name = re.sub(r"[^A-Za-z0-9._-]", "_", source_name)
    name_stem = Path(clean_name).stem or "asset"
    suffix = Path(clean_name).suffix
    short_hash = hashlib.sha1(content).hexdigest()[:8]
    candidate = f"{name_stem}-{short_hash}{suffix}"
    counter = 2
    while candidate in used_names:
        candidate = f"{name_stem}-{short_hash}-{counter}{suffix}"
        counter += 1
    used_names.add(candidate)
    return candidate


def convert_epub(
    epub_path: Path,
    output_root: Path,
    slug: str | None = None,
    title_override: str | None = None,
    creator_override: str | None = None,
) -> Path:
    if not epub_path.exists():
        raise FileNotFoundError(epub_path)

    with zipfile.ZipFile(epub_path) as epub_zip:
        opf_path = find_opf_path(epub_zip)
        opf_dir = posixpath.dirname(opf_path)
        title, creator, manifest, spine_ids = parse_package(epub_zip, opf_path)
        display_title = title_override or title
        display_creator = creator_override or creator

        output_dir = output_root / (slug or slugify(display_title))
        asset_dir = output_dir / "assets"
        if asset_dir.exists():
            for old_asset in asset_dir.iterdir():
                if old_asset.is_file():
                    old_asset.unlink()
        asset_dir.mkdir(parents=True, exist_ok=True)

        used_asset_names: set[str] = set()
        asset_map: dict[str, str] = {}
        for manifest_item in manifest.values():
            media_type = manifest_item["media_type"]
            if not media_type.startswith("image/"):
                continue
            internal_path = resolve_path(opf_dir, manifest_item["href"])
            if internal_path not in epub_zip.namelist():
                continue
            content = epub_zip.read(internal_path)
            asset_name = unique_asset_name(internal_path, content, used_asset_names)
            (asset_dir / asset_name).write_bytes(content)
            asset_map[internal_path] = f"assets/{asset_name}"

        spine_paths: list[str] = []
        for spine_id in spine_ids:
            manifest_item = manifest.get(spine_id)
            if not manifest_item:
                continue
            media_type = manifest_item["media_type"]
            if media_type not in {"application/xhtml+xml", "text/html"}:
                continue
            internal_path = resolve_path(opf_dir, manifest_item["href"])
            if internal_path in epub_zip.namelist():
                spine_paths.append(internal_path)

        spine_anchor_map = {path: f"section-{index + 1}" for index, path in enumerate(spine_paths)}
        sections: list[dict] = []

        for index, internal_path in enumerate(spine_paths, 1):
            document = BeautifulSoup(read_text(epub_zip, internal_path), "lxml")
            for removable in document.find_all(["script", "style", "noscript"]):
                removable.decompose()

            document_dir = posixpath.dirname(internal_path)
            for image_node in document.find_all(["img", "image"]):
                for source_attr in ("src", "href", "xlink:href"):
                    source = image_node.get(source_attr)
                    if not source:
                        continue
                    asset_path = resolve_path(document_dir, source)
                    if asset_path in asset_map:
                        image_node[source_attr] = asset_map[asset_path]
                        break
                for attr in ("srcset", "data-src", "data-srcset"):
                    if image_node.has_attr(attr):
                        del image_node[attr]

            for link in document.find_all("a", href=True):
                href = link.get("href", "")
                parsed = urlparse(href)
                if parsed.scheme or href.startswith("#"):
                    continue
                target_path = resolve_path(document_dir, href)
                target_anchor = spine_anchor_map.get(target_path)
                if target_anchor:
                    link["href"] = f"#{target_anchor}"

            body = document.body or document
            heading = body.find(["h1", "h2", "h3"])
            section_title = heading.get_text(" ", strip=True) if heading else f"Section {index}"
            body_md = html_fragment_to_markdown(body.decode_contents())
            if section_title and not body_md.lstrip().startswith("#"):
                body_md = f"## {section_title}\n\n{body_md}".strip()
            sections.append(
                {
                    "id": f"section-{index}",
                    "class": "epub-section",
                    "kicker": f"Section {index}",
                    "title": section_title,
                    "body_md": body_md,
                }
            )

        preamble = (
            f"# {display_title}\n\n"
            f"*{display_creator} · single-page EPUB conversion*"
        )
        page = build_book_markdown(
            title=display_title,
            author=display_creator,
            source="single-page EPUB conversion",
            preamble_md=preamble,
            sections=sections,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        return write_book_markdown(output_dir / "index.md", page)


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert a local EPUB to published markdown.")
    parser.add_argument("epub", type=Path, help="Path to a local EPUB file")
    parser.add_argument("--slug", help="Output book slug. Defaults to a slugified EPUB title.")
    parser.add_argument("--title", help="Display title override for the generated page.")
    parser.add_argument("--creator", help="Display creator override for the generated page.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "books",
        help="Published output directory. Defaults to books/.",
    )
    args = parser.parse_args()

    output_md = convert_epub(args.epub, args.output_root, args.slug, args.title, args.creator)
    print(output_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())