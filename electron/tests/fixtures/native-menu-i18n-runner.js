'use strict';

const { app, Menu } = require('electron');

const {
  APP_NAME,
  createMacApplicationMenuTemplate,
} = require('../../app-menu');
const { createEditMenuTemplate } = require('../../edit-menu');
const { createLauncherTranslator } = require('../../launcher-native-i18n');
const { createViewMenuTemplate } = require('../../view-menu');

function roleItems(menu) {
  const items = [];

  for (const topLevelItem of menu.items) {
    for (const item of topLevelItem.submenu?.items || []) {
      if (!item.role) continue;
      items.push({
        role: item.role,
        label: item.label,
        accessibilityLabel: item.accessibilityLabel,
      });
    }
  }

  return items;
}

app.setName(APP_NAME);
app.whenReady().then(() => {
  const locale = app.getLocale();
  const translate = createLauncherTranslator(locale);
  const menu = Menu.buildFromTemplate([
    createMacApplicationMenuTemplate(APP_NAME, [], translate),
    createEditMenuTemplate(translate),
    createViewMenuTemplate(translate),
  ]);
  const result = JSON.stringify({ locale, roleItems: roleItems(menu) });

  process.stdout.write(result, () => app.quit());
}).catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  app.exit(1);
});
