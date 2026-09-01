const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

const {
  NATIVE_LAUNCHER_EXTRA,
  NATIVE_LAUNCHER_KEYS,
  NATIVE_LAUNCHER_ROWS,
  NATIVE_MENU_ROLE_KEYS,
  NATIVE_MENU_ROLE_ROWS,
  createLauncherTranslator,
  launcherLocale,
} = require("../launcher-native-i18n");

test("native launcher catalog matches every main-app language and placeholder contract", async () => {
  const appLanguages = (
    await fs.readdir(path.join(__dirname, "..", "..", "frontend", "i18n"), {
      withFileTypes: true,
    })
  )
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort();

  assert.deepEqual(Object.keys(NATIVE_LAUNCHER_ROWS).sort(), appLanguages);
  assert.deepEqual(Object.keys(NATIVE_LAUNCHER_EXTRA).sort(), appLanguages);
  assert.deepEqual(Object.keys(NATIVE_MENU_ROLE_ROWS).sort(), appLanguages);
  assert(NATIVE_LAUNCHER_KEYS.length >= 60);
  assert.equal(new Set(NATIVE_LAUNCHER_KEYS).size, NATIVE_LAUNCHER_KEYS.length);

  for (const [language, row] of Object.entries(NATIVE_LAUNCHER_ROWS)) {
    assert.equal(typeof NATIVE_LAUNCHER_EXTRA[language].cancel, "string");
    for (const key of [
      "legacy_compose_adoption_title",
      "legacy_compose_adoption_detail",
      "legacy_compose_adoption_action",
      "open_omlorix_failed",
      "show_server_files_failed",
    ]) {
      assert.equal(typeof NATIVE_LAUNCHER_EXTRA[language][key], "string", `${language} is missing ${key}`);
      assert.notEqual(NATIVE_LAUNCHER_EXTRA[language][key].trim(), "");
    }
    assert.equal(
      row.length,
      NATIVE_LAUNCHER_KEYS.length,
      `${language} must contain every key`,
    );
    row.forEach((translation, index) => {
      const effectiveTranslation =
        NATIVE_LAUNCHER_EXTRA[language][NATIVE_LAUNCHER_KEYS[index]] ||
        translation;
      assert.equal(typeof effectiveTranslation, "string");
      assert.notEqual(effectiveTranslation.trim(), "");
      assert.doesNotMatch(
        effectiveTranslation,
        /XQZ/i,
        `${language} contains a generation token`,
      );
      const englishPlaceholders = [
        ...NATIVE_LAUNCHER_ROWS.en[index].matchAll(/\{(\w+)\}/g),
      ]
        .map((match) => match[1])
        .sort();
      const translatedPlaceholders = [
        ...effectiveTranslation.matchAll(/\{(\w+)\}/g),
      ]
        .map((match) => match[1])
        .sort();
      assert.deepEqual(
        translatedPlaceholders,
        englishPlaceholders,
        `${language}: ${NATIVE_LAUNCHER_KEYS[index]} must preserve placeholders`,
      );
    });

    const menuRoleRow = NATIVE_MENU_ROLE_ROWS[language];
    assert.equal(
      menuRoleRow.length,
      NATIVE_MENU_ROLE_KEYS.length,
      `${language} must translate every role-backed menu action`,
    );
    menuRoleRow.forEach((translation, index) => {
      const key = NATIVE_MENU_ROLE_KEYS[index];
      assert.equal(NATIVE_LAUNCHER_EXTRA[language][key], translation);
      assert.notEqual(translation.trim(), '');
      const englishPlaceholders = [
        ...NATIVE_MENU_ROLE_ROWS.en[index].matchAll(/\{(\w+)\}/g),
      ].map((match) => match[1]).sort();
      const translatedPlaceholders = [
        ...translation.matchAll(/\{(\w+)\}/g),
      ].map((match) => match[1]).sort();
      assert.deepEqual(
        translatedPlaceholders,
        englishPlaceholders,
        `${language}: ${key} must preserve placeholders`,
      );
    });
  }
});

test("native translator normalizes locales and substitutes named values", () => {
  assert.equal(launcherLocale("de-DE"), "de");
  assert.equal(launcherLocale("unknown"), "en");
  const translate = createLauncherTranslator("de-DE");
  assert.equal(translate("cancel"), "Abbrechen");
  assert.equal(translate("menu_undo"), "Rückgängig");
  assert.equal(
    translate("menu_about_app", { appName: "Omlorix Server Launcher" }),
    "Über Omlorix Server Launcher",
  );
  assert.match(
    translate("launcher_version_available", { latestVersion: "2.4.0" }),
    /2\.4\.0/,
  );
  assert.doesNotMatch(
    translate("launcher_version_available", { latestVersion: "2.4.0" }),
    /\{latestVersion\}/,
  );

  for (const language of Object.keys(NATIVE_LAUNCHER_ROWS)) {
    assert.match(
      createLauncherTranslator(language)("open_omlorix"),
      /Omlorix/,
      `${language}: native Open Omlorix action must preserve the product name`,
    );
  }
});
