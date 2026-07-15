/* spa.js — single-page-app shell for the reading library.
 *
 * Converts the portal into a hash-routed SPA so book content loads into a
 * persistent reader view instead of navigating to a new page. The chatbot
 * module (chatbot.js) stays loaded across book switches, preserving the LLM
 * pipeline, embeddings, and conversation history in memory.
 *
 * Routes:
 *   #/                     → home (portal / catalog)
 *   #/books/<slug>         → reader for books/<slug>/index.md
 *
 * Public API (window.RL) lets chatbot.js read the active reading context:
 *   RL.getState()              → { view, slug, bookUrl, bookTitle, sectionId, sectionTitle }
 *   RL.getActiveSectionText()  → prose text of the current section (for the
 *                                chatbot's priority context), or "" on home.
 *   RL.onSectionChange(cb)     → register a listener fired when the active
 *                                section changes (scroll or book switch).
 *
 * The SPA tracks the active <section> via IntersectionObserver on the reader
 * scroll container and exposes it so the chatbot's scope toggle can filter
 * retrieval to "this chapter".
 */
import { marked } from "./vendor/marked.esm.js";

(function () {
  "use strict";

  marked.setOptions({ gfm: true, breaks: false });

  const HOME_HASH = "#/";
  const SECTION_MARKER_RE = /<!--\s*rl-section\s+([^>]+?)\s*-->/g;
  const readerEl = () => document.getElementById("reader-content");
  const scrollEl = () => document.querySelector(".rl-reader");

  // ── State ──────────────────────────────────────────────────────────
  let currentSlug = null;
  let currentBookTitle = "";
  let currentBookUrl = "";
  let activeSection = null; // { id, title, number, el }
  const sectionListeners = new Set();
  let observer = null;

  // ── Router ─────────────────────────────────────────────────────────
  function parseHash() {
    const h = location.hash || HOME_HASH;
    // A trailing "//<sectionId>" deep-links a section within the book:
    //   #/books/<slug>             → book only
    //   #/books/<slug>//<sectionId> → book + scroll to section
    // "//" avoids colliding with the leading "#" of the hash itself.
    const m = h.match(/^#\/books\/([^/?#]+)(?:\/\/([^/?#]+))?/);
    if (m) {
      return {
        view: "reader",
        slug: decodeURIComponent(m[1]),
        sectionId: m[2] ? decodeURIComponent(m[2]) : null,
      };
    }
    return { view: "home", slug: null, sectionId: null };
  }

  function navigate(hash) {
    if (location.hash !== hash) location.hash = hash;
    else route(); // same hash → still re-run
  }

  function route() {
    const { view, slug, sectionId } = parseHash();
    const home = document.getElementById("view-home");
    const reader = document.getElementById("view-reader");
    if (view === "reader" && slug) {
      home.classList.remove("active");
      reader.classList.add("active");
      document.body.classList.add("rl-reading");
      if (slug !== currentSlug) {
        loadBook(slug, sectionId);
      } else {
        // Same book already open — just scroll to the requested section.
        if (sectionId) scrollToSection(sectionId);
        notifySectionChange();
      }
    } else {
      reader.classList.remove("active");
      home.classList.add("active");
      document.body.classList.remove("rl-reading");
      currentSlug = null;
      currentBookTitle = "";
      currentBookUrl = "";
      activeSection = null;
      if (observer) observer.disconnect();
      notifySectionChange();
    }
  }

  window.addEventListener("hashchange", route);

  // ── Markdown parsing / rendering ───────────────────────────────────
  function parseFrontMatter(text) {
    const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n/);
    if (!match) return { meta: {}, body: text };
    const meta = {};
    for (const line of match[1].split("\n")) {
      const kv = line.match(/^([\w-]+):\s*(.+)$/);
      if (kv) meta[kv[1]] = kv[2].trim();
    }
    return { meta, body: text.slice(match[0].length) };
  }

  function parseSectionAttrs(attrStr) {
    const attrs = {};
    attrStr.replace(/([\w-]+)="([^"]*)"/g, (_, key, value) => {
      attrs[key] = value;
    });
    return attrs;
  }

  function splitSections(body) {
    const parts = body.split(SECTION_MARKER_RE);
    const preamble = parts[0] || "";
    const sections = [];
    for (let i = 1; i + 1 < parts.length; i += 2) {
      sections.push({
        attrs: parseSectionAttrs(parts[i]),
        markdown: parts[i + 1] || "",
      });
    }
    return { preamble, sections };
  }

  function rewriteUrls(root, slug) {
    const bookBase = `books/${slug}/`;
    const rewriteUrl = (u) => {
      if (!u || /^(data:|https?:|\/\/|#|mailto:)/.test(u)) return u;
      return bookBase + u.replace(/^\.?\/+/, "");
    };
    root.querySelectorAll("img[src]").forEach((img) => {
      img.setAttribute("src", rewriteUrl(img.getAttribute("src")));
    });
    const xlinkNs = "http://www.w3.org/1999/xlink";
    root.querySelectorAll("image").forEach((el) => {
      for (const attr of ["href", "xlink:href"]) {
        const raw =
          attr === "xlink:href"
            ? el.getAttribute("xlink:href") || el.getAttributeNS(xlinkNs, "href")
            : el.getAttribute(attr);
        if (!raw) continue;
        const next = rewriteUrl(raw);
        if (attr === "xlink:href") {
          el.setAttributeNS(xlinkNs, "href", next);
        } else {
          el.setAttribute(attr, next);
        }
      }
    });
    root.querySelectorAll('img[srcset], source[srcset]').forEach((el) => {
      const ss = el.getAttribute("srcset");
      if (ss) {
        el.setAttribute(
          "srcset",
          ss.split(",").map((part) => {
            const t = part.trim();
            const sp = t.split(/\s+/);
            sp[0] = rewriteUrl(sp[0]);
            return sp.join(" ");
          }).join(", ")
        );
      }
    });
    root.querySelectorAll('[style*="url("]').forEach((el) => {
      const st = el.getAttribute("style");
      if (st && /url\(/.test(st)) {
        el.setAttribute(
          "style",
          st.replace(
            /url\(\s*(['"]?)(?!data:|https?:|\/\/)(\.?\/?)([^'")]+)\s*\)/g,
            (_m, q, _d, path) =>
              `url(${q}${bookBase}${path.replace(/^\.?\/+/, "")}${q})`
          )
        );
      }
    });
    root.querySelectorAll("a[href]").forEach((a) => {
      const href = a.getAttribute("href");
      if (href && !/^(https?:|\/\/|mailto:|#)/.test(href)) {
        a.setAttribute("href", rewriteUrl(href));
      }
    });
  }

  function renderBookMarkdown(mdText, slug) {
    const { meta, body } = parseFrontMatter(mdText);
    const { preamble, sections } = splitSections(body);
    const contentRoot = document.createElement("article");

    if (preamble.trim()) {
      const pre = document.createElement("div");
      pre.className = "rl-preamble";
      pre.innerHTML = marked.parse(preamble);
      contentRoot.appendChild(pre);
    }

    for (const sec of sections) {
      const el = document.createElement("section");
      const sectionClass = sec.attrs.class || "epub-section";
      el.id = sec.attrs.id || "";
      el.className = sectionClass;
      if (sec.attrs.title) el.dataset.sectionTitle = sec.attrs.title;
      if (sec.attrs.kicker) {
        const kicker = document.createElement("div");
        kicker.className = sectionClass === "chapter" ? "chapter-number" : "section-kicker";
        kicker.textContent = sec.attrs.kicker;
        el.appendChild(kicker);
      }
      const inner = document.createElement("div");
      inner.className = sectionClass === "chapter" ? "chapter-body" : "section-body";
      inner.innerHTML = marked.parse(sec.markdown);
      el.appendChild(inner);
      contentRoot.appendChild(el);
    }

    rewriteUrls(contentRoot, slug);

    let bookTitle = meta.title || "";
    if (!bookTitle) {
      const h1 = contentRoot.querySelector("h1");
      bookTitle = h1 ? h1.textContent.trim() : slug;
    }
    return { contentRoot, bookTitle };
  }

  // ── Book loading ───────────────────────────────────────────────────
  // `sectionId`, when set, deep-links to a section after the book renders:
  // the chatbot's chapter citations navigate to #/books/<slug>//<sectionId>.
  async function loadBook(slug, sectionId = null) {
    const container = readerEl();
    const scroller = scrollEl();
    if (!container || !scroller) return;
    currentSlug = slug;
    currentBookUrl = `books/${slug}/index.md`;
    notifySectionChange();
    const url = currentBookUrl;
    scroller.classList.add("rl-loading");
    container.innerHTML = "";
    if (observer) observer.disconnect();
    activeSection = null;
    updateTopBar();

    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const md = await res.text();
      const { contentRoot, bookTitle } = renderBookMarkdown(md, slug);
      container.appendChild(contentRoot);
      currentBookTitle = bookTitle;
    } catch (err) {
      container.innerHTML = `<p style="color:#f87171">Failed to load book: ${escapeHtml(err.message)}</p>`;
      scroller.classList.remove("rl-loading");
      return;
    }

    scroller.classList.remove("rl-loading");
    // Deep-link: scroll to the requested section instead of the top. Falls
    // back to the top if the section isn't found in this book.
    if (sectionId && scrollToSection(sectionId)) {
      // scrolled successfully
    } else {
      scroller.scrollTop = 0;
    }
    updateTopBar();
    wireSectionObserver();
    notifySectionChange();
  }

  // Scroll the reader to a section by id. Returns true if the section existed
  // and was scrolled into view; false if it wasn't found (caller falls back to top).
  function scrollToSection(sectionId) {
    const container = readerEl();
    if (!container || !sectionId) return false;
    // sectionId is a DOM id; escape any chars that would break a selector.
    const target = container.querySelector(`#${CSS.escape(sectionId)}`);
    if (!target) return false;
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    return true;
  }

  // ── Active-section tracking via IntersectionObserver ───────────────
  function wireSectionObserver() {
    const scroller = scrollEl();
    const container = readerEl();
    if (!scroller || !container) return;
    const sections = container.querySelectorAll("section.epub-section, section.chapter");
    if (!sections.length) return;
    if (observer) observer.disconnect();
    observer = new IntersectionObserver(
      (entries) => {
        let best = null;
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          if (!best || e.boundingClientRect.top < best.boundingClientRect.top) best = e;
        }
        if (best) setActiveSection(best.target);
      },
      { root: scroller, rootMargin: "-3.25rem 0px -70% 0px", threshold: 0 }
    );
    sections.forEach((s) => observer.observe(s));
    if (!activeSection) setActiveSection(sections[0]);
  }

  function setActiveSection(el) {
    const id = el.id || "";
    const title = sectionTitle(el);
    const number = sectionNumber(el, id);
    activeSection = { id, title, number, el };
    updateTopBar();
    notifySectionChange();
  }

  function sectionTitle(el) {
    if (el.dataset.sectionTitle) return el.dataset.sectionTitle;
    const h1ct = el.querySelector("h1.chapter-title");
    if (h1ct && h1ct.textContent.trim()) return h1ct.textContent.trim();
    const h2 = el.querySelector("h2");
    if (h2 && h2.textContent.trim()) return h2.textContent.trim();
    return "";
  }

  function sectionNumber(el, id) {
    const cnum = el.querySelector(".chapter-number");
    if (cnum && cnum.textContent.trim()) {
      const m = cnum.textContent.match(/(\d+)/);
      if (m) return parseInt(m[1], 10);
    }
    const m = id && id.match(/section-(\d+)/);
    if (m) return parseInt(m[1], 10);
    return null;
  }

  // ── Top bar ────────────────────────────────────────────────────────
  function updateTopBar() {
    const titleEl = document.querySelector(".rl-book-title");
    const chapEl = document.querySelector(".rl-chapter");
    if (!titleEl || !chapEl) return;
    titleEl.textContent = currentBookTitle || "";
    if (activeSection && activeSection.title) {
      const numHtml = activeSection.number != null
        ? `<span class="rl-chapter-num">§${activeSection.number}</span>` : "";
      chapEl.innerHTML = `${numHtml}<span class="rl-chapter-title">${escapeHtml(activeSection.title)}</span>`;
    } else if (currentBookTitle) {
      chapEl.innerHTML = `<span class="rl-chapter-placeholder">—</span>`;
    } else {
      chapEl.innerHTML = "";
    }
  }

  // ── Public API for chatbot.js ──────────────────────────────────────
  const RL = {
    getState() {
      return {
        view: currentSlug ? "reader" : "home",
        slug: currentSlug,
        bookUrl: currentBookUrl,
        bookTitle: currentBookTitle,
        sectionId: activeSection ? activeSection.id : "",
        sectionTitle: activeSection ? activeSection.title : "",
      };
    },
    getActiveSectionText() {
      if (!activeSection || !activeSection.el) return "";
      let text = activeSection.el.innerText || activeSection.el.textContent || "";
      text = text.replace(/\s+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
      return text;
    },
    onSectionChange(cb) {
      sectionListeners.add(cb);
      try { cb(RL.getState()); } catch (e) { console.warn(e); }
    },
    navigate,
  };
  window.RL = RL;

  function notifySectionChange() {
    const st = RL.getState();
    sectionListeners.forEach((cb) => {
      try { cb(st); } catch (e) { console.warn(e); }
    });
    document.dispatchEvent(new CustomEvent("rl:sectionchange", { detail: st }));
  }

  // ── Click interception ─────────────────────────────────────────────
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if (!a) return;
    const href = a.getAttribute("href");
    if (!href) return;
    if (href.startsWith("#") && href.length > 1 && !href.startsWith("#/")) {
      const container = readerEl();
      if (container) {
        const target = container.querySelector(href);
        if (target) {
          e.preventDefault();
          target.scrollIntoView({ behavior: "smooth", block: "start" });
          return;
        }
      }
      return;
    }
    // Book citation links from the chatbot: books/<slug>/index.md, optionally
    // with a "#<sectionId>" fragment for a chapter deep-link. Route through the
    // hash router so the SPA (and the chatbot panel) stay loaded.
    const bookMatch = href.match(
      /^(?:\.\/)?books\/([^/?#]+)\/index\.(?:html|md)(?:#([^/?#]+))?$/
    );
    if (bookMatch) {
      e.preventDefault();
      const sectionPart = bookMatch[2] ? `//${bookMatch[2]}` : "";
      navigate(`#/books/${bookMatch[1]}${sectionPart}`);
      return;
    }
  });

  // ── Helpers ────────────────────────────────────────────────────────
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ── Boot ───────────────────────────────────────────────────────────
  function boot() {
    const home = document.getElementById("view-home");
    const reader = document.getElementById("view-reader");
    if (home && reader) {
      home.classList.add("active");
      reader.classList.remove("active");
    }
    const homeBtn = document.querySelector(".rl-home-btn");
    if (homeBtn) homeBtn.addEventListener("click", () => navigate(HOME_HASH));
    route();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
