const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const vm = require('node:vm');

const rendererRoot = path.join(__dirname, '..', 'renderer');

/** Run the production Redis radio handler against a small deterministic DOM. */
function invokeRedisModeHandler(source, selectedMode) {
  const start = source.indexOf('  els.redisModeInputs.forEach((input) => {');
  const end = source.indexOf('\n\n  els.storageModeInputs.forEach((input) => {', start);
  assert(start >= 0 && end > start, 'the Redis mode handler must exist');
  const registrationSource = source.slice(start, end);
  const changedSettings = [];
  const bundledToggle = { checked: false, dataset: { toggle: 'useBundledRedis' } };
  const redisModeInputs = ['off', 'bundled', 'external'].map((mode) => ({
    checked: mode === selectedMode,
    dataset: { redisMode: mode },
    addEventListener(eventName, handler) {
      assert.equal(eventName, 'change');
      this.changeHandler = handler;
    },
  }));
  const context = {
    els: {
      redisEnabledInput: { checked: true },
      redisModeInputs,
      toggleInputs: [bundledToggle],
    },
    markSettingsChanged(...keys) {
      changedSettings.push(...keys);
    },
    renderEnvEditor() {},
    saveSettingsNow() {},
    syncConnectionModeControls() {},
  };

  vm.runInNewContext(registrationSource, context);
  redisModeInputs.find((input) => input.checked).changeHandler();

  return {
    redisEnabled: context.els.redisEnabledInput.checked,
    useBundledRedis: bundledToggle.checked,
    changedSettings,
  };
}

test('Redis settings expose one mutually exclusive Off, bundled, or external selector', async () => {
  const [html, source] = await Promise.all([
    fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8'),
  ]);

  assert.match(html, /name="redisConnectionMode" value="off" data-redis-mode="off"/);
  assert.match(html, /name="redisConnectionMode" value="bundled" data-redis-mode="bundled"/);
  assert.match(html, /name="redisConnectionMode" value="external" data-redis-mode="external"/);
  const controls = html.match(/<input\b[^>]*\bname="redisConnectionMode"[^>]*>/g) || [];
  assert.equal(controls.length, 3);
  controls.forEach((control) => assert.match(control, /\btype="radio"/));
  assert.doesNotMatch(html, /Enable Redis-backed features/);

  assert.deepEqual(invokeRedisModeHandler(source, 'off'), {
    redisEnabled: false,
    useBundledRedis: false,
    changedSettings: ['redisEnabled', 'useBundledRedis'],
  });
  assert.deepEqual(invokeRedisModeHandler(source, 'bundled'), {
    redisEnabled: true,
    useBundledRedis: true,
    changedSettings: ['redisEnabled', 'useBundledRedis'],
  });
  assert.deepEqual(invokeRedisModeHandler(source, 'external'), {
    redisEnabled: true,
    useBundledRedis: false,
    changedSettings: ['redisEnabled', 'useBundledRedis'],
  });
});
