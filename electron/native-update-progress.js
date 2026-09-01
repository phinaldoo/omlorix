const childProcess = require('child_process');
const fs = require('fs');
const path = require('path');

/**
 * Locate the native AppKit helper in development and packaged applications.
 * Keeping path resolution here makes the runtime behavior easy to test without
 * starting Electron or an actual macOS window.
 */
function nativeUpdateHelperPath({
  isPackaged = false,
  resourcesPath = process.resourcesPath,
  projectRoot = path.resolve(__dirname, '..'),
} = {}) {
  if (isPackaged) {
    return path.join(
      resourcesPath,
      'native',
      'OmlorixUpdateProgress.app',
      'Contents',
      'MacOS',
      'OmlorixUpdateProgress',
    );
  }
  return path.join(
    projectRoot,
    '.build',
    'native-macos',
    'OmlorixUpdateProgress.app',
    'Contents',
    'MacOS',
    'OmlorixUpdateProgress',
  );
}

/**
 * Find the containing macOS application bundle for a running executable.
 */
function macApplicationBundlePath(execPath) {
  const normalized = String(execPath || '');
  const marker = '.app/Contents/MacOS/';
  const markerIndex = normalized.indexOf(marker);
  if (markerIndex < 0) return '';
  return normalized.slice(0, markerIndex + '.app'.length);
}

/**
 * Choose the icon source shown by the native update window. Packaged launchers
 * use their installed bundle so Launch Services supplies the exact icon.
 */
function nativeUpdateIconPath({ isPackaged = false, execPath = process.execPath } = {}) {
  if (isPackaged) {
    const appBundlePath = macApplicationBundlePath(execPath);
    if (appBundlePath) return appBundlePath;
  }
  return path.join(__dirname, 'assets', 'launcher-icon.png');
}

/**
 * Create an AppKit-backed progress controller using newline-delimited JSON over
 * standard input/output. The returned surface deliberately matches the web
 * progress controller so the update state machine stays platform-independent.
 */
function createNativeUpdateProgressController({
  app,
  execPath = process.execPath,
  resourcesPath = process.resourcesPath,
  projectRoot = path.resolve(__dirname, '..'),
  spawn = childProcess.spawn,
  existsSync = fs.existsSync,
  shutdownGracePeriodMs = 2000,
  progressText = {},
} = {}) {
  const helperPath = nativeUpdateHelperPath({
    isPackaged: Boolean(app?.isPackaged),
    resourcesPath,
    projectRoot,
  });
  if (!existsSync(helperPath)) {
    throw new Error(
      `Native macOS update UI helper is missing at ${helperPath}. Repackage the launcher before checking for updates.`,
    );
  }

  const iconPath = nativeUpdateIconPath({
    isPackaged: Boolean(app?.isPackaged),
    execPath,
  });
  const child = spawn(helperPath, ['--icon-path', iconPath], {
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const actionWaiters = [];
  const pendingActions = [];
  let stdoutBuffer = '';
  let closedByHost = false;
  let childExited = false;
  let shutdownTimeout = null;

  /** Deliver an action immediately or retain it until the flow starts waiting. */
  const dispatchAction = (action) => {
    const waiter = actionWaiters.shift();
    if (waiter) {
      waiter(action);
      return;
    }
    pendingActions.push(action);
  };

  /** Parse complete protocol lines while retaining a possible partial line. */
  const consumeStdout = (chunk) => {
    stdoutBuffer += String(chunk || '');
    const lines = stdoutBuffer.split('\n');
    stdoutBuffer = lines.pop() || '';
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const payload = JSON.parse(line);
        if (payload?.action) dispatchAction(String(payload.action));
      } catch {
        // Ignore malformed helper output. The helper reserves stdout for JSON,
        // and an unexpected line must never trigger an updater action.
      }
    }
  };

  child.stdout?.on('data', consumeStdout);
  child.stderr?.on('data', (chunk) => {
    const message = String(chunk || '').trim();
    if (message) console.error(`[native-update-ui] ${message}`);
  });
  child.on('error', (error) => {
    console.error(`[native-update-ui] ${error?.message || error}`);
    dispatchAction('cancel');
  });
  /** Finalize child shutdown once, whether Node reports exit or close first. */
  const handleChildClosed = () => {
    if (shutdownTimeout) {
      clearTimeout(shutdownTimeout);
      shutdownTimeout = null;
    }
    if (childExited) return;
    childExited = true;
    if (!closedByHost) dispatchAction('cancel');
  };
  child.on('exit', handleChildClosed);
  child.on('close', handleChildClosed);

  /** Send one state or lifecycle command to the helper. */
  const send = (payload) => {
    if (!child.stdin || child.stdin.destroyed) return;
    child.stdin.write(`${JSON.stringify(payload)}\n`);
  };

  return {
    child,
    setState(state = {}) {
      send({ ...progressText, ...state });
    },
    updateProgress(payload = {}) {
      const percent = Math.max(0, Math.min(100, Number(payload.percent || 0)));
      send({
        ...progressText,
        phase: 'downloading',
        percent,
        transferred: Number(payload.transferred || 0),
        total: Number(payload.total || 0),
        bytesPerSecond: Number(payload.bytesPerSecond || 0),
      });
    },
    waitForAction() {
      // Promise.race does not cancel its losing promises. The update flow races
      // the download against a Cancel action, then starts a fresh wait for the
      // ready-state buttons after the download wins. Resolve that obsolete
      // download waiter before registering the new one so the first Install
      // and Relaunch click reaches the active phase instead of being swallowed.
      while (actionWaiters.length) {
        const staleWaiter = actionWaiters.shift();
        if (staleWaiter) staleWaiter('stale');
      }
      if (pendingActions.length) {
        return Promise.resolve(pendingActions.shift());
      }
      return new Promise((resolve) => actionWaiters.push(resolve));
    },
    close() {
      if (closedByHost) return;
      closedByHost = true;
      send({ command: 'close' });
      child.stdin?.end();
      if (!childExited) {
        shutdownTimeout = setTimeout(() => {
          shutdownTimeout = null;
          if (!childExited && typeof child.kill === 'function') child.kill();
        }, shutdownGracePeriodMs);
        shutdownTimeout.unref?.();
      }
    },
  };
}

module.exports = {
  createNativeUpdateProgressController,
  macApplicationBundlePath,
  nativeUpdateHelperPath,
  nativeUpdateIconPath,
};
