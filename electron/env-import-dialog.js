/**
 * Build the native file picker options used by the launcher .env import flow.
 *
 * Dotfiles such as ".env" are not treated as normal extension-bearing files by
 * every native picker. A restrictive "env" extension filter can therefore make
 * the exact file users need appear disabled even when hidden files are shown.
 * The server-manager import preview performs the real validation after a file is
 * chosen, so the picker should stay permissive and only ensure hidden files are
 * visible.
 */
const { createLauncherTranslator } = require('./launcher-native-i18n');

function createEnvImportDialogOptions(translate = createLauncherTranslator('en')) {
  return {
    title: translate('import_env_file'),
    properties: ['openFile', 'showHiddenFiles'],
    filters: [
      { name: translate('all_files'), extensions: ['*'] },
    ],
  };
}

/**
 * Build the Secrets-page restore picker without reintroducing an extension
 * filter. A real ".env" file is a hidden, extensionless dotfile on macOS, so
 * this flow must use the same permissive picker as the full environment editor.
 */
function createSecretsImportDialogOptions(translate = createLauncherTranslator('en')) {
  return {
    ...createEnvImportDialogOptions(translate),
    title: translate('restore_complete_env_file'),
    buttonLabel: translate('restore_complete_env'),
  };
}

/**
 * Build the full environment export picker without an extension filter.
 *
 * Native macOS save panels treat the dotfile ".env" as extensionless. An
 * active "env" filter therefore changes the suggested filename to ".env.env".
 * The export writes to the exact path selected by the operator, so an all-files
 * filter preserves the conventional dotfile name on every platform.
 */
function createEnvExportDialogOptions(translate = createLauncherTranslator('en')) {
  return {
    title: translate('export_env'),
    defaultPath: '.env',
    buttonLabel: translate('export'),
    filters: [
      { name: translate('all_files'), extensions: ['*'] },
    ],
    properties: ['showOverwriteConfirmation', 'createDirectory', 'showHiddenFiles'],
  };
}

module.exports = {
  createEnvExportDialogOptions,
  createEnvImportDialogOptions,
  createSecretsImportDialogOptions,
};
