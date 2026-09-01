const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function createLocalStorage(initialValues = {}) {
  const entries = new Map(Object.entries(initialValues));
  return {
    getItem(key) {
      return entries.has(key) ? entries.get(key) : null;
    },
    setItem(key, value) {
      entries.set(key, String(value));
    },
    removeItem(key) {
      entries.delete(key);
    },
    snapshot() {
      return Object.fromEntries(entries.entries());
    },
  };
}

function createFailingI18nLocalStorage(initialValues = {}) {
  const storage = createLocalStorage(initialValues);
  return {
    ...storage,
    setItem(key, value) {
      if (String(key).startsWith('omlorix:i18n:')) {
        throw new Error('quota exceeded');
      }
      storage.setItem(key, value);
    },
  };
}

function createJsonResponse(payload) {
  return {
    ok: true,
    clone() {
      return createJsonResponse(payload);
    },
    async json() {
      return payload;
    },
  };
}

function createCaches() {
  const stores = new Map();
  return {
    async open(name) {
      if (!stores.has(name)) stores.set(name, new Map());
      const store = stores.get(name);
      return {
        async match(url) {
          return store.get(String(url)) || undefined;
        },
        async put(url, response) {
          store.set(String(url), response);
        },
      };
    },
    async keys() {
      return Array.from(stores.keys());
    },
    async delete(name) {
      return stores.delete(name);
    },
  };
}

async function runLanguageBootstrap({
  storage,
  fetchImpl,
  caches,
  serverLanguage = '',
  pageKey = 'index',
  translatableElements = [],
}) {
  const source = fs.readFileSync(path.join(__dirname, 'language.js'), 'utf8');
  let i18nUpdated;
  const i18nUpdatedPromise = new Promise((resolve) => {
    i18nUpdated = resolve;
  });

  const document = {
    body: {
      getAttribute(name) {
        return name === 'data-page' ? pageKey : null;
      },
    },
    documentElement: {
      dataset: {},
      attributes: {},
      setAttribute(name, value) {
        this.attributes[name] = String(value);
      },
    },
    readyState: 'complete',
    querySelector(selector) {
      if (selector === 'meta[name="omlorix-build-id"]') {
        return {
          getAttribute(name) {
            return name === 'content' ? 'build-123' : '';
          },
        };
      }
      return null;
    },
    querySelectorAll(selector) {
      if (selector === '[data-translate-key]') {
        return translatableElements.filter((element) => element.getAttribute('data-translate-key'));
      }
      if (selector === '[data-i18n]') {
        return translatableElements.filter((element) => element.getAttribute('data-i18n'));
      }
      if (selector === '[data-i18n-attr]') {
        return translatableElements.filter((element) => element.getAttribute('data-i18n-attr'));
      }
      return [];
    },
    getElementById() {
      return null;
    },
    addEventListener() {},
    dispatchEvent(event) {
      if (event && event.type === 'i18n:updated') {
        i18nUpdated(event);
      }
    },
  };

  const context = {
    console,
    document,
    localStorage: storage,
    location: {
      href: 'https://chat.example.com/',
      pathname: pageKey === 'index' ? '/' : `/${pageKey}.html`,
    },
    navigator: {
      language: 'en-US',
      languages: ['en-US'],
    },
    fetch: fetchImpl,
    Node: {
      TEXT_NODE: 3,
    },
    CustomEvent: class CustomEvent {
      constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail;
      }
    },
    window: {},
  };
  context.window = context;
  if (serverLanguage) {
    context.window.__omlorixAuthenticatedLanguage = serverLanguage;
  }
  if (caches) {
    context.caches = caches;
    context.window.caches = caches;
  }
  context.globalThis = context;

  vm.runInNewContext(source, context, { filename: 'language.js' });
  await i18nUpdatedPromise;
  return context;
}

function createTranslatedSearchInput() {
  const attributes = {
    'data-i18n-attr':
      'placeholder:admin_search_placeholder;aria-label:admin_search_placeholder',
  };
  return {
    attributes,
    getAttribute(name) {
      return attributes[name];
    },
    setAttribute(name, value) {
      attributes[name] = String(value);
    },
  };
}

function createTranslatableElement(attributes, textContent = '') {
  const values = { ...attributes };
  return {
    attributes: values,
    children: [],
    childNodes: [],
    textContent,
    getAttribute(name) {
      return values[name];
    },
    setAttribute(name, value) {
      values[name] = String(value);
    },
  };
}

async function fetchI18nCatalog(url) {
  const match = String(url).match(/^\/i18n\/([^/]+)\/([^?]+)\.json/);
  assert.ok(match, `unexpected translation URL: ${url}`);
  const dictionaryPath = path.join(__dirname, '..', '..', 'i18n', match[1], `${match[2]}.json`);
  return createJsonResponse(JSON.parse(fs.readFileSync(dictionaryPath, 'utf8')));
}

test('language bootstrap reuses build-scoped merged dictionary cache', async () => {
  const storage = createLocalStorage();
  const firstFetchUrls = [];
  const secondFetchUrls = [];

  await runLanguageBootstrap({
    storage,
    fetchImpl: async (url) => {
      firstFetchUrls.push(String(url));
      return createJsonResponse({ cached_language_test_key: 'from network' });
    },
  });

  await runLanguageBootstrap({
    storage: createLocalStorage(storage.snapshot()),
    fetchImpl: async (url) => {
      secondFetchUrls.push(String(url));
      throw new Error(`unexpected translation fetch: ${url}`);
    },
  });

  assert.deepEqual(firstFetchUrls, [
    '/i18n/en/password-requirements.json?v=build-123',
    '/i18n/en/schema.json?v=build-123',
    '/i18n/en/index.json?v=build-123',
    '/i18n/en/server_setup.json?v=build-123',
  ]);
  assert.deepEqual(secondFetchUrls, []);
});

test('language bootstrap reuses Cache Storage when merged localStorage cache cannot be written', async () => {
  const caches = createCaches();
  const storage = createFailingI18nLocalStorage();
  const firstFetchUrls = [];
  const secondFetchUrls = [];

  await runLanguageBootstrap({
    storage,
    caches,
    fetchImpl: async (url) => {
      firstFetchUrls.push(String(url));
      return createJsonResponse({ cached_language_test_key: 'from cache storage' });
    },
  });

  await runLanguageBootstrap({
    storage: createFailingI18nLocalStorage(storage.snapshot()),
    caches,
    fetchImpl: async (url) => {
      secondFetchUrls.push(String(url));
      throw new Error(`unexpected translation fetch: ${url}`);
    },
  });

  assert.equal(firstFetchUrls.length, 4);
  assert.deepEqual(secondFetchUrls, []);
});

test('authenticated account language overrides stale shared browser language', async () => {
  const storage = createLocalStorage({ lang: 'en' });
  const fetchUrls = [];

  const context = await runLanguageBootstrap({
    storage,
    serverLanguage: 'de',
    fetchImpl: async (url) => {
      fetchUrls.push(String(url));
      return createJsonResponse({ authenticated_language_test_key: 'Deutsch' });
    },
  });

  assert.equal(storage.getItem('lang'), 'de');
  assert.equal(context.document.documentElement.attributes.lang, 'de');
  assert.equal(context.document.documentElement.attributes.dir, 'ltr');
  assert.deepEqual(fetchUrls, [
    '/i18n/en/password-requirements.json?v=build-123',
    '/i18n/en/schema.json?v=build-123',
    '/i18n/en/index.json?v=build-123',
    '/i18n/en/server_setup.json?v=build-123',
    '/i18n/de/password-requirements.json?v=build-123',
    '/i18n/de/schema.json?v=build-123',
    '/i18n/de/index.json?v=build-123',
    '/i18n/de/server_setup.json?v=build-123',
  ]);
});

test('authenticated German settings render the shared password-policy labels in German', async () => {
  const storage = createLocalStorage({ lang: 'en' });
  const fetchUrls = [];
  const context = await runLanguageBootstrap({
    storage,
    serverLanguage: 'de',
    fetchImpl: async (url) => {
      const requestedUrl = String(url);
      fetchUrls.push(requestedUrl);
      const match = requestedUrl.match(/^\/i18n\/([^/]+)\/([^?]+)\.json/);
      assert.ok(match, `unexpected translation URL: ${requestedUrl}`);
      const dictionaryPath = path.join(__dirname, '..', '..', 'i18n', match[1], `${match[2]}.json`);
      return createJsonResponse(JSON.parse(fs.readFileSync(dictionaryPath, 'utf8')));
    },
  });

  const passwordRequirementsSource = fs.readFileSync(
    path.join(__dirname, 'passwordRequirements.js'),
    'utf8',
  );
  vm.runInNewContext(passwordRequirementsSource, context, {
    filename: 'passwordRequirements.js',
  });

  const items = Array.from(context.passwordRequirementUtils.getVisibleItems({
    min_len: 10,
    min_special: 1,
    min_upper: 1,
    min_lower: 1,
    min_num: 1,
  }, context.getTranslation));

  assert.ok(fetchUrls.includes('/i18n/de/password-requirements.json?v=build-123'));
  assert.deepEqual(items.map(({ label }) => label), [
    'Mindestens 10 Zeichen',
    'Mindestens 1 Sonderzeichen',
    'Mindestens 1 Großbuchstabe',
    'Mindestens 1 Kleinbuchstabe',
    'Mindestens 1 Ziffer',
  ]);
});

test('generated search attributes follow every live locale switch and a fresh bootstrap', async () => {
  const localeSequence = ['de', 'en', 'es', 'fr', 'zh', 'hi', 'ar', 'ja', 'it', 'pt', 'ru'];
  const expectedPlaceholders = Object.fromEntries(localeSequence.map((locale) => {
    const dictionaryPath = path.join(__dirname, '..', '..', 'i18n', locale, 'index.json');
    const dictionary = JSON.parse(fs.readFileSync(dictionaryPath, 'utf8'));
    return [locale, dictionary.admin_search_placeholder];
  }));
  const liveSearchInput = createTranslatedSearchInput();
  const context = await runLanguageBootstrap({
    storage: createLocalStorage({ lang: 'de' }),
    fetchImpl: fetchI18nCatalog,
    translatableElements: [liveSearchInput],
  });

  for (const locale of localeSequence) {
    if (locale !== 'de') {
      await context.applyUserLanguagePreference(locale, { source: 'user' });
    }
    assert.equal(liveSearchInput.attributes.placeholder, expectedPlaceholders[locale]);
    assert.equal(liveSearchInput.attributes['aria-label'], expectedPlaceholders[locale]);
  }

  const freshEnglishSearchInput = createTranslatedSearchInput();
  await runLanguageBootstrap({
    storage: createLocalStorage({ lang: 'en' }),
    fetchImpl: fetchI18nCatalog,
    translatableElements: [freshEnglishSearchInput],
  });

  assert.equal(freshEnglishSearchInput.attributes.placeholder, expectedPlaceholders.en);
  assert.equal(freshEnglishSearchInput.attributes['aria-label'], expectedPlaceholders.en);
});

test('late-mounted group import controls use the active German translations', async () => {
  const context = await runLanguageBootstrap({
    storage: createLocalStorage({ lang: 'de' }),
    pageKey: 'admin',
    fetchImpl: fetchI18nCatalog,
  });
  const textElements = [
    createTranslatableElement({ 'data-i18n': 'modal_import_groups_title' }, 'Import Groups'),
    createTranslatableElement(
      { 'data-i18n': 'modal_import_groups_subtitle' },
      'Select which groups from the uploaded file should be created.',
    ),
    createTranslatableElement({ 'data-i18n': 'modal_choose_file' }, 'Choose file'),
    createTranslatableElement({ 'data-i18n': 'modal_select_all' }, 'Select all'),
    createTranslatableElement({ 'data-i18n': 'btn_cancel' }, 'Cancel'),
    createTranslatableElement({ 'data-i18n': 'modal_import_selected' }, 'Import Selected'),
  ];
  const closeButton = createTranslatableElement({
    'data-i18n-attr': 'aria-label:modal_close_aria',
    'aria-label': 'Close modal',
  });
  const lateModal = {
    querySelectorAll(selector) {
      if (selector === '[data-i18n]') return textElements;
      if (selector === '[data-i18n-attr]') return [closeButton];
      return [];
    },
  };

  context.translateI18nElements(lateModal);

  assert.deepEqual(textElements.map((element) => element.textContent), [
    'Gruppen importieren',
    'Wählen Sie, welche Gruppen aus der hochgeladenen Datei erstellt werden sollen.',
    'Datei auswählen',
    'Alle auswählen',
    'Abbrechen',
    'Ausgewählte importieren',
  ]);
  assert.equal(closeButton.attributes['aria-label'], 'Modal schließen');
});

test('late-mounted provider import controls use the active German translations', async () => {
  const context = await runLanguageBootstrap({
    storage: createLocalStorage({ lang: 'de' }),
    pageKey: 'admin',
    fetchImpl: fetchI18nCatalog,
  });
  const textElements = [
    createTranslatableElement({ 'data-i18n': 'modal_import_providers_title' }, 'Import Providers'),
    createTranslatableElement(
      { 'data-i18n': 'modal_import_providers_subtitle' },
      'Select the providers you want to import from this file.',
    ),
    createTranslatableElement({ 'data-i18n': 'modal_select_all' }, 'Select all'),
    createTranslatableElement(
      { 'data-i18n': 'providers_import_credentials_notice' },
      'API keys are not included in provider exports. Enter fresh keys for providers that require them.',
    ),
    createTranslatableElement({ 'data-i18n': 'btn_cancel' }, 'Cancel'),
    createTranslatableElement({ 'data-i18n': 'modal_import_selected' }, 'Import Selected'),
  ];
  const closeButton = createTranslatableElement({
    'data-i18n-attr': 'aria-label:modal_close_import_aria',
    'aria-label': 'Close import dialog',
  });
  const lateModal = {
    querySelectorAll(selector) {
      if (selector === '[data-i18n]') return textElements;
      if (selector === '[data-i18n-attr]') return [closeButton];
      return [];
    },
  };

  context.translateI18nElements(lateModal);

  assert.deepEqual(textElements.map((element) => element.textContent), [
    'Anbieter importieren',
    'Wählen Sie die Anbieter aus, die Sie aus dieser Datei importieren möchten.',
    'Alle auswählen',
    'API-Schlüssel sind in Anbieterexporten nicht enthalten. Geben Sie neue Schlüssel für Anbieter ein, die sie benötigen.',
    'Abbrechen',
    'Ausgewählte importieren',
  ]);
  assert.equal(closeButton.attributes['aria-label'], 'Importdialog schließen');
});

test('public chat share merges localized transcript vocabulary with page copy', async () => {
  const storage = createLocalStorage({ lang: 'de' });
  const fetchUrls = [];
  const germanTranscript = {
    chat_sr_transcript_label: 'Unterhaltungsverlauf',
    chat_sr_user_message_label: 'Deine Nachricht',
    chat_sr_user_message_status: 'Gesendet',
    chat_sr_assistant_message_label: 'Assistentenantwort',
    chat_sr_response_complete_status: 'Antwort vollständig',
  };

  const context = await runLanguageBootstrap({
    storage,
    pageKey: 'chat-share',
    fetchImpl: async (url) => {
      fetchUrls.push(String(url));
      if (String(url).includes('/de/index.json')) {
        return createJsonResponse(germanTranscript);
      }
      if (String(url).includes('/de/chat-share.json')) {
        return createJsonResponse({ chat_share_document_title_suffix: 'Geteilt' });
      }
      return createJsonResponse({});
    },
  });

  assert.deepEqual(fetchUrls, [
    '/i18n/en/index.json?v=build-123',
    '/i18n/en/chat-share.json?v=build-123',
    '/i18n/de/index.json?v=build-123',
    '/i18n/de/chat-share.json?v=build-123',
  ]);
  for (const [key, value] of Object.entries(germanTranscript)) {
    assert.equal(context.getTranslation(key), value);
  }
  assert.equal(context.getTranslation('chat_share_document_title_suffix'), 'Geteilt');
});

test('a late server language cannot undo an explicit user choice', async () => {
  const storage = createLocalStorage({ lang: 'en' });
  const context = await runLanguageBootstrap({
    storage,
    fetchImpl: async () => createJsonResponse({ late_language_test_key: 'translated' }),
  });

  await context.applyUserLanguagePreference('fr', { source: 'user' });
  const serverResult = await context.applyAuthenticatedLanguage('de');

  assert.equal(serverResult, false);
  assert.equal(storage.getItem('lang'), 'fr');
  assert.equal(context.document.documentElement.attributes.lang, 'fr');
});

test('translation formatting selects locale-aware ICU plural forms', async () => {
  const storage = createLocalStorage();
  const context = await runLanguageBootstrap({
    storage,
    fetchImpl: async (url) => createJsonResponse(
      String(url).includes('/ru/')
        ? {
            plural_days: 'через {days, plural, one {# день} few {# дня} many {# дней} other {# дня}}',
          }
        : {
            plural_records: '{count, plural, one {# matching record} other {# matching records}}',
          }
    ),
  });

  assert.equal(context.formatTranslation('plural_records', '', { count: 1 }), '1 matching record');
  assert.equal(context.formatTranslation('plural_records', '', { count: 2 }), '2 matching records');

  await context.applyUserLanguagePreference('ru', { source: 'user' });
  assert.equal(context.formatTranslation('plural_days', '', { days: 1 }), 'через 1 день');
  assert.equal(context.formatTranslation('plural_days', '', { days: 2 }), 'через 2 дня');
  assert.equal(context.formatTranslation('plural_days', '', { days: 5 }), 'через 5 дней');
});
