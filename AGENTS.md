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
├── scripts/                # Reusable automation scripts
│   ├── mirror_natgas101.py # Dedicated script for NatGas 101 scraping
│   ├── mirror_oil101.py    # Dedicated script for Oil 101 scraping
│   └── convert_epub.py     # EPUB-to-single-page HTML converter for rights-approved books
```

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
```

*Ensure that BeautifulSoup (`bs4`) and `lxml` are available on your host system before running.*
