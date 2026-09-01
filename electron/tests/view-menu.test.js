const test = require('node:test');
const assert = require('node:assert/strict');

const { createLauncherTranslator } = require('../launcher-native-i18n');
const { createViewMenuTemplate } = require('../view-menu');

test('view menu explicitly localizes German role labels and accessibility text', () => {
  const viewMenu = createViewMenuTemplate(createLauncherTranslator('de-DE'));
  const roleItems = viewMenu.submenu.filter((item) => item.role);

  assert.equal(viewMenu.label, 'Ansicht');
  assert.deepEqual(
    Object.fromEntries(roleItems.map((item) => [item.role, item.label])),
    {
      reload: 'Neu laden',
      forceReload: 'Erneut laden erzwingen',
      toggleDevTools: 'Entwicklertools ein-/ausblenden',
      resetZoom: 'Tatsächliche Größe',
      zoomIn: 'Vergrößern',
      zoomOut: 'Verkleinern',
      togglefullscreen: 'Vollbildmodus ein-/ausschalten',
    },
  );
  roleItems.forEach((item) => assert.equal(item.accessibilityLabel, item.label));
});

test('view menu uses explicit non-Latin role labels', () => {
  const viewMenu = createViewMenuTemplate(createLauncherTranslator('ja-JP'));
  const roleItems = Object.fromEntries(
    viewMenu.submenu.filter((item) => item.role).map((item) => [item.role, item]),
  );

  assert.equal(viewMenu.label, 'ビュー');
  assert.equal(roleItems.reload.label, '再読み込み');
  assert.equal(roleItems.togglefullscreen.label, 'フルスクリーンを切り替える');
  assert.equal(roleItems.reload.accessibilityLabel, roleItems.reload.label);
});
