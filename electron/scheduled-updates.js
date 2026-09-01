const { EventEmitter } = require('events');
const fs = require('fs/promises');
const path = require('path');
const { DEFAULT_CHANNEL, normalizeUpdateChannel } = require('./release-channels');

const STORE_FILE_NAME = 'scheduled-updates.json';
const MAX_TIMER_DELAY_MS = 30 * 60 * 1000;
const DEFAULT_TIME = '03:00';
const ALL_WEEKDAYS = [0, 1, 2, 3, 4, 5, 6];
const WEEKEND_DAYS = [0, 6];

const DEFAULT_SETTINGS = {
  enabled: false,
  channel: DEFAULT_CHANNEL,
  schedule: 'daily',
  weekdays: ALL_WEEKDAYS,
  time: DEFAULT_TIME,
  backupBeforeUpdate: true,
  backupDestinationId: '',
  backupEncryptionEnabled: true,
  onlyWhenHealthy: true,
};

const DEFAULT_STATUS = {
  state: 'idle',
  nextRunAt: '',
  lastAttemptAt: '',
  lastSuccessAt: '',
  lastFailureAt: '',
  lastCheckedAt: '',
  lastMessage: 'Automatic updates are disabled.',
  currentVersion: '',
  latestVersion: '',
  lastAttemptWindowKey: '',
  launcherRequirement: null,
};

function pad2(value) {
  return String(value).padStart(2, '0');
}

function localDateKey(date) {
  return [
    date.getFullYear(),
    pad2(date.getMonth() + 1),
    pad2(date.getDate()),
  ].join('-');
}

function sanitizeTime(value) {
  const match = String(value || '').trim().match(/^([01]?\d|2[0-3]):([0-5]\d)$/);
  if (!match) return DEFAULT_TIME;
  return `${pad2(match[1])}:${match[2]}`;
}

function normalizeWeekdays(value) {
  const raw = Array.isArray(value) ? value : [];
  const selected = raw
    .map((item) => Number(item))
    .filter((item) => Number.isInteger(item) && item >= 0 && item <= 6);
  const unique = Array.from(new Set(selected)).sort((left, right) => left - right);
  return unique.length ? unique : [...ALL_WEEKDAYS];
}

function normalizeProfile(profile) {
  // Profile is no longer used; kept for compatibility with existing stored settings
  return profile || 'singleServer';
}

function normalizeBackupDestinationId(value) {
  const destinationId = String(value || '').trim();
  if (destinationId.length > 255) {
    throw new Error('The selected backup destination is invalid.');
  }
  return destinationId;
}

function normalizeSettings(value = {}) {
  const schedule = ['daily', 'weekends', 'custom'].includes(value.schedule)
    ? value.schedule
    : DEFAULT_SETTINGS.schedule;

  return {
    enabled: Boolean(value.enabled),
    profile: normalizeProfile(value.profile),
    channel: normalizeUpdateChannel(value.channel),
    schedule,
    weekdays: schedule === 'daily'
      ? [...ALL_WEEKDAYS]
      : schedule === 'weekends'
        ? [...WEEKEND_DAYS]
        : normalizeWeekdays(value.weekdays),
    time: sanitizeTime(value.time),
    backupBeforeUpdate: value.backupBeforeUpdate !== false,
    backupDestinationId: normalizeBackupDestinationId(value.backupDestinationId),
    backupEncryptionEnabled: value.backupEncryptionEnabled !== false,
    onlyWhenHealthy: value.onlyWhenHealthy !== false,
  };
}

function normalizeStatus(value = {}) {
  return {
    ...DEFAULT_STATUS,
    ...value,
    launcherRequirement: value.launcherRequirement || null,
  };
}

function normalizeLoadedState(settings, status) {
  // Older launcher builds could persist a launcher compatibility block even
  // after automatic updates were disabled. Treat disabled automatic updates as
  // the source of truth so the launcher does not keep showing a stale repair
  // action on every startup.
  if (settings.enabled) {
    return status;
  }

  return {
    ...status,
    state: 'idle',
    nextRunAt: '',
    lastMessage: 'Automatic updates are disabled.',
    launcherRequirement: null,
  };
}

function scheduleWeekdays(settings) {
  if (settings.schedule === 'daily') return ALL_WEEKDAYS;
  if (settings.schedule === 'weekends') return WEEKEND_DAYS;
  return normalizeWeekdays(settings.weekdays);
}

function windowKeyForRun(date, settings) {
  return `${localDateKey(date)}T${sanitizeTime(settings.time)}`;
}

function nextRunDate(settingsInput, fromDate = new Date()) {
  const settings = normalizeSettings(settingsInput);
  if (!settings.enabled) return null;
  const [hour, minute] = sanitizeTime(settings.time).split(':').map((part) => Number(part));
  const weekdays = new Set(scheduleWeekdays(settings));

  for (let offset = 0; offset < 14; offset += 1) {
    const candidate = new Date(fromDate);
    candidate.setDate(fromDate.getDate() + offset);
    candidate.setHours(hour, minute, 0, 0);
    if (!weekdays.has(candidate.getDay())) continue;
    if (candidate > fromDate) return candidate;
  }

  return null;
}

function isHealthyForUnattendedUpdate(state) {
  const docker = state?.docker || {};
  const stack = state?.stack || {};
  const expectedServicesReady = stack.expectedKnown !== true
    ? Boolean(stack.healthy)
    : Number(stack.total || 0) > 0
      && Number(stack.running || 0) === Number(stack.total || 0)
      && Number(stack.missing || 0) === 0;
  return Boolean(
    docker.installed
    && docker.running
    && docker.compose
    && stack.healthy
    && expectedServicesReady
  );
}

class ScheduledUpdateManager extends EventEmitter {
  constructor({ app, serverManager, now = () => new Date() }) {
    super();
    this.app = app;
    this.serverManager = serverManager;
    this.now = now;
    this.storePath = path.join(serverManager.serverHome || path.join(app.getPath('userData'), 'server'), STORE_FILE_NAME);
    this.legacyStorePath = path.join(app.getPath('userData'), STORE_FILE_NAME);
    this.settings = { ...DEFAULT_SETTINGS };
    this.status = { ...DEFAULT_STATUS };
    this.timer = null;
    this.running = false;
    this.initialized = false;
  }

  /** Use the server-wide lock when available while keeping unit fakes small. */
  withSharedLock(command, operation, options = {}) {
    if (typeof this.serverManager?.withSharedOperationLock === 'function') {
      return this.serverManager.withSharedOperationLock(command, operation, options);
    }
    return operation();
  }

  async initialize() {
    await this.withSharedLock('auto-update migrate', async () => {
      await fs.mkdir(path.dirname(this.storePath), { recursive: true });
      try {
        await fs.access(this.storePath);
      } catch (canonicalError) {
        if (canonicalError?.code !== 'ENOENT') throw canonicalError;
        try {
          // Move the former Launcher-only store into the CLI home. If a CLI
          // store already exists it remains authoritative and is never merged.
          await fs.rename(this.legacyStorePath, this.storePath);
        } catch (legacyError) {
          if (legacyError?.code !== 'ENOENT') throw legacyError;
        }
      }
      await this.load();
    });
    this.initialized = true;
    await this.scheduleNext();
    return this.snapshot();
  }

  async load() {
    try {
      const raw = await fs.readFile(this.storePath, 'utf8');
      const parsed = JSON.parse(raw);
      this.settings = normalizeSettings(parsed.settings);
      this.status = normalizeLoadedState(this.settings, normalizeStatus(parsed.status));
    } catch (error) {
      this.settings = { ...DEFAULT_SETTINGS };
      this.status = { ...DEFAULT_STATUS };
    }
  }

  async save() {
    await fs.mkdir(path.dirname(this.storePath), { recursive: true });
    const tmpPath = `${this.storePath}.tmp`;
    const payload = JSON.stringify({
      settings: this.settings,
      status: this.status,
    }, null, 2);
    await fs.writeFile(tmpPath, `${payload}\n`, 'utf8');
    await fs.rename(tmpPath, this.storePath);
  }

  snapshot() {
    return {
      settings: { ...this.settings, weekdays: [...this.settings.weekdays] },
      status: {
        ...this.status,
        launcherRequirement: this.status.launcherRequirement
          ? { ...this.status.launcherRequirement }
          : null,
      },
    };
  }

  async saveSettings(payload) {
    return this.withSharedLock('auto-update settings', async () => {
    // Reload immediately after acquiring the cross-process lock so a CLI edit
    // completed since Launcher startup cannot be overwritten by stale state.
    await this.load();
    this.settings = normalizeSettings({ ...this.settings, ...payload });
    this.status = {
      ...this.status,
      state: this.settings.enabled ? 'scheduled' : 'idle',
      lastMessage: this.settings.enabled
        ? 'Automatic updates are scheduled.'
        : 'Automatic updates are disabled.',
      launcherRequirement: null,
    };
    await this.scheduleNext();
      return this.snapshot();
    });
  }

  clearTimer() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  async scheduleNext() {
    this.clearTimer();
    if (!this.settings.enabled) {
      this.status.nextRunAt = '';
      this.status.state = 'idle';
      this.status.launcherRequirement = null;
      await this.save();
      this.emit('changed', this.snapshot());
      return this.snapshot();
    }

    if (this.status.state === 'blocked') {
      const unblocked = await this.refreshLauncherBlock();
      if (unblocked) {
        return this.scheduleNext();
      }
      this.status.nextRunAt = '';
      await this.save();
      this.emit('changed', this.snapshot());
      return this.snapshot();
    }

    const next = nextRunDate(this.settings, this.now());
    this.status.nextRunAt = next ? next.toISOString() : '';
    if (!['running', 'success', 'skipped', 'error'].includes(this.status.state)) {
      this.status.state = 'scheduled';
    }
    await this.save();
    this.armTimer();
    this.emit('changed', this.snapshot());
    return this.snapshot();
  }

  armTimer() {
    this.clearTimer();
    if (!this.settings.enabled || !this.status.nextRunAt || this.status.state === 'blocked') return;
    const delayMs = Math.max(0, Date.parse(this.status.nextRunAt) - this.now().getTime());
    this.timer = setTimeout(() => {
      this.wake().catch((error) => {
        this.recordFailure(error).catch(() => {});
      });
    }, Math.min(delayMs, MAX_TIMER_DELAY_MS));
  }

  async wake() {
    if (!this.settings.enabled || this.status.state === 'blocked') return this.snapshot();
    const nextRunAt = this.status.nextRunAt ? new Date(this.status.nextRunAt) : nextRunDate(this.settings, this.now());
    if (!nextRunAt) return this.scheduleNext();
    if (nextRunAt > this.now()) {
      this.armTimer();
      return this.snapshot();
    }

    const windowKey = windowKeyForRun(nextRunAt, this.settings);
    if (this.status.lastAttemptWindowKey === windowKey) {
      return this.scheduleNext();
    }

    return this.runUpdate({ manual: false, windowKey });
  }

  async runNow() {
    return this.runUpdate({
      manual: true,
      windowKey: `manual-${this.now().toISOString()}`,
    });
  }

  async setRunningStatus(windowKey) {
    this.running = true;
    this.clearTimer();
    this.status = {
      ...this.status,
      state: 'running',
      lastAttemptAt: this.now().toISOString(),
      lastAttemptWindowKey: windowKey,
      lastMessage: 'Automatic update check is running.',
      launcherRequirement: null,
    };
    await this.save();
    this.emit('changed', this.snapshot());
  }

  async recordSkipped(message, updateInfo = {}) {
    this.running = false;
    this.status = {
      ...this.status,
      state: 'skipped',
      lastCheckedAt: this.now().toISOString(),
      lastMessage: message,
      currentVersion: updateInfo.currentVersion || this.status.currentVersion,
      latestVersion: updateInfo.latestVersion || this.status.latestVersion,
      launcherRequirement: null,
    };
    await this.scheduleNext();
    return this.snapshot();
  }

  async recordSuccess(message, updateInfo = {}) {
    this.running = false;
    this.status = {
      ...this.status,
      state: 'success',
      lastCheckedAt: this.now().toISOString(),
      lastSuccessAt: this.now().toISOString(),
      lastMessage: message,
      currentVersion: updateInfo.currentVersion || this.status.currentVersion,
      latestVersion: updateInfo.latestVersion || this.status.latestVersion,
      launcherRequirement: null,
    };
    await this.scheduleNext();
    return this.snapshot();
  }

  async recordFailure(error) {
    this.running = false;
    this.status = {
      ...this.status,
      state: 'error',
      lastFailureAt: this.now().toISOString(),
      lastMessage: error?.message || 'Automatic update failed.',
    };
    await this.scheduleNext();
    return this.snapshot();
  }

  async recordLauncherBlocked(error) {
    this.running = false;
    this.status = {
      ...this.status,
      state: 'blocked',
      nextRunAt: '',
      lastCheckedAt: this.now().toISOString(),
      lastFailureAt: this.now().toISOString(),
      lastMessage: error.message,
      latestVersion: error.targetVersion || this.status.latestVersion,
      launcherRequirement: {
        currentLauncherVersion: error.currentLauncherVersion || '',
        minimumLauncherVersion: error.minimumLauncherVersion || '',
        targetVersion: error.targetVersion || '',
        releaseNotes: error.releaseNotes || '',
      },
    };
    await this.save();
    this.emit('changed', this.snapshot());
    return this.snapshot();
  }

  async refreshLauncherBlock() {
    if (this.status.state !== 'blocked' || !this.status.launcherRequirement) {
      return false;
    }

    try {
      const updateInfo = await this.serverManager.getServerUpdateInfo(this.settings.channel);
      this.status = {
        ...this.status,
        state: 'scheduled',
        lastCheckedAt: this.now().toISOString(),
        lastMessage: 'Automatic updates are scheduled.',
        currentVersion: updateInfo.currentVersion || this.status.currentVersion,
        latestVersion: updateInfo.latestVersion || this.status.latestVersion,
        launcherRequirement: null,
      };
      return true;
    } catch (error) {
      if (error?.code === 'LAUNCHER_UPDATE_REQUIRED') {
        this.status = {
          ...this.status,
          lastCheckedAt: this.now().toISOString(),
          lastMessage: error.message,
          latestVersion: error.targetVersion || this.status.latestVersion,
          launcherRequirement: {
            currentLauncherVersion: error.currentLauncherVersion || '',
            minimumLauncherVersion: error.minimumLauncherVersion || '',
            targetVersion: error.targetVersion || '',
            releaseNotes: error.releaseNotes || '',
          },
        };
      }
      return false;
    }
  }

  async runUpdate({ manual = false, windowKey } = {}) {
    return this.withSharedLock('auto-update', async () => {
    await this.load();
    if (this.running) {
      return this.recordSkipped('Another automatic update check is already running.');
    }
    if (!manual && !this.settings.enabled) {
      return this.snapshot();
    }
    if (this.serverManager.activeOperation) {
      return this.recordSkipped(`Skipped because another operation is running: ${this.serverManager.activeOperation}.`);
    }

    await this.setRunningStatus(windowKey || `manual-${this.now().toISOString()}`);

    try {
      if (this.settings.onlyWhenHealthy) {
        const state = await this.serverManager.getState();
        if (!isHealthyForUnattendedUpdate(state)) {
          return this.recordSkipped('Skipped because Omlorix or Docker is not healthy.');
        }
      }

      const updateInfo = await this.serverManager.getServerUpdateInfo(this.settings.channel);
      if (!updateInfo.updateAvailable) {
        return this.recordSkipped('No Omlorix update is available.', updateInfo);
      }

      await this.serverManager.update({
        channel: this.settings.channel,
        skipBackup: !this.settings.backupBeforeUpdate,
        destinationId: this.settings.backupDestinationId,
        encryptionEnabled: this.settings.backupEncryptionEnabled,
        sharedLockHeld: true,
      });
      return this.recordSuccess(`Updated Omlorix to ${updateInfo.latestVersion}.`, updateInfo);
    } catch (error) {
      if (error?.code === 'LAUNCHER_UPDATE_REQUIRED') {
        return this.recordLauncherBlocked(error);
      }
      return this.recordFailure(error);
    }
    });
  }
}

module.exports = {
  ALL_WEEKDAYS,
  DEFAULT_SETTINGS,
  DEFAULT_STATUS,
  ScheduledUpdateManager,
  WEEKEND_DAYS,
  isHealthyForUnattendedUpdate,
  nextRunDate,
  normalizeSettings,
  sanitizeTime,
  windowKeyForRun,
};
