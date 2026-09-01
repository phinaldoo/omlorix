// Common language handler for all pages
// - Determines current language (authenticated account -> localStorage -> browser -> default en)
 // - Loads /i18n/<lang>/<page>.json with English fallback
 // - Loads component dictionaries shared by multiple pages when needed
 // - Loads the shared legal, privacy, and terms dictionaries for the legal page
 // - Applies translations to elements using:
 //   * data-i18n: sets textContent/innerText
 //   * data-translate-key: alias of data-i18n
 //   * data-i18n-attr: semicolon-separated attr:key pairs (e.g., "placeholder:email_placeholder;aria-label:email_label")
 // - Populates a #language-select dropdown if present
 (function () {
   const SUPPORTED_LANGS = ["en", "de", "es", "zh", "fr", "hi", "ar", "ja", "it", "pt", "ru"]; // `zh` is the app's Simplified Chinese locale. Extend as needed.
   const LANGUAGE_STORAGE_KEY = "lang";
   const RTL_LANGS = new Set(["ar"]);
   const LANG_NAMES = {
     en: "English",
     de: "Deutsch",
     es: "Español",
     zh: "简体中文",
     fr: "Français",
     hi: "हिन्दी",
     ar: "العربية",
     ja: "日本語",
     it: "Italiano",
     pt: "Português",
     ru: "Русский",
   };

  let activeTranslations = {};
  let activeLanguage = "";
  let authenticatedLanguageConsumed = false;
  let explicitUserLanguage = "";
  let i18nRequestId = 0;
  window.__omlorixI18nReady = false;
  const BUILD_MARKER_META_NAME = "omlorix-build-id";
  const I18N_CACHE_PREFIX = "omlorix:i18n:merged:v1:";
  const I18N_CACHE_INDEX_KEY = "omlorix:i18n:merged:v1:index";
  const I18N_CACHE_MAX_ENTRIES = 32;
  const I18N_RESPONSE_CACHE_PREFIX = "omlorix-i18n-json-v1-";

  function safeReadLocalStorage(key) {
    try {
      return localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function safeWriteLocalStorage(key, value) {
    try {
      localStorage.setItem(key, value);
      return true;
    } catch (error) {
      return false;
    }
  }

  function safeRemoveLocalStorage(key) {
    try {
      localStorage.removeItem(key);
    } catch (error) {
      // Storage can be disabled or full; cache cleanup is best-effort only.
    }
  }

  /**
   * Normalize a language preference to the two-letter locale used by the
   * frontend dictionaries. Account settings are already stored as ISO 639-1,
   * but accepting a browser-style value here keeps the synchronization boundary
   * defensive and makes the helper safe for auth/bootstrap payloads.
   */
  function normalizeLanguage(value) {
    const normalized = String(value || "").trim().toLowerCase().split(/[-_]/)[0];
    return SUPPORTED_LANGS.includes(normalized) ? normalized : "";
  }

  /**
   * Return the authenticated account preference published by auth.js, if one
   * is available. This value is intentionally kept separate from localStorage:
   * localStorage predates account slots and is shared by every account in a
   * browser, while this value belongs to the current authenticated account.
   */
  function getAuthenticatedLanguage() {
    return normalizeLanguage(window.__omlorixAuthenticatedLanguage);
  }

  function persistLanguage(lang) {
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, lang);
    } catch (error) {
      console.warn("Failed to save language:", error);
    }
  }

  function getI18nCacheKey(pageKey, lang) {
    const marker = getBuildMarker();
    if (!marker) return "";
    return `${I18N_CACHE_PREFIX}${marker}:${lang}:${pageKey}`;
  }

  function readI18nCacheIndex() {
    const raw = safeReadLocalStorage(I18N_CACHE_INDEX_KEY);
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.filter((key) => typeof key === "string") : [];
    } catch (error) {
      return [];
    }
  }

  function rememberI18nCacheKey(cacheKey) {
    if (!cacheKey) return;
    const nextIndex = [cacheKey, ...readI18nCacheIndex().filter((key) => key !== cacheKey)];
    const retained = nextIndex.slice(0, I18N_CACHE_MAX_ENTRIES);
    nextIndex.slice(I18N_CACHE_MAX_ENTRIES).forEach((key) => {
      if (key.startsWith(I18N_CACHE_PREFIX)) safeRemoveLocalStorage(key);
    });
    safeWriteLocalStorage(I18N_CACHE_INDEX_KEY, JSON.stringify(retained));
  }

  function readCachedDictionaries(pageKey, lang) {
    const cacheKey = getI18nCacheKey(pageKey, lang);
    if (!cacheKey) return null;
    const raw = safeReadLocalStorage(cacheKey);
    if (!raw) return null;
    try {
      const payload = JSON.parse(raw);
      if (payload && payload.translations && typeof payload.translations === "object") {
        rememberI18nCacheKey(cacheKey);
        return payload.translations;
      }
    } catch (error) {
      safeRemoveLocalStorage(cacheKey);
    }
    return null;
  }

  function writeCachedDictionaries(pageKey, lang, translations) {
    const cacheKey = getI18nCacheKey(pageKey, lang);
    if (!cacheKey || !translations || typeof translations !== "object") return;
    const payload = JSON.stringify({
      translations,
    });
    if (safeWriteLocalStorage(cacheKey, payload)) {
      rememberI18nCacheKey(cacheKey);
    }
  }

  function getBackendDetailTranslationKey(detail) {
    switch (String(detail || "").trim()) {
      case "File is not a valid image.":
        return "profile_picture_invalid_image_error";
      default:
        return "";
    }
  }

  function getBuildMarker() {
    try {
      const meta = document.querySelector(`meta[name="${BUILD_MARKER_META_NAME}"]`);
      const metaValue = meta ? meta.getAttribute("content") : "";
      if (metaValue) return metaValue;
    } catch (e) {
      // Ignore DOM lookup issues and fall back to URL params.
    }

    try {
      const currentUrl = new URL(window.location.href);
      return currentUrl.searchParams.get("__build") || "";
    } catch (e) {
      return "";
    }
  }

  function withBuildMarker(url) {
    const marker = getBuildMarker();
    if (!marker) return url;
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}v=${encodeURIComponent(marker)}`;
  }

  function getI18nResponseCacheName() {
    const marker = getBuildMarker();
    return marker ? `${I18N_RESPONSE_CACHE_PREFIX}${marker}` : "";
  }

  async function readCachedJsonResponse(url) {
    const cacheName = getI18nResponseCacheName();
    if (!cacheName || !window.caches) return null;
    try {
      const cache = await window.caches.open(cacheName);
      const response = await cache.match(url);
      return response ? response.clone().json() : null;
    } catch (error) {
      return null;
    }
  }

  async function writeCachedJsonResponse(url, response) {
    const cacheName = getI18nResponseCacheName();
    if (!cacheName || !window.caches || !response || !response.ok) return;
    try {
      const cache = await window.caches.open(cacheName);
      await cache.put(url, response.clone());
    } catch (error) {
      // Cache Storage is an optimization; translation loading must keep working without it.
    }
  }

  async function pruneOldI18nResponseCaches() {
    const currentCacheName = getI18nResponseCacheName();
    if (!currentCacheName || !window.caches || typeof window.caches.keys !== "function") return;
    try {
      const cacheNames = await window.caches.keys();
      await Promise.all(
        cacheNames
          .filter((cacheName) => cacheName.startsWith(I18N_RESPONSE_CACHE_PREFIX) && cacheName !== currentCacheName)
          .map((cacheName) => window.caches.delete(cacheName))
      );
    } catch (error) {
      // Stale response caches are harmless because build markers keep request URLs distinct.
    }
  }

  function handleLanguageSelectChange(event) {
    const next = normalizeLanguage(event.target.value);
    if (next) {
      // A direct user choice is more recent than the pending bootstrap value.
      // The shared helper also records the override so a slow auth response
      // cannot immediately switch the page back to the previous language.
      void applyUserLanguagePreference(next, { source: "user" });
    }
  }

   // Determine page key from <body data-page> or filename
   function getPageKey() {
     const body = document.body;
     const explicit = body ? body.getAttribute("data-page") : null;
     if (explicit) return explicit;
     const path = (location.pathname || "").split("/").pop() || "index";
     return (path.replace(/\.html$/i, "") || "index");  
   }

   function detectBrowserLang() {
     const nav = navigator;
     const raw = (nav.languages && nav.languages[0]) || nav.language || "en";
     const code = raw.toLowerCase().split("-")[0];
     return SUPPORTED_LANGS.includes(code) ? code : "en";
   }

   function getCurrentLang() {
     // The authenticated account preference is authoritative over the browser
     // cache. It is published by auth.js before the protected page is shown,
     // but this also handles a response that arrives just after this script.
     const authenticatedLang = getAuthenticatedLanguage();
     if (authenticatedLang && !authenticatedLanguageConsumed) {
       authenticatedLanguageConsumed = true;
       persistLanguage(authenticatedLang);
       return authenticatedLang;
     }

     let stored;
     try {
       stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
     } catch (error) {
       console.warn('Failed to read saved language:', error);
       stored = null;
     }
     const normalizedStored = normalizeLanguage(stored);
     if (normalizedStored) return normalizedStored;
     const detected = detectBrowserLang();
     persistLanguage(detected);
     return detected;
   }

   async function fetchJson(url) {
     try {
       const versionedUrl = withBuildMarker(url);
       const cachedPayload = await readCachedJsonResponse(versionedUrl);
       if (cachedPayload) return cachedPayload;
       const res = await fetch(versionedUrl, { cache: "default" });
       if (!res.ok) {
         notifyError(`HTTP ${res.status}`);
         return null;
       }
       await writeCachedJsonResponse(versionedUrl, res);
       return await res.json();
     } catch (e) {
       return null; // Missing is fine; we'll fallback
     }
   }

   function deepMerge(base, override) {
     if (!override) return { ...base };
     const out = { ...base };
     for (const k of Object.keys(override)) out[k] = override[k];
     return out;
   }

   function applyText(el, text) {
     const childNodes = el.childNodes ? Array.from(el.childNodes) : [];
     const directTextNode = childNodes.find(
       (node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim().length > 0
     );
     if (directTextNode && el.children && el.children.length > 0) {
       directTextNode.textContent = /\s$/.test(directTextNode.textContent) ? `${text} ` : text;
       return;
     }
     // Prefer textContent; if element has children (e.g., button > span), set innerText of first span
     const hasChildSpan = el.children && el.children.length === 1 && el.children[0].tagName === "SPAN";
     if (hasChildSpan) {
       el.children[0].innerText = text;
     } else {
       el.textContent = text;
     }
   }

   function findTranslationTargets(root, selector) {
     if (!root) return [];
     const targets = [];
     if (typeof root.matches === "function" && root.matches(selector)) {
       targets.push(root);
     }
     if (typeof root.querySelectorAll === "function") {
       targets.push(...root.querySelectorAll(selector));
     }
     return targets;
   }

   function applyTranslations(dict, root = document) {
     // data-translate-key alias
     findTranslationTargets(root, "[data-translate-key]").forEach((el) => {
       const key = el.getAttribute("data-translate-key");
       if (key && dict[key] != null) applyText(el, dict[key]);
     });

     // data-i18n text
     findTranslationTargets(root, "[data-i18n]").forEach((el) => {
       const key = el.getAttribute("data-i18n");
       if (key && dict[key] != null) applyText(el, dict[key]);
     });

     // data-i18n-attr attributes
     findTranslationTargets(root, "[data-i18n-attr]").forEach((el) => {
       const spec = el.getAttribute("data-i18n-attr");
       if (!spec) return;
       spec.split(";").forEach((pair) => {
         const [attr, key] = pair.split(":").map((s) => s && s.trim());
         if (!attr || !key) return;
         if (dict[key] != null) el.setAttribute(attr, dict[key]);
       });
     });
   }

  function populateLanguageSelect(current) {
    const select = document.getElementById("languageSelect");
    if (!select) return;
     // Clear existing
     select.innerHTML = "";
     SUPPORTED_LANGS.forEach((code) => {
       const opt = document.createElement("option");
       opt.value = code;
       opt.textContent = LANG_NAMES[code] || code;
        if (code === current) opt.selected = true;
        select.appendChild(opt);
      });
      select.removeEventListener("change", handleLanguageSelectChange);
      select.addEventListener("change", handleLanguageSelectChange);
    }

    async function loadDictionaries(pageKey, lang) {
     if (document.documentElement.dataset.i18nSkip === "true") {
       return activeTranslations;
     }
      const cachedDictionaries = readCachedDictionaries(pageKey, lang);
      if (cachedDictionaries) {
        return cachedDictionaries;
      }
      // Pages that load shared chat, authentication, setup, or settings modules
      // also need the shared index vocabulary those modules use. Load the page's
      // own dictionary last so page-specific wording continues to take priority.
      const pagesUsingIndexVocabulary = new Set([
        "canvas-share",
        "chat-share",
        "leaderboard",
        "login",
        "server_setup",
      ]);
      const pagesUsingPasswordRequirements = new Set(["index", "login"]);
      let pageKeys;
      if (pageKey === "admin") {
        pageKeys = ["schema", "index", "admin", "admin_chats", "server_setup"];
      } else if (pageKey === "index") {
        pageKeys = ["schema", "index", "server_setup"];
      } else if (pageKey === "privacy" || pageKey === "terms") {
        pageKeys = ["legal", pageKey];
      } else if (pageKey === "legal") {
        pageKeys = ["legal", "privacy", "terms"];
      } else {
        pageKeys = pagesUsingIndexVocabulary.has(pageKey) ? ["index", pageKey] : [pageKey];
      }
      if (pagesUsingPasswordRequirements.has(pageKey)) {
        pageKeys.unshift("password-requirements");
      }
      const [baseDictionaries, overrideDictionaries] = await Promise.all([
        Promise.all(pageKeys.map((key) => fetchJson(`/i18n/en/${key}.json`))),
        lang === "en"
          ? Promise.resolve([])
          : Promise.all(pageKeys.map((key) => fetchJson(`/i18n/${lang}/${key}.json`))),
      ]);
      const loadedAllDictionaries = baseDictionaries.every(Boolean) && overrideDictionaries.every(Boolean);

      let base = {};
      for (const dictionary of baseDictionaries) {
        base = deepMerge(base, dictionary || {});
      }
      if (lang === "en") {
        if (loadedAllDictionaries) writeCachedDictionaries(pageKey, lang, base);
        return base;
      }

      let override = {};
      for (const dictionary of overrideDictionaries) {
        override = deepMerge(override, dictionary || {});
      }
      const merged = deepMerge(base, override);
      if (loadedAllDictionaries) writeCachedDictionaries(pageKey, lang, merged);
      return merged;
    }

   /**
    * Wait for the protected index page's auth bootstrap before choosing its
    * first locale. Without this small gate, DOMContentLoaded can initialize
    * i18n from the previous account's localStorage value while auth.js is still
    * resolving the current account's language.
    */
   async function waitForInitialAuthBootstrap() {
     if (getPageKey() !== "index") return;
     const bootstrap = window.__omlorixInitialAuthBootstrap;
     if (!bootstrap || typeof bootstrap.then !== "function") return;
     try {
       await bootstrap;
     } catch (error) {
       // Auth failure/redirect handling belongs to auth.js. Falling back to the
       // browser locale keeps public/error states renderable if it rejects.
     }
   }

   /**
    * Apply a language selected by an authenticated account or by the user.
    * Every call gets its own request generation so a slower dictionary fetch
    * from an earlier locale cannot overwrite the latest selection.
    *
    * @param {string} value Language code or browser-style language tag.
    * @param {{source?: "server"|"user"}} options Source of the preference.
    * @returns {Promise<boolean>} Whether a supported language was applied.
    */
   async function applyUserLanguagePreference(value, { source = "user" } = {}) {
     const lang = normalizeLanguage(value);
     if (!lang) return false;

     // A refresh response can finish after a user changes the language. Do not
     // let that older server value undo the explicit choice. This override is
     // cleared when auth.js starts a different account session.
     if (source === "server" && explicitUserLanguage && explicitUserLanguage !== lang) {
       return false;
     }

     // Both a server value and an explicit user choice consume any pending
     // bootstrap preference. A manual choice must not be reverted by a late
     // auth event, and a server value must not be replaced by stale storage.
     authenticatedLanguageConsumed = true;
     explicitUserLanguage = source === "user" ? lang : "";
     if (source === "server") {
       window.__omlorixAuthenticatedLanguage = lang;
     }
     persistLanguage(lang);

     if (
       activeLanguage === lang
       && (typeof document.documentElement.getAttribute !== "function"
         || document.documentElement.getAttribute("lang") === lang)
       && window.__omlorixI18nReady
     ) {
       return true;
     }

     await initI18n(true);
     return true;
   }

   /**
    * Reset the in-memory account language boundary during logout or an
    * in-place account transition. Keep localStorage intact for public pages;
    * the next authenticated bootstrap will replace it from the new account.
    */
   function resetAuthenticatedLanguagePreference() {
     authenticatedLanguageConsumed = false;
     explicitUserLanguage = "";
     delete window.__omlorixAuthenticatedLanguage;
   }

   async function initI18n(reapplyOnly = false) {
     if (!reapplyOnly) {
       await waitForInitialAuthBootstrap();
     }

     const requestId = ++i18nRequestId;
     const lang = getCurrentLang();
     document.documentElement.setAttribute("lang", lang);
     // Set text direction for RTL languages
     document.documentElement.setAttribute("dir", RTL_LANGS.has(lang) ? "rtl" : "ltr");
     populateLanguageSelect(lang);
     pruneOldI18nResponseCaches();

     const pageKey = getPageKey();
     const dict = await loadDictionaries(pageKey, lang);
     if (requestId !== i18nRequestId) {
       return;
     }
     activeTranslations = dict;
     activeLanguage = lang;
     applyTranslations(dict);
     window.__omlorixI18nReady = true;
     document.dispatchEvent(new CustomEvent("i18n:updated", {
       detail: {
         lang,
         pageKey,
         reapplyOnly,
       },
     }));
    }

    // auth.js can finish before or after this script is evaluated. The global
    // value covers the former case; this event covers the latter case.
    if (typeof window.addEventListener === "function") {
      window.addEventListener("auth:languageReady", (event) => {
        void applyUserLanguagePreference(event?.detail?.language, { source: "server" });
      });
    }

   // Initialize on DOMContentLoaded
   if (document.readyState === "loading") {
     document.addEventListener("DOMContentLoaded", () => initI18n());
   } else {
     initI18n();
   }

    window.getTranslation = function (key, fallback) {
      if (activeTranslations && Object.prototype.hasOwnProperty.call(activeTranslations, key)) {
        const value = activeTranslations[key];
        if (value != null) return value;
      }
      if (fallback !== undefined) return fallback;
      return key;
    };

    /**
     * Expand the ICU plural blocks used by count-sensitive translations.
     *
     * The dictionaries only need the ICU plural subset here. A small balanced-
     * brace parser is preferable to a regular expression because option text may
     * itself contain ordinary ``{token}`` placeholders.
     */
    function formatPluralBlocks(template, vars) {
      let formatted = String(template);
      const pluralStartPattern = /\{(\w+),\s*plural,\s*/g;

      while (true) {
        pluralStartPattern.lastIndex = 0;
        const match = pluralStartPattern.exec(formatted);
        if (!match) break;

        let depth = 1;
        let blockEnd = -1;
        for (let index = pluralStartPattern.lastIndex; index < formatted.length; index += 1) {
          if (formatted[index] === "{") depth += 1;
          if (formatted[index] === "}") depth -= 1;
          if (depth === 0) {
            blockEnd = index;
            break;
          }
        }
        // Leave malformed translation text visible rather than partially
        // consuming it or looping forever.
        if (blockEnd < 0) break;

        const optionsText = formatted.slice(pluralStartPattern.lastIndex, blockEnd);
        const options = {};
        const selectorPattern = /(=?\d+|zero|one|two|few|many|other)\s*\{/g;
        let selectorMatch;
        while ((selectorMatch = selectorPattern.exec(optionsText)) !== null) {
          let optionDepth = 1;
          let optionEnd = -1;
          for (let index = selectorPattern.lastIndex; index < optionsText.length; index += 1) {
            if (optionsText[index] === "{") optionDepth += 1;
            if (optionsText[index] === "}") optionDepth -= 1;
            if (optionDepth === 0) {
              optionEnd = index;
              break;
            }
          }
          if (optionEnd < 0) break;
          options[selectorMatch[1]] = optionsText.slice(selectorPattern.lastIndex, optionEnd);
          selectorPattern.lastIndex = optionEnd + 1;
        }

        const rawValue = vars?.[match[1]];
        const numericValue = Number(rawValue);
        if (!Number.isFinite(numericValue) || !options.other) {
          // Leave malformed or unusable blocks intact. Translation files are
          // validated separately, so this is a defensive runtime fallback.
          break;
        }
        const exactSelector = `=${numericValue}`;
        const pluralCategory = new Intl.PluralRules(activeLanguage || "en").select(numericValue);
        const selected = options[exactSelector] ?? options[pluralCategory] ?? options.other;
        const replacement = selected.replace(/#/g, String(rawValue));
        formatted = `${formatted.slice(0, match.index)}${replacement}${formatted.slice(blockEnd + 1)}`;
      }
      return formatted;
    }

    window.formatTranslation = function (key, fallback, vars) {
      const template = typeof window.getTranslation === "function"
        ? window.getTranslation(key, fallback)
        : (fallback !== undefined ? fallback : key);
      if (!vars || typeof vars !== "object") {
        return template;
      }
      return formatPluralBlocks(template, vars).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? "" : String(value);
      });
    };

    window.translateBackendDetail = function (detail, fallback) {
      if (!detail) return fallback ?? detail ?? "";
      const translationKey = getBackendDetailTranslationKey(detail);
      if (!translationKey) {
        return fallback ?? detail;
      }
      if (typeof window.getTranslation === "function") {
        return window.getTranslation(translationKey, fallback ?? detail);
      }
      return fallback ?? detail;
    };

   window.initI18n = initI18n;
   window.translateI18nElements = function (root = document) {
     applyTranslations(activeTranslations, root);
   };
   window.applyUserLanguagePreference = applyUserLanguagePreference;
   window.applyAuthenticatedLanguage = (value) => (
     applyUserLanguagePreference(value, { source: "server" })
   );
   window.resetAuthenticatedLanguagePreference = resetAuthenticatedLanguagePreference;
 })();
