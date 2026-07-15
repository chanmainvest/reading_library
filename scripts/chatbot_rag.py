"""Cross-book RAG retrieval for the reading-library chatbot CLI.

Loads the same assets/chatbot_chunks.json and assets/chatbot_embeddings.bin
artifacts as the browser chatbot, embeds queries via scripts/embed_query.mjs
(embeddinggemma-300m ONNX q8), and returns top-K excerpts with scope filters
and literal-phrase fallback matching web_assets/chatbot.js.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CHUNKS_PATH = ROOT / "assets" / "chatbot_chunks.json"
EMBEDDINGS_PATH = ROOT / "assets" / "chatbot_embeddings.bin"
EMBED_QUERY_SCRIPT = Path(__file__).resolve().parent / "embed_query.mjs"
EMBED_BIN_MAGIC = 0x42454D43  # "CMEB"
EMBED_BIN_VERSION = 2
# Must match web_assets/chatbot.js EMBED_MODEL_ID and embed_query.mjs. The bin's
# header (v2) embeds the model id that produced its vectors; if it differs from
# this, the stored vectors are from a different model and retrieval is garbage.
EMBED_MODEL_ID = "onnx-community/embeddinggemma-300m-ONNX"
EMBED_DIM = 768
TOP_K = 5
COVER_PATH_RE = re.compile(r"(?:cover|calibre_cover|cover_image|cvi)", re.I)

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "chatbot CLI requires numpy. Install with: uv sync --extra cli"
    ) from exc


@dataclass(frozen=True)
class Chunk:
    id: int
    lang: str
    url: str
    title: str
    author: str
    section_id: str
    section_title: str
    text: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Chunk:
        return cls(
            id=int(raw["id"]),
            lang=str(raw.get("lang", "en")),
            url=str(raw.get("url", "")),
            title=str(raw.get("title", "")),
            author=str(raw.get("author", "")),
            section_id=str(raw.get("sectionId", "")),
            section_title=str(raw.get("sectionTitle", "")),
            text=str(raw.get("text", "")),
        )


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float


def compute_chunks_hash(chunks: list[Chunk]) -> bytes:
    ordered = sorted(chunks, key=lambda c: c.id)
    digest = hashlib.sha256()
    for chunk in ordered:
        digest.update(chunk.text.encode("utf-8"))
    return digest.digest()


def load_chunks(path: Path = CHUNKS_PATH) -> list[Chunk]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing {path}. Run: uv run python scripts/build_chatbot_index.py"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Chunk.from_dict(item) for item in raw]


def load_embeddings(
    chunks: list[Chunk],
    path: Path = EMBEDDINGS_PATH,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (ids, vectors) where vectors shape is (n, 768)."""
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing {path}. Run: cd scripts && npm install && node build_chatbot_embeddings.mjs"
        )
    data = path.read_bytes()
    if len(data) < 52:
        raise ValueError(f"embeddings bin too small: {path}")

    magic, version, count, dim, dtype = struct.unpack_from("<5I", data, 0)
    if magic != EMBED_BIN_MAGIC:
        raise ValueError(f"bad embeddings magic in {path}")
    if version != EMBED_BIN_VERSION:
        raise ValueError(
            f"unsupported embeddings version {version} (expected {EMBED_BIN_VERSION}). "
            "v1 bins carry no model id and cannot be validated — rebuild with "
            "scripts/build_chatbot_embeddings.mjs."
        )
    if dim != EMBED_DIM:
        raise ValueError(f"expected dim {EMBED_DIM}, got {dim}")
    if dtype != 1:
        raise ValueError(f"unsupported embeddings dtype {dtype}")

    bin_hash = data[20:52]
    expected = compute_chunks_hash(chunks)
    if bin_hash != expected:
        raise ValueError(
            "chatbot_embeddings.bin is stale (chunk text changed). "
            "Rebuild with build_chatbot_index.py then build_chatbot_embeddings.mjs"
        )

    # v2 header: u32 model-id length + that many UTF-8 bytes (zero-padded to a
    # 4-byte boundary on write), immediately after the 32-byte chunks hash. The
    # bin's vectors are only valid if produced by the same model used for queries.
    off = 52
    (model_id_len,) = struct.unpack_from("<I", data, off)
    off += 4
    bin_model_id = data[off : off + model_id_len].decode("utf-8", errors="replace")
    # Advance past the padded model-id region (round length up to a 4-byte multiple).
    off += (model_id_len + 3) & ~3
    if bin_model_id != EMBED_MODEL_ID:
        raise ValueError(
            f"chatbot_embeddings.bin was built with model '{bin_model_id}' but "
            f"this build embeds queries with '{EMBED_MODEL_ID}'. The stored "
            "vectors are from a different model, so cosine retrieval is garbage. "
            "Rebuild with: cd scripts && node build_chatbot_embeddings.mjs --clean"
        )

    # Single-language library: one (offset, count) pair at the current offset.
    offset, lang_count = struct.unpack_from("<2I", data, off)
    if lang_count != count:
        raise ValueError(f"lang table count {lang_count} != header count {count}")

    ids_off = off + 8
    ids = np.frombuffer(data, dtype=np.uint32, count=count, offset=ids_off)
    vec_off = ids_off + count * 4
    vectors = np.frombuffer(data, dtype=np.float32, count=count * dim, offset=vec_off)
    vectors = vectors.reshape(count, dim)
    return ids, vectors


def literal_phrase_for_query(question: str) -> str:
    phrase = question.strip().lower().rstrip("?!.")
    phrase = re.sub(
        r"^(what is|what are|what's|define|explain|tell me about)\s+",
        "",
        phrase,
        flags=re.I,
    )
    phrase = re.sub(r"^the\s+", "", phrase, flags=re.I)
    return phrase.strip()


def chunk_passes_filter(
    chunk: Chunk,
    *,
    book_url: str | None,
    section_id: str | None,
) -> bool:
    if book_url and chunk.url != book_url:
        return False
    if section_id is not None and chunk.section_id != section_id:
        return False
    return True


def literal_phrase_search(
    question: str,
    chunks_by_id: dict[int, Chunk],
    ids: np.ndarray,
    *,
    book_url: str | None,
    section_id: str | None,
    k: int,
) -> list[RetrievedChunk]:
    phrase = literal_phrase_for_query(question)
    if len(phrase) < 4:
        return []
    hits: list[RetrievedChunk] = []
    per_url: dict[str, int] = {}
    for chunk_id in ids:
        chunk = chunks_by_id.get(int(chunk_id))
        if not chunk:
            continue
        if not chunk_passes_filter(chunk, book_url=book_url, section_id=section_id):
            continue
        if phrase not in chunk.text.lower():
            continue
        seen = per_url.get(chunk.url, 0)
        if seen >= 2:
            continue
        per_url[chunk.url] = seen + 1
        hits.append(RetrievedChunk(chunk=chunk, score=1.0))
        if len(hits) >= k:
            break
    return hits


def cosine_top_k(
    query_vec: np.ndarray,
    ids: np.ndarray,
    vectors: np.ndarray,
    chunks_by_id: dict[int, Chunk],
    k: int,
    *,
    book_url: str | None,
    section_id: str | None,
) -> list[RetrievedChunk]:
    scores = vectors @ query_vec
    order = np.argsort(scores)[::-1]
    picked: list[RetrievedChunk] = []
    per_url: dict[str, int] = {}
    for idx in order:
        chunk_id = int(ids[idx])
        chunk = chunks_by_id.get(chunk_id)
        if not chunk:
            continue
        if not chunk_passes_filter(chunk, book_url=book_url, section_id=section_id):
            continue
        seen = per_url.get(chunk.url, 0)
        if seen >= 2:
            continue
        per_url[chunk.url] = seen + 1
        picked.append(RetrievedChunk(chunk=chunk, score=float(scores[idx])))
        if len(picked) >= k:
            break
    return picked


def merge_results(
    primary: list[RetrievedChunk],
    secondary: list[RetrievedChunk],
    k: int,
) -> list[RetrievedChunk]:
    seen: set[int] = set()
    merged: list[RetrievedChunk] = []
    for item in [*primary, *secondary]:
        if item.chunk.id in seen:
            continue
        seen.add(item.chunk.id)
        merged.append(item)
        if len(merged) >= k:
            break
    return merged


def embed_query(text: str, *, device: str = "cpu") -> np.ndarray:
    scripts_dir = EMBED_QUERY_SCRIPT.parent
    cmd = ["node", str(EMBED_QUERY_SCRIPT), "--device", device, text]
    try:
        proc = subprocess.run(
            cmd,
            cwd=scripts_dir,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Node.js is required to embed queries. Install Node and run: "
            "cd scripts && npm install"
        ) from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"embed_query.mjs failed: {err or proc.returncode}")
    vec = np.frombuffer(proc.stdout, dtype=np.float32)
    if vec.size != EMBED_DIM:
        raise RuntimeError(f"embed_query returned {vec.size} floats, expected {EMBED_DIM}")
    return vec


class RagIndex:
    def __init__(
        self,
        chunks: list[Chunk] | None = None,
        *,
        ids: np.ndarray | None = None,
        vectors: np.ndarray | None = None,
    ) -> None:
        self.chunks = chunks or load_chunks()
        self.chunks_by_id = {c.id: c for c in self.chunks}
        if ids is None or vectors is None:
            ids, vectors = load_embeddings(self.chunks)
        self.ids = ids
        self.vectors = vectors

    def book_url_for_slug(self, slug: str) -> str:
        return f"books/{slug}/index.md"

    def retrieve(
        self,
        question: str,
        *,
        scope: str = "all",
        book_slug: str | None = None,
        section_id: str | None = None,
        top_k: int = TOP_K,
        embed_device: str = "cpu",
    ) -> list[RetrievedChunk]:
        query_vec = embed_query(question, device=embed_device)
        book_url = None
        sec_id = None
        if scope in ("book", "chapter") and book_slug:
            book_url = self.book_url_for_slug(book_slug)
            if scope == "chapter":
                sec_id = section_id or ""
        elif scope in ("book", "chapter") and not book_slug:
            scope = "all"

        results = merge_results(
            literal_phrase_search(
                question,
                self.chunks_by_id,
                self.ids,
                book_url=book_url,
                section_id=sec_id if scope == "chapter" else None,
                k=top_k,
            ),
            cosine_top_k(
                query_vec,
                self.ids,
                self.vectors,
                self.chunks_by_id,
                top_k,
                book_url=book_url,
                section_id=sec_id if scope == "chapter" else None,
            ),
            top_k,
        )
        if scope in ("book", "chapter") and book_url and len(results) < top_k:
            global_cosine = cosine_top_k(
                query_vec,
                self.ids,
                self.vectors,
                self.chunks_by_id,
                top_k,
                book_url=None,
                section_id=None,
            )
            global_literal = literal_phrase_search(
                question,
                self.chunks_by_id,
                self.ids,
                book_url=None,
                section_id=None,
                k=top_k,
            )
            results = merge_results(results, global_cosine, top_k)
            results = merge_results(results, global_literal, top_k)
        return results


def load_section_text(book_slug: str, section_id: str) -> str:
    """Plain text for a book section (for chapter scope priority context)."""
    from build_chatbot_index import collect_sections, markdown_to_plain

    md_path = ROOT / "books" / book_slug / "index.md"
    if not md_path.is_file():
        return ""
    sections = collect_sections(md_path.read_text(encoding="utf-8"))
    for sec in sections:
        if sec["sectionId"] == section_id:
            return sec["text"]
    return ""


def build_system_prompt(
    question: str,
    retrieved: list[RetrievedChunk],
    *,
    scope: str,
    book_slug: str | None,
    section_id: str | None,
    section_text: str = "",
) -> str:
    has_book = bool(book_slug)
    if scope == "chapter" and book_slug and section_id and not section_text:
        section_text = load_section_text(book_slug, section_id)

    scope_desc = (
        "the CURRENT CHAPTER section"
        if scope == "chapter" and has_book
        else "the CURRENT BOOK"
        if scope == "book" and has_book
        else "RELEVANT EXCERPTS from across the library"
    )
    lines = [
        "You are an assistant for the Chanma Invest reading library, a curated",
        "collection of books on investing, financial history, banking, trading,",
        "commodities, and markets. Answer the user's question using ONLY the",
        "library material provided below.",
        f"Prefer {scope_desc}.",
        "",
    ]
    if has_book:
        lines.extend(
            [
                "The material the user is reading is under CURRENT BOOK below.",
                "Short instructions like summarize or explain refer to that text.",
            ]
        )
    else:
        lines.extend(
            [
                "No single book is selected. Answer from RELEVANT EXCERPTS and",
                'cite book titles in brackets (e.g. "[Book: Antifragile] …").',
            ]
        )
    lines.extend(
        [
            "If excerpts contain relevant material, use them — do not claim the",
            "library lacks an answer when excerpts are provided.",
            "Keep answers concise (~200 words) unless more detail is requested.",
            "",
        ]
    )
    if has_book:
        lines.append("CURRENT BOOK (priority):")
        lines.append(section_text or "(no section text loaded)")
        lines.append("")
    if retrieved:
        header = (
            f"RELEVANT EXCERPTS (FROM THIS {scope.upper()}):"
            if has_book and scope in ("book", "chapter")
            else "RELEVANT EXCERPTS FROM THE LIBRARY:"
        )
        lines.append(header)
        for item in retrieved:
            lines.append(f"[{item.chunk.title}] {item.chunk.text}")
    return "\n".join(lines)
