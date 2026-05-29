# Reading Library

Welcome to **chanmainvest/reading_library**, the investment reading pipeline for Chanma Investment. This repository contains mirrors, catalogs, and curated text versions of core books, research papers, and educational resources related to energy, commodities, financial history, investing, trading, banking, and markets.

This repository is optimized primarily for **AI agent ingestion**, but it also provides a premium, highly responsive, human-readable offline browsing experience via GitHub Pages.

---

## 📚 Currently Mirrored Materials

1. **Oil 101** (`/books/oil101`)
   - An offline mirror of Morgan Downey's *Oil 101*, the definitive guide to the oil industry.
   - Access: [Browse Oil 101 Offline Mirror](./books/oil101/index.html) or via [GitHub Pages Portal](https://chanmainvest.github.io/reading_library/books/oil101/index.html).
   
2. **NatGas 101** (`/books/natgas101`)
   - An offline mirror of Morgan Downey's *NatGas 101*, the complete guide to North American natural gas markets, infrastructure, and geology.
   - Features a programmatically compiled, offline-friendly responsive SVG chart of the **Duck Curve** in Chapter 12 ("Power Generation").
   - Access: [Browse NatGas 101 Offline Mirror](./books/natgas101/index.html) or via [GitHub Pages Portal](https://chanmainvest.github.io/reading_library/books/natgas101/index.html).

## 📖 Finance & Markets Catalog

Requested finance and markets books are tracked in [books/catalog.json](./books/catalog.json). EPUB files with explicit rights are converted into the same published single-page HTML format as the web mirrors.

Currently converted from local EPUB source:

1. **The Ascent of Money** (`/books/the-ascent-of-money`)
   - Access: [Browse The Ascent of Money](./books/the-ascent-of-money/index.html)
2. **Lords of Finance** (`/books/lords-of-finance`)
   - Access: [Browse Lords of Finance](./books/lords-of-finance/index.html)
3. **Material World** (`/books/material-world`)
   - Access: [Browse Material World](./books/material-world/index.html)
4. **The World for Sale** (`/books/the-world-for-sale`)
   - Access: [Browse The World for Sale](./books/the-world-for-sale/index.html)

To convert a rights-approved EPUB into the published `books/` layout, use:

```bash
py scripts/convert_epub.py "E:\ebook\Books\path\book.epub" --slug book-slug
```

---

## 🛠️ Repository Ecosystem

The repository is organized as follows:
* **`index.html`** (Root): The main visual landing portal that connects both books and any future papers. Serves as the index for GitHub Pages (`github.io`).
* **`books/`**: Published single-page book mirrors plus the catalog metadata.
* **`scripts/`**: Contains standalone Python mirroring and EPUB conversion scripts that compile books into self-contained HTML resources.
* **`AGENTS.md`**: Dedicated instructions for AI coding and reading agents detailing the environment, script execution, and structure.
* **`CONTRIBUTING.md`**: Contributing guidelines specifying the automated, agent-led PR workflow.

---

## 🚀 How to Run the Mirrors Locally

To refresh or update the books from their live sources, you can run the mirroring scripts in the `scripts/` folder using Python:

### Prerequisites
Make sure you have `beautifulsoup4` and `lxml` installed in your Python environment:
```bash
pip install beautifulsoup4 lxml
```

### Run Scripts
Each script runs independently to fetch and rebuild its respective book:

```bash
# Mirror and rebuild NatGas 101 (with custom SVG charts injection)
py scripts/mirror_natgas101.py

# Mirror and rebuild Oil 101
py scripts/mirror_oil101.py
```

*Note: The scripts make use of `curl.exe` under the hood to fetch materials reliably on Windows environments.*
