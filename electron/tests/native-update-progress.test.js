const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const path = require('node:path');

const {
  createNativeUpdateProgressController,
  macApplicationBundlePath,
  nativeUpdateHelperPath,
  nativeUpdateIconPath,
} = require('../native-update-progress');

class FakeStream extends EventEmitter {
  constructor() {
    super();
    this.destroyed = false;
    this.writes = [];
  }

  write(value) {
    this.writes.push(String(value));
    return true;
  }

  end() {
    this.destroyed = true;
  }
}

function createFakeChild() {
  const child = new EventEmitter();
  child.stdin = new FakeStream();
  child.stdout = new FakeStream();
  child.stderr = new FakeStream();
  child.killCalls = 0;
  child.kill = () => {
    child.killCalls += 1;
  };
  return child;
}

test('native macOS update helper paths cover development and packaged builds', () => {
  assert.equal(
    nativeUpdateHelperPath({ projectRoot: '/repo' }),
    path.join('/repo', '.build', 'native-macos', 'OmlorixUpdateProgress.app', 'Contents', 'MacOS', 'OmlorixUpdateProgress'),
  );
  assert.equal(
    nativeUpdateHelperPath({ isPackaged: true, resourcesPath: '/Launcher.app/Contents/Resources' }),
    path.join('/Launcher.app/Contents/Resources', 'native', 'OmlorixUpdateProgress.app', 'Contents', 'MacOS', 'OmlorixUpdateProgress'),
  );
  assert.equal(
    macApplicationBundlePath('/Applications/Omlorix Server Launcher.app/Contents/MacOS/Omlorix Server Launcher'),
    '/Applications/Omlorix Server Launcher.app',
  );
  assert.equal(
    nativeUpdateIconPath({
      isPackaged: true,
      execPath: '/Applications/Omlorix Server Launcher.app/Contents/MacOS/Omlorix Server Launcher',
    }),
    '/Applications/Omlorix Server Launcher.app',
  );
});

test('native macOS update controller exchanges states and queued actions as JSON lines', async () => {
  const child = createFakeChild();
  const spawnCalls = [];
  const controller = createNativeUpdateProgressController({
    app: { isPackaged: false },
    projectRoot: '/repo',
    existsSync: () => true,
    spawn: (command, args, options) => {
      spawnCalls.push({ command, args, options });
      return child;
    },
  });

  controller.setState({
    phase: 'downloading',
    percent: 42,
    primaryLabel: 'Cancel',
  });
  assert.equal(spawnCalls.length, 1);
  assert.equal(
    spawnCalls[0].command,
    nativeUpdateHelperPath({ projectRoot: '/repo' }),
  );
  assert.deepEqual(JSON.parse(child.stdin.writes[0]), {
    phase: 'downloading',
    percent: 42,
    primaryLabel: 'Cancel',
  });

  // An action can arrive while the updater is between states. It must be held
  // until the flow requests the next user action instead of being discarded.
  child.stdout.emit('data', '{"action":"primary"}\n');
  assert.equal(await controller.waitForAction(), 'primary');

  controller.close();
  assert.deepEqual(JSON.parse(child.stdin.writes[1]), { command: 'close' });
  assert.equal(child.stdin.destroyed, true);
});

test('native macOS update controller replaces an obsolete phase waiter', async () => {
  const child = createFakeChild();
  const controller = createNativeUpdateProgressController({
    app: { isPackaged: false },
    projectRoot: '/repo',
    existsSync: () => true,
    spawn: () => child,
  });

  // The launcher races this first wait against the download. When the download
  // finishes first, Promise.race leaves the losing action promise alive. A new
  // ready-state wait must retire it before Install and Relaunch can be clicked.
  const downloadPhaseWait = controller.waitForAction();
  const readyPhaseWait = controller.waitForAction();

  assert.equal(await downloadPhaseWait, 'stale');

  // The native Swift helper sends only one terminal action. That one action
  // must therefore reach the current ready-state waiter on the first click.
  child.stdout.emit('data', '{"action":"primary"}\n');
  assert.equal(await readyPhaseWait, 'primary');

  controller.close();
});

test('native macOS update controller force-closes an unresponsive helper', async () => {
  const child = createFakeChild();
  const controller = createNativeUpdateProgressController({
    app: { isPackaged: false },
    projectRoot: '/repo',
    existsSync: () => true,
    spawn: () => child,
    shutdownGracePeriodMs: 5,
  });

  controller.close();
  controller.close();
  await new Promise((resolve) => setTimeout(resolve, 15));
  assert.equal(child.killCalls, 1);
});

test('native macOS update controller cancels its force-close after child exit', async () => {
  const child = createFakeChild();
  const controller = createNativeUpdateProgressController({
    app: { isPackaged: false },
    projectRoot: '/repo',
    existsSync: () => true,
    spawn: () => child,
    shutdownGracePeriodMs: 5,
  });

  controller.close();
  child.emit('exit', 0);
  child.emit('close', 0);
  await new Promise((resolve) => setTimeout(resolve, 15));
  assert.equal(child.killCalls, 0);
});

test('native macOS update controller fails clearly when packaging omitted the helper', () => {
  assert.throws(
    () => createNativeUpdateProgressController({
      app: { isPackaged: true },
      resourcesPath: '/missing',
      existsSync: () => false,
    }),
    /Native macOS update UI helper is missing/i,
  );
});
