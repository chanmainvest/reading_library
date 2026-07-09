/* spa.js — single-page-app shell for the reading library.
 *
 * Converts the portal into a hash-routed SPA so book content loads into a
 * persistent reader view instead of navigating to a new page. The chatbot
 * module (chatbot.js) stays loaded across book switches, preserving the LLM
 * pipeline, embeddings, and conversation history in memory.
 *
 * Routes:
 *   #/                     → home (portal / catalog)
 *   #/books/<slug>         → reader for books/<slug>/index.html
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
(function () {
  "use strict";

  const HOME_HASH = "#/";
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
    const m = h.match(/^#\/books\/([^/?#]+)/);
    if (m) return { view: "reader", slug: decodeURIComponent(m[1]) };
    return { view: "home", slug: null };
  }

  function navigate(hash) {
    if (location.hash !== hash) location.hash = hash;
    else route(); // same hash → still re-run
  }

  function route() {
    const { view, slug } = parseHash();
    const home = document.getElementById("view-home");
    const reader = document.getElementById("view-reader");
    if (view === "reader" && slug) {
      home.classList.remove("active");
      reader.classList.add("active");
      document.body.classList.add("rl-reading");
      if (slug !== currentSlug) loadBook(slug);
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

  // ── CSS scoping: prefix every selector in a book's <style> with
  //    #reader-content so book typography can't leak into the shell. ───
  function scopeCss(cssText, slug) {
    const prefix = "#reader-content";
    // Rewrite relative url()/src references to resolve from the book folder.
    const bookBase = `books/${slug}/`;
    let css = cssText
      // url(./images/x.png) and url(images/x.png) → url(books/<slug>/images/x.png)
      .replace(/url\(\s*(['"]?)(?!data:|https?:|\/\/)(\.?\/?)([^'")]+)\s*\)/g,
        (_, q, _dot, path) => `url(${q}${bookBase}${path}${q})`);
    // Prefix every selector in a selector list. Split on top-level commas
    // inside a rule's selector part (before '{'). A simple state machine
    // handles braces so @media/@supports blocks are scoped correctly: their
    // inner rules get the prefix but the @-rule itself does not.
    const out = [];
    let i = 0;
    while (i < css.length) {
      const brace = css.indexOf("{", i);
      if (brace === -1) { out.push(css.slice(i)); break; }
      const rule = css.slice(i, brace);
      const close = findMatchingBrace(css, brace);
      const body = css.slice(brace, close + 1);
      if (rule.startsWith("@")) {
        // @media / @supports — scope inner selectors, keep the @-rule wrapper.
        // @keyframes / @font-face are exempt (contents aren't selectors).
        if (/^@keyframes\b/i.test(rule) || /^@font-face\b/i.test(rule) || /^@import\b/i.test(rule)) {
          out.push(rule, body);
        } else {
          out.push(rule, "{", scopeInner(css.slice(brace + 1, close), prefix), "}");
        }
      } else {
        out.push(scopeSelectorList(rule, prefix), body);
      }
      i = close + 1;
    }
    return out.join("");
  }

  function findMatchingBrace(s, openIdx) {
    let depth = 0;
    for (let j = openIdx; j < s.length; j++) {
      if (s[j] === "{") depth++;
      else if (s[j] === "}") { depth--; if (depth === 0) return j; }
    }
    return s.length - 1;
  }

  function scopeSelectorList(list, prefix) {
    return list.split(",").map((sel) => {
      sel = sel.trim();
      if (!sel) return sel;
      // Don't prefix @-rules or bare commas; prefix everything else.
      // Handle combinators by prefixing the whole compound.
      return `${prefix} ${sel}`;
    }).join(", ");
  }

  function scopeInner(css, prefix) {
    // Scope the body of an @media/@supports block by recursing on its rules.
    // Each inner rule is "selector { ... }" — prefix the selector part.
    const out = [];
    let i = 0;
    while (i < css.length) {
      const brace = css.indexOf("{", i);
      if (brace === -1) { out.push(css.slice(i)); break; }
      const sel = css.slice(i, brace).trim();
      const close = findMatchingBrace(css, brace);
      const body = css.slice(brace + 1, close);
      if (sel.startsWith("@")) {
        // Nested @-rule inside @media (rare) — recurse.
        out.push(sel, "{", scopeInner(body, prefix), "}");
      } else {
        out.push(scopeSelectorList(sel, prefix), "{", body, "}");
      }
      i = close + 1;
    }
    return out.join("");
  }

  // ── Book loading ───────────────────────────────────────────────────
  async function loadBook(slug) {
    const container = readerEl();
    const scroller = scrollEl();
    if (!container || !scroller) return;
    currentSlug = slug;
    currentBookUrl = `books/${slug}/index.html`;
    const url = currentBookUrl;
    scroller.classList.add("rl-loading");
    container.innerHTML = "";
    if (observer) observer.disconnect();
    activeSection = null;
    updateTopBar();

    let doc;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const html = await res.text();
      doc = new DOMParser().parseFromString(html, "text/html");
    } catch (err) {
      container.innerHTML = `<p style="color:#f87171">Failed to load book: ${escapeHtml(err.message)}</p>`;
      scroller.classList.remove("rl-loading");
      return;
    }

    // Extract per-book <style> and scope it under #reader-content.
    const styles = doc.querySelectorAll("style");
    const styleBuf = [];
    styles.forEach((s) => {
      styleBuf.push(scopeCss(s.textContent, slug));
      s.remove();
    });
    // Remove chatbot <link>/<script> — the SPA hosts the chatbot singleton.
    doc.querySelectorAll('link[href*="chatbot"], script[src*="chatbot"]').forEach((n) => n.remove());

    // Determine the content root. EPUB conversions wrap everything in a
    // body-level <article>; mirrors (oil101/natgas101) put chapters as direct
    // <body> children (natgas101 has <article> nested INSIDE chapter bodies,
    // so a naive querySelector("article") would grab the wrong node). Pick a
    // body-level <article> if present, else clone all body children.
    let contentRoot = null;
    const bodyArticle = Array.from(doc.body.children).find(
      (c) => c.tagName === "ARTICLE"
    );
    if (bodyArticle) {
      contentRoot = bodyArticle;
    } else {
      contentRoot = document.createElement("div");
      Array.from(doc.body.children).forEach((ch) => {
        if (ch.tagName === "SCRIPT" || (ch.tagName === "LINK" && /chatbot/.test(ch.href || ""))) return;
        contentRoot.appendChild(ch.cloneNode(true));
      });
    }

    // Rewrite relative src/href on media to resolve from the book's folder.
    // The SPA renders book content at the root URL, so a bare relative path
    // like "images/x.png" or "assets/cover.jpg" would resolve against the
    // root instead of books/<slug>/. Strip any leading ./ or / and prefix
    // the book base. Absolute URLs, data:, and fragments are left alone.
    const bookBase = `books/${slug}/`;
    const rewriteUrl = (u) => {
      if (!u || /^(data:|https?:|\/\/|#|mailto:)/.test(u)) return u;
      return bookBase + u.replace(/^\.?\/+/, "");
    };
    contentRoot.querySelectorAll("img[src]").forEach((img) => {
      img.setAttribute("src", rewriteUrl(img.getAttribute("src")));
    });
    contentRoot.querySelectorAll('img[srcset], source[srcset]').forEach((el) => {
      const ss = el.getAttribute("srcset");
      if (ss) el.setAttribute("srcset", ss.split(",").map((part) => {
        const t = part.trim();
        const sp = t.split(/\s+/);
        sp[0] = rewriteUrl(sp[0]);
        return sp.join(" ");
      }).join(", "));
    });
    contentRoot.querySelectorAll('[style*="url("]').forEach((el) => {
      const st = el.getAttribute("style");
      if (st && /url\(/.test(st)) {
        el.setAttribute("style", st.replace(/url\(\s*(['"]?)(?!data:|https?:|\/\/)(\.?\/?)([^'")]+)\s*\)/g,
          (_m, q, _d, path) => `url(${q}${bookBase}${path.replace(/^\.?\/+/, "")}${q})`));
      }
    });

    // Inject scoped styles + content.
    container.innerHTML = "";
    if (styleBuf.length) {
      const styleEl = document.createElement("style");
      styleEl.id = "rl-book-style";
      styleEl.textContent = styleBuf.join("\n");
      container.appendChild(styleEl);
    }
    container.appendChild(contentRoot);

    // Book title for the top bar.
    const h1 = contentRoot.querySelector("h1.book-title") || contentRoot.querySelector("h1");
    currentBookTitle = h1 ? h1.textContent.trim() : slug;

    scroller.classList.remove("rl-loading");
    scroller.scrollTop = 0;
    updateTopBar();
    wireSectionObserver();
    notifySectionChange();
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
        // Pick the topmost intersecting section.
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
    // Seed with the first section if none intersect yet.
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
    // EPUB: derive from id="section-N".
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
      // The chapter number/title heading leaks into innerText; trim a leading
      // duplicate of the title to keep the context clean.
      return text;
    },
    onSectionChange(cb) {
      sectionListeners.add(cb);
      // Fire once immediately so callers sync to the current state.
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
  // Route book links + chatbot citation links through the hash router so
  // the SPA loads the book instead of navigating away.
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if (!a) return;
    const href = a.getAttribute("href");
    if (!href) return;
    // Internal chapter anchors: smooth-scroll within the reader.
    if (href.startsWith("#") && href.length > 1 && !href.startsWith("#/")) {
      // Only intercept if we're in a book and the anchor exists in the reader.
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
    // Book links: ./books/<slug>/index.html or books/<slug>/index.html
    const bookMatch = href.match(/^(?:\.\/)?books\/([^/?#]+)\/index\.html$/);
    if (bookMatch) {
      e.preventDefault();
      navigate(`#/books/${bookMatch[1]}`);
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
    // Ensure the home view is active on first load.
    const home = document.getElementById("view-home");
    const reader = document.getElementById("view-reader");
    if (home && reader) {
      home.classList.add("active");
      reader.classList.remove("active");
    }
    // Wire the top-bar home button.
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
