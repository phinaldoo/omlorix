const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const electronRoot = path.join(__dirname, '..');
const launcherInitSource = fs.readFileSync(
  path.join(electronRoot, 'renderer', 'launcher-init.js'),
  'utf8',
);

/** Execute the early theme initializer with a small browser/preload harness. */
function initializeTheme({ storedMode = 'system', systemDark = false } = {}) {
  const nativeModes = [];
  const attributes = new Map();
  const media = {
    matches: systemDark,
    addEventListener() {},
  };
  const document = {
    documentElement: {
      setAttribute(name, value) {
        attributes.set(name, value);
      },
    },
    addEventListener() {},
    querySelectorAll() {
      return [];
    },
  };
  const window = {
    matchMedia: () => media,
    addEventListener() {},
    omlorixServer: {
      setWindowBackground(mode) {
        nativeModes.push(mode);
        return Promise.resolve();
      },
    },
  };

  vm.runInNewContext(launcherInitSource, {
    document,
    localStorage: {
      getItem(key) {
        return key === 'mode' ? storedMode : null;
      },
    },
    window,
  });

  return {
    nativeModes,
    rendererMode: attributes.get('data-mode'),
  };
}

test('early launcher theme keeps the native resize surface in sync', () => {
  assert.deepEqual(initializeTheme({ storedMode: 'dark' }), {
    nativeModes: ['dark'],
    rendererMode: 'dark',
  });
  assert.deepEqual(initializeTheme({ storedMode: 'light', systemDark: true }), {
    nativeModes: ['light'],
    rendererMode: 'light',
  });
  assert.deepEqual(initializeTheme({ storedMode: 'system', systemDark: true }), {
    nativeModes: ['dark'],
    rendererMode: 'dark',
  });
});

test('main window and trusted preload use the launcher canvas colors', () => {
  const mainSource = fs.readFileSync(path.join(electronRoot, 'main.js'), 'utf8');
  const preloadSource = fs.readFileSync(path.join(electronRoot, 'preload.js'), 'utf8');
  const rendererSource = fs.readFileSync(
    path.join(electronRoot, 'renderer', 'launcher.js'),
    'utf8',
  );

  assert.match(mainSource, /light: '#f4f4f2'/);
  assert.match(mainSource, /dark: '#09090b'/);
  assert.match(mainSource, /backgroundColor: launcherBackgroundColor\(nativeTheme\.shouldUseDarkColors/);
  assert.match(mainSource, /handleTrustedIpc\('launcher:set-background-color'/);
  assert.match(mainSource, /mainWindow\.setBackgroundColor\(color\)/);
  assert.match(
    preloadSource,
    /setWindowBackground: \(mode\) => ipcRenderer\.invoke\('launcher:set-background-color', mode\)/,
  );
  assert.match(rendererSource, /setWindowBackground\?\.\(nextMode\)/);
});
