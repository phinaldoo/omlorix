const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const electronRoot = path.join(__dirname, '..');
const rendererRoot = path.join(electronRoot, 'renderer');

test('Services page explains its expected-service view and refresh interval', async () => {
  const html = await fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8');
  const servicesStart = html.indexOf('<section id="services"');
  const servicesEnd = html.indexOf('<!-- Console Section -->', servicesStart);
  const services = html.slice(servicesStart, servicesEnd);

  assert.match(services, /id="servicesSubtitle"/);
  assert.match(services, /Expected services and their current container state/);
  assert.match(services, /id="serviceAutoRefreshStatus"[^>]*>Updates every 10 seconds</);
  assert.match(services, /id="serviceCount"[^>]*>0\/0 running</);
  assert.match(services, /<tbody id="servicesBody">/);
  assert.match(services, /<th>Actions<\/th>/);
});

test('focused service polling is non-overlapping and does not rehydrate settings forms', async () => {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const refreshStart = source.indexOf('async function refreshServiceStatus()');
  const refreshEnd = source.indexOf('function serviceStatusRefreshIntervalMs()', refreshStart);
  const refreshSource = source.slice(refreshStart, refreshEnd);
  const applyStart = source.indexOf('function applyServiceStatus(');
  const applyEnd = refreshStart;
  const applySource = source.slice(applyStart, applyEnd);
  const timerStart = refreshEnd;
  const timerEnd = source.indexOf('function renderState(', timerStart);
  const timerSource = source.slice(timerStart, timerEnd);

  assert.match(source, /const SERVICE_STATUS_REFRESH_INTERVAL_MS = 10 \* 1000/);
  assert.match(source, /const SERVICE_STATUS_ACTION_REFRESH_INTERVAL_MS = 2 \* 1000/);
  assert.match(refreshSource, /document\.hidden/);
  assert.match(refreshSource, /state\.serviceStatusRefreshInFlight/);
  assert.match(refreshSource, /window\.omlorixServer\.getServiceStatus\(\)/);
  assert.match(applySource, /state\.current = \{[\s\S]*stack: mergedStack/);
  assert.match(applySource, /renderStackSnapshot/);
  assert.doesNotMatch(applySource, /hydrateForm/);
  assert.match(
    timerSource,
    /state\.busy[\s\S]*SERVICE_STATUS_ACTION_REFRESH_INTERVAL_MS[\s\S]*SERVICE_STATUS_REFRESH_INTERVAL_MS/,
  );
  assert.match(timerSource, /window\.setTimeout\([\s\S]*serviceStatusRefreshIntervalMs\(\)/);
  assert.match(timerSource, /state\.serviceStatusRefreshStarted/);
  assert.match(timerSource, /visibilitychange/);
});

test('busy actions switch both status views to immediate two-second polling', async () => {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const busyStart = source.indexOf('function setBusy(');
  const busyEnd = source.indexOf('function envActionsBlocked(', busyStart);
  const busySource = source.slice(busyStart, busyEnd);
  const cadenceStart = source.indexOf('function renderServiceStatusRefreshCadence()');
  const cadenceEnd = source.indexOf('function renderServices(', cadenceStart);
  const cadenceSource = source.slice(cadenceStart, cadenceEnd);

  assert.match(busySource, /pollingCadenceChanged/);
  assert.match(busySource, /scheduleServiceStatusRefresh\(\{ refreshNow: true \}\)/);
  assert.match(busySource, /renderServiceStatusRefreshCadence\(\)/);
  assert.match(cadenceSource, /launcher_services_auto_refresh_active/);
  assert.match(cadenceSource, /Updates every 2 seconds while an action is running/);
  assert.match(source, /function renderStackSnapshot\([\s\S]*renderStatusHero[\s\S]*renderMetricStates[\s\S]*renderServices/);
});

test('dashboard and Services table use the expected denominator and explicit missing rows', async () => {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const servicesStart = source.indexOf('function renderServices(');
  const servicesEnd = source.indexOf('function renderVisitorIpStatus(', servicesStart);
  const servicesSource = source.slice(servicesStart, servicesEnd);
  const metricStart = source.indexOf('function stackMetricState(');
  const metricEnd = source.indexOf('function endpointMetricState(', metricStart);
  const metricSource = source.slice(metricStart, metricEnd);

  assert.match(servicesSource, /const total = [^;]*stack\.total/);
  assert.match(servicesSource, /launcher_services_running_count/);
  assert.match(servicesSource, /service\.Missing === true/);
  assert.match(servicesSource, /launcher_service_not_created/);
  assert.match(metricSource, /expectedServicesAreRunning\(stack\)/);
  assert.match(metricSource, /launcher_stack_partial_running_detail/);
  assert.match(metricSource, /Number\(stack\.total \|\| 0\) - Number\(stack\.running \|\| 0\)/);
});

test('healthy legacy status falls back unless expected services are explicitly known', async () => {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const helperStart = source.indexOf('function expectedServicesAreRunning(');
  const helperEnd = source.indexOf('function omlorixServiceRunning(', helperStart);
  const helperSource = source.slice(helperStart, helperEnd);

  assert.match(helperSource, /stack\.expectedKnown !== true && Boolean\(stack\.healthy\)/);
});

test('focused service status is exposed only through trusted launcher IPC', async () => {
  const mainSource = await fs.readFile(path.join(electronRoot, 'main.js'), 'utf8');
  const preloadSource = await fs.readFile(path.join(electronRoot, 'preload.js'), 'utf8');

  assert.match(
    mainSource,
    /handleTrustedIpc\('server:get-service-status',[\s\S]*serverManager\.stackStatus\(\{[\s\S]*includeDiagnostics: false/,
  );
  assert.match(
    preloadSource,
    /getServiceStatus: \(\) => ipcRenderer\.invoke\('server:get-service-status'\)/,
  );
  assert.match(
    mainSource,
    /handleTrustedIpc\('server:service-action',[\s\S]*serverManager\.serviceAction\(action, serviceName, options\)/,
  );
  assert.match(
    preloadSource,
    /serviceAction: \(action, serviceName, options\)[\s\S]*ipcRenderer\.invoke\('server:service-action', action, serviceName, options\)/,
  );
});

test('Services rows expose lifecycle and log actions with accessible labels', async () => {
  const source = await fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8');
  const servicesStart = source.indexOf('function renderServices(');
  const servicesEnd = source.indexOf('function renderVisitorIpStatus(', servicesStart);
  const servicesSource = source.slice(servicesStart, servicesEnd);

  assert.match(servicesSource, /isRunning \? \['stop', 'restart', 'logs'\] : \['start', 'logs'\]/);
  assert.match(servicesSource, /button\.dataset\.serviceAction = action/);
  assert.match(servicesSource, /button\.setAttribute\('aria-label'/);
  assert.match(source, /servicesBody\.addEventListener\('click'/);
  assert.match(source, /window\.omlorixServer\.serviceAction\(action, serviceName\)/);
});
