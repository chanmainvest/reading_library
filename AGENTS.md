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
├── pyproject.toml          # Python deps (bs4, lxml) for uv run
├── books/                  # Published book content (fetched by the SPA at runtime)
│   ├── catalog.json        # Public catalog/status metadata for requested books
│   ├── oil101/             # Offline mirrored book directory
│   │   ├── index.html      # Compiled Oil 101 book (content source for the SPA)
│   │   └── images/         # Scraped and local-hashed high-res oil images
│   ├── natgas101/          # Offline mirrored book directory
│   │   ├── index.html      # Compiled NatGas 101 book (with SVG chart)
│   │   └── images/         # Scraped and local-hashed high-res gas images
│   └── <book-slug>/        # Rights-approved EPUB conversion output
│       ├── index.html      # Single-page converted EPUB (content source for the SPA)
│       └── assets/         # Locally extracted EPUB images
├── assets/                 # Served SPA + chatbot assets (copied from web_assets/)
│   ├── spa.css             # SPA shell styles (top bar, reader viewport)
│   ├── spa.js              # Hash router, book loader, active-section tracker (window.RL)
│   ├── chatbot.css         # Self-contained dark palette for the AI assistant
│   ├── chatbot.js          # On-device Gemma 4 + embeddinggemma RAG assistant (scope toggle)
│   ├── chatbot_chunks.json # Static RAG chunk index (per-section, with sectionId/sectionTitle)
│   └── chatbot_embeddings.bin  # Prebuilt embedding cache (~95 MB, built by Node on GPU)
├── web_assets/             # Source of truth for SPA/chatbot CSS/JS (copy to assets/ to publish)
├── scripts/                # Reusable automation scripts
│   ├── mirror_natgas101.py # Dedicated script for NatGas 101 scraping
│   ├── mirror_oil101.py    # Dedicated script for Oil 101 scraping
│   ├── convert_epub.py     # EPUB-to-single-page HTML converter for rights-approved books
│   ├── convert_azw3.py     # AZW3/MOBI/AZW/DJVU-to-HTML converter (Calibre front-end + convert_epub)
│   ├── build_chatbot_index.py     # Per-section chunker -> assets/chatbot_chunks.json
│   ├── build_chatbot_embeddings.mjs  # Embed chunks -> assets/chatbot_embeddings.bin (GPU/DML)
│   ├── package.json        # Node deps for the embeddings build (@huggingface/transformers v4)
│   └── wire_chatbot.py     # Wire SPA+chatbot into root index.html; strip book pages
```

---

## 🤖 On-Device AI Assistant

The library is a **single-page app** (`assets/spa.js`): the root `index.html`
hosts a hash router (`#/` = home, `#/books/<slug>` = reader). Book content is
fetched and injected into `#reader-content` — the page never reloads, so the
chatbot module stays loaded across book switches and the LLM pipeline,
embeddings, and conversation history persist in memory.

- **Top bar**: shows a home icon, the book title, and the active chapter
  number + title (tracked via `IntersectionObserver` on `<section>` elements
  as the user scrolls). Exposed to the chatbot via `window.RL.getState()`.
- **LLM**: Gemma 4 E2B (~3.1 GB, q4f16, cached in IndexedDB after first load) via transformers.js v4 + WebGPU.
- **Embedding model**: `onnx-community/embeddinggemma-300m-ONNX` (~300 MB q8) for cross-book retrieval, run on WASM (EmbeddingGemma can't compile a WebGPU pipeline and WASM avoids contending with Gemma for the GPU).
- **Cross-book RAG**: a per-section chunk index (`assets/chatbot_chunks.json`, each chunk carrying `sectionId`/`sectionTitle`) plus a prebuilt embedding cache (`assets/chatbot_embeddings.bin`) let the assistant pull relevant excerpts. The browser recomputes a SHA-256 over the chunk texts and refuses a stale bin, falling back to per-chunk embedding.
- **Scope toggle**: a segmented control in the chat panel — **This chapter** / **This book** / **All books** — controls which slice of the corpus RAG retrieval searches. The current section's text is always injected as priority context; the toggle controls the *supporting excerpts* layer. "This chapter"/"This book" are disabled on the home view (no book open). Persisted in `localStorage`.

### Regeneration order (after adding or converting books)
The chunk index and embedding cache are derived artifacts — regenerate them whenever book content changes:

```bash
# 1. Rebuild the per-section chunk index from every book's index.html
uv run python scripts/build_chatbot_index.py

# 2. Rebuild the embedding cache (Node + @huggingface/transformers v4).
#    Auto-selects DirectML (GPU) on Windows + NVIDIA, else CPU. Override with
#    --device cpu|dml|cuda. The q8 weights yield identical vectors on any
#    device, so a GPU build and a CPU build are interchangeable.
cd scripts && npm install && node build_chatbot_embeddings.mjs

# 3. Sync source assets to the served location and (re)wire the SPA shell
cp web_assets/spa.css web_assets/spa.js web_assets/chatbot.css web_assets/chatbot.js assets/
uv run python scripts/wire_chatbot.py
```

`wire_chatbot.py` wires the SPA shell (`spa.css`/`spa.js`) + chatbot
(`chatbot.css`/`chatbot.js`) into the **root** `index.html` only, and
**strips** any chatbot/SPA asset tags from `books/*/index.html` so book
pages remain clean content sources for the SPA's fetch+inject path (a
stray chatbot `<script>` on a book page would spawn a second instance).

The chatbot CSS uses a **self-contained dark palette** (defined in `chatbot.css` `:root`) rather than inheriting the host page's tokens. The assistant looks identical in every view.

---

## 🤖 Directives for AI Agents

### 1. Maintain Scraper Integrity
When modifying mirroring scripts:
* **Headers & Spoofing:** Always use the defined User-Agent `UA` and `--ssl-no-revoke` with `curl.exe` to bypass potential blocking and revocation checking on Windows hosts.
* **Stable Hashing for Assets:** Ensure all image filenames are derived using the `safe_image_name` hashing logic. Collisions must be avoided, and image names should remain stable between runs.
* **Strict DOM Stripping:** Always remove all scripts, styles, forms, newsletter elements, and navigation links. If a website uses Next.js or React, ensure that all `__next_f.push` and similar inline JS blocks are stripped so they don't bloat the offline content or disrupt offline rendering.

### 2. Standalone Offline Compatibility
Each book is a **single, monolithic `index.html`** inside its folder. These
pages are *content sources* fetched and injected by the SPA at runtime — they
do not load the chatbot themselves (the SPA hosts the singleton). They remain
directly browsable as a fallback.
* **No External Dependencies:** No external stylesheets, fonts, or JS charting libraries should be imported dynamically. Per-book inline `<style>` is expected and is scoped under `#reader-content` by the SPA at load time.
* **Static Graphics Resolution:** If a site contains interactive components like charts, do **not** leave empty placeholder divs. You must extract the underlying data array from the JavaScript source code and programmatically compile an offline-friendly, beautiful, responsive SVG chart, injecting it directly into the markup.
* **Relative Assets:** All asset links (images, internal navigation) must be strictly relative (`images/filename-hash.png`, `#chapter-slug`). The SPA rewrites relative `src`/`href` on media to resolve from the book's folder, so relative paths work both standalone and inside the SPA.
* **Section structure:** Books must use `<section>` elements (`section.epub-section` for EPUB conversions, `section.chapter` for mirrors) with stable `id`s — the SPA tracks the active section via `IntersectionObserver` and the chunker indexes per-section for the chatbot's chapter scope.

### 3. Rights-aware EPUB conversions

Commercial EPUBs from a local ebook folder must not be copied or converted into GitHub Pages output unless redistribution rights are explicit. When the repository owner confirms rights, use `scripts/convert_epub.py` and publish the generated single-page HTML under `books/<slug>/index.html` like any other book mirror.

For Kindle-format sources (AZW3/AZW/MOBI/KFX), use `scripts/convert_azw3.py`. It transcodes the source to EPUB via Calibre's `ebook-convert` CLI into a temp directory, then reuses `convert_epub.convert_epub()` to compile the same standalone single-page HTML — there is one code path for HTML generation, so output stays consistent with EPUB-sourced books. The same front-end also handles DJVU (scanned-document) sources when Calibre can extract text from them. Neither script bypasses DRM; if a source is DRMed, Calibre will fail and the error is surfaced.

### 4. SEO & Semantic Best Practices
Every generated page must contain:
* A single descriptive `<h1>` title block.
* Semantic HTML5 tagging (`<article>`, `<section>`, `<nav>`, `<aside>`).
* Descriptive `title` and `meta` tags.
* Clean and readable system-tailored typography and grid CSS to ensure accessibility.

---

## ⚡ Execution Command Reference

To test, update, or regenerate the mirrored books, run the dedicated script from the workspace root:

```bash
# Refresh NatGas 101 (with SVG chart compiler)
uv run python scripts/mirror_natgas101.py

# Refresh Oil 101
uv run python scripts/mirror_oil101.py

# Convert a rights-approved EPUB to single-page HTML
uv run python scripts/convert_epub.py "E:\ebook\Books\path\book.epub" --slug book-slug

# Convert a rights-approved Kindle-format file (AZW3/AZW/MOBI/KFX) or DJVU
uv run python scripts/convert_azw3.py "E:\ebook\Calibre Library\Author\Book (1)\Book - Author.azw3" --slug book-slug
```

*Python dependencies (`beautifulsoup4`, `lxml`) are resolved automatically by [uv](https://docs.astral.sh/uv/) via the project's `pyproject.toml` — no manual install needed. The AZW3/MOBI/DJVU path additionally requires [Calibre](https://calibre-ebook.com/download) installed (provides the `ebook-convert` CLI).*
