# Agent Instructions & Guidelines

Welcome, AI Agent! This file outlines the architecture, standards, and rules of engagement for interacting with this repository. Please read this file in full before performing any editing, scripting, or automated operations.

---

## 📂 Repository Architecture

```
chanmainvest/reading_library/
├── README.md               # Human-facing repository documentation
├── CONTRIBUTING.md         # Process guidelines (PR flows)
├── AGENTS.md               # This file (Agent instructions)
├── index.html              # Main premium portal landing page (GitHub Pages index)
├── books/                  # Published single-page book mirrors and catalog metadata
│   ├── catalog.json        # Public catalog/status metadata for requested books
│   ├── oil101/             # Offline mirrored book directory
│   │   ├── index.html      # Compiled, standalone Oil 101 book
│   │   └── images/         # Scraped and local-hashed high-res oil images
│   ├── natgas101/          # Offline mirrored book directory
│   │   ├── index.html      # Compiled, standalone NatGas 101 book (with SVG chart)
│   │   └── images/         # Scraped and local-hashed high-res gas images
│   └── <book-slug>/        # Rights-approved EPUB conversion output
│       ├── index.html      # Single-page converted EPUB
│       └── assets/         # Locally extracted EPUB images
├── assets/                 # Served chatbot assets (copied from web_assets/)
│   ├── chatbot.css         # Self-contained dark palette for the AI assistant
│   ├── chatbot.js          # On-device Gemma 4 + embeddinggemma RAG assistant
│   ├── chatbot_chunks.json # Static RAG chunk index of every book (built, not hand-edited)
│   └── chatbot_embeddings.bin  # Prebuilt embedding cache (~70 MB, built by Node)
├── web_assets/             # Source of truth for chatbot CSS/JS (copy to assets/ to publish)
├── scripts/                # Reusable automation scripts
│   ├── mirror_natgas101.py # Dedicated script for NatGas 101 scraping
│   ├── mirror_oil101.py    # Dedicated script for Oil 101 scraping
│   ├── convert_epub.py     # EPUB-to-single-page HTML converter for rights-approved books
│   ├── convert_azw3.py     # AZW3/MOBI/AZW-to-HTML converter (Calibre front-end + convert_epub)
│   ├── build_chatbot_index.py     # Build assets/chatbot_chunks.json from every book's HTML
│   ├── build_chatbot_embeddings.mjs  # Embed chunks -> assets/chatbot_embeddings.bin (Node)
│   ├── package.json        # Node deps for the embeddings build (@huggingface/transformers v4)
│   └── wire_chatbot.py     # Inject chatbot <link>/<script> into every index.html (idempotent)
```

---

## 🤖 On-Device AI Assistant

Every page in the library (the portal and each `books/<slug>/index.html`) carries a floating "AI" button that opens an on-device assistant. It runs **entirely in the browser** — no question leaves the user's device.

- **LLM**: Gemma 4 E2B (~3.1 GB, q4f16, cached in IndexedDB after first load) via transformers.js v4 + WebGPU.
- **Embedding model**: `onnx-community/embeddinggemma-300m-ONNX` (~300 MB q8) for cross-book retrieval, run on WASM (EmbeddingGemma can't compile a WebGPU pipeline and WASM avoids contending with Gemma for the GPU).
- **Cross-book RAG**: a static chunk index (`assets/chatbot_chunks.json`) of every published book plus a prebuilt embedding cache (`assets/chatbot_embeddings.bin`) let the assistant pull relevant excerpts from across the whole library. The browser recomputes a SHA-256 over the chunk texts and refuses a stale bin, falling back to per-chunk embedding.

### Regeneration order (after adding or converting books)
The chunk index and embedding cache are derived artifacts — regenerate them whenever book content changes:

```bash
# 1. Rebuild the chunk index from every book's index.html (pure stdlib + bs4)
py scripts/build_chatbot_index.py

# 2. Rebuild the embedding cache (Node + @huggingface/transformers v4)
cd scripts && npm install && node build_chatbot_embeddings.mjs

# 3. Copy source assets to the served location and (re)wire every page
cp web_assets/chatbot.css web_assets/chatbot.js assets/
py scripts/wire_chatbot.py
```

`wire_chatbot.py` is idempotent — it injects `<link>`/`<script>` with the correct relative depth (`assets/…` on the portal, `../assets/…` on book pages) and skips pages already wired. Run it after adding books so new pages get the assistant.

The chatbot CSS uses a **self-contained dark palette** (defined in `chatbot.css` `:root`) rather than inheriting the host page's tokens, because the portal (dark cyan glass) and book pages (light/dark serif) use different design systems. The assistant looks identical on every page.

---

## 🤖 Directives for AI Agents

### 1. Maintain Scraper Integrity
When modifying mirroring scripts:
* **Headers & Spoofing:** Always use the defined User-Agent `UA` and `--ssl-no-revoke` with `curl.exe` to bypass potential blocking and revocation checking on Windows hosts.
* **Stable Hashing for Assets:** Ensure all image filenames are derived using the `safe_image_name` hashing logic. Collisions must be avoided, and image names should remain stable between runs.
* **Strict DOM Stripping:** Always remove all scripts, styles, forms, newsletter elements, and navigation links. If a website uses Next.js or React, ensure that all `__next_f.push` and similar inline JS blocks are stripped so they don't bloat the offline content or disrupt offline rendering.

### 2. Standalone Offline Compatibility
All books and papers must be compiled into a **single, monolithic `index.html`** file inside their respective folder.
* **No External Dependencies:** No external stylesheets, fonts, or JS charting libraries should be imported dynamically.
* **Static Graphics Resolution:** If a site contains interactive components like charts, do **not** leave empty placeholder divs. You must extract the underlying data array from the JavaScript source code and programmatically compile an offline-friendly, beautiful, responsive SVG chart, injecting it directly into the markup.
* **Relative Assets:** All asset links (images, internal navigation) must be strictly relative (`images/filename-hash.png`, `#chapter-slug`) to work seamlessly on native filesystems (`file:///` protocol) and GitHub Pages (`github.io`).

### 3. Rights-aware EPUB conversions

Commercial EPUBs from a local ebook folder must not be copied or converted into GitHub Pages output unless redistribution rights are explicit. When the repository owner confirms rights, use `scripts/convert_epub.py` and publish the generated single-page HTML under `books/<slug>/index.html` like any other book mirror.

For Kindle-format sources (AZW3/AZW/MOBI/KFX), use `scripts/convert_azw3.py`. It transcodes the source to EPUB via Calibre's `ebook-convert` CLI into a temp directory, then reuses `convert_epub.convert_epub()` to compile the same standalone single-page HTML — there is one code path for HTML generation, so output stays consistent with EPUB-sourced books. Neither script bypasses DRM; if a source is DRMed, Calibre will fail and the error is surfaced.

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
py scripts/mirror_natgas101.py

# Refresh Oil 101
py scripts/mirror_oil101.py

# Convert a rights-approved EPUB to single-page HTML
py scripts/convert_epub.py "E:\ebook\Books\path\book.epub" --slug book-slug

# Convert a rights-approved Kindle-format file (AZW3/AZW/MOBI/KFX)
py scripts/convert_azw3.py "E:\ebook\Calibre Library\Author\Book (1)\Book - Author.azw3" --slug book-slug
```

*Ensure that BeautifulSoup (`bs4`) and `lxml` are available on your host system before running. The AZW3/MOBI path additionally requires [Calibre](https://calibre-ebook.com/download) installed (provides the `ebook-convert` CLI).*
