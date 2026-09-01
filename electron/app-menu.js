const APP_NAME = 'Omlorix Server Launcher';
const { createLauncherTranslator } = require('./launcher-native-i18n');
const { createLocalizedRoleMenuItem } = require('./native-menu');

function cleanText(value) {
  return String(value || '').trim();
}

function desktopBuildVersion(env = process.env, appVersion = '') {
  return cleanText(
    env.OMLORIX_DESKTOP_BUILD
      || env.BUILD_NUMBER
      || env.GITHUB_RUN_NUMBER
      || appVersion,
  );
}

function createAboutPanelOptions({
  appName = APP_NAME,
  appVersion = '',
  buildVersion = '',
} = {}) {
  const normalizedAppVersion = cleanText(appVersion);
  const normalizedBuildVersion = cleanText(buildVersion) || normalizedAppVersion;

  return {
    applicationName: cleanText(appName) || APP_NAME,
    applicationVersion: normalizedAppVersion,
    version: normalizedBuildVersion,
  };
}

function createMacApplicationMenuTemplate(
  appName = APP_NAME,
  extraItems = [],
  translate = createLauncherTranslator('en'),
) {
  const normalizedAppName = cleanText(appName) || APP_NAME;
  const normalizedExtraItems = Array.isArray(extraItems)
    ? extraItems.filter(Boolean)
    : [];
  const roleItem = (role, key) => createLocalizedRoleMenuItem(
    role,
    key,
    translate,
    { appName: normalizedAppName },
  );

  return {
    label: normalizedAppName,
    submenu: [
      roleItem('about', 'menu_about_app'),
      ...normalizedExtraItems,
      { type: 'separator' },
      roleItem('services', 'menu_services'),
      { type: 'separator' },
      roleItem('hide', 'menu_hide_app'),
      roleItem('hideOthers', 'menu_hide_others'),
      roleItem('unhide', 'menu_show_all'),
      { type: 'separator' },
      roleItem('quit', 'menu_quit_app'),
    ],
  };
}

module.exports = {
  APP_NAME,
  createAboutPanelOptions,
  createMacApplicationMenuTemplate,
  desktopBuildVersion,
};
