const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const {
  ScheduledUpdateManager,
  nextRunDate,
  normalizeSettings,
  sanitizeTime,
} = require('../scheduled-updates');

async function createManager({ now = new Date('2026-06-05T01:00:00.000Z'), serverManager = {} } = {}) {
  const userData = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-scheduled-updates-test-'));
  const fallbackServerManager = {
    activeOperation: null,
    getState: async () => ({
      docker: { installed: true, running: true, compose: true },
      stack: { healthy: true },
    }),
    getServerUpdateInfo: async () => ({
      currentVersion: '1.0.0',
      latestVersion: '1.0.1',
      updateAvailable: true,
    }),
    update: async () => ({}),
    ...serverManager,
  };
  const manager = new ScheduledUpdateManager({
    app: { getPath: () => userData },
    serverManager: fallbackServerManager,
    now: () => now,
  });
  await manager.initialize();
  return manager;
}

test('sanitizeTime keeps valid HH:mm values and repairs invalid input', () => {
  assert.equal(sanitizeTime('3:05'), '03:05');
  assert.equal(sanitizeTime('23:59'), '23:59');
  assert.equal(sanitizeTime('24:00'), '03:00');
  assert.equal(sanitizeTime('bad'), '03:00');
});

test('normalizeSettings maps preset schedules to their intended weekdays', () => {
  assert.deepEqual(normalizeSettings({ schedule: 'daily' }).weekdays, [0, 1, 2, 3, 4, 5, 6]);
  assert.deepEqual(normalizeSettings({ schedule: 'weekends' }).weekdays, [0, 6]);
  assert.deepEqual(normalizeSettings({ schedule: 'custom', weekdays: [5, 1, 1, 9] }).weekdays, [1, 5]);
  assert.equal(normalizeSettings({ channel: 'beta' }).channel, 'beta');
  assert.equal(normalizeSettings({ channel: 'nightly' }).channel, 'stable');
  assert.equal(normalizeSettings({}).backupDestinationId, '');
  assert.equal(normalizeSettings({}).backupEncryptionEnabled, true);
  assert.equal(normalizeSettings({
    backupDestinationId: ' remote-store ',
    backupEncryptionEnabled: false,
  }).backupDestinationId, 'remote-store');
  assert.equal(normalizeSettings({ backupEncryptionEnabled: false }).backupEncryptionEnabled, false);
  assert.throws(
    () => normalizeSettings({ backupDestinationId: 'x'.repeat(256) }),
    /backup destination is invalid/i,
  );
});

test('scheduled updates share the CLI server home and migrate the legacy Launcher store', async (t) => {
  const userData = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-scheduled-migrate-'));
  t.after(() => fs.rm(userData, { recursive: true, force: true }));
  const legacyPath = path.join(userData, 'scheduled-updates.json');
  await fs.writeFile(legacyPath, JSON.stringify({
    settings: { enabled: true, channel: 'beta', schedule: 'daily', time: '04:15' },
    status: { state: 'scheduled' },
  }));
  const serverHome = path.join(userData, 'server');
  const manager = new ScheduledUpdateManager({
    app: { getPath: () => userData },
    serverManager: { serverHome },
  });

  await manager.initialize();
  manager.clearTimer();

  assert.equal(manager.storePath, path.join(serverHome, 'scheduled-updates.json'));
  assert.equal(manager.settings.channel, 'beta');
  assert.equal(manager.settings.time, '04:15');
  await fs.access(manager.storePath);
  await assert.rejects(fs.access(legacyPath));
});

test('nextRunDate schedules the next local daily time', () => {
  const next = nextRunDate(
    { enabled: true, schedule: 'daily', time: '03:00' },
    new Date('2026-06-05T01:30:00'),
  );

  assert.equal(next.getFullYear(), 2026);
  assert.equal(next.getMonth(), 5);
  assert.equal(next.getDate(), 5);
  assert.equal(next.getHours(), 3);
  assert.equal(next.getMinutes(), 0);
});

test('nextRunDate rolls weekend schedules to Saturday when Friday has passed', () => {
  const next = nextRunDate(
    { enabled: true, schedule: 'weekends', time: '12:00' },
    new Date('2026-06-05T13:00:00'),
  );

  assert.equal(next.getDay(), 6);
  assert.equal(next.getHours(), 12);
});

test('nextRunDate preserves Sunday as day zero for custom schedules', () => {
  const next = nextRunDate(
    { enabled: true, schedule: 'custom', weekdays: [0], time: '12:00' },
    new Date('2026-06-06T13:00:00'),
  );

  assert.equal(next.getDay(), 0);
  assert.equal(next.getDate(), 7);
  assert.equal(next.getHours(), 12);
});

test('runNow skips when the health guard is enabled and Omlorix is unhealthy', async () => {
  const manager = await createManager({
    serverManager: {
      getState: async () => ({
        docker: { installed: true, running: true, compose: true },
        stack: { healthy: false },
      }),
    },
  });

  await manager.saveSettings({ enabled: true, onlyWhenHealthy: true });
  const snapshot = await manager.runNow();
  manager.clearTimer();

  assert.equal(snapshot.status.state, 'skipped');
  assert.match(snapshot.status.lastMessage, /not healthy/i);
});

test('runNow skips when an expected Compose service is missing', async () => {
  let updateCalls = 0;
  const manager = await createManager({
    serverManager: {
      getState: async () => ({
        docker: { installed: true, running: true, compose: true },
        stack: { healthy: true, expectedKnown: true, total: 3, running: 2, missing: 1 },
      }),
      update: async () => { updateCalls += 1; },
    },
  });

  await manager.saveSettings({ enabled: true, onlyWhenHealthy: true });
  const snapshot = await manager.runNow();
  manager.clearTimer();

  assert.equal(snapshot.status.state, 'skipped');
  assert.equal(updateCalls, 0);
});

test('runNow records up-to-date checks without calling update', async () => {
  let updateCalled = false;
  const manager = await createManager({
    serverManager: {
      getServerUpdateInfo: async () => ({
        currentVersion: '1.0.1',
        latestVersion: '1.0.1',
        updateAvailable: false,
      }),
      update: async () => {
        updateCalled = true;
      },
    },
  });

  await manager.saveSettings({ enabled: true });
  const snapshot = await manager.runNow();
  manager.clearTimer();

  assert.equal(snapshot.status.state, 'skipped');
  assert.equal(snapshot.status.currentVersion, '1.0.1');
  assert.equal(snapshot.status.latestVersion, '1.0.1');
  assert.equal(updateCalled, false);
});

test('runNow passes a disabled backup policy to the server update routine', async () => {
  let receivedOptions = null;
  const manager = await createManager({
    serverManager: {
      update: async (options) => {
        receivedOptions = options;
        return {};
      },
    },
  });

  await manager.saveSettings({ enabled: true, backupBeforeUpdate: false });
  const snapshot = await manager.runNow();
  manager.clearTimer();

  assert.equal(snapshot.status.state, 'success');
  assert.deepEqual(receivedOptions, {
    channel: 'stable',
    skipBackup: true,
    destinationId: '',
    encryptionEnabled: true,
    sharedLockHeld: true,
  });
});

test('runNow passes the reviewed destination and plaintext policy to the server update routine', async () => {
  let receivedOptions = null;
  const manager = await createManager({
    serverManager: {
      update: async (options) => {
        receivedOptions = options;
        return {};
      },
    },
  });

  await manager.saveSettings({
    enabled: true,
    backupBeforeUpdate: true,
    backupDestinationId: 'remote-store',
    backupEncryptionEnabled: false,
  });
  const snapshot = await manager.runNow();
  manager.clearTimer();

  assert.equal(snapshot.status.state, 'success');
  assert.deepEqual(receivedOptions, {
    channel: 'stable',
    skipBackup: false,
    destinationId: 'remote-store',
    encryptionEnabled: false,
    sharedLockHeld: true,
  });
});

test('runNow blocks automatic updates when a newer launcher is required', async () => {
  const manager = await createManager({
    serverManager: {
      getServerUpdateInfo: async () => {
        const error = new Error('Omlorix 2.0.0 requires Omlorix Server Launcher 2.0.0 or newer.');
        error.code = 'LAUNCHER_UPDATE_REQUIRED';
        error.currentLauncherVersion = '1.9.0';
        error.minimumLauncherVersion = '2.0.0';
        error.targetVersion = '2.0.0';
        error.releaseNotes = 'New deployment files are required.';
        throw error;
      },
    },
  });

  await manager.saveSettings({ enabled: true });
  const snapshot = await manager.runNow();
  manager.clearTimer();

  assert.equal(snapshot.status.state, 'blocked');
  assert.equal(snapshot.status.nextRunAt, '');
  assert.deepEqual(snapshot.status.launcherRequirement, {
    currentLauncherVersion: '1.9.0',
    minimumLauncherVersion: '2.0.0',
    targetVersion: '2.0.0',
    releaseNotes: 'New deployment files are required.',
  });
});

test('disabled automatic updates clear stale launcher update requirements', async () => {
  const manager = await createManager();
  manager.status = {
    ...manager.status,
    state: 'blocked',
    launcherRequirement: {
      currentLauncherVersion: '1.9.0',
      minimumLauncherVersion: '2.0.0',
      targetVersion: '2.0.0',
      releaseNotes: '',
    },
  };
  manager.settings = {
    ...manager.settings,
    enabled: false,
  };

  const snapshot = await manager.scheduleNext();
  manager.clearTimer();

  assert.equal(snapshot.status.state, 'idle');
  assert.equal(snapshot.status.launcherRequirement, null);
});

test('loading disabled automatic updates clears persisted launcher update requirements', async () => {
  const userData = await fs.mkdtemp(path.join(os.tmpdir(), 'omlorix-scheduled-updates-test-'));
  await fs.writeFile(path.join(userData, 'scheduled-updates.json'), `${JSON.stringify({
    settings: {
      enabled: false,
      schedule: 'daily',
      time: '03:00',
    },
    status: {
      state: 'blocked',
      nextRunAt: '',
      lastMessage: 'Omlorix 2.0.0 requires Omlorix Server Launcher 2.0.0 or newer.',
      launcherRequirement: {
        currentLauncherVersion: '1.9.0',
        minimumLauncherVersion: '2.0.0',
        targetVersion: '2.0.0',
        releaseNotes: '',
      },
    },
  })}\n`, 'utf8');

  const manager = new ScheduledUpdateManager({
    app: { getPath: () => userData },
    serverManager: {
      activeOperation: null,
      getState: async () => ({
        docker: { installed: true, running: true, compose: true },
        stack: { healthy: true },
      }),
      getServerUpdateInfo: async () => ({
        currentVersion: '1.0.0',
        latestVersion: '1.0.1',
        updateAvailable: true,
      }),
      update: async () => ({}),
    },
    now: () => new Date('2026-06-05T01:00:00.000Z'),
  });

  const snapshot = await manager.initialize();
  manager.clearTimer();

  assert.equal(snapshot.settings.enabled, false);
  assert.equal(snapshot.status.state, 'idle');
  assert.equal(snapshot.status.lastMessage, 'Automatic updates are disabled.');
  assert.equal(snapshot.status.launcherRequirement, null);
});

test('blocked automatic updates resume after launcher compatibility is satisfied', async () => {
  const manager = await createManager({
    serverManager: {
      getServerUpdateInfo: async () => ({
        currentVersion: '1.0.0',
        latestVersion: '1.0.1',
        updateAvailable: true,
      }),
    },
  });
  await manager.saveSettings({ enabled: true });
  manager.status = {
    ...manager.status,
    state: 'blocked',
    nextRunAt: '',
    launcherRequirement: {
      currentLauncherVersion: '1.9.0',
      minimumLauncherVersion: '2.0.0',
      targetVersion: '2.0.0',
      releaseNotes: '',
    },
  };

  const snapshot = await manager.scheduleNext();
  manager.clearTimer();

  assert.equal(snapshot.status.state, 'scheduled');
  assert.equal(snapshot.status.launcherRequirement, null);
  assert.equal(snapshot.status.currentVersion, '1.0.0');
  assert.equal(snapshot.status.latestVersion, '1.0.1');
  assert.notEqual(snapshot.status.nextRunAt, '');
});
