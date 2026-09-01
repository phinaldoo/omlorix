const test = require('node:test');
const assert = require('node:assert/strict');

const { createEditMenuTemplate } = require('../edit-menu');
const { createLauncherTranslator } = require('../launcher-native-i18n');

test('edit menu exposes native clipboard roles for text fields', () => {
  const editMenu = createEditMenuTemplate();
  const roles = editMenu.submenu.map((item) => item.role).filter(Boolean);

  assert.equal(editMenu.label, 'Edit');
  assert.ok(roles.includes('cut'));
  assert.ok(roles.includes('copy'));
  assert.ok(roles.includes('paste'));
  assert.ok(roles.includes('selectAll'));
});

test('edit menu explicitly localizes German role labels and accessibility text', () => {
  const editMenu = createEditMenuTemplate(createLauncherTranslator('de-DE'));
  const roleItems = editMenu.submenu.filter((item) => item.role);

  assert.deepEqual(
    Object.fromEntries(roleItems.map((item) => [item.role, item.label])),
    {
      undo: 'Rückgängig',
      redo: 'Wiederholen',
      cut: 'Ausschneiden',
      copy: 'Kopieren',
      paste: 'Einfügen',
      pasteAndMatchStyle: 'Einfügen und Stil anpassen',
      delete: 'Löschen',
      selectAll: 'Alles auswählen',
    },
  );
  roleItems.forEach((item) => assert.equal(item.accessibilityLabel, item.label));
});
