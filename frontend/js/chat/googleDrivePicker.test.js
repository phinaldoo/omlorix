const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'googleDrivePicker.js'), 'utf8');

test('native Google Picker keeps personal Drive first and shared drives separate', async () => {
  let pickerCallback = null;
  let pickerVisible = false;
  let pickerDialogVisible = true;
  let nextIntervalId = 0;
  const pickerIntervals = new Map();
  const builderValues = { views: [] };
  const driveViewValues = [];

  class DocsView {
    constructor(viewId) {
      this.values = { viewId, sharedDrives: false };
      driveViewValues.push(this.values);
    }
    setIncludeFolders(value) { this.values.includeFolders = value; return this; }
    setSelectFolderEnabled(value) { this.values.selectFolder = value; return this; }
    setEnableDrives(value) { this.values.sharedDrives = value; return this; }
  }

  class PickerBuilder {
    addView(value) { builderValues.views.push(value); return this; }
    enableFeature(value) { builderValues.feature = value; return this; }
    setMaxItems(value) { builderValues.maxItems = value; return this; }
    setOAuthToken(value) { builderValues.accessToken = value; return this; }
    setDeveloperKey(value) { builderValues.developerKey = value; return this; }
    setAppId(value) { builderValues.appId = value; return this; }
    setOrigin(value) { builderValues.origin = value; return this; }
    setLocale(value) { builderValues.locale = value; return this; }
    setTitle(value) { builderValues.title = value; return this; }
    setCallback(value) { pickerCallback = value; return this; }
    build() {
      return {
        setVisible(value) { pickerVisible = value; },
        dispose() { pickerVisible = false; },
      };
    }
  }

  const pickerApi = {
    DocsView,
    PickerBuilder,
    ViewId: { DOCS: 'docs' },
    Feature: { MULTISELECT_ENABLED: 'multi' },
    Response: { ACTION: 'action', DOCUMENTS: 'docs' },
    Action: { PICKED: 'picked', CANCEL: 'cancel' },
    Document: { ID: 'id' },
  };
  const document = {
    documentElement: { lang: 'de' },
    activeElement: { focus() {} },
    getElementById() { return null; },
    querySelector(selector) {
      return selector === '.picker-dialog' && pickerDialogVisible ? {} : null;
    },
    createElement() { throw new Error('Google API script should already be initialized'); },
    head: { appendChild() {} },
  };
  const window = {
    google: { picker: pickerApi },
    location: { origin: 'https://chat.example' },
    setInterval(callback) {
      const intervalId = ++nextIntervalId;
      pickerIntervals.set(intervalId, callback);
      return intervalId;
    },
    clearInterval(intervalId) { pickerIntervals.delete(intervalId); },
    getTranslation(_key, fallback) { return fallback; },
    authedFetch: async () => ({
      ok: true,
      json: async () => ({
        picker_ready: true,
        connected: true,
        developer_key: 'restricted-browser-key',
        app_id: '123456789',
        access_token: 'ephemeral-token',
      }),
    }),
  };
  const sandbox = {
    window,
    document,
    navigator: { language: 'de-DE' },
    console,
    Promise,
    Set,
    Error,
  };

  vm.runInNewContext(source, sandbox, { filename: 'googleDrivePicker.js' });
  const selectionPromise = window.GoogleDrivePicker.open();
  for (let index = 0; index < 5 && !pickerCallback; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }

  assert.equal(typeof pickerCallback, 'function');
  assert.equal(pickerVisible, true);
  pickerCallback({
    action: 'picked',
    docs: [{ id: 'file-1' }, { id: 'file-2' }, { id: 'file-1' }],
  });
  const selection = await selectionPromise;

  assert.deepEqual(Array.from(selection.fileIds), ['file-1', 'file-2']);
  assert.equal(driveViewValues.length, 2);
  assert.deepEqual(driveViewValues[0], {
    viewId: 'docs',
    sharedDrives: false,
    includeFolders: true,
    selectFolder: false,
  });
  assert.deepEqual(driveViewValues[1], {
    viewId: 'docs',
    sharedDrives: true,
    includeFolders: true,
    selectFolder: false,
  });
  assert.equal(builderValues.views.length, 2);
  assert.equal(builderValues.feature, 'multi');
  assert.equal(builderValues.maxItems, 20);
  assert.equal(builderValues.accessToken, 'ephemeral-token');
  assert.equal(builderValues.developerKey, 'restricted-browser-key');
  assert.equal(builderValues.appId, '123456789');
  assert.equal(builderValues.origin, 'https://chat.example');
  assert.equal(pickerVisible, false);
  assert.equal(pickerIntervals.size, 0, 'a picked selection should stop the dismissal watcher');

  // The native dialog can disappear without Google invoking the callback.
  // Confirm the watcher treats that path as a normal cancellation instead of
  // leaving the selection promise pending.
  const dismissedPromise = window.GoogleDrivePicker.open();
  for (let index = 0; index < 5 && pickerIntervals.size === 0; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.equal(pickerIntervals.size, 1, 'the watcher starts after the picker becomes visible');
  const dismissalWatcher = Array.from(pickerIntervals.values())[0];
  dismissalWatcher();
  pickerDialogVisible = false;
  dismissalWatcher();
  const dismissedSelection = await dismissedPromise;
  assert.equal(dismissedSelection.cancelled, true);
  assert.equal(pickerVisible, false);
  assert.equal(pickerIntervals.size, 0, 'the watcher stops after detecting dismissal');
});
