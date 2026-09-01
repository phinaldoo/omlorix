const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const vm = require("node:vm");
const espree = require("espree");

const repositoryRoot = path.join(__dirname, "..", "..");
const rendererRoot = path.join(repositoryRoot, "electron", "renderer");

/** Load the browser catalog in an isolated context without starting Electron. */
async function loadLauncherCatalog() {
  const [environmentSource, source] = await Promise.all([
    fs.readFile(path.join(rendererRoot, "launcher-env-translations.js"), "utf8"),
    fs.readFile(path.join(rendererRoot, "launcher-translations.js"), "utf8"),
  ]);
  const context = { window: {} };
  vm.runInNewContext(environmentSource, context);
  vm.runInNewContext(source, context);
  return context.window.OmlorixLauncherTranslations;
}

/** Load the focused direct-key renderer catalog embedded in launcher.js. */
async function loadDirectLauncherCatalog() {
  const source = await fs.readFile(path.join(rendererRoot, "launcher.js"), "utf8");
  const start = source.indexOf("  const LAUNCHER_TRANSLATIONS =");
  const end = source.indexOf("\n  // The complete launcher catalog", start);
  assert(start >= 0 && end > start, "direct launcher catalog must remain discoverable");
  const context = {};
  vm.runInNewContext(
    `${source.slice(start, end)}\nthis.directLauncherCatalog = LAUNCHER_TRANSLATIONS;`,
    context,
  );
  return context.directLauncherCatalog;
}

/** Exercise the production translation helper against a minimal DOM fixture. */
function createLauncherTranslationHarness(rendererSource, catalog, language = "de") {
  const writes = new Map();

  function createElement(attributes) {
    const values = new Map(Object.entries(attributes));
    const elementWrites = [];
    const element = {
      nodeType: 1,
      dataset: {},
      getAttribute(name) {
        return values.get(name) ?? null;
      },
      setAttribute(name, value) {
        values.set(name, value);
        elementWrites.push({ name, value });
      },
      querySelectorAll() {
        return [];
      },
    };
    writes.set(element, elementWrites);
    return element;
  }

  const unchangedElement = createElement({ placeholder: "localhost" });
  const changedElement = createElement({ title: "Settings" });
  const elements = [unchangedElement, changedElement];
  const document = {
    nodeType: 9,
    createTreeWalker() {
      return { nextNode: () => null };
    },
    querySelectorAll() {
      return elements;
    },
  };

  // Evaluate the real production helpers without running the rest of the
  // Electron renderer, which depends on the complete launcher document.
  const start = rendererSource.indexOf("  const LAUNCHER_SOURCE_KEYS");
  const end = rendererSource.indexOf(
    "\n  // Renderer-created status rows",
    start,
  );
  assert(start >= 0 && end > start, "launcher translation helpers must exist");
  const context = {
    COMPLETE_LAUNCHER_TRANSLATIONS: catalog,
    Node: { ELEMENT_NODE: 1, TEXT_NODE: 3, DOCUMENT_NODE: 9 },
    NodeFilter: { SHOW_TEXT: 4 },
    document,
    launcherT(key, fallback, variables = {}) {
      const raw = catalog.locales[language]?.[key] ?? fallback;
      return String(raw).replace(/\{(\w+)\}/g, (match, name) => (
        Object.prototype.hasOwnProperty.call(variables, name)
          ? String(variables[name])
          : match
      ));
    },
  };
  vm.runInNewContext(
    `${rendererSource.slice(start, end)}\nthis.applyLauncherTranslationsForTest = applyLauncherTranslations;\nthis.translateLauncherSourceForTest = translateLauncherSource;`,
    context,
  );

  return {
    apply: () => context.applyLauncherTranslationsForTest(document),
    changedElement,
    unchangedElement,
    writesFor: (element) => writes.get(element),
    translate: (source) => context.translateLauncherSourceForTest(source),
  };
}

/** Read the English keys and source phrases from the setup catalog. */
async function loadSetupEnglishCatalog() {
  const source = await fs.readFile(
    path.join(rendererRoot, "setup-flow.js"),
    "utf8",
  );
  const ast = espree.parse(source, {
    ecmaVersion: "latest",
    sourceType: "script",
  });
  let catalog = {};
  const visit = (node) => {
    if (!node || typeof node !== "object") return;
    if (node.type === "VariableDeclarator" && node.id?.name === "TEXT") {
      const english = node.init?.properties?.find(
        (property) => property.key?.name === "en",
      )?.value;
      catalog = Object.fromEntries((english?.properties || [])
        .map((property) => [
          property.key?.name || property.key?.value,
          property.value?.value,
        ])
        .filter(([key, value]) => key && typeof value === "string"));
    }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === "object") visit(value);
    }
  };
  visit(ast);
  return catalog;
}

/** Load the browser setup catalog without starting Electron. */
async function loadSetupTranslations() {
  const source = await fs.readFile(
    path.join(rendererRoot, "setup-flow-translations.js"),
    "utf8",
  );
  const context = { window: {} };
  vm.runInNewContext(source, context);
  return context.window.OmlorixSetupTranslations;
}

/** Decode the small HTML entity set used by launcher source copy. */
function decodeHtml(value) {
  return value
    .replaceAll("&amp;", "&")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&nbsp;", " ");
}

test("complete launcher catalog matches every main-app language and preserves placeholders", async () => {
  const catalog = await loadLauncherCatalog();
  const appLanguages = (
    await fs.readdir(path.join(repositoryRoot, "frontend", "i18n"), {
      withFileTypes: true,
    })
  )
    .filter((entry) => entry.isDirectory() && entry.name !== "en")
    .map((entry) => entry.name)
    .sort();
  const launcherLanguages = Object.keys(catalog.locales).sort();
  const sourceEntries = Object.entries(catalog.source);

  assert.deepEqual(launcherLanguages, appLanguages);
  assert(
    sourceEntries.length >= 650,
    "the complete launcher catalog must retain all audited UI copy",
  );
  assert.equal(
    new Set(Object.values(catalog.source)).size,
    sourceEntries.length,
  );
  const auditedOperationalCopy = [
    'Docker is ready.',
    'Omlorix restarted.',
    'Omlorix updated and started.',
    'Omlorix restore completed.',
    'Another operation is already running: {value1}',
    'Could not load backup destinations. Make sure Omlorix is running and ready.',
    'Launcher updater feed URL must use HTTPS.',
  ];
  for (const phrase of auditedOperationalCopy) {
    assert(
      Object.values(catalog.source).includes(phrase),
      `operational launcher copy is missing from the catalog: ${phrase}`,
    );
  }

  for (const [key, english] of sourceEntries) {
    assert.match(key, /^launcher_ui_[a-z0-9_]+$/);
    const sourcePlaceholders = [...english.matchAll(/\{(\w+)\}/g)].map(
      (match) => match[1],
    ).sort();
    for (const language of launcherLanguages) {
      const translated = catalog.locales[language][key];
      assert.equal(
        typeof translated,
        "string",
        `${language}: ${key} must be translated`,
      );
      assert.notEqual(
        translated.trim(),
        "",
        `${language}: ${key} must not be empty`,
      );
      const translatedPlaceholders = [...translated.matchAll(/\{(\w+)\}/g)].map(
        (match) => match[1],
      ).sort();
      assert.deepEqual(
        translatedPlaceholders,
        sourcePlaceholders,
        `${language}: ${key} must preserve its placeholders`,
      );
    }
  }

  const sourceKeys = Object.keys(catalog.source).sort();
  for (const [language, translations] of Object.entries(catalog.locales)) {
    assert.deepEqual(
      Object.keys(translations).sort(),
      sourceKeys,
      `${language}: launcher catalog keys must exactly match the source catalog`,
    );
  }
});

test("stopped Docker guidance is concise and fully localized", async () => {
  const catalog = await loadLauncherCatalog();
  const rendererSource = await fs.readFile(
    path.join(rendererRoot, "launcher.js"),
    "utf8",
  );
  const key = "launcher_ui_docker_stopped_dashboard_actions_disabled";

  assert.equal(
    catalog.source[key],
    "Docker is installed, but Docker Desktop/Engine or Compose is not ready. Omlorix dashboard actions are disabled until Docker and Docker Compose are ready.",
  );
  assert.equal(
    catalog.locales.de[key],
    "Docker ist installiert, aber Docker Desktop/Engine oder Compose ist nicht bereit. Omlorix-Dashboard-Aktionen sind deaktiviert, bis Docker und Docker Compose bereit sind.",
  );
  assert.match(rendererSource, /dockerSetupSteps\.hidden\s*=\s*dockerStopped;/);
  assert.match(rendererSource, /if \(dockerStopped\) return;/);
});

test("Sunday uses reviewed weekday labels in every launcher locale", async () => {
  const [html, rendererSource, catalog] = await Promise.all([
    fs.readFile(path.join(rendererRoot, "launcher.html"), "utf8"),
    fs.readFile(path.join(rendererRoot, "launcher.js"), "utf8"),
    loadLauncherCatalog(),
  ]);
  const expectedLabels = {
    en: "Sunday",
    ar: "الأحد",
    de: "Sonntag",
    es: "Domingo",
    fr: "Dimanche",
    hi: "रविवार",
    it: "Domenica",
    ja: "日曜日",
    pt: "Domingo",
    ru: "Воскресенье",
    zh: "星期日",
  };

  assert.equal(catalog.source.launcher_ui_sun, expectedLabels.en);
  assert.deepEqual(
    Object.fromEntries(Object.entries(catalog.locales).map(([locale, translations]) => (
      [locale, translations.launcher_ui_sun]
    ))),
    Object.fromEntries(Object.entries(expectedLabels).filter(([locale]) => locale !== "en")),
  );
  assert.match(
    html,
    /<label><input type="checkbox" value="0" data-auto-update-weekday>Sunday<\/label>/,
  );

  for (const [locale, expected] of Object.entries(expectedLabels)) {
    const harness = createLauncherTranslationHarness(rendererSource, catalog, locale);
    assert.equal(harness.translate("Sunday"), expected, locale);
  }
});

test("non-Latin launcher locales do not silently retain English UI copy", async () => {
  const catalog = await loadLauncherCatalog();
  // These values are identifiers, product names, URLs, example paths, units,
  // or console layouts. Keeping them unchanged is intentional and clearer
  // than transliterating them. All other exact English matches in these
  // locales indicate a real untranslated fallback.
  const intentionalIdentityKeys = new Set([
    "launcher_ui_omlorix_server",
    "launcher_ui_omlorix",
    "launcher_ui_postgres",
    "launcher_ui_localhost",
    "launcher_ui_app",
    "launcher_ui_audit",
    "launcher_ui_logs_identifier",
    "launcher_ui_omlorix_admin_identifier",
    "launcher_ui_omlorix_user_files",
    "launcher_ui_us_east_1",
    "launcher_ui_optional_folder",
    "launcher_ui_omlorix_backend",
    "launcher_ui_docker",
    "launcher_ui_pgbouncer",
    "launcher_ui_redis_url",
    "launcher_ui_s3_compatible",
    "launcher_ui_google_cloud_storage",
    "launcher_ui_azure_blob_storage",
    "launcher_ui_webdav",
    "launcher_ui_webdav_url",
    "launcher_ui_https",
    "launcher_ui_postgresql_omlorix_secret_db_example_com_5432_omlorix",
    "launcher_ui_rediss_secret_redis_example_com_6380_0",
    "launcher_ui_https_s3_example_com",
    "launcher_ui_https_account_blob_core_windows_net",
    "launcher_ui_https_cloud_example_com_remote_php_dav_files_user",
    "launcher_ui_https_otel_collector_4317",
    "launcher_ui_http_localhost_3001",
    "launcher_ui_redis_redis_6379",
    "launcher_ui_path_to_fullchain_pem",
    "launcher_ui_path_to_privkey_pem",
    "launcher_ui_value1_value2",
    "launcher_ui_value1_value2_value3",
    "launcher_ui_ce_256_mb",
    "launcher_ui_ce_512_mb",
    "launcher_ui_ce_1_gb",
    "launcher_ui_ce_2_gb",
    "launcher_ui_ce_4_gb",
    "launcher_ui_ce_8_gb",
    "launcher_ui_service_action_heading",
    "launcher_ui_env_section_redis",
    "launcher_ui_env_section_opentelemetry",
    "launcher_ui_env_section_grafana",
  ]);

  for (const language of ["ar", "hi", "ja", "ru", "zh"]) {
    for (const [key, english] of Object.entries(catalog.source)) {
      if (catalog.locales[language][key] !== english) continue;
      assert(
        intentionalIdentityKeys.has(key),
        `${language}: ${key} still contains untranslated English copy`,
      );
    }
  }
});

test("direct-key launcher translations cover every runtime launcherT call", async () => {
  const [rendererSource, directCatalog, completeCatalog] = await Promise.all([
    fs.readFile(path.join(rendererRoot, "launcher.js"), "utf8"),
    loadDirectLauncherCatalog(),
    loadLauncherCatalog(),
  ]);
  const appLanguages = (
    await fs.readdir(path.join(repositoryRoot, "frontend", "i18n"), {
      withFileTypes: true,
    })
  )
    .filter((entry) => entry.isDirectory() && entry.name !== "en")
    .map((entry) => entry.name)
    .sort();
  assert.deepEqual(Object.keys(directCatalog).sort(), appLanguages);

  const referenceKeys = Object.keys(directCatalog.de).sort();
  for (const [language, translations] of Object.entries(directCatalog)) {
    assert.deepEqual(Object.keys(translations).sort(), referenceKeys, `${language}: direct-key catalog drift`);
    for (const key of referenceKeys) {
      assert.equal(typeof translations[key], "string", `${language}: ${key} must be translated`);
      assert.notEqual(translations[key].trim(), "", `${language}: ${key} must not be empty`);
      const expectedPlaceholders = [...directCatalog.de[key].matchAll(/\{(\w+)\}/g)]
        .map((match) => match[1])
        .sort();
      const actualPlaceholders = [...translations[key].matchAll(/\{(\w+)\}/g)]
        .map((match) => match[1])
        .sort();
      assert.deepEqual(actualPlaceholders, expectedPlaceholders, `${language}: ${key} placeholders`);
    }
  }

  const syntax = espree.parse(rendererSource, {
    ecmaVersion: "latest",
    sourceType: "script",
    loc: true,
  });
  const calls = [];
  const visit = (node) => {
    if (!node || typeof node !== "object") return;
    if (
      node.type === "CallExpression" &&
      node.callee?.type === "Identifier" &&
      node.callee.name === "launcherT" &&
      node.arguments[0]?.type === "Literal" &&
      typeof node.arguments[0].value === "string"
    ) {
      calls.push({ key: node.arguments[0].value, line: node.loc.start.line });
    }
    for (const [property, value] of Object.entries(node)) {
      if (property === "loc" || property === "range") continue;
      if (Array.isArray(value)) value.forEach(visit);
      else if (value?.type) visit(value);
    }
  };
  visit(syntax);

  for (const { key, line } of calls) {
    for (const language of appLanguages) {
      const translated = directCatalog[language][key] ?? completeCatalog.locales[language][key];
      assert.equal(
        typeof translated,
        "string",
        `launcher.js:${line} ${key} is missing from ${language}`,
      );
      assert.notEqual(translated.trim(), "", `launcher.js:${line} ${key} is empty in ${language}`);
    }
  }
});

test("setup catalog translates every onboarding key in every supported language", async () => {
  const [english, translations, html] = await Promise.all([
    loadSetupEnglishCatalog(),
    loadSetupTranslations(),
    fs.readFile(path.join(rendererRoot, "launcher.html"), "utf8"),
  ]);
  const appLanguages = (
    await fs.readdir(path.join(repositoryRoot, "frontend", "i18n"), {
      withFileTypes: true,
    })
  )
    .filter((entry) => entry.isDirectory() && entry.name !== "en")
    .map((entry) => entry.name)
    .sort();

  assert.deepEqual(Object.keys(translations).sort(), appLanguages);
  for (const [key, source] of Object.entries(english)) {
    const sourcePlaceholders = [...source.matchAll(/\{(\w+)\}/g)]
      .map((match) => match[1])
      .sort();
    for (const language of appLanguages) {
      const translated = translations[language][key];
      assert.equal(typeof translated, "string", `${language}: ${key} must be translated`);
      assert.notEqual(translated.trim(), "", `${language}: ${key} must not be empty`);
      const translatedPlaceholders = [...translated.matchAll(/\{(\w+)\}/g)]
        .map((match) => match[1])
        .sort();
      assert.deepEqual(
        translatedPlaceholders,
        sourcePlaceholders,
        `${language}: ${key} must preserve placeholders`,
      );
    }
  }

  for (const match of html.matchAll(/data-setup-i18n(?:-aria-label)?="([^"]+)"/g)) {
    assert.equal(
      typeof english[match[1]],
      "string",
      `setup markup references an unknown translation key: ${match[1]}`,
    );
  }

  const setupStart = html.indexOf('<div class="setup-overlay"');
  const setupEnd = html.indexOf('<div class="launcher-dialog-overlay"', setupStart);
  const setupMarkup = html
    .slice(setupStart, setupEnd)
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<svg\b[\s\S]*?<\/svg>/gi, "");
  const intentionalSetupCopy = new Set([
    "C",
    "Omlorix Server",
    "S3-compatible",
    "Google Cloud Storage",
    "Azure Blob Storage",
    "WebDAV",
  ]);
  for (const match of setupMarkup.matchAll(/<([a-z0-9-]+)\b([^>]*)>([^<]+)</gi)) {
    const phrase = decodeHtml(match[3]).trim().replace(/\s+/g, " ");
    if (!/[A-Za-z]/.test(phrase) || /data-setup-i18n=/.test(match[2])) continue;
    assert(
      intentionalSetupCopy.has(phrase),
      `setup text is not connected to a translation key: ${phrase}`,
    );
  }
});

test("launcher markup and accessibility copy are covered by the complete catalog", async () => {
  const [html, catalog, setupPhrases] = await Promise.all([
    fs.readFile(path.join(rendererRoot, "launcher.html"), "utf8"),
    loadLauncherCatalog(),
    loadSetupEnglishCatalog(),
  ]);
  const sources = new Set([...Object.values(catalog.source), ...Object.values(setupPhrases)]);
  const setupStart = html.indexOf('<div class="setup-overlay"');
  const setupEnd = html.indexOf(
    '<div class="launcher-dialog-overlay"',
    setupStart,
  );
  assert(
    setupStart >= 0 && setupEnd > setupStart,
    "setup overlay boundaries must remain discoverable",
  );

  // The onboarding overlay has its own independently validated setup catalog.
  const launcherHtml = `${html.slice(0, setupStart)}${html.slice(setupEnd)}`
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<svg\b[\s\S]*?<\/svg>/gi, "")
    .replace(/<(?:script|style|code)\b[\s\S]*?<\/(?:script|style|code)>/gi, "");
  const visiblePhrases = decodeHtml(launcherHtml.replace(/<[^>]+>/g, "\0"))
    .split("\0")
    .map((value) => value.trim().replace(/\s+/g, " "))
    .filter((value) => value && /[A-Za-z]/.test(value));

  for (const phrase of new Set(visiblePhrases)) {
    assert(
      sources.has(phrase),
      `launcher text is missing from the catalog: ${phrase}`,
    );
  }

  const technicalPlaceholder =
    /^(?:https?:\/\/|postgres(?:ql)?:\/\/|rediss?:\/\/|(?:[\w.-]*\.[\w.-]+(?::\d+)?|[\w.-]+:\d+)(?:[/?,].*)?|\d+(?:\.\d+)?|MY_CUSTOM_SETTING)$/;
  for (const tag of launcherHtml.match(/<[^>]+>/g) || []) {
    if (/data-setup-i18n-aria-label=/.test(tag)) continue;
    for (const match of tag.matchAll(
      /(?:aria-label|title|placeholder)="([^"]+)"/g,
    )) {
      const phrase = decodeHtml(match[1]).trim();
      if (!/[A-Za-z]/.test(phrase) || technicalPlaceholder.test(phrase))
        continue;
      assert(
        sources.has(phrase),
        `launcher attribute is missing from the catalog: ${phrase}`,
      );
    }
  }

  assert.match(
    html,
    /<script src="launcher-translations\.js"><\/script>[\s\S]*<script src="launcher\.js"><\/script>/,
  );
});

test("runtime launcher translations observe accessibility attribute changes", async () => {
  const source = await fs.readFile(path.join(rendererRoot, "launcher.js"), "utf8");

  assert.match(source, /record\.type === 'attributes'/);
  assert.match(source, /attributes: true/);
  assert.match(source, /attributeFilter: \['aria-label', 'title', 'placeholder'\]/);
  assert.doesNotMatch(
    source,
    /overallBadge\.setAttribute\('aria-label', 'Failed to refresh launcher status\.'/,
  );
});

test("custom environment remove buttons localize their accessible names", async () => {
  const [rendererSource, catalog] = await Promise.all([
    fs.readFile(path.join(rendererRoot, "launcher.js"), "utf8"),
    loadLauncherCatalog(),
  ]);
  const renderStart = rendererSource.indexOf("  function renderEnvField(field)");
  const renderEnd = rendererSource.indexOf(
    "\n  function renderEnvSectionFilter()",
    renderStart,
  );
  const renderSource = rendererSource.slice(renderStart, renderEnd);

  assert(renderStart >= 0 && renderEnd > renderStart, "environment field renderer must remain discoverable");
  assert.match(
    renderSource,
    /removeButton\.setAttribute\(\s*'aria-label',\s*launcherT\(\s*'launcher_ui_remove_custom_environment_variable_value1',\s*'Remove custom environment variable \{value1\}',\s*\{ value1: field\.key \},\s*\),\s*\);/,
  );
  assert.doesNotMatch(
    renderSource,
    /setAttribute\(\s*'aria-label',\s*`Remove custom environment variable/,
  );
  assert.equal(
    catalog.locales.de.launcher_ui_remove_custom_environment_variable_value1
      .replace("{value1}", "E2E_LAUNCHER_TEST"),
    "Benutzerdefinierte Umgebungsvariable E2E_LAUNCHER_TEST entfernen",
  );
});

test("runtime launcher translations skip unchanged accessibility attributes", async () => {
  const [rendererSource, html, catalog] = await Promise.all([
    fs.readFile(path.join(rendererRoot, "launcher.js"), "utf8"),
    fs.readFile(path.join(rendererRoot, "launcher.html"), "utf8"),
    loadLauncherCatalog(),
  ]);

  // German intentionally preserves several technical placeholders. These
  // values reproduce the no-op attribute translation that previously caused
  // MutationObserver to retrigger itself indefinitely during launcher load.
  const sourceKeys = new Map(
    Object.entries(catalog.source).map(([key, value]) => [value, key]),
  );
  const unchangedAttributes = [...html.matchAll(
    /(?:aria-label|title|placeholder)="([^"]+)"/g,
  )]
    .map((match) => match[1])
    .filter((value) => {
      const key = sourceKeys.get(value);
      return key && catalog.locales.de[key] === value;
    });

  assert(
    unchangedAttributes.includes("localhost"),
    "the fixture must retain a real unchanged translated attribute",
  );

  const harness = createLauncherTranslationHarness(rendererSource, catalog);
  const settingsKey = sourceKeys.get("Settings");
  const translatedSettings = catalog.locales.de[settingsKey];
  assert.notEqual(translatedSettings, "Settings");

  harness.apply();

  assert.deepEqual(harness.writesFor(harness.unchangedElement), []);
  assert.deepEqual(harness.writesFor(harness.changedElement), [
    { name: "title", value: translatedSettings },
  ]);
  assert.equal(
    harness.changedElement.getAttribute("title"),
    translatedSettings,
  );
});

test("runtime launcher translations normalize whitespace in wrapped markup", async () => {
  const [rendererSource, catalog] = await Promise.all([
    fs.readFile(path.join(rendererRoot, "launcher.js"), "utf8"),
    loadLauncherCatalog(),
  ]);
  const harness = createLauncherTranslationHarness(rendererSource, catalog, "de");
  const key = "launcher_ui_omlorix_can_run_everything_it_needs_on_this_computer_or";

  assert.equal(
    harness.translate(
      "\n            Omlorix can run everything it needs on this computer, or connect to services you already\n            have.\n          ",
    ),
    catalog.locales.de[key],
  );
});

test("Environment metadata uses stable keys covered by every launcher locale", async () => {
  const [metadataRaw, rendererSource, catalog] = await Promise.all([
    fs.readFile(path.join(repositoryRoot, "electron", "env-metadata.json"), "utf8"),
    fs.readFile(path.join(rendererRoot, "launcher.js"), "utf8"),
    loadLauncherCatalog(),
  ]);
  const metadata = JSON.parse(metadataRaw);
  const sectionKeys = metadata.translations?.sections || {};
  const descriptionKeys = metadata.translations?.descriptions || {};

  assert.equal(Object.keys(descriptionKeys).length, Object.keys(metadata.fields).length);
  assert.equal(new Set(Object.values(descriptionKeys)).size, Object.keys(metadata.fields).length);
  for (const [fieldKey, field] of Object.entries(metadata.fields)) {
    const sectionKey = sectionKeys[field.section];
    const descriptionKey = descriptionKeys[fieldKey];
    assert.equal(typeof sectionKey, "string", `${field.section}: stable section key`);
    assert.equal(typeof descriptionKey, "string", `${fieldKey}: stable description key`);
    assert.equal(catalog.source[descriptionKey], field.description, `${fieldKey}: English source contract`);
    for (const [language, translations] of Object.entries(catalog.locales)) {
      assert.equal(typeof translations[sectionKey], "string", `${language}: ${sectionKey}`);
      assert.equal(typeof translations[descriptionKey], "string", `${language}: ${descriptionKey}`);
      assert.notEqual(translations[descriptionKey].trim(), "", `${language}: ${descriptionKey}`);
    }
  }

  assert.match(rendererSource, /launcherT\(\s*field\.descriptionKey,/);
  assert.match(rendererSource, /launcherT\(groupField\?\.sectionKey, group\)/);
  assert.doesNotMatch(rendererSource, /description\.textContent = field\.description/);
  assert.equal(
    catalog.locales.de[descriptionKeys.ALLOW_LOCAL_OR_PRIVATE_ORIGINS],
    "Erlaubt Browser-Ursprünge von localhost, Loopback- oder privaten IP-Adressen. Nur aktivieren, wenn dieser Server absichtlich über solche Ursprünge erreichbar ist.",
  );
  assert.match(
    catalog.locales.ja[descriptionKeys.OMLORIX_VERSION],
    /Omlorix サーバーのバージョン/,
  );
});

test("Console empty state is localized DOM content instead of CSS-generated copy", async () => {
  const [html, css, catalog] = await Promise.all([
    fs.readFile(path.join(rendererRoot, "launcher.html"), "utf8"),
    fs.readFile(path.join(rendererRoot, "launcher.css"), "utf8"),
    loadLauncherCatalog(),
  ]);

  assert.match(html, /id="consoleEmpty">Console output will appear here\.<\/p>/);
  assert.match(html, /aria-describedby="consoleEmpty"/);
  assert.doesNotMatch(css, /content:\s*["']Console output will appear here\./);
  assert.equal(
    catalog.locales.de.launcher_ui_console_output_will_appear_here,
    "Die Konsolenausgabe wird hier angezeigt.",
  );
  assert.equal(
    catalog.locales.ja.launcher_ui_console_output_will_appear_here,
    "コンソール出力がここに表示されます。",
  );
});

test("launcher console translates manager messages after the operation marker", async () => {
  const [rendererSource, catalog] = await Promise.all([
    fs.readFile(path.join(rendererRoot, "launcher.js"), "utf8"),
    loadLauncherCatalog(),
  ]);
  const translationHarness = createLauncherTranslationHarness(rendererSource, catalog, "ja");
  const start = rendererSource.indexOf("  function translateLauncherConsoleMessage");
  const end = rendererSource.indexOf("\n  function appendConsole", start);
  assert(start >= 0 && end > start, "console translation helper must remain discoverable");
  const context = {
    translateLauncherSource: translationHarness.translate,
  };
  vm.runInNewContext(
    `${rendererSource.slice(start, end)}\nthis.translateConsoleForTest = translateLauncherConsoleMessage;`,
    context,
  );

  assert.equal(context.translateConsoleForTest("> Stopping Omlorix"), "> Omlorix を停止中");
  assert.equal(
    context.translateConsoleForTest("> Another operation is already running: Refresh"),
    "> 別の操作がすでに実行中です: Refresh",
  );
});

test("source template matching prefers fixed copy over generic layouts", async () => {
  const [rendererSource, catalog] = await Promise.all([
    fs.readFile(path.join(rendererRoot, "launcher.js"), "utf8"),
    loadLauncherCatalog(),
  ]);
  const translationHarness = createLauncherTranslationHarness(rendererSource, catalog, "ja");

  /** Substitute the same named values into an English source and translation. */
  function interpolate(value, variables) {
    return String(value).replace(/\{(\w+)\}/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(variables, name)
        ? String(variables[name])
        : match
    ));
  }

  // Each specific source below also matches a broader placeholder-first
  // template. The fixed launcher wording must determine the selected key.
  for (const [key, variables] of [
    ["launcher_ui_settings_failed_value1", { value1: "詳細" }],
    ["launcher_ui_status_failed_value1", { value1: "詳細" }],
    ["launcher_ui_logs_failed_value1", { value1: "詳細" }],
    ["launcher_ui_stable_value1_value2", { value1: "1.0.0", value2: "2.0.0" }],
    ["launcher_ui_stopped_value1", { value1: "証明書なし" }],
    ["launcher_ui_count_known_keys_are_not_in_the_import_file", { count: "2" }],
    ["launcher_ui_count_lines_are_not_key_value_assignments", { count: "2" }],
  ]) {
    const source = interpolate(catalog.source[key], variables);
    const expected = interpolate(catalog.locales.ja[key], variables);
    assert.equal(translationHarness.translate(source), expected, key);
  }
});

test("technical manager failures preserve distinct translated recovery details", async () => {
  const catalog = await loadLauncherCatalog();
  const technicalKeys = [
    "launcher_ui_background_proxy_service_operation_failed",
    "launcher_ui_background_proxy_service_unavailable_build",
    "launcher_ui_windows_proxy_permission_failed",
    "launcher_ui_windows_proxy_operation_cancelled_or_failed",
    "launcher_ui_private_launcher_network_creation_failed",
    "launcher_ui_compose_ownership_verification_failed",
    "launcher_ui_compose_project_changed_before_adoption",
    "launcher_ui_legacy_compose_verification_failed",
    "launcher_ui_legacy_compose_no_containers",
    "launcher_ui_compose_resources_owned_elsewhere",
    "launcher_ui_attach_backend_to_launcher_network_failed",
    "launcher_ui_frontend_not_found_on_named_network",
    "launcher_ui_proxy_authentication_credential_unavailable",
    "launcher_ui_docker_network_topology_unstable",
    "launcher_ui_visitor_ip_verification_failed",
    "launcher_ui_visitor_ip_restore_after_failure",
    "launcher_ui_launcher_updater_not_configured",
    "launcher_ui_launcher_updater_message_box_required",
  ];

  for (const [language, translations] of Object.entries(catalog.locales)) {
    const localizedMessages = technicalKeys.map((key) => translations[key]);
    assert.equal(
      new Set(localizedMessages).size,
      technicalKeys.length,
      `${language}: technical manager failures must retain distinct explanations`,
    );
  }
});

test("JavaScript-composed launcher copy resolves before it reaches a render sink", async () => {
  const [rendererSource, catalog] = await Promise.all([
    fs.readFile(path.join(rendererRoot, "launcher.js"), "utf8"),
    loadLauncherCatalog(),
  ]);
  const sourcePatterns = new Set(Object.values(catalog.source).map((source) => (
    source.trim().replace(/\{\w+\}/g, "{}")
  )));
  const syntax = espree.parse(rendererSource, {
    ecmaVersion: "latest",
    sourceType: "script",
    loc: true,
  });

  /** Build the possible text shapes produced by conditionals and templates. */
  function renderedShapes(node) {
    if (!node) return [""];
    if (node.type === "Literal") {
      return typeof node.value === "string" ? [node.value] : ["{}"];
    }
    if (node.type === "TemplateLiteral") {
      let shapes = [""];
      node.quasis.forEach((quasi, index) => {
        shapes = shapes.map((shape) => shape + quasi.value.cooked);
        if (index < node.expressions.length) {
          shapes = shapes.flatMap((shape) => (
            renderedShapes(node.expressions[index]).map((value) => shape + (value || "{}"))
          ));
        }
      });
      return shapes;
    }
    if (node.type === "ConditionalExpression") {
      return [...renderedShapes(node.consequent), ...renderedShapes(node.alternate)];
    }
    if (node.type === "LogicalExpression") {
      return [...renderedShapes(node.left), ...renderedShapes(node.right)];
    }
    if (node.type === "BinaryExpression" && node.operator === "+") {
      return renderedShapes(node.left).flatMap((left) => (
        renderedShapes(node.right).map((right) => left + right)
      ));
    }
    if (
      node.type === "CallExpression" &&
      node.callee.type === "Identifier" &&
      ["launcherT", "translateLauncherSource"].includes(node.callee.name)
    ) {
      return ["translated"];
    }
    return ["{}"];
  }

  const uncovered = [];
  const visit = (node) => {
    if (!node || typeof node !== "object") return;
    if (
      node.type === "AssignmentExpression" &&
      node.left.type === "MemberExpression" &&
      ["textContent", "innerText", "title", "placeholder", "ariaLabel"].includes(
        node.left.property?.name,
      )
    ) {
      for (const rawShape of renderedShapes(node.right)) {
        const shape = rawShape.trim().replace(/\{\}/g, "{}");
        if (!/[A-Za-z]/.test(shape) || shape === "translated" || sourcePatterns.has(shape)) continue;
        uncovered.push(`launcher.js:${node.loc.start.line} ${shape}`);
      }
    }
    for (const [property, value] of Object.entries(node)) {
      if (property === "loc" || property === "range") continue;
      if (Array.isArray(value)) value.forEach(visit);
      else if (value?.type) visit(value);
    }
  };
  visit(syntax);

  assert.deepEqual(uncovered, []);
});

test("shared validation and scheduled-update messages are launcher-cataloged", async () => {
  const catalog = await loadLauncherCatalog();
  const sources = new Set(Object.values(catalog.source));

  /** Convert a source expression into every possible catalog contract. */
  function sourceContracts(node) {
    if (node?.type === "Literal" && typeof node.value === "string") {
      return [node.value];
    }
    if (node?.type === "TemplateLiteral") {
      let source = "";
      node.quasis.forEach((quasi, index) => {
        source += quasi.value.cooked;
        if (index < node.expressions.length) source += `{value${index + 1}}`;
      });
      return [source];
    }
    if (node?.type === "ConditionalExpression") {
      return [
        ...sourceContracts(node.consequent),
        ...sourceContracts(node.alternate),
      ];
    }
    if (node?.type === "LogicalExpression") {
      return [
        ...sourceContracts(node.left),
        ...sourceContracts(node.right),
      ];
    }
    return [];
  }

  async function parseElectronSource(fileName) {
    return espree.parse(
      await fs.readFile(path.join(repositoryRoot, "electron", fileName), "utf8"),
      { ecmaVersion: "latest", sourceType: "script", loc: true },
    );
  }

  const missing = [];
  const inspect = (syntax, fileName, selectedFunctions, collectNode) => {
    const inspectedByFunction = new Map(
      [...selectedFunctions].map((functionName) => [functionName, 0]),
    );
    const boundFunctionTypes = new Set([
      "FunctionExpression",
      "ArrowFunctionExpression",
    ]);

    /** Resolve the function introduced by declarations and common bindings. */
    function declaredFunctionName(node) {
      if (node.type === "FunctionDeclaration") return node.id?.name || "";

      if (
        node.type === "VariableDeclarator"
        && node.id?.type === "Identifier"
        && boundFunctionTypes.has(node.init?.type)
      ) {
        return node.id.name;
      }
      if (
        ["Property", "MethodDefinition", "PropertyDefinition"].includes(node.type)
        && boundFunctionTypes.has(node.value?.type)
      ) {
        if (node.key?.type === "Identifier") return node.key.name;
        if (node.key?.type === "Literal") return String(node.key.value || "");
      }
      return "";
    }

    const visit = (node, functionName = "") => {
      if (!node || typeof node !== "object") return;
      const scope = declaredFunctionName(node) || functionName;
      if (selectedFunctions.has(scope)) {
        for (const candidate of collectNode(node)) {
          for (const contract of sourceContracts(candidate)) {
            if (!contract || !/[A-Za-z]/.test(contract)) continue;
            inspectedByFunction.set(scope, inspectedByFunction.get(scope) + 1);
            if (!sources.has(contract)) {
              missing.push(`${fileName}:${candidate.loc.start.line} ${contract}`);
            }
          }
        }
      }
      for (const [property, value] of Object.entries(node)) {
        if (property === "loc" || property === "range") continue;
        if (Array.isArray(value)) value.forEach((child) => visit(child, scope));
        else if (value?.type) visit(value, scope);
      }
    };
    visit(syntax);

    // A renamed or refactored function must fail this test instead of turning
    // its catalog check into a silent no-op.
    for (const [functionName, inspectedCount] of inspectedByFunction) {
      assert(
        inspectedCount > 0,
        `${fileName}: ${functionName} produced no inspected message candidates`,
      );
    }
  };

  inspect(
    await parseElectronSource("server-proxy.js"),
    "server-proxy.js",
    new Set(["validatePort", "validateBindHost", "validatePublicHostname", "validateProxyConfig"]),
    (node) => {
      if (node.type === "ReturnStatement") return [node.argument];
      if (
        node.type === "AssignmentExpression" &&
        node.left.type === "MemberExpression" &&
        node.left.object?.name === "errors"
      ) return [node.right];
      if (node.type === "NewExpression" && node.callee?.name === "Error") return [node.arguments[0]];
      return [];
    },
  );
  inspect(
    await parseElectronSource("server-manager.js"),
    "server-manager.js",
    new Set(["validateEnvValue", "buildEnvRequirementStatus"]),
    (node) => {
      if (node.type === "ReturnStatement") return [node.argument];
      if (node.type === "CallExpression" && node.callee?.name === "envRequirementIssue") {
        return [node.arguments[1]];
      }
      return [];
    },
  );
  inspect(
    await parseElectronSource("scheduled-updates.js"),
    "scheduled-updates.js",
    new Set(["saveSettings", "setRunningStatus", "runUpdate"]),
    (node) => {
      if (node.type === "Property" && node.key?.name === "lastMessage") return [node.value];
      if (
        node.type === "CallExpression" &&
        node.callee.type === "MemberExpression" &&
        ["recordSkipped", "recordSuccess"].includes(node.callee.property?.name)
      ) return [node.arguments[0]];
      return [];
    },
  );

  const setupSource = await fs.readFile(path.join(rendererRoot, "setup-flow.js"), "utf8");
  assert.match(setupSource, /function translateLauncherMessage\(message\)/);
  assert.match(setupSource, /const message = translateLauncherMessage\(rawMessage\)/);
  assert.deepEqual(missing, []);
});

test("renderer-created feedback uses cataloged source phrases", async () => {
  const [rendererSource, catalog] = await Promise.all([
    fs.readFile(path.join(rendererRoot, "launcher.js"), "utf8"),
    loadLauncherCatalog(),
  ]);
  const sources = new Set(Object.values(catalog.source));
  const syntax = espree.parse(rendererSource, {
    ecmaVersion: "latest",
    sourceType: "script",
    loc: true,
  });
  const candidates = [];
  const checkedCalls = new Set([
    "appendConsole",
    "runAction",
    "runProxyAction",
    "setStatus",
    "setBadge",
    "setAutoUpdateValidation",
  ]);

  function literalText(node) {
    if (node?.type === "Literal" && typeof node.value === "string")
      return node.value.trim();
    if (node?.type !== "TemplateLiteral") return "";
    let value = "";
    node.quasis.forEach((quasi, index) => {
      value += quasi.value.cooked;
      if (index < node.expressions.length) value += `{value${index + 1}}`;
    });
    return value.trim();
  }

  function visit(node) {
    if (!node || typeof node !== "object") return;
    if (
      node.type === "AssignmentExpression" &&
      node.left.type === "MemberExpression" &&
      ["textContent", "title", "placeholder", "ariaLabel"].includes(
        node.left.property?.name,
      )
    ) {
      const value = literalText(node.right);
      if (value && /[A-Za-z]/.test(value))
        candidates.push([node.loc.start.line, value]);
    }
    if (node.type === "CallExpression") {
      const name =
        node.callee.type === "Identifier"
          ? node.callee.name
          : node.callee.type === "MemberExpression"
            ? node.callee.property?.name
            : "";
      if (checkedCalls.has(name)) {
        for (const argument of node.arguments) {
          const value = literalText(argument);
          if (value && /[A-Za-z]/.test(value))
            candidates.push([node.loc.start.line, value]);
        }
      }
    }
    for (const [property, value] of Object.entries(node)) {
      if (property === "loc" || property === "range") continue;
      if (Array.isArray(value)) value.forEach(visit);
      else if (value?.type) visit(value);
    }
  }
  visit(syntax);

  // Structural values are not rendered copy: they are error kinds, generic
  // pass-through values, or console-prefix layouts whose labels are translated
  // independently before interpolation.
  const structuralValues = new Set([
    "error",
    "{value1}",
    "{value1} {value2}",
    "> {value1}",
  ]);
  for (const [line, phrase] of candidates) {
    if (structuralValues.has(phrase)) continue;
    assert(
      sources.has(phrase),
      `launcher.js:${line} feedback is missing from the catalog: ${phrase}`,
    );
  }
});
