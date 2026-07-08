"""Convert a local AZW3 (Kindle KF8) file into one published, single-page HTML file.

AZW3 is Amazon's proprietary container. It cannot be parsed directly with the
same zipfile/E-PUB toolchain used by ``convert_epub.py``, so this script front-ends
the conversion with Calibre's ``ebook-convert`` CLI to transcode AZW3 -> EPUB into
a temporary directory, then reuses :func:`convert_epub.convert_epub` to compile the
final standalone ``index.html`` exactly like any other EPUB.

This converter is for files that the repository owner has rights to convert and
publish in this personal reading-library repository. It does not bypass DRM; if
the source is DRMed, Calibre's ``ebook-convert`` will fail and this script surfaces
that error.

Prerequisites:
    * Calibre installed (provides ``ebook-convert`` / ``ebook-convert.exe``).
      Download from https://calibre-ebook.com/download.

Usage:
    py scripts/convert_azw3.py "E:\\ebook\\Calibre Library\\Author\\Book (1)\\Book - Author.azw3" --slug book-slug

    # AZW / KFX / MOBI inputs work the same way (any format Calibre understands):
    py scripts/convert_azw3.py "path\\to\\book.mobi" --slug book-slug
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Reuse the single source of truth for EPUB -> HTML compilation.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from convert_epub import convert_epub  # noqa: E402


DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "books"


def find_ebook_convert() -> str:
    """Locate the Calibre ``ebook-convert`` executable on PATH or in common install dirs."""
    found = shutil.which("ebook-convert") or shutil.which("ebook-convert.exe")
    if found:
        return found
    # Fall back to the standard Windows install location.
    for candidate in (
        r"C:\Program Files\Calibre2\ebook-convert.exe",
        r"C:\Program Files (x86)\Calibre2\ebook-convert.exe",
        r"C:\Program Files\Calibre\ebook-convert.exe",
        "/usr/bin/ebook-convert",
        "/usr/local/bin/ebook-convert",
        "/opt/homebrew/bin/ebook-convert",
    ):
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(
        "Calibre 'ebook-convert' was not found. Install Calibre from "
        "https://calibre-ebook.com/download and ensure it is on your PATH."
    )


def transcode_to_epub(source: Path, dest_epub: Path, ebook_convert: str) -> Path:
    """Transcode any Calibre-supported ebook format (AZW3/MOBI/AZW/KFX/...) to EPUB."""
    cmd = [ebook_convert, str(source), str(dest_epub)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not dest_epub.exists():
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"Calibre failed to transcode {source.name} to EPUB.\n"
            f"This most often means the source is DRMed. Calibre output:\n{details}"
        )
    return dest_epub


def convert_azw3(
    source: Path,
    output_root: Path,
    slug: str | None = None,
    title: str | None = None,
    creator: str | None = None,
    keep_epub: Path | None = None,
) -> Path:
    """Convert an AZW3 (or MOBI/AZW) source into a published single-page HTML book.

    Parameters mirror :func:`convert_epub.convert_epub`. The intermediate EPUB is
    written to a temp dir and discarded unless ``keep_epub`` is given.
    """
    if not source.exists():
        raise FileNotFoundError(source)

    ebook_convert = find_ebook_convert()
    tmp_dir = Path(tempfile.mkdtemp(prefix="azw3conv_"))
    try:
        intermediate_epub = tmp_dir / f"{source.stem or 'book'}.epub"
        print(f"[1/2] Transcoding {source.name} -> EPUB via Calibre...", file=sys.stderr)
        transcode_to_epub(source, intermediate_epub, ebook_convert)

        if keep_epub is not None:
            keep_epub.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(intermediate_epub, keep_epub)
            print(f"      Intermediate EPUB kept at: {keep_epub}", file=sys.stderr)

        print("[2/2] Compiling EPUB -> single-page HTML...", file=sys.stderr)
        html_path = convert_epub(
            intermediate_epub, output_root, slug, title_override=title, creator_override=creator
        )
        return html_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a local AZW3/MOBI/AZW ebook to published single-page HTML.",
        epilog="Requires Calibre (ebook-convert) to be installed.",
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Path to a local AZW3/MOBI/AZW/KFX file (any format Calibre can read).",
    )
    parser.add_argument("--slug", help="Output book slug. Defaults to a slugified source title.")
    parser.add_argument("--title", help="Display title override for the generated page.")
    parser.add_argument("--creator", help="Display creator override for the generated page.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Published output directory. Defaults to books/.",
    )
    parser.add_argument(
        "--keep-epub",
        type=Path,
        default=None,
        help="Optional path to also save the intermediate EPUB (e.g. for archival).",
    )
    args = parser.parse_args()

    html_path = convert_azw3(
        args.source,
        args.output_root,
        args.slug,
        args.title,
        args.creator,
        args.keep_epub,
    )
    print(html_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
