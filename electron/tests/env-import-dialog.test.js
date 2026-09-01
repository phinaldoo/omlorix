const test = require('node:test');
const assert = require('node:assert/strict');

const {
  createEnvExportDialogOptions,
  createEnvImportDialogOptions,
  createSecretsImportDialogOptions,
} = require('../env-import-dialog');

test('env export picker preserves the conventional .env filename', () => {
  const options = createEnvExportDialogOptions();

  assert.equal(options.title, 'Export .env');
  assert.equal(options.defaultPath, '.env');
  assert.equal(options.buttonLabel, 'Export');
  assert.deepEqual(options.properties, [
    'showOverwriteConfirmation',
    'createDirectory',
    'showHiddenFiles',
  ]);
  assert.deepEqual(options.filters, [
    { name: 'All files', extensions: ['*'] },
  ]);
  assert.notEqual(options.defaultPath, '.env.env');
});

test('env import picker shows hidden files and does not block extensionless .env files', () => {
  const options = createEnvImportDialogOptions();

  assert.equal(options.title, 'Import .env file');
  assert.deepEqual(options.properties, ['openFile', 'showHiddenFiles']);
  assert.deepEqual(options.filters, [
    { name: 'All files', extensions: ['*'] },
  ]);
});

test('secrets restore picker names the complete .env replacement explicitly', () => {
  const options = createSecretsImportDialogOptions();

  assert.equal(options.title, 'Restore complete .env file');
  assert.equal(options.buttonLabel, 'Restore complete .env');
  assert.deepEqual(options.properties, ['openFile', 'showHiddenFiles']);
  assert.deepEqual(options.filters, [
    { name: 'All files', extensions: ['*'] },
  ]);
});
