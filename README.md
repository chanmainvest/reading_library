# Investment Reading Materials Portal

Welcome to the investment reading materials repository for **chanmainvest**. This repository contains mirrors and curated text versions of core books, research papers, and educational resources related to energy, commodities, and financial markets.

This repository is optimized primarily for **AI agent ingestion**, but it also provides a premium, highly responsive, human-readable offline browsing experience via GitHub Pages.

---

## 📚 Currently Mirrored Materials

1. **Oil 101** (`/oil101`)
   - An offline mirror of Morgan Downey's *Oil 101*, the definitive guide to the oil industry.
   - Access: [Browse Oil 101 Offline Mirror](./oil101/index.html) or via [GitHub Pages Portal](https://chanmainvest.github.io/reading/oil101/index.html).
   
2. **NatGas 101** (`/natgas101`)
   - An offline mirror of Morgan Downey's *NatGas 101*, the complete guide to North American natural gas markets, infrastructure, and geology.
   - Features a programmaticallyCompiled, offline-friendly responsive SVG chart of the **Duck Curve** in Chapter 12 ("Power Generation").
   - Access: [Browse NatGas 101 Offline Mirror](./natgas101/index.html) or via [GitHub Pages Portal](https://chanmainvest.github.io/reading/natgas101/index.html).

---

## 🛠️ Repository Ecosystem

The repository is organized as follows:
* **`index.html`** (Root): The main visual landing portal that connects both books and any future papers. Serves as the index for GitHub Pages (`github.io`).
* **`scripts/`**: Contains standalone Python mirroring scripts that scrape the original web books, clean them, download high-resolution assets locally, and compile them into self-contained HTML resources.
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
