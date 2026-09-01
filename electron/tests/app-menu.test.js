const test = require('node:test');
const assert = require('node:assert/strict');

const {
  APP_NAME,
  createAboutPanelOptions,
  createMacApplicationMenuTemplate,
  desktopBuildVersion,
} = require('../app-menu');
const { createLauncherTranslator } = require('../launcher-native-i18n');

test('mac application menu starts with the native About role', () => {
  const menu = createMacApplicationMenuTemplate(APP_NAME);

  assert.equal(menu.label, APP_NAME);
  assert.equal(menu.submenu[0].role, 'about');
  assert.ok(menu.submenu.some((item) => item.role === 'services'));
  assert.ok(menu.submenu.some((item) => item.role === 'quit'));
});

test('mac application menu can place launcher update action after About', () => {
  const updateItem = { label: 'Check for Updates...', click: () => {} };
  const menu = createMacApplicationMenuTemplate(APP_NAME, [updateItem]);

  assert.equal(menu.submenu[0].role, 'about');
  assert.equal(menu.submenu[1], updateItem);
  assert.equal(menu.submenu[2].type, 'separator');
});

test('mac application menu explicitly localizes German role labels and accessibility text', () => {
  const menu = createMacApplicationMenuTemplate(
    APP_NAME,
    [],
    createLauncherTranslator('de-DE'),
  );
  const roleItems = menu.submenu.filter((item) => item.role);

  assert.deepEqual(
    Object.fromEntries(roleItems.map((item) => [item.role, item.label])),
    {
      about: `Über ${APP_NAME}`,
      services: 'Dienste',
      hide: `${APP_NAME} ausblenden`,
      hideOthers: 'Andere ausblenden',
      unhide: 'Alle einblenden',
      quit: `${APP_NAME} beenden`,
    },
  );
  roleItems.forEach((item) => assert.equal(item.accessibilityLabel, item.label));
});

test('about panel options expose app version and build metadata', () => {
  const options = createAboutPanelOptions({
    appName: APP_NAME,
    appVersion: '0.9.24',
    buildVersion: '1234',
  });

  assert.deepEqual(options, {
    applicationName: APP_NAME,
    applicationVersion: '0.9.24',
    version: '1234',
  });
});

test('desktop build version prefers explicit build metadata', () => {
  assert.equal(desktopBuildVersion({ OMLORIX_DESKTOP_BUILD: '5678' }, '0.9.24'), '5678');
  assert.equal(desktopBuildVersion({}, '0.9.24'), '0.9.24');
});
