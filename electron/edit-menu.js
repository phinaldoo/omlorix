const { createLauncherTranslator } = require('./launcher-native-i18n');
const { createLocalizedRoleMenuItem } = require('./native-menu');

function createEditMenuTemplate(translate = createLauncherTranslator('en')) {
  const roleItem = (role, key) => createLocalizedRoleMenuItem(role, key, translate);

  return {
    label: translate('edit'),
    submenu: [
      roleItem('undo', 'menu_undo'),
      roleItem('redo', 'menu_redo'),
      { type: 'separator' },
      roleItem('cut', 'menu_cut'),
      roleItem('copy', 'menu_copy'),
      roleItem('paste', 'menu_paste'),
      roleItem('pasteAndMatchStyle', 'menu_paste_and_match_style'),
      roleItem('delete', 'menu_delete'),
      { type: 'separator' },
      roleItem('selectAll', 'menu_select_all'),
    ],
  };
}

module.exports = {
  createEditMenuTemplate,
};
