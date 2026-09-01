'use strict';

const { createLauncherTranslator } = require('./launcher-native-i18n');
const { createLocalizedRoleMenuItem } = require('./native-menu');

function createViewMenuTemplate(translate = createLauncherTranslator('en')) {
  const roleItem = (role, key) => createLocalizedRoleMenuItem(role, key, translate);

  return {
    label: translate('view'),
    submenu: [
      roleItem('reload', 'menu_reload'),
      roleItem('forceReload', 'menu_force_reload'),
      roleItem('toggleDevTools', 'menu_toggle_developer_tools'),
      { type: 'separator' },
      roleItem('resetZoom', 'menu_actual_size'),
      roleItem('zoomIn', 'menu_zoom_in'),
      roleItem('zoomOut', 'menu_zoom_out'),
      { type: 'separator' },
      roleItem('togglefullscreen', 'menu_toggle_full_screen'),
    ],
  };
}

module.exports = {
  createViewMenuTemplate,
};
