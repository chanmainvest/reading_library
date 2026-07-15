# Agent Instructions & Guidelines

Welcome, AI Agent! This file outlines the architecture, standards, and rules of engagement for interacting with this repository. Please read this file in full before performing any editing, scripting, or automated operations.

---

## 📂 Repository Architecture

```
chanmainvest/reading_library/
├── README.md               # Human-facing repository documentation
├── CONTRIBUTING.md         # Process guidelines (PR flows)
├── AGENTS.md               # This file (Agent instructions)
├── index.html              # SPA shell: hash router + home/reader views (GitHub Pages index)
├── books.json              # Dynamic JSON catalog database (source of truth for catalog cards)
├── pyproject.toml          # Python deps (bs4, lxml, markdownify) for uv run
├── books/                  # Published book content (fetched by the SPA at runtime)
│   ├── catalog.json        # Public catalog/status metadata for requested books
│   ├── oil101/             # Offline mirrored book directory
│   │   ├── index.md        # Compiled Oil 101 book (content source for the SPA)
│   │   └── images/         # Scraped and local-hashed high-res oil images
│   ├── natgas101/          # Offline mirrored book directory
│   │   ├── index.md        # Compiled NatGas 101 book (with SVG chart)
│   │   └── images/         # Scraped and local-hashed high-res gas images
│   └── <book-slug>/        # Rights-approved EPUB conversion output
│       ├── index.md        # Single-file converted EPUB (content source for the SPA)
│       └── assets/         # Locally extracted EPUB images
├── assets/                 # Served SPA + chatbot assets (copied from web_assets/)
│   ├── spa.css             # SPA shell + reader typography (top bar, reader viewport)
│   ├── spa.js              # Hash router, markdown loader, active-section tracker (window.RL)
│   ├── vendor/
│   │   └── marked.esm.js   # Markdown renderer (vendored from scripts/node_modules/marked)
│   ├── chatbot.css         # Self-contained dark palette for the AI assistant
│   ├── chatbot.js          # On-device Gemma 4 + embeddinggemma RAG assistant (scope toggle)
│   ├── chatbot_chunks.json # Static RAG chunk index (per-section, with sectionId/sectionTitle)
│   └── chatbot_embeddings.bin  # Prebuilt embedding cache (~100 MB, built by Node on GPU)
├── web_assets/             # Source of truth for SPA/chatbot CSS/JS (copy to assets/ to publish)
├── scripts/                # Reusable automation scripts
│   ├── book_markdown.py    # Shared markdown build/convert helpers (section markers, HTML→MD)
│   ├── mirror_natgas101.py # Dedicated script for NatGas 101 scraping
│   ├── mirror_oil101.py    # Dedicated script for Oil 101 scraping
│   ├── convert_epub.py     # EPUB-to-markdown converter for rights-approved books
│   ├── convert_azw3.py     # AZW3/MOBI/AZW/DJVU-to-markdown (Calibre front-end + convert_epub)
│   ├── convert_html_books_to_markdown.py  # One-time migrator: legacy index.html → index.md
│   ├── sync_books_catalog.py       # Refresh coverImage paths + hrefs in books.json
│   ├── build_chatbot_index.py      # Per-section chunker -> assets/chatbot_chunks.json
│   ├── build_chatbot_embeddings.mjs  # Embed chunks -> assets/chatbot_embeddings.bin (GPU/DML)
│   ├── embed_query.mjs             # Single-query embedder for the CLI (same model as browser)
│   ├── gemma_models.mjs            # Gemma E2B/E4B registry + device auto-detect
│   ├── gemma_chat.mjs              # Local Gemma 4 inference for the CLI
│   ├── chatbot_rag.py              # CLI RAG loader + retrieval (chunks + embeddings bin)
│   ├── chatbot_gemma.py            # Python wrapper for gemma_chat.mjs --server
│   ├── chatbot_cli.py              # Terminal chatbot (local Gemma + RAG)
│   ├── package.json        # Node deps (marked, @huggingface/transformers v4)
│   └── wire_chatbot.py     # Wire SPA+chatbot into root index.html
```

---

## 💻 Terminal Chatbot CLI

A command-line assistant shares the browser chatbot's **local Gemma 4 models** and **RAG index** (`chatbot_chunks.json` + `chatbot_embeddings.bin`). No API keys or external LLM servers — inference runs in Node via `@huggingface/transformers`.

### Setup

```bash
cd scripts && npm install   # once — transformers.js + onnxruntime-node
uv sync --extra cli         # numpy
```

### Models

| `--model` | Repo | Effective params | Download |
|-----------|------|------------------|----------|
| `e2b` (default) | `onnx-community/gemma-4-E2B-it-ONNX` | ~2.3B | ~3.1 GB |
| `e4b` | `onnx-community/gemma-4-E4B-it-ONNX` | ~4.5B | ~6 GB |

Both load text-only ONNX sessions (`embed_tokens` + `decoder_model_merged`) via `Gemma4ForCausalLM`, skipping audio/vision encoders.

### Devices

- `--device auto` (default): WebGPU when `nvidia-smi` reports an NVIDIA GPU; else CPU. Matches the browser chatbot backend.
- `--device webgpu|cpu|dml`: force a backend. Prefer WebGPU over DirectML for Gemma 4 — DML often returns garbled/empty output.
- GPU answers that are empty or very short trigger an automatic CPU retry.
- `--embed-device` defaults to `cpu` for query embedding (WebGPU can crash for the small embedder in Node).

### Commands

```bash
# Interactive REPL (E2B, auto GPU)
uv run --extra cli python scripts/chatbot_cli.py

# One-shot
uv run --extra cli python scripts/chatbot_cli.py -q "What is a black swan?"

# Larger model
uv run --extra cli python scripts/chatbot_cli.py --model e4b -q "Explain contango"

# Scoped retrieval
uv run --extra cli python scripts/chatbot_cli.py --book antifragile --scope book
uv run --extra cli python scripts/chatbot_cli.py --book antifragile --section section-10 --scope chapter

# Debug retrieval / force CPU
uv run --extra cli python scripts/chatbot_cli.py --show-context -q "OPEC spare capacity"
uv run --extra cli python scripts/chatbot_cli.py --device cpu -q "What is contango?"
```

REPL slash commands: `/scope`, `/book`, `/section`, `/show-context`, `/quit`.

### Scripts

| Script | Role |
|--------|------|
| `scripts/chatbot_cli.py` | Python REPL + RAG orchestration |
| `scripts/chatbot_rag.py` | Chunk/bin loader + retrieval |
| `scripts/embed_query.mjs` | Query embedding (embeddinggemma-300m q8, CPU) |
| `scripts/gemma_models.mjs` | E2B/E4B registry + device auto-detect |
| `scripts/gemma_chat.mjs` | Local Gemma 4 generation (q4f16, long-lived `--server` worker) |
| `scripts/chatbot_gemma.py` | Python wrapper for `gemma_chat.mjs --server` |

Regenerate RAG artifacts after book changes (same order as the browser chatbot). Agents should read [`.cursor/skills/chatbot-cli/SKILL.md`](.cursor/skills/chatbot-cli/SKILL.md) before running or extending the CLI.

---

## 🤖 On-Device AI Assistant

The library is a **single-page app** (`assets/spa.js`): the root `index.html`
hosts a hash router (`#/` = home, `#/books/<slug>` = reader). Book content is
fetched as `books/<slug>/index.md`, parsed, rendered to HTML with **marked.js**,
and injected into `#reader-content` — the page never reloads, so the chatbot
module stays loaded across book switches and the LLM pipeline, embeddings, and
conversation history persist in memory.

- **Top bar**: shows a home icon, the book title, and the active chapter
  number + title (tracked via `IntersectionObserver` on `<section>` elements
  as the user scrolls). Exposed to the chatbot via `window.RL.getState()`.
- **LLM**: Gemma 4 E2B (~3.1 GB, q4f16, cached in IndexedDB after first load) via transformers.js v4 + WebGPU.
- **Embedding model**: `onnx-community/embeddinggemma-300m-ONNX` (~300 MB q8) for cross-book retrieval, run on WASM (EmbeddingGemma can't compile a WebGPU pipeline and WASM avoids contending with Gemma for the GPU).
- **Cross-book RAG**: a per-section chunk index (`assets/chatbot_chunks.json`, each chunk carrying `sectionId`/`sectionTitle`) plus a prebuilt embedding cache (`assets/chatbot_embeddings.bin`) let the assistant pull relevant excerpts. The browser recomputes a SHA-256 over the chunk texts and refuses a stale bin, falling back to per-chunk embedding. The bin (format v2) also embeds the **embedding model id** in its header; every loader (browser, CLI, build re-run) compares it to its own query embedder's model id and refuses the bin with a clear error if they differ — a chunks-text hash can't detect a model swap, which previously let a stale checkpoint leak wrong-model vectors into a fresh-looking bin. The resume checkpoint (`assets/.embed_cache/<lang>__<model>.bin`) is likewise keyed by model id, so changing the embedding model auto-invalidates the cache.
- **Scope toggle**: a segmented control in the chat panel — **This chapter** / **This book** / **All books** — controls which slice of the corpus RAG retrieval searches. The current section's text is always injected as priority context; the toggle controls the *supporting excerpts* layer. "This chapter"/"This book" are disabled on the home view (no book open). Persisted in `localStorage`.

### Regeneration order (after adding or converting books)
The chunk index and embedding cache are derived artifacts — regenerate them whenever book content changes:

```bash
# 1. Rebuild the per-section chunk index from every book's index.md
uv run python scripts/build_chatbot_index.py

# 2. Rebuild the embedding cache (Node + @huggingface/transformers v4).
#    Auto-selects DirectML (GPU) on Windows + NVIDIA, else CPU. Override with
#    --device cpu|dml|cuda. The q8 weights yield identical vectors on any
#    device, so a GPU build and a CPU build are interchangeable.
cd scripts && npm install && node build_chatbot_embeddings.mjs

# 3. Sync source assets to the served location and (re)wire the SPA shell
cp web_assets/spa.css web_assets/spa.js web_assets/chatbot.css web_assets/chatbot.js assets/
cp web_assets/vendor/marked.esm.js assets/vendor/
uv run python scripts/wire_chatbot.py
```

`wire_chatbot.py` wires the SPA shell (`spa.css`/`spa.js`) + chatbot
(`chatbot.css`/`chatbot.js`) into the **root** `index.html` only. Legacy
`books/*/index.html` files (if any remain) are stripped of stray chatbot/SPA
asset tags so they don't spawn a second instance.

The chatbot CSS uses a **self-contained dark palette** by default (defined in `chatbot.css` `:root`), but it includes dynamic overrides inside `index.html` that align its styling to the light theme when light mode is enabled (providing clear text and light surfaces while preserving dark code block backgrounds for syntax readability).

---

## 🤖 Directives for AI Agents

### 1. Maintain Scraper Integrity
When modifying mirroring scripts:
* **Headers & Spoofing:** Always use the defined User-Agent `UA` and `--ssl-no-revoke` with `curl.exe` to bypass potential blocking and revocation checking on Windows hosts.
* **Stable Hashing for Assets:** Ensure all image filenames are derived using the `safe_image_name` hashing logic. Collisions must be avoided, and image names should remain stable between runs.
* **Strict DOM Stripping:** Always remove all scripts, styles, forms, newsletter elements, and navigation links. If a website uses Next.js or React, ensure that all `__next_f.push` and similar inline JS blocks are stripped so they don't bloat the offline content or disrupt offline rendering.

### 2. Book Markdown Format
Each book is a **single `index.md`** inside its folder. The SPA fetches it at
runtime, splits on `<!-- rl-section ... -->` markers, renders each chunk with
marked.js, and wraps the output in `<section>` elements for scroll tracking and
chatbot chapter scope.

```markdown
---
title: Book Title
author: Author Name
source: single-page EPUB conversion
---

# Book Title

*Author · source note*

<!-- rl-section id="section-1" class="epub-section" kicker="Section 1" title="Introduction" -->

## Introduction

Prose and images here…
```

* **Section markers:** `scripts/book_markdown.py` defines the `<!-- rl-section id="…" class="…" kicker="…" title="…" -->` format. Use `class="epub-section"` for EPUB conversions and `class="chapter"` for web mirrors.
* **No External Dependencies:** Book markdown must not import external stylesheets, fonts, or JS charting libraries. Reader typography lives in `spa.css`.
* **Static Graphics Resolution:** If a site contains interactive components like charts, do **not** leave empty placeholder divs. Extract the underlying data array from the JavaScript source code and programmatically compile an offline-friendly, beautiful, responsive SVG chart, embedding it as a raw HTML block inside the markdown.
* **Relative Assets:** All image links must be strictly relative (`images/filename-hash.png`, `assets/cover.jpg`). The SPA rewrites relative `src`/`href` on media to resolve from the book's folder.
* **Stable section IDs:** Section `id` attributes must remain stable across regenerations — the SPA's `IntersectionObserver` and the chatbot chunker key off them.

### 3. Rights-aware EPUB conversions

Commercial EPUBs from a local ebook folder must not be copied or converted into GitHub Pages output unless redistribution rights are explicit. When the repository owner confirms rights, use `scripts/convert_epub.py` and publish the generated markdown under `books/<slug>/index.md` like any other book mirror.

For Kindle-format sources (AZW3/AZW/MOBI/KFX), use `scripts/convert_azw3.py`. It transcodes the source to EPUB via Calibre's `ebook-convert` CLI into a temp directory, then reuses `convert_epub.convert_epub()` to compile the same standalone markdown — there is one code path for content generation, so output stays consistent with EPUB-sourced books. The same front-end also handles DJVU (scanned-document) sources when Calibre can extract text from them. Neither script bypasses DRM; if a source is DRMed, Calibre will fail and the error is surfaced.

### 4. SEO & Semantic Best Practices
The SPA shell (`index.html`) carries the portal's descriptive `<title>` and
`<meta>` tags. Each book's markdown front matter should include an accurate
`title` and `author`. Rendered output uses semantic HTML (`<article>`, `<section>`,
headings, lists) produced by marked.js from well-structured markdown.

---

## ⚡ Execution Command Reference

To test, update, or regenerate the mirrored books, run the dedicated script from the workspace root:

```bash
# Refresh NatGas 101 (with SVG chart compiler)
uv run python scripts/mirror_natgas101.py

# Refresh Oil 101
uv run python scripts/mirror_oil101.py

# Convert a rights-approved EPUB to markdown
uv run python scripts/convert_epub.py "E:\ebook\Books\path\book.epub" --slug book-slug

# Convert a rights-approved Kindle-format file (AZW3/AZW/MOBI/KFX) or DJVU
uv run python scripts/convert_azw3.py "E:\ebook\Calibre Library\Author\Book (1)\Book - Author.azw3" --slug book-slug

# Terminal chatbot (RAG + local Gemma 4, WebGPU/CPU)
cd scripts && npm install   # once — transformers.js + onnxruntime-node
uv sync --extra cli
uv run --extra cli python scripts/chatbot_cli.py -q "What is a black swan?"
uv run --extra cli python scripts/chatbot_cli.py --model e4b --device auto
uv run --extra cli python scripts/chatbot_cli.py --book antifragile --scope book
```

*Python dependencies (`beautifulsoup4`, `lxml`, `markdownify`) are resolved automatically by [uv](https://docs.astral.sh/uv/) via the project's `pyproject.toml` — no manual install needed. The AZW3/MOBI/DJVU path additionally requires [Calibre](https://calibre-ebook.com/download) installed (provides the `ebook-convert` CLI).*
