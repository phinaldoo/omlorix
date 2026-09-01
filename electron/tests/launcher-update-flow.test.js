const test = require('node:test');
const assert = require('node:assert/strict');

const { runLauncherUpdateFlow } = require('../launcher-update-flow');

function createDialog(responses = []) {
  const calls = [];
  return {
    calls,
    showMessageBox: async (options) => {
      calls.push(options);
      return responses.length ? responses.shift() : { response: 0 };
    },
  };
}

function createUpdater({
  checkResult = {
    channel: 'stable',
    currentVersion: '1.0.0',
    latestVersion: '1.1.0',
    updateAvailable: true,
    status: 'available',
  },
  installResult = { ok: true },
} = {}) {
  const calls = [];
  return {
    calls,
    updater: {
      check: async () => {
        calls.push('check');
        return checkResult;
      },
      download: async () => {
        calls.push('download');
        return {
          channel: checkResult.channel,
          currentVersion: checkResult.currentVersion,
          latestVersion: checkResult.latestVersion,
          downloaded: true,
        };
      },
      install: () => {
        calls.push('install');
        return installResult;
      },
    },
  };
}

function createProgressWindow(actions = []) {
  const states = [];
  return {
    states,
    createProgressWindow: () => ({
      setState: (state) => states.push(state),
      waitForAction: () => {
        const next = actions.shift();
        return next instanceof Promise ? next : Promise.resolve(next);
      },
      close: () => states.push({ phase: 'closed' }),
    }),
  };
}

test('launcher update flow reports current version without downloading', async () => {
  const { updater, calls } = createUpdater({
    checkResult: {
      channel: 'stable',
      currentVersion: '1.1.0',
      latestVersion: '1.1.0',
      updateAvailable: false,
      status: 'current',
    },
  });
  const dialog = createDialog();

  const result = await runLauncherUpdateFlow({
    launcherAutoUpdater: updater,
    showMessageBox: dialog.showMessageBox,
  });

  assert.deepEqual(result, { ok: true, status: 'current' });
  assert.deepEqual(calls, ['check']);
  assert.equal(dialog.calls[0].message, 'Omlorix Server Launcher is up to date.');
});

test('launcher update flow presents and closes native checking progress', async () => {
  const { updater, calls } = createUpdater({
    checkResult: {
      channel: 'stable',
      currentVersion: '1.1.0',
      latestVersion: '1.1.0',
      updateAvailable: false,
      status: 'current',
    },
  });
  const dialog = createDialog();
  const never = new Promise(() => {});
  const progress = createProgressWindow([never]);

  const result = await runLauncherUpdateFlow({
    launcherAutoUpdater: updater,
    showMessageBox: dialog.showMessageBox,
    createProgressWindow: progress.createProgressWindow,
    showCheckingProgress: true,
  });

  assert.deepEqual(result, { ok: true, status: 'current' });
  assert.deepEqual(calls, ['check']);
  assert.equal(progress.states[0].phase, 'checking');
  assert.equal(progress.states[0].windowTitle, 'Software Update');
  assert.deepEqual(progress.states[1], { phase: 'closed' });
});

test('launcher update flow dismisses a native checking operation on cancel', async () => {
  const calls = [];
  const dialog = createDialog();
  const progress = createProgressWindow(['cancel']);
  const updater = {
    check: () => {
      calls.push('check');
      return new Promise(() => {});
    },
  };

  const result = await runLauncherUpdateFlow({
    launcherAutoUpdater: updater,
    showMessageBox: dialog.showMessageBox,
    createProgressWindow: progress.createProgressWindow,
    showCheckingProgress: true,
  });

  assert.deepEqual(result, { ok: true, status: 'cancelled' });
  assert.deepEqual(calls, ['check']);
  assert.equal(dialog.calls.length, 0);
  assert.deepEqual(progress.states.at(-1), { phase: 'closed' });
});

test('launcher update flow defers when the user chooses later', async () => {
  const { updater, calls } = createUpdater();
  const dialog = createDialog([{ response: 1 }]);

  const result = await runLauncherUpdateFlow({
    launcherAutoUpdater: updater,
    showMessageBox: dialog.showMessageBox,
  });

  assert.deepEqual(result, { ok: true, status: 'deferred' });
  assert.deepEqual(calls, ['check']);
  assert.deepEqual(dialog.calls[0].buttons, ['Install Update', 'Later']);
});

test('launcher update flow stops before download when install location is not ready', async () => {
  const { updater, calls } = createUpdater();
  const dialog = createDialog();

  const result = await runLauncherUpdateFlow({
    launcherAutoUpdater: updater,
    showMessageBox: dialog.showMessageBox,
    ensureInstallReady: async () => ({
      ok: false,
      status: 'moving-to-applications',
    }),
  });

  assert.deepEqual(result, { ok: true, status: 'moving-to-applications' });
  assert.deepEqual(calls, ['check']);
  assert.equal(dialog.calls.length, 0);
});

test('launcher update flow downloads and installs after one user approval', async () => {
  const { updater, calls } = createUpdater();
  const dialog = createDialog([{ response: 0 }]);
  const progress = [];

  const result = await runLauncherUpdateFlow({
    launcherAutoUpdater: updater,
    showMessageBox: dialog.showMessageBox,
    setProgressBar: (value) => progress.push(value),
  });

  assert.deepEqual(result, { ok: true, status: 'installing' });
  assert.deepEqual(calls, ['check', 'download', 'install']);
  assert.deepEqual(progress, [0.01, -1]);
  assert.equal(dialog.calls.length, 1);
  assert.match(dialog.calls[0].detail, /download the update, restart, and finish installing/i);
});

test('launcher update flow waits for progress window install action before relaunching', async () => {
  const { updater, calls } = createUpdater();
  const dialog = createDialog([{ response: 0 }]);
  const never = new Promise(() => {});
  const progress = createProgressWindow([never, 'primary']);

  const result = await runLauncherUpdateFlow({
    launcherAutoUpdater: updater,
    showMessageBox: dialog.showMessageBox,
    createProgressWindow: progress.createProgressWindow,
  });

  assert.deepEqual(result, { ok: true, status: 'installing' });
  assert.deepEqual(calls, ['check', 'download', 'install']);
  assert.equal(progress.states[0].phase, 'downloading');
  assert.equal(progress.states[1].phase, 'ready');
  assert.equal(progress.states[1].primaryLabel, 'Install and Relaunch');
});

test('launcher update flow lets progress window defer install after download', async () => {
  const { updater, calls } = createUpdater();
  const dialog = createDialog([{ response: 0 }]);
  const never = new Promise(() => {});
  const progress = createProgressWindow([never, 'secondary']);

  const result = await runLauncherUpdateFlow({
    launcherAutoUpdater: updater,
    showMessageBox: dialog.showMessageBox,
    createProgressWindow: progress.createProgressWindow,
  });

  assert.deepEqual(result, { ok: true, status: 'downloaded' });
  assert.deepEqual(calls, ['check', 'download']);
  assert.equal(progress.states[1].primaryLabel, 'Install and Relaunch');
});

test('launcher update flow ignores non-cancel progress actions while downloading', async () => {
  const { updater, calls } = createUpdater();
  const dialog = createDialog([{ response: 0 }]);
  const progress = createProgressWindow(['primary', 'secondary']);

  const result = await runLauncherUpdateFlow({
    launcherAutoUpdater: updater,
    showMessageBox: dialog.showMessageBox,
    createProgressWindow: progress.createProgressWindow,
  });

  assert.deepEqual(result, { ok: true, status: 'downloaded' });
  assert.deepEqual(calls, ['check', 'download']);
  assert.equal(progress.states[1].primaryLabel, 'Install and Relaunch');
  assert.match(progress.states[1].detail, /1\.1\.0 has been downloaded/);
});
