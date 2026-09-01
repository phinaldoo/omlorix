const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const rendererRoot = path.join(__dirname, '..', 'renderer');

function element() {
  return {
    className: '',
    dataset: {},
    hidden: false,
    textContent: '',
    focusCalls: [],
    focus(options) {
      this.focusCalls.push(options);
    },
  };
}

function rendererSlice(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert(start >= 0 && end > start, `${startMarker} must remain discoverable`);
  return source.slice(start, end);
}

async function createHarness({
  repairResult,
  repairError,
  directTranslations = {},
  sourceTranslations = {},
} = {}) {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const errorTranslation = rendererSlice(
    source,
    '  function translatedErrorMessage',
    '\n  function writeConsoleOutput',
  );
  const visitorRenderer = rendererSlice(
    source,
    '  function renderVisitorIpStatus',
    '\n  function openProxySection',
  );
  const actions = rendererSlice(
    source,
    '  async function runAction',
    '\n  /** Run a destination-aware full backup',
  );
  const els = {
    visitorIpCard: element(),
    visitorIpDot: element(),
    visitorIpTitle: element(),
    visitorIpDescription: element(),
    fixVisitorIpsButton: element(),
    proxyVisitorIpCard: element(),
    proxyVisitorIpDot: element(),
    proxyVisitorIpTitle: element(),
    proxyVisitorIpDescription: element(),
    proxyFixVisitorIpsButton: element(),
  };
  const currentVisitorIp = {
    level: 'warn',
    title: 'Needs setup',
    message: 'Visitor IP trust is not configured.',
    ready: false,
    configured: false,
    recommendedAction: 'fix',
  };
  const state = {
    current: { visitorIp: currentVisitorIp },
    visitorIpRepairFailure: '',
  };
  const renders = [];
  const busy = [];
  const consoleMessages = [];
  let refreshCount = 0;
  const translations = {
    launcher_visitor_ips_heading: 'Visitor IPs',
    launcher_visitor_ip_title_repair_failed: 'Automatic fix failed',
    launcher_visitor_ip_message_repair_failed: '{error} Make sure Omlorix is running and ready, then try again. See Console for details.',
    launcher_visitor_ip_action_open_proxy: 'Open proxy settings',
    launcher_visitor_ip_action_fix: 'Fix automatically',
    ...directTranslations,
  };
  const context = {
    els,
    state,
    window: {
      omlorixServer: {
        async repairVisitorIps() {
          if (repairError) throw repairError;
          return repairResult;
        },
      },
    },
    launcherT(key, fallback, values = {}) {
      return String(translations[key] || fallback).replace(/\{(\w+)\}/g, (match, name) => (
        Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match
      ));
    },
    translateLauncherSource(sourceText) {
      return sourceTranslations[sourceText] || sourceText;
    },
    envActionsBlocked() {
      return false;
    },
    dockerActionsBlocked() {
      return false;
    },
    dockerActionBlockedMessage() {
      return '';
    },
    setBusy(value) {
      busy.push(value);
    },
    appendConsole(message) {
      consoleMessages.push(message);
    },
    renderState(value) {
      renders.push(value);
      state.current = value;
      context.renderVisitorIpStatusForTest(value.visitorIp);
    },
    async refresh() {
      refreshCount += 1;
      return state.current;
    },
  };
  vm.runInNewContext(
    `${errorTranslation}\n${visitorRenderer}\n${actions}\nthis.repairVisitorIpsForTest = repairVisitorIps;\nthis.renderVisitorIpStatusForTest = renderVisitorIpStatus;`,
    context,
  );
  return {
    busy,
    consoleMessages,
    context,
    els,
    get refreshCount() {
      return refreshCount;
    },
    renders,
    state,
  };
}

test('Visitor-IP repair success applies the returned launcher state immediately', async () => {
  const repaired = {
    visitorIp: {
      level: 'ok',
      title: 'Configured',
      message: 'Visitor IP trust is configured.',
      ready: true,
      configured: true,
    },
  };
  const harness = await createHarness({ repairResult: repaired });

  await harness.context.repairVisitorIpsForTest();

  assert.deepEqual(harness.renders, [repaired]);
  assert.equal(harness.refreshCount, 0);
  assert.equal(harness.state.visitorIpRepairFailure, '');
  assert.equal(harness.els.proxyVisitorIpCard.dataset.level, 'ok');
  assert.equal(harness.els.proxyVisitorIpTitle.textContent, 'Visitor IPs: Configured');
  assert.equal(
    harness.els.proxyVisitorIpDescription.textContent,
    'Visitor IP trust is configured.',
  );
  assert.deepEqual(harness.els.proxyVisitorIpCard.focusCalls, []);
  assert.deepEqual(harness.busy, [true, false]);
});

test('Visitor-IP repair failure remains visible and focused on the Proxy page', async () => {
  const harness = await createHarness({
    repairError: new Error('The frontend service is unavailable.'),
  });

  await harness.context.repairVisitorIpsForTest();

  assert.equal(harness.renders.length, 0);
  assert.equal(harness.refreshCount, 1);
  assert.equal(harness.state.visitorIpRepairFailure, 'The frontend service is unavailable.');
  assert.equal(harness.els.proxyVisitorIpCard.dataset.level, 'error');
  assert.equal(harness.els.proxyVisitorIpTitle.textContent, 'Visitor IPs: Automatic fix failed');
  assert.equal(
    harness.els.proxyVisitorIpDescription.textContent,
    'The frontend service is unavailable. Make sure Omlorix is running and ready, then try again. See Console for details.',
  );
  assert.equal(harness.els.proxyVisitorIpCard.focusCalls.length, 1);
  assert.equal(harness.els.proxyVisitorIpCard.focusCalls[0].preventScroll, true);
  assert.match(harness.consoleMessages.at(-1), /frontend service is unavailable/);
  assert.deepEqual(harness.busy, [true, false]);
});

test('Visitor-IP repair localizes Electron-wrapped manager failures end to end', async () => {
  const managerError = 'Visitor IP settings could not be applied and verified. The previous configuration was restored.';
  const localizedManagerError = 'Die Besucher-IP-Einstellungen konnten nicht angewendet und überprüft werden. Die vorherige Konfiguration wurde wiederhergestellt.';
  const action = 'Korrektur der Besucher-IP-Erkennung';
  const harness = await createHarness({
    repairError: new Error(
      `Error invoking remote method 'server:repair-visitor-ips': Error: ${managerError}`,
    ),
    directTranslations: {
      launcher_visitor_ips_heading: 'Besucher-IPs',
      launcher_visitor_ip_title_repair_failed: 'Automatische Korrektur fehlgeschlagen',
      launcher_visitor_ip_message_repair_failed: '{error} Stelle sicher, dass Omlorix läuft und bereit ist, und versuche es erneut. Details findest du in der Konsole.',
      launcher_ui_value1_failed_value2: '{value1} fehlgeschlagen: {value2}',
    },
    sourceTranslations: {
      'Fixing visitor IP detection': action,
      [managerError]: localizedManagerError,
    },
  });

  await harness.context.repairVisitorIpsForTest();

  assert.equal(harness.state.visitorIpRepairFailure, localizedManagerError);
  assert.equal(
    harness.els.proxyVisitorIpDescription.textContent,
    `${localizedManagerError} Stelle sicher, dass Omlorix läuft und bereit ist, und versuche es erneut. Details findest du in der Konsole.`,
  );
  assert.equal(
    harness.consoleMessages.at(-1),
    `${action} fehlgeschlagen: ${localizedManagerError}\n`,
  );
  assert.doesNotMatch(
    `${harness.els.proxyVisitorIpDescription.textContent}\n${harness.consoleMessages.join('')}`,
    /Error invoking remote method|server:repair-visitor-ips|Visitor IP settings| failed:/,
  );
});

test('Visitor-IP repair operation name is localized before Console formatting', async () => {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const operationNames = rendererSlice(
    source,
    '  function serviceActionLabel',
    '\n  function renderServices',
  );
  const context = {
    launcherT(key, fallback) {
      return key === 'launcher_ui_visitor_ip_repair_operation'
        ? 'Korrektur der Besucher-IP-Erkennung'
        : fallback;
    },
  };
  vm.runInNewContext(
    `${operationNames}\nthis.serviceOperationNameForTest = serviceOperationName;`,
    context,
  );

  assert.equal(
    context.serviceOperationNameForTest('Visitor IP repair'),
    'Korrektur der Besucher-IP-Erkennung',
  );
});

test('Proxy Visitor-IP feedback is an atomic live region tied to its action', async () => {
  const html = await fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8');

  assert.match(
    html,
    /id="proxyVisitorIpCard"[^>]*role="status"[^>]*aria-live="polite"[^>]*aria-atomic="true"[^>]*tabindex="-1"/,
  );
  assert.match(
    html,
    /id="proxyFixVisitorIpsButton"[^>]*aria-describedby="proxyVisitorIpDescription"/,
  );
});
