const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const vm = require('node:vm');

const electronRoot = path.join(__dirname, '..');
const progressArgument = '--launcher-update-progress-channel';

/**
 * Execute the sandboxed preload with small Electron API fakes.
 *
 * This exercises the real preload source without launching a GUI, while still
 * proving that the renderer argument creates matching state and action IPC
 * channel names.
 */
async function runPreload(argv) {
  const exposed = {};
  const listeners = new Map();
  const sent = [];
  const source = await fs.readFile(
    path.join(electronRoot, 'update-progress-preload.js'),
    'utf8',
  );
  const electron = {
    contextBridge: {
      exposeInMainWorld(name, value) {
        exposed[name] = value;
      },
    },
    ipcRenderer: {
      on(channel, listener) {
        listeners.set(channel, listener);
      },
      removeListener(channel, listener) {
        if (listeners.get(channel) === listener) listeners.delete(channel);
      },
      send(channel, payload) {
        sent.push({ channel, payload });
      },
    },
  };

  vm.runInNewContext(source, {
    process: { argv },
    require(moduleName) {
      if (moduleName === 'electron') return electron;
      throw new Error(`Unexpected preload dependency: ${moduleName}`);
    },
  });

  return { exposed, listeners, sent };
}

test('Windows-normalized progress argument connects state and action IPC', async () => {
  const channel = 'launcher-update-progress-test';
  const harness = await runPreload([
    'Omlorix Server Launcher.exe',
    `${progressArgument}=${channel}`,
  ]);
  const progressBridge = harness.exposed.launcherUpdateProgress;
  let receivedState = null;

  const removeStateListener = progressBridge.onState((state) => {
    receivedState = state;
  });
  harness.listeners.get(`${channel}:state`)({}, { percent: 42 });
  progressBridge.sendAction('cancel');

  assert.deepEqual(receivedState, { percent: 42 });
  assert.deepEqual(harness.sent, [
    { channel: `${channel}:action`, payload: 'cancel' },
  ]);

  removeStateListener();
  assert.equal(harness.listeners.has(`${channel}:state`), false);
});

test('progress window passes the same lowercase argument expected by its preload', async () => {
  const mainSource = await fs.readFile(path.join(electronRoot, 'main.js'), 'utf8');
  const preloadSource = await fs.readFile(
    path.join(electronRoot, 'update-progress-preload.js'),
    'utf8',
  );

  assert.match(mainSource, /additionalArguments: \[`--launcher-update-progress-channel=\$\{channel\}`\]/);
  assert.match(preloadSource, /startsWith\('--launcher-update-progress-channel='\)/);
});
