/* On-device AI assistant for the Chanma Invest reading library.
 *
 * Loads Gemma in the browser via transformers.js v4 + WebGPU. The model
 * weights are fetched from Hugging Face on first use and cached in
 * IndexedDB by the library — subsequent visits skip the download.
 *
 * Page-context strategy: at send-time, read the innerText of the page's
 * main content (<article> for book pages, <main> for the portal) and
 * stuff the first ~6 K characters into the system prompt. This means the
 * assistant always answers about whatever book the user is currently
 * reading.
 *
 * Cross-book RAG: a static chunk index (assets/chatbot_chunks.json) of
 * every published book, plus a prebuilt embedding cache
 * (assets/chatbot_embeddings.bin), let the assistant pull in relevant
 * excerpts from across the whole library. The reading library is
 * English-only, so there is a single "en" index.
 *
 * No data leaves the browser.
 *
 * Ported from the Chanma Investment Tutorial's chatbot — same model,
 * embedding model, binary format, and IndexedDB persistence, adapted
 * for a single-language book corpus.
 */

const MODEL_OPTIONS = {
    "gemma-4-e2b": {
        id: "onnx-community/gemma-4-E2B-it-ONNX",
        size_mb: 3100,
        label: "Gemma 4 E2B (≈ 3.1 GB)",
    },
};

const ACTIVE_MODEL = "gemma-4-e2b";
const MAX_CONTEXT_CHARS = 6000;       // current-page slice in the system prompt
const MAX_CHAPTER_CHARS = 20000;      // higher cap in "this chapter" scope
const MAX_HISTORY_TURNS = 6;
const MAX_NEW_TOKENS = 512;

const CDN_URL = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4/+esm";

// ----- Cross-book RAG ---------------------------------------------------
// A multilingual embedding model + a static JSON of every book's text,
// chunked at build time by scripts/build_chatbot_index.py. Embeddings are
// shipped prebuilt by scripts/build_chatbot_embeddings.mjs; if the bin is
// missing/stale the browser falls back to per-chunk embedding via the WASM
// embedder. At query time we cosine-sim the user's question against the
// cached embeddings and inject the top-K chunks into the system prompt
// alongside the current page text.
const EMBED_MODEL_ID = "onnx-community/embeddinggemma-300m-ONNX";
const TOP_K = 5;
const RAG_DB_NAME = "chanma-rl-chatbot-rag";
const RAG_STORE = "embeddings";
const RAG_DB_VERSION = 2;

// Prebuilt embedding cache (shipped as a static binary so users get instant
// cross-book search instead of a minutes-long in-browser indexing step).
// Produced by scripts/build_chatbot_embeddings.mjs. The browser still
// downloads the embedding model at runtime to embed user queries (dynamic,
// can't be precomputed), but skips the slow per-chunk indexing because the
// vectors are already in the bin.
const EMBED_BIN_MAGIC = 0x42454d43;   // "CMEB" read as little-endian u32
const EMBED_BIN_VERSION = 1;
const EMBED_BIN_DTYPE_F32 = 1;
const EMBED_BIN_LANGS = ["en"];       // reading library is English-only

// Resolve the asset URL relative to the current page. The portal lives at
// the repo root (assets/...) and book pages live at books/<slug>/
// The SPA hosts the chatbot at the repo root, so assets are always at
// "assets/". (Previously this was computed per-page because each book page
// loaded the script from ../assets/; the SPA consolidates to one location.)
const ASSET_PREFIX = "assets/";
const CHUNKS_URL = ASSET_PREFIX + "chatbot_chunks.json";
const EMBEDDINGS_BIN_URL = ASSET_PREFIX + "chatbot_embeddings.bin";

// ----- i18n (English only) ----------------------------------------------
const I18N = {
    title: "Ask the library assistant",
    subtitle: "Runs on your device — no data leaves your browser.",
    intro_h: "Reading library assistant",
    intro_p: "Ask any question about the book you're reading. The assistant runs a small Gemma model entirely inside your browser using WebGPU. Your questions never leave your device.",
    notes: [
        "First load downloads the model (≈ 3.1 GB) and caches it. Subsequent visits are instant.",
        "Best on a recent desktop browser. Mobile and low-RAM machines may run out of memory.",
        "The assistant reads the book currently shown and can pull excerpts from other books in the library.",
    ],
    load_btn: "Load assistant (≈ 3.1 GB)",
    loading: "Loading model…",
    no_webgpu: "Your browser does not support WebGPU. Try the latest Chrome, Edge, Firefox, or Safari on a desktop.",
    unsupported_title: "On-device AI not available",
    ready: "Ready. Ask anything about this book or the library.",
    thinking: "Thinking…",
    send: "Send",
    clear: "Clear conversation",
    close: "Close",
    placeholder: "Ask about this book…",
    load_failed: "Could not load the model: ",
    gen_failed: "Generation failed: ",
    no_book: "(no book text found on this page)",
    retrieved_header: "RELEVANT EXCERPTS FROM OTHER BOOKS",
    current_page_header: "CURRENT BOOK (priority — answer using this first)",
    index_section_label: "Cross-book search index",
    index_section_help: "Without the index, the assistant answers from the current book only. The index is prebuilt — it downloads once and is cached for future visits.",
    chip_ready: "Cross-book search on",
    chip_indexing: "Indexing",
    chip_none: "Build cross-book index",
    chip_error: "Index failed — retry",
    chip_ready_title: "The assistant can pull excerpts from any book in the library.",
    chip_index_title: "Click to build the cross-book search index (one-time per browser).",
    chip_retry_title: "Click to retry building the index.",
    mermaid_chart: "Chart",
    mermaid_source: "Source",
    mermaid_toggle_label: "Toggle Mermaid chart display",
    mermaid_selected_line: "Line {line}",
    mermaid_selected_lines: "Lines {start}-{end}",
};

function t(key) {
    return I18N[key] || key;
}

function tf(key, vars = {}) {
    const template = t(key);
    if (typeof template !== "string") return template;
    return template.replace(/\{(\w+)\}/g, (_, name) => (
        vars[name] == null ? `{${name}}` : String(vars[name])
    ));
}

// ----- Page-context extraction --------------------------------------------
// In the SPA, the active section's prose comes from window.RL. When a book is
// open the SPA tracks the section in view via IntersectionObserver and exposes
// its text; on the home view (no book) there is no priority context.
// In "this chapter" scope the cap is higher so the model sees the whole
// section (Gemma 4 E2B has a large context window); other scopes use the
// tighter default since RAG excerpts supplement the priority context.
function getActivePageText() {
    if (window.RL && typeof window.RL.getActiveSectionText === "function") {
        let text = window.RL.getActiveSectionText() || "";
        text = text.replace(/\s+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
        const cap = retrievalScope === "chapter" ? MAX_CHAPTER_CHARS : MAX_CONTEXT_CHARS;
        if (text.length > cap) {
            text = text.slice(0, cap) + "\n…(truncated)";
        }
        return text;
    }
    return "";
}

async function buildSystemPrompt(userQuestion) {
    const pageText = getActivePageText() || t("no_book");
    const hasBook = !!(window.RL && window.RL.getState && window.RL.getState().bookUrl);
    let retrieved = [];
    if (userQuestion) {
        retrieved = await retrieveContext(userQuestion);
    }
    // Adapt the prompt to the active scope so the model knows where the
    // supporting excerpts come from and how to weight them.
    const scopeDesc = hasBook
        ? (retrievalScope === "chapter"
            ? "the CURRENT CHAPTER section"
            : retrievalScope === "book"
                ? "the CURRENT BOOK section"
                : "the CURRENT BOOK section and RELEVANT EXCERPTS from across the library")
        : "RELEVANT EXCERPTS from across the library";
    const blocks = [
        "You are an assistant for the Chanma Invest reading library, a curated",
        "collection of books on investing, financial history, banking, trading,",
        "commodities, and markets. Answer the user's question using ONLY the",
        "library material provided below.",
        `Prefer ${scopeDesc}.`,
        "",
        "The material the user is currently reading is provided under CURRENT",
        "CHAPTER / CURRENT BOOK below. When the user gives a short instruction",
        'with no object — such as "summarize", "key points", "explain", or',
        '"what is this about" — apply it to THAT material, not the whole',
        "library. Do not ask the user to provide text; the text is already",
        "in this prompt.",
    ];
    if (retrievalScope === "all" || !hasBook) {
        blocks.push(
            "Use the RELEVANT EXCERPTS only when the current material does not",
            "cover the question, and cite the book title in brackets when you",
            'draw on an excerpt (e.g. "[Book: The Big Short] …").',
        );
    }
    blocks.push(
        "If neither section covers the question, say so honestly rather than guessing.",
        "Reply in the same language the user uses. Keep answers concise (~200 words)",
        "unless the user explicitly asks for more detail.",
        "",
        t("current_page_header") + ":",
        pageText,
    );
    if (retrieved.length) {
        blocks.push("");
        const header = (retrievalScope === "chapter" || retrievalScope === "book")
            ? "RELEVANT EXCERPTS (FROM THIS " + retrievalScope.toUpperCase() + "):"
            : t("retrieved_header") + ":";
        blocks.push(header);
        for (const { chunk } of retrieved) {
            blocks.push(`[${chunk.title}] ${chunk.text}`);
        }
    }
    return blocks.join("\n");
}

// ----- DOM scaffolding ----------------------------------------------------
function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === "class") node.className = v;
        else if (k === "html") node.innerHTML = v;
        else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
        else node.setAttribute(k, v);
    }
    for (const c of children) {
        if (c == null) continue;
        node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
}

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function escapeAttr(text) {
    return escapeHtml(text).replace(/\n/g, "&#10;");
}

function normaliseBlockText(text) {
    return String(text).replace(/\r\n?/g, "\n");
}

function applyInlineMarkdown(text, citeIdx) {
    let html = escapeHtml(text);
    html = html.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label, url) => (
        `<a href="${escapeAttr(url)}" target="_blank" rel="noreferrer noopener">${label}</a>`
    ));
    // Linkify bare-bracket book citations the assistant emits, e.g.
    // "[Book: The Big Short]". Only brackets that start with "Book:" and
    // are NOT followed by "(" (those were already handled as markdown links
    // above). Non-matching brackets are left untouched.
    if (citeIdx) {
        html = html.replace(
            /\[Book:\s*([^\]]+)\](?!\()/gi,
            (match, inner) => {
                // inner is already HTML-escaped (it came from the escaped
                // `html` string above). Decode entities to get the raw title
                // for lookup, but use `inner` as-is for display so we don't
                // double-escape.
                const label = ("Book: " + inner)
                    .replace(/&#39;/g, "'").replace(/&quot;/g, '"')
                    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
                const url = linkifyCitation(label, citeIdx);
                if (!url) return match;
                // Repo-relative href (books/<slug>/index.html) — the SPA's
                // click interceptor routes these through the hash router
                // instead of navigating away, so the chatbot stays open.
                return `<a href="${escapeAttr(url)}" class="chat-citation">Book: ${inner}</a>`;
            }
        );
    }
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*\*([^*]+?)\*\*\*/g, "<strong><em>$1</em></strong>");
    html = html.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/(^|[^\*])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>");
    return html;
}

function renderMarkdownChunk(markdown, citeIdx) {
    const lines = normaliseBlockText(markdown).split("\n");
    const html = [];
    let paragraph = [];
    let listItems = [];
    let listType = null;
    let quoteLines = [];

    const inline = (txt) => applyInlineMarkdown(txt, citeIdx);

    function flushParagraph() {
        if (!paragraph.length) return;
        html.push(`<p>${paragraph.map(inline).join("<br>")}</p>`);
        paragraph = [];
    }

    function flushList() {
        if (!listItems.length) return;
        const tag = listType === "ol" ? "ol" : "ul";
        html.push(`<${tag}>${listItems.join("")}</${tag}>`);
        listItems = [];
        listType = null;
    }

    function flushQuotes() {
        if (!quoteLines.length) return;
        html.push(`<blockquote>${quoteLines.map(inline).join("<br>")}</blockquote>`);
        quoteLines = [];
    }

    for (const rawLine of lines) {
        const line = rawLine.trimEnd();
        const trimmed = line.trim();
        const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
        const bullet = trimmed.match(/^[-*]\s+(.+)$/);
        const ordered = trimmed.match(/^\d+\.\s+(.+)$/);
        const quote = trimmed.match(/^>\s+(.+)$/);

        if (!trimmed) {
            flushParagraph();
            flushList();
            flushQuotes();
            continue;
        }

        if (heading) {
            flushParagraph();
            flushList();
            flushQuotes();
            const level = heading[1].length;
            html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
            continue;
        }

        if (/^---+$/.test(trimmed)) {
            flushParagraph();
            flushList();
            flushQuotes();
            html.push("<hr>");
            continue;
        }

        if (quote) {
            flushParagraph();
            flushList();
            quoteLines.push(quote[1]);
            continue;
        }

        flushQuotes();

        if (bullet || ordered) {
            flushParagraph();
            const nextType = ordered ? "ol" : "ul";
            if (listType && listType !== nextType) flushList();
            listType = nextType;
            listItems.push(`<li>${inline((ordered || bullet)[1])}</li>`);
            continue;
        }

        flushList();
        paragraph.push(trimmed);
    }

    flushParagraph();
    flushList();
    flushQuotes();

    return html.join("\n");
}

function splitMarkdownBlocks(markdown) {
    const input = normaliseBlockText(markdown);
    const blocks = [];
    const fence = /```([\w-]*)\n([\s\S]*?)```/g;
    let cursor = 0;

    for (let match = fence.exec(input); match; match = fence.exec(input)) {
        if (match.index > cursor) {
            blocks.push({ type: "markdown", content: input.slice(cursor, match.index) });
        }
        blocks.push({
            type: "code",
            lang: (match[1] || "").toLowerCase(),
            content: match[2],
        });
        cursor = match.index + match[0].length;
    }

    if (cursor < input.length) {
        blocks.push({ type: "markdown", content: input.slice(cursor) });
    }

    return blocks;
}

function getMermaidSourceLines(source) {
    const lines = normaliseBlockText(source).split("\n");
    if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
    return lines;
}

function buildMermaidSourceMarkup(source) {
    return getMermaidSourceLines(source).map((line, index) => {
        const rendered = line.length ? escapeHtml(line) : "&nbsp;";
        return `<span class="chat-mermaid-source-line" data-line-number="${index + 1}">${rendered}</span>`;
    }).join("");
}

function buildMermaidBlock(source) {
    const chartLabel = escapeHtml(t("mermaid_chart"));
    const sourceLabel = escapeHtml(t("mermaid_source"));
    return [
        `<div class="chat-mermaid-block" data-view="diagram">`,
        `<div class="chat-mermaid-toolbar">`,
        `<div class="chat-mermaid-toggle" role="tablist" aria-label="${escapeAttr(t("mermaid_toggle_label"))}">`,
        `<button type="button" class="active" data-view-target="diagram" aria-pressed="true">${chartLabel}</button>`,
        `<button type="button" data-view-target="source" aria-pressed="false">${sourceLabel}</button>`,
        `</div>`,
        `<span class="chat-mermaid-selection-status" aria-live="polite"></span>`,
        `</div>`,
        `<pre class="chat-mermaid-diagram mermaid" tabindex="0">${escapeHtml(normaliseBlockText(source))}</pre>`,
        `<pre class="chat-mermaid-source" tabindex="0"><code>${buildMermaidSourceMarkup(source)}</code></pre>`,
        `<textarea class="chat-mermaid-selection-proxy" aria-hidden="true" tabindex="-1" readonly>${escapeHtml(normaliseBlockText(source))}</textarea>`,
        `</div>`,
    ].join("");
}

function renderCodeBlock(lang, code) {
    if (lang === "mermaid") return buildMermaidBlock(code);
    const languageClass = lang ? ` class="language-${escapeAttr(lang)}"` : "";
    return `<pre class="chat-code-block"><code${languageClass}>${escapeHtml(normaliseBlockText(code))}</code></pre>`;
}

async function renderMarkdown(text) {
    // Build the book-citation index once (cached) so applyInlineMarkdown
    // can linkify "[Book: …]"-style references in-place.
    const citeIdx = await getBookUrlIndex().catch(() => null);
    return splitMarkdownBlocks(text).map((block) => {
        if (block.type === "code") return renderCodeBlock(block.lang, block.content);
        return renderMarkdownChunk(block.content, citeIdx);
    }).join("\n");
}

function setMermaidSelectionStatus(block, message) {
    const status = block.querySelector(".chat-mermaid-selection-status");
    if (!status) return;
    status.textContent = message || "";
    status.classList.toggle("has-selection", Boolean(message));
}

function formatMermaidSelection(startLine, endLine) {
    if (!startLine || !endLine) return "";
    if (startLine === endLine) return tf("mermaid_selected_line", { line: startLine });
    return tf("mermaid_selected_lines", { start: startLine, end: endLine });
}

function setMermaidView(block, view) {
    block.setAttribute("data-view", view);
    block.querySelectorAll(".chat-mermaid-toggle button").forEach((button) => {
        const active = button.dataset.viewTarget === view;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    if (view !== "source") setMermaidSelectionStatus(block, "");
}

function getMermaidSource(block) {
    const proxy = block.querySelector(".chat-mermaid-selection-proxy");
    return proxy ? normaliseBlockText(proxy.value) : "";
}

function getMermaidTheme() {
    return "dark";
}

function renderMermaidBlocks(root = document) {
    if (typeof mermaid === "undefined") return;
    const nodes = root.querySelectorAll(".chat-mermaid-diagram.mermaid:not([data-processed='true'])");
    if (!nodes.length) return;
    mermaid.initialize({ startOnLoad: false, theme: getMermaidTheme(), securityLevel: "loose" });
    try {
        mermaid.run({ nodes });
    } catch (err) {
        console.warn("chat mermaid render failed", err);
    }
}

function rerenderChatMermaid(root = document) {
    root.querySelectorAll(".chat-mermaid-block").forEach((block) => {
        const diagram = block.querySelector(".chat-mermaid-diagram");
        if (!diagram) return;
        diagram.removeAttribute("data-processed");
        diagram.textContent = getMermaidSource(block);
    });
    renderMermaidBlocks(root);
}

function selectAllMermaidSource(block) {
    const source = getMermaidSource(block);
    if (!source) return;
    const proxy = block.querySelector(".chat-mermaid-selection-proxy");
    if (!proxy) return;
    try {
        proxy.focus({ preventScroll: true });
    } catch (err) {
        proxy.focus();
    }
    proxy.select();
    const totalLines = getMermaidSourceLines(source).length;
    setMermaidSelectionStatus(block, formatMermaidSelection(1, totalLines));
}

function getSourceLineElement(node) {
    if (!node) return null;
    const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    return element ? element.closest(".chat-mermaid-source-line") : null;
}

function updateMermaidSelectionFromPage() {
    const selection = window.getSelection();
    const blocks = document.querySelectorAll(".chat-mermaid-block[data-view='source']");

    blocks.forEach((block) => setMermaidSelectionStatus(block, ""));

    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return;

    const range = selection.getRangeAt(0);
    const startLine = getSourceLineElement(range.startContainer);
    const endLine = getSourceLineElement(range.endContainer);
    if (!startLine || !endLine) return;

    const block = startLine.closest(".chat-mermaid-block");
    if (!block || block !== endLine.closest(".chat-mermaid-block")) return;

    const start = Number(startLine.dataset.lineNumber);
    const end = Number(endLine.dataset.lineNumber);
    setMermaidSelectionStatus(block, formatMermaidSelection(Math.min(start, end), Math.max(start, end)));
}

function initMermaidBlocks(root) {
    root.querySelectorAll(".chat-mermaid-block").forEach((block) => {
        if (block.dataset.initialised === "true") return;
        block.dataset.initialised = "true";

        block.querySelectorAll(".chat-mermaid-toggle button").forEach((button) => {
            button.addEventListener("click", () => {
                setMermaidView(block, button.dataset.viewTarget || "diagram");
            });
        });

        const diagram = block.querySelector(".chat-mermaid-diagram");
        if (diagram) {
            diagram.addEventListener("mouseup", () => {
                if (block.getAttribute("data-view") !== "diagram") return;
                selectAllMermaidSource(block);
            });
        }
    });
}

async function renderBotMessage(node, text) {
    node.classList.add("markdown-rendered");
    node.innerHTML = await renderMarkdown(text);
    initMermaidBlocks(node);
    renderMermaidBlocks(node);
}

function buildIntroPanel(panel, body) {
    body.innerHTML = "";
    const intro = el("div", { class: "chat-intro" });
    intro.appendChild(el("h3", {}, t("intro_h")));
    intro.appendChild(el("p", {}, t("intro_p")));
    const ul = el("ul", {});
    for (const note of I18N.notes) ul.appendChild(el("li", {}, note));
    intro.appendChild(ul);

    const supported = "gpu" in navigator;
    if (!supported) {
        intro.appendChild(el("p", { class: "chat-msg error" }, t("no_webgpu")));
    } else {
        const loadBtn = el(
            "button",
            { class: "chat-load-btn", onclick: () => startLoad(panel, body, loadBtn, intro) },
            t("load_btn"),
        );
        intro.appendChild(loadBtn);

        // Single cross-book index toggle (the library is English-only, so
        // there is one index rather than per-language checkboxes).
        const indexSection = el("div", { class: "chat-index-options" });
        indexSection.appendChild(el("p", { class: "chat-index-label" }, t("index_section_label")));
        indexSection.appendChild(el("p", { class: "chat-index-help muted" }, t("index_section_help")));
        const cbId = "chat-index-cb-en";
        const wrap = el("label", { class: "chat-index-checkbox", for: cbId });
        const ready = getIndexState() === "ready";
        const cb = el("input", { type: "checkbox", id: cbId, "data-lang": "en" });
        cb.checked = true;       // opt in by default — there's only one index
        if (ready) cb.disabled = true;
        wrap.appendChild(cb);
        wrap.appendChild(el("span", {}, "Cross-book search" + (ready ? " ✓" : "")));
        indexSection.appendChild(wrap);
        intro.appendChild(indexSection);

        intro.appendChild(el("p", { class: "muted" }, t("subtitle")));
    }
    body.appendChild(intro);
}

function refreshIntroIndexCheckbox() {
    const cb = document.getElementById("chat-index-cb-en");
    if (!cb) return;
    const span = cb.parentElement.querySelector("span");
    const ready = getIndexState() === "ready";
    if (ready && span && !span.textContent.includes("✓")) {
        cb.checked = true;
        cb.disabled = true;
        span.textContent = "Cross-book search ✓";
    }
}

// ----- Model state --------------------------------------------------------
let pipelinePromise = null;
let generator = null;
let chatHistory = [];
let pendingAbort = null;

// ----- Cross-page persistence --------------------------------------------
// sessionStorage = current browser tab (open/closed state, conversation,
// per-tab index choice). localStorage = browser-wide "model loaded once"
// flag so future visits skip the intro and auto-load from cache.
const STATE_KEYS = {
    open: "chanma-rl-chat-open",
    history: "chanma-rl-chat-history",
    loaded: "chanma-rl-chat-loaded",          // localStorage (persistent)
    indexLangs: "chanma-rl-chat-index-langs",
};

function readState(key) {
    try { return sessionStorage.getItem(key); } catch (err) { return null; }
}
function writeState(key, value) {
    try { sessionStorage.setItem(key, value); } catch (err) { /* quota / disabled */ }
}
function readPersistent(key) {
    try { return localStorage.getItem(key); } catch (err) { return null; }
}
function writePersistent(key, value) {
    try { localStorage.setItem(key, value); } catch (err) { /* quota / disabled */ }
}
function saveOpenState(open) { writeState(STATE_KEYS.open, open ? "1" : "0"); }
function saveHistoryState() {
    try { writeState(STATE_KEYS.history, JSON.stringify(chatHistory)); } catch (err) {}
}
function markLoadedState() { writePersistent(STATE_KEYS.loaded, "1"); }
function saveCheckedLangsState(langs) {
    try { writeState(STATE_KEYS.indexLangs, JSON.stringify(langs)); } catch (err) {}
}
function loadHistoryState() {
    const raw = readState(STATE_KEYS.history);
    if (!raw) return;
    try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) chatHistory = parsed;
    } catch (err) { /* ignore corrupt entry */ }
}
function wasLoadedBefore() { return readPersistent(STATE_KEYS.loaded) === "1"; }
function getStoredCheckedLangs() {
    const raw = readState(STATE_KEYS.indexLangs);
    if (!raw) return null;
    try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) return parsed;
    } catch (err) {}
    return null;
}

// ----- RAG state ----------------------------------------------------------
let embedderPromise = null;
let embedder = null;
let chunksPromise = null;
let allChunks = null;                                 // [{id, lang, url, title, text}]
const langEmbeddings = new Map();                     // lang → { ids, vectors: Float32Array, dim }
const langIndexPromises = new Map();                  // lang → Promise<void>  (in-flight build)
const langIndexState = new Map();                     // lang → "none"|"indexing"|"ready"|"error"
const langIndexProgress = new Map();                  // lang → { done, total }
let ragDb = null;                                     // IDBDatabase

const SUPPORTED_LANGS = ["en"];

async function loadGenerator(intro) {
    if (generator) return generator;
    if (!pipelinePromise) {
        pipelinePromise = (async () => {
            const tx = await import(/* @vite-ignore */ CDN_URL);
            const { AutoProcessor, Gemma4ForConditionalGeneration, TextStreamer } = tx;
            const modelInfo = MODEL_OPTIONS[ACTIVE_MODEL];
            const progressEl = ensureProgressUI(intro);
            resetProgressState();
            const onProgress = (data) => updateProgress(progressEl, data);
            const processor = await AutoProcessor.from_pretrained(modelInfo.id, {
                progress_callback: onProgress,
            });
            const model = await Gemma4ForConditionalGeneration.from_pretrained(modelInfo.id, {
                dtype: "q4f16",
                device: "webgpu",
                progress_callback: onProgress,
            });
            generator = { processor, model, TextStreamer };
            return generator;
        })();
    }
    return pipelinePromise;
}

// ----- RAG: embedding model + chunks --------------------------------------
async function loadEmbedder() {
    if (embedder) return embedder;
    if (!embedderPromise) {
        embedderPromise = (async () => {
            const tx = await import(/* @vite-ignore */ CDN_URL);
            const { pipeline } = tx;
            // EmbeddingGemma is Gemma-architected; some of its ops can't be
            // compiled to a WebGPU shader pipeline (getBindGroupLayout fails
            // during pipeline creation), so we run the embedder on WASM.
            embedder = await pipeline("feature-extraction", EMBED_MODEL_ID, {
                device: "wasm",
                dtype: "q8",
            });
            return embedder;
        })();
    }
    return embedderPromise;
}

async function loadChunks() {
    if (allChunks) return allChunks;
    if (!chunksPromise) {
        chunksPromise = (async () => {
            const res = await fetch(CHUNKS_URL);
            if (!res.ok) throw new Error(`chunks fetch ${res.status}`);
            allChunks = await res.json();
            return allChunks;
        })();
    }
    return chunksPromise;
}

// ----- Book citation → URL index ------------------------------------------
// Maps the bracketed book citations the assistant emits (e.g. "[Book: The
// Big Short]") to the corresponding book page URL, so renderMarkdown can
// linkify them in-place. Built lazily from allChunks on first use and cached.
let bookUrlIndex = null;

function normaliseTitleKey(s) {
    return String(s).toLowerCase().replace(/[\s\-—–:,.!?'"`]+/g, " ").trim();
}

async function getBookUrlIndex() {
    if (bookUrlIndex) return bookUrlIndex;
    const chunks = await loadChunks();
    const exact = new Map();                 // normalised full title → url
    const prefixes = [];                     // { key, url }, longest first
    for (const c of chunks) {
        if (!c || !c.title || !c.url) continue;
        const key = normaliseTitleKey(c.title);
        if (key && !exact.has(key)) exact.set(key, c.url);
        if (key) prefixes.push({ key, url: c.url });
    }
    // Longest key first so a short title can't match a longer unrelated one.
    prefixes.sort((a, b) => b.key.length - a.key.length);
    bookUrlIndex = { exact, prefixes };
    return bookUrlIndex;
}

// Resolve a citation label (bracket inner text, e.g. "Book: The Big Short")
// to a book URL, or null.
function linkifyCitation(label, idx) {
    // Strip a leading "Book:" prefix if present.
    let title = label.replace(/^book:\s*/i, "");
    const key = normaliseTitleKey(title);
    if (!key) return null;
    // 1. Exact full-title match.
    if (idx.exact.has(key)) return idx.exact.get(key);
    // 2. Prefix match: either the label is a prefix of a canonical title
    //    (LLM abbreviated) or vice versa. Iterate longest-first.
    for (const { key: k, url } of idx.prefixes) {
        if (k.startsWith(key) || key.startsWith(k)) return url;
    }
    return null;
}

// ----- Prebuilt embedding cache (shipped static binary) --------------------
let prebuiltBinPromise = null;   // Promise<boolean> — tried at most once

async function loadPrebuiltEmbeddings() {
    if (prebuiltBinPromise) return prebuiltBinPromise;
    prebuiltBinPromise = (async () => {
        let res;
        try {
            res = await fetch(EMBEDDINGS_BIN_URL);
        } catch (err) {
            console.warn("prebuilt embeddings fetch failed", err);
            return false;
        }
        if (!res.ok) {
            console.warn(`prebuilt embeddings not available (${res.status})`);
            return false;
        }
        const buf = await res.arrayBuffer();
        const dv = new DataView(buf);
        let off = 0;
        const readU32 = () => { const v = dv.getUint32(off, true); off += 4; return v; };

        // Header (52 bytes): magic, version, count, dim, dtype, 32-byte hash.
        const magic = readU32();
        if (magic !== EMBED_BIN_MAGIC) {
            console.warn("prebuilt embeddings: bad magic", magic.toString(16));
            return false;
        }
        const version = readU32();
        if (version !== EMBED_BIN_VERSION) {
            console.warn(`prebuilt embeddings: version ${version} != ${EMBED_BIN_VERSION}`);
            return false;
        }
        const count = readU32();
        const dim = readU32();
        if (dim !== 768) {
            console.warn(`prebuilt embeddings: unexpected dim ${dim}`);
            return false;
        }
        const dtype = readU32();
        if (dtype !== EMBED_BIN_DTYPE_F32) {
            console.warn(`prebuilt embeddings: unsupported dtype ${dtype}`);
            return false;
        }
        const binHashBytes = new Uint8Array(buf, off, 32);
        off += 32;

        // Staleness check: SHA-256 of every chunk text in ascending id order,
        // exactly as computed by build_chatbot_embeddings.mjs. If book content
        // changed, the bin is stale and we must not use it.
        const chunks = await loadChunks();
        const ordered = [...chunks].sort((a, b) => a.id - b.id);
        const textBytes = new TextEncoder().encode(ordered.map((c) => c.text).join(""));
        const digest = await crypto.subtle.digest("SHA-256", textBytes);
        const computed = new Uint8Array(digest);
        for (let i = 0; i < 32; i++) {
            if (computed[i] !== binHashBytes[i]) {
                console.warn("prebuilt embeddings: stale (chunk text changed) — falling back");
                return false;
            }
        }

        // Lang table: LANGS.length × (u32 offset, u32 count), canonical order.
        const langTable = [];
        for (let i = 0; i < EMBED_BIN_LANGS.length; i++) {
            const offset = readU32();
            const n = readU32();
            langTable.push({ lang: EMBED_BIN_LANGS[i], offset, count: n });
        }

        // ids: count × u32, then vectors: count × dim × f32, both grouped by lang.
        const idsAll = new Uint32Array(buf, off, count);
        off += count * 4;
        const vectorsAll = new Float32Array(buf, off, count * dim);

        // Slice each language's contiguous block out and hydrate caches.
        for (const { lang, offset, count: n } of langTable) {
            if (n === 0) {
                langEmbeddings.set(lang, { ids: [], vectors: new Float32Array(0), dim });
                setLangIndexState(lang, "ready");
                continue;
            }
            const ids = Array.from(idsAll.subarray(offset, offset + n));
            // Float32Array.subarray is a view into the same ArrayBuffer; copy
            // so the entry owns its memory and survives any future GC of buf.
            const vectors = new Float32Array(vectorsAll.subarray(offset * dim, (offset + n) * dim));
            const entry = { ids, vectors, dim };
            langEmbeddings.set(lang, entry);
            setLangIndexState(lang, "ready");
            saveCachedEmbeddings(lang, {
                ids,
                vectors: vectors.buffer.slice(0),
                dim,
                model: EMBED_MODEL_ID,
            }).catch((err) => console.warn("prebuilt IDB write failed", lang, err));
        }
        return true;
    })().catch((err) => {
        console.warn("prebuilt embeddings failed", err);
        return false;
    });
    return prebuiltBinPromise;
}

// ----- IndexedDB cache for embeddings -------------------------------------
function openRagDb() {
    if (ragDb) return Promise.resolve(ragDb);
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(RAG_DB_NAME, RAG_DB_VERSION);
        req.onupgradeneeded = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains(RAG_STORE)) {
                db.createObjectStore(RAG_STORE);
            }
        };
        req.onsuccess = () => { ragDb = req.result; resolve(ragDb); };
        req.onerror = () => reject(req.error);
    });
}

async function loadCachedEmbeddings(lang) {
    try {
        const db = await openRagDb();
        const cached = await new Promise((resolve, reject) => {
            const tx = db.transaction(RAG_STORE, "readonly");
            const req = tx.objectStore(RAG_STORE).get(lang);
            req.onsuccess = () => resolve(req.result || null);
            req.onerror = () => reject(req.error);
        });
        if (cached && cached.model && cached.model !== EMBED_MODEL_ID) {
            console.warn(`RAG cache for ${lang}: stale model '${cached.model}' != '${EMBED_MODEL_ID}'`);
            return null;
        }
        return cached || null;
    } catch (err) {
        console.warn("RAG cache read failed", err);
        return null;
    }
}

async function saveCachedEmbeddings(lang, payload) {
    if (payload) payload.model = payload.model || EMBED_MODEL_ID;
    try {
        const db = await openRagDb();
        await new Promise((resolve, reject) => {
            const tx = db.transaction(RAG_STORE, "readwrite");
            tx.objectStore(RAG_STORE).put(payload, lang);
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error);
        });
    } catch (err) {
        console.warn("RAG cache write failed", err);
    }
}

// ----- Embedding & retrieval ----------------------------------------------
async function embedTexts(texts, onBatch) {
    const e = await loadEmbedder();
    const dim = 768;                       // embeddinggemma-300m
    const out = new Float32Array(texts.length * dim);
    const batchSize = 16;
    for (let i = 0; i < texts.length; i += batchSize) {
        const batch = texts.slice(i, i + batchSize);
        const result = await e(batch, { pooling: "mean", normalize: true });
        const data = result.data;
        out.set(data, i * dim);
        if (onBatch) onBatch(Math.min(i + batch.length, texts.length), texts.length);
    }
    return { vectors: out, dim };
}

function setLangIndexState(lang, state, progress) {
    langIndexState.set(lang, state);
    if (progress) langIndexProgress.set(lang, progress);
    else langIndexProgress.delete(lang);
    refreshIndexChip();
    refreshIntroIndexCheckbox();
}

function getLangIndexState(lang) {
    return langIndexState.get(lang) || "none";
}

// For the single-language library, "index state" is just the en state.
function getIndexState() {
    return getLangIndexState("en");
}

async function probeCachedIndex(lang) {
    if (langEmbeddings.has(lang)) return true;
    const cached = await loadCachedEmbeddings(lang);
    if (cached && cached.vectors instanceof ArrayBuffer && Array.isArray(cached.ids)) {
        const entry = {
            ids: cached.ids,
            vectors: new Float32Array(cached.vectors),
            dim: cached.dim || 768,
        };
        langEmbeddings.set(lang, entry);
        setLangIndexState(lang, "ready");
        return true;
    }
    // IDB miss — one-shot prebuilt bin fetch (populates all langs at once).
    if (await loadPrebuiltEmbeddings()) {
        return langEmbeddings.has(lang);
    }
    return false;
}

async function probeAllCachedIndexes() {
    for (const lang of SUPPORTED_LANGS) {
        if (!langIndexState.has(lang)) {
            await probeCachedIndex(lang);
        }
    }
}

// Explicitly build (or rebuild) the embedding index. Triggered from the
// intro-panel checkbox during model load, or from the header chip click.
async function buildIndexFor(lang) {
    if (getLangIndexState(lang) === "ready") return;
    if (langIndexPromises.has(lang)) return langIndexPromises.get(lang);

    const promise = (async () => {
        // Fast path: the shipped prebuilt binary populates every language in
        // one download. Only fall through to per-chunk embedding if the bin
        // is missing, stale, or doesn't cover this lang.
        if (await loadPrebuiltEmbeddings() && langEmbeddings.has(lang)) {
            return; // setLangIndexState("ready") already called inside
        }
        setLangIndexState(lang, "indexing", { done: 0, total: 0 });
        try {
            await loadEmbedder();
            const chunks = await loadChunks();
            const subset = chunks.filter((c) => c.lang === lang);
            if (subset.length === 0) {
                langEmbeddings.set(lang, { ids: [], vectors: new Float32Array(0), dim: 768 });
                setLangIndexState(lang, "ready");
                return;
            }
            const { vectors, dim } = await embedTexts(
                subset.map((c) => c.text),
                (done, total) => setLangIndexState(lang, "indexing", { done, total }),
            );
            const entry = { ids: subset.map((c) => c.id), vectors, dim };
            langEmbeddings.set(lang, entry);
            await saveCachedEmbeddings(lang, {
                ids: entry.ids,
                vectors: vectors.buffer,
                dim,
            });
            setLangIndexState(lang, "ready");
        } catch (err) {
            console.warn("buildIndexFor failed", lang, err);
            setLangIndexState(lang, "error");
            throw err;
        }
    })();

    langIndexPromises.set(lang, promise);
    try {
        await promise;
    } finally {
        langIndexPromises.delete(lang);
    }
}

// ----- Retrieval scope -----------------------------------------------------
// The scope toggle controls which slice of the corpus the assistant searches
// for retrieved excerpts. The SPA tracks the active book + section; these
// values filter which chunks cosineTopK may return.
//
//   "chapter" → only chunks in the current section (this book, this sectionId)
//   "book"    → only chunks in the current book
//   "all"     → every chunk (cross-book)
//
// The current section's own text is ALWAYS injected as priority context in
// buildSystemPrompt; the scope controls the RAG *retrieval* layer that adds
// supporting excerpts from beyond the visible section.
const SCOPE_MODES = ["chapter", "book", "all"];
const SCOPE_LABELS = { chapter: "This chapter", book: "This book", all: "All books" };
let retrievalScope = readPersistentScope() || "book";

function readPersistentScope() {
    try { return localStorage.getItem("chanma-rl-chat-scope") || null; }
    catch { return null; }
}
function persistScope(mode) {
    try { localStorage.setItem("chanma-rl-chat-scope", mode); } catch {}
}

// The chunk "url" for the current book, e.g. "books/the-big-short/index.html".
// In the SPA this comes from window.RL.getState(); the URL no longer changes
// on navigation so window.location is not consulted.
function currentPageBookUrl() {
    if (window.RL) {
        const st = window.RL.getState();
        if (st && st.bookUrl) return st.bookUrl;
    }
    return null;
}

function currentSectionId() {
    if (window.RL) {
        const st = window.RL.getState();
        if (st && st.sectionId) return st.sectionId;
    }
    return null;
}

function cosineTopK(query, entry, k, filter) {
    const { ids, vectors, dim } = entry;
    if (!ids.length) return [];
    // Both query and stored vectors are L2-normalized → dot product = cosine.
    const scores = new Float32Array(ids.length);
    for (let i = 0; i < ids.length; i++) {
        let s = 0;
        const off = i * dim;
        for (let d = 0; d < dim; d++) s += query[d] * vectors[off + d];
        scores[i] = s;
    }
    const order = Array.from(scores.keys()).sort((a, b) => scores[b] - scores[a]);
    const picked = [];
    const perUrlCount = new Map();
    for (const i of order) {
        const id = ids[i];
        const chunk = allChunks[id];
        if (!chunk) continue;
        // Apply scope filter.
        if (filter) {
            if (filter.bookUrl && chunk.url !== filter.bookUrl) continue;
            if (filter.sectionId != null && chunk.sectionId !== filter.sectionId) continue;
        }
        // Diversify: at most 2 chunks per source URL so a single book
        // doesn't crowd out everything else. (In chapter/book scope there is
        // only one URL anyway, so this is a no-op there.)
        const c = perUrlCount.get(chunk.url) || 0;
        if (c >= 2) continue;
        perUrlCount.set(chunk.url, c + 1);
        picked.push({ chunk, score: scores[i] });
        if (picked.length >= k) break;
    }
    return picked;
}

async function retrieveContext(question) {
    try {
        const lang = "en";
        if (getLangIndexState(lang) !== "ready") return [];
        const entry = langEmbeddings.get(lang);
        if (!entry || !entry.ids.length) return [];
        const e = await loadEmbedder();
        const result = await e([question], { pooling: "mean", normalize: true });
        const queryVec = new Float32Array(result.data);
        // Build the scope filter from the active SPA state + the toggle.
        // "all" → no filter (search the whole corpus).
        // "book"/"chapter" → restrict to the current book; chapter further
        //   restricts to the active sectionId. On the home view (no book)
        //   there is nothing to scope to, so fall back to "all".
        let filter = null;
        const bookUrl = currentPageBookUrl();
        const sectionId = currentSectionId();
        if (retrievalScope !== "all" && bookUrl) {
            filter = { bookUrl };
            if (retrievalScope === "chapter" && sectionId) {
                filter.sectionId = sectionId;
            }
        }
        return cosineTopK(queryVec, entry, TOP_K, filter);
    } catch (err) {
        console.warn("retrieveContext failed", err);
        return [];
    }
}

function ensureProgressUI(intro) {
    let p = intro.querySelector(".chat-progress");
    if (p) return p;
    p = el("div", { class: "chat-progress" });
    p.appendChild(el("div", { class: "chat-progress-label" }, el("span", {}, t("loading")), el("span", { class: "pct" }, "0%")));
    const bar = el("div", { class: "chat-progress-bar" });
    bar.appendChild(el("div", { class: "chat-progress-fill" }));
    p.appendChild(bar);
    intro.appendChild(p);
    return p;
}

const progressFiles = new Map();   // file name → { loaded, total }
let progressMaxPct = 0;            // monotonic clamp on displayed value

function updateProgress(p, data) {
    if (!data) return;
    const key = data.file || data.name;
    if (key) {
        const entry = progressFiles.get(key) || { loaded: 0, total: 0 };
        if (data.status === "done") {
            entry.loaded = entry.total || data.total || data.loaded || entry.loaded;
        } else if (data.loaded != null && data.total != null && data.total > 0) {
            entry.loaded = data.loaded;
            entry.total = data.total;
        } else if (typeof data.progress === "number" && data.total > 0) {
            entry.loaded = (data.progress / 100) * data.total;
            entry.total = data.total;
        }
        progressFiles.set(key, entry);
    }

    let sumLoaded = 0;
    let sumTotal = 0;
    for (const e of progressFiles.values()) {
        sumLoaded += e.loaded || 0;
        sumTotal += e.total || 0;
    }
    const modelInfo = MODEL_OPTIONS[ACTIVE_MODEL];
    const knownTotal = (modelInfo && modelInfo.size_mb ? modelInfo.size_mb * 1024 * 1024 : 0);
    const denom = Math.max(sumTotal, knownTotal);
    if (denom <= 0) return;

    let pct = (sumLoaded / denom) * 100;
    if (!Number.isFinite(pct)) return;
    pct = Math.max(0, Math.min(100, pct));
    if (pct < progressMaxPct) pct = progressMaxPct;
    progressMaxPct = pct;

    const fill = p.querySelector(".chat-progress-fill");
    const lbl = p.querySelector(".pct");
    if (fill) fill.style.width = pct.toFixed(1) + "%";
    if (lbl) lbl.textContent = pct.toFixed(0) + "%";
}

function resetProgressState() {
    progressFiles.clear();
    progressMaxPct = 0;
}

async function startLoad(panel, body, loadBtn, intro) {
    loadBtn.disabled = true;
    loadBtn.textContent = t("loading");

    const checkedLangs = Array.from(intro.querySelectorAll(".chat-index-checkbox input:checked"))
        .map((cb) => cb.dataset.lang)
        .filter((lang) => SUPPORTED_LANGS.includes(lang));
    saveCheckedLangsState(checkedLangs);

    try {
        // RAG warmup: fetch chunks JSON + embedder model in parallel with
        // the Gemma download.
        loadEmbedder().catch((err) => console.warn("embedder load failed", err));
        loadChunks().catch((err) => console.warn("chunks load failed", err));

        const indexingPromise = (async () => {
            await probeAllCachedIndexes();
            for (const lang of checkedLangs) {
                if (getLangIndexState(lang) === "ready") continue;
                try {
                    await buildIndexFor(lang);
                } catch (err) {
                    /* chip shows error */
                }
            }
        })();

        await loadGenerator(intro);
        markLoadedState();
        showChatUI(panel, body);
        refreshIndexChip();
        indexingPromise.catch((err) => console.warn("indexing failed", err));
    } catch (err) {
        const errBox = el("p", { class: "chat-msg error" }, t("load_failed") + (err && err.message || String(err)));
        intro.appendChild(errBox);
        loadBtn.disabled = false;
        loadBtn.textContent = t("load_btn");
    }
}

// ----- Chat UI ------------------------------------------------------------
function ensureComposer(panel) {
    let composer = panel.querySelector(".chat-composer");
    if (composer) return composer;
    composer = el("div", { class: "chat-composer" });
    const ta = el("textarea", { placeholder: t("placeholder"), rows: "1" });
    ta.addEventListener("input", () => {
        ta.style.height = "auto";
        ta.style.height = Math.min(120, ta.scrollHeight) + "px";
    });
    ta.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend(panel, ta);
        }
    });
    const sendBtn = el(
        "button",
        { title: t("send"), onclick: () => handleSend(panel, ta) },
        "↑",
    );
    composer.appendChild(ta);
    composer.appendChild(sendBtn);
    panel.appendChild(composer);
    return composer;
}

// Build the status-bar scope row: "Search:" label + 3 buttons (This chapter /
// This book / All books) + an "unavailable" message shown when the index
// isn't ready. The active button is highlighted; only one can be selected.
function buildScopeBar() {
    const bar = el("div", { class: "chat-scope-bar" });
    bar.appendChild(el("span", { class: "chat-scope-label" }, "Search:"));
    const btns = el("div", { class: "chat-scope-toggle", role: "group", "aria-label": "Search scope" });
    for (const mode of SCOPE_MODES) {
        const btn = el("button", {
            type: "button",
            class: "chat-scope-btn" + (mode === retrievalScope ? " active" : ""),
            "data-scope": mode,
            title: `Search ${SCOPE_LABELS[mode].toLowerCase()} for supporting excerpts`,
            onclick: () => setScope(mode),
        }, SCOPE_LABELS[mode]);
        btns.appendChild(btn);
    }
    bar.appendChild(btns);
    bar.appendChild(el("span", { class: "chat-scope-unavailable" }, "Search not available"));
    return bar;
}

function setScope(mode) {
    if (!SCOPE_MODES.includes(mode)) return;
    retrievalScope = mode;
    persistScope(mode);
    const row = document.querySelector(".chat-scope-toggle");
    if (row) {
        row.querySelectorAll(".chat-scope-btn").forEach((b) => {
            b.classList.toggle("active", b.getAttribute("data-scope") === mode);
        });
    }
}

// Refresh the scope bar's state: highlight the active button, disable
// chapter/book when no book is open, and disable ALL buttons (showing
// "Search not available") when the cross-book index isn't ready. Called
// after SPA navigation and after index state changes.
function refreshScopeBar() {
    const bar = document.querySelector(".chat-scope-bar");
    if (!bar) return;
    const indexReady = getIndexState() === "ready";
    const hasBook = !!(window.RL && window.RL.getState && window.RL.getState().bookUrl);
    bar.classList.toggle("unavailable", !indexReady);
    bar.querySelectorAll(".chat-scope-btn").forEach((b) => {
        const mode = b.getAttribute("data-scope");
        b.classList.toggle("active", mode === retrievalScope);
        // Disabled when the index isn't ready, or when chapter/book are
        // selected but no book is open.
        let disabled = !indexReady;
        if (!disabled && mode !== "all" && !hasBook) disabled = true;
        b.disabled = disabled;
        // If the active mode just became disabled, fall back to "all".
        if (disabled && mode === retrievalScope && retrievalScope !== "all") setScope("all");
    });
}

function setComposerEnabled(panel, enabled) {
    const composer = panel.querySelector(".chat-composer");
    if (!composer) return;
    const ta = composer.querySelector("textarea");
    const btn = composer.querySelector("button");
    if (ta) ta.disabled = !enabled;
    if (btn) btn.disabled = !enabled;
}

async function renderHistoryIntoBody(body) {
    for (const msg of chatHistory) {
        if (msg.role === "user") {
            appendMsg(body, "user", msg.content);
        } else if (msg.role === "assistant") {
            const node = appendMsg(body, "bot", "");
            await renderBotMessage(node, msg.content);
        }
    }
}

function showChatUI(panel, body) {
    body.innerHTML = "";
    body.classList.add("chat-body");
    if (chatHistory.length) {
        renderHistoryIntoBody(body).catch((err) => console.warn("history render failed", err));
    } else {
        body.appendChild(el("div", { class: "chat-msg system" }, t("ready")));
    }
    ensureComposer(panel);
    body.scrollTop = body.scrollHeight;
}

function appendMsg(body, role, text) {
    const node = el("div", { class: "chat-msg " + role }, text);
    body.appendChild(node);
    body.scrollTop = body.scrollHeight;
    return node;
}

async function handleSend(panel, textarea) {
    const userText = textarea.value.trim();
    if (!userText || !generator) return;
    textarea.value = "";
    textarea.style.height = "auto";

    const body = panel.querySelector(".chat-body");
    appendMsg(body, "user", userText);
    const botNode = appendMsg(body, "bot", "");
    botNode.textContent = t("thinking");

    chatHistory.push({ role: "user", content: userText });
    if (chatHistory.length > MAX_HISTORY_TURNS * 2) {
        chatHistory = chatHistory.slice(-MAX_HISTORY_TURNS * 2);
    }
    saveHistoryState();

    const systemPrompt = await buildSystemPrompt(userText);
    const messages = [
        { role: "system", content: systemPrompt },
        ...chatHistory,
    ];

    try {
        const { processor, model, TextStreamer } = generator;
        const prompt = processor.apply_chat_template(messages, {
            enable_thinking: false,
            add_generation_prompt: true,
            tokenize: false,
        });
        const inputs = await processor(prompt, null, null, { add_special_tokens: false });

        let acc = "";
        let started = false;
        const streamer = new TextStreamer(processor.tokenizer, {
            skip_prompt: true,
            skip_special_tokens: true,
            callback_function: (text) => {
                if (typeof text !== "string" || !text) return;
                acc += text;
                if (!started) { botNode.textContent = ""; started = true; }
                botNode.textContent = acc;
                body.scrollTop = body.scrollHeight;
            },
        });

        const outputs = await model.generate({
            ...inputs,
            max_new_tokens: MAX_NEW_TOKENS,
            do_sample: false,
            streamer,
        });

        if (!started) {
            const inputLen = inputs.input_ids.dims.at(-1);
            const decoded = processor.batch_decode(
                outputs.slice(null, [inputLen, null]),
                { skip_special_tokens: true },
            );
            acc = (decoded && decoded[0]) || "";
            botNode.textContent = acc;
        }
        await renderBotMessage(botNode, acc);
        chatHistory.push({ role: "assistant", content: acc });
        saveHistoryState();
    } catch (err) {
        botNode.classList.add("error");
        botNode.textContent = t("gen_failed") + (err && err.message || String(err));
    }
}

// ----- Wiring -------------------------------------------------------------
function buildPanel() {
    const panel = el("aside", { class: "chat-panel", id: "chat-panel", role: "dialog", "aria-label": t("title") });
    const header = el("div", { class: "chat-header" });
    const titleWrap = el("div", { class: "chat-title" });
    titleWrap.appendChild(el("span", { class: "chat-header-title" }, t("title")));
    titleWrap.appendChild(el("span", { class: "chat-subtitle" }, t("subtitle")));
    header.appendChild(titleWrap);
    header.appendChild(el("button", { class: "chat-header-clear", onclick: () => clearConversation(panel), title: t("clear") }, "↻"));
    header.appendChild(el("button", { class: "chat-header-close", onclick: () => togglePanel(false), title: t("close") }, "✕"));
    panel.appendChild(header);

    const statusBar = el("div", { class: "chat-status-bar" });
    statusBar.appendChild(buildScopeBar());
    panel.appendChild(statusBar);

    const body = el("div", { class: "chat-body" });
    panel.appendChild(body);
    document.body.appendChild(panel);
    if (wasLoadedBefore()) {
        loadHistoryState();
        restoreLoadedSession(panel, body).catch((err) =>
            console.warn("auto-load on panel build failed", err));
    } else {
        buildIntroPanel(panel, body);
    }
    probeAllCachedIndexes().then(refreshIndexChip).catch(() => refreshIndexChip());
    return panel;
}

async function restoreLoadedSession(panel, body) {
    body.innerHTML = "";
    body.classList.add("chat-body");

    const progressHost = el("div", { class: "chat-intro chat-restore-host" });
    body.appendChild(progressHost);
    if (chatHistory.length) await renderHistoryIntoBody(body);

    ensureComposer(panel);
    setComposerEnabled(panel, false);

    const checkedLangs = getStoredCheckedLangs() || ["en"];

    try {
        loadEmbedder().catch((err) => console.warn("embedder load failed", err));
        loadChunks().catch((err) => console.warn("chunks load failed", err));
        const indexingPromise = (async () => {
            await probeAllCachedIndexes();
            for (const lang of checkedLangs) {
                if (getLangIndexState(lang) === "ready") continue;
                try { await buildIndexFor(lang); } catch (err) { /* chip shows error */ }
            }
        })();

        await loadGenerator(progressHost);
        progressHost.remove();
        setComposerEnabled(panel, true);
        refreshIndexChip();
        indexingPromise.catch((err) => console.warn("indexing failed", err));
    } catch (err) {
        progressHost.appendChild(el("p", { class: "chat-msg error" },
            t("load_failed") + (err && err.message || String(err))));
    }
}

// Update the scope bar to reflect the current index state. When the index
// isn't ready, the scope buttons are disabled and the "unavailable" span
// shows the reason (building %, failed, or just "not available"). Clicking
// the span when the index is none/error triggers a (re)build.
function refreshIndexChip() {
    const bar = document.querySelector(".chat-scope-bar");
    if (!bar) return;
    const state = getIndexState();
    const unavail = bar.querySelector(".chat-scope-unavailable");
    bar.classList.toggle("indexing", state === "indexing");
    if (state === "ready") {
        bar.classList.remove("unavailable");
        if (unavail) unavail.textContent = "";
    } else if (state === "indexing") {
        bar.classList.add("unavailable");
        const p = langIndexProgress.get("en") || { done: 0, total: 0 };
        const pct = p.total ? Math.round(100 * p.done / p.total) : 0;
        if (unavail) unavail.textContent = `Indexing… ${pct}%`;
    } else if (state === "error") {
        bar.classList.add("unavailable");
        if (unavail) unavail.textContent = "Index failed — click to retry";
    } else {
        bar.classList.add("unavailable");
        if (unavail) unavail.textContent = "Search not available";
    }
    refreshScopeBar();
}

async function onIndexChipClick() {
    const state = getIndexState();
    if (state === "ready" || state === "indexing") return;
    try {
        await buildIndexFor("en");
    } catch (err) {
        /* setLangIndexState already marked it as error */
    }
}

function clearConversation(panel) {
    chatHistory = [];
    saveHistoryState();
    const body = panel.querySelector(".chat-body");
    if (!body) return;
    if (generator) {
        body.innerHTML = "";
        body.appendChild(el("div", { class: "chat-msg system" }, t("ready")));
    } else {
        buildIntroPanel(panel, body);
    }
}

function togglePanel(force) {
    const panel = document.getElementById("chat-panel") || buildPanel();
    const open = typeof force === "boolean" ? force : !panel.classList.contains("open");
    panel.classList.toggle("open", open);
    document.body.classList.toggle("chat-open", open);
    saveOpenState(open);
}

function buildFab() {
    const fab = el(
        "button",
        { class: "chat-fab", id: "chat-fab", title: t("title"), "aria-label": t("title"), onclick: () => togglePanel() },
    );
    fab.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-8.5 8.5 8.5 8.5 0 0 1-3.6-.8L3 21l1.9-5.7A8.38 8.38 0 0 1 3 11.5a8.5 8.5 0 0 1 17 0z"/></svg><span class="badge">AI</span>`;
    document.body.appendChild(fab);
}

// If the panel was open on the previous page (sessionStorage), pop it back up.
async function autoRestorePanel() {
    if (readState(STATE_KEYS.open) !== "1") return;
    loadHistoryState();
    const panel = document.getElementById("chat-panel") || buildPanel();
    panel.classList.add("open");
    document.body.classList.add("chat-open");
}

document.addEventListener("DOMContentLoaded", () => {
    buildFab();
    autoRestorePanel().catch((err) => console.warn("autoRestorePanel failed", err));
    // The SPA dispatches rl:sectionchange on book load, scroll, and home
    // return. Refresh the scope bar so chapter/book are only enabled when a
    // book is actually open, and the unavailable state tracks the index.
    document.addEventListener("rl:sectionchange", () => { refreshIndexChip(); });
    // Clicking the "Search not available" / error message triggers a build.
    document.addEventListener("click", (e) => {
        if (e.target.closest && e.target.closest(".chat-scope-unavailable")) {
            onIndexChipClick();
        }
    });
    refreshIndexChip();
});

document.addEventListener("selectionchange", updateMermaidSelectionFromPage);
