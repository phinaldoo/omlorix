const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const vm = require('node:vm');

const launcherPath = path.join(__dirname, '..', 'renderer', 'launcher.js');

/**
 * Extract one named function declaration, including nested blocks, from the
 * renderer source. Keeping the production functions intact makes this focused
 * race test fail when their real coordination logic regresses.
 */
function extractFunction(source, name) {
  const asyncSignature = `async function ${name}(`;
  const regularSignature = `function ${name}(`;
  const asyncStart = source.indexOf(asyncSignature);
  const signature = asyncStart >= 0 ? asyncSignature : regularSignature;
  const start = asyncStart >= 0 ? asyncStart : source.indexOf(regularSignature);
  assert(start >= 0, `${name} must exist in launcher.js`);

  // All launcher helpers are declarations at the IIFE's two-space indentation.
  // Using the next declaration/listener boundary avoids parsing template-string
  // interpolation here and keeps this test helper intentionally small.
  const boundaryPatterns = [
    '\n  function ',
    '\n  async function ',
    '\n  els.',
  ];
  const boundaries = boundaryPatterns
    .map((pattern) => source.indexOf(pattern, start + signature.length))
    .filter((index) => index >= 0);
  assert(boundaries.length > 0, `Could not find the end of ${name}`);
  return source.slice(start, Math.min(...boundaries)).trim();
}

/**
 * Build a small deterministic environment around the production proxy-save
 * functions. Timers and IPC promises are controlled by the test so the exact
 * ordering from the security report can be reproduced without Electron.
 */
async function createProxySaveHarness() {
  const source = await fs.readFile(launcherPath, 'utf8');
  const productionFunctions = [
    'queueProxyAutosave',
    'markProxyFormChanged',
    'proxyFormChangedSince',
    'saveProxySettings',
    'handleProxyFieldChange',
  ].map((name) => extractFunction(source, name)).join('\n\n');

  const context = vm.createContext({
    assert,
    console,
  });

  vm.runInContext(`
    class HTMLElement {}
    class HTMLInputElement extends HTMLElement {
      constructor(type = 'text', value = '') {
        super();
        this.type = type;
        this.value = value;
        this.checked = false;
      }
    }

    const pendingTimers = new Map();
    let nextTimerId = 0;
    const saveCalls = [];
    let resolveFirstSave;

    const state = {
      current: { proxy: { config: { enabled: true } } },
      busy: false,
      proxyFormDirty: false,
      proxyEditVersion: 0,
      proxyAutosaveTimer: null,
      proxySaving: false,
      proxySaveRequested: false,
      proxySavePromise: null,
    };

    const bindInput = new HTMLInputElement('text', '0.0.0.0');
    const els = {
      proxyForm: { contains: (target) => target === bindInput },
      proxyBindInput: bindInput,
      proxyEnabledInput: new HTMLInputElement('checkbox'),
      proxyHttpsInput: new HTMLInputElement('checkbox'),
      proxyTlsPassphraseInput: new HTMLInputElement('password'),
      proxyClearPassphraseInput: new HTMLInputElement('checkbox'),
    };

    const window = {
      setTimeout(callback) {
        nextTimerId += 1;
        pendingTimers.set(nextTimerId, callback);
        return nextTimerId;
      },
      clearTimeout(timerId) {
        pendingTimers.delete(timerId);
      },
      omlorixServer: {
        saveProxySettings(payload) {
          saveCalls.push({ ...payload });
          if (saveCalls.length === 1) {
            return new Promise((resolve) => {
              resolveFirstSave = resolve;
            });
          }
          return Promise.resolve({
            proxy: { config: { bindHost: payload.bindHost } },
          });
        },
      },
    };

    function collectProxySettings() {
      return { bindHost: els.proxyBindInput.value };
    }
    function renderState(result) {
      if (!state.proxyFormDirty) {
        els.proxyBindInput.value = result.proxy.config.bindHost;
      }
    }
    function renderProxyValidation() {}
    function setProxyValidation() {}
    function appendConsole() {}
    function setBusy() {}
    function updateProxyVisibility() {}

    const PROXY_AUTOSAVE_DELAY_MS = 450;
    ${productionFunctions}

    globalThis.harness = {
      state,
      els,
      saveCalls,
      pendingTimers,
      resolveFirstSave: (result) => resolveFirstSave(result),
      saveProxySettings,
      handleProxyFieldChange,
      runLatestTimer() {
        const entries = Array.from(pendingTimers.entries());
        assert.ok(entries.length > 0, 'a follow-up autosave must be pending');
        const [timerId, callback] = entries.at(-1);
        pendingTimers.delete(timerId);
        callback();
      },
    };
  `, context);

  return context.harness;
}

test('proxy autosave preserves and persists an edit made during an active save', async () => {
  const harness = await createProxySaveHarness();

  // Start an IPC save with the old, externally reachable bind address.
  const firstSave = harness.saveProxySettings({ silent: true });
  assert.equal(harness.saveCalls[0].bindHost, '0.0.0.0');

  // Reproduce the operator entering the safer address before that IPC returns.
  harness.els.proxyBindInput.value = '127.0.0.1';
  harness.handleProxyFieldChange({
    target: harness.els.proxyBindInput,
    type: 'input',
  });

  // The stale response must not rehydrate 0.0.0.0 over the newer form value.
  harness.resolveFirstSave({
    proxy: { config: { bindHost: '0.0.0.0' } },
  });
  await firstSave;
  assert.equal(harness.els.proxyBindInput.value, '127.0.0.1');
  assert.equal(harness.state.proxyFormDirty, true);

  // The automatically queued follow-up must persist the newer safe address.
  harness.runLatestTimer();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.saveCalls.length, 2);
  assert.equal(harness.saveCalls[1].bindHost, '127.0.0.1');
  assert.equal(harness.state.proxyFormDirty, false);
});
