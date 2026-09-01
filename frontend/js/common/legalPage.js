(function () {
  const TOC_KEYWORDS = [
    "table of contents",
    "inhaltsverzeichnis",
    "tabla de contenidos",
    "table des matieres",
    "table des matières",
    "indice",
    "indice dei contenuti",
    "sumario",
    "sumário",
    "目次",
    "目录",
    "جدول المحتويات",
    "विषयसूची",
    "оглавление",
  ];

  const LAST_UPDATED_PREFIXES = [
    "last updated",
    "zuletzt aktualisiert",
    "ultima actualizacion",
    "última actualización",
    "derniere mise a jour",
    "dernière mise à jour",
    "ultima modifica",
    "última atualização",
    "последнее обновление",
    "最終更新",
    "最后更新",
    "अंतिम अद्यतन",
    "آخر تحديث",
  ];

  const CONTACT_KEYWORDS = [
    "contact",
    "kontakt",
    "contacto",
    "contatto",
    "contato",
    "контакт",
    "連絡",
    "联系方式",
    "संपर्क",
    "اتصال",
  ];

  function normalizeText(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
  }

  function createSlug(text) {
    const normalized = String(text || "")
      .trim()
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^\p{Letter}\p{Number}]+/gu, "-")
      .replace(/^-+|-+$/g, "");

    return normalized || "section";
  }

  function resolveLegalContent(_pageKey, payload) {
    const localized = payload?.content_by_locale || payload?.localized_content;
    if (localized && typeof localized === "object") {
      const currentLang = getCurrentPageLanguage();
      const candidates = [
        currentLang,
        currentLang.split("-", 1)[0],
      ].filter(Boolean);

      for (const candidate of candidates) {
        const value = localized[candidate];
        if (typeof value === "string" && value.trim()) {
          return value;
        }
      }
    }

    return String(payload?.content || "");
  }

  function getCurrentPageLanguage() {
    const lang = document.documentElement.getAttribute("lang") || "en";
    return String(lang || "en").trim().toLowerCase() || "en";
  }

  function normalizeLanguageCode(value) {
    return String(value || "").trim().toLowerCase().split(/[-_]/, 1)[0];
  }

  function getLocalizedLanguageName(languageCode, t) {
    const normalized = normalizeLanguageCode(languageCode);
    if (!normalized) return "";
    return t(`legal_language_name_${normalized}`, normalized.toUpperCase());
  }

  function formatLegalTranslation(key, fallback, vars) {
    if (typeof window.formatTranslation === "function") {
      return window.formatTranslation(key, fallback, vars);
    }
    return String(fallback || "").replace(/\{(\w+)\}/g, (_, token) => {
      const value = vars?.[token];
      return value === undefined || value === null ? "" : String(value);
    });
  }

  function shouldShowLanguageNotice(payload) {
    if (!payload || payload.localized_content_available === true) return false;

    const currentLang = normalizeLanguageCode(getCurrentPageLanguage());
    const contentLang = normalizeLanguageCode(
      payload.content_language || payload.authoritative_language
    );

    if (!currentLang) return false;
    if (contentLang) return currentLang !== contentLang;
    return currentLang !== "en";
  }

  function insertLegalLanguageNotice(container, payload, t) {
    if (!container || !shouldShowLanguageNotice(payload)) return;

    const notice = document.createElement("div");
    notice.className = "info-box legal-language-notice";
    notice.setAttribute("role", "note");
    notice.setAttribute("aria-label", t("legal_language_notice_label", "Language notice"));

    const label = document.createElement("strong");
    label.textContent = t("legal_language_notice_label", "Language notice");
    notice.appendChild(label);

    const languageName = getLocalizedLanguageName(
      payload.content_language || payload.authoritative_language,
      t
    );
    const message = languageName
      ? formatLegalTranslation(
          "legal_language_notice_authoritative",
          "This legal document is provided in {language}. The page interface may be translated, but the document below is not automatically translated.",
          { language: languageName }
        )
      : t(
          "legal_language_notice_operator",
          "This legal document is provided in the operator's authoritative language. The page interface may be translated, but the document below is not automatically translated."
        );

    notice.appendChild(document.createTextNode(` ${message}`));
    container.insertBefore(notice, container.firstChild);
  }

  function looksLikeLastUpdated(text) {
    const normalized = normalizeText(text);
    if (!normalized || normalized.length > 120) return false;

    return LAST_UPDATED_PREFIXES.some((prefix) => normalized.startsWith(prefix));
  }

  function isTocTitleParagraph(element) {
    if (!element || element.tagName !== "P") return false;

    const next = element.nextElementSibling;
    if (!next || next.tagName !== "UL") return false;

    const text = normalizeText(element.textContent);
    if (!text || text.length > 80) return false;

    const hasSingleStrongChild =
      element.children.length === 1 && element.children[0].tagName === "STRONG";

    return hasSingleStrongChild || TOC_KEYWORDS.includes(text);
  }

  function isAnchorOnlyElement(element) {
    if (!element || element.tagName !== "A") return false;
    return Boolean(element.id) && !element.textContent.trim() && !element.getAttribute("href");
  }

  function extractSectionId(heading) {
    const next = heading.nextElementSibling;
    if (isAnchorOnlyElement(next)) {
      const id = next.id;
      next.remove();
      if (id) return id;
    }

    return createSlug(heading.textContent);
  }

  function isContactHeading(text) {
    const normalized = normalizeText(text);
    return CONTACT_KEYWORDS.some((keyword) => normalized.includes(keyword));
  }

  function structureLegalContent(container, options) {
    const t = options?.translate || ((_, fallback) => fallback);
    const usedSectionIds = new Set();

    const h1 = container.querySelector("h1");
    if (h1) {
      const headerDiv = document.createElement("div");
      headerDiv.className = "header";
      h1.parentNode.insertBefore(headerDiv, h1);
      headerDiv.appendChild(h1);

      const nextElem = headerDiv.nextElementSibling;
      if (nextElem && nextElem.tagName === "P" && looksLikeLastUpdated(nextElem.textContent)) {
        nextElem.classList.add("last-updated");
        headerDiv.appendChild(nextElem);
      }
    }

    const tocTitleP = Array.from(container.querySelectorAll("p")).find(isTocTitleParagraph);
    if (tocTitleP) {
      const tocDiv = document.createElement("div");
      tocDiv.className = "table-of-contents";
      tocTitleP.parentNode.insertBefore(tocDiv, tocTitleP);

      const tocTitleDiv = document.createElement("div");
      tocTitleDiv.className = "toc-title";
      tocTitleDiv.textContent = t("legal_toc_title", "Table of Contents");
      tocDiv.appendChild(tocTitleDiv);

      const ul = tocTitleP.nextElementSibling;
      if (ul && ul.tagName === "UL") {
        ul.classList.add("toc-list");
        tocDiv.appendChild(ul);
      }

      tocTitleP.remove();
    }

    container.querySelectorAll("blockquote").forEach((blockquote) => {
      // GitHub-style Markdown alerts already have their own semantic treatment.
      // Keep them intact instead of flattening them into the generic legal box.
      if (blockquote.classList.contains("markdown-alert")) return;

      const infoBox = document.createElement("div");
      infoBox.className = "info-box";

      while (blockquote.firstChild) {
        infoBox.appendChild(blockquote.firstChild);
      }

      blockquote.parentNode.replaceChild(infoBox, blockquote);
    });

    Array.from(container.querySelectorAll("h3"))
      .filter((heading) => isContactHeading(heading.textContent))
      .forEach((heading) => {
        const box = document.createElement("div");
        box.className = "contact-box";
        heading.parentNode.insertBefore(box, heading);
        box.appendChild(heading);

        let next = box.nextElementSibling;
        while (next && !["H1", "H2", "H3", "SECTION"].includes(next.tagName)) {
          const toMove = next;
          next = next.nextElementSibling;
          box.appendChild(toMove);
        }
      });

    const children = Array.from(container.children);
    let currentSection = null;

    children.forEach((child) => {
      if (
        child.classList.contains("header") ||
        child.classList.contains("table-of-contents") ||
        child.tagName === "SCRIPT"
      ) {
        return;
      }

      if (child.tagName === "H2") {
        currentSection = document.createElement("section");
        currentSection.className = "section";

        let candidateId = extractSectionId(child);
        let uniqueId = candidateId;
        let counter = 2;

        while (usedSectionIds.has(uniqueId)) {
          uniqueId = `${candidateId}-${counter}`;
          counter += 1;
        }

        usedSectionIds.add(uniqueId);
        currentSection.id = uniqueId;
        child.parentNode.insertBefore(currentSection, child);
        currentSection.appendChild(child);
        return;
      }

      if (currentSection) {
        currentSection.appendChild(child);
      }
    });
  }

  function shouldReduceMotion() {
    try {
      return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
    } catch (_error) {
      return false;
    }
  }

  function initializeLegalInteractions() {
    const cleanups = [];
    const scrollBehavior = shouldReduceMotion() ? "auto" : "smooth";

    document.querySelectorAll(".toc-list a").forEach((anchor) => {
      const handleClick = function (event) {
        event.preventDefault();
        const href = this.getAttribute("href");
        if (!href || !href.startsWith("#")) return;

        // Fragment identifiers are not CSS selectors. In particular, legal
        // section IDs commonly begin with a number (for example,
        // "1-einleitung"), which would make querySelector throw.
        let targetId = href.slice(1);
        try {
          targetId = decodeURIComponent(targetId);
        } catch (_error) {
          // Keep the literal fragment when an operator supplied malformed
          // percent encoding so the link can still match an equally literal ID.
        }

        const target = document.getElementById(targetId);
        if (!target) return;

        target.scrollIntoView({ behavior: scrollBehavior, block: "start" });
        history.pushState(null, "", href);
      };

      anchor.addEventListener("click", handleClick);
      cleanups.push(() => anchor.removeEventListener("click", handleClick));
    });

    const scrollButton = document.getElementById("scrollToTop");
    if (scrollButton) {
      const updateButtonVisibility = () => {
        if (window.pageYOffset > 300) {
          scrollButton.classList.add("visible");
        } else {
          scrollButton.classList.remove("visible");
        }
      };
      const handleScrollToTop = () => {
        window.scrollTo({ top: 0, behavior: scrollBehavior });
      };

      window.addEventListener("scroll", updateButtonVisibility);
      scrollButton.addEventListener("click", handleScrollToTop);
      cleanups.push(() => window.removeEventListener("scroll", updateButtonVisibility));
      cleanups.push(() => scrollButton.removeEventListener("click", handleScrollToTop));
      updateButtonVisibility();
    }

    const sections = Array.from(document.querySelectorAll(".section"));
    const links = Array.from(document.querySelectorAll(".toc-list a"));

    const updateActiveTocLink = () => {
      let currentId = "";

      sections.forEach((section) => {
        if (window.pageYOffset >= section.offsetTop - 100) {
          currentId = section.getAttribute("id") || "";
        }
      });

      links.forEach((link) => {
        const isActive = link.getAttribute("href") === `#${currentId}`;
        link.classList.toggle("is-active", isActive);
      });
    };

    window.addEventListener("scroll", updateActiveTocLink);
    cleanups.push(() => window.removeEventListener("scroll", updateActiveTocLink));
    updateActiveTocLink();

    return () => {
      cleanups.forEach((cleanup) => cleanup());
    };
  }

  function getTranslator() {
    return function translate(key, fallback) {
      if (typeof window.getTranslation === "function") {
        return window.getTranslation(key, fallback);
      }
      return fallback;
    };
  }

  function renderErrorState(message) {
    const container = document.getElementById("main-container");
    if (!container) return;

    const error = document.createElement("div");
    error.className = "legal-error-state";
    error.setAttribute("role", "alert");
    error.textContent = message;
    container.replaceChildren(error);
  }

  function createMarkdownRenderer(options = {}) {
    if (typeof window.markdownit !== "function") {
      throw new Error("markdown-it is unavailable");
    }

    const renderer = window.markdownit({
      html: Boolean(options.html),
      linkify: options.linkify !== false,
      typographer: options.typographer !== false,
    });
    if (typeof window.markdownitAlerts === "function") {
      renderer.use(window.markdownitAlerts);
    }
    return renderer;
  }

  function isSafeUrl(url) {
    const value = String(url || "").trim();
    if (!value || value.startsWith("#")) return true;

    try {
      const parsed = new URL(value, window.location.href);
      return ["http:", "https:", "mailto:", "tel:"].includes(parsed.protocol);
    } catch (_error) {
      return false;
    }
  }

  function sanitizeRenderedHtml(html, options = {}) {
    const source = String(html || "");
    if (!source) return "";

    const purify = window.DOMPurify;
    if (!purify || typeof purify.sanitize !== "function") {
      if (options.requirePurify) {
        throw new Error("DOMPurify is unavailable");
      }
      return source;
    }

    const sanitized = purify.sanitize(source, {
      USE_PROFILES: { html: true },
      FORBID_TAGS: ["script", "iframe", "object", "embed", "frame", "frameset", "meta", "base", "link"],
      FORBID_ATTR: ["style", "srcdoc"],
      ALLOW_DATA_ATTR: false,
    });

    if (typeof DOMParser === "undefined") {
      return sanitized;
    }

    const parser = new DOMParser();
    const doc = parser.parseFromString(sanitized, "text/html");

    doc.querySelectorAll("[href], [src]").forEach((node) => {
      if (node.hasAttribute("href")) {
        const href = node.getAttribute("href") || "";
        if (!isSafeUrl(href)) {
          node.removeAttribute("href");
        }
      }

      if (node.hasAttribute("src")) {
        const src = node.getAttribute("src") || "";
        if (!isSafeUrl(src)) {
          node.removeAttribute("src");
        }
      }
    });

    doc.querySelectorAll("a[href]").forEach((anchor) => {
      const rel = new Set(
        String(anchor.getAttribute("rel") || "")
          .split(/\s+/)
          .filter(Boolean)
      );
      rel.add("noopener");
      rel.add("noreferrer");
      anchor.setAttribute("rel", Array.from(rel).join(" "));
    });

    return doc.body.innerHTML;
  }

  async function initLegalPage(options) {
    // The language bootstrap normally finishes before a legal document is
    // selected. Avoid refetching every dictionary when users switch documents.
    if (window.__omlorixI18nReady !== true && typeof window.initI18n === "function") {
      await window.initI18n();
    }

    const t = getTranslator();
    const errorMessage = t(options.errorKey, options.errorFallback);
    const showError = (message) => renderErrorState(message || errorMessage);

    let md = null;
    try {
      md = createMarkdownRenderer(options.markdown || {});
    } catch (error) {
      console.error(`Failed to initialize markdown-it for the ${options.pageTitle} page:`, error);
      showError();
      return;
    }

    let latestPayload = null;
    let cleanupInteractions = null;

    const renderPage = async (payload) => {
      latestPayload = payload;

      const resolvedContent = resolveLegalContent(options.pageKey, payload);
      const normalizedContent = String(resolvedContent || "")
        .replace(/<!--[\s\S]*?-->/g, "")
        .trim();

      const container = document.getElementById("main-container");
      if (!container) return;

      if (typeof cleanupInteractions === "function") {
        cleanupInteractions();
        cleanupInteractions = null;
      }

      const markdownSource = window.ChatMarkdownUtils
        && typeof window.ChatMarkdownUtils.normalizeMarkdownForRender === "function"
        ? window.ChatMarkdownUtils.normalizeMarkdownForRender(normalizedContent)
        : normalizedContent;
      const renderedHtml = md.render(markdownSource);
      container.innerHTML = sanitizeRenderedHtml(renderedHtml, {
        requirePurify: options.markdown?.html === true,
      });
      window.ChatMarkdownAlerts?.enhanceIcons?.(container);

      if (typeof options.afterRender === "function") {
        options.afterRender({ container, payload, t });
      }
      insertLegalLanguageNotice(container, payload, t);

      structureLegalContent(container, { translate: t });
      cleanupInteractions = initializeLegalInteractions();
    };

    try {
      let payload = options.payload || null;
      if (!payload) {
        const response = await fetch(options.endpoint, {
          signal: options.signal,
          credentials: "same-origin",
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        payload = await response.json();
        if (typeof options.onPayload === "function") {
          options.onPayload(payload);
        }
      }
      await renderPage(payload);
      if (typeof options.onLoaded === "function") {
        options.onLoaded();
      }
    } catch (error) {
      if (error?.name === "AbortError") {
        return () => {};
      }
      console.error(`Error loading ${options.pageTitle}:`, error);
      showError();
    }

    const handleI18nUpdate = async () => {
      if (!latestPayload) return;

      try {
        await renderPage(latestPayload);
      } catch (error) {
        console.error(`Error re-rendering ${options.pageTitle} after language update:`, error);
        showError();
      }
    };

    const cleanup = () => {
      latestPayload = null;
      if (typeof cleanupInteractions === "function") {
        cleanupInteractions();
        cleanupInteractions = null;
      }
      document.removeEventListener("i18n:updated", handleI18nUpdate);
      window.removeEventListener("pagehide", cleanup);
    };

    document.addEventListener("i18n:updated", handleI18nUpdate);
    window.addEventListener("pagehide", cleanup, { once: true });
    return cleanup;
  }

  window.legalPageUtils = {
    initLegalPage,
    initializeLegalInteractions,
    insertLegalLanguageNotice,
    resolveLegalContent,
    shouldReduceMotion,
    structureLegalContent,
  };
})();
